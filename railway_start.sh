#!/bin/bash
# Railway Production - Bulletproof Startup

# Exit on any error
set -e

# Configuration
PORT=${PORT:-8000}
export PYTHONUNBUFFERED=1
export DJANGO_SETTINGS_MODULE=config.settings

echo "================================"
echo "Starting Railway Backend"
echo "Port: $PORT"
echo "================================"

# Database migrations
python manage.py migrate --noinput || echo "Migration warning (continuing...)"

# Collect static files
python manage.py collectstatic --noinput --clear || echo "Static files warning (continuing...)"

# Start application
echo "Starting Gunicorn..."
exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:$PORT \
    --workers 1 \
    --timeout 60 \
    --access-logfile - \
    --error-logfile - \
    --log-level debug

