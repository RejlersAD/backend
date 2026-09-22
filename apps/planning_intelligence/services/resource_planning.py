"""Explicit resource productivity and version-scoped allocation reads."""
from copy import deepcopy

from django.db.models import Q

from rest_framework.exceptions import ValidationError

from ..access import can_write_project


def resource_is_locked(resource):
    return resource.assignments.filter(is_deleted=False, activity__is_deleted=False).filter(
        Q(activity__version__status__in=['approved', 'baselined', 'superseded'])
        | Q(activity__version__baselines__is_deleted=False, activity__version__baselines__approved_at__isnull=False),
    ).exists()


def require_mutable_allocation_version(version):
    if (version.status not in ['draft', 'calculated']
            or version.baselines.filter(is_deleted=False, approved_at__isnull=False).exists()):
        raise ValidationError('Approved, baselined, and superseded schedule versions are immutable.')


def validate_cost_write(serializer, attrs, field, project):
    request = serializer.context.get('request')
    if request and field in attrs and attrs[field] != getattr(serializer.instance, field, 0):
        from .operational_actuals import can_view_commercial_actuals
        if not can_view_commercial_actuals(request.user, project):
            raise ValidationError({field: 'Commercial access is required to change monetary budgets.'})


def mask_resource_costs(row, request, project):
    if request:
        from .operational_actuals import can_view_commercial_actuals
        if not can_view_commercial_actuals(request.user, project):
            row.pop('unit_cost', None)
            row.pop('budgeted_cost', None)
    return row


def resource_plan(project, version, request):
    from ..models import ActivityAssignment
    from ..schedule_serializers import ActivityAssignmentSerializer, ScheduleResourceSerializer
    context = {'request': request}
    baseline = version.baselines.filter(is_deleted=False, approved_at__isnull=False).first() if version else None
    if baseline:
        snapshot = deepcopy(baseline.snapshot)
        inputs = snapshot.get('accepted_inputs') or {}
        resources = [mask_resource_costs(row, request, project) for row in inputs.get('resources', [])]
        assignments = [mask_resource_costs(row, request, project) for row in inputs.get('assignments', [])]
        activities = snapshot.get('activities') or []
        for row in resources:
            row['can_edit'] = False
    else:
        resources = ScheduleResourceSerializer(project.schedule_resources.filter(is_deleted=False), many=True, context=context).data
        assignments = ActivityAssignmentSerializer(ActivityAssignment.objects.filter(
            activity__version=version, activity__is_deleted=False, is_deleted=False,
        ).select_related('resource__project'), many=True, context=context).data if version else []
        activities = list(version.activities.filter(is_deleted=False).values('id', 'external_id', 'name')) if version else []
    mutable = bool(version and version.status in ['draft', 'calculated'] and not baseline
                   and not version.schedule.versions.filter(is_deleted=False, version__gt=version.version).exists())
    return {'project_id': project.pk, 'version_id': version.pk if version else None,
            'basis': 'approved_baseline' if baseline else 'current_catalog',
            'resources': resources, 'assignments': assignments, 'activities': activities,
            'permissions': {'can_manage_resources': can_write_project(request.user, project) and not baseline,
                            'can_allocate': can_write_project(request.user, project) and mutable},
            'notice': ('This baseline contains no frozen resource inputs.' if baseline and not inputs else '')}
