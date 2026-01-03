#!/bin/bash
set -e
export DJANGO_SETTINGS_MODULE=config.settings
export PYTHONUNBUFFERED=1

echo "================================"
echo "Railway Deployment Starting..."
echo "================================"
echo "PORT: ${PORT:-8000}"
echo "PYTHONPATH: ${PYTHONPATH}"
echo "================================"

# Check if Django can load settings
echo "Testing Django configuration..."
python -c "import django; django.setup(); print('✅ Django settings loaded successfully')" || {
    echo "❌ Failed to load Django settings"
    exit 1
}

# Collect static files
echo "Collecting static files..."
python manage.py collectstatic --noinput --clear || {
    echo "⚠️  Static files collection failed, continuing..."
}

# Run migrations
echo "Migrating database..."
python manage.py migrate --noinput || {
    echo "❌ Database migration failed"
    exit 1
}

echo "================================"
echo "Starting Gunicorn server..."
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

