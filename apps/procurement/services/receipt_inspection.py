"""Read-only receipt register projection; declarations are not inspection proof."""
from collections import Counter
from datetime import date
import re
from uuid import UUID

from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.rbac.action_policy import request_action_allowed


QUEUES = ('all', 'pending', 'exceptions', 'accepted', 'rejected', 'partial',
          'ndt_pending', 'missing_certificates', 'traceability_gaps')
QUALITY_FIELDS = ('quality_check_passed', 'dimensional_check_passed',
                  'visual_inspection_passed', 'material_verification_passed')
FACET_LIMIT = 200


def declaration_names(value):
    """Reject malformed legacy JSON rather than silently completing its evidence."""
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        return None
    return sorted({item.strip().upper() for item in value})


def ndt_requirement(value):
    text = re.sub(r'\s+', ' ', str(value or '').strip().lower()).rstrip('.')
    if text in {'none', 'n/a', 'na', 'nil', 'not applicable', 'not required', 'no ndt', 'no ndt required'}:
        return 'not_required'
    if text in {'yes', 'required', 'ndt required'}:
        return 'required'
    # Only complete, explicit method declarations qualify. Conditional prose is unknown.
    method = r'(?:ut|rt|mt|pt|ultrasonic testing|radiographic testing|magnetic particle testing|dye penetrant testing)'
    if re.fullmatch(r'(?:100% )?' + method + r'(?: required)?', text):
        return 'required'
    return 'unassessed'


def receipt_evidence(receipt):
    po = receipt.purchase_order
    required = declaration_names(po.required_certifications)
    received = declaration_names(receipt.certificates_received)
    assessed = bool(required) and received is not None
    missing = sorted(set(required) - set(received)) if assessed else []
    certificates = {
        'status': ('missing' if missing else 'recorded') if assessed else 'unassessed',
        'required': required or [], 'received': received or [], 'missing': missing,
        'required_count': len(required) if required else None,
        'received_count': len(received) if received is not None else None,
        'matched_count': len(required) - len(missing) if assessed else None,
        'reason': ('Exact certificate names compared with recorded PO requirements; documents are not verified.'
                   if assessed else 'A nonempty PO requirement list and valid received declarations are needed.'),
    }
    heat = declaration_names(receipt.heat_numbers)
    heat_required = bool(po.heat_numbers_required)
    trace_status = ('unassessed' if heat is None else 'recorded' if heat else
                    'missing' if heat_required else 'not_required')
    traceability = {
        'status': trace_status, 'required': heat_required, 'heat_numbers': heat or [],
        'count': len(heat) if heat is not None else None,
        'reason': 'Recorded heat-number declarations and PO requirement flag; item-level traceability is not verified.',
    }
    requirement = ndt_requirement(po.ndt_requirements)
    ndt = {
        'status': ('recorded' if receipt.ndt_performed else 'not_recorded' if requirement == 'required'
                   else 'not_required' if requirement == 'not_required' else 'unassessed'),
        'requirement_status': requirement, 'performed': bool(receipt.ndt_performed),
        'results_recorded': bool(str(receipt.ndt_results or '').strip()),
        'reason': 'NDT performance is a recorded flag, not a verified test result. Conditional PO text is unassessed.',
    }
    return {'certificates': certificates, 'traceability': traceability, 'ndt': ndt}


def project_metadata(po):
    if po.enterprise_project_id:
        project = po.enterprise_project
        return {'project_id': f'core:{project.pk}', 'project_number': project.code,
                'project_name': project.name, 'project_source': 'core'}
    if po.project_id:
        project = po.project
        return {'project_id': f'procurement:{project.pk}', 'project_number': project.project_number,
                'project_name': project.project_name, 'project_source': 'procurement'}
    number = (po.project_number or po.rad_project_no or '').strip()
    return {'project_id': None, 'project_number': number or None, 'project_name': None,
            'project_source': 'recorded_project_number' if number else None}


def capabilities(request):
    def allowed(module, action):
        return bool(request and request_action_allowed(request, module, action))
    return {**{action: allowed('procurement_receipts', action)
               for action in ('create', 'update', 'approve', 'export')},
            'read_purchase_orders': allowed('procurement_orders', 'read')}


def enrich_receipt(data, receipt, request):
    from apps.rbac.approval_eligibility import require_configured_approval
    from rest_framework.exceptions import PermissionDenied

    po = receipt.purchase_order
    grants = capabilities(request)
    def can_decide(operation):
        if receipt.status != 'pending' or not grants['approve']:
            return False
        try:
            require_configured_approval(request.user, 'procurement_receipts', receipt, operation)
        except (PermissionDenied, ValidationError):
            return False
        return True
    data.update({
        'vendor_id': str(po.vendor_id), 'vendor_name': po.vendor.name,
        **project_metadata(po), 'po_category': po.category,
        'required_certifications': po.required_certifications,
        'heat_numbers_required': po.heat_numbers_required, 'ndt_requirements': po.ndt_requirements,
        'evidence': receipt_evidence(receipt),
        'capabilities': {'update': grants['update'], 'accept': can_decide('accept'),
                         'reject': can_decide('reject_delivery'), 'export': grants['export']},
    })
    return data


def selected_queue(params):
    queue = params.get('queue') or 'all'
    if queue not in QUEUES:
        raise ValidationError({'queue': 'Unknown receipt queue.'})
    return queue


def _uuid(value, name):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError({name: 'A valid identifier is required.'})


def filter_receipts(queryset, params):
    """Filters before queue selection; shared by list and summary."""
    if params.get('status'):
        queryset = queryset.filter(status=params['status'])
    quality = params.get('quality_check')
    if quality == 'pending':
        queryset = queryset.filter(status='pending').exclude(exception_query())
    elif quality == 'passed':
        queryset = queryset.filter(status='accepted', quality_check_passed=True).exclude(exception_query())
    elif quality == 'failed':
        queryset = queryset.filter(exception_query())
    if params.get('vendor'):
        queryset = queryset.filter(purchase_order__vendor_id=_uuid(params['vendor'], 'vendor'))
    if params.get('project'):
        kind, _, value = params['project'].partition(':')
        if kind == 'core' and value.isdecimal():
            queryset = queryset.filter(purchase_order__enterprise_project_id=int(value))
        elif kind == 'procurement':
            queryset = queryset.filter(purchase_order__enterprise_project__isnull=True,
                                       purchase_order__project_id=_uuid(value, 'project'))
        else:
            raise ValidationError({'project': 'Use core:<id> or procurement:<uuid>.'})
    for param, field in [('inspector', 'inspector_name'), ('category', 'purchase_order__category')]:
        if params.get(param):
            queryset = queryset.filter(**{field: params[param]})
    dates = {}
    for param, lookup in [('received_from', 'gte'), ('received_to', 'lte')]:
        if params.get(param):
            try:
                raw = params[param]
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw):
                    raise ValueError
                dates[param] = date.fromisoformat(raw)
            except (TypeError, ValueError):
                raise ValidationError({param: 'Use a valid YYYY-MM-DD date.'})
            queryset = queryset.filter(**{f'receipt_date__{lookup}': dates[param]})
    if len(dates) == 2 and dates['received_from'] > dates['received_to']:
        raise ValidationError({'received_to': 'Must be on or after received_from.'})
    search = str(params.get('search') or '').strip()
    if search:
        query = Q()
        for field in ('receipt_number', 'purchase_order__po_number', 'delivery_note_number',
                      'purchase_order__vendor__name', 'inspector_name',
                      'purchase_order__enterprise_project__code', 'purchase_order__enterprise_project__name',
                      'purchase_order__project__project_number', 'purchase_order__project__project_name',
                      'purchase_order__project_number', 'purchase_order__rad_project_no'):
            query |= Q(**{field + '__icontains': search})
        queryset = queryset.filter(query)
    return queryset


def receipt_queues(receipt, evidence=None):
    evidence = evidence or receipt_evidence(receipt)
    queues = {'all', receipt.status}
    if receipt.status == 'rejected' or any(getattr(receipt, field) is False for field in QUALITY_FIELDS):
        queues.add('exceptions')
    if evidence['certificates']['status'] == 'missing':
        queues.add('missing_certificates')
    if evidence['traceability']['required'] and evidence['traceability']['status'] == 'missing':
        queues.add('traceability_gaps')
    if evidence['ndt']['requirement_status'] == 'required' and not evidence['ndt']['performed']:
        queues.add('ndt_pending')
    return queues


def exception_query():
    query = Q(status='rejected')
    for field in QUALITY_FIELDS:
        query |= Q(**{field: False})
    return query


def apply_queue(queryset, queue):
    if queue == 'all':
        return queryset
    if queue in ('pending', 'accepted', 'rejected', 'partial'):
        return queryset.filter(status=queue)
    if queue == 'exceptions':
        return queryset.filter(exception_query())
    # Evidence is legacy JSON/free text. One portable, conservative projection is
    # used by both summary counts and list selection, before server pagination.
    ids = [row.pk for row in queryset.iterator(chunk_size=500) if queue in receipt_queues(row)]
    return queryset.filter(pk__in=ids)


def order_receipts(queryset, params):
    ordering = params.get('ordering') or '-created_at'
    allowed = {'receipt_date', 'receipt_number', 'created_at', 'updated_at', 'status'}
    if ordering.lstrip('-') not in allowed:
        raise ValidationError({'ordering': 'Unsupported receipt ordering.'})
    return queryset.order_by(ordering, 'id')


def inspection_summary(queryset, request):
    today = timezone.localdate()
    month_start = today.replace(day=1)
    counts = dict.fromkeys(QUEUES, 0)
    vendors, projects, inspectors = {}, {}, Counter()
    certificates_assessed = trace_required = trace_recorded = trace_unknown = month_count = 0
    updated = None
    for receipt in queryset.iterator(chunk_size=500):
        evidence = receipt_evidence(receipt)
        for queue in receipt_queues(receipt, evidence):
            if queue in counts:
                counts[queue] += 1
        month_count += int(month_start <= receipt.receipt_date <= today)
        certificates_assessed += int(evidence['certificates']['status'] != 'unassessed')
        if evidence['traceability']['required']:
            trace_required += 1
            trace_recorded += int(evidence['traceability']['status'] == 'recorded')
            trace_unknown += int(evidence['traceability']['status'] == 'unassessed')
        if receipt.updated_at and (updated is None or receipt.updated_at > updated):
            updated = receipt.updated_at
        po = receipt.purchase_order
        key = str(po.vendor_id)
        vendors.setdefault(key, {'id': key, 'name': po.vendor.name, 'count': 0})['count'] += 1
        project = project_metadata(po)
        if project['project_id']:
            key = project['project_id']
            projects.setdefault(key, {'id': key, 'number': project['project_number'],
                                     'name': project['project_name'], 'count': 0})['count'] += 1
        if receipt.inspector_name.strip():
            inspectors[receipt.inspector_name] += 1
    total = counts['all']
    reviewed = counts['accepted'] + counts['partial'] + counts['rejected']
    queue = selected_queue(request.query_params)
    options = {
        'vendors': sorted(vendors.values(), key=lambda row: (row['name'].casefold(), row['id'])),
        'projects': sorted(projects.values(), key=lambda row: (row['number'] or '', row['id'])),
        'inspectors': [{'value': key, 'count': count} for key, count in sorted(inspectors.items())],
    }
    facets = {key: rows[:FACET_LIMIT] for key, rows in options.items()}
    facets['truncated'] = {key: len(rows) > FACET_LIMIT for key, rows in options.items()}
    facets['total_options'] = {key: len(rows) for key, rows in options.items()}
    return {
        'schema_version': '1.0', 'generated_at': timezone.now().isoformat(),
        'as_of_date': today.isoformat(), 'source_updated_at': updated.isoformat() if updated else None,
        'source_timestamp_kind': 'record_updated_at', 'status': 'available',
        'selected_queue': queue, 'filtered_count': counts[queue], 'counts': counts,
        'kpis': {
            'receipts_this_month': {'status': 'available', 'value': month_count,
                                    'period_start': month_start.isoformat(), 'period_end': today.isoformat(),
                                    'definition': 'Receipt records dated this calendar month through today, within the selected filters.'},
            'open_inspections': {'status': 'available', 'value': counts['pending'],
                                 'definition': 'Receipt records with Pending Inspection status.'},
            'missing_certificates': {
                'status': ('partial' if certificates_assessed < total else 'available') if certificates_assessed else 'unavailable',
                'value': counts['missing_certificates'] if certificates_assessed else None,
                'assessed_count': certificates_assessed, 'unassessed_count': total - certificates_assessed,
                'definition': 'Receipts missing an explicitly required certificate name among comparable declaration lists; files are not verified.',
            },
            'acceptance_rate': {'status': 'available' if reviewed else 'unavailable',
                                'value': round(100 * counts['accepted'] / reviewed, 1) if reviewed else None,
                                'numerator': counts['accepted'], 'denominator': reviewed,
                                'definition': 'Accepted receipts divided by accepted, partially accepted and rejected receipts; excludes pending.'},
            'traceability_coverage': {
                'status': ('partial' if trace_unknown else 'available') if trace_required else 'unavailable',
                'value': round(100 * trace_recorded / trace_required, 1) if trace_required else None,
                'numerator': trace_recorded, 'denominator': trace_required, 'unassessed_count': trace_unknown,
                'definition': 'Receipts declaring heat numbers divided by receipts whose PO requires them; declarations are not item-level verification.',
            },
        },
        'unavailable_metrics': {
            'ncr_open': {'status': 'unavailable', 'value': None, 'reason': 'A linked nonconformity register is not available.'},
            'inspection_time': {'status': 'unavailable', 'value': None, 'reason': 'Inspection start and completion timestamps are not recorded.'},
        },
        'capabilities': capabilities(request), 'filter_options': facets,
        'scope': {'visibility': 'Authorized shared receipt register', 'summary': 'Filters and search before queue selection',
                  'date_basis': 'Recorded receipt date', 'time_zone': timezone.get_current_timezone_name()},
        'limitations': [
            'Default true quality flags are not proof an inspection was performed or passed.',
            'Certificate and heat-number declarations do not verify attachments or physical items.',
            'NDT requirements use only explicit recorded method declarations; conditional or missing text is unassessed.',
            'Receipt date is the record creation date; it is not an independently recorded physical arrival time.',
        ],
    }
