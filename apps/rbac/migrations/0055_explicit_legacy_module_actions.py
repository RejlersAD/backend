"""Make previously implicit module access explicit, without replacing policies.

Only existing assigned role/module pairs with no action policy are backfilled.
Reviewed roles, partial action policies and user overrides are never changed.
New module assignments do not acquire permissions automatically.
"""
from collections import defaultdict
from django.db import migrations

# Frozen capability inventory from the registered routes at this rollout.
LEGACY_ACTIONS = {'admin_dashboard': ['create', 'delete', 'read', 'update'],
 'ai_champion': ['create', 'export', 'read', 'update'],
 'audit_logs': ['read'],
 'civil_datasheet': ['read'],
 'crs_documents': ['create', 'delete', 'export', 'read', 'update'],
 'data_mining': ['create', 'delete', 'export', 'read', 'update'],
 'designiq': ['create', 'delete', 'read', 'update'],
 'digitization_datasheet': ['read'],
 'electrical_checklist': ['create', 'delete', 'export', 'read', 'update'],
 'electrical_datasheet': ['approve', 'create', 'delete', 'export', 'read', 'update'],
 'electrical_sld': ['create', 'delete', 'export', 'read', 'update'],
 'enquiry_management': ['create', 'delete', 'export', 'read', 'update'],
 'file_storage': ['create', 'delete', 'export', 'read', 'update'],
 'finance_incoming': ['approve', 'create', 'delete', 'export', 'read', 'update'],
 'finance_outgoing': ['create', 'delete', 'read', 'update'],
 'finance_overview': ['read'],
 'finance_salary': ['approve', 'create', 'delete', 'export', 'read', 'update'],
 'hr_management': ['approve', 'create', 'delete', 'read', 'update'],
 'hr_onboarding': ['approve', 'create', 'delete', 'export', 'read', 'update'],
 'hr_self_service': ['approve', 'create', 'delete', 'read', 'update'],
 'instrument_datasheet': ['create', 'export'],
 'instrument_index': ['create', 'export', 'read'],
 'instrument_io_list': ['create', 'delete', 'export', 'read', 'update'],
 'mechanical_datasheet': ['read'],
 'non_teff_metadata': ['create', 'delete', 'export', 'read', 'update'],
 'org_settings': ['create', 'delete', 'read', 'update'],
 'payroll': ['approve', 'create', 'delete', 'export', 'read', 'update'],
 'pfd_quality': ['create', 'delete', 'export', 'read', 'update'],
 'pfd_to_pid': ['approve', 'create', 'delete', 'export', 'read', 'update'],
 'pid_analysis': ['approve', 'create', 'delete', 'export', 'read', 'update'],
 'pid_equipment_list': ['create', 'delete', 'export', 'read', 'update'],
 'pid_line_list': ['create', 'delete', 'export', 'read', 'update'],
 'piping_critical_line_list': ['create', 'delete', 'export', 'read', 'update'],
 'piping_datasheet': ['read'],
 'piping_pms': ['create', 'read'],
 'planning_package': ['approve', 'create', 'delete', 'export', 'read', 'update'],
 'process_datasheet': ['create', 'delete', 'export', 'read', 'update'],
 'procurement': ['approve', 'create', 'delete', 'read', 'update'],
 'procurement_orders': ['approve', 'create', 'delete', 'export', 'read', 'update'],
 'procurement_receipts': ['approve', 'create', 'delete', 'read', 'update'],
 'procurement_requisitions': ['approve', 'create', 'delete', 'export', 'read', 'update'],
 'procurement_vendors': ['create', 'delete', 'read', 'update'],
 'project_control': ['approve', 'create', 'delete', 'export', 'read', 'update'],
 'qhse': ['create', 'delete', 'read', 'update'],
 'qhse_detailed': ['create', 'delete', 'read', 'update'],
 'qhse_energy': ['create', 'delete', 'read', 'update'],
 'qhse_environmental': ['create', 'delete', 'read', 'update'],
 'qhse_health_safety': ['create', 'delete', 'read', 'update'],
 'qhse_quality': ['create', 'delete', 'read', 'update'],
 'reports': ['read'],
 'role_access_mgmt': ['approve', 'create', 'delete', 'read', 'update'],
 'sales_clients': ['create', 'delete', 'read', 'update'],
 'sales_email_intake': ['approve', 'create', 'delete', 'read', 'update'],
 'sales_forecasts': ['approve', 'create', 'delete', 'read', 'update'],
 'sales_frameworks': ['create', 'delete', 'read', 'update'],
 'sales_handovers': ['approve', 'create', 'delete', 'read', 'update'],
 'sales_opportunities': ['approve', 'create', 'delete', 'read', 'update'],
 'sales_overview': ['create', 'read'],
 'sales_proposals': ['approve', 'create', 'delete', 'read', 'update'],
 'smart_plant_3d': ['read'],
 'spec_customization': ['create', 'delete', 'export', 'read', 'update'],
 'timesheet': ['approve', 'create', 'delete', 'export', 'read', 'update'],
 'user_mgmt': ['create', 'delete', 'export', 'read', 'update'],
 'valve_standards_reference': ['read', 'update'],
 'wrench_integration': ['create', 'delete', 'export', 'read', 'update']}


def backfill_legacy_actions(apps, schema_editor):
    using = schema_editor.connection.alias
    Permission = apps.get_model('rbac', 'Permission')
    RolePermission = apps.get_model('rbac', 'RolePermission')
    RoleModule = apps.get_model('rbac', 'RoleModule')
    AuditLog = apps.get_model('rbac', 'AuditLog')
    actions = ['read', 'create', 'update', 'approve', 'delete', 'export']
    reviewed = set(AuditLog.objects.using(using).filter(
        resource_type='Role', metadata__audit_source='role_access_review',
    ).values_list('resource_id', flat=True))
    # Any existing action definition assigned to the pair is an explicit policy,
    # including legacy Execute/Admin grants that must not be expanded implicitly.
    explicit_pairs = set(RolePermission.objects.using(using).values_list('role_id', 'permission__module_id'))
    definitions = defaultdict(list)
    for permission_id, module_id, module_code, action in Permission.objects.using(using).filter(
        is_active=True, module__is_active=True, action__in=actions,
    ).values_list('id', 'module_id', 'module__code', 'action'):
        if action in LEGACY_ACTIONS.get(module_code, []):
            definitions[module_id].append(permission_id)
    additions = []
    audited = defaultdict(list)
    for role_id, role_name, module_id, module_code in RoleModule.objects.using(using).filter(
        role__is_active=True, module__is_active=True,
    ).exclude(role__code='super_admin').values_list('role_id', 'role__name', 'module_id', 'module__code'):
        if role_id in reviewed or (role_id, module_id) in explicit_pairs:
            continue
        for permission_id in definitions[module_id]:
            additions.append(RolePermission(role_id=role_id, permission_id=permission_id))
        if definitions[module_id]:
            audited[(role_id, role_name)].append(module_code)
    RolePermission.objects.using(using).bulk_create(additions, ignore_conflicts=True, batch_size=1000)
    for (role_id, role_name), modules in audited.items():
        AuditLog.objects.using(using).create(
            user_email='', action='permission_grant', resource_type='Role',
            resource_id=role_id, resource_repr=role_name,
            changes={'legacy_module_actions': {'modules': sorted(modules), 'actions_by_module': {code: LEGACY_ACTIONS[code] for code in modules}}},
            metadata={'audit_source': '0055_explicit_legacy_module_actions',
                      'reason': 'Preserve existing implicit module operations as explicit action grants; existing action policies and user overrides retained.'},
        )


class Migration(migrations.Migration):
    dependencies = [('rbac', '0054_complete_module_action_catalogue')]
    # Preserve subsequently reviewed grants during rollback.
    operations = [migrations.RunPython(backfill_legacy_actions, migrations.RunPython.noop)]
