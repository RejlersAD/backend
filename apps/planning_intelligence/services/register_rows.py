"""Read deliverable-register rows without substituting catalogue document names.

These helpers do no storage access or database writes. Workbook extraction accepts
an already-open binary stream. Text extraction also understands older pipe-joined
Excel parses, but labels their provenance as extracted lines, not worksheet rows.
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata


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


def extract_register_rows(text):
    """Return exact source rows from header-driven CSV, TSV or pipe-joined text."""
    delimiter = _delimiter(text or '')
    if not delimiter:
        return []
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
    return rows


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
