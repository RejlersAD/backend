"""Planner date anchors, kept distinct from immutable document observations."""
from copy import deepcopy
from collections import deque
from datetime import date
from math import isfinite

from django.utils import timezone


def apply_timing_edit(task, original, actor, *, origin=None):
    """Select one exact endpoint; duration determines the opposite endpoint.

    Displayed source/calculated dates are never interpreted as edits. The
    explicit command prevents a whole-table save from pinning every activity.
    """
    previous = original.get('planner_timing')
    if previous:
        task['planner_timing'] = deepcopy(previous)
    else:
        task.pop('planner_timing', None)
    edit = task.pop('timing_edit', None)
    if edit is None:
        # An explicit constraint edit in Activity details replaces this anchor.
        if previous and (task.get('constraint_type'), task.get('constraint_date')) != (
                f"must_{previous['anchor']}", previous.get('date')):
            task.pop('planner_timing', None)
        return
    endpoint, value = edit['field'], edit['value']
    if endpoint not in {'start', 'finish'}:
        raise ValueError('Choose Start or Finish as the date anchor.')
    if value:
        parsed = date.fromisoformat(str(value))
        value = parsed.isoformat()
        if origin and abs((parsed - origin).days) > 36525:
            raise ValueError('Choose a date within 100 years of the project start.')
        task['planner_timing'] = {
            'anchor': endpoint, 'date': value, 'edited_by': actor.pk,
            'edited_at': timezone.now().isoformat(), 'basis': 'planner_input',
        }
        task['constraint_type'], task['constraint_date'] = f'must_{endpoint}', value
        task['date_authority'] = 'planner_override'
        task[f'planned_{endpoint}_date_source'] = 'planner'
        # These are inputs, never a pair of independently asserted dates.
        task['planned_start_date'] = value if endpoint == 'start' else None
        task['planned_finish_date'] = value if endpoint == 'finish' else None
    elif previous and previous.get('anchor') == endpoint:
        task.pop('planner_timing', None)
        task['constraint_type'], task['constraint_date'] = 'none', None
        task['planned_start_date'] = task['planned_finish_date'] = None
        task.pop('date_authority', None)
        task.pop(f'planned_{endpoint}_date_source', None)


def expose_planner_timing(tasks):
    """Show an entered anchor even while CPM inputs are incomplete.

    Neither a date nor a duration edit verifies the source calendar. A pending
    activity can show its planner endpoint but must not acquire calculated
    float, a derived other endpoint, or a calculated badge.
    """
    for task in tasks:
        anchor = task.get('planner_timing') or {}
        endpoint, value = anchor.get('anchor'), anchor.get('date')
        task['planner_start_date'] = value if endpoint == 'start' else None
        task['planner_finish_date'] = value if endpoint == 'finish' else None
        if endpoint not in {'start', 'finish'} or not value or task.get('calculated'):
            continue
        task[f'planned_{endpoint}_date'] = value
        if task.get('is_milestone') or task.get('activity_type') in {'start_milestone', 'finish_milestone'}:
            task['planned_start_date'] = task['planned_finish_date'] = value
        task['date_authority'] = 'planner_override'
    return tasks


def validate_planner_network(tasks):
    """Validate even when missing source calendar/durations prevent a CPM pass."""
    by_id = {task['id']: task for task in tasks}
    if len(by_id) != len(tasks):
        raise ValueError('Each activity must have a unique ID.')
    incoming, outgoing = {}, {key: [] for key in by_id}
    for task in tasks:
        key, predecessors = task['id'], task.get('depends_on') or []
        if len(set(predecessors)) != len(predecessors):
            raise ValueError('An activity cannot repeat a predecessor.')
        if key in predecessors or set(predecessors) - set(by_id):
            raise ValueError('Predecessors must refer to other activities in this plan.')
        details = task.get('dependency_details')
        if details is not None:
            identities = set()
            for link in details:
                identity = (link['task_id'], link.get('type', 'FS'))
                if identity in identities or identity[1] not in {'FS', 'SS', 'FF', 'SF'}:
                    raise ValueError('Select a supported, nonduplicate relationship type for each predecessor.')
                lag = link.get('lag_days', 0)
                if lag is None or not isfinite(float(lag)) or abs(float(lag)) > 365:
                    raise ValueError('Relationship lag must be between -365 and 365 working days.')
                identities.add(identity)
            if {link['task_id'] for link in details} != set(predecessors):
                raise ValueError('Choose relationship details for every selected predecessor.')
        incoming[key] = len(predecessors)
        for predecessor in predecessors:
            outgoing[predecessor].append(key)
    pending = deque(key for key, count in incoming.items() if not count)
    visited = 0
    while pending:
        key = pending.popleft()
        visited += 1
        for successor in outgoing[key]:
            incoming[successor] -= 1
            if not incoming[successor]:
                pending.append(successor)
    if visited != len(tasks):
        raise ValueError('Dependencies must not form a cycle.')
