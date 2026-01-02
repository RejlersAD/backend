#!/bin/bash
set -e
export DJANGO_SETTINGS_MODULE=config.settings
export PYTHONUNBUFFERED=1

echo "Migrating database..."
python manage.py migrate --noinput

echo "Starting server on port ${PORT:-8000}..."
exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:${PORT:-8000} \
    --workers 1 \
    --timeout 60 \
    --log-file - \
    --access-logfile - \
    --error-logfile - \
    --log-level debug

