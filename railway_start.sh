#!/bin/bash
# Railway Production Startup Script
# Handles migrations and starts the application

set -e  # Exit on any error

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🚀 Railway Deployment - Starting Application"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Check environment
echo "📋 Environment: ${RAILWAY_ENVIRONMENT:-development}"
echo "🔧 Python version: $(python --version)"
echo "📦 Gunicorn version: $(gunicorn --version)"

# Run database migrations
echo ""
echo "🔄 Running database migrations..."
python manage.py migrate --noinput || {
    echo "⚠️  Warning: Migrations failed, continuing anyway..."
}

# Collect static files
echo ""
echo "📁 Collecting static files..."
python manage.py collectstatic --noinput --clear || {
    echo "⚠️  Warning: collectstatic failed, continuing anyway..."
}

# Start Gunicorn
echo ""
echo "🌟 Starting Gunicorn web server..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:$PORT \
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
