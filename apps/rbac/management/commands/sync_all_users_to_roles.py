"""
Django Management Command: Sync All Users to Role-Based Modules
Smart batch fix to reset all users to their correct role-based module access
"""

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction
from apps.rbac.models import UserProfile, Role, Module
from apps.rbac.rbac_config import (
    ROLE_MODULE_POLICY,
    DEFAULT_ROLE_CONFIG,
    ADMIN_ROLE_CODES,
)

User = get_user_model()


class Command(BaseCommand):
    help = 'Sync all users to role-based module access (remove direct assignments)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be changed without actually changing it',
        )
        parser.add_argument(
            '--email',
            type=str,
            help='Only sync specific user email',
        )
        parser.add_argument(
            '--role',
            type=str,
            help='Only sync users with specific role (e.g., default, admin)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        specific_email = options.get('email')
        specific_role = options.get('role')
        
        self.stdout.write("=" * 80)
        self.stdout.write(self.style.SUCCESS("  SYNC ALL USERS TO ROLE-BASED MODULES"))
        self.stdout.write("=" * 80)
        
        if dry_run:
            self.stdout.write(self.style.WARNING("\n⚠️  DRY RUN MODE - No changes will be made\n"))
        
        # Get users to process
        if specific_email:
            try:
                user = User.objects.get(email=specific_email)
                profiles = [user.rbac_profile]
                self.stdout.write(f"Processing single user: {specific_email}")
            except (User.DoesNotExist, UserProfile.DoesNotExist):
                self.stdout.write(self.style.ERROR(f"User not found: {specific_email}"))
                return
        else:
            profiles = UserProfile.objects.filter(user__is_active=True)
            if specific_role:
                profiles = profiles.filter(roles__code=specific_role, roles__is_active=True).distinct()
                self.stdout.write(f"Processing users with role: {specific_role}")
            else:
                self.stdout.write(f"Processing all {profiles.count()} active users")
        
        # Stats
        total = profiles.count()
        synced = 0
        skipped = 0
        errors = 0
        changes_made = []
        
        self.stdout.write(f"\n📊 Found {total} user(s) to process\n")
        
        # Process each user
        for profile in profiles:
            try:
                user = profile.user
                user_email = user.email
                
                # Get user's active roles
                user_roles = profile.roles.filter(is_active=True)
                
                # Skip super_admins (they bypass module checks anyway)
                if user_roles.filter(code='super_admin').exists():
                    self.stdout.write(f"  ⏭️  Skipped {user_email} (super_admin)")
                    skipped += 1
                    continue
                
                # Compute expected modules from roles
                expected_modules = set()
                role_names = []
                for role in user_roles:
                    role_names.append(role.name)
                    role_modules = ROLE_MODULE_POLICY.get(role.code, [])
                    expected_modules.update(role_modules)
                
                # Get current modules
                current_modules = set(profile.modules.filter(is_active=True).values_list('code', flat=True))
                
                # Compare
                extra = current_modules - expected_modules
                missing = expected_modules - current_modules
                
                if not extra and not missing:
                    # Perfect match - skip
                    self.stdout.write(f"  ✅ {user_email} - Already correct")
                    skipped += 1
                    continue
                
                # Show changes
                change_summary = f"{user_email} ({', '.join(role_names)})"
                if extra:
                    change_summary += f" | Remove {len(extra)}"
                if missing:
                    change_summary += f" | Add {len(missing)}"
                
                self.stdout.write(f"  🔄 {change_summary}")
                
                if extra:
                    extra_list = ", ".join(sorted(list(extra)[:5]))
                    if len(extra) > 5:
                        extra_list += f" ... (+{len(extra) - 5} more)"
                    self.stdout.write(f"     ❌ Removing: {extra_list}")
                
                if missing:
                    missing_list = ", ".join(sorted(list(missing)[:5]))
                    if len(missing) > 5:
                        missing_list += f" ... (+{len(missing) - 5} more)"
                    self.stdout.write(f"     ➕ Adding: {missing_list}")
                
                # Apply changes if not dry run
                if not dry_run:
                    with transaction.atomic():
                        # Clear all modules
                        profile.modules.clear()
                        
                        # Add role-based modules
                        for module_code in expected_modules:
                            try:
                                module = Module.objects.get(code=module_code, is_active=True)
                                profile.modules.add(module)
                            except Module.DoesNotExist:
                                self.stdout.write(f"     ⚠️  Module not found: {module_code}")
                        
                        profile.save()
                
                synced += 1
                changes_made.append({
                    'email': user_email,
                    'roles': role_names,
                    'removed': len(extra),
                    'added': len(missing),
                })
                
            except Exception as e:
                errors += 1
                self.stdout.write(self.style.ERROR(f"  ❌ Error processing {user.email}: {str(e)}"))
                continue
        
        # Summary
        self.stdout.write("\n" + "=" * 80)
        self.stdout.write(self.style.SUCCESS("  SYNC COMPLETE"))
        self.stdout.write("=" * 80)
        self.stdout.write(f"\n📊 SUMMARY:")
        self.stdout.write(f"   Total users: {total}")
        self.stdout.write(f"   ✅ Synced: {synced}")
        self.stdout.write(f"   ⏭️  Skipped: {skipped}")
        self.stdout.write(f"   ❌ Errors: {errors}")
        
        if dry_run:
            self.stdout.write(self.style.WARNING(f"\n⚠️  DRY RUN - No actual changes made"))
            self.stdout.write(f"\nTo apply changes, run without --dry-run flag:")
            self.stdout.write(self.style.WARNING(f"  python manage.py sync_all_users_to_roles"))
        else:
            self.stdout.write(self.style.SUCCESS(f"\n✅ All users synced to role-based access"))
        
        # Show detailed changes
        if changes_made and len(changes_made) <= 20:
            self.stdout.write(f"\n📋 DETAILED CHANGES:")
            for change in changes_made:
                self.stdout.write(f"   • {change['email']}")
                self.stdout.write(f"     Roles: {', '.join(change['roles'])}")
                self.stdout.write(f"     Removed: {change['removed']} | Added: {change['added']}")
        elif changes_made:
            self.stdout.write(f"\n📋 {len(changes_made)} users had changes (too many to list)")
        
        self.stdout.write("\n💡 NEXT STEPS:")
        self.stdout.write("   1. Users should logout and login again to refresh JWT tokens")
        self.stdout.write("   2. Verify specific users with: python manage.py diagnose_user_rbac --email USER_EMAIL")
        self.stdout.write("   3. Check frontend access to confirm restrictions work")
