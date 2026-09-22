"""Pure, deterministic review findings for supplied schedule snapshots.

Parallel workflows can be entirely appropriate. These findings request a
planner's review; they do not establish missing logic, estimate capacity, or
change the network. Dates are reported as supplied, never recalculated here.
"""
from collections import defaultdict, deque
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json


SCHEMA = 'schedule-logic-quality/1'
PARALLEL_REVIEW_THRESHOLD = 5
_STAGES = {'IFR': 1, 'COMPANY_REVIEW': 2, 'IFA': 3, 'COMPANY_APPROVAL': 4, 'FINAL_ISSUE': 5}


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str)


def _hash(value):
    return sha256(_json(value).encode('utf-8')).hexdigest()


def _id(value):
    return '' if value is None else str(value).strip()


def _field(row, key, default=None):
    return row[key] if key in row else (row.get('metadata') or {}).get(key, default)


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            return None
        return int(number) if number == number.to_integral_value() else float(number)
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        return None


def _date(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError):
        return None


def _timing(row):
    planned = [_date(_field(row, key)) for key in ('planned_start_date', 'planned_finish_date')]
    source = [_date(_field(row, key)) for key in ('source_start_date', 'source_finish_date')]
    evidence = _field(row, 'source_evidence', {}) or {}
    values = evidence.get('values') or {}
    source = [value or _date(values.get(key)) for value, key in
              zip(source, ('planned_start_date', 'planned_finish_date'))]
    review = _field(row, 'source_evidence_review', {}) or {}
    stale = bool(_field(row, 'calculation_stale', False) or _field(row, 'dates_stale', False)
                 or _field(row, 'calculation_status') == 'stale')
    calculated = _field(row, 'calculated')
    authority = _field(row, 'date_authority')
    if review.get('status') == 'requires_review':
        effective, basis = [None, None], 'unknown'
    elif authority == 'source_document' and calculated is not True:
        effective, basis = source, 'source' if any(source) else 'unknown'
    elif any(planned) and not stale and calculated is not False:
        effective, basis = planned, 'calculated' if calculated is True else 'planned'
    elif any(source):
        effective, basis = source, 'source'
    else:
        effective, basis = [None, None], 'unknown'
    if all(effective) and effective[1] < effective[0]:
        effective, basis = [None, None], 'unknown'
    return {'start_date': effective[0], 'finish_date': effective[1], 'date_basis': basis,
            'planned_dates': planned, 'source_dates': source, 'stale': stale,
            'calculated': calculated, 'date_authority': authority, 'evidence_review': review.get('status')}


def _links(row):
    links, detailed = {}, set()
    for detail in _field(row, 'dependency_details', []) or []:
        predecessor = _id(detail.get('task_id', detail.get('predecessor_id')))
        if not predecessor:
            continue
        detailed.add(predecessor)
        metadata = detail.get('metadata') or {}
        source = (detail.get('source') or metadata.get('source')) == 'source_document'
        kind = detail.get('type') or detail.get('relationship_type')
        link = {'task_id': predecessor, 'type': str(kind).upper() if kind else None,
                'lag_days': _number(detail.get('lag_days', detail.get('lag'))),
                'lag_unit': detail.get('lag_unit') or metadata.get('lag_unit') or (None if source else 'working_days')}
        links[_json(link)] = link
    for value in _field(row, 'depends_on', []) or []:
        predecessor = _id(value)
        if predecessor and predecessor not in detailed:
            source_unknown = (_field(row, 'evidence_policy') == 'document_driven'
                              and _field(row, 'dependency_status') in {None, 'not_specified', 'requires_review'})
            link = {'task_id': predecessor, 'type': None if source_unknown else 'FS',
                    'lag_days': None if source_unknown else 0, 'lag_unit': None if source_unknown else 'working_days'}
            links[_json(link)] = link
    return [links[key] for key in sorted(links)]


def _stage_key(task):
    sequence = task['sequence']
    return (sequence if sequence is not None else _STAGES.get(task['stage_code'], 1000000),
            task['stage_code'], task['id'])


def _normalise(tasks, deliverables):
    parents, memberships = {}, defaultdict(set)
    for key, row in (deliverables.items() if isinstance(deliverables, dict) else
                     ((row.get('id'), row) for row in (deliverables or []))):
        if isinstance(row, dict):
            parent_id = _id(row.get('id', key))
            if parent_id:
                parents[parent_id] = row
                for task_id in row.get('workflow_task_ids') or []:
                    memberships[_id(task_id)].add(parent_id)
    normalised = []
    for row in tasks or []:
        task_id = _id(row.get('id', row.get('external_id')))
        if not task_id:
            continue
        parent_id = _id(_field(row, 'parent_deliverable_id'))
        candidates = memberships[task_id]
        ambiguous = len(candidates) > 1 or bool(parent_id and candidates and parent_id not in candidates)
        if not parent_id and len(candidates) == 1:
            parent_id = next(iter(candidates))
        parent = parents.get(parent_id) or {}
        source_parent = _field(row, 'source_deliverable', {}) or {}
        code = str(_field(row, 'workflow_stage_code') or _field(row, 'workflow_stage') or '').upper()
        duration = _number(_field(row, 'duration_days'))
        source_duration = (_field(row, 'duration_source') in {'source_document', 'missing_source', 'source_requirement'}
                           or _field(row, 'evidence_policy') == 'document_driven')
        task = {
            'id': task_id, 'title': str(row.get('title') or row.get('name') or task_id),
            'parent_id': parent_id, 'membership_ambiguous': ambiguous, 'stage_code': code,
            'sequence': _number(_field(row, 'workflow_stage_sequence')),
            'deliverable_title': str(parent.get('title') or parent.get('name') or _field(row, 'deliverable')
                                     or source_parent.get('title') or parent_id),
            'discipline': str(_field(row, 'discipline') or parent.get('discipline') or source_parent.get('discipline') or 'general').strip().casefold(),
            'duration_days': duration, 'duration_unit': _field(row, 'duration_unit') or (None if source_duration else 'working_days'),
            'duration_source': _field(row, 'duration_source'),
            'timing': _timing(row), 'links': _links(row),
            'constraint_type': _field(row, 'constraint_type'), 'constraint_date': _date(_field(row, 'constraint_date')),
            'calendar_id': _id(_field(row, 'calendar_id')),
            'duration_calendar_verified': _field(row, 'duration_calendar_verified'),
        }
        normalised.append(task)
    return sorted(normalised, key=lambda task: (task['id'], _json(task)))


def _blocked_by_cycle(by_id):
    """Kahn's remainder includes cycles and their dependent activities."""
    incoming, outgoing = {}, defaultdict(set)
    for key, task in by_id.items():
        predecessors = {link['task_id'] for link in task['links'] if link['task_id'] in by_id}
        incoming[key] = len(predecessors)
        for predecessor in predecessors:
            outgoing[predecessor].add(key)
    ready = deque(key for key, count in incoming.items() if not count)
    visited = set()
    while ready:
        key = ready.popleft()
        visited.add(key)
        for successor in outgoing[key]:
            incoming[successor] -= 1
            if not incoming[successor]:
                ready.append(successor)
    return set(by_id) - visited


def _workflow(parent_id, tasks, by_id, outgoing, blocked):
    ordered = sorted(tasks, key=_stage_key)
    positions = {task['id']: index for index, task in enumerate(ordered)}
    internal, external = [], []
    invalid = any(task['membership_ambiguous'] for task in ordered)
    for task in ordered:
        for link in task['links']:
            predecessor = link['task_id']
            valid = (predecessor in by_id and link['type'] in {'FS', 'SS', 'FF', 'SF'}
                     and link['lag_days'] is not None)
            invalid = invalid or not valid
            if predecessor in positions:
                internal.append({'from': positions[predecessor], 'to': positions[task['id']],
                                 **{key: link[key] for key in ('type', 'lag_days', 'lag_unit')}})
            elif valid and by_id[predecessor]['parent_id'] != parent_id:
                external.append({**link, 'successor_stage_code': task['stage_code'],
                                 'successor_stage_index': positions[task['id']]})
            else:
                invalid = True
    first_ids = [task['id'] for task in ordered if not any(link['task_id'] in positions for link in task['links'])]
    terminal_ids = [task['id'] for task in ordered if not outgoing[task['id']]]
    # Missing stage dates must not silently become a complete workflow window.
    starts = [task['timing']['start_date'] for task in ordered]
    finishes = [task['timing']['finish_date'] for task in ordered]
    bases = {task['timing']['date_basis'] for task in ordered}
    window = {'start_date': min(starts) if all(starts) else None,
              'finish_date': max(finishes) if all(finishes) else None,
              'date_basis': next(iter(bases)) if len(bases) == 1 else 'mixed'}
    durations = [{'stage_code': task['stage_code'], 'duration_days': task['duration_days'],
                  'duration_unit': task['duration_unit']} for task in ordered]
    signature = {'discipline': ordered[0]['discipline'], 'stage_durations': durations,
                 'internal_links': sorted(internal, key=_json), 'common_predecessors': sorted(external, key=_json),
                 'window': window, 'calendars': [task['calendar_id'] for task in ordered]}
    eligible = (bool(external) and bool(first_ids) and not invalid
                and not any(task['id'] in blocked for task in ordered)
                and len({task['discipline'] for task in ordered}) == 1
                and all(task['duration_days'] is not None and task['duration_days'] >= 0
                        and task['duration_unit'] is not None for task in ordered))
    return {'id': parent_id, 'title': ordered[0]['deliverable_title'], 'discipline': ordered[0]['discipline'],
            'task_ids': [task['id'] for task in ordered], 'first_task_ids': first_ids,
            'unconnected_start_ids': [key for key in first_ids if not any(link['task_id'] in by_id for link in by_id[key]['links'])],
            'terminal_task_ids': terminal_ids, 'signature': signature, 'eligible': eligible}


def _group(kind, workflows, by_id, signature=None):
    workflows = sorted(workflows, key=lambda row: row['id'])
    requires_review = kind == 'parallel_workflow'
    common = []
    for link in (signature or {}).get('common_predecessors', []):
        predecessor = by_id[link['task_id']]
        common.append({**link, 'title': predecessor['title'], 'deliverable_id': predecessor['parent_id'] or None})
    messages = {
        'parallel_workflow': 'These deliverables share incoming gates, workflow durations and the available date window. Confirm that parallel execution is intended and feasible.',
        'unconnected_workflow_start': 'These workflow starts have no incoming link to an activity in this schedule. Confirm that they are intentionally independent or identify their actual release gates.',
        'terminal_branch': 'These workflow branches have no outgoing activity link. Confirm their intended completion or handover; a terminal branch can be legitimate.',
    }
    identity = {'schema': SCHEMA, 'kind': kind, 'deliverable_ids': [row['id'] for row in workflows],
                'task_ids': sorted(key for row in workflows for key in row['task_ids'])}
    terminal_ids = sorted(key for row in workflows for key in row['terminal_task_ids'])
    return {'id': _hash(identity), 'kind': kind,
            'status': 'requires_review' if requires_review else 'warning', 'requires_review': requires_review,
            'discipline': workflows[0]['discipline'], 'deliverable_count': len(workflows),
            'deliverable_ids': identity['deliverable_ids'], 'deliverable_titles': [row['title'] for row in workflows],
            'task_ids': identity['task_ids'],
            'first_task_ids': sorted(key for row in workflows for key in row['first_task_ids']),
            'unconnected_start_ids': sorted(key for row in workflows for key in row['unconnected_start_ids']),
            'terminal_task_ids': terminal_ids, 'terminal_task_count': len(terminal_ids),
            'terminal_deliverable_count': sum(bool(row['terminal_task_ids']) for row in workflows),
            'common_predecessors': common, 'stage_durations': (signature or {}).get('stage_durations', []),
            **(signature or {}).get('window', {'start_date': None, 'finish_date': None, 'date_basis': 'unknown'}),
            'message': messages[kind]}


def analyze_schedule_logic(tasks, deliverables=None):
    """Return review groups, counts and a SHA256 bound to the actual inputs.

    IDs are canonical strings. Input order, duplicate identical link listings,
    and CPM float/critical flags do not affect the fingerprint. Task identity,
    durations, supplied timing, constraints, calendars and typed links do.
    Explicitly stale or uncalculated dates never become calculated evidence.
    Unknown durations cannot establish an equal-duration parallel cluster.
    This complements, and does not replace, existing network validation.
    """
    normalised = _normalise(tasks, deliverables)
    by_id = {row['id']: row for row in normalised}
    counts = defaultdict(int)
    for row in normalised:
        counts[row['id']] += 1
    duplicate_ids = {key for key, count in counts.items() if count > 1}
    outgoing, grouped = defaultdict(set), defaultdict(list)
    cross_links = 0
    for row in by_id.values():
        if row['parent_id'] and row['stage_code']:
            grouped[row['parent_id']].append(row)
        for link in row['links']:
            predecessor = by_id.get(link['task_id'])
            if predecessor:
                outgoing[predecessor['id']].add(row['id'])
                cross_links += bool(row['parent_id'] and predecessor['parent_id'] != row['parent_id'])
    cyclic_or_blocked = _blocked_by_cycle(by_id)
    blocked = cyclic_or_blocked | duplicate_ids
    workflows = [_workflow(key, rows, by_id, outgoing, blocked) for key, rows in sorted(grouped.items())]
    clusters, open_starts, terminals = defaultdict(list), defaultdict(list), defaultdict(list)
    for workflow in workflows:
        if workflow['eligible']:
            clusters[_json(workflow['signature'])].append(workflow)
        if workflow['unconnected_start_ids']:
            open_starts[workflow['discipline']].append(workflow)
        if workflow['terminal_task_ids']:
            terminals[workflow['discipline']].append(workflow)
    groups = [_group('parallel_workflow', rows, by_id, rows[0]['signature'])
              for rows in clusters.values() if len(rows) >= PARALLEL_REVIEW_THRESHOLD]
    groups += [_group('unconnected_workflow_start', rows, by_id) for rows in open_starts.values()]
    groups += [_group('terminal_branch', rows, by_id) for rows in terminals.values()]
    groups.sort(key=lambda row: (not row['requires_review'], row['kind'], row['discipline'], row['id']))
    parallel = [row for row in groups if row['requires_review']]
    summary = {
        'task_count': len(normalised), 'workflow_deliverable_count': len(workflows),
        'relationship_count': sum(len(row['links']) for row in by_id.values()),
        'cross_deliverable_link_count': cross_links,
        'parallel_group_count': len(parallel),
        'parallel_deliverable_count': sum(row['deliverable_count'] for row in parallel),
        'requires_review_count': len(parallel), 'warning_count': len(groups) - len(parallel),
        'unconnected_workflow_start_count': sum(len(row['unconnected_start_ids']) for row in workflows),
        'terminal_branch_count': sum(bool(row['terminal_task_ids']) for row in workflows),
        'cycle_or_blocked_task_count': len(cyclic_or_blocked),
        'duplicate_task_id_count': len(duplicate_ids),
    }
    fingerprint = _hash({'schema': SCHEMA, 'threshold': PARALLEL_REVIEW_THRESHOLD,
                         'tasks': normalised, 'group_ids': [row['id'] for row in groups]})
    return {'groups': groups, 'summary': summary, 'fingerprint': fingerprint}
