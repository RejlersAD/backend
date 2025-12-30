"""
Check user management system synchronization
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Role

User = get_user_model()

print('\n' + '='*60)
print('USER MANAGEMENT DATABASE SYNC CHECK')
print('='*60)

users_count = User.objects.count()
profiles_count = UserProfile.objects.count()
roles_count = Role.objects.count()

print(f'\nDatabase Counts:')
print(f'  Users: {users_count}')
print(f'  Profiles: {profiles_count}')
print(f'  Roles: {roles_count}')

print(f'\nUser-Profile Mapping:')
print(f'{"Email":<35} {"Profile":<10} {"Roles":<10}')
print('-'*60)

for user in User.objects.all()[:20]:
    profile = UserProfile.objects.filter(user=user).first()
    has_profile = 'Yes' if profile else 'No'
    role_count = profile.roles.count() if profile else 0
    print(f'{user.email:<35} {has_profile:<10} {role_count:<10}')

# Check for users without profiles
users_without_profiles = []
for user in User.objects.all():
    if not UserProfile.objects.filter(user=user).exists():
        users_without_profiles.append(user.email)

if users_without_profiles:
    print(f'\n⚠️  WARNING: {len(users_without_profiles)} users without profiles:')
    for email in users_without_profiles:
        print(f'  - {email}')
else:
    print(f'\n✓ All users have profiles')

print('='*60 + '\n')
