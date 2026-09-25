"""Read deliverable-register rows without substituting catalogue document names.

These helpers do no storage access or database writes. Workbook extraction accepts
an already-open binary stream. Text extraction also understands older pipe-joined
Excel parses, but labels their provenance as extracted lines, not worksheet rows.
"""
from __future__ import annotations

import csv
from copy import deepcopy
import hashlib
import io
import re
import unicodedata
from collections import Counter


_SHEET = re.compile(r'^---\s*Sheet:\s*(.*?)\s*---$')
_SERIAL = re.compile(r'^\d+(?:\.\d+)?$')
_LEGACY_REGISTER_ROW = re.compile(
    r'^\s*(?P<item>\d{1,5})\s+(?P<discipline>[A-Z][A-Z &/()-]{1,60}?)\s+'
    r'(?P<number>(?=[A-Z0-9./_~\-]*\d)[A-Z0-9]{2,12}(?:[-/_.][A-Z0-9~]{1,25}){2,})\s+(?P<title_area>.+?)\s+'
    r'(?P<existing>NEW|EXISTING)\s+(?P<class>\d{1,3})\s+(?P<revision>[A-Z0-9]{1,8})(?:\s.*)?$',
    re.I,
)


def _text(value):
    if value is None:
        return ''
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _words(value):
    return re.sub(r'[^a-z0-9]+', ' ', value.casefold()).strip()


def normalize_register_discipline(value):
    """Keep an original label separately; HVAC must remain its own discipline."""
    if not value or not str(value).strip():
        return 'not_specified'
    normalized = _words(value)
    mappings = (
        ('hvac', 'hvac'), ('civil', 'civil'), ('structural', 'civil'),
        ('electrical', 'electrical'), ('instrument', 'instrumentation'),
        ('mechanical', 'mechanical'), ('process', 'process'),
        ('general', 'general'), ('hse', 'hse'), ('pipeline', 'pipeline'),
        ('piping', 'piping'), ('telecom', 'telecom'),
        ('project management', 'pm'), ('project control', 'pc'),
    )
    for term, code in mappings:
        if re.search(r'\b' + re.escape(term) + (r'\w*\b' if term in {'instrument', 'telecom', 'project control'} else r'\b'), normalized):
            return code
    ascii_value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]+', '_', ascii_value.casefold()).strip('_')[:64] or 'not_specified'


def _header_role(value):
    name = _words(value)
    if name in {'discipline', 'discipline name', 'discipline code', 'department'}:
        return 'discipline_label'
    for dimension in ('phase', 'package', 'area'):
        if name in {dimension, dimension + ' name', dimension + ' code'}:
            return dimension + '_label'
    if name in {
        'sl no', 'slno', 's no', 'sr no', 'sr', 'serial no', 'serial number',
        'item', 'item no', 'item number', 'no', 'number',
    }:
        return 'register_item'
    if name in {'revision', 'rev', 'document revision', 'doc revision', 'revision no'}:
        return 'document_revision'
    if name in {
        'document no', 'document number', 'doc no', 'doc number', 'drawing no',
        'drawing number', 'document drawing no', 'drawing document no',
        'drawing document number', 'drawing document code', 'document code',
    }:
        return 'document_number'
    if name in {
        'title', 'document title', 'doc title', 'drawing title', 'deliverable',
        'deliverable title', 'deliverable name', 'document name',
        'document description', 'drawing document title', 'document drawing title',
        'drawing document', 'drawing document description', 'drawing description',
        'description of deliverable', 'description of document',
    }:
        return 'name'
    return None


def _headers(cells):
    columns = {}
    for index, cell in enumerate(cells):
        role = _header_role(cell)
        if role and role not in columns:
            columns[role] = index
    if 'name' in columns and any(role in columns for role in ('discipline_label', 'register_item', 'document_number')):
        return columns
    return None


class _RegisterTable:
    def __init__(self):
        self.columns = None
        self.column_count = 0
        self.header_cells = []
        self.discipline = ''

    def row(self, values, *, collapsed=False):
        cells = [_text(value) for value in values]
        header = _headers(cells)
        if header:
            self.columns = header
            self.column_count = len(cells)
            self.header_cells = cells[:]
            return None
        if not self.columns or not any(cells):
            return None

        # These assertions describe literal cells only. Display-only inherited
        # discipline labels and collapsed legacy columns are never evidence.
        explicit_dimensions = {}
        if len(cells) == self.column_count:
            for dimension in ('discipline', 'phase', 'package', 'area'):
                index = self.columns.get(dimension + '_label')
                if index is not None and cells[index]:
                    explicit_dimensions[dimension] = {'value': cells[index], 'column': index + 1,
                                                      'header': self.header_cells[index]}

        nonempty = [cell for cell in cells if cell]
        if len(nonempty) == 1:
            # Older Excel parses omitted all empty cells, including those in
            # section-heading rows. A single value is never a deliverable row.
            if 'discipline_label' in self.columns and not _SERIAL.fullmatch(nonempty[0]):
                self.discipline = nonempty[0]
            return None

        if collapsed and len(cells) == 2 and self.column_count == 3 and set(self.columns) == {'register_item', 'discipline_label', 'name'}:
            # Merged discipline cells are blank on subsequent rows. Only infer
            # this unambiguous three-column legacy layout; never shift a title
            # into a document-number or revision column.
            if self.columns['register_item'] == 0 and self.columns['discipline_label'] == 1 and _SERIAL.fullmatch(cells[0]):
                cells = [cells[0], self.discipline, cells[1]]

        def get(role):
            index = self.columns.get(role)
            return cells[index] if index is not None and index < len(cells) else ''

        label = get('discipline_label')
        if label:
            self.discipline = label
        name = get('name')
        serial = get('register_item')
        document_number = get('document_number')
        if not name or _header_role(name) == 'name':
            return None
        if 'register_item' in self.columns and not _SERIAL.fullmatch(serial) and not document_number:
            return None
        if _words(name) in {'total', 'grand total', 'subtotal', 'sub total'}:
            return None
        label = label or self.discipline
        return {
            'name': name,
            'original_title': name,
            'discipline': normalize_register_discipline(label),
            'discipline_label': label,
            'register_item': int(serial) if serial.isdigit() else serial or None,
            'document_number': document_number,
            'document_revision': get('document_revision'),
            'explicit_dimensions': explicit_dimensions,
        }


def _delimiter(text):
    for line in text.splitlines():
        for delimiter in ('\t', '|', ',', ';'):
            if delimiter not in line:
                continue
            try:
                cells = next(csv.reader([line], delimiter=delimiter, skipinitialspace=True))
            except csv.Error:
                continue
            if _headers([cell.strip() for cell in cells]):
                return delimiter
    return None


def _captioned_text_register_rows(text):
    """Recover a captioned serial/title PDF table, never a prose obligation list.

    Whitespace does not establish a discipline column. Literal section labels
    remain source groups. Interleaved serial/title text remains ambiguous and
    must be quarantined by consumers that turn register rows into activities.
    """
    lines, offset = [], 0
    for raw in text.splitlines(keepends=True):
        lines.append({'text': raw.strip(), 'start': offset, 'end': offset + len(raw)})
        offset += len(raw)
    repeated = Counter(line['text'] for line in lines)
    caption_re = re.compile(r'^(?:table\s+\d+[a-z]?\s*[:.\-–—]?\s*)?(?:[\w &/()-]+\s+)?(?:deliverables|deliverable register|document register)$', re.I)
    row_re = re.compile(r'^(?P<item>\d{1,4})\s+(?P<title>\S.*)$')
    header_names = {f'{serial} {title}' for serial in ('s no', 'sl no', 'sr no', 'serial no', 'serial number', 'item', 'item no')
                    for title in ('description', 'deliverable', 'deliverable title', 'document title')}
    rows, caption, caption_at, active = [], '', -10, False
    group, group_line, pending = '', None, None

    def finish():
        nonlocal pending
        if pending and pending['parts']:
            title = ' '.join(pending.pop('parts'))
            start, end = pending['start'], pending['end']
            locator = {'line': text[:start].count('\n') + 1, 'table_caption': caption}
            if group:
                locator.update(source_group=group, source_group_line=group_line)
            pending.update(
                name=title, original_title=title, discipline='not_specified', discipline_label='Not Specified',
                source_group=group, explicit_dimensions={}, document_number='', document_revision='',
                source_layout='captioned_text_register', sheet='', row_number=None,
                source_line=locator['line'], source_locator=locator, source_excerpt=text[start:end].strip(),
            )
            rows.append(pending)
        pending = None

    index = 0
    while index < len(lines):
        line = lines[index]
        value = line['text']
        index += 1
        if not value:
            continue
        if caption_re.fullmatch(value) and not re.search(r'\b(?:shall|must|required|not)\b', value, re.I):
            finish()
            caption, caption_at, active, group, group_line = value, index, False, '', None
            continue
        if re.match(r'^(?:appendix\s+\w+|table\s+\d+\b)', value, re.I):
            finish()
            caption, active, group, group_line = '', False, '', None
            continue
        if _words(value) in header_names:
            finish()
            # Repeated page headers may resume only a previously opened table.
            active = bool(caption) and (active or caption_at == -1 or index - caption_at <= 3)
            if active:
                caption_at = -1
            continue
        if not active:
            continue
        match = row_re.fullmatch(value)
        if match:
            finish()
            pending = {'register_item': int(match['item']), 'parts': [match['title']],
                       'start': line['start'], 'end': line['end'], 'title_boundary_status': 'explicit_numbered_row'}
            continue
        if (re.match(r'^(?:page\s*[:.]?\s*\d|rev(?:ision)?\s*[.:]?\s*\w)', value, re.I)
                or (len(value) > 24 and repeated[value] >= 3)):
            finish()
            active = False
            continue
        if re.fullmatch(r'notes?\s*[:.]?', value, re.I):
            finish()
            caption, active, group, group_line = '', False, '', None
            continue
        next_line = lines[index] if index < len(lines) else None
        next_match = row_re.fullmatch(next_line['text']) if next_line else None
        if next_match and (pending is None or int(next_match['item']) == 1) and len(value) <= 100 and not re.search(r'[.;:]$|\b(?:shall|must)\b', value, re.I):
            # A reset serial alone cannot distinguish a section heading from
            # a wrapped title. These cues flag uncertainty; they do not prove
            # the recovered title boundary or assign a discipline.
            if pending and (not pending['parts'] or value[0].islower() or re.search(
                    r'(?:\b(?:for|of|and|or|with|to|in|on|from|by|the|a|an)|[/&,:-])\s*$',
                    pending['parts'][-1], re.I)):
                pending['parts'].append(value)
                pending['end'] = line['end']
                pending['title_boundary_status'] = 'ambiguous'
                continue
            finish()
            group, group_line = value, text[:line['start']].count('\n') + 1
            continue
        # PDF reading order can place a centered serial between wrapped title
        # lines. Keep its full located text but do not claim a proven boundary.
        if next_line and re.fullmatch(r'\d{1,4}', next_line['text']):
            finish()
            pending = {'register_item': int(next_line['text']), 'parts': [value],
                       'start': line['start'], 'end': next_line['end'], 'title_boundary_status': 'ambiguous'}
            index += 1
            continue
        if re.fullmatch(r'\d{1,4}', value):
            finish()
            pending = {'register_item': int(value), 'parts': [], 'start': line['start'],
                       'end': line['end'], 'title_boundary_status': 'ambiguous'}
            continue
        if pending and not re.match(r'^(?:\d+[.)]\s|notes?\s*[:.]|please\s)', value, re.I):
            pending['parts'].append(value)
            pending['end'] = line['end']
            pending['title_boundary_status'] = 'ambiguous'
        else:
            finish()
            active = False
    finish()
    return rows


def register_row_requires_review(row):
    """A preview selection cannot resolve missing matrix scope or text geometry."""
    return (
        row.get('source_layout') in {'captioned_text_register', 'applicability_text_register', 'pdf_geometry_register'}
        and row.get('title_boundary_status') == 'ambiguous'
    ) or row.get('applicability_status', 'marked') != 'marked'


def _applicability_text_register_rows(text):
    """Read explicit hierarchical deliverables matrices conservatively.

    Flattened PDF text does not retain package-column positions. X marks can
    establish only that a row is marked, never which package it belongs to.
    Wrapped/interleaved cells and unmarked/conditional rows remain inventory.
    """
    lines, offset = [], 0
    for raw in text.splitlines(keepends=True):
        lines.append({'text': raw.strip(), 'start': offset, 'end': offset + len(raw)})
        offset += len(raw)
    caption_re = re.compile(r'^table\s+\d+[a-z]?\s*[:.\-]?\s+(?:applicable\s+deliverables\b.*|deliverables\b.*\bapplicability\b.*)$', re.I)
    section_re = re.compile(r'^(\d+(?:\.\d+)+)\s+(.+)$')
    row_re = re.compile(r'^(\d+(?:\.\d+){2,})(?:\s+(.*))?$')
    marker_re = re.compile(r'(?<!\S)[xX](?:[ \t]+[xX])*(?!\S)')
    furniture_re = re.compile(r'^.+\.(?:docx?|pdf)\s+\d+\s*/\s*\d+$|^[\w .&/-]{1,80}Classification:\s*\w.*$', re.I)
    conditional_re = re.compile(r'\b(?:if|unless|as required|where applicable|not required|not applicable|covered|repetition|no new|during detailed engineering)\b', re.I)
    rows = []
    for caption_index, caption_line in enumerate(lines):
        if not caption_re.fullmatch(caption_line['text']):
            continue
        # All column labels must be present together immediately after the
        # caption. A mention of deliverables or a numbered prose list is not a
        # matrix. The body starts at the first hierarchical section/item.
        header_end = caption_index + 1
        while header_end < min(len(lines), caption_index + 9):
            if section_re.fullmatch(lines[header_end]['text']):
                break
            header_end += 1
        header = _words(' '.join(line['text'] for line in lines[caption_index + 1:header_end]))
        if not (re.search(r'\b(?:s no|sl no|serial no|item no)\b', header)
                and all(re.search(r'\b' + word + r'\b', header) for word in ('discipline', 'deliverable', 'description', 'remarks'))
                and re.search(r'\b(?:packages?|applicability)\b', header)):
            continue
        body = []
        for line in lines[header_end:]:
            value = line['text']
            if not value or furniture_re.fullmatch(value):
                continue
            if re.match(r'^(?:table\s+\d|annexure\b|appendix\b|document\s+no\s*[.:])', value, re.I):
                break
            body.append(line)
        group, group_id = '', ''
        for index, line in enumerate(body):
            value = line['text']
            match = row_re.fullmatch(value)
            if not match:
                section = section_re.fullmatch(value)
                if section:
                    group_id, group = section.groups()
                continue
            item, fragment = match[1], match[2] or ''
            explicit_label = group if item.rsplit('.', 1)[0] == group_id and fragment.startswith(group + ' ') else ''
            contents = fragment[len(explicit_label):].strip() if explicit_label else fragment
            mark = marker_re.search(contents) if explicit_label else None
            title = contents[:mark.start()].strip() if mark else contents
            remarks = contents[mark.end():].strip() if mark else ''
            surrounding = [body[pos]['text'] for pos in (index - 1, index + 1) if 0 <= pos < len(body)]
            interleaved = any(not row_re.fullmatch(other) and not section_re.fullmatch(other) for other in surrounding)
            clear = bool(explicit_label and title and mark and not interleaved and not marker_re.search(remarks))
            applicability = 'marked' if mark else ('not_marked' if explicit_label else 'ambiguous')
            if mark and (conditional_re.search(remarks) or conditional_re.search(title)):
                applicability = 'conditional'
            title = title or fragment or value
            locator = {'line': text[:line['start']].count('\n') + 1,
                       'table_caption': caption_line['text'], 'register_item': item,
                       'applicability_status': applicability, 'package_columns_status': 'not_resolved'}
            if explicit_label:
                locator['discipline_label'] = explicit_label
            rows.append({
                'name': title, 'original_title': title, 'register_item': item,
                'discipline': normalize_register_discipline(explicit_label),
                'discipline_label': explicit_label or 'Not Specified',
                'source_group': group if item.rsplit('.', 1)[0] == group_id else '',
                'explicit_dimensions': ({'discipline': {'value': explicit_label, 'header': 'Discipline'}} if explicit_label else {}),
                'document_number': '', 'document_revision': '',
                'source_layout': 'applicability_text_register',
                'title_boundary_status': 'explicit_marked_row' if clear else 'ambiguous',
                'applicability_status': applicability, 'applicability_marks': mark[0] if mark else '',
                'source_remarks': remarks, 'package_columns_status': 'not_resolved',
                'sheet': '', 'row_number': None, 'source_line': locator['line'], 'source_locator': locator,
                'source_excerpt': text[line['start']:line['end']].strip(),
                'start': line['start'], 'end': line['end'],
            })
    return rows


def _merge_register_geometry(text, rows, structured_evidence):
    """Prefer located PDF cells only for the same page and register item."""
    from .register_geometry_cache import SCHEMA_VERSION
    geometry = (structured_evidence or {}).get('register_geometry') or {}
    if (geometry.get('schema_version') != SCHEMA_VERSION
            or geometry.get('text_sha256') != hashlib.sha256(text.encode('utf-8')).hexdigest()
            or geometry.get('status') != 'parsed'):
        return rows
    located = []
    for source in geometry.get('rows') or []:
        start, end = source.get('start'), source.get('end')
        if not (type(start) is int and type(end) is int and 0 <= start < end <= len(text)
                and source.get('register_item') is not None and source.get('original_title')):
            continue
        row = deepcopy(source)
        locator = row.setdefault('source_locator', {})
        page = text.count('\f', 0, start) + 1
        if locator.get('page') != page:
            continue
        # Literal text offsets remain distinct from reconstructed cell titles.
        row['source_excerpt'] = text[start:end]
        locator.update(character_start=start, character_end=end, quote=text[start:end])
        located.append(row)
    identities = {(row['source_locator']['page'], str(row['register_item'])) for row in located}
    fallback = [row for row in rows if (
        (row.get('source_locator') or {}).get('page') or text.count('\f', 0, row['start']) + 1,
        str(row.get('register_item')),
    ) not in identities]
    return sorted([*fallback, *located], key=lambda row: row['start'])


def register_rows_for_file(file_obj):
    from .register_geometry_cache import cached_register_geometry
    geometry = cached_register_geometry(file_obj)
    return extract_register_rows(file_obj.extracted_text or '', structured_evidence={
        'register_geometry': geometry,
    } if geometry else None)


def extract_register_rows(text, *, structured_evidence=None):
    """Return located register rows from delimited or captioned PDF text tables."""
    delimiter = _delimiter(text or '')
    if not delimiter:
        rows = sorted([*_captioned_text_register_rows(text or ''), *_applicability_text_register_rows(text or '')], key=lambda row: row['start'])
        return _merge_register_geometry(text or '', rows, structured_evidence)
    offsets = [0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    reader = csv.reader(io.StringIO(text), delimiter=delimiter, skipinitialspace=True)
    table = _RegisterTable()
    sheet = ''
    previous_line = 0
    rows = []
    for values in reader:
        first_line, previous_line = previous_line, reader.line_num
        start, end = offsets[first_line], offsets[min(reader.line_num, len(offsets) - 1)]
        if len(values) == 1:
            marker = _SHEET.match(values[0].strip())
            if marker:
                sheet = marker.group(1)
                table = _RegisterTable()
                continue
        row = table.row(values, collapsed=delimiter == '|')
        if row:
            row.update({
                'sheet': sheet, 'row_number': None,
                'source_line': first_line + 1,
                'source_locator': {'line': first_line + 1, **({'sheet': sheet} if sheet else {})},
                'source_excerpt': text[start:end].strip(), 'start': start, 'end': end,
            })
            rows.append(row)
    rows = sorted([*rows, *_captioned_text_register_rows(text), *_applicability_text_register_rows(text)], key=lambda row: row['start'])
    return _merge_register_geometry(text, rows, structured_evidence)


def extract_workbook_register_rows(file_obj):
    """Read an XLSX/XLSM stream and retain real sheet and row provenance.

    The caller owns opening/closing the source stream. Invalid workbook errors
    are intentionally propagated so callers can choose their text fallback.
    """
    import openpyxl

    position = file_obj.tell() if hasattr(file_obj, 'tell') else None
    file_obj.seek(0)
    workbook = None
    try:
        workbook = openpyxl.load_workbook(file_obj, data_only=True, read_only=True)
        rows = []
        for sheet in workbook.worksheets:
            table = _RegisterTable()
            for row_number, values in enumerate(sheet.iter_rows(values_only=True), 1):
                row = table.row(values)
                if row:
                    row.update({
                        'sheet': sheet.title, 'row_number': row_number, 'source_line': None,
                        'source_locator': {'sheet': sheet.title, 'row': row_number},
                        'source_excerpt': ' | '.join(_text(value) for value in values if value is not None),
                        'start': None, 'end': None,
                    })
                    rows.append(row)
        return rows
    finally:
        if workbook is not None:
            workbook.close()
        if position is not None:
            file_obj.seek(position)


def extract_legacy_register_rows(text):
    """Read the existing collapsed PDF register layout into the same row shape.

    This is deliberately separate from header detection: callers can retain the
    historical PDF-only workflow while including these rows beside an explicit
    spreadsheet register. Without column boundaries, retain the full title/area
    text and expose its ambiguity instead of guessing which words to remove.
    """
    matches = []
    for line in re.finditer(r'^.*$', text or '', re.M):
        match = _LEGACY_REGISTER_ROW.match(line.group(0))
        if match:
            matches.append((line, match))
    if not matches:
        return []
    rows = []
    for line, match in matches:
        title = re.sub(r'\s+', ' ', match.group('title_area')).strip()
        label = match.group('discipline').strip()
        line_number = text[:line.start()].count('\n') + 1
        locator = {'line': line_number}
        if '\f' in text:
            locator['page'] = text[:line.start()].count('\f') + 1
        rows.append({
            'name': title, 'original_title': title,
            'title_boundary_status': 'ambiguous', 'source_layout': 'collapsed_register',
            'discipline': normalize_register_discipline(label), 'discipline_label': label,
            'register_item': int(match.group('item')),
            'document_number': match.group('number').strip(),
            'document_revision': match.group('revision').strip(),
            'sheet': '', 'row_number': None, 'source_line': line_number,
            'source_locator': locator, 'source_excerpt': line.group(0).strip(),
            'start': line.start(), 'end': line.end(),
        })
    return rows
