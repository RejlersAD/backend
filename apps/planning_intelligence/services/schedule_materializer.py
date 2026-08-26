"""Convert legacy JSON generation output into the relational scheduling domain."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.text import slugify

from ..config import DEFAULT_CALENDAR
from ..models import (
    ActivityAssignment, ActivityRelationship, Schedule, ScheduleActivity,
    ScheduleResource, ScheduleVersion, ScheduleWBSNode, WorkCalendar,
)
from .cpm import calculate_schedule_version


def _as_date(value):
    if isinstance(value, dt.date):
        return value
    if value:
        try:
            return dt.date.fromisoformat(str(value)[:10])
        except ValueError:
            return None
    return None


def _as_decimal(value, default=0):
    try:
        return Decimal(str(value if value is not None else default))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(str(default))


def _weekdays(count):
    count = min(7, max(1, int(count or 5)))
    return list(range(count))


@transaction.atomic
def materialize_generation(generation, *, requested_by=None):
    """Create and calculate one immutable relational version for a generation."""
    existing = ScheduleVersion.objects.filter(source_generation=generation, is_deleted=False).first()
    if existing:
        latest_run = existing.calculation_runs.filter(is_deleted=False).first()
        return existing, latest_run, []

    project = generation.project
    overrides = project.calendar_overrides or {}
    calendar, _ = WorkCalendar.objects.get_or_create(
        project=project,
        name='Project Standard Calendar',
        defaults={
            'working_weekdays': _weekdays(overrides.get('working_days_per_week', DEFAULT_CALENDAR['working_days_per_week'])),
            'hours_per_day': _as_decimal(overrides.get('hours_per_day', DEFAULT_CALENDAR['hours_per_day']), 8),
            'timezone': overrides.get('timezone') or 'Asia/Dubai',
            'is_default': True,
        },
    )
    if not calendar.is_default:
        calendar.is_default = True
        calendar.save(update_fields=['is_default', 'updated_at'])

    activity_dates = [_as_date(item.get('start_date')) for item in generation.activities or []]
    planned_start = project.effective_date or min((value for value in activity_dates if value), default=timezone.localdate())
    schedule, _ = Schedule.objects.select_for_update().get_or_create(
        project=project,
        code='MASTER',
        defaults={
            'name': f'{project.name} Master Schedule', 'planned_start': planned_start,
            'data_date': planned_start, 'default_calendar': calendar,
            'created_by': requested_by or generation.generated_by,
        },
    )
    changed = []
    if not schedule.default_calendar_id:
        schedule.default_calendar = calendar
        changed.append('default_calendar')
    if planned_start < schedule.planned_start:
        schedule.planned_start = planned_start
        changed.append('planned_start')
    if changed:
        schedule.save(update_fields=changed + ['updated_at'])

    next_version = (schedule.versions.aggregate(value=Max('version'))['value'] or 0) + 1
    parent = schedule.versions.filter(is_deleted=False).order_by('-version').first()
    version = ScheduleVersion.objects.create(
        schedule=schedule, version=next_version, parent_version=parent,
        source_generation=generation, change_summary=generation.change_summary,
        created_by=requested_by or generation.generated_by,
    )

    wbs_by_code = {}
    parent_codes = {}
    wbs_nodes = []
    for index, item in enumerate(generation.wbs or []):
        code = str(item.get('code') or f'WBS-{index + 1}')[:128]
        node = ScheduleWBSNode(
            version=version, code=code, name=str(item.get('name') or code)[:255],
            level=max(0, int(item.get('level') or 0)), sort_order=index,
            discipline=str(item.get('discipline') or '')[:64],
        )
        wbs_nodes.append(node)
        wbs_by_code[code] = node
        parent_codes[code] = str(item.get('parent_code') or '')
    ScheduleWBSNode.objects.bulk_create(wbs_nodes, batch_size=500)
    parent_updates = []
    for node in wbs_by_code.values():
        parent = wbs_by_code.get(parent_codes[node.code])
        if parent:
            node.parent = parent
            parent_updates.append(node)
    if parent_updates:
        ScheduleWBSNode.objects.bulk_update(parent_updates, ['parent'], batch_size=500)

    issues = []
    activities_by_external_id = {}
    raw_by_external_id = {}
    activity_rows = []
    for index, item in enumerate(generation.activities or []):
        external_id = str(item.get('id') or f'ACT-{index + 1}')[:64]
        if external_id in activities_by_external_id:
            issues.append({'code': 'duplicate_activity_id', 'activity': external_id})
            continue
        is_milestone = bool(item.get('is_milestone')) or _as_decimal(item.get('original_duration_days'), 1) == 0
        activity = ScheduleActivity(
            version=version, wbs_node=wbs_by_code.get(str(item.get('wbs_code') or '')),
            calendar=calendar, external_id=external_id,
            name=str(item.get('name') or external_id)[:500],
            activity_type=('start_milestone' if is_milestone and not item.get('predecessors') else 'finish_milestone') if is_milestone else 'task',
            duration_days=max(Decimal('0'), _as_decimal(item.get('original_duration_days'), 0 if is_milestone else 1)),
            discipline=str(item.get('discipline') or '')[:64],
            responsible_role=str(item.get('responsible_role') or '')[:120],
            sort_order=index,
            metadata={
                'deliverable': item.get('deliverable'),
                'workflow_status': item.get('workflow_status'),
                'workflow_stage_code': item.get('workflow_stage_code'),
                'workflow_stage_sequence': item.get('workflow_stage_sequence'),
                'workflow_progress_weight': item.get('workflow_progress_weight'),
                'workflow_template_code': item.get('workflow_template_code'),
                'workflow_template_version': item.get('workflow_template_version'),
                'workflow_family': item.get('workflow_family'),
                'generation_plan_id': item.get('generation_plan_id'),
                'plan_deliverable_id': item.get('plan_deliverable_id'),
                'basis_deliverable_id': item.get('basis_deliverable_id'),
                'document_number': item.get('document_number'),
                'document_revision': item.get('document_revision'),
                'phase_code': item.get('phase_code'),
                'scenario_code': item.get('scenario_code'),
                'recurrence': item.get('recurrence'),
                'recurrence_occurrence': item.get('recurrence_occurrence'),
                'source_references': item.get('source_references') or [],
                'source_start': item.get('start_date'),
                'source_finish': item.get('finish_date'),
                'date_authority': 'relational_cpm',
            },
        )
        activities_by_external_id[external_id] = activity
        raw_by_external_id[external_id] = item
        activity_rows.append(activity)
    ScheduleActivity.objects.bulk_create(activity_rows, batch_size=500)

    seen_relationships = set()
    relationship_rows = []
    for successor_id, item in raw_by_external_id.items():
        successor = activities_by_external_id[successor_id]
        for predecessor_data in item.get('predecessors') or []:
            predecessor_id = str(predecessor_data.get('id') or '')
            predecessor = activities_by_external_id.get(predecessor_id)
            relationship_type = str(predecessor_data.get('type') or 'FS').upper()
            key = (predecessor_id, successor_id, relationship_type)
            if not predecessor:
                issues.append({'code': 'missing_predecessor', 'activity': successor_id, 'predecessor': predecessor_id})
                continue
            if relationship_type not in {'FS', 'SS', 'FF', 'SF'}:
                issues.append({'code': 'invalid_relationship_type', 'activity': successor_id, 'type': relationship_type})
                continue
            if predecessor.pk == successor.pk or key in seen_relationships:
                issues.append({'code': 'duplicate_or_self_relationship', 'activity': successor_id, 'predecessor': predecessor_id})
                continue
            relationship_rows.append(ActivityRelationship(
                version=version, predecessor=predecessor, successor=successor,
                relationship_type=relationship_type,
                lag_days=_as_decimal(predecessor_data.get('lag_days'), 0),
                metadata={
                    'source': predecessor_data.get('source', 'generated'),
                    'generation_dependency_id': predecessor_data.get('generation_dependency_id'),
                    'rationale': predecessor_data.get('rationale', ''),
                    'source_references': predecessor_data.get('source_references') or [],
                },
            ))
            seen_relationships.add(key)
    ActivityRelationship.objects.bulk_create(relationship_rows, batch_size=500)

    resources = {}
    assignment_rows = []
    for activity in activities_by_external_id.values():
        if not activity.responsible_role:
            continue
        resource = resources.get(activity.responsible_role)
        if resource is None:
            base_code = (slugify(activity.responsible_role).replace('-', '_').upper() or 'LABOR')[:56]
            code = base_code
            suffix = 1
            while ScheduleResource.objects.filter(project=project, code=code).exclude(role=activity.responsible_role).exists():
                suffix += 1
                code = f'{base_code[:56]}_{suffix}'
            resource, _ = ScheduleResource.objects.get_or_create(
                project=project, code=code,
                defaults={'name': activity.responsible_role, 'role': activity.responsible_role, 'resource_type': 'labor'},
            )
            resources[activity.responsible_role] = resource
        hours = activity.duration_days * calendar.hours_per_day
        assignment_rows.append(ActivityAssignment(
            activity=activity, resource=resource, planned_units=hours, budgeted_hours=hours,
            budgeted_cost=hours * resource.unit_cost,
        ))
    ActivityAssignment.objects.bulk_create(assignment_rows, batch_size=500)

    run = calculate_schedule_version(version, requested_by=requested_by)
    if issues:
        run.issues = list(run.issues or []) + issues
        run.save(update_fields=['issues', 'updated_at'])
    return version, run, issues
