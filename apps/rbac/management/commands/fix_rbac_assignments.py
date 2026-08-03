"""
Fix RBAC Module Assignments
Smart command to ensure all users have proper role-module linkages
"""
import logging
from django.core.management.base import BaseCommand
from django.db import transaction
from django.contrib.auth import get_user_model
from apps.rbac.models import (
    Module, Role, RoleModule, UserProfile, UserRole, Permission, RolePermission
)

User = get_user_model()
logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Fix RBAC role and module assignments for all users'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--email',
            type=str,
            help='Fix assignments for specific user email',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without making changes',
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed output',
        )

    def handle(self, *args, **options):
        email = options.get('email')
        dry_run = options.get('dry_run', False)
        verbose = options.get('verbose', False)
        
        self.stdout.write("\n" + "="*80)
        self.stdout.write(self.style.SUCCESS("🔧 RBAC ASSIGNMENT FIXER"))
        self.stdout.write("="*80 + "\n")
        
        if dry_run:
            self.stdout.write(self.style.WARNING("⚠️  DRY RUN MODE - No changes will be made\n"))
        
        # Get users to process
        if email:
            users = User.objects.filter(email=email, is_active=True)
            if not users.exists():
                self.stdout.write(self.style.ERROR(f"❌ User with email '{email}' not found"))
                return
        else:
            users = User.objects.filter(is_active=True)
        
        self.stdout.write(f"📊 Processing {users.count()} user(s)...\n")
        
        # Track statistics
        stats = {
            'users_processed': 0,
            'users_fixed': 0,
            'roles_created': 0,
            'role_modules_created': 0,
            'permissions_assigned': 0,
            'users_skipped': 0,
        }
        
        for user in users:
            try:
                result = self._fix_user_assignments(user, dry_run, verbose)
                stats['users_processed'] += 1
                if result['fixed']:
                    stats['users_fixed'] += 1
                    stats['roles_created'] += result.get('roles_created', 0)
                    stats['role_modules_created'] += result.get('role_modules_created', 0)
                    stats['permissions_assigned'] += result.get('permissions_assigned', 0)
                else:
                    stats['users_skipped'] += 1
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"❌ Error processing {user.email}: {str(e)}"))
                logger.error(f"Error fixing RBAC for {user.email}", exc_info=True)
        
        # Print summary
        self.stdout.write("\n" + "="*80)
        self.stdout.write(self.style.SUCCESS("📈 SUMMARY"))
        self.stdout.write("="*80)
        self.stdout.write(f"✅ Users processed: {stats['users_processed']}")
        self.stdout.write(f"🔧 Users fixed: {stats['users_fixed']}")
        self.stdout.write(f"⏭️  Users skipped (already OK): {stats['users_skipped']}")
        self.stdout.write(f"👥 Custom roles created: {stats['roles_created']}")
        self.stdout.write(f"📦 Role-module links created: {stats['role_modules_created']}")
        self.stdout.write(f"🔐 Permissions assigned: {stats['permissions_assigned']}")
        self.stdout.write("="*80 + "\n")
        
        if dry_run:
            self.stdout.write(self.style.WARNING("\n⚠️  This was a DRY RUN - run without --dry-run to apply changes\n"))
    
    @transaction.atomic
    def _fix_user_assignments(self, user, dry_run=False, verbose=False):
        """Fix RBAC assignments for a single user"""
        result = {
            'fixed': False,
            'roles_created': 0,
            'role_modules_created': 0,
            'permissions_assigned': 0,
        }
        
        try:
            profile = user.rbac_profile
        except UserProfile.DoesNotExist:
            if verbose:
                self.stdout.write(self.style.WARNING(f"⚠️  {user.email}: No profile found, skipping"))
            return result
        
        # Get user's assigned roles
        user_roles = UserRole.objects.filter(user_profile=profile)
        
        if not user_roles.exists():
            if verbose:
                self.stdout.write(self.style.WARNING(f"⚠️  {user.email}: No roles assigned, skipping"))
            return result
        
        # Check current module access
        current_modules = profile.get_all_modules()
        current_module_codes = set(current_modules.values_list('code', flat=True))
        
        if verbose:
            self.stdout.write(f"\n👤 {user.email}")
            self.stdout.write(f"   Roles: {', '.join(ur.role.name for ur in user_roles)}")
            self.stdout.write(f"   Current modules: {', '.join(current_module_codes) if current_module_codes else 'NONE'}")
        
        # For each role, check if modules are properly linked
        modules_to_link = set()
        for user_role in user_roles:
            role = user_role.role
            
            # Get all modules that should be accessible through this role
            # based on RoleModule relationships
            role_modules = RoleModule.objects.filter(role=role).select_related('module')
            
            if role_modules.exists():
                for rm in role_modules:
                    modules_to_link.add(rm.module)
            elif role.code in ['super_admin', 'admin']:
                # Super admin and admin should have access to all modules
                all_modules = Module.objects.filter(is_active=True)
                modules_to_link.update(all_modules)
        
        # Check if there are module gaps
        expected_module_codes = set(m.code for m in modules_to_link)
        missing_modules = expected_module_codes - current_module_codes
        
        if not missing_modules:
            if verbose:
                self.stdout.write(self.style.SUCCESS(f"   ✅ All modules properly assigned"))
            return result
        
        # Fix missing modules
        if verbose:
            self.stdout.write(self.style.WARNING(f"   🔧 Missing modules: {', '.join(missing_modules)}"))
        
        result['fixed'] = True
        
        # Link missing modules to user's roles
        for module in modules_to_link:
            if module.code in missing_modules:
                for user_role in user_roles:
                    role = user_role.role
                    
                    if not dry_run:
                        role_module, created = RoleModule.objects.get_or_create(
                            role=role,
                            module=module,
                            defaults={'granted_by': None}
                        )
                        
                        if created:
                            result['role_modules_created'] += 1
                            
                            # Assign all permissions for this module to the role
                            permissions = Permission.objects.filter(
                                module=module,
                                is_active=True
                            )
                            
                            for permission in permissions:
                                role_perm, perm_created = RolePermission.objects.get_or_create(
                                    role=role,
                                    permission=permission,
                                    defaults={'granted_by': None}
                                )
                                if perm_created:
                                    result['permissions_assigned'] += 1
                    else:
                        result['role_modules_created'] += 1
        
        # Verify fix worked
        if not dry_run:
            new_modules = profile.get_all_modules()
            new_module_codes = set(new_modules.values_list('code', flat=True))
            
            if verbose:
                self.stdout.write(self.style.SUCCESS(f"   ✅ Fixed! New modules: {', '.join(new_module_codes)}"))
        
        return result
