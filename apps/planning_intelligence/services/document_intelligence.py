"""Provenance-first document classification and engineering fact extraction."""
from __future__ import annotations

import hashlib
import os
import re
from collections import defaultdict

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from ..config import (
    DEFAULT_HSE_STUDIES, DELIVERABLE_ALIASES, DISCIPLINE_DEFAULT_DELIVERABLES,
    DISCIPLINE_NAME_BY_CODE,
)
from ..models import (
    DocumentIntelligenceRun, DocumentProfile, IntelligenceConflict, IntelligenceFact,
)
from .intelligence import analyze_project

ENGINE_VERSION = '3.0'

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
    ('duration_months', 'duration_months', re.compile(r'(\d{1,3})\s*[- ]?months?\b', re.I), 1, .86),
    ('client', 'client', re.compile(r'(?:client|company)\s*[:\-]\s*([^\n|]{2,255})', re.I), 1, .82),
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
    if ocr_pages:
        page = int(ocr_pages[-1].group(1))
    elif printed_pages:
        page = int(printed_pages[-1].group(1))
    else:
        page = prefix.count('\f') + 1
    if page > 1 or '\f' in text or ocr_pages or printed_pages:
        locator['page'] = page
    if match:
        locator['matched_term'] = match[:160]
    return locator


def _add_fact(rows, file_obj, fact_type, key, value, confidence, text, start, end, *, method='deterministic', matched=None):
    normalized = _normalize(value)
    identity = (file_obj.pk if file_obj else None, fact_type, key, normalized)
    if identity in rows['_seen']:
        return
    rows['_seen'].add(identity)
    rows['facts'].append(IntelligenceFact(
        run=rows['run'], source_file=file_obj, fact_type=fact_type, key=key[:160], value=value,
        normalized_value=normalized, confidence=confidence, extraction_method=method,
        source_excerpt=_excerpt(text, start, end) if text else '',
        source_locator=_locator(text, start, matched) if text else {'provider': method},
    ))


def profile_document(file_obj):
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
    extension = os.path.splitext(file_obj.original_filename or '')[1].lower().lstrip('.')
    profile, _ = DocumentProfile.objects.update_or_create(file=file_obj, defaults={
        'declared_category': file_obj.category, 'detected_category': detected,
        'classification_confidence': round(confidence, 3), 'extension': extension,
        'mime_type': file_obj.content_type or '', 'language': 'en',
        'page_count': max(1, text.count('\f') + 1) if text else 0, 'word_count': words,
        'checksum_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest() if text else '',
        'extraction_method': 'tesseract_ocr' if 'ocr_extracted' in flags else (extension or 'plain_text'),
        'quality_flags': flags,
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


def _extract_register_rows(rows, file_obj, text):
    """Extract collapsed PDF/Excel register rows, preserving number/revision/title."""
    if file_obj.category not in {'mdr', 'eddr'}:
        return
    matches = []
    for line_match in re.finditer(r'^.*$', text, re.M):
        match = _REGISTER_ROW_RE.match(line_match.group(0))
        if match:
            matches.append((line_match, match))
    if not matches:
        return

    # PDF table extraction commonly appends a repeated AREA column to TITLE.
    # Detect that repeated suffix from the register itself instead of using a
    # project/location-specific hardcode.
    suffix_counts = defaultdict(int)
    for _line, match in matches:
        tokens = match.group('title_area').split()
        for size in range(1, min(5, len(tokens))):
            suffix_counts[' '.join(tokens[-size:]).casefold()] += 1
    threshold = max(2, int(len(matches) * .5))
    repeated_suffixes = [
        suffix for suffix, count in suffix_counts.items()
        if count >= threshold and len(suffix.split()) >= 2
    ]
    area_suffix = max(repeated_suffixes, key=lambda value: len(value.split()), default='')

    for line_match, match in matches:
        title = re.sub(r'\s+', ' ', match.group('title_area')).strip()
        if area_suffix and title.casefold().endswith(' ' + area_suffix):
            title = title[:-(len(area_suffix) + 1)].strip()
        discipline = _register_discipline(match.group('discipline'))
        number = match.group('number').strip()
        _add_fact(
            rows, file_obj, 'deliverable', f'{discipline}:{slugify(number)}',
            {
                'discipline': discipline, 'name': title, 'original_title': title,
                'document_number': number, 'document_revision': match.group('revision').strip(),
                'register_item': int(match.group('item')),
            }, .97, text, line_match.start(), line_match.end(), matched=number,
        )


def _extract_file_facts(rows, file_obj):
    text = file_obj.extracted_text or ''
    lower = text.casefold()
    _extract_register_rows(rows, file_obj, text)
    for fact_type, key, pattern, group, confidence in _SCALAR_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(group).strip(' :-|')
            if fact_type == 'duration_months':
                value = int(value)
            _add_fact(rows, file_obj, fact_type, key, value, confidence, text, match.start(), match.end(), matched=match.group(0))

    for code, name in DISCIPLINE_NAME_BY_CODE.items():
        terms = {code.replace('_', ' '), name.casefold()}
        found = next((term for term in terms if len(term) > 3 and term in lower), None)
        if found:
            start = lower.find(found)
            _add_fact(rows, file_obj, 'discipline', code, {'code': code, 'name': name}, .80, text, start, start + len(found), matched=found)

    for discipline, deliverables in DISCIPLINE_DEFAULT_DELIVERABLES.items():
        for deliverable in deliverables:
            terms = [deliverable, *(DELIVERABLE_ALIASES.get(deliverable) or [])]
            hits = [(lower.find(term.casefold()), term) for term in terms if lower.find(term.casefold()) >= 0]
            if hits:
                start, term = min(hits)
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
                        'original_title': deliverable,
                        'document_number': document_number_match.group(0) if document_number_match else '',
                        'document_revision': revision_match.group('revision') if revision_match else '',
                    }, .88, text, start,
                    start + len(term), matched=term,
                )

    for study in DEFAULT_HSE_STUDIES:
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
    for match in re.finditer(r'[^\n.]{0,180}\b(?:shall|must|required to)\b[^\n.]{1,300}[.]?', text, re.I):
        value = re.sub(r'\s+', ' ', match.group(0)).strip()
        if len(value) < 20:
            continue
        requirement_index += 1
        _add_fact(rows, file_obj, 'requirement', f'{file_obj.pk}:{requirement_index}', value, .76, text, match.start(), match.end(), matched='requirement language')
        if requirement_index >= 100:
            break


def _persist_ai_facts(rows, intelligence):
    review = intelligence.get('ai_review') or {}
    mapping = {'project_name': 'project_name', 'effective_date': 'effective_date', 'duration_months': 'duration_months'}
    for source_key, fact_type in mapping.items():
        value = review.get(source_key)
        if value not in (None, ''):
            _add_fact(rows, None, fact_type, fact_type, value, .65, '', 0, 0, method='ai')


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
    for fact in run.facts.filter(is_deleted=False, fact_type__in=scalar_types):
        grouped[(fact.fact_type, fact.key)].append(fact)
    conflicts = []
    for (fact_type, key), facts in grouped.items():
        values = {fact.normalized_value for fact in facts}
        if len(values) <= 1:
            continue
        ids = [fact.id for fact in facts]
        run.facts.filter(id__in=ids).update(status='conflicted')
        conflicts.append(IntelligenceConflict(
            run=run, key=f'{fact_type}:{key}', fact_ids=ids,
            description=f'Conflicting {fact_type.replace("_", " ")} values were found across source evidence.',
        ))
    exclusions = list(run.facts.filter(is_deleted=False, fact_type='exclusion'))
    for code, name in DISCIPLINE_NAME_BY_CODE.items():
        positive = list(run.facts.filter(is_deleted=False, fact_type='discipline', key=code))
        negative = [
            fact for fact in exclusions
            if code.replace('_', ' ') in fact.normalized_value or name.casefold() in fact.normalized_value
        ]
        if not positive or not negative:
            continue
        ids = [fact.id for fact in [*positive, *negative]]
        run.facts.filter(id__in=ids).update(status='conflicted')
        conflicts.append(IntelligenceConflict(
            run=run, key=f'discipline:{code}', conflict_type='explicit_exclusion', fact_ids=ids,
            description=f'{name} is mentioned as scope but also appears in explicit exclusion language.',
        ))
    IntelligenceConflict.objects.bulk_create(conflicts)
    return conflicts


def compile_run_intelligence(run):
    """Compile current reviewed facts over the legacy-compatible intelligence payload."""
    intelligence = dict((run.summary or {}).get('base_intelligence') or {})
    facts = run.facts.filter(is_deleted=False).exclude(status__in=['rejected', 'superseded', 'conflicted'])
    scalar_output = {
        'project_name': 'detected_project_name', 'effective_date': 'detected_effective_date_text',
        'duration_months': 'detected_duration_months', 'client': 'detected_client', 'location': 'detected_location',
    }
    unresolved_conflict_keys = set(
        run.conflicts.filter(is_deleted=False, status__in=['open', 'ignored']).values_list('key', flat=True)
    )
    for fact_type, output_key in scalar_output.items():
        if any(key.startswith(f'{fact_type}:') for key in unresolved_conflict_keys):
            intelligence[output_key] = None
            continue
        candidates = list(facts.filter(fact_type=fact_type).order_by('-status', '-confidence', 'id'))
        confirmed = [fact for fact in candidates if fact.status == 'confirmed']
        choice = confirmed[0] if confirmed else (candidates[0] if candidates else None)
        if choice:
            intelligence[output_key] = choice.value
    disciplines = intelligence.get('disciplines') or {}
    confirmed_disciplines = facts.filter(fact_type='discipline', status='confirmed')
    for fact in confirmed_disciplines:
        if fact.key in disciplines:
            disciplines[fact.key]['in_scope'] = True
    for fact in facts.filter(fact_type='exclusion', status='confirmed'):
        for code, name in DISCIPLINE_NAME_BY_CODE.items():
            if code in disciplines and (
                code.replace('_', ' ') in fact.normalized_value or name.casefold() in fact.normalized_value
            ):
                disciplines[code]['in_scope'] = False
    intelligence.update({
        'document_intelligence_run_id': run.id,
        'evidence_summary': {
            'fact_count': run.fact_count,
            'conflict_count': run.conflicts.filter(is_deleted=False, status='open').count(),
            'confirmed_count': run.facts.filter(is_deleted=False, status='confirmed').count(),
            'rejected_count': run.facts.filter(is_deleted=False, status='rejected').count(),
        },
        'open_conflicts': list(run.conflicts.filter(is_deleted=False, status='open').values('id', 'key', 'description')),
    })
    return intelligence


def run_document_intelligence(project, *, user=None, files=None):
    files = list(files if files is not None else project.files.filter(is_deleted=False, parse_status='done'))
    if not files:
        raise ValueError('No successfully parsed files are available.')
    run = DocumentIntelligenceRun.objects.create(
        project=project, status='running', engine_version=ENGINE_VERSION,
        source_file_ids=sorted(file_obj.id for file_obj in files), started_at=timezone.now(), requested_by=user,
    )
    try:
        with transaction.atomic():
            for file_obj in files:
                profile_document(file_obj)
            legacy = analyze_project(files, project=project, user=user)
            rows = {'run': run, 'facts': [], '_seen': set()}
            for file_obj in files:
                _extract_file_facts(rows, file_obj)
            _persist_project_record_facts(rows, project)
            _persist_ai_facts(rows, legacy)
            IntelligenceFact.objects.bulk_create(rows['facts'])
            conflicts = _create_conflicts(run)
            run.fact_count = len(rows['facts'])
            run.conflict_count = len(conflicts)
            run.status = 'succeeded'
            run.finished_at = timezone.now()
            run.summary = {'base_intelligence': legacy}
            run.save(update_fields=['fact_count', 'conflict_count', 'status', 'finished_at', 'summary', 'updated_at'])
        return run, compile_run_intelligence(run)
    except Exception as exc:
        run.status = 'failed'
        run.error_message = str(exc)
        run.finished_at = timezone.now()
        run.save(update_fields=['status', 'error_message', 'finished_at', 'updated_at'])
        raise


def get_or_run_document_intelligence(project, *, user=None, force=False):
    files = list(project.files.filter(is_deleted=False, parse_status='done'))
    ids = sorted(file_obj.id for file_obj in files)
    if not force:
        existing = project.intelligence_runs.filter(
            is_deleted=False, status='succeeded', engine_version=ENGINE_VERSION, source_file_ids=ids,
        ).first()
        if existing:
            return existing, compile_run_intelligence(existing)
    return run_document_intelligence(project, user=user, files=files)
