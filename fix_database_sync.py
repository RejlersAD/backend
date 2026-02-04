"""
Cleanup Script: Fix Database Synchronization Issues
1. Create RBAC profile for users without one
2. Clean up RBAC profiles for soft-deleted users
3. Ensure proper role assignments
"""

import os
import sys
import django
from datetime import datetime

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.db import transaction
from apps.rbac.models import UserProfile, Role, Organization

User = get_user_model()

def print_section(title):
    print(f"\n{'='*90}")
    print(f"  {title}")
    print(f"{'='*90}\n")

def create_missing_rbac_profiles():
    """Create RBAC profiles for users without them"""
    print_section("1️⃣ Creating Missing RBAC Profiles")
    
    users_without_profile = User.objects.filter(rbac_profile__isnull=True, is_active=True)
    count = users_without_profile.count()
    
    if count == 0:
        print("✅ All active users have RBAC profiles")
        return 0
    
    print(f"Found {count} active user(s) without RBAC profile:\n")
    
    # Get default organization or use the first one available
    try:
        default_org = Organization.objects.get(code='DEFAULT')
    except Organization.DoesNotExist:
        try:
            # Try to get organization by name
            default_org = Organization.objects.get(name='Default Organization')
        except Organization.DoesNotExist:
            # Get the first organization or create one
            default_org = Organization.objects.first()
            if not default_org:
                default_org = Organization.objects.create(
                    code='DEFAULT_ORG',
                    name='Default Organization New',
                    description='Default organization for users'
                )
    
    created_count = 0
    for user in users_without_profile:
        try:
            with transaction.atomic():
                profile = UserProfile.objects.create(
                    user=user,
                    organization=default_org,
                    status='active'
                )
                print(f"   ✅ Created RBAC profile for: {user.email}")
                created_count += 1
        except Exception as e:
            print(f"   ❌ Failed to create profile for {user.email}: {str(e)}")
    
    print(f"\n✅ Created {created_count} RBAC profile(s)")
    return created_count

def cleanup_deleted_user_profiles():
    """Clean up RBAC profiles for soft-deleted users"""
    print_section("2️⃣ Cleaning Up Soft-Deleted User Profiles")
    
    # Find profiles for users with .deleted_ emails
    deleted_profiles = UserProfile.objects.filter(user__email__contains='.deleted_')
    count = deleted_profiles.count()
    
    if count == 0:
        print("✅ No profiles found for soft-deleted users")
        return 0
    
    print(f"Found {count} profile(s) for soft-deleted users")
    print(f"Action: Removing role assignments (keeping profiles for audit)")
    
    cleaned_count = 0
    for profile in deleted_profiles:
        try:
            with transaction.atomic():
                # Remove all role assignments
                role_count = profile.roles.count()
                profile.roles.clear()
                print(f"   ✅ Cleaned {role_count} role(s) from: {profile.user.email}")
                cleaned_count += 1
        except Exception as e:
            print(f"   ❌ Failed to clean {profile.user.email}: {str(e)}")
    
    print(f"\n✅ Cleaned {cleaned_count} profile(s)")
    return cleaned_count

def assign_default_roles():
    """Assign default Engineering & Common role to profiles without roles"""
    print_section("3️⃣ Assigning Default Roles")
    
    # Get active users with profiles but no roles
    profiles_without_roles = UserProfile.objects.filter(
        user__is_active=True,
        roles__isnull=True
    ).exclude(
        user__email__contains='.deleted_'
    )
    
    count = profiles_without_roles.count()
    
    if count == 0:
        print("✅ All active users have roles assigned")
        return 0
    
    # Get the Engineering & Common Features Access role
    try:
        default_role = Role.objects.get(code='engineering_common_access')
    except Role.DoesNotExist:
        print("⚠️  Default role 'Engineering & Common Features Access' not found")
        print("   Skipping default role assignment")
        return 0
    
    print(f"Found {count} active user(s) without roles")
    print(f"Assigning role: {default_role.name}\n")
    
    assigned_count = 0
    for profile in profiles_without_roles:
        try:
            with transaction.atomic():
                profile.roles.add(default_role)
                print(f"   ✅ Assigned role to: {profile.user.email}")
                assigned_count += 1
        except Exception as e:
            print(f"   ❌ Failed to assign role to {profile.user.email}: {str(e)}")
    
    print(f"\n✅ Assigned roles to {assigned_count} user(s)")
    return assigned_count

def verify_admin_ui_count():
    """Verify the expected admin UI count"""
    print_section("4️⃣ Verifying Admin UI Count")
    
    # Query that admin UI should use
    admin_users = User.objects.filter(
        is_active=True
    ).exclude(
        email__contains='.deleted_'
    )
    
    count = admin_users.count()
    
    print(f"📊 Admin UI Query Result:")
    print(f"   Filter: is_active=True AND NOT email LIKE '%.deleted_%'")
    print(f"   Expected Count: {count} users")
    print(f"   Reported Count: 325 users")
    
    discrepancy = count - 325
    if abs(discrepancy) <= 5:
        print(f"\n✅ Counts are consistent (difference: {abs(discrepancy)})")
    else:
        print(f"\n⚠️  Discrepancy: {discrepancy} users")
        print(f"   This might be due to:")
        print(f"   - Cached data in frontend")
        print(f"   - Different filtering logic")
        print(f"   - Recent database changes")
    
    return count

def main():
    print(f"\n{'#'*90}")
    print(f"#{'DATABASE SYNCHRONIZATION CLEANUP'.center(88)}#")
    print(f"#{'Fixing RBAC Profile and Role Issues'.center(88)}#")
    print(f"{'#'*90}")
    print(f"\n🕐 Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Run cleanup operations
    created = create_missing_rbac_profiles()
    cleaned = cleanup_deleted_user_profiles()
    assigned = assign_default_roles()
    admin_count = verify_admin_ui_count()
    
    # Final summary
    print_section("📋 CLEANUP SUMMARY")
    print(f"✅ RBAC Profiles Created:        {created}")
    print(f"✅ Deleted Profiles Cleaned:     {cleaned}")
    print(f"✅ Default Roles Assigned:       {assigned}")
    print(f"📊 Expected Admin UI Count:      {admin_count}")
    print(f"\n🕐 Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"\n{'='*90}")
    print(f"✅ Database synchronization cleanup completed!")
    print(f"{'='*90}\n")

if __name__ == "__main__":
    response = input("🚀 Start database cleanup? (yes/no): ").strip().lower()
    if response in ['yes', 'y']:
        main()
    else:
        print("\n❌ Operation cancelled")
