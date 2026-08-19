"""
Verify User Module Access
Smart command to diagnose RBAC module assignment issues
"""
import logging
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from apps.rbac.models import Module, Role, RoleModule, UserProfile, UserRole

User = get_user_model()
logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Verify module access for users and diagnose RBAC issues'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--email',
            type=str,
            help='Check specific user email',
        )
        parser.add_argument(
            '--module',
            type=str,
            help='Check specific module code',
        )

    def handle(self, *args, **options):
        email = options.get('email')
        module_code = options.get('module')
        
        self.stdout.write("\n" + "="*80)
        self.stdout.write(self.style.SUCCESS("🔍 RBAC MODULE ACCESS VERIFIER"))
        self.stdout.write("="*80 + "\n")
        
        if email:
            # Check specific user
            try:
                user = User.objects.get(email=email, is_active=True)
                self._verify_user(user, module_code)
            except User.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"❌ User '{email}' not found"))
        else:
            # Check all users
            users = User.objects.filter(is_active=True)
            self.stdout.write(f"📊 Checking {users.count()} active users...\n")
            
            issues_found = 0
            for user in users:
                if not self._verify_user(user, module_code, silent=True):
                    issues_found += 1
            
            self.stdout.write(f"\n{'='*80}")
            if issues_found > 0:
                self.stdout.write(self.style.WARNING(f"⚠️  Found {issues_found} user(s) with module assignment issues"))
                self.stdout.write("\nRun: python manage.py fix_rbac_assignments")
                self.stdout.write("To automatically fix these issues\n")
            else:
                self.stdout.write(self.style.SUCCESS("✅ All users have proper module assignments\n"))
    
    def _verify_user(self, user, module_code=None, silent=False):
        """
        Verify user's module access
        Returns True if OK, False if issues found
        """
        try:
            profile = user.rbac_profile
        except UserProfile.DoesNotExist:
            if not silent:
                self.stdout.write(self.style.ERROR(f"❌ {user.email}: No profile found"))
            return False
        
        # Get user's roles
        user_roles = UserRole.objects.filter(user_profile=profile)
        
        if not user_roles.exists():
            if not silent:
                self.stdout.write(self.style.WARNING(f"⚠️  {user.email}: No roles assigned"))
            return True  # Not an issue if no roles
        
        if not silent:
            self.stdout.write(f"\n👤 {user.email}")
            self.stdout.write(f"   Profile ID: {profile.id}")
            self.stdout.write(f"   Organization: {profile.organization.name}")
            self.stdout.write(f"   Status: {profile.status}")
        
        # Show roles
        if not silent:
            self.stdout.write(f"\n   📋 Assigned Roles ({user_roles.count()}):")
        
        for user_role in user_roles:
            role = user_role.role
            if not silent:
                primary = "PRIMARY" if user_role.is_primary else ""
                self.stdout.write(f"      • {role.name} (level {role.level}) {primary}")
        
        # Get modules through roles (expected)
        expected_modules = set()
        if not silent:
            self.stdout.write(f"\n   📦 Expected Modules (via RoleModule):")
        
        for user_role in user_roles:
            role = user_role.role
            role_modules = RoleModule.objects.filter(role=role).select_related('module')
            
            if not silent:
                if role_modules.exists():
                    self.stdout.write(f"      From {role.name}:")
                    for rm in role_modules:
                        self.stdout.write(f"         • {rm.module.code} - {rm.module.name}")
                        expected_modules.add(rm.module.code)
                else:
                    self.stdout.write(f"      From {role.name}: NONE linked")
        
        # Get actual accessible modules (via profile.get_all_modules())
        actual_modules = profile.get_all_modules()
        actual_module_codes = set(m.code for m in actual_modules)
        
        if not silent:
            self.stdout.write(f"\n   ✅ Actual Accessible Modules ({len(actual_module_codes)}):")
            if actual_module_codes:
                for code in sorted(actual_module_codes):
                    self.stdout.write(f"      • {code}")
            else:
                self.stdout.write("      NONE")
        
        # Compare expected vs actual
        missing = expected_modules - actual_module_codes
        unexpected = actual_module_codes - expected_modules
        
        has_issues = False
        
        if missing:
            has_issues = True
            if not silent:
                self.stdout.write(self.style.WARNING(f"\n   ⚠️  MISSING MODULES ({len(missing)}):"))
                for code in sorted(missing):
                    self.stdout.write(f"      • {code}")
        
        if unexpected:
            if not silent:
                self.stdout.write(self.style.NOTICE(f"\n   ℹ️  Unexpected modules ({len(unexpected)}):"))
                for code in sorted(unexpected):
                    self.stdout.write(f"      • {code}")
        
        if module_code:
            # Check specific module
            if not silent:
                self.stdout.write(f"\n   🔍 Checking module: {module_code}")
            
            has_access = profile.has_module_access(module_code)
            
            if not silent:
                if has_access:
                    self.stdout.write(self.style.SUCCESS(f"      ✅ User HAS access to {module_code}"))
                else:
                    self.stdout.write(self.style.ERROR(f"      ❌ User DOES NOT have access to {module_code}"))
        
        if not silent:
            if not has_issues and not missing:
                self.stdout.write(self.style.SUCCESS(f"\n   ✅ All expected modules are properly assigned"))
            elif has_issues:
                self.stdout.write(self.style.WARNING(f"\n   🔧 NEEDS FIX: Run 'python manage.py fix_rbac_assignments --email {user.email}'"))
        
        return not has_issues
