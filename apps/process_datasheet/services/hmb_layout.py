import re


def label(value):
    return ' '.join(str(value or '').lower().split()).rstrip(':')


def section_identity(value):
    normalized = label(value)
    return {
        'overall': 'general', 'general': 'general', 'conditions': 'general',
        'vapor': 'vapour', 'vapour phase': 'vapour', 'vapor phase': 'vapour',
        'liquid phase': 'light_liquid', 'aqueous phase': 'heavy_liquid',
    }.get(normalized, re.sub(r'\s+', '_', normalized))


def detect_template_layout(workbook, role):
    candidates = []
    for sheet in workbook.worksheets:
        if sheet.sheet_state != 'visible':
            continue
        cells = [(row, column, label(sheet.cell(row, column).value))
                 for row in range(1, min(sheet.max_row, 80) + 1)
                 for column in range(1, min(sheet.max_column, 24) + 1)]
        properties = [(row, column) for row, column, value in cells if value in {'property', 'parameter', 'property name'}]
        units = [(row, column) for row, column, value in cells if value in {'unit', 'units', 'uom'} or value.endswith('pfd no. unit')]
        for property_row, property_column in properties:
            nearby_units = [(row, column) for row, column in units
                            if abs(row - property_row) <= 2 and column > property_column]
            if len(nearby_units) != 1:
                continue
            unit_row, unit_column = nearby_units[0]
            header_row = max(property_row, unit_row)
            phase_columns = [column for row, column, value in cells
                             if abs(row - property_row) <= 2 and column < property_column
                             and value in {'phase', 'section'}]
            phase_column = phase_columns[0] if len(set(phase_columns)) == 1 else None
            data_column = max(property_column, unit_column) + 1
            stream_rows = [row for row, column, value in cells
                           if column == unit_column and row < header_row and value in {'stream', 'stream id', 'stream number'}]
            if role == 'master':
                if len(stream_rows) == 1:
                    stream_row = stream_rows[0]
                elif not stream_rows:
                    stream_row = header_row
                else:
                    continue
                headings = [sheet.cell(stream_row, column).value for column in range(data_column, sheet.max_column + 1)]
                if not any(value not in (None, '') for value in headings):
                    continue
                if any(str(value or '').startswith('=') or re.match(r'^case\s+[abc]\s*\(', str(value or ''), re.I) for value in headings):
                    continue
            else:
                stream_row = None
                if not any(sheet.cell(header_row, column).value not in (None, '') for column in range(data_column, sheet.max_column + 1)):
                    continue
            description_rows = [row for row, column, value in cells if column == unit_column and row < header_row and value == 'description']
            pfd_rows = [row for row, column, value in cells if column == unit_column and row < header_row and value in {'pfd no.', 'pfd no', 'pfd number'}]
            candidates.append({'sheet_name': sheet.title, 'header_row': header_row,
                'property_col': property_column, 'unit_col': unit_column,
                'phase_col': phase_column, 'stream_start_col': data_column,
                'stream_header_row': stream_row,
                'stream_label_row': stream_rows[0] if len(stream_rows) == 1 else None,
                'stream_description_row': description_rows[0] if len(description_rows) == 1 else None,
                'pfd_row': pfd_rows[0] if len(pfd_rows) == 1 else None})
    if len(candidates) != 1:
        detail = 'multiple matching worksheets or headers' if candidates else 'no supported property/unit headers'
        raise ValueError(f'Cannot identify a unique {role} template layout: {detail}. Use one visible data sheet with Property and Unit headers.')
    return candidates[0]


def template_sections(sheet, layout):
    sections = []
    current = None
    identities = set()
    for row in range(layout['header_row'] + 1, sheet.max_row + 1):
        marker = sheet.cell(row, layout['phase_col']).value if layout['phase_col'] else None
        if marker:
            key = section_identity(marker)
            if current is None or current['key'] != key:
                current = {'key': key, 'label': str(marker).strip(), 'start_row': row, 'end_row': row, 'properties': []}
                sections.append(current)
        name = sheet.cell(row, layout['property_col']).value
        if name in (None, ''):
            continue
        if current is None:
            current = {'key': 'general', 'label': 'General', 'start_row': row, 'end_row': row, 'properties': []}
            sections.append(current)
        unit = str(sheet.cell(row, layout['unit_col']).value or '').strip()
        identity = (current['key'], label(name), label(unit))
        if identity in identities:
            raise ValueError(f'Duplicate property/phase/unit at {sheet.title}!{sheet.cell(row, layout["property_col"]).coordinate}.')
        identities.add(identity)
        current['end_row'] = row
        current['properties'].append({'row': row, 'property': str(name).strip(), 'unit': unit})
    sections = [section for section in sections if section['properties']]
    if not sections:
        raise ValueError('Template contains no properties below its headers.')
    return sections