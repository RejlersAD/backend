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

# Strategy 1: Use automated conflict resolver if available
if [ -f "fix_migration_conflict.py" ]; then
    echo "✅ Running automated migration conflict resolver..."
    python fix_migration_conflict.py 2>&1 && {
        echo "✅ Migration conflict resolver succeeded"
    } || {
        echo "⚠️  Migration conflict resolver failed, trying fallback..."
        
        # Strategy 2: Fake the specific problematic migration
        echo "Attempting to fake pid_analysis.0003_referencedocument..."
        python manage.py migrate pid_analysis 0003 --fake 2>&1 || {
            echo "ℹ️  Could not fake migration (may not exist or already applied)"
        }
    }
else
    echo "ℹ️  No migration conflict resolver found, using fallback strategy"
    
    # Strategy 2: Fake known problematic migrations
    echo "Attempting to fake known problematic migrations..."
    python manage.py migrate pid_analysis 0002 --fake 2>&1 || echo "  (Migration 0002 not needed)"
    python manage.py migrate pid_analysis 0003 --fake 2>&1 || echo "  (Migration 0003 not needed)"
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

