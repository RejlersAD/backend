"""Select source-named work without a model, catalogue, or invented scope.

An explicit deliverables list takes precedence over narrative obligations.  The
latter commonly repeat the list, and a sentence containing ``shall`` alone is
not evidence of an activity.  This module selects scope only; callers must mark
any dates/durations subsequently allocated to it as planning assumptions.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter


ENGINE_VERSION = 'source-requirement-activities-v2'
_ROW = re.compile(r'^\s*(?:(?P<item_number>\d{1,4})[.)]?\s+|[-*•]\s+)(?P<title>\S.*)$')
_BARE_NUMBER = re.compile(r'^\d{1,4}[.)]?$')
_BULLET = re.compile(r'^\s*(?:\(cid:\d+\)|[-*•])\s*')
_SECTION = re.compile(r'^(?:appendix\s+[\w-]+\b|\d+(?:\.\d+)+[.)]?\s+)', re.I)
_ACTION = re.compile(
    r'\b(?:shall|must|is required to)\s+(?:(?:also|further)\s+)?'
    r'(?P<action>prepare|develop|produce|conduct|perform|carry\s*out|'
    r'undertake|design|review|verify|check|revalidate|collect)\s+(?P<object>.+)',
    re.I,
)
_OUTPUT = re.compile(
    r'\b(?:reports?|drawings?|layouts?|diagrams?|schedules?|specifications?|'
    r'datasheets?|registers?|plans?|philosoph(?:y|ies)|design basis|'
    r'calculations?|estimates?|MTO|BOQ|scope of work|terms of reference)\b', re.I,
)
_CONDITION = re.compile(r'\b(?:if required|if any|as required|if necessary|where applicable|optional)\b', re.I)
_GENERIC_OBLIGATION = re.compile(
    r'\b(?:order of precedence|use of the word|all services|all (?:necessary )?works|'
    r'all documents and drawings|all relevant|all the deliverables|'
    r'no (?:extra|additional) (?:cost|payment)|prior to (?:the )?bidding|'
    r'along with (?:the|their) (?:bid|technical offer)|security pass|'
    r'personnel|qualifications?|all inclusive|contractual|latest (?:issue|standards)|'
    r'following (?:in|sessions|documents)|following:|for example|example only)\b', re.I,
)


def _clean(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def _read(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _source_lines(text):
    """Keep physical offsets while ignoring repeated PDF page furniture."""
    lines = []
    page = 1
    page_line = 0
    for match in re.finditer(r'([^\n\f]*)([\n\f]|$)', text):
        if not match.group(0):
            continue
        raw = match.group(1)
        cleaned = _clean(raw)
        if cleaned:
            lines.append({'text': cleaned, 'start': match.start(),
                          'end': match.start() + len(raw), 'page': page, 'page_line': page_line})
            page_line += 1
        if match.group(2) == '\f':
            page += 1
            page_line = 0
    header_counts = Counter(line['text'] for line in lines if line['page_line'] < 9)
    for line in lines:
        value = line['text']
        line['furniture'] = bool(
            (line['page_line'] < 9 and header_counts[value] >= 3)
            or re.match(r'^(?:Page\s*:?\s*\d|Rev\.?\s*\d|All\w*\s+pl?la\b|All parties consent)', value, re.I)
            or (line['page_line'] < 12 and re.search(r'\bDoc(?:ument)?\.?\s+No(?:\.|\b)\s*:?', value, re.I))
            or re.match(r'^(?:S\.?\s*No\.?|No\.?|Item)\s+(?:Description|Deliverable|Title)\b', value, re.I)
        )
    return lines


def _list_heading(value):
    if len(value) > 140 or re.search(r'\.{3,}|\b(?:shall|must|list is|listed|responsib|tentative)\b', value, re.I):
        return False
    return bool(re.fullmatch(
        r'(?:(?:appendix\s+[\w-]+\s*[-–—:]?\s*|table\s+\d+\s*:\s*|\d+[.)]\s*))?'
        r'(?:(?:list of|project|engineering|FEED|required|contract|scope)\s+)*'
        r'deliverables?(?:\s+(?:list|register))?\s*:?', value, re.I,
    ))


def _discipline(heading):
    """Only classify an explicit heading; topic mentions never create scope."""
    value = _clean(heading).casefold()
    for needle, code in (
        ('instrument', 'instrumentation'), ('electrical', 'electrical'),
        ('civil', 'civil'), ('structural', 'civil'), ('mechanical', 'mechanical'),
        ('piping', 'piping'), ('telecom', 'telecom'), ('process', 'process'),
        ('hse', 'hse'), ('loss prevention', 'hse'), ('project management', 'pm'),
        ('project control', 'pc'), ('survey', 'survey'), ('procurement', 'procurement'),
    ):
        if needle in value:
            return code
    return 'general'


def _stop_list(value):
    return bool(
        re.match(r'^(?:notes?\b|please consider\b|terms (?:and|&) conditions\b)', value, re.I)
        or re.search(r'\b(?:this deliverable list|list is (?:only |a )?tentative|shall|must)\b', value, re.I)
        or (_SECTION.match(value) and not _list_heading(value))
    )


def _explicit_deliverables(text):
    lines = [line for line in _source_lines(text) if not line['furniture']]
    selected = []
    active = False
    current = None
    heading = 'General'

    def finish():
        nonlocal current
        if current:
            title = _clean(' '.join(current.pop('parts')))
            # A row must identify a concrete output, not be another sentence
            # of the notes following the register.
            if len(title) >= 4 and len(title) <= 500 and not _GENERIC_OBLIGATION.search(title):
                current['title'] = title
                selected.append(current)
            current = None

    for index, line in enumerate(lines):
        value = line['text']
        if _list_heading(value):
            finish()
            active = True
            continue
        if not active:
            continue
        if _stop_list(value):
            finish()
            active = False
            continue
        following = lines[index + 1]['text'] if index + 1 < len(lines) else ''
        row = _ROW.match(value)
        if row:
            finish()
            current = {'parts': [row.group('title')], 'start': line['start'], 'end': line['end'],
                       'source_item_number': row.group('item_number'),
                       'heading': heading, 'selection_basis': 'explicit_deliverable_list'}
        elif _BARE_NUMBER.fullmatch(value):
            if current:
                current['end'] = line['end']
                current['source_item_number'] = value.rstrip('.)')
        elif _BARE_NUMBER.fullmatch(following):
            # Some PDF tables read the description before the item number.
            finish()
            current = {'parts': [value], 'start': line['start'], 'end': line['end'],
                       'heading': heading, 'selection_basis': 'explicit_deliverable_list'}
        elif (len(value) < 100 and not value.endswith(('/', ',', '-', '&'))
              and _ROW.match(following)
              and (current is None or _discipline(value) != 'general'
                   or value.casefold() in {'general', 'conceptual design study'})):
            finish()
            heading = value
        elif current:
            current['parts'].append(value)
            current['end'] = line['end']
        elif len(value) < 100:
            heading = value
    finish()
    return selected


def _requirement_span(text, fact, lines):
    locator = _read(fact, 'source_locator', {}) or {}
    start = locator.get('character_start')
    if not isinstance(start, int) or start < 0 or start >= len(text):
        # Unlocated or stale facts must never silently acquire another quote.
        return None
    expected_hash = locator.get('extracted_text_sha256')
    if expected_hash and expected_hash != hashlib.sha256(text.encode('utf-8')).hexdigest():
        return None
    value = _read(fact, 'value', '')
    if isinstance(value, str) and _clean(value) not in _clean(text[start:locator.get('character_end', start + len(value))]):
        return None
    index = next((i for i, line in enumerate(lines) if line['start'] <= start < line['end']), None)
    if index is None:
        return None
    first = index
    while first > 0 and index - first < 8:
        line = lines[first]
        previous = lines[first - 1]
        if (_BULLET.match(line['text']) or _SECTION.match(line['text']) or previous['furniture']
                or previous['text'].endswith(('.', ';', ':')) or previous['page'] != line['page']):
            break
        first -= 1
    last = index
    while last + 1 < len(lines) and last - first < 14:
        line = lines[last]
        following = lines[last + 1]
        if (line['text'].endswith(('.', ';', ':')) or following['furniture']
                or _BULLET.match(following['text']) or _SECTION.match(following['text'])
                or following['page'] != line['page']):
            break
        last += 1
    return lines[first]['start'], lines[last]['end']


def _narrative_work(text, facts):
    lines = _source_lines(text)
    selected = []
    reasons = Counter()
    seen = set()
    for fact in facts:
        span = _requirement_span(text, fact, lines)
        if not span:
            reasons['missing_or_stale_source_locator'] += 1
            continue
        if span in seen:
            reasons['same_source_statement'] += 1
            continue
        seen.add(span)
        start, end = span
        quote = _clean(_BULLET.sub('', text[start:end]))
        action = _ACTION.search(quote)
        if not action or _GENERIC_OBLIGATION.search(quote):
            reasons['not_a_specific_work_instruction'] += 1
            continue
        title = action.group('action') + ' ' + action.group('object')
        title = re.split(r'(?<=[.!?;])\s+', title, maxsplit=1)[0].rstrip('.;')
        if len(title) < 12 or len(title) > 350 or title.endswith((',', ':')):
            reasons['incomplete_or_ambiguous_work_instruction'] += 1
            continue
        title = title[0].upper() + title[1:]
        output = action.group('action').lower() in {'prepare', 'develop', 'produce', 'design'} and _OUTPUT.search(title)
        selected.append({'title': title, 'start': start, 'end': end, 'heading': 'General',
                         'task_type': 'deliverable' if output else 'task',
                         'requirement_ids': [_read(fact, 'id')], 'selection_basis': 'explicit_work_instruction'})
    return selected, reasons


def _task(source, text, candidate):
    file_id = _read(source, 'id', _read(source, 'pk'))
    text_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()
    start, end = candidate['start'], candidate['end']
    quote = text[start:end]
    locator = {'character_start': start, 'character_end': end,
               'line': text[:start].count('\n') + 1, 'page': text[:start].count('\f') + 1,
               'extracted_text_sha256': text_hash, 'quote': quote,
               'selection_basis': candidate['selection_basis']}
    if candidate.get('source_item_number') is not None:
        locator['source_item_number'] = candidate['source_item_number']
    identity = f'{file_id}:{text_hash}:{start}:{end}'
    reference = {'file_id': file_id, 'filename': _read(source, 'original_filename', ''),
                 'category': _read(source, 'category', ''), 'locator': locator, 'excerpt': quote}
    flags = ['scope_requires_review', 'dates_and_durations_require_planning_assumptions']
    if _CONDITION.search(candidate['title']):
        flags.append('conditional_scope')
    return {
        'id': 'requirement-' + hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20],
        'title': candidate['title'], 'description': quote,
        'discipline': _discipline(candidate['heading']), 'source_heading': candidate['heading'],
        'source_item_number': candidate.get('source_item_number'),
        'task_type': candidate.get('task_type', 'deliverable'),
        'source_file_id': file_id, 'source_quote': quote, 'source_locator': locator,
        'source_references': [reference], 'requirement_ids': candidate.get('requirement_ids', []),
        'selection_basis': candidate['selection_basis'], 'needs_review': True, 'review_flags': flags,
    }


def extract_requirement_activities(files, requirements=()):
    """Return quoted draft work and an honest account of the selection method.

    ``files`` and ``requirements`` accept dictionaries or model instances. Only
    existing text and validated fact offsets are used. Explicit deliverable
    lists across the source set suppress narrative expansion to avoid duplicate
    work. No calendar, duration, relationship, or workflow steps are invented.
    """
    sources = list(files)
    facts = list(requirements)
    tasks = []
    reasons = Counter()
    for source in sources:
        text = _read(source, 'extracted_text', '') or ''
        tasks.extend(_task(source, text, row) for row in _explicit_deliverables(text))
    used_list = bool(tasks)
    if not used_list:
        for source in sources:
            text = _read(source, 'extracted_text', '') or ''
            source_id = _read(source, 'id', _read(source, 'pk'))
            selected, skipped = _narrative_work(text, [fact for fact in facts if _read(fact, 'source_file_id') == source_id])
            tasks.extend(_task(source, text, row) for row in selected)
            reasons.update(skipped)
    # Explicit list rows are source occurrences, including repeated numbers and
    # titles. They must remain separate rather than silently collapsing a WBS.
    # Narrative repetitions alone may be consolidated with all their evidence.
    unique = {}
    for task in tasks:
        key = task['id'] if used_list else (task['discipline'], task['source_heading'].casefold(), _clean(task['title']).casefold())
        if key in unique:
            unique[key]['source_references'].extend(task['source_references'])
            unique[key]['requirement_ids'].extend(task['requirement_ids'])
            reasons['duplicate_work_title'] += 1
        else:
            unique[key] = task
    tasks = list(unique.values())
    selected_ids = {value for task in tasks for value in task['requirement_ids'] if value is not None}
    return {'tasks': tasks, 'selection_summary': {
        'engine_version': ENGINE_VERSION, 'ai_used': False,
        'selection_basis': 'explicit_deliverable_list' if used_list else 'explicit_work_instructions',
        'requirements_found': len(facts), 'requirements_selected': len(selected_ids),
        'requirements_not_individually_materialized': max(0, len(facts) - len(selected_ids)),
        'activities_selected': len(tasks),
        'deliverables_selected': sum(task['task_type'] == 'deliverable' for task in tasks),
        'excluded_reasons': dict(reasons), 'needs_review': True,
        'explanation': (
            'Activities use the explicit deliverable list in the source documents. Requirement statements '
            'are not added separately because they may repeat these deliverables or describe contract obligations.'
            if used_list else
            'Activities use specific work instructions with verified source quotes. Definitions, general '
            'contract obligations and incomplete instructions are not converted into activities.'
        ),
    }}
