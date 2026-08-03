"""
Migration 0011 — Sync New Modules & Re-apply Role-Module Policy
===============================================================
SOFT-CODED: This migration derives its full module catalogue and role policy
from rbac_config.py (ALL_MODULES_CATALOGUE + ROLE_MODULE_POLICY).

No module codes are hardcoded here. To extend coverage in the future,
add entries to ALL_MODULES_CATALOGUE / ENGINEERING_SECTION_MODULES in
rbac_config.py and re-run this migration (or create a new one).

What this migration does (all idempotent — safe to re-run):
  1. Create any module in ALL_MODULES_CATALOGUE that is not yet in the DB.
     Previously missing examples:
       - non_teff_metadata  (Non-TEFF Metadata Extractor — 1.7 Digitization)
       - pfd_quality        (PFD Quality Checker — core Process)
  2. Re-apply ROLE_MODULE_POLICY to all existing roles in the DB.
     Ensures every engineering role (process_engineer, electrical_engineer,
     instrument_engineer, mechanical_engineer, civil_engineer, piping_engineer,
     qhse_engineer, design_engineer, project_manager, viewer, admin) has all
     modules listed in ENGINEERING_SECTION_MODULES, including newly added ones.
  3. Assign all newly created modules to the super_admin role as well.

Why this was needed:
  Users with "1. Engineering" and "2. COMMON" access (any engineering-level
  role) could not see newly added features (e.g. 1.7 Digitization → Non-TEFF
  Metadata) because the corresponding Module DB row did not exist when their
  RoleModule assignments were last applied.  The fix seeds the missing modules
  then links them to every role that should have them per ROLE_MODULE_POLICY.
"""
from django.db import migrations


# ── Pull config from rbac_config at migration time (not at import time) ──────
def _get_config():
    from apps.rbac.rbac_config import (
        ALL_MODULES_CATALOGUE,
        ROLE_MODULE_POLICY,
    )
    return ALL_MODULES_CATALOGUE, ROLE_MODULE_POLICY


def sync_modules_and_policy(apps, schema_editor):
    """
    Idempotent: creates missing modules, updates order/name if changed,
    and re-applies the full ROLE_MODULE_POLICY to all existing roles.
    """
    Module    = apps.get_model('rbac', 'Module')
    Role      = apps.get_model('rbac', 'Role')
    RoleModule = apps.get_model('rbac', 'RoleModule')
    db_alias  = schema_editor.connection.alias

    ALL_MODULES_CATALOGUE, ROLE_MODULE_POLICY = _get_config()

    # ── Step 1: Seed / update every module in the catalogue ──────────────────
    modules_map   = {}
    created_count = 0
    updated_count = 0

    for entry in ALL_MODULES_CATALOGUE:
        obj, created = Module.objects.using(db_alias).get_or_create(
            code=entry['code'],
            defaults={
                'name':        entry['name'],
                'description': entry.get('description', ''),
                'icon':        entry.get('icon', ''),
                'order':       entry.get('order', 0),
                'is_active':   True,
            },
        )
        if created:
            created_count += 1
            print(f'  [0011] ✓ Created module: {entry["code"]}')
        else:
            # Keep name/order in sync with the config without overwriting custom overrides
            changed = False
            if obj.order != entry.get('order', 0):
                obj.order = entry.get('order', 0)
                changed = True
            if not obj.is_active:
                obj.is_active = True
                changed = True
            if changed:
                obj.save(using=db_alias)
                updated_count += 1

        modules_map[entry['code']] = obj

    print(f'  [0011] Modules: {created_count} created, {updated_count} updated, '
          f'{len(modules_map) - created_count - updated_count} already current.')

    # ── Step 2: Apply ROLE_MODULE_POLICY to all roles that exist in the DB ───
    linked_count  = 0
    skipped_roles = []

    for role_code, module_codes in ROLE_MODULE_POLICY.items():
        try:
            role = Role.objects.using(db_alias).get(code=role_code)
        except Role.DoesNotExist:
            skipped_roles.append(role_code)
            continue

        for mod_code in module_codes:
            mod = modules_map.get(mod_code)
            if not mod:
                # Should not happen after Step 1, but guard defensively
                print(f'  [0011] ⚠ Module not in catalogue: {mod_code} (for role {role_code})')
                continue

            _, created = RoleModule.objects.using(db_alias).get_or_create(
                role=role, module=mod
            )
            if created:
                linked_count += 1

    print(f'  [0011] Role-module links: {linked_count} new assignments applied.')
    if skipped_roles:
        print(f'  [0011] Roles not yet in DB (skipped): {skipped_roles}')

    # ── Step 3: Assign ALL modules to super_admin role (belt-and-braces) ─────
    super_admin_extra = 0
    try:
        sa_role = Role.objects.using(db_alias).get(code='super_admin')
        for mod in modules_map.values():
            _, c = RoleModule.objects.using(db_alias).get_or_create(
                role=sa_role, module=mod
            )
            if c:
                super_admin_extra += 1
        if super_admin_extra:
            print(f'  [0011] super_admin: {super_admin_extra} new module links added.')
    except Role.DoesNotExist:
        print('  [0011] super_admin role not found — skipping super_admin step.')

    print('  [0011] Done — all modules and role policies are now in sync.')


def reverse_sync(apps, schema_editor):
    """
    Reversal is a no-op: we never delete module rows or RoleModule entries
    because other data (user assignments, audit logs) may reference them.
    The migration is safe to re-run forward at any time.
    """
    print('  [0011] Reverse: no destructive changes — migration is a no-op on reverse.')


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0010_engineer_profile_table'),
    ]

    operations = [
        migrations.RunPython(
            sync_modules_and_policy,
            reverse_sync,
        ),
    ]
