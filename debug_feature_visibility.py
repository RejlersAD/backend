#!/usr/bin/env python
"""Debug why only one PROJECT_CONTROL feature is showing"""
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from apps.core.feature_registry import get_registry
from django.contrib.auth import get_user_model

User = get_user_model()
user = User.objects.filter(is_active=True, is_staff=True).first()
if not user:
    user = User.objects.filter(is_active=True).first()

print('\n' + '='*70)
print('DEBUGGING FEATURE VISIBILITY')
print('='*70)

registry = get_registry()
all_features = registry.get_active_features()
print(f'\n📊 Registry has {len(all_features)} active features')

pc_features = [f for f in all_features if f.category.value == 'project_control']
print(f'\n🎯 PROJECT_CONTROL features in registry: {len(pc_features)}')
for f in sorted(pc_features, key=lambda x: x.order):
    print(f'  Order {f.order}: {f.name}')
    print(f'    Route: {f.frontend_route}')
    print(f'    Required Permissions: {f.required_permissions or "None"}')
    print(f'    Department Access: {f.department_access or "All departments"}')
    print()

# Test get_features_for_user
print(f'👤 Testing for user: {user.email}')
permissions = list(user.get_all_permissions()) if hasattr(user, 'get_all_permissions') else []
print(f'   User has {len(permissions)} permissions')

accessible = registry.get_features_for_user(permissions, None)
print(f'\n✅ Accessible features for user: {len(accessible)}')

pc_accessible = [f for f in accessible if f.category.value == 'project_control']
print(f'\n🎯 Accessible PROJECT_CONTROL features: {len(pc_accessible)}')
for f in sorted(pc_accessible, key=lambda x: x.order):
    print(f'  Order {f.order}: {f.name}')
    print(f'    Route: {f.frontend_route}')

if len(pc_accessible) < len(pc_features):
    print(f'\n⚠️  WARNING: User can only access {len(pc_accessible)} of {len(pc_features)} PROJECT_CONTROL features!')
    missing = set(f.id for f in pc_features) - set(f.id for f in pc_accessible)
    for missing_id in missing:
        f = registry.get(missing_id)
        print(f'\n❌ Missing feature: {f.name}')
        if f.required_permissions:
            has_all = all(p in permissions for p in f.required_permissions)
            print(f'   Required permissions: {f.required_permissions}')
            print(f'   User has all: {has_all}')
            if not has_all:
                missing_perms = [p for p in f.required_permissions if p not in permissions]
                print(f'   Missing: {missing_perms}')

print('\n' + '='*70)
