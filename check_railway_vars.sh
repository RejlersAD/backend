#!/bin/bash
# Railway Environment Diagnostic
# Run this in Railway shell to check what environment variables are set
# Usage: railway run bash check_railway_vars.sh

echo "========================================"
echo "🔍 RAILWAY ENVIRONMENT DIAGNOSTIC"
echo "========================================"
echo ""

echo "📋 Critical Variables:"
echo "----------------------------------------"

check_var() {
    local var_name=$1
    local var_value="${!var_name}"
    
    if [ -n "$var_value" ]; then
        # Hide sensitive parts
        if [[ "$var_name" == *"URL"* ]] || [[ "$var_name" == *"KEY"* ]] || [[ "$var_name" == *"PASSWORD"* ]]; then
            echo "✅ $var_name = ${var_value:0:15}...${var_value: -10}"
        else
            echo "✅ $var_name = $var_value"
        fi
    else
        echo "❌ $var_name = NOT SET"
    fi
}

# Check critical variables
check_var "DATABASE_URL"
check_var "REDIS_URL"
check_var "SECRET_KEY"
check_var "DEBUG"
check_var "ALLOWED_HOSTS"
check_var "PORT"
check_var "RAILWAY_ENVIRONMENT"

echo ""
echo "📋 Python Environment:"
echo "----------------------------------------"
python --version 2>&1 || echo "❌ Python not found"
which python 2>&1 || echo "❌ Python path not found"

echo ""
echo "📋 Django Check:"
echo "----------------------------------------"
if [ -n "$DATABASE_URL" ]; then
    echo "Attempting Django setup..."
    timeout 10 python -c "
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
try:
    import django
    django.setup()
    print('✅ Django loaded successfully')
    
    from django.db import connection
    try:
        connection.ensure_connection()
        print('✅ Database connection OK')
    except Exception as e:
        print(f'❌ Database connection failed: {e}')
except Exception as e:
    print(f'❌ Django failed to load: {e}')
" 2>&1 || echo "⚠️  Django check timed out"
else
    echo "⚠️  DATABASE_URL not set - skipping Django check"
fi

echo ""
echo "📋 Network Check:"
echo "----------------------------------------"
echo "Testing health endpoint on port ${PORT:-8000}..."
if curl -s http://localhost:${PORT:-8000}/api/v1/health/ 2>&1 | head -n 5; then
    echo "✅ Health endpoint responding"
else
    echo "⚠️  Health endpoint not responding (service may not be started yet)"
fi

echo ""
echo "========================================"
echo "📊 DIAGNOSTIC COMPLETE"
echo "========================================"
echo ""
echo "💡 If DATABASE_URL is NOT SET:"
echo "   1. Go to Railway Dashboard"
echo "   2. PostgreSQL service → Connect → Copy connection string"
echo "   3. Backend service → Variables → Add DATABASE_URL"
echo "   4. Redeploy"
echo ""
echo "💡 If DATABASE_URL is SET but connection fails:"
echo "   1. Verify the credentials are correct"
echo "   2. Check if the database service is running"
echo "   3. Try connecting with: psql \$DATABASE_URL"
echo ""
