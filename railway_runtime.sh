#!/bin/bash

set -e

export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings}"
export PYTHONUNBUFFERED=1
export PORT="${PORT:-8000}"

if [ "${CELERY_WORKER_ENABLED:-false}" = "true" ]; then
    echo "[CELERY] Starting worker..."
    celery -A config worker \
        --loglevel="${CELERY_LOG_LEVEL:-info}" \
        --concurrency="${CELERY_CONCURRENCY:-1}" \
        --pool=prefork \
        --max-tasks-per-child="${CELERY_MAX_TASKS_PER_CHILD:-100}" \
        --without-heartbeat \
        --without-mingle &
    echo "[CELERY] Worker started"
else
    echo "[CELERY] Worker disabled (set CELERY_WORKER_ENABLED=true to enable)"
fi

exec gunicorn config.wsgi_bulletproof:application \
    --bind "0.0.0.0:${PORT}" \
    --workers "${GUNICORN_WORKERS:-1}" \
    --threads "${GUNICORN_THREADS:-4}" \
    --worker-class "${GUNICORN_WORKER_CLASS:-gthread}" \
    --timeout "${GUNICORN_TIMEOUT:-150}" \
    --graceful-timeout "${GUNICORN_GRACEFUL_TIMEOUT:-30}" \
    --keep-alive "${GUNICORN_KEEPALIVE:-75}" \
    --max-requests "${GUNICORN_MAX_REQUESTS:-500}" \
    --max-requests-jitter "${GUNICORN_MAX_REQUESTS_JITTER:-50}" \
    --log-file - \
    --access-logfile - \
    --error-logfile - \
    --log-level "${GUNICORN_LOG_LEVEL:-info}" \
    --capture-output \
    --enable-stdio-inheritance
