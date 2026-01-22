#!/usr/bin/env python
"""Check user QHSE module assignment"""
import sys
import os
import django

sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import Module, Role, RoleModule, UserProfile, UserRole

User = get_user_model()

print("\n" + "="*70)
print("🔍 CHECKING XERXEZ.IN@GMAIL.COM MODULE ACCESS")
print("="*70 + "\n")

try:
    user = User.objects.get(email='xerxez.in@gmail.com')
    profile = user.rbac_profile
    
    print(f"✅ User found: {user.email}")
    print(f"   Profile ID: {profile.id}")
    print(f"   Organization: {profile.organization.name}")
    
    # Get user's roles
    user_roles = UserRole.objects.filter(user_profile=profile)
    print(f"\n📋 Assigned Roles ({user_roles.count()}):")
    for ur in user_roles:
        print(f"   • {ur.role.name} (code: {ur.role.code}, level: {ur.role.level})")
        print(f"     Primary: {ur.is_primary}")
        
        # Check modules linked to this role
        role_modules = RoleModule.objects.filter(role=ur.role).select_related('module')
        if role_modules.exists():
            print(f"     Linked modules:")
            for rm in role_modules:
                print(f"       - {rm.module.code}: {rm.module.name}")
        else:
            print(f"     ⚠️  NO MODULES LINKED TO THIS ROLE")
    
    # Check what modules are available
    print(f"\n📦 All Available Modules:")
    all_modules = Module.objects.filter(is_active=True).order_by('order')
    for mod in all_modules:
        print(f"   • {mod.code}: {mod.name}")
    
    # Check if QHSE module exists
    qhse_module = Module.objects.filter(code='qhse').first()
    if qhse_module:
        print(f"\n✅ QHSE Module exists:")
        print(f"   ID: {qhse_module.id}")
        print(f"   Name: {qhse_module.name}")
        print(f"   Active: {qhse_module.is_active}")
        
        # Check if user has access
        has_access = profile.has_module_access('qhse')
        print(f"   User has access: {has_access}")
    else:
        print(f"\n❌ QHSE Module NOT FOUND in database!")
    
    print(f"\n🔍 User's accessible modules via get_all_modules():")
    accessible = profile.get_all_modules()
    if accessible.exists():
        for mod in accessible:
            print(f"   • {mod.code}: {mod.name}")
    else:
        print(f"   ⚠️  NO ACCESSIBLE MODULES")
    
except User.DoesNotExist:
    print(f"❌ User 'xerxez.in@gmail.com' not found")
except Exception as e:
    print(f"❌ Error: {str(e)}")
    import traceback
    traceback.print_exc()

print("\n" + "="*70 + "\n")
