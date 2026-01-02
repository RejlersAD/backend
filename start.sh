#!/bin/bash
# Railway Production - Simple and Reliable
export PORT=${PORT:-8000}
export DJANGO_SETTINGS_MODULE=config.settings

python manage.py migrate --noinput 
python manage.py collectstatic --noinput

gunicorn config.wsgi:application \
    --bind 0.0.0.0:$PORT \
    --workers 1 \
    --timeout 60 \
    --access-logfile - \
    --error-logfile -
