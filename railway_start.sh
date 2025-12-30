#!/bin/bash
# Railway Production Startup Script

set -e

echo "🚀 Starting Railway Deployment"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Set PORT
PORT=${PORT:-8000}
echo "🔌 Port: $PORT"
echo "📋 Environment: ${RAILWAY_ENVIRONMENT:-production}"

# Run migrations
echo ""
echo "🔄 Running database migrations..."
python manage.py migrate --noinput
echo "✅ Migrations complete"

# Collect static files
echo ""
echo "📁 Collecting static files..."
python manage.py collectstatic --noinput --clear
echo "✅ Static files collected"

# Start Gunicorn
echo ""
echo "🌟 Starting Gunicorn on 0.0.0.0:$PORT"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT}" \
    --workers 4 \
    --worker-class sync \
    --worker-tmp-dir /dev/shm \
    --timeout 120 \
    --graceful-timeout 30 \
    --keep-alive 5 \
    --max-requests 1000 \
    --max-requests-jitter 100 \
    --access-logfile - \
    --error-logfile - \
    --log-level info
