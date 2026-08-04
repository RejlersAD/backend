#!/usr/bin/env python
"""
Bulk Fix All Users with RBAC Permission Issues
===============================================
Fixes 85+ users who have Django flags (is_superuser, is_staff) but non-admin RBAC roles

Usage:
    In Railway Shell:
        python bulk_fix_rbac_production.py --dry-run      # Preview changes
        python bulk_fix_rbac_production.py --fix-all      # Apply all fixes
        python bulk_fix_rbac_production.py --reactivate   # Reactivate users after fix
"""

import os
import sys
import django
from datetime import datetime
import argparse

# Setup Django environment
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.db import models, transaction
from apps.rbac.models import UserProfile, UserRole, Role

User = get_user_model()

# ============================================================
# SOFT-CODED CONFIGURATION
# ============================================================

# Roles that are AUTHORIZED to have Django staff/superuser flags
AUTHORIZED_ADMIN_ROLES = [
    'super_admin',
    'admin',
    'ict_admin',
]

# Known authorized admin users (always skip these)
KNOWN_AUTHORIZED_ADMINS = [
    'mohammed.agra@rejlers.ae',
    'fahad.hussein@rejlers.ae',
    'tanzeem.agra@rejlers.ae',
]

# ============================================================
# HELPER FUNCTIONS
# ============================================================

def print_header(message, char='='):
    """Print formatted header"""
    print(f"\n{char * 80}")
    print(f"  {message}")
    print(f"{char * 80}\n")


def print_step(step_num, message):
    """Print step header"""
    print(f"\n{'=' * 80}")
    print(f"STEP {step_num}: {message}")
    print(f"{'=' * 80}\n")


# ============================================================
# AUDIT FUNCTIONS
# ============================================================

def find_affected_users(include_inactive=True):
    """
    Find all users with Django flags but non-admin RBAC roles
    
    Args:
        include_inactive: Include deactivated users (default True)
    
    Returns:
        dict with categorized users
    """
    print_header("🔍 SCANNING PRODUCTION DATABASE")
    
    # Build query
    users_query = User.objects.select_related('rbac_profile')
    
    if not include_inactive:
        users_query = users_query.filter(is_active=True)
    
    # Find users with Django flags
    users_with_flags = users_query.filter(
        models.Q(is_superuser=True) | models.Q(is_staff=True)
    ).exclude(
        email__in=KNOWN_AUTHORIZED_ADMINS
    )
    
    print(f"📊 Total users with Django flags: {users_with_flags.count()}")
    
    results = {
        'affected': [],      # Users needing fix
        'authorized': [],    # Users with admin roles (OK)
        'errors': [],        # Errors during check
    }
    
    for user in users_with_flags:
        try:
            profile = UserProfile.objects.get(user=user, is_deleted=False)
            user_roles = UserRole.objects.filter(
                user_profile=profile,
                role__is_active=True
            ).select_related('role')
            
            role_codes = [ur.role.code for ur in user_roles]
            primary_role = next((ur.role for ur in user_roles if ur.is_primary), None)
            
            has_admin_role = any(code in AUTHORIZED_ADMIN_ROLES for code in role_codes)
            
            user_info = {
                'user': user,
                'email': user.email,
                'name': f"{user.first_name} {user.last_name}".strip(),
                'is_superuser': user.is_superuser,
                'is_staff': user.is_staff,
                'is_active': user.is_active,
                'primary_role': primary_role.code if primary_role else 'None',
                'all_roles': ', '.join(role_codes) if role_codes else 'None',
                'last_login': user.last_login,
            }
            
            if has_admin_role:
                results['authorized'].append(user_info)
            else:
                results['affected'].append(user_info)
        
        except UserProfile.DoesNotExist:
            results['errors'].append({
                'email': user.email,
                'error': 'UserProfile not found'
            })
        except Exception as e:
            results['errors'].append({
                'email': user.email,
                'error': str(e)
            })
    
    return results


def print_audit_results(results):
    """Print formatted audit results"""
    
    # Affected users (need fix)
    if results['affected']:
        print_header(f"🔴 USERS NEEDING FIX ({len(results['affected'])} users)", '-')
        print("These users have Django flags but non-admin RBAC roles\n")
        
        for i, user in enumerate(results['affected'][:20], 1):  # Show first 20
            status = "🔴 CRITICAL" if user['is_superuser'] else "🟡 WARNING"
            active = "✅ Active" if user['is_active'] else "⚠️ Inactive"
            print(f"{i}. {user['email']}")
            print(f"   Name: {user['name']}")
            print(f"   Status: {status} | {active}")
            print(f"   Flags: superuser={user['is_superuser']}, staff={user['is_staff']}")
            print(f"   RBAC Role: {user['primary_role']}")
            print(f"   Last Login: {user['last_login'].strftime('%Y-%m-%d') if user['last_login'] else 'Never'}")
            print()
        
        if len(results['affected']) > 20:
            print(f"... and {len(results['affected']) - 20} more users\n")
    
    # Authorized users (OK)
    if results['authorized']:
        print_header(f"✅ AUTHORIZED ADMINS ({len(results['authorized'])} users)", '-')
        for user in results['authorized']:
            print(f"  {user['email']} - {user['primary_role']}")
        print()
    
    # Errors
    if results['errors']:
        print_header(f"❌ ERRORS ({len(results['errors'])} users)", '-')
        for error in results['errors']:
            print(f"  {error['email']}: {error['error']}")
        print()
    
    # Summary
    print_header("📊 SUMMARY")
    print(f"🔴 Users Needing Fix:      {len(results['affected'])}")
    print(f"✅ Authorized Admins (OK): {len(results['authorized'])}")
    print(f"❌ Errors:                 {len(results['errors'])}")
    print()


# ============================================================
# FIX FUNCTIONS
# ============================================================

def bulk_fix_django_flags(dry_run=True):
    """
    Remove is_superuser and is_staff flags from all affected users
    
    Args:
        dry_run: If True, only preview changes. If False, apply changes.
    
    Returns:
        Number of users fixed
    """
    print_step(1, "BULK FIX DJANGO FLAGS")
    
    # Find affected users
    results = find_affected_users(include_inactive=True)
    affected_users = results['affected']
    
    if not affected_users:
        print("✅ No users need fixing!")
        return 0
    
    print(f"Found {len(affected_users)} users to fix\n")
    
    if dry_run:
        print("🔍 DRY RUN - Preview changes:\n")
        for user_info in affected_users[:10]:  # Show first 10
            print(f"  Would fix: {user_info['email']}")
            print(f"    Current: superuser={user_info['is_superuser']}, staff={user_info['is_staff']}")
            print(f"    New:     superuser=False, staff=False")
            print(f"    RBAC Role: {user_info['primary_role']}")
            print()
        
        if len(affected_users) > 10:
            print(f"  ... and {len(affected_users) - 10} more users\n")
        
        print(f"⚠️  DRY RUN COMPLETE - No changes made")
        print(f"   To apply fixes, run with --fix-all flag")
        return 0
    
    # Apply fixes
    print("⚠️  APPLYING FIXES - Modifying database...\n")
    
    fixed_count = 0
    failed_count = 0
    
    with transaction.atomic():
        for user_info in affected_users:
            try:
                user = user_info['user']
                user.is_superuser = False
                user.is_staff = False
                user.save(update_fields=['is_superuser', 'is_staff'])
                
                print(f"✅ Fixed: {user_info['email']}")
                fixed_count += 1
            
            except Exception as e:
                print(f"❌ Failed: {user_info['email']} - {str(e)}")
                failed_count += 1
    
    print(f"\n{'=' * 80}")
    print(f"✅ BULK FIX COMPLETE")
    print(f"   Fixed:  {fixed_count} users")
    print(f"   Failed: {failed_count} users")
    print(f"{'=' * 80}\n")
    
    return fixed_count


def reactivate_fixed_users(dry_run=True):
    """
    Reactivate users after their Django flags have been removed
    Only reactivates users who have been fixed (no Django flags)
    
    Args:
        dry_run: If True, only preview. If False, reactivate users.
    
    Returns:
        Number of users reactivated
    """
    print_step(2, "REACTIVATE FIXED USERS")
    
    # Find inactive users without Django flags and non-admin roles
    inactive_fixed_users = User.objects.filter(
        is_active=False,
        is_superuser=False,
        is_staff=False
    ).exclude(
        email__in=KNOWN_AUTHORIZED_ADMINS
    ).select_related('rbac_profile')
    
    # Filter to only users with non-admin RBAC roles
    users_to_reactivate = []
    
    for user in inactive_fixed_users:
        try:
            profile = UserProfile.objects.get(user=user, is_deleted=False)
            user_roles = UserRole.objects.filter(
                user_profile=profile,
                role__is_active=True
            ).select_related('role')
            
            role_codes = [ur.role.code for ur in user_roles]
            has_admin_role = any(code in AUTHORIZED_ADMIN_ROLES for code in role_codes)
            
            if not has_admin_role and role_codes:  # Has non-admin role
                primary_role = next((ur.role for ur in user_roles if ur.is_primary), None)
                users_to_reactivate.append({
                    'user': user,
                    'email': user.email,
                    'name': f"{user.first_name} {user.last_name}".strip(),
                    'role': primary_role.code if primary_role else 'None',
                })
        except:
            continue
    
    if not users_to_reactivate:
        print("✅ No inactive users to reactivate (or all still have Django flags)")
        return 0
    
    print(f"Found {len(users_to_reactivate)} users to reactivate\n")
    
    if dry_run:
        print("🔍 DRY RUN - Preview reactivation:\n")
        for user_info in users_to_reactivate[:10]:
            print(f"  Would reactivate: {user_info['email']}")
            print(f"    RBAC Role: {user_info['role']}")
            print()
        
        if len(users_to_reactivate) > 10:
            print(f"  ... and {len(users_to_reactivate) - 10} more users\n")
        
        print(f"⚠️  DRY RUN COMPLETE - No changes made")
        print(f"   To reactivate, run with --reactivate flag")
        return 0
    
    # Reactivate users
    print("⚠️  REACTIVATING USERS...\n")
    
    reactivated_count = 0
    
    with transaction.atomic():
        for user_info in users_to_reactivate:
            try:
                user = user_info['user']
                user.is_active = True
                user.save(update_fields=['is_active'])
                
                print(f"✅ Reactivated: {user_info['email']}")
                reactivated_count += 1
            
            except Exception as e:
                print(f"❌ Failed: {user_info['email']} - {str(e)}")
    
    print(f"\n{'=' * 80}")
    print(f"✅ REACTIVATION COMPLETE")
    print(f"   Reactivated: {reactivated_count} users")
    print(f"{'=' * 80}\n")
    
    return reactivated_count


# ============================================================
# MAIN EXECUTION
# ============================================================

def main():
    """Main execution with command line arguments"""
    parser = argparse.ArgumentParser(
        description='Bulk fix RBAC permission issues in production'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview changes without applying them (default)'
    )
    parser.add_argument(
        '--fix-all',
        action='store_true',
        help='Apply fixes to all affected users'
    )
    parser.add_argument(
        '--reactivate',
        action='store_true',
        help='Reactivate fixed users (run after --fix-all)'
    )
    parser.add_argument(
        '--audit-only',
        action='store_true',
        help='Only show audit results, no fixes'
    )
    
    args = parser.parse_args()
    
    print_header("🔧 BULK RBAC FIX - PRODUCTION")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Environment: {os.getenv('RAILWAY_ENVIRONMENT', 'local')}")
    print()
    
    try:
        # Audit only
        if args.audit_only:
            results = find_affected_users(include_inactive=True)
            print_audit_results(results)
            return
        
        # Fix all users
        if args.fix_all:
            print("⚠️  WARNING: This will modify the database!")
            print(f"   Authorized admins will NOT be affected: {', '.join(KNOWN_AUTHORIZED_ADMINS)}")
            print()
            
            # Ask for confirmation if running interactively
            if sys.stdin.isatty():
                response = input("Type 'YES' to proceed: ")
                if response != 'YES':
                    print("❌ Cancelled")
                    return
            
            fixed = bulk_fix_django_flags(dry_run=False)
            
            if fixed > 0:
                print("\n✅ All Django flags removed successfully!")
                print("   Users now have RBAC-only permissions")
                print("\n💡 Next step: Run with --reactivate to reactivate users")
            
            return
        
        # Reactivate users
        if args.reactivate:
            print("⚠️  WARNING: This will reactivate users!")
            print("   Only users with is_superuser=False and is_staff=False will be reactivated")
            print()
            
            if sys.stdin.isatty():
                response = input("Type 'YES' to proceed: ")
                if response != 'YES':
                    print("❌ Cancelled")
                    return
            
            reactivated = reactivate_fixed_users(dry_run=False)
            
            if reactivated > 0:
                print("\n✅ Users reactivated successfully!")
                print("   They now have access based on their RBAC roles only")
            
            return
        
        # Default: Dry run
        results = find_affected_users(include_inactive=True)
        print_audit_results(results)
        
        if results['affected']:
            print("\n" + "=" * 80)
            print("💡 NEXT STEPS:")
            print("=" * 80)
            print("\n1. Review the users listed above")
            print("2. Run with --fix-all to remove Django flags")
            print("3. Run with --reactivate to reactivate users")
            print("\nCommands:")
            print("  python bulk_fix_rbac_production.py --fix-all")
            print("  python bulk_fix_rbac_production.py --reactivate")
            print()
    
    except Exception as e:
        print(f"\n❌ FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
