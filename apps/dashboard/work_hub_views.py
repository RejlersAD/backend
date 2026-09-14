"""Project recorded frontend navigation onto verified workspaces, never API paths.

The client tracker records pathname (without query parameters), its broad module
guess and source=frontend-route. Only the pathname is used for classification.
Routes below mirror existing App.jsx destinations and their effective read gates.
Record-detail visits link to the workspace list, avoiding stale object access.
"""
from collections import Counter
import re

from django.utils import timezone

from apps.rbac.action_policy import module_action_allowed


VIEW_DEDUPLICATION_SECONDS = 30

# Path: (friendly title/source, effective read module; None means authenticated).
WORKSPACES = {
    '/projects': ('Project Control', 'project_control'),
    '/executive': ('Executive overview', 'executive_dashboard'),
    '/planning-packages': ('Planning packages', 'planning_package'),
    '/approvals': ('Approvals', None),
    '/profile': ('HR Self Service', None),
    '/my-enquiries': ('My enquiries', None),
    '/admin/enquiries': ('Enquiry Operations', 'enquiry_management'),
    '/hr': ('Human Resources', 'hr_management'),
    '/hr/employees': ('Employee directory', 'hr_management'),
    '/hr/payroll': ('Payroll', 'payroll'),
    '/hr/leave': ('Leave management', 'payroll'),
    '/hr/attendance': ('Attendance', 'payroll'),
    '/hr/onboarding': ('Onboarding', 'hr_onboarding'),
    '/finance': ('Finance overview', 'finance_overview'),
    '/finance/incoming-invoices': ('Incoming invoices', 'finance_incoming'),
    '/finance/outgoing-invoices': ('Outgoing invoices', 'finance_outgoing'),
    '/finance/salary-slip': ('Salary slips', 'finance_salary'),
    '/sales': ('Sales overview', 'sales_overview'),
    '/sales/opportunities': ('Sales opportunities', 'sales_opportunities'),
    '/sales/proposals': ('Sales proposals', 'sales_proposals'),
    '/sales/clients': ('Clients and contacts', 'sales_clients'),
    '/sales/frameworks': ('Framework agreements', 'sales_frameworks'),
    '/sales/forecasts': ('Sales forecasts', 'sales_forecasts'),
    '/sales/email-intake': ('Sales email intake', 'sales_email_intake'),
    '/sales/project-handovers': ('Project handovers', 'sales_handovers'),
    '/procurement': ('Procurement', 'procurement'),
    '/procurement/vendors': ('Vendors', 'procurement_vendors'),
    '/procurement/requisitions': ('Purchase requisitions', 'procurement_requisitions'),
    '/procurement/orders': ('Purchase orders', 'procurement_orders'),
    '/procurement/receipts': ('Goods receipts', 'procurement_receipts'),
    '/qhse': ('QHSE', 'qhse'),
    '/qhse/general/detailed': ('QHSE project register', 'qhse_detailed'),
    '/qhse/general/quality': ('Quality', 'qhse_quality'),
    '/qhse/general/health-safety': ('Health and Safety', 'qhse_health_safety'),
    '/qhse/general/environmental': ('Environment', 'qhse_environmental'),
    '/qhse/general/energy': ('Energy', 'qhse_energy'),
    '/engineering/process/datasheet': ('Process Datasheets', 'process_datasheet'),
    '/engineering/process/pid-verification': ('P&ID analysis', 'pid_analysis'),
    '/engineering/process/pid-verification-v1': ('P&ID analysis', 'pid_analysis'),
    '/engineering/process/pid-checker-v2': ('P&ID analysis', 'pid_analysis'),
    '/engineering/process/line-list': ('Line list', 'pid_analysis'),
    '/engineering/process/equipment-list': ('Equipment list', 'pid_analysis'),
    '/pfd/upload': ('PFD to P&ID', 'pfd_to_pid'),
    '/engineering/electrical': ('Electrical Engineering', 'electrical_datasheet'),
    '/engineering/electrical/datasheet': ('Electrical Datasheets', 'electrical_datasheet'),
    '/engineering/electrical/transformer-verification': ('Transformer verification', 'electrical_datasheet'),
    '/engineering/electrical/checklist': ('Electrical checklist', 'electrical_checklist'),
    '/engineering/electrical/sld': ('Single-line diagrams', 'electrical_sld'),
    '/engineering/instrument/datasheet': ('Instrument Datasheets', 'instrument_datasheet'),
    '/engineering/instrument/index': ('Instrument index', 'instrument_index'),
    '/engineering/mechanical/datasheet': ('Mechanical Datasheets', 'mechanical_datasheet'),
    '/engineering/civil/datasheet': ('Civil Datasheets', 'civil_datasheet'),
    '/engineering/piping/pms': ('Piping material specifications', 'piping_pms'),
    '/engineering/piping/datasheet': ('Piping Datasheets', 'piping_datasheet'),
    '/engineering/digitization/smart-plant-3d': ('SmartPlant 3D', 'smart_plant_3d'),
    '/engineering/digitization/spec-customization': ('Specification customization', 'spec_customization'),
    '/crs/documents': ('Document review', 'crs_documents'),
    '/crs/multiple-revision': ('Revision comparison', 'crs_documents'),
    '/data-mining': ('Data mining', 'data_mining'),
    '/designiq': ('DesignIQ', 'designiq'),
}

# Verified workspaces with child/detail routes. Never accept a generic /admin,
# /finance or /sales prefix: those would grant unrelated services a false label.
CHILD_WORKSPACES = (
    '/engineering/process/datasheet', '/engineering/electrical/datasheet',
    '/engineering/instrument/datasheet', '/crs/documents', '/designiq',
    '/sales/opportunities', '/sales/proposals', '/sales/clients', '/sales/frameworks',
    '/sales/forecasts', '/sales/email-intake', '/sales/project-handovers',
    '/finance/incoming-invoices', '/finance/outgoing-invoices',
    '/procurement/vendors', '/procurement/requisitions', '/procurement/orders', '/procurement/receipts',
    '/hr/employees',
)


def workspace_destination(path):
    """Return a canonical route/label/grant only for safe recorded app paths."""
    if not isinstance(path, str) or len(path) > 600 or not re.fullmatch(r'/[A-Za-z0-9_/-]*', path):
        return None
    if '//' in path:
        return None
    path = path.rstrip('/') or '/'
    route = path if path in WORKSPACES else None
    if route is None:
        route = next((prefix for prefix in CHILD_WORKSPACES if path.startswith(prefix + '/')), None)
    if route is None and re.fullmatch(r'/planning-workspace/[A-Za-z0-9_-]+', path):
        route = '/planning-packages'
    if route is None and re.fullmatch(r'/proposal-workspace/[A-Za-z0-9_-]+', path):
        route = '/projects'
    if route is None and re.fullmatch(r'/pid/(upload|history|report(?:/[A-Za-z0-9_-]+)?)', path):
        route = '/engineering/process/pid-verification-v1'
    if route is None and path.startswith('/pfd/'):
        route = '/pfd/upload'
    return (route, *WORKSPACES[route]) if route else None


def workspace_view_summary(Event, user, start, end, limit):
    """Read only this actor; stream full history so the cap does not cap counts."""
    records = Event.objects.filter(user=user, timestamp__gte=start, timestamp__lte=end,
                                   action_type='view', success=True, metadata__source='frontend-route')
    daily, preview, count, collapsed = Counter(), [], 0, 0
    allowed = {}
    previous_path, previous_time = None, None
    for row in records.only('id', 'timestamp', 'metadata').order_by('-timestamp', '-pk').iterator(chunk_size=500):
        metadata = row.metadata if isinstance(row.metadata, dict) else {}
        path = metadata.get('path')
        destination = workspace_destination(path)
        duplicate = (destination is not None and path == previous_path and previous_time is not None
                     and (previous_time - row.timestamp).total_seconds() <= VIEW_DEDUPLICATION_SECONDS)
        previous_path, previous_time = path, row.timestamp
        if destination is None:
            continue
        if duplicate:
            collapsed += 1
            continue
        count += 1
        daily[timezone.localdate(row.timestamp).isoformat()] += 1
        if len(preview) >= limit:
            continue
        route, label, module = destination
        if module not in allowed:
            allowed[module] = module is None or module_action_allowed(user, module, 'read')
        preview.append({'id': f'view:{row.pk}', 'type': 'workspace_view', 'category': 'workspace_navigation',
                        'title': f'Viewed {label}', 'source_label': label, 'basis': 'workspace_view',
                        'status_label': 'Viewed', 'success': True, 'timestamp': row.timestamp.isoformat(),
                        'description': f'You viewed {label}.',
                        'route': route if allowed[module] else None})
    return {'count': count, 'daily': daily, 'rows': preview, 'collapsed_count': collapsed}
