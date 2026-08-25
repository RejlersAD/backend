#!/usr/bin/env python
"""Quick script to check user roles and modules"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserRole, RoleModule, UserProfile

User = get_user_model()
user = User.objects.get(email='tanzeem.agra@rejlers.ae')

print(f"✅ User: {user.email}")
print(f"   Is superuser: {user.is_superuser}")
print(f"   Is staff: {user.is_staff}")
print(f"   Is active: {user.is_active}")
print()

# Get user profile
try:
    profile = UserProfile.objects.get(user=user)
    print(f"👤 User Profile ID: {profile.id}")
    
    roles = UserRole.objects.filter(user_profile=profile).select_related('role')
    print(f"📋 User roles ({roles.count()}):")
    for ur in roles:
        print(f"   - {ur.role.name} (code: {ur.role.code})")
        
        # Get modules for this role
        modules = RoleModule.objects.filter(role=ur.role).select_related('module')
        module_codes = [rm.module.code for rm in modules]
        print(f"     Modules ({modules.count()}): {module_codes[:10]}")
        if len(module_codes) > 10:
            print(f"     ... and {len(module_codes) - 10} more")
    print()
except UserProfile.DoesNotExist:
    print("❌ No UserProfile found for this user!")
    print()

# Check all available modules
from apps.rbac.models import Module
all_modules = Module.objects.filter(is_active=True)
print(f"📦 Total active modules in system: {all_modules.count()}")
print(f"   First 20: {[m.code for m in all_modules[:20]]}")
