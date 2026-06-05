import os
import sys
import re
import json
import csv
import io
import sqlite3
import textwrap
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


@app.route("/healthz")
def healthz():
    return {"status": "ok", "app": APP_NAME, "database": str(DB_PATH.name)}


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
        "description": "Plataforma para criar, organizar e divulgar produtos digitais educacionais.",
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
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9áàãâéêíóôõúçñ\s-]", "", text)
    text = text.replace("ç", "c").replace("ã", "a").replace("õ", "o")
    text = re.sub(r"\s+", "-", text)
    return text[:70].strip("-") or "produto"


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
        "name": "Educação - todas as disciplinas",
        "score": 99,
        "why": "Público maior: professores, reforço escolar, pais, escolas pequenas e criadores de materiais didáticos. Permite vender pacotes por ano, bimestre e disciplina.",
        "examples": ["Mega Kit Atividades", "Pacote Bimestral", "Banco de Questões", "Simulados"],
        "price": "R$ 27,00 a R$ 97,00",
    },
    {
        "name": "Ensino fundamental anos iniciais",
        "score": 97,
        "why": "Alta procura por atividades prontas, alfabetização, matemática básica, leitura, ciências e datas comemorativas.",
        "examples": ["Kit 1º ao 5º ano", "Alfabetização", "Matemática Básica", "Projetos Interdisciplinares"],
        "price": "R$ 19,90 a R$ 67,00",
    },
    {
        "name": "Ensino fundamental anos finais",
        "score": 94,
        "why": "Permite vender por disciplina: Português, Matemática, Ciências, História, Geografia, Inglês e Artes.",
        "examples": ["Banco de Questões 6º ao 9º", "Revisões", "Trabalhos", "Simulados"],
        "price": "R$ 29,90 a R$ 97,00",
    },
    {
        "name": "Ensino médio e ENEM",
        "score": 91,
        "why": "Bom para simulados, redação, mapas mentais, revisão por área e listas de exercícios.",
        "examples": ["Redação", "Simulados ENEM", "Biologia", "Física", "Química", "Matemática"],
        "price": "R$ 37,00 a R$ 147,00",
    },
    {
        "name": "Reforço escolar e pais",
        "score": 90,
        "why": "Público maior fora da escola: pais, tutores, professores particulares e reforço escolar buscam material pronto para acompanhar aprendizagem.",
        "examples": ["Tarefas de Casa", "Caderno de Reforço", "Leitura", "Tabuada", "Interpretação"],
        "price": "R$ 17,00 a R$ 67,00",
    },
    {
        "name": "Datas comemorativas e projetos escolares",
        "score": 87,
        "why": "Produtos sazonais podem vender bem em épocas específicas: volta às aulas, festa junina, consciência negra, meio ambiente e natal.",
        "examples": ["Projetos Prontos", "Murais", "Sequências Didáticas", "Atividades Temáticas"],
        "price": "R$ 12,90 a R$ 49,90",
    },
]

PRODUCT_TYPES = [
    "Mega kit de atividades",
    "Pacote bimestral",
    "Banco de questões",
    "Simulado com gabarito",
    "Apostila editável",
    "Sequência didática",
    "Planner/Checklist",
    "Ebook PDF",
    "Pack de templates",
]

SUBJECTS = [
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
                "Você é um especialista em produtos digitais educacionais no Brasil. "
                "Crie conteúdo útil, claro, honesto e adaptável para escolas, professores, pais e reforço escolar. "
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
    price = float(str(form.get("price") or 47.00).replace(",", "."))

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
        "title": "Mega Kit Professor Total - Atividades para Todas as Disciplinas",
        "niche": "Educação - todas as disciplinas",
        "discipline": "Todas as disciplinas",
        "school_level": "Ensino fundamental anos iniciais",
        "product_type": "Mega kit de atividades",
        "target": "professores do 1º ao 5º ano, reforço escolar e pais",
        "price": 47.00,
        "promise": "economizar tempo com atividades prontas de Português, Matemática, Ciências, História, Geografia, Inglês, Artes e Educação Física",
        "channel": "TikTok, Instagram, Facebook, WhatsApp e grupos de professores",
        "difficulty": "baixa",
    },
    {
        "title": "Banco de Questões Fundamental - 6º ao 9º Ano",
        "niche": "Ensino fundamental anos finais",
        "discipline": "Todas as disciplinas",
        "school_level": "Ensino fundamental anos finais",
        "product_type": "Banco de questões",
        "target": "professores do 6º ao 9º ano e reforço escolar",
        "price": 67.00,
        "promise": "montar provas, revisões e simulados com questões prontas e gabarito separado",
        "channel": "grupos de professores, Instagram, Facebook e WhatsApp",
        "difficulty": "média",
    },
    {
        "title": "Pacote Bimestral Pronto - Português e Matemática",
        "niche": "Ensino fundamental anos iniciais",
        "discipline": "Todas as disciplinas",
        "school_level": "Ensino fundamental anos iniciais",
        "product_type": "Pacote bimestral",
        "target": "professores dos anos iniciais e reforço escolar",
        "price": 39.90,
        "promise": "ter um pacote organizado para trabalhar leitura, interpretação, escrita, operações e problemas matemáticos",
        "channel": "WhatsApp, grupos de pedagogia, Instagram e Pinterest",
        "difficulty": "baixa",
    },
    {
        "title": "Kit Redação Nota Melhor - Temas, Modelos e Correção Guiada",
        "niche": "Ensino médio e ENEM",
        "discipline": "Redação",
        "school_level": "ENEM e vestibulares",
        "product_type": "Apostila editável",
        "target": "estudantes, professores de redação e cursinhos",
        "price": 57.00,
        "promise": "organizar treino de redação com temas, repertórios, estrutura e checklist de revisão",
        "channel": "TikTok, Instagram Reels, YouTube Shorts e grupos de estudos",
        "difficulty": "média",
    },
    {
        "title": "Simulados ENEM por Área - Ciências da Natureza e Matemática",
        "niche": "Ensino médio e ENEM",
        "discipline": "Todas as disciplinas",
        "school_level": "ENEM e vestibulares",
        "product_type": "Simulado com gabarito",
        "target": "estudantes do ensino médio, professores e reforço escolar",
        "price": 77.00,
        "promise": "treinar com simulados organizados, gabarito comentado e plano de revisão",
        "channel": "TikTok, Instagram, grupos de estudo e WhatsApp",
        "difficulty": "média",
    },
    {
        "title": "Caderno de Reforço Escolar para Pais - Português e Matemática",
        "niche": "Reforço escolar e pais",
        "discipline": "Todas as disciplinas",
        "school_level": "Reforço escolar",
        "product_type": "Apostila editável",
        "target": "pais, mães, tutores e professores particulares",
        "price": 29.90,
        "promise": "ajudar crianças em casa com atividades simples de leitura, escrita, tabuada e problemas",
        "channel": "Facebook, WhatsApp, Instagram e comunidades locais",
        "difficulty": "baixa",
    },
    {
        "title": "Kit Ciências na Prática - Experimentos Simples e Atividades",
        "niche": "Ensino fundamental anos finais",
        "discipline": "Ciências",
        "school_level": "Ensino fundamental anos finais",
        "product_type": "Sequência didática",
        "target": "professores de Ciências, reforço escolar e escolas pequenas",
        "price": 37.00,
        "promise": "aplicar atividades e experiências simples com materiais acessíveis e orientação clara",
        "channel": "Instagram, TikTok, YouTube Shorts e grupos de professores",
        "difficulty": "média",
    },
    {
        "title": "Projetos Escolares Prontos - Datas Comemorativas do Ano",
        "niche": "Datas comemorativas e projetos escolares",
        "discipline": "Todas as disciplinas",
        "school_level": "Ensino fundamental anos iniciais",
        "product_type": "Sequência didática",
        "target": "professores, coordenação pedagógica e escolas pequenas",
        "price": 47.00,
        "promise": "ter projetos prontos para trabalhar temas do calendário escolar com atividades e culminância",
        "channel": "Pinterest, Instagram, Facebook e grupos de pedagogia",
        "difficulty": "baixa",
    },
    {
        "title": "Pack História e Geografia - Mapas, Linha do Tempo e Atividades",
        "niche": "Ensino fundamental anos finais",
        "discipline": "História",
        "school_level": "Ensino fundamental anos finais",
        "product_type": "Banco de questões",
        "target": "professores de História e Geografia",
        "price": 39.90,
        "promise": "economizar tempo com atividades de leitura, mapas, linha do tempo, paisagem e sociedade",
        "channel": "Instagram, Facebook, WhatsApp e grupos de professores",
        "difficulty": "média",
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
    if any(w in text for w in ["professor", "escola", "pais", "reforço", "pedagogia", "atividade", "questões", "simulado", "redação", "matemática", "português", "ciências", "história", "geografia", "enem"]):
        score += 12
        reasons.append("Público tem sinal de necessidade prática.")
    else:
        reasons.append("Público parece amplo; nichar mais pode aumentar conversão.")
    if any(w in text for w in ["economizar", "pronto", "organizar", "evitar", "melhorar", "gabarito", "checklist"]):
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
        price = float(str(form.get("price") or 47.00).replace(",", "."))
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
            status=?, checkout_link=?, platform=?, content=?, sales_page=?, social_posts=?, public_slug=?, public_enabled=?, lead_magnet_title=?, lead_magnet_content=?, guarantee_days=?, bonus_stack=?, updated_at=?
            WHERE id=?
            """,
            (
                form.get("title"), form.get("niche"), form.get("product_type"), form.get("discipline"), form.get("school_level"), form.get("target_audience"),
                form.get("promise"), float(str(form.get("price") or 0).replace(",", ".")), form.get("status"),
                form.get("checkout_link"), form.get("platform"), form.get("content"), form.get("sales_page"),
                form.get("social_posts"), public_slug, int(form.get("public_enabled", "0") == "1"), form.get("lead_magnet_title"), form.get("lead_magnet_content"), int(form.get("guarantee_days") or 7), form.get("bonus_stack"), now, product_id,
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
    if section not in {"content", "sales_page", "social_posts", "prompt_pack", "bncc_map"}:
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
    product = get_product(product_id)
    if not product:
        abort(404)
    out = generate_sales_video(product)
    return send_file(out, as_attachment=True, download_name=out.name, mimetype="video/mp4")


@app.route("/v/<int:product_id>/video-venda.mp4")
def public_sales_video(product_id):
    product = get_product(product_id)
    if not product or not product.get("public_enabled"):
        abort(404)
    out = generate_sales_video(product)
    return send_file(out, mimetype="video/mp4")


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


ensure_social_tables()


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG", "0") == "1")
