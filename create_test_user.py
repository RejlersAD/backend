#!/usr/bin/env python
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.rbac.models import Organization, UserProfile, Role
from django.contrib.auth import get_user_model

User = get_user_model()

# Create test user
user, created = User.objects.get_or_create(
    email='test@test.com',
    defaults={
        'is_active': True,
        'is_superuser': True,
        'is_staff': True
    }
)
user.set_password('test123')
user.save()
print(f'User: {user.email}')

# Get organization
org = Organization.objects.first()
if not org:
    print("No organization found! Run seed_rbac first.")
    exit(1)

# Create user profile
profile, created = UserProfile.objects.get_or_create(
    user=user,
    defaults={
        'organization': org,
        'job_title': 'Admin',
        'department': 'IT',
        'status': 'active'
    }
)
print(f'UserProfile: {profile}')

# Assign super admin role
super_admin = Role.objects.filter(code='super_admin').first()
if super_admin:
    profile.roles.add(super_admin)
    print(f'✅ Super admin role assigned to {user.email}')
else:
    print('❌ Super admin role not found!')
