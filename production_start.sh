#!/bin/sh
set -eu

# A dedicated Railway Celery service remains the preferred production layout.
# For a single-service deployment, auto-start one worker whenever a broker is
# configured so queued planning jobs have a consumer.
worker_mode="${CELERY_WORKER_ENABLED:-auto}"
broker_config="${CELERY_BROKER_URL:-${REDIS_URL:-${REDIS_HOST:-}}}"
broker_available=false
if [ -n "$broker_config" ] && [ "$broker_config" != "None" ]; then
    broker_available=true
fi

if [ "$worker_mode" = "true" ] || { [ "$worker_mode" = "auto" ] && [ "$broker_available" = "true" ]; }; then
    echo "[startup] Starting embedded Celery worker (concurrency=${CELERY_CONCURRENCY:-1})"
    celery -A config worker \
        --loglevel="${CELERY_LOG_LEVEL:-info}" \
        --concurrency="${CELERY_CONCURRENCY:-1}" \
        --pool=solo \
        --without-heartbeat \
        --without-mingle &
else
    echo "[startup] Celery worker not started (mode=$worker_mode, broker_configured=$broker_available)"
fi

exec gunicorn config.wsgi_bulletproof:application \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers "${GUNICORN_WORKERS:-1}" \
    --timeout "${GUNICORN_TIMEOUT:-150}" \
    --log-level "${GUNICORN_LOG_LEVEL:-info}"
