"""
Preprod Onboarding Access Verification Script
Checks if lira.viaga@rejlers.ae has proper access to hr_onboarding module.

USAGE: Run inside Docker backend container on PREPROD environment
    docker exec <backend-container> python verify_onboarding_access.py
"""
import os
import sys
import django

# Django setup
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.rbac.models import Role, UserProfile, UserRole, RoleModule, Module
from apps.rbac.rbac_config import is_module_enabled, SENSITIVE_MODULE_CODES
from django.core.cache import cache

User = get_user_model()

# ANSI color codes for terminal output
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    END = '\033[0m'
    BOLD = '\033[1m'

def print_header(text):
    print(f"\n{Colors.HEADER}{'='*80}")
    print(f"{text.center(80)}")
    print(f"{'='*80}{Colors.END}\n")

def print_section(text):
    print(f"\n{Colors.CYAN}{'─'*80}")
    print(f"{text}")
    print(f"{'─'*80}{Colors.END}")

def print_success(text):
    print(f"{Colors.GREEN}✓ {text}{Colors.END}")

def print_error(text):
    print(f"{Colors.RED}✗ {text}{Colors.END}")

def print_warning(text):
    print(f"{Colors.YELLOW}⚠ {text}{Colors.END}")

def print_info(text):
    print(f"{Colors.BLUE}• {text}{Colors.END}")

def main():
    print_header("PREPROD: Onboarding Access Verification")
    
    # Configuration
    USER_EMAIL = 'lira.viaga@rejlers.ae'
    REQUIRED_MODULE = 'hr_onboarding'
    
    print_info(f"User: {USER_EMAIL}")
    print_info(f"Required Module: {REQUIRED_MODULE}")
    print_info(f"Feature: 4.3 Onboarding | Offboarding")
    
    # Step 1: Check if user exists
    print_section("Step 1: Verify User Exists")
    try:
        user = User.objects.get(email=USER_EMAIL)
        profile = user.rbac_profile
        print_success(f"User found: {user.email}")
        print_info(f"  Name: {user.first_name} {user.last_name}")
        print_info(f"  Active: {user.is_active}")
        print_info(f"  Superuser: {user.is_superuser}")
    except User.DoesNotExist:
        print_error(f"User not found: {USER_EMAIL}")
        return False
    except Exception as e:
        print_error(f"Error: {str(e)}")
        return False
    
    # Step 2: Check user's roles
    print_section("Step 2: Check User's Role Assignments")
    user_roles = UserRole.objects.filter(user_profile=profile).select_related('role')
    
    if not user_roles.exists():
        print_error("User has NO ROLES assigned!")
        print_warning("Solution: Assign a role to this user via /admin/users")
        return False
    
    print_success(f"User has {user_roles.count()} role(s) assigned:")
    onboarding_role = None
    for ur in user_roles:
        role = ur.role
        is_primary = "PRIMARY" if ur.is_primary else ""
        print_info(f"  • {role.name} (code: {role.code}, level: {role.level}) {is_primary}")
        if 'onboarding' in role.code.lower() or 'onboarding' in role.name.lower():
            onboarding_role = role
            print_success(f"    → Found Onboarding-related role!")
    
    # Step 3: Check if hr_onboarding module exists
    print_section("Step 3: Verify Module Exists in Database")
    try:
        onboarding_module = Module.objects.get(code=REQUIRED_MODULE)
        print_success(f"Module found: {onboarding_module.name}")
        print_info(f"  Code: {onboarding_module.code}")
        print_info(f"  Active: {onboarding_module.is_active}")
        
        # Check feature flag
        is_enabled = is_module_enabled(REQUIRED_MODULE)
        if is_enabled:
            print_success(f"  Feature flag: Enabled ✓")
        else:
            print_error(f"  Feature flag: DISABLED!")
            print_warning("  Solution: Set MODULE_FEATURE_FLAGS['hr_onboarding'] = True in rbac_config.py")
            return False
        
        # Check if sensitive
        if REQUIRED_MODULE in SENSITIVE_MODULE_CODES:
            print_warning(f"  This is a SENSITIVE module (requires special permissions)")
        
    except Module.DoesNotExist:
        print_error(f"Module NOT FOUND in database: {REQUIRED_MODULE}")
        print_warning("Solution: Run migrations or seed_rbac to create this module")
        return False
    
    # Step 4: Check modules assigned to user's roles
    print_section("Step 4: Check Modules Assigned to User's Roles")
    
    all_assigned_modules = set()
    has_onboarding_module = False
    
    for ur in user_roles:
        role = ur.role
        role_modules = RoleModule.objects.filter(role=role).select_related('module')
        module_count = role_modules.count()
        
        print_info(f"Role: {role.name} ({module_count} modules)")
        
        if module_count == 0:
            print_warning(f"  → This role has NO MODULES assigned!")
            print_warning(f"     Users with this role cannot access any features")
        else:
            for rm in role_modules:
                module = rm.module
                all_assigned_modules.add(module.code)
                if module.code == REQUIRED_MODULE:
                    has_onboarding_module = True
                    print_success(f"  ✓ {module.code} - {module.name}")
                else:
                    print_info(f"  • {module.code} - {module.name}")
    
    # Step 5: Check get_all_modules() result
    print_section("Step 5: Check get_all_modules() Result (With Cache Clear)")
    
    # Clear cache to get fresh data
    cache_key = f'user_modules_{profile.id}'
    cache.delete(cache_key)
    print_info("Cache cleared for fresh data")
    
    accessible_modules = profile.get_all_modules()
    accessible_codes = [m.code for m in accessible_modules]
    
    print_success(f"User can access {len(accessible_modules)} modules:")
    
    if REQUIRED_MODULE in accessible_codes:
        print_success(f"✓ '{REQUIRED_MODULE}' IS ACCESSIBLE!")
        for m in accessible_modules:
            if m.code == REQUIRED_MODULE:
                print_success(f"  → {m.code} - {m.name}")
    else:
        print_error(f"✗ '{REQUIRED_MODULE}' IS NOT ACCESSIBLE!")
        print_info("User's accessible modules:")
        for m in accessible_modules:
            print_info(f"  • {m.code} - {m.name}")
    
    # Final diagnosis
    print_section("DIAGNOSIS SUMMARY")
    
    if REQUIRED_MODULE in accessible_codes:
        print_success("SUCCESS: User has access to hr_onboarding module!")
        print_info("If user still cannot see the feature:")
        print_info("  1. User must LOGOUT and LOGIN again")
        print_info("  2. Or clear browser cache and refresh (F5)")
        print_info("  3. Check browser console (F12) for errors")
        print_info("  4. Verify preprod frontend URL: https://frontend-cyan-eta-q169h70uw0.vercel.app")
        return True
    else:
        print_error("PROBLEM: User does NOT have access to hr_onboarding module!")
        print_warning("\nPossible causes:")
        
        if not onboarding_role:
            print_warning("  1. User doesn't have an 'Onboarding' role assigned")
            print_info("     Solution: Create or assign the Onboarding role via /admin/users")
        elif onboarding_role:
            role_has_module = RoleModule.objects.filter(
                role=onboarding_role, 
                module__code=REQUIRED_MODULE
            ).exists()
            
            if not role_has_module:
                print_warning(f"  2. Role '{onboarding_role.name}' exists but hr_onboarding module is NOT assigned")
                print_info("     Solution:")
                print_info(f"       • Open: https://[preprod-url]/admin/roles")
                print_info(f"       • Select role: {onboarding_role.name}")
                print_info(f"       • Go to 'Modules' tab")
                print_info(f"       • Toggle ON: hr_onboarding (Onboarding | Offboarding)")
                print_info(f"       • User must logout/login after change")
            else:
                print_warning("  3. Module is assigned but something else is wrong")
                print_info("     Check:")
                print_info("       • Module.is_active = True")
                print_info("       • Role.is_active = True")
                print_info("       • Feature flag enabled")
        
        return False

if __name__ == '__main__':
    try:
        success = main()
        sys.exit(0 if success else 1)
    except Exception as e:
        print_error(f"Unexpected error: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
