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
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from django.db import close_old_connections

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
             'chapter': item['chapter']} for item in data['items']]


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


def _call(payload, schema, instructions):
    from .email_ai_provider import analyze_technical_proposal_sources

    try:
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
    repeated = {re.sub(r'[^a-z]', '', part_heading.casefold())} - {''}
    for block in raw_blocks or []:
        kind = block.get('type')
        citations = [value for value in block.get('citations') or [] if value in allowed]
        text = str(block.get('text') or '').strip()
        if kind == 'heading':
            if text and re.sub(r'[^a-z]', '', text.casefold()) not in repeated:  # never repeat the part heading
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
        cited.update(citations)
        blocks.append(record)
    return blocks, dropped, cited


MAX_OPEN_POINTS = 5
MAX_SECTION_OPEN_POINTS = 8
_PACKAGES_LOCK = threading.Lock()
SCOPE_PART = {'heading': 'SCOPE OF SERVICES', 'categories': ['technical'],
              'keywords': ['scope', 'package', 'sow', 'terms of reference', 'study', 'feed', 'design', 'engineering']}


def scope_packages(pool, standard, facts, config, style):
    """The tender's scope packages, listed once per generation and shared by every part that
    is written per package (scope of services, deliverables)."""
    with _PACKAGES_LOCK:
        cached = facts.get('_scope_packages')
        if cached is None:
            excerpts = select_excerpts(SCOPE_PART, pool, standard, config['author']['excerpt_budget_bytes'] // 2)
            result = _call(_payload(facts, SCOPE_PART, excerpts, ''), PACKAGE_SCHEMA,
                           f'{style}\nList every separate scope package or work item the tender defines, in tender '
                           f'order, each with the ids of the excerpts that define it. Titles only, no descriptions. '
                           f'If the tender has one scope, return one package.') if excerpts else {
                'status': 'completed', 'proposal': {'packages': []}}
            allowed = {item['id'] for item in excerpts}
            packages = [item for item in (result.get('proposal') or {}).get('packages') or []
                        if str(item.get('title') or '').strip() and set(item.get('citations') or []) & allowed
                        ][:MAX_PACKAGES] if result.get('status') == 'completed' else []
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
    if part.get('mode') == 'packages':
        result, packages = scope_packages(pool, standard, facts, config, style)
        if result.get('status') != 'completed':
            return {'blocks': [], 'cited': set(), 'findings': [finding('ERROR', 'SECTION_NOT_DRAFTED',
                    f"{label}: could not be drafted ({result.get('error_code') or 'provider unavailable'})")]}
        if not packages:
            return {'blocks': [], 'cited': set(), 'findings': [finding('ERROR', 'SECTION_NOT_DRAFTED',
                    f'{label}: no tender text describes the scope packages')]}
        blocks = []
        jobs = []
        for number, package in enumerate(packages, 1):
            title = package['title'].strip()
            words = re.findall(r'[A-Za-z]{4,}', title)
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
        return {'blocks': blocks, 'cited': cited, 'findings': findings}

    excerpts = select_excerpts(part, pool, standard, budget)
    if not excerpts:
        return {'blocks': [], 'cited': set(), 'findings': [finding('ERROR', 'SECTION_NOT_DRAFTED',
                f'{label}: no tender text relates to this part')]}
    shape = ('Present the result as one table block (columns as described) with a short introductory paragraph.'
             if part.get('mode') == 'table' else 'Keep it under 450 words. Do not repeat the part heading.')
    result = _call(_payload(facts, part, excerpts, reviewer_note), BLOCK_SCHEMA,
                   f"{style}\n{part['guidance']}\n{shape}")
    if result.get('status') != 'completed':
        return {'blocks': [], 'cited': set(), 'findings': [finding('ERROR', 'SECTION_NOT_DRAFTED',
                f"{label}: could not be drafted ({result.get('error_code') or 'provider unavailable'})")]}
    blocks, dropped, cited = _clean_blocks(result['proposal'].get('blocks'), {item['id'] for item in excerpts}, label)
    if dropped:
        findings.append(finding('WARN', 'UNSUPPORTED_STATEMENT_REMOVED',
                                f'{label}: {dropped} statement(s) without a tender source were removed'))
    findings.extend(_open_points(label, result['proposal'].get('open_points')))
    return {'blocks': blocks, 'cited': cited, 'findings': findings}


def draft_section(section, pool, standard, facts, config, reviewer_note=''):
    """Content for one configured section: authored parts, evidence placeholder or fixed text."""
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
    with ThreadPoolExecutor(max_workers=config['author']['parallel_calls']) as executor:
        futures = [(part, executor.submit(draft_part, part, pool, standard, facts, config, reviewer_note))
                   for part in section.get('parts') or []]
        for part, future in futures:
            outcomes.append((part, future.result()))
    for part, outcome in outcomes:
        blocks.append({'type': 'part', 'text': part['heading']})
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
    return {'blocks': blocks, 'cited': cited, 'findings': findings}


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
        for item in ordered:
            match = re.search(rule['pattern'], item['text'], re.I)
            if match:
                facts[name] = match.group(1).strip().rstrip('.')
                provenance[name] = {'value': facts[name], 'source': item['source'], 'location': item['location']}
                break
    return facts, provenance


# --- full proposal --------------------------------------------------------------------------

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
        records.append({'id': f'PAR-{digest}', 'kind': block['type'], 'text': text[:100_000],
                        'requirement_ids': [c for c in citations if c in requirement_ids],
                        'evidence_ids': list(citations)})
    return records
