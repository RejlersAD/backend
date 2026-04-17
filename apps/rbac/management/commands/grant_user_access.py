"""
One-shot management command: verify & grant full access to a user.
Soft-coded: works for any email, confirms active status, super-admin role,
and ensures every module (or a specific module) is reachable.

Usage:
    python manage.py grant_user_access --email user@example.com
    python manage.py grant_user_access --email user@example.com --modules qhse,pid_analysis
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from apps.rbac.models import Role, UserProfile, UserRole, Organization, Module, RoleModule

User = get_user_model()


class Command(BaseCommand):
    help = 'Verify and grant full access (super-admin + modules) to a user by email'

    def add_arguments(self, parser):
        parser.add_argument('--email', type=str, required=True, help='Target user email')
        parser.add_argument(
            '--modules',
            type=str,
            default='',
            help='Comma-separated extra module codes to explicitly add to the super_admin role '
                 '(optional — super_admin already includes all modules seeded via seed_rbac)',
        )

    def handle(self, *args, **options):
        email = options['email']
        extra_modules = [m.strip() for m in options['modules'].split(',') if m.strip()]

        sep = '=' * 60
        self.stdout.write(f'\n{sep}')
        self.stdout.write(f'GRANT ACCESS: {email}')
        self.stdout.write(sep)

        # ── 1. Find user ──────────────────────────────────────────────
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'❌ User not found: {email}'))
            return

        # ── 2. Ensure account is active & verified ────────────────────
        changed = False
        if not user.is_active:
            user.is_active = True
            changed = True
            self.stdout.write('  ✓ Activated user account')
        if not user.is_verified:
            user.is_verified = True
            changed = True
            self.stdout.write('  ✓ Marked user as verified')
        if changed:
            user.save()

        self.stdout.write(f'  • Active:    {user.is_active}')
        self.stdout.write(f'  • Verified:  {user.is_verified}')
        self.stdout.write(f'  • Staff:     {user.is_staff}')

        # ── 3. Ensure RBAC profile exists & is active ─────────────────
        default_org, _ = Organization.objects.get_or_create(
            code='default',
            defaults={
                'name': 'Default Organization',
                'primary_contact_email': 'admin@rejlers.com',
                'is_active': True,
            },
        )

        profile, p_created = UserProfile.objects.get_or_create(
            user=user,
            defaults={'organization': default_org, 'status': 'active'},
        )
        if p_created:
            self.stdout.write('  ✓ Created RBAC profile')
        if not profile.organization:
            profile.organization = default_org
        if profile.status != 'active':
            profile.status = 'active'
            self.stdout.write('  ✓ Activated RBAC profile')
        profile.save()

        # ── 4. Ensure super_admin role exists ─────────────────────────
        try:
            super_admin_role = Role.objects.get(code='super_admin')
        except Role.DoesNotExist:
            self.stdout.write(self.style.ERROR('❌ super_admin role not found — run seed_rbac first'))
            return

        user_role, r_created = UserRole.objects.get_or_create(
            user_profile=profile,
            role=super_admin_role,
            defaults={'is_primary': True},
        )
        if r_created:
            self.stdout.write(self.style.SUCCESS('  ✅ Assigned Super Admin role'))
        else:
            self.stdout.write('  ✓ Super Admin role already assigned')

        # ── 5. Ensure super_admin role has ALL active modules ─────────
        all_modules = Module.objects.filter(is_active=True)
        newly_added = []
        for module in all_modules:
            _, m_created = RoleModule.objects.get_or_create(
                role=super_admin_role,
                module=module,
                defaults={'granted_by': None},
            )
            if m_created:
                newly_added.append(module.code)

        if newly_added:
            self.stdout.write(f'  ✓ Added {len(newly_added)} missing modules to super_admin role: {newly_added}')
        else:
            self.stdout.write(f'  ✓ super_admin role already covers all {all_modules.count()} modules')

        # ── 6. Handle any explicitly requested extra modules ─────────-
        if extra_modules:
            for code in extra_modules:
                mod = Module.objects.filter(code=code, is_active=True).first()
                if not mod:
                    self.stdout.write(self.style.WARNING(f'  ⚠ Module not found: {code}'))
                    continue
                _, created = RoleModule.objects.get_or_create(
                    role=super_admin_role, module=mod, defaults={'granted_by': None}
                )
                status = 'added' if created else 'already present'
                self.stdout.write(f'  ✓ Module {code}: {status}')

        # ── 7. Clear permission cache ─────────────────────────────────
        try:
            from apps.rbac.cache_utils import clear_user_cache
            clear_user_cache(str(user.id))
            self.stdout.write('  ✓ Permission cache cleared')
        except Exception:
            pass  # non-critical

        # ── 8. Final verification ─────────────────────────────────────
        accessible_modules = [m.code for m in profile.get_all_modules()]
        qhse_ok = profile.has_module_access('qhse')
        all_roles = list(
            UserRole.objects.filter(user_profile=profile).values_list('role__code', flat=True)
        )

        self.stdout.write(f'\n{sep}')
        self.stdout.write(self.style.SUCCESS('✅ ACCESS GRANT COMPLETE'))
        self.stdout.write(sep)
        self.stdout.write(f'  Email:         {email}')
        self.stdout.write(f'  Roles:         {all_roles}')
        self.stdout.write(f'  QHSE access:   {qhse_ok}')
        self.stdout.write(f'  All modules:   {accessible_modules}')
        self.stdout.write(sep + '\n')
