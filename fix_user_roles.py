"""
Fix users without primary roles by assigning appropriate roles
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Role, UserRole

User = get_user_model()

print('\n' + '='*60)
print('FIXING USER ROLES')
print('='*60)

# Get roles
try:
    super_admin_role = Role.objects.get(code='super_admin')
    admin_role = Role.objects.get(code='admin')
    engineer_role = Role.objects.get(code='engineer')
except Role.DoesNotExist as e:
    print(f'ERROR: Required role not found: {e}')
    exit(1)

# Define user role mappings
user_roles = {
    'tanzeem.agra@rejlers.ae': super_admin_role,
    'info@rejlers.com': admin_role,
    'darshna.chetwani@rejlers.ae': admin_role,
}

print('\nAssigning roles to users:')
print('-'*60)

for email, role in user_roles.items():
    try:
        user = User.objects.get(email=email)
        profile = UserProfile.objects.get(user=user)
        
        # Check if user already has a role
        existing_role = UserRole.objects.filter(user_profile=profile).first()
        
        if existing_role:
            print(f'{email}')
            print(f'  Current role: {existing_role.role.name}')
            if existing_role.role != role:
                existing_role.role = role
                existing_role.is_primary = True
                existing_role.save()
                print(f'  ✓ Updated to: {role.name}')
            else:
                print(f'  ✓ Already has correct role')
        else:
            # Create new role assignment
            UserRole.objects.create(
                user_profile=profile,
                role=role,
                is_primary=True
            )
            print(f'{email}')
            print(f'  ✓ Assigned: {role.name}')
            
    except User.DoesNotExist:
        print(f'{email}')
        print(f'  ✗ User not found')
    except UserProfile.DoesNotExist:
        print(f'{email}')
        print(f'  ✗ Profile not found')
    except Exception as e:
        print(f'{email}')
        print(f'  ✗ Error: {e}')

print('\n' + '='*60)
print('VERIFICATION')
print('='*60)

print('\nUsers with roles:')
for email in user_roles.keys():
    try:
        user = User.objects.get(email=email)
        profile = UserProfile.objects.get(user=user)
        user_role = UserRole.objects.filter(user_profile=profile, is_primary=True).first()
        if user_role:
            print(f'  ✓ {email}: {user_role.role.name}')
        else:
            print(f'  ✗ {email}: No primary role!')
    except Exception as e:
        print(f'  ✗ {email}: Error - {e}')

print('='*60 + '\n')
