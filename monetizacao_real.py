"""Central operacional de monetização: dados informados, sem promessas de renda.

Rotas isoladas para preservar os fluxos existentes de produtos, leads e vendas.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hmac import compare_digest
import secrets
from urllib.parse import urlsplit

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for


CHANNELS = {
    "proprio": "Produto digital próprio",
    "mercado_livre": "Afiliado Mercado Livre",
    "hotmart": "Afiliado Hotmart",
    "tiktok": "Afiliado TikTok Shop",
    "amazon": "Associados Amazon",
}
STAGES = {
    "planejando": "Planejando",
    "publicado": "Publicado",
    "testando": "Testando",
    "encerrado": "Encerrado",
}


def _whole_number(value, label):
    try:
        num = int(str(value).strip())
        if not 0 <= num <= 100_000_000:
            raise ValueError
        return num
    except (ValueError, TypeError):
        raise ValueError(f"{label}: informe um inteiro entre 0 e 100 milhões.")


def _amount(value, label):
    try:
        num = Decimal(str(value or "0").replace(",", ".").strip())
        if not num.is_finite() or not Decimal("0") <= num <= Decimal("100000000"):
            raise ValueError
        return float(num.quantize(Decimal("0.01")))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label}: informe um valor válido igual ou maior que zero.")


def _allowed_url(value):
    value = (value or "").strip()
    if not value:
        return ""
    parsed = urlsplit(value)
    if len(value) > 500 or parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Use um link válido começando com https:// ou http://.")
    if parsed.username or parsed.password:
        raise ValueError("Links não podem conter usuário ou senha.")
    return value


def register_monetizacao_real(app, db_conn):
    """Instala o módulo no Flask existente sem modificar tabelas legadas."""
    bp = Blueprint("monetizacao", __name__)

    def initialize(conn):
        conn.execute("""
            CREATE TABLE IF NOT EXISTS marketing_experiments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                channel TEXT NOT NULL,
                reference_url TEXT NOT NULL DEFAULT '',
                stage TEXT NOT NULL DEFAULT 'planejando',
                clicks INTEGER NOT NULL DEFAULT 0,
                sales INTEGER NOT NULL DEFAULT 0,
                received REAL NOT NULL DEFAULT 0,
                cost REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()

    @bp.route("/monetizacao-real", methods=["GET", "POST"])
    def painel():
        if not session.get("user_id"):
            return redirect(url_for("login"))
        uid = int(session["user_id"])
        if not session.get("monetizacao_csrf"):
            session["monetizacao_csrf"] = secrets.token_urlsafe(32)
        csrf = session["monetizacao_csrf"]

        with db_conn() as conn:
            initialize(conn)
            if request.method == "POST":
                if not compare_digest(str(csrf), str(request.form.get("csrf", ""))):
                    abort(400, "Requisição inválida. Recarregue a página.")
                try:
                    action = request.form.get("action")
                    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    if action == "create":
                        title = (request.form.get("title") or "").strip()
                        channel = request.form.get("channel")
                        if len(title) < 3 or len(title) > 110:
                            raise ValueError("Descreva a oferta com 3 a 110 caracteres.")
                        if channel not in CHANNELS:
                            raise ValueError("Selecione uma plataforma válida.")
                        reference_url = _allowed_url(request.form.get("reference_url"))
                        conn.execute("""
                            INSERT INTO marketing_experiments
                              (user_id, title, channel, reference_url, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (uid, title, channel, reference_url, now, now))
                        flash("Teste criado. Atualize os números somente com dados reais.", "success")
                    elif action == "metrics":
                        test_id = _whole_number(request.form.get("test_id"), "Campanha")
                        stage = request.form.get("stage")
                        if stage not in STAGES:
                            raise ValueError("Selecione uma etapa válida.")
                        clicks = _whole_number(request.form.get("clicks"), "Cliques")
                        sales = _whole_number(request.form.get("sales"), "Vendas")
                        received = _amount(request.form.get("received"), "Dinheiro recebido")
                        cost = _amount(request.form.get("cost"), "Custos")
                        result = conn.execute("""
                            UPDATE marketing_experiments
                            SET stage=?, clicks=?, sales=?, received=?, cost=?, updated_at=?
                            WHERE id=? AND user_id=?
                        """, (stage, clicks, sales, received, cost, now, test_id, uid))
                        if result.rowcount != 1:
                            abort(404)
                        flash("Resultados informados atualizados.", "success")
                    else:
                        abort(400, "Ação desconhecida.")
                    conn.commit()
                    return redirect(url_for("monetizacao.painel", _anchor="acompanhamento"))
                except ValueError as exc:
                    flash(str(exc), "error")
                    return redirect(url_for("monetizacao.painel", _anchor="acompanhamento"))

            rows = conn.execute("""
                SELECT id, title, channel, reference_url, stage, clicks, sales,
                       received, cost, created_at
                FROM marketing_experiments WHERE user_id=?
                ORDER BY id DESC LIMIT 100
            """, (uid,)).fetchall()
            all_stats = conn.execute("""
                SELECT COUNT(*) AS experiments, COALESCE(SUM(clicks),0) AS clicks,
                       COALESCE(SUM(sales),0) AS sales,
                       COALESCE(SUM(received),0) AS received,
                       COALESCE(SUM(cost),0) AS cost
                FROM marketing_experiments WHERE user_id=?
            """, (uid,)).fetchone()

        stats = dict(all_stats)
        stats["net"] = round(stats["received"] - stats["cost"], 2)
        return render_template(
            "monetizacao_real.html",
            title="Central de monetização real",
            experiments=[dict(row) for row in rows],
            stats=stats,
            channels=CHANNELS,
            stages=STAGES,
            csrf=csrf,
        )

    app.register_blueprint(bp)
