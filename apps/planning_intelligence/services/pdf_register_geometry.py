"""Read explicitly captioned PDF applicability registers using their cell rules.

The text extractor's reading order is not a table schema. This optional layer
keeps the original text unchanged and supplies separate page/cell evidence.
An X in a merged cell is one shared-cell assertion, not six inferred marks.
"""
from __future__ import annotations

import re
from bisect import bisect_left, bisect_right

from .register_rows import normalize_register_discipline


GEOMETRY_VERSION = 'pdf-register-geometry-v2'
MAX_PAGES = 500
MAX_ROWS = 20_000
MAX_PAGE_WORDS = 150_000
_CAPTION = re.compile(r'^\s*(?:table\s+\w+\s*[:.\-]?\s*)?(?:applicable\s+deliverables\b.*|deliverables\b.*\bapplicability\b.*)$', re.I)
# Only applied inside a verified serial column, never to ordinary prose. These
# are conventional item identifiers, not a project-specific WBS depth.
_ITEM_PATTERN = r'(?:\d+(?:\.\d+)*|(?:[A-Za-z]{1,12}-)+\d+(?:[.-]\d+)*[A-Za-z]?)'
_ITEM = re.compile(r'^' + _ITEM_PATTERN + r'$')
_CONDITIONAL = re.compile(r'\b(?:if|unless|as required|where applicable|where required|if required|no new|during detailed engineering|to be assessed)\b', re.I)
_NOT_REQUIRED = re.compile(r'\b(?:not required|not applicable|not in (?:the )?scope|out of scope)\b', re.I)
_BUNDLED = re.compile(
    r'\b(?:no separate (?:report|deliverable|document)|repetition|'
    r'(?<!not )(?:included (?:in|within)|covered (?:in|by)|part of)\s+(?:'
    r'(?!(?:this|these)\b)(?:(?:the|a|an)\s+)?(?:[\w&/-]+\s+){0,8}'
    r'(?:report|document|deliverable)|'
    r'(?:(?:sr\.?\s*no\.?|item(?:\s+no\.?)?|row)\s*)?\d+(?:[.,]\d+)+))\b', re.I,
)


def _center(word):
    return ((word['x0'] + word['x1']) / 2, (word['top'] + word['bottom']) / 2)


def _box(box):
    return [round(float(value), 3) for value in box]


def _cluster(values, tolerance=1):
    groups = []
    for value in sorted(values):
        if groups and value - groups[-1][-1] <= tolerance:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [sum(group) / len(group) for group in groups]


def _cell_words(words, bounds):
    left, top, right, bottom = bounds
    return [word for word in words if left < _center(word)[0] < right and top < _center(word)[1] < bottom]


def _literal_text(words):
    """Retain line endings for evidence; normalize only the displayed title."""
    lines = []
    for word in sorted(words, key=lambda w: (w['top'], w['x0'])):
        if not lines or abs(word['top'] - lines[-1][0]) > 2:
            lines.append((word['top'], [word]))
        else:
            lines[-1][1].append(word)
    return '\n'.join(' '.join(word['text'] for word in sorted(line, key=lambda w: w['x0'])) for _, line in lines)


def _display_text(literal):
    # A printed word wrapping at the cell edge is not a title/remarks boundary.
    value = re.sub(r'(?<=\w)-\n(?=[a-z])', '', literal)
    return re.sub(r'\s+', ' ', value).strip()


def _header(page, text):
    captions = [line.strip() for line in text.splitlines() if _CAPTION.fullmatch(line.strip())]
    if not captions:
        return None
    for table in page.find_tables():
        extracted = table.extract()
        candidates = []
        for cells, values in zip(table.rows[:5], extracted[:5]):
            for box, value in zip(cells.cells, values):
                if box and value:
                    candidates.append((box, _display_text(value)))

        def find(pattern):
            return next((box for box, value in candidates if re.fullmatch(pattern, value, re.I)), None)

        serial = find(r'(?:s\.?\s*no\.?|sl\.?\s*no\.?|sr\.?\s*no\.?|item(?:\s+no\.?)?|serial(?:\s+number)?)')
        discipline = find(r'discipline')
        title = find(r'(?:document\s*/\s*)?deliverable(?:\s+(?:description|title))?|document\s+title')
        remarks = find(r'remarks?|comments?')
        if not all((serial, discipline, title, remarks)):
            continue
        if not any(re.search(r'\b(?:packages?|applicability)\b', value, re.I) for _, value in candidates):
            continue
        if not serial[2] <= discipline[0] + 1 <= discipline[2] <= title[0] + 1 < title[2] < remarks[0] < remarks[2]:
            continue
        package_cells = sorted(
            ({'label': value, 'left': box[0], 'right': box[2]} for box, value in candidates
             if title[2] - 1 <= box[0] < remarks[0] and re.fullmatch(r'(?:WP\s*)?\d+', value, re.I)),
            key=lambda cell: cell['left'],
        )
        if not package_cells or len({cell['label'] for cell in package_cells}) != len(package_cells):
            continue
        caption = captions[-1]
        # An explicit inverse legend always takes precedence over an applicable
        # caption. A different annexure cannot inherit this header's meaning.
        inverse = bool(re.search(r'\bX\s*(?:[=:\-\u2013\u2014]|means|indicates|denotes)\s*(?:not required|not applicable|excluded)', text, re.I))
        return {
            'caption': caption, 'serial': (serial[0], serial[2]),
            'discipline': (discipline[0], discipline[2]), 'title': (title[0], title[2]),
            'remarks': (remarks[0], remarks[2]), 'packages': package_cells,
            'body_top': max(serial[3], discipline[3], remarks[3]),
            'width': float(page.width), 'mark_meaning': 'not_required' if inverse else 'applicable',
        }
    return None


def _row_bounds(page, context, word):
    """Use serial-cell horizontal rules, including open continuation-page tops."""
    x, y = _center(word)
    left, right = context['serial']
    rules = [edge['top'] for edge in page.edges if edge.get('orientation') == 'h'
             and edge['x0'] <= x <= edge['x1'] and edge['x1'] - edge['x0'] >= (right - left) * .6]
    # Some PDFs draw an open first row using only the sides. A side segment's
    # endpoints are real row evidence; the page margin is never a guessed rule.
    for edge in page.edges:
        if edge.get('orientation') == 'v' and min(abs(edge['x0'] - left), abs(edge['x0'] - right)) < 1:
            if edge['top'] < y < edge['bottom']:
                rules.extend((edge['top'], edge['bottom']))
    rules = _cluster(rules)
    below, above = bisect_right(rules, y), bisect_left(rules, y) - 1
    if above < 0 or below >= len(rules):
        return None
    top, bottom = rules[above], rules[below]
    if not top < word['top'] < word['bottom'] < bottom:
        return None
    return top, bottom


def _title_cell_is_ruled(page, context, top, bottom):
    middle = (top + bottom) / 2
    return all(any(edge.get('orientation') == 'v' and abs(edge['x0'] - side) < 1
                   and edge['top'] < middle < edge['bottom'] for edge in page.edges)
               for side in context['title'])


def _applicability(page, words, context, top, bottom):
    left, right = context['title'][1], context['remarks'][0]
    middle = (top + bottom) / 2
    boundaries = _cluster([edge['x0'] for edge in page.edges
                           if edge.get('orientation') == 'v' and left - 1 < edge['x0'] < right + 1
                           and edge['top'] < middle < edge['bottom']])
    marks = [word for word in _cell_words(words, (left, top, right, bottom)) if word['text'].casefold() == 'x']
    cells = []
    for word in marks:
        x, _ = _center(word)
        lower, upper = bisect_left(boundaries, x) - 1, bisect_right(boundaries, x)
        if lower < 0 or upper >= len(boundaries):
            cells.append({'mark': word['text'], 'package_labels': [], 'merged': None,
                          'bbox': _box((word['x0'], word['top'], word['x1'], word['bottom'])), 'mapping_status': 'unresolved'})
            continue
        x0, x1 = boundaries[lower], boundaries[upper]
        labels = [cell['label'] for cell in context['packages'] if x0 - 1 <= (cell['left'] + cell['right']) / 2 <= x1 + 1]
        cells.append({'mark': word['text'], 'package_labels': labels, 'merged': len(labels) > 1,
                      'bbox': _box((x0, top, x1, bottom)), 'mapping_status': 'shared_cell' if len(labels) > 1 else 'individual_cell'})
    return cells


class PdfRegisterGeometryExtractor:
    """Per-document extractor; instantiate afresh for each source file."""

    def __init__(self):
        self.context = None
        self.previous_page = None

    def extract_page(self, page, page_number, text=None):
        text = page.extract_text() or '' if text is None else text
        words = None
        if self.previous_page is not None and page_number != self.previous_page + 1:
            self.context = None
        self.previous_page = page_number
        fresh = _header(page, text)
        if fresh:
            self.context = fresh
        elif self.context:
            if abs(float(page.width) - self.context['width']) > 1:
                self.context = None
            elif re.search(r'\b(?:ANNEXURE|APPENDIX)\b', text, re.I):
                words = page.extract_words(x_tolerance=1, y_tolerance=2)
                # A page number before an annex heading is not a register row.
                # Anchor this boundary to actual serial/title cell rules, while
                # allowing the original annex's footer below continuation rows.
                first_row_top = None
                left, right = self.context['serial']
                for word in sorted(words, key=lambda value: value['top']):
                    if not (left < _center(word)[0] < right and _ITEM.fullmatch(word['text'])):
                        continue
                    bounds = _row_bounds(page, self.context, word)
                    if bounds and _title_cell_is_ruled(page, self.context, *bounds):
                        first_row_top = bounds[0]
                        break
                if any(re.match(r'^(?:ANNEXURE|APPENDIX)\b', word['text'], re.I)
                       and (first_row_top is None or word['top'] < first_row_top) for word in words):
                    self.context = None
        context = self.context
        if not context:
            return []
        if words is None:
            words = page.extract_words(x_tolerance=1, y_tolerance=2)
        if len(words) > MAX_PAGE_WORDS:
            raise ValueError('pdf_register_geometry_page_word_limit')
        serial_left, serial_right = context['serial']
        items = [word for word in words if serial_left < _center(word)[0] < serial_right and _ITEM.fullmatch(word['text'])
                 and (not fresh or word['top'] > context['body_top'])]
        if not items:
            self.context = None
            return []
        # A remarks cell may span several serial rows. Its text is shared
        # evidence for that explicit span, even when vertically centered beside
        # only the last row. Keep the actual span rather than slicing its words.
        table_cells = [cell for table in page.find_tables() for cell in table.cells]
        rows = []
        for item in items:
            bounds = _row_bounds(page, context, item)
            if bounds is None:
                continue
            top, bottom = bounds
            # Incomplete ruling that encloses several serials is not a row.
            if sum(top < _center(other)[1] < bottom for other in items) != 1:
                continue
            # A discipline heading may merge across the title column. Long
            # labels then visually cross the header's nominal x boundary; they
            # are not deliverable titles. Require the real title-cell sides at
            # this row before using those header-derived boundaries.
            if not _title_cell_is_ruled(page, context, top, bottom):
                continue
            boxes = {key: (context[key][0], top, context[key][1], bottom) for key in ('serial', 'discipline', 'title', 'remarks')}
            remarks_middle = sum(context['remarks']) / 2
            remarks_cell = next((cell for cell in table_cells
                                 if cell[0] < remarks_middle < cell[2] and cell[1] < _center(item)[1] < cell[3]
                                 and abs(cell[0] - context['remarks'][0]) < 1
                                 and abs(cell[2] - context['remarks'][1]) < 1), None)
            shared_remarks = []
            if remarks_cell and (remarks_cell[1] < top - 1 or remarks_cell[3] > bottom + 1):
                boxes['remarks'] = remarks_cell
                shared_remarks = [other['text'] for other in items if remarks_cell[1] < _center(other)[1] < remarks_cell[3]]
            literal = {key: _literal_text(_cell_words(words, box)) for key, box in boxes.items()}
            title, discipline, remarks = (_display_text(literal[key]) for key in ('title', 'discipline', 'remarks'))
            if not title or not discipline:
                continue
            cells = _applicability(page, words, context, top, bottom)
            status = 'marked' if cells else 'not_marked'
            combined = title + ' ' + remarks
            if cells and context['mark_meaning'] == 'not_required':
                status = 'not_required'
            elif _BUNDLED.search(combined):
                status = 'bundled'
            elif _NOT_REQUIRED.search(combined):
                status = 'not_required'
            elif _CONDITIONAL.search(combined):
                status = 'conditional'
            row_bbox = (serial_left, top, context['remarks'][1], bottom)
            locator = {'page': page_number, 'bbox': _box(row_bbox), 'register_item': item['text'],
                       'table_caption': context['caption'], 'columns': {key: _box(box) for key, box in boxes.items()},
                       'applicability_cells': cells, 'mark_meaning': context['mark_meaning'],
                       'extraction_method': GEOMETRY_VERSION}
            if len(shared_remarks) > 1:
                locator['shared_cells'] = {'remarks': {'bbox': _box(boxes['remarks']), 'register_items': shared_remarks}}
            rows.append({
                'name': title, 'original_title': title, 'register_item': item['text'],
                'discipline': normalize_register_discipline(discipline), 'discipline_label': discipline,
                'source_group': discipline, 'explicit_dimensions': {'discipline': {'value': discipline, 'header': 'Discipline'}},
                'document_number': '', 'document_revision': '', 'source_layout': 'pdf_geometry_register',
                'title_boundary_status': 'explicit_cell', 'applicability_status': status,
                'applicability_marked': bool(cells), 'applicability_marks': ' '.join(cell['mark'] for cell in cells),
                'applicability_cells': cells, 'source_remarks': remarks, 'literal_cells': literal,
                'package_columns_status': 'resolved_cells' if all(cell['package_labels'] for cell in cells) else 'not_resolved',
                'sheet': '', 'row_number': None, 'source_line': None, 'source_locator': locator,
                'source_excerpt': _literal_text(_cell_words(words, row_bbox)), 'start': None, 'end': None,
            })
        return rows


def extract_pdf_register_rows(file_obj, extracted_text=''):
    """Extract geometry evidence without changing or closing the caller's stream.

    Offsets identify a unique original item line or literal title-cell line;
    ``literal_cells`` and page bounding boxes carry the reconstructed cell text.
    Ambiguous repeated text retains boxes without fabricated offsets. A wrapped
    title is never falsely claimed to be one contiguous flattened quotation.
    """
    import pdfplumber

    position = file_obj.tell() if hasattr(file_obj, 'tell') else None
    file_obj.seek(0)
    pages = extracted_text.split('\f') if extracted_text else []
    offsets, offset = [], 0
    for text in pages:
        offsets.append(offset)
        offset += len(text) + 1
    extractor, rows = PdfRegisterGeometryExtractor(), []
    try:
        with pdfplumber.open(file_obj) as pdf:
            if len(pdf.pages) > MAX_PAGES:
                raise ValueError('pdf_register_geometry_page_limit')
            if pages and len(pages) != len(pdf.pages):
                # Offsets from an unpaginated/truncated parse must not be
                # attached to another physical page's geometry.
                raise ValueError('pdf_register_geometry_text_page_mismatch')
            for index, page in enumerate(pdf.pages):
                page_text = pages[index] if index < len(pages) else None
                extracted = extractor.extract_page(page, index + 1, page_text)
                for row in extracted:
                    if page_text is not None:
                        ranges = []
                        for field, literal in row['literal_cells'].items():
                            for line in literal.splitlines():
                                positions = list(re.finditer(re.escape(line), page_text)) if line else []
                                # Repeated cell content is not a unique locator.
                                # Never choose a nearby occurrence by guessing.
                                if len(positions) == 1:
                                    located = positions[0]
                                    ranges.append({'character_start': offsets[index] + located.start(),
                                                   'character_end': offsets[index] + located.end(),
                                                   'quote': located.group(0), 'field': field})
                        row['source_locator']['text_ranges'] = ranges
                        matches = list(re.finditer(r'^[ \t]*' + re.escape(str(row['register_item'])) + r'(?=\s|$).*$', page_text, re.M))
                        located = None
                        if len(matches) == 1:
                            located = (offsets[index] + matches[0].start(), offsets[index] + matches[0].end())
                        else:
                            title_ranges = [part for part in ranges if part['field'] == 'title']
                            title_matches = [match for match in matches if any(
                                offsets[index] + match.start() <= part['character_start']
                                < part['character_end'] <= offsets[index] + match.end() for part in title_ranges)]
                            if len(title_matches) == 1:
                                match = title_matches[0]
                                located = (offsets[index] + match.start(), offsets[index] + match.end())
                                row['source_locator']['text_row_status'] = 'unique_title_in_item_line'
                            elif title_ranges:
                                title_part = max(title_ranges, key=lambda part: len(part['quote']))
                                located = (title_part['character_start'], title_part['character_end'])
                                row['source_locator']['text_row_status'] = 'unique_title_cell'
                            else:
                                # Cell boxes remain exact evidence. Preserve
                                # ambiguity instead of attaching an unrelated
                                # first occurrence to a later repeated item.
                                row['source_locator']['text_row_status'] = 'ambiguous_repeated_item' if matches else 'item_line_not_located'
                        if located:
                            row['start'], row['end'] = located
                            row['source_line'] = extracted_text.count('\n', 0, row['start']) + 1
                            row['source_locator']['line'] = row['source_line']
                            row['source_locator']['raw_text_start'] = row['start']
                            row['source_locator']['raw_text_end'] = row['end']
                            row['source_text_excerpt'] = extracted_text[row['start']:row['end']]
                            row['source_locator']['quote'] = row['source_text_excerpt']
                rows.extend(extracted)
                if len(rows) > MAX_ROWS:
                    raise ValueError('pdf_register_geometry_row_limit')
        return rows
    finally:
        if position is not None and not file_obj.closed:
            file_obj.seek(position)
