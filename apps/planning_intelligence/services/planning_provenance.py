"""Read-only field provenance; labels do not confer evidence or rule approval."""
from copy import deepcopy
from uuid import UUID
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace

from ..evidence_models import EvidenceNode


FIELDS = {'title': 'identity', 'duration_days': 'duration', 'planned_start_date': 'start_date',
          'planned_finish_date': 'finish_date', 'depends_on': 'dependencies',
          'calendar_id': 'calendar', 'constraint_type': 'constraints', 'responsible_role': 'responsible_role'}


def _label(kind, status, references=None, **details):
    names = {'document': 'Document evidence', 'approved_rule': 'Approved planning rule',
             'derived': 'Derived from accepted inputs',
             'proposal': 'Proposal', 'calculated': 'Calculated', 'planner': 'Planner input',
             'unknown': 'Provenance not recorded'}
    return {'type': kind, 'label': names[kind], 'status': status,
            'source_references': deepcopy(references or []), **details}


def _fact_id(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def _bound_value(node, task, field, entities):
    if node.property != FIELDS[field] or node.entity_id != task.get('evidence_entity_id'):
        return False
    value = node.value
    if field == 'duration_days':
        try:
            return isinstance(value, dict) and value.get('unit') == 'working_days' and Decimal(str(value['value'])) == Decimal(str(task.get(field)))
        except (KeyError, InvalidOperation, TypeError, ValueError):
            return False
    if field == 'title':
        return value == task.get(field)
    if field == 'depends_on':
        actual = [{'predecessor_id': entities.get(row.get('task_id')), 'type': row.get('type'),
                   'lag': row.get('lag_days'), 'lag_unit': 'working_days'} for row in task.get('dependency_details') or []]
        return isinstance(value, list) and sorted(actual, key=str) == sorted(value, key=str)
    if field in {'planned_start_date', 'planned_finish_date'}:
        return value == task.get(field)
    # Calendar and constraint facts remain accessible in Evidence until their
    # full structured values are projected, not just database IDs/type labels.
    return False


def annotate_plan_provenance(project, state):
    tasks = state.get('tasks') or []
    from ..models import ScheduleVersion
    from .planning_builds import build_provenance
    version_id = state.get('version_id') or state.get('current_version_id')
    version = ScheduleVersion.objects.filter(pk=version_id, schedule__project=project, is_deleted=False).first() if version_id else None
    build_fields = build_provenance(version) if version and version.planning_build_id else {}
    ids = {_fact_id(value) for row in tasks for value in (row.get('property_provenance') or {}).values()}
    ids.discard(None)
    # Project isolation and server-owned graph decisions are mandatory. Client
    # metadata named "approved_rule" or "document" cannot grant those labels.
    facts = {str(row.pk): row for row in EvidenceNode.objects.filter(graph__project=project, pk__in=ids, kind='fact')}
    counts = {}
    entities = {row['id']: row.get('evidence_entity_id') for row in tasks}
    for task in tasks:
        result = {}
        recorded = task.get('property_provenance') or {}
        for field, prop in FIELDS.items():
            node = facts.get(_fact_id(recorded.get(prop)))
            if node and not _bound_value(node, task, field, entities):
                node = None
            if node and node.provenance_type == 'document_evidence' and node.sources and node.validation.get('quote_verified'):
                result[field] = _label('document', node.status if node.current else 'superseded', node.sources,
                                       fact_ids=[str(node.pk)])
            elif node and node.provenance_type == 'approved_planning_input' and node.status == 'accepted':
                result[field] = _label('planner', 'accepted' if node.current else 'superseded', node.sources,
                                       fact_ids=[str(node.pk)])
            else:
                result[field] = _label('unknown', 'not_recorded')
        source = task.get('duration_source')
        if result['duration_days']['type'] == 'unknown':
            if source in {'proposed', 'template', 'workflow_template', 'default', 'estimated', 'estimate', 'ai'}:
                result['duration_days'] = _label('proposal', 'requires_review')
            elif source in {'planner', 'manual', 'user', 'confirmed'}:
                result['duration_days'] = _label('planner', 'declared')
            elif source in {'source_document', 'source_requirement'}:
                refs = (task.get('duration_evidence') or {}).get('source_references') or []
                if refs:
                    result['duration_days'] = _label('unknown', 'requires_review', refs)
        if task.get('dependency_status') == 'planner' and result['depends_on']['type'] == 'unknown':
            result['depends_on'] = _label('planner', 'declared')
        # A workflow selection proposes internal stages; it is never a source
        # quote nor an approved profile application merely because IDs match.
        if task.get('workflow_stage_code'):
            result['workflow_stage_code'] = _label('proposal', 'requires_review')
        lineage = build_fields.get(task.get('external_id') or task['id'], {})
        for field, prop in FIELDS.items():
            entry = lineage.get(prop)
            if not entry:
                continue
            # This map comes only from a persisted, verified projection whose
            # values match the immutable build. Arbitrary metadata is ignored.
            kind = {'document_evidence': 'document', 'approved_planning_input': 'planner',
                    'approved_planning_rule': 'approved_rule', 'planner_decision': 'planner', 'planner_input': 'planner',
                    'deterministic_derivation': 'derived'}.get(entry.get('type'), 'unknown')
            result[field] = _label(kind, 'accepted', entry.get('source_references'), lineage=entry,
                                   fact_ids=entry.get('fact_ids', []), rule_id=entry.get('rule_id'))
        if lineage and task.get('workflow_stage_code'):
            result['workflow_stage_code'] = _label('approved_rule', 'approved',
                build_id=str(version.planning_build_id), profile_id=version.planning_build.profile_id)
        if task.get('calculated'):
            for field in ('planned_start_date', 'planned_finish_date', 'total_float_days', 'free_float_days'):
                source_value = result.get(field)
                result[field] = _label('calculated', 'calculated', calculation_basis=task.get('calculation_basis'),
                                       source_value=source_value if source_value and source_value['type'] != 'unknown' else None)
        task['field_provenance'] = result
        for item in result.values():
            counts[item['type']] = counts.get(item['type'], 0) + 1
    state['provenance_summary'] = counts
    return state


def annotate_published_provenance(state, snapshot):
    """Historical decisions come exclusively from the approved snapshot."""
    inputs = snapshot.get('accepted_inputs') or {}
    build = inputs.get('planning_build') or {}
    graph = inputs.get('evidence_graph') or inputs.get('version_evidence_input_snapshot') or build.get('evidence_snapshot') or {}
    facts = {str(row['id']): SimpleNamespace(pk=row['id'], property=row['property'], entity_id=row['entity_id'],
              value=row.get('value'), sources=row.get('sources') or [], status=row.get('status'),
              provenance_type=row.get('provenance_type'), validation=row.get('validation') or {})
             for row in graph.get('facts') or []}
    planned = {row['id']: row for row in (build.get('plan') or {}).get('activities') or []}
    entities = {row['id']: row.get('evidence_entity_id') for row in state['tasks']}
    counts = {}
    for task in state['tasks']:
        result = {field: _label('unknown', 'not_retained') for field in FIELDS}
        for field, prop in FIELDS.items():
            node = facts.get(str((task.get('property_provenance') or {}).get(prop)))
            if node and node.status == 'accepted' and _bound_value(node, task, field, entities):
                if node.provenance_type == 'document_evidence' and node.sources and node.validation.get('quote_verified'):
                    result[field] = _label('document', 'accepted_at_publication', node.sources, fact_ids=[str(node.pk)])
                elif node.provenance_type == 'approved_planning_input':
                    result[field] = _label('planner', 'accepted_at_publication', node.sources, fact_ids=[str(node.pk)])
        expected = planned.get(task['id']) or {}
        lineage = expected.get('property_lineage') or {}
        # The baseline snapshot is immutable; still bind badges to the exact
        # saved leaf values rather than trusting arbitrary activity metadata.
        bindings = {'identity': task['title'], 'responsible_role': task.get('responsible_role'),
                    'start_date': task.get('planned_start_date'), 'finish_date': task.get('planned_finish_date'),
                    'constraints': [] if task.get('constraint_type') in {None, 'none'} else
                        [{'type': task['constraint_type'], 'date': task.get('constraint_date')}],
                    'dependencies': [{'predecessor_id': row['task_id'], 'type': row['type'],
                                      'lag': row['lag_days'], 'lag_unit': 'working_days'} for row in task['dependency_details']]}
        for field, prop in FIELDS.items():
            entry = lineage.get(prop)
            if not entry:
                continue
            matches = bindings.get(prop) == entry.get('value') if prop in bindings else False
            if prop == 'duration':
                try:
                    matches = entry['value']['unit'] == 'working_days' and Decimal(str(entry['value']['value'])) == Decimal(str(task['duration_days']))
                except (KeyError, InvalidOperation, TypeError):
                    matches = False
            if prop == 'calendar':
                source_calendar = next((row for row in inputs.get('calendars') or []
                                        if row['id'] == (task.get('calendar_id') or inputs.get('default_calendar_id'))), None)
                if source_calendar:
                    from .planning_builds import _calendar_normal
                    normalized = {key: deepcopy(source_calendar.get(key)) for key in ('working_weekdays', 'timezone', 'working_times')}
                    normalized['hours_per_day'] = float(source_calendar['hours_per_day'])
                    normalized['exceptions'] = [{key: value for key, value in row.items()
                         if key in {'date', 'is_working', 'working_times', 'working_hours'} and value is not None}
                         for row in source_calendar.get('exceptions') or []]
                    for exception in normalized['exceptions']:
                        if exception.get('working_hours') is not None:
                            exception['working_hours'] = float(exception['working_hours'])
                    matches = _calendar_normal(normalized) == _calendar_normal(entry.get('value'))
            if not matches:
                continue
            kind = {'document_evidence': 'document', 'approved_planning_input': 'planner',
                    'approved_planning_rule': 'approved_rule', 'planner_decision': 'planner', 'planner_input': 'planner',
                    'deterministic_derivation': 'derived'}.get(entry.get('type'), 'unknown')
            result[field] = _label(kind, 'accepted_at_publication', entry.get('source_references'),
                                    lineage=deepcopy(entry), fact_ids=entry.get('fact_ids', []), rule_id=entry.get('rule_id'))
        if expected and task.get('workflow_stage_code') == expected.get('workflow_stage_code') and (build.get('profile_snapshot') or {}).get('approval'):
            result['workflow_stage_code'] = _label('approved_rule', 'approved_at_publication',
                                                  build_id=build.get('id'), profile_id=build.get('profile_id'))
        if task.get('calculated'):
            for field in ('planned_start_date', 'planned_finish_date', 'total_float_days', 'free_float_days'):
                previous = result.get(field)
                if task.get(field) is not None:
                    result[field] = _label('calculated', 'published_baseline', calculation_basis='published_baseline',
                        source_value=previous if previous and previous['type'] != 'unknown' else None)
        task['field_provenance'] = result
        for item in result.values():
            counts[item['type']] = counts.get(item['type'], 0) + 1
    state['provenance_summary'] = counts
    return state
