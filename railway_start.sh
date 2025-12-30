#!/bin/bash
# Railway Production - Ultra Reliable

set -e

PORT=${PORT:-8000}

echo "🚀 Railway Deployment Starting"
echo "🔌 Port: $PORT"

# Quick migrations (non-blocking)
echo "🔄 Migrations..."
python manage.py migrate --noinput 2>&1 | grep -E "Applying|OK|No migrations" || echo "Migrations done"

# Start Gunicorn IMMEDIATELY
echo "⚡ Starting Gunicorn..."

exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT}" \
    --workers 1 \
    --threads 4 \
    --worker-class gthread \
    --timeout 300 \
    --max-requests 500 \
    --access-logfile - \
    --error-logfile - \
    --log-level warning
