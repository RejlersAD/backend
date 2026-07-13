"""
Check if latest config changes are deployed in production
Run in Railway shell: python check_production_deployment.py
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from apps.rbac.rbac_config import MODULE_ACCESS_RULES

print("=" * 80)
print("🔍 CHECKING PRODUCTION DEPLOYMENT")
print("=" * 80)

print(f"\n📋 MODULE_ACCESS_RULES Configuration:")
print(f"   check_role_first: {MODULE_ACCESS_RULES.get('check_role_first')}")
print(f"   check_direct_assignment: {MODULE_ACCESS_RULES.get('check_direct_assignment')}")
print(f"   admin_has_all_access: {MODULE_ACCESS_RULES.get('admin_has_all_access')}")
print(f"   superadmin_has_all_access: {MODULE_ACCESS_RULES.get('superadmin_has_all_access')}")

print(f"\n🎯 EXPECTED VALUES:")
print(f"   admin_has_all_access: False  ✅ (Security fix)")
print(f"   superadmin_has_all_access: True  ✅ (Emergency access)")

print(f"\n🔍 CURRENT STATUS:")
admin_bypass = MODULE_ACCESS_RULES.get('admin_has_all_access')
if admin_bypass is True:
    print(f"   ❌ PROBLEM: admin_has_all_access = True")
    print(f"   ❌ Railway hasn't deployed latest changes yet")
    print(f"   ⏳ Wait 2-3 minutes for Railway auto-deploy")
    print(f"   🔄 Then re-run this check")
elif admin_bypass is False:
    print(f"   ✅ CORRECT: admin_has_all_access = False")
    print(f"   ✅ Latest config is deployed!")
    print(f"   ✅ Admin users must now follow ROLE_MODULE_POLICY")
else:
    print(f"   ⚠️  UNKNOWN: admin_has_all_access = {admin_bypass}")

print(f"\n💡 If config is correct but user still sees Finance/QHSE:")
print(f"   1. User still has 'admin' role in database → Run fix_kiran_production.py")
print(f"   2. User's JWT token has cached permissions → User must logout/login")
print(f"   3. Frontend cache → User must hard refresh (Ctrl+F5)")
print()
