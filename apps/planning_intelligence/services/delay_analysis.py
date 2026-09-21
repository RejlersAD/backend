"""Evidence-linked remaining-work sensitivity, never contractual entitlement.

Every scenario starts from a frozen published observation. Absolute proposed
values replace explicitly matched reference values; event durations are never
added automatically and individual event effects are never summed.
"""
from collections import defaultdict, deque
from copy import deepcopy
from datetime import timedelta

from .operational_calculations import (
    FrozenCalendar, MILESTONES, _date, _decimal, _json, forecast_operational_schedule,
)


DELAY_RULE_VERSION = 'delay-recovery-sensitivity/1.0'
METHOD = DELAY_RULE_VERSION
REFERENCE_RULE = 'operational-controls/1.0'
MAX_WITNESSES = 30
MAX_PATH_NODES = 10000
MAX_PATH_EDGES = 20000
LIMITATIONS = [
    'Prospective remaining-work sensitivity at the published data date, not retrospective forensic delay attribution.',
    'Combined net effects are calculated once; isolated event days are not added and overlapping causes are not apportioned.',
    'One frozen calendar and whole-working-day durations/lags; unsupported or missing inputs remain unavailable.',
    'Recovery inputs are proposals. Resource capacity, productivity, overtime, cost, safety and contractual feasibility are not inferred.',
    'No baseline or contractual date is changed. No responsibility, concurrency entitlement or extension entitlement is determined.',
    'Paths distinguish potential reachability from changed timing; witnesses are bounded examples, not an enumeration of every path.',
]


class DelayAnalysisError(ValueError):
    def __init__(self, message, *, code='invalid_delay_change', **details):
        super().__init__(message)
        self.code = code
        self.issues = [{'code': code, 'message': message, 'severity': 'error', **details}]


def _fail(message, code='invalid_delay_change', **details):
    raise DelayAnalysisError(message, code=code, **details)


def _identity(value, label):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value).strip():
        _fail(f'{label} requires an explicit identity.', 'invalid_identity')
    return str(value)


def _activity_map(baseline):
    result = {}
    for row in baseline.get('activities') or []:
        key = row.get('id') if isinstance(row, dict) else None
        if type(key) is not int or key <= 0 or key in result:
            _fail('Frozen baseline activity IDs must be distinct positive integers.', 'baseline_identity_invalid')
        result[key] = row
    if not result:
        _fail('The reference baseline contains no activities.', 'baseline_identity_invalid')
    return result


def _observation_map(observations, activities):
    result = {}
    for row in observations:
        key = row.get('activity_id') if isinstance(row, dict) else None
        if type(key) is not int or key not in activities or key in result:
            _fail('Published observations require distinct exact baseline activity IDs.', 'reference_observation_invalid')
        result[key] = row
    return result


def _event_map(events, activities):
    result = {}
    for row in events:
        if not isinstance(row, dict):
            _fail('Supply structured delay events.', 'invalid_event')
        key = _identity(row.get('id'), 'Event')
        if key in result:
            _fail('An event may appear only once in the selected set.', 'duplicate_event', event_id=key)
        identifiers = row.get('activity_ids')
        if (not isinstance(identifiers, list) or not identifiers
                or any(type(value) is not int or value not in activities for value in identifiers)
                or len(set(identifiers)) != len(identifiers)):
            _fail('An event requires distinct exact activities from this baseline.', 'event_scope_invalid', event_id=key)
        evidence = row.get('evidence')
        if (not isinstance(evidence, list) or not evidence
                or any(not isinstance(item, dict) or not str(item.get('reference') or '').strip() for item in evidence)):
            _fail('Each event requires explicit supporting evidence references.', 'event_evidence_required', event_id=key)
        start, finish = _date(row.get('start_date')), _date(row.get('end_date'))
        if ((row.get('start_date') is not None and start is None)
                or (row.get('end_date') is not None and finish is None)
                or start and finish and finish < start):
            _fail('Event dates must be valid and ordered.', 'event_dates_invalid', event_id=key)
        result[key] = row
    return result


def _number(value, *, nonnegative=False):
    result = _decimal(value)
    if result is None or result != result.to_integral_value() or nonnegative and result < 0:
        _fail('Supply an explicit finite whole-working-day value.', 'unsupported_day_value')
    return result


def _relationship(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {'type', 'lag_days'} or value.get('type') not in {'FS', 'SS', 'FF', 'SF'}:
        _fail('A relationship requires its explicit type and signed whole-working-day lag.', 'relationship_value_invalid')
    return {'type': value['type'], 'lag_days': _number(value['lag_days'])}


def _link_ids(row):
    return row.get('predecessor', row.get('predecessor_id')), row.get('successor', row.get('successor_id'))


def _apply(baseline, observations, release_floors, changes, events):
    source, statuses, floors = deepcopy(baseline), deepcopy(observations), dict(release_floors)
    activities = _activity_map(source)
    observed = _observation_map(statuses, activities)
    writes, seeds = set(), set()
    for change in changes:
        if not isinstance(change, dict) or 'expected_before' not in change or 'value' not in change:
            _fail('Every change requires expected_before and an explicit proposed value.')
        event_id = _identity(change.get('event_id'), 'Change event')
        if event_id not in events:
            _fail('A change must cite one of the selected events.', 'change_event_not_selected', event_id=event_id)
        for field in ('evidence', 'reason'):
            if not isinstance(change.get(field), str) or not change[field].strip():
                _fail(f'Every change requires an explicit {field}.', 'change_evidence_required', event_id=event_id)
        field = change.get('field')
        if field == 'relationship':
            pred, successor = change.get('predecessor_id'), change.get('successor_id')
            if (type(pred) is not int or type(successor) is not int or pred not in activities
                    or successor not in activities or pred == successor):
                _fail('A relationship change requires two distinct exact baseline activity IDs.', 'relationship_scope_invalid')
            key, identity = successor, ('relationship', pred, successor)
        elif field in {'remaining_duration_days', 'remaining_not_before'}:
            key = change.get('activity_id')
            if type(key) is not int or key not in activities:
                _fail('A changed activity must belong to the exact reference baseline.', 'change_scope_invalid')
            identity = (field, key)
        else:
            _fail('Only remaining duration, remaining release date or explicit relationship changes are supported. Actuals and contractual dates cannot be edited.', 'change_field_forbidden')
        if key not in events[event_id]['activity_ids']:
            _fail('The changed activity or relationship successor must be explicitly linked to its event.', 'change_event_scope_mismatch', activity_id=key, event_id=event_id)
        if identity in writes:
            _fail('Conflicting or duplicate changes to the same field/link require one reconciled proposed value.', 'duplicate_change', activity_id=key)
        writes.add(identity)
        if key not in observed:
            _fail('A scenario cannot invent status for an activity absent from the published report.', 'reference_observation_missing', activity_id=key)
        if observed[key].get('actual_finish'):
            _fail('Completed work is historical evidence and cannot be changed by this scenario.', 'completed_activity_immutable', activity_id=key)
        seeds.add(key)
        if field == 'remaining_duration_days':
            old = _decimal(observed[key].get(field))
            if observed[key].get(field) is not None and old is None:
                _fail('The frozen remaining duration is invalid; review the published reference.', 'reference_value_invalid', activity_id=key)
            expected = None if change['expected_before'] is None else _number(change['expected_before'], nonnegative=True)
            if old != expected:
                _fail('The expected remaining duration does not match the selected scenario input.', 'expected_value_mismatch', activity_id=key)
            value = _number(change['value'], nonnegative=True)
            milestone = activities[key].get('activity_type') in MILESTONES
            if (milestone and value != 0) or (not milestone and value == 0):
                _fail('Milestones require zero remaining duration; unfinished tasks require a positive duration.', 'remaining_duration_invalid', activity_id=key)
            observed[key][field] = str(value)
        elif field == 'remaining_not_before':
            expected = _date(change['expected_before']) if change['expected_before'] is not None else None
            value = _date(change['value']) if change['value'] is not None else None
            if ((change['expected_before'] is not None and expected is None)
                    or (change['value'] is not None and value is None)):
                _fail('Remaining release dates must be ISO calendar dates or an explicit null to remove a proposed hold.', 'release_date_invalid', activity_id=key)
            if _date(floors.get(key)) != expected:
                _fail('The expected release date does not match the selected scenario input.', 'expected_value_mismatch', activity_id=key)
            if value is None:
                floors.pop(key, None)
            else:
                floors[key] = value.isoformat()
        else:
            before, after = _relationship(change['expected_before']), _relationship(change['value'])
            if before is None and after is None:
                _fail('A relationship change must add, remove or replace an explicit relationship.', 'relationship_value_invalid')
            seeds.add(pred)
            links = source.setdefault('relationships', [])
            matches = [row for row in links if _link_ids(row) == (pred, successor)
                       and row.get('relationship_type') == (before or after)['type']]
            if before is None:
                if matches:
                    _fail('The relationship already exists; match it explicitly before replacing it.', 'expected_value_mismatch')
            elif len(matches) != 1 or _decimal(matches[0].get('lag_days')) != before['lag_days']:
                _fail('The expected typed relationship does not match one unique frozen link.', 'expected_value_mismatch')
            if after and any(_link_ids(row) == (pred, successor) and row.get('relationship_type') == after['type']
                             for row in links if row not in matches):
                _fail('The replacement would duplicate an existing typed relationship.', 'duplicate_relationship')
            for row in matches:
                links.remove(row)
            if after:
                links.append({'predecessor': pred, 'successor': successor,
                              'relationship_type': after['type'], 'lag_days': str(after['lag_days'])})
    if any(key[0] == 'relationship' for key in writes):
        outgoing, indegree = defaultdict(list), {key: 0 for key in activities}
        for link in source.get('relationships') or []:
            pred, successor = _link_ids(link)
            if pred not in activities or successor not in activities:
                _fail('A proposed relationship lies outside this exact baseline.', 'relationship_scope_invalid')
            outgoing[pred].append(successor)
            indegree[successor] += 1
        pending, count = deque(key for key, degree in indegree.items() if degree == 0), 0
        while pending:
            count += 1
            for key in outgoing[pending.popleft()]:
                indegree[key] -= 1
                if indegree[key] == 0:
                    pending.append(key)
        if count != len(activities):
            _fail('The proposed typed relationships contain a dependency cycle.', 'scenario_dependency_cycle')
    return source, statuses, floors, seeds


def _forecast_signature(forecast):
    if not isinstance(forecast, dict):
        return None
    rows, seen = [], set()
    numeric = {'remaining_duration_days', 'total_float_days'}
    for row in forecast.get('activities') or []:
        key = row.get('activity_id')
        if type(key) is not int or key in seen:
            return None
        seen.add(key)
        values = {}
        for field in ('actual_start', 'actual_finish', 'forecast_start', 'forecast_finish', 'remaining_start', 'remaining_finish',
                      'remaining_duration_days', 'total_float_days', 'is_critical', 'status'):
            value = row.get(field)
            if value is not None and field in numeric and _decimal(value) is None:
                return None
            values[field] = _decimal(value) if value is not None and field in numeric else value
        rows.append((key, values))
    return {'status': forecast.get('status'), 'method': forecast.get('method'),
            'data_date': forecast.get('data_date'), 'data_date_convention': forecast.get('data_date_convention'),
            'forecast_finish': forecast.get('forecast_finish'), 'contractual_finish': forecast.get('contractual_finish'),
            'finish_variance_calendar_days': forecast.get('finish_variance_calendar_days'),
            'activities': sorted(rows, key=lambda item: item[0])}


def _shift(before, after):
    first, last = _date(before), _date(after)
    return (last - first).days if first is not None and last is not None else None


def _calendar(baseline, data_date):
    inputs = baseline.get('accepted_inputs') or {}
    referenced = {row.get('calendar', row.get('calendar_id')) or inputs.get('default_calendar_id') for row in baseline.get('activities') or []}
    if len(referenced) != 1:
        return None
    values = [row for row in inputs.get('calendars') or [] if row.get('id') in referenced]
    if len(values) != 1:
        return None
    try:
        return FrozenCalendar(values[0], _date(data_date) + timedelta(days=1))
    except (ValueError, OverflowError):
        return None


def _driving_slack(link, rows, activities, calendar):
    if calendar is None:
        return None
    pred, succ = _link_ids(link)
    previous, following = rows.get(pred) or {}, rows.get(succ) or {}
    kind, lag = link.get('relationship_type'), _decimal(link.get('lag_days'))
    if kind not in {'FS', 'SS', 'FF', 'SF'} or lag is None or lag != lag.to_integral_value():
        return None
    # These are the same inclusive-date/exclusive-finish boundaries used by
    # retained-logic forecasting. An actual start remains a fixed boundary.
    try:
        when = _date(previous.get('forecast_finish' if kind[0] == 'F' else 'forecast_start'))
        if when is None:
            return None
        anchor = calendar.index(when)
        if kind[0] == 'F' and activities[pred].get('activity_type') not in MILESTONES:
            anchor += 1
        release = _date(following.get('remaining_start'))
        if release is None or following.get('status') != 'forecast':
            return None
        target = calendar.index(release)
        if kind[1] == 'F':
            remaining = _decimal(following.get('remaining_duration_days'))
            if remaining is None:
                return None
            target += int(remaining)
        return target - anchor - int(lag)
    except (ValueError, OverflowError, KeyError):
        return None


def _paths(reference_baseline, scenario_baseline, reference_forecast, scenario_forecast, seeds, comparisons, data_date):
    activities = _activity_map(scenario_baseline)
    reference_rows = {row['activity_id']: row for row in reference_forecast.get('activities') or []}
    scenario_rows = {row['activity_id']: row for row in scenario_forecast.get('activities') or []}
    reference_calendar, scenario_calendar = _calendar(reference_baseline, data_date), _calendar(scenario_baseline, data_date)
    edges = {}
    for label, baseline, rows, calendar in [('reference', reference_baseline, reference_rows, reference_calendar),
                                          ('scenario', scenario_baseline, scenario_rows, scenario_calendar)]:
        for link in baseline.get('relationships') or []:
            pred, succ = _link_ids(link)
            lag = _decimal(link.get('lag_days'))
            identity = pred, succ, link.get('relationship_type'), str(lag.normalize()) if lag is not None else None
            edge = edges.setdefault(identity, {'predecessor_id': pred, 'successor_id': succ,
                'type': link.get('relationship_type'), 'lag_days': lag, 'in_reference': False, 'in_scenario': False,
                'reference_slack_working_days': None, 'scenario_slack_working_days': None})
            edge[f'in_{label}'] = True
            edge[f'{label}_slack_working_days'] = _driving_slack(link, rows, activities, calendar)
    forward, reverse, driving_reverse = defaultdict(list), defaultdict(list), defaultdict(list)
    for edge in edges.values():
        pred, succ = edge['predecessor_id'], edge['successor_id']
        if pred not in activities or succ not in activities:
            continue
        forward[pred].append(succ)
        reverse[succ].append(pred)
        edge['driving_in_scenario'] = edge['in_scenario'] and edge['scenario_slack_working_days'] == 0
        if edge['driving_in_scenario']:
            driving_reverse[succ].append(pred)
    reachable, queue = set(seeds), deque(sorted(seeds))
    while queue:
        for successor in sorted(set(forward[queue.popleft()])):
            if successor not in reachable:
                reachable.add(successor)
                queue.append(successor)
    changed = {row['activity_id']: row for row in comparisons}
    shown = sorted(reachable)[:MAX_PATH_NODES]
    shown_set = set(shown)
    nodes = [{'activity_id': key, 'external_id': activities[key].get('external_id'), 'name': activities[key].get('name'),
              'directly_changed': key in seeds, 'potentially_affected': True,
              'timing_changed': changed.get(key, {}).get('timing_changed', False),
              'milestone': activities[key].get('activity_type') in MILESTONES} for key in shown]
    reachable_edges = [row for row in edges.values() if row['predecessor_id'] in reachable and row['successor_id'] in reachable]
    shown_edges = [row for row in reachable_edges if row['predecessor_id'] in shown_set and row['successor_id'] in shown_set]
    shown_edges.sort(key=lambda row: (row['predecessor_id'], row['successor_id'], str(row['type']), str(row['lag_days'])))
    terminals = sorted(key for key in reachable if key in changed and changed[key]['timing_changed'] and (
        activities[key].get('activity_type') in MILESTONES or not forward[key]
        or scenario_rows.get(key, {}).get('forecast_finish') == scenario_forecast.get('forecast_finish')))

    def witness(terminal, parents):
        pending, visited, next_node = deque([terminal]), {terminal}, {}
        found = None
        while pending:
            key = pending.popleft()
            if key in seeds:
                found = key
                break
            for predecessor in sorted(set(parents[key])):
                if predecessor not in visited:
                    visited.add(predecessor)
                    next_node[predecessor] = key
                    pending.append(predecessor)
        if found is None:
            return None
        path = [found]
        while path[-1] != terminal and len(path) <= MAX_PATH_NODES:
            path.append(next_node[path[-1]])
        return path

    witnesses = []
    for terminal in terminals[:MAX_WITNESSES]:
        path, basis = witness(terminal, driving_reverse), 'scenario_driving_edges'
        if path is None:
            path, basis = witness(terminal, reverse), 'potential_reachability_only'
        if path:
            witnesses.append({'terminal_activity_id': terminal, 'activity_ids': path[:MAX_PATH_NODES], 'basis': basis,
                              'truncated': len(path) > MAX_PATH_NODES, 'causal_entitlement': False})
    return {'nodes': nodes, 'edges': shown_edges[:MAX_PATH_EDGES], 'witnesses': witnesses,
            'potentially_affected_activity_ids': sorted(reachable), 'timing_changed_activity_ids': sorted(key for key, row in changed.items() if row['timing_changed']),
            'reachable_activity_count': len(reachable), 'edge_count': len(reachable_edges), 'witness_terminal_count': len(terminals),
            'truncated': {'nodes': max(0, len(reachable) - MAX_PATH_NODES), 'edges': len(reachable_edges) - min(len(shown_edges), MAX_PATH_EDGES),
                          'witnesses': max(0, len(terminals) - MAX_WITNESSES)}}


def _comparison(reference_baseline, scenario_baseline, reference_forecast, scenario_forecast, seeds, data_date):
    reference_rows = {row['activity_id']: row for row in reference_forecast.get('activities') or []}
    scenario_rows = {row['activity_id']: row for row in scenario_forecast.get('activities') or []}
    activities, changed = _activity_map(reference_baseline), []
    for key, activity in activities.items():
        before, after = reference_rows.get(key) or {}, scenario_rows.get(key) or {}
        timing_changed = any(before.get(field) is not None and after.get(field) is not None and before[field] != after[field]
                             for field in ('forecast_start', 'forecast_finish', 'remaining_start', 'remaining_finish'))
        availability_changed = any((before.get(field) is None) != (after.get(field) is None)
                                   for field in ('forecast_start', 'forecast_finish', 'remaining_start', 'remaining_finish'))
        float_changed = _decimal(before.get('total_float_days')) != _decimal(after.get('total_float_days'))
        if not (timing_changed or availability_changed or float_changed or key in seeds):
            continue
        changed.append({'activity_id': key, 'external_id': activity.get('external_id'), 'name': activity.get('name'),
            'reference_start': before.get('forecast_start'), 'reference_finish': before.get('forecast_finish'),
            'scenario_start': after.get('forecast_start'), 'scenario_finish': after.get('forecast_finish'),
            'reference_remaining_start': before.get('remaining_start'), 'scenario_remaining_start': after.get('remaining_start'),
            'start_shift_calendar_days': _shift(before.get('forecast_start'), after.get('forecast_start')),
            'finish_shift_calendar_days': _shift(before.get('forecast_finish'), after.get('forecast_finish')),
            'reference_float': before.get('total_float_days'), 'scenario_float': after.get('total_float_days'),
            'directly_changed': key in seeds, 'timing_changed': timing_changed, 'availability_changed': availability_changed,
            'float_changed': float_changed, 'reference_status': before.get('status'), 'scenario_status': after.get('status')})
    paths = _paths(reference_baseline, scenario_baseline, reference_forecast, scenario_forecast, seeds, changed, data_date)
    reachable = set(paths['potentially_affected_activity_ids'])
    for row in changed:
        row['potentially_affected'] = row['activity_id'] in reachable
    finish = scenario_forecast.get('forecast_finish')
    shift = _shift(reference_forecast.get('forecast_finish'), finish)
    overrun = _shift(reference_forecast.get('contractual_finish'), finish)
    status = ('complete' if reference_forecast.get('status') == scenario_forecast.get('status') == 'complete'
              else 'partial' if scenario_forecast.get('status') in {'complete', 'partial'} else 'unavailable')
    return {'status': status, 'forecast_finish': finish, 'net_finish_shift_calendar_days': shift,
            'contract_overrun_calendar_days': max(0, overrun) if overrun is not None else None,
            'affected_activities': changed,
            'affected_milestones': [row for row in changed if activities[row['activity_id']].get('activity_type') in MILESTONES],
            'paths': paths, 'forecast': scenario_forecast}


def _unavailable(reference, issue):
    return {'method': METHOD, 'reference': reference,
            'impact': {'status': 'unavailable', 'forecast_finish': None, 'net_finish_shift_calendar_days': None,
                       'contract_overrun_calendar_days': None, 'affected_activities': [], 'affected_milestones': [],
                       'paths': {'nodes': [], 'edges': [], 'witnesses': []}, 'forecast': None},
            'scenarios': [], 'issues': [issue], 'limitations': list(LIMITATIONS)}


def analyze_delay_case(reference, events, changes, scenarios):
    """Review combined event impacts and independent recovery proposals.

    Changes use absolute values and optimistic expected-before checks. Recovery
    options each start from the same combined impacted state. A proposed hold
    may be removed with an explicit null value. Only the caller persists and
    reviews results; this function never decides extension entitlement.
    """
    if not isinstance(reference, dict):
        _fail('Supply a frozen published-report reference.', 'reference_required')
    baseline = reference.get('baseline')
    observations = reference.get('observations')
    when = _date(reference.get('data_date'))
    if not isinstance(baseline, dict) or not isinstance(observations, list) or when is None:
        _fail('The reference requires its frozen baseline, observations and ISO data date.', 'reference_invalid')
    activities = _activity_map(baseline)
    _observation_map(observations, activities)
    reference_forecast = reference.get('forecast') or {}
    if not isinstance(reference_forecast, dict):
        _fail('The reference requires a structured published forecast.', 'reference_invalid')
    summary = {'report_id': reference.get('report_id'), 'data_date': when.isoformat(),
               'forecast_finish': reference_forecast.get('forecast_finish'),
               'contractual_finish': reference_forecast.get('contractual_finish')}
    if reference.get('rule_version') != REFERENCE_RULE:
        return _unavailable(summary, {'code': 'reference_rule_version_unsupported', 'severity': 'error',
            'message': 'The published report uses another forecast rule version. Review a compatible reference; history was not recalculated as a new baseline.'})
    unchanged = forecast_operational_schedule(baseline, observations, when.isoformat())
    if _forecast_signature(reference_forecast) != _forecast_signature(unchanged['forecast']):
        return _unavailable(summary, {'code': 'published_forecast_not_reproduced', 'severity': 'error',
            'message': 'Unchanged frozen inputs do not reproduce the published forecast. Resolve the reference mismatch before attributing scenario impacts.'})
    if not all(isinstance(value, list) for value in (events, changes, scenarios)):
        _fail('Events, changes and recovery scenarios must be explicit lists.')
    if len(scenarios) > 20:
        _fail('Analyze at most 20 explicitly defined recovery options per case.', 'scenario_limit')
    selected_events = _event_map(events, activities)
    source, observed, holds, seeds = _apply(baseline, observations, {}, changes, selected_events)
    impacted = forecast_operational_schedule(source, observed, when.isoformat(), remaining_release_by_activity=holds)
    impact = _comparison(baseline, source, reference_forecast, impacted['forecast'], seeds, when.isoformat())
    issues = [{**row, 'analysis': 'reference'} for row in unchanged['issues']]
    issues.extend({**row, 'analysis': 'impact'} for row in impacted['issues'])
    for event_id, event in selected_events.items():
        if event.get('end_date') and _date(event['end_date']) <= when:
            issues.append({'code': 'event_may_already_be_observed', 'severity': 'warning', 'event_id': event_id,
                'message': 'This event ended by the published data date and may already be reflected in reported remaining work. Review evidence; this sensitivity is not proof of retrospective causation.'})
    for label, change_rows in [('impact', changes), *[(str(row.get('id')), row.get('changes') or []) for row in scenarios if isinstance(row, dict)]]:
        for change in change_rows:
            if isinstance(change, dict) and change.get('field') == 'remaining_not_before' and change.get('value') is not None:
                release = _date(change['value'])
                if release is not None and release <= when:
                    issues.append({'code': 'release_not_after_data_date', 'severity': 'warning', 'analysis': label,
                        'activity_id': change.get('activity_id'),
                        'message': 'This proposed hold ends on or before the data date. It adds no remaining-work delay; event elapsed days were not automatically added.'})
    if reference_forecast.get('status') != 'complete':
        issues.append({'code': 'reference_forecast_incomplete', 'severity': 'warning', 'analysis': 'reference',
                       'message': 'The published reference has missing forecast inputs; only available activity comparisons are reported and net completion impact remains unknown.'})
    results, identities = [], set()
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            _fail('Each recovery option must be structured.', 'scenario_invalid')
        key = _identity(scenario.get('id'), 'Recovery option')
        if key in identities:
            _fail('Recovery option IDs must be distinct.', 'duplicate_scenario')
        identities.add(key)
        if not isinstance(scenario.get('name'), str) or not scenario['name'].strip() or not isinstance(scenario.get('changes'), list):
            _fail('Each recovery option requires a name and explicit changes.', 'scenario_invalid')
        option_source, option_observed, option_holds, option_seeds = _apply(source, observed, holds, scenario['changes'], selected_events)
        option = forecast_operational_schedule(option_source, option_observed, when.isoformat(), remaining_release_by_activity=option_holds)
        result = _comparison(baseline, option_source, reference_forecast, option['forecast'], seeds | option_seeds, when.isoformat())
        result.update(id=key, name=scenario['name'],
            recovered_calendar_days=_shift(option['forecast'].get('forecast_finish'), impacted['forecast'].get('forecast_finish')))
        issues.extend({**row, 'analysis': key} for row in option['issues'])
        results.append(result)
    return _json({'method': METHOD, 'reference': summary, 'impact': impact, 'scenarios': results,
                  'issues': issues, 'limitations': list(LIMITATIONS)})
