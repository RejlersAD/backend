"""Quoted planning assertions, never executable schedule defaults or identity joins.

Structured values retain the wording and units in the source. A dependency here
is an unresolved statement: accepting its extraction does not bind its endpoints
to activities or authorize a schedule relationship.
"""
from __future__ import annotations

import math
import re


ASSERTION_SCHEMA_VERSION = 'planning-assertions/1.0'
SCALAR_FACT_TYPES = {
    'project_name', 'effective_date', 'duration_months', 'client', 'location',
    'deliverable', 'requirement', 'exclusion',
}
# (required fields, optional fields). No inferred units, dates, kinds or IDs.
STRUCTURED_FACT_FIELDS = {
    'milestone': ({'name'}, {'date', 'source_id'}),
    'constraint': ({'text'}, {'date', 'applies_to'}),
    'review_cycle': ({'name', 'duration', 'unit'}, {'applies_to'}),
    'package': ({'name'}, {'source_id', 'discipline'}),
    'discipline': ({'name'}, {'source_id'}),
    'responsibility': ({'role', 'work'}, {'person', 'organization'}),
    'resource_requirement': ({'resource'}, {'quantity', 'unit', 'work'}),
    'dependency': ({'predecessor', 'successor'}, {'relationship_type', 'lag', 'lag_unit'}),
    'risk': ({'text'}, {'impact', 'owner', 'response'}),
}
SUPPORTED_FACT_TYPES = SCALAR_FACT_TYPES | set(STRUCTURED_FACT_FIELDS)

CATEGORY_GUIDANCE = {
    'mdr': 'Retain every explicit register identity/title/revision; MDR defines deliverable scope. Do not convert row order to execution order.',
    'eddr': 'Retain exact document identities and revisions; missing values stay absent. Do not copy schedule facts from similarly named rows.',
    'sow': 'Extract explicit obligations, exclusions, responsibilities, resources, packages, milestones and timing clauses. A topic mention is not required work.',
    'wbs': 'Extract explicitly named work packages and disciplines. Hierarchy or section order does not establish schedule dependencies.',
    'reference_schedule': 'Extract only printed activity identities, milestone dates, constraints and relationships. Similar bar positions do not establish links.',
    'timeline': 'Extract explicitly stated milestone labels and dates; missing dates stay absent.',
    'schedule_requirements': 'Retain review periods, calendars and constraints with exact units and applicability. Do not apply a general clause to every activity.',
    'project_control_procedure': 'Retain stated controls, responsibilities and measurement requirements; procedure examples are not project scope.',
}


def category_guidance(category):
    return CATEGORY_GUIDANCE.get(category, 'Extract explicit project facts only. The declared type is context, not evidence or scope authority.')


def quoted_value(value, quote):
    """Every supplied leaf must occur literally; finite numbers use token bounds."""
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return False
        token = str(value)
        return bool(re.search(r'(?<![\w.+-])' + re.escape(token) + r'(?![\w.]|,\d)', quote))
    elif isinstance(value, str) and value.strip():
        token = value
    else:
        return False
    # Preserve exact source capitalization/spelling; AI normalization is a
    # separate reviewed transformation, not an extraction fact.
    return bool(re.search(r'(?<!\w)' + re.escape(token) + r'(?!\w)', quote))


def validate_fact_value(kind, value, quote):
    if kind in SCALAR_FACT_TYPES:
        if kind == 'duration_months' and (type(value) is not int or value <= 0):
            return False
        if kind != 'duration_months' and not isinstance(value, str):
            return False
        return quoted_value(value, quote)
    if not valid_assertion_shape(kind, value):
        return False
    return all(quoted_value(item, quote) for item in value.values())


def valid_assertion_shape(kind, value, *, allow_legacy=False):
    """Value shape only; source verification and acceptance are separate gates."""
    fields = STRUCTURED_FACT_FIELDS.get(kind)
    if fields is None or not isinstance(value, dict):
        return False
    required, optional = fields
    if allow_legacy and kind == 'discipline':
        optional = optional | {'code'}
    if allow_legacy and kind == 'review_cycle' and 'working_days' in value:
        required, optional = {'name', 'working_days'}, set()
    if not required <= value.keys() or not value.keys() <= required | optional:
        return False
    if any(not isinstance(item, str) or not item.strip() for key, item in value.items()
           if key not in {'duration', 'quantity', 'lag', 'working_days'}):
        return False
    if any(type(value[key]) not in (int, float) or value[key] < 0 or
           (isinstance(value[key], float) and not math.isfinite(value[key]))
           for key in ('duration', 'quantity', 'working_days') if key in value):
        return False
    if ('lag' in value) != ('lag_unit' in value):
        return False
    if 'lag' in value and (type(value['lag']) not in (int, float) or
                           (isinstance(value['lag'], float) and not math.isfinite(value['lag']))):
        return False
    return True


def assertion_input_schema(kind):
    required, optional = STRUCTURED_FACT_FIELDS[kind]
    fields = required | optional | ({'code'} if kind == 'discipline' else set())
    schema = {'type': 'object', 'required': sorted(required), 'additionalProperties': False,
              'description': 'A quoted assertion only; no schedule links, constraints or assignments are created by accepting it.',
              'properties': {key: {'type': 'number' if key in {'duration', 'quantity', 'lag'} else 'string'}
                             for key in sorted(fields)}}
    if kind == 'review_cycle':
        return {'anyOf': [schema, {'type': 'object', 'required': ['name', 'working_days'], 'additionalProperties': False,
                                 'properties': {'name': {'type': 'string'}, 'working_days': {'type': 'number', 'minimum': 0}}}]}
    return schema


def validated_claim(claim, chunk):
    """Validate quote location and all fields, without asserting interpretation."""
    if not isinstance(claim, dict) or str(claim.get('source_file_id')) != str(chunk['source_file_id']):
        return None
    if claim.get('classification', 'document_fact') != 'document_fact':
        return None
    quote, value, kind = claim.get('quote'), claim.get('value'), claim.get('type')
    if not isinstance(quote, str) or not quote.strip() or not validate_fact_value(kind, value, quote):
        return None
    offset = claim.get('quote_start')
    if offset is not None:
        if type(offset) is not int or offset < 0 or chunk['text'][offset:offset + len(quote)] != quote:
            return None
    else:
        offset = chunk['text'].find(quote)
        # Repeated text cannot identify a physical occurrence without its offset.
        if offset < 0 or chunk['text'].find(quote, offset + 1) >= 0:
            return None
    discipline = claim.get('discipline')
    if not isinstance(discipline, str) or not quoted_value(discipline, quote):
        discipline = None
    return {
        'type': kind, 'value': value, 'discipline': discipline,
        'source_file_id': chunk['source_file_id'], 'quote': quote,
        'character_start': chunk['character_start'] + offset,
        'character_end': chunk['character_start'] + offset + len(quote),
        'status': 'requires_review', 'classification': 'document_fact',
        'executable': False, 'schema_version': ASSERTION_SCHEMA_VERSION,
    }


_LABELS = {
    'package': r'(?:work package|package)',
    'discipline': r'discipline',
    'constraint': r'(?:constraint|restriction)',
    'risk': r'risk',
    'resource_requirement': r'(?:resource requirement|required resource)',
}
_LABELED = re.compile(
    r'^[ \t]*(?P<label>' + '|'.join(_LABELS.values()) + r')[ \t]*[:=][ \t]*(?P<value>[^\n]+?)[ \t]*$', re.I | re.M,
)


def explicit_labeled_assertions(text):
    """Conservative generic fallback for explicit labeled clauses, never topics.

Narrative interpretation belongs to quoted AI review. Retaining a complete
resource clause as its resource value avoids guessing quantity/units from prose.
"""
    for match in _LABELED.finditer(text):
        label = match.group('label')
        kind = next(key for key, pattern in _LABELS.items() if re.fullmatch(pattern, label, re.I))
        value = match.group('value')
        # An illustrative or excluded label is not a positive scope assertion.
        if re.search(r'\b(?:example|illustrative|not required|excluded|out of scope)\b', value, re.I):
            continue
        field = 'name' if kind in {'package', 'discipline'} else 'resource' if kind == 'resource_requirement' else 'text'
        yield {'type': kind, 'value': {field: value}, 'quote': match.group(0),
               'character_start': match.start(), 'character_end': match.end()}
