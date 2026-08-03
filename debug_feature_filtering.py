#!/usr/bin/env python
"""Debug why features are being filtered out"""
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.core.feature_registry import get_registry

User = get_user_model()

print("=" * 70)
print("DEBUGGING FEATURE FILTERING")
print("=" * 70)

# Get a user
user = User.objects.filter(is_active=True, is_staff=True).first()
if not user:
    user = User.objects.filter(is_active=True).first()

print(f"\n👤 User: {user.email}")
print(f"   Department: {getattr(user, 'department', None)}")

# Get user permissions
user_permissions = []
if hasattr(user, 'get_all_permissions'):
    user_permissions = list(user.get_all_permissions())

print(f"\n🔐 User Permissions: {len(user_permissions)} total")
if user_permissions:
    for perm in user_permissions[:10]:  # Show first 10
        print(f"   - {perm}")
    if len(user_permissions) > 10:
        print(f"   ... and {len(user_permissions) - 10} more")
else:
    print("   ⚠️  NO PERMISSIONS FOUND!")

# Get registry
registry = get_registry()
all_features = list(registry._features.values())
print(f"\n📊 Total features in registry: {len(all_features)}")

# Get active features
active_features = registry.get_active_features()
print(f"📊 Active features: {len(active_features)}")

# Test get_features_for_user
accessible = registry.get_features_for_user(user_permissions, getattr(user, 'department', None))
print(f"📊 Accessible features for user: {len(accessible)}")

if len(accessible) == 0:
    print("\n❌ NO ACCESSIBLE FEATURES! Debugging why...")
    print("\n🔍 Checking each feature:")
    for feature in active_features[:5]:  # Check first 5
        print(f"\n   Feature: {feature.name}")
        print(f"   Required Permissions: {feature.required_permissions or 'None'}")
        print(f"   Department Access: {feature.department_access or 'None (all departments)'}")
        
        # Check if passes permission check
        if feature.required_permissions:
            has_perms = all(perm in user_permissions for perm in feature.required_permissions)
            print(f"   Permission Check: {'✅ PASS' if has_perms else '❌ FAIL'}")
            if not has_perms:
                missing = [p for p in feature.required_permissions if p not in user_permissions]
                print(f"   Missing: {missing}")
        else:
            print(f"   Permission Check: ✅ PASS (no permissions required)")
        
        # Check department
        if feature.department_access and getattr(user, 'department', None):
            in_dept = getattr(user, 'department', None) in feature.department_access
            print(f"   Department Check: {'✅ PASS' if in_dept else '❌ FAIL'}")
        else:
            print(f"   Department Check: ✅ PASS (no department restriction)")

else:
    print(f"\n✅ User has access to {len(accessible)} features")
    pc_features = [f for f in accessible if f.category.value == 'project_control']
    print(f"   Including {len(pc_features)} PROJECT_CONTROL features")

print("\n" + "=" * 70)
