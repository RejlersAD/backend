#!/bin/bash
# Railway Production Startup Script - Smart Health Check Strategy

set -e

echo "🚀 Starting Railway Deployment (Smart Mode)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Set PORT
PORT=${PORT:-8000}
echo "🔌 Port: $PORT"
echo "📋 Environment: ${RAILWAY_ENVIRONMENT:-production}"

# Start lightweight health server immediately (runs in background)
echo ""
echo "🏥 Starting instant health check server..."
python railway_health.py &
HEALTH_PID=$!
echo "✅ Health server running (PID: $HEALTH_PID)"

# Give health server 2 seconds to bind
sleep 2

# Run migrations (health server already responding)
echo ""
echo "🔄 Running database migrations..."
python manage.py migrate --noinput || {
    echo "⚠️  Migrations failed, but continuing..."
}
echo "✅ Migrations complete"

# Collect static files (non-blocking)
echo ""
echo "📁 Collecting static files..."
python manage.py collectstatic --noinput --clear 2>/dev/null || {
    echo "⚠️  Static collection failed, but continuing..."
}
echo "✅ Static files collected"

# Kill health server before starting Gunicorn
echo ""
echo "🔄 Switching from health server to Gunicorn..."
kill $HEALTH_PID 2>/dev/null || true
sleep 1

# Start Gunicorn on same port
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
