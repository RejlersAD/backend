#!/bin/bash
# Railway Production - Simple and Reliable
set -e

# Ensure PORT is properly set
export PORT="${PORT:-8000}"
export DJANGO_SETTINGS_MODULE=config.settings
export PYTHONUNBUFFERED=1

echo "🚀 Starting on port: $PORT"

# Run migrations
python manage.py migrate --noinput 

# Collect static files
# python manage.py collectstatic --noinput --clear  # TEMP DISABLED

echo "✅ Pre-flight complete - Starting Gunicorn..."

# Start Gunicorn using gunicorn_config.py for timeout handling
exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT}" \
    --config gunicorn_config.py \
    --worker-tmp-dir /dev/shm \
    --preload \
    --access-logfile - \
    --error-logfile - \
    --capture-output
