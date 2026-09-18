"""Pure, conservative parsing of text from a printed Primavera activity table.

This reads existing extracted text only. It does not import a native schedule,
recalculate dates, infer links/calendars, or open files. Blank date columns lose
their positions in PDF text, so a single printed date remains unlocated.
"""
from collections import Counter
from datetime import date
from decimal import Decimal
import re


_HEADER = re.compile(
    r'#\s+Activity\s+ID\s+Activity\s+Name\s+Original(?:\s+Duration)?\s+'
    r'Start\s+Finish\s+Total\s+Float\b', re.I,
)
_DATE = r'\d{2}-[A-Za-z]{3}-(?:\d{4}|\d{2})'
_NUMBER = r'-?\d+(?:\.\d+)?'
_CELLS = re.compile(
    rf'(?<!\S)(?P<duration>{_NUMBER})\s+(?P<first>{_DATE})'
    rf'(?:\s+(?P<second>{_DATE}))?\s+(?P<float>{_NUMBER})(?!\S)',
)
_ROW = re.compile(r'^(?P<row>\d+)\s+(?P<body>\S.*)$')
_ID = re.compile(r'^(?=[A-Za-z0-9_.:/-]*\d)[A-Za-z][A-Za-z0-9_.:/-]*$')
_STAGE = re.compile(r'\s+-\s+(IFR|COMPANY REVIEW|IFA|COMPANY APPROVAL|IFT/IFM)\s*$', re.I)
_STAGES = ['IFR', 'COMPANY REVIEW', 'IFA', 'COMPANY APPROVAL', 'IFT/IFM']
_MONTHS = {month: index for index, month in enumerate(
    ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'], 1,
)}


def _normal(value):
    return ' '.join(value.split())


def _undouble(value):
    """Decode overprinted summary text only when every token is exactly paired."""
    words = value.split()
    if not words or any(len(word) % 2 or any(word[i] != word[i + 1] for i in range(0, len(word), 2)) for word in words):
        return None
    return ' '.join(word[::2] for word in words)


def _number(value):
    number = Decimal(value)
    return int(number) if number == number.to_integral_value() else float(number)


def _date(value, years):
    day, month, year = value.split('-')
    if len(year) == 2:
        candidates = {candidate for candidate in years if candidate % 100 == int(year)}
        if len(candidates) != 1:
            raise ValueError('A two-digit year has no unique full-year anchor.')
        year = candidates.pop()
    else:
        year = int(year)
    return date(year, _MONTHS[month.lower()], int(day)).isoformat()


def parse_reference_schedule_text(text):
    """Return printed rows and five-stage groups, with explicit extraction gaps.

    ``status`` is not_detected, parsed or partial; none means approved/verified.
    Activity IDs and printed values remain independent. ``rows`` also includes
    summaries for evidence comparison; their native WBS IDs/levels are unknown.
    """
    result = {'status': 'not_detected', 'project_summary': None, 'activities': [],
              'deliverables': [], 'rows': [], 'issues': [], 'relationships': None,
              'calendar': None, 'logic_verified': False, 'calendar_verified': False,
              'extraction_basis': 'saved_text', 'title_geometry_verified': False}
    if not isinstance(text, str) or not text.strip():
        return result
    if len(text) > 2_000_000:
        result['issues'].append({'code': 'text_limit', 'message': 'Schedule text exceeds the supported extraction limit.'})
        return result
    pages = text.split('\f')
    detected = any(_HEADER.search(page) for page in pages)
    if not detected:
        return result
    issues, rows = result['issues'], result['rows']
    unknown_page = len(pages) == 1 and len(re.findall(r'Page\s+\d+\s+of\s+\d+', text, re.I)) > 1
    if unknown_page:
        issues.append({'code': 'page_boundaries_unknown', 'message': 'Page separators were not retained; physical page locators are unknown.'})
    if '[truncated]' in text:
        issues.append({'code': 'text_truncated', 'message': 'The saved text is truncated; schedule coverage is incomplete.'})
    for page_index, page in enumerate(pages, 1):
        if not _HEADER.search(page):
            issues.append({'code': 'page_header_missing', 'page': page_index, 'message': 'This page has no supported schedule-table header.'})
            continue
        active = False
        years = {int(year) for year in re.findall(r'\b(?:19|20|21)\d{2}\b', page)}
        for line_number, line in enumerate(page.splitlines(), 1):
            line = line.strip()
            if _HEADER.search(line):
                active = True
                continue
            if not active:
                continue
            if re.match(r'^Date\s+Revision\s+Checked\s+Approved', line, re.I):
                active = False
                continue
            match = _ROW.match(line)
            if not match or not re.search(r'[A-Za-z]', match['body']):
                continue
            row_number = int(match['row'])
            locator = {'page': None if unknown_page else page_index, 'row': row_number, 'line': line_number}
            if not 1 <= row_number <= 20_000:
                issues.append({'code': 'row_number_limit', 'source_locator': locator,
                               'message': 'The row number is outside the supported bounded table range.'})
                continue
            cells = list(_CELLS.finditer(match['body']))
            if len(cells) != 1:
                issues.append({'code': 'row_cells_ambiguous', 'source_locator': locator,
                               'message': 'The numeric/date columns could not be located uniquely.', 'raw_text': line})
                continue
            cells = cells[0]
            prefix = match['body'][:cells.start()].strip()
            decoded = _undouble(prefix)
            first, separator, remainder = prefix.partition(' ')
            if decoded:
                kind, identifier, title = 'summary', None, decoded
            elif separator and _ID.fullmatch(first):
                kind, identifier, title = 'activity', first, remainder.strip()
            else:
                issues.append({'code': 'row_identity_unknown', 'source_locator': locator,
                               'message': 'Cannot distinguish an activity ID from a summary title.', 'raw_text': line})
                continue
            row = {'id': identifier or f'pdf-summary-{row_number}', 'row_number': row_number,
                   'kind': kind, 'activity_id': identifier, 'activity_code': identifier,
                   'title': _normal(title), 'original_title_text': prefix,
                   'original_duration_days': _number(cells['duration']),
                   'planned_start_date': None, 'planned_finish_date': None,
                   'total_float_days': _number(cells['float']), 'source_locator': locator,
                   'date_columns_status': 'parsed', 'title_status': 'text_extracted',
                   'is_milestone': kind == 'activity' and _number(cells['duration']) == 0,
                   'raw_text': line}
            if row['original_duration_days'] < 0:
                row['original_duration_days'] = None
                issues.append({'code': 'negative_duration', 'source_locator': locator,
                               'message': 'A negative original duration is not accepted.'})
            try:
                if cells['second']:
                    row['planned_start_date'], row['planned_finish_date'] = _date(cells['first'], years), _date(cells['second'], years)
                    if row['planned_finish_date'] < row['planned_start_date']:
                        issues.append({'code': 'date_range_invalid', 'source_locator': locator,
                                       'message': 'The printed finish precedes the printed start; values were preserved for review.'})
                else:
                    row['printed_single_date'] = _date(cells['first'], years)
                    row['date_columns_status'] = 'ambiguous'
                    issues.append({'code': 'single_date_column_unknown', 'source_locator': locator,
                                   'activity_id': identifier, 'message': 'A single printed date cannot be identified as Start or Finish from collapsed text.'})
            except (ValueError, KeyError):
                row['planned_start_date'] = row['planned_finish_date'] = None
                row['date_columns_status'] = 'invalid'
                issues.append({'code': 'invalid_date', 'source_locator': locator, 'message': 'A printed date is invalid or its two-digit year has no unique full-year anchor.'})
            stage = _STAGE.search(row['title']) if kind == 'activity' else None
            if stage:
                row['workflow_stage_name'] = stage[1].upper()
                row['deliverable_title_text'] = row['title'][:stage.start()].rstrip()
            rows.append(row)
    row_counts = Counter(row['row_number'] for row in rows)
    duplicate_rows = sorted(key for key, count in row_counts.items() if count > 1)
    if duplicate_rows:
        issues.append({'code': 'duplicate_row_numbers', 'row_numbers': duplicate_rows,
                       'message': 'Printed row numbers are not unique; source row identity requires review.'})
    if rows:
        missing = sorted(set(range(1, max(row_counts) + 1)) - set(row_counts))
        if missing:
            issues.append({'code': 'missing_rows', 'row_numbers': missing,
                           'message': 'The printed row sequence is incomplete.'})
        first = rows[0]
        if first['row_number'] == 1 and first['kind'] == 'summary' and 1 not in duplicate_rows:
            result['project_summary'] = dict(first)
    else:
        issues.append({'code': 'rows_not_recovered', 'message': 'The table header was detected but no supported rows could be recovered.'})
    result['activities'] = [row for row in rows if row['kind'] == 'activity']
    ids = Counter(row['activity_id'] for row in result['activities'])
    duplicate_ids = sorted(key for key, count in ids.items() if count > 1)
    if duplicate_ids:
        issues.append({'code': 'duplicate_activity_ids', 'activity_ids': duplicate_ids,
                       'message': 'Activity IDs are duplicated; do not use them as an import identity.'})
    for index, parent in enumerate(rows):
        if parent['kind'] != 'summary':
            continue
        children = rows[index + 1:index + 6]
        if len(children) != 5 or [child.get('workflow_stage_name') for child in children] != _STAGES:
            continue
        if any(child['row_number'] != parent['row_number'] + offset for offset, child in enumerate(children, 1)):
            continue
        titles_match = all(_normal(child['deliverable_title_text']).casefold() == _normal(parent['title']).casefold() for child in children)
        group = {'id': f'pdf-deliverable-{parent["row_number"]}', 'title': parent['title'],
                 'source_locator': dict(parent['source_locator']), 'summary': dict(parent),
                 'workflow_task_ids': [child['activity_id'] for child in children],
                 'stage_names': list(_STAGES), 'activities': [dict(child) for child in children],
                 'title_match_status': 'matched' if titles_match else 'mismatch',
                 'grouping_basis': 'consecutive_printed_rows_and_stage_labels', 'relationships_verified': False}
        if not titles_match:
            issues.append({'code': 'deliverable_title_mismatch', 'source_locator': parent['source_locator'],
                           'message': 'The five stage titles do not exactly match their preceding summary title.'})
        result['deliverables'].append(group)
    result.update(status='partial' if issues or not rows else 'parsed',
                  page_count=None if unknown_page else len(pages), row_count=len(rows),
                  activity_count=len(result['activities']), deliverable_count=len(result['deliverables']),
                  date_year_interpretation='Two-digit years require a unique matching full-year value on their source page.',
                  hierarchy_status='native_wbs_ids_and_levels_not_available_in_text')
    return result
