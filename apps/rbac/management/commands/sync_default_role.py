"""
Management command to sync Default role modules to match configuration.
Updates the 'default' role in the database to have exactly the modules
defined in DEFAULT_ROLE_MODULES (Dashboard + Engineering + COMMON sections only).

Usage:
    python manage.py sync_default_role
    python manage.py sync_default_role --dry-run
"""
from django.core.management.base import BaseCommand
from apps.rbac.models import Role, Module, RoleModule
from apps.rbac.rbac_config import DEFAULT_ROLE_MODULES


class Command(BaseCommand):
    help = 'Sync Default role modules to match DEFAULT_ROLE_MODULES configuration'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be changed without making changes',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        
        if dry_run:
            self.stdout.write(self.style.WARNING('🔍 DRY RUN MODE - No changes will be made'))
        
        self.stdout.write(self.style.WARNING('Starting Default role module sync...'))
        
        # Get the Default role
        try:
            default_role = Role.objects.get(code='default')
        except Role.DoesNotExist:
            self.stdout.write(self.style.ERROR('❌ Default role not found in database'))
            self.stdout.write('   Run "python manage.py seed_rbac" first to create system roles')
            return
        
        self.stdout.write(f'✓ Found role: {default_role.name} (code: {default_role.code})')
        
        # Get configured modules from DEFAULT_ROLE_MODULES
        configured_modules = set(DEFAULT_ROLE_MODULES)
        self.stdout.write(f'\n📋 Configured modules ({len(configured_modules)}):')
        self.stdout.write(f'   Dashboard (always accessible - no module required)')
        self.stdout.write(f'   1. Engineering: {len([m for m in configured_modules if m not in ["crs_documents", "pfd_to_pid", "designiq", "data_mining", "hr_self_service"]])} modules')
        self.stdout.write(f'   2. COMMON: crs_documents, pfd_to_pid, designiq, data_mining, hr_self_service')
        
        # Get current modules assigned to Default role
        current_role_modules = RoleModule.objects.filter(role=default_role)
        current_module_codes = set(rm.module.code for rm in current_role_modules)
        
        self.stdout.write(f'\n📊 Current database state ({len(current_module_codes)} modules):')
        if current_module_codes:
            for code in sorted(current_module_codes):
                status = '✓' if code in configured_modules else '⚠️ EXTRA'
                self.stdout.write(f'   {status} {code}')
        else:
            self.stdout.write('   (no modules assigned)')
        
        # Calculate changes
        modules_to_add = configured_modules - current_module_codes
        modules_to_remove = current_module_codes - configured_modules
        
        self.stdout.write(f'\n🔧 Changes needed:')
        if modules_to_add:
            self.stdout.write(f'   ➕ Add {len(modules_to_add)} modules:')
            for code in sorted(modules_to_add):
                self.stdout.write(f'      + {code}')
        else:
            self.stdout.write(f'   ➕ No modules to add')
        
        if modules_to_remove:
            self.stdout.write(f'   ➖ Remove {len(modules_to_remove)} modules:')
            for code in sorted(modules_to_remove):
                self.stdout.write(f'      - {code}')
        else:
            self.stdout.write(f'   ➖ No modules to remove')
        
        if not modules_to_add and not modules_to_remove:
            self.stdout.write(self.style.SUCCESS('\n✅ Default role is already in sync - no changes needed'))
            return
        
        if dry_run:
            self.stdout.write(self.style.WARNING('\n🔍 DRY RUN - No changes were made'))
            self.stdout.write('   Run without --dry-run to apply changes')
            return
        
        # Apply changes
        self.stdout.write(f'\n⚙️ Applying changes...')
        
        # Add new modules
        added_count = 0
        for module_code in modules_to_add:
            try:
                module = Module.objects.get(code=module_code)
                RoleModule.objects.get_or_create(role=default_role, module=module)
                added_count += 1
                self.stdout.write(f'   ✓ Added: {module_code}')
            except Module.DoesNotExist:
                self.stdout.write(self.style.WARNING(f'   ⚠️ Module not found in DB: {module_code} (run seed_rbac first)'))
        
        # Remove extra modules
        removed_count = 0
        for module_code in modules_to_remove:
            deleted = RoleModule.objects.filter(
                role=default_role,
                module__code=module_code
            ).delete()[0]
            if deleted:
                removed_count += 1
                self.stdout.write(f'   ✓ Removed: {module_code}')
        
        # Final status
        self.stdout.write(f'\n✅ Sync complete!')
        self.stdout.write(f'   Added: {added_count} modules')
        self.stdout.write(f'   Removed: {removed_count} modules')
        self.stdout.write(f'\n📌 Default role now has access to:')
        self.stdout.write(f'   • Dashboard (always accessible)')
        self.stdout.write(f'   • 1. Engineering (all sub-sections)')
        self.stdout.write(f'   • 2. COMMON (CRS, PFD to P&ID, DesignIQ, Data Mining, My Profile)')
        self.stdout.write(f'\n🚫 Default role does NOT have access to:')
        self.stdout.write(f'   • 4. Human Resource (except My Profile)')
        self.stdout.write(f'   • 5. Finance')
        self.stdout.write(f'   • 6. Procurement')
        self.stdout.write(f'   • 7. QHSE')
        self.stdout.write(f'   • 8. AI/ML')
        self.stdout.write(f'   • 9. Admin')
        self.stdout.write(self.style.SUCCESS('\n🎉 Default role successfully updated!'))
