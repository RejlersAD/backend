"""Fit editable planning estimates inside the registered dates, without extending them.

Source/manual values and work already under way remain authoritative. Impossible
networks retain honest forecast dates and a warning, never clipped bars or invented
calendar exceptions. This pure operation is used only in the reviewed proposal.
"""
from collections import deque
from copy import deepcopy
from datetime import date
from math import ceil

from .cpm import _edge_weight, calculate_backward_pass


def _active(task):
    return (task.get('status') in {'in_progress', 'completed', 'done', 'in_review', 'under_review'}
            or float(task.get('progress_percent') or 0) > 0
            or any(task.get(key) for key in ('actual_start', 'actual_start_date', 'actual_finish', 'actual_finish_date')))


def protected_work_ids(tasks):
    """Protect work already under way together with its known upstream chain."""
    by_id = {task['id']: task for task in tasks}
    protected = {key for key, task in by_id.items() if _active(task)}
    pending = list(protected)
    while pending:
        for predecessor in by_id[pending.pop()].get('depends_on') or []:
            if predecessor in by_id and predecessor not in protected:
                protected.add(predecessor)
                pending.append(predecessor)
    return protected


def fit_proposed_schedule(tasks, context, calendar, *, review_requirement=None):
    """Keep relationships intact; move generated windows, then resize estimates if needed."""
    tasks = deepcopy(tasks)
    start_date, finish_date = context['start_date'], context['finish_date']
    if isinstance(start_date, str):
        start_date = date.fromisoformat(start_date)
    if isinstance(finish_date, str):
        finish_date = date.fromisoformat(finish_date)
    last = calendar.index_of(calendar.on_or_before(finish_date))
    by_id = {task['id']: task for task in tasks}
    incoming = {key: set(task.get('depends_on') or []) for key, task in by_id.items()}
    if len(by_id) != len(tasks) or any(key in values or values - by_id.keys() for key, values in incoming.items()):
        raise ValueError('Dependencies must reference distinct activities in this plan.')
    successors = {key: [] for key in by_id}
    degree = {key: len(values) for key, values in incoming.items()}
    for key, values in incoming.items():
        for predecessor in values:
            successors[predecessor].append(key)
    pending = deque(key for key, count in degree.items() if not count)
    order = []
    while pending:
        key = pending.popleft()
        order.append(key)
        for successor in successors[key]:
            degree[successor] -= 1
            if not degree[successor]:
                pending.append(successor)
    if len(order) != len(tasks):
        raise ValueError('Dependency cycles must be corrected before calculating dates.')

    # Preserve the complete upstream chain of work that has already started.
    protected = protected_work_ids(tasks)
    original = deepcopy(by_id)
    movable = {key for key, task in by_id.items() if key not in protected and not task.get('source_timing')
               and ('planned_start_date' in (task.get('schedule_generated_fields') or [])
                    or (not task.get('planned_start_date') and task.get('duration_source') == 'proposed'))}
    resizable = {key for key, task in by_id.items() if key not in protected
                 and task.get('duration_source') == 'proposed' and not task.get('effort_hours')
                 and 'duration_days' in (task.get('schedule_generated_fields') or [])
                 and not task.get('is_milestone') and task.get('activity_type') not in {'start_milestone', 'finish_milestone'}
                 and not task.get('source_timing')}
    source_review = set()
    review_days = (review_requirement or {}).get('duration_days')
    if review_days:
        for key, task in by_id.items():
            if task.get('workflow_stage_code') == 'COMPANY_REVIEW':
                source_review.add(key)
                if key in resizable:
                    task['duration_days'] = max(float(task['duration_days']), float(review_days))
        resizable -= source_review

    def duration_map():
        return {key: 0 if task.get('is_milestone') or task.get('activity_type') in {'start_milestone', 'finish_milestone'}
                else max(1, ceil(float(task.get('duration_days') or 1))) for key, task in by_id.items()}

    def network(durations, preferences=True, latest=None, keep_inside=False):
        starts, outgoing = {}, {key: [] for key in order}
        for key in order:
            task, bounds = by_id[key], []
            for predecessor in incoming[key]:
                details = [link for link in task.get('dependency_details') or [] if link['task_id'] == predecessor] or [{}]
                for link in details:
                    weight = _edge_weight(link.get('type', 'FS'), durations[predecessor], durations[key], link.get('lag_days', 0))
                    bounds.append(starts[predecessor] + weight)
                    outgoing[predecessor].append((key, weight))
            earliest = max(bounds, default=0)
            if keep_inside and key in movable:
                earliest = max(0, earliest)
            requested = task.get('planned_start_date')
            if requested and (preferences or key not in movable):
                point = calendar.index_of(date.fromisoformat(str(requested)))
                if key in movable and latest is not None:
                    point = min(point, latest[key])
                earliest = max(earliest, point)
            starts[key] = earliest
        finish = max((starts[key] + max(durations[key] - 1, 0) for key in order), default=0)
        return starts, outgoing, finish

    durations = duration_map()
    _, _, original_finish = network(durations)
    early, outgoing, earliest_finish = network(durations, preferences=False, keep_inside=True)
    compressed = False
    # Reduce only adjustable estimates when moving soft windows is insufficient.
    # Source/manual durations and contractual company-review periods stay intact.
    if earliest_finish > last and resizable:
        initial = dict(durations)

        def scaled(factor):
            return {key: max(1, ceil(initial[key] * factor)) if key in resizable else value
                    for key, value in initial.items()}

        minimum = scaled(0)
        minimum_starts, _, minimum_finish = network(minimum, preferences=False, keep_inside=True)
        if minimum_finish <= last and min(minimum_starts.values(), default=0) >= 0:
            low, high, best = 0.0, 1.0, minimum
            for _ in range(40):
                middle = (low + high) / 2
                candidate = scaled(middle)
                candidate_starts, _, candidate_finish = network(candidate, preferences=False, keep_inside=True)
                if candidate_finish <= last and min(candidate_starts.values(), default=0) >= 0:
                    low, best = middle, candidate
                else:
                    high = middle
            durations = best
            for key in resizable:
                if durations[key] != initial[key]:
                    by_id[key]['duration_days'] = durations[key]
                    compressed = True
            early, outgoing, earliest_finish = network(durations, preferences=False, keep_inside=True)

    feasible = earliest_finish <= last and last >= 0 and min(early.values(), default=0) >= 0
    if feasible:
        late, _ = calculate_backward_pass(order, durations, early, outgoing, last)
        selected, _, final_finish = network(durations, latest=late, keep_inside=True)
        unconstrained, _, _ = network(durations, latest=late)
        for key in movable:
            # A missing lower bound already follows the network and needs no
            # stored synthetic constraint. Move only existing generated windows.
            if by_id[key].get('planned_start_date') or selected[key] != unconstrained[key]:
                by_id[key]['planned_start_date'] = calendar.date_at(selected[key]).isoformat()
                by_id[key]['schedule_generated_fields'] = sorted(set(by_id[key].get('schedule_generated_fields') or []) | {'planned_start_date'})
        # Verify the dates without any display-only clamps, using the same
        # lower-bound and relationship semantics as the saved CPM calculation.
        final_starts, _, final_finish = network(durations)
        feasible = final_finish <= last and min(final_starts.values(), default=0) >= 0
    else:
        # Never falsify a source/manual network to make the Gantt look on time.
        _, _, final_finish = network(durations)
    moved = [key for key in order if by_id[key].get('planned_start_date') != original[key].get('planned_start_date')]
    resized = [key for key in order if by_id[key].get('duration_days') != original[key].get('duration_days')]
    for key in moved + resized:
        note = ('Proposed timing fitted within the registered project dates; source and manual constraints are retained.'
                if feasible else 'Source review periods are retained; fixed-date feasibility still needs review.')
        if note not in (by_id[key].get('schedule_rationale') or ''):
            by_id[key]['schedule_rationale'] = f"{by_id[key].get('schedule_rationale') or ''} {note}".strip()
    warnings = []
    if compressed:
        warnings.append('Proposed estimates were reduced to meet the fixed finish. Review workload and resource capacity; no source or manual duration was shortened.')
    if not feasible:
        warnings.append('The fixed project dates are unchanged. Preserved durations, review periods or constraints cannot fit this network; the forecast remains visible as a warning and does not prevent submission.')
    return tasks, {
        'policy': 'fixed_project_dates', 'start_date': start_date.isoformat(), 'finish_date': finish_date.isoformat(),
        'fits': feasible, 'original_forecast_finish': calendar.date_at(original_finish).isoformat(),
        'forecast_finish': calendar.date_at(final_finish).isoformat(),
        'moved_activity_ids': moved, 'resized_activity_ids': resized,
        'source_review_days': review_days, 'source_values_changed': False,
    }, warnings
