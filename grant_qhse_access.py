#!/usr/bin/env python
"""
Grant QHSE Access to Specific User
Soft-coded configuration for granting QHSE module access
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.rbac.models import UserProfile, Role, Module, UserRole
from django.contrib.auth import get_user_model

User = get_user_model()

print('\n' + '='*70)
print('GRANTING QHSE ACCESS')
print('='*70 + '\n')

# Soft-coded configuration
QHSE_USERS = [
    'shaju.chacko@rejlers.ae',
]

# Get QHSE module and role
try:
    qhse_module = Module.objects.get(code='qhse')
    print(f'✅ QHSE Module found: {qhse_module.name}')
except Module.DoesNotExist:
    print('❌ QHSE Module not found!')
    exit(1)

try:
    # Try QHSE Manager role first
    qhse_role = Role.objects.filter(code='qhse_manager').first()
    if not qhse_role:
        # Fall back to Administrator role (which has QHSE module)
        qhse_role = Role.objects.get(code='admin')
        print(f'✅ Using Administrator role (includes QHSE): {qhse_role.name}\n')
    else:
        print(f'✅ QHSE Role found: {qhse_role.name}\n')
except Role.DoesNotExist:
    print('❌ Neither QHSE Manager nor Administrator role found!')
    exit(1)

# Process each user
for email in QHSE_USERS:
    print(f'Processing: {email}')
    print('-' * 70)
    
    try:
        user = User.objects.get(email=email)
        print(f'  ✅ User found: {user.get_full_name() or user.email}')
        
        # Get user profile
        profile, created = UserProfile.objects.get_or_create(
            user=user,
            defaults={'organization_id': 1}  # Default organization
        )
        
        if created:
            print(f'  ✅ Profile created')
        else:
            print(f'  ✅ Profile exists')
        
        # Grant QHSE role
        user_role, created = UserRole.objects.get_or_create(
            user_profile=profile,
            role=qhse_role
        )
        
        if created:
            print(f'  ✅ QHSE Manager role granted')
        else:
            print(f'  ℹ️  Already has QHSE Manager role')
        
        # Check current roles
        current_roles = [r.name for r in profile.roles.all()]
        print(f'  📋 Current roles: {", ".join(current_roles)}')
        
        # Check module access
        accessible_modules = []
        for role in profile.roles.all():
            for module in role.modules.all():
                if module.code not in [m.code for m in accessible_modules]:
                    accessible_modules.append(module)
        
        has_qhse = any(m.code == 'qhse' for m in accessible_modules)
        if has_qhse:
            print(f'  ✅ Has QHSE module access')
        else:
            print(f'  ⚠️  Does NOT have QHSE module access')
            print(f'  📝 Accessible modules: {[m.name for m in accessible_modules]}')
        
        print(f'  ✅ QHSE access granted successfully!\n')
        
    except User.DoesNotExist:
        print(f'  ❌ User not found: {email}\n')
    except Exception as e:
        print(f'  ❌ Error: {str(e)}\n')

print('='*70)
print('QHSE ACCESS GRANT COMPLETED')
print('='*70 + '\n')

# Verification
print('🔍 VERIFICATION')
print('-' * 70)
for email in QHSE_USERS:
    try:
        user = User.objects.get(email=email)
        profile = UserProfile.objects.get(user=user)
        roles = [r.name for r in profile.roles.all()]
        modules = []
        for role in profile.roles.all():
            for module in role.modules.all():
                if module.code not in [m.code for m in modules]:
                    modules.append(module)
        has_qhse = any(m.code == 'qhse' for m in modules)
        print(f'{email}:')
        print(f'  Roles: {", ".join(roles)}')
        print(f'  QHSE Access: {"✅ YES" if has_qhse else "❌ NO"}')
        print(f'  Modules: {[m.name for m in modules]}\n')
    except:
        print(f'{email}: ❌ Not found\n')

print('✅ All done!')
