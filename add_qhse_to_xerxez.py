#!/usr/bin/env python
"""Add QHSE module access to xerxez.in@gmail.com"""
import sys
import os
import django

sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import Module, Role, RoleModule, UserRole, Permission, RolePermission

User = get_user_model()

print("\n" + "="*70)
print("🔧 ADDING QHSE MODULE ACCESS TO XERXEZ.IN@GMAIL.COM")
print("="*70 + "\n")

try:
    user = User.objects.get(email='xerxez.in@gmail.com')
    profile = user.rbac_profile
    
    # Get user's custom role
    user_role = UserRole.objects.filter(user_profile=profile, is_primary=True).first()
    if not user_role:
        user_role = UserRole.objects.filter(user_profile=profile).first()
    
    if not user_role:
        print("❌ No role found for user!")
        sys.exit(1)
    
    role = user_role.role
    print(f"✅ Found user role: {role.name}")
    
    # Get QHSE module
    qhse_module = Module.objects.get(code='qhse', is_active=True)
    print(f"✅ Found QHSE module: {qhse_module.name}")
    
    # Link QHSE module to the role
    role_module, created = RoleModule.objects.get_or_create(
        role=role,
        module=qhse_module,
        defaults={'granted_by': None}
    )
    
    if created:
        print(f"✅ Linked QHSE module to role '{role.name}'")
    else:
        print(f"ℹ️  QHSE module already linked to role '{role.name}'")
    
    # Get all permissions for QHSE module
    qhse_permissions = Permission.objects.filter(module=qhse_module, is_active=True)
    print(f"\n🔐 Assigning {qhse_permissions.count()} QHSE permissions to role...")
    
    permissions_added = 0
    for permission in qhse_permissions:
        role_perm, created = RolePermission.objects.get_or_create(
            role=role,
            permission=permission,
            defaults={'granted_by': None}
        )
        if created:
            permissions_added += 1
            print(f"   ✅ Added: {permission.code}")
    
    print(f"\n📊 Summary:")
    print(f"   • Permissions added: {permissions_added}")
    
    # Verify access
    has_access = profile.has_module_access('qhse')
    all_modules = profile.get_all_modules()
    module_codes = [m.code for m in all_modules]
    
    print(f"\n🔍 Verification:")
    print(f"   • User has QHSE access: {has_access}")
    print(f"   • All accessible modules: {', '.join(module_codes)}")
    
    if has_access:
        print(f"\n{'='*70}")
        print(f"✅ SUCCESS! User now has access to QHSE module")
        print(f"{'='*70}\n")
    else:
        print(f"\n{'='*70}")
        print(f"❌ ERROR! User still doesn't have QHSE access")
        print(f"{'='*70}\n")
        
except User.DoesNotExist:
    print(f"❌ User 'xerxez.in@gmail.com' not found")
except Module.DoesNotExist:
    print(f"❌ QHSE module not found in database!")
except Exception as e:
    print(f"❌ Error: {str(e)}")
    import traceback
    traceback.print_exc()
