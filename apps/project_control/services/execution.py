"""A complete EPC acceptance cycle without manufacturing source approvals."""
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.core.project_models import Project
from apps.planning_intelligence.models import ActivityProgressUpdate, ScheduleVersion
from apps.planning_intelligence.services.project_controls import capture_control_snapshot
from apps.procurement.services.receipt_inspection import QUALITY_FIELDS, receipt_evidence
from ..access import can_write_enterprise_project
from ..execution_models import EPCWorkEvent, EPCWorkItem
from ..epc_models import WBSActivityLink, control_scope


def can_accept_work(user, project):
    return bool(user and user.is_authenticated and user.is_active and (
        user.is_staff or user.is_superuser or project.owner_id == user.pk or
        project.memberships.filter(user=user, is_active=True, role='project_manager').exists()))


def document_manifest(item):
    return list(item.documents.order_by('id').values('id', 'project_id', 'file', 'original_filename',
        'size_bytes', 'content_type', 'is_deleted', 'updated_at'))


def _json(value):
    if isinstance(value, dict):
        return {key: _json(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json(val) for val in value]
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _quantity(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result >= 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _line_key(row):
    if not isinstance(row, dict):
        return ''
    return str(row.get('code') or row.get('sku') or row.get('description') or row.get('item') or row.get('name') or '').strip().casefold()


def review_evidence(item):
    """Copy actual decision inputs so later source corrections require a new review."""
    po = item.purchase_order
    material = None
    if item.requires_materials and po:
        material = {
            'purchase_order': str(po.pk), 'project': po.enterprise_project_id,
            'approved_by': po.approved_by_id, 'approved_at': po.approved_at,
            **{field: getattr(po, field) for field in ('items', 'status', 'required_certifications',
                'heat_numbers_required', 'ndt_requirements')},
            'receipts': [{
                'id': str(row.pk), 'date': row.receipt_date, 'status': row.status,
                **{field: getattr(row, field) for field in (*QUALITY_FIELDS, 'items_received',
                    'certificates_received', 'heat_numbers', 'ndt_performed', 'ndt_results',
                    'inspection_report_number', 'attachments')},
            } for row in po.receipts.filter(receipt_date__lte=item.data_date).order_by('id')],
        }
    return _json({'documents': document_manifest(item), 'criteria': item.acceptance_criteria,
        'data_date': item.data_date, 'reviewer': item.reviewer_id, 'evidence_note': item.evidence_note,
        'requires_materials': item.requires_materials, 'material': material})


def material_readiness(item):
    """Require explicit, unambiguous quantity evidence for every ordered line."""
    def result(ready, reason, receipts=None):
        return {'ready': ready, 'reason': reason, 'receipts': receipts or []}
    if not item.requires_materials:
        return result(True, 'Material acceptance is not required for this work item.')
    po = item.purchase_order
    if not po or po.enterprise_project_id != item.project_id:
        return result(False, 'Link a purchase order assigned to this project.')
    if po.status not in ('sent', 'acknowledged', 'in_progress', 'partially_received', 'completed'):
        return result(False, 'The purchase order has not been approved for delivery.')
    if not po.approved_by_id or not po.approved_at or timezone.localtime(po.approved_at).date() > item.data_date:
        return result(False, 'The purchase order needs recorded approval by the work data date.')
    if not isinstance(po.items, list) or not po.items:
        return result(False, 'The purchase order needs item-level quantities.')
    ordered = {}
    for row in po.items:
        key = _line_key(row)
        quantity = _quantity(row.get('quantity', row.get('qty'))) if isinstance(row, dict) else None
        if not key or key in ordered or quantity is None or quantity <= 0:
            return result(False, 'Order line identifiers or quantities are missing or ambiguous.')
        ordered[key] = quantity
    totals = {key: Decimal('0') for key in ordered}
    receipts = []
    for receipt in po.receipts.filter(status__in=['accepted', 'partial']).order_by('id'):
        if receipt.receipt_date > item.data_date or not all(getattr(receipt, field) for field in QUALITY_FIELDS):
            continue
        evidence = receipt_evidence(receipt)
        if evidence['certificates']['status'] == 'unassessed' or evidence['traceability']['status'] == 'unassessed' or evidence['ndt']['requirement_status'] == 'unassessed':
            continue
        if evidence['certificates']['missing'] or evidence['traceability']['status'] == 'missing':
            continue
        if evidence['ndt']['requirement_status'] == 'required' and not (
                evidence['ndt']['performed'] and evidence['ndt']['results_recorded']):
            continue
        if not isinstance(receipt.items_received, list):
            continue
        lines = []
        seen = set()
        for row in receipt.items_received:
            key = _line_key(row)
            accepted = _quantity(row.get('accepted_qty')) if isinstance(row, dict) else None
            received = _quantity(row.get('received_qty')) if isinstance(row, dict) else None
            if key not in ordered or key in seen or accepted is None or received is None or accepted > received:
                return result(False, 'Accepted receipt lines need unique order references and valid received/accepted quantities.')
            seen.add(key)
            totals[key] += accepted
            lines.append({'key': key, 'accepted_qty': str(accepted), 'received_qty': str(received)})
        receipts.append({'id': str(receipt.pk), 'number': receipt.receipt_number,
            'updated_at': receipt.updated_at.isoformat(), 'lines': lines})
    if not receipts or any(totals[key] < required for key, required in ordered.items()):
        return result(False, 'Accepted, quality-checked receipts do not yet cover all ordered quantities by the work data date.', receipts)
    return result(True, 'Recorded accepted receipts cover the order quantities; linked evidence still requires reviewer confirmation.', receipts)


def execution_scope_blockers(project, phase, node):
    if project.scope_type != 'detailed_engineering':
        return []
    from .epc import wbs_options, wbs_phase
    nodes = {row['id']: row for row in wbs_options(project)}
    try:
        owned_node = node is not None and wbs_phase(node.pk, nodes) == 'engineering'
    except ValidationError:
        owned_node = False
    if phase not in control_scope(project.scope_type)['owned_phases'] or not owned_node:
        return ['Only owned Engineering work can earn acceptance progress. Track other EPC phases as external schedule dependencies.']
    return []


def action_blockers(item):
    scope_blockers = execution_scope_blockers(item.project, item.phase, item.wbs_node)
    submit = list(scope_blockers)
    if not item.acceptance_criteria:
        submit.append('Define the acceptance criteria.')
    if not item.evidence_note.strip():
        submit.append('Describe the completion evidence.')
    docs = document_manifest(item)
    if not docs or any(row['is_deleted'] or not row['file'] or row['project_id'] != item.project_id for row in docs):
        submit.append('Attach existing project document files as evidence.')
    if item.owner_id == item.reviewer_id:
        submit.append('Assign a reviewer other than the work owner.')
    if not item.owner.is_active or not item.reviewer.is_active:
        submit.append('The work owner and reviewer must be active users.')
    if item.data_date > timezone.localdate():
        submit.append('Actual completion evidence cannot use a future data date.')
    if item.project.start_date and item.data_date < item.project.start_date:
        submit.append('The work data date precedes the project start date.')
    if item.status != 'draft':
        submit.append('Only draft work can be submitted.')
    review = list(scope_blockers)
    if item.status != 'submitted':
        review.append('Submit the work for review first.')
    if not docs or any(row['is_deleted'] or not row['file'] or row['project_id'] != item.project_id for row in docs):
        review.append('Project evidence is missing or no longer available.')
    accept = list(scope_blockers)
    if item.status != 'reviewed':
        accept.append('The assigned reviewer must approve the criteria and evidence first.')
    if item.review_manifest != review_evidence(item):
        accept.append('Completion evidence or material records changed or have not been reviewed; return the work for a new review.')
    if item.wbs_node.is_deleted or item.wbs_node.project_id != item.project_id:
        accept.append('The WBS node is archived or belongs to another project.')
    if item.baseline_id and item.baseline.project_id != item.project_id:
        accept.append('The integrated baseline belongs to another project.')
    if item.baseline_id and item.baseline.data_date > item.data_date:
        accept.append('The selected integrated baseline takes effect after the work data date.')
    if item.milestone_id and (item.milestone.is_deleted or item.milestone.project_id != item.project_id):
        accept.append('The milestone is archived or belongs to another project.')
    if not item.owner.is_active or not item.reviewer.is_active:
        accept.append('The work owner and reviewer must remain active users.')
    if not item.baseline_id or not item.activity_id:
        accept.append('Select an integrated baseline and a linked schedule activity.')
    else:
        links = item.baseline.manifest.get('activity_links', [])
        if not any(str(row.get('activity')) == str(item.activity_id) and str(row.get('wbs_node')) == str(item.wbs_node_id)
                   and row.get('link_type') == item.phase for row in links):
            accept.append('The selected baseline does not include this WBS, activity and EPC phase link.')
        if item.baseline.schedule_baseline.source_version_id != item.activity.version_id:
            accept.append('The activity is not in the selected baseline schedule version.')
        if not WBSActivityLink.objects.filter(project=item.project, activity=item.activity,
                wbs_node=item.wbs_node, link_type=item.phase, is_deleted=False).exists():
            accept.append('The current WBS/activity link no longer matches this work item.')
        if item.activity.is_deleted or item.activity.version.is_deleted or item.activity.version.status == 'superseded':
            accept.append('The linked schedule activity or version is no longer active.')
        schedule = item.activity.version.schedule
        if schedule.is_deleted or schedule.project.is_deleted or schedule.project.enterprise_project_id != item.project_id:
            accept.append('The linked schedule is archived or no longer belongs to this project.')
        if ActivityProgressUpdate.objects.filter(activity=item.activity, data_date__gt=item.data_date, is_deleted=False).exists():
            accept.append('Later progress exists. Use the current reporting date for acceptance.')
    if item.predecessors.exclude(status='accepted').exists() or item.predecessors.filter(is_deleted=True).exists():
        accept.append('Accept all predecessor work before this item.')
    if item.predecessors.filter(data_date__gt=item.data_date).exists():
        accept.append('The work data date cannot precede accepted predecessor completion.')
    if item.data_date > timezone.localdate():
        accept.append('Acceptance cannot record future actual completion.')
    readiness = material_readiness(item)
    if not readiness['ready']:
        accept.append(readiness['reason'])
    return {'submit': submit, 'review': review, 'accept': accept}


def _locked(item):
    Project.objects.select_for_update().get(pk=item.project_id, is_deleted=False)
    return EPCWorkItem.objects.select_for_update(of=('self',)).select_related(
        'project', 'owner', 'reviewer', 'baseline__schedule_baseline', 'activity__version', 'purchase_order'
    ).get(pk=item.pk, is_deleted=False)


def _write(user, item):
    if not user.is_active or not can_write_enterprise_project(user, item.project):
        raise PermissionDenied('Project write access is required.')


def _event(item, action, user, note='', payload=None):
    EPCWorkEvent.objects.create(work_item=item, action=action, actor=user, note=note, payload=_json(payload or {}))


@transaction.atomic
def submit_work(item, *, user):
    item = _locked(item)
    _write(user, item)
    if user.pk == item.reviewer_id:
        raise PermissionDenied('The assigned reviewer cannot submit their own review evidence.')
    blockers = action_blockers(item)['submit']
    if blockers:
        raise ValidationError({'blockers': blockers})
    item.status = 'submitted'
    item.submitted_by = user
    item.submitted_at = timezone.now()
    item.save(update_fields=['status', 'submitted_by', 'submitted_at', 'updated_at'])
    _event(item, 'submitted', user, item.evidence_note, {'documents': document_manifest(item)})
    return item


@transaction.atomic
def review_work(item, *, user, decision, note, criteria_confirmed=False):
    item = _locked(item)
    if not user.is_active or user.pk != item.reviewer_id:
        raise PermissionDenied('Only the assigned reviewer can record this review.')
    if not note.strip():
        raise ValidationError({'note': 'Record the review findings.'})
    if decision == 'return':
        if item.status not in ('submitted', 'reviewed'):
            raise ValidationError({'status': 'Only submitted or reviewed work can be returned.'})
        item.status = 'draft'
        item.review_manifest = {}
        item.reviewed_at = None
    elif decision == 'approve':
        blockers = action_blockers(item)['review']
        if user.pk == item.submitted_by_id or user.pk == item.owner_id:
            raise PermissionDenied('The reviewer must be independent of the owner and submitter.')
        if blockers:
            raise ValidationError({'blockers': blockers})
        if criteria_confirmed is not True:
            raise ValidationError({'criteria_confirmed': 'Confirm every acceptance criterion against the attached evidence.'})
        item.status = 'reviewed'
        item.reviewed_at = timezone.now()
        item.review_manifest = review_evidence(item)
    else:
        raise ValidationError({'decision': 'Choose approve or return.'})
    item.review_note = note.strip()
    item.save(update_fields=['status', 'review_manifest', 'review_note', 'reviewed_at', 'updated_at'])
    _event(item, 'reviewed' if decision == 'approve' else 'returned', user, note, item.review_manifest)
    return item


@transaction.atomic
def accept_work(item, *, user, note):
    item = _locked(item)
    if not can_accept_work(user, item.project):
        raise PermissionDenied('Only the project owner, project manager or administrator can accept work.')
    if item.status == 'accepted':
        return item  # A retried acceptance must not double-post progress or history.
    if not note.strip():
        raise ValidationError({'note': 'Record the acceptance decision.'})
    if item.activity_id:
        version = ScheduleVersion.objects.select_for_update().get(pk=item.activity.version_id)
        item.activity.version = version
    # Lock source rows as well as the work/project so evidence cannot change mid-decision.
    list(item.documents.select_for_update().order_by('id'))
    if item.purchase_order_id:
        from apps.procurement.models import PurchaseOrder
        item.purchase_order = PurchaseOrder.objects.select_for_update().get(pk=item.purchase_order_id)
        list(item.purchase_order.receipts.select_for_update().order_by('id'))
    blockers = action_blockers(item)['accept']
    if blockers:
        raise ValidationError({'blockers': blockers})
    previous = ActivityProgressUpdate.objects.filter(activity=item.activity, is_deleted=False,
        data_date__lte=item.data_date).order_by('-data_date', '-id').first()
    progress, _ = ActivityProgressUpdate.objects.update_or_create(activity=item.activity, data_date=item.data_date,
        defaults={'version': version, 'physical_progress_pct': Decimal('100'), 'remaining_duration_days': 0,
            'actual_start': previous.actual_start if previous and previous.actual_start else item.data_date,
            'actual_finish': item.data_date, 'forecast_finish': item.data_date,
            'actual_hours': previous.actual_hours if previous else 0,
            'actual_cost': previous.actual_cost if previous else 0,
            'reported_by': user, 'notes': f'EPC acceptance {item.code}: {note.strip()}', 'is_deleted': False, 'deleted_at': None})
    schedule = version.schedule
    if not schedule.data_date or schedule.data_date < item.data_date:
        schedule.data_date = item.data_date
        schedule.save(update_fields=['data_date', 'updated_at'])
    snapshot = capture_control_snapshot(version, item.data_date, user)
    if item.milestone_id:
        milestone = item.milestone
        milestone.is_completed = True
        milestone.completed_date = item.data_date
        milestone.save(update_fields=['is_completed', 'completed_date', 'updated_at'])
    item.status = 'accepted'
    item.accepted_by = user
    item.accepted_at = timezone.now()
    item.progress_update = progress
    item.control_snapshot = snapshot
    item.acceptance_manifest = _json({'baseline': item.baseline_id, 'baseline_checksum': item.baseline.checksum,
        'review': item.review_manifest, 'material_readiness': material_readiness(item),
        'predecessors': list(item.predecessors.values('id', 'code', 'accepted_at', 'data_date')),
        'progress_update': progress.pk, 'schedule_observation': snapshot.pk,
        'previous_progress': str(previous.physical_progress_pct) if previous else None})
    item.save(update_fields=['status', 'accepted_by', 'accepted_at', 'progress_update', 'control_snapshot', 'acceptance_manifest', 'updated_at'])
    _event(item, 'accepted', user, note, item.acceptance_manifest)
    return item
