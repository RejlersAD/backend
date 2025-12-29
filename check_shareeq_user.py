#!/usr/bin/env python
import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.users.models import User
from apps.rbac.models import UserProfile, Module, Role, UserRole

def main():
    print("=" * 60)
    print("Checking Shareeq User")
    print("=" * 60)
    
    # Get or create user
    user, created = User.objects.get_or_create(
        email='shareeq@rejlers.ae',
        defaults={
            'username': 'shareeq',
            'first_name': 'Shareeq',
            'last_name': 'Khan',
            'is_active': True,
        }
    )
    
    if created:
        print(f"\n✅ Created user: {user.email}")
    else:
        print(f"\n✅ Found user: {user.email}")
    
    # Set password
    user.set_password('shareeq123')
    user.save()
    print(f"✅ Password reset to: shareeq123")
    
    print(f"\nUser Details:")
    print(f"  Username: {user.username}")
    print(f"  Email: {user.email}")
    print(f"  Name: {user.first_name} {user.last_name}")
    print(f"  Active: {user.is_active}")
    print(f"  Staff: {user.is_staff}")
    print(f"  Superuser: {user.is_superuser}")
    
    # Get or create profile
    profile, prof_created = UserProfile.objects.get_or_create(
        user=user,
        defaults={'status': 'active'}
    )
    
    if prof_created:
        print(f"\n✅ Created UserProfile")
    
    # Get Super Admin role and assign it (gives access to all modules)
    super_admin_role = Role.objects.filter(code='SUPER_ADMIN').first()
    if super_admin_role:
        user_role, role_created = UserRole.objects.get_or_create(
            user_profile=profile,
            role=super_admin_role,
            defaults={'assigned_by': user}
        )
        if role_created:
            print(f"\n✅ Assigned Super Admin role (all modules)")
        else:
            print(f"\n✅ Already has Super Admin role")
    else:
        # Fallback to Admin role
        admin_role = Role.objects.filter(code='ADMIN').first()
        if admin_role:
            user_role, role_created = UserRole.objects.get_or_create(
                user_profile=profile,
                role=admin_role,
                defaults={'assigned_by': user}
            )
            if role_created:
                print(f"\n✅ Assigned Admin role")
            else:
                print(f"\n✅ Already has Admin role")
    
    # Check modules
    modules = profile.get_all_modules()
    print(f"\n📦 User Modules ({modules.count()}):")
    for module in modules:
        print(f"  ✓ {module.code}: {module.name}")
    
    print("\n" + "=" * 60)
    print("✅ User check completed!")
    print(f"   Login: {user.email} / shareeq123")
    print("=" * 60)

if __name__ == '__main__':
    main()
