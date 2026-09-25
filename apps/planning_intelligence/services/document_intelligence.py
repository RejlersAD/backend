"""Provenance-first document classification and engineering fact extraction."""
from __future__ import annotations

import hashlib
import os
import re
from collections import defaultdict
from copy import deepcopy

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.text import slugify

from ..config import (
    DEFAULT_HSE_STUDIES, DISCIPLINE_DEFAULT_DELIVERABLES,
    DISCIPLINE_NAME_BY_CODE,
)
from ..models import (
    DocumentIntelligenceRun, DocumentProfile, IntelligenceConflict, IntelligenceFact,
)
from .intelligence import analyze_project
from .deliverable_matching import find_deliverable_match
from .preview_confirmation import apply_confirmed_preview, source_fingerprint
from .register_rows import extract_legacy_register_rows, register_rows_for_file
from .extraction_coverage import file_coverage, summarize_coverage, summarize_assertions
from .planning_fact_extraction import explicit_labeled_assertions, validated_claim
from .intelligence_review_retention import retain_unchanged_reviews

ENGINE_VERSION = '6.2-applicability-registers'

_DOCUMENT_NUMBER_RE = re.compile(
    r'\b(?=[A-Z0-9./_~\-]{6,100}\b)(?=[A-Z0-9./_~\-]*\d)[A-Z0-9]{2,12}(?:[-/_.][A-Z0-9~]{1,25}){2,}\b',
    re.I,
)
_REVISION_RE = re.compile(r'\b(?:rev(?:ision)?[.: -]*)?(?P<revision>[A-Z]|\d{1,3})\b', re.I)

_CATEGORY_TERMS = {
    'sow': ('scope of work', 'scope of services', 'contractor shall'),
    'wbs': ('work breakdown structure', 'wbs code', 'wbs level'),
    'mdr': ('master document register', 'master deliverable register', 'mdr'),
    'eddr': ('engineering document deliverable register', 'eddr', 'document number'),
    'schedule_requirements': ('schedule requirements', 'baseline schedule', 'critical path', 'primavera'),
    'project_control_procedure': ('project control procedure', 'progress measurement', 'earned value'),
    'reference_schedule': ('activity id', 'predecessor', 'total float', 'early start'),
    'timeline': ('key milestone', 'milestone date', 'project timeline'),
}
_SCALAR_PATTERNS = [
    ('project_name', 'project_name', re.compile(r'(?:project title|project name)\s*[:\-]\s*([^\n|]{2,255})', re.I), 1, .94),
    ('effective_date', 'effective_date', re.compile(r'(?:effective date|zero date|contract award)\s*[:\-]?\s*(\d{1,2}[\-/][A-Za-z]{3,9}[\-/]\d{2,4}|\d{4}-\d{2}-\d{2})', re.I), 1, .92),
    ('duration_months', 'duration_months', re.compile(r'(?:\b(?:project|contract)\s+duration|(?m:^\s*duration))\s*(?:is|of|:|=)?\s*(\d{1,3})\s*[- ]?months?\b', re.I), 1, .86),
    ('client', 'client', re.compile(r'\b(?:client|company)(?:[ \t]*:[ \t]*|[ \t]+-[ \t]+)([^\n|]{2,255})', re.I), 1, .82),
    ('location', 'location', re.compile(r'(?:project location|site location|location)\s*[:\-]\s*([^\n|]{2,255})', re.I), 1, .82),
]
_CALENDAR_PATTERNS = [
    ('working_days_per_week', re.compile(r'(\d)\s*(?:working\s*)?days?\s*(?:per|/)\s*week', re.I)),
    ('hours_per_day', re.compile(r'(\d+(?:\.\d+)?)\s*(?:working\s*)?hours?\s*(?:per|/)\s*day', re.I)),
]
_REVIEW_RE = re.compile(r'(?P<label>[A-Za-z][A-Za-z /&-]{2,80}review[A-Za-z /&-]{0,40})[^\n]{0,30}?(?P<days>\d{1,3})\s*working days?', re.I)
_MILESTONE_RE = re.compile(r'(?P<label>[A-Za-z][A-Za-z0-9 /&()_-]{2,100}(?:milestone|award|kickoff|completion|handover))\s*[:\-]?\s*(?P<date>\d{4}-\d{2}-\d{2}|\d{1,2}[\-/][A-Za-z]{3,9}[\-/]\d{2,4})', re.I)
_EXCLUSION_RE = re.compile(r'(?P<text>[^\n.]{0,120}\b(?:out of scope|excluded|not required)\b[^\n.]{0,160})', re.I)
_REGISTER_ROW_RE = re.compile(
    r'^\s*(?P<item>\d{1,5})\s+(?P<discipline>[A-Z][A-Z &/()-]{1,60}?)\s+'
    r'(?P<number>(?=[A-Z0-9./_~\-]*\d)[A-Z0-9]{2,12}(?:[-/_.][A-Z0-9~]{1,25}){2,})\s+(?P<title_area>.+?)\s+'
    r'(?P<existing>NEW|EXISTING)\s+(?P<class>\d{1,3})\s+(?P<revision>[A-Z0-9]{1,8})(?:\s.*)?$',
    re.I,
)



def _normalize(value):
    return re.sub(r'\s+', ' ', str(value).strip()).casefold()[:500]


def _excerpt(text, start, end, radius=100):
    return re.sub(r'\s+', ' ', text[max(0, start - radius):min(len(text), end + radius)]).strip()[:1000]


def _locator(text, start, match=None):
    prefix = text[:start]
    line = prefix.count('\n') + 1
    sheet_matches = list(re.finditer(r'^--- Sheet: (.+?) ---$', prefix, re.M))
    locator = {'line': line, 'character_start': start}
    if sheet_matches:
        locator['sheet'] = sheet_matches[-1].group(1).strip()
    ocr_pages = list(re.finditer(r'^--- OCR Page:\s*(\d+)\s*---$', prefix, re.M))
    printed_pages = list(re.finditer(r'\bPage(?:\s+Page)?\s+(\d+)\s+of\s+\d+\b', prefix, re.I))
    page = prefix.count('\f') + 1
    if ocr_pages and '\f' not in text:
        page = int(ocr_pages[-1].group(1))
    if printed_pages:
        locator['printed_page'] = int(printed_pages[-1].group(1))
    if page > 1 or '\f' in text or ocr_pages or printed_pages:
        locator['page'] = page
    if match:
        locator['matched_term'] = match[:160]
    return locator


def _add_fact(rows, file_obj, fact_type, key, value, confidence, text, start, end, *, method='deterministic', matched=None):
    normalized = _normalize(value)
    # Different source occurrences are separate assertions, even if their
    # identifiers, wording and values coincide. Retry deduplication applies
    # only to the exact same occurrence in the same extracted text version.
    hashes = rows.setdefault('_source_text_hashes', {})
    source_key = (file_obj.pk if file_obj else None, text or '')
    if source_key not in hashes:
        hashes[source_key] = hashlib.sha256((text or '').encode('utf-8')).hexdigest()
    text_hash = hashes[source_key]
    identity = (file_obj.pk if file_obj else None, text_hash, fact_type, key, normalized, start, end)
    if identity in rows['_seen']:
        return
    rows['_seen'].add(identity)
    located = bool(text and type(start) is int and type(end) is int and 0 <= start < end <= len(text))
    locator = _locator(text, start, matched) if located else {'provider': method}
    if text and file_obj:
        locator['extracted_text_sha256'] = text_hash
    if located and file_obj:
        locator['character_end'] = end
    rows['facts'].append(IntelligenceFact(
        run=rows['run'], source_file=file_obj, fact_type=fact_type, key=key[:160], value=value,
        normalized_value=normalized, confidence=confidence, extraction_method=method,
        source_excerpt=_excerpt(text, start, end) if located else '',
        source_locator=locator,
    ))
    return rows['facts'][-1]


def profile_document(file_obj, *, extraction_coverage=None):
    """Classify a parsed file and persist extraction-quality metadata."""
    text = file_obj.extracted_text or ''
    lower = text.casefold()
    scores = {category: sum(lower.count(term) for term in terms) for category, terms in _CATEGORY_TERMS.items()}
    detected = max(scores, key=scores.get) if scores and max(scores.values()) else file_obj.category
    best = scores.get(detected, 0)
    total = sum(scores.values())
    confidence = .55 if not best else min(.99, .62 + (best / max(total, 1)) * .33)
    if detected == file_obj.category:
        confidence = min(.99, confidence + .08)
    flags = []
    words = len(re.findall(r'\b\w+\b', text))
    if words < 30:
        flags.append('low_text_volume')
    if text.endswith('...[truncated]'):
        flags.append('text_truncated')
    if '--- OCR Page:' in text:
        flags.append('ocr_extracted')
    if detected != file_obj.category:
        flags.append('category_mismatch')
    if extraction_coverage is None:
        existing = DocumentProfile.objects.filter(file=file_obj).values_list('extraction_coverage', flat=True).first()
        extraction_coverage = file_coverage(file_obj, existing, include_structured=True)
    if extraction_coverage.get('status') != 'complete':
        flags.append('extraction_coverage_incomplete')
    extension = os.path.splitext(file_obj.original_filename or '')[1].lower().lstrip('.')
    profile, _ = DocumentProfile.objects.update_or_create(file=file_obj, defaults={
        'declared_category': file_obj.category, 'detected_category': detected,
        'classification_confidence': round(confidence, 3), 'extension': extension,
        'mime_type': file_obj.content_type or '', 'language': 'und',
        'page_count': (extraction_coverage.get('units_total') if extraction_coverage.get('unit_type') == 'page' else (max(1, text.count('\f') + 1) if text else 0)), 'word_count': words,
        'checksum_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest() if text else '',
        'extraction_method': 'tesseract_ocr' if 'ocr_extracted' in flags else (extension or 'plain_text'),
        'quality_flags': flags,
        'extraction_coverage': extraction_coverage,
        'classified_at': timezone.now(), 'is_deleted': False, 'deleted_at': None,
    })
    return profile


def _register_discipline(value):
    normalized = re.sub(r'[^a-z0-9]+', ' ', value.casefold()).strip()
    mappings = (
        ('civil', 'civil'), ('structural', 'civil'), ('electrical', 'electrical'),
        ('hvac', 'mechanical'), ('mechanical', 'mechanical'), ('mep', 'mechanical'),
        ('instrument', 'instrumentation'), ('process', 'process'), ('general', 'general'),
        ('hse', 'hse'), ('pipeline', 'pipeline'), ('piping', 'piping'),
    )
    return next((code for term, code in mappings if term in normalized), slugify(normalized)[:64] or 'general')


def _extract_register_rows(rows, file_obj, text, *, register_mode=False):
    """Extract collapsed PDF/Excel register rows, preserving number/revision/title."""
    structured = register_rows_for_file(file_obj)
    if not structured and file_obj.category not in {'mdr', 'eddr'}:
        return
    if not structured and register_mode:
        structured = extract_legacy_register_rows(text)
    if structured:
        for index, row in enumerate(structured):
            _add_fact(
                rows, file_obj, 'deliverable', f"{row['discipline']}:register:{index + 1}",
                {
                    'source_register': True, 'discipline': row['discipline'],
                    'discipline_label': row['discipline_label'],
                    'name': row['original_title'], 'original_title': row['original_title'],
                    'document_number': row.get('document_number', ''),
                    'document_revision': row.get('document_revision', ''),
                    'register_item': row.get('register_item'),
                    'title_boundary_status': row.get('title_boundary_status', 'explicit_columns'),
                    'source_layout': row.get('source_layout', 'table_columns'),
                    **({'source_group': row['source_group']} if row.get('source_group') else {}),
                    **({key: row[key] for key in ('applicability_status', 'applicability_marks', 'source_remarks', 'package_columns_status')
                        if key in row}),
                }, .99, text, row['start'], row['end'], matched=row['original_title'],
            )
            locator = rows['facts'][-1].source_locator
            locator.update(row.get('source_locator') or {})
            if row.get('sheet'):
                locator['sheet'] = row['sheet']
            locator['register_item'] = row.get('register_item')
        return
    matches = []
    for line_match in re.finditer(r'^.*$', text, re.M):
        match = _REGISTER_ROW_RE.match(line_match.group(0))
        if match:
            matches.append((line_match, match))
    if not matches:
        return

    for line_match, match in matches:
        title = re.sub(r'\s+', ' ', match.group('title_area')).strip()
        discipline = _register_discipline(match.group('discipline'))
        number = match.group('number').strip()
        _add_fact(
            rows, file_obj, 'deliverable', f'{discipline}:{slugify(number)}',
            {
                'discipline': discipline, 'name': title, 'original_title': title,
                'title_boundary_status': 'ambiguous', 'source_layout': 'collapsed_register',
                'document_number': number, 'document_revision': match.group('revision').strip(),
                'register_item': int(match.group('item')),
            }, .97, text, line_match.start(), line_match.end(), matched=number,
        )


def _extract_file_facts(rows, file_obj, *, include_catalogue_deliverables=False):
    """Extract located assertions; legacy vocabulary scanning is explicit opt-in.

    The live document-driven entry point never opts in. A topic mention is not
    an obligation to prepare a catalogue deliverable or perform an HSE study.
    """
    text = file_obj.extracted_text or ''
    lower = text.casefold()
    _extract_register_rows(rows, file_obj, text, register_mode=not include_catalogue_deliverables)
    for fact_type, key, pattern, group, confidence in _SCALAR_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(group).strip(' :-|')
            if fact_type == 'duration_months':
                value = int(value)
            _add_fact(rows, file_obj, fact_type, key, value, confidence, text, match.start(), match.end(), matched=match.group(0))

    for code, name in (DISCIPLINE_NAME_BY_CODE if include_catalogue_deliverables else {}).items():
        terms = {code.replace('_', ' '), name.casefold()}
        found = next((term for term in terms if len(term) > 3 and term in lower), None)
        if found:
            start = lower.find(found)
            _add_fact(rows, file_obj, 'discipline', code, {'code': code, 'name': name}, .80, text, start, start + len(found), matched=found)

    catalogue = DISCIPLINE_DEFAULT_DELIVERABLES if include_catalogue_deliverables else {}
    for discipline, deliverables in catalogue.items():
        for deliverable in deliverables:
            match = find_deliverable_match(text, deliverable)
            if match:
                start, term = match.start(), match.group(0)
                line_start = text.rfind('\n', 0, start) + 1
                line_end = text.find('\n', start)
                line_end = len(text) if line_end < 0 else line_end
                source_line = text[line_start:line_end].strip()
                document_number_match = _DOCUMENT_NUMBER_RE.search(source_line)
                revision_match = re.search(r'\bRev(?:ision)?[.: -]*(?P<revision>[A-Z0-9]{1,8})\b', source_line, re.I)
                _add_fact(
                    rows, file_obj, 'deliverable', f'{discipline}:{slugify(deliverable)}',
                    {
                        'discipline': discipline, 'name': deliverable,
                        'original_title': re.sub(r'\s+', ' ', term).strip(),
                        'document_number': document_number_match.group(0) if document_number_match else '',
                        'document_revision': revision_match.group('revision') if revision_match else '',
                    }, .88, text, start,
                    match.end(), matched=term,
                )

    for study in DEFAULT_HSE_STUDIES if include_catalogue_deliverables else []:
        start = lower.find(study.casefold())
        if start >= 0:
            _add_fact(rows, file_obj, 'hse_study', slugify(study), study, .88, text, start, start + len(study), matched=study)

    for key, pattern in _CALENDAR_PATTERNS:
        for match in pattern.finditer(text):
            value = float(match.group(1)) if '.' in match.group(1) else int(match.group(1))
            _add_fact(rows, file_obj, 'calendar', key, value, .90, text, match.start(), match.end(), matched=match.group(0))
    for match in _REVIEW_RE.finditer(text):
        label = re.sub(r'\s+', ' ', match.group('label')).strip()
        _add_fact(rows, file_obj, 'review_cycle', slugify(label), {'name': label, 'working_days': int(match.group('days'))}, .84, text, match.start(), match.end(), matched=match.group(0))
    for match in _MILESTONE_RE.finditer(text):
        label = re.sub(r'\s+', ' ', match.group('label')).strip()
        _add_fact(rows, file_obj, 'milestone', slugify(label), {'name': label, 'date': match.group('date')}, .86, text, match.start(), match.end(), matched=match.group(0))
    for index, match in enumerate(_EXCLUSION_RE.finditer(text)):
        value = re.sub(r'\s+', ' ', match.group('text')).strip()
        _add_fact(rows, file_obj, 'exclusion', f'exclusion:{index + 1}', value, .82, text, match.start(), match.end(), matched=match.group(0))

    requirement_index = 0
    for match in re.finditer(r'^[^\n]*\b(?:shall|must|required to)\b[^\n]*$', text, re.I | re.M):
        value = re.sub(r'\s+', ' ', match.group(0)).strip()
        if len(value) < 20:
            continue
        requirement_index += 1
        _add_fact(rows, file_obj, 'requirement', f'{file_obj.pk}:{requirement_index}', value, .76, text, match.start(), match.end(), matched='requirement language')

    for assertion in explicit_labeled_assertions(text):
        fact = _add_fact(rows, file_obj, assertion['type'],
                         f"{assertion['type']}:{assertion['character_start']}", assertion['value'], .80,
                         text, assertion['character_start'], assertion['character_end'])
        if fact is not None:
            fact.source_locator.update(
                quote=assertion['quote'], assertion_classification='document_fact', executable=False,
            )


def _persist_ai_facts(rows, intelligence, files):
    for claim in intelligence.get('ai_evidence_facts') or []:
        fact_type, value = claim.get('type'), claim.get('value')
        source = next((item for item in files if str(item.pk) == str(claim.get('source_file_id'))), None)
        quote = claim.get('quote')
        start = claim.get('character_start')
        text = (source.extracted_text or '') if source else ''
        validated = validated_claim({**claim, 'quote_start': start},
                                    {'source_file_id': source.pk, 'character_start': 0, 'text': text}) if source and type(start) is int else None
        if validated is None:
            intelligence.setdefault('unverified_ai_claims', []).append({'field': fact_type, 'status': 'not_specified', 'reason': 'Missing or invalid source evidence'})
            continue
        key = fact_type if fact_type in {'project_name', 'effective_date', 'duration_months', 'client', 'location'} else f'{fact_type}:{start}'
        if fact_type == 'deliverable':
            discipline = slugify(claim.get('discipline') or 'not_specified').replace('-', '_')
            key = f'{discipline}:{slugify(str(value))}'
            value = {'name': value, 'original_title': value, 'discipline': discipline, 'source_verified_quote': True}
        fact = _add_fact(rows, source, fact_type, key, value, .65, text, start, start + len(quote), method='ai')
        if fact is not None:
            fact.source_locator.update(
                quote=quote, assertion_classification='document_fact', executable=False,
                assertion_schema=validated['schema_version'],
            )


def _persist_project_record_facts(rows, project):
    """Treat existing workspace values as evidence so document mismatches are visible."""
    values = {
        'project_name': project.name,
        'effective_date': project.effective_date.isoformat() if project.effective_date else None,
        'duration_months': float(project.duration_months) if project.duration_months is not None else None,
        'client': project.client or None,
        'location': project.location or None,
    }
    for fact_type, value in values.items():
        if value in (None, ''):
            continue
        _add_fact(rows, None, fact_type, fact_type, value, .99, '', 0, 0, method='deterministic')
        rows['facts'][-1].source_locator = {'source': 'project_record', 'project_id': project.id}
        rows['facts'][-1].source_excerpt = 'Current planning workspace value'


def _create_conflicts(run):
    scalar_types = {'project_name', 'effective_date', 'duration_months', 'client', 'location', 'calendar'}
    grouped = defaultdict(list)
    related = list(run.facts.filter(is_deleted=False, fact_type__in=scalar_types | {'discipline', 'exclusion'}))
    for fact in related:
        grouped[(fact.fact_type, fact.key)].append(fact)
    conflicts = []
    conflicted_ids = set()
    for (fact_type, key), facts in grouped.items():
        if fact_type not in scalar_types:
            continue
        values = {fact.normalized_value for fact in facts}
        if len(values) <= 1:
            continue
        ids = [fact.id for fact in facts]
        conflicted_ids.update(ids)
        conflicts.append(IntelligenceConflict(
            run=run, key=f'{fact_type}:{key}', fact_ids=ids,
            description=f'Conflicting {fact_type.replace("_", " ")} values were found across source evidence.',
        ))
    exclusions = [fact for fact in related if fact.fact_type == 'exclusion']
    for code, name in DISCIPLINE_NAME_BY_CODE.items():
        positive = grouped.get(('discipline', code), [])
        negative = [
            fact for fact in exclusions
            if code.replace('_', ' ') in fact.normalized_value or name.casefold() in fact.normalized_value
        ]
        if not positive or not negative:
            continue
        ids = [fact.id for fact in [*positive, *negative]]
        conflicted_ids.update(ids)
        conflicts.append(IntelligenceConflict(
            run=run, key=f'discipline:{code}', conflict_type='explicit_exclusion', fact_ids=ids,
            description=f'{name} is mentioned as scope but also appears in explicit exclusion language.',
        ))
    if conflicted_ids:
        run.facts.filter(id__in=conflicted_ids).update(status='conflicted')
    IntelligenceConflict.objects.bulk_create(conflicts)
    return conflicts


def compile_run_intelligence(run, *, include_confirmation=True):
    """Compile current reviewed facts over the legacy-compatible intelligence payload."""
    intelligence = deepcopy((run.summary or {}).get('base_intelligence') or {})
    scalar_output = {
        'project_name': 'detected_project_name', 'effective_date': 'detected_effective_date_text',
        'duration_months': 'detected_duration_months', 'client': 'detected_client', 'location': 'detected_location',
    }
    # Read only the values used by this projection. Large deliverable/requirement
    # payloads stay in the database; one aggregate supplies counts across all types.
    # Keep this snapshot local: later review edits must be visible on the next read.
    active_facts = run.facts.filter(is_deleted=False)
    facts = list(active_facts.filter(fact_type__in=set(scalar_output) | {'discipline', 'exclusion'})
        .exclude(status__in=['rejected', 'superseded', 'conflicted']).only(
            'id', 'run_id', 'fact_type', 'key', 'value', 'status', 'confidence', 'normalized_value',
        ).order_by('-status', '-confidence', 'id'))
    counts = active_facts.aggregate(
        confirmed_count=Count('id', filter=Q(status='confirmed')),
        rejected_count=Count('id', filter=Q(status='rejected')),
    )
    conflicts = list(run.conflicts.filter(is_deleted=False, status__in=['open', 'ignored']).values(
        'id', 'key', 'description', 'status',
    ))
    unresolved_conflict_keys = {item['key'] for item in conflicts}
    for fact_type, output_key in scalar_output.items():
        if any(key.startswith(f'{fact_type}:') for key in unresolved_conflict_keys):
            intelligence[output_key] = None
            continue
        candidates = [fact for fact in facts if fact.fact_type == fact_type]
        confirmed = [fact for fact in candidates if fact.status == 'confirmed']
        choice = confirmed[0] if confirmed else (candidates[0] if candidates else None)
        if choice:
            intelligence[output_key] = choice.value
        else:
            intelligence[output_key] = None
    disciplines = intelligence.get('disciplines') or {}
    confirmed_disciplines = [fact for fact in facts if fact.fact_type == 'discipline' and fact.status == 'confirmed']
    for fact in confirmed_disciplines:
        if fact.key in disciplines:
            disciplines[fact.key]['in_scope'] = True
    for fact in (fact for fact in facts if fact.fact_type == 'exclusion' and fact.status == 'confirmed'):
        for code, name in DISCIPLINE_NAME_BY_CODE.items():
            if code in disciplines and (
                code.replace('_', ' ') in fact.normalized_value or name.casefold() in fact.normalized_value
            ):
                disciplines[code]['in_scope'] = False
    intelligence.update({
        'document_intelligence_run_id': run.id,
        'evidence_summary': {
            'fact_count': run.fact_count,
            'conflict_count': sum(item['status'] == 'open' for item in conflicts),
            **counts,
        },
        'open_conflicts': [{key: item[key] for key in ('id', 'key', 'description')}
                           for item in conflicts if item['status'] == 'open'],
        'processing_coverage': (run.summary or {}).get('processing_coverage') or {},
        'extraction_summary': (run.summary or {}).get('extraction_summary') or {},
    })
    return apply_confirmed_preview(run, intelligence) if include_confirmation else intelligence


from apps.rbac.ai_telemetry import tracked_planning


def _extraction_source_manifest(files):
    return [{
        'file_id': source.pk, 'category': source.category, 'parse_status': source.parse_status,
        'extracted_text_sha256': hashlib.sha256((source.extracted_text or '').encode('utf-8')).hexdigest(),
    } for source in sorted(files, key=lambda item: item.pk)]


class ResumeSourceChanged(ValueError):
    """Safe, actionable message when queued extraction no longer matches inputs."""


def validate_analysis_source(project, run, files=None):
    """Validate unchanged inputs independently of the extraction engine version."""
    active = list(project.files.filter(is_deleted=False))
    if not active or any(source.parse_status != 'done' for source in active):
        raise ResumeSourceChanged('Finish parsing all current source documents and start a new analysis before continuing.')
    selected = list(files) if files is not None else active
    manifest = _extraction_source_manifest(selected)
    if (run.project_id != project.pk or run.is_deleted or
            (run.summary or {}).get('source_fingerprint') != source_fingerprint(project) or
            (run.summary or {}).get('extraction_source_manifest') != manifest or
            _extraction_source_manifest(active) != manifest or
            run.source_file_ids != sorted(source.pk for source in selected)):
        raise ResumeSourceChanged('The source set changed. Start a new document analysis instead of resuming this run.')
    return selected


def validate_resume_source(project, run, files=None):
    """Resume requires both unchanged inputs and compatible extraction code.

    The enqueue endpoint and worker repeat this read-only preflight. Viewing
    saved evidence uses source validation alone and does not resume extraction.
    """
    selected = validate_analysis_source(project, run, files)
    if run.engine_version != ENGINE_VERSION:
        raise ResumeSourceChanged('The analysis engine changed. Start a new document analysis instead of resuming this run.')
    return selected


@tracked_planning('document_intelligence')
def run_document_intelligence(project, *, user=None, files=None, allow_ai=True, resume_run=None, progress_callback=None):
    files = list(files if files is not None else project.files.filter(is_deleted=False, parse_status='done'))
    if not files:
        raise ValueError('No successfully parsed files are available.')
    if resume_run is not None:
        files = validate_resume_source(project, resume_run, files)
    from .register_geometry_cache import ensure_register_geometry
    for file_obj in files:
        ensure_register_geometry(file_obj)
    fingerprint = source_fingerprint(project)
    extraction_manifest = _extraction_source_manifest(files)
    resume_state = None
    if resume_run is not None:
        resume_state = (resume_run.summary or {}).get('ai_checkpoint')
    run = DocumentIntelligenceRun.objects.create(
        project=project, status='running', engine_version=ENGINE_VERSION,
        source_file_ids=sorted(file_obj.id for file_obj in files), started_at=timezone.now(), requested_by=user,
        summary={'source_fingerprint': fingerprint, 'extraction_source_manifest': extraction_manifest,
                 **({'resumed_from_run_id': resume_run.pk} if resume_run else {})},
    )
    try:
        # Provider work may span several chunks. Do not hold a database
        # transaction open while waiting for optional external analysis.
        def checkpoint(state):
            run.summary = {**run.summary, 'ai_checkpoint': state}
            run.save(update_fields=['summary', 'updated_at'])

        legacy = analyze_project(files, project=project, user=user, allow_ai=allow_ai,
                                 resume_state=resume_state, checkpoint_callback=checkpoint,
                                 resume_coverage=((resume_run.summary or {}).get('processing_coverage') or {}).get('ai_processing') if resume_run else None,
                                 progress_callback=progress_callback)
        current_files = project.files.filter(pk__in=[item.pk for item in files], is_deleted=False)
        if source_fingerprint(project) != fingerprint or _extraction_source_manifest(current_files) != extraction_manifest:
            raise ValueError('Source documents changed during analysis. Start a new analysis to use the current sources.')
        if progress_callback:
            progress_callback({'phase': 'persistence', 'file_count': len(files)})
        with transaction.atomic():
            for file_obj in files:
                profile_document(file_obj)
            rows = {'run': run, 'facts': [], '_seen': set()}
            for file_obj in files:
                _extract_file_facts(rows, file_obj, include_catalogue_deliverables=False)
            _persist_project_record_facts(rows, project)
            _persist_ai_facts(rows, legacy, files)
            IntelligenceFact.objects.bulk_create(rows['facts'])
            conflicts = _create_conflicts(run)
            run.fact_count = len(rows['facts'])
            run.conflict_count = len(conflicts)
            run.status = 'succeeded'
            run.finished_at = timezone.now()
            coverage = summarize_coverage(project.files.filter(is_deleted=False).select_related('document_profile'), analyzed_file_ids=[item.pk for item in files])
            ai_checkpoint = legacy.pop('ai_checkpoint', None)
            if ai_checkpoint is not None:
                run.summary['ai_checkpoint'] = ai_checkpoint
            coverage['ai_processing'] = deepcopy(legacy.get('ai_processing_coverage') or {})
            summary = summarize_assertions(rows['facts'], coverage, coverage['ai_processing'])
            run.summary = {**run.summary, 'base_intelligence': legacy, 'processing_coverage': coverage,
                           'extraction_summary': summary}
            retain_unchanged_reviews(run, actor=user)
            run.save(update_fields=['fact_count', 'conflict_count', 'status', 'finished_at', 'summary', 'updated_at'])
        return run, compile_run_intelligence(run)
    except Exception as exc:
        run.status = 'failed'
        run.error_message = str(exc)
        run.finished_at = timezone.now()
        run.save(update_fields=['status', 'error_message', 'finished_at', 'updated_at'])
        raise


def get_or_run_document_intelligence(project, *, user=None, force=False, resume_run=None, progress_callback=None):
    files = list(project.files.filter(is_deleted=False, parse_status='done'))
    ids = sorted(file_obj.id for file_obj in files)
    if resume_run is not None:
        return run_document_intelligence(project, user=user, files=files, resume_run=resume_run, progress_callback=progress_callback)
    if not force:
        existing = project.intelligence_runs.filter(
            is_deleted=False, status='succeeded', engine_version=ENGINE_VERSION, source_file_ids=ids,
        ).first()
        if existing and (existing.summary or {}).get('source_fingerprint') == source_fingerprint(project):
            return existing, compile_run_intelligence(existing)
    return run_document_intelligence(project, user=user, files=files, progress_callback=progress_callback)
