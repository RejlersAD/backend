"""Project-scoped risk management, separate from computed scheduling inputs."""
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
import json
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.rbac.action_policy import module_action_allowed
from ..access import can_write_project
from ..models import PlanningRiskRecord, PlanningProject, ScheduleVersion
from .audit import record_event
from .operational_jobs import canonical_fingerprint
from .operational_actuals import can_view_commercial_actuals
from .schedule_approval import current_schedule_version


def risk_owners(project):
    User = get_user_model()
    if project.enterprise_project_id:
        ids = set(project.enterprise_project.memberships.filter(is_active=True).values_list('user_id', flat=True))
        ids.add(project.enterprise_project.owner_id)
    else:
        ids = {project.created_by_id}
    return User.objects.filter(is_active=True).filter(Q(pk__in=ids) | Q(is_superuser=True)).order_by('first_name', 'last_name', 'pk')


def can_manage(version, actor):
    return bool(current_schedule_version(version) and can_write_project(actor, version.schedule.project)
                and module_action_allowed(actor, 'planning_package', 'update'))


MANAGEMENT_FIELDS = ('status', 'priority', 'owner_id', 'response', 'resolution', 'probability_percent',
                     'cost_impact', 'impact_currency', 'schedule_impact_days', 'impact_basis',
                     'mitigation_due_date', 'mitigation_status')


def serialize_risk(item, *, include_costs=False):
    expected_cost = None
    if include_costs and item.probability_percent is not None and item.cost_impact is not None and item.impact_currency:
        expected_cost = (item.cost_impact * item.probability_percent / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    data = {key: getattr(item, key) for key in ('id', 'version_id', 'source_key', 'title', 'description', 'provenance',
            'status', 'priority', 'response', 'resolution', 'revision', 'impact_basis', 'mitigation_status')} | {
        'owner_id': str(item.owner_id) if item.owner_id else None,
        'owner_name': item.owner.get_full_name() or item.owner.email if item.owner else None,
        'probability_percent': str(item.probability_percent) if item.probability_percent is not None else None,
        'schedule_impact_days': str(item.schedule_impact_days) if item.schedule_impact_days is not None else None,
        'cost_impact': str(item.cost_impact) if include_costs and item.cost_impact is not None else None,
        'impact_currency': item.impact_currency if include_costs else None,
        'expected_cost_impact': str(expected_cost) if expected_cost is not None else None,
        'cost_data_restricted': not include_costs,
        'mitigation_due_date': item.mitigation_due_date.isoformat() if item.mitigation_due_date else None,
        'updated_at': item.updated_at.isoformat()}
    return data


def risk_snapshot(version, *, include_costs=False):
    # Baselines and schedule exports are visible to planning readers without
    # commercial access. Keep structured financial assessments out of those
    # shared snapshots; the authorized live register retains the full record.
    return [serialize_risk(item, include_costs=include_costs) for item in version.planning_risks.select_related('owner').order_by('pk')]


def impact_analysis(items, *, include_costs):
    active = [row for row in items if row['status'] != 'closed']
    totals = {}
    for row in active:
        if include_costs and row['expected_cost_impact'] is not None:
            currency = row['impact_currency']
            totals[currency] = totals.get(currency, Decimal('0')) + Decimal(row['expected_cost_impact'])
    today = timezone.localdate().isoformat()
    return {
        'active_risks': len(active),
        'probability_assessed': sum(row['probability_percent'] is not None for row in active),
        'schedule_assessed': sum(row['schedule_impact_days'] is not None for row in active),
        'cost_assessed': sum(row['expected_cost_impact'] is not None for row in active) if include_costs else None,
        'expected_cost_by_currency': {key: str(value) for key, value in sorted(totals.items())} if include_costs else None,
        'overdue_mitigations': sum(bool(row['mitigation_due_date'] and row['mitigation_due_date'] < today
                                      and row['mitigation_status'] != 'completed') for row in active),
        'cost_basis': 'Sum of probability × conditional cost impact for assessed active risks, by currency. Unassessed risks are excluded; this is not a contingency forecast.',
        'schedule_basis': 'Conditional delay estimates per risk. They are not added together or applied to the approved schedule.',
    }


def risk_collection(version, actor):
    include_costs = can_view_commercial_actuals(actor, version.schedule.project)
    items = risk_snapshot(version, include_costs=include_costs)
    editable = can_manage(version, actor)
    return {'items': items, 'version_id': version.pk, 'revision': canonical_fingerprint(items),
            'permissions': {'can_create': editable, 'can_edit': editable, 'can_view_costs': include_costs},
            'analysis': impact_analysis(items, include_costs=include_costs),
            'owners': [{'id': str(user.pk), 'name': user.get_full_name() or user.email} for user in risk_owners(version.schedule.project)]}


def seed_build_risks(version):
    if not version.planning_build_id:
        return
    for row in version.planning_build.plan.get('risks', []):
        value = row['value']
        description = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
        title = value.get('text') or value.get('title') or value.get('description') or value.get('statement') if isinstance(value, dict) else value
        from_document = row['lineage'].get('type') == 'document_evidence'
        PlanningRiskRecord.objects.get_or_create(version=version, source_key=str(row['id']), defaults={
            'title': str(title or description)[:255], 'description': description,
            'provenance': {'type': 'document' if from_document else 'planner',
                           'label': 'Accepted document risk' if from_document else 'Accepted planner risk', 'source': deepcopy(value),
                           'lineage': deepcopy(row['lineage']), 'build_id': str(version.planning_build_id)}})


@transaction.atomic
def manage_risk(project, actor, data, *, create=False):
    project = PlanningProject.objects.select_for_update().get(pk=project.pk)
    version = get_object_or_404(ScheduleVersion.objects.select_for_update(), pk=data['version_id'],
                               schedule__project=project, is_deleted=False, schedule__is_deleted=False)
    if not can_manage(version, actor):
        raise PermissionDenied('You cannot manage this schedule risk register.')
    reason = data.get('reason', '').strip()
    if not reason:
        raise ValidationError({'reason': 'Record the reason for this risk decision.'})
    if 'owner_id' in data and data['owner_id'] is not None and not risk_owners(project).filter(pk=data['owner_id']).exists():
        raise ValidationError({'owner_id': 'Select an active employee with access to this project.'})
    if create:
        item = PlanningRiskRecord(version=version, source_key=f'planner:{uuid4()}', title=data['title'],
            description=data['description'], provenance={'type': 'planner', 'label': 'Planner entry', 'actor_id': str(actor.pk), 'reason': reason})
        before = {}
    else:
        item = get_object_or_404(PlanningRiskRecord.objects.select_for_update(), pk=data['item_id'], version=version)
        if item.revision != data['revision']:
            raise ValidationError({'revision': 'This risk changed. Refresh before saving your decision.'})
        before = serialize_risk(item)
        item.revision += 1
    include_costs = can_view_commercial_actuals(actor, project)
    if any(key in data for key in ('cost_impact', 'impact_currency')) and not include_costs:
        raise PermissionDenied('Commercial access is required to change risk cost assessments.')
    for key in MANAGEMENT_FIELDS:
        if key in data:
            setattr(item, key, data[key])
    if item.status == 'closed' and not item.resolution.strip():
        raise ValidationError({'resolution': 'Record a resolution before closing the risk.'})
    if item.cost_impact is not None and not item.impact_currency:
        raise ValidationError({'impact_currency': 'Record a three-letter currency for the cost impact.'})
    if any(getattr(item, key) is not None for key in ('probability_percent', 'cost_impact', 'schedule_impact_days')) and not item.impact_basis.strip():
        raise ValidationError({'impact_basis': 'Record the evidence or assumptions supporting the impact assessment.'})
    if (item.mitigation_status != 'not_planned' or item.mitigation_due_date) and not item.response.strip():
        raise ValidationError({'response': 'Describe the mitigation plan before tracking its status or due date.'})
    item.save()
    # Planning audit readers may not have commercial access. Keep monetary
    # values and guessable monetary fingerprints out of this shared trail.
    metadata = {'reason': reason}
    if any(key in data for key in ('cost_impact', 'impact_currency')):
        metadata['cost_assessment_updated'] = True
    record_event(project=project, actor=actor, action='planning.risk_created' if create else 'planning.risk_updated',
                 entity=item, before=before, after=serialize_risk(item), metadata=metadata)
    return {**risk_collection(version, actor), 'item': serialize_risk(item, include_costs=include_costs)}


def clone_risks(source, target):
    for item in source.planning_risks.all():
        values = {key: deepcopy(getattr(item, key)) for key in ('source_key', 'title', 'description', 'provenance', *MANAGEMENT_FIELDS)}
        PlanningRiskRecord.objects.create(version=target, **values)
