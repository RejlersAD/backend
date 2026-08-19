"""
Migrate Custom Roles to Default Role
=====================================
This command migrates all users with custom roles (e.g., "Custom Role - John Doe")
to the "Default" system role, then optionally cleans up the custom roles.

The script:
  1. Identifies all roles with the custom_role_prefix (default: 'custom_')
  2. For each user assigned to a custom role:
     - Assigns the Default role if not already assigned
     - Removes the custom role assignment
  3. Optionally deletes empty custom roles (use --cleanup flag)

Usage:
    # Dry run (default): report what would happen, change nothing.
    python manage.py migrate_custom_roles_to_default

    # Actually migrate users to Default role
    python manage.py migrate_custom_roles_to_default --apply

    # Migrate and clean up empty custom roles
    python manage.py migrate_custom_roles_to_default --apply --cleanup

    # Verbose output
    python manage.py migrate_custom_roles_to_default --apply --verbosity 2

Safety:
    - Always runs in dry-run mode unless --apply is provided
    - Uses database transactions for atomicity
    - Logs all actions for audit trail
    - Preserves other role assignments (multi-role support)
"""
import logging
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.rbac.models import Role, UserRole, UserProfile
from apps.rbac.rbac_config import MODULE_ASSIGNMENT_CONFIG, DEFAULT_ROLE_CONFIG

logger = logging.getLogger(__name__)

DEFAULT_CUSTOM_ROLE_PREFIX = MODULE_ASSIGNMENT_CONFIG.get('custom_role_prefix', 'custom_')
DEFAULT_ROLE_CODE = DEFAULT_ROLE_CONFIG.get('code', 'default')


class Command(BaseCommand):
    help = (
        'Migrate all users with custom roles to the Default role. '
        'Dry-run by default; pass --apply to execute changes.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            default=False,
            help='Actually migrate users. Without this flag the command only reports.',
        )
        parser.add_argument(
            '--cleanup',
            action='store_true',
            default=False,
            help='Delete custom roles after migration (only with --apply).',
        )
        parser.add_argument(
            '--prefix',
            default=DEFAULT_CUSTOM_ROLE_PREFIX,
            help=f'Custom role code prefix to match (default: "{DEFAULT_CUSTOM_ROLE_PREFIX}").',
        )

    def handle(self, *args, **options):
        apply_changes = options['apply']
        cleanup_roles = options['cleanup']
        prefix = options['prefix']
        verbosity = int(options.get('verbosity', 1))

        mode_label = 'APPLY' if apply_changes else 'DRY-RUN'
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'[{mode_label}] Migrating users from custom roles (prefix: "{prefix}") to Default role'
        ))

        # Get the Default role
        try:
            default_role = Role.objects.get(code=DEFAULT_ROLE_CODE, is_active=True)
        except Role.DoesNotExist:
            self.stdout.write(self.style.ERROR(
                f'Error: Default role (code="{DEFAULT_ROLE_CODE}") not found. '
                f'Run "python manage.py seed_rbac" first.'
            ))
            return

        # Find all custom roles
        custom_roles = list(
            Role.objects.filter(code__startswith=prefix, is_active=True)
            .prefetch_related('userrole_set__user_profile__user')
            .order_by('code')
        )

        if not custom_roles:
            self.stdout.write(self.style.SUCCESS(
                f'No custom roles found with prefix "{prefix}". Nothing to do.'
            ))
            return

        self.stdout.write(f'Found {len(custom_roles)} custom role(s).\n')

        # Statistics
        total_users_migrated = 0
        total_roles_to_cleanup = 0
        migration_log = []

        # Header row for the summary table
        header = f'{"Custom Role Code":<50} {"Users":>8} {"Action"}'
        self.stdout.write(header)
        self.stdout.write('-' * len(header))

        for custom_role in custom_roles:
            user_assignments = list(custom_role.userrole_set.filter(
                user_profile__is_deleted=False
            ))
            user_count = len(user_assignments)

            if user_count == 0:
                action = 'empty (skip)'
                if cleanup_roles:
                    action = 'delete' if apply_changes else 'would delete'
                    total_roles_to_cleanup += 1
                self.stdout.write(f'{custom_role.code[:49]:<50} {user_count:>8} {action}')
                continue

            # For each user with this custom role
            users_processed = []
            for user_role in user_assignments:
                profile = user_role.user_profile
                user_email = profile.user.email
                user_name = profile.user.get_full_name() or user_email

                # Check if user already has Default role
                has_default = UserRole.objects.filter(
                    user_profile=profile,
                    role=default_role
                ).exists()

                if verbosity >= 2:
                    if has_default:
                        self.stdout.write(self.style.WARNING(
                            f'    ✓ {user_name} ({user_email}) already has Default role'
                        ))
                    else:
                        self.stdout.write(
                            f'    → {user_name} ({user_email}) will receive Default role'
                        )

                if apply_changes:
                    with transaction.atomic():
                        # Assign Default role if not already assigned
                        if not has_default:
                            UserRole.objects.create(
                                user_profile=profile,
                                role=default_role,
                                is_primary=(user_role.is_primary),  # Preserve primary flag
                                assigned_by=None,  # System migration
                            )
                            logger.info(
                                '[migrate_custom_roles] Assigned Default role to %s (%s)',
                                user_email, profile.id
                            )

                        # Remove custom role assignment
                        user_role.delete()
                        logger.info(
                            '[migrate_custom_roles] Removed custom role %s from %s (%s)',
                            custom_role.code, user_email, profile.id
                        )

                users_processed.append({
                    'email': user_email,
                    'name': user_name,
                    'had_default': has_default,
                })

            total_users_migrated += user_count
            action = 'migrated' if apply_changes else 'would migrate'
            self.stdout.write(f'{custom_role.code[:49]:<50} {user_count:>8} {action}')

            if apply_changes and verbosity >= 2:
                self.stdout.write(self.style.SUCCESS(
                    f'    ✓ Migrated {user_count} user(s) to Default role'
                ))

            migration_log.append({
                'role_code': custom_role.code,
                'role_id': custom_role.id,
                'users': users_processed,
            })

        # Clean up empty custom roles if requested
        if cleanup_roles and apply_changes:
            self.stdout.write('')
            self.stdout.write(self.style.MIGRATE_HEADING('Cleaning up custom roles...'))

            roles_deleted = 0
            for custom_role in custom_roles:
                # Refresh to check if any users remain
                remaining_users = UserRole.objects.filter(role=custom_role).count()
                if remaining_users == 0:
                    custom_role.delete()
                    roles_deleted += 1
                    logger.info(
                        '[migrate_custom_roles] Deleted empty custom role %s',
                        custom_role.code
                    )
                    if verbosity >= 2:
                        self.stdout.write(f'    ✓ Deleted role: {custom_role.code}')

            self.stdout.write(self.style.SUCCESS(f'Deleted {roles_deleted} empty custom role(s).'))

        # Summary
        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING('Summary'))
        self.stdout.write(f'  Custom roles found:       {len(custom_roles)}')
        self.stdout.write(f'  Users migrated:           {total_users_migrated}')
        self.stdout.write(f'  Default role code:        {DEFAULT_ROLE_CODE}')
        if cleanup_roles and apply_changes:
            self.stdout.write(f'  Roles cleaned up:         {roles_deleted}')

        if not apply_changes:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                'This was a DRY RUN. Re-run with --apply to actually migrate users.'
            ))
            if cleanup_roles:
                self.stdout.write(self.style.WARNING(
                    'The --cleanup flag will be ignored without --apply.'
                ))
        else:
            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS('Migration complete!'))
            self.stdout.write('')
            self.stdout.write('Next steps:')
            if not cleanup_roles:
                self.stdout.write('  1. Verify user access in frontend')
                self.stdout.write('  2. Run with --cleanup flag to remove custom roles:')
                self.stdout.write(f'     python manage.py migrate_custom_roles_to_default --apply --cleanup')
            else:
                self.stdout.write('  1. Verify user access in frontend')
                self.stdout.write('  2. Check audit logs for migration activity')
