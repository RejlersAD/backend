"""
Fetch all users from Railway PostgreSQL production database
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile

User = get_user_model()

print('\n' + '=' * 100)
print('FETCHING ALL USERS FROM RAILWAY POSTGRESQL PRODUCTION DATABASE')
print('=' * 100)

# Get all users
all_users = User.objects.all().order_by('email')
total_users = all_users.count()

print(f'\nTotal Users in Database: {total_users}')
print('=' * 100)

# Show summary by domain
from django.db.models import Count
email_domains = User.objects.values('email').annotate(
    domain=Count('id')
)

# Count by domain
domain_counts = {}
for user in all_users:
    if user.email:
        domain = user.email.split('@')[1] if '@' in user.email else 'no-domain'
        domain_counts[domain] = domain_counts.get(domain, 0) + 1

print('\nUsers by Email Domain:')
print('-' * 100)
for domain, count in sorted(domain_counts.items(), key=lambda x: x[1], reverse=True):
    print(f'  {domain}: {count} users')

print('\n' + '=' * 100)
print('DETAILED USER LIST')
print('=' * 100)

for idx, user in enumerate(all_users, 1):
    print(f'\n[{idx}] Email: {user.email}')
    print(f'    Username: {user.username}')
    print(f'    Name: {user.first_name} {user.last_name}')
    print(f'    Staff: {user.is_staff} | Superuser: {user.is_superuser} | Active: {user.is_active}')
    print(f'    Joined: {user.date_joined}')
    print(f'    Last Login: {user.last_login if user.last_login else "Never"}')
    
    # Check for UserProfile
    profile = UserProfile.objects.filter(user=user, is_deleted=False).first()
    if profile:
        roles = list(profile.roles.values_list('name', flat=True))
        print(f'    Profile: ✅ EXISTS')
        print(f'      - Roles: {", ".join(roles) if roles else "No roles"}')
        print(f'      - Department: {profile.department}')
        print(f'      - Job Title: {profile.job_title}')
        print(f'      - Organization: {profile.organization.name if profile.organization else "None"}')
        print(f'      - Status: {profile.status}')
        print(f'      - Profile Photo: {"Yes" if profile.profile_photo else "No"}')
    else:
        print(f'    Profile: ❌ NO PROFILE')

print('\n' + '=' * 100)
print('SEARCH FOR SPECIFIC USER: tanzeem.agra@rejlers.ae')
print('=' * 100)

tanzeem = User.objects.filter(email='tanzeem.agra@rejlers.ae').first()
if tanzeem:
    print(f'\n✅ FOUND USER: {tanzeem.email}')
    print(f'   ID: {tanzeem.id}')
    print(f'   Username: {tanzeem.username}')
    print(f'   Name: {tanzeem.first_name} {tanzeem.last_name}')
    print(f'   Staff: {tanzeem.is_staff}')
    print(f'   Superuser: {tanzeem.is_superuser}')
    print(f'   Active: {tanzeem.is_active}')
    print(f'   Date Joined: {tanzeem.date_joined}')
    print(f'   Last Login: {tanzeem.last_login}')
    
    profile = UserProfile.objects.filter(user=tanzeem, is_deleted=False).first()
    if profile:
        print(f'\n   ✅ PROFILE EXISTS:')
        print(f'      Profile ID: {profile.id}')
        roles = list(profile.roles.values_list('name', flat=True))
        print(f'      Roles: {", ".join(roles)}')
        print(f'      Department: {profile.department}')
        print(f'      Job Title: {profile.job_title}')
        print(f'      Organization: {profile.organization.name if profile.organization else "None"}')
        print(f'      Status: {profile.status}')
        print(f'      is_deleted: {profile.is_deleted}')
        print(f'      Phone: {profile.phone}')
        print(f'      Bio: {profile.bio}')
        print(f'      Profile Photo: {profile.profile_photo.url if profile.profile_photo else "No photo"}')
    else:
        print(f'\n   ❌ NO PROFILE FOUND')
else:
    print(f'\n❌ USER NOT FOUND: tanzeem.agra@rejlers.ae')

print('\n' + '=' * 100)
print('END OF REPORT')
print('=' * 100)
