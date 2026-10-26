"""Positioned DGS-1300-010 appendix tables; no inherited engineering values."""
import re

from .local_tables import clean, parse_size, FIELD_LIMITS


def read_dgs_table(parser, rows, page):
    text = '\n'.join(' '.join(cell[2] for cell in row['cells']) for row in rows)
    if 'DGS-1300-010' not in text or 'DETAILED PIPING CLASSES' not in text:
        return False
    headings = [row for row in rows if 'DETAILED PIPING CLASSES' in ' '.join(c[2] for c in row['cells'])]
    codes = {clean(cell[2]) for row in headings for cell in row['cells'] if re.fullmatch(r'\d{5}', clean(cell[2]))}
    header = next((row for row in rows if {'Rating', 'End', 'Schedule', 'Description', 'Notes'} <=
                   {str(word[4]) for word in row.get('words', [])}), None)
    if not header:
        return False  # Notes and pressure/temperature pages are not component tables.
    if len(codes) != 1:
        parser.issues.append({'page': page, 'reason': 'ambiguous_dgs_class'})
        return True
    code = next(iter(codes))
    parser.current = parser.classes.setdefault(code, {
        'class_code': code, 'class_full_code': code,
        'material_grade': '', 'pressure_rating': '', 'flange_facing': '',
        'corrosion_allowance': '', 'service_list': [], 'pt_rating_table': [],
        'components': [], 'raw_notes': '', 'confidence': .5,
        '_engine': parser.engine, '_source_pages': [page, page], '_source_only': True,
    })
    parser.current['_source_pages'][1] = page
    subheader = next((row for row in rows if header['y'] < row['y'] < header['y'] + 25 and
                      {'From', 'To', 'Thickness'} <= {str(w[4]) for w in row.get('words', [])}), None)
    if not subheader:
        parser.issues.append({'page': page, 'reason': 'dgs_size_header_unreadable'})
        return True
    words = header['words'] + subheader['words']
    centers = {str(w[4]): (float(w[0]) + float(w[2])) / 2 for w in words}
    if not {'Short', 'Stock', 'code', 'Rev'} <= centers.keys():
        parser.issues.append({'page': page, 'reason': 'dgs_column_layout_unsupported'})
        return True
    # The description heading is centered over a wide column. Its left edge
    # follows the narrow Schedule column, using the adjacent column spacing.
    schedule = centers['Schedule']
    boundaries = [(centers['Short'] + centers['From']) / 2,
                  (centers['From'] + centers['To']) / 2,
                  (centers['To'] + centers['Rating']) / 2,
                  (centers['Rating'] + centers['End']) / 2,
                  (centers['End'] + schedule) / 2,
                  max(float(w[2]) for w in words if w[4] in ('Schedule', 'Thickness')) + 2,
                  centers['Stock'] - (centers['Notes'] - centers['Stock']) / 2,
                  (centers['code'] + centers['Notes']) / 2,
                  (centers['Notes'] + centers['Rev']) / 2]
    if boundaries != sorted(boundaries):
        parser.issues.append({'page': page, 'reason': 'dgs_column_layout_unsupported'})
        return True
    names = ('short_code', 'size_from', 'size_to', 'rating', 'end_connection',
             'schedule', 'description', 'stock_code', 'notes', 'revision_number')
    pending = None

    def flush():
        nonlocal pending
        if not pending:
            return
        row = pending
        pending = None
        description = row['description']
        # Nouns in the literal description establish type, including qualified
        # forms such as SEAMLESS PIPE and TRUNNION MOUNTED TYPE BALL VALVE.
        kind = next((kind for kind, pattern in (
            ('valve', r'\bVALVES?\b'), ('gasket', r'\bGASKETS?\b'),
            ('bolt', r'\b(?:BOLTS?|STUDS?|NUTS?)\b'), ('flange', r'\bFLANGES?\b'),
            ('fitting', r'\b(?:ELBOW|TEE|REDUCER|CAP|COUPLING|UNION|SWAGE|NIPPLE|WELDOLET|SOCKOLET)\b'),
            ('pipe', r'\b(?:PIPE|TUBE)\b')) if re.search(pattern, description, re.I)), '')
        if row.get('ambiguous') or not kind or not parse_size(row['size_from']) or not parse_size(row['size_to']):
            parser.issues.append({'page': page, 'row': row['row'], 'reason': 'dgs_ambiguous_component_or_size'})
            return
        component = {key: row.get(key, '') for key in ('size_from', 'size_to', 'description', 'end_connection', 'revision_number')}
        component.update(component_type=kind, sub_type='', material_standard='',
                         schedule_or_rating=row['schedule'] or row['rating'])
        if any(len(component.get(key, '')) > limit for key, limit in FIELD_LIMITS.items()):
            parser.issues.append({'page': page, 'row': row['row'], 'reason': 'field_exceeds_storage_boundary'})
            return
        evidence = '; '.join(f'{key}={row[key]}' for key in ('short_code', 'rating', 'stock_code', 'notes') if row[key])
        component['notes'] = f'Source page {page}, row {row["row"]}; DGS local table extraction; engineering review required. {evidence}'.strip()
        component['_source_reference'] = {'page': page, 'row': row['row'], 'layout': 'dgs'}
        parser.current['components'].append(component)
        parser.component_count += 1

    for index, row in enumerate(rows):
        if row['y'] <= subheader['y']:
            continue
        line = ' '.join(c[2] for c in row['cells'])
        if re.search(r'\bSheet\s+\d+\s+of\b|END OF CLASS', line, re.I):
            break
        if line.strip().upper() in ('PIPES', 'FITTINGS', 'FLANGES', 'VALVES', 'GASKETS', 'BOLTING', 'BOLTS AND NUTS'):
            flush()
            continue
        fields = dict.fromkeys(names, '')
        for word in row.get('words', []):
            center = (float(word[0]) + float(word[2])) / 2
            key = names[sum(center >= boundary for boundary in boundaries)]
            fields[key] = clean(fields[key] + ' ' + str(word[4]))
        has_size = bool(fields['size_from'] or fields['size_to'])
        if has_size and fields['description']:
            flush()
            pending = {**fields, 'row': index + 1}
        elif has_size:
            if pending:
                pending['ambiguous'] = True  # Multi-diameter tee/reducer rows need review.
            else:
                parser.issues.append({'page': page, 'row': index + 1, 'reason': 'dgs_orphan_size_row'})
        elif pending:
            for key in ('description', 'end_connection', 'schedule', 'notes', 'revision_number', 'stock_code'):
                pending[key] = clean(pending[key] + ' ' + fields[key])
    flush()
    return True
