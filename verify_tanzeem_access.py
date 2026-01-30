#!/usr/bin/env python
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Role, UserRole, Module, RoleModule

User = get_user_model()

print('\n' + '='*70)
print('VERIFYING tanzeem.agra@rejlers.ae DATABASE ACCESS')
print('='*70 + '\n')

# Find user
user = User.objects.filter(email='tanzeem.agra@rejlers.ae').first()

if not user:
    print('❌ User not found!')
    exit(1)

print('✅ Django User Found:')
print(f'   ID: {user.id}')
print(f'   Username: {user.username}')
print(f'   Email: {user.email}')
print(f'   First Name: {user.first_name}')
print(f'   Last Name: {user.last_name}')
print(f'   is_staff: {user.is_staff}')
print(f'   is_superuser: {user.is_superuser}')
print(f'   is_active: {user.is_active}')

# Check UserProfile
profile = UserProfile.objects.filter(user=user).first()

if not profile:
    print('\n❌ UserProfile not found!')
    exit(1)

print(f'\n✅ UserProfile Found:')
print(f'   ID: {profile.id}')
print(f'   Status: {profile.status}')
print(f'   Is Deleted: {profile.is_deleted}')
print(f'   Department: {profile.department if profile.department else "None"}')

# Check assigned roles
user_roles = UserRole.objects.filter(user_profile=profile).select_related('role')

print(f'\n📋 Assigned Roles: {user_roles.count()}')
for ur in user_roles:
    print(f'   ✓ {ur.role.name} (code: {ur.role.code})')

if not user_roles.exists():
    print('   ❌ No roles assigned!')
    exit(1)

# Check modules for each role
for ur in user_roles:
    role = ur.role
    role_modules = RoleModule.objects.filter(role=role).select_related('module')
    print(f'\n📦 Modules for "{role.name}": {role_modules.count()}')
    for rm in role_modules:
        print(f'   ✓ {rm.module.name} (code: {rm.module.code})')

# Check if Super Administrator role exists and has all modules
print('\n' + '='*70)
print('SUPER ADMINISTRATOR ROLE CHECK')
print('='*70)

super_admin_role = Role.objects.filter(code='super_admin').first()
if super_admin_role:
    print(f'\n✅ Super Administrator Role Exists (ID: {super_admin_role.id})')
    
    # Check if user has this role
    has_super_admin = UserRole.objects.filter(
        user_profile=profile,
        role=super_admin_role
    ).exists()
    
    if has_super_admin:
        print('   ✅ User HAS Super Administrator role')
    else:
        print('   ❌ User DOES NOT have Super Administrator role')
    
    # Check modules assigned to Super Admin role
    super_admin_modules = RoleModule.objects.filter(role=super_admin_role).count()
    total_modules = Module.objects.count()
    
    print(f'\n📦 Super Admin Modules: {super_admin_modules}/{total_modules}')
    
    if super_admin_modules < total_modules:
        print(f'   ⚠️  Missing {total_modules - super_admin_modules} modules!')
else:
    print('\n❌ Super Administrator role not found in database!')

print('\n' + '='*70)
print('VERIFICATION COMPLETE')
print('='*70 + '\n')
