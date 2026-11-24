"""Deterministic tender requirement extraction and evidence matching."""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict


_OBLIGATION = re.compile(
    r'\b(?:shall|must|will|should|is required to|are required to|submit|provide|include)\b',
    re.I,
)
_MANDATORY = re.compile(r'\b(?:shall|must|required|mandatory)\b', re.I)
_NEGATIVE = re.compile(r'\b(?:shall|must|will)\s+not\b', re.I)
_CLAUSE = re.compile(r'(?m)^\s*((?:\d{1,3}\.){1,6}\d+|\d{1,3}\.)(?:\s+|$)')
_AMENDMENT = re.compile(
    r'(?is)(?:clause\s+)?(?P<clause>\d+(?:\.\d+)*)\s+'
    r'(?P<action>is\s+replaced|is\s+amended|amended|replaced)'
)


def _source_text(source):
    chunks = source.get('chunks')
    if not isinstance(chunks, list):
        return ''
    return ''.join(str(item.get('text') or '') for item in chunks if isinstance(item, dict))


def _source_type(text, name=''):
    combined = f'{name}\n{text}'.casefold()
    patterns = (
        ('Bulletin', r'\btender\s+bulletin\b'),
        ('Addendum', r'\baddendum\b'),
        ('RFT', r'\b(?:request for tender|rft)\b'),
        ('SOW', r'\b(?:statement of work|scope of work|sow)\b'),
        ('Specification', r'\b(?:technical specification|specification)\b'),
    )
    return next((label for label, pattern in patterns if re.search(pattern, combined)), 'Unclassified')


def _requirement_category(text):
    lowered = text.casefold()
    categories = []
    terms = (
        ('commercial', ('price', 'pricing', 'rate', 'commercial', 'payment')),
        ('submission', ('submit', 'submission', 'schedule', 'form')),
        ('qualification', ('iso', 'certificate', 'certification', 'qualification', 'experience')),
        ('quality', ('quality', 'inspection', 'test', 'verification')),
        ('hse', ('hse', 'safety', 'environment', 'risk assessment')),
        ('technical', ('technical', 'system', 'design', 'engineering', 'method statement')),
    )
    for category, words in terms:
        if any(word in lowered for word in words):
            categories.append(category)
    if not categories:
        categories.append('technical')
    return categories


def _clauses(text):
    matches = list(_CLAUSE.finditer(text))
    if not matches:
        return [('', text, 0)]
    rows = []
    prefix = text[:matches[0].start()].strip()
    if prefix:
        rows.append(('', prefix, 0))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        rows.append((match.group(1).rstrip('.'), text[match.end():end].strip(), match.start()))
    return rows


def _statements(text):
    """Split clauses without truncating obligation sentences crossing storage chunks."""
    for clause, body, offset in _clauses(text):
        if not body:
            continue
        for match in re.finditer(r'[^.!?\n]+(?:[.!?](?=\s|$)|\n|$)', body):
            value = match.group(0).strip(' \t\r\n')
            if value:
                yield clause, value, offset + match.start()


def _location_at(source, offset):
    chunks = source.get('chunks')
    if not isinstance(chunks, list):
        return 'Unknown source location'
    consumed = 0
    for chunk in chunks:
        text = str(chunk.get('text') or '') if isinstance(chunk, dict) else ''
        next_consumed = consumed + len(text)
        if offset < next_consumed:
            return str(chunk.get('location') or 'Document text')
        consumed = next_consumed
    return str(chunks[-1].get('location') or 'Document text') if chunks else 'Document text'


def _identifier(source, statement, location, sequence):
    source_identity = source.get('source_identity') or source.get('source_id') or sequence
    payload = f'{source_identity}|{location}|{statement}|{sequence}'
    return 'TR-' + hashlib.sha256(payload.encode()).hexdigest()[:16].upper()


def _page(location):
    match = re.search(r'\bpage\s+(\d+)', str(location), re.I)
    return int(match.group(1)) if match else None


def extract_tender_requirements(sources):
    """Extract every recognized obligation with immutable source coordinates."""
    sources = [item for item in sources if isinstance(item, dict)]
    tender_sources = [
        item for item in sources
        if str(item.get('folder') or '').casefold() in {'tender', 'tender documents'}
    ]
    requirements = []
    amendments = []
    source_failures = 0
    unreadable = 0
    partial = 0
    unlinked = 0
    specification_statements = 0
    specification_sources = 0

    for source in tender_sources:
        status = str(source.get('parse_status') or '')
        if status == 'failed':
            source_failures += 1
            unreadable += 1
            continue
        if status == 'partial':
            partial += 1
        text = _source_text(source)
        source_type = _source_type(text, str(source.get('source_name') or ''))
        if source_type == 'Specification':
            specification_sources += 1
        linked = bool(source.get('source_id') and source.get('source_identity'))
        source_ids_by_clause = defaultdict(list)

        for clause, statement, offset in _statements(text):
            if not _OBLIGATION.search(statement):
                continue
            if not linked:
                unlinked += 1
                continue
            location = _location_at(source, offset)
            item_id = _identifier(source, statement, location, len(requirements) + 1)
            item = {
                'id': item_id,
                'requirement_text': statement.strip(),
                'source_id': source.get('source_id'),
                'source_identity': source.get('source_identity'),
                'source_file': source.get('source_name') or 'Unknown source',
                'source_version': str(source.get('source_version') or ''),
                'page': _page(location),
                'source_location': location,
                'clause_location': clause,
                'source_type': source_type,
                'requirement_type': (
                    'commercial' if 'commercial' in _requirement_category(statement)
                    else 'qualification' if 'qualification' in _requirement_category(statement)
                    else 'technical'
                ),
                'requirement_categories': _requirement_category(statement),
                'mandatory_optional': 'mandatory' if _MANDATORY.search(statement) else 'optional',
                'superseded_by': [],
                'amended_by': [],
            }
            requirements.append(item)
            if clause:
                source_ids_by_clause[clause].append(item_id)
            if source_type == 'Specification':
                specification_statements += 1

        if source_type in {'Addendum', 'Bulletin'}:
            for match in _AMENDMENT.finditer(text):
                action = 'replaces' if 'replac' in match.group('action') else 'amends'
                clause = match.group('clause')
                amendment_id = 'AMD-' + hashlib.sha256(
                    f'{source.get("source_id")}|{clause}|{action}|{len(amendments)}'.encode()
                ).hexdigest()[:16].upper()
                amendments.append({
                    'id': amendment_id,
                    'source_id': source.get('source_id'),
                    'source_version': str(source.get('source_version') or ''),
                    'clause_location': clause,
                    'action': action,
                    'linked_requirement_ids': [],
                    'resolution_status': 'pending',
                })

    by_clause = defaultdict(list)
    for requirement in requirements:
        if requirement.get('clause_location'):
            by_clause[requirement['clause_location']].append(requirement)
    for amendment in amendments:
        prior = [
            item for item in by_clause.get(amendment['clause_location'], [])
            if item['source_id'] != amendment['source_id']
        ]
        amendment['resolution_status'] = (
            'matched_unique_source' if len({item['source_id'] for item in prior}) == 1
            else 'ambiguous' if prior else 'unmatched'
        )
        amendment['linked_requirement_ids'] = [item['id'] for item in prior]
        for item in prior:
            target = 'superseded_by' if amendment['action'] == 'replaces' else 'amended_by'
            item[target].append(amendment['id'])

    detected = len(requirements) + unlinked
    coverage = {
        'status': 'processed' if not (unlinked or unreadable or partial) else 'partial',
        'requirements_detected': detected,
        'requirements_captured': len(requirements),
        'requirements_missing': detected - len(requirements),
        'requirement_coverage_percent': round(len(requirements) * 100 / detected, 2) if detected else 100.0,
        'requirements_extracted': len(requirements),
        'records_truncated': False,
        'source_files': len(tender_sources),
        'source_files_unclassified': sum(_source_type(_source_text(item), item.get('source_name', '')) == 'Unclassified'
                                           for item in tender_sources),
        'source_files_unreadable': unreadable,
        'source_files_partial': partial,
        'source_files_failed': source_failures,
        'unlinked_candidates_excluded': unlinked,
        'source_linkage_complete': unlinked == 0,
        'semantic_completeness_verified': False,
        'specification_statements_indexed': specification_statements,
        'specification_sources': specification_sources,
        'source_provenance_percent': (
            round(sum(bool(item.get('source_id') and item.get('source_identity')) for item in requirements)
                  * 100 / len(requirements), 2) if requirements else 100.0
        ),
        'requirement_extraction_passes': max(1, (len(sources) + 49) // 50),
        'requirement_extraction_pass_size': 50,
    }
    return requirements, amendments, coverage


def _normal_form(value):
    text = str(value).strip().casefold()
    text = _NEGATIVE.sub('must not', text)
    text = re.sub(r'\bshall\b', 'must', text)
    text = re.sub(r'\bshould\b', 'may', text)
    return re.sub(r'[^a-z0-9]+', ' ', text).strip()


def normalize_requirements(requirements):
    groups = {}
    order = []
    audit = []
    for item in requirements:
        normalized = _normal_form(item.get('requirement_text'))
        if normalized not in groups:
            identifier = 'NR-' + hashlib.sha256(normalized.encode()).hexdigest()[:16].upper()
            groups[normalized] = {
                'id': identifier,
                'requirement_text': item.get('requirement_text', ''),
                'original_requirement_ids': [],
                'source_references': [],
            }
            order.append(normalized)
        target = groups[normalized]
        target['original_requirement_ids'].append(item.get('id'))
        reference = {
            'original_requirement_id': item.get('id'),
            'source_id': item.get('source_id'),
            'source_identity': item.get('source_identity'),
            'source_file': item.get('source_file'),
            'source_location': item.get('source_location'),
            'evidence_snippet': item.get('requirement_text'),
        }
        target['source_references'].append(reference)
        audit.append({
            'original_requirement_id': item.get('id'),
            'normalized_requirement_id': target['id'],
            'action': 'merged_equivalent_requirement' if len(target['original_requirement_ids']) > 1
            else 'retained',
        })
    normalized = [groups[key] for key in order]
    return normalized, {
        'audit': audit,
        'duplicate_requirements': len(requirements) - len(normalized),
    }


def _keywords(text):
    stop = {
        'the', 'and', 'for', 'with', 'that', 'from', 'shall', 'must', 'will', 'should',
        'provide', 'submit', 'include', 'prepared', 'supplier', 'contractor', 'project',
    }
    return {word for word in re.findall(r'[a-z0-9-]{3,}', text.casefold()) if word not in stop}


def _evidence_type(text):
    lowered = text.casefold()
    if 'subcontractor' in lowered or 'specialist partner' in lowered:
        return 'subcontractor'
    if 'project reference' in lowered or 'previous project' in lowered:
        return 'project_reference'
    if 'cv' in lowered or 'curriculum vitae' in lowered or 'key personnel' in lowered:
        return 'personnel'
    if 'certificate' in lowered or 'certification' in lowered or 'iso ' in lowered:
        return 'certificate'
    return 'company_evidence'


def map_capability_evidence(requirements, sources):
    """Return reviewable candidates only; never claim verified capability."""
    evidence_sources = [
        item for item in sources
        if isinstance(item, dict)
        and str(item.get('folder') or '').casefold() not in {'tender', 'tender documents'}
        and item.get('parse_status') in {None, '', 'ready', 'partial', 'extracted'}
    ]
    chunks_considered = sum(len(item.get('chunks') or []) for item in evidence_sources)
    mappings = []
    for requirement in requirements:
        needle = _keywords(requirement.get('requirement_text', ''))
        candidates = []
        for source in evidence_sources:
            for chunk in source.get('chunks') or []:
                text = str(chunk.get('text') or '')
                words = _keywords(text)
                overlap = needle & words
                if not overlap or (len(needle) > 1 and len(overlap) < min(2, len(needle))):
                    continue
                candidates.append({
                    'source_id': source.get('source_id'),
                    'source_identity': source.get('source_identity'),
                    'source_file': source.get('source_name') or 'Unknown source',
                    'source_location': chunk.get('location') or 'Document text',
                    'evidence_type': _evidence_type(text),
                    'excerpt': text.strip(),
                    'status': 'candidate_needs_human_review',
                })
        mappings.append({
            'requirement_id': requirement.get('id'),
            'candidate_evidence': candidates,
            'evidence_gap': not candidates,
        })
    return mappings, {
        'capability_or_compliance_claimed': False,
        'requirements_without_candidate_evidence': sum(item['evidence_gap'] for item in mappings),
        'company_evidence_chunks_considered': chunks_considered,
        'evidence_capture_passes': max(1, (len(evidence_sources) + 49) // 50),
    }


def requirement_matrix_is_complete(coverage, requirement_count):
    return bool(
        isinstance(coverage, dict)
        and requirement_count >= 1
        and coverage.get('requirements_detected') == coverage.get('requirements_captured') == requirement_count
        and coverage.get('requirements_missing') == 0
        and float(coverage.get('requirement_coverage_percent') or 0) >= 100
        and not coverage.get('records_truncated')
        and not coverage.get('source_files_unclassified')
        and not coverage.get('source_files_unreadable')
        and not coverage.get('source_files_partial')
        and not coverage.get('unlinked_candidates_excluded')
    )


def proposal_source_coverage_is_complete(coverage):
    return bool(
        isinstance(coverage, dict)
        and coverage.get('files_selected', 0) > 0
        and coverage.get('files_processed') == coverage.get('files_selected')
        and coverage.get('files_failed') == 0
        and coverage.get('files_unreadable') == 0
        and coverage.get('files_partial') == 0
        and coverage.get('mandatory_documents_processed') is True
        and coverage.get('critical_file_failures') == 0
        and coverage.get('source_selection_complete') is True
    )
