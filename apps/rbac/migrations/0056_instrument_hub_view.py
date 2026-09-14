"""Preserve access to the instrument hub, whose APIs only create/export.

Only untouched policies created by the preceding compatibility migration qualify.
Explicit policies, reviewed roles and user overrides remain unchanged.
"""
from django.db import migrations


def backfill_hub_view(apps, schema_editor):
    using = schema_editor.connection.alias
    AuditLog = apps.get_model('rbac', 'AuditLog')
    RolePermission = apps.get_model('rbac', 'RolePermission')
    RoleModule = apps.get_model('rbac', 'RoleModule')
    Permission = apps.get_model('rbac', 'Permission')
    reviewed = set(AuditLog.objects.using(using).filter(
        resource_type='Role', metadata__audit_source='role_access_review',
    ).values_list('resource_id', flat=True))
    definitions = Permission.objects.using(using).filter(module__code='instrument_datasheet', module__is_active=True, is_active=True)
    previous = set(definitions.filter(action__in=['create', 'export']).values_list('pk', flat=True))
    views = list(definitions.filter(action='read').values_list('pk', flat=True))
    for event in AuditLog.objects.using(using).filter(metadata__audit_source='0055_explicit_legacy_module_actions'):
        if event.resource_id in reviewed or 'instrument_datasheet' not in event.changes.get('legacy_module_actions', {}).get('modules', []):
            continue
        role_id = event.resource_id
        if not RoleModule.objects.using(using).filter(role_id=role_id, role__is_active=True, module__code='instrument_datasheet', module__is_active=True).exists():
            continue
        current = set(RolePermission.objects.using(using).filter(role_id=role_id, permission__module__code='instrument_datasheet').values_list('permission_id', flat=True))
        if not previous or current != previous or not views:
            continue
        RolePermission.objects.using(using).bulk_create([RolePermission(role_id=role_id, permission_id=pk) for pk in views], ignore_conflicts=True)
        AuditLog.objects.using(using).create(
            user_email='', action='permission_grant', resource_type='Role', resource_id=role_id,
            resource_repr=event.resource_repr,
            changes={'module': 'instrument_datasheet', 'actions_added': ['read']},
            metadata={'audit_source': '0056_instrument_hub_view', 'reason': 'Preserve instrument hub navigation for untouched legacy policies.'},
        )


class Migration(migrations.Migration):
    dependencies = [('rbac', '0055_explicit_legacy_module_actions')]
    operations = [migrations.RunPython(backfill_hub_view, migrations.RunPython.noop)]
