#!/bin/bash
# Railway Production Startup Script
# Optimized for reliability and performance

set -e  # Exit immediately on error
PORT=${PORT:-8000}

echo "=========================================="
echo "Railway Backend - Starting..."
echo "=========================================="
echo "Port: $PORT"
echo "Django Settings: ${DJANGO_SETTINGS_MODULE:-config.settings}"
echo "Database: ${DATABASE_URL:0:30}..."
echo "=========================================="

# Set environment
export DJANGO_SETTINGS_MODULE=config.settings
export PYTHONUNBUFFERED=1

# Run migrations
echo "[1/3] Running database migrations..."
python manage.py migrate --noinput

# Collect static files
echo "[2/3] Collecting static files..."
python manage.py collectstatic --noinput

# Start Gunicorn
echo "[3/3] Starting Gunicorn..."
exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT}" \
    --workers 2 \
    --threads 4 \
    --worker-class gthread \
    --timeout 120 \
    --access-logfile - \
    --error-logfile - \
    --log-level info \
    --capture-output
