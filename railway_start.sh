#!/bin/bash
# Railway Production - Diagnostic Mode

PORT=${PORT:-8000}

echo "==================================="
echo "Railway Startup Diagnostics"
echo "==================================="
echo "PORT: $PORT"
echo "CORS_ALLOW_ALL_ORIGINS: ${CORS_ALLOW_ALL_ORIGINS:-not set}"
echo "DATABASE_URL: ${DATABASE_URL:0:20}... (truncated)"
echo "DJANGO_SETTINGS_MODULE: ${DJANGO_SETTINGS_MODULE:-not set}"
echo "EMAIL_VERIFICATION_REQUIRED: ${EMAIL_VERIFICATION_REQUIRED:-not set}"
echo "==================================="

# CRITICAL FIX: Explicitly set CORS to allow all origins
export CORS_ALLOW_ALL_ORIGINS=True
export DJANGO_SETTINGS_MODULE=config.settings

echo ""
echo "Testing Django configuration..."
python manage.py check --deploy 2>&1 || echo "Django check failed, continuing anyway..."

echo ""
echo "Running migrations..."
python manage.py migrate --noinput 2>&1 || echo "Migrations failed, continuing anyway..."

echo ""
echo "Collecting static files..."
python manage.py collectstatic --noinput 2>&1 || echo "Collectstatic failed, continuing anyway..."

echo ""
echo "Starting Gunicorn on 0.0.0.0:${PORT}..."

# Start Gunicorn with more verbose logging
exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT}" \
    --workers 1 \
    --threads 4 \
    --worker-class gthread \
    --timeout 300 \
    --access-logfile - \
    --error-logfile - \
    --log-level debug \
    --capture-output \
    --enable-stdio-inheritance
