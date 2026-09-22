"""Explicitly place reviewed source rows in Master Schedule without inventing CPM.

The preserved working draft remains the register/assignment workspace. This
command creates a separate source snapshot version; accepting its scope does not
approve a calendar, create cross-document identity, or approve a baseline.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.core import signing
from django.db import transaction
from django.http import Http404

from apps.rbac.action_policy import module_action_allowed
from ..access import can_write_project
from ..models import ActivityRelationship, PlanningProject, Schedule, ScheduleActivity, ScheduleVersion, ScheduleWBSNode
from .audit import record_event
from .document_plan import project_document_plan
from .evidence_graph import input_fingerprint, source_manifest
from .operational_jobs import canonical_fingerprint, schedule_state_fingerprint
from .schedule_approval import ScheduleApprovalError
from .source_date_read_model import source_date_fields, source_float_fields


SCHEMA = 'source-schedule-import/1'
SALT = 'planning.source-schedule-import'
MAX_AGE = 3600


def _error(message, code='source_import_conflict', status_code=409):
    raise ScheduleApprovalError(message, code=code, status_code=status_code)


def can_import_source(project, actor):
    return can_write_project(actor, project) and module_action_allowed(actor, 'planning_package', 'update')


def _write(project, actor):
    if not can_import_source(project, actor):
        _error('Your project access does not permit importing a source schedule.', 'source_import_forbidden', 403)


def source_rows(plan, source_file_id=None):
    return [row for row in plan.get('activities') or []
            if (row.get('source_evidence') or {}).get('basis') != 'document_register'
            and (source_file_id is None or any(ref.get('file_id') == source_file_id
                                              for ref in row.get('source_references') or []))]


def _state(project):
    version = project.master_schedule_version
    return canonical_fingerprint({
        'content': _content(project),
        'master_revision': project.master_schedule_revision, 'master_id': project.master_schedule_version_id,
        'master_inputs': schedule_state_fingerprint(version) if version else None,
        'master_artifact': None if not version else {
            'version': ScheduleVersion.objects.filter(pk=version.pk).values().first(),
            'schedule': Schedule.objects.filter(pk=version.schedule_id).values().first(),
            'activities': list(version.activities.order_by('pk').values()),
            'wbs': list(version.wbs_nodes.order_by('pk').values()),
            'relationships': list(version.relationships.order_by('pk').values()),
            'reviews': list(version.governance_reviews.order_by('pk').values()),
            'baselines': list(version.baselines.order_by('pk').values()),
        },
    })


def _content(project):
    return canonical_fingerprint({'evidence': input_fingerprint(project), 'draft': project.simple_planning_state,
                                  'project_name': project.name})


def _activity_type(row):
    explicit = str((row.get('source_evidence') or {}).get('record_type') or '').strip().lower().replace('_', ' ')
    return {'task': 'task', 'start milestone': 'start_milestone', 'finish milestone': 'finish_milestone',
            'level of effort': 'level_of_effort'}.get(explicit, 'task')


def _metadata(row):
    return {'evidence_policy': 'document_driven', 'duration_policy': 'source_only',
        'source_import_schema': SCHEMA, 'evidence_entity_id': row['id'],
        'source_activity_id': row.get('source_activity_id'), 'source_references': row['source_references'],
        'source_evidence': row['source_evidence'], 'duration_evidence': row['source_evidence'],
        'duration_source': 'source_document', 'duration_unit': row.get('duration_unit'),
        'duration_calendar_verified': False, 'duration_review_status': 'requires_review',
        'dependency_status': row.get('dependency_status'), 'source_missing_fields': row.get('missing_fields') or [],
        'date_authority': 'source_document', 'source_is_milestone': row.get('is_milestone')}


def _link_metadata(row):
    return {'source_import_schema': SCHEMA, 'source_references': row.get('source_references') or [],
            'source_excerpt': row.get('source_excerpt'), 'lag_unit': row.get('lag_unit'), 'evidence_policy': 'document_driven'}


def _decimal_fits(value, *, digits, nonnegative=True):
    try:
        number = Decimal(str(value))
        return (number.is_finite() and (not nonnegative or number >= 0)
                and abs(number) < Decimal(10) ** (digits - 2)
                and number == number.quantize(Decimal('0.01')))
    except (InvalidOperation, TypeError, ValueError):
        return False


def _geometry_hierarchy(activities, summaries):
    """Retain measured printed indentation without inventing native WBS IDs."""
    records = [(row['source_evidence'], row['id']) for row in activities] + [(row, None) for row in summaries]
    if not any(record.get('source_hierarchy') for record, _ in records):
        return None
    if any((record.get('source_hierarchy') or {}).get('basis') != 'printed_pdf_indentation' for record, _ in records):
        raise ValueError('The source hierarchy is incomplete.')
    ordered = sorted(records, key=lambda item: item[0]['source_hierarchy'].get('row_number', 0))
    nodes, parents, seen = [], {}, set()
    levels = {}
    for evidence, activity_id in ordered:
        hierarchy = evidence['source_hierarchy']
        number, parent, level = hierarchy.get('row_number'), hierarchy.get('parent_row_number'), hierarchy.get('level')
        if (type(number) is not int or number < 1 or number in seen or type(level) is not int or level < 0
                or (parent is not None and (type(parent) is not int or parent not in levels))
                or level != (levels[parent] + 1 if parent is not None else 0)):
            raise ValueError('The printed row hierarchy is inconsistent.')
        seen.add(number)
        if activity_id is not None:
            parents[activity_id] = str(parent) if parent is not None else None
            continue
        title = evidence.get('title')
        if not title or len(title) > 255:
            raise ValueError('A printed summary title exceeds the supported field length.')
        levels[number] = level
        nodes.append({'key': str(number), 'parent_key': str(parent) if parent is not None else None,
                      'code': f'PDF-{number}', 'name': title, 'level': level, 'sort_order': number,
                      'source_evidence': deepcopy(evidence)})
    return {'basis': 'printed_pdf_indentation', 'nodes': nodes, 'activity_parents': parents}


def _source_summary(evidence):
    values = evidence.get('values') or {}
    duration = values.get('original_duration_days')
    return {**source_date_fields({'source_evidence': evidence}),
            **source_float_fields({'source_evidence': evidence}),
            'duration_days': duration, 'original_duration_days': duration,
            'duration_unit': values.get('duration_unit'),
            'duration_source': 'source_document' if duration is not None else 'missing_source',
            'duration_evidence': deepcopy(evidence), 'source_evidence': deepcopy(evidence),
            'source_references': deepcopy(evidence.get('source_references') or []),
            'duration_calendar_verified': False, 'duration_basis': 'source_document', 'calculated': False}


def _payload(project, source_file_id):
    source = project.files.filter(pk=source_file_id, is_deleted=False).first()
    if source is None:
        raise Http404
    plan = project_document_plan(project)
    rows = source_rows(plan, source_file_id)
    ids = {row['id'] for row in rows}
    links = [{**row, 'successor_id': row['activity_id']} for row in plan.get('logic_matrix') or []
             if row.get('predecessor_id') in ids and row.get('activity_id') in ids]
    # Keep the source records, register, and review findings independently. No
    # title similarity or filename makes a schedule activity an MDR deliverable.
    snapshot = {
        'schema': SCHEMA, 'project_id': project.pk,
        'source_file': {'id': source.pk, 'name': source.original_filename},
        'source_manifest': source_manifest(project),
        'project_window': {'start_date': str(project.effective_date) if project.effective_date else None,
                           'finish_date': str(project.planned_end_date) if project.planned_end_date else None},
        'activities': deepcopy(rows), 'relationships': deepcopy(links),
        'register_inventory': deepcopy(plan.get('register_inventory') or []),
        'source_summaries': deepcopy([row for row in plan.get('source_summaries') or []
                                     if any(ref.get('file_id') == source_file_id for ref in row.get('source_references') or [])]),
        'source_project_summaries': deepcopy([row for row in plan.get('source_project_summaries') or []
                                             if any(ref.get('file_id') == source_file_id for ref in row.get('source_references') or [])]),
        'extraction_reports': deepcopy(plan.get('extraction_reports') or []),
        'validation': deepcopy(plan.get('validation') or []),
    }
    warnings = []
    errors = []
    def finding(code, message, count=None, *, blocks=False):
        row = {'code': code, 'message': message, 'severity': 'error' if blocks else 'warning'}
        if count is not None:
            row['count'] = count
        (errors if blocks else warnings).append(row)
    if source.parse_status != 'done':
        finding('source_import_parse_incomplete', 'Wait for this document to finish processing before importing.', blocks=True)
    try:
        hierarchy = _geometry_hierarchy(rows, snapshot['source_summaries'])
        if hierarchy is not None:
            snapshot['source_hierarchy'] = hierarchy
    except ValueError as exc:
        finding('source_import_hierarchy_unsupported', str(exc), blocks=True)
    if not rows:
        finding('source_import_empty', 'This document has no extracted schedule activity rows. Review its extraction first.', blocks=True)
    if not project.effective_date:
        finding('source_import_project_start_missing', 'Set the project start date before importing this source schedule.', blocks=True)
    invalid_duration = sum(not _decimal_fits(row.get('duration_days'), digits=10) for row in rows)
    if invalid_duration:
        finding('source_import_duration_unsupported',
                'Review the missing or unsupported source durations before importing. No default duration will be substituted.',
                invalid_duration, blocks=True)
    invalid_identity = sum(not row.get('name') or len(row['name']) > 500 or len(row['id']) > 64 for row in rows)
    if invalid_identity or len(ids) != len(rows):
        finding('source_import_identity_unsupported', 'Resolve duplicate source rows or activity names that exceed the schedule field limit.',
                invalid_identity or len(rows) - len(ids), blocks=True)
    invalid_links = sum(row.get('type') not in {'FS', 'SS', 'FF', 'SF'}
                        or not _decimal_fits(row.get('lag_days'), digits=8, nonnegative=False) for row in links)
    if invalid_links:
        finding('source_import_relationship_unsupported', 'Review relationship types or lag values that cannot be preserved exactly.', invalid_links, blocks=True)
    dated = [source_date_fields(row) for row in rows]
    starts = [row['source_start_date'] for row in dated if row['source_start_date']]
    finishes = [row['source_finish_date'] for row in dated if row['source_finish_date']]
    undated = sum(row['source_date_status'] != 'extracted' for row in dated)
    if undated:
        finding('source_import_dates_unresolved', 'Some rows have missing or ambiguous date columns. Those dates remain Not Specified.', undated)
    if snapshot['register_inventory']:
        finding('source_import_register_separate', 'The MDR and existing assignments are preserved separately. This import does not assert that schedule rows match MDR deliverables.', len(snapshot['register_inventory']))
    finding('source_import_review_required', 'Source dates and durations will be displayed as extracted. Calendar, activity logic and scope still require review before calculation or baseline approval.')
    if not links:
        finding('source_import_logic_not_specified', 'No explicit predecessor links were recovered from this document. No links will be guessed from row order or dates.')
    if ((starts and project.effective_date and min(starts) < str(project.effective_date))
            or (finishes and project.planned_end_date and max(finishes) > str(project.planned_end_date))):
        finding('source_import_outside_window', 'Some source dates fall outside the registered project window. The source dates and project dates will both be preserved for review.')
    if any(ref.get('file_id') != source_file_id for row in rows for ref in row.get('source_references') or []):
        finding('source_import_multiple_references', 'Some selected rows cite additional project documents. Review those source references too.')
    return snapshot, {
        'source_file': deepcopy(snapshot['source_file']), 'activity_count': len(rows),
        'start_date': min(starts) if starts else None, 'finish_date': max(finishes) if finishes else None,
        'duration_count': len(rows) - invalid_duration, 'relationship_count': len(links),
        'unmapped_register_count': len(plan.get('register_inventory') or []) - sum(
            bool(ids.intersection(row.get('schedule_activity_ids') or [])) for row in plan.get('register_inventory') or []),
        'prior_work_preserved': True,
    }, errors + warnings, not errors


@transaction.atomic
def preview_source_import(project, actor, *, source_file_id, master_revision):
    project = PlanningProject.objects.select_for_update().get(pk=project.pk, is_deleted=False)
    _write(project, actor)
    if project.master_schedule_revision != master_revision:
        _error('The current Master Schedule changed. Refresh before reviewing the import.', 'source_import_stale')
    fingerprint = _state(project)
    snapshot, summary, warnings, can_apply = _payload(project, source_file_id)
    if fingerprint != _state(project):
        _error('Project sources changed during preview. Refresh and review the import again.', 'source_import_stale')
    token = signing.dumps({
        'schema': SCHEMA, 'project_id': project.pk, 'actor_id': actor.pk,
        'source_file_id': source_file_id, 'state_fingerprint': fingerprint,
        'content_fingerprint': _content(project),
        'snapshot_fingerprint': canonical_fingerprint(snapshot), 'nonce': uuid4().hex,
    }, salt=SALT, compress=True) if can_apply else None
    return {'proposal_token': token, 'summary': summary, 'warnings': warnings, 'can_apply': can_apply,
            'project_window': snapshot['project_window']}


def imported_snapshot(version):
    """Recognize a server-signed, unchanged imported source snapshot."""
    stored = version.evidence_input_snapshot or {}
    if stored.get('schema') != SCHEMA:
        return None
    try:
        signed = signing.loads(stored.get('review_token', ''), salt=SALT)
    except (signing.BadSignature, TypeError):
        return None
    snapshot = {key: value for key, value in stored.items() if key not in {'review_token', 'reason'}}
    if (signed.get('project_id') != version.schedule.project_id or signed.get('actor_id') != version.created_by_id
            or signed.get('snapshot_fingerprint') != canonical_fingerprint(snapshot)):
        return None
    actual = list(version.activities.filter(is_deleted=False))
    expected = {row['id']: row for row in snapshot['activities']}
    hierarchy = snapshot.get('source_hierarchy') or {}
    expected_nodes = {row['code']: row for row in hierarchy.get('nodes') or []}
    actual_nodes = list(version.wbs_nodes.filter(is_deleted=False))
    node_keys = {node.pk: expected_nodes[node.code]['key'] for node in actual_nodes if node.code in expected_nodes}
    if len(actual_nodes) != len(expected_nodes):
        return None
    for node in actual_nodes:
        expected_node = expected_nodes.get(node.code)
        if (expected_node is None or node.name != expected_node['name'] or node.level != expected_node['level']
                or node.sort_order != expected_node['sort_order'] or node.discipline
                or (node.parent_id is not None and node.parent_id not in node_keys)
                or node_keys.get(node.parent_id) != expected_node['parent_key']):
            return None
    if (len(actual) != len(expected) or version.calculated_at or version.schedule.default_calendar_id
            or str(version.schedule.planned_start) != snapshot['project_window']['start_date']):
        return None
    for activity in actual:
        row = expected.get(activity.external_id)
        if (row is None or activity.name != row['name'] or activity.duration_days != Decimal(str(row['duration_days']))
                or activity.activity_type != _activity_type(row) or activity.metadata != _metadata(row)
                or activity.planned_start or activity.planned_finish or activity.calendar_id
                or (activity.wbs_node_id is not None and activity.wbs_node_id not in node_keys)
                or node_keys.get(activity.wbs_node_id) != (hierarchy.get('activity_parents') or {}).get(activity.external_id)
                or activity.constraint_type != 'none' or activity.constraint_date):
            return None
    keys = {row.pk: row.external_id for row in actual}
    current_links = {(keys.get(row.predecessor_id), keys.get(row.successor_id), row.relationship_type, row.lag_days,
                      canonical_fingerprint(row.metadata))
                     for row in version.relationships.filter(is_deleted=False)}
    expected_links = {(row['predecessor_id'], row['successor_id'], row['type'], Decimal(str(row['lag_days'])),
                       canonical_fingerprint(_link_metadata(row)))
                      for row in snapshot['relationships']}
    if current_links != expected_links:
        return None
    return snapshot


def enrich_imported_state(version, state):
    """Expose source facts separately, bound to their signed server snapshot."""
    snapshot = imported_snapshot(version)
    if snapshot is None:
        return state
    # A source snapshot can be current while its calendar/network remains
    # unverified. Freshness comes from the signed import inputs, independently
    # of the calculation and approval readiness gates.
    signed = signing.loads(version.evidence_input_snapshot['review_token'], salt=SALT)
    state['stale_inputs'] = signed.get('content_fingerprint') != _content(version.schedule.project)
    expected = {row['id']: row for row in snapshot['activities']}
    state['source_import'] = {
        'source_file': deepcopy(snapshot['source_file']), 'activity_count': len(expected),
        'register_count': len(snapshot['register_inventory']), 'prior_work_preserved': True,
        'project_window': deepcopy(snapshot['project_window']),
        'source_summaries': deepcopy(snapshot['source_summaries']),
        'source_project_summaries': deepcopy(snapshot.get('source_project_summaries') or []),
        'status': 'extracted_requires_review',
    }
    state['read_only_reason'] = 'This source schedule preserves printed values. Review and link source evidence to create a calculated plan.'
    # No default work week is implied by the read-model's legacy fallback.
    state['calendar'].update(name='Not Specified', working_weekdays=[], hours_per_day=None,
                             is_fallback=False, source_verified=False, evidence_status='Not Specified')
    state['work_calendar'] = deepcopy(state['calendar'])
    project_summaries = snapshot.get('source_project_summaries') or []
    if len(project_summaries) == 1:
        evidence = project_summaries[0]
        state['project_summary'].update(_source_summary(evidence))
    hierarchy = snapshot.get('source_hierarchy') or {}
    if hierarchy:
        nodes = {row['code']: row for row in hierarchy['nodes']}
        state['hierarchy_source'] = 'printed_pdf_indentation'
        for node in state['wbs_nodes']:
            printed = nodes.get(node['code'])
            if printed:
                evidence = printed['source_evidence']
                project_evidence = project_summaries[0] if len(project_summaries) == 1 else None
                is_source_project = bool(project_evidence and evidence.get('source_references')
                    and evidence['source_references'] == project_evidence.get('source_references')
                    and evidence.get('values') == project_evidence.get('values'))
                node.update(source_row_number=int(printed['key']), code_source='printed_row_reference',
                            is_source_project=is_source_project,
                            source_evidence=deepcopy(evidence), source_references=deepcopy(evidence['source_references']))
                node['summary'].update(_source_summary(evidence))
    for task in state['tasks']:
        row = expected.get(task['id'])
        if not row:
            continue
        references = deepcopy(row.get('source_references') or [])
        label = {'type': 'document', 'label': 'Document evidence', 'status': 'extracted_requires_review',
                 'source_references': references}
        provenance = task.setdefault('field_provenance', {})
        if task.get('title') == row['name']:
            provenance['title'] = deepcopy(label)
        if Decimal(str(task.get('duration_days'))) == Decimal(str(row['duration_days'])):
            provenance['duration_days'] = deepcopy(label)
        if task.get('source_evidence') == row['source_evidence']:
            for endpoint in ('start', 'finish'):
                if task.get(f'source_{endpoint}_status') == 'extracted':
                    provenance[f'source_{endpoint}_date'] = deepcopy(label)
            if task.get('source_total_float_status') == 'extracted':
                provenance['source_total_float_days'] = deepcopy(label)
            task['is_milestone'] = row.get('is_milestone') is True
            task['source_activity_type'] = (row['source_evidence'] or {}).get('record_type')
        if row.get('source_activity_id') and task.get('source_activity_id') == row['source_activity_id']:
            task['activity_code'] = row['source_activity_id']
            task['activity_code_source'] = 'source_document'
        expected_links = [{
            'task_id': item['id'], 'type': item['type'], 'lag_days': item['lag_days'],
            'lag_unit': item.get('lag_unit'), 'source_references': deepcopy(item.get('source_references') or []),
            'source_excerpt': item.get('source_excerpt'), 'source': 'source_document',
        } for item in row.get('predecessors') or [] if item['id'] in expected]
        actual = {(item['task_id'], item['type'], Decimal(str(item['lag_days']))) for item in task['dependency_details']}
        projected = {(item['task_id'], item['type'], Decimal(str(item['lag_days']))) for item in expected_links}
        if actual == projected:
            task['dependency_details'] = expected_links
            if expected_links or row.get('dependency_status') == 'explicit_none':
                provenance['depends_on'] = deepcopy(label)
    counts = {}
    for task in state['tasks']:
        for item in task.get('field_provenance', {}).values():
            counts[item['type']] = counts.get(item['type'], 0) + 1
    state['provenance_summary'] = counts
    return state


@transaction.atomic
def apply_source_import(project, actor, *, proposal_token, reason, acknowledge_scope):
    project = PlanningProject.objects.select_for_update().get(pk=project.pk, is_deleted=False)
    _write(project, actor)
    if not acknowledge_scope or not reason.strip():
        _error('Confirm the selected source applies to this project and record the reason for importing it.', 'source_import_scope_required')
    try:
        signed = signing.loads(proposal_token, salt=SALT, max_age=MAX_AGE)
    except signing.BadSignature:
        _error('The import preview expired or is invalid. Review the source import again.', 'source_import_token_invalid')
    if signed.get('schema') != SCHEMA or signed.get('project_id') != project.pk or signed.get('actor_id') != actor.pk:
        _error('This import preview belongs to another project or user.', 'source_import_token_invalid')
    existing = ScheduleVersion.objects.filter(schedule__project=project,
        evidence_input_snapshot__review_token=proposal_token).select_related('schedule').first()
    if existing:
        if (existing.is_deleted or existing.schedule.is_deleted or project.master_schedule_version_id != existing.pk
                or imported_snapshot(existing) is None or signed.get('content_fingerprint') != _content(project)):
            _error('This preview was already applied and the active schedule has changed. Review a new import.', 'source_import_stale')
        return {'schedule_version_id': existing.pk, 'created': False, 'notice': 'This source schedule is already the current Master Schedule.'}
    # Lock source rows so an in-flight extraction cannot alter this snapshot.
    list(project.files.select_for_update().filter(is_deleted=False).values_list('pk', flat=True))
    if project.master_schedule_version_id:
        ScheduleVersion.objects.select_for_update().get(pk=project.master_schedule_version_id)
    if signed['state_fingerprint'] != _state(project):
        _error('The documents, working draft or Master Schedule changed after preview. Review a new import.', 'source_import_stale')
    snapshot, summary, warnings, can_apply = _payload(project, signed['source_file_id'])
    if not can_apply or signed['snapshot_fingerprint'] != canonical_fingerprint(snapshot):
        _error('Source extraction changed or needs correction. Review the source import again.', 'source_import_stale')
    schedule = Schedule.objects.create(project=project, code='SOURCE-' + signed['nonce'],
        name=('Source schedule · ' + snapshot['source_file']['name'])[:255], planned_start=project.effective_date,
        default_calendar=None, created_by=actor)
    version = ScheduleVersion.objects.create(schedule=schedule, version=1, status='draft', created_by=actor,
        change_summary='Reviewed source schedule import; calculation and evidence acceptance pending.',
        evidence_input_snapshot={**snapshot, 'review_token': proposal_token, 'reason': reason.strip()})
    activities = {}
    hierarchy = snapshot.get('source_hierarchy') or {}
    nodes = {}
    for row in hierarchy.get('nodes') or []:
        nodes[row['key']] = ScheduleWBSNode.objects.create(version=version, parent=nodes.get(row['parent_key']),
            code=row['code'], name=row['name'], level=row['level'], sort_order=row['sort_order'])
    for index, row in enumerate(snapshot['activities']):
        activities[row['id']] = ScheduleActivity(version=version, external_id=row['id'], name=row['name'],
            wbs_node=nodes.get((hierarchy.get('activity_parents') or {}).get(row['id'])),
            duration_days=Decimal(str(row['duration_days'])), activity_type=_activity_type(row), sort_order=index,
            metadata=_metadata(row))
    ScheduleActivity.objects.bulk_create(list(activities.values()), batch_size=500)
    ActivityRelationship.objects.bulk_create([ActivityRelationship(version=version,
        predecessor=activities[row['predecessor_id']], successor=activities[row['successor_id']],
        relationship_type=row['type'], lag_days=Decimal(str(row['lag_days'])),
        metadata=_link_metadata(row))
        for row in snapshot['relationships']], batch_size=500)
    previous = project.master_schedule_version_id
    project.master_schedule_version = version
    project.master_schedule_revision += 1
    project.save(update_fields=['master_schedule_version', 'master_schedule_revision'])
    record_event(project=project, actor=actor, action='source_schedule.imported', entity=version,
        before={'master_version_id': previous}, after={'master_version_id': version.pk, 'summary': summary,
            'snapshot_fingerprint': signed['snapshot_fingerprint'], 'reason': reason.strip()},
        metadata={'warnings': warnings, 'working_draft_preserved': True})
    return {'schedule_version_id': version.pk, 'created': True,
            'notice': 'Source activities and dates are now in Master Schedule. The MDR and existing assignments are preserved; calculation and baseline approval still require evidence review.'}
