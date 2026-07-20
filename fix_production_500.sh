#!/bin/bash
# Quick fix for production 500 error - Apply missing migrations via Railway CLI
# Usage: ./fix_production_500.sh

set -e

echo "================================================================================"
echo "🔧 PRODUCTION FIX - Applying Spec Customization Migrations"
echo "================================================================================"
echo ""

# Check if Railway CLI is installed
if ! command -v railway &> /dev/null; then
    echo "❌ Railway CLI not found!"
    echo ""
    echo "Install it first:"
    echo "  npm install -g @railway/cli"
    echo ""
    echo "Then run: railway login"
    echo "          railway link"
    echo "          ./fix_production_500.sh"
    echo ""
    exit 1
fi

echo "✅ Railway CLI found"
echo ""

# Check migrations status
echo "📋 Checking current migration status..."
echo ""
railway run python manage.py showmigrations spec_customization

echo ""
echo "🔄 Applying missing migrations..."
echo ""
railway run python manage.py migrate spec_customization

echo ""
echo "✅ Verifying migrations applied..."
echo ""
railway run python manage.py showmigrations spec_customization | grep spec_customization

echo ""
echo "🔍 Checking if columns exist in database..."
echo ""
railway run python check_production_migrations.py || echo "⚠️  Check script not found (optional)"

echo ""
echo "================================================================================"
echo "✅ MIGRATIONS APPLIED!"
echo "================================================================================"
echo ""
echo "Next steps:"
echo "  1. Test production: https://www.radai.ae/engineering/digitization/spec-customization"
echo "  2. Check console - should see 200 OK instead of 500 error"
echo "  3. Job history table should load successfully"
echo ""
echo "If still seeing errors:"
echo "  - Check Railway logs: railway logs"
echo "  - Restart service: railway restart"
echo "  - Or trigger redeploy in Railway dashboard"
echo ""
echo "================================================================================"
