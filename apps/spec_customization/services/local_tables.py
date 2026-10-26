"""Conservative local table reader. All values are literal source evidence.

Supported inputs are positioned PDF/OCR words or pipe/tab-separated tables.
There is deliberately no free-prose extraction, unit conversion, size expansion,
material equivalence or inherited engineering default.
"""
from __future__ import annotations

import re
from statistics import median


HEADER_ALIASES = {
    'component': 'component_type', 'component type': 'component_type',
    'item': 'component_type', 'type': 'component_type',
    'subtype': 'sub_type', 'sub type': 'sub_type',
    'description': 'description', 'component description': 'description',
    'size': 'size', 'nominal size': 'size', 'nps': 'size', 'dn': 'size',
    'size from': 'size_from', 'from': 'size_from',
    'size to': 'size_to', 'to': 'size_to',
    'material': 'material_standard', 'material standard': 'material_standard',
    'specification': 'material_standard', 'material specification': 'material_standard',
    'schedule': 'schedule_or_rating', 'sch': 'schedule_or_rating',
    'rating': 'schedule_or_rating', 'schedule rating': 'schedule_or_rating',
    'schedule or rating': 'schedule_or_rating',
    'ends': 'end_connection', 'end': 'end_connection', 'end connection': 'end_connection',
    'notes': 'notes', 'remarks': 'notes', 'revision': 'revision_number', 'rev': 'revision_number',
}
COMPONENT_TYPES = (
    ('pipe', r'^(?:pipes?|tubes?)\b'),
    ('flange', r'^(?:flanges?|weld neck|slip on|blind flange)\b'),
    ('fitting', r'^(?:fittings?|elbows?|tees?|reducers?|caps?|couplings?|unions?|weldolets?|sockolets?|nipples?)\b'),
    ('valve', r'^(?:(?:gate|globe|ball|check|butterfly|plug|needle)\s+)?valves?\b'),
    ('gasket', r'^gaskets?\b'),
    ('bolt', r'^(?:bolts?|studs?|stud bolts?|nuts?)\b'),
)
FIELD_LIMITS = {'sub_type': 128, 'size_from': 32, 'size_to': 32,
                'schedule_or_rating': 128, 'material_standard': 256,
                'end_connection': 128, 'revision_number': 32}


def clean(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def header_key(value):
    return re.sub(r'[^a-z0-9]+', ' ', clean(value).lower()).strip()


def positioned_rows(words):
    """Group (x0,y0,x1,y1,text) words into visual lines and separated cells."""
    words = [word[:5] for word in words if clean(word[4])]
    if not words:
        return []
    height = median(max(1, float(w[3]) - float(w[1])) for w in words)
    lines = []
    for word in sorted(words, key=lambda w: ((float(w[1]) + float(w[3])) / 2, float(w[0]))):
        center = (float(word[1]) + float(word[3])) / 2
        if not lines or abs(center - lines[-1][0]) > height * .45:
            lines.append((center, [word]))
        else:
            lines[-1][1].append(word)
    result = []
    for _, line in lines:
        cells = []
        for word in sorted(line, key=lambda w: float(w[0])):
            if not cells or float(word[0]) - cells[-1][1] > height * 1.2:
                cells.append([float(word[0]), float(word[2]), str(word[4])])
            else:
                cells[-1][1] = float(word[2])
                cells[-1][2] += ' ' + str(word[4])
        result.append({'y': min(float(w[1]) for w in line), 'cells': cells, 'words': sorted(line, key=lambda w: float(w[0]))})
    return result


def parse_size(value):
    value = clean(value)
    number = r'(?:\d+(?:[ -]\d+/\d+|/\d+|\.\d+)?|\d*[¼½¾])'
    single = rf'(?:DN\s*|NPS\s*)?{number}\s*(?:["″]|in(?:ch(?:es)?)?|mm)?'
    if re.fullmatch(single, value, re.I):
        return value, value
    parts = re.split(r'\s+(?:to|thru|[-–—])\s+', value, flags=re.I)
    if len(parts) == 2 and all(re.fullmatch(single, part, re.I) for part in parts):
        return parts[0], parts[1]
    return None


class LocalTableParser:
    def __init__(self, header_regex, engine):
        self.header = re.compile(header_regex, re.I)
        self.numeric_header = re.compile(r'(?:PIPING\s+(?:MATERIAL\s+)?(?:CLASS|SPEC(?:IFICATION)?)|PIPE\s+CLASS|CLASS)\s*[:\-]?\s*(?P<code>\d{6}\s*[-–]\s*[A-Z])\b', re.I)
        self.engine = engine
        self.classes = {}
        self.current = None
        self.columns = None
        self.header_fields = []
        self.issues = []
        self.component_count = 0
        self.size_unit = ''

    def _class(self, text, page):
        match = self.numeric_header.search(text) or self.header.search(text)
        if not match:
            return False
        code = re.sub(r'\s+', '', match.group('code')).upper().replace('–', '-')
        self.current = self.classes.setdefault(code, {
            'class_code': code, 'class_full_code': match.group(0),
            'material_grade': '', 'pressure_rating': '', 'flange_facing': '',
            'corrosion_allowance': '', 'service_list': [], 'pt_rating_table': [],
            'components': [], 'raw_notes': '', 'confidence': .5,
            '_engine': self.engine, '_source_pages': [page, page], '_source_only': True,
        })
        self.current['_source_pages'][1] = page
        self.columns = None
        return True

    def read_rows(self, rows, page):
        # A class must be restated on each page. Never attach a continuation
        # table to the preceding page's class based on proximity alone.
        self.current = None
        self.columns = None
        if self._read_nominal_matrix(rows, page):
            return
        for index, row in enumerate(rows):
            cells = row['cells']
            text = ' '.join(clean(cell[2]) for cell in cells)
            if self._class(text, page):
                continue
            keys = [HEADER_ALIASES.get(header_key(cell[2])) for cell in cells]
            mapped = [key for key in keys if key]
            if (len(mapped) >= 3 and len(mapped) == len(set(mapped))
                    and ('description' in mapped or 'component_type' in mapped)
                    and ('size' in mapped or 'size_from' in mapped)):
                self.columns = [(float(cell[0]), key) for cell, key in zip(cells, keys)]
                self.header_fields = mapped
                self.size_unit = next((clean(cell[2]).upper() for cell in cells if clean(cell[2]).upper() in ('DN', 'NPS')), '')
                continue
            if not self.columns:
                continue
            if not self.current:
                self.issues.append({'page': page, 'row': index + 1, 'reason': 'missing_class_heading'})
                continue
            # Bound each column by the next heading's start. Preserve blank
            # cells and reject overflow instead of shifting fields left.
            fields = {}
            for x0, _, value in cells:
                positions = [i for i, (left, _) in enumerate(self.columns) if x0 >= left - 2]
                if not positions:
                    continue
                key = self.columns[max(positions)][1]
                if key:
                    fields[key] = clean(fields.get(key, '') + ' ' + clean(value))
            if not any(fields.values()):
                continue
            label = fields.get('component_type') or fields.get('description', '')
            component_type = next((kind for kind, pattern in COMPONENT_TYPES if re.search(pattern, label, re.I)), '')
            sizes = parse_size(fields.get('size', '')) if 'size' in self.header_fields else (
                parse_size(fields.get('size_from', '')) if fields.get('size_from') else None)
            if sizes and 'size_to' in self.header_fields:
                upper = parse_size(fields.get('size_to', ''))
                sizes = (sizes[0], upper[1]) if upper else None
            if not component_type or not sizes:
                self.issues.append({'page': page, 'row': index + 1, 'reason': 'unsupported_component_or_size'})
                continue
            if self.size_unit:
                sizes = tuple(value if re.match(r'^(?:DN|NPS)\b', value, re.I) else f'{self.size_unit} {value}' for value in sizes)
            component = {key: fields.get(key, '') for key in (
                'sub_type', 'description', 'material_standard', 'schedule_or_rating',
                'end_connection', 'notes', 'revision_number')}
            component.update(component_type=component_type, size_from=sizes[0], size_to=sizes[1])
            if any(len(component.get(key, '')) > limit for key, limit in FIELD_LIMITS.items()):
                self.issues.append({'page': page, 'row': index + 1, 'reason': 'field_exceeds_storage_boundary'})
                continue
            locator = f'Source page {page}, row {index + 1}; local table extraction; engineering review required.'
            component['notes'] = clean(component['notes'] + ' ' + locator)
            component['_source_reference'] = {'page': page, 'row': index + 1}
            self.current['components'].append(component)
            self.current['_source_pages'][1] = page
            self.component_count += 1

    def _read_nominal_matrix(self, rows, page):
        """BUHASA item-code/nominal-size matrix, identified by source headings.

        Each populated size cell is one literal component candidate. Item and
        cell codes remain separate evidence; they are never guessed into a
        commodity code. Material/schedule/end data are not inferred from codes.
        """
        text = '\n'.join(' '.join(cell[2] for cell in row['cells']) for row in rows)
        if 'PIPING COMPONENTS' not in text.upper() or 'nominal pipe size' not in text.lower():
            return False
        matches = list(self.numeric_header.finditer(text))
        codes = {match.group('code') for match in matches}
        if len(codes) != 1:
            self.issues.append({'page': page, 'reason': 'ambiguous_matrix_class'})
            return True
        self._class(matches[0].group(0), page)
        unit = 'DN' if re.search(r'\bMetric units\b', text, re.I) else 'NPS' if re.search(r'\bImperial units\b', text, re.I) else None
        if not unit:
            self.issues.append({'page': page, 'reason': 'matrix_unit_system_missing'})
            return True
        title_index = next(i for i, row in enumerate(rows) if 'nominal pipe size' in ' '.join(cell[2] for cell in row['cells']).lower())
        header = None
        for row in rows[title_index + 1:title_index + 4]:
            words = row.get('words', [])
            if len(words) >= 3 and all(parse_size(word[4]) for word in words):
                header = [(float(word[0]), clean(word[4])) for word in words]
                header_y = row['y']
                break
        if not header:
            self.issues.append({'page': page, 'reason': 'matrix_size_header_unreadable'})
            return True
        for index, row in enumerate(rows):
            if row['y'] <= header_y:
                continue
            words = row.get('words', [])
            item = next((word for word in words if re.fullmatch(r'\d{6}', str(word[4])) and float(word[0]) < header[0][0]), None)
            if not item:
                continue
            item_code = clean(' '.join(str(word[4]) for word in words if float(item[0]) <= float(word[0]) < header[0][0]))
            description = clean(' '.join(str(word[4]) for word in words if float(word[0]) < float(item[0]) - 2))
            kind = next((kind for kind, pattern in COMPONENT_TYPES if re.search(pattern, description, re.I)), '')
            if not kind:
                if re.search(r'\bflange|spectacle blind|spacer ring|spade\b', description, re.I):
                    kind = 'flange'
                elif re.search(r'\b(?:elbow|tee|reducer|cap|olet|coupling)\b', description, re.I):
                    kind = 'fitting'
            values = [word for word in words if float(word[0]) >= header[0][0]]
            if not kind or not values:
                self.issues.append({'page': page, 'row': index + 1, 'reason': 'unsupported_or_empty_matrix_row'})
                continue
            assigned = {}
            for word in values:
                column = min(range(len(header)), key=lambda i: abs(float(word[0]) - header[i][0]))
                if abs(float(word[0]) - header[column][0]) > 8 or column in assigned or not re.fullmatch(r'\d+', str(word[4])):
                    self.issues.append({'page': page, 'row': index + 1, 'reason': 'ambiguous_matrix_cell'})
                    assigned = {}
                    break
                assigned[column] = str(word[4])
            for column, code in assigned.items():
                size = f'{unit} {header[column][1]}'
                component = {'component_type': kind, 'sub_type': '', 'description': description,
                             'size_from': size, 'size_to': size, 'material_standard': '',
                             'schedule_or_rating': '', 'end_connection': '',
                             'notes': f'Source page {page}, row {index + 1}; item code {item_code}; size-cell code {code}; engineering review required.',
                             '_source_reference': {'page': page, 'row': index + 1, 'item_code': item_code, 'cell_code': code}}
                self.current['components'].append(component)
                self.component_count += 1
        return True

    def read_text(self, text, page):
        rows = []
        for index, line in enumerate(text.splitlines()):
            # Pipe and tab delimiters carry column evidence. Prose is only
            # used for class headings, never guessed into component fields.
            cells = re.split(r'\s*\|\s*|\t+', line.strip())
            rows.append({'y': index, 'cells': [[i * 100, i * 100 + 90, cell] for i, cell in enumerate(cells)]})
        self.read_rows(rows, page)

    def result(self):
        return {'piping_classes': list(self.classes.values()), 'engine_used': self.engine,
                'local_report': {'components': self.component_count, 'issues': self.issues,
                                 'review_required': True}}
