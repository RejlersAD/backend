"""Validated EPC foundation and explicit source associations; no implicit approvals."""
import hashlib
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Max, Q
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.core.project_models import CURRENCY_CHOICES, Project
from apps.planning_intelligence.access import can_final_approve_defaults
from apps.planning_intelligence.models import ScheduleActivity, ScheduleBaseline, ScheduleVersion
from apps.procurement.models import PurchaseOrder, PurchaseRequisition
from apps.procurement.services.project_relationships import (
    extract_requisition_project_codes, normalize_project_code,
    resolve_project_relationship, resolve_requisition_enterprise_project,
)
from ..access import can_approve_commercial, can_write_enterprise_project, has_commercial_module_access
from ..epc_models import EPC_LINK_TYPES, EPC_SCOPE_TYPES, IntegratedBaseline, RequisitionWBSLink, WBSActivityLink, control_scope
from ..models import BudgetAllocation, ControlAccount, WBSNode


EPC_PHASES = [('EPC-ENG', 'Engineering'), ('EPC-PROC', 'Procurement'),
              ('EPC-CON', 'Construction'), ('EPC-COM', 'Commissioning')]


def _write_access(project, user):
    if not getattr(user, 'is_authenticated', False) or not user.is_active or not can_write_enterprise_project(user, project):
        raise PermissionDenied('Project write access is required.')


def _locked_project(project):
    # Serialize metadata writes without blocking deferred FK checks from an
    # observation whose version/period lock a scope change may need to await.
    return Project.objects.select_for_update(no_key=True).get(pk=project.pk, is_deleted=False)


@transaction.atomic
def require_editable_delivery_scope(project, new_scope):
    """Changing metadata must not reinterpret any already published history."""
    if new_scope == project.scope_type:
        return
    from ..execution_models import EPCWorkItem
    from ..models import IntegratedReportingSnapshot, ReportingPeriod
    from apps.planning_intelligence.models import ScheduleControlSnapshot
    # Setup and the generic project update hold Project first. Wait for any
    # first observation already in flight before deciding no history exists.
    list(ScheduleVersion.objects.select_for_update(of=('self',)).filter(
        schedule__project__enterprise_project=project).order_by('pk').values_list('pk', flat=True))
    list(ReportingPeriod.objects.select_for_update(of=('self',)).filter(project=project)
         .order_by('pk').values_list('pk', flat=True))
    if (IntegratedBaseline.objects.filter(project=project).exists()
            or EPCWorkItem.objects.filter(project=project).exists()
            or IntegratedReportingSnapshot.objects.filter(project=project).exists()
            or ScheduleControlSnapshot.objects.filter(version__schedule__project__enterprise_project=project).exists()):
        raise ValidationError({'scope_type': 'Delivery scope cannot change after an integrated baseline, execution work or published schedule/commercial observation exists. Preserve the recorded ownership boundary.'})


def _wbs(project, node_id):
    node = WBSNode.objects.filter(pk=node_id, project=project, is_deleted=False).first()
    if node is None:
        raise ValidationError({'wbs_node': 'Select an active WBS node from this project.'})
    return node


def project_fields(project):
    return {**{field: getattr(project, field) for field in (
        'id', 'code', 'name', 'client_name', 'start_date', 'end_date', 'currency', 'scope_type',
    )}, 'owner': project.owner_id, 'owner_name': (
        project.owner.get_full_name() or project.owner.username if project.owner_id else ''
    )}


def wbs_options(project):
    return list(WBSNode.objects.filter(project=project, is_deleted=False).values('id', 'code', 'name', 'parent'))


def wbs_phase(node_id, wbs_by_id):
    """Resolve an active, same-project branch without trusting a link's label."""
    visited = set()
    while node_id is not None:
        if node_id in visited or node_id not in wbs_by_id:
            raise ValidationError({'wbs': 'The baseline WBS ancestry contains a cycle or an archived/foreign parent. Review the WBS hierarchy.'})
        visited.add(node_id)
        node = wbs_by_id[node_id]
        if node['parent'] is None:
            phases = dict(zip((code for code, _ in EPC_PHASES), (value for value, _ in EPC_LINK_TYPES)))
            return phases.get(node['code'])
        node_id = node['parent']
    return None


def foundation_checks(project):
    roots = {node.code: node for node in WBSNode.objects.filter(project=project, is_deleted=False, parent=None)}
    definitions = [
        ('identity', 'Project identity', bool(project.code.strip() and project.name.strip()), 'Project code and name are required.'),
        ('client', 'Client', bool(project.client_name.strip()), 'Record the contractual client.'),
        ('owner', 'Active project owner', bool(project.owner_id and project.owner.is_active), 'Select an active project owner.'),
        ('dates', 'Project dates', bool(project.start_date and project.end_date and project.start_date <= project.end_date), 'Record valid start and finish dates.'),
        ('currency', 'Project currency', project.currency in dict(CURRENCY_CHOICES), 'Select a supported project currency.'),
        ('scope', 'Delivery scope', project.scope_type in dict(EPC_SCOPE_TYPES), 'Select Full EPC or Detailed Engineering scope.'),
        ('wbs', 'Four EPC phases', all(code in roots and roots[code].name == name for code, name in EPC_PHASES), 'Set up Engineering, Procurement, Construction and Commissioning roots.'),
    ]
    return [{'id': key, 'label': label, 'ready': bool(ready), 'detail': 'Ready' if ready else detail}
            for key, label, ready, detail in definitions]


def setup_payload(project, user):
    checks = foundation_checks(project)
    can_write = bool(user.is_active and can_write_enterprise_project(user, project))
    return {
        'project': project_fields(project), 'checks': checks, 'ready': all(row['ready'] for row in checks),
        'wbs': wbs_options(project), 'phases': [{'code': code, 'name': name} for code, name in EPC_PHASES],
        'owners': [{'id': user.pk, 'label': user.get_full_name() or user.username}
                   for user in get_user_model().objects.filter(is_active=True).order_by('first_name', 'id')],
        'currencies': [{'value': value, 'label': label} for value, label in CURRENCY_CHOICES],
        'scope_options': [{'value': value, 'label': label} for value, label in EPC_SCOPE_TYPES],
        'control_scope': control_scope(project.scope_type),
        'capabilities': {'can_setup': can_write, 'can_link': can_write,
                         'can_associate_requisitions': can_write and has_commercial_module_access(user),
                         'can_capture_baseline': can_write and can_approve_commercial(user)},
    }


@transaction.atomic
def setup_epc_project(project, values, *, user):
    project = _locked_project(project)
    _write_access(project, user)
    from ..epc_serializers import EpcSetupSerializer
    # Service callers receive the same field and date validation as API callers.
    raw = {**values, 'owner': getattr(values.get('owner'), 'pk', values.get('owner'))}
    serializer = EpcSetupSerializer(data=raw)
    serializer.is_valid(raise_exception=True)
    values = serializer.validated_data
    require_editable_delivery_scope(project, values['scope_type'])
    if Project.objects.filter(code__iexact=values['code']).exclude(pk=project.pk).exists():
        raise ValidationError({'code': 'This project code is already in use.'})
    if BudgetAllocation.objects.filter(project=project, status='approved', is_deleted=False).exclude(currency=values['currency']).exists():
        raise ValidationError({'currency': 'Existing approved budgets use a different currency. Resolve their approved basis first.'})
    for field, value in values.items():
        setattr(project, field, value)
    project.save(update_fields=[*values.keys(), 'updated_at'])
    for position, (code, name) in enumerate(EPC_PHASES):
        existing = WBSNode.objects.filter(project=project, code=code).first()
        if existing and (existing.is_deleted or existing.parent_id or existing.name != name):
            raise ValidationError({'wbs': f'{code} already exists with different scope. Review the WBS before setup.'})
        if existing is None:
            WBSNode.objects.create(project=project, code=code, name=name, level=0, sort_order=position)
    return project


def activity_link_payload(project, user):
    versions = ScheduleVersion.objects.filter(schedule__project__enterprise_project=project,
        is_deleted=False, schedule__is_deleted=False, schedule__project__is_deleted=False).select_related('schedule')
    return {
        'results': list(WBSActivityLink.objects.filter(project=project, is_deleted=False).select_related('project', 'wbs_node', 'activity')),
        'wbs': wbs_options(project),
        'versions': [{'id': row.pk, 'schedule': row.schedule_id, 'name': row.schedule.name,
                      'version': row.version, 'status': row.status} for row in versions],
        'activities': list(ScheduleActivity.objects.filter(version__in=versions, is_deleted=False)
                           .values('id', 'version', 'external_id', 'name', 'activity_type')),
        'link_types': [{'value': value, 'label': label} for value, label in EPC_LINK_TYPES],
        'control_scope': control_scope(project.scope_type),
        'can_edit': bool(user.is_active and can_write_enterprise_project(user, project)),
    }


@transaction.atomic
def save_activity_link(project, values, *, user):
    project = _locked_project(project)
    _write_access(project, user)
    from ..epc_serializers import ActivityLinkInputSerializer
    serializer = ActivityLinkInputSerializer(data=values)
    serializer.is_valid(raise_exception=True)
    values = serializer.validated_data
    node = _wbs(project, values['wbs_node'])
    if project.scope_type == 'detailed_engineering':
        phase = wbs_phase(node.pk, {row['id']: row for row in wbs_options(project)})
        if phase != values['link_type']:
            raise ValidationError({'link_type': 'The activity phase must match its EPC WBS branch; external dependencies cannot be labelled as owned Engineering.'})
    activity = ScheduleActivity.objects.filter(pk=values['activity'], is_deleted=False,
        version__is_deleted=False, version__schedule__is_deleted=False,
        version__schedule__project__is_deleted=False,
        version__schedule__project__enterprise_project=project).first()
    if activity is None:
        raise ValidationError({'activity': 'Select an active schedule activity linked to this project.'})
    # Controls capture holds the version lock; mapping edits use the same lock
    # so an observation cannot read ownership halfway through an edit.
    ScheduleVersion.objects.select_for_update().get(pk=activity.version_id)
    row = None
    if values.get('id'):
        row = WBSActivityLink.objects.filter(pk=values['id'], project=project, is_deleted=False).first()
        if row is None:
            raise ValidationError({'id': 'Activity link was not found in this project.'})
    existing = WBSActivityLink.objects.filter(activity=activity).first()
    if existing and row and existing.pk != row.pk:
        raise ValidationError({'activity': 'This activity already has a WBS assignment.'})
    if existing and existing.project_id != project.pk:
        raise ValidationError({'activity': 'This activity has a conflicting project assignment.'})
    row = row or existing or WBSActivityLink(project=project, created_by=user)
    row.wbs_node, row.activity = node, activity
    row.link_type, row.notes = values['link_type'], values['notes']
    row.is_deleted, row.deleted_at = False, None
    row.save()
    return row


@transaction.atomic
def delete_activity_link(project, link_id, *, user):
    project = _locked_project(project)
    _write_access(project, user)
    row = WBSActivityLink.objects.filter(pk=link_id, project=project, is_deleted=False).first()
    if row is None:
        raise ValidationError({'id': 'Activity link was not found in this project.'})
    ScheduleVersion.objects.select_for_update().get(pk=row.activity.version_id)
    row.soft_delete()


def requisition_match(row, project, *, index=None):
    codes = extract_requisition_project_codes(row.project, row.project_details)
    candidate, reason = resolve_requisition_enterprise_project(project=row.project, project_details=row.project_details, index=index)
    if row.enterprise_project_id:
        state = 'associated' if row.enterprise_project_id == project.pk else 'other_project'
    else:
        state = 'exact_match' if candidate and candidate.pk == project.pk else ('other_project' if candidate else reason)
    # Unknown explicit codes are not silently discarded by the EPC review.
    if not row.enterprise_project_id and state == 'exact_match' and any(
            normalize_project_code(code) != normalize_project_code(project.code) for code in codes):
        state = 'review_required'
    return state, candidate, codes


def requisition_payload(project, user):
    commercial = has_commercial_module_access(user)
    links = {row.requisition_id: row for row in RequisitionWBSLink.objects.filter(project=project, is_deleted=False)}
    index = {}
    for candidate in Project.objects.filter(is_deleted=False).only('id', 'code'):
        index.setdefault(normalize_project_code(candidate.code), []).append(candidate)
    rows = []
    for row in PurchaseRequisition.objects.filter(Q(enterprise_project=project) | Q(enterprise_project__isnull=True)).order_by('-created_at'):
        state, candidate, codes = requisition_match(row, project, index=index)
        if state == 'other_project' or (not commercial and state not in {'associated', 'exact_match'}):
            continue
        link = links.get(row.pk)
        rows.append({'id': str(row.pk), 'pr_number': row.pr_number, 'title': row.title or row.product_service,
                     'status': row.status, 'enterprise_project': row.enterprise_project_id,
                     'wbs_node': link.wbs_node_id if link else None, 'link_id': link.pk if link else None,
                     'match_status': state, 'suggested_project': candidate.pk if candidate else None,
                     'project_codes': codes})
    return {'results': rows, 'wbs': wbs_options(project),
            'can_edit': commercial and user.is_active and can_write_enterprise_project(user, project)}


@transaction.atomic
def associate_requisition(project, values, *, user):
    project = _locked_project(project)
    _write_access(project, user)
    if not has_commercial_module_access(user):
        raise PermissionDenied('Procurement or commercial module access is required.')
    from ..epc_serializers import RequisitionLinkInputSerializer
    serializer = RequisitionLinkInputSerializer(data=values)
    serializer.is_valid(raise_exception=True)
    values = serializer.validated_data
    node = _wbs(project, values['wbs_node'])
    row = PurchaseRequisition.objects.select_for_update().filter(pk=values['requisition']).first()
    if row is None:
        raise ValidationError({'requisition': 'Purchase requisition was not found.'})
    state, _, _ = requisition_match(row, project)
    if state == 'other_project':
        raise ValidationError({'requisition': 'This requisition identifies a different project. Resolve its canonical association first.'})
    if state not in {'associated', 'exact_match'} and not (values['review_confirmed'] and can_approve_commercial(user)):
        raise ValidationError({'review_confirmed': 'An authorized commercial reviewer must confirm this ambiguous or unmatched association with a reason.'})
    # Nullable joins must not be included in PostgreSQL FOR UPDATE.
    orders = list(PurchaseOrder.objects.select_for_update().filter(pr_reference=row))
    for order in orders:
        masters = [order.project] if order.project_id else []
        if order.budget_allocation_id and order.budget_allocation.project_id:
            masters.append(order.budget_allocation.project)
        if ((order.enterprise_project_id and order.enterprise_project_id != project.pk)
            or any((master.enterprise_project_id and master.enterprise_project_id != project.pk)
                or (master.project_number and normalize_project_code(master.project_number) != normalize_project_code(project.code))
                for master in masters)
            or any(code and normalize_project_code(code) != normalize_project_code(project.code)
                   for code in [order.project_number, order.rad_project_no])):
            raise ValidationError({'requisition': 'A linked purchase order identifies another project. Review the procurement relationships first.'})
    try:
        resolution = resolve_project_relationship(record_type='purchase_requisition', record_id=row.pk,
            enterprise_project_id=project.pk, user=user, reason=values['reason'])
    except DjangoValidationError as exc:
        raise ValidationError(getattr(exc, 'message_dict', exc.messages)) from exc
    existing = RequisitionWBSLink.objects.filter(requisition=row).first()
    if existing and existing.project_id != project.pk:
        raise ValidationError({'requisition': 'A conflicting WBS association already exists.'})
    link = existing or RequisitionWBSLink(project=project, requisition=row)
    link.wbs_node, link.reason, link.linked_by = node, values['reason'], user
    link.is_deleted, link.deleted_at = False, None
    link.save()
    return {'id': link.pk, 'requisition': str(row.pk), 'project': project.pk, 'wbs_node': node.pk,
            'resolution': resolution}


def _approved_baselines(project):
    return ScheduleBaseline.objects.filter(schedule__project__enterprise_project=project,
        is_deleted=False, schedule__is_deleted=False, schedule__project__is_deleted=False,
        source_version__is_deleted=False, approved_by__isnull=False, approved_at__isnull=False)


def baseline_payload(project, user):
    baselines = list(_approved_baselines(project).select_related('schedule__project'))
    budgets = BudgetAllocation.objects.filter(project=project, is_deleted=False, status='approved',
        approved_by__isnull=False, approved_at__isnull=False)
    budget_choices = list(budgets.values('id', 'code', 'name', 'wbs_node', 'amount', 'currency'))
    excluded_budgets = 0
    if project.scope_type == 'detailed_engineering':
        nodes = {row['id']: row for row in wbs_options(project)}
        eligible = []
        for row in budget_choices:
            try:
                owned = wbs_phase(row['wbs_node'], nodes) == 'engineering'
            except ValidationError:
                owned = False
            if owned:
                eligible.append(row)
        excluded_budgets = len(budget_choices) - len(eligible)
        budget_choices = eligible
    blockers = [row['detail'] for row in foundation_checks(project) if not row['ready']]
    if not baselines:
        blockers.append('Approve and save a schedule baseline through the schedule workflow.')
    if not budget_choices:
        blockers.append('Approve owned control budget allocations through the commercial workflow.')
    return {'results': list(IntegratedBaseline.objects.filter(project=project)),
            'control_scope': control_scope(project.scope_type),
            'schedule_baselines': list(_approved_baselines(project).values('id', 'name', 'source_version', 'data_date', 'approved_at')),
            'budgets': budget_choices, 'excluded_dependency_budget_count': excluded_budgets,
            'can_capture': bool(user.is_active and can_write_enterprise_project(user, project)
                and can_approve_commercial(user) and any(can_final_approve_defaults(user, row.schedule.project) for row in baselines)),
            'blockers': blockers}


def _json_safe(value):
    return json.loads(json.dumps(value, default=str))


@transaction.atomic
def capture_integrated_baseline(project, values, *, user):
    project = _locked_project(project)
    _write_access(project, user)
    if not can_approve_commercial(user):
        raise PermissionDenied('Existing commercial approval authority is required to seal an integrated baseline.')
    from ..epc_serializers import BaselineInputSerializer
    serializer = BaselineInputSerializer(data=values)
    serializer.is_valid(raise_exception=True)
    values = serializer.validated_data
    incomplete = [row['label'] for row in foundation_checks(project) if not row['ready']]
    if incomplete:
        raise ValidationError({'foundation': incomplete})
    baseline = _approved_baselines(project).filter(pk=values['schedule_baseline']).first()
    if baseline is None:
        raise ValidationError({'schedule_baseline': 'Select an approved schedule baseline for this project.'})
    # Serialize with source schedule transitions. Lock only non-null parent rows.
    version = ScheduleVersion.objects.select_for_update().get(pk=baseline.source_version_id)
    baseline = ScheduleBaseline.objects.select_for_update().get(pk=baseline.pk)
    if not can_final_approve_defaults(user, baseline.schedule.project):
        raise PermissionDenied('Existing schedule final approval authority is required.')
    if (baseline.source_version_id != version.pk or version.schedule_id != baseline.schedule_id
            or baseline.is_deleted or not baseline.approved_by_id or not baseline.approved_at):
        raise ValidationError({'schedule_baseline': 'The schedule baseline changed. Refresh and review its approval.'})
    if baseline.data_date and values['data_date'] < baseline.data_date:
        raise ValidationError({'data_date': 'Integrated baseline date cannot precede its schedule baseline data date.'})
    saved_activities = baseline.snapshot.get('activities', []) if isinstance(baseline.snapshot, dict) else []
    if not saved_activities or any(not isinstance(row, dict) or not row.get('id') for row in saved_activities):
        raise ValidationError({'schedule_baseline': 'The approved baseline has no usable saved activity evidence.'})
    saved_ids = {str(row['id']) for row in saved_activities}
    if len(saved_ids) != len(saved_activities):
        raise ValidationError({'schedule_baseline': 'The saved baseline contains duplicate activity IDs. Review the source baseline.'})
    saved_by_id = {str(row['id']): row for row in saved_activities}
    budgets = list(BudgetAllocation.objects.select_for_update().filter(pk__in=values['budget_ids'], project=project, is_deleted=False).order_by('id'))
    if len(budgets) != len(values['budget_ids']) or any(row.status != 'approved' or not row.approved_by_id or not row.approved_at
            or row.wbs_node.is_deleted or row.wbs_node.project_id != project.pk for row in budgets):
        raise ValidationError({'budget_ids': 'Every selected budget must be approved for this project with approval evidence.'})
    if any(row.currency != project.currency for row in budgets):
        raise ValidationError({'budget_ids': 'All baseline budgets must use the project currency.'})
    links = list(WBSActivityLink.objects.filter(project=project, wbs_node__project=project, activity__version=version, is_deleted=False,
        wbs_node__is_deleted=False, activity__is_deleted=False).select_related('activity', 'wbs_node'))
    if saved_ids != {str(row.activity_id) for row in links}:
        raise ValidationError({'links': 'Map every saved schedule baseline activity once to this project WBS; remove links outside its saved scope.'})
    scope = control_scope(project.scope_type)
    owned_links = [row for row in links if row.link_type in scope['owned_phases']]
    dependency_links = [row for row in links if row.link_type in scope['dependency_phases']]
    if not owned_links:
        raise ValidationError({'links': 'Map at least one activity within the owned delivery scope.'})
    budget_wbs = {row.wbs_node_id for row in budgets}
    shared_wbs = wbs_options(project)
    wbs_by_id = {row['id']: row for row in shared_wbs}
    for node_id in budget_wbs | {row.wbs_node_id for row in links}:
        wbs_phase(node_id, wbs_by_id)
    if project.scope_type == 'detailed_engineering':
        if any(wbs_phase(row.wbs_node_id, wbs_by_id) != row.link_type for row in links):
            raise ValidationError({'links': 'Every activity phase must match its EPC WBS branch; external dependencies cannot become owned Engineering scope.'})
        if any(wbs_phase(node_id, wbs_by_id) != 'engineering' for node_id in budget_wbs):
            raise ValidationError({'budget_ids': 'Detailed Engineering baseline budgets may cover only the owned Engineering WBS. External dependency budgets must not be included.'})
    accounts = list(ControlAccount.objects.select_for_update().filter(project=project, wbs_node_id__in=budget_wbs,
        is_deleted=False, status__in=['active', 'closed'], approved_by__isnull=False, approved_at__isnull=False))
    if budget_wbs != {row.wbs_node_id for row in accounts}:
        raise ValidationError({'control_accounts': 'Each budget WBS requires an approved Control Account.'})
    if budget_wbs != {row.wbs_node_id for row in owned_links}:
        raise ValidationError({'links': 'The selected approved budget WBS scope must match the mapped owned schedule WBS scope.'})
    revision = (IntegratedBaseline.objects.filter(project=project).aggregate(value=Max('revision'))['value'] or 0) + 1
    manifest = _json_safe({
        'schema_version': 2, 'revision': revision, 'data_date': values['data_date'],
        'approved_by_id': user.pk, 'project': project_fields(project),
        'control_scope': scope,
        'owned_activity_ids': sorted(row.activity_id for row in owned_links),
        'dependency_activity_ids': sorted(row.activity_id for row in dependency_links),
        'schedule_baseline': {'id': baseline.pk, 'source_version': version.pk, 'name': baseline.name,
            'data_date': baseline.data_date, 'approved_by': baseline.approved_by_id,
            'approved_at': baseline.approved_at, 'snapshot': baseline.snapshot},
        'budgets': [{'id': row.pk, 'code': row.code, 'name': row.name, 'wbs_node': row.wbs_node_id,
            'amount': row.amount, 'currency': row.currency, 'approved_by': row.approved_by_id,
            'approved_at': row.approved_at} for row in budgets],
        'activity_links': [{'id': row.pk, 'wbs_node': row.wbs_node_id, 'activity': row.activity_id,
            'external_id': saved_by_id[str(row.activity_id)].get('external_id'), 'link_type': row.link_type,
            'control_role': 'owned' if row.link_type in scope['owned_phases'] else 'dependency'} for row in links],
        'wbs': shared_wbs,
        'control_accounts': [{'id': row.pk, 'code': row.code, 'wbs_node': row.wbs_node_id,
            'manager': row.manager_id, 'earned_value_method': row.earned_value_method,
            'baseline_start': row.baseline_start, 'baseline_finish': row.baseline_finish,
            'approved_by': row.approved_by_id, 'approved_at': row.approved_at} for row in accounts],
    })
    checksum = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return IntegratedBaseline.objects.create(project=project, revision=revision, name=values['name'],
        data_date=values['data_date'], schedule_baseline=baseline, currency=project.currency,
        budget_total=sum((row.amount for row in budgets), Decimal('0')), manifest=manifest,
        checksum=checksum, approved_by=user)
