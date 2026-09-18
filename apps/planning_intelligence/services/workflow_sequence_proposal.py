"""Propose deliverable windows and gates around preserved workflow activities.

The function operates on supplied snapshots only. It does not query, save, import
source schedule dates, or assign employees. Existing executable stage rows remain
the authority for durations, manual constraints, links and employee work.
"""
from copy import deepcopy
from datetime import date
import hashlib
import json

from .simple_workflow_expansion import WorkflowExpansionError, _validate_network


_NOTE = ' Deliverable sequencing: '


def _parent_proposal(parents, context, calendar):
    # Keep this module importable without Django initialization. The existing
    # proposal function itself operates only on these in-memory arguments.
    from .simple_schedule_proposal import build_proposed_tasks
    return build_proposed_tasks(parents, context, calendar)


def _has_actual_work(task):
    try:
        progressed = float(task.get('progress_percent') or 0) > 0
    except (TypeError, ValueError):
        progressed = False
    return bool(progressed or task.get('status') in {'in_progress', 'completed', 'done', 'in_review', 'under_review'}
                or task.get('actual_start') or task.get('actual_start_date')
                or task.get('actual_finish') or task.get('actual_finish_date'))


def _chain_span(chain, dated, calendar):
    starts = [dated[task['id']].get('planned_start_date') for task in chain]
    finishes = [dated[task['id']].get('planned_finish_date') for task in chain]
    if not all(starts) or not all(finishes):
        raise ValueError('Calculate the current workflow dates before proposing deliverable windows.')
    start, finish = date.fromisoformat(min(starts)), date.fromisoformat(max(finishes))
    if start == finish and all(task.get('is_milestone') for task in chain):
        return 0
    return max(1, calendar.index_of(finish) - calendar.index_of(start) + 1)


def _window_signature(chain, context):
    """Keep an accepted window basis stable while its timing inputs are equal."""
    values = []
    for task in chain:
        generated_links = {link['task_id'] for link in task.get('dependency_details') or []
                           if link.get('source') == 'deliverable_sequence'}
        details = [{key: link.get(key) for key in ('task_id', 'type', 'lag_days')}
                   for link in task.get('dependency_details') or []
                   if link.get('source') != 'deliverable_sequence']
        values.append({
            'id': task['id'], 'duration_days': task['duration_days'],
            'activity_type': task.get('activity_type'),
            'manual_start': (None if 'planned_start_date' in (task.get('schedule_generated_fields') or [])
                             else task.get('planned_start_date')),
            'depends_on': sorted(set(task.get('depends_on') or []) - generated_links),
            'dependency_details': details,
        })
    payload = {'stages': values, 'start': context.get('start_date'),
               'finish': context.get('finish_date'), 'calendar': context.get('calendar')}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _link_counts(tasks):
    by_id = {task['id']: task for task in tasks}
    internal = cross = 0
    for task in tasks:
        for predecessor in set(task.get('depends_on') or []):
            if by_id[predecessor]['parent_deliverable_id'] == task['parent_deliverable_id']:
                internal += 1
            else:
                cross += 1
    return internal, cross


def _references_for_gate(rationale, predecessor, successor, constraints):
    references = deepcopy(rationale.get('source_references') or [])
    if predecessor.get('schedule_phase') == 'survey' and successor.get('schedule_phase') == 'basis':
        for constraint in constraints:
            if constraint.get('kind') == 'sequence' and constraint.get('value') == 'survey_before_design':
                for reference in constraint.get('source_references') or []:
                    if reference not in references:
                        references.append(deepcopy(reference))
    return references


def sequence_workflow_deliverables(parents, tasks, dated_tasks, context, calendar):
    """Return reviewed snapshots plus proposed windows/gates, without mutation.

    Parent proxies provide classification and phase windows only. Their graph is
    deliberately separate from the real stage DAG: valid interleaved stage links
    can look cyclic when collapsed into a graph of whole deliverables.
    """
    parents, tasks = deepcopy(parents), deepcopy(tasks)
    _validate_network(tasks)
    by_id = {task['id']: task for task in tasks}
    dated = {task['id']: task for task in dated_tasks}
    chains, proxies, active, manual_starts, externally_linked = {}, [], set(), set(), set()
    for task in tasks:
        for predecessor in task.get('depends_on') or []:
            if by_id[predecessor].get('parent_deliverable_id') != task.get('parent_deliverable_id'):
                externally_linked.add(task['parent_deliverable_id'])
    for parent in parents:
        chain = [by_id[key] for key in parent.get('workflow_task_ids') or []]
        if len(chain) != 5 or any(task.get('parent_deliverable_id') != parent['id'] for task in chain):
            raise ValueError('Each deliverable must retain its five ordered workflow activities.')
        chains[parent['id']] = chain
        first = chain[0]
        generated = set(first.get('schedule_generated_fields') or [])
        if first.get('planned_start_date') and 'planned_start_date' not in generated:
            manual_starts.add(parent['id'])
        if any(_has_actual_work(task) for task in chain):
            active.add(parent['id'])
    protected = set(active)
    pending = list(active)
    while pending:
        parent_id = pending.pop()
        for task in chains[parent_id]:
            for predecessor in task.get('depends_on') or []:
                upstream = by_id[predecessor]['parent_deliverable_id']
                if upstream not in protected:
                    protected.add(upstream)
                    pending.append(upstream)
    for parent in parents:
        chain, first = chains[parent['id']], chains[parent['id']][0]
        generated = set(first.get('schedule_generated_fields') or [])
        signature = _window_signature(chain, context)
        previous_basis = parent.get('sequence_window_basis') or {}
        span = (previous_basis['duration_days'] if previous_basis.get('signature') == signature
                else _chain_span(chain, dated, calendar))
        parent['sequence_window_basis'] = {'signature': signature, 'duration_days': span}
        proxy = deepcopy(parent)
        proxy.update(
            duration_days=span, duration_source='planner',
            planned_start_date=first.get('planned_start_date'),
            depends_on=[], dependency_details=[], dependency_rationales={},
            schedule_generated_fields=['planned_start_date'] if 'planned_start_date' in generated else [],
        )
        if parent['id'] in protected:
            # A missing stored lower bound can still represent work already in
            # progress. Keep its current date rather than proposing a new one.
            proxy['planned_start_date'] = dated[first['id']]['planned_start_date']
            proxy['schedule_generated_fields'] = []
        proxies.append(proxy)

    proposed, constraints, assumptions, warnings = _parent_proposal(proxies, context, calendar)
    proposed_by_id = {parent['id']: parent for parent in proposed}
    assumptions = [message for message in assumptions if 'aggregated at the existing deliverable level' not in message]
    assumptions.append('Deliverable windows use the current five-stage calendar span; individual stage durations, manual dates and employee assignments are retained.')
    changed_windows = added_links = rejected_cycles = 0
    for parent in parents:
        proposal = proposed_by_id[parent['id']]
        chain, first = chains[parent['id']], chains[parent['id']][0]
        parent['schedule_phase'] = proposal['schedule_phase']
        rationale = proposal['schedule_rationale'].replace(
            'Existing planner duration retained.', 'Current five-stage calendar span retained; stage durations are unchanged.',
        )
        if parent['id'] not in protected and parent['id'] not in manual_starts:
            proposed_start = proposal.get('planned_start_date')
            if proposed_start:
                changed_windows += int(first.get('planned_start_date') != proposed_start)
                first['planned_start_date'] = proposed_start
                first['schedule_generated_fields'] = sorted(set(first.get('schedule_generated_fields') or []) | {'planned_start_date'})
                parent['planned_start_date'] = proposed_start
                parent['schedule_generated_fields'] = sorted(set(parent.get('schedule_generated_fields') or []) | {'planned_start_date'})
        parent['schedule_rationale'] = rationale
        for task in chain:
            task['schedule_phase'] = proposal['schedule_phase']
            original_note = (task.get('schedule_rationale') or '').split(_NOTE, 1)[0]
            task['schedule_rationale'] = original_note + _NOTE + rationale

    for parent in parents:
        first = chains[parent['id']][0]
        if (parent['id'] in protected or parent['id'] in externally_linked
                or 'depends_on' not in (first.get('schedule_generated_fields') or [])):
            continue
        proposal = proposed_by_id[parent['id']]
        for predecessor_id in proposal.get('depends_on') or []:
            predecessor = chains[predecessor_id][-1]
            if predecessor['id'] in (first.get('depends_on') or []):
                continue
            rationale = deepcopy((proposal.get('dependency_rationales') or {}).get(predecessor_id) or {})
            references = _references_for_gate(rationale, proposed_by_id[predecessor_id], proposal, constraints)
            metadata = {
                'source': 'deliverable_sequence', 'status': 'proposed', 'evidence_type': 'planning_inference',
                'rationale': rationale.get('rationale') or 'Proposed deliverable gate; confirm applicability before approval.',
                'source_references': references, 'parent_predecessor_id': predecessor_id,
                'parent_successor_id': parent['id'],
            }
            before = {key: deepcopy(first.get(key)) for key in ('depends_on', 'dependency_details', 'dependency_rationales')}
            first.setdefault('depends_on', []).append(predecessor['id'])
            first.setdefault('dependency_details', []).append({
                'task_id': predecessor['id'], 'type': 'FS', 'lag_days': 0, **deepcopy(metadata),
            })
            first.setdefault('dependency_rationales', {})[predecessor['id']] = {
                'relationship_type': 'FS', 'lag_days': 0, **deepcopy(metadata),
            }
            try:
                _validate_network(tasks)
            except WorkflowExpansionError as error:
                first.update(before)
                if error.code != 'workflow_dependency_cycle':
                    raise
                rejected_cycles += 1
                warnings.append(f"A proposed gate from {proposed_by_id[predecessor_id]['title']} to {parent['title']} was not added because it conflicts with the existing activity sequence.")
                continue
            added_links += 1
            first['schedule_generated_fields'] = sorted(set(first.get('schedule_generated_fields') or []) | {'depends_on'})

    _validate_network(tasks)
    internal, cross = _link_counts(tasks)
    window_count = sum(bool(chain[0].get('planned_start_date')) and
                       'planned_start_date' in (chain[0].get('schedule_generated_fields') or [])
                       for chain in chains.values())
    if active:
        warnings.append(f'{len(active)} deliverable(s) already have work in progress or completed stages. Their current start constraints and incoming gates were retained.')
    if protected - active:
        warnings.append(f'{len(protected - active)} upstream deliverable(s) also retain their current windows and gates to avoid moving work already in progress or completed.')
    if len(parents) > 1 and cross == 0:
        warnings.append('Only internal workflow links exist. Deliverables may run in parallel within their proposed windows; confirm the technical gates and resource capacity before approval.')
    summary = {
        'deliverable_count': len(parents), 'internal_relationship_count': internal,
        'cross_deliverable_relationship_count': cross, 'window_count': window_count,
        'added_relationship_count': added_links, 'changed_window_count': changed_windows,
        'retained_manual_start_count': len(manual_starts),
        'retained_active_deliverable_count': len(active), 'rejected_cycle_count': rejected_cycles,
        'protected_upstream_deliverable_count': len(protected - active),
    }
    return parents, tasks, constraints, assumptions, warnings, summary
