"""
Custom Role Diagnostic Tool
Verifies that custom roles have modules assigned and users can access features.

SOFT-CODED: Reads configuration from rbac_config.py
Usage: python diagnose_custom_role.py [role_code] [user_email]
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
from apps.rbac.rbac_config import is_module_enabled
from django.core.cache import cache
from colorama import init, Fore, Style

init(autoreset=True)

User = get_user_model()


def print_header(text):
    """Print formatted header"""
    print(f"\n{Fore.YELLOW}{'=' * 80}")
    print(f"{Fore.YELLOW}{text.center(80)}")
    print(f"{Fore.YELLOW}{'=' * 80}\n")


def print_section(text):
    """Print formatted section"""
    print(f"\n{Fore.CYAN}{'─' * 80}")
    print(f"{Fore.CYAN}{text}")
    print(f"{Fore.CYAN}{'─' * 80}")


def check_role(role_code):
    """Check if role exists and has modules assigned"""
    print_section(f"Checking Role: {role_code}")
    
    try:
        role = Role.objects.get(code=role_code)
        print(f"{Fore.GREEN}✓ Role found:")
        print(f"  Name: {role.name}")
        print(f"  Code: {role.code}")
        print(f"  Level: {role.level}")
        print(f"  System Role: {role.is_system_role}")
        print(f"  Active: {role.is_active}")
        print(f"  User Count: {UserRole.objects.filter(role=role).count()}")
        
        # Check modules
        role_modules = RoleModule.objects.filter(role=role).select_related('module')
        module_count = role_modules.count()
        
        print(f"\n{Fore.CYAN}Module Assignment:")
        if module_count == 0:
            print(f"{Fore.RED}✗ NO MODULES ASSIGNED TO THIS ROLE!")
            print(f"{Fore.YELLOW}  This is why users with this role can't see any features.")
            print(f"{Fore.YELLOW}  Solution: Assign modules via /admin/roles in the UI")
            return None
        
        print(f"{Fore.GREEN}✓ {module_count} modules assigned:")
        for rm in role_modules:
            module = rm.module
            enabled = is_module_enabled(module.code)
            status_icon = "✓" if enabled and module.is_active else "✗"
            status_color = Fore.GREEN if enabled and module.is_active else Fore.RED
            print(f"  {status_color}{status_icon} {module.code.ljust(30)} - {module.name}")
            if not enabled:
                print(f"    {Fore.YELLOW}⚠ Module disabled by feature flag")
            if not module.is_active:
                print(f"    {Fore.YELLOW}⚠ Module is inactive")
        
        return role
    
    except Role.DoesNotExist:
        print(f"{Fore.RED}✗ Role not found: {role_code}")
        print(f"{Fore.YELLOW}  Available roles:")
        for r in Role.objects.filter(is_active=True).order_by('name'):
            print(f"    • {r.code.ljust(30)} - {r.name}")
        return None


def check_user(user_email, role_code=None):
    """Check user's role assignments and accessible modules"""
    print_section(f"Checking User: {user_email}")
    
    try:
        user = User.objects.get(email=user_email)
        profile = user.rbac_profile
        
        print(f"{Fore.GREEN}✓ User found:")
        print(f"  Email: {user.email}")
        print(f"  Name: {user.first_name} {user.last_name}")
        print(f"  Superuser: {user.is_superuser}")
        print(f"  Active: {user.is_active}")
        
        # Check roles
        user_roles = UserRole.objects.filter(user_profile=profile).select_related('role')
        print(f"\n{Fore.CYAN}Assigned Roles ({user_roles.count()}):")
        
        has_target_role = False
        for ur in user_roles:
            is_primary = "PRIMARY" if ur.is_primary else ""
            is_target = "← TARGET ROLE" if role_code and ur.role.code == role_code else ""
            marker = f"{is_primary} {is_target}".strip()
            print(f"  • {ur.role.name.ljust(40)} (level {ur.role.level}) {marker}")
            if role_code and ur.role.code == role_code:
                has_target_role = True
        
        if role_code and not has_target_role:
            print(f"\n{Fore.RED}✗ User is NOT assigned to role '{role_code}'!")
            print(f"{Fore.YELLOW}  Solution: Assign user to role via /admin/users in the UI")
            return None
        
        # Check accessible modules
        print(f"\n{Fore.CYAN}Accessible Modules (via get_all_modules):")
        
        # Clear cache to get fresh data
        cache_key = f'user_modules_{profile.id}'
        cache.delete(cache_key)
        
        modules = profile.get_all_modules()
        if not modules:
            print(f"{Fore.RED}✗ NO MODULES ACCESSIBLE!")
            print(f"{Fore.YELLOW}  This user cannot access any features.")
            print(f"{Fore.YELLOW}  Possible causes:")
            print(f"{Fore.YELLOW}    1. Roles have no modules assigned")
            print(f"{Fore.YELLOW}    2. All modules disabled by feature flags")
            print(f"{Fore.YELLOW}    3. Roles are inactive")
            return None
        
        print(f"{Fore.GREEN}✓ {len(modules)} modules accessible:")
        for module in modules:
            print(f"  • {module.code.ljust(30)} - {module.name}")
        
        # Check cache
        print(f"\n{Fore.CYAN}Cache Status:")
        cached = cache.get(cache_key)
        if cached:
            print(f"{Fore.YELLOW}⚠ Modules are cached (TTL: 60 seconds)")
            print(f"{Fore.YELLOW}  Cache cleared for this diagnosis")
        else:
            print(f"{Fore.GREEN}✓ No cache (fresh data)")
        
        return profile
    
    except User.DoesNotExist:
        print(f"{Fore.RED}✗ User not found: {user_email}")
        return None
    except Exception as e:
        print(f"{Fore.RED}✗ Error: {str(e)}")
        return None


def suggest_fixes(role_code=None, user_email=None):
    """Suggest fixes based on diagnosis"""
    print_section("SOLUTIONS & RECOMMENDATIONS")
    
    print(f"{Fore.CYAN}1. Assign Modules to Role (If role has no modules):")
    print(f"   • Open: https://[your-domain]/admin/roles")
    print(f"   • Select the '{role_code}' role")
    print(f"   • Go to 'Modules' tab")
    print(f"   • Toggle ON the modules this role should access")
    print(f"   • Changes take effect immediately (users must refresh browser)")
    
    print(f"\n{Fore.CYAN}2. Assign Role to User (If user doesn't have role):")
    print(f"   • Open: https://[your-domain]/admin/users")
    print(f"   • Find user: {user_email}")
    print(f"   • Click 'Edit' button")
    print(f"   • Under 'Roles' section, select '{role_code}'")
    print(f"   • Save changes")
    print(f"   • User must logout and login again")
    
    print(f"\n{Fore.CYAN}3. Clear Module Cache (If changes not reflecting):")
    print(f"   • User must:")
    print(f"     → Refresh browser (F5)")
    print(f"     → Or logout and login again")
    print(f"   • Backend cache clears automatically after 60 seconds")
    
    print(f"\n{Fore.CYAN}4. Verify Module Feature Flags:")
    print(f"   • Check: backend/apps/rbac/rbac_config.py")
    print(f"   • Look for: MODULE_FEATURE_FLAGS")
    print(f"   • Ensure required modules are set to True")
    
    print(f"\n{Fore.GREEN}✓ SOFT-CODED DESIGN:")
    print(f"   • All configuration in: backend/apps/rbac/rbac_config.py")
    print(f"   • Single source of truth for RBAC rules")
    print(f"   • No hardcoded role/module checks in views")
    print(f"   • Easy to add/remove modules without DB migration")


def main():
    """Main diagnostic function"""
    print_header("RADAI - Custom Role Diagnostic Tool")
    
    # Parse arguments
    role_code = sys.argv[1] if len(sys.argv) > 1 else None
    user_email = sys.argv[2] if len(sys.argv) > 2 else None
    
    if not role_code and not user_email:
        print(f"{Fore.YELLOW}Usage: python diagnose_custom_role.py [role_code] [user_email]")
        print(f"{Fore.YELLOW}Example: python diagnose_custom_role.py onboarding lira.viaga@rejlers.ae")
        return
    
    # Run diagnostics
    role = None
    profile = None
    
    if role_code:
        role = check_role(role_code)
    
    if user_email:
        profile = check_user(user_email, role_code)
    
    # Suggest fixes
    if role_code or user_email:
        suggest_fixes(role_code, user_email)
    
    # Summary
    print_section("DIAGNOSIS COMPLETE")
    
    if role and profile:
        print(f"{Fore.GREEN}✓ Both role and user are configured correctly")
        print(f"{Fore.GREEN}  If user still can't see features:")
        print(f"{Fore.YELLOW}    1. User must refresh browser or re-login")
        print(f"{Fore.YELLOW}    2. Check browser console for errors (F12)")
        print(f"{Fore.YELLOW}    3. Verify backend logs for module access denials")
    elif role and not profile:
        print(f"{Fore.YELLOW}⚠ Role exists but user check failed")
    elif not role and profile:
        print(f"{Fore.YELLOW}⚠ User exists but role check failed")
    else:
        print(f"{Fore.RED}✗ Issues found - see solutions above")


if __name__ == '__main__':
    main()
