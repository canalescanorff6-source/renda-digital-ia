"""Trading PRO v5 deployment preflight; no trade or broker credentials."""
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from flask import jsonify, session

VERSION = "5.0.0"
DEFAULT_APP_SECRET = "mude-esta-chave-antes-de-colocar-online"
DEFAULT_ADMIN_PASSWORD = "MudeEssaSenha123!"


def _utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _temporary_database(path):
    if not path:
        return None
    try:
        resolved = Path(path).resolve()
        for parent in ("/tmp", "/var/tmp", "/dev/shm"):
            if resolved.is_relative_to(Path(parent)):
                return True
        return False
    except (OSError, RuntimeError, ValueError):
        return None


def _check(id_, good, good_detail, bad_detail, blocking=True):
    return {
        "id": id_, "state": "pass" if good else ("blocker" if blocking else "warning"),
        "detail": good_detail if good else bad_detail,
    }


def init_deployment_tables(db_conn):
    conn = db_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tp_deploy_checkpoints (
                user_id INTEGER PRIMARY KEY,
                checkpoint_code TEXT NOT NULL,
                created_at TEXT NOT NULL,
                version_at_creation TEXT NOT NULL
            )
        """)
        conn.commit()
    finally:
        conn.close()


def get_checkpoint(conn, user_id):
    row = conn.execute(
        "SELECT checkpoint_code, created_at, version_at_creation "
        "FROM tp_deploy_checkpoints WHERE user_id=?", (user_id,),
    ).fetchone()
    return dict(row) if row else None


def create_checkpoint(db_conn, user_id):
    """Make a stable proof token for manual comparison AFTER a redeploy.

    This action alone does not prove that a restart happened or storage persisted.
    """
    conn = db_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT OR IGNORE INTO tp_deploy_checkpoints "
            "(user_id,checkpoint_code,created_at,version_at_creation) "
            "VALUES (?,?,?,?)",
            (user_id, secrets.token_hex(6).upper(), _utc(), VERSION),
        )
        stored = get_checkpoint(conn, user_id)
        conn.commit()
        return stored
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def deployment_preflight(app, db_conn, user_id):
    """Read-only authenticated checks. No secret values or paths disclosed."""
    checks = []
    key = str(app.secret_key or "")
    secure_key = len(key) >= 32 and key != DEFAULT_APP_SECRET
    checks.append(_check(
        "session_secret", secure_key,
        "Chave de sessão definida; não confirma rotação ou gestão externa de segredos.",
        "Defina SECRET_KEY forte e exclusiva na hospedagem.",
    ))
    admin = os.getenv("ADMIN_PASSWORD", DEFAULT_ADMIN_PASSWORD)
    secure_admin_config = len(admin) >= 12 and admin != DEFAULT_ADMIN_PASSWORD
    checks.append(_check(
        "admin_password_config", secure_admin_config,
        "ADMIN_PASSWORD de inicialização diferente do padrão; senha no banco não auditada.",
        "Troque a senha administrativa padrão e confira a conta já gravada no banco.",
    ))

    db_state = "unknown"
    checkpoint = None
    db_ok = False
    try:
        conn = db_conn()
        try:
            database_file = next(
                (row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main"),
                None,
            )
            temp = _temporary_database(database_file)
            db_state = "temporary" if temp else "persistent_candidate" if temp is False else "unknown"
            quick = conn.execute("PRAGMA quick_check(1)").fetchone()[0]
            db_ok = str(quick).lower() == "ok"
            checkpoint = get_checkpoint(conn, user_id)
        finally:
            conn.close()
    except Exception:
        db_ok = False

    checks.append(_check(
        "sqlite_integrity", db_ok,
        "SQLite respondeu à verificação rápida de integridade.",
        "Banco indisponível ou integridade não verificada.",
    ))
    checks.append(_check(
        "storage_location", db_state == "persistent_candidate",
        "Banco fora dos diretórios temporários comuns; persistência ainda exige teste após deploy.",
        "Banco temporário ou localização incerta; configure DATABASE_PATH em volume persistente e confira após reiniciar.",
    ))

    cron_enabled = os.getenv("TRADING_PRO_SCHEDULER_ENABLED", "") == "1"
    cron_secret = os.getenv("TRADING_PRO_CRON_SECRET", "")
    checks.append(_check(
        "scheduler_endpoint", cron_enabled and len(cron_secret) >= 32,
        "Endpoint do cron está configurado no servidor. Configuração no GitHub não verificada.",
        "Agendamento do servidor não habilitado ou segredo insuficiente. Mantenha-o desligado até validar HTTPS e banco persistente.",
        blocking=False,
    ))

    blocking = [item["id"] for item in checks if item["state"] == "blocker"]
    return {
        "version": VERSION,
        "execution_mode": "PAPER_ONLY",
        "can_claim_live": False,
        "status": "needs_configuration" if blocking else "preflight_passed_not_production_verified",
        "blocking_check_ids": blocking,
        "checks": checks,
        "database_location_kind": db_state,
        "checkpoint": checkpoint,
        "checkpoint_explanation": (
            "Este identificador serve para comparação manual após reiniciar ou redeploy. "
            "Sua presença antes do restart não prova persistência."
        ),
        "deployment_note": (
            "Esta API verifica somente a instância que respondeu à requisição. "
            "Não confirma agendamento do GitHub, acesso permanente à Binance "
            "nem operações reais ou lucro."
        ),
    }


def register_deployment_routes(app, login_required, protected_write, db_conn):
    init_deployment_tables(db_conn)

    @app.route("/trading-pro/ping", methods=["GET"])
    def trading_deploy_ping():
        """Public build marker; contains no configuration details."""
        response = jsonify({
            "ok": True, "component": "Renda Digital IA Trading PRO",
            "version": VERSION, "execution_mode": "PAPER_ONLY",
        })
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/trading-pro/api/deployment", methods=["GET"])
    @login_required
    def trading_deploy_diagnostic():
        result = deployment_preflight(app, db_conn, int(session["user_id"]))
        response = jsonify({"ok": True, "preflight": result})
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/trading-pro/api/deployment/checkpoint", methods=["POST"])
    @protected_write
    def trading_deploy_checkpoint():
        saved = create_checkpoint(db_conn, int(session["user_id"]))
        response = jsonify({"ok": True, "checkpoint": saved})
        response.headers["Cache-Control"] = "no-store"
        return response
