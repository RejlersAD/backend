#!/bin/bash
# Railway Production Startup - Robust Configuration
set -e  # Exit on error

PORT=${PORT:-8000}
export DJANGO_SETTINGS_MODULE=config.settings
export PYTHONUNBUFFERED=1

echo "=========================================="
echo "Railway Backend Starting..."
echo "Port: $PORT"
echo "=========================================="

# Quick health check
python -c "import django; print('Django OK')" || { echo "ERROR: Django import failed"; exit 1; }

# Run migrations (allow to continue if fails)
python manage.py migrate --noinput 2>&1 || echo "Warning: Migrations had issues"

# Collect static files (allow to continue if fails)
python manage.py collectstatic --noinput 2>&1 || echo "Warning: Static collection had issues"

echo "Starting Gunicorn on 0.0.0.0:${PORT}..."

# Start with minimal config for reliability
exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT}" \
    --workers 2 \
    --worker-class sync \
    --timeout 120 \
    --access-logfile - \
    --error-logfile - \
    --log-level info
