# Provision Production Profiles - Quick Script

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Organization
from django.db import transaction

User = get_user_model()

print("=" * 70)
print("Creating Profiles for Production Users")
print("=" * 70)

# Get or create organization
org, org_created = Organization.objects.get_or_create(
    code='DEFAULT_ORG',
    defaults={
        'name': 'Default Organization',
        'is_active': True,
    }
)

if org_created:
    print(f'\n✅ Created organization: {org.name}')
else:
    print(f'\n✅ Using organization: {org.name} (ID: {org.id})')

# Find users without profiles
users_without_profile = User.objects.filter(rbac_profile__isnull=True)
count = users_without_profile.count()

print(f'\nFound {count} users without profiles\n')

# Create profiles
for user in users_without_profile:
    with transaction.atomic():
        profile, created = UserProfile.objects.get_or_create(
            user=user,
            defaults={
                'organization': org,
                'bio': '',
                'job_title': user.username or user.email.split('@')[0] if user.email else 'User',
            }
        )
    
    if created:
        print(f'✅ Created profile for: {user.email}')

print(f'\n✅ Done! All users now have profiles')
