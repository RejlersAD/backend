#!/usr/bin/env python
"""
Fix Custom Role Permission Issue
=================================
Issue: Users have Django superuser/staff flags set to True, which bypasses all RBAC checks
Solution: Set is_superuser=False and is_staff=False for users who should not have admin access

Usage:
    python fix_custom_role_permissions.py
    
Or in Railway shell:
    python manage.py shell < fix_custom_role_permissions.py
"""

import os
import sys
import django

# Setup Django environment
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, UserRole, Role

User = get_user_model()

# ============================================================
# CONFIGURATION: Users to fix
# ============================================================
# Add email addresses of users who have incorrect Django flags
# These users should NOT have is_superuser or is_staff access
USERS_TO_FIX = [
    "kiran.ingale@rejlers.ae",
    "ravikumar.naickar@rejlers.ae",
]

# Roles that are authorized to have Django staff/superuser flags
AUTHORIZED_ADMIN_ROLES = [
    'super_admin',
    'admin',
    'ict_admin',
]

# ============================================================
# FUNCTIONS
# ============================================================

def check_user_state(email):
    """Check current state of a user"""
    try:
        user = User.objects.get(email=email)
        profile = UserProfile.objects.get(user=user, is_deleted=False)
        
        # Get active roles
        user_roles = UserRole.objects.filter(
            user_profile=profile,
            role__is_active=True
        ).select_related('role')
        
        role_codes = [ur.role.code for ur in user_roles]
        primary_role = next((ur.role for ur in user_roles if ur.is_primary), None)
        
        state = {
            'email': email,
            'exists': True,
            'is_superuser': user.is_superuser,
            'is_staff': user.is_staff,
            'is_active': user.is_active,
            'role_codes': role_codes,
            'primary_role': primary_role.code if primary_role else None,
            'has_admin_role': any(code in AUTHORIZED_ADMIN_ROLES for code in role_codes),
            'needs_fix': False,
        }
        
        # Determine if user needs fixing
        # If user has Django flags but doesn't have an authorized admin role
        if (user.is_superuser or user.is_staff) and not state['has_admin_role']:
            state['needs_fix'] = True
            state['issue'] = []
            if user.is_superuser:
                state['issue'].append('is_superuser=True')
            if user.is_staff:
                state['issue'].append('is_staff=True')
        
        return state
    
    except User.DoesNotExist:
        return {
            'email': email,
            'exists': False,
            'error': 'User not found in database'
        }
    except UserProfile.DoesNotExist:
        return {
            'email': email,
            'exists': True,
            'error': 'UserProfile not found or deleted'
        }
    except Exception as e:
        return {
            'email': email,
            'exists': True,
            'error': f'Error checking user: {str(e)}'
        }


def fix_user_permissions(email, dry_run=True):
    """Fix Django flags for a user"""
    try:
        user = User.objects.get(email=email)
        
        if dry_run:
            print(f"    [DRY RUN] Would set is_superuser=False, is_staff=False for {email}")
            return True
        
        # Apply fix
        user.is_superuser = False
        user.is_staff = False
        user.save(update_fields=['is_superuser', 'is_staff'])
        
        print(f"    ✅ Fixed: Set is_superuser=False, is_staff=False for {email}")
        return True
    
    except User.DoesNotExist:
        print(f"    ❌ Error: User {email} not found")
        return False
    except Exception as e:
        print(f"    ❌ Error fixing {email}: {str(e)}")
        return False


def print_separator():
    print("=" * 70)


def main():
    """Main execution function"""
    print_separator()
    print("🔧 FIX CUSTOM ROLE PERMISSIONS ISSUE")
    print_separator()
    print()
    
    # Step 1: Check all users
    print("📋 STEP 1: Checking current state of users...")
    print()
    
    users_needing_fix = []
    users_ok = []
    users_error = []
    
    for email in USERS_TO_FIX:
        state = check_user_state(email)
        
        if not state.get('exists'):
            print(f"❌ {email}")
            print(f"   Error: {state.get('error')}")
            users_error.append(email)
        
        elif state.get('error'):
            print(f"⚠️  {email}")
            print(f"   Error: {state.get('error')}")
            users_error.append(email)
        
        elif state.get('needs_fix'):
            print(f"🔴 {email}")
            print(f"   Issue: {', '.join(state['issue'])}")
            print(f"   RBAC Role: {state['primary_role']}")
            print(f"   All Roles: {', '.join(state['role_codes'])}")
            print(f"   Status: NEEDS FIX")
            users_needing_fix.append(email)
        
        else:
            print(f"✅ {email}")
            print(f"   is_superuser: {state['is_superuser']}")
            print(f"   is_staff: {state['is_staff']}")
            print(f"   RBAC Role: {state['primary_role']}")
            print(f"   Status: OK")
            users_ok.append(email)
        
        print()
    
    # Summary
    print_separator()
    print("📊 SUMMARY:")
    print(f"   ✅ Users OK: {len(users_ok)}")
    print(f"   🔴 Users needing fix: {len(users_needing_fix)}")
    print(f"   ❌ Users with errors: {len(users_error)}")
    print_separator()
    print()
    
    if not users_needing_fix:
        print("✅ All users are correctly configured. No action needed.")
        return
    
    # Step 2: Apply fixes (dry run first)
    print("📋 STEP 2: Dry run - showing what would be changed...")
    print()
    
    for email in users_needing_fix:
        fix_user_permissions(email, dry_run=True)
    
    print()
    print_separator()
    print("⚠️  DRY RUN COMPLETE")
    print("   To apply fixes, modify this script:")
    print("   Change: fix_user_permissions(email, dry_run=True)")
    print("   To:     fix_user_permissions(email, dry_run=False)")
    print_separator()
    print()
    
    # Uncomment the section below to apply fixes automatically
    # WARNING: This will modify the database!
    
    """
    print("📋 STEP 3: Applying fixes...")
    print()
    
    fixed_count = 0
    failed_count = 0
    
    for email in users_needing_fix:
        if fix_user_permissions(email, dry_run=False):
            fixed_count += 1
        else:
            failed_count += 1
    
    print()
    print_separator()
    print("✅ FIX COMPLETE")
    print(f"   Fixed: {fixed_count} users")
    print(f"   Failed: {failed_count} users")
    print_separator()
    print()
    print("⚠️  IMPORTANT: Users must logout and login again for changes to take effect")
    """


if __name__ == '__main__':
    main()
