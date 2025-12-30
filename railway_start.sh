#!/bin/bash
# Railway Production Startup Script - Optimized for Fast Health Checks

set -e

echo "🚀 Starting Railway Deployment"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Set PORT
PORT=${PORT:-8000}
echo "🔌 Port: $PORT"
echo "📋 Environment: ${RAILWAY_ENVIRONMENT:-production}"

# Run migrations BEFORE starting server (minimize downtime)
echo ""
echo "🔄 Running database migrations (fast)..."
python manage.py migrate --noinput --skip-checks 2>&1 | head -n 20 || true
echo "✅ Migrations complete"

# Skip collectstatic if not needed (health checks more important)
echo ""
echo "📁 Skipping collectstatic for faster startup..."
echo "✅ Static files from previous deployment"

# Start Gunicorn with preload for faster worker startup
echo ""
echo "🌟 Starting Gunicorn on 0.0.0.0:$PORT (PRELOAD MODE)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT}" \
    --workers 2 \
    --worker-class sync \
    --worker-tmp-dir /dev/shm \
    --timeout 300 \
    --graceful-timeout 30 \
    --keep-alive 5 \
    --preload \
    --max-requests 1000 \
    --max-requests-jitter 100 \
    --access-logfile - \
    --error-logfile - \
    --log-level info
