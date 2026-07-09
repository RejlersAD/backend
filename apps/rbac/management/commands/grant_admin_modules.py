"""
Django management command to grant admin modules to admin role
Ensures admin role has all 6 admin section modules

Usage:
    python manage.py grant_admin_modules
"""
from django.core.management.base import BaseCommand
from django.core.cache import cache
from apps.rbac.models import Role, Module, RoleModule
from apps.rbac.rbac_config import ALL_MODULES_CATALOGUE


class Command(BaseCommand):
    help = 'Grant all admin section modules to admin role (soft-coded)'

    def handle(self, *args, **options):
        self.stdout.write(f"\n{'='*70}")
        self.stdout.write(self.style.WARNING("GRANT ADMIN MODULES TO ADMIN ROLE"))
        self.stdout.write(f"{'='*70}\n")
        
        # Soft-coded admin module codes from rbac_config.py
        ADMIN_MODULE_CODES = [
            'admin_dashboard',
            'user_mgmt',
            'role_access_mgmt',
            'wrench_integration',
            'ai_champion',
            'enquiry_management',
        ]
        
        try:
            # Get admin role
            admin_role = Role.objects.get(code='admin')
            self.stdout.write(f"📋 Role: {admin_role.name} ({admin_role.code}) - Level {admin_role.level}\n")
            
            # Get admin modules from catalogue (soft-coded)
            admin_modules = []
            for code in ADMIN_MODULE_CODES:
                try:
                    module = Module.objects.get(code=code)
                    admin_modules.append(module)
                except Module.DoesNotExist:
                    self.stdout.write(self.style.ERROR(f"❌ Module not found: {code}"))
                    self.stdout.write(self.style.WARNING(f"   Run migrations first: python manage.py migrate rbac"))
                    continue
            
            if not admin_modules:
                self.stdout.write(self.style.ERROR("\n❌ No admin modules found in database!"))
                self.stdout.write(self.style.WARNING("Run: python manage.py migrate rbac"))
                return
            
            self.stdout.write(f"📦 Admin Modules to Grant: {len(admin_modules)}\n")
            
            # Grant each module to admin role
            granted_count = 0
            already_granted_count = 0
            
            for module in admin_modules:
                role_module, created = RoleModule.objects.get_or_create(
                    role=admin_role,
                    module=module,
                )
                
                if created:
                    self.stdout.write(self.style.SUCCESS(f"  ✅ Granted: {module.code}"))
                    granted_count += 1
                else:
                    self.stdout.write(f"  ⚠️  Already granted: {module.code}")
                    already_granted_count += 1
            
            # Clear cache for all users with admin role
            from apps.rbac.models import UserRole, UserProfile
            admin_user_profiles = UserProfile.objects.filter(
                user_roles__role=admin_role,
                is_deleted=False
            ).distinct()
            
            cleared_cache_count = 0
            self.stdout.write(f"\n🔄 Clearing cache for {admin_user_profiles.count()} admin users...")
            for profile in admin_user_profiles:
                cache.delete(f'user_modules_{profile.id}')
                cache.delete(f'user_permissions_{profile.id}')
                cleared_cache_count += 1
                self.stdout.write(f"  ✅ Cleared cache: {profile.user.email}")
            
            # Summary
            self.stdout.write(f"\n{'='*70}")
            self.stdout.write(self.style.SUCCESS(f"✅ GRANT SUMMARY"))
            self.stdout.write(f"{'='*70}")
            self.stdout.write(f"Modules granted:        {granted_count}")
            self.stdout.write(f"Already granted:        {already_granted_count}")
            self.stdout.write(f"Total admin modules:    {len(admin_modules)}")
            self.stdout.write(f"Cache cleared for:      {cleared_cache_count} users")
            self.stdout.write(f"{'='*70}\n")
            
            # Show final role-module mappings
            self.stdout.write(f"📋 Admin Role Module Access ({RoleModule.objects.filter(role=admin_role).count()} total):")
            for rm in RoleModule.objects.filter(role=admin_role).select_related('module').order_by('module__order')[:20]:
                is_admin_module = "✅ ADMIN" if rm.module.code in ADMIN_MODULE_CODES else ""
                self.stdout.write(f"  • {rm.module.code:25} {rm.module.name:40} {is_admin_module}")
            
            self.stdout.write(f"\n{'='*70}")
            self.stdout.write(self.style.WARNING("⚠️  USERS MUST REFRESH BROWSER TO SEE CHANGES"))
            self.stdout.write(f"{'='*70}\n")
            
        except Role.DoesNotExist:
            self.stdout.write(self.style.ERROR("❌ Admin role not found!"))
            self.stdout.write(self.style.WARNING("Run: python manage.py seed_rbac"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Error: {e}"))
            import traceback
            self.stdout.write(traceback.format_exc())
