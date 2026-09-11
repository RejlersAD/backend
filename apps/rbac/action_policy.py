"""Module/action policy shared by route guards and explicit DRF permissions.

More specific route prefixes precede their parent. Public and independently
authenticated workflows are exempted by callback identity, never by user input.
"""
import re

from django.utils import timezone


ROUTE_MODULES = {
    'pid/pressure-instruments/': 'process_datasheet',
    'pid/instrument-index/': 'instrument_index',
    'pid/cable-block-diagram/': 'instrument_datasheet',
    'pid/equipment/': 'pid_equipment_list',
    'pid/': 'pid_analysis', 'pid-export/': 'pid_analysis',
    'pid-verification/extract-valve-mto/': 'piping_pms',
    'pid-verification/': 'pid_analysis', 'pid-checker-v2/': 'pid_analysis',
    'pfd/': 'pfd_to_pid', 'pfd-quality/': 'pfd_quality',
    'crs/': 'crs_documents', 'designiq/lists/': 'pid_line_list',
    'designiq/critical-lists/': 'piping_critical_line_list',
    'designiq/': 'designiq', 'data-mining/': 'data_mining',
    'process-datasheet/': 'process_datasheet',
    'electrical-datasheet/': 'electrical_datasheet',
    'electrical-checklist/': 'electrical_checklist',
    'sld-verification/': 'electrical_sld',
    'instrument-tools/cable-block-diagram/': 'instrument_datasheet',
    'instrument-tools/cable-schedule/': 'instrument_datasheet',
    'instrument-tools/': 'instrument_io_list',
    'instrument-io-workflow/': 'instrument_io_list',
    'cross-recommendation/': 'designiq', 'non-teff/': 'non_teff_metadata',
    'spec-customization/': 'spec_customization',
    'valve-standards/': 'valve_standards_reference',
    'core/s3/': 'file_storage', 'wrench/': 'wrench_integration',
    'project-control/': 'project_control', 'projects/': 'project_control',
    'planning-intelligence/': 'planning_package',
    'procurement/vendors/': 'procurement_vendors',
    'procurement/requisitions/': 'procurement_requisitions',
    'procurement/orders/': 'procurement_orders',
    'procurement/po-documents/': 'procurement_orders',
    'procurement/receipts/': 'procurement_receipts',
    'procurement/': 'procurement',
    'qhse/areas/detailed/': 'qhse_detailed', 'qhse/areas/quality/': 'qhse_quality',
    'qhse/areas/health-safety/': 'qhse_health_safety',
    'qhse/areas/environmental/': 'qhse_environmental', 'qhse/areas/energy/': 'qhse_energy',
    'qhse/audits/': 'qhse_quality', 'qhse/': 'qhse',
    'finance/invoices/combined_summary/': 'finance_overview',
    'finance/invoices/': 'finance_incoming',
    'finance/approval-routes/': 'finance_incoming',
    'finance/dashboard/': 'finance_overview',
    'finance/employee-salary-info/': 'payroll', 'finance/salary-components/': 'payroll',
    'finance/employee-salary-components/': 'payroll', 'finance/payroll-runs/': 'payroll',
    'finance/salary-approvals/': 'payroll', 'finance/salary-emails/': 'payroll',
    'finance/salary-audit-logs/': 'payroll', 'finance/payroll-schedule/': 'payroll',
    'finance/payroll-workflows/': 'payroll',
    'finance/': 'finance_salary', 'invoice-tracker/': 'finance_outgoing',
    'sales/clients/': 'sales_clients', 'sales/contacts/': 'sales_clients',
    'sales/analytics/clients/': 'sales_clients',
    'sales/deals/': 'sales_opportunities', 'sales/activities/': 'sales_opportunities',
    'sales/analytics/pipeline/': 'sales_opportunities',
    'sales/analytics/activities/': 'sales_opportunities',
    'sales/quotes/': 'sales_proposals', 'sales/frameworks/': 'sales_frameworks',
    'sales/forecasts/': 'sales_forecasts', 'sales/project-handovers/': 'sales_handovers',
    'sales/mailbox-connections/': 'sales_email_intake', 'sales/email-intakes/': 'sales_email_intake',
    'sales/': 'sales_overview',
    'hr/self-service-workspace/': 'hr_self_service',
    'hr/service-requests/': 'hr_self_service',
    'hr/': 'hr_management', 'payroll/': 'payroll', 'payroll-engine/': 'payroll',
    'onboarding/': 'hr_onboarding', 'site-visits/': 'timesheet',
    'timesheet/my-attendance/': 'hr_self_service', 'timesheet/': 'timesheet',
    'enquiry/': 'enquiry_management', 'enquiries/': 'enquiry_management',
    'rbac/users/': 'user_mgmt', 'user-management/': 'user_mgmt', 'users/employees/': 'hr_management',
    'rbac/roles/': 'role_access_mgmt', 'rbac/modules/': 'role_access_mgmt',
    'rbac/permissions/': 'role_access_mgmt', 'rbac/access-requests/': 'role_access_mgmt',
    'rbac/organizations/': 'org_settings', 'rbac/audit-logs/': 'audit_logs',
    'rbac/storage/': 'file_storage', 'rbac/analytics/': 'admin_dashboard',
    'rbac/admin/': 'admin_dashboard', 'rbac/ai-champion/': 'ai_champion',
    'dashboard/personal/project-control/': 'project_control',
    'dashboard/aws-status/': 'file_storage', 'dashboard/aws-report/': 'file_storage',
    'analytics/': 'reports', 'marketing-analytics/': 'reports',
}

# These handlers retain their existing token/API-key or requester/owner checks.
INDEPENDENT_WORKFLOWS = {
    ('apps.instrument_tools.views', 'MetaView'),
    ('apps.finance.views', 'get_approval_details'),
    ('apps.finance.views', 'submit_approval_decision'),
    ('apps.finance.views', 'approval_action'),
    ('apps.timesheet.mirror_views', 'ingest_events'),
    ('apps.timesheet.mirror_views', 'heartbeat'),
    ('apps.timesheet.mirror_views', 'ingest_users'),
    ('apps.sales.intake_views', 'sales_email_intake'),
    *{('apps.core.views_enquiry', name) for name in (
        'submit_enquiry', 'enquiry_options', 'public_enquiry_feedback',
        'my_enquiries', 'my_enquiry_detail', 'my_enquiry_response',
        'my_enquiry_resolution', 'my_enquiry_feedback', 'enquiry_attachment_download')},
}

# Account bootstrap reads must remain available before module grants are loaded.
SELF_SERVICE_ACTIONS = {
    'UserProfileViewSet': {'me', 'my_profile', 'my_permissions', 'my_modules', 'change_password'},
    'PermissionViewSet': {'check_permission', 'my_permissions'},
    'ModuleViewSet': {'my_modules'},
    'AccessRequestViewSet': {'mine', 'create'},
}

# These operations perform their own requester/stage-assignee checks. They do
# not confer access to the module register; explicit user action denies still win.
RECORD_SCOPED_ACTIONS = {
    ('apps.procurement.views', 'PurchaseOrderViewSet'): {'retrieve', 'pending_for_me', 'approve', 'reject'},
    ('apps.procurement.views', 'PurchaseRequisitionViewSet'): {
        'retrieve', 'pending_for_me', 'pm_approve', 'pm_reject', 'vp_approve', 'vp_reject',
        'eng_manager_approve', 'eng_manager_reject', 'manager_projects_approve',
        'manager_projects_reject', 'process_dynamic_approval', 'process_dynamic_rejection',
    },
}

READ_OPERATIONS = {'list', 'retrieve', 'preview', 'stats', 'statistics', 'summary',
                   'dashboard', 'status', 'health', 'search', 'lookup',
                   'check_permission', 'check_access', 'check_pr_number', 'by_module', 'options',
                   'discover_svc_url', 'test_connection', 'ai_settings_test', 'payment_batch_status'}
UPDATE_OPERATIONS = {'update', 'partial_update', 'assign', 'unassign', 'reassign',
                     'revoke', 'cancel', 'submit', 'restore', 'resolve', 'ignore',
                     'implement', 'transition', 'complete', 'close', 'reopen',
                     'activate', 'deactivate', 'mark', 'set', 'edit', 'save',
                     'review_access', 'permission_overrides', 'flush', 'sync',
                     'acknowledge', 'accounting_posted', 'apply_benefit', 'apply_deduction',
                     'ai_settings', 'bulk_deduction_action', 'bulk_deduction_reverse',
                     'change_password', 'change_status', 'checkout', 'connect_my_outlook',
                     'connect_outlook', 'disconnect_outlook', 'dismiss', 'enter_negotiation',
                     'force_revert', 'fulfill', 'inject_token', 'investigate', 'link_radai_account',
                     'lock', 'move_stage', 'progress', 'rate_vendor', 'reassign_approver_action',
                     'reassign_reviewer_action', 'rebuild_generated_logic', 'recalculate',
                     'recheck', 'recheck_output', 'reconcile', 'reconcile_status', 'recompute',
                     'refresh', 'regenerate', 'repair_identity', 'reprocess', 'request_revision',
                     'resend_missing_approvals', 'reset_password', 'retry', 'return_for_correction',
                     'reverse', 'revert', 'review', 'reviews', 'stop', 'supersede', 'validate',
                     'validate_item', 'remove_member', 'calculate', 'escalate_rejection', 'refer_rejection'}
APPROVE_OPERATIONS = {'accept', 'approval_decision', 'bid_decision', 'confirm_po', 'decide',
                      'decision', 'deny', 'issue', 'payment_batch', 'payment_operations',
                      'process_dynamic_approval', 'process_dynamic_rejection',
                      'project_manager_decision', 'publish', 'review_decision'}
CREATE_OPERATIONS = {
    'add_comment', 'add_company_response', 'add_contractor_response', 'add_dependency',
    'add_documents', 'add_fact', 'add_hold', 'add_member', 'add_revision', 'add_step',
    'ai_takeoff', 'analyze', 'analyze_chain', 'analyze_five_stages', 'analyze_hybrid', 'ask',
    'attach_file', 'base_extraction', 'baseline', 'batch_quality_check', 'batch_upload',
    'build_generation_plan_action', 'build_schedule_basis_action', 'build_workable_plan',
    'bulk_create', 'bulk_import', 'bulk_send_approved', 'bulk_send_email', 'bulk_upload',
    'calculate_health_score', 'calculate_win_probability', 'capture_controls', 'cell_comments',
    'change_detection', 'clone', 'combined_upload', 'comment', 'comments', 'compliance_check',
    'convert_to_opportunity', 'convert_to_po', 'convert_to_project', 'create_employee',
    'create_project', 'create_revision', 'create_share', 'create_version', 'duplicate',
    'ensure_employee_workflow', 'execute_pipeline', 'extract', 'extract_checklist', 'extract_data',
    'extract_from_pdf', 'extract_handwriting', 'extract_pdf_comments', 'generate',
    'generate_dg_datasheet', 'generate_forecast', 'generate_lv_switchgear_datasheet', 'generate_qr',
    'generate_smart_datasheet', 'generate_switchgear_datasheet', 'generate_transformer_datasheet',
    'generate_upload_url', 'governance_comments', 'governance_items', 'import_boq', 'import_excel',
    'import_full_xlsx', 'import_reviewed', 'import_signed_pdf', 'import_xlsx', 'intelligent_generate',
    'library_watch_start', 'materialize', 'my_signature', 'output_drawings', 'process',
    'quality_check', 're_extract', 'recommend_vendors', 'reserve_number', 'run_assurance',
    'run_three_way_match', 'save_output', 'score_lead', 'send_email', 'send_test_mail',
    'send_test_teams', 'send_to_client', 'send_to_vendor', 'smart_upload', 'snapshots', 'start',
    'start_checklist_stage', 'start_it_checklist', 'start_review', 'start_verification',
    'suggest_cell', 'test_with_s3_pfd', 'track_activity', 'track_ai_usage', 'trigger', 'trigger_now',
    'upload', 'upload_adjustments', 'upload_and_add_revision', 'upload_and_process',
    'upload_attachment', 'upload_enriched_pid', 'upload_external', 'upload_file',
    'upload_my_profile_photo', 'upload_pfd', 'upload_pid', 'upload_profile_photo',
    'upload_reference_doc', 'use', 'use_template', 'validate_diagram', 'verify', 'verify_pid',
    'verify_transformer_datasheet', 'verify_upload',
}


def route_module(path):
    path = re.sub(r'^/?api/v\d+/', '', path)
    return next((ROUTE_MODULES[prefix] for prefix in sorted(ROUTE_MODULES, key=len, reverse=True)
                 if path.startswith(prefix)), None)


def operation_action(request, view):
    """Custom operation semantics take precedence over the HTTP method."""
    method = getattr(request, 'method', 'GET')
    if method == 'OPTIONS':
        return None
    explicit = getattr(view, 'permission_action', None)
    if explicit:
        return explicit
    name = getattr(view, 'action', '') or view.__class__.__name__
    name = re.sub(r'(?<!^)(?=[A-Z])', '_', name).lower().replace('-', '_')
    words = set(name.split('_'))
    route = getattr(getattr(request, 'resolver_match', None), 'url_name', '') or ''
    words.update(route.lower().replace('-', '_').split('_'))
    if name in {'retention_cleanup'}:
        return 'delete'
    if name == 'remove_member':
        return 'update'
    if words & {'delete', 'destroy', 'remove', 'purge'}:
        return 'delete'
    if name in APPROVE_OPERATIONS or words & {'approve', 'reject', 'authorize', 'release'}:
        return 'approve'
    if name in {'generate_pdf'} or words & {'export', 'download'}:
        return 'export'
    if name == 'generate_upload_url' or {'presigned', 'upload'}.issubset(words):
        return 'create'
    if method in {'GET', 'HEAD'}:
        return 'read'
    if name == 'save_output':
        return 'create'
    if name in READ_OPERATIONS or words & {'preview', 'search', 'lookup'}:
        return 'read'
    if name in UPDATE_OPERATIONS or words & UPDATE_OPERATIONS:
        return 'update'
    if method == 'POST' and getattr(view, 'action', None) not in {None, '', 'create'}:
        # Unknown custom writes must be classified before they can ship.
        return 'create' if name in CREATE_OPERATIONS else None
    return {'POST': 'create', 'PUT': 'update', 'PATCH': 'update', 'DELETE': 'delete'}.get(method)


def module_action_allowed(user, module_code, action):
    """Effective action grants; user denies win, including over super-admin."""
    from .models import Module, Permission, RoleModule, RolePermission, UserProfile
    from .rbac_config import is_module_enabled
    if not user or not user.is_authenticated or not user.is_active:
        return False
    try:
        profile = user.rbac_profile
    except UserProfile.DoesNotExist:
        return False
    if profile.is_deleted or profile.status != 'active' or (
        profile.locked_until and profile.locked_until > timezone.now()
    ):
        return False
    if not is_module_enabled(module_code) or not Module.objects.filter(code=module_code, is_active=True).exists():
        return False
    roles = list(profile.roles.filter(is_active=True).values_list('pk', 'code'))
    role_ids = [pk for pk, code in roles]
    super_admin = user.is_superuser or any(code == 'super_admin' for pk, code in roles)
    if not super_admin and not RoleModule.objects.filter(module__code=module_code, role_id__in=role_ids).exists():
        return False
    definitions = set(Permission.objects.filter(module__code=module_code, is_active=True, action=action).values_list('pk', flat=True))
    if not definitions:
        return False
    overrides = dict(profile.permission_overrides.filter(permission_id__in=definitions).values_list('permission_id', 'allowed'))
    if False in overrides.values():
        return False
    if super_admin:
        return True
    allowed = {pk for pk, granted in overrides.items() if granted}
    allowed.update(RolePermission.objects.filter(role_id__in=role_ids, permission_id__in=definitions).values_list('permission_id', flat=True))
    # A partially selected legacy action cell is not a full module-wide grant.
    return definitions.issubset(allowed)


def request_action_allowed(request, module, action):
    """Reuse duplicate gates within one HTTP request, never across requests."""
    from rest_framework.request import Request
    if not isinstance(request, Request):
        return module_action_allowed(request.user, module, action)
    if not hasattr(request, '_module_action_decisions'):
        request._module_action_decisions = {}
    key = (request.user.pk, module, action)
    if key not in request._module_action_decisions:
        request._module_action_decisions[key] = module_action_allowed(request.user, module, action)
    return request._module_action_decisions[key]


def record_workflow_not_denied(user, module, action):
    """Additional guard only; the original record-assignment check must run."""
    from .models import Module, UserProfile
    from .rbac_config import is_module_enabled
    if not user or not user.is_authenticated or not user.is_active:
        return False
    try:
        profile = user.rbac_profile
    except UserProfile.DoesNotExist:
        return False
    if profile.is_deleted or profile.status != 'active' or (profile.locked_until and profile.locked_until > timezone.now()):
        return False
    return (is_module_enabled(module) and Module.objects.filter(code=module, is_active=True).exists()
            and not profile.permission_overrides.filter(allowed=False, permission__module__code=module,
                                                       permission__action=action, permission__is_active=True).exists())


def effective_action_map(profile):
    """Batched current-user UI policy, with the same complete-cell semantics."""
    from collections import defaultdict
    from .models import Permission
    if profile.is_deleted or profile.status != 'active' or not profile.user.is_active or (
        profile.locked_until and profile.locked_until > timezone.now()
    ):
        return {}
    effective = {permission.pk for permission in profile.get_all_permissions()}
    grouped = defaultdict(set)
    for pk, code, action in Permission.objects.filter(is_active=True, module__is_active=True).values_list('pk', 'module__code', 'action'):
        grouped[(code, action)].add(pk)
    result = defaultdict(list)
    for (code, action), ids in grouped.items():
        if ids.issubset(effective):
            result[code].append(action)
    return dict(result)


def request_module(request, view):
    """Resolve registered service subdivisions before any broad module alias."""
    from .service_catalogue import required_service
    from .rbac_config import ALL_MODULES_CATALOGUE
    if view.__class__.__module__ == 'apps.procurement.views' and view.__class__.__name__ == 'PurchaseRequisitionViewSet' and getattr(view, 'action', '') == 'convert_to_po':
        return 'procurement_orders'
    module = route_module(getattr(request, 'path', ''))
    specific = required_service(view)
    return specific if specific in {row['code'] for row in ALL_MODULES_CATALOGUE} else module or specific


def additional_actions(request):
    """Approval and download flags must not tunnel through generic write APIs."""
    actions = set()
    if request.method not in {'POST', 'PUT', 'PATCH'}:
        return actions
    data = request.data
    def inspect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                approval_field = key in {'status', 'approval', 'decision', 'stage'} or key.endswith('_status')
                if approval_field and isinstance(item, str):
                    if item.strip().lower() in {'approved', 'approve', 'rejected', 'reject', 'not_approved', 'declined', 'released', 'authorized', 'paid'}:
                        actions.add('approve')
                if key in {'approved', 'is_approved', 'rejected', 'is_rejected'} and str(item).strip().lower() in {'true', '1', 'yes', 'on'}:
                    actions.add('approve')
                if key == 'download' and str(item).strip().lower() in {'true', '1', 'yes', 'on'}:
                    actions.add('export')
                if isinstance(item, (dict, list)):
                    inspect(item)
        elif isinstance(value, list):
            for item in value:
                inspect(item)
    inspect(data)
    if str(request.query_params.get('download', '')).strip().lower() in {'true', '1', 'yes', 'on'}:
        actions.add('export')
    return actions


def resource_modules(request, view, default):
    """Resolve list subdivisions from stored resources, not a spoofable query.

An unfiltered mixed-list read/export requires access to all returned list types.
For updates, both the stored and proposed types must be authorized.
"""
    if view.__class__.__name__ != 'EngineeringListItemViewSet':
        return {default}
    from . import action_resources
    return action_resources.engineering_list_modules(request, view, default)
