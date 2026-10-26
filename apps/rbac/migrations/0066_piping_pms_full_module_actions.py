"""Grant the full piping_pms action set to roles that hold the module.

Root cause (verified live in production): 0055 backfilled piping_pms with
only the legacy ['create', 'read'] actions, but the Valve MTO workspace
(routes under /api/v1/valve-mto/, mapped to module 'piping_pms' in
action_policy.ROUTE_MODULES) also performs update operations (confirm
drawing, PUT settings, save-rows, legend edits) and export. Users whose
roles only carry the legacy grant get 403 on those endpoints — e.g. the
"Could not confirm the drawing" failure.

'approve' is intentionally excluded: no valve-mto route declares an
approve operation, so the grant stays least-privilege.
"""
from django.db import migrations

MODULE_CODE = 'piping_pms'
GRANTED_ACTIONS = ('read', 'create', 'update', 'delete', 'export')


def grant_piping_pms_actions(apps, schema_editor):
    using = schema_editor.connection.alias
    Module = apps.get_model('rbac', 'Module')
    Permission = apps.get_model('rbac', 'Permission')
    RoleModule = apps.get_model('rbac', 'RoleModule')
    RolePermission = apps.get_model('rbac', 'RolePermission')
    AuditLog = apps.get_model('rbac', 'AuditLog')

    module = Module.objects.using(using).filter(code=MODULE_CODE, is_active=True).first()
    if not module:
        return
    permission_ids = list(Permission.objects.using(using).filter(
        module_id=module.pk, action__in=GRANTED_ACTIONS, is_active=True,
    ).values_list('id', flat=True))
    if not permission_ids:
        return

    role_rows = RoleModule.objects.using(using).filter(
        module_id=module.pk, role__is_active=True,
    ).exclude(role__code='super_admin').values_list('role_id', 'role__name')

    additions = []
    for role_id, role_name in role_rows:
        existing = set(RolePermission.objects.using(using).filter(
            role_id=role_id, permission_id__in=permission_ids,
        ).values_list('permission_id', flat=True))
        missing = [pid for pid in permission_ids if pid not in existing]
        if not missing:
            continue
        additions.extend(RolePermission(role_id=role_id, permission_id=pid) for pid in missing)
        AuditLog.objects.using(using).create(
            user_email='', action='permission_grant', resource_type='Role',
            resource_id=role_id, resource_repr=role_name,
            changes={'module': MODULE_CODE, 'actions_added': list(GRANTED_ACTIONS)},
            metadata={'audit_source': '0066_piping_pms_full_module_actions',
                      'reason': 'Valve MTO workspace needs update/delete/export; 0055 only backfilled create/read.'},
        )
    RolePermission.objects.using(using).bulk_create(additions, ignore_conflicts=True, batch_size=1000)


class Migration(migrations.Migration):
    dependencies = [('rbac', '0065_executive_tab_permissions')]
    operations = [migrations.RunPython(grant_piping_pms_actions, migrations.RunPython.noop)]
