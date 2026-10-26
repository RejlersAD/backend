"""Native LNG grids with explicit merged cells and verified class continuation.

Only source cell spans share values. Blank cells never inherit neighbouring text.
Class context requires consecutive physical/logical pages of the same document.
"""
from __future__ import annotations

import re

from .local_tables import clean, parse_size, FIELD_LIMITS


DOC = re.compile(r'\bA-100-A4Z-0-25020\b', re.I)
CLASS_MENTION = re.compile(r'^\s*PIPING\s+SPEC\s*:\s*(.*)$', re.I | re.M)
CLASS = re.compile(r'^\s*PIPING\s+SPEC\s*:\s*([A-Z0-9][A-Z0-9._/-]{0,31})(?:\s+\(NOTE[\s-]*\d+\))?\s*$', re.I | re.M)
FOOTER = re.compile(r'\bPage\s+(\d+)\s+of\s+(\d+)\b', re.I)


def table_page(text):
    return bool(DOC.search(text) and re.search(r'PART\s+NAME', text, re.I)
                and re.search(r'\bDESCRIPTION\b', text, re.I)
                and re.search(r'\bSYMBOL\b', text, re.I))


def identity(text):
    doc = DOC.search(text)
    footer = FOOTER.findall(text)
    return (doc.group(0).upper(), int(footer[-1][0]), int(footer[-1][1])) if doc and len(footer) == 1 else None


def consecutive(previous, current):
    return bool(previous and current and previous[0] == current[0]
                and previous[2] == current[2] and previous[1] + 1 == current[1])


class Grid:
    """Resolve PyMuPDF None cells using the actual parent cell rectangle."""
    def __init__(self, table):
        self.values = table.extract()
        self.rows = table.rows
        self.columns = table.col_count
        self.anchors = []
        for r, row in enumerate(self.rows):
            for c, box in enumerate(row.cells):
                if box is not None:
                    self.anchors.append((r, c, box, clean(self.values[r][c])))
        self.xs = sorted({round(float(box[0]), 3) for _, _, box, _ in self.anchors}
                         | {round(float(box[2]), 3) for _, _, box, _ in self.anchors})

    def cell(self, r, c):
        direct = self.rows[r].cells[c]
        if direct is not None:
            return clean(self.values[r][c]), r, tuple(direct)
        if len(self.xs) != self.columns + 1:
            return '', None, None
        x = (self.xs[c] + self.xs[c + 1]) / 2
        row_boxes = [box for box in self.rows[r].cells if box is not None]
        if not row_boxes:
            return '', None, None
        # Use the top of this physical row, not the center of a spanning cell.
        y = max(float(box[1]) for box in row_boxes) + .1
        matches = [(value, ar, tuple(box)) for ar, _, box, value in self.anchors
                   if ar <= r and float(box[0]) <= x < float(box[2])
                   and float(box[1]) <= y < float(box[3])]
        return matches[0] if len(matches) == 1 else ('', None, None)

    def header(self):
        aliases = {'rev no': 'revision_number', 'part name': 'part_name',
                   'part symbol': 'part_symbol', 'size from': 'size_from',
                   'size to': 'size_to', 'w t': 'schedule_or_rating',
                   'description': 'description', 'me no': 'me_number',
                   'commodity code': 'commodity_code', 'notes': 'source_notes'}
        for depth in (1, 2, 3):
            if depth >= len(self.values):
                break
            columns = {}
            for c in range(self.columns):
                parts = list(dict.fromkeys(self.cell(r, c)[0] for r in range(depth) if self.cell(r, c)[0]))
                label = re.sub(r'[^a-z0-9]+', ' ', ' '.join(parts).lower()).strip()
                field = aliases.get(label)
                if field:
                    if field in columns:
                        return None
                    columns[field] = c
            if {'part_name', 'size_from', 'size_to', 'description'} <= columns.keys():
                return depth, columns
        return None


def size_bounds(lower, upper):
    """Keep literal values and open bounds; never invent a minimum/maximum."""
    if parse_size(lower) and parse_size(upper):
        return lower, upper
    for value, other in ((lower, upper), (upper, lower)):
        match = re.fullmatch(r'(.+?)\s*(?:&|AND)\s*(BELOW|SMALLER|ABOVE|LARGER)', value, re.I)
        if (not other or other == value) and match and parse_size(clean(match[1])):
            bound = clean(match[1])
            return ('', bound) if match[2].upper() in ('BELOW', 'SMALLER') else (bound, '')
    if lower and parse_size(lower) and not upper:
        return lower, ''
    if upper and parse_size(upper) and not lower:
        return '', upper
    return None


def component_kind(name):
    for kind, pattern in (
        ('valve', r'\bVALVES?\b'), ('gasket', r'\bGASKETS?\b'),
        ('bolt', r'\b(?:BOLTS?|STUDS?|NUTS?|BOLTING)\b'),
        ('flange', r'\b(?:FLANGES?|SPECTACLE|SPADE|SPACER)\b'),
        ('fitting', r'\b(?:FITTINGS?|ELBOWS?|TEES?|REDUCERS?|CAPS?|COUPLINGS?|UNIONS?|NIPPLES?|SWAGES?|WELDOLETS?|SOCKOLETS?)\b'),
        ('pipe', r'\b(?:PIPES?|PIPING|TUBES?)\b'),
    ):
        if re.search(pattern, name, re.I):
            return kind
    return ''


class LNGTableReader:
    def __init__(self, parser, lookback=64):
        self.parser = parser
        self.lookback = lookback
        self.context = None

    def _context(self, document, index, text):
        own = set(CLASS.findall(text))
        mentions = CLASS_MENTION.findall(text)
        if mentions:
            if len({clean(value).upper() for value in mentions}) != 1:
                return None
            return (next(iter(own)).upper(), index + 1) if len(own) == 1 else None
        current = identity(text)
        if self.context:
            last_index, last_identity, code, anchor = self.context
            if last_index + 1 == index and consecutive(last_identity, current):
                return code, anchor
        for prior in range(index - 1, max(-1, index - self.lookback - 1), -1):
            previous_text = document[prior].get_text('text', sort=True)
            previous = identity(previous_text)
            if not table_page(previous_text) or not consecutive(previous, current):
                break
            codes = set(CLASS.findall(previous_text))
            mentions = CLASS_MENTION.findall(previous_text)
            if mentions:
                if len({clean(value).upper() for value in mentions}) != 1:
                    return None
                return (next(iter(codes)).upper(), prior + 1) if len(codes) == 1 else None
            current = previous
        return None

    def read_page(self, document, index, text, words):
        if not table_page(text):
            self.context = None
            return False
        page = index + 1
        context = self._context(document, index, text)
        if not context:
            self.context = None
            self.parser.issues.append({'page': page, 'reason': 'lng_class_context_unverified'})
            return True
        code, anchor = context
        self.context = (index, identity(text), code, anchor)
        anchor_text = text if anchor == page else document[anchor - 1].get_text('text', sort=True)
        source_heading = 'PIPING SPEC: ' + clean(CLASS_MENTION.findall(anchor_text)[0])
        if re.search(r'\(NOTE[\s-]*\d+\)', source_heading, re.I):
            self.parser.issues.append({'page': page, 'reason': 'lng_class_heading_note_requires_review'})
        cls = self.parser.classes.setdefault(code, {
            'class_code': code, 'class_full_code': source_heading,
            'material_grade': '', 'pressure_rating': '', 'flange_facing': '',
            'corrosion_allowance': '', 'service_list': [], 'pt_rating_table': [],
            'components': [], 'raw_notes': f'LNG source class heading on page {anchor}: {source_heading}', 'confidence': .5,
            '_engine': self.parser.engine, '_source_pages': [anchor, page], '_source_only': True,
        })
        cls['_source_pages'] = [min(cls['_source_pages'][0], anchor), max(cls['_source_pages'][1], page)]
        header_y = [float(w[1]) for w in words if re.fullmatch(r'W\.?T\.?', str(w[4]), re.I)]
        top = max(0, min(header_y) - 30) if header_y else 140
        try:
            tables = document[index].find_tables(clip=(0, top, document[index].rect.width, document[index].rect.height)).tables
        except (AttributeError, RuntimeError, ValueError):
            self.context = None
            self.parser.issues.append({'page': page, 'reason': 'lng_native_grid_unavailable'})
            return True
        matched = False
        for table_index, table in enumerate(tables):
            grid = Grid(table)
            header = grid.header()
            if not header:
                continue
            matched = True
            depth, columns = header
            for row in range(depth, len(grid.values)):
                fields = {field: grid.cell(row, column) for field, column in columns.items()}
                values = {field: cell[0] for field, cell in fields.items()}
                if not any(values.values()):
                    continue
                bounds = size_bounds(values['size_from'], values['size_to'])
                kind = component_kind(values['part_name'])
                if not bounds or not values['part_name'] or not values['description']:
                    self.parser.issues.append({'page': page, 'table': table_index + 1, 'row': row + 1,
                                               'reason': 'lng_component_or_size_requires_review'})
                    continue
                if not kind:
                    kind = 'other'
                    self.parser.issues.append({'page': page, 'row': row + 1, 'reason': 'lng_component_classification_requires_review'})
                component = {field: values.get(field, '') for field in ('description', 'schedule_or_rating', 'revision_number')}
                component.update(component_type=kind, sub_type=values['part_name'],
                                 size_from=bounds[0], size_to=bounds[1], material_standard='', end_connection='')
                if any(len(component.get(field, '')) > limit for field, limit in FIELD_LIMITS.items()):
                    self.parser.issues.append({'page': page, 'row': row + 1, 'reason': 'field_exceeds_storage_boundary'})
                    continue
                source = {'layout': 'lng', 'page': page, 'table': table_index + 1, 'row': row + 1,
                          'class_heading_page': anchor, 'cells': {field: {'row': cell[1] + 1, 'bbox': list(cell[2])}
                           for field, cell in fields.items() if cell[1] is not None}}
                evidence = '; '.join(f'{field}={values[field]}' for field in
                                     ('part_symbol', 'me_number', 'commodity_code', 'source_notes') if values.get(field))
                parents = '; '.join(f'{field} source row {cell[1] + 1}' for field, cell in fields.items()
                                    if cell[1] is not None and cell[1] != row)
                component['notes'] = (f'Source page {page}, LNG table {table_index + 1}, row {row + 1}; '
                                      f'class heading page {anchor}; engineering review required. {evidence}; {parents}').strip(' ;')
                if not all(bounds):
                    component['notes'] += f'; source size constraint={values["size_from"]} {values["size_to"]}'
                    if not re.search(r'(?:&|AND)\s*(?:BELOW|SMALLER|ABOVE|LARGER)',
                                     values['size_from'] + ' ' + values['size_to'], re.I):
                        self.parser.issues.append({'page': page, 'row': row + 1, 'reason': 'lng_size_bound_requires_review'})
                if re.search(r'\bAS\s+(?:ABOVE|BELOW)|\bREFER\b|\bSEE\s+NOTE', values['description'], re.I):
                    self.parser.issues.append({'page': page, 'row': row + 1, 'reason': 'lng_description_reference_requires_review'})
                component['_source_reference'] = source
                cls['components'].append(component)
                self.parser.component_count += 1
        if not matched:
            self.context = None
            self.parser.issues.append({'page': page, 'reason': 'lng_grid_header_unreadable'})
        return True
