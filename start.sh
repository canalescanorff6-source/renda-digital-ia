#!/usr/bin/env bash
set -eu

# RunSite/Docker: usa a porta que a plataforma fornece.
PORT_TO_USE="${PORT:-8080}"
WORKERS_TO_USE="${WEB_CONCURRENCY:-1}"
TIMEOUT_TO_USE="${GUNICORN_TIMEOUT:-180}"

# Diretórios de runtime. Em hospedagens com container, /tmp evita erro de permissão.
# Para dados permanentes, configure DATABASE_PATH/APP_RUNTIME_DIR apontando para um volume persistente do RunSite.
export APP_RUNTIME_DIR="${APP_RUNTIME_DIR:-/tmp/renda_digital_ia}"
export STORAGE_DIR="${STORAGE_DIR:-$APP_RUNTIME_DIR/storage}"
export EXPORT_DIR="${EXPORT_DIR:-$STORAGE_DIR/exports}"
mkdir -p "$STORAGE_DIR" "$EXPORT_DIR"

printf '[start] APP_RUNTIME_DIR=%s\n' "$APP_RUNTIME_DIR"
printf '[start] STORAGE_DIR=%s\n' "$STORAGE_DIR"
printf '[start] PORT=%s\n' "$PORT_TO_USE"
printf '[start] WEB_CONCURRENCY=%s\n' "$WORKERS_TO_USE"
printf '[start] iniciando gunicorn direto para evitar timeout/rollback no RunSite...\n'

exec gunicorn app:app \
  --bind "0.0.0.0:${PORT_TO_USE}" \
  --workers "${WORKERS_TO_USE}" \
  --timeout "${TIMEOUT_TO_USE}" \
  --access-logfile - \
  --error-logfile - \
  --log-level info
