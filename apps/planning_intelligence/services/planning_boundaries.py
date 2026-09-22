"""Enforce the boundary between accepted knowledge and schedule calculation.

Existing manually authored schedules remain editable through their established
workflow. Document-driven schedules require accepted graph inputs; metadata or
a high extraction confidence cannot grant permission to calculate or approve.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation

from django.core.serializers.json import DjangoJSONEncoder


BOUNDARY_RULE_VERSION = 'accepted-planning-inputs/2.0'
CALCULATION_RULE_VERSION = 'working-day-cpm/2.1'


def is_document_driven_version(version):
    seen = set()
    while version is not None and version.pk not in seen:
        seen.add(version.pk)
        if getattr(version, 'evidence_graph_id', None) is not None or getattr(version, 'planning_build_id', None) is not None:
            return True
        generation = version.source_generation
        if generation:
            engine = (generation.intelligence or {}).get('schedule_engine') or {}
            if engine.get('policy') == 'document_driven':
                return True
        state = version.schedule.project.simple_planning_state or {}
        if str(state.get('version_id')) == str(version.pk) and state.get('evidence_policy') == 'document_driven':
            return True
        if version.activities.filter(is_deleted=False, metadata__evidence_policy='document_driven').exists():
            return True
        version = version.parent_version
    return False


def _issue(code, message, *, entity_id=None, field=None):
    return {'code': code, 'message': message, 'entity_id': entity_id, 'field': field,
            'severity': 'error', 'blocks': ['calculation', 'approval', 'export']}


def _whole_number(value):
    try:
        number = Decimal(str(value))
        return number.is_finite() and number == number.to_integral_value()
    except (InvalidOperation, TypeError, ValueError):
        return False


def accepted_input_validation(version):
    """Return current readiness without materializing or changing any inputs."""
    if (version.evidence_input_snapshot or {}).get('schema') == 'planner-schedule-revision/1':
        from .gantt_editing import planner_revision_readiness
        return planner_revision_readiness(version)
    if (version.evidence_input_snapshot or {}).get('schema') == 'source-schedule-logic/1':
        from .source_schedule_logic import logic_readiness
        return logic_readiness(version)
    strict = is_document_driven_version(version)
    issues = []
    schedule = version.schedule
    if (version.evidence_input_snapshot or {}).get('schema') == 'ai-sequence-proposal/1':
        return {'policy': 'document_driven', 'rule_version': BOUNDARY_RULE_VERSION,
            'ready_for_calculation': False, 'ready_for_approval': False, 'ready_for_export': False,
            'issues': [_issue('ai_sequence_review_required',
                'Review AI proposed durations, calendar and logic in Evidence before accepting planning inputs and approving a baseline.')]}
    if (version.evidence_input_snapshot or {}).get('schema') == 'source-schedule-import/1':
        issues.append(_issue('source_import_review_required',
            'This version displays imported source dates. Review scope, calendar and activity logic in Evidence, then create an accepted schedule before calculating or approving a baseline.'))
        return {
            'policy': 'document_driven', 'rule_version': BOUNDARY_RULE_VERSION,
            'ready_for_calculation': False, 'ready_for_approval': False,
            'ready_for_export': False, 'issues': issues,
        }
    if schedule.default_calendar_id is None:
        issues.append(_issue('calendar_not_specified', 'Select and validate a working calendar before calculating.', field='calendar'))
    if schedule.planned_start is None:
        issues.append(_issue('project_start_not_specified', 'An accepted project start is required.', field='project_start'))
    if strict and schedule.default_calendar_id:
        calendar = schedule.default_calendar
        if calendar.exceptions.filter(is_deleted=False, is_working=True).exclude(working_hours=None).exclude(
            working_hours=calendar.hours_per_day,
        ).exists():
            issues.append(_issue('partial_day_calendar_unsupported',
                                 'The current working-day engine cannot preserve partial-day calendar exceptions.', field='calendar'))
    for activity in version.activities.filter(is_deleted=False):
        metadata = activity.metadata or {}
        if metadata.get('duration_pending') or metadata.get('duration_source') == 'missing_source':
            issues.append(_issue('duration_not_specified', 'Supply an accepted duration; missing duration is not zero.',
                                 entity_id=activity.external_id, field='duration'))
        if strict and not _whole_number(activity.duration_days):
            issues.append(_issue('duration_resolution_unsupported', 'The current calculation engine supports whole working days; it will not round this duration.',
                                 entity_id=activity.external_id, field='duration'))
        if strict and activity.calendar_id and activity.calendar_id != schedule.default_calendar_id:
            issues.append(_issue('mixed_calendars_unsupported', 'This calculation engine cannot preserve mixed activity calendars.',
                                 entity_id=activity.external_id, field='calendar'))
        if strict and activity.is_milestone and activity.duration_days != 0:
            issues.append(_issue('milestone_duration_conflict', 'A milestone has a nonzero duration; resolve the conflicting inputs.',
                                 entity_id=activity.external_id, field='duration'))
        if strict and activity.activity_type == 'level_of_effort':
            issues.append(_issue('level_of_effort_calculation_unsupported',
                                 'Dynamic level-of-effort scheduling is not implemented by the current CPM engine.',
                                 entity_id=activity.external_id, field='activity_type'))
    if strict:
        for relationship in version.relationships.filter(is_deleted=False):
            if not _whole_number(relationship.lag_days):
                issues.append(_issue('lag_resolution_unsupported', 'The current calculation engine supports whole working-day lags; it will not round this lag.',
                                     entity_id=relationship.pk, field='lag'))
        # The graph service resolves persisted evidence and decisions. Do not
        # replace it with a client-supplied "validated" or "ready" flag.
        if version.planning_build_id:
            from .planning_builds import build_input_validation
            issues.extend(build_input_validation(version))
        else:
            from .evidence_graph import validate_schedule_inputs
            issues.extend(validate_schedule_inputs(version))
        issues.extend(source_date_comparisons(version))
    return {
        'policy': 'document_driven' if strict else 'legacy_explicit_schedule',
        'rule_version': BOUNDARY_RULE_VERSION,
        'ready_for_calculation': not any('calculation' in item.get('blocks', []) for item in issues),
        'ready_for_approval': not any('approval' in item.get('blocks', []) for item in issues),
        'ready_for_export': not any('export' in item.get('blocks', []) for item in issues),
        'issues': issues,
    }


def source_date_comparisons(version, *, activities=None):
    """Compare source planned dates without turning them into constraints.

    CPM can calculate a useful draft from accepted duration/network/calendar
    inputs. A mismatch with an accepted source plan remains a review issue;
    it cannot silently become an approved baseline or validated native export.
    """
    if not is_document_driven_version(version):
        return []
    from .evidence_graph import evidence_graph_snapshot
    snapshot = evidence_graph_snapshot(version.schedule.project)
    accepted = snapshot.get('accepted_inputs') or {}
    facts = {(row['entity_id'], row['property']): row['id'] for row in snapshot.get('facts', [])
             if row.get('status') == 'accepted'}
    rows = activities if activities is not None else version.activities.filter(is_deleted=False)
    if version.planning_build_id:
        # Workflow dates belong to the deliverable envelope, not separately to
        # every generated stage. Source activity dates still compare directly.
        actual = {row.external_id: row for row in rows}
        groups = {}
        for item in version.planning_build.plan.get('activities', []):
            activity = actual.get(item['id'])
            if activity:
                groups.setdefault(item['source_entity_id'], []).append(activity)
        issues = []
        for entity, group in groups.items():
            values = accepted.get(entity, {})
            for field, attr, aggregate in [('start_date', 'planned_start', min), ('finish_date', 'planned_finish', max)]:
                dates = [getattr(activity, attr) for activity in group if getattr(activity, attr) is not None]
                source = values.get(field)
                if source and len(dates) == len(group) and str(aggregate(dates)) != source:
                    issues.append({'code': 'source_planned_date_mismatch', 'severity': 'error', 'entity_id': entity,
                        'field': field, 'source_date': source, 'calculated_date': str(aggregate(dates)),
                        'source_fact_id': facts.get((entity, field)), 'blocks': ['approval', 'export'],
                        'message': 'The calculated activity or deliverable dates differ from the accepted source. Review timing and logic before approval.'})
        return issues
    issues = []
    for activity in rows:
        metadata = activity.metadata or {}
        entity = metadata.get('evidence_entity_id') or (
            'task:' + str(metadata['simple_task_id']) if metadata.get('simple_task_id') else str(metadata.get('source_activity_id') or ''))
        values = accepted.get(entity, {})
        for source_field, calculated_field in (('start_date', 'planned_start'), ('finish_date', 'planned_finish')):
            source_date, calculated_date = values.get(source_field), getattr(activity, calculated_field)
            if source_date is None or calculated_date is None or str(calculated_date) == source_date:
                continue
            issues.append({
                'code': 'source_planned_date_mismatch', 'severity': 'error',
                'message': f'The calculated {source_field.replace("_", " ")} differs from the accepted source planned date. Review the dates, logic or an explicitly authorized constraint.',
                'entity_id': entity, 'activity_id': activity.pk, 'activity': activity.external_id,
                'field': source_field, 'source_date': source_date, 'calculated_date': str(calculated_date),
                'source_fact_id': facts.get((entity, source_field)), 'blocks': ['approval', 'export'],
            })
    return issues


def freeze_schedule_inputs(version):
    """A JSON-safe, hashed manifest retained in the immutable baseline snapshot."""
    from ..schedule_serializers import WorkCalendarSerializer, ScheduleResourceSerializer, ActivityAssignmentSerializer
    from ..models import ActivityAssignment
    project = version.schedule.project
    calendar_ids = set(version.activities.filter(is_deleted=False).exclude(calendar_id=None).values_list('calendar_id', flat=True))
    if version.schedule.default_calendar_id:
        calendar_ids.add(version.schedule.default_calendar_id)
    calendars = project.work_calendars.filter(pk__in=calendar_ids).order_by('pk')
    from .evidence_graph import evidence_graph_snapshot
    graph = evidence_graph_snapshot(project)
    from .planning_builds import build_manifest
    manifest = {
        'schema_version': '1.0', 'boundary_rule_version': BOUNDARY_RULE_VERSION,
        'calculation_rule_version': CALCULATION_RULE_VERSION,
        'project_id': project.pk, 'version_id': version.pk,
        'project_identity': {'id': project.pk, 'name': project.name, 'client': project.client, 'phase': project.phase,
                             'enterprise_project_id': project.enterprise_project_id,
                             'code': project.enterprise_project.code if project.enterprise_project_id else None},
        'schedule_identity': {'id': version.schedule.pk, 'name': version.schedule.name, 'code': version.schedule.code},
        'project_start': version.schedule.planned_start, 'project_finish': project.planned_end_date,
        'default_calendar_id': version.schedule.default_calendar_id,
        'calendars': WorkCalendarSerializer(calendars, many=True).data,
        'resources': ScheduleResourceSerializer(project.schedule_resources.filter(is_deleted=False), many=True).data,
        'assignments': ActivityAssignmentSerializer(ActivityAssignment.objects.filter(
            activity__version=version, activity__is_deleted=False, is_deleted=False,
        ), many=True).data,
        'activities': list(version.activities.filter(is_deleted=False).order_by('pk').values(
            'id', 'external_id', 'name', 'activity_type', 'duration_days', 'calendar_id',
            'constraint_type', 'constraint_date', 'metadata',
        )),
        'relationships': list(version.relationships.filter(is_deleted=False).order_by('pk').values(
            'id', 'predecessor_id', 'successor_id', 'relationship_type', 'lag_days', 'metadata',
        )),
        'source_generation_id': version.source_generation_id,
        'generation_input_fingerprint': version.source_generation.input_fingerprint if version.source_generation else None,
        'version_evidence_graph_id': getattr(version, 'evidence_graph_id', None),
        'version_evidence_graph_revision': getattr(version, 'evidence_graph_revision', None),
        'version_evidence_input_snapshot': getattr(version, 'evidence_input_snapshot', None),
        'planning_build': build_manifest(version) if version.planning_build_id else None,
        'evidence_graph': graph,
        'source_documents': list(project.files.filter(is_deleted=False).order_by('pk').values(
            'id', 'original_filename', 'category', 'size_bytes', 'updated_at', 'parse_status',
        )),
        'calculation': {'date_resolution': 'working_day', 'finish_convention': 'inclusive',
                        'relationship_types': ['FS', 'SS', 'FF', 'SF'],
                        'target_finish': 'explicit_project_finish_when_provided'},
    }
    encoded = json.dumps(manifest, cls=DjangoJSONEncoder, sort_keys=True, separators=(',', ':'))
    return {**json.loads(encoded), 'sha256': hashlib.sha256(encoded.encode()).hexdigest()}


def calculation_inputs_current(version):
    """Document-plan approval must use the exact inputs of its successful CPM."""
    if not is_document_driven_version(version):
        return True
    from ..models import PlanningAuditEvent
    run = version.calculation_runs.filter(is_deleted=False, status='succeeded').order_by('-finished_at', '-pk').first()
    if run is None:
        return False
    event = PlanningAuditEvent.objects.filter(
        project=version.schedule.project, action='schedule.calculated_inputs',
        entity_type='ScheduleCalculationRun', entity_id=str(run.pk),
    ).order_by('-pk').first()
    return bool(event and event.after.get('input_sha256') == freeze_schedule_inputs(version)['sha256'])
