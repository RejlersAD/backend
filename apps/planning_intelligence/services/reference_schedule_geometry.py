"""Read supported ruled schedule tables from original PDF geometry.

Headers and their printed cell rectangles establish column boundaries. Row
numbers and indentation establish printed hierarchy, never native WBS IDs.
Blank date cells stay blank; no calendar, links, durations or float are inferred.
Unsupported layouts continue through the ordinary text adapters.
"""
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import re

from .reference_schedule_text import _date, _number, _STAGE, _STAGES


ADAPTER = 'printed_schedule_geometry_v1'
MAX_PAGES = 500
MAX_ROWS = 20_000
MAX_BYTES = 50 * 1024 * 1024
_HEADERS = {'#': 'row', 'activityid': 'id', 'activityname': 'title',
            'originalduration': 'duration', 'start': 'start', 'finish': 'finish', 'totalfloat': 'float'}
_NUMBER = re.compile(r'-?\d+(?:\.\d+)?$')
_DATE = re.compile(r'\d{2}-[A-Za-z]{3}-(?:\d{4}|\d{2})$')
_ID = re.compile(r'[A-Za-z][A-Za-z0-9_.:/-]*$')


def _box(value):
    return [round(float(coordinate), 3) for coordinate in value]


def _inside(word, rectangle, tolerance=.6):
    return (word[0] >= rectangle[0] - tolerance and word[2] <= rectangle[2] + tolerance
            and word[1] >= rectangle[1] - tolerance and word[3] <= rectangle[3] + tolerance)


def _unique_words(words):
    return list({(word[4], *[round(float(value), 2) for value in word[:4]]): word for word in words}.values())


def _header(words, rectangles):
    candidates = defaultdict(list)
    header_markers = [word for word in words if word[4] == '#']
    for rectangle in rectangles:
        width, height = rectangle[2] - rectangle[0], rectangle[3] - rectangle[1]
        if width < 5 or not 5 <= height <= 100:
            continue
        if not any(rectangle[1] - .6 <= word[1] and word[3] <= rectangle[3] + .6 for word in header_markers):
            continue
        marker_y = min(word[1] for word in header_markers if rectangle[1] - .6 <= word[1] and word[3] <= rectangle[3] + .6)
        content = sorted(_unique_words([word for word in words if _inside(word, rectangle)
                                       and abs(word[1] - marker_y) < 1]), key=lambda word: word[0])
        label = re.sub(r'\s+', '', ''.join(word[4] for word in content)).lower()
        if label == 'original' and any(word[4].casefold() == 'duration' and _inside(word, rectangle) for word in words):
            label = 'originalduration'
        if label in _HEADERS:
            candidates[_HEADERS[label]].append(rectangle)
    for anchor in candidates['row']:
        aligned = {key: [rectangle for rectangle in candidates[key]
                         if abs(rectangle[1] - anchor[1]) <= 2 and abs(rectangle[3] - anchor[3]) <= 2]
                   for key in _HEADERS.values()}
        if not all(len({tuple(_box(rectangle)) for rectangle in values}) == 1 for values in aligned.values()):
            continue
        cells = {key: values[0] for key, values in aligned.items()}
        ordered = [cells[key] for key in ('row', 'id', 'title', 'duration', 'start', 'finish', 'float')]
        if all(abs(left[2] - right[0]) <= 2 for left, right in zip(ordered, ordered[1:])):
            return cells
    return None


def _page_rows(page, page_number, issues):
    words = page.get_text('words')
    if len(words) > 150_000:
        issues.append({'code': 'geometry_page_word_limit', 'page': page_number})
        return [], False
    drawings = page.get_drawings()
    rectangles = [item[1] for drawing in drawings for item in drawing['items'] if item[0] == 're']
    cells = _header(words, rectangles)
    if not cells:
        return [], False
    top = max(rectangle[3] for rectangle in cells.values())
    borders = [max(item[1].y, item[2].y) for drawing in drawings for item in drawing['items']
               if item[0] == 'l' and abs(item[1].x - item[2].x) < .5
               and abs(item[1].x - cells['row'][0]) < 2
               and min(item[1].y, item[2].y) <= top and max(item[1].y, item[2].y) > top + 20]
    bottom = max(borders) if borders else float(page.rect.height)
    # A footer heading is an additional printed boundary, even without a border.
    footer_dates = [word[1] for word in words if word[4].casefold() == 'date' and word[1] > top
                    and any(other[4].casefold() == 'revision' and abs(other[1] - word[1]) < 2 for other in words)]
    if footer_dates:
        bottom = min(bottom, min(footer_dates))
    anchors = sorted(_unique_words([word for word in words if cells['row'][0] <= word[0] < cells['row'][2]
        and top <= word[1] < bottom and re.fullmatch(r'\d+', word[4])]), key=lambda word: word[1])
    lines = [line for block in page.get_text('dict')['blocks'] if 'lines' in block for line in block['lines']]
    years = {int(year) for year in re.findall(r'\b(?:19|20|21)\d{2}\b', page.get_text())}
    rows = []
    for index, anchor in enumerate(anchors):
        row_number = int(anchor[4])
        next_top = anchors[index + 1][1] - .4 if index + 1 < len(anchors) else bottom
        locator = {'page': page_number, 'row': row_number,
                   'bbox': _box([cells['row'][0], anchor[1] - .4, cells['float'][2], next_top])}
        if not 1 <= row_number <= MAX_ROWS:
            issues.append({'code': 'row_number_limit', 'source_locator': locator})
            continue
        selected = [line for line in lines if anchor[1] - .4 <= line['bbox'][1] < next_top]
        left = [line for line in selected if cells['id'][0] <= line['bbox'][0] < cells['id'][2]]
        names = [line for line in selected if cells['title'][0] <= line['bbox'][0] < cells['title'][2]]
        if not left:
            issues.append({'code': 'row_identity_unknown', 'source_locator': locator})
            continue
        line_text = lambda line: ''.join(span['text'] for span in line['spans']).strip()
        # Summary rows can be overprinted once clipped to ID, then across the
        # title column. The longest original line retains the complete printing.
        longest = max(left, key=lambda line: len(line_text(line)))
        left_text = line_text(longest)
        activity = bool(_ID.fullmatch(left_text) and names and all(line_text(line) == left_text for line in left))
        title = ' '.join(dict.fromkeys(line_text(line) for line in sorted(names, key=lambda line: line['bbox'][1]))) if activity else left_text
        if not title or longest['bbox'][2] > cells['duration'][0] + 1:
            issues.append({'code': 'row_title_ambiguous', 'source_locator': locator})
            continue
        row = {'id': left_text if activity else f'pdf-summary-{row_number}', 'row_number': row_number,
               'kind': 'activity' if activity else 'summary', 'activity_id': left_text if activity else None,
               'activity_code': left_text if activity else None, 'title': title,
               'original_title_text': left_text, 'original_duration_days': None,
               'planned_start_date': None, 'planned_finish_date': None, 'total_float_days': None,
               'date_columns_status': 'parsed', 'title_status': 'geometry_extracted',
               'source_locator': locator, 'indent_x': round(longest['bbox'][0] - cells['id'][0], 3),
               'field_evidence': {}, 'extraction_basis': ADAPTER}
        raw_cells = []
        for column, field in [('duration', 'original_duration_days'), ('start', 'planned_start_date'),
                              ('finish', 'planned_finish_date'), ('float', 'total_float_days')]:
            rectangle = [cells[column][0], anchor[1] - .4, cells[column][2], next_top]
            found = sorted(_unique_words([word for word in words if _inside(word, rectangle)]), key=lambda word: (word[1], word[0]))
            raw = ' '.join(word[4] for word in found)
            raw_cells.append(raw)
            field_locator = {'page': page_number, 'row': row_number, 'bbox': _box(rectangle)}
            evidence = {'raw_text': raw, 'status': 'extracted' if raw else 'explicit_none' if column in {'start', 'finish'} else 'not_specified',
                        'source_locator': field_locator, 'column': column, 'basis': ADAPTER}
            row['field_evidence'][field] = evidence
            if not raw:
                continue
            try:
                if column in {'start', 'finish'}:
                    if not _DATE.fullmatch(raw):
                        raise ValueError('Unsupported date cell.')
                    row[field] = _date(raw, years)
                else:
                    if not _NUMBER.fullmatch(raw):
                        raise ValueError('Unsupported numeric cell.')
                    row[field] = _number(raw)
                    if column == 'duration' and row[field] < 0:
                        raise ValueError('Negative duration.')
            except (ValueError, KeyError):
                row[field] = None
                evidence['status'] = 'invalid'
                issues.append({'code': 'geometry_cell_invalid', 'field': field, 'source_locator': field_locator})
        row['raw_text'] = ' | '.join([str(row_number), left_text, title if activity else '', *raw_cells])
        row['is_milestone'] = activity and row['original_duration_days'] == 0
        if row['is_milestone']:
            if row['planned_start_date'] and not row['planned_finish_date']:
                row['record_type'] = 'start milestone'
            elif row['planned_finish_date'] and not row['planned_start_date']:
                row['record_type'] = 'finish milestone'
        if row['planned_start_date'] and row['planned_finish_date'] and row['planned_finish_date'] < row['planned_start_date']:
            issues.append({'code': 'date_range_invalid', 'source_locator': locator})
        stage = _STAGE.search(title) if activity else None
        if stage:
            row.update(workflow_stage_name=stage[1].upper(), deliverable_title_text=title[:stage.start()].rstrip())
        rows.append(row)
    return rows, True


def parse_reference_schedule_pdf(file_obj):
    """Return source-only geometry; caller retains the normal extracted text."""
    result = {'adapter': ADAPTER, 'status': 'not_detected', 'rows': [], 'activities': [],
              'deliverables': [], 'project_summary': None, 'issues': [], 'relationships': None,
              'calendar': None, 'logic_verified': False, 'calendar_verified': False,
              'extraction_basis': 'original_pdf_geometry', 'title_geometry_verified': True}
    file_obj.seek(0)
    data = file_obj.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        result['issues'].append({'code': 'geometry_file_limit'})
        return result
    result['checksum_sha256'] = hashlib.sha256(data).hexdigest()
    try:
        import pymupdf
        with pymupdf.open(stream=data, filetype='pdf') as document:
            result['page_count'] = len(document)
            if len(document) > MAX_PAGES:
                result['issues'].append({'code': 'geometry_page_limit'})
                return result
            detected_pages = []
            for index, page in enumerate(document, 1):
                rows, detected = _page_rows(page, index, result['issues'])
                if detected:
                    detected_pages.append(index)
                result['rows'].extend(rows)
                if len(result['rows']) > MAX_ROWS:
                    result['rows'] = result['rows'][:MAX_ROWS]
                    result['issues'].append({'code': 'geometry_row_limit'})
                    break
    except (ImportError, RuntimeError, ValueError) as error:
        result['issues'].append({'code': 'geometry_extraction_unavailable', 'message': str(error)[:250]})
        return result
    if not detected_pages:
        return result
    rows, issues = result['rows'], result['issues']
    if len(detected_pages) != result['page_count']:
        issues.append({'code': 'geometry_table_pages_incomplete', 'detected_pages': detected_pages})
    numbers = Counter(row['row_number'] for row in rows)
    duplicates = sorted(key for key, count in numbers.items() if count > 1)
    missing = sorted(set(range(1, max(numbers, default=0) + 1)) - set(numbers))
    if duplicates:
        issues.append({'code': 'duplicate_row_numbers', 'row_numbers': duplicates})
    if missing:
        issues.append({'code': 'missing_rows', 'row_numbers': missing})
    if [row['row_number'] for row in rows] != sorted(row['row_number'] for row in rows):
        issues.append({'code': 'row_order_ambiguous'})
    ids = Counter(row['activity_id'] for row in rows if row['kind'] == 'activity')
    if any(count > 1 for count in ids.values()):
        issues.append({'code': 'duplicate_activity_ids', 'activity_ids': sorted(key for key, count in ids.items() if count > 1)})
    hierarchy_valid = bool(rows) and not any(issue['code'] in {
        'duplicate_row_numbers', 'missing_rows', 'row_order_ambiguous', 'row_identity_unknown',
        'row_title_ambiguous', 'geometry_row_limit', 'geometry_table_pages_incomplete',
    } for issue in issues)
    stack, children = [], defaultdict(list)
    for row in rows:
        if row['kind'] == 'summary':
            while stack and stack[-1]['indent_x'] >= row['indent_x'] - .5:
                stack.pop()
        if hierarchy_valid:
            row.update(parent_row_number=stack[-1]['row_number'] if stack else None, level=len(stack))
            row['source_hierarchy'] = {'row_number': row['row_number'], 'parent_row_number': row['parent_row_number'],
                                       'level': row['level'], 'basis': 'printed_pdf_indentation'}
            children[row['parent_row_number']].append(row)
        if row['kind'] == 'summary':
            stack.append(row)
    if rows and rows[0]['row_number'] == 1 and rows[0]['kind'] == 'summary':
        result['project_summary'] = deepcopy(rows[0])
    for parent in rows:
        members = children[parent['row_number']]
        if parent['kind'] != 'summary' or [row.get('workflow_stage_name') for row in members] != _STAGES:
            continue
        matched = all(row.get('deliverable_title_text', '').casefold() == parent['title'].casefold() for row in members)
        result['deliverables'].append({'id': f'pdf-deliverable-{parent["row_number"]}', 'title': parent['title'],
            'source_locator': parent['source_locator'], 'summary': deepcopy(parent), 'activities': deepcopy(members),
            'workflow_task_ids': [row['activity_id'] for row in members], 'stage_names': list(_STAGES),
            'title_match_status': 'matched' if matched else 'mismatch', 'grouping_basis': 'printed_pdf_indentation',
            'relationships_verified': False})
    result['activities'] = [row for row in rows if row['kind'] == 'activity']
    result.update(status='partial' if issues or not rows else 'parsed', row_count=len(rows),
                  activity_count=len(result['activities']), deliverable_count=len(result['deliverables']),
                  hierarchy_status='printed_indentation' if hierarchy_valid else 'unverified')
    return result


def cached_schedule_geometry(file_obj):
    """Read the cache only when bound to the current text and storage object."""
    profile = getattr(file_obj, 'document_profile', None)
    coverage = getattr(profile, 'extraction_coverage', None) or {}
    result = (coverage.get('structured_evidence') or {}).get('reference_schedule_geometry')
    if (not isinstance(result, dict) or result.get('adapter') != ADAPTER or not result.get('rows')
            or result.get('status') != 'parsed'):
        return None
    digest = hashlib.sha256((file_obj.extracted_text or '').encode('utf-8')).hexdigest()
    if result.get('text_sha256') != digest or coverage.get('text_sha256') != digest:
        return None
    if result.get('source_storage_name') != getattr(file_obj.file, 'name', None):
        return None
    return deepcopy(result)
