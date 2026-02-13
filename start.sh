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

echo "✅ Pre-flight complete - Starting Gunicorn with 8 workers..."

# Start Gunicorn using gunicorn_config.py (8 workers, 20min timeout)
exec gunicorn config.wsgi:application \
    --config gunicorn_config.py \
    --bind "0.0.0.0:${PORT}"
