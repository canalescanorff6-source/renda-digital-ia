#!/usr/bin/env bash
set -e
PORT_TO_USE="${PORT:-5000}"
WORKERS_TO_USE="${WEB_CONCURRENCY:-2}"
exec gunicorn app:app --bind "0.0.0.0:${PORT_TO_USE}" --workers "${WORKERS_TO_USE}" --timeout 180
