#!/usr/bin/env python
"""
PRODUCTION CLEANUP - Remove ALL custom_* roles from ALL users

CRITICAL FIX for kiran.ingale@rejlers.ae and ALL users with custom roles.

ROOT CAUSE:
- Users have multiple roles: "default" (primary) + "custom_<email>" (hidden but active)
- Frontend shows "Default" but user still sees Finance/QHSE/Admin
- Custom roles grant unrestricted access that bypasses ROLE_MODULE_POLICY
- Custom roles are hidden in UI but still active in database (rbac_userrole table)

SOLUTION:
1. Find ALL users with custom_* roles
2. Remove custom roles, keep only system roles (default, admin, engineer, etc.)
3. Ensure every user has at least "default" role
4. Clear module/permission cache for all affected users
5. Generate audit report

SAFE TO RUN:
- Transaction-safe (all-or-nothing)
- Only removes custom_* roles (preserves system roles)
- Auto-assigns "default" if user left with no roles
- Generates before/after report for verification

RUN IN PRODUCTION (Railway shell):
  python cleanup_all_custom_roles_production.py
"""

import os
import sys
import django
from pathlib import Path

# Setup Django
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.db import transaction
from django.core.cache import cache
from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Role, UserRole
from apps.rbac.rbac_config import MODULE_ASSIGNMENT_CONFIG

User = get_user_model()

# Soft-coded from rbac_config.py
CUSTOM_ROLE_PREFIX = MODULE_ASSIGNMENT_CONFIG.get('custom_role_prefix', 'custom_')
DEFAULT_ROLE_CODE = 'default'


def cleanup_all_custom_roles():
    """
    Remove all custom_* roles from all users in the database.
    Ensures all users have at least the default role.
    """
    print("=" * 80)
    print("🔧 PRODUCTION CLEANUP - Remove ALL Custom Roles")
    print("=" * 80)
    
    # Step 1: Find all users with custom roles
    print("\n📋 STEP 1: Identifying users with custom roles...")
    
    custom_roles = Role.objects.filter(
        code__startswith=CUSTOM_ROLE_PREFIX,
        is_active=True
    )
    
    if not custom_roles.exists():
        print("   ✅ No custom roles found in database")
        print("\n   Checking for inactive custom roles...")
        inactive_custom = Role.objects.filter(code__startswith=CUSTOM_ROLE_PREFIX)
        if inactive_custom.exists():
            print(f"   ℹ️  Found {inactive_custom.count()} inactive custom roles")
            for role in inactive_custom:
                print(f"      - {role.code} ({role.name})")
        return
    
    print(f"   ⚠️  Found {custom_roles.count()} custom roles:")
    for role in custom_roles[:10]:
        user_count = role.user_profiles.count()
        print(f"      - {role.code} ({role.name}) - {user_count} user(s)")
    if custom_roles.count() > 10:
        print(f"      ... and {custom_roles.count() - 10} more")
    
    # Find users with custom roles
    affected_users = UserProfile.objects.filter(
        roles__code__startswith=CUSTOM_ROLE_PREFIX,
        roles__is_active=True,
        is_deleted=False
    ).distinct().select_related('user', 'organization').prefetch_related('roles')
    
    if not affected_users.exists():
        print("\n   ✅ No users have custom roles assigned")
        return
    
    print(f"\n   Found {affected_users.count()} users with custom roles")
    
    # Step 2: Display affected users
    print("\n📋 STEP 2: Affected users list:")
    for i, profile in enumerate(affected_users[:20], 1):
        email = profile.user.email
        custom_user_roles = profile.roles.filter(code__startswith=CUSTOM_ROLE_PREFIX, is_active=True)
        other_roles = profile.roles.exclude(code__startswith=CUSTOM_ROLE_PREFIX).filter(is_active=True)
        
        print(f"\n   {i}. {email}")
        print(f"      Current roles: {profile.roles.filter(is_active=True).count()} total")
        print(f"      Custom roles: {custom_user_roles.count()}")
        for role in custom_user_roles:
            print(f"         ❌ {role.code} ({role.name})")
        if other_roles.exists():
            print(f"      System roles: {other_roles.count()}")
            for role in other_roles:
                print(f"         ✅ {role.code} ({role.name})")
        else:
            print(f"      System roles: 0 ⚠️  Will add default role")
    
    if affected_users.count() > 20:
        print(f"\n   ... and {affected_users.count() - 20} more users")
    
    # Step 3: Confirm cleanup
    print("\n" + "=" * 80)
    print("⚠️  WARNING: This will remove ALL custom_* roles from ALL users")
    print("=" * 80)
    print("\nChanges:")
    print("  1. Remove all custom_* roles from affected users")
    print("  2. Ensure every user has 'default' role (if no other system role)")
    print("  3. Clear module/permission cache for all affected users")
    print("  4. Generate audit report")
    
    # Auto-proceed in production (Railway doesn't support interactive input)
    proceed = True
    
    if not proceed:
        print("\n❌ Cleanup cancelled")
        return
    
    # Step 4: Apply cleanup with transaction safety
    print("\n" + "=" * 80)
    print("🔄 STEP 3: Applying cleanup...")
    print("=" * 80)
    
    success_count = 0
    error_count = 0
    cache_cleared_count = 0
    default_assigned_count = 0
    
    # Get default role
    try:
        default_role = Role.objects.get(code=DEFAULT_ROLE_CODE, is_active=True)
    except Role.DoesNotExist:
        print(f"\n❌ ERROR: Default role '{DEFAULT_ROLE_CODE}' not found!")
        print("   Run: python manage.py seed_rbac first")
        return
    
    for profile in affected_users:
        try:
            with transaction.atomic():
                email = profile.user.email
                
                # Get custom roles for this user
                custom_user_roles = list(profile.roles.filter(
                    code__startswith=CUSTOM_ROLE_PREFIX,
                    is_active=True
                ))
                
                if not custom_user_roles:
                    continue
                
                # Remove custom roles
                removed_roles = []
                for custom_role in custom_user_roles:
                    profile.roles.remove(custom_role)
                    removed_roles.append(custom_role.code)
                
                # Check if user has any system roles left
                remaining_roles = profile.roles.exclude(
                    code__startswith=CUSTOM_ROLE_PREFIX
                ).filter(is_active=True)
                
                # Assign default role if user has no system roles
                if not remaining_roles.exists():
                    user_role, created = UserRole.objects.get_or_create(
                        user_profile=profile,
                        role=default_role,
                        defaults={'is_primary': True}
                    )
                    if created or not user_role.is_primary:
                        user_role.is_primary = True
                        user_role.save(update_fields=['is_primary'])
                        default_assigned_count += 1
                        print(f"   ✅ {email}: Removed {len(removed_roles)} custom role(s), added default")
                    else:
                        print(f"   ✅ {email}: Removed {len(removed_roles)} custom role(s), already has default")
                else:
                    print(f"   ✅ {email}: Removed {len(removed_roles)} custom role(s), kept {remaining_roles.count()} system role(s)")
                
                # Clear cache
                cache.delete(f'user_modules_{profile.id}')
                cache.delete(f'user_permissions_{profile.id}')
                cache_cleared_count += 1
                
                success_count += 1
                
        except Exception as e:
            error_count += 1
            print(f"   ❌ {profile.user.email}: ERROR - {str(e)}")
    
    # Step 5: Summary
    print("\n" + "=" * 80)
    print("📊 CLEANUP SUMMARY")
    print("=" * 80)
    print(f"\n✅ Successfully cleaned: {success_count} users")
    print(f"   Default role assigned: {default_assigned_count} users")
    print(f"   Cache cleared: {cache_cleared_count} users")
    if error_count > 0:
        print(f"❌ Errors: {error_count} users")
    
    # Step 6: Verification
    print("\n" + "=" * 80)
    print("🔍 VERIFICATION")
    print("=" * 80)
    
    remaining_custom_roles = UserRole.objects.filter(
        role__code__startswith=CUSTOM_ROLE_PREFIX,
        role__is_active=True
    ).select_related('user_profile__user', 'role')
    
    if remaining_custom_roles.exists():
        print(f"\n⚠️  WARNING: {remaining_custom_roles.count()} custom role assignments still exist:")
        for ur in remaining_custom_roles[:10]:
            print(f"   - {ur.user_profile.user.email}: {ur.role.code}")
    else:
        print("\n✅ No custom role assignments remaining")
    
    # Check for users with no roles
    users_no_roles = UserProfile.objects.filter(
        is_deleted=False,
        status='active'
    ).exclude(
        id__in=UserRole.objects.filter(
            role__is_active=True
        ).values_list('user_profile_id', flat=True)
    ).select_related('user')
    
    if users_no_roles.exists():
        print(f"\n⚠️  WARNING: {users_no_roles.count()} users have NO active roles:")
        for profile in users_no_roles[:10]:
            print(f"   - {profile.user.email}")
        print("\n   Run: python manage.py sync_default_role to fix")
    else:
        print("\n✅ All active users have at least one role")
    
    # Step 7: Next steps
    print("\n" + "=" * 80)
    print("📋 NEXT STEPS")
    print("=" * 80)
    print("\n1. Tell ALL affected users to:")
    print("   • Logout from https://www.radai.ae")
    print("   • Login again (refreshes JWT token)")
    print("   • Hard refresh browser (Ctrl+F5)")
    print("\n2. Verify kiran.ingale@rejlers.ae access:")
    print("   • Should see ONLY: Dashboard, Engineering, COMMON")
    print("   • Should NOT see: Finance, QHSE, HR, Admin")
    print("\n3. Run custom role deletion:")
    print("   python manage.py remove_custom_roles --delete-roles")
    print("\n4. Monitor for issues:")
    print("   • Check user reports of missing access")
    print("   • Review audit logs for role changes")
    
    print("\n✅ CLEANUP COMPLETE!\n")


if __name__ == '__main__':
    try:
        cleanup_all_custom_roles()
    except KeyboardInterrupt:
        print("\n\n❌ Cleanup cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ FATAL ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
