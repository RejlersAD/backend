#!/usr/bin/env python
"""
Create RBAC profiles for existing users
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.rbac.models import UserProfile, Organization, Role, UserRole
from django.contrib.auth import get_user_model

User = get_user_model()

def create_profiles():
    # Create or get organization
    org, org_created = Organization.objects.get_or_create(
        name='Rejlers',
        code='REJ',
        defaults={
            'description': 'Rejlers Engineering',
            'is_active': True
        }
    )
    print(f"Organization: {org.name} ({'created' if org_created else 'exists'})")
    
    # Create or get Super Admin role
    role, role_created = Role.objects.get_or_create(
        name='Super Admin',
        defaults={
            'description': 'Full system access',
            'level': 100,
            'is_system_role': True
        }
    )
    print(f"Role: {role.name} ({'created' if role_created else 'exists'})")
    
    # Get all users
    users = User.objects.all()
    print(f"\nProcessing {users.count()} users...")
    
    created_count = 0
    for user in users:
        # Create user profile
        profile, profile_created = UserProfile.objects.get_or_create(
            user=user,
            defaults={
                'organization': org,
                'status': 'active',
                'job_title': 'Administrator',
                'department': 'Engineering'
            }
        )
        
        if profile_created:
            created_count += 1
            print(f"  ✓ Created profile for {user.email}")
        else:
            print(f"  - Profile exists for {user.email}")
        
        # Assign Super Admin role
        user_role, role_assigned = UserRole.objects.get_or_create(
            user_profile=profile,
            role=role,
            defaults={
                'assigned_by': user
            }
        )
        
        if role_assigned:
            print(f"    → Assigned Super Admin role")
    
    print(f"\n✅ Summary:")
    print(f"   Total profiles: {UserProfile.objects.count()}")
    print(f"   New profiles created: {created_count}")
    print(f"   Organization: {org.name}")
    print(f"   Default Role: {role.name}")

if __name__ == '__main__':
    create_profiles()
