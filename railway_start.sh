#!/bin/bash
set -e

# Activate virtual environment if it exists
if [ -f "/opt/venv/bin/activate" ]; then
    echo "Activating virtual environment..."
    source /opt/venv/bin/activate
fi

export DJANGO_SETTINGS_MODULE=config.settings
export PYTHONUNBUFFERED=1
export PORT="${PORT:-8000}"

echo "================================"
echo "🚀 Railway Deployment Starting..."
echo "================================"
echo "PORT: ${PORT}"
echo "DATABASE_URL: ${DATABASE_URL:0:30}..." 
echo "Python: $(which python)"
echo "================================"

# Test Django settings import
echo "Testing Django configuration..."
python -c "import django; django.setup(); print('✅ Django loaded successfully')" 2>&1 || {
    echo "❌ FATAL: Django settings failed to load"
    echo "Check Railway logs for Python traceback"
    exit 1
}

# Collect static files (don't fail if this errors)
echo "Collecting static files..."
python manage.py collectstatic --noinput --clear 2>&1 || {
    echo "⚠️  Static files collection failed, continuing..."
}

# SOFT-CODED MIGRATION CONFLICT RESOLUTION
echo "=========================================="
echo "🔍 Checking for migration conflicts..."
echo "=========================================="

# Strategy 1: Dynamically detect and fix any InconsistentMigrationHistory across all apps
if [ -f "fix_migration_record.py" ]; then
    echo "✅ Running dynamic migration history consistency fixer..."
    python fix_migration_record.py 2>&1 && {
        echo "✅ Migration history consistency check passed"
    } || {
        echo "⚠️  Migration history fixer completed with warnings"
        echo "    Attempting remaining fixes..."
    }
fi

# Strategy 2: Use automated conflict resolver for table conflicts
if [ -f "fix_migration_conflict.py" ]; then
    echo "✅ Running automated migration conflict resolver..."
    python fix_migration_conflict.py 2>&1 && {
        echo "✅ Migration conflict resolver succeeded"
    } || {
        echo "⚠️  Migration conflict resolver completed with warnings"
        echo "    Continuing with standard migrations..."
    }
else
    echo "⚠️  Migration conflict resolver not found!"
    echo "    This should not happen in production."
    echo "    Attempting standard migrations anyway..."
fi

# Run remaining migrations
echo "=========================================="
echo "🚀 Running database migrations..."
echo "=========================================="
python manage.py migrate --noinput 2>&1 || {
    echo "❌ FATAL: Database migration failed"
    echo "Check DATABASE_URL and PostgreSQL connection"
    echo ""
    echo "Troubleshooting tips:"
    echo "  1. Verify DATABASE_URL is set correctly"
    echo "  2. Check PostgreSQL is accessible"
    echo "  3. Review migration conflicts above"
    exit 1
}

echo "================================"
echo "✅ Pre-flight checks passed"
echo "================================"

# ── SOFT-CODED: Start Celery worker alongside Gunicorn ────────────────────
# Controlled by CELERY_WORKER_ENABLED env var (default: true)
# Set CELERY_WORKER_ENABLED=false to disable (e.g. dedicated Celery service)
if [ "${CELERY_WORKER_ENABLED:-true}" = "true" ]; then
    echo "🔧 Starting Celery worker in background..."
    celery -A config worker \
        --loglevel=info \
        --concurrency="${CELERY_CONCURRENCY:-2}" \
        --pool=prefork \
        --max-tasks-per-child="${CELERY_MAX_TASKS_PER_CHILD:-100}" \
        --without-heartbeat \
        --without-mingle \
        2>&1 | stdbuf -oL sed 's/^/[Celery] /' &
    CELERY_PID=$!
    echo "✅ Celery worker started (PID: ${CELERY_PID})"
else
    echo "⚠️  Celery worker disabled (CELERY_WORKER_ENABLED=false)"
fi

echo "🚀 Starting Gunicorn server..."
echo "================================"

exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:${PORT:-8000} \
    --workers 1 \
    --threads 2 \
    --worker-class sync \
    --timeout 120 \
    --graceful-timeout 30 \
    --keep-alive 5 \
    --log-file - \
    --access-logfile - \
    --error-logfile - \
    --log-level info \
    --capture-output \
    --enable-stdio-inheritance

