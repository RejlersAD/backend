"""
Management command to bulk assign modules to existing users
Useful for adding new app access to all existing users
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Module, RoleModule, UserRole
from django.db import transaction

User = get_user_model()


class Command(BaseCommand):
    help = 'Bulk assign modules to existing users'

    def add_arguments(self, parser):
        parser.add_argument(
            '--modules',
            type=str,
            required=True,
            help='Comma-separated module codes (e.g., pid_analysis,pfd,qhse)'
        )
        parser.add_argument(
            '--all-users',
            action='store_true',
            help='Assign to all active users'
        )
        parser.add_argument(
            '--organization',
            type=str,
            help='Filter by organization code'
        )
        parser.add_argument(
            '--role',
            type=str,
            help='Filter by role code (e.g., engineer, manager)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without making changes'
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('='*70))
        self.stdout.write(self.style.SUCCESS('BULK MODULE ASSIGNMENT'))
        self.stdout.write(self.style.SUCCESS('='*70))

        # Parse module codes
        module_codes = [m.strip() for m in options['modules'].split(',')]
        self.stdout.write(f"\n📦 Modules to assign: {', '.join(module_codes)}")

        # Validate modules exist
        modules = Module.objects.filter(code__in=module_codes, is_active=True)
        if modules.count() != len(module_codes):
            found = set(modules.values_list('code', flat=True))
            missing = set(module_codes) - found
            self.stdout.write(self.style.ERROR(f"\n❌ Error: Modules not found: {', '.join(missing)}"))
            self.stdout.write(self.style.WARNING("\nAvailable modules:"))
            for m in Module.objects.filter(is_active=True):
                self.stdout.write(f"  • {m.code} - {m.name}")
            return

        self.stdout.write(self.style.SUCCESS(f"✅ All modules validated"))

        # Build user profile queryset
        profiles = UserProfile.objects.filter(is_deleted=False, status='active')

        # Apply filters
        if options['organization']:
            profiles = profiles.filter(organization__code=options['organization'])
            self.stdout.write(f"🏢 Filtering by organization: {options['organization']}")

        if options['role']:
            profiles = profiles.filter(roles__code=options['role'])
            self.stdout.write(f"👤 Filtering by role: {options['role']}")

        if not options['all_users'] and not options['organization'] and not options['role']:
            self.stdout.write(self.style.ERROR(
                "\n❌ Error: You must specify either --all-users, --organization, or --role"
            ))
            return

        profiles = profiles.distinct()
        total_users = profiles.count()

        if total_users == 0:
            self.stdout.write(self.style.WARNING("\n⚠️  No users found matching criteria"))
            return

        self.stdout.write(f"\n👥 Found {total_users} users matching criteria")

        # Dry run mode
        if options['dry_run']:
            self.stdout.write(self.style.WARNING("\n🔍 DRY RUN MODE - No changes will be made\n"))
            self.stdout.write("Users that would be updated:")
            for profile in profiles[:10]:  # Show first 10
                roles = ', '.join(profile.roles.values_list('name', flat=True))
                self.stdout.write(f"  • {profile.user.email} ({roles})")
            if total_users > 10:
                self.stdout.write(f"  ... and {total_users - 10} more users")
            self.stdout.write(f"\nRun without --dry-run to apply changes")
            return

        # Confirm action
        self.stdout.write(self.style.WARNING(f"\n⚠️  This will assign {len(module_codes)} module(s) to {total_users} users"))
        confirm = input("Continue? (yes/no): ")
        if confirm.lower() not in ['yes', 'y']:
            self.stdout.write(self.style.WARNING("Cancelled"))
            return

        # Process assignments
        self.stdout.write(self.style.SUCCESS("\n🚀 Starting bulk assignment...\n"))

        success_count = 0
        failed_count = 0
        total_assignments = 0

        with transaction.atomic():
            for idx, profile in enumerate(profiles, 1):
                try:
                    # Get user's roles
                    user_roles = UserRole.objects.filter(user_profile=profile)

                    if not user_roles.exists():
                        self.stdout.write(self.style.WARNING(
                            f"  ⚠️  Skipped {profile.user.email}: No roles assigned"
                        ))
                        failed_count += 1
                        continue

                    # Assign modules to each role
                    assigned_count = 0
                    for user_role in user_roles:
                        role = user_role.role
                        for module in modules:
                            role_module, created = RoleModule.objects.get_or_create(
                                role=role,
                                module=module
                            )
                            if created:
                                assigned_count += 1

                    total_assignments += assigned_count
                    success_count += 1

                    # Progress indicator
                    if idx % 50 == 0 or idx == total_users:
                        self.stdout.write(f"  📊 Progress: {idx}/{total_users} users processed...")

                except Exception as e:
                    self.stdout.write(self.style.ERROR(
                        f"  ❌ Failed for {profile.user.email}: {str(e)}"
                    ))
                    failed_count += 1

        # Summary
        self.stdout.write(self.style.SUCCESS("\n" + "="*70))
        self.stdout.write(self.style.SUCCESS("SUMMARY"))
        self.stdout.write(self.style.SUCCESS("="*70))
        self.stdout.write(f"✅ Successful: {success_count} users")
        self.stdout.write(f"❌ Failed: {failed_count} users")
        self.stdout.write(f"📦 Total module assignments created: {total_assignments}")
        self.stdout.write(f"📋 Modules assigned: {', '.join([m.name for m in modules])}")

        self.stdout.write(self.style.SUCCESS("\n✨ Bulk assignment completed!"))
