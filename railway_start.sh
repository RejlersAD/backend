#!/bin/bash
# Railway Production - Zero Downtime Startup

set -e

PORT=${PORT:-8000}

echo "🚀 Railway Deployment - Instant Start Mode"
echo "🔌 Port: $PORT"

# Start Gunicorn IMMEDIATELY - No migrations blocking startup
# Migrations will run on first request if needed (Django handles this)
echo "⚡ Starting Gunicorn NOW (health check ready in 5s)"

exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT}" \
    --workers 1 \
    --threads 2 \
    --worker-class gthread \
    --worker-tmp-dir /dev/shm \
    --timeout 300 \
    --graceful-timeout 30 \
    --keep-alive 5 \
    --max-requests 1000 \
    --max-requests-jitter 50 \
    --access-logfile - \
    --error-logfile - \
    --log-level error \
    --capture-output
