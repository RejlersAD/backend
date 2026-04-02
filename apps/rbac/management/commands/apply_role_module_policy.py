"""
Apply Role-Module Policy
========================
Soft-coded management command that:
  1. Seeds all engineering modules (idempotent — safe to re-run)
  2. Applies ROLE_MODULE_POLICY from rbac_config.py to all existing roles
  3. Optionally fixes a specific user via --email

Usage:
    # Seed modules + apply policy to ALL users:
    python manage.py apply_role_module_policy

    # Fix one specific user (e.g. add process_datasheet to sahaya.jawaher):
    python manage.py apply_role_module_policy --email sahaya.jawaher@rejlers.ae

    # Dry-run:
    python manage.py apply_role_module_policy --dry-run
"""
import logging
from django.core.management.base import BaseCommand
from django.db import transaction
from django.contrib.auth import get_user_model
from apps.rbac.models import Module, Role, RoleModule, UserProfile, UserRole
from apps.rbac.rbac_config import ROLE_MODULE_POLICY

User = get_user_model()
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Full engineering module catalogue — single source of truth for seeding
# SOFT-CODED: add a new module here to have it created automatically
# ─────────────────────────────────────────────────────────────────────────────
ENGINEERING_MODULES = [
    {'code': 'pid_analysis',          'name': 'P&ID Analysis',               'order': 1},
    {'code': 'pfd_to_pid',            'name': 'PFD to P&ID Converter',       'order': 2},
    {'code': 'crs_documents',         'name': 'CRS Document Management',     'order': 3},
    {'code': 'designiq',              'name': 'DesignIQ',                    'order': 4},
    {'code': 'process_datasheet',     'name': 'Process Datasheet',           'order': 10},
    {'code': 'electrical_datasheet',  'name': 'Electrical Datasheet',        'order': 11},
    {'code': 'electrical_sld',        'name': 'Electrical SLD',              'order': 12},
    {'code': 'instrument_datasheet',  'name': 'Instrument Datasheet',        'order': 13},
    {'code': 'instrument_index',      'name': 'Instrument Index',            'order': 14},
    {'code': 'mechanical_datasheet',  'name': 'Mechanical Datasheet',        'order': 15},
    {'code': 'civil_datasheet',       'name': 'Civil Datasheet',             'order': 16},
    {'code': 'piping_datasheet',      'name': 'Piping Datasheet',            'order': 17},
    {'code': 'piping_pms',            'name': 'Piping Material Specification','order': 18},
    {'code': 'digitization_datasheet','name': 'Digitization Datasheet',      'order': 19},
    {'code': 'spec_customization',    'name': 'Spec Customization',          'order': 20},
    {'code': 'qhse',                  'name': 'QHSE Management',             'order': 30},
    {'code': 'user_mgmt',             'name': 'User Management',             'order': 40},
    {'code': 'org_settings',          'name': 'Organization Settings',       'order': 41},
    {'code': 'audit_logs',            'name': 'Audit Logs',                  'order': 42},
    {'code': 'reports',               'name': 'Reports & Analytics',         'order': 43},
    {'code': 'api_access',            'name': 'API Access',                  'order': 44},
]


class Command(BaseCommand):
    help = (
        'Seed engineering modules and apply ROLE_MODULE_POLICY from rbac_config.py. '
        'Use --email to fix a specific user.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--email',
            type=str,
            default='',
            help='Target a specific user email. If omitted, policy is applied to ALL users.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without making changes.',
        )

    def handle(self, *args, **options):
        email = options.get('email', '').strip()
        dry_run = options.get('dry_run', False)

        sep = '=' * 70
        self.stdout.write(f'\n{sep}')
        self.stdout.write('  APPLY ROLE-MODULE POLICY')
        if dry_run:
            self.stdout.write('  ⚠️  DRY-RUN MODE — no changes will be written')
        self.stdout.write(f'{sep}\n')

        # ── 1. Seed missing modules ────────────────────────────────────
        self.stdout.write('── Step 1: Seed engineering modules ──────────────────────')
        module_objects = {}
        for m in ENGINEERING_MODULES:
            obj, created = (
                (Module(code=m['code'], name=m['name'], order=m['order'], is_active=True), True)
                if dry_run
                else Module.objects.get_or_create(
                    code=m['code'],
                    defaults={'name': m['name'], 'order': m['order'], 'is_active': True}
                )
            )
            module_objects[m['code']] = obj
            status = '✓ Created' if created else '  exists'
            self.stdout.write(f'  {status}: {m["code"]}')

        # ── 2. Apply ROLE_MODULE_POLICY to existing roles ──────────────
        self.stdout.write('\n── Step 2: Apply ROLE_MODULE_POLICY to roles ─────────────')
        for role_code, module_codes in ROLE_MODULE_POLICY.items():
            try:
                role = Role.objects.get(code=role_code)
            except Role.DoesNotExist:
                self.stdout.write(f'  skip (role not in DB): {role_code}')
                continue

            for mod_code in module_codes:
                mod = Module.objects.filter(code=mod_code, is_active=True).first()
                if not mod:
                    self.stdout.write(
                        self.style.WARNING(f'    ⚠ module not found: {mod_code} (for role {role_code})')
                    )
                    continue
                already = RoleModule.objects.filter(role=role, module=mod).exists()
                if already:
                    self.stdout.write(f'    ✓ already linked: {role_code} → {mod_code}')
                elif dry_run:
                    self.stdout.write(f'    [dry] would link: {role_code} → {mod_code}')
                else:
                    with transaction.atomic():
                        RoleModule.objects.get_or_create(role=role, module=mod)
                    self.stdout.write(
                        self.style.SUCCESS(f'    ✓ linked: {role_code} → {mod_code}')
                    )

        # ── 3. Apply to user(s) ────────────────────────────────────────
        self.stdout.write('\n── Step 3: Apply to users ────────────────────────────────')
        if email:
            users = User.objects.filter(email=email)
            if not users.exists():
                self.stdout.write(self.style.ERROR(f'❌ User not found: {email}'))
                return
        else:
            users = User.objects.filter(is_active=True)

        for user in users:
            self._fix_user(user, module_objects, dry_run)

        self.stdout.write(f'\n{sep}')
        self.stdout.write('  Done.')
        self.stdout.write(sep + '\n')

    # ──────────────────────────────────────────────────────────────────
    def _fix_user(self, user, module_objects, dry_run):
        self.stdout.write(f'\n  User: {user.email}')

        # Ensure profile exists
        profile, _ = UserProfile.objects.get_or_create(user=user)
        if not profile.status:
            profile.status = 'active'
            if not dry_run:
                profile.save(update_fields=['status'])

        # Collect modules the user's roles should provide
        needed_codes = set()
        user_roles = UserRole.objects.filter(user_profile=profile).select_related('role')
        if not user_roles.exists():
            self.stdout.write('    (no roles assigned — skipping)')
            return

        for user_role in user_roles:
            role_code = user_role.role.code
            policy_modules = ROLE_MODULE_POLICY.get(role_code, [])
            self.stdout.write(f'    role: {role_code} → {policy_modules or "(no policy)"}')
            needed_codes.update(policy_modules)

        if not needed_codes:
            self.stdout.write('    (no modules in policy for these roles)')
            return

        # Apply: ensure every needed module is linked to at least one of the user's roles
        # We attach to the first non-custom role found, or create/use a custom one
        target_role = user_roles.first().role

        for mod_code in sorted(needed_codes):
            mod = module_objects.get(mod_code) or Module.objects.filter(code=mod_code, is_active=True).first()
            if not mod:
                self.stdout.write(
                    self.style.WARNING(f'    ⚠ module missing in DB: {mod_code}')
                )
                continue

            already = RoleModule.objects.filter(role=target_role, module=mod).exists()
            if already:
                self.stdout.write(f'    ✓ already has: {mod_code}')
            elif dry_run:
                self.stdout.write(f'    [dry] would grant: {mod_code}')
            else:
                with transaction.atomic():
                    RoleModule.objects.get_or_create(role=target_role, module=mod)
                self.stdout.write(self.style.SUCCESS(f'    ✓ granted: {mod_code}'))
