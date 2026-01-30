"""
Find tanzeem.agra@rejlers.ae user in production database
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile

User = get_user_model()

print('\n' + '=' * 100)
print('SEARCHING FOR: tanzeem.agra@rejlers.ae IN RAILWAY POSTGRESQL')
print('=' * 100)

# Search with case variations
search_emails = [
    'tanzeem.agra@rejlers.ae',
    'Tanzeem.Agra@rejlers.ae',
    'tanzeem@rejlers.ae',
]

for email in search_emails:
    user = User.objects.filter(email__iexact=email).first()
    if user:
        print(f'\n✅ FOUND: {email}')
        print(f'   Actual Email: {user.email}')
        print(f'   ID: {user.id}')
        print(f'   Username: {user.username}')
        print(f'   First Name: "{user.first_name}"')
        print(f'   Last Name: "{user.last_name}"')
        print(f'   is_staff: {user.is_staff}')
        print(f'   is_superuser: {user.is_superuser}')
        print(f'   is_active: {user.is_active}')
        print(f'   Date Joined: {user.date_joined}')
        print(f'   Last Login: {user.last_login}')
        
        # Check password
        print(f'\n   Testing password: Tanzilla@tanzeem786')
        from django.contrib.auth import authenticate
        auth_user = authenticate(username=user.email, password='Tanzilla@tanzeem786')
        if auth_user:
            print(f'   ✅ Password is CORRECT')
        else:
            print(f'   ❌ Password is INCORRECT')
        
        # Check profile
        profile = UserProfile.objects.filter(user=user, is_deleted=False).first()
        if profile:
            print(f'\n   ✅ USERPROFILE EXISTS:')
            print(f'      Profile ID: {profile.id}')
            roles = list(profile.roles.values_list('name', flat=True))
            print(f'      Roles: {", ".join(roles) if roles else "NO ROLES"}')
            print(f'      Department: "{profile.department}"')
            print(f'      Job Title: "{profile.job_title}"')
            print(f'      Organization: {profile.organization.name if profile.organization else "None"}')
            print(f'      Status: {profile.status}')
            print(f'      is_deleted: {profile.is_deleted}')
            print(f'      Phone: {profile.phone}')
            print(f'      Bio: {profile.bio}')
            print(f'      Profile Photo: {profile.profile_photo}')
        else:
            print(f'\n   ❌ NO USERPROFILE FOUND')
            print(f'   All profiles for this user:')
            all_profiles = UserProfile.objects.filter(user=user)
            if all_profiles.exists():
                for p in all_profiles:
                    print(f'      - Profile ID: {p.id}, is_deleted: {p.is_deleted}')
            else:
                print(f'      No profiles at all!')
        
        break
else:
    print(f'\n❌ USER NOT FOUND with any variation')

print('\n' + '=' * 100)
print('Checking all users with "tanzeem" in email or name:')
print('=' * 100)

users = User.objects.filter(email__icontains='tanzeem') | User.objects.filter(first_name__icontains='tanzeem') | User.objects.filter(last_name__icontains='tanzeem')
if users.exists():
    for u in users:
        print(f'\n  Email: {u.email}')
        print(f'  Username: {u.username}')
        print(f'  Name: {u.first_name} {u.last_name}')
        print(f'  Active: {u.is_active}')
else:
    print('  No users found')

print('\n' + '=' * 100)
