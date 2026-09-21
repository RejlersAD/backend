"""Project-scoped risk management, separate from computed scheduling inputs."""
from copy import deepcopy
import json
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.rbac.action_policy import module_action_allowed
from ..access import can_write_project
from ..models import PlanningRiskRecord, PlanningProject, ScheduleVersion
from .audit import record_event
from .operational_jobs import canonical_fingerprint
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


def serialize_risk(item):
    return {key: getattr(item, key) for key in ('id', 'version_id', 'source_key', 'title', 'description', 'provenance',
            'status', 'priority', 'response', 'resolution', 'revision')} | {
        'owner_id': str(item.owner_id) if item.owner_id else None,
        'owner_name': item.owner.get_full_name() or item.owner.email if item.owner else None,
        'updated_at': item.updated_at.isoformat()}


def risk_snapshot(version):
    return [serialize_risk(item) for item in version.planning_risks.select_related('owner').order_by('pk')]


def risk_collection(version, actor):
    items = risk_snapshot(version)
    editable = can_manage(version, actor)
    return {'items': items, 'version_id': version.pk, 'revision': canonical_fingerprint(items),
            'permissions': {'can_create': editable, 'can_edit': editable},
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
    for key in ('status', 'priority', 'owner_id', 'response', 'resolution'):
        if key in data:
            setattr(item, key, data[key])
    if item.status == 'closed' and not item.resolution.strip():
        raise ValidationError({'resolution': 'Record a resolution before closing the risk.'})
    item.save()
    record_event(project=project, actor=actor, action='planning.risk_created' if create else 'planning.risk_updated',
                 entity=item, before=before, after=serialize_risk(item), metadata={'reason': reason})
    return {**risk_collection(version, actor), 'item': serialize_risk(item)}


def clone_risks(source, target):
    for item in source.planning_risks.all():
        values = {key: deepcopy(getattr(item, key)) for key in ('source_key', 'title', 'description', 'provenance',
                  'status', 'priority', 'owner_id', 'response', 'resolution')}
        PlanningRiskRecord.objects.create(version=target, **values)
