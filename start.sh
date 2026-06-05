#!/usr/bin/env bash
set -eu

# RunSite/Docker: usa a porta da plataforma. Se ela não vier, usa 8080.
PORT_TO_USE="${PORT:-8080}"
WORKERS_TO_USE="${WEB_CONCURRENCY:-1}"
TIMEOUT_TO_USE="${GUNICORN_TIMEOUT:-180}"

# Em alguns hosts o diretório /app pode ser somente leitura em runtime.
# Por isso, por padrão salvamos banco/exportações em /tmp.
export APP_RUNTIME_DIR="${APP_RUNTIME_DIR:-/tmp/renda_digital_ia}"
export STORAGE_DIR="${STORAGE_DIR:-$APP_RUNTIME_DIR/storage}"
export EXPORT_DIR="${EXPORT_DIR:-$STORAGE_DIR/exports}"
mkdir -p "$STORAGE_DIR" "$EXPORT_DIR"

echo "[start] APP_RUNTIME_DIR=$APP_RUNTIME_DIR"
echo "[start] STORAGE_DIR=$STORAGE_DIR"
echo "[start] PORT=$PORT_TO_USE"
echo "[start] WEB_CONCURRENCY=$WORKERS_TO_USE"

# Teste de import antes do Gunicorn para mostrar erro real no log, caso exista.
python - <<'PY'
import os
print('[start] testando import do app...')
import app
print('[start] import OK:', app.app.name)
PY

exec gunicorn app:app \
  --bind "0.0.0.0:${PORT_TO_USE}" \
  --workers "${WORKERS_TO_USE}" \
  --timeout "${TIMEOUT_TO_USE}" \
  --access-logfile - \
  --error-logfile -
