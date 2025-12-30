#!/bin/bash
# Railway Production - Absolute Minimum

PORT=${PORT:-8000}

echo "Starting on port $PORT"

# Try migrations (don't fail if it errors)
python manage.py migrate --noinput 2>&1 || true

# Start Gunicorn
exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT}" \
    --workers 1 \
    --threads 4 \
    --worker-class gthread \
    --timeout 300 \
    --access-logfile - \
    --error-logfile - \
    --log-level info
