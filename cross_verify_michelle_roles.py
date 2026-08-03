"""
Cross-Verify and Fix Michelle's Role Configuration
Compares local vs production and auto-fixes using soft-coding
"""

import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Role, UserRole
from django.core.cache import cache

User = get_user_model()

# ============================================================================
# SOFT-CODED CONFIGURATION
# ============================================================================
TARGET_USER_EMAIL = 'michelle.dehoedt@rejlers.ae'

REQUIRED_ROLES = [
    {
        'code': 'default',
        'is_primary': False,
        'description': 'Basic platform access'
    },
    {
        'code': 'hr_admin',
        'is_primary': True,
        'description': 'HR & Payroll Administrator'
    }
]

# ============================================================================


def print_header(text):
    print("\n" + "="*80)
    print(f"  {text}")
    print("="*80 + "\n")


def get_environment():
    """Detect current environment"""
    env = os.getenv('ENVIRONMENT', 'local')
    return env.upper()


def check_michelle_current_state():
    """Check Michelle's current role configuration"""
    print_header("STEP 1: CHECKING MICHELLE'S CURRENT STATE")
    
    try:
        user = User.objects.get(email=TARGET_USER_EMAIL)
        profile = UserProfile.objects.get(user=user)
        
        print(f"✅ User Found: {user.email}")
        print(f"   Profile ID: {profile.id}")
        print(f"   User ID: {user.id}")
        
        # Check UserRole records
        user_roles = UserRole.objects.filter(user_profile=profile)
        print(f"\n📋 Current UserRole Records: {user_roles.count()}")
        
        role_map = {}
        for ur in user_roles:
            role_map[ur.role.code] = {
                'name': ur.role.name,
                'is_primary': ur.is_primary,
                'is_active': ur.role.is_active,
                'role_id': ur.role.id
            }
            print(f"   • {ur.role.name}")
            print(f"     - Code: {ur.role.code}")
            print(f"     - Primary: {ur.is_primary}")
            print(f"     - Active: {ur.role.is_active}")
        
        return {
            'user': user,
            'profile': profile,
            'role_map': role_map,
            'total_roles': user_roles.count()
        }
        
    except User.DoesNotExist:
        print(f"❌ ERROR: User {TARGET_USER_EMAIL} not found")
        return None
    except UserProfile.DoesNotExist:
        print(f"❌ ERROR: UserProfile for {TARGET_USER_EMAIL} not found")
        return None


def check_required_roles_exist():
    """Verify all required roles exist in database"""
    print_header("STEP 2: VERIFYING REQUIRED ROLES EXIST")
    
    roles_status = {}
    all_exist = True
    
    for role_config in REQUIRED_ROLES:
        code = role_config['code']
        try:
            role = Role.objects.get(code=code, is_active=True)
            roles_status[code] = {
                'exists': True,
                'role': role,
                'name': role.name
            }
            print(f"✅ Role '{code}' exists: {role.name}")
        except Role.DoesNotExist:
            roles_status[code] = {'exists': False, 'role': None}
            print(f"❌ Role '{code}' NOT FOUND or inactive")
            all_exist = False
    
    return roles_status, all_exist


def compare_and_identify_gaps(current_state, roles_status):
    """Compare current state with required configuration"""
    print_header("STEP 3: GAP ANALYSIS")
    
    gaps = []
    current_role_codes = set(current_state['role_map'].keys())
    required_role_codes = set(r['code'] for r in REQUIRED_ROLES)
    
    # Missing roles
    missing = required_role_codes - current_role_codes
    if missing:
        print(f"❌ MISSING ROLES: {', '.join(missing)}")
        for code in missing:
            role_config = next(r for r in REQUIRED_ROLES if r['code'] == code)
            gaps.append({
                'type': 'missing',
                'code': code,
                'config': role_config
            })
    
    # Extra roles (unexpected)
    extra = current_role_codes - required_role_codes
    if extra:
        print(f"⚠️  EXTRA ROLES (not in config): {', '.join(extra)}")
    
    # Wrong primary flag
    for code in current_role_codes & required_role_codes:
        required_config = next(r for r in REQUIRED_ROLES if r['code'] == code)
        current_config = current_state['role_map'][code]
        
        if current_config['is_primary'] != required_config['is_primary']:
            print(f"⚠️  WRONG PRIMARY FLAG for '{code}':")
            print(f"   Current: {current_config['is_primary']}")
            print(f"   Expected: {required_config['is_primary']}")
            gaps.append({
                'type': 'wrong_primary',
                'code': code,
                'config': required_config
            })
    
    if not gaps and not extra:
        print("✅ NO GAPS - Configuration is correct!")
    
    return gaps


def fix_michelle_roles(profile, gaps, roles_status):
    """Apply fixes based on identified gaps"""
    print_header("STEP 4: APPLYING FIXES")
    
    if not gaps:
        print("✅ No fixes needed - configuration is already correct")
        return True
    
    print(f"🔧 Applying {len(gaps)} fix(es)...\n")
    
    for gap in gaps:
        code = gap['code']
        config = gap['config']
        
        if gap['type'] == 'missing':
            # Add missing role
            if roles_status[code]['exists']:
                role = roles_status[code]['role']
                user_role, created = UserRole.objects.get_or_create(
                    user_profile=profile,
                    role=role,
                    defaults={'is_primary': config['is_primary']}
                )
                
                if created:
                    print(f"✅ ADDED role '{code}' ({role.name})")
                    print(f"   - Primary: {config['is_primary']}")
                else:
                    print(f"⚠️  Role '{code}' already exists (no action)")
            else:
                print(f"❌ Cannot add '{code}' - role does not exist in database")
        
        elif gap['type'] == 'wrong_primary':
            # Fix primary flag
            try:
                user_role = UserRole.objects.get(
                    user_profile=profile,
                    role__code=code
                )
                old_value = user_role.is_primary
                user_role.is_primary = config['is_primary']
                user_role.save()
                
                print(f"✅ FIXED primary flag for '{code}'")
                print(f"   - Changed: {old_value} → {config['is_primary']}")
                
            except UserRole.DoesNotExist:
                print(f"❌ Cannot fix '{code}' - UserRole not found")
    
    return True


def clear_user_cache(user):
    """Clear cached permissions for user"""
    print_header("STEP 5: CLEARING CACHE")
    
    cache_keys = [
        f'user_permissions_{user.id}',
        f'user_modules_{user.id}',
        f'user_roles_{user.id}',
        f'rbac_permissions_{user.id}',
        f'user_profile_{user.id}',
    ]
    
    cleared = 0
    for key in cache_keys:
        if cache.delete(key):
            cleared += 1
            print(f"✅ Cleared cache: {key}")
    
    print(f"\n🔄 Total cache keys cleared: {cleared}")


def verify_final_state(profile):
    """Verify the configuration after fixes"""
    print_header("STEP 6: VERIFICATION")
    
    user_roles = UserRole.objects.filter(user_profile=profile)
    
    print(f"✅ Final Role Count: {user_roles.count()}")
    print(f"\n📋 Final Configuration:")
    
    for ur in user_roles:
        print(f"\n   • {ur.role.name} (code: {ur.role.code})")
        print(f"     - Primary: {ur.is_primary}")
        print(f"     - Active: {ur.role.is_active}")
        print(f"     - Modules: {ur.role.modules.count()}")
    
    # Check against requirements
    current_codes = set(ur.role.code for ur in user_roles)
    required_codes = set(r['code'] for r in REQUIRED_ROLES)
    
    if current_codes >= required_codes:
        print("\n✅ SUCCESS: All required roles are present!")
        return True
    else:
        missing = required_codes - current_codes
        print(f"\n❌ STILL MISSING: {', '.join(missing)}")
        return False


def main():
    print("\n" + "╔" + "="*78 + "╗")
    print("║" + " "*20 + "MICHELLE ROLE CROSS-VERIFICATION" + " "*26 + "║")
    print("╚" + "="*78 + "╝")
    
    env = get_environment()
    print(f"\n🌍 Environment: {env}")
    print(f"👤 Target User: {TARGET_USER_EMAIL}")
    print(f"🎯 Required Roles: {len(REQUIRED_ROLES)}")
    
    # Step 1: Check current state
    current_state = check_michelle_current_state()
    if not current_state:
        print("\n❌ FATAL: Cannot proceed without user profile")
        return
    
    # Step 2: Verify required roles exist
    roles_status, all_exist = check_required_roles_exist()
    if not all_exist:
        print("\n❌ ERROR: Some required roles do not exist in database")
        print("   Action: Ensure migrations are applied (python manage.py migrate)")
        return
    
    # Step 3: Gap analysis
    gaps = compare_and_identify_gaps(current_state, roles_status)
    
    # Step 4: Apply fixes if needed
    if gaps:
        fix_michelle_roles(current_state['profile'], gaps, roles_status)
    
    # Step 5: Clear cache
    clear_user_cache(current_state['user'])
    
    # Step 6: Verify final state
    success = verify_final_state(current_state['profile'])
    
    print("\n" + "="*80)
    if success:
        print("  ✅ VERIFICATION AND FIX COMPLETE")
        print("\n  📌 Next Steps:")
        print("     1. Michelle should logout and login again")
        print("     2. Verify she can access both Default and HR features")
        print("     3. Check /admin/roles page to assign additional roles if needed")
    else:
        print("  ❌ VERIFICATION FAILED - Manual intervention required")
    print("="*80 + "\n")


if __name__ == '__main__':
    main()
