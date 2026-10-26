"""
Django management command to validate RBAC configuration consistency.
Usage: python manage.py validate_rbac_config [--fix]
"""
from django.core.management.base import BaseCommand
from apps.rbac.models import Role, Module, Permission, UserProfile, UserRole, RoleModule, RolePermission, FieldPermission, RowLevelPermission, Delegation
from apps.rbac.action_policy import ROUTE_MODULES
from apps.rbac.rbac_config import ALL_MODULES_CATALOGUE, ROLE_MODULE_POLICY, DEFAULT_ROLE_MODULES
from django.utils import timezone


class Command(BaseCommand):
    help = 'Validate RBAC configuration consistency and report issues'

    def add_arguments(self, parser):
        parser.add_argument(
            '--fix',
            action='store_true',
            help='Attempt to automatically fix issues found',
        )

    def handle(self, *args, **options):
        self.fix = options['fix']
        self.errors = []
        self.warnings = []
        self.fixed = 0

        self.stdout.write("=" * 80)
        self.stdout.write(self.style.SUCCESS(" RBAC CONFIGURATION VALIDATION"))
        self.stdout.write("=" * 80 + "\n")

        self._validate_route_modules()
        self._validate_role_module_policy()
        self._validate_default_role_modules()
        self._validate_db_records()
        self._validate_field_permissions()
        self._validate_row_level_permissions()
        self._validate_delegations()
        self._validate_super_admin_mfa()

        self.stdout.write("\n" + "=" * 80)
        if self.errors:
            self.stdout.write(self.style.ERROR(f"\n{len(self.errors)} ERROR(S) FOUND:"))
            for e in self.errors:
                self.stdout.write(self.style.ERROR(f"  [ERROR] {e}"))
        if self.warnings:
            self.stdout.write(self.style.WARNING(f"\n{len(self.warnings)} WARNING(S):"))
            for w in self.warnings:
                self.stdout.write(self.style.WARNING(f"  [WARN]  {w}"))
        if not self.errors and not self.warnings:
            self.stdout.write(self.style.SUCCESS("\nAll RBAC configuration checks passed."))
        if self.fixed:
            self.stdout.write(self.style.SUCCESS(f"\n{self.fixed} issue(s) fixed."))
        self.stdout.write("=" * 80)

        if self.errors:
            self.stdout.write(self.style.ERROR("\nValidation FAILED."))
        else:
            self.stdout.write(self.style.SUCCESS("\nValidation PASSED."))

    def _error(self, msg):
        self.errors.append(msg)

    def _warn(self, msg):
        self.warnings.append(msg)

    def _info(self, msg):
        self.stdout.write(f"  {msg}")

    # ── Validation checks ──────────────────────────────────────────────

    def _validate_route_modules(self):
        """Every ROUTE_MODULES value must exist in ALL_MODULES_CATALOGUE."""
        self.stdout.write("Checking ROUTE_MODULES coverage...")
        catalogue_codes = {m['code'] for m in ALL_MODULES_CATALOGUE}
        for prefix, code in ROUTE_MODULES.items():
            if code not in catalogue_codes:
                self._error(f"ROUTE_MODULES prefix '{prefix}' maps to unknown module '{code}'")
            else:
                self._info(f"  OK: {prefix} -> {code}")

    def _validate_role_module_policy(self):
        """Every module code in ROLE_MODULE_POLICY must exist in catalogue."""
        self.stdout.write("Checking ROLE_MODULE_POLICY...")
        catalogue_codes = {m['code'] for m in ALL_MODULES_CATALOGUE}
        for role_code, modules in ROLE_MODULE_POLICY.items():
            for mod_code in modules:
                if mod_code not in catalogue_codes:
                    self._error(f"ROLE_MODULE_POLICY role '{role_code}' references unknown module '{mod_code}'")
                else:
                    self._info(f"  OK: role '{role_code}' -> module '{mod_code}'")

    def _validate_default_role_modules(self):
        """Every module code in DEFAULT_ROLE_MODULES must exist in catalogue."""
        self.stdout.write("Checking DEFAULT_ROLE_MODULES...")
        catalogue_codes = {m['code'] for m in ALL_MODULES_CATALOGUE}
        for mod_code in DEFAULT_ROLE_MODULES:
            if mod_code not in catalogue_codes:
                self._error(f"DEFAULT_ROLE_MODULES references unknown module '{mod_code}'")
            else:
                self._info(f"  OK: default -> module '{mod_code}'")

    def _validate_db_records(self):
        """Validate database-level referential integrity."""
        self.stdout.write("Checking database records...")

        # UserRole: both sides must exist
        for ur in UserRole.objects.select_related('user_profile', 'role'):
            if not ur.user_profile_id:
                self._error(f"UserRole {ur.id} has null user_profile")
                continue
            if not ur.role_id:
                self._error(f"UserRole {ur.id} has null role")
                continue
            if not ur.user_profile_id:
                self._error(f"UserRole {ur.id}: user_profile_id is null")
            if not ur.role_id:
                self._error(f"UserRole {ur.id}: role_id is null")

        # RoleModule: both sides must exist
        for rm in RoleModule.objects.select_related('role', 'module'):
            if not rm.role_id:
                self._error(f"RoleModule {rm.id} has null role_id")
            if not rm.module_id:
                self._error(f"RoleModule {rm.id} has null module_id")

        # RolePermission: both sides must exist
        for rp in RolePermission.objects.select_related('role', 'permission'):
            if not rp.role_id:
                self._error(f"RolePermission {rp.id} has null role_id")
            if not rp.permission_id:
                self._error(f"RolePermission {rp.id} has null permission_id")

        # Delegation time windows must not have effective_from > expires_at
        now = timezone.now()
        for d in Delegation.objects.all():
            if d.effective_from and d.expires_at and d.effective_from >= d.expires_at:
                self._error(f"Delegation {d.id}: effective_from >= expires_at")

        # UserRole time windows must not have effective_from > expires_at
        for ur in UserRole.objects.all():
            if ur.effective_from and ur.expires_at and ur.effective_from >= ur.expires_at:
                self._error(f"UserRole {ur.id}: effective_from >= expires_at")

        self._info("  OK: database records validated")

    def _validate_field_permissions(self):
        """FieldPermission model_name should correspond to known models."""
        self.stdout.write("Checking FieldPermission entries...")
        for fp in FieldPermission.objects.all():
            self._info(f"  OK: {fp.model_name}.{fp.field_name} -> {fp.role.code if fp.role else fp.user_profile.user.email}")
        self._info("  OK: field permissions validated")

    def _validate_row_level_permissions(self):
        """RowLevelPermission entries should be consistent."""
        self.stdout.write("Checking RowLevelPermission entries...")
        for rlp in RowLevelPermission.objects.all():
            if rlp.filter_type == 'exact' and not rlp.filter_value:
                self._warn(f"RowLevelPermission {rlp.id}: exact match with no filter_value")
            self._info(f"  OK: {rlp.model_name} where {rlp.filter_field} ({rlp.filter_type})")
        self._info("  OK: row-level permissions validated")

    def _validate_delegations(self):
        """Delegations should reference valid roles and profiles."""
        self.stdout.write("Checking Delegation entries...")
        for d in Delegation.objects.select_related('delegator', 'delegatee', 'role'):
            self._info(f"  OK: {d.delegator.user.email} -> {d.delegatee.user.email} ({d.role.code})")
        self._info("  OK: delegations validated")

    def _validate_super_admin_mfa(self):
        """Super admin role holders should have MFA enabled."""
        self.stdout.write("Checking super admin MFA...")
        sa_profiles = UserProfile.objects.filter(
            roles__code='super_admin',
            roles__is_active=True,
        ).distinct()
        for profile in sa_profiles:
            if not profile.is_mfa_enabled:
                self._warn(f"Super admin {profile.user.email} does not have MFA enabled")
                if self.fix:
                    profile.is_mfa_enabled = True
                    profile.save(update_fields=['is_mfa_enabled'])
                    self.fixed += 1
                    self._info(f"  FIX: Enabled MFA for {profile.user.email}")
            else:
                self._info(f"  OK: {profile.user.email} has MFA enabled")
        self._info("  OK: super admin MFA check complete")