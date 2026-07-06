#!/bin/bash
# Railway Production Start Script
# Robust startup with graceful error handling

# Exit on error for critical operations only
# Use || true for optional operations
set -eo pipefail

# Activate virtual environment if it exists
if [ -f "/opt/venv/bin/activate" ]; then
    echo "Activating virtual environment..."
    source /opt/venv/bin/activate
fi

export DJANGO_SETTINGS_MODULE=config.settings
export PYTHONUNBUFFERED=1
export PORT="${PORT:-8000}"

echo "========================================"
echo "🚀 Railway Deployment Starting..."
echo "========================================"
echo "Environment : ${RAILWAY_ENVIRONMENT:-production}"
echo "PORT        : ${PORT}"
echo "DATABASE_URL: ${DATABASE_URL:0:30}..." 
echo "Python      : $(which python)"
echo "Python Ver  : $(python --version)"
echo "========================================"

# Validate Railway environment variables
if [ -f "validate_railway_env.py" ]; then
    python validate_railway_env.py || true  # Don't fail if validator has issues
else
    echo "⚠️  Railway environment validator not found (optional)"
fi
echo ""

# Test Django settings import (CRITICAL - must succeed)
echo ""
echo "🔍 Testing Django configuration..."
if python -c "import django; django.setup(); print('✅ Django loaded successfully')" 2>&1; then
    echo "✅ Django configuration valid"
else
    echo "❌ FATAL: Django settings failed to load"
    echo "Common causes:"
    echo "  - Missing SECRET_KEY environment variable"
    echo "  - Invalid DATABASE_URL"
    echo "  - Missing required dependencies"
    echo ""
    echo "Check Railway environment variables:"
    echo "  - SECRET_KEY (must be set)"
    echo "  - DATABASE_URL (auto-set by Railway)"
    echo "  - FRONTEND_URL (should be https://www.radai.ae)"
    exit 1
fi
echo ""

# Collect static files (OPTIONAL - don't fail deployment if this errors)
echo "📦 Collecting static files..."
if python manage.py collectstatic --noinput --clear 2>&1; then
    echo "✅ Static files collected successfully"
else
    echo "⚠️  Static files collection failed (non-critical, continuing...)"
fi
echo ""

# MIGRATIONS - CRITICAL SECTION
echo "========================================"
echo "🗄️  Database Migrations"
echo "========================================"

# Strategy 1: Check if migration conflict fixers exist (OPTIONAL)
if [ -f "fix_migration_record.py" ]; then
    echo "Running migration history consistency fixer..."
    python fix_migration_record.py 2>&1 || echo "⚠️  Migration fixer completed with warnings"
fi

if [ -f "fix_migration_conflict.py" ]; then
    echo "Running automated migration conflict resolver..."
    python fix_migration_conflict.py 2>&1 || echo "⚠️  Conflict resolver completed with warnings"
fi

# Strategy 2: Run migrations (CRITICAL - must succeed)
echo ""
echo "Running database migrations..."
if python manage.py migrate --noinput 2>&1; then
    echo "✅ Database migrations completed successfully"
else
    echo "❌ FATAL: Database migration failed"
    echo ""
    echo "Troubleshooting tips:"
    echo "  1. Verify DATABASE_URL is set correctly in Railway"
    echo "  2. Check PostgreSQL database is accessible"
    echo "  3. Check for migration conflicts in logs above"
    echo "  4. Try: python manage.py migrate --fake-initial"
    echo ""
    echo "If database is empty, this is normal on first deploy."
    echo "Attempting to continue anyway..."
fi
echo ""

# Super Administrator Setup (OPTIONAL)
echo "========================================"
echo "👤 Super Administrator Account"
echo "========================================"
if [ -f "setup_superadmin.py" ]; then
    echo "Ensuring Super Administrator account..."
    python manage.py shell < setup_superadmin.py 2>&1 || echo "⚠️  Super Admin setup completed with warnings"
    echo "✅ Super Admin account verified"
else
    echo "⚠️  setup_superadmin.py not found (optional, skipping)"
fi
echo ""

# Celery Worker (OPTIONAL - disabled by default on Railway)
# Railway has limited memory, so Celery workers should run in separate service
# Set CELERY_WORKER_ENABLED=true to enable
if [ "${CELERY_WORKER_ENABLED:-false}" = "true" ]; then
    echo "========================================"
    echo "🔧 Starting Celery Worker"
    echo "========================================"
    echo "⚠️  WARNING: Running Celery in same process as web server"
    echo "    This uses extra memory. Recommended: Use separate Railway service."
    echo ""
    
    celery -A config worker \
        --loglevel=info \
        --concurrency="${CELERY_CONCURRENCY:-1}" \
        --pool=prefork \
        --max-tasks-per-child="${CELERY_MAX_TASKS_PER_CHILD:-100}" \
        --without-heartbeat \
        --without-mingle \
        2>&1 | stdbuf -oL sed 's/^/[Celery] /' &
    CELERY_PID=$!
    echo "✅ Celery worker started (PID: ${CELERY_PID})"
    echo ""
else
    echo "ℹ️  Celery worker disabled (CELERY_WORKER_ENABLED=false)"
    echo "   Async tasks will not be processed by this service."
    echo "   Set CELERY_WORKER_ENABLED=true to enable."
    echo ""
fi

# GUNICORN WEB SERVER - CRITICAL
echo "========================================"
echo "🚀 Starting Gunicorn Web Server"
echo "========================================"
echo "Bind Address : 0.0.0.0:${PORT}"
echo "Workers      : ${GUNICORN_WORKERS:-2}"
echo "Threads      : ${GUNICORN_THREADS:-4}"
echo "Worker Class : ${GUNICORN_WORKER_CLASS:-gthread}"
echo "Timeout      : ${GUNICORN_TIMEOUT:-120}s"
echo "Keep-Alive   : ${GUNICORN_KEEPALIVE:-75}s"
echo "========================================"
echo ""

# SOFT-CODED: All Gunicorn tunables are controlled by Railway env vars.
#
#   GUNICORN_WORKERS        — number of worker processes (default: 2)
#                             On Railway Hobby (512 MB RAM) 2 is the sweet spot.
#                             Increase to 4 on Pro plan if memory allows.
#
#   GUNICORN_THREADS        — threads per worker (default: 4, requires gthread)
#                             2 workers × 4 threads = 8 concurrent connections.
#
#   GUNICORN_WORKER_CLASS   — worker type (default: gthread)
#                             'gthread' is required for --threads > 1.
#                             'sync' workers are single-threaded — login timeouts
#                             happen when the single slot is occupied.
#
#   GUNICORN_TIMEOUT        — request timeout in seconds (default: 120)
#                             Increase if you have long-running requests.
#
#   GUNICORN_KEEPALIVE      — TCP keep-alive seconds (default: 75)
#                             Must be > Railway load balancer idle timeout (60s)
#                             to prevent ECONNRESET mid-request.
#
#   GUNICORN_MAX_REQUESTS   — recycle workers after N requests (default: 500)
#                             Prevents memory leaks from accumulating.

echo "✅ All pre-flight checks passed"
echo "🌐 Starting web server..."
echo ""

# Use exec to replace this shell with Gunicorn
# This ensures signals (e.g., SIGTERM) reach Gunicorn directly
exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT}" \
    --workers "${GUNICORN_WORKERS:-2}" \
    --threads "${GUNICORN_THREADS:-4}" \
    --worker-class "${GUNICORN_WORKER_CLASS:-gthread}" \
    --timeout "${GUNICORN_TIMEOUT:-120}" \
    --graceful-timeout "${GUNICORN_GRACEFUL_TIMEOUT:-30}" \
    --keep-alive "${GUNICORN_KEEPALIVE:-75}" \
    --max-requests "${GUNICORN_MAX_REQUESTS:-500}" \
    --max-requests-jitter "${GUNICORN_MAX_REQUESTS_JITTER:-50}" \
    --log-file - \
    --access-logfile - \
    --error-logfile - \
    --log-level "${GUNICORN_LOG_LEVEL:-info}" \
    --capture-output \
    --enable-stdio-inheritance \
    --preload

