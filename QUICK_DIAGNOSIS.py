#!/usr/bin/env python
"""
Quick diagnostic to check production profile setup.
Paste this into Railway Shell to check everything.
"""

import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.rbac.models import Organization, UserProfile
from django.contrib.auth import get_user_model

User = get_user_model()

print("\n" + "=" * 60)
print("🔍 PRODUCTION PROFILE DIAGNOSIS")
print("=" * 60)

# Check migration
print("\n1️⃣ Checking Migration 0046...")
import subprocess
result = subprocess.run(
    ['python', 'manage.py', 'showmigrations', 'rbac'],
    capture_output=True,
    text=True
)
migration_lines = [line for line in result.stdout.split('\n') if '0046' in line]
if migration_lines:
    print(f"   {migration_lines[0]}")
else:
    print("   ⚠️  Migration 0046 not found in output")

# Check organizations
print("\n2️⃣ Checking Organizations...")
orgs = Organization.objects.filter(is_active=True)
org_count = orgs.count()
print(f"   Active Organizations: {org_count}")
if org_count > 0:
    for org in orgs:
        print(f"   ✅ {org.name} (ID: {org.id}, Code: {org.code})")
else:
    print("   ❌ NO ACTIVE ORGANIZATIONS FOUND!")
    all_orgs = Organization.objects.all()
    if all_orgs.exists():
        print(f"   ⚠️  Found {all_orgs.count()} inactive organizations")

# Check users and profiles
print("\n3️⃣ Checking Users and Profiles...")
user_count = User.objects.count()
profile_count = UserProfile.objects.count()
print(f"   Total Users: {user_count}")
print(f"   Total Profiles: {profile_count}")

users_without_profiles = User.objects.filter(rbac_profile__isnull=True)
missing = users_without_profiles.count()
print(f"   Missing Profiles: {missing}")

if missing > 0:
    print("\n   Users without profiles:")
    for user in users_without_profiles:
        print(f"   ❌ {user.email} (ID: {user.id})")

# Test profile creation
print("\n4️⃣ Testing Profile Auto-Creation...")
if users_without_profiles.exists():
    test_user = users_without_profiles.first()
    print(f"   Testing with user: {test_user.email}")
    
    from apps.rbac.profile_utils import get_or_create_profile
    try:
        profile = get_or_create_profile(test_user, source='DiagnosticScript')
        print(f"   ✅ SUCCESS: Profile {profile.id} created for {test_user.email}")
    except Exception as e:
        print(f"   ❌ FAILED: {e}")
else:
    print("   ℹ️  All users already have profiles")

print("\n" + "=" * 60)
print("✅ DIAGNOSIS COMPLETE")
print("=" * 60)
print("\nNext: Share this output to diagnose the issue")
print("=" * 60 + "\n")
