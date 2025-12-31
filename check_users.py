"""Check users in database"""
import os
import sys
import django

sys.path.insert(0, '/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.users.models import User
from apps.rbac.models import UserProfile

print("=" * 80)
print("DATABASE USERS CHECK")
print("=" * 80)

users = User.objects.all()
print(f"\nTotal Users in Database: {users.count()}\n")

for user in users:
    try:
        profile = UserProfile.objects.get(user=user)
        profile_exists = True
        org_name = profile.organization.name if profile.organization else "No Org"
    except UserProfile.DoesNotExist:
        profile_exists = False
        org_name = "No Profile"
    
    print(f"ID: {user.id}")
    print(f"  Email: {user.email}")
    print(f"  Username: {user.username}")
    print(f"  Name: {user.first_name} {user.last_name}")
    print(f"  Active: {user.is_active}")
    print(f"  Staff: {user.is_staff}")
    print(f"  Superuser: {user.is_superuser}")
    print(f"  Has Profile: {profile_exists}")
    print(f"  Organization: {org_name}")
    print("-" * 80)

print("\n" + "=" * 80)
print("USER PROFILES CHECK")
print("=" * 80)

profiles = UserProfile.objects.all()
print(f"\nTotal Profiles: {profiles.count()}\n")

for profile in profiles:
    print(f"Profile ID: {profile.id}")
    print(f"  User Email: {profile.user.email}")
    print(f"  Status: {profile.status}")
    print(f"  Organization: {profile.organization.name if profile.organization else 'None'}")
    print(f"  Roles: {profile.roles.count()}")
    print("-" * 80)
