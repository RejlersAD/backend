"""
Management command: sync_line_list_access
==========================================
Ensures that the 'viewer' and 'default' roles have the pid_line_list and
pid_equipment_list modules assigned, then flushes Redis module-cache keys so
the change takes effect immediately.

Usage (Railway shell / local docker exec):
    python manage.py sync_line_list_access
    python manage.py sync_line_list_access --dry-run
    python manage.py sync_line_list_access --roles viewer default engineer
"""
import uuid

from django.core.management.base import BaseCommand

from apps.rbac.models import Module, Role, RoleModule, UserRole

# Soft-coded: modules to guarantee on every standard-access role
REQUIRED_MODULE_CODES = ['pid_line_list', 'pid_equipment_list']

# Soft-coded: roles that should always have these modules
DEFAULT_TARGET_ROLES = ['viewer', 'default']


class Command(BaseCommand):
    help = 'Ensure Line List and Equipment List access for viewer/default roles'

    def add_arguments(self, parser):
        parser.add_argument(
            '--roles',
            nargs='+',
            default=DEFAULT_TARGET_ROLES,
            metavar='ROLE_CODE',
            help='Role codes to fix (default: viewer default)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would change without writing anything',
        )

    def handle(self, *args, **options):
        dry_run      = options['dry_run']
        target_roles = options['roles']
        mode         = '[DRY-RUN] ' if dry_run else ''

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n{mode}sync_line_list_access — fixing access for roles: {", ".join(target_roles)}'
        ))

        # ── 1. Verify/create module objects ───────────────────────────────
        module_objs = {}
        for code in REQUIRED_MODULE_CODES:
            try:
                mod = Module.objects.get(code=code)
                if not mod.is_active:
                    if not dry_run:
                        mod.is_active = True
                        mod.save(update_fields=['is_active'])
                    self.stdout.write(f'  {mode}✓ Activated module: {code}')
                else:
                    self.stdout.write(f'  ✓ Module exists and active: {code}')
                module_objs[code] = mod
            except Module.DoesNotExist:
                self.stderr.write(self.style.ERROR(
                    f'  ✗ Module "{code}" not found in DB — run migrations first'
                ))

        if not module_objs:
            self.stderr.write(self.style.ERROR('No modules found. Aborting.'))
            return

        # ── 2. Grant modules to target roles ──────────────────────────────
        affected_role_ids = []
        for role_code in target_roles:
            try:
                role = Role.objects.get(code=role_code, is_active=True)
            except Role.DoesNotExist:
                self.stdout.write(self.style.WARNING(
                    f'  ⚠ Role "{role_code}" not found or inactive — skipping'
                ))
                continue

            affected_role_ids.append(role.id)
            for code, mod_obj in module_objs.items():
                exists = RoleModule.objects.filter(role=role, module=mod_obj).exists()
                if exists:
                    self.stdout.write(f'  ✓ {role_code} already has {code}')
                else:
                    if not dry_run:
                        RoleModule.objects.get_or_create(
                            role=role,
                            module=mod_obj,
                            defaults={'id': uuid.uuid4(), 'granted_by': None},
                        )
                    self.stdout.write(self.style.SUCCESS(
                        f'  {mode}✓ Granted {code} → {role_code}'
                    ))

        # ── 3. Flush Redis module-cache keys for affected users ────────────
        if dry_run:
            count = UserRole.objects.filter(role_id__in=affected_role_ids).values('user_profile_id').distinct().count()
            self.stdout.write(f'\n  [DRY-RUN] Would flush cache for ~{count} user(s)')
            self.stdout.write(self.style.SUCCESS('\nDry run complete — no changes written.\n'))
            return

        flushed = self._flush_caches(affected_role_ids)
        self.stdout.write(
            self.style.SUCCESS(f'\n  ✓ Flushed Redis cache for {flushed} user profile(s)')
        )
        self.stdout.write(self.style.SUCCESS(
            '\nsync_line_list_access complete. Users can now access Line List and Equipment List.\n'
        ))

    # ──────────────────────────────────────────────────────────────────────
    def _flush_caches(self, role_ids):
        """Delete user_modules_<id> Redis keys for all users on the given roles."""
        try:
            import os
            import redis as redis_lib

            profile_ids = list(
                UserRole.objects
                .filter(role_id__in=role_ids)
                .values_list('user_profile_id', flat=True)
                .distinct()
            )
            if not profile_ids:
                return 0

            redis_host     = os.environ.get('REDIS_HOST', 'redis')
            redis_port     = int(os.environ.get('REDIS_PORT', 6379))
            redis_password = os.environ.get('REDIS_PASSWORD') or None
            r = redis_lib.Redis(
                host=redis_host, port=redis_port, password=redis_password,
                socket_connect_timeout=3, decode_responses=True,
            )
            flushed = 0
            for pid in profile_ids:
                flushed += r.delete(f'user_modules_{pid}')
            return flushed
        except Exception as exc:
            self.stdout.write(self.style.WARNING(
                f'  ⚠ Redis flush skipped (non-fatal): {exc}'
            ))
            return 0
