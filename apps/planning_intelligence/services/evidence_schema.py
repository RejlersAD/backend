"""Versioned value contracts. Validity never implies human acceptance."""
from datetime import date
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from .planning_fact_extraction import STRUCTURED_FACT_FIELDS, assertion_input_schema, valid_assertion_shape

SCHEMA_VERSION = 'planning-evidence-1'
RULE_VERSION = 'accepted-inputs-1'
PROVENANCE_TYPES = {'document_evidence', 'approved_planning_input', 'deterministic_derivation'}
UNITS = {'working_days', 'calendar_days', 'hours'}
REQUIRED = {'identity', 'duration', 'dependencies', 'calendar', 'activity_type', 'constraints'}


def input_schema(prop):
    if prop in STRUCTURED_FACT_FIELDS:
        return assertion_input_schema(prop)
    if prop == 'constraints':
        return {'type': 'array', 'description': 'An empty list explicitly confirms no date constraints.', 'items': {
            'type': 'object', 'required': ['type', 'date'], 'properties': {
                'type': {'type': 'string', 'enum': ['start_no_earlier', 'start_no_later', 'finish_no_later', 'must_start', 'must_finish']},
                'date': {'type': 'string', 'format': 'date'}}}}
    if prop == 'duration':
        return {'type': 'object', 'required': ['value', 'unit'], 'properties': {
            'value': {'type': 'number', 'minimum': 0}, 'unit': {'type': 'string', 'enum': sorted(UNITS)}}}
    if prop == 'dependencies':
        return {'type': 'array', 'description': 'An empty list explicitly confirms no predecessors.', 'items': {
            'type': 'object', 'required': ['predecessor_id', 'type', 'lag', 'lag_unit'], 'properties': {
                'predecessor_id': {'type': 'string'}, 'type': {'type': 'string', 'enum': ['FS', 'SS', 'FF', 'SF']},
                'lag': {'type': 'number'}, 'lag_unit': {'type': 'string', 'enum': sorted(UNITS)}}}}
    if prop == 'calendar':
        return {'type': 'object', 'required': ['working_weekdays', 'hours_per_day', 'timezone', 'exceptions'], 'properties': {
            'working_weekdays': {'type': 'array', 'items': {'type': 'integer', 'minimum': 0, 'maximum': 6}},
            'hours_per_day': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 24},
            'timezone': {'type': 'string'}, 'exceptions': {'type': 'array', 'items': {'type': 'object',
                'required': ['date', 'is_working'], 'properties': {'date': {'type': 'string', 'format': 'date'},
                    'is_working': {'type': 'boolean'}, 'working_hours': {'type': 'number', 'minimum': 0, 'maximum': 24}}}}}}
    if prop == 'activity_type':
        return {'type': 'string', 'enum': ['task', 'start_milestone', 'finish_milestone', 'level_of_effort']}
    if prop.endswith('_date') or prop in {'project_start', 'project_finish'}:
        return {'type': 'string', 'format': 'date'}
    if prop == 'scope_complete':
        return {'type': 'boolean', 'description': 'Confirms that reviewed scope covers the required project scope.'}
    return {'type': 'string'}


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _date(value):
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def validate_value(prop, value):
    if value is None:
        return 'Not Specified'
    if prop in STRUCTURED_FACT_FIELDS:
        return None if valid_assertion_shape(prop, value, allow_legacy=True) else 'Supply the explicit assertion fields; leave unrecorded optional values absent.'
    if prop == 'constraints':
        if not isinstance(value, list) or any(not isinstance(item, dict) or item.get('type') not in {
                'start_no_earlier', 'start_no_later', 'finish_no_later', 'must_start', 'must_finish'} or not _date(item.get('date')) for item in value):
            return 'Supply explicit constraint types and exact dates, or an empty list to confirm no date constraints.'
    elif prop == 'duration':
        if not isinstance(value, dict) or _number(value.get('value')) is None or _number(value['value']) < 0 or value.get('unit') not in UNITS:
            return 'Supply a nonnegative duration and an explicit working_days, calendar_days or hours unit.'
    elif prop == 'dependencies':
        if not isinstance(value, list):
            return 'Supply explicit predecessor relationships, or an empty list to confirm independence.'
        for link in value:
            if not isinstance(link, dict) or not isinstance(link.get('predecessor_id'), str) or not link['predecessor_id'] or link.get('type') not in {'FS', 'SS', 'FF', 'SF'} or _number(link.get('lag')) is None or link.get('lag_unit') not in UNITS:
                return 'Each relationship requires predecessor_id, type, numeric lag and lag_unit.'
        keys = [(item['predecessor_id'], item['type']) for item in value]
        if len(keys) != len(set(keys)):
            return 'Duplicate predecessor relationships require review.'
    elif prop == 'calendar':
        if not isinstance(value, dict):
            return 'Supply the complete approved working calendar.'
        weekdays = value.get('working_weekdays')
        if not isinstance(weekdays, list) or not weekdays or any(type(day) is not int or day not in range(7) for day in weekdays) or len(set(weekdays)) != len(weekdays):
            return 'Specify unique working weekdays (Monday=0 through Sunday=6).'
        hours = _number(value.get('hours_per_day'))
        if hours is None or not 0 < hours <= 24 or not isinstance(value.get('exceptions'), list):
            return 'Specify hours_per_day and an explicit exceptions list.'
        try:
            ZoneInfo(value.get('timezone', ''))
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            return 'Specify an IANA timezone.'
        dates = set()
        for exception in value['exceptions']:
            if not isinstance(exception, dict) or not _date(exception.get('date')) or type(exception.get('is_working')) is not bool:
                return 'Calendar exceptions require an exact date and is_working boolean.'
            if exception['date'] in dates:
                return 'Conflicting calendar exceptions require review.'
            dates.add(exception['date'])
            if exception['is_working'] and (_number(exception.get('working_hours')) is None or not 0 < _number(exception['working_hours']) <= 24):
                return 'A working exception requires explicit working_hours.'
        from .calendar_intervals import calendar_intervals_error
        error = calendar_intervals_error(value)
        if error:
            return error
    elif prop == 'activity_type':
        if value not in {'task', 'start_milestone', 'finish_milestone', 'level_of_effort'}:
            return 'Specify the activity or milestone type.'
    elif prop.endswith('_date') or prop in {'project_start', 'project_finish'}:
        if not _date(value):
            return 'Specify an exact ISO date (YYYY-MM-DD).'
    elif prop == 'scope_complete':
        if value is not True:
            return 'Scope completeness has not been confirmed.'
    elif not isinstance(value, str) or not value.strip():
        return 'Supply a nonempty value.'
    return None


def network_issues(entities):
    """Check the complete accepted graph, including disconnected roots."""
    issues, visiting, complete = [], set(), set()
    for entity, properties in entities.items():
        for link in properties.get('dependencies') or []:
            if link['predecessor_id'] not in entities:
                issues.append({'code': 'dangling_predecessor', 'entity_id': entity, 'field': 'dependencies',
                               'message': f"Predecessor {link['predecessor_id']} is not an accepted activity."})
    # Iterative traversal handles large source networks without recursion limits.
    for root in entities:
        if root in complete:
            continue
        stack = [(root, False)]
        while stack:
            node, exiting = stack.pop()
            if exiting:
                visiting.discard(node)
                complete.add(node)
                continue
            if node in visiting:
                issues.append({'code': 'dependency_cycle', 'entity_id': node, 'field': 'dependencies',
                               'message': 'The accepted relationships contain a dependency cycle.'})
                continue
            if node in complete or node not in entities:
                continue
            visiting.add(node)
            stack.append((node, True))
            stack.extend((link['predecessor_id'], False) for link in entities[node].get('dependencies') or [])
    return issues
