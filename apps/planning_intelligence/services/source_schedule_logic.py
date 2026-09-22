"""Explicit deterministic planning revisions of intact imported source schedules.

Named stage rules and a declared planning calendar are planner inputs, never
claims about the PDF's native network. The original source version is retained.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import date
from decimal import Decimal
import re
from uuid import uuid4

from django.core import signing
from django.db import transaction
from django.http import Http404

from ..models import (ActivityRelationship, CalendarException, PlanningProject, Schedule,
                      ScheduleActivity, ScheduleVersion, ScheduleWBSNode, WorkCalendar)
from .audit import record_event
from .cpm import calculate_schedule_version
from .evidence_schema import network_issues, validate_value
from .operational_jobs import canonical_fingerprint
from .schedule_approval import ScheduleApprovalError
from .source_schedule_import import imported_snapshot, can_import_source, _content, _metadata, _source_summary

SCHEMA = 'source-schedule-logic/1'
SALT = 'planning.source-schedule-logic'
RULE = 'named-five-stage-fs/1'
STAGES = [('IFR', 'IFR'), ('COMPANY_REVIEW', 'COMPANY REVIEW'), ('IFA', 'IFA'),
          ('COMPANY_APPROVAL', 'COMPANY APPROVAL'), ('FINAL_ISSUE', 'IFT/IFM')]
_STAGE = re.compile(r'\s+-\s+(IFR|COMPANY REVIEW|IFA|COMPANY APPROVAL|IFT/IFM)\s*$', re.I)


def _error(message, code='source_logic_invalid', status=409):
    raise ScheduleApprovalError(message, code=code, status_code=status)


def _write(project, actor):
    if not can_import_source(project, actor):
        _error('Your project access does not permit building a planning revision.', 'source_logic_forbidden', 403)


def _calendar(spec):
    if not isinstance(spec, dict) or set(spec) - {'working_weekdays', 'hours_per_day', 'timezone', 'exceptions', 'working_times', 'origin'}:
        _error('Supply an explicit planning calendar and its origin.')
    origin = spec.get('origin')
    if origin not in {'user_selected', 'scenario_assumption'}:
        _error('Identify the calendar as user selected or a scenario assumption.')
    calendar = {key: deepcopy(value) for key, value in spec.items() if key != 'origin'}
    calendar.setdefault('working_times', {})
    error = validate_value('calendar', calendar)
    if error:
        _error(error)
    calendar['working_weekdays'] = sorted(calendar['working_weekdays'])
    calendar['hours_per_day'] = float(calendar['hours_per_day'])
    calendar['exceptions'] = sorted(calendar['exceptions'], key=lambda row: row['date'])
    for row in calendar['exceptions']:
        row.setdefault('working_times', [])
        if row.get('working_hours') is None:
            row.pop('working_hours', None)
        else:
            row['working_hours'] = float(row['working_hours'])
        if row['is_working'] and Decimal(str(row['working_hours'])) != Decimal(str(calendar['hours_per_day'])):
            _error('The daily CPM engine cannot use partial-day calendar exceptions.')
    if Decimal(str(calendar['hours_per_day'])) != Decimal(str(calendar['hours_per_day'])).quantize(Decimal('.01')):
        _error('Calendar hours support at most two decimal places.')
    return {**calendar, 'origin': origin}


def _source(project, version_id):
    version = ScheduleVersion.objects.select_related('schedule').filter(pk=version_id, schedule__project=project,
        schedule__is_deleted=False, is_deleted=False).first()
    if version is None:
        raise Http404
    snapshot = imported_snapshot(version)
    if snapshot is None:
        _error('Select an unchanged imported source schedule.', 'source_logic_source_invalid')
    original = signing.loads(version.evidence_input_snapshot['review_token'], salt='planning.source-schedule-import')
    if original.get('content_fingerprint') != _content(project):
        _error('The source inputs changed. Review and import the current source before building logic.', 'source_logic_source_stale')
    return version, snapshot


def _compile(snapshot, calendar):
    rows = snapshot['activities']
    if not rows or len(rows) > 20000:
        _error('Select a source schedule with between one and 20,000 activities.')
    hierarchy = snapshot.get('source_hierarchy') or {}
    nodes = deepcopy(hierarchy.get('nodes') or [])
    if not nodes:
        _error('The source needs verified printed hierarchy to apply named stage rules.')
    grouped = defaultdict(list)
    for row in rows:
        grouped[(hierarchy.get('activity_parents') or {}).get(row['id'])].append(row)
    stage_fields, links, grouped_ids = {}, [], set()
    by_id = {row['id']: row for row in rows}
    for node in nodes:
        siblings = grouped[node['key']]
        matched = [_STAGE.search(row['name']) for row in siblings]
        if (len(siblings) != 5 or any(item is None for item in matched)
                or [item[1].upper() for item in matched] != [label for _, label in STAGES]
                or any(row['name'][:item.start()].strip().casefold() != node['name'].strip().casefold()
                       for row, item in zip(siblings, matched))):
            continue
        for row, (code, label) in zip(siblings, STAGES):
            stage_fields[row['id']] = {'workflow_stage_code': code, 'workflow_stage_name': label}
            grouped_ids.add(row['id'])
        for predecessor, successor in zip(siblings, siblings[1:]):
            links.append({'predecessor_id': predecessor['id'], 'successor_id': successor['id'], 'type': 'FS', 'lag_days': 0,
                'metadata': {'source': 'source_stage_rule', 'evidence_type': 'planning_inference', 'status': 'planner_applied', 'source_fact': False, 'rule': RULE,
                    'rationale': 'Named stages within this printed deliverable follow IFR, company review, IFA, company approval and final issue.',
                    'source_references': deepcopy(node['source_evidence']['source_references'])}})
    if not links:
        _error('No exact five-stage deliverable groups were found. No relationships were inferred from row order.')
    existing = []
    for row in snapshot.get('relationships') or []:
        if row['predecessor_id'] not in by_id or row['successor_id'] not in by_id:
            _error('A source relationship points outside the imported activities.')
        existing.append({'predecessor_id': row['predecessor_id'], 'successor_id': row['successor_id'],
            'type': row['type'], 'lag_days': row['lag_days'], 'metadata': {
                'source': 'source_document', 'status': 'source_document', 'source_fact': True,
                'source_references': deepcopy(row.get('source_references') or [])}})
    combined = {}
    for row in existing:
        key = row['predecessor_id'], row['successor_id']
        if key in combined:
            _error('Multiple source relationships connect the same pair. Review these links before applying the stage rule.')
        combined[key] = row
    for row in links:
        key = row['predecessor_id'], row['successor_id']
        previous = combined.get(key)
        if previous and (previous['type'], previous['lag_days']) != (row['type'], row['lag_days']):
            _error('An existing source relationship conflicts with the named stage rule.')
        combined.setdefault(key, row)
    relationships = list(combined.values())
    incoming = defaultdict(list)
    for link in relationships:
        lag = Decimal(str(link['lag_days']))
        if lag != lag.to_integral_value():
            _error('The daily CPM engine requires whole-day relationship lags.')
        incoming[link['successor_id']].append({'predecessor_id': link['predecessor_id'], 'type': link['type'],
            'lag': link['lag_days'], 'lag_unit': 'working_days'})
    issues = network_issues({row['id']: {'dependencies': incoming[row['id']]} for row in rows})
    if issues:
        _error(issues[0]['message'])
    activities = []
    nodes_by_key = {row['key']: row for row in nodes}
    for index, row in enumerate(rows):
        quantity = Decimal(str(row['duration_days']))
        if quantity < 0 or quantity != quantity.to_integral_value():
            _error('The daily CPM engine requires whole-day source durations. No source values were rounded.')
        evidence = row['source_evidence']
        values = evidence.get('values') or {}
        record_type = str(evidence.get('record_type') or '').lower().replace('_', ' ')
        if record_type == 'level of effort':
            _error('Dynamic level-of-effort activities are not supported by the daily CPM engine.')
        kind = {'start milestone': 'start_milestone', 'finish milestone': 'finish_milestone', 'task': 'task'}.get(record_type, 'task')
        if row.get('is_milestone') and kind == 'task':
            _error('A source milestone needs an explicit start or finish type before calculation.')
        release = values.get('planned_start_date') or (values.get('planned_finish_date') if kind == 'finish_milestone' else None)
        if release:
            date.fromisoformat(release)
        parent_key = (hierarchy.get('activity_parents') or {}).get(row['id'])
        parent = nodes_by_key.get(parent_key)
        while parent and parent.get('parent_key') is not None and nodes_by_key[parent['parent_key']].get('parent_key') is not None:
            parent = nodes_by_key[parent['parent_key']]
        metadata = _metadata(row)
        metadata.pop('source_import_schema', None)
        metadata.update(source_logic_schema=SCHEMA, duration_calendar_verified=True,
            source_calendar_verified=False, planning_calendar_basis='declared_planning_input',
            calendar_origin=calendar['origin'], dependency_status='planner_rule',
            release_constraint_basis=('printed_finish_milestone_date' if kind == 'finish_milestone' and not values.get('planned_start_date')
                                      else 'printed_start_date') if release else None,
            release_constraint_source_fact=False,
            schedule_phase=parent['name'] if parent else 'Source schedule',
            **stage_fields.get(row['id'], {}))
        activities.append({'id': row['id'], 'name': row['name'], 'duration_days': row['duration_days'],
            'activity_type': kind, 'wbs_key': parent_key, 'sort_order': index,
            'constraint_type': 'start_no_earlier' if release else 'none', 'constraint_date': release,
            'metadata': metadata})
    summary = {'activity_count': len(rows), 'wbs_count': len(nodes), 'workflow_group_count': len(grouped_ids) // 5,
               'relationship_count': len(relationships), 'unsequenced_activity_count': len(rows) - len(grouped_ids),
               'source_start_date': snapshot['project_window']['start_date'], 'source_finish_date': snapshot['project_window']['finish_date']}
    return {'activities': activities, 'relationships': relationships, 'wbs': nodes, 'summary': summary}


def _payload(project, version_id, calendar, reason):
    source, snapshot = _source(project, version_id)
    compiled = _compile(snapshot, calendar)
    return {'schema': SCHEMA, 'project_id': project.pk, 'source_version_id': source.pk,
        'source_snapshot_fingerprint': canonical_fingerprint(snapshot), 'content_fingerprint': _content(project),
        'project_start': str(project.effective_date), 'project_finish': str(project.planned_end_date),
        'calendar': calendar, 'rule': RULE, 'reason': reason, **compiled}


def _assumptions(payload):
    return ['The selected calendar is a planning input; the PDF does not establish its working calendar.',
            'Named five-stage deliverables use finish-to-start links with zero lag. These are applied planning rules, not imported native relationships.',
            'Printed start dates are planning release dates. Calculated dates and float may differ from the printed schedule.',
            f"{payload['summary']['unsequenced_activity_count']} activities outside exact five-stage groups are retained without inferred cross-discipline links.",
            'Calendar and network completeness require review before baseline approval.']


@transaction.atomic
def preview_source_logic(project, actor, *, source_version_id, calendar_spec, revision, reason='Build named source-stage logic.'):
    project = PlanningProject.objects.select_for_update().get(pk=project.pk, is_deleted=False)
    _write(project, actor)
    if project.master_schedule_revision != revision or project.master_schedule_version_id != source_version_id:
        _error('Refresh the current imported schedule before previewing logic.', 'source_logic_stale')
    if not reason.strip():
        _error('Record the reason for this planning revision.')
    payload = _payload(project, source_version_id, _calendar(calendar_spec), reason.strip())
    token = signing.dumps({'schema': SCHEMA, 'project_id': project.pk, 'actor_id': actor.pk,
        'source_version_id': source_version_id, 'revision': revision, 'calendar': payload['calendar'],
        'reason': reason.strip(), 'fingerprint': canonical_fingerprint(payload), 'nonce': uuid4().hex}, salt=SALT, compress=True)
    return {'preview_token': token, 'can_apply': True, 'summary': payload['summary'], 'calendar': payload['calendar'],
            'assumptions': _assumptions(payload), 'warnings': [{'code': 'planning_network_review_required',
                'message': 'Only exact named stage groups are linked. Review the remaining activities and calendar before baselining.'}]}


def _calendar_value(calendar):
    return {'working_weekdays': sorted(calendar.working_weekdays), 'hours_per_day': float(calendar.hours_per_day),
        'timezone': calendar.timezone, 'working_times': calendar.working_times,
        'exceptions': [{'date': row.date.isoformat(), 'is_working': row.is_working,
            **({'working_hours': float(row.working_hours)} if row.working_hours is not None else {}),
            'working_times': row.working_times} for row in calendar.exceptions.filter(is_deleted=False).order_by('date')]}


def verified_logic_payload(version, *, current=True):
    stored = version.evidence_input_snapshot or {}
    if stored.get('schema') != SCHEMA:
        return None
    payload = stored.get('payload')
    try:
        signed = signing.loads(stored.get('input_token', ''), salt=SALT + '.inputs')
    except (signing.BadSignature, TypeError):
        return None
    if (not isinstance(payload, dict) or signed.get('fingerprint') != canonical_fingerprint(payload)
            or signed.get('project_id') != version.schedule.project_id or signed.get('actor_id') != version.created_by_id
            or payload['project_id'] != version.schedule.project_id or version.parent_version_id != payload['source_version_id']):
        return None
    source = ScheduleVersion.objects.select_related('schedule').filter(pk=payload['source_version_id'],
        schedule__project_id=version.schedule.project_id, is_deleted=False, schedule__is_deleted=False).first()
    original = imported_snapshot(source) if source else None
    if original is None or canonical_fingerprint(original) != payload['source_snapshot_fingerprint']:
        return None
    if current and _content(version.schedule.project) != payload['content_fingerprint']:
        return None
    calendar = version.schedule.default_calendar
    expected_calendar = {key: value for key, value in payload['calendar'].items() if key != 'origin'}
    if (calendar is None or calendar.project_id != version.schedule.project_id
            or canonical_fingerprint(_calendar_value(calendar)) != canonical_fingerprint(expected_calendar)
            or str(version.schedule.planned_start) != payload['project_start']):
        return None
    actual_nodes = list(version.wbs_nodes.filter(is_deleted=False))
    expected_nodes = {row['code']: row for row in payload['wbs']}
    node_keys = {row.pk: expected_nodes[row.code]['key'] for row in actual_nodes if row.code in expected_nodes}
    if len(actual_nodes) != len(expected_nodes):
        return None
    for node in actual_nodes:
        expected = expected_nodes.get(node.code)
        if (not expected or node.name != expected['name'] or node.level != expected['level'] or node.sort_order != expected['sort_order']
                or node.discipline or (node.parent_id is not None and node.parent_id not in node_keys)
                or node_keys.get(node.parent_id) != expected['parent_key']):
            return None
    actual = list(version.activities.filter(is_deleted=False))
    expected = {row['id']: row for row in payload['activities']}
    if len(actual) != len(expected):
        return None
    for row in actual:
        plan = expected.get(row.external_id)
        if (not plan or row.name != plan['name'] or row.duration_days != Decimal(str(plan['duration_days']))
                or row.activity_type != plan['activity_type'] or row.metadata != plan['metadata']
                or row.calendar_id != calendar.pk or row.sort_order != plan['sort_order']
                or row.constraint_type != plan['constraint_type'] or (str(row.constraint_date) if row.constraint_date else None) != plan['constraint_date']
                or (row.wbs_node_id is not None and row.wbs_node_id not in node_keys) or node_keys.get(row.wbs_node_id) != plan['wbs_key']):
            return None
    keys = {row.pk: row.external_id for row in actual}
    actual_links = [(keys.get(row.predecessor_id), keys.get(row.successor_id), row.relationship_type, str(row.lag_days.normalize()), canonical_fingerprint(row.metadata))
                   for row in version.relationships.filter(is_deleted=False)]
    expected_links = [(row['predecessor_id'], row['successor_id'], row['type'], str(Decimal(str(row['lag_days'])).normalize()), canonical_fingerprint(row['metadata']))
                      for row in payload['relationships']]
    return payload if sorted(actual_links, key=str) == sorted(expected_links, key=str) else None


def logic_readiness(version):
    intact = verified_logic_payload(version) is not None
    return {'policy': 'deterministic_source_logic', 'rule_version': RULE, 'ready_for_calculation': intact,
        'ready_for_approval': False, 'ready_for_export': False, 'issues': [{
            'code': 'source_logic_review_required' if intact else 'source_logic_inputs_changed',
            'message': 'Review the planning calendar and complete activity network before baseline approval.' if intact else
                'Source or planning revision inputs changed after their recorded decision. Build a new reviewed revision.',
            'severity': 'warning' if intact else 'error',
            'blocks': ['approval', 'export'] if intact else ['calculation', 'approval', 'export']}]}


@transaction.atomic
def apply_source_logic(project, actor, *, preview_token, reason='Apply the reviewed deterministic planning revision.'):
    project = PlanningProject.objects.select_for_update().get(pk=project.pk, is_deleted=False)
    _write(project, actor)
    try:
        signed = signing.loads(preview_token, salt=SALT, max_age=3600)
    except (signing.BadSignature, TypeError):
        _error('The logic preview expired or is invalid. Preview it again.', 'source_logic_token_invalid')
    if signed.get('schema') != SCHEMA or signed.get('project_id') != project.pk or signed.get('actor_id') != actor.pk:
        _error('This logic preview belongs to another project or actor.', 'source_logic_token_invalid')
    existing = ScheduleVersion.objects.select_related('schedule').filter(schedule__project=project,
        evidence_input_snapshot__preview_token=preview_token).first()
    if existing:
        if project.master_schedule_version_id != existing.pk or verified_logic_payload(existing) is None:
            _error('This preview was already applied and its inputs or selected version changed.', 'source_logic_stale')
        return {'schedule_version_id': existing.pk, 'created': False, 'notice': 'This planning revision is already selected.'}
    if project.master_schedule_revision != signed['revision'] or project.master_schedule_version_id != signed['source_version_id']:
        _error('The current schedule changed after preview.', 'source_logic_stale')
    list(project.files.select_for_update().filter(is_deleted=False).values_list('pk', flat=True))
    ScheduleVersion.objects.select_for_update().get(pk=signed['source_version_id'])
    payload = _payload(project, signed['source_version_id'], _calendar(signed['calendar']), signed['reason'])
    if canonical_fingerprint(payload) != signed['fingerprint']:
        _error('The source or planning inputs changed after preview.', 'source_logic_stale')
    if not reason.strip():
        _error('Record the reason for applying this planning revision.')
    spec = payload['calendar']
    calendar = WorkCalendar.objects.create(project=project, name='Source-logic planning calendar ' + signed['nonce'][:16],
        working_weekdays=spec['working_weekdays'], hours_per_day=spec['hours_per_day'], timezone=spec['timezone'], working_times=spec['working_times'])
    CalendarException.objects.bulk_create([CalendarException(calendar=calendar, date=row['date'], is_working=row['is_working'],
        working_hours=row.get('working_hours'), working_times=row.get('working_times') or [], name='Declared planning exception') for row in spec['exceptions']])
    schedule = Schedule.objects.create(project=project, code='SOURCE-LOGIC-' + signed['nonce'],
        name=(project.name + ' — Planning logic')[:255], planned_start=payload['project_start'], default_calendar=calendar, created_by=actor)
    input_token = signing.dumps({'fingerprint': canonical_fingerprint(payload), 'project_id': project.pk, 'actor_id': actor.pk}, salt=SALT + '.inputs')
    version = ScheduleVersion.objects.create(schedule=schedule, version=1, parent_version_id=payload['source_version_id'],
        created_by=actor, change_summary='Deterministic named-stage planning revision; source schedule preserved.',
        evidence_input_snapshot={'schema': SCHEMA, 'payload': payload, 'input_token': input_token,
                                 'preview_token': preview_token, 'apply_reason': reason.strip()})
    nodes = {}
    for row in payload['wbs']:
        nodes[row['key']] = ScheduleWBSNode.objects.create(version=version, parent=nodes.get(row['parent_key']),
            code=row['code'], name=row['name'], level=row['level'], sort_order=row['sort_order'])
    activities = {row['id']: ScheduleActivity(version=version, external_id=row['id'], name=row['name'],
        duration_days=row['duration_days'], activity_type=row['activity_type'], wbs_node=nodes.get(row['wbs_key']),
        calendar=calendar, constraint_type=row['constraint_type'], constraint_date=row['constraint_date'],
        sort_order=row['sort_order'], metadata=row['metadata']) for row in payload['activities']}
    ScheduleActivity.objects.bulk_create(list(activities.values()), batch_size=500)
    ActivityRelationship.objects.bulk_create([ActivityRelationship(version=version, predecessor=activities[row['predecessor_id']],
        successor=activities[row['successor_id']], relationship_type=row['type'], lag_days=row['lag_days'], metadata=row['metadata'])
        for row in payload['relationships']], batch_size=500)
    run = calculate_schedule_version(version, requested_by=actor)
    project.master_schedule_version = version
    project.master_schedule_revision += 1
    project.save(update_fields=['master_schedule_version', 'master_schedule_revision'])
    record_event(project=project, actor=actor, action='source_logic.applied', entity=version,
        after={'source_version_id': payload['source_version_id'], 'summary': payload['summary'], 'rule': RULE,
               'calendar_origin': spec['origin'], 'reason': reason.strip(), 'assumptions': _assumptions(payload)})
    return {'schedule_version_id': version.pk, 'created': True, 'summary': {**payload['summary'],
        'critical_activity_count': run.critical_activity_count, 'calculated_finish': str(run.project_finish)},
        'notice': 'The deterministic planning revision is calculated. Its source version is preserved; calendar and network review remain open.'}


def enrich_logic_state(version, state):
    payload = verified_logic_payload(version, current=False)
    if payload is None:
        return state
    state['stale_inputs'] = _content(version.schedule.project) != payload['content_fingerprint']
    state['source_logic'] = {'source_version_id': payload['source_version_id'], 'rule': RULE,
        'calendar_origin': payload['calendar']['origin'], 'calendar': deepcopy(payload['calendar']), 'assumptions': _assumptions(payload),
        'summary': {**payload['summary'], 'calculated_start_date': state.get('project_summary', {}).get('planned_start_date'),
                    'calculated_finish_date': state.get('project_summary', {}).get('planned_finish_date'),
                    'critical_activity_count': sum(bool(row.get('is_critical')) for row in state['tasks']) if state.get('calculation_available') else None},
        'status': 'calculated_planning_revision' if state.get('calculation_available') else 'inputs_changed'}
    state['read_only_reason'] = 'Calculated planning revision. The original source dates and float remain available for comparison.'
    state['calendar'].update(source_verified=False, evidence_status='Declared planning calendar', is_fallback=False)
    state['work_calendar'] = deepcopy(state['calendar'])
    expected = {row['id']: row for row in payload['activities']}
    incoming = defaultdict(list)
    for link in payload['relationships']:
        incoming[link['successor_id']].append(link)
    for task in state['tasks']:
        row = expected[task['id']]
        task.update(duration_calendar_verified=True, activity_code=row['metadata'].get('source_activity_id') or task['id'],
                    source_calendar_verified=False, planning_calendar_basis='declared_planning_input', calendar_origin=payload['calendar']['origin'],
                    activity_code_source='source_document', **{key: row['metadata'][key] for key in
                    ('workflow_stage_code', 'workflow_stage_name', 'schedule_phase') if key in row['metadata']})
        task['dependency_details'] = [{'task_id': link['predecessor_id'], 'type': link['type'], 'lag_days': link['lag_days'],
                                      **deepcopy(link['metadata'])} for link in incoming[task['id']]]
        task['dependency_rationales'] = {link['predecessor_id']: deepcopy(link['metadata']) for link in incoming[task['id']]}
        if task.get('calculated'):
            task['calculation_basis'] = 'source_rule_cpm'
    original = imported_snapshot(version.parent_version)
    project_evidence = (original.get('source_project_summaries') or []) if original else []
    nodes = {row['code']: row for row in payload['wbs']}
    for node in state['wbs_nodes']:
        source = nodes[node['code']]['source_evidence']
        node.update(code_source='printed_row_reference', source_row_number=int(nodes[node['code']]['key']),
            is_source_project=bool(len(project_evidence) == 1 and source['source_references'] == project_evidence[0]['source_references']),
            source_summary=_source_summary(source))
    if len(project_evidence) == 1:
        state['source_project_summary'] = _source_summary(project_evidence[0])
        comparison = state['source_project_summary']
        state['source_logic']['summary'].update(source_duration_days=comparison.get('duration_days'),
            source_duration_unit=comparison.get('duration_unit'), source_total_float_days=comparison.get('source_total_float_days'),
            source_total_float_status=comparison.get('source_total_float_status'))
    state['source_logic']['summary'].update(calculated_duration_days=state.get('project_summary', {}).get('duration_days'),
        calculated_total_float_days=state.get('project_summary', {}).get('total_float_days'))
    return state
