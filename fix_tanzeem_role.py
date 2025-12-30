"""
Check and fix tanzeem user role
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Role, UserRole

User = get_user_model()

print('\n' + '='*60)
print('CHECKING TANZEEM USER')
print('='*60)

user = User.objects.get(email='tanzeem.agra@rejlers.ae')
profile = UserProfile.objects.get(user=user)

print(f'\nUser: {user.email}')
print(f'Profile: {profile}')
print(f'Organization: {profile.organization}')

user_roles = UserRole.objects.filter(user_profile=profile)
print(f'\nTotal roles assigned: {user_roles.count()}')

for ur in user_roles:
    print(f'  - {ur.role.name} (Code: {ur.role.code}, Primary: {ur.is_primary})')

# Fix: Set one as primary if none exists
primary_role = UserRole.objects.filter(user_profile=profile, is_primary=True).first()

if not primary_role and user_roles.exists():
    # Get super admin role
    super_admin_role = Role.objects.get(code='super_admin')
    
    # Check if user has super admin role
    super_admin_user_role = user_roles.filter(role=super_admin_role).first()
    
    if super_admin_user_role:
        super_admin_user_role.is_primary = True
        super_admin_user_role.save()
        print(f'\n✓ Set Super Administrator as primary role')
    else:
        # Create super admin role
        UserRole.objects.create(
            user_profile=profile,
            role=super_admin_role,
            is_primary=True
        )
        print(f'\n✓ Created and assigned Super Administrator as primary role')
elif primary_role:
    print(f'\n✓ Primary role already exists: {primary_role.role.name}')
else:
    print(f'\n✗ No roles assigned!')

print('='*60 + '\n')
