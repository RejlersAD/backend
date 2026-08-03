"""
Django Management Command: Diagnose User RBAC Access
Smart diagnostic to check why a user can access modules they shouldn't have access to
"""

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Role, Module
from apps.rbac.rbac_config import (
    DEFAULT_ROLE_MODULES,
    ROLE_MODULE_POLICY,
    ADMIN_ROLE_CODES,
    SENSITIVE_MODULE_CODES
)

User = get_user_model()


class Command(BaseCommand):
    help = 'Diagnose RBAC access issues for a specific user'

    def add_arguments(self, parser):
        parser.add_argument(
            '--email',
            type=str,
            required=True,
            help='User email to diagnose',
        )
        parser.add_argument(
            '--fix',
            action='store_true',
            help='Automatically fix issues by syncing user to role-based modules',
        )

    def handle(self, *args, **options):
        email = options['email']
        auto_fix = options['fix']
        
        self.stdout.write("=" * 80)
        self.stdout.write(self.style.SUCCESS(f"  RBAC DIAGNOSTIC FOR: {email}"))
        self.stdout.write("=" * 80)
        
        # Step 1: Check if user exists
        self.stdout.write("\n📧 Step 1: Checking user exists...")
        try:
            user = User.objects.get(email=email)
            self.stdout.write(self.style.SUCCESS(f"  ✅ User found: {user.first_name} {user.last_name} ({user.email})"))
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"  ❌ User not found: {email}"))
            return
        
        # Step 2: Check RBAC profile
        self.stdout.write("\n👤 Step 2: Checking RBAC profile...")
        try:
            profile = user.rbac_profile
            self.stdout.write(self.style.SUCCESS(f"  ✅ RBAC profile exists (ID: {profile.id})"))
        except UserProfile.DoesNotExist:
            self.stdout.write(self.style.ERROR("  ❌ No RBAC profile found"))
            self.stdout.write("  FIX: Run 'python manage.py create_missing_profiles' to create profile")
            return
        
        # Step 3: Check roles
        self.stdout.write("\n🎭 Step 3: Checking assigned roles...")
        user_roles = profile.roles.filter(is_active=True)
        if not user_roles.exists():
            self.stdout.write(self.style.WARNING("  ⚠️  No roles assigned!"))
            self.stdout.write("  FIX: User should have at least 'default' role")
        else:
            self.stdout.write(f"  ✅ {user_roles.count()} role(s) assigned:")
            for role in user_roles:
                is_admin = role.code in ADMIN_ROLE_CODES
                badge = "🔴 ADMIN" if is_admin else "🟢 USER"
                self.stdout.write(f"     {badge} {role.name} (code: {role.code}, level: {role.level})")
        
        # Step 4: Check expected modules based on roles
        self.stdout.write("\n📦 Step 4: Computing expected modules from roles...")
        expected_modules = set()
        for role in user_roles:
            role_modules = ROLE_MODULE_POLICY.get(role.code, [])
            expected_modules.update(role_modules)
        
        self.stdout.write(f"  Expected modules: {len(expected_modules)}")
        if expected_modules:
            # Group by category for readability
            engineering = [m for m in expected_modules if any(x in m for x in ['process', 'pid', 'pfd', 'electrical', 'instrument', 'mechanical', 'civil', 'piping'])]
            admin_mods = [m for m in expected_modules if any(x in m for x in ['admin', 'user_mgmt', 'org_settings', 'audit', 'role_access'])]
            business = [m for m in expected_modules if any(x in m for x in ['finance', 'procurement', 'sales'])]
            common = [m for m in expected_modules if m not in engineering + admin_mods + business]
            
            if engineering:
                self.stdout.write(f"     🔧 Engineering: {len(engineering)} modules")
            if common:
                self.stdout.write(f"     🌐 Common: {len(common)} modules")
            if admin_mods:
                self.stdout.write(f"     👨‍💼 Admin: {len(admin_mods)} modules")
            if business:
                self.stdout.write(f"     💼 Business: {len(business)} modules")
        
        # Step 5: Check actual modules assigned
        self.stdout.write("\n📋 Step 5: Checking actual modules assigned to profile...")
        actual_modules = profile.modules.filter(is_active=True)
        self.stdout.write(f"  Actual modules: {actual_modules.count()}")
        
        # Step 6: Compare expected vs actual
        self.stdout.write("\n🔍 Step 6: Comparing expected vs actual...")
        actual_codes = set(actual_modules.values_list('code', flat=True))
        expected_codes = expected_modules
        
        extra_modules = actual_codes - expected_codes
        missing_modules = expected_codes - actual_codes
        
        if not extra_modules and not missing_modules:
            self.stdout.write(self.style.SUCCESS("  ✅ PERFECT MATCH - user has exactly the right modules"))
        else:
            if extra_modules:
                self.stdout.write(self.style.ERROR(f"\n  ❌ EXTRA MODULES ({len(extra_modules)}) - User has access they shouldn't:"))
                for module_code in sorted(extra_modules):
                    module = actual_modules.get(code=module_code)
                    is_sensitive = module_code in SENSITIVE_MODULE_CODES
                    is_admin_mod = any(x in module_code for x in ['admin', 'user_mgmt', 'org_settings'])
                    is_finance = 'finance' in module_code
                    is_procurement = 'procurement' in module_code
                    
                    badge = ""
                    if is_sensitive:
                        badge = "🔴 SENSITIVE"
                    elif is_admin_mod:
                        badge = "🟠 ADMIN"
                    elif is_finance:
                        badge = "💰 FINANCE"
                    elif is_procurement:
                        badge = "🛒 PROCUREMENT"
                    else:
                        badge = "⚠️  EXTRA"
                    
                    self.stdout.write(f"       {badge} {module.name} ({module_code})")
            
            if missing_modules:
                self.stdout.write(self.style.WARNING(f"\n  ⚠️  MISSING MODULES ({len(missing_modules)}) - User should have but doesn't:"))
                for module_code in sorted(missing_modules):
                    try:
                        module = Module.objects.get(code=module_code, is_active=True)
                        self.stdout.write(f"       ➕ {module.name} ({module_code})")
                    except Module.DoesNotExist:
                        self.stdout.write(f"       ❓ {module_code} (module doesn't exist in DB)")
        
        # Step 7: Show problematic modules in detail
        if extra_modules:
            self.stdout.write("\n⚠️  Step 7: Analyzing problematic access...")
            
            admin_access = [m for m in extra_modules if any(x in m for x in ['admin', 'user_mgmt', 'role_access', 'org_settings', 'audit'])]
            finance_access = [m for m in extra_modules if 'finance' in m]
            hr_access = [m for m in extra_modules if any(x in m for x in ['hr_management', 'payroll', 'timesheet', 'hr_onboarding']) and m != 'hr_self_service']
            procurement_access = [m for m in extra_modules if 'procurement' in m]
            
            if admin_access:
                self.stdout.write(f"\n  🔴 ADMIN ACCESS ISSUE:")
                self.stdout.write(f"     User can access {len(admin_access)} admin module(s): {', '.join(admin_access)}")
                self.stdout.write(f"     This allows: User management, system settings, audit logs")
                self.stdout.write(f"     Risk Level: HIGH")
            
            if finance_access:
                self.stdout.write(f"\n  💰 FINANCE ACCESS ISSUE:")
                self.stdout.write(f"     User can access {len(finance_access)} finance module(s): {', '.join(finance_access)}")
                self.stdout.write(f"     This allows: View invoices, billing, financial data")
                self.stdout.write(f"     Risk Level: MEDIUM")
            
            if hr_access:
                self.stdout.write(f"\n  🔐 HR ACCESS ISSUE:")
                self.stdout.write(f"     User can access {len(hr_access)} sensitive HR module(s): {', '.join(hr_access)}")
                self.stdout.write(f"     This allows: View employee salaries, payroll, personal data")
                self.stdout.write(f"     Risk Level: CRITICAL")
            
            if procurement_access:
                self.stdout.write(f"\n  🛒 PROCUREMENT ACCESS ISSUE:")
                self.stdout.write(f"     User can access {len(procurement_access)} procurement module(s): {', '.join(procurement_access)}")
                self.stdout.write(f"     This allows: Create POs, manage vendors, requisitions")
                self.stdout.write(f"     Risk Level: MEDIUM")
        
        # Step 8: Root cause analysis
        self.stdout.write("\n🔬 Step 8: Root cause analysis...")
        
        is_default_only = user_roles.count() == 1 and user_roles.first().code == 'default'
        has_admin_role = any(role.code in ADMIN_ROLE_CODES for role in user_roles)
        
        if is_default_only and extra_modules:
            self.stdout.write(self.style.ERROR("\n  ROOT CAUSE: User has 'default' role only, but modules are directly assigned"))
            self.stdout.write("  LIKELY REASON: Legacy per-user module assignment or manual override")
            self.stdout.write("  SOLUTION: Remove extra modules and sync to role-based policy")
        elif has_admin_role and extra_modules:
            self.stdout.write(self.style.WARNING("\n  ROOT CAUSE: User has admin role which grants many modules"))
            self.stdout.write("  SOLUTION: If user shouldn't be admin, change role to 'default'")
        elif not user_roles.exists():
            self.stdout.write(self.style.ERROR("\n  ROOT CAUSE: User has no roles assigned"))
            self.stdout.write("  SOLUTION: Assign 'default' role")
        
        # Step 9: Auto-fix if requested
        if auto_fix:
            self.stdout.write("\n🔧 Step 9: AUTO-FIX ENABLED - Syncing user to role-based modules...")
            
            # Clear all direct module assignments
            profile.modules.clear()
            self.stdout.write("  ✓ Cleared all direct module assignments")
            
            # Re-apply role-based modules
            for role in user_roles:
                role_modules = ROLE_MODULE_POLICY.get(role.code, [])
                for module_code in role_modules:
                    try:
                        module = Module.objects.get(code=module_code, is_active=True)
                        profile.modules.add(module)
                    except Module.DoesNotExist:
                        pass
            
            profile.save()
            
            new_count = profile.modules.filter(is_active=True).count()
            self.stdout.write(self.style.SUCCESS(f"  ✅ Synced {new_count} modules based on roles"))
            self.stdout.write(self.style.SUCCESS(f"\n✅ AUTO-FIX COMPLETE - User now has role-based access only"))
        else:
            # Show fix command
            if extra_modules or missing_modules:
                self.stdout.write("\n💡 Step 9: Recommended fix...")
                self.stdout.write(f"\n  Run this command to fix:")
                self.stdout.write(self.style.WARNING(f"  python manage.py diagnose_user_rbac --email {email} --fix"))
                self.stdout.write(f"\n  Or run for ALL users:")
                self.stdout.write(self.style.WARNING(f"  python manage.py sync_all_users_to_roles"))
        
        self.stdout.write("\n" + "=" * 80)
        self.stdout.write(self.style.SUCCESS("  DIAGNOSTIC COMPLETE"))
        self.stdout.write("=" * 80)
