"""A deliberately bounded MSPDI writer, with fail-closed input preservation.

The XML is checked by libxml2 against our supported-subset XSD, then parsed
independently of the stdlib writer and compared with the input projection.
Neither check substitutes for a Microsoft Project application round-trip.
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import date, time, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

from .schedule_export_contract import ScheduleExportError, validate_export_model

NS = 'http://schemas.microsoft.com/project'
RELATION_TYPES = {'FF': 0, 'FS': 1, 'SF': 2, 'SS': 3}
CONSTRAINT_TYPES = {'none': 0, 'must_start': 2, 'must_finish': 3,
                    'start_no_earlier': 4, 'start_no_later': 5, 'finish_no_later': 7}
SCHEMA_PATH = Path(__file__).with_name('schemas') / 'mspdi-supported-subset-1.xsd'
SIDECAR_FIELDS = ['Original database IDs and activity IDs', 'Start/finish milestone distinction',
                  'Resource capacity, unit costs, assignment quantities and budgeted costs',
                  'Calendar timezone and unreferenced project calendars',
                  'Source evidence, review decisions and baseline approval provenance']
ET.register_namespace('', NS)


def _error(code, message, entity=None, field=None):
    raise ScheduleExportError(message, code='mspdi_unsupported_semantics', issues=[{
        'code': code, 'message': message, 'entity_id': entity, 'field': field, 'severity': 'error'}])


def _number(value, entity, field, *, nonnegative=False):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or (nonnegative and result < 0):
            raise ValueError
        return result
    except (ValueError, TypeError, InvalidOperation):
        _error('mspdi_numeric_input', 'A finite numeric input is required.', entity, field)


def _integer(value, entity, field):
    result = _number(value, entity, field)
    if result != result.to_integral_value():
        _error('mspdi_precision_unsupported', 'The value cannot be represented exactly in the target time unit.', entity, field)
    return int(result)


def _date(value, entity, field):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        _error('mspdi_date_required', 'A valid explicit ISO calendar date is required.', entity, field)


def _text(value, entity, field, max_length=None):
    text = str(value or '')
    if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]', text):
        _error('mspdi_invalid_xml_text', 'Text contains a character XML 1.0 cannot preserve.', entity, field)
    if max_length and len(text) > max_length:
        _error('mspdi_text_length', 'The text exceeds the supported native field length; it was not truncated.', entity, field)
    return text


def _index(rows, kind):
    result = {}
    for row in rows:
        key = row.get('id')
        if key is None or key in result:
            _error('mspdi_duplicate_identity', f'Missing or duplicate {kind} identity.', key)
        result[key] = row
    return result


def _intervals(raw, entity, field):
    if not isinstance(raw, list) or not 1 <= len(raw) <= 5:
        _error('mspdi_working_times_required', 'Provide one to five explicit working-time intervals; no shifts are assumed.', entity, field)
    pairs = []
    for interval in raw:
        if not isinstance(interval, dict):
            _error('mspdi_calendar_interval', 'Working intervals must contain from and to clock times.', entity, field)
        a, b = interval.get('from'), interval.get('to')
        try:
            if not all(isinstance(v, str) and re.fullmatch(r'\d{2}:\d{2}:\d{2}', v) for v in (a, b)):
                raise ValueError
            start, finish = time.fromisoformat(a), time.fromisoformat(b)
            if start >= finish or (pairs and a < pairs[-1][1]):
                raise ValueError
        except (TypeError, ValueError):
            _error('mspdi_calendar_interval', 'Intervals must be ordered, nonoverlapping, within one day, and use HH:MM:SS.', entity, field)
        pairs.append((a, b))
    return pairs


def _seconds(intervals):
    def clock(value):
        hh, mm, ss = map(int, value.split(':'))
        return hh * 3600 + mm * 60 + ss
    return sum(clock(finish) - clock(start) for start, finish in intervals)


def _calendar(row):
    key = row['id']
    weekdays = row.get('working_weekdays')
    if (not isinstance(weekdays, list) or not weekdays or len(set(weekdays)) != len(weekdays)
            or any(type(day) is not int or day not in range(7) for day in weekdays)):
        _error('mspdi_calendar_weekdays', 'Specify unique ISO working weekdays (Monday 0 to Sunday 6).', key)
    hours = _number(row.get('hours_per_day'), key, 'hours_per_day', nonnegative=True)
    if not 0 < hours <= 24:
        _error('mspdi_calendar_hours', 'Working hours per day must be greater than zero and at most 24.', key)
    minutes = _integer(hours * 60, key, 'hours_per_day')
    raw = row.get('working_times')
    if not isinstance(raw, dict):
        raw = {}
    if any(str(day) not in {str(d) for d in weekdays} and intervals for day, intervals in raw.items()):
        _error('mspdi_calendar_nonworking_shift', 'A nonworking weekday contains working intervals.', key)
    week = {}
    for weekday in weekdays:
        intervals = _intervals(raw.get(str(weekday), raw.get(weekday)), key, f'working_times.{weekday}')
        if _seconds(intervals) != hours * 3600:
            _error('mspdi_calendar_hours_mismatch', 'Working intervals do not equal the stated hours per day.', key)
        week[weekday] = intervals
    exceptions = {}
    for exception in row.get('exceptions') or []:
        day = _date(exception.get('date'), key, 'exception.date')
        if day in exceptions:
            _error('mspdi_calendar_duplicate_exception', 'Multiple exceptions apply to the same date.', key)
        if type(exception.get('is_working')) is not bool:
            _error('mspdi_calendar_exception_status', 'An exception must explicitly be working or nonworking.', key)
        pairs = _intervals(exception.get('working_times'), key, 'exception.working_times') if exception['is_working'] else []
        if not exception['is_working'] and exception.get('working_times'):
            _error('mspdi_calendar_nonworking_shift', 'A nonworking exception contains working intervals.', key)
        if exception.get('working_hours') is not None:
            expected = _number(exception['working_hours'], key, 'exception.working_hours', nonnegative=True) * 3600
            if expected != _seconds(pairs):
                _error('mspdi_calendar_hours_mismatch', 'Exception intervals do not equal its stated hours.', key)
        exceptions[day] = {'times': pairs, 'name': _text(exception.get('name'), key, 'exception.name', 255)}
    return {'row': row, 'minutes': minutes, 'week': week, 'exceptions': exceptions}


def _day_times(calendar, day):
    if day in calendar['exceptions']:
        return calendar['exceptions'][day]['times']
    return calendar['week'].get(day.weekday(), [])


def _instant(calendar, day, finish, entity):
    intervals = _day_times(calendar, day)
    if not intervals:
        _error('mspdi_nonworking_endpoint', 'An activity or constraint endpoint falls on a nonworking date; it was not shifted.', entity)
    return f'{day.isoformat()}T{intervals[-1][1] if finish else intervals[0][0]}'


def _duration(seconds):
    return f'PT{int(seconds)}S'


def _preflight(snapshot):
    issues = validate_export_model(snapshot)
    if issues:
        raise ScheduleExportError('Invalid schedule references or values.', code='schedule_export_invalid', issues=issues)
    readiness = snapshot.get('readiness') or {}
    baseline = snapshot.get('export_state') == 'approved_baseline'
    if baseline:
        if not snapshot.get('baseline_id') or not snapshot.get('baseline_approved_at') or not snapshot.get('traceability'):
            _error('mspdi_baseline_manifest_missing', 'An approved baseline requires its frozen input and approval manifest.')
    elif ((snapshot.get('version') or {}).get('status') not in {'calculated', 'approved'}
          or snapshot.get('calculation_status') == 'blocked' or readiness.get('ready_for_export') is False):
        _error('mspdi_schedule_not_ready', 'Calculate the current accepted plan and resolve export-blocking issues first. JSON and Excel remain available for review.')
    activities = _index(snapshot.get('activities') or [], 'activity')
    if not activities:
        _error('mspdi_empty_schedule', 'There are no calculated activities to export.')
    wbs = _index(snapshot.get('wbs') or [], 'WBS')
    default = (snapshot.get('calendar') or {}).get('id')
    referenced_calendars = {default} | {row.get('calendar') for row in activities.values() if row.get('calendar') is not None}
    calendars = {key: _calendar(row) for key, row in _index(snapshot.get('calendars') or [], 'calendar').items()
                 if key in referenced_calendars}
    if default not in calendars:
        _error('mspdi_default_calendar_missing', 'An explicit default calendar is required.')
    minutes = calendars[default]['minutes']
    if any(cal['minutes'] != minutes for cal in calendars.values()):
        _error('mspdi_mixed_day_units', 'Calendars use different hours per day; day-valued duration, slack and lag cannot share one Project conversion.')
    if len({cal['row'].get('timezone') for cal in calendars.values()}) > 1:
        _error('mspdi_mixed_timezones', 'Different calendar timezones cannot be represented as one local Project timeline without a timezone conversion policy.')
    for key, row in wbs.items():
        seen, current = {key}, row.get('parent')
        while current is not None:
            if current not in wbs or current in seen:
                _error('mspdi_wbs_hierarchy', 'The WBS has a missing parent or a cycle.', key)
            seen.add(current)
            current = wbs[current].get('parent')
    endpoints = {}
    for key, row in activities.items():
        if row.get('wbs_node') is not None and row['wbs_node'] not in wbs:
            _error('mspdi_wbs_reference', 'The activity WBS is missing.', key)
        kind = row.get('activity_type')
        if kind not in {'task', 'start_milestone', 'finish_milestone'}:
            _error('mspdi_activity_type_unsupported', 'Only tasks and explicit start/finish milestones are supported; level of effort is not translated.', key)
        calendar = calendars.get(row.get('calendar') or default)
        if calendar is None:
            _error('mspdi_activity_calendar_missing', 'The activity calendar is missing.', key)
        start, finish = (_date(row.get(field), key, field) for field in ('planned_start', 'planned_finish'))
        days = _number(row.get('duration_days'), key, 'duration_days', nonnegative=True)
        seconds = _integer(days * minutes * 60, key, 'duration_days')
        if kind != 'task' and (seconds != 0 or start != finish):
            _error('mspdi_milestone_dates', 'A milestone requires zero duration and identical start and finish dates.', key)
        start_time = _instant(calendar, start, kind == 'finish_milestone', key)
        finish_time = start_time if kind != 'task' else _instant(calendar, finish, True, key)
        if kind == 'task':
            if (finish - start).days > 36600:
                _error('mspdi_date_span_limit', 'The supported export validation range is 100 years per activity.', key)
            actual = sum(_seconds(_day_times(calendar, start + timedelta(days=offset))) for offset in range((finish - start).days + 1))
            if seconds != actual:
                _error('mspdi_duration_date_mismatch', 'Duration differs from the explicit working time between the date-only endpoints; intraday times cannot be guessed.', key, 'duration_days')
        constraint = row.get('constraint_type') or 'none'
        if constraint not in CONSTRAINT_TYPES:
            _error('mspdi_constraint_unsupported', 'The activity constraint has no supported exact mapping.', key)
        constraint_at = None
        if constraint != 'none':
            constraint_at = _instant(calendar, _date(row.get('constraint_date'), key, 'constraint_date'),
                                     constraint in {'must_finish', 'finish_no_later'}, key)
        endpoints[key] = {'start': start_time, 'finish': finish_time, 'seconds': seconds, 'constraint': constraint_at}
        for field in ('total_float_days', 'free_float_days'):
            if row.get(field) is not None:
                _integer(_number(row[field], key, field) * minutes * 10, key, field)
    adjacency, degree, seen_links = defaultdict(list), {key: 0 for key in activities}, set()
    for row in snapshot.get('relationships') or []:
        _integer(_number(row.get('lag_days'), row.get('id'), 'lag_days') * minutes * 10, row.get('id'), 'lag_days')
        if row['predecessor'] == row['successor']:
            _error('mspdi_relationship_cycle', 'An activity cannot depend on itself.', row.get('id'))
        identity = (row['predecessor'], row['successor'], row['relationship_type'])
        if identity in seen_links:
            _error('mspdi_relationship_duplicate', 'Duplicate typed relationship endpoints are ambiguous.', row.get('id'))
        seen_links.add(identity)
        adjacency[row['predecessor']].append(row['successor'])
        degree[row['successor']] += 1
    pending, visited = deque(key for key, count in degree.items() if count == 0), 0
    while pending:
        current = pending.popleft()
        visited += 1
        for successor in adjacency[current]:
            degree[successor] -= 1
            if degree[successor] == 0:
                pending.append(successor)
    if visited != len(activities):
        _error('mspdi_relationship_cycle', 'The relationship graph contains a cycle; no links were removed.')
    resources = _index(snapshot.get('resources') or [], 'resource')
    assignments = _index(snapshot.get('assignments') or [], 'assignment')
    for key, row in resources.items():
        if row.get('resource_type') not in {'labor', 'equipment', 'material'}:
            _error('mspdi_resource_type_unsupported', 'The resource type is unsupported.', key)
    for key, row in assignments.items():
        if row.get('activity') not in activities or row.get('resource') not in resources:
            _error('mspdi_assignment_reference', 'An assignment references a missing task or resource.', key)
        if resources[row['resource']]['resource_type'] == 'material':
            _error('mspdi_material_assignment_unsupported', 'Material assignment quantity/rate semantics need a separate adapter; no work-hour substitution is made.', key)
        _integer(_number(row.get('budgeted_hours'), key, 'budgeted_hours', nonnegative=True) * 3600, key, 'budgeted_hours')
    return activities, wbs, calendars, default, minutes, endpoints, resources, assignments


def _add(parent, name, value=None):
    node = ET.SubElement(parent, f'{{{NS}}}{name}')
    if value is not None:
        node.text = str(value)
    return node


def _working_times(parent, pairs):
    if pairs:
        collection = _add(parent, 'WorkingTimes')
        for start, finish in pairs:
            row = _add(collection, 'WorkingTime')
            _add(row, 'FromTime', start)
            _add(row, 'ToTime', finish)


def build_mspdi(snapshot):
    """Return (XML bytes, provenance dict, verification report); never change inputs."""
    activities, wbs, calendars, default, minutes, endpoints, resources, assignments = _preflight(snapshot)
    calendar_uids = {key: index for index, key in enumerate(calendars, 1)}
    resource_uids = {key: index for index, key in enumerate(resources, 1)}
    task_uids = {key: index for index, key in enumerate(activities, len(wbs) + 1)}
    wbs_uids = {key: index for index, key in enumerate(wbs, 1)}
    root = ET.Element(f'{{{NS}}}Project')
    _add(root, 'SaveVersion', 12)
    name = (snapshot.get('schedule') or {}).get('name') or (snapshot.get('project') or {}).get('name')
    _add(root, 'Name', _text(name, None, 'schedule.name', 255))
    _add(root, 'ScheduleFromStart', 1)
    _add(root, 'StartDate', min(value['start'] for value in endpoints.values()))
    _add(root, 'FinishDate', max(value['finish'] for value in endpoints.values()))
    _add(root, 'CalendarUID', calendar_uids[default])
    _add(root, 'MinutesPerDay', minutes)
    _add(root, 'MinutesPerWeek', minutes * len(calendars[default]['week']))
    _add(root, 'DefaultTaskType', 1)
    _add(root, 'DurationFormat', 7)
    _add(root, 'NewTasksEffortDriven', 0)
    _add(root, 'NewTasksEstimated', 0)
    container = _add(root, 'Calendars')
    for key, calendar in calendars.items():
        row = _add(container, 'Calendar')
        _add(row, 'UID', calendar_uids[key])
        _add(row, 'Name', _text(calendar['row'].get('name'), key, 'calendar.name', 255))
        _add(row, 'IsBaseCalendar', 1)
        _add(row, 'BaseCalendarUID', -1)
        week = _add(row, 'WeekDays')
        for native_day in range(1, 8):
            day = (native_day + 5) % 7
            pairs = calendar['week'].get(day, [])
            child = _add(week, 'WeekDay')
            _add(child, 'DayType', native_day)
            _add(child, 'DayWorking', int(bool(pairs)))
            _working_times(child, pairs)
        if calendar['exceptions']:
            exceptions = _add(row, 'Exceptions')
            for day, value in sorted(calendar['exceptions'].items()):
                child = _add(exceptions, 'Exception')
                _add(child, 'EnteredByOccurrences', 0)
                period = _add(child, 'TimePeriod')
                _add(period, 'FromDate', f'{day}T00:00:00')
                _add(period, 'ToDate', f'{day}T23:59:59')
                _add(child, 'Occurrences', 1)
                _add(child, 'Name', value['name'])
                _add(child, 'Type', 1)
                _add(child, 'DayWorking', int(bool(value['times'])))
                _working_times(child, value['times'])
    tasks = _add(root, 'Tasks')
    children, member_tasks = defaultdict(list), defaultdict(list)
    for key, row in wbs.items():
        children[row.get('parent')].append(key)
    for key, row in activities.items():
        member_tasks[row.get('wbs_node')].append(key)
    inbound = defaultdict(list)
    for link in snapshot.get('relationships') or []:
        inbound[link['successor']].append(link)
    expected_tasks = {}
    counter = 0

    def emit(parent, outline=''):
        nonlocal counter
        entries = [('wbs', key) for key in children[parent]] + [('activity', key) for key in member_tasks[parent]]
        for number, (kind, key) in enumerate(entries, 1):
            counter += 1
            path = f'{outline}.{number}' if outline else str(number)
            source = wbs[key] if kind == 'wbs' else activities[key]
            uid = wbs_uids[key] if kind == 'wbs' else task_uids[key]
            row = _add(tasks, 'Task')
            _add(row, 'UID', uid)
            _add(row, 'ID', counter)
            _add(row, 'Name', _text(source.get('name'), key, 'name', 512))
            _add(row, 'Type', 1)
            _add(row, 'WBS', _text(source.get('code') if kind == 'wbs' else source.get('external_id'), key, 'code', 512))
            _add(row, 'OutlineNumber', path)
            _add(row, 'OutlineLevel', path.count('.') + 1)
            if kind == 'wbs':
                _add(row, 'Summary', 1)
                expected_tasks[str(uid)] = {'source_kind': kind, 'source_id': key, 'name': source['name'],
                                            'code': source['code'], 'outline': path, 'parent': parent}
                emit(key, path)
                continue
            points = endpoints[key]
            _add(row, 'Start', points['start'])
            _add(row, 'Finish', points['finish'])
            _add(row, 'Duration', _duration(points['seconds']))
            _add(row, 'DurationFormat', 7)
            _add(row, 'EffortDriven', 0)
            _add(row, 'Estimated', 0)
            _add(row, 'Milestone', int(source['activity_type'] != 'task'))
            _add(row, 'Summary', 0)
            if source.get('is_critical') is not None:
                _add(row, 'Critical', int(bool(source['is_critical'])))
            for field, native in [('free_float_days', 'FreeSlack'), ('total_float_days', 'TotalSlack')]:
                if source.get(field) is not None:
                    _add(row, native, int(Decimal(str(source[field])) * minutes * 10))
            _add(row, 'ConstraintType', CONSTRAINT_TYPES[source.get('constraint_type') or 'none'])
            _add(row, 'CalendarUID', calendar_uids[source.get('calendar') or default])
            if points['constraint']:
                _add(row, 'ConstraintDate', points['constraint'])
            _add(row, 'Notes', json.dumps({'radai_activity_id': key, 'external_id': source['external_id'],
                                         'activity_type': source['activity_type'], 'provenance': 'radai-provenance.json'}, ensure_ascii=False))
            for link in inbound[key]:
                predecessor = _add(row, 'PredecessorLink')
                _add(predecessor, 'PredecessorUID', task_uids[link['predecessor']])
                _add(predecessor, 'Type', RELATION_TYPES[link['relationship_type']])
                _add(predecessor, 'CrossProject', 0)
                _add(predecessor, 'LinkLag', int(Decimal(str(link['lag_days'])) * minutes * 10))
                _add(predecessor, 'LagFormat', 7)
            if snapshot.get('export_state') == 'approved_baseline':
                baseline = _add(row, 'Baseline')
                _add(baseline, 'Number', 0)
                _add(baseline, 'Start', points['start'])
                _add(baseline, 'Finish', points['finish'])
                _add(baseline, 'Duration', _duration(points['seconds']))
                _add(baseline, 'DurationFormat', 7)
            expected_tasks[str(uid)] = {'source_kind': kind, 'source_id': key, 'name': source['name'],
                                        'code': source['external_id'], 'outline': path, 'parent': parent}
    emit(None)
    container = _add(root, 'Resources')
    for key, source in resources.items():
        row = _add(container, 'Resource')
        _add(row, 'UID', resource_uids[key])
        _add(row, 'ID', resource_uids[key])
        _add(row, 'Name', _text(source.get('name'), key, 'resource.name', 255))
        _add(row, 'Type', 0 if source['resource_type'] == 'material' else 1)
        if source['resource_type'] == 'material':
            _add(row, 'MaterialLabel', _text(source.get('unit'), key, 'resource.unit', 255))
        _add(row, 'Code', _text(source.get('code'), key, 'resource.code', 255))
        _add(row, 'Notes', 'Original resource type, capacity and costing: radai-provenance.json')
    container = _add(root, 'Assignments')
    for number, (key, source) in enumerate(assignments.items(), 1):
        row = _add(container, 'Assignment')
        _add(row, 'UID', number)
        _add(row, 'TaskUID', task_uids[source['activity']])
        _add(row, 'ResourceUID', resource_uids[source['resource']])
        _add(row, 'Notes', 'Original assignment quantity and budget cost: radai-provenance.json')
        _add(row, 'Work', _duration(Decimal(str(source['budgeted_hours'])) * 3600))
    content = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    mapping = {'tasks': expected_tasks, 'calendars': {str(uid): key for key, uid in calendar_uids.items()},
               'resources': {str(uid): key for key, uid in resource_uids.items()},
               'assignments': {str(i): key for i, key in enumerate(assignments, 1)}}
    report = verify_mspdi(content, snapshot, mapping)
    provenance = {'format': 'RADAI MSPDI provenance 1.0', 'xml_sha256': sha256(content).hexdigest(),
                  'identity_mapping': mapping, 'sidecar_only_fields': SIDECAR_FIELDS, 'snapshot': snapshot}
    return content, provenance, report


def verify_mspdi(content, snapshot, mapping):
    """Independent libxml2 parse and XSD validation, then source-value comparison."""
    try:
        from lxml import etree
    except ImportError:
        _error('mspdi_validator_unavailable', 'Install the XML schema validator before generating a verified interchange file.')
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    try:
        root = etree.fromstring(content, parser)
        schema = etree.XMLSchema(etree.parse(str(SCHEMA_PATH), parser))
        schema.assertValid(root)
    except (etree.XMLSyntaxError, etree.DocumentInvalid, etree.XMLSchemaParseError) as exc:
        raise ScheduleExportError('Generated XML failed supported-subset schema validation.', code='mspdi_validation_failed') from exc
    ns = {'p': NS}
    def value(row, name):
        return row.findtext(f'p:{name}', namespaces=ns)
    def check(actual, expected, field):
        if actual != expected:
            raise ScheduleExportError(f'XML input preservation failed for {field}.', code='mspdi_preservation_failed')
    activities = {row['id']: row for row in snapshot['activities']}
    calendars = {row['id']: row for row in snapshot['calendars']}
    minutes = Decimal(str(snapshot['calendar']['hours_per_day'])) * 60
    tasks = root.findall('p:Tasks/p:Task', ns)
    check(len(tasks), len(mapping['tasks']), 'task count')
    by_uid = {value(row, 'UID'): row for row in tasks}
    check(len(by_uid), len(tasks), 'unique task UIDs')
    check(value(root, 'MinutesPerDay'), str(int(minutes)), 'day unit conversion')
    check(mapping['calendars'][value(root, 'CalendarUID')], snapshot['calendar']['id'], 'default calendar')
    check(value(root, 'StartDate'), min(value(row, 'Start') for row in tasks if value(row, 'Summary') == '0'), 'project envelope start')
    check(value(root, 'FinishDate'), max(value(row, 'Finish') for row in tasks if value(row, 'Summary') == '0'), 'project envelope finish')
    parent_by_outline = {value(by_uid[uid], 'OutlineNumber'): item['source_id'] for uid, item in mapping['tasks'].items()
                         if item['source_kind'] == 'wbs'}
    actual_links = []
    reverse_types = {str(number): name for name, number in RELATION_TYPES.items()}
    for uid, identity in mapping['tasks'].items():
        row = by_uid[uid]
        check(value(row, 'Name') or '', identity['name'], 'task name')
        check(value(row, 'WBS'), identity['code'], 'original activity/WBS code')
        check(value(row, 'OutlineNumber'), identity['outline'], 'WBS hierarchy')
        parent_outline = (value(row, 'OutlineNumber') or '').rpartition('.')[0]
        actual_parent = parent_by_outline.get(parent_outline)
        check(actual_parent, identity['parent'], 'WBS parent reference')
        check(value(row, 'Summary'), '1' if identity['source_kind'] == 'wbs' else '0', 'task type')
        if identity['source_kind'] == 'wbs':
            continue
        source = activities[identity['source_id']]
        check(value(row, 'Start')[:10], source['planned_start'], 'start date')
        check(value(row, 'Finish')[:10], source['planned_finish'], 'finish date')
        calendar_source = calendars[source.get('calendar') or snapshot['calendar']['id']]
        def explicit_boundary(day, finishing):
            override = next((item for item in calendar_source.get('exceptions') or [] if item['date'] == day), None)
            shifts = (override['working_times'] if override is not None else
                      calendar_source['working_times'].get(str(date.fromisoformat(day).weekday()),
                                                           calendar_source['working_times'].get(date.fromisoformat(day).weekday())))
            return day + 'T' + (shifts[-1]['to'] if finishing else shifts[0]['from'])
        check(value(row, 'Start'), explicit_boundary(source['planned_start'], source['activity_type'] == 'finish_milestone'), 'start working-time boundary')
        check(value(row, 'Finish'), explicit_boundary(source['planned_finish'], source['activity_type'] != 'start_milestone'), 'finish working-time boundary')
        check(value(row, 'Duration'), f"PT{int(Decimal(str(source['duration_days'])) * minutes * 60)}S", 'duration')
        check(value(row, 'DurationFormat'), '7', 'duration display unit')
        check(value(row, 'Type'), '1', 'fixed-duration interchange policy')
        check(value(row, 'EffortDriven'), '0', 'non-effort-driven interchange policy')
        check(value(row, 'Milestone'), '0' if source['activity_type'] == 'task' else '1', 'milestone type')
        notes = json.loads(value(row, 'Notes'))
        check(notes['radai_activity_id'], source['id'], 'original activity identity')
        check(notes['external_id'], source['external_id'], 'original external activity identity')
        check(notes['activity_type'], source['activity_type'], 'start/finish milestone distinction')
        check(mapping['calendars'][value(row, 'CalendarUID')], source.get('calendar') or snapshot['calendar']['id'], 'task calendar')
        for native, field in [('FreeSlack', 'free_float_days'), ('TotalSlack', 'total_float_days')]:
            expected = None if source.get(field) is None else str(int(Decimal(str(source[field])) * minutes * 10))
            check(value(row, native), expected, field)
        check(value(row, 'ConstraintType'), str(CONSTRAINT_TYPES[source.get('constraint_type') or 'none']), 'constraint type')
        if source.get('constraint_type') not in {None, 'none'}:
            check(value(row, 'ConstraintDate')[:10], source['constraint_date'], 'constraint date')
        for predecessor in row.findall('p:PredecessorLink', ns):
            check(value(predecessor, 'LagFormat'), '7', 'lag display unit')
            check(value(predecessor, 'CrossProject'), '0', 'same-project relationship')
            actual_links.append((mapping['tasks'][value(predecessor, 'PredecessorUID')]['source_id'], source['id'],
                                 reverse_types[value(predecessor, 'Type')], Decimal(value(predecessor, 'LinkLag')) / (minutes * 10)))
        if snapshot.get('export_state') == 'approved_baseline':
            frozen = row.find('p:Baseline', ns)
            check(value(frozen, 'Start'), value(row, 'Start'), 'baseline start')
            check(value(frozen, 'Finish'), value(row, 'Finish'), 'baseline finish')
            check(value(frozen, 'Duration'), value(row, 'Duration'), 'baseline duration')
    expected_links = [(row['predecessor'], row['successor'], row['relationship_type'], Decimal(str(row['lag_days'])))
                      for row in snapshot.get('relationships') or []]
    check(sorted(actual_links), sorted(expected_links), 'typed relationships and signed lag')
    parsed_calendars = root.findall('p:Calendars/p:Calendar', ns)
    check(len(parsed_calendars), len(mapping['calendars']), 'referenced calendar count')
    for row in parsed_calendars:
        source = calendars[mapping['calendars'][value(row, 'UID')]]
        check(value(row, 'Name') or '', source.get('name') or '', 'calendar name')
        observed = {}
        for day in row.findall('p:WeekDays/p:WeekDay', ns):
            iso_day = (int(value(day, 'DayType')) + 5) % 7
            if value(day, 'DayWorking') == '1':
                observed[str(iso_day)] = [{'from': value(pair, 'FromTime'), 'to': value(pair, 'ToTime')}
                                        for pair in day.findall('p:WorkingTimes/p:WorkingTime', ns)]
        check(observed, {str(day): source['working_times'].get(str(day), source['working_times'].get(day))
                         for day in source['working_weekdays']}, 'weekday shifts')
        observed = []
        for exception in row.findall('p:Exceptions/p:Exception', ns):
            observed.append({'date': exception.findtext('p:TimePeriod/p:FromDate', namespaces=ns)[:10],
                             'is_working': value(exception, 'DayWorking') == '1', 'name': value(exception, 'Name') or '',
                             'working_times': [{'from': value(pair, 'FromTime'), 'to': value(pair, 'ToTime')}
                                               for pair in exception.findall('p:WorkingTimes/p:WorkingTime', ns)]})
        expected = [{'date': item['date'], 'is_working': item['is_working'], 'name': item.get('name') or '',
                     'working_times': item.get('working_times') or []} for item in source.get('exceptions') or []]
        check(sorted(observed, key=lambda item: item['date']), sorted(expected, key=lambda item: item['date']), 'calendar exceptions')
    resources = {row['id']: row for row in snapshot.get('resources') or []}
    parsed_resources = root.findall('p:Resources/p:Resource', ns)
    check(len(parsed_resources), len(resources), 'resource count')
    check({value(row, 'UID') for row in parsed_resources}, set(mapping['resources']), 'resource UIDs')
    for row in parsed_resources:
        source = resources[mapping['resources'][value(row, 'UID')]]
        check(value(row, 'Name'), source['name'], 'resource name')
        check(value(row, 'Code'), source['code'], 'resource code')
        check(value(row, 'Type'), '0' if source['resource_type'] == 'material' else '1', 'resource type')
    assignments = {row['id']: row for row in snapshot.get('assignments') or []}
    parsed_assignments = root.findall('p:Assignments/p:Assignment', ns)
    check(len(parsed_assignments), len(assignments), 'assignment count')
    check({value(row, 'UID') for row in parsed_assignments}, set(mapping['assignments']), 'assignment UIDs')
    for row in parsed_assignments:
        source = assignments[mapping['assignments'][value(row, 'UID')]]
        check(mapping['tasks'][value(row, 'TaskUID')]['source_id'], source['activity'], 'assignment task')
        check(mapping['resources'][value(row, 'ResourceUID')], source['resource'], 'assignment resource')
        check(value(row, 'Work'), f"PT{int(Decimal(str(source['budgeted_hours'])) * 3600)}S", 'assignment hours')
    return {'adapter_version': '1.0', 'xml_sha256': sha256(content).hexdigest(),
            'schema_sha256': sha256(SCHEMA_PATH.read_bytes()).hexdigest(),
            'xml_well_formed': 'passed', 'schema_validation': 'RADAI supported-subset XSD passed',
            'input_preservation': 'Independent XML parser compared supported source fields',
            'vendor_application_roundtrip': 'not_tested', 'native_mpp': False,
            'sidecar_only_fields': SIDECAR_FIELDS, 'task_count': len(activities), 'relationship_count': len(expected_links)}


def mspdi_bundle(snapshot):
    content, provenance, report = build_mspdi(snapshot)
    provenance_bytes = json.dumps(provenance, ensure_ascii=False, indent=2).encode('utf-8')
    report['provenance_sha256'] = sha256(provenance_bytes).hexdigest()
    stream = BytesIO()
    with ZipFile(stream, 'w', compression=ZIP_DEFLATED) as archive:
        archive.writestr('schedule.xml', content)
        archive.writestr('radai-provenance.json', provenance_bytes)
        archive.writestr('verification.json', json.dumps(report, ensure_ascii=False, indent=2))
    return stream.getvalue()
