"""Pure operational controls over frozen inputs and explicitly reported actuals.

This module performs no reads or writes. Approval, evidence acceptance, and period
locking belong to its caller. Money and percentages are decimal strings in the
returned JSON. Missing observations never mean zero progress or zero cost.

Forecast convention: the data date is the end of a reporting day. Remaining
work uses the next working day and retained, typed baseline logic. Actual dates
are facts and are never moved. Unsupported inputs stay unavailable.
"""
from collections import defaultdict, deque
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


ZERO = Decimal('0')
ONE = Decimal('1')
HUNDRED = Decimal('100')
MAX_DAYS = 366 * 100
MILESTONES = {'start_milestone', 'finish_milestone'}


def _decimal(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _date(value):
    if isinstance(value, datetime):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value) if isinstance(value, str) and len(value) == 10 else None
    except ValueError:
        return None


def _rounded(value, precision='0.01'):
    return value.quantize(Decimal(precision), rounding=ROUND_HALF_UP) if value is not None else None


def _json(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    return value


def _issue(issues, code, message, activity_id=None, *, severity='warning', field=None):
    item = {'code': code, 'message': message, 'severity': severity}
    if activity_id is not None:
        item['activity_id'] = activity_id
    if field:
        item['field'] = field
    if item not in issues:
        issues.append(item)


def _rows_by_id(rows, key, issues, label):
    result, duplicates = {}, set()
    for row in rows:
        identifier = row.get(key) if isinstance(row, dict) else None
        if type(identifier) is not int or identifier <= 0:
            _issue(issues, f'{label}_invalid_identity', f'{label} requires an exact positive integer activity ID.', severity='error')
            continue
        if identifier in result:
            duplicates.add(identifier)
        result[identifier] = row
    for identifier in duplicates:
        result[identifier] = None
        _issue(issues, f'{label}_duplicate_identity', f'Duplicate {label} rows are ambiguous.', identifier, severity='error')
    return result


class FrozenCalendar:
    """Bounded working-day arithmetic with no default weekdays or holidays."""

    def __init__(self, value, origin):
        weekdays = value.get('working_weekdays') if isinstance(value, dict) else None
        if (not isinstance(weekdays, list) or not weekdays
                or any(type(day) is not int or not 0 <= day <= 6 for day in weekdays)
                or len(set(weekdays)) != len(weekdays)):
            raise ValueError('Frozen working weekdays are missing or invalid.')
        self.weekdays = set(weekdays)
        self.exceptions = {}
        hours = _decimal(value.get('hours_per_day'))
        for row in value.get('exceptions') or []:
            when = _date(row.get('date'))
            if when is None or type(row.get('is_working')) is not bool or when in self.exceptions:
                raise ValueError('Frozen calendar exceptions are invalid or duplicated.')
            exception_hours = _decimal(row.get('working_hours'))
            if row['is_working'] and row.get('working_hours') is not None and (
                    exception_hours is None or hours is None or exception_hours != hours):
                raise ValueError('Variable-length working-day exceptions require an hourly forecast engine.')
            self.exceptions[when] = row['is_working']
        self.origin = self.on_or_after(origin)
        self.days = {0: self.origin}
        self.indices = {self.origin: 0}
        self.low = self.high = 0

    def working(self, when):
        return self.exceptions.get(when, when.weekday() in self.weekdays)

    def _move(self, when, direction):
        for _ in range(MAX_DAYS):
            if self.working(when):
                return when
            when += timedelta(days=direction)
        raise ValueError('The frozen calendar has no working day within the supported horizon.')

    def on_or_after(self, when):
        return self._move(when, 1)

    def on_or_before(self, when):
        return self._move(when, -1)

    def at(self, index):
        if abs(index) > MAX_DAYS:
            raise ValueError('The forecast exceeds the supported 100-year working-day horizon.')
        while index > self.high:
            self.days[self.high + 1] = self.on_or_after(self.days[self.high] + timedelta(days=1))
            self.high += 1
            self.indices[self.days[self.high]] = self.high
        while index < self.low:
            self.days[self.low - 1] = self.on_or_before(self.days[self.low] - timedelta(days=1))
            self.low -= 1
            self.indices[self.days[self.low]] = self.low
        return self.days[index]

    def index(self, when):
        if not self.working(when):
            raise ValueError('An actual date falls on a nonworking day; its date is retained without rounding.')
        if abs((when - self.origin).days) > MAX_DAYS:
            raise ValueError('The date exceeds the supported 100-year horizon.')
        while self.days[self.high] < when:
            self.at(self.high + 1)
        while self.days[self.low] > when:
            self.at(self.low - 1)
        return self.indices[when]

    def count(self, start, finish):
        if finish < start:
            return 0
        if (finish - start).days > MAX_DAYS:
            raise ValueError('The baseline span exceeds the supported 100-year horizon.')
        first, last = self.on_or_after(start), self.on_or_before(finish)
        return max(0, self.index(last) - self.index(first) + 1)


def _progress(policy, observation, issues, identifier):
    if observation is None:
        _issue(issues, 'progress_not_reported', 'No accepted observation was supplied for this activity.', identifier)
        return None
    method = policy.get('method') if policy else None
    start, finish = _date(observation.get('actual_start')), _date(observation.get('actual_finish'))
    if method in {'zero_hundred', 'fifty_fifty'} and not start and not finish:
        if _decimal(observation.get('physical_progress_pct')) != ZERO:
            _issue(issues, 'progress_status_not_reported', 'Report an explicit zero for unstarted work, or its actual start/finish; an empty status row is not zero progress.', identifier)
            return None
    if method == 'manual_percent':
        percent = _decimal(observation.get('physical_progress_pct'))
        if percent is None or not ZERO <= percent <= HUNDRED:
            _issue(issues, 'progress_percent_not_specified', 'Report a physical progress percentage from 0 to 100.', identifier)
            return None
        result = percent / HUNDRED
    elif method == 'zero_hundred':
        result = ONE if finish else ZERO
    elif method == 'fifty_fifty':
        result = ONE if finish else Decimal('0.5') if start else ZERO
    elif method == 'quantity':
        planned = _decimal(policy.get('planned_quantity'))
        installed = _decimal(observation.get('installed_quantity'))
        if not policy.get('quantity_unit') or planned is None or planned <= ZERO or installed is None or installed < ZERO:
            _issue(issues, 'quantity_measurement_incomplete', 'Quantity earning requires an approved positive quantity/unit and reported installed quantity.', identifier)
            return None
        if installed > planned:
            _issue(issues, 'quantity_exceeds_budget', 'Installed quantity exceeds the approved earning quantity; earned progress is capped at 100%.', identifier)
        result = min(ONE, installed / planned)
    else:
        _issue(issues, 'earning_method_not_specified', 'An approved earning method is required.', identifier)
        return None
    if finish and result != ONE:
        _issue(issues, 'completed_progress_inconsistent', 'An actual finish conflicts with reported progress below 100%.', identifier, severity='error')
        return None
    remaining = _decimal(observation.get('remaining_duration_days'))
    if result == ONE and remaining is not None and remaining > ZERO:
        _issue(issues, 'completed_remaining_inconsistent', 'Complete earned progress conflicts with positive remaining work. Review the accepted measurement.', identifier, severity='error')
        return None
    if result > ZERO and start is None:
        _issue(issues, 'actual_start_not_reported', 'Reported earned work requires an actual start date.', identifier, severity='error')
        return None
    return result


def _planned_fraction(activity, policy, when, budget, calendar):
    method = policy.get('pv_method') if policy else None
    if method == 'working_day_linear':
        if calendar is None:
            return None, None, 'frozen_calendar_unavailable'
        start, finish = _date(activity.get('planned_start')), _date(activity.get('planned_finish'))
        if not start or not finish or finish < start:
            return None, None, 'baseline_dates_unavailable'
        if when < start:
            fraction = ZERO
        elif when >= finish:
            fraction = ONE
        elif activity.get('activity_type') in MILESTONES:
            fraction = ZERO
        else:
            count = calendar.count(start, finish)
            if not count:
                return None, None, 'baseline_working_days_empty'
            fraction = Decimal(calendar.count(start, when)) / Decimal(count)
        return fraction, budget * fraction if budget is not None else None, None
    if method == 'explicit_points':
        points = policy.get('planned_value')
        if not isinstance(points, list) or not points:
            return None, None, 'planned_value_points_missing'
        previous_date, previous_value = None, None
        value = None
        for point in points:
            point_date, amount = _date(point.get('date')), _decimal(point.get('value'))
            if (point_date is None or amount is None or amount < ZERO
                    or previous_date is not None and point_date <= previous_date
                    or previous_value is not None and amount < previous_value
                    or budget is not None and amount > budget):
                return None, None, 'planned_value_points_invalid'
            previous_date, previous_value = point_date, amount
            if point_date <= when:
                value = amount
        # Explicit cumulative points are step values, never interpolated. No
        # observation before the first point is invented, including zero.
        if value is None:
            return None, None, 'planned_value_before_first_point'
        fraction = value / budget if budget is not None and budget > ZERO else None
        return fraction, value, None
    return None, None, 'planned_value_method_not_specified'


def _forecast(activities, observations, calendars, default_calendar_id, inputs, data_date, issues,
              remaining_release_by_activity=None):
    rows, states = [], {}
    activity_map = {row['id']: row for row in activities}
    for activity in activities:
        identifier = activity['id']
        observed = observations.get(identifier)
        row = {'activity_id': identifier, 'actual_start': (observed or {}).get('actual_start'),
               'actual_finish': (observed or {}).get('actual_finish'), 'forecast_start': None, 'forecast_finish': None,
               'remaining_start': None, 'remaining_finish': None, 'remaining_duration_days': (observed or {}).get('remaining_duration_days'),
               'total_float_days': None, 'is_critical': None, 'status': 'unavailable', 'unavailable_reasons': []}
        rows.append(row)
        states[identifier] = {'row': row, 'model': activity, 'observation': observed, 'start': None, 'end': None,
                              'remaining_start': None, 'remaining': None, 'done': False, 'upper': None}
    result = {'status': 'unavailable', 'method': 'retained_logic_whole_working_days', 'data_date': data_date,
              'data_date_convention': 'end_of_day', 'contractual_finish': inputs.get('project_finish'),
              'forecast_finish': None, 'finish_variance_calendar_days': None, 'activities': rows,
              'activity_count': len(rows), 'calculated_activity_count': 0,
              'critical_activity_ids': [], 'limitations': ['Single frozen calendar; whole working-day remaining durations and lags.',
              'Remaining work follows retained logic. Actual dates are never moved.']}
    referenced = {row.get('calendar', row.get('calendar_id')) or default_calendar_id for row in activities}
    if len(referenced) != 1 or None in referenced or next(iter(referenced), None) not in calendars:
        _issue(issues, 'forecast_calendar_unsupported', 'A forecast requires one complete frozen calendar for all activities; mixed calendars are not yet supported.')
        for row in rows:
            row['unavailable_reasons'].append('forecast_calendar_unsupported')
        return result
    calendar = calendars[next(iter(referenced))]
    if calendar is None:
        for row in rows:
            row['unavailable_reasons'].append('frozen_calendar_unavailable')
        return result
    incoming, outgoing, indegrees = defaultdict(list), defaultdict(list), {key: 0 for key in states}
    blocked = set()
    for relationship in inputs.get('_relationships') or []:
        pred = relationship.get('predecessor', relationship.get('predecessor_id'))
        succ = relationship.get('successor', relationship.get('successor_id'))
        lag = _decimal(relationship.get('lag_days'))
        kind = relationship.get('relationship_type')
        if pred not in states or succ not in states:
            _issue(issues, 'forecast_relationship_identity_invalid', 'A frozen relationship references an activity outside this baseline.', severity='error')
            blocked.update(states)  # Scope is corrupt; do not silently drop an edge.
            continue
        incoming[succ].append((pred, kind, lag))
        outgoing[pred].append(succ)
        indegrees[succ] += 1
        if kind not in {'FS', 'SS', 'FF', 'SF'} or lag is None or lag != lag.to_integral_value():
            blocked.add(succ)
            _issue(issues, 'forecast_relationship_unsupported', 'Forecast logic requires FS/SS/FF/SF and an explicit whole-working-day lag.', succ)
    queue = deque(key for key, value in indegrees.items() if not value)
    order = []
    while queue:
        key = queue.popleft()
        order.append(key)
        for successor in outgoing[key]:
            indegrees[successor] -= 1
            if indegrees[successor] == 0:
                queue.append(successor)
    for key in states.keys() - set(order):
        blocked.add(key)
        _issue(issues, 'forecast_dependency_cycle', 'This activity is in or depends on a cyclic network.', key, severity='error')
    order.extend(sorted(states.keys() - set(order)))
    dynamic_edges = defaultdict(list)
    project_start = _date(inputs.get('project_start'))
    global_release = max(0, calendar.index(calendar.on_or_after(project_start))) if project_start else 0

    for key in order:
        state, activity = states[key], activity_map[key]
        row, observed = state['row'], state['observation']
        if observed is None:
            row['unavailable_reasons'].append('observation_missing')
            continue
        actual_start, actual_finish = _date(observed.get('actual_start')), _date(observed.get('actual_finish'))
        reported_remaining = _decimal(observed.get('remaining_duration_days'))
        if actual_finish and reported_remaining is not None and reported_remaining > ZERO:
            _issue(issues, 'completed_remaining_inconsistent', 'An actual finish conflicts with positive remaining work; actual dates are retained but the forecast is unavailable.', key, severity='error')
            row['unavailable_reasons'].append('completed_remaining_inconsistent')
            continue
        invalid_dates = any(observed.get(field) is not None and _date(observed[field]) is None for field in ('actual_start', 'actual_finish'))
        if (invalid_dates or actual_start and actual_start > data_date or actual_finish and actual_finish > data_date
                or actual_start and actual_finish and actual_finish < actual_start):
            _issue(issues, 'actual_dates_invalid', 'Actual dates must be valid, ordered and no later than the data date.', key, severity='error')
            row['unavailable_reasons'].append('actual_dates_invalid')
            continue
        milestone = activity.get('activity_type') in MILESTONES
        row['forecast_start'], row['forecast_finish'] = actual_start, actual_finish
        state['done'] = bool(actual_finish)
        try:
            state['start'] = calendar.index(actual_start) if actual_start else None
            state['end'] = calendar.index(actual_finish) + (0 if milestone else 1) if actual_finish else None
        except ValueError as exc:
            _issue(issues, 'actual_calendar_incompatible', str(exc), key)
            row['unavailable_reasons'].append('actual_calendar_incompatible')
            continue
        if key in blocked:
            row['unavailable_reasons'].append('unsupported_or_cyclic_logic')
            continue
        if activity.get('activity_type') not in {'task', 'start_milestone', 'finish_milestone'}:
            _issue(issues, 'forecast_activity_type_unsupported', 'This activity type requires a forecast method not supported by the whole-day engine.', key)
            row['unavailable_reasons'].append('forecast_activity_type_unsupported')
            continue
        if not actual_finish:
            remaining = _decimal(observed.get('remaining_duration_days'))
            if (remaining is None or remaining < ZERO or remaining != remaining.to_integral_value()
                    or not milestone and remaining == ZERO or milestone and remaining != ZERO):
                _issue(issues, 'remaining_duration_not_supported', 'Report an explicit positive whole-day remaining duration (zero for milestones); completed work requires an actual finish.', key)
                row['unavailable_reasons'].append('remaining_duration_not_supported')
                continue
            progress = _decimal(observed.get('physical_progress_pct'))
            quantity = _decimal(observed.get('installed_quantity'))
            if not actual_start and ((progress is not None and progress > ZERO) or (quantity is not None and quantity > ZERO)):
                _issue(issues, 'actual_start_not_reported', 'Reported work requires an actual start before the remaining work can be forecast.', key)
                row['unavailable_reasons'].append('actual_start_not_reported')
                continue
            state['remaining'] = int(remaining)
        earliest = global_release
        release = (remaining_release_by_activity or {}).get(key)
        if release is not None and not actual_finish:
            earliest = max(earliest, calendar.index(calendar.on_or_after(_date(release))))
        missing = False
        for pred, kind, lag in incoming[key]:
            source = states[pred]
            anchor = source['end'] if kind[0] == 'F' else source['start']
            if anchor is None:
                if not actual_finish:
                    missing = True
                    row['unavailable_reasons'].append('predecessor_boundary_unavailable')
                _issue(issues, 'predecessor_boundary_unavailable', f'Activity {pred} has no supported {"finish" if kind[0] == "F" else "start"} boundary for {kind} logic.', key)
                continue
            required = anchor + int(lag)
            actual_boundary = state['end'] if kind[1] == 'F' else state['start']
            if actual_boundary is not None and actual_boundary < required:
                _issue(issues, 'out_of_sequence_actual', f'Actual {kind[1]} boundary precedes the {kind} requirement from activity {pred}; actual dates were retained.', key)
            if actual_finish:
                continue
            offset = 0 if kind[1] == 'S' else state['remaining']
            earliest = max(earliest, required - offset)
            # Only unfinished predecessor boundaries that still depend on its
            # remaining-start variable participate in forecast backward pass.
            if not source['done'] and (kind[0] == 'F' or source['observation'].get('actual_start') is None):
                pred_offset = source['remaining'] if kind[0] == 'F' else 0
                dynamic_edges[pred].append((key, pred_offset + int(lag) - offset))
        if actual_finish:
            row['status'] = 'actual'
            continue
        if missing:
            continue
        constraint = activity.get('constraint_type') or 'none'
        constrained_date = _date(activity.get('constraint_date'))
        if constraint != 'none':
            if constraint not in {'start_no_earlier', 'start_no_later', 'finish_no_later', 'must_start', 'must_finish'} or constrained_date is None:
                row['unavailable_reasons'].append('forecast_constraint_unsupported')
                _issue(issues, 'forecast_constraint_unsupported', 'The frozen constraint is missing or unsupported.', key)
                continue
            if constraint.startswith('must_') and not calendar.working(constrained_date):
                row['unavailable_reasons'].append('exact_constraint_nonworking')
                _issue(issues, 'exact_constraint_nonworking', 'An exact-date constraint is nonworking in the frozen calendar; it was not rounded.', key)
                continue
            upper_only = constraint in {'start_no_later', 'finish_no_later'}
            point = calendar.index(calendar.on_or_before(constrained_date) if upper_only else calendar.on_or_after(constrained_date))
            finish_constraint = constraint in {'finish_no_later', 'must_finish'}
            bound = point + (0 if milestone else 1) - state['remaining'] if finish_constraint else point
            if actual_start and not finish_constraint:
                bad = state['start'] > point if upper_only else state['start'] != point if constraint == 'must_start' else state['start'] < point
                if bad:
                    _issue(issues, 'actual_constraint_violation', 'The actual start conflicts with the frozen start constraint; it was retained.', key)
            else:
                if not upper_only:
                    earliest = max(earliest, bound)
                if constraint != 'start_no_earlier':
                    state['upper'] = bound
        state['remaining_start'] = earliest
        state['start'] = state['start'] if actual_start else earliest
        state['end'] = earliest + state['remaining']
        row.update(forecast_start=actual_start or calendar.at(earliest),
                   forecast_finish=calendar.at(state['end'] - (0 if milestone else 1)),
                   remaining_start=calendar.at(earliest), remaining_finish=calendar.at(state['end'] - (0 if milestone else 1)), status='forecast')
        if state['upper'] is not None and earliest > state['upper']:
            _issue(issues, 'forecast_constraint_overrun', 'Remaining work cannot meet the frozen upper-bound constraint; no constraint or baseline date was changed.', key)

    complete = bool(rows) and all(row['status'] in {'actual', 'forecast'} and row['forecast_finish'] for row in rows)
    known = sum(row['status'] in {'actual', 'forecast'} for row in rows)
    result['status'] = 'complete' if complete else 'partial' if known else 'unavailable'
    result['calculated_activity_count'] = known
    result['activity_count'] = len(rows)
    contractual_finish = _date(inputs.get('project_finish'))
    if complete:
        result['forecast_finish'] = max(_date(row['forecast_finish']) for row in rows)
        if contractual_finish:
            result['finish_variance_calendar_days'] = (result['forecast_finish'] - contractual_finish).days
            target = calendar.index(calendar.on_or_before(contractual_finish))
            late = {key: target + (0 if state['model'].get('activity_type') in MILESTONES else 1) - state['remaining']
                    for key, state in states.items() if not state['done']}
            for key in reversed(order):
                if key not in late:
                    continue
                if states[key]['upper'] is not None:
                    late[key] = min(late[key], states[key]['upper'])
                for successor, weight in dynamic_edges[key]:
                    if successor in late:
                        late[key] = min(late[key], late[successor] - weight)
                slack = late[key] - states[key]['remaining_start']
                states[key]['row'].update(total_float_days=slack, is_critical=slack <= 0)
                if slack <= 0:
                    result['critical_activity_ids'].append(key)
            if result['finish_variance_calendar_days'] > 0:
                _issue(issues, 'forecast_contractual_finish_overrun', 'Reported remaining work forecasts beyond the contractual finish. The contractual date and baseline remain unchanged.')
        else:
            _issue(issues, 'forecast_target_not_specified', 'No frozen contractual finish is available; target-based float is unavailable.')
    return result


def forecast_operational_schedule(baseline_snapshot, observations, data_date, *, remaining_release_by_activity=None):
    """Pure forecast adapter for reviewable scenarios, without recomputing EVM.

    Release floors affect remaining work only. They do not change actual start,
    baseline constraints or contractual dates. A nonworking release date means
    no earlier than the next working day in the frozen calendar.
    """
    when = _date(data_date)
    if when is None:
        raise ValueError('Provide an ISO calendar data date.')
    issues = []
    activity_map = _rows_by_id(baseline_snapshot.get('activities') or [], 'id', issues, 'baseline_activity')
    observed = _rows_by_id(observations or [], 'activity_id', issues, 'observation')
    for key in observed.keys() - activity_map.keys():
        _issue(issues, 'observation_outside_baseline', 'The observation is outside this exact frozen baseline.', key, severity='error')
    releases = remaining_release_by_activity or {}
    if any(type(key) is not int or key not in activity_map or _date(value) is None for key, value in releases.items()):
        raise ValueError('Remaining release dates require exact baseline activity IDs and ISO dates.')
    inputs = baseline_snapshot.get('accepted_inputs') or {}
    calendars = {}
    for value in inputs.get('calendars') or []:
        key = value.get('id')
        if key in calendars:
            calendars[key] = None
            _issue(issues, 'frozen_calendar_duplicate', 'A frozen calendar ID is duplicated.', severity='error')
            continue
        try:
            calendars[key] = FrozenCalendar(value, when + timedelta(days=1))
        except (ValueError, OverflowError) as exc:
            calendars[key] = None
            _issue(issues, 'frozen_calendar_unavailable', str(exc))
    activities = [row for row in activity_map.values() if row is not None]
    try:
        forecast = _forecast(activities, observed, calendars, inputs.get('default_calendar_id'),
            {**inputs, '_relationships': baseline_snapshot.get('relationships') or []}, when, issues, releases)
    except (ValueError, OverflowError) as exc:
        _issue(issues, 'forecast_calendar_horizon_unsupported', str(exc))
        forecast = {'status': 'unavailable', 'method': 'retained_logic_whole_working_days', 'data_date': when,
                    'contractual_finish': inputs.get('project_finish'), 'forecast_finish': None, 'activities': []}
    if not activities or any(row['code'].endswith(('invalid_identity', 'duplicate_identity', 'outside_baseline')) for row in issues):
        forecast.update(status='unavailable', forecast_finish=None, finish_variance_calendar_days=None, critical_activity_ids=[])
        for row in forecast['activities']:
            row.update(forecast_start=None, forecast_finish=None, remaining_start=None, remaining_finish=None,
                       total_float_days=None, is_critical=None, status='unavailable')
            row['unavailable_reasons'].append('baseline_scope_incomplete')
    return _json({'forecast': forecast, 'issues': issues})


def calculate_operational_report(baseline_snapshot, policy_definition, observations, data_date, actuals, *, cost_coverage_confirmed=False):
    """Calculate without persistence; caller must supply approved frozen inputs.

    ``actuals.costs_by_currency`` is a currency-to-cumulative-cost mapping. An
    absent currency is unknown, including when the mapping is empty. Explicit
    zero is supported. AC coverage confirmation is separate from approval of
    earning rules; no currency conversion or implicit cost completeness exists.
    """
    issues = [dict(row) for row in (actuals or {}).get('issues') or [] if isinstance(row, dict)]
    when = _date(data_date)
    if when is None:
        raise ValueError('Provide an ISO calendar data date.')
    activities_by_id = _rows_by_id(baseline_snapshot.get('activities') or [], 'id', issues, 'baseline_activity')
    activities = [row for row in activities_by_id.values() if row is not None]
    policies = _rows_by_id(policy_definition.get('activities') or [], 'activity_id', issues, 'earning_policy')
    observed = _rows_by_id(observations or [], 'activity_id', issues, 'observation')
    for label, rows in [('earning_policy', policies), ('observation', observed)]:
        for key in rows.keys() - activities_by_id.keys():
            _issue(issues, f'{label}_outside_baseline', f'This {label} does not identify an activity in the exact frozen baseline.', key, severity='error')
    inputs = baseline_snapshot.get('accepted_inputs') or {}
    default_calendar_id = inputs.get('default_calendar_id')
    calendars = {}
    for value in inputs.get('calendars') or []:
        identifier = value.get('id')
        if identifier in calendars:
            calendars[identifier] = None
            _issue(issues, 'frozen_calendar_duplicate', 'A frozen calendar ID is duplicated.', severity='error')
            continue
        try:
            calendars[identifier] = FrozenCalendar(value, when + timedelta(days=1))
        except (ValueError, OverflowError) as exc:
            calendars[identifier] = None
            _issue(issues, 'frozen_calendar_unavailable', str(exc))
    currency = policy_definition.get('currency')
    currency_valid = isinstance(currency, str) and bool(currency.strip())
    if not currency_valid:
        currency = None
        _issue(issues, 'currency_not_specified', 'Approve the reporting currency before publishing monetary metrics.')
    comparisons, budgets, weights, fractions, planned_fractions, planned_values = [], [], [], [], [], []
    for activity in activities:
        identifier = activity['id']
        policy, observation = policies.get(identifier), observed.get(identifier)
        budget = _decimal((policy or {}).get('budget'))
        weight = _decimal((policy or {}).get('weight'))
        if budget is not None and budget < ZERO:
            budget = None
        if weight is not None and weight < ZERO:
            weight = None
        budgets.append(budget)
        weights.append(weight)
        fraction = _progress(policy, observation, issues, identifier)
        # Malformed/future actual dates must not earn progress even if a method
        # could otherwise produce 100% from the mere presence of a finish.
        if observation:
            start, finish = _date(observation.get('actual_start')), _date(observation.get('actual_finish'))
            if (any(observation.get(key) is not None and _date(observation[key]) is None for key in ('actual_start', 'actual_finish'))
                    or start and start > when or finish and finish > when or start and finish and finish < start):
                fraction = None
                _issue(issues, 'actual_dates_invalid', 'Actual dates must be valid, ordered and no later than the data date.', identifier, severity='error')
        fractions.append(fraction)
        calendar = calendars.get(activity.get('calendar', activity.get('calendar_id')) or default_calendar_id)
        try:
            planned, value, reason = _planned_fraction(activity, policy, when, budget, calendar)
        except (ValueError, OverflowError) as exc:
            planned, value, reason = None, None, 'planned_calendar_unsupported'
        planned_fractions.append(planned)
        planned_values.append(value)
        if reason:
            _issue(issues, reason, 'Planned value is unavailable until the approved time-phasing inputs are complete.', identifier)
        comparisons.append({'activity_id': identifier, 'external_id': activity.get('external_id'), 'name': activity.get('name'),
            'baseline_start': activity.get('planned_start'), 'baseline_finish': activity.get('planned_finish'),
            'budget': _rounded(budget) if currency_valid else None, 'weight': weight,
            'physical_progress_pct': _rounded(fraction * HUNDRED) if fraction is not None else None,
            'planned_progress_pct': _rounded(planned * HUNDRED) if planned is not None else None,
            'planned_value': _rounded(value) if currency_valid else None,
            'earned_value': _rounded(budget * fraction) if currency_valid and budget is not None and fraction is not None else None})
    valid_scope = bool(activities) and not any(item['code'].endswith(('invalid_identity', 'duplicate_identity', 'outside_baseline')) for item in issues)
    money_ready = valid_scope and currency_valid and all(value is not None for value in budgets)
    progress_ready = valid_scope and all(value is not None for value in fractions)
    weight_ready = valid_scope and all(value is not None for value in weights) and sum(weights, ZERO) > ZERO
    pv_ready = money_ready and all(value is not None for value in planned_values)
    ev_ready = money_ready and progress_ready
    bac = sum(budgets, ZERO) if money_ready else None
    pv = sum(planned_values, ZERO) if pv_ready else None
    ev = sum((budget * fraction for budget, fraction in zip(budgets, fractions)), ZERO) if ev_ready else None
    raw_costs = (actuals or {}).get('costs_by_currency') or {}
    costs = {key: _decimal(value) for key, value in raw_costs.items()} if isinstance(raw_costs, dict) else {}
    coverage = (actuals or {}).get('coverage') or {}
    cost_exclusions = {key: value for key, value in (coverage.get('costs') or {}).items()
                       if key not in {'seen', 'included', 'deleted', 'future', 'reversed', 'not_actual'} and value}
    hour_exclusions = {key: value for key, value in (coverage.get('hours') or {}).items()
                       if key not in {'seen', 'included', 'deleted', 'future', 'reversed'} and value}
    invalid_cost_source = any(row.get('code', '').startswith(('cost_', 'hour_'))
        or row.get('code') in {'approved_hours_not_posted', 'labor_rate_unconfirmed', 'labor_cost_inconsistent', 'enterprise_project_not_linked'}
        for row in (actuals or {}).get('issues') or [] if isinstance(row, dict))
    ac_reason = None
    if not cost_coverage_confirmed:
        ac_reason = 'cost_coverage_not_confirmed'
    elif not currency_valid:
        ac_reason = 'currency_not_specified'
    elif cost_exclusions or hour_exclusions or invalid_cost_source:
        ac_reason = 'actual_cost_source_coverage_incomplete'
    elif any(key != currency for key in costs):
        ac_reason = 'actual_cost_currency_mismatch'
    elif currency not in costs or costs[currency] is None:
        ac_reason = 'actual_cost_not_reported'
    ac = None if ac_reason else costs[currency]
    if ac_reason:
        _issue(issues, ac_reason, 'Actual cost requires confirmed complete coverage in the approved currency; no foreign-exchange conversion or missing-cost zero is applied.')
    progress = sum((weight * fraction for weight, fraction in zip(weights, fractions)), ZERO) / sum(weights, ZERO) * HUNDRED if weight_ready and progress_ready else None
    planned_progress = sum((weight * fraction for weight, fraction in zip(weights, planned_fractions)), ZERO) / sum(weights, ZERO) * HUNDRED if weight_ready and all(item is not None for item in planned_fractions) else None
    spi = ev / pv if ev is not None and pv is not None and pv > ZERO else None
    cpi = ev / ac if ev is not None and ac is not None and ac > ZERO else None
    eac = bac * ac / ev if bac is not None and ac is not None and ac > ZERO and ev is not None and ev > ZERO else None
    values = {'bac': bac, 'planned_value': pv, 'earned_value': ev, 'actual_cost': ac,
              'progress_pct': progress, 'planned_progress_pct': planned_progress,
              'schedule_variance': ev - pv if ev is not None and pv is not None else None,
              'cost_variance': ev - ac if ev is not None and ac is not None else None,
              'spi': spi, 'cpi': cpi, 'eac': eac,
              'etc': eac - ac if eac is not None else None, 'vac': bac - eac if eac is not None else None}
    reasons = {}
    for key, value in values.items():
        if value is not None:
            continue
        reason = []
        if not valid_scope:
            reason.append('baseline_scope_incomplete')
        if key in {'progress_pct', 'planned_progress_pct'}:
            if not weight_ready:
                reason.append('approved_weights_incomplete')
        else:
            if not currency_valid:
                reason.append('currency_not_specified')
            if not money_ready:
                reason.append('approved_budgets_incomplete')
        if key in {'earned_value', 'progress_pct', 'schedule_variance', 'cost_variance', 'spi', 'cpi', 'eac', 'etc', 'vac'} and not progress_ready:
            reason.append('progress_observations_incomplete')
        if key in {'planned_value', 'planned_progress_pct', 'schedule_variance', 'spi'} and not all(item is not None for item in planned_values):
            reason.append('planned_value_incomplete')
        if key in {'actual_cost', 'cost_variance', 'cpi', 'eac', 'etc', 'vac'} and ac_reason:
            reason.append(ac_reason)
        if key == 'spi' and pv == ZERO:
            reason.append('planned_value_zero')
        if key in {'cpi', 'eac', 'etc', 'vac'} and ac is not None and ac <= ZERO:
            reason.append('actual_cost_zero' if ac == ZERO else 'actual_cost_nonpositive')
        if key in {'eac', 'etc', 'vac'} and ev == ZERO:
            reason.append('earned_value_zero')
        reasons[key] = reason or ['required_inputs_incomplete']
    metrics = {key: _rounded(value, '0.0001' if key in {'spi', 'cpi'} else '0.01') for key, value in values.items()}
    metrics.update(currency=currency, data_date=when, null_reasons=reasons,
        coverage={'activity_count': len(activities), 'reported_activity_count': sum(row is not None for key, row in observed.items() if key in activities_by_id),
                  'earning_measurement_count': sum(value is not None for value in fractions),
                  'budgeted_activity_count': sum(value is not None for value in budgets), 'cost_coverage_confirmed': bool(cost_coverage_confirmed)},
        formulas={'PV': 'Sum of approved activity time-phased budgets', 'EV': 'Sum of approved activity budget × measured earning fraction',
                  'progress_pct': 'Sum of approved activity weight × measured fraction / sum of approved weights × 100',
                  'SPI': 'EV / PV', 'CPI': 'EV / AC', 'EAC': 'BAC × AC / EV (unrounded inputs)'})
    # Baseline PV curve only. Historical EV/AC must come from sealed observations,
    # never from projecting this report's cumulative values into earlier weeks.
    dates = {_date(row.get('planned_start')) for row in activities} | {_date(row.get('planned_finish')) for row in activities}
    dates.discard(None)
    for policy in policies.values():
        dates.update(_date(point.get('date')) for point in (policy or {}).get('planned_value') or [])
    dates.discard(None)
    if dates and (max(dates) - min(dates)).days <= MAX_DAYS:
        cursor, finish = min(dates), max(dates)
        while cursor < finish:
            dates.add(cursor)
            cursor += timedelta(days=7)
    dates.add(when)
    curve = []
    for curve_date in sorted(dates):
        point_values = []
        for activity, budget in zip(activities, budgets):
            try:
                _, value, _ = _planned_fraction(activity, policies.get(activity['id']), curve_date, budget,
                    calendars.get(activity.get('calendar', activity.get('calendar_id')) or default_calendar_id))
            except (ValueError, OverflowError):
                value = None
            point_values.append(value)
        curve.append({'date': curve_date, 'planned_value': _rounded(sum(point_values, ZERO)) if money_ready and all(value is not None for value in point_values) else None})
    metrics['planned_curve'] = curve
    try:
        forecast = _forecast(activities, observed, calendars, default_calendar_id,
                             {**inputs, '_relationships': baseline_snapshot.get('relationships') or []}, when, issues)
    except (ValueError, OverflowError) as exc:
        _issue(issues, 'forecast_calendar_horizon_unsupported', str(exc))
        forecast = {'status': 'unavailable', 'method': 'retained_logic_whole_working_days', 'data_date': when,
                    'contractual_finish': inputs.get('project_finish'), 'forecast_finish': None, 'activities': []}
    if not valid_scope:
        forecast.update(status='unavailable', forecast_finish=None, finish_variance_calendar_days=None, critical_activity_ids=[])
        for row in forecast['activities']:
            row.update(forecast_start=None, forecast_finish=None, remaining_start=None, remaining_finish=None,
                       total_float_days=None, is_critical=None, status='unavailable')
            row['unavailable_reasons'].append('baseline_scope_incomplete')
    forecast_rows = {row['activity_id']: row for row in forecast['activities']}
    for row in comparisons:
        current = forecast_rows.get(row['activity_id'], {})
        row.update({key: current.get(key) for key in ('actual_start', 'actual_finish', 'forecast_start', 'forecast_finish', 'remaining_start', 'remaining_finish', 'total_float_days', 'is_critical')})
        for field in ('actual_start', 'actual_finish'):
            row[field] = (observed.get(row['activity_id']) or {}).get(field)
        for label in ('start', 'finish'):
            before, after = _date(row[f'baseline_{label}']), _date(row[f'forecast_{label}'])
            row[f'{label}_variance_calendar_days'] = (after - before).days if before and after else None
        row['unavailable_reasons'] = current.get('unavailable_reasons', ['forecast_unavailable'])
    return _json({'metrics': metrics, 'activity_comparisons': comparisons, 'forecast': forecast, 'issues': issues})
