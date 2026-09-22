"""One export contract; capabilities must describe tested behavior truthfully."""
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation


EXPORT_SCHEMA_VERSION = '2.0'
_CAPABILITIES = {
    'json': {'name': 'RADAI JSON', 'format_version': EXPORT_SCHEMA_VERSION, 'status': 'implemented',
             'structured_draft': True, 'baseline': True, 'traceability': 'embedded',
             'limitations': ['RADAI schema; not a Primavera or Microsoft Project interchange file.']},
    'xlsx': {'name': 'Excel workbook', 'format_version': 'Office Open XML', 'status': 'implemented',
             'structured_draft': True, 'baseline': True, 'traceability': 'embedded sheets',
             'limitations': ['Tabular schedule and evidence; Excel does not execute the scheduling logic.']},
    'csv': {'name': 'Activities CSV', 'format_version': 'UTF-8 CSV', 'status': 'implemented',
            'structured_draft': True, 'baseline': False, 'traceability': 'not included',
            'limitations': ['Activities only; relationships, calendars and evidence require the JSON or Excel export.']},
    'xer': {'name': 'Primavera P6 XER', 'format_version': None, 'status': 'legacy_unvalidated',
            'structured_draft': False, 'baseline': False, 'traceability': 'not included',
            'limitations': ['Legacy serializer retained for existing integrations only.',
                            'Calendar exceptions, constraints, resources and provenance are not preserved.',
                            'No validated P6 round-trip; unavailable for document-driven plans.']},
    'primavera_xml': {'name': 'Primavera XML', 'format_version': None, 'status': 'unavailable',
                      'structured_draft': False, 'baseline': False, 'traceability': 'unavailable',
                      'limitations': ['No implemented or validated adapter.']},
    'ms_project_xml': {'name': 'Microsoft Project XML', 'format_version': None, 'status': 'unavailable',
                       'structured_draft': False, 'baseline': False, 'traceability': 'unavailable',
                       'limitations': ['No implemented or validated adapter.']},
}

_MSPDI_LIMITATIONS = [
    'Validated against the RADAI supported-subset XSD and an independent XML parser; Microsoft Project application import/recalculation has not been tested.',
    'Explicit working-time intervals, current calculated dates and supported task semantics are required; no working shifts are invented.',
    'Original identities, source evidence, calendar timezone, resource capacity/costing and approval provenance require the companion JSON in the ZIP bundle.',
    'This is XML interchange, not a native MPP file. Primavera application compatibility is not certified.',
]
for _format in ('mspdi', 'mspdi_zip'):
    _CAPABILITIES[_format] = {
        'name': 'Microsoft Project XML' + (' + provenance bundle' if _format == 'mspdi_zip' else ''),
        'format_version': 'MSPDI supported subset 1.0', 'status': 'implemented_subset',
        'structured_draft': False, 'baseline': True,
        'extension': 'zip' if _format == 'mspdi_zip' else 'xml',
        'traceability': 'companion JSON' if _format == 'mspdi_zip' else 'use mspdi_zip for complete provenance',
        'verification': {'xml_well_formed': 'validated', 'subset_schema': 'validated',
                         'input_preservation': 'validated', 'vendor_application_roundtrip': 'not_tested'},
        'limitations': _MSPDI_LIMITATIONS,
    }
# Preserve the previously advertised key as an honest alias, not a second adapter.
_CAPABILITIES['ms_project_xml'] = {**_CAPABILITIES['mspdi'], 'canonical_format': 'mspdi'}


class ScheduleExportError(ValueError):
    def __init__(self, message, *, code='schedule_export_unavailable', issues=None, status_code=409):
        super().__init__(message)
        self.status_code = status_code
        self.payload = {'error': message, 'code': code, 'issues': issues or []}


def export_capabilities():
    return [{'format': key, **deepcopy(value)} for key, value in _CAPABILITIES.items()]


def adapter_capability(export_format):
    if export_format not in _CAPABILITIES:
        raise ScheduleExportError('Unsupported schedule export format.', status_code=400)
    return {'format': export_format, **deepcopy(_CAPABILITIES[export_format])}


def validate_export_model(snapshot):
    """Check references without repairing values or claiming a draft is ready."""
    issues = []
    def issue(code, message, entity_id=None):
        issues.append({'code': code, 'message': message, 'entity_id': entity_id, 'severity': 'error'})
    activities = snapshot.get('activities') or []
    ids, external_ids = set(), set()
    for row in activities:
        key = row.get('id')
        external = row.get('external_id')
        if key is None or key in ids or not external or external in external_ids:
            issue('export_activity_identifier', 'Activity identifiers are missing or duplicated.', key)
        ids.add(key)
        external_ids.add(external)
        start, finish = row.get('planned_start'), row.get('planned_finish')
        try:
            start, finish = date.fromisoformat(start) if start else None, date.fromisoformat(finish) if finish else None
            if start and finish and start > finish:
                issue('export_activity_dates', 'The activity finish precedes its start.', external)
        except (ValueError, TypeError):
            issue('export_activity_dates', 'An activity date is not an ISO calendar date.', external)
        if row.get('duration_days') is not None:
            try:
                duration = Decimal(str(row['duration_days']))
                if not duration.is_finite() or duration < 0:
                    raise ValueError
                if row.get('is_milestone') and duration != 0:
                    issue('export_milestone_duration', 'A milestone must not carry nonzero duration.', external)
            except (ValueError, TypeError, InvalidOperation):
                issue('export_duration_invalid', 'The duration is not a finite nonnegative value.', external)
    for row in snapshot.get('relationships') or []:
        if row.get('predecessor') not in ids or row.get('successor') not in ids:
            issue('export_dangling_relationship', 'A relationship endpoint is missing from this schedule.', row.get('id'))
        if row.get('relationship_type') not in {'FS', 'SS', 'FF', 'SF'} or row.get('lag_days') is None:
            issue('export_relationship_semantics', 'The relationship type or lag is not specified.', row.get('id'))
        else:
            try:
                if not Decimal(str(row['lag_days'])).is_finite():
                    raise ValueError
            except (ValueError, TypeError, InvalidOperation):
                issue('export_relationship_semantics', 'Relationship lag must be a finite number.', row.get('id'))
    return issues
