#!/bin/bash
set -e
export DJANGO_SETTINGS_MODULE=config.settings
export PYTHONUNBUFFERED=1

echo "================================"
echo "🚀 Railway Deployment Starting..."
echo "================================"
echo "PORT: ${PORT:-8000}"
echo "DATABASE_URL: ${DATABASE_URL:0:30}..." 
echo "================================"

# Test Django settings import
echo "Testing Django configuration..."
python -c "import django; django.setup(); print('✅ Django loaded successfully')" 2>&1 || {
    echo "❌ FATAL: Django settings failed to load"
    echo "Check Railway logs for Python traceback"
    exit 1
}

# Collect static files (don't fail if this errors)
echo "Collecting static files..."
python manage.py collectstatic --noinput --clear 2>&1 || {
    echo "⚠️  Static files collection failed, continuing..."
}

# Run migrations
echo "Running database migrations..."
python manage.py migrate --noinput 2>&1 || {
    echo "❌ FATAL: Database migration failed"
    echo "Check DATABASE_URL and PostgreSQL connection"
    exit 1
}

echo "================================"
echo "✅ Pre-flight checks passed"
echo "🚀 Starting Gunicorn server..."
echo "================================"

exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:${PORT:-8000} \
    --workers 1 \
    --threads 2 \
    --worker-class sync \
    --timeout 120 \
    --graceful-timeout 30 \
    --keep-alive 5 \
    --log-file - \
    --access-logfile - \
    --error-logfile - \
    --log-level info \
    --capture-output \
    --enable-stdio-inheritance

