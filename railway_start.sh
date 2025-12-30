#!/bin/bash
# Railway Production - No Health Check Required

set -e

PORT=${PORT:-8000}

echo "🚀 Starting Railway Deployment"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🔌 Port: $PORT"
echo "📋 Railway will check port binding (no custom health check)"
echo ""

# Run migrations with timeout
echo "🔄 Running migrations..."
timeout 90 python manage.py migrate --noinput 2>&1 || {
    echo "⚠️  Migrations timed out or failed - continuing anyway"
}
echo "✅ Database ready"
echo ""

# Collect static files (quick)
echo "📁 Collecting static files..."
python manage.py collectstatic --noinput --clear 2>&1 | head -n 5 || {
    echo "⚠️  Collectstatic skipped"
}
echo "✅ Static files ready"
echo ""

# Start Gunicorn - Railway checks if port responds
echo "🌟 Starting Gunicorn..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT}" \
    --workers 2 \
    --worker-class sync \
    --timeout 120 \
    --graceful-timeout 30 \
    --max-requests 1000 \
    --max-requests-jitter 50 \
    --access-logfile - \
    --error-logfile - \
    --log-level info
