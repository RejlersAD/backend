"""
Grant Engineering Section Access to All Users
==============================================
Idempotent management command that grants every active user in the system
full access to the Engineering section (modules 1.1 – 1.7).

The list of engineering modules is driven entirely by ENGINEERING_SECTION_MODULES
in rbac_config.py — no module codes are hardcoded here.

Strategy:
  1. Seed all engineering modules in Module table (idempotent).
  2. Apply the updated ROLE_MODULE_POLICY to all existing roles.
  3. For every active user:
       a. Ensure they have a UserProfile.
       b. If they have no roles at all, assign the default 'viewer' role so the
          policy can attach modules to them.
       c. Walk their roles and attach all ENGINEERING_SECTION_MODULES via
          RoleModule — this is the same mechanism used by apply_role_module_policy,
          so it is compatible with the existing access-check logic.

Usage:
    # Apply to all users (production Railway run):
    python manage.py grant_engineering_to_all

    # Dry-run first to preview changes:
    python manage.py grant_engineering_to_all --dry-run

    # Fix a single user only:
    python manage.py grant_engineering_to_all --email user@example.com
"""
import logging

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.rbac.models import Module, Organization, Role, RoleModule, UserProfile, UserRole
from apps.rbac.rbac_config import ENGINEERING_SECTION_MODULES, ROLE_MODULE_POLICY

User = get_user_model()
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Soft-coded: fallback role assigned to users who have no roles at all.
# Change to 'process_engineer' or any other role code if a different default
# is preferred.  The role must already exist in the DB (seeded by seed_rbac).
# ─────────────────────────────────────────────────────────────────────────────
FALLBACK_ROLE_CODE = 'viewer'


class Command(BaseCommand):
    help = (
        'Grant full Engineering section access (1.1–1.7) to ALL active users. '
        'Idempotent — safe to re-run. Reads module list from ENGINEERING_SECTION_MODULES '
        'in rbac_config.py.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--email',
            type=str,
            default='',
            help='Target a single user by email. Omit to process ALL active users.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview changes without writing to the database.',
        )

    def handle(self, *args, **options):
        email    = options.get('email', '').strip()
        dry_run  = options.get('dry_run', False)

        sep = '=' * 70
        self.stdout.write(f'\n{sep}')
        self.stdout.write('  GRANT ENGINEERING ACCESS TO ALL USERS')
        if dry_run:
            self.stdout.write('  ⚠️  DRY-RUN — no DB changes will be written')
        self.stdout.write(f'  Modules: {ENGINEERING_SECTION_MODULES}')
        self.stdout.write(f'{sep}\n')

        # ── Step 1: Seed/verify all engineering modules exist ──────────
        self.stdout.write('── Step 1: Seed engineering modules ──────────────────────')
        module_map: dict = {}
        for code in ENGINEERING_SECTION_MODULES:
            if dry_run:
                mod = Module(code=code, name=code.replace('_', ' ').title(), is_active=True)
                self.stdout.write(f'  [dry] would ensure: {code}')
            else:
                mod, created = Module.objects.get_or_create(
                    code=code,
                    defaults={
                        'name': code.replace('_', ' ').title(),
                        'is_active': True,
                        'order': ENGINEERING_SECTION_MODULES.index(code) + 1,
                    },
                )
                status = '✓ created' if created else '  exists'
                self.stdout.write(f'  {status}: {code}')
            module_map[code] = mod

        # ── Step 2: Attach engineering modules to ALL existing roles ───
        self.stdout.write('\n── Step 2: Attach engineering modules to roles ────────────')
        roles_qs = Role.objects.all()
        for role in roles_qs:
            # super_admin bypasses module checks — skip attachment
            if role.code == 'super_admin':
                self.stdout.write(f'  skip (super_admin bypasses module checks): {role.code}')
                continue

            # Determine the module set for this role: always engineering + any extras from policy
            policy_extras = [
                c for c in ROLE_MODULE_POLICY.get(role.code, [])
                if c not in ENGINEERING_SECTION_MODULES
            ]
            all_codes = list(ENGINEERING_SECTION_MODULES) + policy_extras

            for code in all_codes:
                mod = module_map.get(code) or Module.objects.filter(code=code, is_active=True).first()
                if not mod:
                    self.stdout.write(self.style.WARNING(f'  ⚠ module not in DB: {code}'))
                    continue
                if dry_run:
                    already = RoleModule.objects.filter(role=role, module=mod).exists()
                    label = 'already' if already else 'would link'
                    self.stdout.write(f'  [dry] {label}: {role.code} → {code}')
                else:
                    _, created = RoleModule.objects.get_or_create(role=role, module=mod)
                    if created:
                        self.stdout.write(self.style.SUCCESS(f'  ✓ linked: {role.code} → {code}'))

        # ── Step 3: Ensure every user has a profile + fallback role ───
        self.stdout.write('\n── Step 3: Grant access to users ─────────────────────────')
        if email:
            users = User.objects.filter(email=email)
            if not users.exists():
                self.stdout.write(self.style.ERROR(f'❌ User not found: {email}'))
                return
        else:
            users = User.objects.filter(is_active=True)

        self.stdout.write(f'  Processing {users.count()} active user(s)…\n')

        default_org = None
        if not dry_run:
            default_org, _ = Organization.objects.get_or_create(
                code='default',
                defaults={
                    'name': 'Default Organization',
                    'primary_contact_email': 'admin@rejlers.com',
                    'is_active': True,
                },
            )

        total_users   = 0
        already_ok    = 0
        newly_granted = 0
        role_assigned = 0

        for user in users:
            total_users += 1

            # Ensure profile
            if dry_run:
                profile = UserProfile.objects.filter(user=user).first()
                if not profile:
                    self.stdout.write(f'  [dry] would create profile for: {user.email}')
                    continue
            else:
                profile, p_created = UserProfile.objects.get_or_create(
                    user=user,
                    defaults={'organization': default_org, 'status': 'active'},
                )
                if p_created:
                    self.stdout.write(f'  ✓ Created profile: {user.email}')

            # If no roles, assign fallback role so policy modules attach
            user_roles = UserRole.objects.filter(user_profile=profile)
            if not user_roles.exists():
                fallback_role = Role.objects.filter(code=FALLBACK_ROLE_CODE).first()
                if fallback_role:
                    if dry_run:
                        self.stdout.write(
                            f'  [dry] would assign {FALLBACK_ROLE_CODE} role to: {user.email}'
                        )
                    else:
                        with transaction.atomic():
                            UserRole.objects.get_or_create(
                                user_profile=profile, role=fallback_role
                            )
                        self.stdout.write(
                            self.style.SUCCESS(
                                f'  ✓ Assigned {FALLBACK_ROLE_CODE} role to: {user.email}'
                            )
                        )
                        role_assigned += 1
                        user_roles = UserRole.objects.filter(user_profile=profile)
                else:
                    self.stdout.write(
                        self.style.WARNING(
                            f'  ⚠ Fallback role "{FALLBACK_ROLE_CODE}" not found for: {user.email}'
                        )
                    )
                    continue

            # Check if all engineering modules already reachable through roles
            user_role_objs = [ur.role for ur in user_roles.select_related('role')]
            existing_mod_codes = set(
                RoleModule.objects.filter(
                    role__in=user_role_objs,
                    module__code__in=ENGINEERING_SECTION_MODULES,
                    module__is_active=True,
                ).values_list('module__code', flat=True)
            )
            missing = [c for c in ENGINEERING_SECTION_MODULES if c not in existing_mod_codes]

            if not missing:
                already_ok += 1
                continue

            # Attach missing modules to the user's first non-super_admin role
            target_role = next(
                (r for r in user_role_objs if r.code != 'super_admin'),
                user_role_objs[0],
            )
            for code in missing:
                mod = module_map.get(code) or Module.objects.filter(code=code, is_active=True).first()
                if not mod:
                    continue
                if dry_run:
                    self.stdout.write(f'  [dry] {user.email} → would grant: {code}')
                else:
                    with transaction.atomic():
                        RoleModule.objects.get_or_create(role=target_role, module=mod)
                    newly_granted += 1

            if not dry_run:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'  ✓ {user.email} — granted {len(missing)} missing module(s)'
                    )
                )

        # ── Summary ────────────────────────────────────────────────────
        self.stdout.write(f'\n{sep}')
        self.stdout.write('  SUMMARY')
        self.stdout.write(f'  Total active users processed : {total_users}')
        self.stdout.write(f'  Already had full access      : {already_ok}')
        self.stdout.write(f'  Newly granted module access  : {newly_granted}')
        self.stdout.write(f'  Roles assigned (no-role users): {role_assigned}')
        if dry_run:
            self.stdout.write('  ⚠️  DRY-RUN — no changes were written')
        self.stdout.write(sep + '\n')
