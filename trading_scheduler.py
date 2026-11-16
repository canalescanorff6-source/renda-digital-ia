"""Trading PRO v3: explicit opt-in scheduled PAPER-ONLY simulation.

The task is triggered externally. No threads or intervals are started by Flask.
Without explicit operator configuration and per-user opt-in, nothing runs.
"""
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

import requests
from flask import jsonify, request, session

from trading_pro import BOTS, execute_cycle, fetch_markets, state_of, ensure_account

MAX_USERS_PER_TICK = 10
MAX_CANDLE_AGE_MS = 2 * 3_600_000 + 30 * 60_000
LEASE_SECONDS = 180
HOUR_MS = 3_600_000


def utc_now():
    return datetime.now(timezone.utc)


def timestamp():
    return utc_now().isoformat(timespec="seconds")


def scheduler_configured():
    secret = os.getenv("TRADING_PRO_CRON_SECRET", "")
    enabled = os.getenv("TRADING_PRO_SCHEDULER_ENABLED", "").strip() == "1"
    return enabled and len(secret) >= 32


def init_scheduler_tables(db_conn):
    conn = db_conn()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tp_auto_settings (
                user_id INTEGER PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
                updated_at TEXT NOT NULL,
                last_attempt_at TEXT
            );
            CREATE TABLE IF NOT EXISTS tp_scheduler_lease (
                id INTEGER PRIMARY KEY CHECK(id=1),
                token TEXT,
                leased_until TEXT
            );
            INSERT OR IGNORE INTO tp_scheduler_lease (id, token, leased_until)
            VALUES (1, NULL, NULL);
            CREATE TABLE IF NOT EXISTS tp_job_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                eligible_count INTEGER NOT NULL DEFAULT 0,
                processed_count INTEGER NOT NULL DEFAULT 0,
                error_count INTEGER NOT NULL DEFAULT 0,
                last_candle_time INTEGER,
                note TEXT
            );
            CREATE TABLE IF NOT EXISTS tp_daily_snapshots (
                user_id INTEGER NOT NULL,
                day_utc TEXT NOT NULL,
                as_of TEXT NOT NULL,
                last_candle_time INTEGER NOT NULL,
                cash REAL NOT NULL,
                equity REAL NOT NULL,
                pnl REAL NOT NULL,
                pnl_pct REAL NOT NULL,
                drawdown_pct REAL NOT NULL,
                fees_total REAL NOT NULL,
                positions_count INTEGER NOT NULL,
                trades_count INTEGER NOT NULL,
                PRIMARY KEY(user_id, day_utc)
            );
        """)
        conn.commit()
    finally:
        conn.close()


def get_auto_preference(conn, user_id):
    row = conn.execute(
        "SELECT enabled FROM tp_auto_settings WHERE user_id=?", (user_id,),
    ).fetchone()
    return bool(row and row["enabled"] == 1)


def set_auto_preference(db_conn, user_id, enabled):
    if not isinstance(enabled, bool):
        raise ValueError("O campo enabled deve ser booleano")
    conn = db_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        ensure_account(conn, user_id)
        conn.execute(
            "INSERT INTO tp_auto_settings (user_id,enabled,updated_at) VALUES (?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET enabled=excluded.enabled, "
            "updated_at=excluded.updated_at",
            (user_id, int(enabled), timestamp()),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_automation_report(db_conn, user_id):
    conn = db_conn()
    try:
        account = ensure_account(conn, user_id)
        opted_in = get_auto_preference(conn, user_id)
        job = conn.execute(
            "SELECT started_at, finished_at, status, eligible_count, "
            "processed_count, error_count, last_candle_time, note "
            "FROM tp_job_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        rows = conn.execute(
            "SELECT day_utc, as_of, last_candle_time, cash, equity, pnl, pnl_pct, "
            "drawdown_pct, fees_total, positions_count, trades_count "
            "FROM tp_daily_snapshots WHERE user_id=? ORDER BY day_utc DESC LIMIT 14",
            (user_id,),
        ).fetchall()
        conn.commit()
        return {
            "paper_only": True,
            "opted_in": opted_in,
            "server_configured": scheduler_configured(),
            "account_mode": account["mode"],
            "will_run_when_triggered": (
                opted_in and scheduler_configured() and account["mode"] == "active"
            ),
            "latest_scheduler_run": dict(job) if job else None,
            "daily_snapshots": [dict(row) for row in reversed(rows)],
            "snapshot_disclaimer": (
                "Valores capturados no último ciclo de cada dia UTC. "
                "Não são necessariamente fechamentos do dia nem preços em tempo real."
            ),
        }
    finally:
        conn.close()


def acquire_lease(db_conn):
    """Atomic SQLite lease; duplicate webhook runs do not overlap."""
    token = secrets.token_hex(24)
    now = timestamp()
    expires = (utc_now() + timedelta(seconds=LEASE_SECONDS)).isoformat(timespec="seconds")
    conn = db_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        changed = conn.execute(
            "UPDATE tp_scheduler_lease SET token=?, leased_until=? "
            "WHERE id=1 AND (leased_until IS NULL OR leased_until < ?)",
            (token, expires, now),
        )
        if changed.rowcount != 1:
            conn.rollback()
            return None
        conn.commit()
        return token
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def release_lease(db_conn, token):
    conn = db_conn()
    try:
        conn.execute(
            "UPDATE tp_scheduler_lease SET token=NULL, leased_until=NULL "
            "WHERE id=1 AND token=?", (token,),
        )
        conn.commit()
    finally:
        conn.close()


def eligible_user_ids(db_conn):
    conn = db_conn()
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM tp_auto_settings s "
            "JOIN tp_accounts a ON a.user_id=s.user_id "
            "WHERE s.enabled=1 AND a.mode='active'"
        ).fetchone()[0]
        users = conn.execute(
            "SELECT s.user_id FROM tp_auto_settings s "
            "JOIN tp_accounts a ON a.user_id=s.user_id "
            "WHERE s.enabled=1 AND a.mode='active' "
            "ORDER BY COALESCE(s.last_attempt_at,''), s.user_id LIMIT ?",
            (MAX_USERS_PER_TICK,),
        ).fetchall()
        return total, [row["user_id"] for row in users]
    finally:
        conn.close()


def mark_attempt(db_conn, user_id):
    conn = db_conn()
    try:
        conn.execute(
            "UPDATE tp_auto_settings SET last_attempt_at=? WHERE user_id=?",
            (timestamp(), user_id),
        )
        conn.commit()
    finally:
        conn.close()


def record_snapshot(db_conn, user_id, state, candle_time):
    conn = db_conn()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM tp_trades WHERE user_id=?", (user_id,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO tp_daily_snapshots "
            "(user_id,day_utc,as_of,last_candle_time,cash,equity,pnl,pnl_pct,"
            "drawdown_pct,fees_total,positions_count,trades_count) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(user_id,day_utc) DO UPDATE SET "
            "as_of=excluded.as_of,last_candle_time=excluded.last_candle_time,"
            "cash=excluded.cash,equity=excluded.equity,pnl=excluded.pnl,"
            "pnl_pct=excluded.pnl_pct,drawdown_pct=excluded.drawdown_pct,"
            "fees_total=excluded.fees_total,positions_count=excluded.positions_count,"
            "trades_count=excluded.trades_count",
            (
                user_id, utc_now().date().isoformat(), timestamp(), candle_time,
                state["cash"], state["equity"], state["pnl"], state["pnl_pct"],
                state["drawdown_pct"], state["fees_total"],
                len(state["positions"]), count,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def validate_fresh_markets(markets, now_millis=None):
    expected = {bot["symbol"] for bot in BOTS}
    if set(markets) != expected:
        raise ValueError("Mercados incompletos")
    now_millis = now_millis if now_millis is not None else int(utc_now().timestamp() * 1000)
    last_candles = {int(markets[s]["last_candle"]) for s in expected}
    if len(last_candles) != 1:
        raise ValueError("Mercados com candles fora de sincronia")
    last_candle = next(iter(last_candles))
    age = now_millis - last_candle
    if not HOUR_MS <= age <= MAX_CANDLE_AGE_MS:
        raise ValueError("Fonte de candles defasada ou com horário inválido")
    for symbol in expected:
        if len(markets[symbol]["bars"]) < 100:
            raise ValueError("Indicadores sem histórico suficiente")
        if int(markets[symbol]["bars"][-1]["time"]) != last_candle:
            raise ValueError("Último candle inconsistente")
    return last_candle


def begin_job(db_conn):
    conn = db_conn()
    try:
        cursor = conn.execute(
            "INSERT INTO tp_job_runs (started_at,status) VALUES (?,'running')",
            (timestamp(),),
        )
        job_id = cursor.lastrowid
        conn.commit()
        return job_id
    finally:
        conn.close()


def finish_job(db_conn, job_id, status, eligible, processed, errors, candle, note):
    conn = db_conn()
    try:
        conn.execute(
            "UPDATE tp_job_runs SET finished_at=?,status=?,eligible_count=?,"
            "processed_count=?,error_count=?,last_candle_time=?,note=? WHERE id=?",
            (timestamp(), status, eligible, processed, errors, candle, note[:180], job_id),
        )
        conn.commit()
    finally:
        conn.close()


def run_scheduled_paper_cycle(db_conn, market_fetch=None):
    """Run a bounded, opted-in job. Fails closed on data errors, no fake returns."""
    if not scheduler_configured():
        raise PermissionError("Agendador não configurado")
    token = acquire_lease(db_conn)
    if not token:
        return {"status": "busy", "processed": 0, "errors": 0}
    job_id = None
    eligible = processed = errors = 0
    candle = None
    status = "idle"
    note = "Nenhum usuário habilitado"
    try:
        job_id = begin_job(db_conn)
        eligible, users = eligible_user_ids(db_conn)
        if users:
            try:
                market_data = (market_fetch or fetch_markets)()
                candle = validate_fresh_markets(market_data)
            except (requests.RequestException, ValueError, KeyError, TypeError, IndexError) as exc:
                status = "market_error"
                errors = 1
                note = "Fonte de mercado indisponível ou defasada: " + type(exc).__name__
            else:
                for user_id in users:
                    mark_attempt(db_conn, user_id)
                    # execute_cycle verifies mode inside its transaction; a
                    # concurrent pause request wins if it commits first.
                    try:
                        state = execute_cycle(db_conn, user_id, market_data)
                        record_snapshot(db_conn, user_id, state, candle)
                        processed += 1
                    except ValueError:
                        # Another request may have paused user after selection.
                        continue
                    except Exception:
                        errors += 1
                status = "partial_error" if errors else "success"
                note = ("Processamento limitado a %s usuários por disparo" % MAX_USERS_PER_TICK
                        if eligible > len(users) else "Ciclos fictícios registrados")
        if job_id:
            finish_job(db_conn, job_id, status, eligible, processed, errors, candle, note)
        return {
            "status": status, "eligible": eligible, "processed": processed,
            "errors": errors, "last_candle_time": candle,
            "backlog": max(0, eligible - processed),
        }
    except Exception:
        if job_id:
            finish_job(
                db_conn, job_id, "internal_error", eligible, processed,
                errors + 1, candle, "Falha interna no agendador",
            )
        raise
    finally:
        release_lease(db_conn, token)


def register_automation_routes(app, login_required, protected_write, db_conn):
    init_scheduler_tables(db_conn)

    @app.route("/trading-pro/api/automation/status", methods=["GET"])
    @login_required
    def tp_auto_status():
        return jsonify({"ok": True, "automation": get_automation_report(
            db_conn, int(session["user_id"])
        )})

    @app.route("/trading-pro/api/automation/settings", methods=["POST"])
    @protected_write
    def tp_auto_settings():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get("enabled"), bool):
            return jsonify({"ok": False, "error": "Informe enabled: true/false"}), 400
        user_id = int(session["user_id"])
        set_auto_preference(db_conn, user_id, payload["enabled"])
        return jsonify({"ok": True, "automation": get_automation_report(db_conn, user_id)})

    @app.route("/internal/trading-pro/cron", methods=["POST"])
    def tp_scheduled_cron():
        if not scheduler_configured():
            return jsonify({"ok": False, "error": "Agendador desabilitado"}), 404
        token = os.getenv("TRADING_PRO_CRON_SECRET", "")
        supplied = request.headers.get("Authorization", "")
        wanted = "Bearer " + token
        if not hmac.compare_digest(supplied, wanted):
            return jsonify({"ok": False, "error": "Não autorizado"}), 401
        try:
            result = run_scheduled_paper_cycle(db_conn)
        except Exception:
            app.logger.exception("Scheduled PAPER cycle failed")
            return jsonify({"ok": False, "error": "Falha interna no ciclo simulado"}), 503
        return jsonify({"ok": result["status"] not in ("market_error", "internal_error"),
                        "job": result}), 503 if result["status"] == "market_error" else 200
