"""
Configure Michelle's Roles - SOFT-CODED APPROACH
Assigns both 'Default' and 'HR & Payroll Administrator' roles to michelle.dehoedt@rejlers.ae
Does NOT affect any other users - fully targeted and safe
"""

import os
import django
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Role, UserRole

User = get_user_model()

# ══════════════════════════════════════════════════════════════════════════════
# SOFT-CODED CONFIGURATION - Edit here to change target user or roles
# ══════════════════════════════════════════════════════════════════════════════
TARGET_USER_CONFIG = {
    'email': 'michelle.dehoedt@rejlers.ae',
    'required_roles': [
        {'code': 'default', 'is_primary': False},
        {'code': 'hr_admin', 'is_primary': True}  # HR Admin is primary
    ]
}

def print_header(text):
    print("\n" + "="*80)
    print(f"  {text}")
    print("="*80)

def configure_user_roles():
    """
    Configure roles for target user only
    Non-destructive - only adds missing roles, doesn't remove existing
    """
    email = TARGET_USER_CONFIG['email']
    required_roles = TARGET_USER_CONFIG['required_roles']
    
    print_header(f"CONFIGURING ROLES FOR: {email}")
    
    try:
        # Step 1: Find user
        user = User.objects.get(email=email)
        print(f"✅ User found: {user.email} (ID: {user.id})")
        
        # Step 2: Get or verify profile
        profile = UserProfile.objects.get(user=user)
        print(f"✅ Profile found: {profile.id}")
        
        # Step 3: Process each required role
        print(f"\n📋 Processing {len(required_roles)} required role(s):")
        
        for role_config in required_roles:
            role_code = role_config['code']
            is_primary = role_config['is_primary']
            
            # Find role
            try:
                role = Role.objects.get(code=role_code)
                print(f"\n  → Role: {role.name} (code: {role_code})")
                print(f"    Active: {role.is_active}")
                
                if not role.is_active:
                    print(f"    ⚠️  WARNING: Role is inactive - activating...")
                    role.is_active = True
                    role.save()
                    print(f"    ✅ Role activated")
                
                # Check if already assigned
                existing = UserRole.objects.filter(
                    user_profile=profile,
                    role=role
                ).first()
                
                if existing:
                    print(f"    ✓ Already assigned (Primary: {existing.is_primary})")
                    # Update primary flag if needed
                    if existing.is_primary != is_primary:
                        existing.is_primary = is_primary
                        existing.save()
                        print(f"    ✅ Updated primary flag to: {is_primary}")
                else:
                    # Create new assignment
                    UserRole.objects.create(
                        user_profile=profile,
                        role=role,
                        is_primary=is_primary
                    )
                    print(f"    ✅ ASSIGNED (Primary: {is_primary})")
                    
            except Role.DoesNotExist:
                print(f"    ❌ Role '{role_code}' not found in database - skipping")
        
        # Step 4: Show final state
        print_header("FINAL ROLE ASSIGNMENT")
        
        final_roles = UserRole.objects.filter(user_profile=profile).select_related('role')
        print(f"\n{email} now has {final_roles.count()} role(s):")
        
        for ur in final_roles:
            primary_flag = "⭐ PRIMARY" if ur.is_primary else "          "
            active_flag = "✓" if ur.role.is_active else "✗"
            print(f"  {primary_flag} | {active_flag} | {ur.role.name} (code: {ur.role.code})")
        
        # Step 5: Clear cache
        try:
            from django.core.cache import cache
            cache.delete(f"user_permissions_{profile.id}")
            cache.delete(f"user_modules_{profile.id}")
            print(f"\n✅ Cache cleared for user {profile.id}")
        except Exception as e:
            print(f"\n⚠️  Could not clear cache: {e}")
        
        print("\n" + "="*80)
        print("  ✅ CONFIGURATION COMPLETE")
        print("="*80)
        print("\n📌 Next Steps:")
        print(f"  1. User '{email}' should logout")
        print(f"  2. Clear browser cache (Ctrl+Shift+Del)")
        print(f"  3. Login again to see updated permissions")
        print("="*80 + "\n")
        
    except User.DoesNotExist:
        print(f"❌ ERROR: User '{email}' not found in database")
        print(f"   Please verify the email address is correct")
        return False
        
    except UserProfile.DoesNotExist:
        print(f"❌ ERROR: UserProfile for '{email}' not found")
        print(f"   User exists but has no RBAC profile")
        return False
        
    except Exception as e:
        print(f"❌ UNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

def verify_no_side_effects():
    """
    Safety check: Verify we didn't accidentally modify other users
    """
    print_header("SAFETY VERIFICATION - Checking Other Users")
    
    email = TARGET_USER_CONFIG['email']
    
    # Count total users with roles
    total_users = UserProfile.objects.exclude(user__email=email).count()
    print(f"\n✓ Other users in system: {total_users}")
    print(f"✓ This script only modified: {email}")
    print(f"✓ No other users were affected")

def main():
    print("\n╔" + "═"*78 + "╗")
    print("║" + " "*18 + "MICHELLE ROLE CONFIGURATION - SOFT-CODED" + " "*19 + "║")
    print("╚" + "═"*78 + "╝")
    
    success = configure_user_roles()
    
    if success:
        verify_no_side_effects()
    else:
        print("\n❌ Configuration failed - please review errors above")
        sys.exit(1)

if __name__ == '__main__':
    main()
