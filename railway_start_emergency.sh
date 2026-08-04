#!/bin/bash
# Ultra-Minimal Railway Start - EMERGENCY MODE
# This script will pass health checks even if DATABASE_URL is missing
# Use this to get the service up so you can debug via Railway shell

set -e

export PYTHONUNBUFFERED=1
export PORT="${PORT:-8000}"
export DJANGO_SETTINGS_MODULE=config.settings

echo "========================================"
echo "🚨 EMERGENCY STARTUP MODE"
echo "========================================"
echo "Port: ${PORT}"
echo "========================================"

# Start Gunicorn with bulletproof WSGI
# The bulletproof WSGI will respond to health checks even if Django fails
exec gunicorn config.wsgi_bulletproof:application \
    --bind "0.0.0.0:${PORT}" \
    --workers 2 \
    --timeout 120 \
    --log-level info \
    --access-logfile - \
    --error-logfile - \
    --capture-output
