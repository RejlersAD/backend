#!/bin/bash
# Railway Production - Simple and Reliable
set -e

# Ensure PORT is properly set
export PORT="${PORT:-8000}"
export DJANGO_SETTINGS_MODULE=config.settings
export PYTHONUNBUFFERED=1

echo "🚀 Starting on port: $PORT"

# SOFT-CODED MIGRATION CONFLICT RESOLUTION
echo "=========================================="
echo "🔍 Checking for migration conflicts..."
echo "=========================================="

# Check if fix_migration_conflict.py exists and run it
if [ -f "fix_migration_conflict.py" ]; then
    echo "✅ Running automated migration conflict resolver..."
    python fix_migration_conflict.py 2>&1 || {
        echo "⚠️  Migration conflict resolver completed with warnings"
        echo "Attempting standard migrations..."
    }
else
    echo "ℹ️  No migration conflict resolver found"
    # Fallback: Try faking known problematic migrations
    python manage.py migrate pid_analysis 0002 --fake 2>/dev/null || echo "  (Migration 0002 not found)"
    python manage.py migrate pid_analysis 0003 --fake 2>/dev/null || echo "  (Migration 0003 not found)"
    python manage.py migrate pid_analysis 0004 --fake 2>/dev/null || echo "  (Migration 0004 not found)"
fi

# Run migrations
echo "=========================================="
echo "🚀 Running database migrations..."
echo "=========================================="
python manage.py migrate --noinput 

# Collect static files
# python manage.py collectstatic --noinput --clear  # TEMP DISABLED

echo "✅ Pre-flight complete - Starting Gunicorn with 8 workers..."

# Start Gunicorn using gunicorn_config.py (8 workers, 20min timeout)
exec gunicorn config.wsgi:application \
    --config gunicorn_config.py \
    --bind "0.0.0.0:${PORT}"
