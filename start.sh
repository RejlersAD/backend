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
python manage.py collectstatic --noinput --clear

# Start Gunicorn
exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT}" \
    --workers 1 \
    --timeout 60 \
    --access-logfile - \
    --error-logfile -
