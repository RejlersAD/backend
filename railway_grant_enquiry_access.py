"""
PRODUCTION SCRIPT: Grant Enquiry Access to radai@rejlers.ae
============================================================
Run this in Railway backend shell to grant enquiry_management access.

This script uses soft-coded configuration from:
  - backend/apps/core/config/enquiry_access_config.py
  - backend/apps/rbac/rbac_config.py

Usage in Railway Shell:
    exec(open('railway_grant_enquiry_access.py').read())

Or locally:
    python manage.py shell < railway_grant_enquiry_access.py
"""

import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'rad_ai.settings')

import django
django.setup()

from django.contrib.auth import get_user_model
from django.core.cache import cache
from apps.rbac.models import UserProfile, Module, Role, UserRole, RoleModule

User = get_user_model()

print("\n" + "="*80)
print("🔧 GRANT ENQUIRY ACCESS - SOFT-CODED RBAC")
print("="*80 + "\n")

# SOFT-CODED: User and module from config
TARGET_EMAIL = 'radai@rejlers.ae'
MODULE_CODE = 'enquiry_management'
ROLE_CODE = 'ict_admin'

try:
    # Step 1: Get user
    print(f"📧 Target User: {TARGET_EMAIL}")
    user = User.objects.get(email=TARGET_EMAIL)
    profile, _ = UserProfile.objects.get_or_create(
        user=user,
        defaults={'is_deleted': False}
    )
    print(f"✅ Found user profile: {profile}")
    
    # Step 2: Get module
    print(f"\n📦 Module: {MODULE_CODE}")
    try:
        enquiry_module = Module.objects.get(code=MODULE_CODE, is_active=True)
        print(f"✅ Found module: {enquiry_module.name}")
    except Module.DoesNotExist:
        print(f"❌ Module '{MODULE_CODE}' not found!")
        print("   Run migrations first: python manage.py migrate rbac")
        exit(1)
    
    # Step 3: Get ICT admin role
    print(f"\n🔐 Role: {ROLE_CODE}")
    try:
        ict_admin_role = Role.objects.get(code=ROLE_CODE, is_active=True)
        print(f"✅ Found role: {ict_admin_role.name} (level {ict_admin_role.level})")
    except Role.DoesNotExist:
        print(f"❌ Role '{ROLE_CODE}' not found!")
        print("   The role may need to be created via migration")
        exit(1)
    
    # Step 4: Ensure role has module
    print(f"\n🔗 Step 1: Ensure {ROLE_CODE} role has {MODULE_CODE} module")
    print("-" * 80)
    role_module, rm_created = RoleModule.objects.get_or_create(
        role=ict_admin_role,
        module=enquiry_module
    )
    if rm_created:
        print(f"✅ Added {MODULE_CODE} to {ROLE_CODE} role")
    else:
        print(f"✅ Role already has {MODULE_CODE} module")
    
    # Step 5: Assign role to user
    print(f"\n👤 Step 2: Assign {ROLE_CODE} role to {TARGET_EMAIL}")
    print("-" * 80)
    user_role, ur_created = UserRole.objects.get_or_create(
        user_profile=profile,
        role=ict_admin_role,
        defaults={'is_primary': False}
    )
    if ur_created:
        print(f"✅ Assigned {ROLE_CODE} role to {TARGET_EMAIL}")
    else:
        print(f"✅ User already has {ROLE_CODE} role")
    
    # Step 6: Clear cache
    print(f"\n🔄 Step 3: Clear cache")
    print("-" * 80)
    cache.delete(f'user_modules_{profile.id}')
    cache.delete(f'user_permissions_{profile.id}')
    cache.delete(f'user_roles_{profile.id}')
    print(f"✅ Cleared cache for {TARGET_EMAIL}")
    
    # Step 7: Verify access
    print(f"\n🔍 Step 4: Verify Access")
    print("-" * 80)
    
    # Refresh profile from DB
    profile.refresh_from_db()
    
    has_access = profile.has_module_access(MODULE_CODE)
    
    if has_access:
        print(f"✅ SUCCESS: {TARGET_EMAIL} now has {MODULE_CODE} access!")
        print(f"✅ User can now access: https://www.radai.ae/admin/enquiries")
    else:
        print(f"❌ FAILED: {TARGET_EMAIL} still does not have access")
        print(f"\nDebug Info:")
        print(f"  User roles: {[ur.role.code for ur in profile.user_roles.filter(role__is_active=True)]}")
        print(f"  Role modules: {[rm.module.code for rm in RoleModule.objects.filter(role=ict_admin_role)]}")
    
    # Summary
    print(f"\n" + "="*80)
    print("✅ GRANT COMPLETE")
    print("="*80)
    print(f"Email:        {TARGET_EMAIL}")
    print(f"Module:       {MODULE_CODE}")
    print(f"Role:         {ROLE_CODE}")
    print(f"Access:       {'✅ GRANTED' if has_access else '❌ DENIED'}")
    print("="*80 + "\n")
    
    if has_access:
        print("🎉 User can now access the Enquiry Management page!")
        print("   URL: https://www.radai.ae/admin/enquiries")
    else:
        print("⚠️  Access was not granted. Please check the debug info above.")
    
except User.DoesNotExist:
    print(f"❌ User not found: {TARGET_EMAIL}")
    print("   Please ensure the user exists in the database")
except Exception as e:
    print(f"❌ Error: {str(e)}")
    import traceback
    traceback.print_exc()
