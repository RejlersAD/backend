"""Header-driven schedule facts from saved document text, without planning.

CSV, TSV, semicolon and pipe tables are adapters, not document classifiers.
Only named columns establish meaning. Unknown units, dates, links and columns
remain unknown; row order, filenames, headings and task names establish no
relationships. Coverage describes extraction of the saved text, never coverage
of a source binary that may already have been truncated or poorly OCR'd.
"""
from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal, InvalidOperation
import re


MAX_TEXT_CHARS = 8_000_000
MAX_TABLE_ROWS = 100_000
_DELIMITERS = ('\t', '|', ',', ';')
_MARKER = re.compile(r'^---\s*(Sheet|Table|OCR Page):\s*(.*?)\s*---$', re.I)
_NUMBER = r'[+-]?(?:\d+(?:\.\d+)?|\.\d+)'
_QUANTITY = re.compile(rf'^(?P<number>{_NUMBER})\s*(?P<unit>[A-Za-z ]*)$')
_NONE = {'none', 'no predecessors', 'no dependencies', 'no predecessor', 'no dependency'}
_MISSING = {'', 'not specified', 'unspecified', 'tbd', 'tbc', 'n/a', 'na', '-', '—'}
_MONTHS = {name: index for index, name in enumerate(
    ('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'), 1)}
_MONTHS.update({name: index for index, name in enumerate(
    ('january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december'), 1)})
_UNITS = {
    'd': 'days', 'day': 'days', 'days': 'days',
    'working day': 'working_days', 'working days': 'working_days', 'wd': 'working_days',
    'calendar day': 'calendar_days', 'calendar days': 'calendar_days', 'cd': 'calendar_days',
    'h': 'hours', 'hr': 'hours', 'hrs': 'hours', 'hour': 'hours', 'hours': 'hours',
    'w': 'weeks', 'wk': 'weeks', 'week': 'weeks', 'weeks': 'weeks',
    'month': 'months', 'months': 'months',
}
_RELATION_TYPES = {'fs': 'FS', 'ss': 'SS', 'ff': 'FF', 'sf': 'SF',
                   'finish to start': 'FS', 'start to start': 'SS',
                   'finish to finish': 'FF', 'start to finish': 'SF'}
_ALIASES = {
    'activity_id': {'activity id', 'activity code', 'task id', 'task code', 'work id', 'package id', 'id',
                    'document number', 'document no', 'document code', 'deliverable id'},
    'title': {'activity name', 'activity title', 'task name', 'task title', 'task', 'activity',
              'work package', 'package name', 'work package name', 'deliverable', 'deliverable name',
              'deliverable title', 'document title', 'name', 'title', 'description',
              'module name', 'module title', 'module id & name', 'module id and name'},
    'duration': {'duration', 'planned duration', 'original duration', 'baseline duration'},
    'duration_unit': {'duration unit', 'duration units'},
    'planned_start_date': {'start', 'start date', 'planned start', 'planned start date', 'baseline start'},
    'planned_finish_date': {'finish', 'finish date', 'end', 'end date', 'planned finish',
                            'planned finish date', 'planned end', 'baseline finish',
                            'completion date', 'target completion date', 'target finish date', 'target end date'},
    'milestone_date': {'milestone date'},
    'total_float': {'float', 'total float'},
    'predecessors': {'predecessor', 'predecessors', 'predecessor id', 'predecessor ids',
                     'depends on', 'dependencies'},
    'relationship_type': {'relationship type', 'dependency type', 'relation type', 'link type'},
    'lag': {'lag', 'relationship lag', 'dependency lag'},
    'lag_unit': {'lag unit', 'lag units'},
    'kind': {'activity type', 'task type', 'record type', 'row type', 'type'},
    'is_milestone': {'milestone', 'is milestone'},
    'constraint_type': {'constraint type', 'constraint'},
    'constraint_date': {'constraint date'},
    'calendar': {'calendar', 'calendar name', 'calendar id'},
    'wbs_code': {'wbs', 'wbs code', 'wbs id'},
    'parent_id': {'parent id', 'parent activity id', 'parent task id', 'parent package id'},
    'row_number': {'row', 'row number', 'row no', '#'},
    'notes': {'notes', 'note', 'remarks', 'requirements'},
}
_SCHEDULE_FIELDS = {'duration', 'planned_start_date', 'planned_finish_date', 'milestone_date',
                    'predecessors', 'is_milestone', 'constraint_type', 'constraint_date'}
_HEADER_MAP = {alias: (field, None) for field, aliases in _ALIASES.items() for alias in aliases}
_HEADER_MAP.update({alias + ' ' + label: (field, unit)
                    for field in ('duration', 'total_float', 'lag')
                    for alias in _ALIASES[field] for label, unit in _UNITS.items()})


def _normal(value):
    return ' '.join(str(value or '').replace('_', ' ').strip().casefold().split())


def _header(value):
    text = _normal(value)
    known = _HEADER_MAP.get(text)
    if known:
        return {'field': known[0], 'unit': known[1], 'label': str(value)}
    # Units belong to explicit header labels, never to the uploaded filename.
    match = re.fullmatch(r'(.*?)\s*[([]([^\])]+)[)\]]', text)
    if match:
        text, unit = match[1].strip(), _UNITS.get(_normal(match[2]))
        known = _HEADER_MAP.get(text)
        if unit is not None and known and known[0] in {'duration', 'total_float', 'lag'}:
            return {'field': known[0], 'unit': unit, 'label': str(value)}
    return None


def _cells(line, delimiter):
    try:
        cells = next(csv.reader([line], delimiter=delimiter, skipinitialspace=True, strict=True))
    except (csv.Error, StopIteration):
        return []
    return cells


def _table_header(line, next_line=''):
    for delimiter in _DELIMITERS:
        if delimiter not in line:
            continue
        cells = _cells(line, delimiter)
        separator = _cells(next_line, delimiter) if delimiter == '|' else []
        # Only a real Markdown separator establishes boundary pipes. Blank
        # first/last worksheet columns must otherwise retain their positions.
        wrapped = bool(delimiter == '|' and line.lstrip().startswith('|') and line.rstrip().endswith('|')
                       and len(separator) > 2 and separator[0] == separator[-1] == ''
                       and all(re.fullmatch(r'\s*:?-{2,}:?\s*', cell) for cell in separator[1:-1]))
        if wrapped:
            cells = cells[1:-1]
        mapped = [_header(cell) for cell in cells]
        fields = [item['field'] for item in mapped if item]
        if 'title' in fields and _SCHEDULE_FIELDS.intersection(fields):
            duplicates = sorted({field for field in fields if fields.count(field) > 1})
            return delimiter, cells, mapped, duplicates, wrapped
    return None


def _value_status(value, *, allow_none=False):
    normalized = _normal(value)
    if allow_none and normalized in _NONE:
        return 'explicit_none'
    return 'not_specified' if normalized in _MISSING else 'extracted'


def _quantity(raw, header_unit=None, declared_unit=None):
    """Preserve numeric units; never convert hours/weeks to working days."""
    if _value_status(raw) == 'not_specified':
        return None, 'not_specified'
    match = _QUANTITY.fullmatch(raw.strip())
    if not match:
        return None, 'invalid'
    try:
        number = Decimal(match['number'])
    except InvalidOperation:
        return None, 'invalid'
    if not number.is_finite():
        return None, 'invalid'
    explicit_label = _normal(match['unit'])
    explicit_unit = _UNITS.get(explicit_label) if explicit_label else None
    separate_label = _normal(declared_unit)
    separate_unit = _UNITS.get(separate_label) if separate_label else None
    if (explicit_label and explicit_unit is None) or (separate_label and separate_unit is None):
        return {'value': float(number), 'unit': None, 'raw': raw}, 'unsupported_unit'
    units = {unit for unit in (explicit_unit, separate_unit, header_unit) if unit}
    if len(units) > 1:
        return {'value': float(number), 'unit': None, 'raw': raw}, 'conflicting_units'
    numeric = int(number) if number == number.to_integral_value() else float(number)
    unit = next(iter(units)) if units else None
    return {'value': numeric, 'unit': unit, 'raw': raw}, 'extracted' if unit else 'unit_not_specified'


def _date(raw):
    if _value_status(raw) == 'not_specified':
        return None, 'not_specified'
    raw = raw.strip()
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw):
            return date.fromisoformat(raw).isoformat(), 'extracted'
        match = re.fullmatch(r'(\d{1,2})[- ]([A-Za-z]{3,9})[- ](\d{4})', raw)
        if match and match[2].casefold() in _MONTHS:
            return date(int(match[3]), _MONTHS[match[2].casefold()], int(match[1])).isoformat(), 'extracted'
    except ValueError:
        return None, 'invalid'
    # Ambiguous numeric formats and two-digit years stay literal evidence.
    return None, 'unsupported_date_format'


def _relationships(raw, *, relation_type='', lag='', lag_unit=None):
    status = _value_status(raw, allow_none=True)
    if status != 'extracted':
        return ([] if status == 'explicit_none' else None), status
    chunks = [item.strip() for item in re.split(r'[;,\n]', raw) if item.strip()]
    links = []
    for chunk in chunks:
        # A delimiter before FS/SS/FF/SF is mandatory: ABCFS may be an ID.
        match = re.fullmatch(
            rf'(?P<id>[^\s:;,+\[\]()]+)(?:\s*:\s*|\s+|\[)'
            rf'(?P<type>FS|SS|FF|SF|(?:Finish|Start)[ -]to[ -](?:Finish|Start))'
            rf'(?:\s*:?\s*(?P<lag>{_NUMBER}\s*[A-Za-z ]*))?\]?',
            chunk, re.I,
        )
        if match:
            quantity, lag_status = _quantity(match['lag'] or '')
            inline_type = _RELATION_TYPES.get(_normal(match['type'].replace('-', ' ')))
            explicit_type = _RELATION_TYPES.get(_normal(relation_type.replace('-', ' '))) if len(chunks) == 1 else None
            if explicit_type and explicit_type != inline_type:
                return None, 'conflicting_relationship_type'
            if len(chunks) == 1 and lag.strip():
                separate_lag, separate_status = _quantity(lag, header_unit=lag_unit)
                if match['lag'] and (separate_status != lag_status or
                                     (separate_lag or {}).get('value') != (quantity or {}).get('value') or
                                     (separate_lag or {}).get('unit') != (quantity or {}).get('unit')):
                    return None, 'conflicting_relationship_lag'
                if not match['lag']:
                    quantity, lag_status = separate_lag, separate_status
            links.append({'predecessor_id': match['id'], 'relationship_type': inline_type,
                          'lag': quantity, 'lag_status': lag_status, 'raw': chunk})
        elif re.fullmatch(r'[^\s:;,+\[\]()]+', chunk):
            explicit_type = _RELATION_TYPES.get(_normal(relation_type.replace('-', ' '))) if len(chunks) == 1 else None
            quantity, lag_status = _quantity(lag, header_unit=lag_unit) if len(chunks) == 1 else (None, 'not_specified')
            links.append({'predecessor_id': chunk,
                          'relationship_type': explicit_type if explicit_type in {'FS', 'SS', 'FF', 'SF'} else None,
                          'lag': quantity, 'lag_status': lag_status, 'raw': chunk})
        else:
            return None, 'unsupported_relationship_syntax'
    if not links:
        return None, 'not_specified'
    return links, 'extracted'


def _row(cells, mapped, locator, excerpt):
    fields, columns = {}, {}
    for index, column in enumerate(mapped):
        if column:
            fields[column['field']] = cells[index].strip()
            columns[column['field']] = {**column, 'column': index + 1}
    title = fields.get('title', '').strip()
    if not title or _value_status(title) == 'not_specified':
        return None
    statuses = {field: 'not_specified' for field in _ALIASES}
    statuses.update({field: _value_status(value) for field, value in fields.items()})
    values = {'original_duration_days': None, 'planned_start_date': None, 'planned_finish_date': None,
              'duration': None, 'total_float_days': None, 'is_milestone': None}
    quantity, statuses['duration'] = _quantity(
        fields.get('duration', ''), columns.get('duration', {}).get('unit'), fields.get('duration_unit'))
    if quantity and quantity['value'] < 0:
        statuses['duration'] = 'invalid'
    values['duration'] = quantity
    if quantity and statuses['duration'] == 'extracted' and quantity['unit'] in {'days', 'working_days', 'calendar_days'}:
        values['original_duration_days'] = quantity['value']
        values['duration_unit'] = quantity['unit']
    for field in ('planned_start_date', 'planned_finish_date', 'milestone_date', 'constraint_date'):
        values[field], statuses[field] = _date(fields.get(field, ''))
    values['date_columns_status'] = 'parsed' if all(
        statuses[field] in {'extracted', 'not_specified'} for field in ('planned_start_date', 'planned_finish_date')
    ) else 'unresolved'
    float_value, statuses['total_float'] = _quantity(fields.get('total_float', ''), columns.get('total_float', {}).get('unit'))
    values['total_float'] = float_value
    if float_value and statuses['total_float'] == 'extracted' and float_value['unit'] in {'days', 'working_days', 'calendar_days'}:
        values['total_float_days'] = float_value['value']
    kind = _normal(fields.get('kind', ''))
    if kind in {'milestone', 'start milestone', 'finish milestone'}:
        values['is_milestone'] = True
    elif kind in {'task', 'level of effort'}:
        values['is_milestone'] = False
    milestone = _normal(fields.get('is_milestone', ''))
    if milestone in {'yes', 'true', '1'}:
        if values['is_milestone'] is False:
            statuses['is_milestone'] = 'conflicting_values'
            values['is_milestone'] = None
        else:
            values['is_milestone'] = True
    elif milestone in {'no', 'false', '0'}:
        if values['is_milestone'] is True:
            statuses['is_milestone'] = 'conflicting_values'
            values['is_milestone'] = None
        else:
            values['is_milestone'] = False
    elif milestone and statuses['is_milestone'] != 'not_specified':
        statuses['is_milestone'] = 'invalid'
    relations, statuses['predecessors'] = _relationships(
        fields.get('predecessors', ''), relation_type=fields.get('relationship_type', ''),
        lag=fields.get('lag', ''), lag_unit=columns.get('lag', {}).get('unit') or _UNITS.get(_normal(fields.get('lag_unit'))))
    values['predecessors'] = relations
    if fields.get('relationship_type') and _normal(fields['relationship_type'].replace('-', ' ')) not in _RELATION_TYPES:
        statuses['relationship_type'] = 'unsupported_relationship_type'
    for field in ('calendar', 'constraint_type', 'notes', 'wbs_code', 'parent_id'):
        values[field] = fields.get(field) if statuses[field] == 'extracted' else None
    explicit_row = fields.get('row_number', '')
    if explicit_row.isdigit():
        locator = {**locator, 'row': int(explicit_row), 'row_basis': 'explicit_row_number_column'}
    return {
        'activity_id': fields.get('activity_id') or None, 'title': title,
        'kind': 'summary' if kind in {'summary', 'project summary', 'package summary', 'wbs summary'} else 'activity',
        'record_type': fields.get('kind') or None,
        'values': values, 'field_status': statuses, 'field_columns': columns,
        'raw_fields': {column['label']: cells[index] for index, column in enumerate(mapped) if column},
        'unmapped_cells': [{'column': index + 1, 'value': cells[index]} for index, column in enumerate(mapped) if not column],
        'source_locator': locator, 'source_excerpt': excerpt,
    }


class _Lines:
    def __init__(self, lines):
        self.lines, self.index = lines, 0

    def __iter__(self):
        return self

    def __next__(self):
        if self.index >= len(self.lines):
            raise StopIteration
        line = self.lines[self.index]
        self.index += 1
        return line


def parse_structured_schedule_evidence(text, *, source_locator=None):
    """Read independent source facts from explicitly headed, delimited tables.

    Missing columns and empty cells remain ``not_specified``. An explicit
    ``None`` predecessor cell alone establishes an empty predecessor set.
    Date interpretation accepts ISO and named months with four-digit years;
    date locale, two-digit centuries and working calendars are never guessed.
    ``source_locator`` may supply independently verified page/sheet metadata.
    """
    text = text if isinstance(text, str) else ''
    result = {'status': 'not_detected', 'rows': [], 'issues': [], 'coverage': {},
              'adapter': 'header_driven_delimited_table', 'calendar_verified': False,
              'relationships_verified': False, 'complete_document_understanding': False}
    input_length = len(text)
    if input_length > MAX_TEXT_CHARS:
        result['issues'].append({'code': 'text_limit', 'message': 'The saved text exceeds this adapter limit; remaining content was not analyzed.'})
        text = text[:MAX_TEXT_CHARS]
    if '[truncated]' in text:
        result['issues'].append({'code': 'upstream_text_truncated', 'message': 'The upstream extractor truncated the saved text; source document coverage is incomplete.'})
    # splitlines also consumes form feeds; keep them as page metadata.
    lines = text.splitlines(keepends=True)
    offsets, page_by_line = [0], []
    page = 1
    for line in lines:
        page_by_line.append(page)
        page += line.count('\f')
        offsets.append(offsets[-1] + len(line))
    stream = _Lines(lines)
    active, reader, table_number, table_row = None, None, 0, 0
    metadata = dict(source_locator or {})
    classified = set()
    data_lines, unparsed = set(), []
    header_count = 0
    while stream.index < len(lines):
        index = stream.index
        line = lines[index]
        if not line.strip():
            stream.index += 1
            classified.add(index)
            continue
        marker_text = line.strip()
        if marker_text.startswith('"') and marker_text.endswith('"'):
            marker_text = marker_text[1:-1]
        marker = _MARKER.fullmatch(marker_text)
        if marker:
            label, value = marker[1].casefold(), marker[2]
            if label == 'sheet':
                metadata['sheet'] = value
            elif label == 'ocr page' and value.isdigit():
                metadata['page'] = int(value)
            elif label == 'table':
                metadata['document_table'] = value
            active, reader = None, None
            stream.index += 1
            classified.add(index)
            continue
        header = _table_header(line, lines[index + 1] if index + 1 < len(lines) else '')
        if header:
            delimiter, labels, mapped, duplicates, wrapped = header
            table_number += 1
            table_row = 1
            header_count += 1
            classified.add(index)
            stream.index += 1
            if duplicates:
                result['issues'].append({'code': 'duplicate_header_fields', 'line': index + 1, 'fields': duplicates,
                                         'message': 'Multiple columns have the same meaning; this table is not interpreted.'})
                active, reader = None, None
            else:
                unmapped = [{'column': index + 1, 'header': label}
                            for index, (label, column) in enumerate(zip(labels, mapped)) if column is None]
                if unmapped:
                    result['issues'].append({'code': 'unmapped_columns', 'line': index + 1,
                                             'columns': unmapped,
                                             'message': 'Unrecognized columns are preserved in the source excerpt but have not been interpreted.'})
                active = (delimiter, labels, mapped, wrapped)
                reader = csv.reader(stream, delimiter=delimiter, skipinitialspace=True, strict=True)
            continue
        if not active:
            unparsed.append(index + 1)
            stream.index += 1
            continue
        delimiter, labels, mapped, wrapped = active
        try:
            cells = next(reader)
        except csv.Error:
            result['issues'].append({'code': 'invalid_csv_record', 'line': index + 1,
                                     'message': 'Quoted table cells are malformed; no values were assigned.'})
            unparsed.extend(range(index + 1, stream.index + 1))
            active, reader = None, None
            continue
        except StopIteration:
            break
        if wrapped and line.lstrip().startswith('|') and lines[stream.index - 1].rstrip().endswith('|'):
            cells = cells[1:-1]
        if cells and all(re.fullmatch(r'\s*:?-{2,}:?\s*', cell or '') for cell in cells):
            classified.update(range(index, stream.index))
            continue
        table_row += 1
        excerpt = text[offsets[index]:offsets[stream.index]]
        locator = {**metadata, 'line': index + 1, 'line_end': stream.index,
                   'character_start': offsets[index], 'character_end': offsets[stream.index],
                   'table': table_number, 'table_row': table_row, 'row': None,
                   'row_basis': 'saved_text_table_record'}
        if '\f' in text and 'page' not in metadata:
            locator['page'] = page_by_line[index]
        if len(cells) != len(mapped):
            result['issues'].append({'code': 'column_count_mismatch', 'source_locator': locator,
                                     'source_excerpt': excerpt, 'message': 'Column positions are incomplete; no cells were shifted or inferred.'})
            unparsed.extend(range(index + 1, stream.index + 1))
            continue
        row = _row(cells, mapped, locator, excerpt)
        if row is None:
            unparsed.extend(range(index + 1, stream.index + 1))
            continue
        result['rows'].append(row)
        classified.update(range(index, stream.index))
        data_lines.update(range(index, stream.index))
        for field, status in row['field_status'].items():
            if status not in {'extracted', 'not_specified', 'explicit_none'}:
                result['issues'].append({'code': status, 'field': field, 'source_locator': locator,
                                         'message': f'{field.replace("_", " ")} requires source review; no value was guessed.'})
        if len(result['rows']) >= MAX_TABLE_ROWS and stream.index < len(lines):
            result['issues'].append({'code': 'row_limit', 'message': 'Remaining saved text exceeds the table row limit and was not analyzed.'})
            unparsed.extend(range(stream.index + 1, len(lines) + 1))
            break
    nonempty = sum(bool(line.strip()) for line in lines)
    result['coverage'] = {
        'basis': 'saved_text_only', 'input_characters': input_length,
        'examined_characters': len(text), 'nonempty_lines': nonempty,
        'table_count': header_count, 'parsed_records': len(result['rows']),
        'parsed_data_lines': len(data_lines), 'uninterpreted_line_count': len(unparsed),
        'uninterpreted_line_numbers': unparsed[:100], 'uninterpreted_line_sample_limited': len(unparsed) > 100,
        'complete_saved_text_table_coverage': bool(result['rows']) and not unparsed and not result['issues'],
        'original_document_coverage_verified': False,
    }
    if not header_count:
        result['issues'].append({'code': 'structured_schedule_not_detected',
                                 'message': 'No supported explicit schedule table was detected. Narrative, image, native schedule and other layouts require their own evidence adapters.'})
    result['status'] = ('partial' if result['issues'] or unparsed else 'parsed') if header_count else 'not_detected'
    return result
