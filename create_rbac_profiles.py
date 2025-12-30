#!/usr/bin/env python
"""Create RBAC profiles for all users in the database"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.users.models import User
from apps.rbac.models import UserProfile, Role, Organization

# Get default organization
default_org = Organization.objects.first()
if not default_org:
    print("❌ Error: No organization found")
    exit(1)

print(f"✅ Using organization: {default_org.name}")

# Get existing roles
try:
    super_admin_role = Role.objects.get(code='super_admin')
    admin_role = Role.objects.get(code='admin')
    engineer_role = Role.objects.get(code='engineer')
    print(f"✅ Roles loaded: Super Administrator, Administrator, Engineer")
except Role.DoesNotExist as e:
    print(f"❌ Error: Required roles not found: {e}")
    exit(1)

# Create profiles for all users
users = User.objects.all()
created_count = 0
updated_count = 0

for user in users:
    profile, created = UserProfile.objects.get_or_create(
        user=user,
        defaults={
            'organization': default_org,
            'department': 'Engineering',
            'status': 'active' if user.is_active else 'inactive'
        }
    )
    
    if created:
        # Assign role based on user permissions
        if user.is_superuser:
            profile.roles.add(super_admin_role)
        elif user.is_staff:
            profile.roles.add(admin_role)
        else:
            profile.roles.add(engineer_role)
        
        created_count += 1
        role_names = ', '.join([r.name for r in profile.roles.all()])
        print(f"✅ Created profile for: {user.email} (Roles: {role_names})")
    else:
        # Ensure existing profile has at least one role
        if profile.roles.count() == 0:
            if user.is_superuser:
                profile.roles.add(super_admin_role)
            elif user.is_staff:
                profile.roles.add(admin_role)
            else:
                profile.roles.add(engineer_role)
            updated_count += 1
            role_names = ', '.join([r.name for r in profile.roles.all()])
            print(f"🔄 Updated profile for: {user.email} (Roles: {role_names})")
        else:
            role_names = ', '.join([r.name for r in profile.roles.all()])
            print(f"ℹ️  Profile exists for: {user.email} (Roles: {role_names})")

print(f"\n📊 Summary:")
print(f"   Total users: {users.count()}")
print(f"   Profiles created: {created_count}")
print(f"   Profiles updated: {updated_count}")
print(f"   Total profiles: {UserProfile.objects.count()}")
