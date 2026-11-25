"""Write a Rejlers technical proposal from tender documents (DEC-TP-001, authored mode).

Every drafted paragraph, list and table cites the tender excerpts (or approved Rejlers standard
text) it relies on; blocks whose citations do not resolve to supplied excerpts are removed and
reported. Missing information becomes an open point, never invented content.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from django.db import close_old_connections
from rest_framework.exceptions import ValidationError

_TEXT = {'type': 'string'}
_LIST = {'type': 'array', 'items': {'type': 'string'}}
BLOCK_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['blocks', 'open_points'],
    'properties': {
        'blocks': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['type', 'text', 'items', 'columns', 'rows', 'citations'],
            'properties': {
                'type': {'type': 'string', 'enum': ['heading', 'paragraph', 'bullets', 'table']},
                'text': _TEXT, 'items': _LIST, 'columns': _LIST,
                'rows': {'type': 'array', 'items': _LIST}, 'citations': _LIST,
            }}},
        'open_points': _LIST,
    },
}
PACKAGE_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['packages', 'open_points'],
    'properties': {
        'packages': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['title', 'citations'],
            'properties': {'title': _TEXT, 'citations': _LIST}}},
        'open_points': _LIST,
    },
}
MAX_PACKAGES = 15
# Primary tender documents (what the proposal answers); standards/specifications are background only.
TENDER_SOURCE = re.compile(r'request for tender|\brft\b|scope of work|\bsow\b|terms of reference|\btor\b|bulletin|'
                           r'clarification|technical quer|\btq', re.I)
PRIMARY_TYPES = {'RFT', 'SOW', 'Addendum', 'Bulletin'}
MAX_COMPLIANCE_ROWS = 300


RFT_SOURCE = re.compile(r'request for tender|\brft\b', re.I)


def is_primary_requirement(requirement):
    # Decided by document name: the content-based source type labels any text that mentions
    # "scope of work" or "specification" as SOW, which sweeps in reference standards.
    return bool(TENDER_SOURCE.search(str(requirement.get('source_file') or '')))


GENERIC_HEADINGS = {'scopeofservices', 'listofdeliverables', 'deliverables'}
MIN_REQUIREMENT_WORDS = 6


def compliance_requirements(requirements):
    """Rows a client reader can act on: whole requirement sentences, each listed once.

    The extractor splits on obligation words, so it also yields fragments ("SHALL", "shall be
    final.") and repeats every requirement of a document uploaded twice under another name.
    """
    seen, rows = set(), []
    for item in requirements:
        text = ' '.join(str(item.get('requirement_text') or '').split())
        if len(text.split()) < MIN_REQUIREMENT_WORDS or not text[:1].isupper():
            continue
        key = re.sub(r'[^a-z0-9]', '', text.casefold())
        if key in seen:
            continue
        seen.add(key)
        rows.append(item)
    return rows


def finding(severity, code, message):
    return {'severity': severity, 'code': code, 'message': message}


# --- tender evidence ------------------------------------------------------------------------

def gather_tender(quote, actor, source_keys):
    """Requirements and readable text of the selected tender documents.

    Uses the text that document analysis has extracted. It does not wait for the separate
    Go/No-Go evidence-claim stage, which proposal writing does not need (and which can take an
    hour on large tenders). Access and freshness are checked as for every analysis read.
    """
    from .opportunity_analysis import authority
    from .proposal_workspace_service import _report
    from .tender_requirements import extract_tender_requirements

    authority(quote.deal, actor)
    report = _report(quote.deal, actor)
    sources = []
    for source in report.sources.filter(source_key__in=list(source_keys)).prefetch_related('chunks').order_by('pk'):
        chunks = [{'location': chunk.location, 'text': chunk.text}
                  for chunk in sorted(source.chunks.all(), key=lambda item: item.ordinal) if chunk.text]
        if not chunks:
            continue
        metadata = source.metadata or {}
        sources.append({
            'source_key': source.source_key, 'source_id': source.pk, 'source_identity': source.identity,
            'source_name': metadata.get('name'), 'source_version': str(metadata.get('version') or ''),
            'folder': metadata.get('folder'), 'source_path': metadata.get('source_path') or metadata.get('name'),
            'parse_status': 'ready', 'parse_reason': source.diagnostic, 'chunks': chunks,
        })
    requirements, _, _ = extract_tender_requirements(sources)
    return requirements, sources


def _location(requirement):
    parts = []
    if requirement.get('page'):
        parts.append(f"p. {requirement['page']}")
    if requirement.get('clause_location'):
        parts.append(f"clause {requirement['clause_location']}")
    return ', '.join(parts) or str(requirement.get('source_location') or '')


def excerpt_pool(requirements, sources, max_chars):
    pool = []
    for requirement in requirements:
        pool.append({'id': requirement['id'], 'kind': 'tender', 'source': requirement.get('source_file', ''),
                     'location': _location(requirement), 'text': requirement['requirement_text'][:max_chars],
                     'categories': requirement.get('requirement_categories') or [], 'requirement': True,
                     'primary': is_primary_requirement(requirement)})
    for s_index, source in enumerate(sources, 1):
        name = source.get('source_name') or source.get('source_path') or ''
        for c_index, chunk in enumerate(source.get('chunks') or [], 1):
            text = chunk.get('text') or ''
            for w_index, start in enumerate(range(0, len(text), max_chars), 1):
                window = text[start:start + max_chars].strip()
                if len(window) > 40:
                    pool.append({'id': f'T{s_index}.{c_index}.{w_index}', 'kind': 'tender', 'source': name,
                                 'location': str(chunk.get('location') or ''), 'text': window,
                                 'categories': [], 'requirement': False,
                                 'primary': bool(TENDER_SOURCE.search(name))})
    return pool


def standard_pool(path):
    with open(path, encoding='utf-8') as handle:
        data = json.load(handle)
    return [{'id': item['id'], 'kind': 'standard', 'source': 'Rejlers standard methodology',
             'location': ' / '.join(filter(None, (item['chapter'], item['section']))), 'text': item['text'],
             'chapter': item['chapter'], 'section': item.get('section') or ''} for item in data['items']]


def select_excerpts(part, pool, standard, budget, extra_keywords=()):
    keywords = [word.casefold() for word in list(part.get('keywords') or []) + list(extra_keywords)]
    keywords += [word.casefold() for word in re.findall(r'[A-Za-z]{4,}', part.get('heading', ''))]
    categories = set(part.get('categories') or [])
    scored = []
    for order, item in enumerate(pool):
        lowered = item['text'].casefold()
        hits = min(6, sum(lowered.count(word) for word in keywords))
        if not hits and not (categories & set(item['categories'])):
            continue
        # Primary tender documents first; long reference standards cannot win on keyword volume.
        score = hits + 2 * len(categories & set(item['categories'])) + (8 if item.get('primary') else 0)
        if score > 0:
            scored.append((-score, order, item))
    scored.sort()
    chosen, used = [], 0
    chapters = set(part.get('standard') or [])
    std_items = [item for item in standard if item['chapter'] in chapters]
    std_budget = budget * 0.4 if std_items else 0
    for item in std_items:
        size = len(item['text']) + 120
        if used + size > std_budget:
            break
        chosen.append(item)
        used += size
    for _, _, item in scored:
        size = len(item['text']) + 120
        if used + size > budget:
            continue
        chosen.append(item)
        used += size
    return chosen


# --- drafting -------------------------------------------------------------------------------

def _payload(facts, part, excerpts, reviewer_note, package=None):
    return {
        'project': {key: facts.get(key) for key in ('client', 'tender_title', 'quote_ref', 'rft_number', 'company')},
        'part': {'heading': part['heading'], 'package': package, 'reviewer_instructions': reviewer_note or ''},
        'excerpts': [{key: item[key] for key in ('id', 'kind', 'source', 'location', 'text')} for item in excerpts],
    }


TRANSIENT_PROVIDER_ERRORS = {'provider_unavailable', 'provider_rate_limit', 'provider_timeout'}
RETRY_DELAY_SECONDS = 3


def _provider_failure(result):
    """True when the AI service itself failed (as opposed to the tender lacking text)."""
    return str(result.get('error_code') or '').startswith('provider_') or result.get('status') in ('unavailable', 'disabled')


def _call(payload, schema, instructions):
    from .email_ai_provider import analyze_technical_proposal_sources

    try:
        result = analyze_technical_proposal_sources(payload, schema, instructions=instructions, output_token_limit=3500)
        if result.get('error_code') in TRANSIENT_PROVIDER_ERRORS:   # one retry after a short pause
            time.sleep(RETRY_DELAY_SECONDS)
            result = analyze_technical_proposal_sources(payload, schema, instructions=instructions, output_token_limit=3500)
        if result.get('error_code') == 'provider_incomplete':  # output hit the cap: one shorter retry
            result = analyze_technical_proposal_sources(
                payload, schema, output_token_limit=3500,
                instructions=f'{instructions}\nBe concise: at most 250 words and at most 3 open points.')
        return result
    finally:
        close_old_connections()


def _clean_blocks(raw_blocks, allowed, part_heading=''):
    blocks, dropped, cited = [], 0, set()
    repeated = re.sub(r'[^a-z0-9]', '', part_heading.casefold())
    for block in raw_blocks or []:
        kind = block.get('type')
        citations = [value for value in block.get('citations') or [] if value in allowed]
        text = str(block.get('text') or '').strip()
        if kind == 'heading':
            # Never restate the part or package heading the document already shows.
            key = re.sub(r'[^a-z0-9]', '', text.casefold())
            if key and key not in GENERIC_HEADINGS and not (repeated and repeated in key):
                blocks.append({'type': 'heading', 'level': 2, 'text': text[:200]})
            continue
        if not citations:
            dropped += 1
            continue
        record = {'type': kind, 'text': text[:4000], 'citations': citations}
        if kind == 'bullets':
            record['items'] = [str(item).strip()[:1000] for item in block.get('items') or [] if str(item).strip()]
            if not record['items']:
                dropped += 1
                continue
        if kind == 'table':
            columns = [str(value)[:120] for value in block.get('columns') or []][:8]
            record['columns'] = columns
            record['rows'] = [[str(cell)[:600] for cell in row][:max(1, len(columns))] for row in block.get('rows') or [] if row]
            if not record['rows']:
                dropped += 1
                continue
            groups = {f'{index}:{column}': [{'text': cell, 'bold': True}]
                      for index, row in enumerate(record['rows']) if row and GROUP_ROW.fullmatch(row[0].strip())
                      for column, cell in enumerate(row) if cell}
            if groups:
                record['cell_runs'] = groups
        cited.update(citations)
        blocks.append(record)
    return blocks, dropped, cited


GROUP_ROW = re.compile(r'[A-H]\.?')   # "A." Process, "B." HSE … in a deliverables list
MAX_OPEN_POINTS = 5
MAX_SECTION_OPEN_POINTS = 8
_PACKAGES_LOCK = threading.Lock()
SCOPE_PART = {'heading': 'SCOPE OF SERVICES', 'categories': ['technical'],
              'keywords': ['scope', 'package', 'sow', 'terms of reference', 'study', 'feed', 'design', 'engineering']}


# Tender packages are usually filed one folder per scope ("3. SOW - P11742/..."), and scopes added
# later arrive as a stand-alone terms of reference ("TOR FOR MODIFICATION WORKS AT ...").
_SOW_FOLDER = re.compile(r'(?:^|/)(\d{1,2})\s*\.\s*SOW\s*[-–]\s*(P\d{4,6}[A-Z]?)\s*/', re.I)
_STANDALONE_TOR = re.compile(r'(?:^|/)(TOR\b[^/]*|Terms of Reference[^/]*)\.(?:pdf|docx?)$', re.I)


def package_register(pool):
    """Scope packages the tender files define, in tender order: [{'key', 'number', 'code', 'items'}].

    Built from the document structure, not by the AI, so no package is missed or invented.
    Duplicate copies of a stand-alone TOR (e.g. in the RFT and a bulletin) count once.
    """
    register, seen_tor = {}, {}
    for item in pool:
        source = str(item.get('source') or '').replace('\\', '/')
        match = _SOW_FOLDER.search(source)
        if match:
            number, code = int(match.group(1)), match.group(2).upper()
            entry = register.setdefault(code, {'key': code, 'number': number, 'code': code, 'items': []})
            entry['items'].append(item)
            continue
        tor = _STANDALONE_TOR.search(source)
        if tor:
            name = re.sub(r'[-_ ]*\d*$', '', tor.group(1)).strip().casefold()
            key = seen_tor.setdefault(name, f'TOR-{len(seen_tor) + 1}')
            entry = register.setdefault(key, {'key': key, 'number': 1000 + len(seen_tor), 'code': '', 'items': [],
                                              'name': tor.group(1)})
            entry['items'].append(item)
    for entry in register.values():   # copies of the same document (RFT and bulletin) count once
        unique, texts = [], set()
        for item in entry['items']:
            text = ' '.join(item['text'].split())[:300]
            if text not in texts:
                texts.add(text)
                unique.append(item)
        # The package's own terms of reference / scope of work first, attachments and standards last.
        entry['items'] = sorted(unique, key=lambda item: _main_document_rank(item['source']))
    return sorted(register.values(), key=lambda entry: entry['number'])


_MAIN_DOCUMENT = re.compile(r'\b(tor|sow|scope of work|terms of reference)\b|-0601\b', re.I)


def _main_document_rank(source):
    path = str(source or '').replace(chr(92), '/')
    name = path.rsplit('/', 1)[-1]
    return ('attachment' in path.casefold(), not _MAIN_DOCUMENT.search(name), name.casefold().startswith('master'))


def _register_title(entry):
    if entry['code']:
        name = entry['items'][0]['source'].replace('\\', '/').rsplit('/', 1)[-1]
        name = re.sub(r'\.(pdf|docx?)$', '', name, flags=re.I)
        return f"{entry['code']} – {name}"
    return re.sub(r'[-_ ]*\d*$', '', entry.get('name') or 'Terms of reference').strip()


def scope_packages(pool, standard, facts, config, style):
    """The tender's scope packages, listed once per generation and shared by every part that
    is written per package (scope of services, deliverables).

    When the tender files are organised per scope package (SOW folders, stand-alone TORs), the
    package list is exactly that register; the AI only supplies each package's title from its
    own excerpts. Otherwise the AI lists the packages, and each must cite a tender excerpt.
    """
    with _PACKAGES_LOCK:
        cached = facts.get('_scope_packages')
        if cached is not None:
            return cached
        register = package_register(pool)
        if len(register) >= 2:
            excerpts, owner = [], {}
            for entry in register:
                for item in entry['items'][:2]:          # the opening text of each package's documents
                    excerpts.append(item)
                    owner[item['id']] = entry['key']
            listing = '\n'.join(f"- {entry['key']}: excerpts {', '.join(i['id'] for i in entry['items'][:2])}"
                                for entry in register)
            result = _call(_payload(facts, SCOPE_PART, excerpts, ''), PACKAGE_SCHEMA,
                           f'{style}\nThe tender defines exactly these {len(register)} scope packages, in this '
                           f'order:\n{listing}\nReturn one package per line above, in the same order, with the '
                           f"package's full title as the tender states it (project number first when it has one) "
                           f'and the ids of that package\'s excerpts as citations. Do not add, merge or split packages.')
            titles = {}
            if result.get('status') == 'completed':
                for item in (result.get('proposal') or {}).get('packages') or []:
                    keys = {owner.get(c) for c in item.get('citations') or []} - {None}
                    title = ' '.join(str(item.get('title') or '').split())[:200]
                    if len(keys) == 1 and title:
                        titles.setdefault(keys.pop(), title)
            packages = [{'title': titles.get(entry['key']) or _register_title(entry), 'key': entry['key'],
                         'citations': [i['id'] for i in entry['items'][:2]], 'items': entry['items']}
                        for entry in register][:MAX_PACKAGES]
            if result.get('status') != 'completed' and not _provider_failure(result):
                result = {'status': 'completed', 'proposal': {'packages': []}}
            if result.get('status') == 'completed':
                result = {**result, 'register': True, 'untitled': [e['key'] for e in register if e['key'] not in titles]}
            cached = facts['_scope_packages'] = (result, packages)
            return cached
        excerpts = select_excerpts(SCOPE_PART, pool, standard, config['author']['excerpt_budget_bytes'] // 2)
        result = _call(_payload(facts, SCOPE_PART, excerpts, ''), PACKAGE_SCHEMA,
                       f'{style}\nList every separate scope package or work item the tender defines, in tender '
                       f'order, each with the ids of the excerpts that define it. Titles only, no descriptions. '
                       f'If the tender has one scope, return one package.') if excerpts else {
            'status': 'completed', 'proposal': {'packages': []}}
        allowed = {item['id'] for item in excerpts}
        listed = [item for item in (result.get('proposal') or {}).get('packages') or []
                  if str(item.get('title') or '').strip()] if result.get('status') == 'completed' else []
        packages = [item for item in listed if set(item.get('citations') or []) & allowed][:MAX_PACKAGES]
        if result.get('status') == 'completed':
            result = {**result, 'unconfirmed': [str(item['title'])[:120] for item in listed if item not in packages]}
        cached = facts['_scope_packages'] = (result, packages)
        return cached


def _open_points(label, points):
    return [finding('WARN', 'OPEN_POINT', f'{label}: {point}')
            for point in (points or []) if str(point).strip()][:MAX_OPEN_POINTS]


def draft_part(part, pool, standard, facts, config, reviewer_note=''):
    """Draft one configured part. Returns {'blocks', 'cited', 'findings'}."""
    author = config['author']
    budget = author['excerpt_budget_bytes']
    label = part['heading']
    findings, cited = [], set()
    style = author['style']
    if part.get('mode') == 'fixed':
        items = [item for item in standard if item['chapter'] in set(part.get('standard') or [])]
        intro = [item for item in items if not item.get('section')]
        rows = [item for item in items if item.get('section')]
        blocks = [{'type': 'paragraph', 'text': item['text'], 'citations': [item['id']]} for item in intro]
        if rows:
            blocks.append({'type': 'table', 'text': '', 'columns': list(part.get('columns') or ['Phase', 'Activities']),
                           'rows': [[item['section'], item['text']] for item in rows],
                           'citations': [item['id'] for item in rows]})
        return {'blocks': blocks, 'cited': {item['id'] for item in items}, 'findings': [] if items else [
            finding('ERROR', 'SECTION_NOT_DRAFTED', f'{label}: the approved standard text is missing')]}
    if part.get('mode') == 'packages':
        result, packages = scope_packages(pool, standard, facts, config, style)
        if result.get('status') != 'completed':
            return {'blocks': [], 'cited': set(), 'provider_failed': _provider_failure(result),
                    'findings': [finding('ERROR', 'SECTION_NOT_DRAFTED',
                    f"{label}: could not be drafted ({result.get('error_code') or 'provider unavailable'})")]}
        if not packages:
            return {'blocks': [], 'cited': set(), 'findings': [finding('ERROR', 'SECTION_NOT_DRAFTED',
                    f'{label}: no tender text describes the scope packages')]}
        blocks = []
        jobs = []
        failed_packages = 0
        for title in result.get('unconfirmed') or []:
            findings.append(finding('WARN', 'SCOPE_PACKAGE_UNCONFIRMED',
                                    f'{label}: "{title}" is not described by any tender excerpt and was left out'))
        for number, package in enumerate(packages, 1):
            title = package['title'].strip()
            words = re.findall(r'[A-Za-z]{4,}', title)
            if package.get('items'):
                package_excerpts = select_excerpts(part, list(package['items']), standard, budget // 2,
                                                   extra_keywords=words * 3)
                if not any(item in package['items'] for item in package_excerpts):
                    package_excerpts = list(package['items'][:3]) + package_excerpts
            else:
                package_excerpts = select_excerpts(part, pool, standard, budget // 2, extra_keywords=words * 3)
            jobs.append((number, title, package_excerpts))
        with ThreadPoolExecutor(max_workers=author['parallel_calls']) as executor:
            futures = [(number, title, excerpts_, executor.submit(
                _call, _payload(facts, part, excerpts_, reviewer_note, package=title), BLOCK_SCHEMA,
                f"{style}\n{part['guidance']}\nWrite only about this scope package: {title}. "
                f"Keep it under 450 words.")) for number, title, excerpts_ in jobs]
            for number, title, excerpts_, future in futures:
                outcome = future.result()
                blocks.append({'type': 'heading', 'level': 2, 'text': f'SCOPE {number}: {title.upper()}'})
                if outcome.get('status') != 'completed':
                    failed_packages += _provider_failure(outcome)
                    findings.append(finding('ERROR', 'SECTION_NOT_DRAFTED',
                                            f"{label} – scope {number}: could not be drafted ({outcome.get('error_code')})"))
                    continue
                clean, dropped, used = _clean_blocks(outcome['proposal'].get('blocks'), {e['id'] for e in excerpts_}, title)
                for block in clean:
                    if block['type'] == 'heading':
                        block['level'] = 3
                blocks.extend(clean)
                cited |= used
                if dropped:
                    findings.append(finding('WARN', 'UNSUPPORTED_STATEMENT_REMOVED',
                                            f'{label} – scope {number}: {dropped} statement(s) without a tender source were removed'))
                findings.extend(_open_points(f'{label} – scope {number}', outcome['proposal'].get('open_points')))
        return {'blocks': blocks, 'cited': cited, 'findings': findings,
                'provider_failed': bool(jobs) and failed_packages == len(jobs)}

    excerpts = select_excerpts(part, pool, standard, budget)
    if not excerpts:
        return {'blocks': [], 'cited': set(), 'findings': [finding('ERROR', 'SECTION_NOT_DRAFTED',
                f'{label}: no tender text relates to this part')]}
    shape = ('Present the result as one table block (columns as described) with a short introductory paragraph.'
             if part.get('mode') == 'table' else 'Keep it under 450 words. Do not repeat the part heading.')
    result = _call(_payload(facts, part, excerpts, reviewer_note), BLOCK_SCHEMA,
                   f"{style}\n{part['guidance']}\n{shape}")
    if result.get('status') != 'completed':
        return {'blocks': [], 'cited': set(), 'provider_failed': _provider_failure(result),
                'findings': [finding('ERROR', 'SECTION_NOT_DRAFTED',
                f"{label}: could not be drafted ({result.get('error_code') or 'provider unavailable'})")]}
    blocks, dropped, cited = _clean_blocks(result['proposal'].get('blocks'), {item['id'] for item in excerpts}, label)
    if dropped:
        findings.append(finding('WARN', 'UNSUPPORTED_STATEMENT_REMOVED',
                                f'{label}: {dropped} statement(s) without a tender source were removed'))
    findings.extend(_open_points(label, result['proposal'].get('open_points')))
    return {'blocks': blocks, 'cited': cited, 'findings': findings}


def _previous_parts(blocks):
    """Split a stored section's blocks into {part heading: [blocks after the part marker]}."""
    parts, current = {}, None
    for block in blocks or []:
        if block.get('type') == 'part':
            current = parts.setdefault(block.get('text') or '', [])
        elif current is not None:
            current.append(block)
    return parts


def draft_section(section, pool, standard, facts, config, reviewer_note='', previous_blocks=None):
    """Content for one configured section: authored parts, evidence placeholder or fixed text.

    A part the AI service could not draft keeps its text from `previous_blocks` (the latest
    version) when there is one. The result counts `ai_parts` and `ai_failed` (provider failures).
    """
    kind, label = section['kind'], section['title']
    if kind == 'evidence':
        items = ', '.join(section.get('evidence') or [])
        return {'blocks': [{'type': 'part', 'text': section['title'].upper()},
                           {'type': 'placeholder', 'text': f'[Company evidence to be inserted: {items}]'}],
                'cited': set(), 'findings': [finding('ERROR', 'EVIDENCE_REQUIRED',
                f'{label}: company evidence required ({items}); supplied by the Rejlers Evidence Library')]}
    if kind == 'not_applicable':
        return {'blocks': [{'type': 'part', 'text': section['title'].upper()},
                           {'type': 'paragraph', 'text': 'Not Applicable'}], 'cited': set(), 'findings': []}
    blocks, cited, findings = [], set(), []
    outcomes = []
    previous = _previous_parts(previous_blocks)
    ai_failed = 0
    with ThreadPoolExecutor(max_workers=config['author']['parallel_calls']) as executor:
        futures = [(part, executor.submit(draft_part, part, pool, standard, facts, config, reviewer_note))
                   for part in section.get('parts') or []]
        for part, future in futures:
            outcomes.append((part, future.result()))
    for part, outcome in outcomes:
        blocks.append({'type': 'part', 'text': part['heading']})
        kept = previous.get(part['heading']) if outcome.get('provider_failed') else None
        if outcome.get('provider_failed'):
            ai_failed += 1
        if kept:
            blocks.extend(kept)
            cited |= {c for block in kept for c in block.get('citations') or []}
            findings.append(finding('WARN', 'PART_KEPT_FROM_PREVIOUS',
                                    f"{part['heading']}: the AI service was unavailable, so the previous "
                                    f"version's text is kept"))
            continue
        blocks.extend(outcome['blocks'])
        cited |= outcome['cited']
        findings.extend(outcome['findings'])
    # Keep reviewer attention on the most important open points of each section.
    open_points = [item for item in findings if item['code'] == 'OPEN_POINT']
    if len(open_points) > MAX_SECTION_OPEN_POINTS:
        extra = len(open_points) - MAX_SECTION_OPEN_POINTS
        keep = {id(item) for item in open_points[:MAX_SECTION_OPEN_POINTS]}
        findings = [item for item in findings if item['code'] != 'OPEN_POINT' or id(item) in keep]
        findings.append(finding('WARN', 'OPEN_POINTS_SUMMARISED',
                                f"{section['title']}: {extra} further open points were noted while writing"))
    return {'blocks': blocks, 'cited': cited, 'findings': findings,
            'ai_parts': sum(1 for part, _ in outcomes if part.get('mode') != 'fixed'), 'ai_failed': ai_failed}


# --- facts ----------------------------------------------------------------------------------

def _ordinal(day):
    return f"{day}{'th' if 11 <= day % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')}"


def proposal_facts(quote, pool, config, version):
    deal = quote.deal
    today = date.today()
    revision = max(0, version - 1)
    facts = {
        'quote_ref': deal.deal_code, 'client': quote.client.company_name, 'tender_title': deal.deal_name,
        'company': config['company']['name'], 'revision': revision,
        'date': today.strftime('%d %B %Y'),
        'revision_line': f'REV {revision}, {_ordinal(today.day)} {today.strftime("%B %Y").upper()}',
    }
    provenance = {key: {'value': value, 'source': 'Opportunity record'} for key, value in
                  (('quote_ref', facts['quote_ref']), ('client', facts['client']), ('tender_title', facts['tender_title']))}
    # Facts come from the Request for Tender itself first, then other primary tender documents.
    ordered = sorted(pool, key=lambda item: (not RFT_SOURCE.search(item['source'] or ''), not item.get('primary'),
                                             not item.get('requirement')))
    for name, rule in (config.get('facts') or {}).items():
        if rule.get('mode') == 'most_frequent':
            counts, first = {}, {}
            for item in ordered:
                for match in re.finditer(rule['pattern'], item['text'], re.I):
                    value = _name_case(match.group(1))
                    counts[value] = counts.get(value, 0) + 1
                    first.setdefault(value, item)
            if counts:
                value = max(counts, key=lambda key: (counts[key], len(key)))
                facts[name] = value
                provenance[name] = {'value': value, 'source': first[value]['source'],
                                    'location': first[value]['location']}
            continue
        for item in ordered:
            match = re.search(rule['pattern'], item['text'], re.I)
            if match:
                facts[name] = match.group(1).strip().rstrip('.')
                provenance[name] = {'value': facts[name], 'source': item['source'], 'location': item['location']}
                break
    if facts.get('company_name'):
        # The proposal addresses the client as the tender names it; the CRM record is kept for review.
        facts['client_record'] = facts['client']
        facts['client'] = facts['company_name']
        provenance['client'] = dict(provenance['company_name'])
    return facts, provenance


def _name_case(value):
    """'ADNOC ONSHORE' / 'adnoc onshore' -> 'ADNOC Onshore' (acronyms stay upper case)."""
    words = ' '.join(str(value).replace('-', ' ').split()).split(' ')
    return ' '.join(word.upper() if word.upper() in {'ADNOC', 'LLC', 'PJSC'} else word.capitalize() for word in words)


# --- full proposal --------------------------------------------------------------------------

EDITABLE_KINDS = ('authored', 'not_applicable', 'cover', 'letter', 'compliance')
GENERATED_KINDS = ('cover', 'letter', 'compliance')
MAX_EDIT_BLOCKS = 400
MAX_RUNS = 400
_MARKUP = re.compile(r'<\s*[a-zA-Z/!?]')
_FONT = re.compile(r'^[A-Za-z0-9][A-Za-z0-9 \-]{0,62}$')
_COLOR = re.compile(r'^#[0-9A-Fa-f]{6}$')
_ALIGN = ('left', 'center', 'right', 'justify')
_RUN_KEYS = {'text', 'bold', 'italic', 'underline', 'strike', 'font', 'size', 'color', 'highlight'}


def _edit_text(value, field, limit, required=True):
    if not isinstance(value, str):
        raise ValidationError({'blocks': f'{field} must be text.'})
    text = value.strip()
    if required and not text:
        raise ValidationError({'blocks': f'{field} cannot be empty.'})
    if len(text) > limit:
        raise ValidationError({'blocks': f'{field} is longer than {limit} characters.'})
    if _MARKUP.search(text):
        raise ValidationError({'blocks': f'{field} must be plain text, without markup.'})
    return text


def _number(value, low, high, field):
    if type(value) not in (int, float) or value != value or not low <= value <= high:
        raise ValidationError({'blocks': f'{field} must be a number between {low} and {high}.'})
    return round(float(value), 1)


def _edit_runs(value, text, field, limit, required=True):
    """Character formatting of one text: returns (plain text, runs or None).

    The plain text is the runs joined and trimmed; runs without formatting are dropped so plain
    blocks stay plain. Fonts, sizes and colours are whitelisted shapes, never markup."""
    if value is None:
        return _edit_text(text if text is not None else '', field, limit, required), None
    if not isinstance(value, list) or not value or len(value) > MAX_RUNS:
        raise ValidationError({'blocks': f'{field}: provide between 1 and {MAX_RUNS} formatted runs.'})
    runs = []
    for run in value:
        if not isinstance(run, dict) or not isinstance(run.get('text'), str) or set(run) - _RUN_KEYS:
            raise ValidationError({'blocks': f'{field}: a formatted run is invalid.'})
        item = {'text': run['text']}
        for flag in ('bold', 'italic', 'underline', 'strike'):
            if run.get(flag) is True:
                item[flag] = True
        if run.get('font') is not None:
            if not isinstance(run['font'], str) or not _FONT.match(run['font']):
                raise ValidationError({'blocks': f'{field}: the font name is invalid.'})
            item['font'] = run['font']
        if run.get('size') is not None:
            item['size'] = _number(run['size'], 6, 72, f'{field} font size')
        for name in ('color', 'highlight'):
            if run.get(name) is not None:
                if not isinstance(run[name], str) or not _COLOR.match(run[name]):
                    raise ValidationError({'blocks': f'{field}: colours are #RRGGBB values.'})
                item[name] = run[name].upper()
        if runs and {k: v for k, v in runs[-1].items() if k != 'text'} == {k: v for k, v in item.items() if k != 'text'}:
            runs[-1]['text'] += item['text']
        else:
            runs.append(item)
    runs[0]['text'] = runs[0]['text'].lstrip()
    runs[-1]['text'] = runs[-1]['text'].rstrip()
    runs = [run for run in runs if run['text']]
    plain = _edit_text(''.join(run['text'] for run in runs), field, limit, required)
    return plain, (runs if any(len(run) > 1 for run in runs) else None)


def _paragraph_format(block, record, field):
    align = block.get('align')
    if align is not None:
        if align not in _ALIGN:
            raise ValidationError({'blocks': f'{field}: alignment is left, center, right or justify.'})
        record['align'] = align
    indent = block.get('indent')
    if indent is not None:
        if not isinstance(indent, dict) or set(indent) - {'left', 'first'}:
            raise ValidationError({'blocks': f'{field}: indent has left and first values in points.'})
        left = _number(indent.get('left', 0), 0, 288, f'{field} left indent')
        first = _number(indent.get('first', 0), -144, 288, f'{field} first-line indent')
        if left or first:
            record['indent'] = {'left': left, 'first': first}


def _runs_list(value, count, field):
    if value is None:
        return [None] * count
    if not isinstance(value, list) or len(value) != count:
        raise ValidationError({'blocks': f'{field}: formatting must match the number of entries.'})
    return value


def validate_section_blocks(original, edited, generated=False):
    """A reviewer's edit of one section, made safe to render.

    Each edited block may name the original block it came from (`ref`, its index). A block that
    keeps its origin keeps that origin's tender citations: the reviewer reworded sourced content.
    New blocks carry no citations and are marked as reviewer-written. Part headings (the fixed
    method-statement chapters) cannot be renamed, removed or reordered. Optional formatting
    (runs, alignment, indents, list markers and levels, table header and cell formatting) is
    validated here and reproduced by the renderer. `generated` sections (cover, letter,
    compliance) have no tender citations.
    """
    if not isinstance(edited, list) or not edited or len(edited) > MAX_EDIT_BLOCKS:
        raise ValidationError({'blocks': f'Provide between 1 and {MAX_EDIT_BLOCKS} blocks.'})
    blocks = []
    for position, block in enumerate(edited, 1):
        if not isinstance(block, dict) or block.get('type') not in ('part', 'heading', 'paragraph', 'bullets', 'table'):
            raise ValidationError({'blocks': f'Block {position} has an unsupported type.'})
        ref = block.get('ref')
        source = original[ref] if isinstance(ref, int) and 0 <= ref < len(original) else None
        if source is not None and source.get('type') != block['type']:
            source = None
        kind = block['type']
        if kind == 'part':
            if source is None:
                raise ValidationError({'blocks': 'Chapter headings cannot be added or changed.'})
            blocks.append(dict(source))
            continue
        label = f'Block {position}'
        record = {'type': kind}
        if kind == 'heading':
            record['text'], runs = _edit_runs(block.get('runs'), block.get('text'), label, 200)
            record['level'] = block.get('level') if block.get('level') in (1, 2, 3) else 2
        elif kind == 'paragraph':
            record['text'], runs = _edit_runs(block.get('runs'), block.get('text'), label, 4000)
        elif kind == 'bullets':
            items = block.get('items')
            if not isinstance(items, list) or not items or len(items) > 100:
                raise ValidationError({'blocks': f'{label}: provide between 1 and 100 bullet items.'})
            record['text'], runs = _edit_runs(block.get('runs'), block.get('text') or '', f'{label} introduction', 4000, required=False)
            item_runs = _runs_list(block.get('item_runs'), len(items), f'{label} items')
            pairs = [_edit_runs(item_runs[i], item, f'{label} item', 1000) for i, item in enumerate(items)]
            record['items'] = [plain for plain, _ in pairs]
            if any(formatted for _, formatted in pairs):
                record['item_runs'] = [formatted for _, formatted in pairs]
            markers = block.get('markers')
            if markers is not None:
                if not isinstance(markers, list) or len(markers) != len(items) or set(markers) - {'bullet', 'number'}:
                    raise ValidationError({'blocks': f'{label}: list markers are bullet or number, one per item.'})
                if 'number' in markers:
                    record['markers'] = list(markers)
            levels = block.get('levels')
            if levels is not None:
                if not isinstance(levels, list) or len(levels) != len(items) or any(level not in (0, 1, 2) for level in levels):
                    raise ValidationError({'blocks': f'{label}: list levels are 0, 1 or 2, one per item.'})
                if any(levels):
                    record['levels'] = list(levels)
        else:
            columns, rows = block.get('columns'), block.get('rows')
            header = block.get('header', True) is not False
            if not isinstance(columns, list) or not 1 <= len(columns) <= 8:
                raise ValidationError({'blocks': f'{label}: a table has 1 to 8 columns.'})
            if not isinstance(rows, list) or not 1 <= len(rows) <= 300:
                raise ValidationError({'blocks': f'{label}: a table has 1 to 300 rows.'})
            record['text'], runs = _edit_runs(block.get('runs'), block.get('text') or '', f'{label} introduction', 4000, required=False)
            column_runs = _runs_list(block.get('column_runs'), len(columns), f'{label} columns')
            pairs = [_edit_runs(column_runs[i], value, f'{label} column', 120, required=header)
                     for i, value in enumerate(columns)]
            record['columns'] = [plain for plain, _ in pairs]
            if header and any(formatted for _, formatted in pairs):
                record['column_runs'] = [formatted for _, formatted in pairs]
            if not header:
                record['header'] = False
            cell_runs = block.get('cell_runs') or {}
            if not isinstance(cell_runs, dict) or len(cell_runs) > 2400:
                raise ValidationError({'blocks': f'{label}: cell formatting is invalid.'})
            record['rows'], formatted_cells = [], {}
            for row_index, row in enumerate(rows):
                if not isinstance(row, list) or len(row) > len(columns):
                    raise ValidationError({'blocks': f'{label}: each row has at most one cell per column.'})
                cells = []
                for column, cell in enumerate(row):
                    key = f'{row_index}:{column}'
                    plain, formatted = _edit_runs(cell_runs.get(key), cell, f'{label} cell', 600, required=False)
                    cells.append(plain)
                    if formatted:
                        formatted_cells[key] = formatted
                record['rows'].append(cells)
            unknown = set(cell_runs) - {f'{r}:{c}' for r, row in enumerate(rows) for c in range(len(row))}
            if unknown:
                raise ValidationError({'blocks': f'{label}: cell formatting refers to a missing cell.'})
            if formatted_cells:
                record['cell_runs'] = formatted_cells
        if runs:
            record['runs'] = runs
        _paragraph_format(block, record, label)
        if kind != 'heading':
            if generated:
                record['citations'], record['source'] = [], 'reviewer'
            elif source is not None:
                record['citations'] = list(source.get('citations') or [])
                if source.get('source'):
                    record['source'] = source['source']
            else:
                record['citations'], record['source'] = [], 'reviewer'
        blocks.append(record)
    parts = lambda items: [b.get('text') for b in items if b.get('type') == 'part']  # noqa: E731
    if parts(blocks) != parts(original):
        raise ValidationError({'blocks': 'Chapter headings must stay in their original order.'})
    return blocks


def validate_layout(defaults, layout):
    """Reviewer header/footer lines: same number of lines as the template, plain text."""
    if not isinstance(layout, dict) or set(layout) - {'header_lines', 'footer_lines'}:
        raise ValidationError({'layout': 'Provide header_lines and footer_lines.'})
    clean = {}
    for name in ('header_lines', 'footer_lines'):
        lines = layout.get(name, defaults.get(name) or [])
        if not isinstance(lines, list) or len(lines) != len(defaults.get(name) or []):
            raise ValidationError({'layout': f'{name} must keep {len(defaults.get(name) or [])} lines.'})
        clean[name] = [_edit_text(line, f'{name} line', 200, required=False) for line in lines]
    return clean


def validate_title(value):
    return _edit_text(value, 'Section title', 200)


def paragraph_records(section_key, blocks, requirement_ids):
    records = []
    for index, block in enumerate(blocks):
        if block['type'] in ('part', 'heading'):
            continue
        if block['type'] == 'bullets':
            text = '\n'.join(f'• {item}' for item in block.get('items') or [])
            if block.get('text'):
                text = f"{block['text']}\n{text}"
        elif block['type'] == 'table':
            text = '\n'.join(' | '.join(row) for row in [block.get('columns') or []] + (block.get('rows') or []))
        else:
            text = block.get('text') or ''
        citations = block.get('citations') or []
        digest = hashlib.sha256(f'{section_key}:{index}:{text}'.encode()).hexdigest()[:12]
        kind = 'reviewer' if block.get('source') == 'reviewer' else block['type']
        records.append({'id': f'PAR-{digest}', 'kind': kind, 'text': text[:100_000],
                        'requirement_ids': [c for c in citations if c in requirement_ids],
                        'evidence_ids': list(citations)})
    return records
