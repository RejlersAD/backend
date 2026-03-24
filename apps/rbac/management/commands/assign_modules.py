"""
Assign Modules to User
Django management command to assign modules to a user using soft coding principles
"""
import logging
from django.core.management.base import BaseCommand
from django.db import transaction
from django.contrib.auth import get_user_model
from apps.rbac.models import Module, Role, RoleModule, UserProfile, UserRole, Permission, RolePermission
from apps.rbac.rbac_config import (
    MODULE_ASSIGNMENT_CONFIG,
    get_custom_role_code,
    get_custom_role_name
)

User = get_user_model()
logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Assign modules to a user (creates/updates custom role)'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--email',
            type=str,
            required=True,
            help='User email address',
        )
        parser.add_argument(
            '--modules',
            type=str,
            required=True,
            help='Comma-separated list of module codes (e.g., "qhse,finance,pid_analysis")',
        )
        parser.add_argument(
            '--add',
            action='store_true',
            help='Add modules to existing (don\'t replace)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without making changes',
        )

    def handle(self, *args, **options):
        email = options['email']
        module_codes = [m.strip() for m in options['modules'].split(',')]
        add_only = options.get('add', False)
        dry_run = options.get('dry_run', False)
        
        self.stdout.write("\n" + "="*80)
        self.stdout.write(self.style.SUCCESS("📦 MODULE ASSIGNMENT TOOL"))
        self.stdout.write("="*80 + "\n")
        
        if dry_run:
            self.stdout.write(self.style.WARNING("⚠️  DRY RUN MODE - No changes will be made\n"))
        
        # Get user
        try:
            user = User.objects.get(email=email, is_active=True)
            profile = user.rbac_profile
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"❌ User '{email}' not found"))
            return
        except UserProfile.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"❌ User profile not found for '{email}'"))
            return
        
        self.stdout.write(f"👤 User: {user.email}")
        self.stdout.write(f"   Name: {user.first_name} {user.last_name}")
        self.stdout.write(f"   Organization: {profile.organization.name}\n")
        
        # Validate modules
        modules = Module.objects.filter(code__in=module_codes, is_active=True)
        if modules.count() != len(module_codes):
            found_codes = set(modules.values_list('code', flat=True))
            missing = set(module_codes) - found_codes
            self.stdout.write(self.style.ERROR(f"❌ Invalid module codes: {', '.join(missing)}"))
            self.stdout.write("\nAvailable modules:")
            all_modules = Module.objects.filter(is_active=True).values_list('code', 'name')
            for code, name in all_modules:
                self.stdout.write(f"   • {code}: {name}")
            return
        
        self.stdout.write(f"📦 Modules to assign ({modules.count()}):")
        for module in modules:
            self.stdout.write(f"   • {module.code}: {module.name}")
        
        if not dry_run:
            # Perform assignment
            result = self._assign_modules(user, profile, modules, add_only)
            
            self.stdout.write(f"\n{'='*80}")
            self.stdout.write(self.style.SUCCESS("✅ MODULE ASSIGNMENT COMPLETE"))
            self.stdout.write("="*80)
            self.stdout.write(f"Role: {result['role_name']}")
            self.stdout.write(f"Modules assigned: {result['modules_assigned']}")
            self.stdout.write(f"Permissions assigned: {result['permissions_assigned']}")
            
            # Verify
            accessible = profile.get_all_modules()
            self.stdout.write(f"\n🔍 User's accessible modules ({accessible.count()}):")
            for mod in accessible:
                self.stdout.write(f"   • {mod.code}: {mod.name}")
            self.stdout.write()
        else:
            self.stdout.write(f"\n⚠️  DRY RUN - Run without --dry-run to apply changes\n")
    
    @transaction.atomic
    def _assign_modules(self, user, profile, modules, add_only=False):
        """Assign modules to user's custom role"""
        result = {
            'role_name': '',
            'modules_assigned': 0,
            'permissions_assigned': 0
        }
        
        # Get or create custom role — soft-coded: handles name collision gracefully
        role_code = get_custom_role_code(user.email)
        role_name = get_custom_role_name(user.first_name, user.last_name)

        # First: try to find by code (fastest path)
        custom_role = Role.objects.filter(code=role_code).first()

        if custom_role is None:
            # Fallback: find by name in case a previous run used a different code
            custom_role = Role.objects.filter(name=role_name).first()
            if custom_role:
                # Align the code so future lookups find it correctly
                custom_role.code = role_code
                custom_role.save(update_fields=['code'])

        if custom_role is None:
            # Neither code nor name exists — safe to create
            custom_role = Role.objects.create(
                code=role_code,
                name=role_name,
                description=f'Custom role for {user.email} with selected modules',
                level=MODULE_ASSIGNMENT_CONFIG['custom_role_level'],
                is_active=True
            )
        else:
            # Ensure name stays in sync (user may have changed their display name)
            if custom_role.name != role_name:
                try:
                    custom_role.name = role_name
                    custom_role.save(update_fields=['name'])
                except Exception:
                    pass  # name already taken by another role — keep existing name
        
        result['role_name'] = custom_role.name
        
        # Ensure user has this role
        UserRole.objects.get_or_create(
            user_profile=profile,
            role=custom_role,
            defaults={'assigned_by': None, 'is_primary': True}
        )
        
        # Clear existing if not adding
        if not add_only:
            RoleModule.objects.filter(role=custom_role).delete()
            RolePermission.objects.filter(role=custom_role).delete()
        
        # Assign modules
        for module in modules:
            role_module, rm_created = RoleModule.objects.get_or_create(
                role=custom_role,
                module=module,
                defaults={'granted_by': None}
            )
            if rm_created:
                result['modules_assigned'] += 1
        
        # Assign permissions
        module_ids = [m.id for m in modules]
        permissions = Permission.objects.filter(module_id__in=module_ids, is_active=True)
        
        for permission in permissions:
            role_perm, rp_created = RolePermission.objects.get_or_create(
                role=custom_role,
                permission=permission,
                defaults={'granted_by': None}
            )
            if rp_created:
                result['permissions_assigned'] += 1
        
        return result
