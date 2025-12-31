"""Fix deleted profiles - set is_deleted=False"""
import os
import sys
import django

sys.path.insert(0, '/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.users.models import User
from apps.rbac.models import UserProfile

print("=" * 80)
print("FIXING DELETED USER PROFILES")
print("=" * 80)

# Find all profiles marked as deleted but user is active
deleted_profiles = UserProfile.objects.filter(is_deleted=True, user__is_active=True)

print(f"\nFound {deleted_profiles.count()} profiles marked as deleted but user is active:\n")

for profile in deleted_profiles:
    print(f"Fixing: {profile.user.email}")
    print(f"  Current is_deleted: {profile.is_deleted}")
    
    profile.is_deleted = False
    profile.save()
    
    print(f"  Updated is_deleted: {profile.is_deleted}")
    print(f"  ✅ Fixed!")
    print("-" * 80)

print("\n" + "=" * 80)
print("ALL FIXED! Users should now be visible in the dashboard.")
print("=" * 80)
