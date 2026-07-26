"""
RBAC Issue Fix Script
Fixes identified issues from diagnostic:
1. Assigns Default role to users without any roles
2. Clears cached module/permission data
3. Optionally removes inactive role assignments
"""
import os
import sys
import django

# Setup Django
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Role, UserRole
from apps.rbac.rbac_config import DEFAULT_ROLE_CONFIG
from django.core.cache import cache
from django.db import transaction

User = get_user_model()

print("="*80)
print("RBAC ISSUE FIX SCRIPT")
print("="*80)
print()

# ==================== FIX 1: Assign Default Role to Users Without Roles ====================
print("FIX 1: Assigning Default Role to Users Without Roles")
print("-"*80)

default_role_code = DEFAULT_ROLE_CONFIG.get('code', 'default')
try:
    default_role = Role.objects.get(code=default_role_code, is_active=True)
    print(f"✅ Found Default Role: {default_role.name} (ID: {default_role.id})")
except Role.DoesNotExist:
    print(f"❌ DEFAULT ROLE NOT FOUND with code: {default_role_code}")
    print("Cannot proceed with fix. Please ensure Default role exists.")
    sys.exit(1)

# Find users without roles
profiles_without_roles = UserProfile.objects.filter(
    userrole__isnull=True,
    is_deleted=False,
    status='active'
).distinct()

print(f"Found {profiles_without_roles.count()} active users without roles")

if profiles_without_roles.count() > 0:
    print("\nUsers without roles:")
    for profile in profiles_without_roles:
        print(f"  • {profile.user.email} (ID: {profile.id})")
    
    confirm = input("\nAssign Default role to these users? (yes/no): ")
    
    if confirm.lower() == 'yes':
        with transaction.atomic():
            fixed_count = 0
            for profile in profiles_without_roles:
                user_role, created = UserRole.objects.get_or_create(
                    user_profile=profile,
                    role=default_role,
                    defaults={'is_primary': True}
                )
                if created:
                    print(f"  ✅ Assigned Default role to {profile.user.email}")
                    fixed_count += 1
                else:
                    print(f"  ⚠️  {profile.user.email} already has Default role")
            
            print(f"\n✅ Successfully assigned Default role to {fixed_count} users")
    else:
        print("Skipped Default role assignment")
else:
    print("✅ All active users have roles assigned")

print()

# ==================== FIX 2: Clear Cached Module/Permission Data ====================
print("FIX 2: Clearing Cached Module/Permission Data")
print("-"*80)

confirm_cache = input("Clear all user module/permission caches? (yes/no): ")

if confirm_cache.lower() == 'yes':
    cleared_count = 0
    all_profiles = UserProfile.objects.filter(is_deleted=False)
    
    print(f"Clearing cache for {all_profiles.count()} user profiles...")
    
    for profile in all_profiles:
        cache.delete(f'user_modules_{profile.id}')
        cache.delete(f'user_permissions_{profile.id}')
        cleared_count += 1
    
    print(f"✅ Cleared cache for {cleared_count} users")
    print("Users will see their current role-based access on next page load")
else:
    print("Skipped cache clearing")

print()

# ==================== FIX 3: Remove Inactive Role Assignments (Optional) ====================
print("FIX 3: Remove Inactive Role Assignments (Optional)")
print("-"*80)

inactive_userroles = UserRole.objects.filter(role__is_active=False)
print(f"Found {inactive_userroles.count()} user-role assignments to INACTIVE roles")

if inactive_userroles.count() > 0:
    # Group by role
    from django.db.models import Count
    inactive_by_role = inactive_userroles.values('role__name', 'role__code').annotate(
        user_count=Count('id')
    )
    
    print("\nInactive role assignments by role:")
    for item in inactive_by_role:
        print(f"  • {item['role__name']} ({item['role__code']}): {item['user_count']} users")
    
    print("\n⚠️  WARNING: Inactive roles are already filtered out in the UI.")
    print("Removing these assignments is optional and will clean up the database.")
    
    confirm_remove = input("\nRemove all inactive role assignments? (yes/no): ")
    
    if confirm_remove.lower() == 'yes':
        with transaction.atomic():
            deleted_count = inactive_userroles.delete()[0]
            print(f"✅ Removed {deleted_count} inactive role assignments")
            
            # Clear cache for affected users
            print("Clearing cache for affected users...")
            all_profiles = UserProfile.objects.filter(is_deleted=False)
            for profile in all_profiles:
                cache.delete(f'user_modules_{profile.id}')
                cache.delete(f'user_permissions_{profile.id}')
            print("✅ Cache cleared")
    else:
        print("Skipped inactive role removal")
else:
    print("✅ No inactive role assignments found")

print()

# ==================== FIX 4: Ensure Primary Role Consistency ====================
print("FIX 4: Ensure Primary Role Consistency")
print("-"*80)

# Find users without a primary role but have roles
from django.db.models import Q

profiles_with_roles_no_primary = UserProfile.objects.filter(
    userrole__isnull=False
).exclude(
    userrole__is_primary=True
).distinct()

print(f"Found {profiles_with_roles_no_primary.count()} users with roles but no primary role")

if profiles_with_roles_no_primary.count() > 0:
    print("\nUsers without primary role:")
    for profile in profiles_with_roles_no_primary[:10]:  # Show first 10
        user_roles = profile.userrole_set.all()
        print(f"  • {profile.user.email}: {user_roles.count()} roles, none primary")
    
    if profiles_with_roles_no_primary.count() > 10:
        print(f"  ... and {profiles_with_roles_no_primary.count() - 10} more")
    
    confirm_primary = input("\nSet first role as primary for these users? (yes/no): ")
    
    if confirm_primary.lower() == 'yes':
        with transaction.atomic():
            fixed_count = 0
            for profile in profiles_with_roles_no_primary:
                # Get first active role
                first_role = profile.userrole_set.filter(role__is_active=True).first()
                if first_role:
                    first_role.is_primary = True
                    first_role.save(update_fields=['is_primary'])
                    fixed_count += 1
                    print(f"  ✅ Set {first_role.role.name} as primary for {profile.user.email}")
            
            print(f"\n✅ Set primary role for {fixed_count} users")
    else:
        print("Skipped primary role assignment")
else:
    print("✅ All users with roles have a primary role")

print()

# ==================== SUMMARY ====================
print("="*80)
print("FIX SCRIPT COMPLETE")
print("="*80)
print()
print("NEXT STEPS:")
print("1. Restart backend: docker-compose --profile local restart backend_local")
print("2. Hard refresh frontend: Ctrl+Shift+R in browser")
print("3. Test role assignment in /admin/users")
print("4. Check that role changes reflect immediately in user profile")
print()
print("If issues persist, check:")
print("- Browser console for API errors")
print("- Network tab for failed /rbac/users/ calls")
print("- Backend logs for permission errors")
print("="*80)
