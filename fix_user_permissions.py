import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.users.models import User
from apps.rbac.models import UserProfile, Role, UserRole

# Check user
user = User.objects.get(email='tanzeem.agra@rejlers.ae')
print(f'User: {user.email}')
print(f'Is Staff: {user.is_staff}')
print(f'Is Superuser: {user.is_superuser}')

# Check profile
profile = UserProfile.objects.filter(user=user).first()
print(f'\nProfile exists: {profile is not None}')

if profile:
    print(f'Status: {profile.status}')
    print(f'Organization: {profile.organization}')
    
    # Check roles
    user_roles = UserRole.objects.filter(user_profile=profile)
    print(f'\nRoles assigned: {user_roles.count()}')
    for ur in user_roles:
        print(f'  - {ur.role.name} ({ur.role.code})')
else:
    print('\n⚠️  No UserProfile found!')
    
# Check if super_admin role exists
super_admin_role = Role.objects.filter(code='super_admin').first()
print(f'\nSuper Admin role exists: {super_admin_role is not None}')
if super_admin_role:
    print(f'Super Admin role name: {super_admin_role.name}')

# Make user superuser and staff
user.is_staff = True
user.is_superuser = True
user.save()
print(f'\n✅ Updated user to staff and superuser')

# Create profile if missing
if not profile:
    from apps.rbac.models import Organization
    org = Organization.objects.first()
    if not org:
        org = Organization.objects.create(
            name='Rejlers AB',
            code='REJLERS',
            is_active=True
        )
    profile = UserProfile.objects.create(
        user=user,
        organization=org,
        status='active'
    )
    print(f'✅ Created UserProfile')

# Assign super_admin role
if super_admin_role and profile:
    user_role, created = UserRole.objects.get_or_create(
        user_profile=profile,
        role=super_admin_role
    )
    if created:
        print(f'✅ Assigned super_admin role')
    else:
        print(f'ℹ️  User already has super_admin role')

print('\n' + '='*50)
print('User setup complete!')
print('='*50)
