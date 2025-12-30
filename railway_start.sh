#!/bin/bash
# Railway Production Startup Script
# Handles migrations and starts the application

# DO NOT use set -e - we want to continue on non-critical errors
set +e

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🚀 Railway Deployment - Starting Application"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Set PORT with fallback (Railway sets PORT env var)
PORT=${PORT:-8000}
echo "🔌 Port: $PORT"

# Check environment
echo "📋 Environment: ${RAILWAY_ENVIRONMENT:-development}"
echo "🔧 Python version: $(python --version)"
echo "📦 Gunicorn version: $(gunicorn --version)"

# Run database migrations
echo ""
echo "🔄 Running database migrations..."
python manage.py migrate --noinput 2>&1
MIGRATE_EXIT_CODE=$?
if [ $MIGRATE_EXIT_CODE -eq 0 ]; then
    echo "✅ Migrations completed successfully"
else
    echo "⚠️  Warning: Migrations returned code $MIGRATE_EXIT_CODE, continuing anyway..."
fi

# Collect static files
echo ""
echo "📁 Collecting static files..."
python manage.py collectstatic --noinput --clear 2>&1
COLLECTSTATIC_EXIT_CODE=$?
if [ $COLLECTSTATIC_EXIT_CODE -eq 0 ]; then
    echo "✅ Static files collected successfully"
else
    echo "⚠️  Warning: collectstatic returned code $COLLECTSTATIC_EXIT_CODE, continuing anyway..."
fi

# Start Gunicorn
echo ""
echo "🌟 Starting Gunicorn web server on 0.0.0.0:$PORT"
echo "🔍 Using simple_health:application for fast health checks"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Use simple_health wrapper for immediate health check response
exec gunicorn simple_health:application \
    --bind "0.0.0.0:${PORT}" \
    --workers 3 \
    --threads 2 \
    --worker-class sync \
    --worker-tmp-dir /dev/shm \
    --timeout 120 \
    --graceful-timeout 30 \
    --keep-alive 5 \
    --max-requests 1000 \
    --max-requests-jitter 50 \
    --access-logfile - \
    --error-logfile - \
    --log-level info \
    --capture-output \
    --enable-stdio-inheritance
