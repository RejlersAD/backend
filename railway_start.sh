#!/bin/bash
# Railway Production - Absolute Minimum

PORT=${PORT:-8000}

# CRITICAL FIX: Explicitly set CORS to allow all origins
export CORS_ALLOW_ALL_ORIGINS=True

echo "Starting on port $PORT"
echo "CORS_ALLOW_ALL_ORIGINS: $CORS_ALLOW_ALL_ORIGINS"

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
