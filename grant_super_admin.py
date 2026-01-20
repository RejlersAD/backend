#!/usr/bin/env python
"""
Ensure key users have super_admin access to see all 276 users
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.rbac.models import UserProfile, Role
from django.contrib.auth import get_user_model

User = get_user_model()

print('\n' + '='*70)
print('GRANTING SUPER ADMIN ACCESS')
print('='*70)

# Get super_admin role
super_admin_role = Role.objects.get(code='super_admin')

# List of emails that should have super_admin access
super_admin_emails = [
    'admin@rejlers.com',
    'info@rejlers.com',
    'tanzeem.agra@rejlers.ae',
    'darshna.chetwani@rejlers.ae',
    'shareeq@rejlers.ae',
    'jarmo.suominen@rejlers.ae',
    'moghawanmeh@rejlers.ae',
]

for email in super_admin_emails:
    try:
        user = User.objects.get(email=email)
        profile = UserProfile.objects.get(user=user, is_deleted=False)
        
        # Check if already has super_admin
        if profile.roles.filter(code='super_admin', is_active=True).exists():
            print(f'✅ {email:40} - Already super admin')
        else:
            # Add super_admin role
            profile.roles.add(super_admin_role)
            print(f'✨ {email:40} - Super admin granted!')
            
    except (User.DoesNotExist, UserProfile.DoesNotExist) as e:
        print(f'⚠️  {email:40} - User not found')

print('\n' + '='*70)
print('VERIFICATION - All Super Admins')
print('='*70)

super_admins = UserProfile.objects.filter(
    roles__code='super_admin',
    roles__is_active=True,
    is_deleted=False
).distinct().select_related('user', 'organization')

for sa in super_admins:
    org_name = sa.organization.name if sa.organization else 'None'
    print(f'{sa.user.email:40} | Org: {org_name}')

print(f'\n✅ Total users with super_admin access: {super_admins.count()}')
print('\n💡 These users can now see ALL 276 users in the system')
print('='*70)
