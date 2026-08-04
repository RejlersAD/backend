#!/bin/bash
# Smart ICT Admin Setup - Run in Railway Shell
# https://railway.app → backend service → Shell tab
# Paste this entire block and press Enter

echo "================================================================================"
echo "🚀 SMART ICT ADMIN SETUP - ONE COMMAND"
echo "================================================================================"
echo ""
echo "📧 Target User: radai@rejlers.ae"
echo "🎯 Role: ICT Administrator (ict_admin)"
echo "📦 Modules: 6 admin section features only"
echo ""
echo "⏳ Step 1: Running migration to create ICT Admin role..."
python manage.py migrate rbac 2>&1 | grep -E "(Applying|✅|⚠️|Error)" || python manage.py migrate rbac
echo ""
echo "⏳ Step 2: Assigning ICT Admin role to radai@rejlers.ae..."
python manage.py setup_ict_admin
echo ""
echo "================================================================================"
echo "✅ SMART SETUP COMPLETE - User must now refresh browser"
echo "================================================================================"
echo ""
echo "📋 Next Steps:"
echo "  1. Tell radai@rejlers.ae to log out from https://www.radai.ae"
echo "  2. Clear browser cache (Ctrl+Shift+Delete)"
echo "  3. Log back in"
echo "  4. Test all admin URLs - should work!"
echo ""
