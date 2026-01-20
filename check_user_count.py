#!/usr/bin/env python
"""
Check actual user count in PostgreSQL database
"""
import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.rbac.models import UserProfile
from django.contrib.auth import get_user_model

User = get_user_model()

print("=" * 70)
print("CHECKING USERS IN POSTGRESQL DATABASE")
print("=" * 70)

# Count Django Users
total_django_users = User.objects.count()
print(f"\n✓ Total Django Users: {total_django_users}")

# Count UserProfiles
total_profiles = UserProfile.objects.count()
active_profiles = UserProfile.objects.filter(is_deleted=False).count()
deleted_profiles = UserProfile.objects.filter(is_deleted=True).count()

print(f"✓ Total UserProfiles: {total_profiles}")
print(f"  - Active: {active_profiles}")
print(f"  - Deleted: {deleted_profiles}")

# List all active users
print("\n" + "=" * 70)
print("ACTIVE USER DETAILS:")
print("=" * 70)

profiles = UserProfile.objects.filter(is_deleted=False).select_related('user', 'organization').order_by('id')

if profiles.exists():
    for idx, profile in enumerate(profiles, 1):
        org_name = profile.organization.name if profile.organization else "No Organization"
        print(f"\n{idx}. User ID: {profile.id}")
        print(f"   Email: {profile.user.email}")
        print(f"   Name: {profile.user.first_name} {profile.user.last_name}")
        print(f"   Status: {profile.status}")
        print(f"   Organization: {org_name}")
        print(f"   Department: {profile.department or 'N/A'}")
        print(f"   Job Title: {profile.job_title or 'N/A'}")
else:
    print("\n⚠ No active users found in database!")

print("\n" + "=" * 70)
print(f"SUMMARY: Found {active_profiles} active users in PostgreSQL")
print("=" * 70)
