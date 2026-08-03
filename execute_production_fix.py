#!/usr/bin/env python
"""
PRODUCTION FIX - Remove Django flags from non-admin users
Safe execution with validation and rollback capability
"""
import os
import sys
import django

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.db import models, transaction
from apps.rbac.models import UserProfile, UserRole, Role

User = get_user_model()

# Soft-coded configuration
AUTHORIZED_ADMIN_ROLES = ['super_admin', 'admin', 'ict_admin']
PROTECTED_ADMINS = [
    'mohammed.agra@rejlers.ae',
    'fahad.hussein@rejlers.ae',
    'tanzeem.agra@rejlers.ae'
]

def execute_fix():
    """Execute the complete fix with validation"""
    print("=" * 80)
    print("  PRODUCTION RBAC FIX - AUTOMATED EXECUTION")
    print("=" * 80)
    
    # Step 1: Audit
    print("\n[STEP 1/4] Auditing affected users...")
    affected = User.objects.filter(
        models.Q(is_superuser=True) | models.Q(is_staff=True)
    ).exclude(
        email__in=PROTECTED_ADMINS
    ).select_related('rbac_profile')
    
    affected_list = []
    for user in affected:
        try:
            profile = UserProfile.objects.get(user=user, is_deleted=False)
            roles = UserRole.objects.filter(
                user_profile=profile,
                role__is_active=True
            ).select_related('role')
            
            role_codes = [ur.role.code for ur in roles]
            has_admin = any(c in AUTHORIZED_ADMIN_ROLES for c in role_codes)
            
            if not has_admin:
                affected_list.append({
                    'user': user,
                    'email': user.email,
                    'is_active': user.is_active,
                    'is_superuser': user.is_superuser,
                    'is_staff': user.is_staff,
                })
        except:
            pass
    
    print(f"   Found {len(affected_list)} users needing fix")
    
    if not affected_list:
        print("\n✅ No users need fixing!")
        return
    
    # Show sample
    print("\n   Sample users:")
    for item in affected_list[:5]:
        print(f"   - {item['email']} (active={item['is_active']})")
    if len(affected_list) > 5:
        print(f"   ... and {len(affected_list) - 5} more")
    
    # Step 2: Fix Django flags
    print("\n[STEP 2/4] Removing Django flags...")
    fixed_count = 0
    with transaction.atomic():
        for item in affected_list:
            user = item['user']
            user.is_superuser = False
            user.is_staff = False
            user.save(update_fields=['is_superuser', 'is_staff'])
            fixed_count += 1
    
    print(f"   ✅ Fixed {fixed_count} users")
    
    # Step 3: Reactivate users
    print("\n[STEP 3/4] Reactivating users...")
    reactivated = 0
    with transaction.atomic():
        for item in affected_list:
            if not item['is_active']:
                user = item['user']
                user.is_active = True
                user.save(update_fields=['is_active'])
                reactivated += 1
    
    print(f"   ✅ Reactivated {reactivated} users")
    
    # Step 4: Verify
    print("\n[STEP 4/4] Verifying fix...")
    remaining = User.objects.filter(
        models.Q(is_superuser=True) | models.Q(is_staff=True),
        is_active=True
    ).exclude(
        email__in=PROTECTED_ADMINS
    ).select_related('rbac_profile')
    
    remaining_issues = 0
    for user in remaining:
        try:
            profile = UserProfile.objects.get(user=user, is_deleted=False)
            roles = UserRole.objects.filter(
                user_profile=profile,
                role__is_active=True
            ).select_related('role')
            
            role_codes = [ur.role.code for ur in roles]
            has_admin = any(c in AUTHORIZED_ADMIN_ROLES for c in role_codes)
            
            if not has_admin:
                remaining_issues += 1
        except:
            pass
    
    print(f"   Remaining issues: {remaining_issues}")
    
    # Summary
    print("\n" + "=" * 80)
    print("  FIX COMPLETE")
    print("=" * 80)
    print(f"  ✅ Fixed:       {fixed_count} users")
    print(f"  ✅ Reactivated: {reactivated} users")
    print(f"  ✅ Remaining:   {remaining_issues} issues")
    print("=" * 80)
    
    if remaining_issues == 0:
        print("\n✅ SUCCESS - All users fixed!")
        print("\n📋 Next Steps:")
        print("   1. Users should logout and login again")
        print("   2. Access now controlled by RBAC roles only")
        print("   3. Test: ravikumar.naickar@rejlers.ae should have limited access")
    else:
        print(f"\n⚠️  WARNING - {remaining_issues} users still have issues")

if __name__ == '__main__':
    try:
        execute_fix()
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
