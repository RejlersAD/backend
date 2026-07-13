"""
Django Management Command: Remove Custom Roles and Migrate to Default
Smart migration to eliminate all custom roles and enforce role-based access
"""

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction
from apps.rbac.models import UserProfile, Role, Module
from apps.rbac.rbac_config import (
    MODULE_ASSIGNMENT_CONFIG,
    DEFAULT_ROLE_CONFIG,
    ROLE_MODULE_POLICY,
)

User = get_user_model()


class Command(BaseCommand):
    help = 'Remove all custom roles and migrate users to Default role'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be changed without actually changing it',
        )
        parser.add_argument(
            '--delete-roles',
            action='store_true',
            help='Also delete custom role records from database',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        delete_roles = options['delete_roles']
        
        self.stdout.write("=" * 80)
        self.stdout.write(self.style.SUCCESS("  REMOVE CUSTOM ROLES - MIGRATE TO DEFAULT"))
        self.stdout.write("=" * 80)
        
        if dry_run:
            self.stdout.write(self.style.WARNING("\n⚠️  DRY RUN MODE - No changes will be made\n"))
        
        # Soft-coded: custom role prefix from config
        custom_prefix = MODULE_ASSIGNMENT_CONFIG.get('custom_role_prefix', 'custom_')
        default_role_code = DEFAULT_ROLE_CONFIG['code']
        
        # Step 1: Find all custom roles
        self.stdout.write("\n🔍 Step 1: Finding custom roles...")
        custom_roles = Role.objects.filter(code__startswith=custom_prefix)
        total_custom_roles = custom_roles.count()
        
        if total_custom_roles == 0:
            self.stdout.write(self.style.SUCCESS("  ✅ No custom roles found - system is clean!"))
            return
        
        self.stdout.write(f"  Found {total_custom_roles} custom role(s)")
        
        # Show sample
        sample_roles = custom_roles[:10]
        for role in sample_roles:
            self.stdout.write(f"     • {role.code} - {role.name}")
        if total_custom_roles > 10:
            self.stdout.write(f"     ... and {total_custom_roles - 10} more")
        
        # Step 2: Find users with custom roles
        self.stdout.write("\n👥 Step 2: Finding users with custom roles...")
        users_with_custom = UserProfile.objects.filter(
            roles__code__startswith=custom_prefix,
            roles__is_active=True
        ).distinct()
        
        total_users = users_with_custom.count()
        self.stdout.write(f"  Found {total_users} user(s) with custom roles")
        
        if total_users > 0:
            # Show sample
            sample_users = users_with_custom[:10]
            for profile in sample_users:
                user_roles = profile.roles.filter(code__startswith=custom_prefix, is_active=True)
                role_names = ", ".join([r.code for r in user_roles])
                self.stdout.write(f"     • {profile.user.email} → {role_names}")
            if total_users > 10:
                self.stdout.write(f"     ... and {total_users - 10} more")
        
        # Step 3: Get default role
        self.stdout.write("\n📦 Step 3: Preparing default role...")
        try:
            default_role = Role.objects.get(code=default_role_code, is_active=True)
            self.stdout.write(f"  ✅ Default role found: {default_role.name} (code: {default_role_code})")
            
            # Get default role modules
            default_modules = ROLE_MODULE_POLICY.get(default_role_code, [])
            self.stdout.write(f"  Default role includes {len(default_modules)} modules")
        except Role.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"  ❌ Default role not found (code: {default_role_code})"))
            self.stdout.write("  Run: python manage.py seed_rbac")
            return
        
        # Step 4: Migrate users
        if not dry_run:
            self.stdout.write("\n🔄 Step 4: Migrating users to default role...")
            
            migrated = 0
            errors = 0
            
            for profile in users_with_custom:
                try:
                    with transaction.atomic():
                        user_email = profile.user.email
                        
                        # Remove custom roles
                        custom_roles_for_user = profile.roles.filter(
                            code__startswith=custom_prefix,
                            is_active=True
                        )
                        
                        for custom_role in custom_roles_for_user:
                            profile.roles.remove(custom_role)
                            self.stdout.write(f"     ❌ Removed {custom_role.code} from {user_email}")
                        
                        # Add default role if not already present
                        if not profile.roles.filter(code=default_role_code, is_active=True).exists():
                            profile.roles.add(default_role)
                            self.stdout.write(f"     ✅ Added 'default' role to {user_email}")
                        
                        # Clear direct module assignments
                        profile.modules.clear()
                        
                        # Add default role modules
                        for module_code in default_modules:
                            try:
                                module = Module.objects.get(code=module_code, is_active=True)
                                profile.modules.add(module)
                            except Module.DoesNotExist:
                                pass
                        
                        profile.save()
                        migrated += 1
                        
                except Exception as e:
                    errors += 1
                    self.stdout.write(self.style.ERROR(f"     ❌ Error migrating {profile.user.email}: {str(e)}"))
            
            self.stdout.write(f"\n  ✅ Migrated {migrated} user(s)")
            if errors > 0:
                self.stdout.write(self.style.WARNING(f"  ⚠️  {errors} error(s)"))
        else:
            self.stdout.write("\n🔄 Step 4: Would migrate users (DRY RUN)...")
            self.stdout.write(f"  Would migrate {total_users} user(s) to 'default' role")
        
        # Step 5: Delete custom roles
        if delete_roles:
            self.stdout.write("\n🗑️  Step 5: Deleting custom role records...")
            
            if not dry_run:
                # Check if any profiles still have these roles
                still_used = UserProfile.objects.filter(
                    roles__code__startswith=custom_prefix,
                    roles__is_active=True
                ).exists()
                
                if still_used:
                    self.stdout.write(self.style.WARNING("  ⚠️  Some custom roles are still in use - run migration first"))
                else:
                    deleted_count, _ = custom_roles.delete()
                    self.stdout.write(f"  ✅ Deleted {deleted_count} custom role(s)")
            else:
                self.stdout.write(f"  Would delete {total_custom_roles} custom role(s) (DRY RUN)")
        else:
            self.stdout.write("\n💡 Step 5: Custom role records NOT deleted")
            self.stdout.write(f"  To delete them, run with --delete-roles flag")
            self.stdout.write(f"  Warning: Only delete after verifying all users are migrated")
        
        # Step 6: Verify no custom roles remain in use
        if not dry_run:
            self.stdout.write("\n✅ Step 6: Verification...")
            
            remaining = UserProfile.objects.filter(
                roles__code__startswith=custom_prefix,
                roles__is_active=True
            ).count()
            
            if remaining == 0:
                self.stdout.write(self.style.SUCCESS("  ✅ SUCCESS - No users have custom roles"))
            else:
                self.stdout.write(self.style.WARNING(f"  ⚠️  {remaining} user(s) still have custom roles"))
        
        # Summary
        self.stdout.write("\n" + "=" * 80)
        self.stdout.write(self.style.SUCCESS("  MIGRATION COMPLETE"))
        self.stdout.write("=" * 80)
        
        if not dry_run:
            self.stdout.write(f"\n📊 SUMMARY:")
            self.stdout.write(f"   Custom roles found: {total_custom_roles}")
            self.stdout.write(f"   Users migrated: {total_users}")
            if delete_roles:
                self.stdout.write(f"   Roles deleted: {total_custom_roles if not dry_run else 0}")
            
            self.stdout.write(self.style.SUCCESS(f"\n✅ All users now use role-based access only"))
            
            self.stdout.write(f"\n💡 NEXT STEPS:")
            self.stdout.write(f"   1. Users must logout and login to refresh JWT tokens")
            self.stdout.write(f"   2. Run: python manage.py sync_all_users_to_roles")
            self.stdout.write(f"   3. Verify: python manage.py diagnose_user_rbac --email USER_EMAIL")
        else:
            self.stdout.write(self.style.WARNING(f"\n⚠️  DRY RUN - No changes made"))
            self.stdout.write(f"\nTo apply changes, run:")
            self.stdout.write(self.style.WARNING(f"  python manage.py remove_custom_roles"))
            self.stdout.write(f"\nTo also delete custom role records:")
            self.stdout.write(self.style.WARNING(f"  python manage.py remove_custom_roles --delete-roles"))
