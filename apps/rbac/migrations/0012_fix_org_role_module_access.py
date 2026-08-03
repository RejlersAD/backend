"""
Migration 0012 — Fix Organisation-Level Role Module Access
===========================================================
SOFT-CODED: Derives module catalogue and role policy from rbac_config.py.

Root cause (discovered 2026-04-20):
  Users with the 'engineering_common_access' (and similar org-level) roles
  could not access features under '1. Engineering' and '2. COMMON' because
  that role code was not listed in ROLE_MODULE_POLICY.  Migration 0011 only
  applied the policy to roles explicitly named in ROLE_MODULE_POLICY and
  skipped all unrecognised org/custom roles.

What this migration does (all idempotent — safe to re-run):
  1. Apply the updated ROLE_MODULE_POLICY from rbac_config.py to every role
     that is now listed (includes the newly added 'engineering_common_access').

  2. Catch-all sweep — for every role in the DB whose code or name contains
     any of ENGINEERING_ROLE_KEYWORDS but is NOT yet covered by ROLE_MODULE_POLICY,
     assign ENGINEERING_SECTION_MODULES automatically.  This protects against
     future org roles created outside the standard system.

  3. Custom-role sweep — for every role whose code starts with 'custom_',
     find the owning user's other (non-custom) roles and grant that user's
     custom role the union of modules those non-custom roles entitle them to.
     This ensures custom per-user roles always mirror the entitlements of the
     user's primary roles.

SOFT-CODED: To extend coverage, add keywords to ENGINEERING_ROLE_KEYWORDS
or add new role codes to ROLE_MODULE_POLICY in rbac_config.py.
"""
from django.db import migrations

# ── Soft-coded keyword list — any role whose code OR name contains one of
# these strings (case-insensitive) will receive ENGINEERING_SECTION_MODULES
# if it is not already in ROLE_MODULE_POLICY.
ENGINEERING_ROLE_KEYWORDS = [
    'engineering',
    'engineer',
    'common',
    'process',
    'electrical',
    'instrument',
    'mechanical',
    'civil',
    'piping',
    'qhse',
    'design',
    'digitiz',
]


def _get_config():
    from apps.rbac.rbac_config import (
        ALL_MODULES_CATALOGUE,
        ROLE_MODULE_POLICY,
        ENGINEERING_SECTION_MODULES,
    )
    return ALL_MODULES_CATALOGUE, ROLE_MODULE_POLICY, ENGINEERING_SECTION_MODULES


def fix_org_role_access(apps, schema_editor):
    Module     = apps.get_model('rbac', 'Module')
    Role       = apps.get_model('rbac', 'Role')
    RoleModule = apps.get_model('rbac', 'RoleModule')
    UserProfile = apps.get_model('rbac', 'UserProfile')
    UserRole    = apps.get_model('rbac', 'UserRole')
    db_alias   = schema_editor.connection.alias

    ALL_MODULES_CATALOGUE, ROLE_MODULE_POLICY, ENGINEERING_SECTION_MODULES = _get_config()

    # Build quick-lookup maps
    modules_map = {
        m['code']: Module.objects.using(db_alias).filter(code=m['code'], is_active=True).first()
        for m in ALL_MODULES_CATALOGUE
    }
    # Drop any that don't exist in DB yet (should not happen after 0011)
    modules_map = {k: v for k, v in modules_map.items() if v is not None}

    eng_module_objs = [modules_map[c] for c in ENGINEERING_SECTION_MODULES if c in modules_map]

    linked_count   = 0
    catchall_count = 0
    custom_count   = 0

    # ── Step 1: Apply updated ROLE_MODULE_POLICY (now includes engineering_common_access) ──
    print('\n  [0012] Step 1 — Apply updated ROLE_MODULE_POLICY to all known roles…')
    for role_code, module_codes in ROLE_MODULE_POLICY.items():
        try:
            role = Role.objects.using(db_alias).get(code=role_code)
        except Role.DoesNotExist:
            continue
        for mod_code in module_codes:
            mod = modules_map.get(mod_code)
            if not mod:
                continue
            _, created = RoleModule.objects.using(db_alias).get_or_create(
                role=role, module=mod
            )
            if created:
                linked_count += 1
                print(f'    ✓ linked: {role_code} → {mod_code}')

    print(f'  [0012] Step 1 done: {linked_count} new policy links applied.')

    # ── Step 2: Catch-all — any "engineering-flavoured" role not in policy ──
    print('\n  [0012] Step 2 — Catch-all sweep for unlisted engineering-flavoured roles…')
    policy_codes = set(ROLE_MODULE_POLICY.keys())
    all_roles = Role.objects.using(db_alias).all()

    for role in all_roles:
        code_lower = (role.code or '').lower()
        name_lower = (role.name or '').lower()

        # Skip roles already covered by Step 1
        if role.code in policy_codes:
            continue
        # Skip custom per-user roles (handled in Step 3)
        if code_lower.startswith('custom_'):
            continue

        matched_keyword = next(
            (kw for kw in ENGINEERING_ROLE_KEYWORDS if kw in code_lower or kw in name_lower),
            None,
        )
        if not matched_keyword:
            continue

        for mod in eng_module_objs:
            _, created = RoleModule.objects.using(db_alias).get_or_create(
                role=role, module=mod
            )
            if created:
                catchall_count += 1

        print(f'    ✓ catch-all applied to role: {role.code!r} (matched keyword: {matched_keyword!r})')

    print(f'  [0012] Step 2 done: {catchall_count} new links from catch-all sweep.')

    # ── Step 3: Custom per-user roles — inherit modules from the user's primary roles ──
    print('\n  [0012] Step 3 — Custom per-user role inheritance sweep…')
    custom_roles = Role.objects.using(db_alias).filter(code__startswith='custom_')

    for custom_role in custom_roles:
        # Find users who have this custom role
        user_roles_qs = UserRole.objects.using(db_alias).filter(
            role=custom_role
        ).select_related('user_profile')

        for user_role_entry in user_roles_qs:
            profile = user_role_entry.user_profile
            # Find all the non-custom roles this user also has
            primary_roles = UserRole.objects.using(db_alias).filter(
                user_profile=profile
            ).exclude(
                role__code__startswith='custom_'
            ).select_related('role')

            inherited_codes = set()
            for pr in primary_roles:
                if pr.role.code in ROLE_MODULE_POLICY:
                    inherited_codes.update(ROLE_MODULE_POLICY[pr.role.code])

            for mod_code in inherited_codes:
                mod = modules_map.get(mod_code)
                if not mod:
                    continue
                _, created = RoleModule.objects.using(db_alias).get_or_create(
                    role=custom_role, module=mod
                )
                if created:
                    custom_count += 1

    print(f'  [0012] Step 3 done: {custom_count} new links from custom-role inheritance.')

    total = linked_count + catchall_count + custom_count
    print(f'\n  [0012] ✅ Complete — {total} total new role-module links created.')
    print('  [0012]    All users with engineering/common roles now have full module access.')


def reverse_noop(apps, schema_editor):
    """No-op reverse — never delete role-module links as other data may depend on them."""
    print('  [0012] Reverse: no-op (role-module links are not deleted on reverse).')


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0011_sync_new_modules_and_policy'),
    ]

    operations = [
        migrations.RunPython(fix_org_role_access, reverse_noop),
    ]
