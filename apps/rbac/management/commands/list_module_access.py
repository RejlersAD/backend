"""
Management command to list modules and their user access
Helps audit who has access to which applications
"""
from django.core.management.base import BaseCommand
from apps.rbac.models import Module, UserProfile, RoleModule, UserRole
from django.db.models import Count


class Command(BaseCommand):
    help = 'List modules and user access statistics'

    def add_arguments(self, parser):
        parser.add_argument(
            '--module',
            type=str,
            help='Show detailed user list for specific module code'
        )
        parser.add_argument(
            '--organization',
            type=str,
            help='Filter by organization code'
        )

    def handle(self, *args, **options):
        if options['module']:
            self.show_module_users(options['module'], options.get('organization'))
        else:
            self.show_all_modules(options.get('organization'))

    def show_all_modules(self, org_code=None):
        """Show all modules with user counts"""
        self.stdout.write(self.style.SUCCESS('='*70))
        self.stdout.write(self.style.SUCCESS('APPLICATION MODULES - ACCESS OVERVIEW'))
        self.stdout.write(self.style.SUCCESS('='*70 + '\n'))

        modules = Module.objects.filter(is_active=True).order_by('order', 'name')

        if not modules.exists():
            self.stdout.write(self.style.WARNING('No modules found'))
            return

        for module in modules:
            # Count users with access through roles
            user_profiles = UserProfile.objects.filter(
                roles__rolemodule__module=module,
                is_deleted=False,
                status='active'
            ).distinct()

            if org_code:
                user_profiles = user_profiles.filter(organization__code=org_code)

            user_count = user_profiles.count()

            # Get roles with access
            roles_with_access = RoleModule.objects.filter(
                module=module
            ).select_related('role').values_list('role__name', flat=True)

            icon = module.icon or '📦'
            self.stdout.write(f"{icon}  {self.style.SUCCESS(module.name)} ({module.code})")
            self.stdout.write(f"   👥 Users with access: {user_count}")
            if roles_with_access:
                self.stdout.write(f"   🎭 Roles: {', '.join(roles_with_access)}")
            self.stdout.write(f"   📋 Order: {module.order}")
            self.stdout.write("")

        self.stdout.write(self.style.SUCCESS(f"\n✅ Total modules: {modules.count()}"))

        # Overall statistics
        total_active_users = UserProfile.objects.filter(
            is_deleted=False,
            status='active'
        )
        if org_code:
            total_active_users = total_active_users.filter(organization__code=org_code)

        self.stdout.write(f"👥 Total active users: {total_active_users.count()}")

        if org_code:
            self.stdout.write(f"🏢 Organization filter: {org_code}")

        self.stdout.write(self.style.WARNING(
            f"\n💡 Tip: Use --module <code> to see detailed user list for a specific module"
        ))

    def show_module_users(self, module_code, org_code=None):
        """Show detailed user list for a specific module"""
        try:
            module = Module.objects.get(code=module_code, is_active=True)
        except Module.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"\n❌ Module not found: {module_code}"))
            self.stdout.write(self.style.WARNING("\nAvailable modules:"))
            for m in Module.objects.filter(is_active=True).order_by('name'):
                self.stdout.write(f"  • {m.code} - {m.name}")
            return

        self.stdout.write(self.style.SUCCESS('='*70))
        icon = module.icon or '📦'
        self.stdout.write(self.style.SUCCESS(f'{icon}  {module.name} ({module.code}) - USER ACCESS'))
        self.stdout.write(self.style.SUCCESS('='*70 + '\n'))

        # Get users with access
        user_profiles = UserProfile.objects.filter(
            roles__rolemodule__module=module,
            is_deleted=False,
            status='active'
        ).select_related('user', 'organization').distinct()

        if org_code:
            user_profiles = user_profiles.filter(organization__code=org_code)

        user_profiles = user_profiles.order_by('organization__name', 'user__email')

        if not user_profiles.exists():
            self.stdout.write(self.style.WARNING('No users have access to this module'))
            return

        # Group by organization
        current_org = None
        user_count = 0

        for profile in user_profiles:
            if profile.organization.name != current_org:
                if current_org is not None:
                    self.stdout.write("")  # Blank line between orgs
                current_org = profile.organization.name
                self.stdout.write(self.style.SUCCESS(f"\n🏢 {current_org}:"))

            # Get user's roles
            user_role_names = UserRole.objects.filter(
                user_profile=profile
            ).values_list('role__name', flat=True)

            roles_str = ', '.join(user_role_names) if user_role_names else 'No roles'

            # Format user info
            name = f"{profile.user.first_name} {profile.user.last_name}".strip() or "N/A"
            dept = profile.department or "N/A"

            self.stdout.write(
                f"  • {profile.user.email:40s} | {name:25s} | {dept:20s} | {roles_str}"
            )
            user_count += 1

        # Summary
        self.stdout.write(self.style.SUCCESS(f"\n{'='*70}"))
        self.stdout.write(f"👥 Total users with access: {user_count}")

        if org_code:
            self.stdout.write(f"🏢 Organization filter: {org_code}")

        # Show roles with access
        self.stdout.write("\n🎭 Roles with access to this module:")
        roles_with_access = RoleModule.objects.filter(
            module=module
        ).select_related('role').values_list('role__name', 'role__code')

        for role_name, role_code in roles_with_access:
            self.stdout.write(f"  • {role_name} ({role_code})")
