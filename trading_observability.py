"""Trading PRO v4: auditable monitoring and read-only market diagnostics.

No exchange credentials, exchange orders, user deposits or simulated performance
fabrication. A successful unit test is not proof of real market connectivity.
"""
import json
import math
import sqlite3
from datetime import datetime, timezone

import requests
from flask import jsonify, render_template, request, session

from trading_pro import BOTS, fetch_markets, ensure_account, open_positions, state_of
from trading_scheduler import scheduler_configured, get_auto_preference, validate_fresh_markets

PROBE_COOLDOWN_SECONDS = 90
SCHEDULER_STALE_SECONDS = 3 * 3600
MAX_DAYS = 30


def _now():
    return datetime.now(timezone.utc)


def _stamp(now=None):
    return (now or _now()).isoformat(timespec="seconds")


def _age(timestamp, now=None):
    if not timestamp:
        return None
    try:
        then = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        return max(0, int(((now or _now()) - then).total_seconds()))
    except (TypeError, ValueError, OverflowError):
        return None


def init_observability(db_conn):
    conn = db_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tp_market_probes (
                user_id INTEGER PRIMARY KEY,
                checked_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('checking','ok','error')),
                result_json TEXT
            )
        """)
        conn.commit()
    finally:
        conn.close()


def _trades_metrics(conn, user_id):
    row = conn.execute(
        "SELECT COUNT(*) AS executions, "
        "COALESCE(SUM(fee), 0) AS fees, "
        "COALESCE(SUM(CASE WHEN side='SELL' THEN 1 ELSE 0 END),0) AS exits, "
        "COALESCE(SUM(CASE WHEN side='SELL' AND realized_pnl > 0 THEN 1 ELSE 0 END),0) AS wins, "
        "COALESCE(SUM(CASE WHEN side='SELL' THEN realized_pnl ELSE 0 END),0) AS realized, "
        "COALESCE(SUM(CASE WHEN side='SELL' AND realized_pnl > 0 "
        "THEN realized_pnl ELSE 0 END),0) AS gains, "
        "COALESCE(SUM(CASE WHEN side='SELL' AND realized_pnl < 0 "
        "THEN -realized_pnl ELSE 0 END),0) AS losses "
        "FROM tp_trades WHERE user_id=?", (user_id,),
    ).fetchone()
    exits, wins, gains, losses = (
        int(row["exits"]), int(row["wins"]), float(row["gains"]), float(row["losses"])
    )
    return {
        "executions": int(row["executions"]),
        "closed_trades": exits,
        "wins": wins,
        "win_rate_pct": round(wins / exits * 100, 2) if exits else None,
        "realized_pnl_usdt": round(float(row["realized"]), 4),
        "fees_usdt": round(float(row["fees"]), 4),
        "profit_factor": round(gains / losses, 3) if losses > 0 else None,
        "profit_factor_note": (
            "Sem perdas fechadas registradas: fator de lucro indefinido"
            if losses == 0 else "Calculado apenas sobre posições encerradas"
        ),
    }


def _strategy_stats(conn, user_id):
    rows = conn.execute(
        "SELECT bot_id, "
        "COALESCE(SUM(CASE WHEN side='SELL' THEN 1 ELSE 0 END),0) AS exits, "
        "COALESCE(SUM(CASE WHEN side='SELL' AND realized_pnl > 0 THEN 1 ELSE 0 END),0) AS wins, "
        "COALESCE(SUM(CASE WHEN side='SELL' THEN realized_pnl ELSE 0 END),0) AS realized, "
        "COALESCE(SUM(fee),0) AS fees "
        "FROM tp_trades WHERE user_id=? GROUP BY bot_id", (user_id,),
    ).fetchall()
    by_id = {r["bot_id"]: dict(r) for r in rows}
    positions = {
        r["bot_id"]: r for r in conn.execute(
            "SELECT bot_id,quantity,entry_price,last_price "
            "FROM tp_positions WHERE user_id=?", (user_id,),
        ).fetchall()
    }
    result = []
    for bot in BOTS:
        data = by_id.get(bot["id"], {})
        closes = int(data.get("exits", 0))
        position = positions.get(bot["id"])
        result.append({
            "id": bot["id"], "name": bot["name"], "symbol": bot["symbol"],
            "closed_trades": closes,
            "wins": int(data.get("wins", 0)),
            "win_rate_pct": (
                round(int(data.get("wins", 0)) / closes * 100, 2)
                if closes else None
            ),
            "realized_pnl_usdt": round(float(data.get("realized", 0)), 4),
            "fees_usdt": round(float(data.get("fees", 0)), 4),
            "has_open_position": bool(position),
            "last_mark_unrealized_usdt": (
                round(float(position["quantity"]) *
                      (float(position["last_price"]) - float(position["entry_price"])), 4)
                if position else None
            ),
        })
    return result


def get_monitor_report(db_conn, user_id, now=None):
    now = now or _now()
    conn = db_conn()
    try:
        account = ensure_account(conn, user_id)
        opt = get_auto_preference(conn, user_id)
        cfg = scheduler_configured()
        settings = conn.execute(
            "SELECT last_attempt_at FROM tp_auto_settings WHERE user_id=?",
            (user_id,),
        ).fetchone()
        last_attempt = settings["last_attempt_at"] if settings else None
        snapshot_rows = conn.execute(
            "SELECT day_utc,as_of,last_candle_time,equity,cash,pnl,pnl_pct,"
            "drawdown_pct,fees_total,positions_count,trades_count "
            "FROM tp_daily_snapshots WHERE user_id=? "
            "ORDER BY day_utc DESC LIMIT ?",
            (user_id, MAX_DAYS),
        ).fetchall()
        snapshots = [dict(r) for r in reversed(snapshot_rows)]
        latest = snapshots[-1] if snapshots else None
        probe = conn.execute(
            "SELECT checked_at,status,result_json FROM tp_market_probes "
            "WHERE user_id=?", (user_id,),
        ).fetchone()
        probe_info = None
        if probe:
            probe_info = {
                "checked_at": probe["checked_at"],
                "status": probe["status"],
                "age_seconds": _age(probe["checked_at"], now),
                "result": json.loads(probe["result_json"]) if probe["result_json"] else None,
            }
        job = conn.execute(
            "SELECT started_at,finished_at,status,processed_count,error_count,note "
            "FROM tp_job_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        state = state_of(conn, user_id)
        activity_age = _age(latest["as_of"] if latest else None, now)
        if account["mode"] == "risk_paused":
            state_code = "risk_locked"
        elif account["mode"] == "paused":
            state_code = "paused"
        elif not opt:
            state_code = "disabled"
        elif not cfg:
            state_code = "server_not_configured"
        elif latest is None:
            state_code = "awaiting_first_run"
        elif activity_age is None or activity_age > SCHEDULER_STALE_SECONDS:
            state_code = "stale"
        else:
            state_code = "recent_snapshot"
        conn.commit()
        return {
            "execution_mode": "PAPER_ONLY",
            "generated_at": _stamp(now),
            "state": state_code,
            "scheduler_configured_on_server": cfg,
            "individual_opt_in": opt,
            "last_user_attempt_at": last_attempt,
            "last_user_attempt_age_seconds": _age(last_attempt, now),
            "last_snapshot_at": latest["as_of"] if latest else None,
            "last_snapshot_age_seconds": activity_age,
            "last_job_global": dict(job) if job else None,
            "market_probe": probe_info,
            "portfolio": {
                "equity_usdt": state["equity"],
                "cash_usdt": state["cash"],
                "total_pnl_usdt": state["pnl"],
                "pnl_pct": state["pnl_pct"],
                "drawdown_pct": state["drawdown_pct"],
                "price_last_updated_at": state["last_run"],
                "note": (
                    "Patrimônio calculado com os últimos preços persistidos; "
                    "sem nova coleta pode estar desatualizado."
                ),
            },
            "trades": _trades_metrics(conn, user_id),
            "strategies": _strategy_stats(conn, user_id),
            "daily": snapshots,
            "warnings": [
                "PAPER ONLY: nenhuma ordem real é executada.",
                "Status de atividade recente não comprova agendamento garantido 24/7.",
                "Lucros realizados não incluem variações não realizadas de posições abertas.",
                "Saldos e custos são fictícios e estimados, não prova de renda.",
            ],
        }
    finally:
        conn.close()


def market_diagnostic(db_conn, user_id, fetcher=None, now=None):
    """Read-only public market probe; per-user persisted throttle prevents API abuse.

    Never executes a cycle, creates a trade or updates portfolio valuations.
    """
    now = now or _now()
    conn = db_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        previous = conn.execute(
            "SELECT checked_at FROM tp_market_probes WHERE user_id=?", (user_id,),
        ).fetchone()
        age = _age(previous["checked_at"], now) if previous else None
        if age is not None and age < PROBE_COOLDOWN_SECONDS:
            conn.rollback()
            return {
                "ok": False, "error": "Aguarde antes de repetir o diagnóstico",
                "retry_after_seconds": PROBE_COOLDOWN_SECONDS - age,
            }, 429
        conn.execute(
            "INSERT INTO tp_market_probes (user_id,checked_at,status,result_json) "
            "VALUES (?,?,'checking',NULL) "
            "ON CONFLICT(user_id) DO UPDATE SET checked_at=excluded.checked_at,"
            "status='checking',result_json=NULL",
            (user_id, _stamp(now)),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    status = "ok"
    try:
        markets = (fetcher or fetch_markets)()
        candle = validate_fresh_markets(markets, now_millis=int(now.timestamp() * 1000))
        data = {
            "source": "Binance Spot API pública",
            "candle_open_time_utc": datetime.fromtimestamp(
                candle / 1000, timezone.utc
            ).isoformat(timespec="seconds"),
            "symbols": [
                {
                    "symbol": symbol, "price": round(float(markets[symbol]["price"]), 8),
                    "bars": len(markets[symbol]["bars"]),
                }
                for symbol in sorted({bot["symbol"] for bot in BOTS})
            ],
            "no_orders_placed": True,
        }
    except (requests.RequestException, ValueError, KeyError, TypeError, IndexError, OverflowError):
        status = "error"
        data = {
            "message": "Dados indisponíveis, defasados ou inválidos. Nenhuma ordem foi criada.",
            "no_orders_placed": True,
        }
    conn = db_conn()
    try:
        conn.execute(
            "UPDATE tp_market_probes SET status=?, result_json=? WHERE user_id=?",
            (status, json.dumps(data, ensure_ascii=False), user_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": status == "ok", "status": status, "result": data}, (
        200 if status == "ok" else 503
    )


def register_monitor_routes(app, login_required, protected_write, db_conn):
    init_observability(db_conn)

    @app.route("/trading-pro/monitor")
    @login_required
    def tp_monitor_page():
        return render_template("trading_monitor.html")

    @app.route("/trading-pro/api/monitor")
    @login_required
    def tp_monitor_status():
        return jsonify({
            "ok": True,
            "monitor": get_monitor_report(db_conn, int(session["user_id"])),
        })

    @app.route("/trading-pro/api/monitor/check-market", methods=["POST"])
    @protected_write
    def tp_monitor_check_market():
        result, status = market_diagnostic(db_conn, int(session["user_id"]))
        return jsonify(result), status
