#!/usr/bin/env python
"""
Script to grant procurement module access to users
Run this with: docker exec radai_backend_local python grant_procurement_access.py
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.rbac.models import Module, UserProfile
from apps.users.models import User

# Get procurement module
procurement_module = Module.objects.get(code='procurement')
print(f"📦 Procurement Module ID: {procurement_module.id}")

# Get all superusers and staff
admin_users = User.objects.filter(is_staff=True) | User.objects.filter(is_superuser=True)
print(f"\n👥 Found {admin_users.count()} admin users")

# Grant access to all admin users
for user in admin_users:
    # Get or create UserProfile
    profile, profile_created = UserProfile.objects.get_or_create(
        user=user,
        defaults={
            'preferred_language': 'en',
            'timezone': 'UTC'
        }
    )
    
    if profile_created:
        print(f"   📝 Created UserProfile for {user.email}")
    
    # Add procurement module
    if procurement_module not in profile.modules.all():
        profile.modules.add(procurement_module)
        print(f"✅ Granted procurement access to: {user.email}")
    else:
        print(f"ℹ️  {user.email} already has procurement access")

print(f"\n✨ Procurement module setup complete!")
