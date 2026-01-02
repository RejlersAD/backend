#!/bin/bash
# Railway Production - Enhanced Diagnostic Mode
set -e  # Exit on error
set -x  # Print commands

PORT=${PORT:-8000}

echo "=========================================="
echo "RAILWAY STARTUP DIAGNOSTICS - ENHANCED"
echo "=========================================="
echo "Timestamp: $(date)"
echo "PORT: $PORT"
echo "PYTHONUNBUFFERED: ${PYTHONUNBUFFERED:-not set}"
echo "DJANGO_SETTINGS_MODULE: ${DJANGO_SETTINGS_MODULE:-not set}"
echo "DATABASE_URL: ${DATABASE_URL:0:30}..."
echo "MONGODB_URI: ${MONGODB_URI:0:30}..."
echo "CORS_ALLOW_ALL_ORIGINS: ${CORS_ALLOW_ALL_ORIGINS:-not set}"
echo "EMAIL_HOST: ${EMAIL_HOST:-not set}"
echo "SECRET_KEY: ${SECRET_KEY:0:10}..."
echo "DEBUG: ${DEBUG:-not set}"
echo "ALLOWED_HOSTS: ${ALLOWED_HOSTS:-not set}"
echo "=========================================="

# Set critical environment variables
export DJANGO_SETTINGS_MODULE=config.settings
export PYTHONUNBUFFERED=1

echo ""
echo "[1/5] Testing Python and Django imports..."
python -c "import django; print(f'Django version: {django.VERSION}')" || exit 1
python -c "from config import settings; print('Settings imported successfully')" || exit 1

echo ""
echo "[2/5] Running Django system check..."
python manage.py check --deploy 2>&1

echo ""
echo "[3/5] Running database migrations..."
python manage.py migrate --noinput 2>&1

echo ""
echo "[4/5] Collecting static files..."
python manage.py collectstatic --noinput 2>&1

echo ""
echo "[5/5] Testing database connection..."
python manage.py shell -c "from django.db import connection; connection.ensure_connection(); print('✅ Database connected')" 2>&1

echo ""
echo "=========================================="
echo "All pre-flight checks passed!"
echo "Starting Gunicorn on 0.0.0.0:${PORT}"
echo "=========================================="

# Start Gunicorn with enhanced logging
exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT}" \
    --workers 2 \
    --threads 4 \
    --worker-class gthread \
    --timeout 300 \
    --access-logfile - \
    --error-logfile - \
    --log-level info \
    --capture-output \
    --enable-stdio-inheritance \
    2>&1
