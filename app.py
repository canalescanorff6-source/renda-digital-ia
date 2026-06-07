import os
import sys
import re
import json
import csv
import io
import sqlite3
import textwrap
import unicodedata
import traceback
from datetime import datetime, date
from functools import wraps
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from hashlib import sha256
from typing import Dict, Any, List, Optional

from flask import (
    Flask, render_template, request, redirect, url_for, session, flash,
    send_file, abort, jsonify, Response
)
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
    from reportlab.pdfbase.pdfmetrics import stringWidth
    REPORTLAB_OK = True
except Exception:
    REPORTLAB_OK = False

load_dotenv()

# Diretório dos arquivos do aplicativo. Também funciona quando empacotado em .exe com PyInstaller.
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)).resolve()
RUNTIME_DIR = Path(os.getenv("APP_RUNTIME_DIR", Path(__file__).resolve().parent)).resolve()
STORAGE_DIR = Path(os.getenv("STORAGE_DIR", RUNTIME_DIR / "storage")).resolve()
EXPORT_DIR = Path(os.getenv("EXPORT_DIR", STORAGE_DIR / "exports")).resolve()
DB_PATH = Path(os.getenv("DATABASE_PATH", STORAGE_DIR / "renda_digital_ia.db")).resolve()
STORAGE_DIR.mkdir(parents=True, exist_ok=True)
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"), static_folder=str(BASE_DIR / "static"))
app.secret_key = os.getenv("SECRET_KEY", "mude-esta-chave-antes-de-colocar-online")

APP_NAME = "Renda Digital IA Pro"
DEFAULT_ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@local.com")
DEFAULT_ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "MudeEssaSenha123!")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "troque-webhook-secret")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip()
FACEBOOK_PAGE_ID = os.getenv("FACEBOOK_PAGE_ID", "").strip()
FACEBOOK_PAGE_ACCESS_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN", "").strip()
INSTAGRAM_ACCOUNT_ID = os.getenv("INSTAGRAM_ACCOUNT_ID", "").strip()
TIKTOK_ACCESS_TOKEN = os.getenv("TIKTOK_ACCESS_TOKEN", "").strip()
YOUTUBE_READY = bool(os.getenv("YOUTUBE_CLIENT_SECRET_JSON", "").strip())

# ----------------------------- Banco de dados -----------------------------

def db_conn():
    # Timeout maior evita falhas temporárias no SQLite quando o app sobe em hospedagem.
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
    except Exception:
        pass
    return conn


def init_db():
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            niche TEXT NOT NULL,
            product_type TEXT NOT NULL,
            discipline TEXT DEFAULT 'Todas as disciplinas',
            school_level TEXT DEFAULT 'Ensino fundamental',
            target_audience TEXT NOT NULL,
            promise TEXT,
            price REAL DEFAULT 29.90,
            status TEXT DEFAULT 'rascunho',
            checkout_link TEXT,
            platform TEXT DEFAULT 'Manual',
            content TEXT,
            sales_page TEXT,
            social_posts TEXT,
            prompt_pack TEXT,
            public_slug TEXT UNIQUE,
            public_enabled INTEGER DEFAULT 1,
            lead_magnet_title TEXT,
            lead_magnet_content TEXT,
            guarantee_days INTEGER DEFAULT 7,
            bonus_stack TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    # Migração simples para quem já usava a v1/v2 e está atualizando para a v3.
    product_cols = [row[1] for row in cur.execute("PRAGMA table_info(products)").fetchall()]
    if "discipline" not in product_cols:
        cur.execute("ALTER TABLE products ADD COLUMN discipline TEXT DEFAULT 'Todas as disciplinas'")
    if "school_level" not in product_cols:
        cur.execute("ALTER TABLE products ADD COLUMN school_level TEXT DEFAULT 'Ensino fundamental'")
    if "public_slug" not in product_cols:
        cur.execute("ALTER TABLE products ADD COLUMN public_slug TEXT")
    if "public_enabled" not in product_cols:
        cur.execute("ALTER TABLE products ADD COLUMN public_enabled INTEGER DEFAULT 1")
    if "lead_magnet_title" not in product_cols:
        cur.execute("ALTER TABLE products ADD COLUMN lead_magnet_title TEXT")
    if "lead_magnet_content" not in product_cols:
        cur.execute("ALTER TABLE products ADD COLUMN lead_magnet_content TEXT")
    if "guarantee_days" not in product_cols:
        cur.execute("ALTER TABLE products ADD COLUMN guarantee_days INTEGER DEFAULT 7")
    if "bonus_stack" not in product_cols:
        cur.execute("ALTER TABLE products ADD COLUMN bonus_stack TEXT")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER,
            buyer_name TEXT,
            buyer_email TEXT,
            platform TEXT DEFAULT 'Manual',
            amount REAL NOT NULL,
            status TEXT DEFAULT 'aprovada',
            external_id TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(product_id) REFERENCES products(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER,
            name TEXT,
            email TEXT,
            whatsapp TEXT,
            source TEXT DEFAULT 'Página pública',
            note TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(product_id) REFERENCES products(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS visits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER,
            path TEXT,
            user_agent TEXT,
            ip_hash TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(product_id) REFERENCES products(id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    # Garante links públicos para produtos antigos após atualização.
    try:
        rows_without_slug = cur.execute("SELECT id, title FROM products WHERE public_slug IS NULL OR public_slug = ''").fetchall()
        for row in rows_without_slug:
            base = slugify(row["title"] if isinstance(row, sqlite3.Row) else row[1])
            candidate = base
            n = 2
            while cur.execute("SELECT id FROM products WHERE public_slug = ? AND id != ?", (candidate, row["id"] if isinstance(row, sqlite3.Row) else row[0])).fetchone():
                candidate = f"{base}-{n}"
                n += 1
            cur.execute("UPDATE products SET public_slug=?, public_enabled=1 WHERE id=?", (candidate, row["id"] if isinstance(row, sqlite3.Row) else row[0]))
    except Exception:
        pass

    # Evita CrashLoopBackOff quando a hospedagem inicia mais de um processo ao mesmo tempo.
    # Se outro worker já criou o admin, o INSERT OR IGNORE não derruba o app.
    cur.execute(
        "INSERT OR IGNORE INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
        (DEFAULT_ADMIN_EMAIL, generate_password_hash(DEFAULT_ADMIN_PASSWORD), datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


init_db()

# ----------------------------- Utilidades -----------------------------

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


def row_to_dict(row):
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def money(value: float) -> str:
    try:
        return f"R$ {float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "R$ 0,00"


app.jinja_env.filters["money"] = money


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    return response


@app.errorhandler(404)
def handle_404(exc):
    return (
        "<h1>Página não encontrada</h1>"
        "<p>Esse link não existe ou o produto ainda não foi criado nesta base.</p>"
        "<p><a href='/dashboard'>Voltar ao painel</a></p>",
        404,
    )


@app.errorhandler(500)
def handle_500(exc):
    traceback.print_exc()
    return (
        "<h1>Erro interno corrigível</h1>"
        "<p>O sistema encontrou um erro ao processar esta ação. Volte ao painel, tente novamente e confira os logs do RunSite se persistir.</p>"
        "<p><a href='/dashboard'>Voltar ao painel</a></p>",
        500,
    )


@app.route("/diagnostico")
@login_required
def diagnostico():
    checks = []
    def add(nome, ok, detalhe=""):
        checks.append({"nome": nome, "ok": bool(ok), "detalhe": detalhe})
    try:
        conn = db_conn(); conn.execute("SELECT 1").fetchone(); conn.close(); add("Banco de dados", True, str(DB_PATH))
    except Exception as exc:
        add("Banco de dados", False, str(exc))
    add("Pasta storage gravável", os.access(STORAGE_DIR, os.W_OK), str(STORAGE_DIR))
    add("Pasta exports gravável", os.access(EXPORT_DIR, os.W_OK), str(EXPORT_DIR))
    add("ReportLab PDF", REPORTLAB_OK, "PDF ativo" if REPORTLAB_OK else "Instale reportlab")
    try:
        from PIL import Image  # noqa
        import imageio.v2 as imageio  # noqa
        import numpy as np  # noqa
        add("Vídeos MP4", True, "Pillow + imageio + numpy OK")
    except Exception as exc:
        add("Vídeos MP4", False, str(exc))
    try:
        conn = db_conn(); total = conn.execute("SELECT COUNT(*) AS n FROM products").fetchone()["n"]; conn.close(); add("Produtos cadastrados", True, str(total))
    except Exception as exc:
        add("Produtos cadastrados", False, str(exc))
    html = "<h1>Diagnóstico do sistema</h1><ul>" + "".join([f"<li>{'✅' if c['ok'] else '❌'} <b>{c['nome']}</b> — {c['detalhe']}</li>" for c in checks]) + "</ul><p><a href='/dashboard'>Voltar</a></p>"
    return html


@app.route("/healthz")
def healthz():
    return {"status": "ok", "app": APP_NAME, "database": str(DB_PATH.name)}


@app.route("/favicon.ico")
def favicon():
    icon_path = BASE_DIR / "static" / "icons" / "icon-192.png"
    if icon_path.exists():
        return send_file(icon_path, mimetype="image/png")
    return ("", 204)


@app.route("/manifest.webmanifest")
def web_manifest():
    data = {
        "name": "Renda Digital IA Pro",
        "short_name": "Renda IA Pro",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#070917",
        "theme_color": "#19f5d0",
        "description": "Plataforma comercial para criar, organizar, divulgar e vender produtos digitais em múltiplos nichos.",
        "icons": [
            {"src": url_for("static", filename="icons/icon-192.png"), "sizes": "192x192", "type": "image/png"},
            {"src": url_for("static", filename="icons/icon-512.png"), "sizes": "512x512", "type": "image/png"}
        ]
    }
    return Response(json.dumps(data, ensure_ascii=False), mimetype="application/manifest+json")


@app.route("/service-worker.js")
def service_worker():
    js = """
const CACHE_NAME = 'renda-digital-ia-pro-v1';
const CORE_ASSETS = ['/', '/static/css/style.css', '/static/js/app.js'];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(CORE_ASSETS)).catch(() => null));
  self.skipWaiting();
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  event.respondWith(fetch(event.request).catch(() => caches.match(event.request)));
});
"""
    return Response(js, mimetype="application/javascript")


def slugify(text: str) -> str:
    """Gera slugs seguros para URL e nomes de arquivos.

    V19: remove acentos e caracteres especiais para evitar links públicos
    com caracteres difíceis em hospedagens, WhatsApp e checkouts.
    """
    text = str(text or "produto").lower().strip()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:70].strip("-") or "produto"


def safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None or str(value).strip() == "":
            return default
        raw = str(value).replace("R$", "").strip()
        if "," in raw:
            raw = raw.replace(".", "").replace(",", ".")
        return float(raw)
    except Exception:
        return default


def safe_int(value, default: int = 0) -> int:
    try:
        if value is None or str(value).strip() == "":
            return default
        return int(float(str(value).strip()))
    except Exception:
        return default


def form_or_existing(form: Dict[str, Any], key: str, existing: Dict[str, Any], default: Any = "") -> Any:
    """Usa valor enviado no formulário; se o campo não veio, preserva o antigo.

    Isso evita apagar textos premium ao salvar só preço/checkout/status por scripts ou telas parciais.
    """
    if key in form:
        return form.get(key)
    return existing.get(key, default)


def unique_public_slug(title: str, conn, product_id: Optional[int] = None) -> str:
    base = slugify(title)
    candidate = base
    n = 2
    while True:
        if product_id:
            row = conn.execute("SELECT id FROM products WHERE public_slug = ? AND id != ?", (candidate, product_id)).fetchone()
        else:
            row = conn.execute("SELECT id FROM products WHERE public_slug = ?", (candidate,)).fetchone()
        if not row:
            return candidate
        candidate = f"{base}-{n}"
        n += 1


def first_paragraphs(text: str, max_chars: int = 1800) -> str:
    text = text or ""
    clean_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("# "):
            clean_lines.append(stripped)
        if sum(len(x) for x in clean_lines) > max_chars:
            break
    sample = "\n".join(clean_lines)[:max_chars].strip()
    return sample or "Amostra do material disponível após cadastro."


NICHES = [
    {
        "name": "IA para pequenos negócios",
        "score": 99,
        "why": "Alta procura por soluções simples com IA: prompts, roteiros, atendimento, posts, cardápios, ideias de conteúdo e organização para MEI e pequenos negócios.",
        "examples": ["Pack de prompts", "Kit atendimento WhatsApp", "Roteiros Reels", "Calendário de conteúdo"],
        "price": "R$ 19,90 a R$ 97,00",
    },
    {
        "name": "Finanças pessoais e organização",
        "score": 97,
        "why": "Produtos de controle financeiro, orçamento familiar, metas, dívidas e planilhas simples têm apelo amplo. Use linguagem educativa, sem prometer enriquecimento.",
        "examples": ["Planner financeiro", "Desafio 52 semanas", "Controle de dívidas", "Planilha de orçamento"],
        "price": "R$ 17,00 a R$ 67,00",
    },
    {
        "name": "Pequenos negócios e MEI",
        "score": 96,
        "why": "Público grande que precisa vender melhor, organizar preço, estoque, agenda, atendimento e divulgação sem contratar agência.",
        "examples": ["Kit MEI organizado", "Calculadora de preço", "Agenda de clientes", "Pacote de posts"],
        "price": "R$ 27,00 a R$ 147,00",
    },
    {
        "name": "Beleza, estética e atendimento",
        "score": 94,
        "why": "Manicures, designers de sobrancelha, barbeiros, cabeleireiras e esteticistas usam muito WhatsApp e Instagram. Vendem bem kits de agenda, fichas, posts e mensagens prontas.",
        "examples": ["Agenda de clientes", "Fichas de atendimento", "Posts para Instagram", "Scripts WhatsApp"],
        "price": "R$ 19,90 a R$ 97,00",
    },
    {
        "name": "Casa, organização e rotina",
        "score": 92,
        "why": "Planner de limpeza, cardápio semanal, lista de compras, rotina familiar e organização doméstica têm público amplo, principalmente em redes sociais visuais.",
        "examples": ["Planner da casa", "Cardápio semanal", "Lista de compras", "Rotina familiar"],
        "price": "R$ 12,90 a R$ 49,90",
    },
    {
        "name": "Culinária, marmitas e confeitaria",
        "score": 91,
        "why": "Receitas, fichas técnicas, precificação, cardápios, marmitas e confeitaria são produtos práticos para quem quer economizar ou vender comida.",
        "examples": ["Receitas econômicas", "Ficha técnica", "Cardápio de marmitas", "Precificação de bolos"],
        "price": "R$ 19,90 a R$ 97,00",
    },
    {
        "name": "Carreira, currículo e renda extra",
        "score": 90,
        "why": "Modelos de currículo, roteiro de entrevista, organização de estudos e guias práticos atraem público grande. Evite promessa de emprego garantido.",
        "examples": ["Kit currículo", "LinkedIn básico", "Roteiro de entrevista", "Planner de carreira"],
        "price": "R$ 17,00 a R$ 67,00",
    },
    {
        "name": "Templates, design e redes sociais",
        "score": 90,
        "why": "Templates, calendários, legendas, bio, anúncios e identidade simples são fáceis de vender para autônomos, lojas e prestadores de serviço.",
        "examples": ["Pack Canva", "Calendário de posts", "Legendas prontas", "Kit identidade simples"],
        "price": "R$ 19,90 a R$ 97,00",
    },
    {
        "name": "Pets e rotina de cuidados",
        "score": 87,
        "why": "Produtos para tutores de pets podem vender bem: organização, rotina, banho, checklist, gastos e agenda. Não substitui orientação veterinária.",
        "examples": ["Planner pet", "Controle de gastos", "Rotina de cuidados", "Checklist de viagem"],
        "price": "R$ 12,90 a R$ 49,90",
    },
    {
        "name": "Educação - todas as disciplinas",
        "score": 86,
        "why": "Continua sendo uma categoria boa, mas agora é apenas uma das opções. Serve para professores, reforço escolar, pais, escolas pequenas e materiais pedagógicos.",
        "examples": ["Mega Kit Atividades", "Banco de Questões", "Simulados", "Pacote Bimestral"],
        "price": "R$ 27,00 a R$ 97,00",
    },
]

PRODUCT_TYPES = [
    "Pack de prompts IA",
    "Planner PDF",
    "Checklist prático",
    "Planilha simples",
    "Kit de templates",
    "Calendário de conteúdo",
    "Roteiros de vídeos curtos",
    "Scripts de WhatsApp",
    "Ebook guia prático",
    "Apostila editável",
    "Pacote completo ZIP",
    "Mega kit de atividades",
    "Banco de questões",
    "Simulado com gabarito",
    "Sequência didática",
]

SUBJECTS = [
    "Não se aplica / produto geral",
    "Todas as disciplinas",
    "Português",
    "Matemática",
    "Ciências",
    "História",
    "Geografia",
    "Inglês",
    "Artes",
    "Educação Física",
    "Ensino Religioso",
    "Redação",
    "Biologia",
    "Física",
    "Química",
    "Filosofia",
    "Sociologia",
    "Projeto de Vida",
]

SCHOOL_LEVELS = [
    "Não se aplica / público geral",
    "Iniciantes",
    "Público geral",
    "MEI e autônomos",
    "Pequenos negócios",
    "Profissionais de beleza",
    "Famílias e organização doméstica",
    "Educação infantil",
    "Ensino fundamental anos iniciais",
    "Ensino fundamental anos finais",
    "Ensino médio",
    "ENEM e vestibulares",
    "Reforço escolar",
]


# ----------------------------- BNCC profissional -----------------------------
# Base editável de referência. Revise com a BNCC oficial e o currículo local antes de vender.
BNCC_SKILLS = {
    "Português": [
        {"level":"Ensino fundamental anos iniciais","code":"EF15LP03","unit":"Leitura/escuta","object":"Estratégias de leitura","summary":"Localizar informações explícitas e compreender o texto."},
        {"level":"Ensino fundamental anos iniciais","code":"EF15LP05","unit":"Produção de textos","object":"Planejamento e escrita","summary":"Planejar e produzir textos com apoio e revisão."},
        {"level":"Ensino fundamental anos finais","code":"EF69LP03","unit":"Leitura","object":"Estratégias de leitura","summary":"Interpretar textos de diferentes gêneros."},
        {"level":"Ensino médio","code":"EM13LP02","unit":"Linguagens","object":"Análise de discursos","summary":"Analisar práticas de linguagem e efeitos de sentido."},
    ],
    "Redação": [
        {"level":"Ensino médio","code":"EM13LP15","unit":"Produção textual","object":"Argumentação","summary":"Planejar, produzir, revisar e reescrever textos argumentativos."},
        {"level":"ENEM e vestibulares","code":"EM13LP19","unit":"Produção textual","object":"Projeto de texto","summary":"Organizar tese, argumentos, coesão e conclusão."},
    ],
    "Matemática": [
        {"level":"Ensino fundamental anos iniciais","code":"EF03MA05","unit":"Números","object":"Operações","summary":"Resolver problemas com adição e subtração."},
        {"level":"Ensino fundamental anos iniciais","code":"EF04MA03","unit":"Números","object":"Problemas com números naturais","summary":"Resolver e elaborar problemas com números naturais."},
        {"level":"Ensino fundamental anos finais","code":"EF06MA03","unit":"Números","object":"Operações com naturais","summary":"Resolver problemas com operações e raciocínio numérico."},
        {"level":"Ensino médio","code":"EM13MAT101","unit":"Matemática e suas Tecnologias","object":"Modelagem","summary":"Interpretar situações por meio de conceitos matemáticos."},
    ],
    "Ciências": [
        {"level":"Ensino fundamental anos iniciais","code":"EF02CI04","unit":"Vida e evolução","object":"Seres vivos","summary":"Observar e comparar seres vivos e ambientes."},
        {"level":"Ensino fundamental anos iniciais","code":"EF04CI06","unit":"Vida e evolução","object":"Cadeias alimentares","summary":"Relacionar seres vivos, alimentação e equilíbrio ambiental."},
        {"level":"Ensino fundamental anos finais","code":"EF06CI01","unit":"Matéria e energia","object":"Misturas","summary":"Classificar materiais, misturas e processos de separação."},
        {"level":"Ensino médio","code":"EM13CNT101","unit":"Ciências da Natureza","object":"Fenômenos naturais","summary":"Analisar fenômenos naturais e tecnológicos com modelos científicos."},
    ],
    "Biologia": [
        {"level":"Ensino médio","code":"EM13CNT202","unit":"Ciências da Natureza","object":"Vida e ambiente","summary":"Analisar processos biológicos, biodiversidade, saúde e ambiente."},
    ],
    "Física": [
        {"level":"Ensino médio","code":"EM13CNT101","unit":"Ciências da Natureza","object":"Matéria e energia","summary":"Interpretar fenômenos físicos com conceitos e modelos científicos."},
    ],
    "Química": [
        {"level":"Ensino médio","code":"EM13CNT104","unit":"Ciências da Natureza","object":"Transformações da matéria","summary":"Analisar materiais, transformações, energia e segurança."},
    ],
    "História": [
        {"level":"Ensino fundamental anos iniciais","code":"EF03HI02","unit":"Mundo pessoal e social","object":"Memória e identidade","summary":"Identificar registros de memória e histórias locais."},
        {"level":"Ensino fundamental anos finais","code":"EF06HI01","unit":"Tempo, espaço e registro","object":"Fontes históricas","summary":"Compreender fontes, tempo histórico e formas de registro."},
        {"level":"Ensino médio","code":"EM13CHS101","unit":"Ciências Humanas","object":"Tempo e sociedade","summary":"Analisar processos históricos, sociais e culturais."},
    ],
    "Geografia": [
        {"level":"Ensino fundamental anos iniciais","code":"EF03GE01","unit":"Lugar e mundo","object":"Paisagem e lugar","summary":"Identificar características do lugar de vivência e paisagem."},
        {"level":"Ensino fundamental anos finais","code":"EF06GE01","unit":"Conexões e escalas","object":"Paisagem e espaço","summary":"Comparar paisagens e transformações do espaço geográfico."},
        {"level":"Ensino médio","code":"EM13CHS106","unit":"Ciências Humanas","object":"Território e sociedade","summary":"Analisar paisagens, territórios, fluxos e impactos sociais."},
    ],
    "Inglês": [
        {"level":"Ensino fundamental anos finais","code":"EF06LI01","unit":"Oralidade","object":"Interação discursiva","summary":"Usar cumprimentos, apresentações e frases simples."},
        {"level":"Ensino fundamental anos finais","code":"EF06LI04","unit":"Leitura","object":"Estratégias de leitura","summary":"Identificar palavras conhecidas e sentido geral em textos curtos."},
    ],
    "Artes": [
        {"level":"Ensino fundamental anos iniciais","code":"EF15AR01","unit":"Artes visuais","object":"Contextos e práticas","summary":"Apreciar e identificar elementos das artes visuais."},
        {"level":"Ensino fundamental anos finais","code":"EF69AR06","unit":"Artes integradas","object":"Processos de criação","summary":"Criar com diferentes materialidades e referências culturais."},
    ],
    "Educação Física": [
        {"level":"Ensino fundamental anos iniciais","code":"EF12EF01","unit":"Brincadeiras e jogos","object":"Jogos populares","summary":"Vivenciar brincadeiras e jogos respeitando regras."},
        {"level":"Ensino fundamental anos finais","code":"EF67EF01","unit":"Esportes","object":"Práticas corporais","summary":"Vivenciar e refletir sobre esportes, jogos e cooperação."},
    ],
    "Ensino Religioso": [
        {"level":"Ensino fundamental anos iniciais","code":"EF01ER01","unit":"Identidades e alteridades","object":"Convivência","summary":"Reconhecer a si, o outro, respeito e convivência."},
    ],
    "Filosofia": [
        {"level":"Ensino médio","code":"EM13CHS501","unit":"Ciências Humanas","object":"Ética e cidadania","summary":"Analisar valores, responsabilidade e convivência democrática."},
    ],
    "Sociologia": [
        {"level":"Ensino médio","code":"EM13CHS401","unit":"Ciências Humanas","object":"Sociedade e cultura","summary":"Analisar relações sociais, desigualdades, cultura e cidadania."},
    ],
    "Projeto de Vida": [
        {"level":"Ensino médio","code":"PV-ESCOLA","unit":"Projeto de Vida","object":"Autoconhecimento e planejamento","summary":"Organizar metas, rotina, escolhas e planejamento pessoal."},
    ],
}

BNCC_REVIEW_NOTICE = (
    "Atenção: os campos de BNCC são uma base editável para ajudar na organização pedagógica. "
    "Antes de vender ou aplicar, revise os códigos e descrições com o documento oficial da BNCC, "
    "o currículo do seu estado/município e a realidade da turma."
)


def get_subjects_for_product(discipline: str) -> List[str]:
    base = ["Português", "Matemática", "Ciências", "História", "Geografia", "Inglês", "Artes", "Educação Física"]
    if discipline == "Todas as disciplinas":
        return base
    return [discipline if discipline else "Português"]


def get_bncc_suggestions(discipline: str, school_level: str, limit_per_subject: int = 3) -> List[Dict[str, str]]:
    subjects = get_subjects_for_product(discipline)
    level_key = (school_level or "").lower()
    suggestions: List[Dict[str, str]] = []
    for subject in subjects:
        items = BNCC_SKILLS.get(subject, [])
        filtered = [i for i in items if (i.get("level", "").lower() in level_key or level_key in i.get("level", "").lower())]
        if not filtered:
            filtered = items
        for item in filtered[:limit_per_subject]:
            suggestions.append({"subject": subject, **item})
    return suggestions


def bncc_map_markdown(discipline: str, school_level: str) -> str:
    rows = get_bncc_suggestions(discipline, school_level, limit_per_subject=6)
    lines = [
        "# Mapa BNCC do produto", "",
        f"Disciplina selecionada: {discipline}",
        f"Ano/Nível: {school_level}", "",
        BNCC_REVIEW_NOTICE, "",
        "| Disciplina | Código | Unidade/Campo | Objeto | Resumo pedagógico editável |",
        "|---|---|---|---|---|",
    ]
    if not rows:
        lines.append("| Revisar | BNCC | Inserir unidade | Inserir objeto | Inserir habilidade conforme currículo local |")
    for r in rows:
        lines.append(f"| {r.get('subject','')} | {r.get('code','')} | {r.get('unit','')} | {r.get('object','')} | {r.get('summary','')} |")
    lines += ["", "## Checklist de conformidade BNCC",
        "- [ ] O ano/série da atividade foi definido.",
        "- [ ] A habilidade está coerente com a disciplina e a turma.",
        "- [ ] O objetivo da atividade conversa com a habilidade escolhida.",
        "- [ ] O enunciado está adequado à faixa etária.",
        "- [ ] O gabarito e a orientação do professor estão separados da folha do aluno.",
        "- [ ] O material foi revisado antes de ser anunciado como alinhado à BNCC."]
    return "\n".join(lines)


def format_bncc_block(subject: str, school_level: str, skill: Dict[str, str], activity_title: str, activity_goal: str) -> List[str]:
    return [
        f"**Disciplina:** {subject}",
        f"**Etapa/Ano:** {school_level}",
        f"**Unidade temática/Campo:** {skill.get('unit', 'Revisar conforme BNCC')}",
        f"**Objeto de conhecimento:** {skill.get('object', 'Revisar conforme BNCC')}",
        f"**Habilidade BNCC:** {skill.get('code', 'Inserir código BNCC')} — {skill.get('summary', 'Inserir descrição resumida da habilidade.')}",
        f"**Objetivo de aprendizagem:** {activity_goal}",
        f"**Atividade:** {activity_title}",
        "**Tempo sugerido:** 30 a 50 minutos, ajustável conforme a turma.",
        "**Materiais:** folha impressa, lápis/caneta e recurso complementar opcional.",
    ]



def build_prompt_pack(title: str, niche: str, product_type: str, target: str, pages: int, promise: str, discipline: str = "Todas as disciplinas", school_level: str = "Ensino fundamental") -> str:
    bncc_rows = get_bncc_suggestions(discipline, school_level, limit_per_subject=4)
    bncc_text = "\n".join([f"- {r.get('subject')}: {r.get('code')} | {r.get('unit')} | {r.get('object')} | {r.get('summary')}" for r in bncc_rows]) or "- Inserir habilidades conforme ano e currículo local."
    return f"""
PROMPT 1 — Criar produto educacional completo com estrutura BNCC
Crie um {product_type} chamado "{title}".
Nicho: {niche}.
Disciplina: {discipline}.
Ano/nível: {school_level}.
Público-alvo: {target}.
Objetivo do produto: {promise}.
Quantidade aproximada: {pages} páginas.
Escreva com linguagem simples, brasileira, clara e profissional.
Não prometa aprovação, nota garantida, resultado milagroso ou dinheiro fácil.

Use esta matriz BNCC como referência editável. Revise antes de vender:
{bncc_text}

Estrutura obrigatória para cada atividade:
1. Disciplina
2. Etapa/ano
3. Unidade temática/campo de atuação
4. Objeto de conhecimento
5. Habilidade BNCC com código
6. Objetivo de aprendizagem
7. Folha do aluno pronta para imprimir
8. Orientação para o professor
9. Gabarito separado e comentado
10. Avaliação rápida
11. Adaptação para alunos com dificuldade
12. Observação de revisão BNCC/currículo local

Se a disciplina for "Todas as disciplinas", crie blocos separados para Português, Matemática, Ciências, História, Geografia, Inglês, Artes e Educação Física. Gere também um mapa BNCC no início do material.

PROMPT 2 — Página de venda honesta
Crie uma página de venda para o produto "{title}" com título, subtítulo, para quem é, o que recebe, benefícios, bônus, como usar, aviso de revisão pedagógica/BNCC, garantia honesta, perguntas frequentes e chamada para ação.

PROMPT 3 — Divulgação
Crie 15 legendas para Instagram/Facebook, 10 mensagens para WhatsApp e 10 roteiros curtos para TikTok/Reels para vender "{title}". Não diga que é material oficial do MEC; diga que é material editável e organizado com campos BNCC para revisão.
""".strip()


def template_content(title: str, niche: str, product_type: str, target: str, pages: int, promise: str, discipline: str = "Todas as disciplinas", school_level: str = "Ensino fundamental") -> str:
    today = date.today().strftime("%d/%m/%Y")
    activity_bank = {
        "Português": [("Leitura e interpretação", "Ler um texto curto, localizar informações explícitas, identificar tema e responder questões de compreensão."), ("Produção textual", "Planejar e produzir um texto curto com começo, meio e fim."), ("Gramática em contexto", "Identificar palavras, pontuação e recursos linguísticos em frases reais.")],
        "Matemática": [("Situações-problema", "Resolver problemas com dados do cotidiano, registrar estratégia e conferir resultado."), ("Cálculo e raciocínio", "Realizar cálculos com operações adequadas ao nível e explicar o caminho utilizado."), ("Grandezas e medidas", "Interpretar medidas, tempo, dinheiro, tabelas simples e situações práticas.")],
        "Ciências": [("Investigação científica", "Observar fenômeno, levantar hipótese, registrar dados e apresentar conclusão."), ("Seres vivos e ambiente", "Relacionar seres vivos, ambiente, alimentação, preservação e equilíbrio."), ("Corpo humano e saúde", "Reconhecer cuidados com saúde, higiene, alimentação e prevenção.")],
        "História": [("Tempo e memória", "Organizar acontecimentos em linha do tempo e comparar passado e presente."), ("Fontes históricas", "Analisar imagens, relatos e objetos como fontes de informação histórica."), ("Cultura e sociedade", "Comparar costumes, trabalho, moradia e convivência social.")],
        "Geografia": [("Lugar e paisagem", "Observar elementos naturais e humanos da paisagem e descrever transformações do espaço."), ("Mapas e localização", "Usar legenda, pontos de referência e leitura simples de mapas."), ("Ambiente e sociedade", "Relacionar consumo, água, lixo, transporte e cuidado com o lugar onde vive.")],
        "Inglês": [("Vocabulário contextualizado", "Praticar saudações, números, cores, objetos, família e expressões de uso cotidiano."), ("Leitura curta", "Ler frases simples e identificar palavras conhecidas pelo contexto."), ("Diálogo guiado", "Completar diálogos curtos com cumprimentos e perguntas básicas.")],
        "Artes": [("Leitura de imagem", "Observar cores, formas, linhas, personagens e sentimentos presentes em uma imagem."), ("Produção artística", "Criar desenho, colagem, cartaz ou composição visual a partir de um tema."), ("Cultura visual", "Relacionar arte, cultura local, festas, símbolos e expressões do cotidiano.")],
        "Educação Física": [("Jogos e brincadeiras", "Vivenciar uma brincadeira, explicar regras, respeitar colegas e registrar o que aprendeu."), ("Movimento e corpo", "Praticar atividade com coordenação, segurança, cooperação e consciência corporal."), ("Saúde e convivência", "Discutir hábitos saudáveis, respeito, participação e trabalho em equipe.")],
    }
    selected_subjects = get_subjects_for_product(discipline)
    body = [
        f"# {title}", f"**Tipo:** {product_type}", f"**Nicho:** {niche}", f"**Disciplina:** {discipline}", f"**Ano/Nível:** {school_level}", f"**Público-alvo:** {target}", f"**Criado em:** {today}", "",
        "## Apresentação profissional", f"Este material foi criado para ajudar {target.lower()} com uma solução prática: {promise}. A estrutura foi organizada para facilitar uso em sala, reforço escolar e venda como produto digital revisável.", "",
        "## Padrão pedagógico usado", "Cada atividade traz: disciplina, etapa/ano, unidade temática, objeto de conhecimento, habilidade BNCC, objetivo, folha do aluno, orientação, gabarito, avaliação e adaptação.", "",
        "## Aviso de revisão BNCC", BNCC_REVIEW_NOTICE, "",
        "## Mapa BNCC do material", "| Disciplina | Código | Unidade/Campo | Objeto | Resumo pedagógico editável |", "|---|---|---|---|---|",
    ]
    for r in get_bncc_suggestions(discipline, school_level, limit_per_subject=4):
        body.append(f"| {r.get('subject','')} | {r.get('code','')} | {r.get('unit','')} | {r.get('object','')} | {r.get('summary','')} |")
    body += ["", "## Como usar este material", "1. Escolha a disciplina e a atividade desejada.", "2. Confira o ano/série e revise a habilidade BNCC indicada.", "3. Imprima apenas a folha do aluno.", "4. Use a orientação do professor para conduzir a aula.", "5. Corrija com o gabarito separado.", "6. Adapte a dificuldade conforme a turma.", "", "## Sumário"]
    for i, subj in enumerate(selected_subjects, 1):
        body.append(f"{i}. {subj} — atividades, BNCC, orientações, gabarito e avaliação")
    body.extend(["", "---", ""])
    for i, subj in enumerate(selected_subjects, 1):
        skills = get_bncc_suggestions(subj, school_level, limit_per_subject=6) or [{"subject": subj, "code": "Inserir código BNCC", "unit": "Revisar", "object": "Revisar", "summary": "Inserir habilidade conforme currículo local."}]
        activities = activity_bank.get(subj) or [("Atividade principal", f"Desenvolver uma atividade de {subj} adequada ao nível informado."), ("Revisão", f"Revisar conteúdos de {subj} com perguntas objetivas."), ("Aplicação", f"Aplicar conceitos de {subj} em situação prática.")]
        body.append(f"## {i}. {subj}")
        body.append(f"**Objetivo do bloco:** oferecer atividades de {subj} para {school_level.lower()}, com campos BNCC, linguagem clara, aplicação rápida e gabarito separado.")
        body.append("")
        for n, (topic, instruction) in enumerate(activities, 1):
            skill = skills[(n - 1) % len(skills)]
            body.append(f"### Atividade {n} — {topic}")
            body.extend(format_bncc_block(subj, school_level, skill, topic, instruction))
            body += ["", "#### Folha do aluno", "**Nome:** __________________________________________  **Data:** ____/____/______", f"**Turma:** ____________________  **Disciplina:** {subj}", "", f"**Leia com atenção:** {instruction}", "", "1. Explique com suas palavras o que você entendeu da atividade.", "Resposta: ________________________________________________________________", "", "2. Resolva a situação proposta ou produza o registro solicitado pelo professor.", "Resposta: ________________________________________________________________", "", "3. Escreva uma conclusão curta sobre o que aprendeu.", "Resposta: ________________________________________________________________", "", "#### Orientação para o professor", "- Apresente o objetivo antes da atividade e verifique conhecimentos prévios.", "- Leia o enunciado com a turma e destaque palavras-chave.", "- Permita adaptação do tempo e apoio individual quando necessário.", "- Use as respostas para planejar revisão ou aprofundamento.", "", "#### Gabarito comentado", "- Questão 1: espera-se compreensão do comando e registro coerente.", "- Questão 2: avaliar procedimento, organização, uso de conceitos da disciplina e adequação à proposta.", "- Questão 3: resposta pessoal, com síntese do aprendizado e vocabulário adequado ao nível.", "", "#### Avaliação rápida", "Critérios: participação, compreensão do enunciado, coerência da resposta, organização e avanço em relação à habilidade indicada.", "", "#### Adaptação e inclusão", "Para alunos com dificuldade, reduza itens, leia o comando em voz alta, permita resposta oral antes da escrita e ofereça exemplos guiados.", ""]
        body.extend(["---", ""])
    body += ["## Bônus 1: modelo de planejamento semanal", "- Disciplina:", "- Ano/turma:", "- Tema da semana:", "- Habilidade BNCC:", "- Objetivo da aula:", "- Atividade principal:", "- Forma de avaliação:", "- Adaptações necessárias:", "- Observações da turma:", "", "## Bônus 2: checklist BNCC antes de vender", "- [ ] Revisei os códigos de habilidade.", "- [ ] Verifiquei se a atividade realmente trabalha a habilidade indicada.", "- [ ] Separei folha do aluno, professor e gabarito.", "- [ ] Retirei promessas exageradas da página de venda.", "- [ ] Testei o PDF no celular e no computador.", "- [ ] Ajustei linguagem e dificuldade para o ano/série.", "", "## Conclusão", "Produto educacional vendável precisa ser útil, claro, revisável e honesto. Este material foi estruturado com aparência profissional e campos BNCC editáveis."]
    return "\n".join(body)


def template_sales_page(title: str, niche: str, product_type: str, target: str, price: float, promise: str, discipline: str = "Todas as disciplinas", school_level: str = "Ensino fundamental") -> str:
    return f"""
# Página de Venda — {title}

## Título
{title}: material escolar pronto para usar, adaptar e economizar tempo.

## Subtítulo
Um {product_type.lower()} para {target.lower()}, com foco em {discipline.lower()} e nível {school_level.lower()}.

## Para quem é
Este produto é indicado para professores, escolas pequenas, reforço escolar, pais, tutores e criadores de material pedagógico que precisam de uma solução prática no nicho de {niche.lower()}.

## O que você recebe
- Produto digital pronto para baixar.
- Atividades organizadas por disciplina/tema.
- Mapa BNCC editável por atividade.
- Folha do aluno.
- Orientações para o professor.
- Gabarito comentado.
- Campo de avaliação e adaptação.
- Checklist de aplicação.
- Modelo de planejamento.
- Textos editáveis para adaptar.

## Benefícios
- Economiza tempo na preparação de aulas e atividades.
- Ajuda a montar provas, revisões, tarefas e reforço.
- Pode ser adaptado para diferentes turmas.
- Linguagem simples e objetiva.
- Funciona como base para criar novos materiais.

## Promessa honesta
Este material ajuda você a {promise.lower()}. Ele não promete nota garantida nem resultado automático; a qualidade final depende de revisão, adaptação e aplicação correta.

## Preço sugerido
{money(price)}

## Chamada para ação
Clique no botão de compra e receba o material digital para começar a usar e adaptar hoje.

## Aviso pedagógico/BNCC
O material é organizado com campos BNCC editáveis. Antes de aplicar ou vender como alinhado à BNCC, revise os códigos e adapte ao currículo local.

## Perguntas frequentes
**Recebo na hora?** Sim, a entrega pode ser feita automaticamente pela plataforma de venda.

**Posso editar?** Sim, ofereça junto uma versão editável sempre que possível.

**Serve para qualquer turma?** O material é uma base prática. O ideal é adaptar para o ano, nível e realidade da turma.

**É para uma disciplina só?** Depende do pacote escolhido. Você pode vender versão de uma disciplina, por bimestre ou um mega kit com várias disciplinas.

**Vem separado por disciplina?** No pacote de lançamento, o sistema gera o mega kit completo e também arquivos por disciplina quando o produto for de todas as disciplinas.
""".strip()


def template_social_posts(title: str, target: str, niche: str, promise: str, discipline: str = "Todas as disciplinas", school_level: str = "Ensino fundamental") -> str:
    captions = [
        f"Professor, você perde muito tempo montando atividade do zero? O {title} foi criado para ajudar {target.lower()} a {promise.lower()}.",
        f"Material pronto para {discipline.lower()} no nível {school_level.lower()}: folha do aluno, orientação e gabarito em um só pacote.",
        f"Pare de começar sempre do zero. Com o {title}, você recebe uma base organizada para adaptar e usar.",
        f"Atividades prontas, gabarito e planejamento em um material simples. Conheça o {title}.",
        f"Se você trabalha com escola, reforço ou aulas particulares, esse kit pode economizar horas de preparação.",
        f"Mostrando por dentro: o {title} vem com blocos prontos para aplicar e adaptar conforme sua turma.",
        f"Pais e professores: material digital prático para reforçar aprendizagem sem complicação.",
        f"Quer uma amostra do {title}? Me chama que eu envio um exemplo do material.",
    ]

    whatsapp = [
        f"Oi! Estou disponibilizando o {title}, um material digital para {target.lower()}. Ele ajuda a {promise.lower()} e já vem com estrutura organizada. Posso te mandar o link?",
        f"Novo material pronto: {title}. Ideal para professores, reforço escolar e pais que querem atividades organizadas. Valor acessível e entrega digital.",
        f"Tenho um produto digital que pode ajudar com {discipline.lower()}: {title}. Ele é simples, prático e fácil de adaptar. Quer ver uma amostra?",
        f"Bom dia! Preparei um kit escolar com folha do aluno, orientação e gabarito. Se fizer sentido para você, posso enviar o link.",
    ]

    scripts = [
        f"Roteiro TikTok 1: 'Você ainda monta atividade do zero? Eu criei o {title} para facilitar sua rotina. Vem com folha do aluno, orientação e gabarito.'",
        f"Roteiro TikTok 2: 'Três coisas que esse material resolve: falta de tempo, falta de organização e dificuldade para preparar revisão. O nome é {title}.'",
        f"Roteiro TikTok 3: 'Mostrando por dentro: esse é o {title}. Ele foi feito para {target.lower()} que precisam de praticidade.'",
        f"Roteiro TikTok 4: 'Professor, salve horas de trabalho com um material pronto para adaptar. Olha esse exemplo de atividade.'",
        f"Roteiro TikTok 5: 'Pais que ajudam os filhos em casa também podem usar esse material como reforço simples.'",
    ]

    return "\n".join([
        "# Posts e mensagens de divulgação",
        "",
        "## Legendas para Instagram/Facebook",
        *[f"{i+1}. {c}" for i, c in enumerate(captions)],
        "",
        "## Mensagens para WhatsApp",
        *[f"{i+1}. {w}" for i, w in enumerate(whatsapp)],
        "",
        "## Roteiros curtos para TikTok/Reels",
        *[f"{i+1}. {s}" for i, s in enumerate(scripts)],
    ])



# ----------------------------- Multinichos profissionais -----------------------------

EDU_KEYWORDS = [
    "educação", "ensino", "escolar", "professor", "professores", "bncc", "enem",
    "fundamental", "médio", "disciplina", "questões", "simulado", "reforço", "aluno", "aula"
]

GENERAL_CONTENT_MODELS = {
    "IA para pequenos negócios": {
        "modules": ["Diagnóstico rápido do negócio", "Prompts prontos para atendimento", "Prompts para posts e anúncios", "Roteiros de Reels e Shorts", "Checklist semanal de execução", "Calendário de 30 dias"],
        "examples": ["prompt para responder cliente indeciso", "roteiro de vídeo mostrando produto", "mensagem de pós-venda", "ideia de promoção honesta"],
        "warning": "Use os prompts como apoio. Revise tudo antes de publicar e não automatize spam.",
    },
    "Finanças pessoais e organização": {
        "modules": ["Diagnóstico financeiro simples", "Controle de entradas e saídas", "Planejamento de dívidas", "Metas mensais", "Desafio de economia", "Revisão semanal"],
        "examples": ["lista de gastos fixos", "quadro de dívidas", "meta de reserva", "checklist de compras conscientes"],
        "warning": "Material educativo de organização. Não é consultoria financeira, investimento ou promessa de enriquecimento.",
    },
    "Pequenos negócios e MEI": {
        "modules": ["Organização do negócio", "Precificação simples", "Controle de clientes", "Atendimento no WhatsApp", "Divulgação semanal", "Indicadores básicos"],
        "examples": ["modelo de cadastro de cliente", "ficha de produto", "mensagem de orçamento", "checklist de venda do dia"],
        "warning": "Adapte os modelos à realidade do negócio e às regras fiscais/contábeis locais.",
    },
    "Beleza, estética e atendimento": {
        "modules": ["Agenda de clientes", "Ficha de atendimento", "Mensagens para WhatsApp", "Posts de antes/depois", "Pacotes e promoções", "Pós-atendimento"],
        "examples": ["mensagem de confirmação", "modelo de ficha simples", "checklist de materiais", "roteiro para divulgar horários"],
        "warning": "Não substitui orientação profissional, técnica, sanitária ou regulamentar da área.",
    },
    "Casa, organização e rotina": {
        "modules": ["Rotina semanal", "Lista de limpeza", "Cardápio da semana", "Lista de compras", "Organização por cômodo", "Planejamento familiar"],
        "examples": ["checklist da cozinha", "cronograma de limpeza", "cardápio econômico", "lista de tarefas por dia"],
        "warning": "Modelo de organização pessoal. Adapte conforme sua casa, renda e rotina.",
    },
    "Culinária, marmitas e confeitaria": {
        "modules": ["Planejamento de receitas", "Lista de ingredientes", "Ficha técnica", "Precificação simples", "Cardápio de venda", "Divulgação e entrega"],
        "examples": ["ficha de bolo", "cardápio semanal de marmitas", "controle de encomendas", "mensagem de venda por WhatsApp"],
        "warning": "Respeite normas de higiene, validade, alergênicos e legislação local para alimentos.",
    },
    "Carreira, currículo e renda extra": {
        "modules": ["Diagnóstico de perfil", "Modelo de currículo", "Carta de apresentação", "Roteiro de entrevista", "Plano de busca de vagas", "Checklist de melhoria"],
        "examples": ["currículo simples", "bio profissional", "respostas de entrevista", "rotina de candidaturas"],
        "warning": "Material educativo. Não promete emprego, aprovação ou renda garantida.",
    },
    "Templates, design e redes sociais": {
        "modules": ["Identidade simples", "Calendário de conteúdo", "Legendas prontas", "Roteiros de vídeo", "Checklist de publicação", "Métricas básicas"],
        "examples": ["bio do Instagram", "legenda de promoção", "roteiro para Reels", "checklist de carrossel"],
        "warning": "Os modelos devem ser adaptados à marca. Evite copiar marcas, imagens ou promessas de terceiros.",
    },
    "Pets e rotina de cuidados": {
        "modules": ["Rotina do pet", "Controle de gastos", "Agenda de cuidados", "Checklist de passeio", "Checklist de viagem", "Observações importantes"],
        "examples": ["agenda de banho", "lista de compras pet", "controle de vacina a conferir", "rotina de alimentação"],
        "warning": "Material de organização para tutores. Não substitui veterinário ou orientação técnica.",
    },
}


def is_education_product(niche: str = "", product_type: str = "", discipline: str = "", school_level: str = "") -> bool:
    text = f"{niche} {product_type} {discipline} {school_level}".lower()
    if "não se aplica" in text and not any(k in text for k in ["bncc", "escolar", "professor", "ensino"]):
        return False
    return any(k in text for k in EDU_KEYWORDS)


def general_model_for(niche: str) -> Dict[str, Any]:
    if niche in GENERAL_CONTENT_MODELS:
        return GENERAL_CONTENT_MODELS[niche]
    return {
        "modules": ["Diagnóstico do público", "Passo a passo principal", "Modelos prontos", "Checklist de aplicação", "Calendário de execução", "Próximos passos"],
        "examples": ["modelo prático", "checklist editável", "roteiro simples", "plano semanal"],
        "warning": "Material educativo e editável. Revise antes de vender, aplicar ou publicar.",
    }


def build_prompt_pack_general(title: str, niche: str, product_type: str, target: str, pages: int, promise: str, discipline: str = "Não se aplica / produto geral", school_level: str = "Público geral") -> str:
    model = general_model_for(niche)
    modules = "\n".join([f"- {m}" for m in model["modules"]])
    examples = "\n".join([f"- {e}" for e in model["examples"]])
    return f"""
PROMPT 1 — Criar produto digital vendável e realista
Crie um {product_type} chamado "{title}".
Nicho: {niche}.
Público-alvo: {target}.
Objetivo do produto: {promise}.
Quantidade aproximada: {pages} páginas.
Linguagem: brasileira, simples, direta, profissional e sem promessas milagrosas.

Estrutura recomendada:
{modules}

Exemplos que devem aparecer no produto:
{examples}

Regras:
1. Criar capa textual, apresentação, sumário e instruções de uso.
2. Gerar modelos prontos para copiar, preencher ou adaptar.
3. Criar checklist final de aplicação.
4. Criar uma amostra grátis de 1 a 3 páginas.
5. Não prometer dinheiro fácil, cura, resultado financeiro garantido, emprego garantido, emagrecimento garantido ou solução mágica.
6. Incluir aviso responsável: {model['warning']}

PROMPT 2 — Página de venda honesta
Crie uma página de venda para "{title}" com título, subtítulo, problema do público, o que recebe, benefícios, bônus, garantia honesta, FAQ e chamada para ação.

PROMPT 3 — Divulgação
Crie 20 legendas, 10 mensagens de WhatsApp e 10 roteiros curtos de Reels/TikTok/Shorts para vender "{title}" sem spam e sem promessa exagerada.
""".strip()


def template_content_general(title: str, niche: str, product_type: str, target: str, pages: int, promise: str, discipline: str = "Não se aplica / produto geral", school_level: str = "Público geral") -> str:
    today = date.today().strftime("%d/%m/%Y")
    model = general_model_for(niche)
    body = [
        f"# {title}",
        f"**Tipo:** {product_type}",
        f"**Nicho:** {niche}",
        f"**Público-alvo:** {target}",
        f"**Criado em:** {today}",
        "",
        "## Apresentação profissional",
        f"Este produto digital foi organizado para ajudar {target.lower()} com uma solução prática: {promise}.",
        "A proposta é entregar um material claro, editável e fácil de usar, sem depender de conhecimento técnico avançado.",
        "",
        "## Aviso importante",
        model["warning"],
        "",
        "## O que vem neste produto",
    ]
    for m in model["modules"]:
        body.append(f"- {m}")
    body += ["", "## Como usar", "1. Leia a apresentação e identifique sua necessidade principal.", "2. Escolha o modelo ou checklist mais útil para sua situação.", "3. Preencha os campos com seus dados reais.", "4. Revise a linguagem antes de enviar, publicar ou usar com clientes.", "5. Guarde uma cópia e atualize semanalmente.", "", "---", ""]

    for idx, module in enumerate(model["modules"], 1):
        example = model["examples"][(idx - 1) % len(model["examples"])]
        body += [
            f"## {idx}. {module}",
            f"**Objetivo:** ajudar {target.lower()} a aplicar {module.lower()} de forma prática.",
            "",
            "### Modelo pronto",
            f"Use este modelo como base para: {example}.",
            "",
            "**Situação:** ________________________________________________",
            "**O que preciso resolver:** ___________________________________",
            "**Passo 1:** _________________________________________________",
            "**Passo 2:** _________________________________________________",
            "**Passo 3:** _________________________________________________",
            "**Resultado esperado:** _______________________________________",
            "",
            "### Exemplo preenchido",
            f"Exemplo: uma pessoa do público {target.lower()} pode usar este bloco para organizar, vender melhor, economizar tempo ou divulgar com mais clareza, conforme a proposta do produto.",
            "",
            "### Checklist rápido",
            "- [ ] Entendi o objetivo deste bloco.",
            "- [ ] Preenchi com informações reais.",
            "- [ ] Revisei antes de usar.",
            "- [ ] Adaptei para minha realidade.",
            "",
            "---",
            "",
        ]
    body += [
        "## Bônus 1: plano de 7 dias",
        "**Dia 1:** organize seus dados principais.",
        "**Dia 2:** escolha o primeiro modelo para aplicar.",
        "**Dia 3:** revise e personalize.",
        "**Dia 4:** publique, envie ou use o material.",
        "**Dia 5:** anote dúvidas e melhorias.",
        "**Dia 6:** teste uma segunda versão.",
        "**Dia 7:** avalie resultados e ajuste o próximo passo.",
        "",
        "## Bônus 2: checklist final",
        "- [ ] O material está com seu nome/marca quando necessário.",
        "- [ ] As informações estão corretas.",
        "- [ ] Não há promessa exagerada.",
        "- [ ] O arquivo abre no celular e computador.",
        "- [ ] O uso está claro para o comprador.",
        "",
        "## Conclusão",
        "Produto digital vendável precisa resolver um problema simples, ser fácil de usar e parecer confiável. Revise, melhore e use feedback real dos compradores.",
    ]
    return "\n".join(body)


def template_sales_page_general(title: str, niche: str, product_type: str, target: str, price: float, promise: str, discipline: str = "Não se aplica / produto geral", school_level: str = "Público geral") -> str:
    model = general_model_for(niche)
    modules = "\n".join([f"- {m}" for m in model["modules"]])
    return f"""
# Página de Venda — {title}

## Título
{title}: material digital prático para organizar, aplicar e economizar tempo.

## Subtítulo
Um {product_type.lower()} para {target.lower()} que querem {promise.lower()}.

## Para quem é
Este produto é indicado para {target.lower()} que precisam de uma solução simples, pronta para adaptar e usar no dia a dia.

## O que você recebe
{modules}
- Amostra grátis para conhecer por dentro.
- Checklist de aplicação.
- Modelos editáveis em texto.
- Orientação de uso sem complicação.

## Benefícios
- Evita começar do zero.
- Ajuda a organizar rotina, atendimento, conteúdo ou planejamento.
- Linguagem simples e direta.
- Pode ser adaptado para sua realidade.
- Produto digital com entrega rápida pela plataforma escolhida.

## Promessa honesta
Este material ajuda você a {promise.lower()}. Ele não promete resultado automático; o resultado depende de aplicação, adaptação e consistência.

## Preço sugerido
{money(price)}

## Chamada para ação
Clique no botão de compra, baixe o material e comece pela primeira página do passo a passo.

## Aviso responsável
{model['warning']}

## Perguntas frequentes
**Recebo na hora?** Sim, a entrega pode ser feita automaticamente pela plataforma de venda.

**Posso editar?** Sim, os textos e modelos foram feitos para adaptação.

**Serve para iniciantes?** Sim. A linguagem é simples e o uso é guiado.

**Tem garantia?** Use a política da plataforma escolhida e deixe isso claro no checkout.

**É resultado garantido?** Não. O produto é uma ferramenta prática; o resultado depende de execução e contexto.
""".strip()


def template_social_posts_general(title: str, target: str, niche: str, promise: str, discipline: str = "Não se aplica / produto geral", school_level: str = "Público geral") -> str:
    captions = [
        f"Você ainda começa tudo do zero? O {title} foi criado para ajudar {target.lower()} a {promise.lower()}.",
        f"Material digital pronto, simples e editável para quem precisa organizar melhor a rotina no nicho de {niche.lower()}.",
        f"Mostrando por dentro: o {title} vem com modelos, checklists e passo a passo para adaptar.",
        f"Uma solução simples para economizar tempo e deixar tudo mais profissional: {title}.",
        f"Se você trabalha com {niche.lower()}, esse material pode facilitar sua rotina sem complicação.",
        f"Antes: começar do zero. Depois: usar um modelo pronto e adaptar. Esse é o objetivo do {title}.",
        f"Quer uma amostra grátis do {title}? Cadastre-se na página e veja por dentro.",
        f"Produto digital não precisa ser complicado. O {title} foi feito para uso prático e direto.",
    ]
    whatsapp = [
        f"Oi! Preparei o {title}, um material digital para {target.lower()}. Ele ajuda a {promise.lower()}. Quer que eu te mande uma amostra?",
        f"Tenho um kit pronto no nicho de {niche.lower()}: {title}. É digital, simples e fácil de adaptar. Posso te enviar o link?",
        f"Se você quer economizar tempo, esse material pode ajudar: {title}. Ele vem com modelos e checklist para usar no dia a dia.",
        f"Estou divulgando uma amostra grátis do {title}. Se fizer sentido para você, posso mandar o link.",
    ]
    scripts = [
        f"Roteiro 1: 'Você perde tempo criando tudo do zero? Eu preparei o {title} para facilitar sua rotina.'",
        f"Roteiro 2: 'Três coisas que esse material resolve: organização, clareza e rapidez. O nome é {title}.'",
        f"Roteiro 3: 'Mostrando por dentro: esse é o {title}. Ele foi feito para {target.lower()}.'",
        f"Roteiro 4: 'Antes e depois: antes você improvisava; agora usa um modelo pronto e adapta.'",
        f"Roteiro 5: 'Quer testar antes? Baixe a amostra grátis e veja se serve para você.'",
    ]
    return "\n".join([
        "# Posts e mensagens de divulgação",
        "",
        "## Legendas para Instagram/Facebook",
        *[f"{i+1}. {c}" for i, c in enumerate(captions)],
        "",
        "## Mensagens para WhatsApp",
        *[f"{i+1}. {w}" for i, w in enumerate(whatsapp)],
        "",
        "## Roteiros curtos para TikTok/Reels",
        *[f"{i+1}. {r}" for i, r in enumerate(scripts)],
    ])


# Guarda as versões educacionais originais e redefine as funções principais com suporte a produto geral.
_build_prompt_pack_educacional = build_prompt_pack
_template_content_educacional = template_content
_template_sales_page_educacional = template_sales_page
_template_social_posts_educacional = template_social_posts


def build_prompt_pack(title: str, niche: str, product_type: str, target: str, pages: int, promise: str, discipline: str = "Todas as disciplinas", school_level: str = "Ensino fundamental") -> str:
    if is_education_product(niche, product_type, discipline, school_level):
        return _build_prompt_pack_educacional(title, niche, product_type, target, pages, promise, discipline, school_level)
    return build_prompt_pack_general(title, niche, product_type, target, pages, promise, discipline, school_level)


def template_content(title: str, niche: str, product_type: str, target: str, pages: int, promise: str, discipline: str = "Todas as disciplinas", school_level: str = "Ensino fundamental") -> str:
    if is_education_product(niche, product_type, discipline, school_level):
        return _template_content_educacional(title, niche, product_type, target, pages, promise, discipline, school_level)
    return template_content_general(title, niche, product_type, target, pages, promise, discipline, school_level)


def template_sales_page(title: str, niche: str, product_type: str, target: str, price: float, promise: str, discipline: str = "Todas as disciplinas", school_level: str = "Ensino fundamental") -> str:
    if is_education_product(niche, product_type, discipline, school_level):
        return _template_sales_page_educacional(title, niche, product_type, target, price, promise, discipline, school_level)
    return template_sales_page_general(title, niche, product_type, target, price, promise, discipline, school_level)


def template_social_posts(title: str, target: str, niche: str, promise: str, discipline: str = "Todas as disciplinas", school_level: str = "Ensino fundamental") -> str:
    if is_education_product(niche, "", discipline, school_level):
        return _template_social_posts_educacional(title, target, niche, promise, discipline, school_level)
    return template_social_posts_general(title, target, niche, promise, discipline, school_level)

def optional_openai_generate(title: str, niche: str, product_type: str, target: str, pages: int, promise: str, discipline: str = "Todas as disciplinas", school_level: str = "Ensino fundamental") -> Optional[Dict[str, str]]:
    """Geração automática opcional. Se não houver chave, retorna None e usamos templates locais."""
    if not OPENAI_API_KEY:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        prompt = build_prompt_pack(title, niche, product_type, target, pages, promise, discipline, school_level)
        response = client.responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            input=(
                "Você é um especialista em produtos digitais vendáveis no Brasil, incluindo educação, pequenos negócios, IA, organização, templates, beleza, culinária, carreira e finanças pessoais educativas. "
                "Crie conteúdo útil, claro, honesto e adaptável para o público indicado. "
                "Não invente códigos específicos de currículo e não prometa resultado garantido.\n\n" + prompt
            ),
        )
        text = response.output_text
        return {
            "content": text,
            "sales_page": template_sales_page(title, niche, product_type, target, 29.90, promise, discipline, school_level),
            "social_posts": template_social_posts(title, target, niche, promise, discipline, school_level),
            "prompt_pack": prompt,
        }
    except Exception as exc:
        app.logger.exception("Falha na geração OpenAI: %s", exc)
        return None


def generate_product_assets(form: Dict[str, Any]) -> Dict[str, str]:
    title = form.get("title") or "Produto Digital Escolar"
    niche = form.get("niche") or "Educação - todas as disciplinas"
    product_type = form.get("product_type") or "Mega kit de atividades"
    discipline = form.get("discipline") or "Todas as disciplinas"
    school_level = form.get("school_level") or "Ensino fundamental anos iniciais"
    target = form.get("target_audience") or "professores, reforço escolar, escolas e pais"
    promise = form.get("promise") or "economizar tempo com atividades, provas, gabaritos e materiais prontos para adaptar"
    pages = int(form.get("pages") or 30)
    price = safe_float(form.get("price"), 47.00)

    ai = optional_openai_generate(title, niche, product_type, target, pages, promise, discipline, school_level)
    if ai:
        ai["sales_page"] = template_sales_page(title, niche, product_type, target, price, promise, discipline, school_level)
        return ai
    return {
        "content": template_content(title, niche, product_type, target, pages, promise, discipline, school_level),
        "sales_page": template_sales_page(title, niche, product_type, target, price, promise, discipline, school_level),
        "social_posts": template_social_posts(title, target, niche, promise, discipline, school_level),
        "prompt_pack": build_prompt_pack(title, niche, product_type, target, pages, promise, discipline, school_level),
    }


def get_product(product_id: int):
    conn = db_conn()
    row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    conn.close()
    return row_to_dict(row)


def make_pdf(product: Dict[str, Any], section: str = "content") -> Path:
    if not REPORTLAB_OK:
        raise RuntimeError("ReportLab não está instalado. Rode: pip install reportlab")
    title = product["title"]
    content = product.get(section) or "Sem conteúdo."
    filename = f"{slugify(title)}-{section}.pdf"
    out = EXPORT_DIR / filename

    doc = SimpleDocTemplate(
        str(out),
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title=title,
        author=APP_NAME,
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="CenterTitle", parent=styles["Title"], alignment=TA_CENTER, fontSize=20, leading=24, spaceAfter=18))
    styles.add(ParagraphStyle(name="SmallMuted", parent=styles["Normal"], fontSize=9, leading=12, textColor="#555555"))
    story = [Paragraph(title, styles["CenterTitle"]), Spacer(1, 0.2 * cm)]
    for line in content.splitlines():
        clean = line.strip()
        if not clean:
            story.append(Spacer(1, 0.15 * cm))
            continue
        clean_html = clean.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if clean.startswith("# "):
            story.append(Spacer(1, 0.3 * cm))
            story.append(Paragraph(clean_html[2:], styles["Heading1"]))
        elif clean.startswith("## "):
            story.append(Spacer(1, 0.2 * cm))
            story.append(Paragraph(clean_html[3:], styles["Heading2"]))
        elif clean.startswith("### "):
            story.append(Paragraph(clean_html[4:], styles["Heading3"]))
        elif clean.startswith("- "):
            story.append(Paragraph("• " + clean_html[2:], styles["Normal"]))
        else:
            story.append(Paragraph(clean_html.replace("**", ""), styles["Normal"]))
    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph("Gerado pelo Renda Digital IA. Revise antes de vender.", styles["SmallMuted"]))
    doc.build(story)
    return out


def make_markdown(product: Dict[str, Any], section: str = "content") -> Path:
    content = product.get(section) or "Sem conteúdo."
    filename = f"{slugify(product['title'])}-{section}.md"
    out = EXPORT_DIR / filename
    out.write_text(content, encoding="utf-8")
    return out



# ----------------------------- Estratégia de renda / oferta -----------------------------

PRODUCT_IDEAS = [
    {
        "title": "Kit IA para Pequenos Negócios — Prompts, Posts e WhatsApp",
        "niche": "IA para pequenos negócios",
        "discipline": "Não se aplica / produto geral",
        "school_level": "Pequenos negócios",
        "product_type": "Pack de prompts IA",
        "target": "MEIs, autônomos, vendedores, lojas pequenas e prestadores de serviço",
        "price": 47.00,
        "promise": "criar mensagens, posts e roteiros de venda com mais rapidez usando IA de forma simples",
        "channel": "TikTok, Instagram, WhatsApp, Facebook e grupos de empreendedores",
        "difficulty": "baixa",
    },
    {
        "title": "Planner Financeiro Familiar — Controle de Gastos e Metas",
        "niche": "Finanças pessoais e organização",
        "discipline": "Não se aplica / produto geral",
        "school_level": "Público geral",
        "product_type": "Planner PDF",
        "target": "famílias, casais, jovens e pessoas que querem organizar dinheiro sem planilha complicada",
        "price": 27.00,
        "promise": "organizar gastos, dívidas, metas e compras do mês com um passo a passo simples",
        "channel": "Reels, TikTok, Pinterest, Facebook e WhatsApp",
        "difficulty": "baixa",
    },
    {
        "title": "Kit MEI Organizado — Clientes, Preços, Estoque e Divulgação",
        "niche": "Pequenos negócios e MEI",
        "discipline": "Não se aplica / produto geral",
        "school_level": "MEI e autônomos",
        "product_type": "Pacote completo ZIP",
        "target": "MEIs, autônomos, vendedores locais e pequenos prestadores de serviço",
        "price": 67.00,
        "promise": "organizar clientes, preço, atendimento, divulgação e rotina do negócio em modelos simples",
        "channel": "Instagram, WhatsApp, Facebook, grupos de MEI e comunidades locais",
        "difficulty": "média",
    },
    {
        "title": "Pack Canva e Legendas para Pequenos Negócios",
        "niche": "Templates, design e redes sociais",
        "discipline": "Não se aplica / produto geral",
        "school_level": "Pequenos negócios",
        "product_type": "Kit de templates",
        "target": "lojas, salões, delivery, autônomos e pequenos negócios que postam no Instagram",
        "price": 37.00,
        "promise": "postar com mais frequência usando modelos de legendas, ideias de conteúdo e estrutura visual simples",
        "channel": "Instagram, TikTok, Pinterest e WhatsApp",
        "difficulty": "baixa",
    },
    {
        "title": "Agenda Profissional de Beleza — Clientes, Fichas e WhatsApp",
        "niche": "Beleza, estética e atendimento",
        "discipline": "Não se aplica / produto geral",
        "school_level": "Profissionais de beleza",
        "product_type": "Planner PDF",
        "target": "manicures, cabeleireiras, barbeiros, designers de sobrancelha e profissionais de estética",
        "price": 39.90,
        "promise": "organizar agenda, atendimento, retorno de clientes, mensagens e divulgação semanal",
        "channel": "Instagram, TikTok, grupos de beleza, WhatsApp e Facebook",
        "difficulty": "baixa",
    },
    {
        "title": "Planner da Casa — Limpeza, Cardápio, Compras e Rotina",
        "niche": "Casa, organização e rotina",
        "discipline": "Não se aplica / produto geral",
        "school_level": "Famílias e organização doméstica",
        "product_type": "Planner PDF",
        "target": "famílias, donas de casa, casais, mães e pessoas que querem organizar a rotina",
        "price": 19.90,
        "promise": "organizar limpeza, cardápio, compras, tarefas e rotina semanal de forma simples",
        "channel": "Pinterest, TikTok, Reels, Facebook e WhatsApp",
        "difficulty": "baixa",
    },
    {
        "title": "Kit Marmitas e Confeitaria — Cardápio, Ficha Técnica e Preço",
        "niche": "Culinária, marmitas e confeitaria",
        "discipline": "Não se aplica / produto geral",
        "school_level": "Iniciantes",
        "product_type": "Planilha simples",
        "target": "pessoas que vendem marmitas, bolos, doces, salgados e comida caseira",
        "price": 47.00,
        "promise": "organizar receitas, custos, preço, cardápio, pedidos e divulgação de forma prática",
        "channel": "TikTok, Instagram, WhatsApp, Facebook e grupos locais",
        "difficulty": "média",
    },
    {
        "title": "Kit Currículo e Entrevista — Modelos Prontos para Editar",
        "niche": "Carreira, currículo e renda extra",
        "discipline": "Não se aplica / produto geral",
        "school_level": "Iniciantes",
        "product_type": "Ebook guia prático",
        "target": "jovens, trabalhadores, pessoas buscando recolocação e primeiro emprego",
        "price": 27.00,
        "promise": "montar currículo, bio profissional e respostas de entrevista com modelos simples",
        "channel": "TikTok, Instagram, Facebook, WhatsApp e grupos de emprego",
        "difficulty": "baixa",
    },
    {
        "title": "Planner Pet — Rotina, Gastos, Cuidados e Checklist",
        "niche": "Pets e rotina de cuidados",
        "discipline": "Não se aplica / produto geral",
        "school_level": "Público geral",
        "product_type": "Planner PDF",
        "target": "tutores de cães e gatos que querem organizar rotina e gastos do pet",
        "price": 17.00,
        "promise": "organizar alimentação, banho, passeios, gastos, consultas e checklist de viagem",
        "channel": "TikTok, Instagram, Pinterest, Facebook e grupos de pets",
        "difficulty": "baixa",
    },
    {
        "title": "Mega Kit Professor Total — Atividades para Todas as Disciplinas",
        "niche": "Educação - todas as disciplinas",
        "discipline": "Todas as disciplinas",
        "school_level": "Ensino fundamental anos iniciais",
        "product_type": "Mega kit de atividades",
        "target": "professores do 1º ao 5º ano, reforço escolar e pais",
        "price": 47.00,
        "promise": "economizar tempo com atividades prontas, gabaritos, orientações e material escolar organizado por disciplina",
        "channel": "TikTok, Instagram, Facebook, WhatsApp e grupos de professores",
        "difficulty": "baixa",
    },
]


DAILY_PLAN = [
    ("Definir o público e escolher 1 problema real para resolver", "Sem público claro, a oferta fica genérica."),
    ("Criar o primeiro produto no sistema", "Use produto simples: kit, apostila, planner ou banco de questões."),
    ("Revisar conteúdo e transformar em PDF", "Nunca publique material sem revisão humana."),
    ("Criar capa simples e nome mais direto", "O nome precisa explicar o resultado prático."),
    ("Montar página de venda", "Use promessa honesta, benefícios e perguntas frequentes."),
    ("Criar checkout na plataforma escolhida", "Cole o link no cadastro do produto."),
    ("Publicar 3 posts de teste", "Teste ângulos diferentes: dor, benefício e demonstração."),
    ("Mandar oferta para 20 contatos com respeito", "Sem spam; envie só para quem faz sentido."),
    ("Gravar vídeo mostrando por dentro", "Mostre telas, páginas e exemplos do material."),
    ("Criar bônus simples", "Ex: checklist, modelo editável, roteiro de uso."),
    ("Melhorar a headline da página", "Título claro vende mais que título bonito e confuso."),
    ("Postar prova de utilidade", "Mostre um exemplo gratuito do produto."),
    ("Criar 5 mensagens de WhatsApp", "Mensagens curtas e naturais convertem melhor."),
    ("Responder dúvidas e anotar objeções", "Cada dúvida pode virar melhoria na página."),
    ("Ajustar preço se necessário", "Produto inicial costuma vender melhor entre R$ 17 e R$ 47."),
    ("Criar segundo produto complementar", "Ex: banco de questões depois do planner."),
    ("Criar pacote com 2 produtos", "Aumenta ticket médio sem depender de mais clientes."),
    ("Criar post comparando antes e depois", "Mostre a rotina sem o material e com o material."),
    ("Fazer abordagem em grupos certos", "Respeite regras dos grupos e ajude antes de vender."),
    ("Publicar FAQ", "Responda: entrega, edição, uso, suporte e garantia."),
    ("Criar oferta limitada honesta", "Ex: bônus para as primeiras compras, sem falsa escassez."),
    ("Adicionar depoimento real quando tiver", "Não invente depoimentos."),
    ("Revisar produto pelo feedback", "Atualize arquivos para reduzir dúvidas."),
    ("Criar 10 roteiros de vídeo", "Vídeo curto precisa mostrar problema e solução rápido."),
    ("Organizar calendário da próxima semana", "Venda exige repetição e consistência."),
    ("Testar novo ângulo de oferta", "Ex: economia de tempo, organização, material pronto."),
    ("Criar versão premium", "Inclua editáveis, bônus e mais exemplos."),
    ("Reativar interessados", "Chame quem perguntou e não comprou com educação."),
    ("Analisar números", "Veja cliques, conversas, vendas e objeções."),
    ("Escolher o que escalar", "Continue no produto que gerou mais interesse."),
]


def estimate_offer_score(title: str, target: str, promise: str, price: float, channel: str = "") -> Dict[str, Any]:
    text = f"{title} {target} {promise} {channel}".lower()
    score = 40
    reasons = []

    if len(title.strip()) >= 18:
        score += 10
        reasons.append("Nome do produto está específico.")
    else:
        reasons.append("Nome ainda está curto; deixe claro para quem é e o que entrega.")
    if any(w in text for w in ["professor", "escola", "pais", "reforço", "pedagogia", "atividade", "questões", "simulado", "redação", "matemática", "português", "ciências", "história", "geografia", "enem", "mei", "autônomo", "beleza", "cliente", "whatsapp", "instagram", "negócio", "finanças", "gastos", "casa", "rotina", "marmita", "confeitaria", "currículo", "pet", "templates", "ia", "prompt"]):
        score += 12
        reasons.append("Público tem sinal de necessidade prática.")
    else:
        reasons.append("Público parece amplo; nichar mais pode aumentar conversão.")
    if any(w in text for w in ["economizar", "pronto", "organizar", "evitar", "melhorar", "gabarito", "checklist", "modelo", "template", "roteiro", "controle", "planejar", "clientes", "posts", "mensagens"]):
        score += 14
        reasons.append("Promessa tem benefício prático e honesto.")
    else:
        reasons.append("Promessa precisa ficar mais concreta e mensurável.")
    if 9 <= price <= 67:
        score += 10
        reasons.append("Preço bom para produto de entrada.")
    elif 67 < price <= 147:
        score += 4
        reasons.append("Preço pode funcionar, mas precisa de mais prova, bônus e valor percebido.")
    else:
        reasons.append("Preço pode estar fora do ideal para primeira compra.")
    if any(w in text for w in ["whatsapp", "tiktok", "instagram", "facebook", "grupos"]):
        score += 8
        reasons.append("Canal de divulgação foi definido.")
    else:
        reasons.append("Defina onde você vai divulgar diariamente.")

    score = max(0, min(100, score))
    if score >= 85:
        level = "Oferta forte para testar"
    elif score >= 70:
        level = "Boa, mas precisa de ajuste"
    elif score >= 55:
        level = "Média; valide antes de investir tempo"
    else:
        level = "Fraca; precisa nichar e melhorar a promessa"
    return {"score": score, "level": level, "reasons": reasons}


def build_launch_calendar(product: Dict[str, Any]) -> str:
    title = product.get("title", "Produto Digital")
    target = product.get("target_audience", "público-alvo")
    promise = product.get("promise", "resolver um problema prático")
    lines = [f"# Calendário de divulgação — {title}", "", f"Público: {target}", f"Promessa: {promise}", "", "Use este plano por 30 dias. Poste, responda dúvidas e anote o que gerou conversa.", ""]
    angles = [
        "dor do público", "demonstração por dentro", "benefício prático", "erro comum", "antes e depois",
        "checklist gratuito", "pergunta direta", "história curta", "bônus", "prova de utilidade",
    ]
    for i, (task, why) in enumerate(DAILY_PLAN, 1):
        angle = angles[(i - 1) % len(angles)]
        lines.extend([
            f"## Dia {i:02d} — {task}",
            f"**Objetivo:** {why}",
            f"**Post sugerido:** Faça um conteúdo de {angle} mostrando como o {title} ajuda {target.lower()}.",
            f"**Chamada:** Se quiser o material pronto, me chama que eu te envio o link.",
            "",
        ])
    return "\n".join(lines)


def build_sales_funnel(product: Dict[str, Any]) -> str:
    title = product.get("title", "Produto Digital")
    price = float(product.get("price") or 29.90)
    target = product.get("target_audience", "público-alvo")
    promise = product.get("promise", "resolver um problema prático")
    bump_price = max(7, round(price * 0.45, 2))
    premium_price = max(round(price * 2.4, 2), price + 20)
    return f"""
# Funil de venda — {title}

## Oferta principal
Produto: {title}
Preço de entrada sugerido: {money(price)}
Público: {target}
Promessa honesta: {promise}

## Isca gratuita
Crie um PDF de 1 a 3 páginas com um exemplo do produto:
- 1 checklist pronto
- 1 página de amostra
- 1 orientação rápida de uso

Chamada: “Quer receber uma amostra grátis? Comente EU QUERO ou me chame no WhatsApp.”

## Order bump
Produto extra simples oferecido no checkout.
Sugestão: versão editável, checklist extra, capa, modelo de organização ou banco com mais exemplos.
Preço sugerido do bump: {money(bump_price)}

## Oferta premium
Pacote maior para quem quer mais material.
Inclua: produto principal + bônus + versão editável + calendário de uso + suporte básico por e-mail.
Preço sugerido premium: {money(premium_price)}

## Pós-venda
1. Envie agradecimento.
2. Pergunte se conseguiu baixar.
3. Peça feedback real depois de 3 dias.
4. Ofereça produto complementar apenas se fizer sentido.

## Cuidado
Não prometa venda garantida, nota garantida, emprego garantido ou resultado financeiro automático. Venda solução prática e entregue o que foi prometido.
""".strip()


def build_offer_checklist(product: Dict[str, Any]) -> str:
    title = product.get("title", "Produto Digital")
    return f"""
# Checklist antes de publicar — {title}

## Produto
- [ ] O título explica para quem é.
- [ ] O PDF foi revisado.
- [ ] O material tem capa, sumário e instruções.
- [ ] O conteúdo tem exemplos práticos.
- [ ] Não existe promessa exagerada ou enganosa.
- [ ] O arquivo final abre corretamente no celular.

## Página de venda
- [ ] Headline clara.
- [ ] Preço visível.
- [ ] O que recebe está explicado.
- [ ] Perguntas frequentes respondidas.
- [ ] Link do checkout testado.
- [ ] Garantia/política da plataforma está correta.

## Divulgação
- [ ] 10 posts prontos.
- [ ] 5 roteiros de vídeo prontos.
- [ ] 3 mensagens de WhatsApp prontas.
- [ ] Calendário de 30 dias criado.
- [ ] Lista de lugares para divulgar feita.

## Métricas simples
- [ ] Quantas pessoas viram?
- [ ] Quantas chamaram no WhatsApp?
- [ ] Quantas clicaram no checkout?
- [ ] Quantas compraram?
- [ ] Qual dúvida apareceu mais?
""".strip()


def build_marketplace_listing(product: Dict[str, Any]) -> str:
    title = product.get("title", "Produto Digital")
    target = product.get("target_audience", "público-alvo")
    promise = product.get("promise", "resolver um problema prático")
    return f"""
# Cadastro do produto na plataforma — {title}

## Nome curto
{title}

## Descrição curta
Material digital para {target.lower()} que precisam de uma solução prática para {promise.lower()}.

## Descrição completa
O {title} é um produto digital organizado para ajudar {target.lower()} no dia a dia. O material foi pensado para economizar tempo, reduzir improviso e facilitar a aplicação prática.

Você recebe arquivos digitais com estrutura pronta, exemplos, orientações e materiais de apoio. Antes de usar, adapte para sua realidade.

## Palavras-chave
produto digital, ebook, apostila, material pronto, organização, {target}, checklist, PDF

## Benefícios principais
- Material pronto para baixar.
- Linguagem simples.
- Fácil de adaptar.
- Ajuda a organizar a rotina.
- Ideal para quem quer começar sem montar tudo do zero.

## Mensagem de entrega
Obrigado pela compra! Baixe o material, leia as instruções e adapte para sua realidade. Se tiver dificuldade com o arquivo, entre em contato pelo canal informado na página de venda.
""".strip()


def subject_file_name(subject: str) -> str:
    order = {"Português": "01", "Matemática": "02", "Ciências": "03", "História": "04", "Geografia": "05", "Inglês": "06", "Artes": "07", "Educação Física": "08"}
    prefix = order.get(subject, "99")
    return f"{prefix} - {subject}/atividades-bncc-{slugify(subject)}.md"


def extract_subject_section(content: str, subject: str) -> str:
    pattern = rf"(## \d+\. {re.escape(subject)}[\s\S]*?)(?=\n## \d+\. |\n## Bônus|\Z)"
    m = re.search(pattern, content)
    if m:
        return f"# {subject} — Arquivo separado\n\n" + m.group(1).strip() + "\n"
    return f"# {subject} — Arquivo separado\n\nNão foi possível separar automaticamente. Revise o mega kit completo.\n"




def build_lead_magnet(product: Dict[str, Any]) -> str:
    title = product.get("title", "Produto Digital")
    discipline = product.get("discipline") or "Todas as disciplinas"
    level = product.get("school_level") or "Ensino fundamental"
    return f"""
# Amostra grátis — {title}

Obrigado por baixar esta amostra. Ela serve para mostrar a organização do material antes da compra.

## O que tem nesta amostra
- 1 atividade demonstrativa.
- 1 orientação rápida de uso.
- 1 checklist para revisar BNCC e adaptação da turma.

## Atividade demonstrativa
**Disciplina:** {discipline}
**Ano/Nível:** {level}
**Objetivo:** mostrar como o material é organizado para uso prático.

### Folha do aluno
Nome: __________________________________  Data: ____/____/______

1. Leia o enunciado da atividade com atenção.
2. Responda com suas palavras o que você entendeu.
3. Faça uma pequena conclusão sobre o tema trabalhado.

### Orientação para o professor ou responsável
Apresente o objetivo da atividade, leia o comando com calma e adapte a dificuldade conforme a realidade da turma.

### Gabarito comentado
Resposta pessoal, avaliando compreensão, organização e coerência.

## Quer o material completo?
O pacote completo traz mais atividades, gabaritos, orientações, mapa BNCC editável, bônus e posts de apoio para organização.
""".strip()


def build_whatsapp_sequence(product: Dict[str, Any]) -> str:
    title = product.get("title", "Produto Digital")
    price = money(float(product.get("price") or 0))
    checkout = product.get("checkout_link") or "COLE_AQUI_O_LINK_DO_CHECKOUT"
    return f"""
# Sequência WhatsApp — {title}

## Mensagem 1 — Amostra grátis
Oi! Preparei uma amostra grátis do {title}. É um material digital organizado para professores, reforço escolar e pais. Quer que eu te envie?

## Mensagem 2 — Apresentação curta
O material completo vem com atividades, orientação, gabarito e estrutura para adaptar. O valor sugerido é {price}. Link: {checkout}

## Mensagem 3 — Quebra de objeção
Ele não é material oficial do MEC; é um material editável com campos BNCC para revisão. Você pode adaptar conforme sua turma e currículo local.

## Mensagem 4 — Fechamento honesto
Se fizer sentido para você, aqui está o link para comprar com entrega digital: {checkout}. Depois que baixar, revise e adapte antes de aplicar.

## Mensagem 5 — Pós-venda
Obrigado pela compra! Conseguiu baixar tudo certinho? Qualquer dificuldade, me avise por aqui.
""".strip()


def build_public_page_copy(product: Dict[str, Any]) -> str:
    title = product.get("title", "Produto Digital")
    checkout = product.get("checkout_link") or "COLE_AQUI_O_LINK_DO_CHECKOUT"
    return f"""
# Página pública pronta — {title}

Use a rota pública do sistema para vender:
/p/{product.get('public_slug') or slugify(title)}

Botão de compra apontando para:
{checkout}

Dica: divulgue a página pública e ofereça a amostra grátis para capturar contatos antes da venda.
""".strip()

def build_package_files(product: Dict[str, Any]) -> Dict[str, str]:
    discipline = product.get("discipline") or "Todas as disciplinas"
    school_level = product.get("school_level") or "Ensino fundamental"
    content = product.get("content") or ""
    files: Dict[str, str] = {
        "00-leia-primeiro.md": "# Leia primeiro\n\nEste pacote foi gerado pelo Renda Digital IA Escolar BNCC. Revise tudo antes de vender, publicar ou aplicar. Não anuncie como material oficial do MEC. Use como material editável com campos BNCC para conferência pedagógica.\n",
        "01-mega-kit-completo.md": content,
        "02-pagina-de-venda.md": product.get("sales_page") or "",
        "03-posts-e-mensagens.md": product.get("social_posts") or "",
        "04-prompts-chatgpt.md": product.get("prompt_pack") or "",
        "05-mapa-bncc.md": bncc_map_markdown(discipline, school_level),
        "06-calendario-30-dias.md": build_launch_calendar(product),
        "07-funil-de-venda.md": build_sales_funnel(product),
        "08-checklist-publicacao.md": build_offer_checklist(product),
        "09-cadastro-plataforma.md": build_marketplace_listing(product),
        "10-isca-gratis-amostra.md": product.get("lead_magnet_content") or build_lead_magnet(product),
        "11-sequencia-whatsapp.md": build_whatsapp_sequence(product),
        "12-pagina-publica-e-links.md": build_public_page_copy(product),
    }
    for subject in get_subjects_for_product(discipline):
        files[subject_file_name(subject)] = extract_subject_section(content, subject)
    return files



# ----------------------------- Central comercial -----------------------------

def build_customer_package_files(product: Dict[str, Any]) -> Dict[str, str]:
    """Pacote limpo para entregar ao comprador. Não inclui arquivos internos de divulgação."""
    discipline = product.get("discipline") or "Todas as disciplinas"
    school_level = product.get("school_level") or "Ensino fundamental"
    content = product.get("content") or ""
    files: Dict[str, str] = {
        "00-LEIA-PRIMEIRO.md": f"# Leia primeiro — {product.get('title','Produto Digital')}\n\nObrigado por adquirir este material. Ele é um produto digital editável para apoiar professores, escolas, pais, tutores e reforço escolar.\n\n## Importante\n- Revise antes de aplicar.\n- Adapte para sua turma, escola e currículo local.\n- Os campos BNCC são editáveis e precisam de conferência pedagógica.\n- Este material não é um documento oficial do MEC.\n\n## Como usar\n1. Abra o mega kit completo.\n2. Escolha a disciplina ou tema.\n3. Imprima apenas a folha do aluno.\n4. Use a orientação e o gabarito para correção.\n5. Faça adaptações para alunos com dificuldade.\n",
        "01-MEGA-KIT-COMPLETO.md": content,
        "02-AMOSTRA-GRATIS.md": product.get("lead_magnet_content") or build_lead_magnet(product),
        "03-MAPA-BNCC-EDITAVEL.md": bncc_map_markdown(discipline, school_level),
        "04-MANUAL-DE-USO.md": build_teacher_manual(product),
        "05-CHECKLIST-DE-APLICACAO.md": build_classroom_checklist(product),
        "06-BONUS-PLANEJAMENTO-SEMANAL.md": build_weekly_planner(product),
    }
    for subject in get_subjects_for_product(discipline):
        files["DISCIPLINAS/" + subject_file_name(subject)] = extract_subject_section(content, subject)
    return files


def build_teacher_manual(product: Dict[str, Any]) -> str:
    title = product.get("title", "Produto Digital")
    return f"""
# Manual de uso — {title}

## Para quem este material foi feito
Este material foi criado para professores, escolas pequenas, reforço escolar, tutores e pais que precisam de uma base pronta para organizar atividades.

## Como aplicar em sala ou reforço
1. Escolha uma atividade compatível com o ano/série.
2. Revise o código BNCC indicado e ajuste conforme currículo local.
3. Leia o enunciado com os alunos.
4. Dê tempo para resposta individual.
5. Corrija com base no gabarito comentado.
6. Registre dificuldades para revisar na aula seguinte.

## Como adaptar
- Para turma com dificuldade: reduza o número de questões e dê exemplo guiado.
- Para turma avançada: peça justificativa, comparação ou produção final maior.
- Para alunos com dificuldade de leitura: leia o comando em voz alta e aceite resposta oral antes da escrita.

## Aviso pedagógico
Este produto é uma base editável. O professor ou responsável deve revisar e adaptar antes de usar.
""".strip()


def build_classroom_checklist(product: Dict[str, Any]) -> str:
    return """
# Checklist de aplicação

- [ ] A disciplina está correta.
- [ ] O ano/série está compatível com a turma.
- [ ] A habilidade BNCC foi conferida.
- [ ] O enunciado está claro.
- [ ] A atividade tem espaço suficiente para resposta.
- [ ] O gabarito foi separado da folha do aluno.
- [ ] A atividade foi adaptada para alunos com dificuldade.
- [ ] O tempo de aplicação foi definido.
- [ ] A forma de avaliação foi escolhida.
- [ ] A revisão final foi feita antes de imprimir ou enviar.
""".strip()


def build_weekly_planner(product: Dict[str, Any]) -> str:
    return """
# Bônus — Planejamento semanal

## Semana: ____/____ a ____/____

| Dia | Disciplina | Tema | Habilidade BNCC | Atividade | Observações |
|---|---|---|---|---|---|
| Segunda | | | | | |
| Terça | | | | | |
| Quarta | | | | | |
| Quinta | | | | | |
| Sexta | | | | | |

## Acompanhamento da turma
- Alunos com facilidade:
- Alunos que precisam de apoio:
- Conteúdos para revisar:
- Próxima atividade sugerida:
""".strip()


def build_ad_creatives(product: Dict[str, Any]) -> str:
    title = product.get("title", "Produto Digital")
    price = money(float(product.get("price") or 0))
    checkout = product.get("checkout_link") or "COLE_AQUI_O_LINK_DO_CHECKOUT"
    lead = request.host_url.rstrip("/") + url_for("public_product_page", slug=product.get("public_slug")) if product.get("public_slug") else "COLE_AQUI_A_PAGINA_PUBLICA"
    return f"""
# Criativos prontos para venda — {title}

## Headline principal
{title}: material pronto para economizar tempo com atividades escolares organizadas.

## Texto curto para anúncio
Professores, pais e reforço escolar: receba um material digital pronto, com atividades, gabarito, orientação e campos BNCC editáveis. Baixe uma amostra grátis e veja por dentro.

Página pública: {lead}
Checkout: {checkout}
Preço: {price}

## 10 chamadas para post
1. Professor, você ainda monta atividade do zero?
2. Material pronto para revisar, adaptar e usar.
3. Atividades com gabarito e orientação separada.
4. Economize tempo na preparação das aulas.
5. Ideal para escola, reforço escolar e pais.
6. Baixe uma amostra grátis antes de comprar.
7. Veja por dentro antes de decidir.
8. Produto digital com entrega rápida pelo checkout.
9. Organize sua semana com atividades prontas.
10. Material editável com campos BNCC para revisão.

## Roteiro de vídeo curto
Cena 1: mostrar uma folha em branco. Texto: “Cansado de montar atividade do zero?”
Cena 2: mostrar o material aberto. Texto: “Aqui já vem atividade, gabarito e orientação.”
Cena 3: mostrar o botão de amostra. Texto: “Baixe uma amostra grátis e veja por dentro.”
Cena 4: chamada final. Texto: “Link na bio ou me chama no WhatsApp.”

## Mensagem para grupos, sem spam
Oi, pessoal! Preparei um material digital escolar com atividades prontas, gabarito e orientação para professor/responsável. Tem amostra grátis para ver antes. Quem quiser, posso enviar o link.
""".strip()


def venda_real_status() -> Dict[str, Any]:
    conn = db_conn()
    products_total = conn.execute("SELECT COUNT(*) c FROM products").fetchone()["c"]
    checkout_ready = conn.execute("SELECT COUNT(*) c FROM products WHERE checkout_link IS NOT NULL AND checkout_link != ''").fetchone()["c"]
    public_ready = conn.execute("SELECT COUNT(*) c FROM products WHERE COALESCE(public_enabled,1)=1 AND public_slug IS NOT NULL AND public_slug != ''").fetchone()["c"]
    leads_total = conn.execute("SELECT COUNT(*) c FROM leads").fetchone()["c"]
    visits_total = conn.execute("SELECT COUNT(*) c FROM visits").fetchone()["c"]
    sales_total = conn.execute("SELECT COUNT(*) c FROM sales WHERE status='aprovada'").fetchone()["c"]
    revenue = conn.execute("SELECT COALESCE(SUM(amount),0) total FROM sales WHERE status='aprovada'").fetchone()["total"]
    top_product = conn.execute("SELECT * FROM products ORDER BY created_at DESC LIMIT 1").fetchone()
    conn.close()
    items = [
        {"name":"Produto criado", "ok": products_total > 0, "help":"Crie pelo menos um produto pronto para venda."},
        {"name":"Página pública ativa", "ok": public_ready > 0, "help":"A página pública captura leads e apresenta a oferta."},
        {"name":"Checkout configurado", "ok": checkout_ready > 0, "help":"Cole o link da Kiwify/Hotmart/Eduzz/Monetizze no produto."},
        {"name":"Amostra grátis pronta", "ok": products_total > 0, "help":"Use a isca grátis para capturar contatos."},
        {"name":"Leads capturados", "ok": leads_total > 0, "help":"Divulgue a amostra grátis para começar a lista."},
        {"name":"Primeiras visitas", "ok": visits_total > 0, "help":"Abra e divulgue a página pública."},
        {"name":"Primeira venda", "ok": sales_total > 0, "help":"Após venda aprovada, cadastre manualmente ou use webhook."},
    ]
    percent = int(sum(1 for i in items if i["ok"]) / len(items) * 100)
    return {
        "items": items,
        "percent": percent,
        "products_total": products_total,
        "checkout_ready": checkout_ready,
        "public_ready": public_ready,
        "leads_total": leads_total,
        "visits_total": visits_total,
        "sales_total": sales_total,
        "revenue": revenue,
        "top_product": row_to_dict(top_product) if top_product else None,
    }


def ready_mega_form() -> Dict[str, Any]:
    return {
        "title": "Mega Kit Professor Total — Todas as Disciplinas",
        "niche": "Educação - todas as disciplinas",
        "product_type": "Mega kit de atividades",
        "discipline": "Todas as disciplinas",
        "school_level": "Ensino fundamental anos iniciais",
        "target_audience": "professores, escolas pequenas, reforço escolar, pais e tutores",
        "promise": "economizar tempo com atividades prontas, gabaritos, orientações e material escolar organizado por disciplina",
        "price": "47.00",
        "pages": "80",
        "status": "publicado",
        "platform": "Kiwify/Hotmart",
        "checkout_link": "",
        "public_enabled": "1",
        "bonus_stack": "Arquivos separados por disciplina, amostra grátis, checklist BNCC, manual de uso, planejamento semanal e sequência de WhatsApp.",
        "guarantee_days": "7",
    }



# ----------------------------- Rotas -----------------------------


@app.route("/venda-real")
@app.route("/central-comercial")
@login_required
def venda_real_page():
    status = venda_real_status()
    return render_template("venda_real.html", status=status, title="Central de venda estruturada")


@app.route("/produto-pronto/mega-kit", methods=["POST"])
@login_required
def create_ready_mega_kit():
    form = ready_mega_form()
    assets = generate_product_assets(form)
    now = datetime.utcnow().isoformat()
    conn = db_conn()
    title = form["title"]
    existing = conn.execute("SELECT id FROM products WHERE title = ?", (title,)).fetchone()
    if existing:
        conn.close()
        flash("O Mega Kit pronto já existe. Abri o produto para você continuar.", "success")
        return redirect(url_for("product_detail", product_id=existing["id"]))
    slug = unique_public_slug(title, conn)
    lead_content = build_lead_magnet({**form, **assets, "public_slug": slug})
    conn.execute(
        """
        INSERT INTO products (title, niche, product_type, discipline, school_level, target_audience, promise, price, status, checkout_link, platform, content, sales_page, social_posts, prompt_pack, public_slug, public_enabled, lead_magnet_title, lead_magnet_content, guarantee_days, bonus_stack, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            title,
            form["niche"],
            form["product_type"],
            form["discipline"],
            form["school_level"],
            form["target_audience"],
            form["promise"],
            float(form["price"]),
            form["status"],
            form["checkout_link"],
            form["platform"],
            assets["content"],
            assets["sales_page"],
            assets["social_posts"],
            assets["prompt_pack"],
            slug,
            1,
            "Amostra grátis do Mega Kit Professor Total",
            lead_content,
            int(form["guarantee_days"]),
            form["bonus_stack"],
            now,
            now,
        ),
    )
    product_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()
    conn.close()
    flash("Produto principal criado. Agora cole o link do checkout e publique a página.", "success")
    return redirect(url_for("product_detail", product_id=product_id))


@app.route("/p/<slug>")
def public_product_page(slug):
    conn = db_conn()
    row = conn.execute("SELECT * FROM products WHERE public_slug = ? AND COALESCE(public_enabled,1)=1", (slug,)).fetchone()
    if not row:
        conn.close()
        abort(404)
    product = row_to_dict(row)
    ip_hash = sha256((request.remote_addr or "").encode()).hexdigest()[:16]
    conn.execute("INSERT INTO visits (product_id, path, user_agent, ip_hash, created_at) VALUES (?, ?, ?, ?, ?)", (product["id"], request.path, request.headers.get("User-Agent", "")[:250], ip_hash, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()
    sample = first_paragraphs(product.get("content") or "", 2200)
    return render_template("public_product.html", product=product, sample=sample, title=product["title"])


@app.route("/p/<slug>/lead", methods=["POST"])
def public_product_lead(slug):
    conn = db_conn()
    row = conn.execute("SELECT * FROM products WHERE public_slug = ? AND COALESCE(public_enabled,1)=1", (slug,)).fetchone()
    if not row:
        conn.close()
        abort(404)
    product = row_to_dict(row)
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    whatsapp = request.form.get("whatsapp", "").strip()
    conn.execute("INSERT INTO leads (product_id, name, email, whatsapp, source, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (product["id"], name, email, whatsapp, "Página pública", request.form.get("note", ""), datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()
    flash("Contato salvo. A amostra está liberada abaixo.", "success")
    return redirect(url_for("public_product_page", slug=slug) + "#amostra")


@app.route("/p/<slug>/amostra.md")
def public_product_sample(slug):
    conn = db_conn()
    row = conn.execute("SELECT * FROM products WHERE public_slug = ? AND COALESCE(public_enabled,1)=1", (slug,)).fetchone()
    conn.close()
    if not row:
        abort(404)
    product = row_to_dict(row)
    content = product.get("lead_magnet_content") or build_lead_magnet(product)
    data = io.BytesIO(content.encode("utf-8-sig"))
    return send_file(data, mimetype="text/markdown", as_attachment=True, download_name=f"amostra-{slug}.md")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        conn = db_conn()
        user = conn.execute("SELECT * FROM users WHERE lower(email) = ?", (email,)).fetchone()
        conn.close()
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["user_email"] = user["email"]
            return redirect(url_for("dashboard"))
        flash("E-mail ou senha incorretos.", "danger")
    return render_template("login.html", app_name=APP_NAME, default_email=DEFAULT_ADMIN_EMAIL)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@app.route("/dashboard")
@login_required
def dashboard():
    conn = db_conn()
    stats = conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN status = 'aprovada' THEN amount ELSE 0 END), 0) AS total,
            COUNT(CASE WHEN status = 'aprovada' THEN 1 END) AS approved_count,
            COALESCE(AVG(CASE WHEN status = 'aprovada' THEN amount END), 0) AS avg_ticket
        FROM sales
        """
    ).fetchone()
    products_count = conn.execute("SELECT COUNT(*) AS c FROM products").fetchone()["c"]
    clients_count = conn.execute("SELECT COUNT(DISTINCT buyer_email) AS c FROM sales WHERE buyer_email IS NOT NULL AND buyer_email != ''").fetchone()["c"]
    leads_count = conn.execute("SELECT COUNT(*) AS c FROM leads").fetchone()["c"]
    visits_count = conn.execute("SELECT COUNT(*) AS c FROM visits").fetchone()["c"]
    recent_sales = conn.execute(
        """
        SELECT sales.*, products.title AS product_title
        FROM sales LEFT JOIN products ON products.id = sales.product_id
        ORDER BY sales.created_at DESC LIMIT 8
        """
    ).fetchall()
    top_products = conn.execute(
        """
        SELECT products.id, products.title, COALESCE(SUM(sales.amount),0) AS total, COUNT(sales.id) AS qty
        FROM products LEFT JOIN sales ON products.id = sales.product_id AND sales.status = 'aprovada'
        GROUP BY products.id ORDER BY total DESC LIMIT 5
        """
    ).fetchall()
    # Meta inicial realista para acompanhamento. Ajuste livremente no código se quiser.
    monthly_goal = float(os.getenv("MONTHLY_GOAL", "3000"))
    avg_ticket = float(stats["avg_ticket"] or 29.90)
    total_done = float(stats["total"] or 0)
    goal_remaining = max(0, monthly_goal - total_done)
    sales_needed = int((goal_remaining / avg_ticket) + (1 if goal_remaining % avg_ticket else 0)) if avg_ticket > 0 else 0
    conn.close()
    return render_template(
        "dashboard.html",
        stats=row_to_dict(stats),
        products_count=products_count,
        clients_count=clients_count,
        leads_count=leads_count,
        visits_count=visits_count,
        recent_sales=[row_to_dict(r) for r in recent_sales],
        top_products=[row_to_dict(r) for r in top_products],
        openai_ok=bool(OPENAI_API_KEY),
        monthly_goal=monthly_goal,
        goal_remaining=goal_remaining,
        sales_needed=sales_needed,
    )


@app.route("/nichos")
@login_required
def niches():
    return render_template("niches.html", niches=NICHES)


@app.route("/disciplinas")
@login_required
def discipline_center():
    ideas_by_subject = []
    for subject in SUBJECTS:
        if subject == "Todas as disciplinas":
            title = "Mega Kit Professor Total - Todas as Disciplinas"
            promise = "economizar tempo com atividades prontas, gabaritos e orientações para várias disciplinas"
            product_type = "Mega kit de atividades"
            price = 47.00
        else:
            title = f"Kit {subject} Pronto - Atividades, Provas e Gabaritos"
            promise = f"economizar tempo criando atividades, provas e revisões de {subject}"
            product_type = "Banco de questões" if subject in ["Matemática", "Português", "Ciências", "História", "Geografia"] else "Apostila editável"
            price = 29.90
        ideas_by_subject.append({
            "subject": subject,
            "title": title,
            "promise": promise,
            "product_type": product_type,
            "price": price,
            "target": "professores, reforço escolar, escolas e pais",
            "school_level": "Ensino fundamental anos iniciais",
        })
    return render_template("disciplinas.html", subjects=SUBJECTS, school_levels=SCHOOL_LEVELS, ideas=ideas_by_subject)


@app.route("/bncc")
@login_required
def bncc_center():
    discipline = request.args.get("discipline") or "Todas as disciplinas"
    school_level = request.args.get("school_level") or "Ensino fundamental anos iniciais"
    mapa = bncc_map_markdown(discipline, school_level)
    return render_template("bncc.html", subjects=SUBJECTS, school_levels=SCHOOL_LEVELS, discipline=discipline, school_level=school_level, mapa=mapa)


@app.route("/produtos")
@login_required
def products():
    q = request.args.get("q", "").strip()
    conn = db_conn()
    if q:
        rows = conn.execute(
            "SELECT * FROM products WHERE title LIKE ? OR niche LIKE ? OR discipline LIKE ? OR school_level LIKE ? ORDER BY created_at DESC",
            (f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM products ORDER BY created_at DESC").fetchall()
    conn.close()
    return render_template("products.html", products=[row_to_dict(r) for r in rows], q=q)


@app.route("/produtos/novo", methods=["GET", "POST"])
@login_required
def product_new():
    if request.method == "POST":
        form = request.form.to_dict()
        title = form.get("title") or "Produto Digital Escolar"
        niche = form.get("niche") or "Educação - todas as disciplinas"
        product_type = form.get("product_type") or "Mega kit de atividades"
        discipline = form.get("discipline") or "Todas as disciplinas"
        school_level = form.get("school_level") or "Ensino fundamental anos iniciais"
        target = form.get("target_audience") or "professores, reforço escolar, escolas e pais"
        promise = form.get("promise") or "economizar tempo com atividades, provas, gabaritos e materiais prontos para adaptar"
        price = safe_float(form.get("price"), 47.00)
        assets = generate_product_assets({**form, "title": title, "niche": niche, "product_type": product_type, "discipline": discipline, "school_level": school_level, "target_audience": target, "promise": promise, "price": price})
        now = datetime.utcnow().isoformat()
        conn = db_conn()
        cur = conn.cursor()
        public_slug = unique_public_slug(form.get("public_slug") or title, conn)
        lead_magnet_title = form.get("lead_magnet_title") or f"Amostra grátis — {title}"
        lead_magnet_content = form.get("lead_magnet_content") or build_lead_magnet({"title": title, "discipline": discipline, "school_level": school_level})
        bonus_stack = form.get("bonus_stack") or "Amostra grátis; Checklist BNCC; Sequência WhatsApp; Calendário de divulgação; Arquivos separados por disciplina"
        guarantee_days = int(form.get("guarantee_days") or 7)
        cur.execute(
            """
            INSERT INTO products
            (title, niche, product_type, discipline, school_level, target_audience, promise, price, status, checkout_link, platform,
             content, sales_page, social_posts, prompt_pack, public_slug, public_enabled, lead_magnet_title, lead_magnet_content, guarantee_days, bonus_stack, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title, niche, product_type, discipline, school_level, target, promise, price, form.get("status", "rascunho"),
                form.get("checkout_link", ""), form.get("platform", "Manual"),
                assets["content"], assets["sales_page"], assets["social_posts"], assets["prompt_pack"], public_slug, int(form.get("public_enabled", "1") == "1"),
                lead_magnet_title, lead_magnet_content, guarantee_days, bonus_stack, now, now,
            ),
        )
        conn.commit()
        product_id = cur.lastrowid
        conn.close()
        flash("Produto criado com sucesso. Revise o conteúdo antes de vender.", "success")
        return redirect(url_for("product_detail", product_id=product_id))
    preset = request.args.get("niche") or "Educação - todas as disciplinas"
    preset_data = {
        "title": request.args.get("title") or "Mega Kit Professor Total - Atividades para Todas as Disciplinas",
        "target_audience": request.args.get("target") or "professores, reforço escolar, escolas e pais",
        "promise": request.args.get("promise") or "economizar tempo com atividades, provas, gabaritos e materiais prontos para adaptar",
        "price": request.args.get("price") or "47.00",
        "product_type": request.args.get("product_type") or "Mega kit de atividades",
        "discipline": request.args.get("discipline") or "Todas as disciplinas",
        "school_level": request.args.get("school_level") or "Ensino fundamental anos iniciais",
    }
    return render_template("product_form.html", niches=NICHES, product_types=PRODUCT_TYPES, subjects=SUBJECTS, school_levels=SCHOOL_LEVELS, preset=preset, preset_data=preset_data, product=None)


@app.route("/produtos/<int:product_id>")
@login_required
def product_detail(product_id):
    product = get_product(product_id)
    if not product:
        abort(404)
    conn = db_conn()
    sales = conn.execute("SELECT * FROM sales WHERE product_id = ? ORDER BY created_at DESC LIMIT 20", (product_id,)).fetchall()
    conn.close()
    product["bncc_map"] = bncc_map_markdown(product.get("discipline") or "Todas as disciplinas", product.get("school_level") or "Ensino fundamental")
    return render_template("product_detail.html", product=product, sales=[row_to_dict(s) for s in sales])


@app.route("/produtos/<int:product_id>/editar", methods=["GET", "POST"])
@login_required
def product_edit(product_id):
    product = get_product(product_id)
    if not product:
        abort(404)
    if request.method == "POST":
        form = request.form.to_dict()
        now = datetime.utcnow().isoformat()
        conn = db_conn()
        public_slug = unique_public_slug(form.get("public_slug") or form.get("title") or product["title"], conn, product_id=product_id)
        conn.execute(
            """
            UPDATE products SET title=?, niche=?, product_type=?, discipline=?, school_level=?, target_audience=?, promise=?, price=?,
            status=?, checkout_link=?, platform=?, content=?, sales_page=?, social_posts=?, prompt_pack=?, public_slug=?, public_enabled=?, lead_magnet_title=?, lead_magnet_content=?, guarantee_days=?, bonus_stack=?, updated_at=?
            WHERE id=?
            """,
            (
                form_or_existing(form, "title", product, product["title"]),
                form_or_existing(form, "niche", product, product["niche"]),
                form_or_existing(form, "product_type", product, product["product_type"]),
                form_or_existing(form, "discipline", product, product.get("discipline") or "Não se aplica / produto geral"),
                form_or_existing(form, "school_level", product, product.get("school_level") or "Público geral"),
                form_or_existing(form, "target_audience", product, product.get("target_audience") or "público geral"),
                form_or_existing(form, "promise", product, product.get("promise") or "material pronto para adaptar"),
                safe_float(form.get("price"), float(product.get("price") or 47)),
                form_or_existing(form, "status", product, product.get("status") or "rascunho"),
                form_or_existing(form, "checkout_link", product, product.get("checkout_link") or ""),
                form_or_existing(form, "platform", product, product.get("platform") or "Manual"),
                form_or_existing(form, "content", product, product.get("content") or ""),
                form_or_existing(form, "sales_page", product, product.get("sales_page") or ""),
                form_or_existing(form, "social_posts", product, product.get("social_posts") or ""),
                form_or_existing(form, "prompt_pack", product, product.get("prompt_pack") or ""),
                public_slug,
                int(form.get("public_enabled", "1" if product.get("public_enabled") != 0 else "0") == "1"),
                form_or_existing(form, "lead_magnet_title", product, product.get("lead_magnet_title") or f"Amostra grátis — {product['title']}"),
                form_or_existing(form, "lead_magnet_content", product, product.get("lead_magnet_content") or ""),
                safe_int(form.get("guarantee_days"), int(product.get("guarantee_days") or 7)),
                form_or_existing(form, "bonus_stack", product, product.get("bonus_stack") or ""),
                now,
                product_id,
            ),
        )
        conn.commit()
        conn.close()
        flash("Produto atualizado.", "success")
        return redirect(url_for("product_detail", product_id=product_id))
    return render_template("product_edit.html", product=product, niches=NICHES, product_types=PRODUCT_TYPES, subjects=SUBJECTS, school_levels=SCHOOL_LEVELS)


@app.route("/produtos/<int:product_id>/regenerar", methods=["POST"])
@login_required
def product_regenerate(product_id):
    product = get_product(product_id)
    if not product:
        abort(404)
    assets = generate_product_assets({
        "title": product["title"],
        "niche": product["niche"],
        "product_type": product["product_type"],
        "discipline": product.get("discipline") or "Todas as disciplinas",
        "school_level": product.get("school_level") or "Ensino fundamental anos iniciais",
        "target_audience": product["target_audience"],
        "promise": product["promise"],
        "price": product["price"],
        "pages": 24,
    })
    conn = db_conn()
    conn.execute(
        "UPDATE products SET content=?, sales_page=?, social_posts=?, prompt_pack=?, updated_at=? WHERE id=?",
        (assets["content"], assets["sales_page"], assets["social_posts"], assets["prompt_pack"], datetime.utcnow().isoformat(), product_id),
    )
    conn.commit()
    conn.close()
    flash("Conteúdo regenerado. Revise antes de publicar.", "success")
    return redirect(url_for("product_detail", product_id=product_id))


@app.route("/produtos/<int:product_id>/baixar/<section>/<fmt>")
@login_required
def product_download(product_id, section, fmt):
    aliases = {
        "produto": "content",
        "pagina": "sales_page",
        "venda": "sales_page",
        "posts": "social_posts",
        "prompts": "prompt_pack",
        "bncc": "bncc_map",
        "amostra": "lead_magnet_content",
    }
    section = aliases.get(section, section)
    if section not in {"content", "sales_page", "social_posts", "prompt_pack", "bncc_map", "lead_magnet_content"}:
        abort(400)
    # Compatibilidade: alguns botões/links antigos usam txt, mas o gerador real é markdown.
    if fmt == "txt":
        fmt = "md"
    if fmt not in {"pdf", "md"}:
        abort(400)
    product = get_product(product_id)
    if not product:
        abort(404)
    try:
        if section == "bncc_map":
            product = dict(product)
            product["bncc_map"] = bncc_map_markdown(product.get("discipline") or "Todas as disciplinas", product.get("school_level") or "Ensino fundamental")
        if fmt == "pdf":
            path = make_pdf(product, section)
            return send_file(path, as_attachment=True)
        if fmt == "md":
            path = make_markdown(product, section)
            return send_file(path, as_attachment=True)
    except Exception as exc:
        flash(f"Não foi possível gerar arquivo: {exc}", "danger")
        return redirect(url_for("product_detail", product_id=product_id))
    abort(400)


@app.route("/produtos/<int:product_id>/excluir", methods=["POST"])
@login_required
def product_delete(product_id):
    conn = db_conn()
    conn.execute("DELETE FROM sales WHERE product_id = ?", (product_id,))
    conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
    conn.commit()
    conn.close()
    flash("Produto excluído.", "success")
    return redirect(url_for("products"))


@app.route("/vendas")
@login_required
def sales():
    conn = db_conn()
    rows = conn.execute(
        """
        SELECT sales.*, products.title AS product_title
        FROM sales LEFT JOIN products ON products.id = sales.product_id
        ORDER BY sales.created_at DESC
        """
    ).fetchall()
    products_rows = conn.execute("SELECT id, title FROM products ORDER BY title ASC").fetchall()
    conn.close()
    return render_template("sales.html", sales=[row_to_dict(r) for r in rows], products=[row_to_dict(p) for p in products_rows])


@app.route("/vendas/nova", methods=["POST"])
@login_required
def sale_new():
    form = request.form
    product_id = int(form.get("product_id") or 0) or None
    amount = float(str(form.get("amount") or 0).replace(",", "."))
    conn = db_conn()
    conn.execute(
        """
        INSERT INTO sales (product_id, buyer_name, buyer_email, platform, amount, status, external_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            product_id,
            form.get("buyer_name", ""),
            form.get("buyer_email", ""),
            form.get("platform", "Manual"),
            amount,
            form.get("status", "aprovada"),
            form.get("external_id", ""),
            datetime.utcnow().isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    flash("Venda cadastrada.", "success")
    return redirect(url_for("sales"))


@app.route("/vendas/<int:sale_id>/excluir", methods=["POST"])
@login_required
def sale_delete(sale_id):
    conn = db_conn()
    conn.execute("DELETE FROM sales WHERE id = ?", (sale_id,))
    conn.commit()
    conn.close()
    flash("Venda excluída.", "success")
    return redirect(url_for("sales"))


@app.route("/configuracao")
@login_required
def settings_page():
    webhook_url = request.host_url.rstrip("/") + url_for("webhook_sales")
    return render_template(
        "settings.html",
        admin_email=session.get("user_email"),
        openai_ok=bool(OPENAI_API_KEY),
        webhook_url=webhook_url,
        webhook_secret_set=WEBHOOK_SECRET != "troque-webhook-secret",
        reportlab_ok=REPORTLAB_OK,
        db_path=str(DB_PATH),
    )


@app.route("/api/resumo")
@login_required
def api_summary():
    conn = db_conn()
    stats = conn.execute(
        "SELECT COALESCE(SUM(amount),0) total, COUNT(*) qty FROM sales WHERE status='aprovada'"
    ).fetchone()
    conn.close()
    return jsonify(row_to_dict(stats))


@app.route("/webhooks/venda", methods=["POST"])
def webhook_sales():
    # Webhook genérico. Configure um segredo em produção e envie como header X-Webhook-Secret.
    received_secret = request.headers.get("X-Webhook-Secret") or request.args.get("secret")
    if WEBHOOK_SECRET and received_secret != WEBHOOK_SECRET:
        return jsonify({"ok": False, "error": "segredo inválido"}), 401

    data = request.get_json(silent=True) or request.form.to_dict() or {}
    raw = json.dumps(data, ensure_ascii=False)

    # Campos comuns em plataformas variam. Este parser aceita nomes genéricos.
    status = str(data.get("status") or data.get("event") or data.get("transaction_status") or "aprovada").lower()
    approved = any(word in status for word in ["approved", "aprov", "paid", "pago", "purchase_approved"])
    amount = data.get("amount") or data.get("price") or data.get("value") or data.get("total")
    if amount is None and isinstance(data.get("transaction"), dict):
        amount = data["transaction"].get("value")
    try:
        amount = float(str(amount or 0).replace(",", "."))
    except Exception:
        amount = 0.0

    buyer = data.get("buyer") if isinstance(data.get("buyer"), dict) else {}
    product_payload = data.get("product") if isinstance(data.get("product"), dict) else {}
    buyer_name = data.get("buyer_name") or buyer.get("name") or data.get("name") or ""
    buyer_email = data.get("buyer_email") or buyer.get("email") or data.get("email") or ""
    product_title = data.get("product_name") or product_payload.get("name") or data.get("product") or ""
    platform = data.get("platform") or data.get("source") or "Webhook"
    external_id = str(data.get("id") or data.get("transaction_id") or data.get("order_id") or sha256(raw.encode()).hexdigest()[:16])

    conn = db_conn()
    existing = conn.execute("SELECT id FROM sales WHERE external_id = ?", (external_id,)).fetchone()
    if existing:
        conn.close()
        return jsonify({"ok": True, "message": "venda já importada"})

    product_id = None
    if product_title:
        prod = conn.execute("SELECT id FROM products WHERE lower(title) LIKE ? LIMIT 1", (f"%{str(product_title).lower()}%",)).fetchone()
        if prod:
            product_id = prod["id"]

    conn.execute(
        """
        INSERT INTO sales (product_id, buyer_name, buyer_email, platform, amount, status, external_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (product_id, buyer_name, buyer_email, platform, amount, "aprovada" if approved else "pendente", external_id, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/maquina-de-renda")
@login_required
def income_machine():
    return render_template("income_machine.html", ideas=PRODUCT_IDEAS, niches=NICHES)


@app.route("/plano-30-dias")
@login_required
def plan_30_days():
    return render_template("plan_30_days.html", daily_plan=DAILY_PLAN)


@app.route("/laboratorio-oferta", methods=["GET", "POST"])
@login_required
def offer_lab():
    result = None
    form = {"title": "", "target": "", "promise": "", "price": "29.90", "channel": ""}
    if request.method == "POST":
        form = request.form.to_dict()
        try:
            price = float(str(form.get("price") or 0).replace(",", "."))
        except Exception:
            price = 0.0
        result = estimate_offer_score(
            form.get("title", ""),
            form.get("target", ""),
            form.get("promise", ""),
            price,
            form.get("channel", ""),
        )
        result["suggested_headline"] = f"{form.get('title','Produto Digital')}: material pronto para {form.get('target','seu público')}"
        result["suggested_cta"] = "Baixe o material, adapte para sua realidade e comece usando hoje."
        result["next_steps"] = [
            "Criar uma amostra grátis de 1 a 3 páginas.",
            "Postar 3 vídeos mostrando o problema que o produto resolve.",
            "Mandar a oferta para pessoas do público certo, sem spam.",
            "Anotar as dúvidas e melhorar a página de venda.",
        ]
    return render_template("offer_lab.html", result=result, form=form)



@app.route("/produtos/<int:product_id>/pacote-cliente")
@login_required
def product_customer_package(product_id):
    product = get_product(product_id)
    if not product:
        abort(404)
    filename = f"{slugify(product['title'])}-pacote-cliente-final.zip"
    out = EXPORT_DIR / filename
    with ZipFile(out, "w", ZIP_DEFLATED) as z:
        for name, content in build_customer_package_files(product).items():
            z.writestr(name, content.encode("utf-8"))
    return send_file(out, as_attachment=True)


@app.route("/produtos/<int:product_id>/criativos-venda")
@login_required
def product_ad_creatives(product_id):
    product = get_product(product_id)
    if not product:
        abort(404)
    content = build_ad_creatives(product)
    data = io.BytesIO(content.encode("utf-8-sig"))
    return send_file(data, mimetype="text/markdown", as_attachment=True, download_name=f"criativos-venda-{slugify(product['title'])}.md")


@app.route("/produtos/<int:product_id>/duplicar", methods=["POST"])
@login_required
def product_duplicate(product_id):
    product = get_product(product_id)
    if not product:
        abort(404)
    now = datetime.utcnow().isoformat()
    conn = db_conn()
    new_title = product["title"] + " — Cópia"
    slug = unique_public_slug(new_title, conn)
    conn.execute(
        """
        INSERT INTO products (title, niche, product_type, discipline, school_level, target_audience, promise, price, status, checkout_link, platform, content, sales_page, social_posts, prompt_pack, public_slug, public_enabled, lead_magnet_title, lead_magnet_content, guarantee_days, bonus_stack, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (new_title, product["niche"], product["product_type"], product.get("discipline") or "Todas as disciplinas", product.get("school_level") or "Ensino fundamental", product["target_audience"], product.get("promise") or "", float(product.get("price") or 0), "rascunho", "", product.get("platform") or "Manual", product.get("content") or "", product.get("sales_page") or "", product.get("social_posts") or "", product.get("prompt_pack") or "", slug, 1, product.get("lead_magnet_title") or "Amostra grátis", product.get("lead_magnet_content") or "", int(product.get("guarantee_days") or 7), product.get("bonus_stack") or "", now, now),
    )
    new_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()
    conn.close()
    flash("Produto duplicado. Ajuste título, preço e checkout para testar outra oferta.", "success")
    return redirect(url_for("product_detail", product_id=new_id))


@app.route("/produtos/<int:product_id>/pacote-lancamento")
@login_required
def product_launch_package(product_id):
    product = get_product(product_id)
    if not product:
        abort(404)
    filename = f"{slugify(product['title'])}-pacote-lancamento.zip"
    out = EXPORT_DIR / filename
    with ZipFile(out, "w", ZIP_DEFLATED) as z:
        for name, content in build_package_files(product).items():
            z.writestr(name, content.encode("utf-8"))
    return send_file(out, as_attachment=True)


@app.route("/produtos/<int:product_id>/calendario")
@login_required
def product_calendar(product_id):
    product = get_product(product_id)
    if not product:
        abort(404)
    calendar = build_launch_calendar(product)
    funnel = build_sales_funnel(product)
    checklist = build_offer_checklist(product)
    score = estimate_offer_score(product["title"], product["target_audience"], product["promise"] or "", float(product["price"] or 0), product["platform"] or "")
    return render_template("product_calendar.html", product=product, calendar=calendar, funnel=funnel, checklist=checklist, score=score)


@app.route("/vendas/exportar.csv")
@login_required
def sales_export_csv():
    conn = db_conn()
    rows = conn.execute(
        """
        SELECT sales.created_at, products.title AS product_title, sales.buyer_name, sales.buyer_email,
               sales.platform, sales.status, sales.amount, sales.external_id
        FROM sales LEFT JOIN products ON products.id = sales.product_id
        ORDER BY sales.created_at DESC
        """
    ).fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["data", "produto", "cliente", "email", "plataforma", "status", "valor", "id_externo"])
    for r in rows:
        writer.writerow([r["created_at"], r["product_title"] or "", r["buyer_name"] or "", r["buyer_email"] or "", r["platform"], r["status"], r["amount"], r["external_id"] or ""])
    data = io.BytesIO(output.getvalue().encode("utf-8-sig"))
    return send_file(data, mimetype="text/csv", as_attachment=True, download_name="vendas-renda-digital-ia.csv")


@app.route("/leads")
@login_required
def leads_page():
    conn = db_conn()
    rows = conn.execute(
        """
        SELECT leads.*, products.title AS product_title
        FROM leads LEFT JOIN products ON products.id = leads.product_id
        ORDER BY leads.created_at DESC
        """
    ).fetchall()
    total_visits = conn.execute("SELECT COUNT(*) AS c FROM visits").fetchone()["c"]
    total_leads = conn.execute("SELECT COUNT(*) AS c FROM leads").fetchone()["c"]
    conn.close()
    conv = round((total_leads / total_visits) * 100, 2) if total_visits else 0
    return render_template("leads.html", leads=[row_to_dict(r) for r in rows], total_visits=total_visits, total_leads=total_leads, conversion_rate=conv)


@app.route("/leads/exportar.csv")
@login_required
def leads_export_csv():
    conn = db_conn()
    rows = conn.execute(
        """
        SELECT leads.created_at, products.title AS product_title, leads.name, leads.email, leads.whatsapp, leads.source, leads.note
        FROM leads LEFT JOIN products ON products.id = leads.product_id
        ORDER BY leads.created_at DESC
        """
    ).fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["data", "produto", "nome", "email", "whatsapp", "origem", "observacao"])
    for r in rows:
        writer.writerow([r["created_at"], r["product_title"] or "", r["name"] or "", r["email"] or "", r["whatsapp"] or "", r["source"] or "", r["note"] or ""])
    data = io.BytesIO(output.getvalue().encode("utf-8-sig"))
    return send_file(data, mimetype="text/csv", as_attachment=True, download_name="leads-renda-digital-ia.csv")


# ----------------------------- Campanhas e vídeos -----------------------------

def ensure_social_tables():
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS social_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER,
            platform TEXT NOT NULL,
            publish_at TEXT,
            post_type TEXT DEFAULT 'video_curto',
            caption TEXT,
            hashtags TEXT,
            status TEXT DEFAULT 'rascunho',
            video_file TEXT,
            external_url TEXT,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(product_id) REFERENCES products(id)
        )
        """
    )
    conn.commit()
    conn.close()


def public_base_url() -> str:
    return (PUBLIC_BASE_URL or request.host_url.rstrip("/")).rstrip("/")


def product_public_url(product: Dict[str, Any]) -> str:
    slug = product.get("public_slug") or slugify(product.get("title", "produto"))
    return f"{public_base_url()}/p/{slug}"


def build_video_script(product: Dict[str, Any]) -> List[Dict[str, str]]:
    title = product.get("title") or "Mega Kit Professor Total"
    price = money(float(product.get("price") or 47))
    discipline = product.get("discipline") or "Todas as disciplinas"
    level = product.get("school_level") or "Ensino fundamental"
    return [
        {"scene":"1", "text":"Professor, você ainda monta atividade do zero?", "small":"Economize tempo com material pronto"},
        {"scene":"2", "text":title[:90], "small":f"{discipline} • {level}"},
        {"scene":"3", "text":"Atividades + gabarito + orientação", "small":"Organizado para adaptar e usar"},
        {"scene":"4", "text":"Baixe uma amostra grátis", "small":"Veja o material por dentro antes de comprar"},
        {"scene":"5", "text":f"Oferta inicial: {price}", "small":"Link na bio, página ou WhatsApp"},
    ]


def build_social_campaign(product: Dict[str, Any], days: int = 30) -> List[Dict[str, str]]:
    title = product.get("title") or "Mega Kit Professor Total"
    price = money(float(product.get("price") or 47))
    url = product_public_url(product)
    hooks = [
        "Professor, você ainda monta atividade do zero?",
        "Material pronto para economizar tempo na semana.",
        "Atividades, gabarito e orientação em um só lugar.",
        "Baixe a amostra grátis e veja por dentro.",
        "Ideal para professor, reforço escolar e pais.",
        "Quer uma aula mais organizada sem começar do zero?",
        "Kit escolar pronto para adaptar e imprimir.",
        "Pare de perder horas montando atividades simples.",
        "Veja um material escolar digital pronto para usar.",
        "Oferta inicial para quem quer praticidade na escola.",
    ]
    platforms = ["Instagram Reels", "TikTok", "Facebook Page", "WhatsApp"]
    rows = []
    for day in range(1, days + 1):
        hook = hooks[(day - 1) % len(hooks)]
        platform = platforms[(day - 1) % len(platforms)]
        if platform == "WhatsApp":
            caption = (
                f"Oi, pessoal! Preparei uma amostra grátis do {title}. "
                f"É um material digital escolar com atividades, gabarito e orientação para adaptar. "
                f"Quem quiser ver por dentro, acesse: {url}"
            )
        elif platform == "Facebook Page":
            caption = (
                f"{hook}\n\n{title} reúne atividades prontas, orientação e gabarito para facilitar a rotina. "
                f"Tem amostra grátis para ver antes. Preço inicial: {price}.\n\nAcesse: {url}"
            )
        else:
            caption = (
                f"{hook}\n\n{title}\n✅ atividades prontas\n✅ gabarito\n✅ orientação\n✅ amostra grátis\n\nAcesse: {url}"
            )
        rows.append({
            "day": str(day),
            "platform": platform,
            "post_type": "video_curto" if platform in ["Instagram Reels", "TikTok"] else "post_texto",
            "caption": caption,
            "hashtags": "#professores #atividadesescolares #bncc #educacao #reforcoescolar #aulasprontas",
        })
    return rows


def _wrap_text(text, draw, font, max_width):
    words = str(text).split()
    lines, current = [], ""
    for word in words:
        test = (current + " " + word).strip()
        try:
            width = draw.textbbox((0, 0), test, font=font)[2]
        except Exception:
            width = draw.textlength(test, font=font)
        if width <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def generate_sales_video(product: Dict[str, Any]) -> Path:
    """Gera um MP4 vertical simples para Reels/TikTok/Shorts sem depender de editor externo."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        import imageio.v2 as imageio
        import numpy as np
    except Exception as exc:
        raise RuntimeError("Para gerar vídeo, instale: pip install pillow imageio imageio-ffmpeg") from exc

    title = product.get("title") or "produto"
    out = EXPORT_DIR / f"{slugify(title)}-video-venda.mp4"
    W, H = 1080, 1920
    fps = 24
    seconds_per_scene = 2.8
    frames_per_scene = int(fps * seconds_per_scene)

    def font(size, bold=False):
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ]
        for c in candidates:
            try:
                return ImageFont.truetype(c, size)
            except Exception:
                pass
        return ImageFont.load_default()

    big = font(82, True)
    mid = font(44, False)
    small = font(34, False)
    badge_font = font(32, True)
    scenes = build_video_script(product)
    arrays = []
    for idx, scene in enumerate(scenes):
        img = Image.new("RGB", (W, H), (10, 10, 25))
        draw = ImageDraw.Draw(img)
        # gradiente premium simples
        for y in range(H):
            r = 12 + int(55 * y / H)
            g = 10 + int(24 * (1 - y / H))
            b = 32 + int(75 * (1 - y / H))
            draw.line((0, y, W, y), fill=(r, g, b))
        draw.ellipse((-250, -180, 660, 760), fill=(82, 57, 168))
        draw.ellipse((620, 1250, 1380, 2040), fill=(20, 130, 120))
        draw.rounded_rectangle((70, 85, 1010, 1835), radius=64, outline=(255,255,255), width=4)
        draw.rounded_rectangle((110, 125, 420, 195), radius=35, fill=(36,245,208))
        draw.text((145, 143), "RENDA DIGITAL IA", fill=(5,5,10), font=badge_font)
        draw.text((120, 310), f"Cena {scene['scene']}/5", fill=(190, 180, 255), font=small)
        y = 560
        for line in _wrap_text(scene["text"], draw, big, 840)[:5]:
            draw.text((120, y), line, fill=(255, 255, 255), font=big)
            y += 100
        y += 35
        for line in _wrap_text(scene["small"], draw, mid, 840)[:4]:
            draw.text((120, y), line, fill=(220, 215, 255), font=mid)
            y += 58
        draw.rounded_rectangle((120, 1535, 960, 1645), radius=40, fill=(143, 92, 255))
        draw.text((170, 1565), "Amostra grátis • link na bio", fill=(255,255,255), font=mid)
        arr = np.array(img)
        for _ in range(frames_per_scene):
            arrays.append(arr)
    with imageio.get_writer(str(out), fps=fps, codec="libx264", quality=8, pixelformat="yuv420p", macro_block_size=1) as writer:
        for arr in arrays:
            writer.append_data(arr)
    return out


def social_queue_rows(limit=80):
    ensure_social_tables()
    conn = db_conn()
    rows = conn.execute(
        """
        SELECT social_queue.*, products.title AS product_title, products.public_slug
        FROM social_queue LEFT JOIN products ON products.id = social_queue.product_id
        ORDER BY social_queue.publish_at ASC, social_queue.id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [row_to_dict(r) for r in rows]


@app.route("/automacao-posts")
@app.route("/campanhas")
@login_required
def social_automation():
    ensure_social_tables()
    conn = db_conn()
    products = conn.execute("SELECT * FROM products ORDER BY created_at DESC").fetchall()
    conn.close()
    api_status = {
        "Facebook Page": bool(FACEBOOK_PAGE_ID and FACEBOOK_PAGE_ACCESS_TOKEN),
        "Instagram Reels": bool(INSTAGRAM_ACCOUNT_ID and FACEBOOK_PAGE_ACCESS_TOKEN and public_base_url()),
        "TikTok": bool(TIKTOK_ACCESS_TOKEN),
        "YouTube Shorts": bool(YOUTUBE_READY),
    }
    return render_template(
        "auto_posts.html",
        products=[row_to_dict(p) for p in products],
        queue=social_queue_rows(),
        api_status=api_status,
        public_base_url=public_base_url(),
    )


@app.route("/produtos/<int:product_id>/gerar-fila-social", methods=["POST"])
@login_required
def generate_social_queue(product_id):
    product = get_product(product_id)
    if not product:
        abort(404)
    ensure_social_tables()
    days = int(request.form.get("days") or 30)
    days = max(7, min(days, 60))
    from datetime import timedelta
    campaign = build_social_campaign(product, days)
    conn = db_conn()
    now = datetime.utcnow().isoformat()
    conn.execute("DELETE FROM social_queue WHERE product_id = ? AND status = 'rascunho'", (product_id,))
    for item in campaign:
        publish_day = date.today() + timedelta(days=int(item["day"]) - 1)
        publish_at = f"{publish_day.isoformat()}T09:00:00"
        conn.execute(
            """
            INSERT INTO social_queue (product_id, platform, publish_at, post_type, caption, hashtags, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'rascunho', ?, ?)
            """,
            (product_id, item["platform"], publish_at, item["post_type"], item["caption"], item["hashtags"], now, now),
        )
    conn.commit()
    conn.close()
    flash(f"Fila de {days} dias criada para posts, vídeos e mensagens.", "success")
    return redirect(url_for("social_automation"))


@app.route("/produtos/<int:product_id>/video-venda.mp4")
@login_required
def download_sales_video(product_id):
    """Compatibilidade com links antigos .mp4.
    Em algumas hospedagens, links terminados em .mp4 podem ser tratados de forma diferente.
    Por isso a interface nova usa /gerar-video e /baixar-video, mas mantemos esta rota.
    """
    product = get_product(product_id)
    if not product:
        abort(404)
    try:
        out = generate_sales_video(product)
        return send_file(out, as_attachment=True, download_name=out.name, mimetype="video/mp4", conditional=False, max_age=0)
    except Exception as exc:
        flash(f"Não foi possível gerar o vídeo agora: {exc}", "error")
        return redirect(url_for("product_detail", product_id=product_id))


@app.route("/produtos/<int:product_id>/gerar-video")
@login_required
def video_generate_page(product_id):
    product = get_product(product_id)
    if not product:
        abort(404)
    try:
        out = generate_sales_video(product)
        return render_template("video_result.html", title="Vídeo gerado", product=product, video_file=out.name, video_size=out.stat().st_size)
    except Exception as exc:
        flash(f"Erro ao gerar vídeo: {exc}", "error")
        return redirect(url_for("product_detail", product_id=product_id))


@app.route("/produtos/<int:product_id>/baixar-video")
@login_required
def video_download_safe(product_id):
    product = get_product(product_id)
    if not product:
        abort(404)
    try:
        out = generate_sales_video(product)
        return send_file(out, as_attachment=True, download_name=out.name, mimetype="video/mp4", conditional=False, max_age=0)
    except Exception as exc:
        flash(f"Erro ao baixar vídeo: {exc}", "error")
        return redirect(url_for("product_detail", product_id=product_id))


@app.route("/produtos/<int:product_id>/assistir-video")
@login_required
def video_stream_safe(product_id):
    product = get_product(product_id)
    if not product:
        abort(404)
    try:
        out = generate_sales_video(product)
        return send_file(out, mimetype="video/mp4", conditional=False, max_age=0)
    except Exception as exc:
        flash(f"Erro ao abrir vídeo: {exc}", "error")
        return redirect(url_for("product_detail", product_id=product_id))


@app.route("/v/<int:product_id>/video-venda.mp4")
def public_sales_video(product_id):
    product = get_product(product_id)
    if not product or not product.get("public_enabled"):
        abort(404)
    out = generate_sales_video(product)
    return send_file(out, mimetype="video/mp4", conditional=False, max_age=0)


@app.route("/produtos/<int:product_id>/pacote-social-auto")
@login_required
def download_social_auto_package(product_id):
    product = get_product(product_id)
    if not product:
        abort(404)
    campaign = build_social_campaign(product, 30)
    video = generate_sales_video(product)
    out = EXPORT_DIR / f"{slugify(product['title'])}-pacote-social.zip"
    rows = io.StringIO()
    writer = csv.writer(rows)
    writer.writerow(["dia", "plataforma", "tipo", "legenda", "hashtags"])
    for item in campaign:
        writer.writerow([item["day"], item["platform"], item["post_type"], item["caption"], item["hashtags"]])
    script_text = "\n\n".join([f"Cena {s['scene']}: {s['text']}\nTexto menor: {s['small']}" for s in build_video_script(product)])
    api_instructions = f"""
# Automação oficial de postagem

Este pacote contém vídeo MP4, legendas e calendário de 30 dias.

## O que já fica automático dentro do sistema
- Geração de vídeo vertical MP4 para Reels/TikTok/Shorts.
- Geração de calendário de posts de 30 dias.
- Legendas, hashtags e mensagens de WhatsApp.
- Fila interna de publicação.
- Postagem automática em Facebook Page quando FACEBOOK_PAGE_ID e FACEBOOK_PAGE_ACCESS_TOKEN estiverem configurados.

## O que precisa de conta/API oficial
- Instagram Reels: precisa conta Profissional/Creator conectada à página e permissão de Content Publishing.
- TikTok: precisa Content Posting API, OAuth e aprovação do app.
- YouTube Shorts: precisa YouTube Data API com OAuth.

Página pública do produto: {product_public_url(product)}
Vídeo público, se o sistema estiver online: {public_base_url()}/v/{product['id']}/video-venda.mp4
""".strip()
    with ZipFile(out, "w", ZIP_DEFLATED) as z:
        z.write(video, arcname="01-video-venda-reels-tiktok-shorts.mp4")
        z.writestr("02-calendario-posts-30-dias.csv", rows.getvalue().encode("utf-8-sig"))
        z.writestr("03-roteiro-do-video.txt", script_text)
        z.writestr("04-legendas-e-hashtags.txt", "\n\n---\n\n".join([f"Dia {i['day']} - {i['platform']}\n{i['caption']}\n{i['hashtags']}" for i in campaign]))
        z.writestr("05-configurar-apis-oficiais.md", api_instructions)
    return send_file(out, as_attachment=True, download_name=out.name)


@app.route("/social/<int:item_id>/marcar-postado", methods=["POST"])
@login_required
def mark_social_posted(item_id):
    ensure_social_tables()
    conn = db_conn()
    conn.execute("UPDATE social_queue SET status='postado_manual', updated_at=? WHERE id=?", (datetime.utcnow().isoformat(), item_id))
    conn.commit()
    conn.close()
    flash("Post marcado como publicado.", "success")
    return redirect(url_for("social_automation"))


@app.route("/social/<int:item_id>/publicar-facebook", methods=["POST"])
@login_required
def publish_facebook_page(item_id):
    ensure_social_tables()
    if not (FACEBOOK_PAGE_ID and FACEBOOK_PAGE_ACCESS_TOKEN):
        flash("Configure FACEBOOK_PAGE_ID e FACEBOOK_PAGE_ACCESS_TOKEN no .env para publicar automaticamente no Facebook Page.", "danger")
        return redirect(url_for("social_automation"))
    conn = db_conn()
    row = conn.execute(
        """
        SELECT social_queue.*, products.public_slug, products.title
        FROM social_queue LEFT JOIN products ON products.id = social_queue.product_id
        WHERE social_queue.id = ?
        """,
        (item_id,),
    ).fetchone()
    if not row:
        conn.close()
        abort(404)
    product = {"public_slug": row["public_slug"], "title": row["title"]}
    message = (row["caption"] or "") + "\n\n" + (row["hashtags"] or "")
    link = product_public_url(product)
    try:
        import urllib.parse, urllib.request
        data = urllib.parse.urlencode({
            "message": message,
            "link": link,
            "access_token": FACEBOOK_PAGE_ACCESS_TOKEN,
        }).encode("utf-8")
        req = urllib.request.Request(f"https://graph.facebook.com/v20.0/{FACEBOOK_PAGE_ID}/feed", data=data, method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8")
        conn.execute("UPDATE social_queue SET status='postado_auto', external_url=?, error='', updated_at=? WHERE id=?", (body[:400], datetime.utcnow().isoformat(), item_id))
        conn.commit()
        flash("Post enviado para a Facebook Page pela API oficial.", "success")
    except Exception as exc:
        conn.execute("UPDATE social_queue SET status='erro', error=?, updated_at=? WHERE id=?", (str(exc)[:500], datetime.utcnow().isoformat(), item_id))
        conn.commit()
        flash(f"Não foi possível publicar automaticamente: {exc}", "danger")
    finally:
        conn.close()
    return redirect(url_for("social_automation"))




# ----------------------------- Camada profissional e realista -----------------------------

PROFESSIONAL_NOTICE = (
    "Este sistema ajuda a criar produto, oferta, página, criativos e rotina de divulgação. "
    "Ele não garante vendas automáticas. Central comercial depende de produto revisado, checkout funcionando, "
    "tráfego, atendimento, consistência e teste de oferta."
)

REALISTIC_CHANNELS = [
    {"name": "TikTok/Reels/Shorts", "role": "alcance", "cadence": "1 a 3 vídeos curtos por dia", "note": "mostrar dor, produto por dentro e amostra grátis"},
    {"name": "WhatsApp", "role": "conversa", "cadence": "responder interessados diariamente", "note": "sem spam; enviar amostra para quem pediu"},
    {"name": "Facebook/Grupos", "role": "comunidade", "cadence": "2 a 4 posts úteis por semana", "note": "respeitar regras e ajudar antes de vender"},
    {"name": "Página pública", "role": "conversão", "cadence": "sempre ativa", "note": "amostra grátis + botão de compra + FAQ"},
]


def product_scorecard(product: Dict[str, Any]) -> Dict[str, Any]:
    """Auditoria comercial honesta para evitar produto bonito, mas fraco de vender."""
    title = (product.get("title") or "").strip()
    promise = (product.get("promise") or "").strip()
    target = (product.get("target_audience") or "").strip()
    price = float(product.get("price") or 0)
    checkout = bool((product.get("checkout_link") or "").strip())
    content = product.get("content") or ""
    sales_page = product.get("sales_page") or ""
    lead = product.get("lead_magnet_content") or ""
    score = 0
    items = []
    def add(name, ok, fix, points):
        nonlocal score
        if ok: score += points
        items.append({"name": name, "ok": bool(ok), "fix": fix, "points": points})
    add("Título específico", len(title) >= 22 and any(w in title.lower() for w in ["kit", "atividades", "banco", "simulado", "professor"]), "Deixe claro o tipo de produto, público e resultado prático.", 12)
    add("Público definido", len(target) >= 18 and any(w in target.lower() for w in ["professor", "pais", "reforço", "escola", "tutor", "estudante"]), "Evite público genérico. Escreva exatamente quem compra.", 12)
    add("Promessa honesta", len(promise) >= 35 and not any(w in promise.lower() for w in ["garantido", "milagroso", "100%", "fácil demais"]), "Troque promessa exagerada por economia de tempo, organização e praticidade.", 12)
    add("Preço de entrada vendável", 17 <= price <= 97, "Para primeira oferta, teste R$ 27, R$ 47 ou R$ 67 antes de subir preço.", 10)
    add("Checkout configurado", checkout, "Cadastre o pacote final na Kiwify/Hotmart e cole o link do checkout.", 14)
    add("Produto com volume mínimo", len(content) > 6000, "Gere e revise o pacote final. Produto muito curto reduz valor percebido.", 12)
    add("Página de venda completa", len(sales_page) > 2200 and "perguntas" in sales_page.lower(), "Inclua dor, entrega, bônus, FAQ, garantia e botão de compra.", 10)
    add("Isca grátis", len(lead) > 800, "Ofereça amostra grátis para capturar interessados antes de vender.", 8)
    add("Aviso pedagógico/BNCC", "bncc" in content.lower() and "revise" in content.lower(), "Mantenha aviso de revisão BNCC e currículo local.", 10)
    level = "Pronto para tráfego" if score >= 82 else "Quase pronto" if score >= 65 else "Precisa melhorar antes de anunciar"
    return {"score": min(score, 100), "level": level, "items": items, "notice": PROFESSIONAL_NOTICE}


def build_professional_brand_kit(product: Dict[str, Any]) -> str:
    title = product.get("title", "Produto Digital")
    slug = product.get("public_slug") or slugify(title)
    return f"""
# Kit de marca profissional — {title}

## Posicionamento
Material digital escolar editável para professores, escolas pequenas, reforço escolar, pais e tutores que precisam economizar tempo com atividades organizadas.

## Tom de voz
- Claro, humano e responsável.
- Promessa honesta: praticidade, organização e economia de tempo.
- Nunca usar: "resultado garantido", "material oficial do MEC", "venda automática", "aprenda sem estudar".

## Frase curta da marca
Atividades prontas para revisar, adaptar e aplicar com mais organização.

## Bio curta para rede social
Materiais digitais escolares para professores, reforço e pais. Atividades, gabaritos e orientações editáveis. Baixe uma amostra grátis no link.

## Identidade visual sugerida
- Fundo escuro premium ou fundo claro limpo.
- Destaque em roxo/azul/verde para tecnologia e educação.
- Imagens do produto em mockup: capa, folhas, celular e notebook.
- Evitar excesso de promessas e excesso de texto pequeno.

## Links
- Página pública: /p/{slug}
- Checkout: {product.get('checkout_link') or 'COLE_AQUI_O_LINK_DO_CHECKOUT'}

## Aviso obrigatório
Este material é editável e deve ser revisado antes da aplicação. Os campos BNCC são referência organizacional e precisam de conferência com o currículo local.
""".strip()


def build_realistic_sales_plan(product: Dict[str, Any]) -> str:
    title = product.get("title", "Produto Digital")
    price = money(float(product.get("price") or 47))
    url = product_public_url(product)
    return f"""
# Plano realista de vendas — {title}

{PROFESSIONAL_NOTICE}

## Meta inicial realista
Antes de pensar em escala, o objetivo é validar se pessoas reais se interessam pelo produto.

### Semana 1 — validação
- Publicar 7 a 14 vídeos curtos mostrando a dor do público.
- Enviar a página pública para contatos que fazem sentido, sem spam.
- Capturar pelo menos 20 interessados na amostra grátis.
- Conversar com interessados e anotar dúvidas.
- Ajustar página de venda com base nas objeções.

### Semana 2 — primeiras vendas
- Postar prova de utilidade: mostrar páginas, gabarito, organização e bônus.
- Testar preço de entrada: {price}.
- Oferecer amostra grátis antes da venda.
- Fazer atendimento simples no WhatsApp.
- Registrar cliques, leads, conversas e vendas.

### Semana 3 — melhoria
- Melhorar capa, título e bônus.
- Criar variações: kit por disciplina, kit por ano e mega kit completo.
- Gravar vídeo mostrando o produto por dentro.
- Usar depoimentos reais somente quando existirem.

### Semana 4 — escala com cuidado
- Repetir os posts que geraram leads.
- Testar anúncios pagos somente após primeira venda orgânica.
- Abrir afiliados apenas depois de produto revisado e checkout testado.

## Indicadores simples
- Visitas na página pública: {url}
- Leads capturados
- Cliques no checkout
- Vendas aprovadas
- Dúvidas mais repetidas
- Reembolsos ou reclamações

## Regra de decisão
Se gerar leads, mas não vender: melhore a oferta e o preço.
Se não gerar leads: melhore vídeos, título e público.
Se vender e gerar dúvidas: melhore entrega e FAQ.
""".strip()


def build_professional_video_playbook(product: Dict[str, Any]) -> str:
    title = product.get("title", "Produto Digital")
    return f"""
# Playbook de vídeos profissionais — {title}

## Padrão do vídeo
- Duração: 15 a 25 segundos.
- Formato: vertical 1080x1920.
- Estrutura: dor forte + solução visual + amostra grátis + CTA.
- Evitar promessas exageradas.

## 7 ângulos de vídeo
1. Dor: "Professor, você perde horas montando atividade?"
2. Demonstração: mostrar o que vem dentro do kit.
3. Antes/depois: folha em branco vs material pronto.
4. Amostra grátis: convidar para baixar exemplo.
5. Organização: mostrar disciplinas, gabarito e orientação.
6. Objeção: "É editável e precisa ser revisado antes de usar." 
7. Oferta: preço inicial + entrega digital + link.

## Roteiro principal
Cena 1: dor real do público.
Cena 2: promessa honesta.
Cena 3: mockup do produto.
Cena 4: o que vem no pacote.
Cena 5: amostra grátis.
Cena 6: aviso responsável.
Cena 7: chamada para ação.

## Legenda base
Material escolar digital editável para economizar tempo. Baixe uma amostra grátis, veja por dentro e adapte conforme sua turma. Link na página.
""".strip()


def build_professional_package_files(product: Dict[str, Any]) -> Dict[str, str]:
    audit = product_scorecard(product)
    audit_text = "# Auditoria profissional da oferta\n\n" + f"Pontuação: {audit['score']}/100 — {audit['level']}\n\n" + "\n".join([f"- [{'x' if i['ok'] else ' '}] {i['name']} ({i['points']} pts) — {i['fix']}" for i in audit['items']])
    files = {
        "00-LEIA-PRIMEIRO-PROFISSIONAL.md": f"# Pacote profissional\n\n{PROFESSIONAL_NOTICE}\n\nUse este pacote para revisar produto, oferta, criativos, página e rotina de divulgação antes de investir dinheiro em anúncios.\n",
        "01-auditoria-da-oferta.md": audit_text,
        "02-kit-de-marca.md": build_professional_brand_kit(product),
        "03-plano-realista-de-vendas.md": build_realistic_sales_plan(product),
        "04-playbook-videos-profissionais.md": build_professional_video_playbook(product),
        "05-pagina-de-venda-revisada.md": product.get("sales_page") or "",
        "06-criativos-e-whatsapp.md": build_ad_creatives(product),
        "07-mapa-bncc-e-conformidade.md": bncc_map_markdown(product.get("discipline") or "Todas as disciplinas", product.get("school_level") or "Ensino fundamental"),
        "08-checklist-final-de-publicacao.md": build_offer_checklist(product),
    }
    return files


# Sobrescreve/fortalece alguns geradores antigos com linguagem mais profissional.
def build_video_script(product: Dict[str, Any]) -> List[Dict[str, str]]:
    title = product.get("title") or "Mega Kit Professor Total"
    price = money(float(product.get("price") or 47))
    discipline = product.get("discipline") or "Todas as disciplinas"
    level = product.get("school_level") or "Ensino fundamental"
    return [
        {"scene":"1", "tag":"DOR REAL", "text":"Professor, você perde horas montando atividade do zero?", "small":"A rotina pesa quando tudo precisa ser criado na pressa."},
        {"scene":"2", "tag":"SOLUÇÃO", "text":"Um kit escolar pronto para revisar, adaptar e aplicar", "small":f"{discipline} • {level}"},
        {"scene":"3", "tag":"PRODUTO", "text":title[:92], "small":"Folha do aluno, orientação, gabarito e bônus organizados."},
        {"scene":"4", "tag":"POR DENTRO", "text":"Arquivos separados por disciplina", "small":"Mais fácil de encontrar, imprimir, revisar e usar."},
        {"scene":"5", "tag":"CONFIANÇA", "text":"BNCC editável para conferência pedagógica", "small":"Não é material oficial: revise conforme sua turma e currículo local."},
        {"scene":"6", "tag":"AMOSTRA", "text":"Baixe uma amostra grátis antes de comprar", "small":"Veja por dentro e decida com segurança."},
        {"scene":"7", "tag":"OFERTA", "text":f"Oferta inicial: {price}", "small":"Link na página, bio ou WhatsApp."},
    ]


def build_social_campaign(product: Dict[str, Any], days: int = 30) -> List[Dict[str, str]]:
    title = product.get("title") or "Mega Kit Professor Total"
    price = money(float(product.get("price") or 47))
    url = product_public_url(product)
    angles = [
        ("dor", "Professor, você perde tempo montando atividade do zero?"),
        ("demonstração", "Veja por dentro como o material vem organizado."),
        ("amostra", "Baixe uma amostra grátis antes de comprar."),
        ("organização", "Atividades, gabarito e orientação em um só pacote."),
        ("BNCC", "Campos BNCC editáveis para revisar conforme a turma."),
        ("pais/reforço", "Material também ajuda reforço escolar e acompanhamento em casa."),
        ("oferta", f"Oferta inicial: {price}, com entrega digital."),
    ]
    platforms = ["Instagram Reels", "TikTok", "Facebook Page", "WhatsApp", "YouTube Shorts"]
    rows = []
    for day in range(1, days + 1):
        angle, hook = angles[(day - 1) % len(angles)]
        platform = platforms[(day - 1) % len(platforms)]
        if platform == "WhatsApp":
            caption = f"Oi! Preparei uma amostra grátis do {title}. É um material escolar digital editável, com atividades, gabaritos e orientação. Quer ver por dentro? {url}"
        elif platform == "Facebook Page":
            caption = f"{hook}\n\n{title}\n\nMaterial digital editável para professores, reforço escolar e pais. Veja a amostra grátis e confira se faz sentido para você.\n\nAcesse: {url}"
        else:
            caption = f"{hook}\n\n{title}\n✅ atividades prontas\n✅ gabarito separado\n✅ orientação de uso\n✅ amostra grátis\n\nVeja por dentro: {url}"
        rows.append({
            "day": str(day),
            "platform": platform,
            "post_type": "video_curto" if platform in ["Instagram Reels", "TikTok", "YouTube Shorts"] else "post_texto",
            "caption": caption,
            "hashtags": "#professores #atividadesescolares #bncc #educacao #reforcoescolar #materialpedagogico",
            "angle": angle,
        })
    return rows


def generate_sales_video(product: Dict[str, Any]) -> Path:
    """Gera MP4 vertical com visual mais profissional, mockup, ritmo e texto de anúncio."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        import imageio.v2 as imageio
        import numpy as np
    except Exception as exc:
        raise RuntimeError("Para gerar vídeo, instale: pip install pillow imageio imageio-ffmpeg") from exc

    title = product.get("title") or "produto"
    out = EXPORT_DIR / f"{slugify(title)}-video-profissional.mp4"
    W, H = 1080, 1920
    fps = 24
    seconds_per_scene = 2.45
    frames_per_scene = int(fps * seconds_per_scene)

    def font(size, bold=False):
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ]
        for c in candidates:
            try:
                return ImageFont.truetype(c, size)
            except Exception:
                pass
        return ImageFont.load_default()

    big = font(76, True); mid = font(42, False); small = font(30, False); badge_font = font(27, True); mini = font(24, False)
    scenes = build_video_script(product)

    def draw_gradient(draw):
        for y in range(H):
            r = 8 + int(30 * (y/H))
            g = 10 + int(15 * (1-y/H))
            b = 24 + int(50 * (1-y/H))
            draw.line((0, y, W, y), fill=(r, g, b))

    with imageio.get_writer(str(out), fps=fps, codec="libx264", quality=8, pixelformat="yuv420p", macro_block_size=1) as writer:
        for idx, scene in enumerate(scenes):
            for f in range(frames_per_scene):
                t = f / max(1, frames_per_scene - 1)
                img = Image.new("RGB", (W, H), (8, 9, 24))
                draw = ImageDraw.Draw(img)
                draw_gradient(draw)
                # círculos com leve movimento
                shift = int(24 * t)
                draw.ellipse((-260+shift, -180, 650+shift, 735), fill=(75, 45, 160))
                draw.ellipse((650-shift, 1230, 1400-shift, 2040), fill=(18, 120, 116))
                # moldura e progresso
                draw.rounded_rectangle((64, 78, 1016, 1845), radius=58, outline=(255,255,255), width=3)
                progress_w = int((idx + t) / len(scenes) * 900)
                draw.rounded_rectangle((90, 1800, 990, 1818), radius=8, fill=(55, 55, 84))
                draw.rounded_rectangle((90, 1800, 90+progress_w, 1818), radius=8, fill=(36,245,208))
                # badge
                draw.rounded_rectangle((110, 120, 470, 190), radius=34, fill=(36,245,208))
                draw.text((145, 140), "RENDA DIGITAL IA", fill=(5,5,12), font=badge_font)
                draw.rounded_rectangle((110, 220, 380, 274), radius=26, outline=(255,255,255), width=2)
                draw.text((138, 234), scene.get("tag", "VENDA"), fill=(235,232,255), font=badge_font)
                # mockup produto à direita
                mx = 660 + int(20*t); my = 315
                draw.rounded_rectangle((mx, my, mx+270, my+380), radius=28, fill=(245,245,250))
                draw.rounded_rectangle((mx+22, my+28, mx+248, my+92), radius=18, fill=(143,92,255))
                draw.text((mx+42, my+46), "KIT DIGITAL", fill=(255,255,255), font=mini)
                draw.text((mx+36, my+132), "Atividades", fill=(12,12,24), font=small)
                draw.text((mx+36, my+182), "Gabarito", fill=(12,12,24), font=small)
                draw.text((mx+36, my+232), "BNCC", fill=(12,12,24), font=small)
                draw.rounded_rectangle((mx+35, my+300, mx+235, my+342), radius=18, fill=(36,245,208))
                # headline
                y = 760
                for line in _wrap_text(scene["text"], draw, big, 835)[:5]:
                    draw.text((112, y), line, fill=(255,255,255), font=big)
                    y += 92
                y += 24
                for line in _wrap_text(scene["small"], draw, mid, 820)[:4]:
                    draw.text((114, y), line, fill=(225,220,255), font=mid)
                    y += 58
                # CTA fixo
                draw.rounded_rectangle((115, 1542, 965, 1658), radius=42, fill=(143, 92, 255))
                draw.text((165, 1575), "Baixar amostra grátis • Ver por dentro", fill=(255,255,255), font=mid)
                draw.text((115, 1705), "Produto digital editável. Revise antes de usar ou vender.", fill=(185, 181, 216), font=small)
                writer.append_data(np.array(img))
    return out


@app.route("/central-profissional")
@login_required
def professional_center():
    conn = db_conn()
    products = [row_to_dict(r) for r in conn.execute("SELECT * FROM products ORDER BY created_at DESC LIMIT 10").fetchall()]
    conn.close()
    audits = [{"product": p, "audit": product_scorecard(p)} for p in products]
    return render_template("professional_center.html", title="Central profissional", audits=audits, channels=REALISTIC_CHANNELS, notice=PROFESSIONAL_NOTICE)


@app.route("/produtos/<int:product_id>/pacote-profissional")
@login_required
def product_professional_package(product_id):
    product = get_product(product_id)
    if not product:
        abort(404)
    video = generate_sales_video(product)
    out = EXPORT_DIR / f"{slugify(product['title'])}-pacote-profissional.zip"
    with ZipFile(out, "w", ZIP_DEFLATED) as z:
        for name, content in build_professional_package_files(product).items():
            z.writestr(name, content.encode("utf-8"))
        z.write(video, arcname="09-video-profissional-reels-tiktok-shorts.mp4")
    return send_file(out, as_attachment=True, download_name=out.name)


# ---------------------------------------------------------------------------
# v11 — Motor premium de conteúdo, multinichos e páginas extras
# Esta camada melhora os textos gerados para parecerem produto pago, com
# organização comercial, criativos, bônus, funil e biblioteca de ideias.
# ---------------------------------------------------------------------------

PREMIUM_NOTICE = (
    "Estrutura premium realista: produto bonito, promessa clara, bônus, amostra, "
    "copy, funil e calendário. Sem prometer dinheiro fácil ou resultado garantido."
)

TRENDING_BLUEPRINTS = [
    {
        "title": "Kit IA para Pequenos Negócios — Prompts, WhatsApp e Posts Prontos",
        "niche": "IA para pequenos negócios",
        "product_type": "Pacote completo ZIP",
        "target_audience": "MEIs, autônomos, lojas pequenas e prestadores de serviço que querem usar IA sem complicação",
        "promise": "economizar tempo criando mensagens, posts, respostas para clientes, ideias de promoção e organização de atendimento",
        "price": "47.00",
        "discipline": "Não se aplica / produto geral",
        "school_level": "MEI e autônomos",
        "angle": "IA prática para vender e atender melhor, sem linguagem técnica.",
    },
    {
        "title": "Pack Social Media para Salão, Manicure e Barbearia — Posts, Legendas e WhatsApp",
        "niche": "Beleza, estética e atendimento",
        "product_type": "Kit de templates",
        "target_audience": "manicures, barbeiros, cabeleireiras, designers de sobrancelha e profissionais de estética",
        "promise": "deixar o Instagram e o WhatsApp mais profissionais com posts, legendas, agenda e mensagens prontas",
        "price": "37.00",
        "discipline": "Não se aplica / produto geral",
        "school_level": "Profissionais de beleza",
        "angle": "Visual de negócio organizado para atrair confiança e facilitar atendimento.",
    },
    {
        "title": "Planner Financeiro da Família — Controle de Gastos, Dívidas e Metas",
        "niche": "Finanças pessoais e organização",
        "product_type": "Planner PDF",
        "target_audience": "famílias, casais e pessoas que querem organizar gastos sem planilhas complicadas",
        "promise": "enxergar para onde o dinheiro está indo, organizar contas, planejar compras e acompanhar metas mensais",
        "price": "27.00",
        "discipline": "Não se aplica / produto geral",
        "school_level": "Público geral",
        "angle": "Organização financeira educativa, simples e visual, sem promessa de riqueza.",
    },
    {
        "title": "Cardápio Econômico da Semana — Lista de Compras, Marmitas e Rotina da Cozinha",
        "niche": "Casa, organização e rotina",
        "product_type": "Checklist prático",
        "target_audience": "famílias, donas de casa, estudantes e pessoas que querem organizar alimentação e compras",
        "promise": "planejar refeições, evitar desperdício, montar lista de compras e organizar marmitas da semana",
        "price": "19.90",
        "discipline": "Não se aplica / produto geral",
        "school_level": "Famílias e organização doméstica",
        "angle": "Economia de tempo e organização doméstica com material bonito e imprimível.",
    },
    {
        "title": "Kit Marmitas para Vender — Cardápio, Precificação e Atendimento",
        "niche": "Culinária, marmitas e confeitaria",
        "product_type": "Pacote completo ZIP",
        "target_audience": "pessoas que vendem ou querem organizar a venda de marmitas, lanches e comidas caseiras",
        "promise": "organizar cardápio, ficha técnica, preço, divulgação e atendimento pelo WhatsApp de forma mais profissional",
        "price": "47.00",
        "discipline": "Não se aplica / produto geral",
        "school_level": "Público geral",
        "angle": "Material prático para organizar operação e divulgação, sem prometer lucro garantido.",
    },
    {
        "title": "Kit Currículo, Entrevista e LinkedIn Básico — Modelos Prontos para Adaptar",
        "niche": "Carreira, currículo e renda extra",
        "product_type": "Ebook guia prático",
        "target_audience": "jovens, trabalhadores, estudantes e pessoas procurando melhorar currículo e apresentação profissional",
        "promise": "montar currículo mais claro, preparar respostas de entrevista e organizar perfil profissional básico",
        "price": "27.00",
        "discipline": "Não se aplica / produto geral",
        "school_level": "Iniciantes",
        "angle": "Ajuda prática para apresentação profissional, sem prometer emprego garantido.",
    },
    {
        "title": "Pack Canva para Lojas e Prestadores — Posts, Bio, Promoções e Calendário",
        "niche": "Templates, design e redes sociais",
        "product_type": "Calendário de conteúdo",
        "target_audience": "lojas pequenas, MEIs, autônomos e prestadores de serviço que precisam postar com aparência profissional",
        "promise": "organizar 30 dias de conteúdo com ideias de posts, legendas, chamadas e estrutura visual para adaptar",
        "price": "47.00",
        "discipline": "Não se aplica / produto geral",
        "school_level": "Pequenos negócios",
        "angle": "Aparência de agência para quem ainda não pode contratar uma.",
    },
    {
        "title": "Planner Pet Completo — Rotina, Gastos, Vacinas e Cuidados do Dia a Dia",
        "niche": "Pets e rotina de cuidados",
        "product_type": "Planner PDF",
        "target_audience": "tutores de cães e gatos que querem organizar rotina, gastos e cuidados básicos do pet",
        "promise": "centralizar informações do pet, controlar gastos, rotina, consultas e lembretes importantes",
        "price": "19.90",
        "discipline": "Não se aplica / produto geral",
        "school_level": "Público geral",
        "angle": "Organização afetuosa para tutores, sem substituir veterinário.",
    },
    {
        "title": "Mega Kit Professor Total Premium — Atividades, Provas e Materiais Editáveis",
        "niche": "Educação - todas as disciplinas",
        "product_type": "Mega kit de atividades",
        "target_audience": "professores, reforço escolar, escolas pequenas e pais que acompanham estudos em casa",
        "promise": "economizar tempo com atividades, gabaritos, orientações, avaliações rápidas e materiais por disciplina",
        "price": "67.00",
        "discipline": "Todas as disciplinas",
        "school_level": "Ensino fundamental anos iniciais",
        "angle": "Produto educacional organizado e revisável, com campos BNCC editáveis.",
    },
]

# Mais nichos para o seletor. Mantém os antigos e acrescenta ideias comerciais amplas.
_EXTRA_PREMIUM_NICHES = [
    {"name":"Vendas pelo WhatsApp para negócios locais","score":98,"why":"Negócios pequenos precisam responder melhor, apresentar oferta e organizar atendimento sem parecer amador.","examples":["Scripts de atendimento","Respostas prontas","Catálogo textual","Follow-up"],"price":"R$ 27,00 a R$ 97,00"},
    {"name":"Festas, eventos e papelaria digital","score":89,"why":"Convites, checklists, roteiros e organização de festa têm apelo visual e compra rápida.","examples":["Planner de festa","Checklist de aniversário","Mensagens para convidados","Roteiro de evento"],"price":"R$ 17,00 a R$ 67,00"},
    {"name":"Produtividade pessoal e rotina","score":91,"why":"Planners, rotina semanal, metas e organização visual vendem bem como produto digital simples.","examples":["Planner semanal","Rotina de estudos","Mapa de metas","Checklist diário"],"price":"R$ 12,90 a R$ 49,90"},
]
try:
    _names = {n.get("name") for n in NICHES}
    for _n in _EXTRA_PREMIUM_NICHES:
        if _n["name"] not in _names:
            NICHES.insert(0, _n)
except Exception:
    pass


def _premium_model_for(niche: str) -> Dict[str, Any]:
    base = general_model_for(niche)
    premium_by_niche = {
        "IA para pequenos negócios": {
            "promise_word":"usar IA no atendimento, nos posts e na organização do negócio sem complicação",
            "deliverables":["100 prompts separados por finalidade", "30 respostas prontas para WhatsApp", "30 ideias de posts", "roteiro de atendimento", "checklist de implantação em 7 dias", "bônus: calendário de campanhas"],
            "modules":["Comece por aqui: como usar o kit", "Prompts para atendimento", "Prompts para Instagram e Facebook", "Mensagens para WhatsApp", "Ideias de promoções simples", "Calendário de 30 dias", "Checklist de revisão antes de publicar"],
            "warning":"Material educativo e editável. Não promete aumento automático de vendas; o resultado depende de oferta, público, atendimento e divulgação.",
        },
        "Finanças pessoais e organização": {
            "promise_word":"organizar gastos, dívidas, contas e metas de forma simples e visual",
            "deliverables":["planner mensal", "controle de contas", "mapa de dívidas", "desafio de economia", "lista de compras consciente", "checklist de fechamento do mês"],
            "modules":["Diagnóstico financeiro simples", "Mapa de gastos fixos", "Controle de contas e vencimentos", "Plano para dívidas", "Metas e reserva", "Rotina semanal de revisão", "Fechamento do mês"],
            "warning":"Material educativo de organização financeira. Não é consultoria financeira e não promete enriquecimento.",
        },
        "Beleza, estética e atendimento": {
            "promise_word":"deixar atendimento, agenda, mensagens e divulgação com aparência mais profissional",
            "deliverables":["agenda de clientes", "ficha de atendimento", "mensagens para remarcar", "posts para Instagram", "script de confirmação", "checklist do atendimento premium"],
            "modules":["Identidade simples do atendimento", "Agenda e confirmação", "Ficha de cliente", "Mensagens prontas", "Posts de antes/depois sem exagero", "Pacotes e combos", "Pós-atendimento e fidelização"],
            "warning":"Material de organização e comunicação. Procedimentos técnicos devem seguir capacitação profissional e normas aplicáveis.",
        },
        "Casa, organização e rotina": {
            "promise_word":"organizar a rotina da casa, compras, cardápio e tarefas da semana",
            "deliverables":["planner da casa", "cardápio semanal", "lista de compras", "rotina de limpeza", "divisão de tarefas", "checklist de domingo"],
            "modules":["Diagnóstico da rotina", "Cardápio semanal", "Lista de compras", "Rotina de limpeza", "Organização de documentos", "Tarefas da família", "Plano de 7 dias"],
            "warning":"Material de organização doméstica e rotina. Adapte conforme sua casa, orçamento e necessidades.",
        },
        "Culinária, marmitas e confeitaria": {
            "promise_word":"organizar cardápio, precificação, divulgação e atendimento para vender comida com mais clareza",
            "deliverables":["ficha técnica", "cardápio semanal", "modelo de preço", "mensagens para clientes", "checklist de produção", "controle de pedidos"],
            "modules":["Cardápio enxuto", "Ficha técnica", "Precificação básica", "Controle de pedidos", "WhatsApp de atendimento", "Divulgação local", "Checklist de higiene e organização"],
            "warning":"Material de organização comercial. Siga normas sanitárias locais e não prometa lucro garantido.",
        },
        "Carreira, currículo e renda extra": {
            "promise_word":"melhorar apresentação profissional com currículo, perfil e respostas de entrevista mais claros",
            "deliverables":["modelo de currículo", "roteiro de entrevista", "bio profissional", "mensagem para enviar currículo", "checklist LinkedIn básico", "plano de 7 dias"],
            "modules":["Diagnóstico profissional", "Currículo objetivo", "Perfil e bio", "Mensagens de candidatura", "Perguntas de entrevista", "Organização de vagas", "Plano de melhoria"],
            "warning":"Material educativo de carreira. Não garante emprego, entrevista ou contratação.",
        },
        "Templates, design e redes sociais": {
            "promise_word":"criar uma presença digital mais organizada com posts, legendas e calendário prontos para adaptar",
            "deliverables":["30 ideias de posts", "30 legendas", "bio de Instagram", "chamadas de venda", "calendário editorial", "checklist visual"],
            "modules":["Posicionamento simples", "Bio e apresentação", "Pilares de conteúdo", "Legendas prontas", "Calendário de 30 dias", "Campanhas e ofertas", "Checklist antes de postar"],
            "warning":"Material de comunicação e organização. Resultados dependem de constância, oferta e relacionamento com o público.",
        },
        "Pets e rotina de cuidados": {
            "promise_word":"organizar rotina, gastos, documentos e cuidados básicos do pet em um só lugar",
            "deliverables":["ficha do pet", "controle de gastos", "agenda de vacinas", "rotina de alimentação", "checklist de viagem", "contatos importantes"],
            "modules":["Ficha completa do pet", "Rotina diária", "Agenda de saúde", "Gastos e compras", "Checklist de passeio", "Checklist de viagem", "Cuidados e observações"],
            "warning":"Material de organização para tutores. Não substitui atendimento veterinário.",
        },
    }
    custom = premium_by_niche.get(niche, {})
    return {**base, **custom}


def _premium_bonus_stack(title: str, niche: str) -> List[str]:
    return [
        "Bônus 1 — Amostra grátis para captar interessados",
        "Bônus 2 — Checklist de uso rápido em 7 dias",
        "Bônus 3 — Mensagens de WhatsApp prontas",
        "Bônus 4 — Calendário de conteúdo de 30 dias",
        "Bônus 5 — Manual do comprador com passo a passo",
        "Bônus 6 — Página de oferta pronta para copiar",
    ]


def template_content_general(title: str, niche: str, product_type: str, target: str, pages: int, promise: str, discipline: str = "Não se aplica / produto geral", school_level: str = "Público geral") -> str:
    today = date.today().strftime("%d/%m/%Y")
    model = _premium_model_for(niche)
    modules = model.get("modules") or general_model_for(niche).get("modules", [])
    deliverables = model.get("deliverables") or general_model_for(niche).get("examples", [])
    warning = model.get("warning") or general_model_for(niche).get("warning", "Material educativo e editável.")
    promise_word = model.get("promise_word") or promise
    body = [
        f"# {title}",
        "",
        "## Capa do produto",
        f"**Produto:** {title}",
        f"**Formato:** {product_type}",
        f"**Nicho:** {niche}",
        f"**Público-alvo:** {target}",
        f"**Versão:** Premium editável — {today}",
        "",
        "---",
        "",
        "# Apresentação",
        f"Este material foi pensado para {target.lower()} que precisam de uma solução pronta, bonita e organizada para {promise.lower()}.",
        "A proposta não é entregar um texto genérico. A proposta é entregar um pacote com estrutura de produto pago: orientação, modelos, exemplos, checklists, bônus e uma ordem clara de uso.",
        "",
        "## Resultado prático esperado",
        f"Ao usar este material, o comprador terá um caminho mais claro para {promise_word}. O resultado depende de adaptação, execução e realidade de cada pessoa ou negócio.",
        "",
        "## Aviso responsável",
        warning,
        "",
        "---",
        "",
        "# O que vem no pacote",
    ]
    for item in deliverables:
        body.append(f"- **{item}**")
    body += ["", "# Como usar este material em 15 minutos", "1. Leia a página de apresentação.", "2. Escolha o módulo mais urgente para sua realidade.", "3. Copie o modelo pronto.", "4. Substitua os campos pelos seus dados.", "5. Revise antes de publicar, enviar ou imprimir.", "6. Salve uma versão final para reutilizar.", "", "---", "", "# Sumário premium"]
    for i, module in enumerate(modules, 1):
        body.append(f"{i}. {module}")
    body += ["", "---", ""]

    for idx, module in enumerate(modules, 1):
        example = deliverables[(idx - 1) % len(deliverables)] if deliverables else "modelo editável"
        body += [
            f"# Módulo {idx} — {module}",
            "",
            "## Objetivo do módulo",
            f"Ajudar {target.lower()} a aplicar **{module.lower()}** com rapidez, clareza e aparência profissional.",
            "",
            "## Por que isso chama atenção do comprador",
            "Produtos digitais vendem melhor quando o comprador percebe que não está comprando apenas informação, mas sim economia de tempo, organização e um modelo pronto para adaptar.",
            "",
            "## Modelo pronto para copiar e adaptar",
            f"**Uso principal:** {example}",
            "",
            "**Situação atual:** ________________________________________________",
            "**Problema que preciso resolver:** _________________________________",
            "**Minha versão personalizada:** ____________________________________",
            "**Próximo passo prático:** _________________________________________",
            "**Data para revisar:** ____/____/______",
            "",
            "## Exemplo preenchido",
            f"Exemplo: uma pessoa do público {target.lower()} usa este bloco para organizar a rotina, melhorar a comunicação, divulgar com mais clareza ou economizar tempo sem começar tudo do zero.",
            "",
            "## Checklist premium do módulo",
            "- [ ] O modelo foi preenchido com dados reais.",
            "- [ ] A linguagem está simples e confiável.",
            "- [ ] A promessa não está exagerada.",
            "- [ ] O conteúdo pode ser usado no celular e no computador.",
            "- [ ] O comprador entende o próximo passo.",
            "",
            "## Versão rápida para WhatsApp ou redes sociais",
            f"Estou usando um material pronto para {promise.lower()}. Ele traz {example.lower()} e pode ser adaptado para a minha realidade.",
            "",
            "---",
            "",
        ]

    body += [
        "# Bônus premium",
    ]
    for b in _premium_bonus_stack(title, niche):
        body.append(f"- {b}")
    body += [
        "",
        "# Amostra grátis sugerida",
        "Use as próximas páginas como isca digital para captar interessados antes de oferecer o produto completo.",
        "",
        "## Página de amostra",
        f"Você está vendo uma amostra do **{title}**. O pacote completo inclui modelos, checklists, mensagens prontas, calendário e orientação de uso.",
        "",
        "## Mini-checklist da amostra",
        "- [ ] Entendi para quem é o produto.",
        "- [ ] Vi um exemplo por dentro.",
        "- [ ] Sei como adaptar para minha realidade.",
        "- [ ] Posso decidir se quero o pacote completo.",
        "",
        "# Manual de entrega ao comprador",
        "1. Baixe o arquivo ZIP/PDF na plataforma de pagamento.",
        "2. Abra primeiro o arquivo 'Comece por aqui'.",
        "3. Escolha o módulo mais urgente.",
        "4. Preencha os modelos com calma.",
        "5. Use o checklist final antes de publicar, enviar ou imprimir.",
        "",
        "# Checklist final de qualidade",
        "- [ ] Título claro e específico.",
        "- [ ] Produto com começo, meio e fim.",
        "- [ ] Modelos prontos para adaptar.",
        "- [ ] Bônus com valor percebido.",
        "- [ ] Aviso responsável incluído.",
        "- [ ] Linguagem simples, brasileira e profissional.",
        "",
        "# Conclusão",
        "Um produto digital com aparência profissional precisa parecer útil antes mesmo da compra. Nome claro, entrega organizada, bônus coerentes, página de venda honesta e criativos fortes aumentam a confiança do comprador.",
    ]
    return "\n".join(body)


def template_sales_page_general(title: str, niche: str, product_type: str, target: str, price: float, promise: str, discipline: str = "Não se aplica / produto geral", school_level: str = "Público geral") -> str:
    model = _premium_model_for(niche)
    deliverables = model.get("deliverables") or general_model_for(niche).get("examples", [])
    warning = model.get("warning") or "Material educativo e editável."
    bullets = "\n".join([f"- {d}" for d in deliverables])
    bonuses = "\n".join([f"- {b}" for b in _premium_bonus_stack(title, niche)])
    return f"""
# Página de Venda Premium — {title}

## Headline principal
{title}: o pacote digital pronto para {target.lower()} que querem {promise.lower()} sem começar do zero.

## Subheadline
Um {product_type.lower()} com modelos, checklists, mensagens, calendário e orientação prática para deixar tudo mais organizado, bonito e fácil de aplicar.

## Abertura emocional
Você já percebeu como é cansativo montar tudo do zero? Procurar modelo, criar texto, organizar ideias, revisar e ainda tentar deixar com aparência profissional toma tempo. Este material foi criado para encurtar esse caminho.

## Problema que o produto resolve
O público de {niche.lower()} normalmente precisa de três coisas: clareza, rapidez e organização. O {title} entrega uma estrutura pronta para adaptar, diminuindo improviso e aumentando a confiança na hora de usar, postar, enviar ou imprimir.

## O que você recebe no pacote completo
{bullets}

## Bônus incluídos
{bonuses}

## Para quem é
- Para quem quer um material pronto e organizado.
- Para quem não quer perder horas criando tudo sozinho.
- Para quem precisa de algo simples, bonito e editável.
- Para quem quer testar uma estrutura profissional antes de investir em algo maior.

## Para quem não é
- Não é para quem procura promessa milagrosa.
- Não é para quem quer resultado sem aplicar.
- Não é substituto de profissional especializado quando o assunto exigir orientação técnica.

## Benefícios práticos
- Economiza tempo de criação.
- Ajuda a organizar ideias e rotina.
- Melhora a apresentação do material.
- Facilita divulgação e comunicação.
- Pode ser adaptado para a realidade do comprador.

## Como funciona
1. Compre pela plataforma segura.
2. Receba o arquivo digital.
3. Abra o manual 'Comece por aqui'.
4. Escolha o modelo que precisa usar primeiro.
5. Personalize com seus dados.
6. Use o checklist antes de publicar ou enviar.

## Preço de lançamento sugerido
**{money(price)}**

## Chamada para ação
Clique no botão de compra, baixe o material e comece hoje pela primeira página do passo a passo.

## Garantia honesta
Use a garantia da plataforma escolhida e deixe claro no checkout. O produto é digital e foi feito para ajudar na organização e execução, mas o resultado depende do uso correto.

## Aviso responsável
{warning}

## FAQ
**Recebo na hora?** Sim, a entrega pode ser automática pela Kiwify, Hotmart ou plataforma escolhida.

**Posso editar?** Sim. O material foi pensado para adaptação.

**Serve para iniciantes?** Sim. A linguagem é simples e guiada.

**É resultado garantido?** Não. O produto ajuda no processo; não garante vendas, emprego, lucro, cura, aprovação ou qualquer resultado automático.

**Posso usar no celular?** Sim, o material foi pensado para abrir no celular e computador, dependendo do formato exportado.
""".strip()


def template_social_posts_general(title: str, target: str, niche: str, promise: str, discipline: str = "Não se aplica / produto geral", school_level: str = "Público geral") -> str:
    hooks = [
        f"Você ainda cria tudo do zero? O {title} foi feito para cortar esse caminho.",
        f"Se você trabalha com {niche.lower()}, esse kit pode deixar sua rotina mais organizada em poucos minutos.",
        f"Antes: improviso. Depois: modelos prontos, checklist e um passo a passo para adaptar.",
        f"Mostrando por dentro: veja como o {title} entrega estrutura, modelos e ideias prontas.",
        f"O erro de muita gente é vender ou divulgar sem organização. Esse material ajuda a arrumar isso.",
        f"Quer uma amostra grátis antes de comprar? Eu preparei uma página para você ver por dentro.",
        f"Produto digital bom não é texto jogado. É organização, modelo pronto, exemplo e caminho de uso.",
        f"Se você precisa {promise.lower()}, esse pacote foi criado para facilitar sua vida.",
        f"Pare de perder tempo procurando modelo solto. Use uma estrutura completa e adaptável.",
        f"Esse é o tipo de material que parece simples, mas muda a forma como você organiza o trabalho.",
    ]
    captions = []
    for i, h in enumerate(hooks, 1):
        captions.append(f"{h}\n\nO {title} traz modelos, checklists, mensagens e orientação de uso para {target.lower()}. Não é promessa mágica; é material pronto para adaptar e aplicar.\n\nComente EU QUERO ou acesse o link para ver a amostra grátis.\n\n#produtodigital #organização #negociodigital #mei #whatsapp #instagram")
    whatsapp = [
        f"Oi! Preparei o {title}. É um material digital para {target.lower()} que querem {promise.lower()}. Quer que eu te envie uma amostra grátis para ver por dentro?",
        f"Tenho um pacote pronto que pode te ajudar com {niche.lower()}: {title}. Ele vem com modelos, checklists e mensagens prontas. Posso te mandar o link?",
        f"Passando para te mostrar um material novo: {title}. A ideia é economizar tempo e deixar tudo mais profissional, sem começar do zero.",
        f"Se você quiser, te envio a amostra grátis primeiro. Assim você vê se o {title} faz sentido para sua realidade antes de comprar.",
        f"O pacote completo está com preço de lançamento e entrega digital. Ele inclui material principal, bônus, calendário e mensagens prontas.",
    ]
    scripts = [
        f"Roteiro 1 — Dor direta: 'Você ainda perde tempo criando tudo do zero? Eu preparei o {title}, um pacote pronto para {target.lower()}.' Mostre 3 páginas do material e finalize: 'baixe a amostra grátis no link'.",
        f"Roteiro 2 — Por dentro: 'Olha o que vem dentro do {title}'. Mostre módulos, bônus e checklist. CTA: 'quer receber a amostra?'",
        f"Roteiro 3 — Antes e depois: 'Antes você improvisava. Depois você usa um modelo pronto e adapta'. Mostre a transformação visual.",
        f"Roteiro 4 — Lista rápida: '3 coisas que esse kit resolve: organização, rapidez e apresentação profissional'. Feche com link do checkout.",
        f"Roteiro 5 — Oferta honesta: 'Não é milagre. É material pronto para economizar tempo e aplicar com mais clareza'.",
        f"Roteiro 6 — Objeção: 'Será que serve para iniciante?' Responda mostrando o manual comece por aqui.",
        f"Roteiro 7 — Amostra grátis: 'Antes de comprar, veja por dentro'. Mostre a página pública e peça cadastro.",
        f"Roteiro 8 — Bônus: 'Além do material principal, você recebe mensagens, calendário e checklist'.",
    ]
    return "\n".join([
        "# Kit de divulgação premium",
        "",
        "## Ganchos para Reels/TikTok/Shorts",
        *[f"{i}. {h}" for i, h in enumerate(hooks, 1)],
        "",
        "## Legendas prontas",
        *[f"### Legenda {i}\n{c}" for i, c in enumerate(captions, 1)],
        "",
        "## Mensagens para WhatsApp",
        *[f"{i}. {w}" for i, w in enumerate(whatsapp, 1)],
        "",
        "## Roteiros de vídeo",
        *[f"{i}. {s}" for i, s in enumerate(scripts, 1)],
        "",
        "## Hashtags base",
        "#produtodigital #rendaextra #mei #empreendedorismo #whatsappbusiness #instagramparanegocios #organização #templates #ia",
    ])


def build_prompt_pack_general(title: str, niche: str, product_type: str, target: str, pages: int, promise: str, discipline: str = "Não se aplica / produto geral", school_level: str = "Público geral") -> str:
    model = _premium_model_for(niche)
    modules = "\n".join([f"- {m}" for m in model.get("modules", [])])
    deliverables = "\n".join([f"- {d}" for d in model.get("deliverables", [])])
    return f"""
PROMPT PREMIUM — Criar produto digital com aparência de produto pago

Crie um {product_type} chamado "{title}".
Nicho: {niche}
Público-alvo: {target}
Promessa honesta: {promise}
Quantidade aproximada: {pages} páginas
Linguagem: brasileira, clara, comercial, organizada e confiável.

Objetivo: criar um produto que pareça pago, útil e bem estruturado, não um texto simples.

Módulos obrigatórios:
{modules}

Entregáveis obrigatórios:
{deliverables}

Estrutura obrigatória:
1. Capa textual profissional
2. Apresentação com dor do público
3. Promessa honesta sem exagero
4. Sumário organizado
5. Módulos com objetivo, modelo pronto, exemplo preenchido e checklist
6. Bônus com valor percebido
7. Amostra grátis
8. Manual do comprador
9. Checklist final
10. Página de venda completa
11. 10 ganchos de vídeo
12. 10 legendas
13. 5 mensagens de WhatsApp
14. Aviso responsável: {model.get('warning', 'Material educativo e editável.')}

Proibições:
- Não prometer dinheiro fácil.
- Não prometer lucro garantido.
- Não prometer emprego garantido.
- Não prometer cura, emagrecimento ou resultado de saúde.
- Não usar depoimentos falsos.
""".strip()


def build_premium_copy(product: Dict[str, Any]) -> Dict[str, List[str]]:
    title = product.get("title", "Produto Digital")
    target = product.get("target_audience", "público-alvo")
    promise = product.get("promise", "resolver um problema prático")
    niche = product.get("niche", "produto digital")
    headlines = [
        f"{title}: o pacote pronto para {target.lower()} que querem {promise.lower()} sem começar do zero",
        f"Organize sua rotina com o {title}: modelos, checklists e mensagens prontas para adaptar",
        f"Pare de improvisar: use um material digital completo, bonito e pronto para aplicar",
        f"Tudo que você precisa para {promise.lower()} em um único pacote digital",
        f"O atalho organizado para {target.lower()} economizarem tempo com aparência profissional",
    ]
    bullets = [
        "Modelos prontos para copiar, preencher e adaptar",
        "Checklist final para evitar improviso",
        "Amostra grátis para gerar confiança antes da compra",
        "Mensagens de WhatsApp e legendas para divulgar",
        "Manual de uso para o comprador começar sem dúvida",
        "Oferta honesta, sem promessa milagrosa",
    ]
    objections = [
        "Não sei se serve para mim → Baixe a amostra grátis e veja por dentro antes de comprar.",
        "Não tenho experiência → O material vem com passo a passo e exemplos preenchidos.",
        "Tenho medo de ser complicado → A estrutura foi feita para uso simples no celular ou computador.",
        "Será que vale o preço? → O pacote economiza tempo e já vem organizado com bônus e modelos.",
    ]
    ctas = [
        "Baixar amostra grátis",
        "Ver o pacote completo",
        "Quero receber o material",
        "Comprar com entrega digital",
        "Começar agora pelo passo a passo",
    ]
    return {"headlines": headlines, "bullets": bullets, "objections": objections, "ctas": ctas, "niche": [niche]}


def _insert_product_from_form(form: Dict[str, Any]) -> int:
    """Cria um produto a partir da Biblioteca Premium.

    Correção v18: algumas versões anteriores da biblioteca usavam o campo
    ``target`` em vez de ``target_audience``. Em produção isso causava erro
    500 por causa da coluna obrigatória ``target_audience`` no SQLite.
    Aqui normalizamos todos os campos antes de gerar e salvar o produto.
    """
    title = (form.get("title") or "Produto digital premium").strip()
    niche = (form.get("niche") or "Produto digital").strip()
    product_type = (form.get("product_type") or "Pacote digital").strip()
    target = (
        form.get("target_audience")
        or form.get("target")
        or form.get("audience")
        or "pessoas interessadas em uma solução prática, organizada e pronta para adaptar"
    )
    promise = (form.get("promise") or "economizar tempo com um material pronto, bonito e organizado").strip()
    discipline = (form.get("discipline") or "Não se aplica / produto geral").strip()
    school_level = (form.get("school_level") or "Público geral").strip()
    try:
        price = float(str(form.get("price") or 47).replace(",", "."))
    except Exception:
        price = 47.0

    normalized = {
        **form,
        "title": title,
        "niche": niche,
        "product_type": product_type,
        "target_audience": target,
        "promise": promise,
        "discipline": discipline,
        "school_level": school_level,
        "price": price,
    }

    assets = generate_product_assets(normalized)
    now = datetime.utcnow().isoformat()
    conn = db_conn()
    slug = unique_public_slug(title, conn)
    lead_content = build_lead_magnet({**normalized, **assets, "public_slug": slug})
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO products (title, niche, product_type, discipline, school_level, target_audience, promise, price, status, checkout_link, platform, content, sales_page, social_posts, prompt_pack, public_slug, public_enabled, lead_magnet_title, lead_magnet_content, guarantee_days, bonus_stack, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            title, niche, product_type, discipline, school_level, target, promise, price,
            "rascunho", "", "Manual", assets["content"], assets["sales_page"], assets["social_posts"], assets["prompt_pack"], slug, 1,
            f"Amostra grátis — {title}", lead_content, 7, "; ".join(_premium_bonus_stack(title, niche)), now, now,
        ),
    )
    product_id = cur.lastrowid
    conn.commit(); conn.close()
    return product_id


@app.route("/biblioteca-premium")
@login_required
def premium_library():
    return render_template("premium_library.html", title="Biblioteca premium", blueprints=TRENDING_BLUEPRINTS, notice=PREMIUM_NOTICE)


@app.route("/biblioteca-premium/criar/<int:index>", methods=["POST"])
@login_required
def premium_library_create(index: int):
    if index < 0 or index >= len(TRENDING_BLUEPRINTS):
        abort(404)
    form = dict(TRENDING_BLUEPRINTS[index])
    try:
        product_id = _insert_product_from_form(form)
    except Exception as exc:
        traceback.print_exc()
        flash(f"Não foi possível criar este produto agora: {exc}. Tente outro modelo ou veja os logs do RunSite.", "danger")
        return redirect(url_for("premium_library"))
    flash("Produto premium criado com textos mais completos. Revise, coloque checkout e divulgue a página pública.", "success")
    return redirect(url_for("product_detail", product_id=product_id))


@app.route("/copy-premium")
@login_required
def copy_premium_page():
    selected_id = request.args.get("produto", type=int)
    conn = db_conn()
    products = [row_to_dict(r) for r in conn.execute("SELECT * FROM products ORDER BY created_at DESC LIMIT 30").fetchall()]
    selected = None
    if selected_id:
        selected = row_to_dict(conn.execute("SELECT * FROM products WHERE id=?", (selected_id,)).fetchone())
    elif products:
        selected = products[0]
    conn.close()
    copy = build_premium_copy(selected) if selected else None
    return render_template("copy_premium.html", title="Copy premium", products=products, selected=selected, copy=copy)


@app.route("/funil-profissional")
@login_required
def professional_funnel_page():
    selected_id = request.args.get("produto", type=int)
    conn = db_conn()
    products = [row_to_dict(r) for r in conn.execute("SELECT * FROM products ORDER BY created_at DESC LIMIT 30").fetchall()]
    selected = None
    if selected_id:
        selected = row_to_dict(conn.execute("SELECT * FROM products WHERE id=?", (selected_id,)).fetchone())
    elif products:
        selected = products[0]
    conn.close()
    funnel = build_sales_funnel(selected) if selected else "Nenhum produto criado."
    checklist = build_offer_checklist(selected) if selected else "Crie um produto primeiro."
    return render_template("funnel_profissional.html", title="Funil profissional", products=products, selected=selected, funnel=funnel, checklist=checklist)


# ----------------------------- Robô comercial ético -----------------------------

def ensure_robot_tables():
    ensure_social_tables()
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS robot_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            details TEXT,
            status TEXT DEFAULT 'ok',
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS robot_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    defaults = {
        "robot_mode": "assistido",
        "daily_posts": "3",
        "start_hour": "09",
        "auto_facebook": "0",
        "avoid_spam": "1",
    }
    for k, v in defaults.items():
        cur.execute("INSERT OR IGNORE INTO robot_settings (key, value) VALUES (?, ?)", (k, v))
    conn.commit()
    conn.close()


def robot_log(action: str, details: str = "", status: str = "ok"):
    try:
        ensure_robot_tables()
        conn = db_conn()
        conn.execute(
            "INSERT INTO robot_logs (action, details, status, created_at) VALUES (?, ?, ?, ?)",
            (action, details[:1200], status, datetime.utcnow().isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def robot_settings_dict() -> Dict[str, str]:
    ensure_robot_tables()
    conn = db_conn()
    rows = conn.execute("SELECT key, value FROM robot_settings").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


def update_robot_settings(form: Dict[str, Any]):
    ensure_robot_tables()
    allowed = ["robot_mode", "daily_posts", "start_hour", "auto_facebook", "avoid_spam"]
    conn = db_conn()
    for key in allowed:
        value = str(form.get(key, "0" if key in {"auto_facebook", "avoid_spam"} else "")).strip()
        if key == "daily_posts":
            try:
                value = str(max(1, min(8, int(value or 3))))
            except Exception:
                value = "3"
        if key == "start_hour":
            try:
                value = str(max(6, min(22, int(value or 9))))
            except Exception:
                value = "9"
        conn.execute("INSERT OR REPLACE INTO robot_settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit(); conn.close()


def is_school_niche(product: Dict[str, Any]) -> bool:
    txt = " ".join([str(product.get(k) or "") for k in ["niche", "discipline", "school_level", "target_audience", "title"]]).lower()
    return any(w in txt for w in ["professor", "educação", "escolar", "bncc", "disciplina", "ensino", "atividade escolar"])


def build_robot_campaign(product: Dict[str, Any], days: int = 30) -> List[Dict[str, str]]:
    """Campanha mais ampla: funciona para educação e também para MEI, beleza, culinária, IA, finanças, pets etc."""
    title = product.get("title") or "Produto Digital"
    niche = product.get("niche") or "produto digital"
    target = product.get("target_audience") or "pessoas interessadas"
    promise = product.get("promise") or "economizar tempo com materiais prontos e organizados"
    price = money(float(product.get("price") or 47))
    url = product_public_url(product)
    school = is_school_niche(product)

    if school:
        angles = [
            ("dor", "Você ainda perde horas montando material do zero?"),
            ("por_dentro", "Veja como o kit vem organizado por partes para usar com mais facilidade."),
            ("amostra", "Baixe uma amostra grátis antes de comprar."),
            ("autoridade", "Material com orientação, gabarito e estrutura editável."),
            ("oferta", f"Oferta inicial por {price}, com entrega digital."),
            ("prova", "Mostre para um colega professor que precisa economizar tempo."),
        ]
        bullets = "✅ atividades prontas\n✅ gabarito\n✅ orientação de uso\n✅ amostra grátis"
        hashtags = "#professores #educacao #atividadesprontas #reforcoescolar #materialdigital #aulasprontas"
    else:
        angles = [
            ("dor", f"Você trabalha com {niche.lower()} e ainda cria tudo do zero?"),
            ("transformacao", f"Organize sua rotina com modelos prontos, exemplos e checklist."),
            ("por_dentro", f"Veja por dentro o que vem no {title}."),
            ("amostra", "Baixe uma amostra grátis antes de comprar."),
            ("objeção", "Não é promessa mágica: é material pronto para adaptar e aplicar."),
            ("oferta", f"Preço de lançamento: {price}, com entrega digital."),
            ("uso", f"Feito para {target.lower()} que querem {promise.lower()}.")
        ]
        bullets = "✅ modelos prontos\n✅ checklists\n✅ mensagens e roteiros\n✅ amostra grátis"
        hashtags = "#produtodigital #empreendedorismo #mei #rendaextra #organização #templates #negociosonline"

    platforms = ["Instagram Reels", "TikTok", "YouTube Shorts", "Facebook Page", "WhatsApp"]
    rows = []
    for day in range(1, days + 1):
        angle, hook = angles[(day - 1) % len(angles)]
        platform = platforms[(day - 1) % len(platforms)]
        if platform == "WhatsApp":
            caption = (
                f"Oi! Preparei uma amostra grátis do {title}.\n\n"
                f"É um material digital para {target.lower()} que querem {promise.lower()}. "
                f"Ele vem organizado com modelos, orientação e bônus para facilitar o uso.\n\n"
                f"Quer ver por dentro? {url}"
            )
        elif platform == "Facebook Page":
            caption = (
                f"{hook}\n\n{title}\n\n"
                f"Material digital organizado para {target.lower()}.\n\n{bullets}\n\n"
                f"Veja a amostra grátis e confira se faz sentido para você: {url}"
            )
        else:
            caption = (
                f"{hook}\n\n{title}\n{bullets}\n\n"
                f"Veja por dentro antes de comprar: {url}"
            )
        rows.append({
            "day": str(day),
            "platform": platform,
            "post_type": "video_curto" if platform in ["Instagram Reels", "TikTok", "YouTube Shorts"] else "post_texto",
            "caption": caption,
            "hashtags": hashtags,
            "angle": angle,
        })
    return rows


def get_robot_products(limit: int = 50) -> List[Dict[str, Any]]:
    conn = db_conn()
    rows = conn.execute(
        """
        SELECT * FROM products
        WHERE COALESCE(status, '') != 'arquivado'
        ORDER BY
          CASE WHEN checkout_link IS NOT NULL AND checkout_link != '' THEN 0 ELSE 1 END,
          created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [row_to_dict(r) for r in rows]


def robot_create_queue_for_products(products: List[Dict[str, Any]], days: int, daily_posts: int, clear_old: bool = False) -> Dict[str, int]:
    ensure_robot_tables()
    from datetime import timedelta
    conn = db_conn()
    now = datetime.utcnow().isoformat()
    created = 0
    skipped = 0
    start_hour = int(robot_settings_dict().get("start_hour", "9") or 9)
    if clear_old:
        ids = [p["id"] for p in products]
        if ids:
            placeholders = ",".join(["?"] * len(ids))
            conn.execute(f"DELETE FROM social_queue WHERE status = 'rascunho' AND product_id IN ({placeholders})", ids)
    for p_index, product in enumerate(products):
        campaign = build_robot_campaign(product, days)
        # Limita volume para não parecer spam. O ideal é consistência e variação, não excesso.
        max_items = max(1, min(len(campaign), int(days) * max(1, min(8, int(daily_posts)))))
        for idx, item in enumerate(campaign[:max_items]):
            publish_day = date.today() + timedelta(days=int(item["day"]) - 1)
            hour = start_hour + ((idx + p_index) % max(1, min(8, int(daily_posts)))) * 2
            hour = min(22, hour)
            publish_at = f"{publish_day.isoformat()}T{hour:02d}:00:00"
            exists = conn.execute(
                "SELECT id FROM social_queue WHERE product_id=? AND platform=? AND publish_at=? AND caption=? LIMIT 1",
                (product["id"], item["platform"], publish_at, item["caption"]),
            ).fetchone()
            if exists:
                skipped += 1
                continue
            conn.execute(
                """
                INSERT INTO social_queue (product_id, platform, publish_at, post_type, caption, hashtags, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'rascunho', ?, ?)
                """,
                (product["id"], item["platform"], publish_at, item["post_type"], item["caption"], item["hashtags"], now, now),
            )
            created += 1
    conn.commit(); conn.close()
    robot_log("Criou fila automática", f"{created} posts criados, {skipped} duplicados ignorados, {len(products)} produtos.")
    return {"created": created, "skipped": skipped}


def robot_due_rows(limit: int = 20) -> List[Dict[str, Any]]:
    ensure_robot_tables()
    conn = db_conn()
    rows = conn.execute(
        """
        SELECT social_queue.*, products.title AS product_title, products.public_slug
        FROM social_queue LEFT JOIN products ON products.id = social_queue.product_id
        WHERE social_queue.status='rascunho' AND social_queue.publish_at <= ?
        ORDER BY social_queue.publish_at ASC LIMIT ?
        """,
        (datetime.utcnow().isoformat()[:19], limit),
    ).fetchall()
    conn.close()
    return [row_to_dict(r) for r in rows]


def robot_publish_due_facebook(limit: int = 5) -> Dict[str, int]:
    ensure_robot_tables()
    sent = 0
    failed = 0
    if not (FACEBOOK_PAGE_ID and FACEBOOK_PAGE_ACCESS_TOKEN):
        robot_log("Publicação automática", "Facebook Page não configurado.", "aviso")
        return {"sent": 0, "failed": 0}
    due = [r for r in robot_due_rows(limit * 3) if r.get("platform") == "Facebook Page"][:limit]
    for row in due:
        conn = db_conn()
        message = (row.get("caption") or "") + "\n\n" + (row.get("hashtags") or "")
        product = {"public_slug": row.get("public_slug"), "title": row.get("product_title")}
        link = product_public_url(product)
        try:
            import urllib.parse, urllib.request
            data = urllib.parse.urlencode({
                "message": message,
                "link": link,
                "access_token": FACEBOOK_PAGE_ACCESS_TOKEN,
            }).encode("utf-8")
            req = urllib.request.Request(f"https://graph.facebook.com/v20.0/{FACEBOOK_PAGE_ID}/feed", data=data, method="POST")
            with urllib.request.urlopen(req, timeout=25) as resp:
                body = resp.read().decode("utf-8")
            conn.execute("UPDATE social_queue SET status='postado_auto', external_url=?, error='', updated_at=? WHERE id=?", (body[:400], datetime.utcnow().isoformat(), row["id"]))
            conn.commit(); sent += 1
        except Exception as exc:
            conn.execute("UPDATE social_queue SET status='erro', error=?, updated_at=? WHERE id=?", (str(exc)[:500], datetime.utcnow().isoformat(), row["id"]))
            conn.commit(); failed += 1
        finally:
            conn.close()
    robot_log("Publicação Facebook Page", f"{sent} enviados, {failed} erros.", "ok" if failed == 0 else "aviso")
    return {"sent": sent, "failed": failed}


def robot_audit_products(products: List[Dict[str, Any]]) -> Dict[str, Any]:
    ready = 0; needs_checkout = 0; needs_copy = 0; needs_public = 0
    for p in products:
        if not (p.get("checkout_link") or "").strip():
            needs_checkout += 1
        if not (p.get("sales_page") or "").strip() or len(p.get("sales_page") or "") < 700:
            needs_copy += 1
        if not int(p.get("public_enabled") or 0):
            needs_public += 1
        if (p.get("checkout_link") or "").strip() and (p.get("sales_page") or "").strip() and int(p.get("public_enabled") or 0):
            ready += 1
    return {"total": len(products), "ready": ready, "needs_checkout": needs_checkout, "needs_copy": needs_copy, "needs_public": needs_public}


@app.route("/robo-comercial")
@login_required
def commercial_robot():
    ensure_robot_tables()
    products = get_robot_products()
    audit = robot_audit_products(products)
    settings = robot_settings_dict()
    conn = db_conn()
    logs = [row_to_dict(r) for r in conn.execute("SELECT * FROM robot_logs ORDER BY id DESC LIMIT 20").fetchall()]
    queue_stats = conn.execute(
        """
        SELECT status, COUNT(*) AS total FROM social_queue GROUP BY status
        """
    ).fetchall()
    conn.close()
    stats = {r["status"]: r["total"] for r in queue_stats}
    due = robot_due_rows(12)
    api_status = {
        "Facebook Page": bool(FACEBOOK_PAGE_ID and FACEBOOK_PAGE_ACCESS_TOKEN),
        "Instagram Reels": bool(INSTAGRAM_ACCOUNT_ID and FACEBOOK_PAGE_ACCESS_TOKEN and public_base_url()),
        "TikTok": bool(TIKTOK_ACCESS_TOKEN),
        "YouTube Shorts": bool(YOUTUBE_READY),
    }
    return render_template("commercial_robot.html", title="Robô comercial", products=products, audit=audit, settings=settings, stats=stats, due=due, logs=logs, api_status=api_status, public_base_url=public_base_url())


@app.route("/robo-comercial/configurar", methods=["POST"])
@login_required
def commercial_robot_configure():
    update_robot_settings(request.form)
    robot_log("Configuração atualizada", "Preferências do robô comercial foram alteradas.")
    flash("Configurações do robô comercial salvas.", "success")
    return redirect(url_for("commercial_robot"))


@app.route("/robo-comercial/rodar", methods=["POST"])
@login_required
def commercial_robot_run():
    products = get_robot_products()
    selected = request.form.getlist("product_ids")
    if selected:
        selected_set = {int(x) for x in selected if str(x).isdigit()}
        products = [p for p in products if int(p["id"]) in selected_set]
    days = max(7, min(90, int(request.form.get("days") or 30)))
    daily_posts = max(1, min(8, int(request.form.get("daily_posts") or robot_settings_dict().get("daily_posts", "3"))))
    clear_old = request.form.get("clear_old") == "1"
    result = robot_create_queue_for_products(products, days=days, daily_posts=daily_posts, clear_old=clear_old)
    flash(f"Robô preparou {result['created']} posts/vídeos/mensagens. Duplicados ignorados: {result['skipped']}.", "success")
    return redirect(url_for("commercial_robot"))


@app.route("/robo-comercial/publicar-facebook", methods=["POST"])
@login_required
def commercial_robot_publish_facebook():
    result = robot_publish_due_facebook(limit=5)
    if result["sent"] or result["failed"]:
        flash(f"Publicação Facebook: {result['sent']} enviados, {result['failed']} erros.", "success" if result["failed"] == 0 else "danger")
    else:
        flash("Nenhum post enviado. Verifique se há posts vencidos e se a API do Facebook Page está configurada.", "warning")
    return redirect(url_for("commercial_robot"))


@app.route("/api/robo-comercial/cron")
def commercial_robot_cron():
    secret = request.args.get("secret", "")
    expected = os.getenv("ROBOT_SECRET", WEBHOOK_SECRET)
    if not expected or secret != expected:
        return jsonify({"ok": False, "error": "secret inválido"}), 403
    settings = robot_settings_dict()
    products = get_robot_products()
    created = 0
    if request.args.get("create_queue", "0") == "1":
        result = robot_create_queue_for_products(products, days=int(request.args.get("days", 30)), daily_posts=int(settings.get("daily_posts", "3")), clear_old=False)
        created = result["created"]
    published = robot_publish_due_facebook(limit=int(request.args.get("limit", 3))) if settings.get("auto_facebook") == "1" else {"sent": 0, "failed": 0}
    return jsonify({"ok": True, "products": len(products), "created": created, "facebook": published})


ensure_social_tables()
ensure_robot_tables()


# ============================================================================
# Motor comercial premium, textos chamativos e vídeo temático robusto
# ============================================================================
# Esta camada foi adicionada no final para substituir os geradores simples sem
# quebrar as rotas antigas. As rotas continuam as mesmas, mas passam a usar estas
# funções por causa da resolução dinâmica de nomes do Python.

V15_VISUAL_THEMES = {
    "ia": {"emoji":"🤖", "accent":(34, 245, 208), "accent2":(125, 92, 255), "bg":(7, 12, 27), "icons":["🤖","⚡","📲","🚀"], "label":"IA & Automação"},
    "financas": {"emoji":"💰", "accent":(52, 211, 153), "accent2":(245, 158, 11), "bg":(6, 20, 16), "icons":["💰","📊","✅","🎯"], "label":"Finanças & Organização"},
    "mei": {"emoji":"🛍️", "accent":(251, 146, 60), "accent2":(236, 72, 153), "bg":(24, 16, 10), "icons":["🛍️","📦","📲","⭐"], "label":"MEI & Negócios"},
    "beleza": {"emoji":"✨", "accent":(244, 114, 182), "accent2":(168, 85, 247), "bg":(26, 12, 25), "icons":["✨","💅","📅","💬"], "label":"Beleza & Atendimento"},
    "casa": {"emoji":"🏡", "accent":(45, 212, 191), "accent2":(59, 130, 246), "bg":(7, 20, 28), "icons":["🏡","🧺","📋","🌿"], "label":"Casa & Rotina"},
    "culinaria": {"emoji":"🍲", "accent":(251, 191, 36), "accent2":(239, 68, 68), "bg":(30, 15, 8), "icons":["🍲","🍰","🧾","📦"], "label":"Culinária & Marmitas"},
    "carreira": {"emoji":"💼", "accent":(96, 165, 250), "accent2":(34, 211, 238), "bg":(10, 18, 35), "icons":["💼","📄","🎯","🚀"], "label":"Carreira & Currículo"},
    "design": {"emoji":"🎨", "accent":(168, 85, 247), "accent2":(236, 72, 153), "bg":(20, 12, 38), "icons":["🎨","📱","🖼️","🔥"], "label":"Design & Redes"},
    "pets": {"emoji":"🐾", "accent":(56, 189, 248), "accent2":(52, 211, 153), "bg":(7, 19, 28), "icons":["🐾","🦴","📅","❤️"], "label":"Pets & Cuidados"},
    "educacao": {"emoji":"📚", "accent":(34, 245, 208), "accent2":(99, 102, 241), "bg":(8, 13, 30), "icons":["📚","📝","✅","🎓"], "label":"Educação & Materiais"},
    "default": {"emoji":"🚀", "accent":(34, 245, 208), "accent2":(168, 85, 247), "bg":(8, 10, 25), "icons":["🚀","✅","📲","🎁"], "label":"Produto Digital"},
}


def v15_theme_for(niche: str = "", title: str = "") -> Dict[str, Any]:
    s = f"{niche} {title}".lower()
    if any(k in s for k in ["ia", "inteligência", "inteligencia", "chatgpt", "prompts", "automação", "automacao"]):
        return V15_VISUAL_THEMES["ia"]
    if any(k in s for k in ["finança", "financa", "financeiro", "gastos", "dinheiro", "dívida", "divida"]):
        return V15_VISUAL_THEMES["financas"]
    if any(k in s for k in ["mei", "negócio", "negocio", "loja", "vendedor", "empreendedor", "cliente"]):
        return V15_VISUAL_THEMES["mei"]
    if any(k in s for k in ["beleza", "estética", "estetica", "salão", "salao", "unha", "maquiagem"]):
        return V15_VISUAL_THEMES["beleza"]
    if any(k in s for k in ["casa", "rotina", "organização", "organizacao", "limpeza", "planner"]):
        return V15_VISUAL_THEMES["casa"]
    if any(k in s for k in ["culinária", "culinaria", "marmita", "confeitaria", "bolo", "cardápio", "cardapio"]):
        return V15_VISUAL_THEMES["culinaria"]
    if any(k in s for k in ["carreira", "currículo", "curriculo", "emprego", "entrevista", "linkedin"]):
        return V15_VISUAL_THEMES["carreira"]
    if any(k in s for k in ["design", "redes", "instagram", "canva", "template", "post"]):
        return V15_VISUAL_THEMES["design"]
    if any(k in s for k in ["pet", "pets", "cachorro", "gato", "veterin"]):
        return V15_VISUAL_THEMES["pets"]
    if is_education_product(niche, "", "", "") or any(k in s for k in ["professor", "bncc", "atividade", "escolar", "aula", "aluno"]):
        return V15_VISUAL_THEMES["educacao"]
    return V15_VISUAL_THEMES["default"]



def _css_rgb(value, fallback="34, 245, 208"):
    """Converte tupla/lista RGB para string segura de CSS."""
    try:
        if isinstance(value, (tuple, list)) and len(value) >= 3:
            return f"{int(value[0])}, {int(value[1])}, {int(value[2])}"
        text = str(value or "").strip()
        return text if text else fallback
    except Exception:
        return fallback


def product_theme(product=None) -> Dict[str, Any]:
    """Tema visual usado pela página pública.

    Corrige erro 500 quando o template chama product_theme(product).
    Retorna valores prontos para CSS e textos de venda por nicho.
    """
    try:
        if isinstance(product, sqlite3.Row):
            p = row_to_dict(product)
        elif isinstance(product, dict):
            p = product
        else:
            p = {}
        niche = p.get("niche", "") or ""
        title = p.get("title", "") or ""
        base = v15_theme_for(niche, title)
        model = v15_model_for(niche, title) if "v15_model_for" in globals() else {}
        icons = base.get("icons") or [base.get("emoji", "🚀")]
        return {
            "emoji": base.get("emoji", "🚀"),
            "icon": icons[0] if icons else base.get("emoji", "🚀"),
            "label": base.get("label", "Produto Digital"),
            "accent": _css_rgb(base.get("accent"), "34, 245, 208"),
            "accent2": _css_rgb(base.get("accent2"), "168, 85, 247"),
            "bg": _css_rgb(base.get("bg"), "8, 10, 25"),
            "hook": model.get("hook", "Material digital organizado, bonito e pronto para adaptar."),
        }
    except Exception:
        return {
            "emoji": "🚀", "icon": "🚀", "label": "Produto Digital",
            "accent": "34, 245, 208", "accent2": "168, 85, 247", "bg": "8, 10, 25",
            "hook": "Material digital organizado, bonito e pronto para adaptar.",
        }


def public_bullets(product=None) -> List[str]:
    """Lista de benefícios usada na página pública.

    Mantém a página de venda viva e evita erro 500 quando o template chama public_bullets(product).
    """
    try:
        if isinstance(product, sqlite3.Row):
            p = row_to_dict(product)
        elif isinstance(product, dict):
            p = product
        else:
            p = {}
        niche = p.get("niche", "") or ""
        title = p.get("title", "") or ""
        model = v15_model_for(niche, title)
        deliverables = list(model.get("deliverables") or [])
        theme = product_theme(p)
        bullets = []
        for item in deliverables[:6]:
            item = str(item).strip()
            if item:
                bullets.append(f"✅ {item[0].upper() + item[1:]}")
        if not bullets:
            bullets = [
                "✅ Guia principal organizado em módulos práticos",
                "✅ Modelos prontos para copiar, adaptar e usar",
                "✅ Checklists para aplicar sem começar do zero",
                "✅ Mensagens e chamadas para divulgação",
                "✅ Amostra grátis para conhecer antes de comprar",
                "✅ Manual de uso com próximos passos",
            ]
        # Ajuste final para parecer produto pago sem exagero.
        if theme.get("emoji") and len(bullets) < 7:
            bullets.append(f"{theme['emoji']} Identidade visual e estrutura temática do nicho")
        return bullets[:7]
    except Exception:
        return [
            "✅ Guia principal organizado",
            "✅ Modelos prontos para adaptar",
            "✅ Checklists práticos",
            "✅ Amostra grátis",
            "✅ Manual de uso",
        ]


# Funções disponíveis dentro dos templates Jinja.
app.jinja_env.globals.update(product_theme=product_theme, public_bullets=public_bullets)

def v15_model_for(niche: str, title: str = "") -> Dict[str, Any]:
    theme = v15_theme_for(niche, title)
    key = theme["label"]
    base = {
        "hook": "Pare de começar do zero e entregue uma versão mais bonita, organizada e pronta para usar.",
        "main_promise": "economizar tempo, organizar a rotina e apresentar algo com aparência profissional",
        "deliverables": ["guia principal", "modelos editáveis", "checklists práticos", "mensagens prontas", "calendário de ação", "manual de uso"],
        "modules": ["Diagnóstico rápido", "Organização do material", "Modelos prontos", "Aplicação prática", "Divulgação", "Checklist final"],
        "warnings": "Material digital educativo e organizacional. Revise e adapte antes de usar, publicar ou vender.",
        "emotional": "A sensação que o comprador precisa ter é simples: 'isso já está bem encaminhado, só preciso adaptar para minha realidade'.",
    }
    if theme is V15_VISUAL_THEMES["ia"]:
        base.update({
            "hook":"🤖 Transforme tarefas repetitivas em textos, posts e mensagens prontas com ajuda da IA.",
            "main_promise":"usar IA de forma simples para criar posts, respostas, ofertas, mensagens e ideias de conteúdo sem travar na tela em branco",
            "deliverables":["prompts prontos por situação", "mensagens de WhatsApp", "posts para redes sociais", "roteiros de vídeo curto", "modelos de oferta", "checklist de uso seguro da IA"],
            "modules":["Comece pela dor do cliente", "Prompts para atendimento", "Prompts para posts", "Prompts para vendas", "Roteiros de vídeo", "Calendário de 30 dias", "Checklist antes de publicar"],
            "warnings":"A IA ajuda a criar rascunhos e ideias. Revise tudo antes de publicar e não prometa resultados garantidos.",
        })
    elif theme is V15_VISUAL_THEMES["financas"]:
        base.update({
            "hook":"💰 Organize gastos, contas e metas sem planilha complicada.",
            "main_promise":"clarear a vida financeira com páginas simples, checklists e metas visuais",
            "deliverables":["controle mensal de gastos", "mapa de dívidas", "metas financeiras", "lista de contas fixas", "planejamento de compras", "checklist semanal"],
            "modules":["Raio-X do dinheiro", "Contas fixas", "Gastos variáveis", "Dívidas e acordos", "Metas do mês", "Compras planejadas", "Revisão semanal"],
            "warnings":"Material de organização financeira, sem promessa de enriquecimento e sem substituir orientação profissional.",
        })
    elif theme is V15_VISUAL_THEMES["mei"]:
        base.update({
            "hook":"🛍️ Seu negócio pequeno também pode parecer organizado e profissional.",
            "main_promise":"organizar clientes, preços, mensagens, divulgação e rotina comercial em modelos simples",
            "deliverables":["ficha de cliente", "controle de pedidos", "tabela de preços", "mensagens de atendimento", "posts de divulgação", "checklist de entrega"],
            "modules":["Organização do negócio", "Clientes e pedidos", "Preço e oferta", "Atendimento no WhatsApp", "Divulgação local", "Pós-venda", "Rotina semanal"],
            "warnings":"Material de organização comercial. Vendas dependem de oferta, atendimento, público e constância.",
        })
    elif theme is V15_VISUAL_THEMES["beleza"]:
        base.update({
            "hook":"✨ Atendimento bonito, agenda organizada e mensagens prontas para encantar clientes.",
            "main_promise":"deixar a rotina de beleza/estética mais organizada, visual e fácil de divulgar",
            "deliverables":["agenda de clientes", "ficha de atendimento", "mensagens de confirmação", "posts para Instagram", "checklist de procedimento", "pós-atendimento"],
            "modules":["Agenda premium", "Ficha da cliente", "Atendimento antes", "Atendimento depois", "Posts de prova social", "Pacotes e ofertas", "Fidelização"],
            "warnings":"Material de organização e comunicação. Procedimentos técnicos exigem formação e responsabilidade profissional.",
        })
    elif theme is V15_VISUAL_THEMES["culinaria"]:
        base.update({
            "hook":"🍲 Cardápios, pedidos e preços organizados para vender com mais clareza.",
            "main_promise":"organizar cardápio, pedidos, lista de compras, divulgação e rotina de produção",
            "deliverables":["cardápio editável", "controle de pedidos", "lista de compras", "precificação simples", "mensagens para clientes", "posts de oferta"],
            "modules":["Cardápio irresistível", "Pedidos da semana", "Lista de compras", "Preço sem confusão", "Divulgação", "Entrega", "Pós-venda"],
            "warnings":"Material de organização. Cuidados com higiene, legislação local e segurança alimentar são responsabilidade do vendedor.",
        })
    elif theme is V15_VISUAL_THEMES["carreira"]:
        base.update({
            "hook":"💼 Currículo, entrevista e apresentação profissional sem parecer improviso.",
            "main_promise":"organizar currículo, perfil profissional, entrevista e mensagens de candidatura",
            "deliverables":["modelo de currículo", "roteiro de entrevista", "mensagens para recrutadores", "checklist de LinkedIn", "mapa de vagas", "plano de 7 dias"],
            "modules":["Posicionamento", "Currículo claro", "LinkedIn", "Candidaturas", "Entrevista", "Mensagens", "Plano semanal"],
            "warnings":"Material de preparação profissional. Não garante contratação; ajuda na organização e apresentação.",
        })
    elif theme is V15_VISUAL_THEMES["pets"]:
        base.update({
            "hook":"🐾 Rotina do pet, vacinas, gastos e cuidados em um só lugar.",
            "main_promise":"organizar a vida do pet com fichas, agenda, lembretes e checklists visuais",
            "deliverables":["ficha do pet", "agenda de vacinas", "controle de gastos", "rotina alimentar", "checklist de viagem", "contatos importantes"],
            "modules":["Ficha completa", "Rotina diária", "Saúde e vacinas", "Gastos", "Passeios", "Viagem", "Observações"],
            "warnings":"Material de organização para tutores. Não substitui atendimento veterinário.",
        })
    elif theme is V15_VISUAL_THEMES["educacao"]:
        base.update({
            "hook":"📚 Material escolar pronto, organizado e com campos pedagógicos para revisar antes de aplicar.",
            "main_promise":"economizar tempo com atividades, gabaritos, orientação do professor, adaptação e campos BNCC editáveis",
            "deliverables":["folha do aluno", "orientação do professor", "gabarito comentado", "campo BNCC editável", "avaliação rápida", "adaptação para dificuldades"],
            "modules":["Apresentação do kit", "Como escolher a atividade", "Atividades por área", "Gabaritos", "Orientações", "Adaptações", "Revisão BNCC"],
            "warnings":"Material pedagógico editável. Revise habilidades BNCC, currículo local e realidade da turma antes de aplicar ou vender.",
        })
    return {**base, "theme": theme, "label": key}


def v15_emoji_line(items: List[str], theme: Dict[str, Any]) -> str:
    icons = theme.get("icons") or ["✅"]
    out = []
    for i, item in enumerate(items):
        out.append(f"{icons[i % len(icons)]} **{item}**")
    return "\n".join(f"- {x}" for x in out)


def _premium_bonus_stack(title: str, niche: str) -> List[str]:  # override v11
    theme = v15_theme_for(niche, title)
    e = theme["emoji"]
    return [
        f"{e} Bônus 1 — Amostra grátis pronta para captar interessados",
        "📲 Bônus 2 — Mensagens de WhatsApp para divulgar sem parecer robô",
        "🎬 Bônus 3 — Roteiros curtos para Reels, TikTok e Shorts",
        "📅 Bônus 4 — Calendário de 30 dias com ideias de postagem",
        "🧾 Bônus 5 — Checklist de entrega para o comprador",
        "⭐ Bônus 6 — Página de oferta pronta para copiar e adaptar",
    ]


def v15_signature_block(niche: str, title: str) -> str:
    theme = v15_theme_for(niche, title)
    return f"""
---

## {theme['emoji']} Identidade visual sugerida
- **Tema:** {theme['label']}
- **Estilo:** fundo temático, cards com ícones, títulos fortes e blocos fáceis de ler.
- **Tom:** brasileiro, direto, visual, profissional e sem promessa falsa.
- **Uso de emojis:** 1 a 2 por bloco, para chamar atenção sem parecer spam.
""".strip()


def template_content(title: str, niche: str, product_type: str, target: str, pages: int, promise: str, discipline: str = "Todas as disciplinas", school_level: str = "Ensino fundamental") -> str:  # override principal
    model = v15_model_for(niche, title)
    theme = model["theme"]
    e = theme["emoji"]
    deliverables = model["deliverables"]
    modules = model["modules"]
    bonuses = _premium_bonus_stack(title, niche)
    today = date.today().strftime("%d/%m/%Y")
    body = [
        f"# {e} {title}",
        "",
        f"**Produto digital premium editável** • {product_type} • Versão {today}",
        f"**Nicho:** {niche}",
        f"**Público:** {target}",
        "",
        f"> {model['hook']}",
        "",
        "---",
        "",
        f"# ✨ Apresentação que chama atenção",
        f"Este material foi criado para {target.lower()} que querem **{model['main_promise']}** sem perder horas montando tudo do zero.",
        "",
        "A ideia é entregar uma experiência de produto pago: nome claro, módulos organizados, exemplos prontos, checklists, bônus, amostra grátis e um caminho simples para o comprador aplicar.",
        "",
        f"**Promessa honesta:** {promise}. O resultado depende da adaptação, divulgação, execução e realidade de cada comprador.",
        "",
        "---",
        "",
        f"# 🎁 O que vem dentro do pacote",
        v15_emoji_line(deliverables, theme),
        "",
        "---",
        "",
        "# 🧭 Como usar em 15 minutos",
        "1. Abra o arquivo **Comece por aqui**.",
        "2. Escolha o módulo que resolve sua necessidade mais urgente.",
        "3. Copie o modelo pronto.",
        "4. Substitua os campos pelos seus dados reais.",
        "5. Revise a promessa, o visual e as informações.",
        "6. Publique, envie, imprima ou use conforme o objetivo.",
        "",
        "---",
        "",
        "# 📌 Sumário organizado",
    ]
    for i, m in enumerate(modules, 1):
        body.append(f"{i}. {m}")
    body += ["", "---", ""]
    for idx, module in enumerate(modules, 1):
        icon = theme["icons"][(idx - 1) % len(theme["icons"])]
        example = deliverables[(idx - 1) % len(deliverables)]
        body += [
            f"# {icon} Módulo {idx} — {module}",
            "",
            "## Objetivo do módulo",
            f"Ajudar o comprador a aplicar **{module.lower()}** com clareza, rapidez e aparência profissional.",
            "",
            "## Por que isso aumenta o valor percebido",
            "O comprador não quer apenas informação solta. Ele quer um caminho pronto, bonito e fácil de adaptar. Este módulo entrega exatamente isso: um ponto de partida confiável.",
            "",
            "## Modelo pronto para copiar",
            f"**Entrega principal:** {example}",
            "",
            "**Meu objetivo:** ________________________________________________",
            "**O que preciso organizar agora:** _______________________________",
            "**Texto/modelo adaptado:** _______________________________________",
            "**Próxima ação prática:** ________________________________________",
            "**Data para revisar:** ____/____/______",
            "",
            "## Exemplo preenchido",
            f"Uma pessoa do público **{target.lower()}** usa este bloco para sair do improviso e transformar uma ideia confusa em uma ação clara, apresentável e pronta para executar.",
            "",
            "## Checklist premium",
            "- [ ] O conteúdo está claro para uma pessoa leiga.",
            "- [ ] A promessa não está exagerada.",
            "- [ ] O modelo tem espaço para personalização.",
            "- [ ] O visual pode ser apresentado em PDF, WhatsApp ou redes sociais.",
            "- [ ] O comprador entende o próximo passo.",
            "",
            "## Versão rápida para WhatsApp/redes",
            f"{icon} Estou usando um material pronto para {promise.lower()}. Ele traz **{example.lower()}** e pode ser adaptado em poucos minutos.",
            "",
            "---",
            "",
        ]
    body += [
        "# 🎁 Bônus de alto valor percebido",
        "",
        *[f"- {b}" for b in bonuses],
        "",
        "# 🔥 Amostra grátis para captar interessados",
        f"A amostra deve mostrar uma parte real do **{title}**, com visual bonito e promessa simples. O objetivo é fazer a pessoa pensar: *se a amostra já ajuda, o pacote completo deve valer a pena*.",
        "",
        "## Mini-amostra sugerida",
        "- 1 página de apresentação",
        "- 1 modelo pronto preenchível",
        "- 1 checklist rápido",
        "- 1 chamada para conhecer o pacote completo",
        "",
        "# 🧾 Manual do comprador",
        "1. Baixe o arquivo após a compra.",
        "2. Leia o aviso responsável.",
        "3. Use primeiro o modelo mais simples.",
        "4. Adapte ao seu caso antes de divulgar ou imprimir.",
        "5. Guarde uma versão preenchida e outra limpa.",
        "",
        "# ⚠️ Aviso responsável",
        model["warnings"],
        "",
        v15_signature_block(niche, title),
        "",
        "# ✅ Checklist final antes de vender",
        "- [ ] O título diz claramente o que a pessoa recebe.",
        "- [ ] O pacote tem começo, meio e fim.",
        "- [ ] A amostra grátis abre rápido no celular.",
        "- [ ] A página de venda tem botão de checkout visível.",
        "- [ ] O vídeo mostra dor, solução, produto e chamada para ação.",
        "- [ ] O preço está coerente com a entrega.",
        "",
        "# Conclusão",
        "Produto digital vendável precisa parecer útil antes mesmo da compra. Quanto mais claro, visual e organizado ele for, maior a chance de gerar confiança no comprador.",
    ]
    return "\n".join(body)


def template_sales_page(title: str, niche: str, product_type: str, target: str, price: float, promise: str, discipline: str = "Todas as disciplinas", school_level: str = "Ensino fundamental") -> str:  # override principal
    model = v15_model_for(niche, title)
    theme = model["theme"]
    e = theme["emoji"]
    deliverables = v15_emoji_line(model["deliverables"], theme)
    bonuses = "\n".join(f"- {b}" for b in _premium_bonus_stack(title, niche))
    checkout_text = "Clique no botão da página pública e acesse o checkout seguro." 
    return f"""
# {e} {title}

## Headline principal
**{title}: o pacote digital pronto para {target.lower()} que querem {model['main_promise']} sem começar do zero.**

## Subheadline
{e} Um {product_type.lower()} com modelos, checklists, mensagens, calendário, amostra grátis e orientação prática para aplicar com mais segurança e aparência profissional.

## Gancho de atenção
Você já perdeu tempo tentando montar algo bonito, organizado e vendável, mas acabou travando na tela em branco?  
Este pacote foi criado para encurtar esse caminho.

## Dor do público
O problema não é falta de vontade. O problema é ter que pensar em tudo ao mesmo tempo: estrutura, texto, design, divulgação, organização, mensagem, bônus e oferta. Quando tudo fica solto, o comprador não sente confiança.

## Solução
Com o **{title}**, você recebe uma estrutura pronta para adaptar, usar, divulgar e entregar com mais profissionalismo.

## O que vem no pacote completo
{deliverables}

## Bônus incluídos
{bonuses}

## Por que esse produto parece mais profissional
- Tem uma promessa clara e específica.
- Tem módulos organizados.
- Tem modelos prontos para copiar e adaptar.
- Tem amostra grátis para gerar confiança.
- Tem linguagem visual com ícones e chamadas diretas.
- Tem aviso responsável, sem promessa milagrosa.

## Para quem é
- Para quem quer economizar tempo.
- Para quem precisa de algo pronto para adaptar.
- Para quem quer divulgar com mais confiança.
- Para quem gosta de material organizado e bonito.
- Para quem prefere começar com um modelo pronto em vez de criar tudo sozinho.

## Para quem não é
- Não é para quem procura resultado sem ação.
- Não é para quem quer promessa de dinheiro fácil.
- Não substitui profissional especializado quando o tema exigir orientação técnica.

## Como funciona
1. Você acessa o checkout seguro.
2. Recebe o material digital.
3. Abre o guia **Comece por aqui**.
4. Escolhe o modelo que precisa primeiro.
5. Personaliza com seus dados.
6. Usa, publica, imprime ou envia conforme seu objetivo.

## Preço de lançamento
**{money(price)}**

## Chamada para ação
🚀 **Quero acessar o pacote completo agora**  
🎁 **Prefiro baixar a amostra grátis primeiro**

## FAQ rápido
**É físico ou digital?**  
É digital. Você recebe para baixar e usar.

**Posso editar?**  
Sim. O objetivo é adaptar para sua realidade.

**Funciona no celular?**  
Sim, o material foi pensado para leitura simples e uso prático.

**Tem garantia?**  
Use a política configurada na sua plataforma de checkout.

## Aviso responsável
{model['warnings']}

## Orientação para o botão
{checkout_text}
""".strip()


def template_social_posts(title: str, target: str, niche: str, promise: str, discipline: str = "Todas as disciplinas", school_level: str = "Ensino fundamental") -> str:  # override principal
    model = v15_model_for(niche, title)
    theme = model["theme"]
    e = theme["emoji"]
    return f"""
# {e} Kit de divulgação chamativo — {title}

## 10 legendas prontas para Instagram/Facebook
1. {e} Você não precisa começar do zero. O **{title}** já vem organizado para {promise.lower()}.
2. ⚡ Pare de improvisar. Use um material pronto, bonito e adaptável para sua rotina.
3. 🎁 Quer ver antes de comprar? Baixe a amostra grátis e veja o produto por dentro.
4. 📲 Se você usa WhatsApp para divulgar, esse pacote já traz mensagens e chamadas prontas.
5. ✅ O material foi pensado para economizar tempo e deixar tudo com aparência mais profissional.
6. 🔥 O problema não é falta de ideia. É falta de um modelo pronto para adaptar.
7. ⭐ Um pacote digital com módulos, checklists, bônus e página de oferta pronta.
8. 🚀 Transforme uma ideia solta em um material organizado para usar, vender ou divulgar.
9. {theme['icons'][0]} Feito para {target.lower()} que querem praticidade sem abrir mão de organização.
10. 🧾 Produto digital editável, com aviso responsável e estrutura clara.

## 8 mensagens para WhatsApp
1. Oi! Preparei uma amostra grátis do **{title}**. Quer que eu te envie o link?
2. Esse material ajuda a {promise.lower()} sem começar tudo do zero. Posso te mostrar por dentro?
3. Tenho um pacote digital organizado com modelos, checklists e bônus. Quer ver a amostra?
4. Se fizer sentido para você, o pacote completo já está disponível com entrega digital.
5. O material é simples de usar: baixa, abre, adapta e aplica.
6. Não é promessa mágica; é organização pronta para facilitar sua rotina.
7. Posso te mandar o link da página com a amostra e os detalhes?
8. Estou com preço de lançamento por tempo limitado. Quer conferir?

## 6 roteiros curtos para vídeo
1. **Dor:** “Você perde tempo criando tudo do zero?” → Mostre tela vazia → Mostre o pacote.
2. **Produto por dentro:** Mostre módulos, checklists e bônus → “É só adaptar”.
3. **Amostra grátis:** “Antes de comprar, veja por dentro” → Mostre a amostra.
4. **Antes/depois:** “Antes: improviso. Depois: material pronto e organizado”.
5. **Oferta:** “Preço de lançamento + entrega digital imediata”.
6. **Confiança:** “Sem promessa milagrosa. É material prático para adaptar”.

## Hashtags sugeridas
#produtodigital #rendaextra #empreendedorismo #organização #templates #ia #vendasonline #materialdigital
""".strip()


def build_video_script(product: Dict[str, Any]) -> List[Dict[str, str]]:  # override v15
    title = product.get("title") or "Produto Digital Premium"
    niche = product.get("niche") or "Produto digital"
    price = money(float(product.get("price") or 47))
    model = v15_model_for(niche, title)
    theme = model["theme"]
    e = theme["emoji"]
    return [
        {"scene":"1", "tag":"ATENÇÃO", "text":f"{e} Você ainda cria tudo do zero?", "small":"Pare de travar na tela em branco."},
        {"scene":"2", "tag":"DOR", "text":"Texto solto não vende. Organização gera confiança.", "small":"O comprador precisa entender rápido o valor."},
        {"scene":"3", "tag":"SOLUÇÃO", "text":title[:95], "small":model["main_promise"][:115]},
        {"scene":"4", "tag":"POR DENTRO", "text":"Modelos + checklists + bônus + amostra grátis", "small":"Tudo em um pacote digital para adaptar."},
        {"scene":"5", "tag":"VISUAL", "text":"Produto com cara profissional e tema do nicho", "small":f"Tema: {theme['label']}"},
        {"scene":"6", "tag":"OFERTA", "text":f"Preço inicial: {price}", "small":"Entrega digital • revise e adapte antes de usar."},
        {"scene":"7", "tag":"AÇÃO", "text":"Baixe a amostra grátis e veja por dentro", "small":"Link na página, bio ou WhatsApp."},
    ]


def build_social_campaign(product: Dict[str, Any], days: int = 30) -> List[Dict[str, str]]:  # override v15
    title = product.get("title") or "Produto Digital Premium"
    niche = product.get("niche") or "Produto digital"
    url = product_public_url(product)
    price = money(float(product.get("price") or 47))
    model = v15_model_for(niche, title)
    theme = model["theme"]
    e = theme["emoji"]
    angles = [
        ("dor", f"{e} Você ainda perde tempo criando tudo do zero?"),
        ("amostra", "🎁 Baixe uma amostra grátis antes de comprar."),
        ("por_dentro", "👀 Veja por dentro o que vem no pacote."),
        ("valor", "⭐ Produto organizado chama mais confiança."),
        ("oferta", f"🔥 Preço inicial: {price}. Entrega digital."),
        ("antes_depois", "⚡ Antes: improviso. Depois: modelo pronto para adaptar."),
        ("beneficios", f"✅ Feito para {model['main_promise']}.")
    ]
    platforms = ["Instagram Reels", "TikTok", "Facebook Page", "WhatsApp", "YouTube Shorts"]
    rows = []
    hashtags = "#produtodigital #rendaextra #templates #vendasonline #empreendedorismo #organizacao"
    if theme["label"].startswith("Educação"):
        hashtags = "#professores #educacao #atividadesescolares #bncc #materialpedagogico"
    elif theme["label"].startswith("IA"):
        hashtags += " #inteligenciaartificial #chatgpt #prompts"
    elif theme["label"].startswith("Finanças"):
        hashtags += " #financas #organizacaofinanceira"
    elif theme["label"].startswith("Beleza"):
        hashtags += " #beleza #estetica #salaodebeleza"
    for day in range(1, days + 1):
        angle, hook = angles[(day - 1) % len(angles)]
        platform = platforms[(day - 1) % len(platforms)]
        if platform == "WhatsApp":
            caption = f"Oi! {hook}\n\nPreparei uma amostra grátis do {title}. É um material digital com modelos, checklists e bônus para adaptar. Quer ver por dentro?\n\n{url}"
        elif platform == "Facebook Page":
            caption = f"{hook}\n\n{title}\n\nMaterial digital organizado, com modelos prontos, bônus e amostra grátis. Ideal para quem quer praticidade sem começar do zero.\n\nAcesse: {url}"
        else:
            caption = f"{hook}\n\n{title}\n\n✅ modelos prontos\n✅ visual temático\n✅ bônus e checklist\n✅ amostra grátis\n\nVeja por dentro: {url}"
        rows.append({"day": str(day), "platform": platform, "post_type": "video_curto" if platform in ["Instagram Reels", "TikTok", "YouTube Shorts"] else "post_texto", "caption": caption, "hashtags": hashtags, "angle": angle})
    return rows


def _v15_font(size, bold=False):
    try:
        from PIL import ImageFont
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ]
        for c in candidates:
            try:
                return ImageFont.truetype(c, size)
            except Exception:
                pass
        return ImageFont.load_default()
    except Exception:
        return None


def generate_sales_video(product: Dict[str, Any]) -> Path:  # override v15 robusto
    """Gera MP4 vertical leve, temático e chamativo. Reduz tamanho para não travar hospedagem."""
    try:
        from PIL import Image, ImageDraw
        import imageio.v2 as imageio
        import numpy as np
    except Exception as exc:
        raise RuntimeError("Dependências de vídeo ausentes. Rode: pip install pillow imageio imageio-ffmpeg numpy") from exc

    title = product.get("title") or "produto"
    niche = product.get("niche") or "Produto digital"
    theme = v15_theme_for(niche, title)
    out = EXPORT_DIR / f"{slugify(title)}-video-v15-tematico.mp4"
    if out.exists() and out.stat().st_size > 15000:
        return out

    W, H = 720, 1280
    fps = 18
    seconds_per_scene = 1.75
    frames_per_scene = int(fps * seconds_per_scene)
    bg = theme["bg"]
    accent = theme["accent"]
    accent2 = theme["accent2"]
    scenes = build_video_script(product)
    big = _v15_font(50, True); mid = _v15_font(28, False); small = _v15_font(22, False); badge = _v15_font(20, True); mini = _v15_font(18, False)

    def draw_text_lines(draw, text, xy, font, fill, max_width, line_gap=8, max_lines=5):
        x, y = xy
        words = str(text).split()
        lines, cur = [], ""
        for w in words:
            test = (cur + " " + w).strip()
            try:
                bbox = draw.textbbox((0, 0), test, font=font)
                width = bbox[2] - bbox[0]
            except Exception:
                width = len(test) * 12
            if width <= max_width or not cur:
                cur = test
            else:
                lines.append(cur); cur = w
            if len(lines) >= max_lines:
                break
        if cur and len(lines) < max_lines:
            lines.append(cur)
        for line in lines:
            draw.text((x, y), line, font=font, fill=fill)
            try:
                h = draw.textbbox((0, 0), line, font=font)[3]
            except Exception:
                h = 40
            y += h + line_gap
        return y

    def frame_image(idx, scene, t):
        img = Image.new("RGB", (W, H), bg)
        draw = ImageDraw.Draw(img)
        # gradiente manual
        for y in range(H):
            mix = y / H
            r = int(bg[0] * (1-mix) + (accent2[0]//2) * mix)
            g = int(bg[1] * (1-mix) + (accent2[1]//2) * mix)
            b = int(bg[2] * (1-mix) + (accent2[2]//2) * mix)
            draw.line((0, y, W, y), fill=(r, g, b))
        pulse = int(24 * t)
        draw.ellipse((-180+pulse, -100, 430+pulse, 520), fill=tuple(min(255, c//2 + 20) for c in accent2))
        draw.ellipse((430-pulse, 820, 900-pulse, 1360), fill=tuple(max(0, c//2) for c in accent))
        # card principal
        draw.rounded_rectangle((42, 54, W-42, H-62), radius=42, fill=(10, 12, 28), outline=(255,255,255), width=2)
        # topo
        draw.rounded_rectangle((70, 84, 310, 132), radius=22, fill=accent)
        draw.text((92, 98), "RENDA DIGITAL IA", font=badge, fill=(0,0,0))
        draw.text((70, 154), scene.get("tag", "VENDA"), font=badge, fill=(235,235,255))
        # mockup temático
        mx, my = 430, 180
        draw.rounded_rectangle((mx, my, mx+190, my+260), radius=24, fill=(246,246,250))
        draw.rounded_rectangle((mx+16, my+18, mx+174, my+64), radius=14, fill=accent2)
        draw.text((mx+30, my+30), "KIT DIGITAL", font=mini, fill=(255,255,255))
        for j, icon in enumerate(theme["icons"][:3]):
            yy = my + 95 + j*47
            draw.text((mx+28, yy), icon, font=mid, fill=(15,15,25))
            draw.line((mx+70, yy+19, mx+155, yy+19), fill=(80,80,100), width=3)
        draw.rounded_rectangle((mx+25, my+215, mx+165, my+245), radius=12, fill=accent)
        # headline
        y = 500
        y = draw_text_lines(draw, scene["text"], (74, y), big, (255,255,255), 585, 10, 4)
        y += 18
        y = draw_text_lines(draw, scene["small"], (76, y), mid, (226, 224, 245), 560, 8, 3)
        # CTA
        draw.rounded_rectangle((76, 1040, W-76, 1118), radius=30, fill=accent2)
        draw.text((112, 1062), "Ver amostra grátis • Comprar pelo link", font=mid, fill=(255,255,255))
        # progresso
        draw.rounded_rectangle((76, 1162, W-76, 1178), radius=8, fill=(50, 54, 85))
        pw = int((idx + t) / len(scenes) * (W-152))
        draw.rounded_rectangle((76, 1162, 76+pw, 1178), radius=8, fill=accent)
        draw.text((76, 1202), "Produto digital editável. Sem promessa milagrosa.", font=small, fill=(190,190,210))
        return img

    try:
        with imageio.get_writer(str(out), fps=fps, codec="libx264", quality=7, pixelformat="yuv420p", macro_block_size=16) as writer:
            for idx, scene in enumerate(scenes):
                for f in range(frames_per_scene):
                    t = f / max(1, frames_per_scene - 1)
                    writer.append_data(np.asarray(frame_image(idx, scene, t)))
        return out
    except Exception as exc:
        # Fallback: cria um MP4 menor com menos frames; se ainda falhar, gera PNG explicativo e re-levanta erro claro.
        fallback = EXPORT_DIR / f"{slugify(title)}-video-v15-fallback.mp4"
        try:
            with imageio.get_writer(str(fallback), fps=8, codec="libx264", quality=5, pixelformat="yuv420p", macro_block_size=16) as writer:
                for idx, scene in enumerate(scenes[:5]):
                    writer.append_data(np.asarray(frame_image(idx, scene, 0.5)))
                    writer.append_data(np.asarray(frame_image(idx, scene, 0.85)))
            return fallback
        except Exception:
            raise RuntimeError(f"Falha ao gerar vídeo MP4. Erro real: {exc}")


def v15_offer_audit(product: Dict[str, Any]) -> Dict[str, Any]:
    content = (product.get("content") or "")
    sales_page = (product.get("sales_page") or "")
    checkout = (product.get("checkout_link") or "").strip()
    score = 0
    checks = []
    def add(name, ok, points, fix):
        nonlocal score
        if ok: score += points
        checks.append({"name": name, "ok": ok, "points": points, "fix": fix})
    add("Título específico", len(product.get("title", "")) >= 18, 12, "Deixe o título mais específico e com promessa clara.")
    add("Conteúdo robusto", len(content) >= 5500, 18, "Regere o produto pela Biblioteca Premium ou botão Regenerar.")
    add("Página de venda forte", len(sales_page) >= 2800, 18, "Regere a página de venda com dor, solução, bônus e FAQ.")
    add("Amostra grátis", bool(product.get("lead_magnet_content")), 12, "Adicione amostra grátis para captar leads.")
    add("Checkout configurado", bool(checkout), 18, "Cadastre na Kiwify/Hotmart e cole o link.")
    add("Público claro", len(product.get("target_audience", "")) >= 20, 10, "Especifique melhor quem compra.")
    add("Visual/tema", True, 12, "Tema visual aplicado automaticamente.")
    return {"score": min(score, 100), "checks": checks}


@app.route("/estudio-visual")
@login_required
def visual_studio_page():
    conn = db_conn()
    products = [row_to_dict(r) for r in conn.execute("SELECT * FROM products ORDER BY created_at DESC LIMIT 30").fetchall()]
    conn.close()
    enriched = []
    for p in products:
        theme = v15_theme_for(p.get("niche", ""), p.get("title", ""))
        enriched.append({"product": p, "theme": theme, "audit": v15_offer_audit(p)})
    return render_template("visual_studio.html", title="Estúdio visual", products=enriched, themes=V15_VISUAL_THEMES)


@app.route("/produtos/<int:product_id>/regenerar-premium-v15", methods=["POST"])
@login_required
def product_regenerate_premium_v15(product_id):
    product = get_product(product_id)
    if not product:
        abort(404)
    assets = generate_product_assets({
        "title": product["title"],
        "niche": product["niche"],
        "product_type": product["product_type"],
        "target_audience": product["target_audience"],
        "promise": product.get("promise") or "economizar tempo com um material pronto, bonito e organizado",
        "discipline": product.get("discipline") or "Não se aplica / produto geral",
        "school_level": product.get("school_level") or "Público geral",
        "price": product.get("price") or 47,
        "pages": 60,
    })
    theme = v15_theme_for(product.get("niche", ""), product.get("title", ""))
    lead = f"""# {theme['emoji']} Amostra grátis — {product['title']}

Você está recebendo uma prévia do material completo.

## O que observar nesta amostra
- Clareza da proposta.
- Organização dos módulos.
- Modelos prontos para adaptar.
- Checklist de uso rápido.

## Mini-modelo preenchível
**Meu objetivo:** ____________________________________
**O que vou adaptar primeiro:** ______________________
**Próximo passo:** ___________________________________

Se a amostra já ajudou, o pacote completo traz mais modelos, bônus e calendário de ação.
"""
    conn = db_conn()
    now = datetime.utcnow().isoformat()
    conn.execute("""
        UPDATE products SET content=?, sales_page=?, social_posts=?, prompt_pack=?, lead_magnet_title=?, lead_magnet_content=?, bonus_stack=?, updated_at=? WHERE id=?
    """, (assets["content"], assets["sales_page"], assets["social_posts"], assets["prompt_pack"], f"Amostra grátis — {product['title']}", lead, "\n".join(_premium_bonus_stack(product['title'], product['niche'])), now, product_id))
    conn.commit(); conn.close()
    # Apaga vídeo antigo para gerar novamente com o tema atualizado.
    for f in EXPORT_DIR.glob(f"{slugify(product['title'])}-video-*.mp4"):
        try: f.unlink()
        except Exception: pass
    flash("Produto regenerado com textos premium v15, emojis moderados, visual temático e material mais vendável.", "success")
    return redirect(url_for("product_detail", product_id=product_id))


@app.route("/produtos/<int:product_id>/video-teste")
@login_required
def product_video_test(product_id):
    product = get_product(product_id)
    if not product:
        abort(404)
    try:
        out = generate_sales_video(product)
        return jsonify({"ok": True, "file": out.name, "size": out.stat().st_size})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

# Atualiza biblioteca premium com modelos mais fortes.
TRENDING_BLUEPRINTS = [
    {"title":"Kit IA para Pequenos Negócios — Prompts, Posts e WhatsApp","niche":"IA para pequenos negócios","product_type":"Pack premium de prompts","target_audience":"MEIs, autônomos, lojas pequenas e prestadores de serviço","promise":"criar posts, mensagens de atendimento, respostas e ofertas com ajuda da IA sem começar do zero","price":47.00,"angle":"🤖 IA simples para vender, atender e divulgar melhor no dia a dia."},
    {"title":"Planner Financeiro Visual — Gastos, Dívidas e Metas","niche":"Finanças pessoais e organização","product_type":"Planner PDF editável","target_audience":"famílias, casais, jovens e pessoas que querem organizar dinheiro","promise":"organizar gastos, contas, dívidas e metas com páginas visuais simples","price":27.00,"angle":"💰 Organização financeira sem promessa de riqueza e sem planilha complicada."},
    {"title":"Kit MEI Organizado — Clientes, Preços e Divulgação","niche":"Pequenos negócios e MEI","product_type":"Pacote digital completo","target_audience":"MEIs, vendedores locais, autônomos e pequenos prestadores","promise":"organizar clientes, pedidos, preços, WhatsApp e divulgação local","price":67.00,"angle":"🛍️ Rotina comercial mais bonita, clara e organizada."},
    {"title":"Agenda Premium para Beleza — Clientes, Posts e Atendimento","niche":"Beleza, estética e atendimento","product_type":"Planner + templates","target_audience":"manicures, designers de sobrancelha, cabeleireiras e profissionais da beleza","promise":"organizar agenda, atendimento, mensagens e posts para clientes","price":47.00,"angle":"✨ Atendimento com aparência mais profissional."},
    {"title":"Kit Marmitaria Lucrativa Organizada — Cardápio, Pedidos e Preços","niche":"Culinária, marmitas e confeitaria","product_type":"Pack de organização","target_audience":"vendedores de marmita, bolos, doces e comida caseira","promise":"organizar cardápio, pedidos, lista de compras e divulgação","price":47.00,"angle":"🍲 Mais clareza para vender comida por encomenda."},
    {"title":"Kit Currículo e Entrevista — Apresentação Profissional","niche":"Carreira, currículo e renda extra","product_type":"Templates + roteiro","target_audience":"pessoas buscando emprego, primeiro trabalho ou recolocação","promise":"organizar currículo, LinkedIn, mensagens e preparação para entrevista","price":37.00,"angle":"💼 Melhor apresentação profissional, sem garantir contratação."},
    {"title":"Pack Redes Sociais 30 Dias — Posts, Legendas e Calendário","niche":"Templates, design e redes sociais","product_type":"Calendário + legendas","target_audience":"empreendedores, criadores e pequenos negócios","promise":"organizar 30 dias de conteúdo com ideias, legendas e chamadas prontas","price":37.00,"angle":"🎨 Conteúdo visual mais organizado para divulgar com constância."},
    {"title":"Mega Kit Professor Total — Atividades por Disciplinas","niche":"Educação - todas as disciplinas","product_type":"Mega kit pedagógico editável","target_audience":"professores, reforço escolar, escolas pequenas e pais","promise":"economizar tempo com atividades, gabaritos, orientações e campos BNCC editáveis","price":47.00,"angle":"📚 Material pedagógico editável com revisão necessária."},
]


# ============================================================================
# v23 — Checkup final: rotas seguras, textos multinicho e execução local correta
# ============================================================================

def _safe_float(value, default=0.0):
    try:
        return float(str(value or default).replace(',', '.'))
    except Exception:
        return float(default)


def product_scorecard(product: Dict[str, Any]) -> Dict[str, Any]:  # override v23 multinicho
    """Auditoria comercial para qualquer nicho, sem ficar presa apenas à educação."""
    title = (product.get("title") or "").strip()
    niche = (product.get("niche") or "").strip()
    promise = (product.get("promise") or "").strip()
    target = (product.get("target_audience") or "").strip()
    price = _safe_float(product.get("price"), 0)
    checkout = bool((product.get("checkout_link") or "").strip())
    content = product.get("content") or ""
    sales_page = product.get("sales_page") or ""
    lead = product.get("lead_magnet_content") or ""
    model = v15_model_for(niche, title)
    is_edu = product_theme(product).get("label", "").lower().startswith("educação")
    score = 0
    items = []
    def add(name, ok, fix, points):
        nonlocal score
        if ok:
            score += points
        items.append({"name": name, "ok": bool(ok), "fix": fix, "points": points})
    add("Título vendável", len(title) >= 18 and any(w in title.lower() for w in ["kit", "pack", "planner", "guia", "agenda", "templates", "prompts", "calendário", "mega"]), "Use um nome com formato claro: Kit, Pack, Planner, Guia, Agenda, Templates ou Prompts.", 12)
    add("Nicho claro", len(niche) >= 6, "Escolha um nicho específico para o comprador entender rapidamente.", 8)
    add("Público definido", len(target) >= 18, "Escreva exatamente quem compra: MEI, autônomos, famílias, profissionais de beleza, vendedores etc.", 12)
    add("Promessa honesta", len(promise) >= 30 and not any(w in promise.lower() for w in ["garantido", "milagroso", "100%", "enriquecer", "cura"]), "Troque promessa exagerada por organização, economia de tempo, clareza e praticidade.", 12)
    add("Preço de entrada testável", 17 <= price <= 97, "Para começar, teste R$ 27, R$ 37, R$ 47 ou R$ 67.", 10)
    add("Conteúdo robusto", len(content) >= 5500, "Regere pela Biblioteca Premium/Estúdio visual para sair com módulos, exemplos, checklists e bônus.", 14)
    add("Página de venda completa", len(sales_page) >= 2600 and any(w in sales_page.lower() for w in ["perguntas", "faq", "garantia", "bônus", "bonus"]), "Inclua dor, transformação, entregáveis, bônus, FAQ, garantia e chamada para ação.", 14)
    add("Isca grátis", len(lead) >= 500, "Mantenha uma amostra grátis para capturar interessados.", 8)
    add("Checkout configurado", checkout, "Cadastre na Kiwify/Hotmart/Eduzz/Monetizze e cole o link do checkout no produto.", 14)
    if is_edu:
        add("Aviso BNCC responsável", "bncc" in content.lower() and any(w in content.lower() for w in ["revise", "revisão", "currículo"]), "Em educação, mantenha aviso para revisar BNCC e currículo local.", 6)
    else:
        add("Aviso responsável", not any(w in sales_page.lower() for w in ["resultado garantido", "dinheiro garantido"]), "Não use promessa de resultado garantido.", 6)
    level = "Pronto para testar tráfego" if score >= 82 else "Quase pronto" if score >= 65 else "Precisa melhorar antes de divulgar forte"
    return {"score": min(score, 100), "level": level, "items": items, "notice": model.get("warnings", PROFESSIONAL_NOTICE)}


def build_ad_creatives(product: Dict[str, Any]) -> str:  # override v23 multinicho
    title = product.get("title", "Produto Digital")
    niche = product.get("niche", "Produto digital")
    price = money(_safe_float(product.get("price"), 47))
    checkout = product.get("checkout_link") or "COLE_AQUI_O_LINK_DO_CHECKOUT"
    url = product_public_url(product)
    theme = product_theme(product)
    model = v15_model_for(niche, title)
    deliverables = public_bullets(product)[:6]
    hooks = [
        f"{theme['emoji']} Você ainda perde tempo começando tudo do zero?",
        "👀 Veja por dentro antes de comprar.",
        "🎁 Baixe uma amostra grátis e confira se faz sentido para você.",
        f"⚡ Um pacote organizado para {model['main_promise']}.",
        f"💎 {title}: aparência profissional, modelos prontos e uso simples.",
        "📲 Ideal para postar, vender, atender ou organizar melhor a rotina.",
        f"🔥 Oferta inicial: {price}. Entrega digital.",
        "✅ Menos improviso, mais clareza e apresentação.",
        "🚀 Um material bonito para adaptar e usar com mais confiança.",
        "🔗 Acesse a página, baixe a amostra e veja o que vem no pacote.",
    ]
    return f"""
# Criativos premium de venda — {title}

## Posicionamento
**Nicho:** {niche}  
**Tema visual:** {theme['emoji']} {theme['label']}  
**Preço inicial sugerido:** {price}  
**Página pública:** {url}  
**Checkout:** {checkout}

## Headline principal
{theme['emoji']} {title}: um pacote digital bonito, organizado e pronto para adaptar.

## Promessa curta
{model['main_promise'].capitalize()} sem começar tudo do zero.

## Benefícios para usar na página e nos posts
""" + "\n".join(deliverables) + f"""

## 10 chamadas prontas para Reels, TikTok, Shorts e WhatsApp
""" + "\n".join([f"{i+1}. {h}" for i, h in enumerate(hooks)]) + f"""

## Roteiro de vídeo curto
Cena 1 — Dor: “Você ainda faz tudo no improviso?”  
Cena 2 — Solução: “Este pacote já vem organizado por módulos.”  
Cena 3 — Prova visual: “Modelos, checklists, mensagens e páginas prontas para adaptar.”  
Cena 4 — Segurança: “Baixe a amostra grátis e veja por dentro.”  
Cena 5 — Ação: “Acesse o link e escolha se faz sentido para você.”

## Mensagem de WhatsApp sem spam
Oi! Preparei uma amostra grátis do **{title}**. É um material digital organizado para {model['main_promise']}. Se quiser ver por dentro, aqui está o link: {url}

## Legenda curta
{theme['emoji']} Novo material digital pronto: **{title}**.  
✅ organizado  
✅ bonito  
✅ fácil de adaptar  
✅ com amostra grátis  

Veja por dentro: {url}
""".strip()


def build_professional_brand_kit(product: Dict[str, Any]) -> str:  # override v23 multinicho
    title = product.get("title", "Produto Digital")
    niche = product.get("niche", "Produto digital")
    theme = product_theme(product)
    model = v15_model_for(niche, title)
    return f"""
# Kit de marca comercial — {title}

## Identidade do produto
- **Nicho:** {niche}
- **Tema visual:** {theme['emoji']} {theme['label']}
- **Promessa:** {model['main_promise']}
- **Página pública:** {product_public_url(product)}
- **Checkout:** {product.get('checkout_link') or 'COLE_AQUI_O_LINK_DO_CHECKOUT'}

## Tom de voz
- Claro, direto e humano.
- Visual de produto pago, sem promessas falsas.
- Emojis moderados para guiar a leitura, não para poluir.
- Foco em praticidade, organização e transformação realista.

## Frase curta da marca
{theme['emoji']} Um pacote digital pronto para organizar, adaptar e colocar em prática.

## Bio curta
Produtos digitais organizados, bonitos e prontos para adaptar. Baixe a amostra grátis e veja por dentro.

## Cores e atmosfera
- Fundo temático do nicho, com contraste alto.
- Cards com bordas suaves e efeito premium.
- Ícones relacionados ao nicho: {' '.join(v15_theme_for(niche, title).get('icons', []))}
- Evitar textos pequenos demais no vídeo.

## Promessas que pode usar
- “Economize tempo na criação do material.”
- “Use modelos prontos e adapte para sua realidade.”
- “Veja por dentro antes de comprar.”
- “Entrega digital organizada.”

## Promessas que deve evitar
- “Ganhe dinheiro garantido.”
- “Resultado 100% garantido.”
- “Produto oficial de órgão público.”
- “Substitui orientação profissional.”
""".strip()


@app.route("/produtos/<int:product_id>/video")
@app.route("/produtos/<int:product_id>/video-venda")
@app.route("/produtos/<int:product_id>/gerar-video/")
@login_required
def video_generate_aliases(product_id):
    return redirect(url_for("video_generate_page", product_id=product_id))


@app.errorhandler(404)
def handle_404(exc):
    back = request.referrer or url_for("dashboard")
    return (
        "<h1>Página não encontrada</h1>"
        "<p>Essa rota não existe nesta versão do sistema ou o produto foi apagado após reinício/deploy.</p>"
        f"<p><a href='{back}'>Voltar</a> · <a href='/dashboard'>Dashboard</a> · <a href='/diagnostico'>Diagnóstico</a></p>",
        404,
    )


@app.errorhandler(500)
def handle_500_final(exc):
    traceback.print_exc()
    return (
        "<h1>Erro interno corrigível</h1>"
        "<p>O sistema encontrou um erro ao processar esta ação. Abra o Diagnóstico e confira os logs do RunSite se persistir.</p>"
        "<p><a href='/dashboard'>Dashboard</a> · <a href='/diagnostico'>Diagnóstico</a></p>",
        500,
    )


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG", "0") == "1")
