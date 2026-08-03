"""
Migration 0037 — Seed Planning Package Module
==============================================
Splits "6.2 Planning Package" out from the shared 'project_control' module
code into its own dedicated module: 'planning_package'.

Previously both sidebar sub-features under "6. Project Control"
  - 6.1 Projects            -> moduleCode 'project_control'
  - 6.2 Planning Package    -> moduleCode 'project_control'  (SAME code)
were gated by the single 'project_control' module, so Role & Access
Management could only grant/revoke both sub-features together — Planning
Package never appeared as its own toggle.

This migration seeds the new 'planning_package' Module (idempotent,
get_or_create) and grants it to the same roles that already have
'project_control' seeded by migration 0020 (super_admin, admin), mirroring
the existing project_control grant so access does not regress for any
role that currently sees "6.1 Projects".

See also: rbac_config.py ALL_MODULES_CATALOGUE / ROLE_MODULE_POLICY['admin']
(single source of truth going forward) and frontend
rbacAccess/RoleManagement.jsx NON_ENGINEERING_GROUPS['project_control'].
"""
from django.db import migrations

NEW_MODULE = {
    'code':        'planning_package',
    'name':        'Planning Package',
    'icon':        'Cube',
    'order':       823,
    'description': 'AI-assisted work package planning — WBS, schedule, EDDR, manhours and narrative generation',
}

# Roles that should receive this module — mirrors migration 0020's
# project_control grant so access does not regress.
GRANT_TO_ROLES = ['super_admin', 'admin']


def seed_planning_package_module(apps, schema_editor):
    Module     = apps.get_model('rbac', 'Module')
    Role       = apps.get_model('rbac', 'Role')
    RoleModule = apps.get_model('rbac', 'RoleModule')
    db_alias   = schema_editor.connection.alias

    module, created = Module.objects.using(db_alias).get_or_create(
        code=NEW_MODULE['code'],
        defaults={
            'name':        NEW_MODULE['name'],
            'description': NEW_MODULE['description'],
            'icon':        NEW_MODULE['icon'],
            'order':       NEW_MODULE['order'],
            'is_active':   True,
        },
    )
    if not created:
        module.name        = NEW_MODULE['name']
        module.description = NEW_MODULE['description']
        module.icon        = NEW_MODULE['icon']
        module.order       = NEW_MODULE['order']
        module.is_active   = True
        module.save(using=db_alias)
    action = 'created' if created else 'updated'
    print(f'  [0037] ✓ Module {action}: {module.code}')

    for role_code in GRANT_TO_ROLES:
        try:
            role = Role.objects.using(db_alias).get(code=role_code)
        except Role.DoesNotExist:
            print(f'  [0037] ⚠ Role not found: {role_code} — skipping')
            continue
        _, created = RoleModule.objects.using(db_alias).get_or_create(role=role, module=module)
        if created:
            print(f'  [0037] ✓ Granted {module.code} → {role_code}')

    # Mirror the grant for any OTHER role that already has 'project_control'
    # (covers roles granted ad-hoc outside rbac_config.py, e.g. via a one-off
    # script) so Planning Package access does not regress for them either.
    try:
        project_control = Module.objects.using(db_alias).get(code='project_control')
    except Module.DoesNotExist:
        project_control = None

    if project_control is not None:
        role_ids_with_pc = RoleModule.objects.using(db_alias).filter(
            module=project_control
        ).values_list('role_id', flat=True)
        for role_id in role_ids_with_pc:
            _, created = RoleModule.objects.using(db_alias).get_or_create(role_id=role_id, module=module)
            if created:
                print(f'  [0037] ✓ Mirrored project_control grant → role_id={role_id}')


def noop(apps, schema_editor):
    """Intentional no-op reverse — do not strip modules on rollback."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0036_add_human_resource_role'),
    ]

    operations = [
        migrations.RunPython(seed_planning_package_module, noop),
    ]
