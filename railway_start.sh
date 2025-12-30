#!/bin/bash
# Railway Production - Simple & Reliable

set -e

PORT=${PORT:-8000}

echo "🚀 Railway Deployment"
echo "🔌 Port: $PORT"

# Quick migrations (with timeout protection)
echo "🔄 Migrations..."
timeout 60 python manage.py migrate --noinput || echo "⚠️ Migrations skipped"

# Start Gunicorn
echo "⚡ Starting Gunicorn..."

exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT}" \
    --workers 2 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile - \
    --log-level info
