"""
Sync Role Modules (generalized, soft-coded)
============================================
Bidirectionally syncs a role's RoleModule rows to exactly match its entry in
ROLE_MODULE_POLICY (rbac_config.py) — adds missing modules AND removes extras.

This generalizes `sync_default_role` (which only handles the 'default' role)
to any role present in ROLE_MODULE_POLICY. Use this to recover from drift
caused by ad-hoc scripts/commands that grant modules outside the soft-coded
policy (e.g. the historical apply_role_module_policy Step-3 cross-role bug).

Roles with an EMPTY policy list (e.g. 'super_admin', which bypasses module
checks entirely via is_superuser) are skipped by default — pass
--include-empty-policy to force-sync those too (this would strip ALL of
their RoleModule rows).

Usage:
    # Preview every role in ROLE_MODULE_POLICY:
    python manage.py sync_role_modules --dry-run

    # Preview/apply a single role:
    python manage.py sync_role_modules --role qhse_engineer --dry-run
    python manage.py sync_role_modules --role qhse_engineer

    # Apply to ALL roles in ROLE_MODULE_POLICY (skips empty-policy roles):
    python manage.py sync_role_modules
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from apps.rbac.models import Role, Module, RoleModule
from apps.rbac.rbac_config import ROLE_MODULE_POLICY


class Command(BaseCommand):
    help = 'Bidirectionally sync RoleModule rows for one or all roles to match ROLE_MODULE_POLICY.'

    def add_arguments(self, parser):
        parser.add_argument('--role', type=str, default='', help='Only sync this role code.')
        parser.add_argument('--dry-run', action='store_true', help='Preview changes without writing.')
        parser.add_argument(
            '--include-empty-policy',
            action='store_true',
            help='Also sync roles whose policy list is empty (e.g. super_admin) — DANGEROUS: strips ALL their modules.',
        )

    def handle(self, *args, **options):
        role_filter = options.get('role', '').strip()
        dry_run = options.get('dry_run', False)
        include_empty = options.get('include_empty_policy', False)

        sep = '=' * 70
        self.stdout.write(f'\n{sep}')
        self.stdout.write('  SYNC ROLE MODULES (generalized)')
        if dry_run:
            self.stdout.write('  ⚠️  DRY-RUN MODE — no changes will be written')
        self.stdout.write(f'{sep}\n')

        role_codes = [role_filter] if role_filter else list(ROLE_MODULE_POLICY.keys())

        total_added = 0
        total_removed = 0

        for role_code in role_codes:
            configured_modules = set(ROLE_MODULE_POLICY.get(role_code, []))

            if not configured_modules and not include_empty:
                self.stdout.write(f'  skip (empty policy, use --include-empty-policy): {role_code}')
                continue

            try:
                role = Role.objects.get(code=role_code)
            except Role.DoesNotExist:
                self.stdout.write(f'  skip (role not in DB): {role_code}')
                continue

            current_modules = set(
                RoleModule.objects.filter(role=role).values_list('module__code', flat=True)
            )

            to_add = configured_modules - current_modules
            to_remove = current_modules - configured_modules

            if not to_add and not to_remove:
                self.stdout.write(f'  ✓ {role_code}: already in sync ({len(current_modules)} modules)')
                continue

            self.stdout.write(
                f'  {role_code}: {len(current_modules)} → {len(configured_modules)} modules '
                f'(+{len(to_add)} / -{len(to_remove)})'
            )

            for mod_code in sorted(to_add):
                mod = Module.objects.filter(code=mod_code, is_active=True).first()
                if not mod:
                    self.stdout.write(self.style.WARNING(f'      ⚠ module not found: {mod_code}'))
                    continue
                if dry_run:
                    self.stdout.write(f'      [dry] would add: {mod_code}')
                else:
                    with transaction.atomic():
                        RoleModule.objects.get_or_create(role=role, module=mod)
                    self.stdout.write(self.style.SUCCESS(f'      + added: {mod_code}'))
                total_added += 1

            for mod_code in sorted(to_remove):
                if dry_run:
                    self.stdout.write(f'      [dry] would remove: {mod_code}')
                else:
                    RoleModule.objects.filter(role=role, module__code=mod_code).delete()
                    self.stdout.write(self.style.WARNING(f'      - removed: {mod_code}'))
                total_removed += 1

        self.stdout.write(f'\n{sep}')
        self.stdout.write(f'  Done. Total: +{total_added} / -{total_removed}')
        self.stdout.write(sep + '\n')
