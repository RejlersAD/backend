"""
Check UserProfile is_deleted status
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile

User = get_user_model()

user = User.objects.get(email='tanzeem.agra@rejlers.ae')
profiles = UserProfile.objects.filter(user=user)

print(f'\nFound {profiles.count()} profile(s) for {user.email}:')
print('=' * 60)

for profile in profiles:
    print(f'\nProfile ID: {profile.id}')
    print(f'Organization: {profile.organization.name if profile.organization else "None"}')
    print(f'Department: {profile.department}')
    print(f'Job Title: {profile.job_title}')
    print(f'Status: {profile.status}')
    print(f'is_deleted: {profile.is_deleted}')
    print(f'Roles: {list(profile.roles.values_list("name", flat=True))}')
    
print('\n' + '=' * 60)
print('Checking what /rbac/users/me/ would return...')
print('=' * 60)

# Simulate the view logic
active_profile = UserProfile.objects.filter(
    user=user,
    is_deleted=False
).first()

if active_profile:
    print(f'✅ Active profile found')
    print(f'   ID: {active_profile.id}')
    print(f'   Department: {active_profile.department}')
else:
    print(f'❌ No active profile (all profiles are deleted or no profiles exist)')
