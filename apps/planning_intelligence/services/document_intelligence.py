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

ENGINE_VERSION = '2.0'

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
    page = prefix.count('\f') + 1
    if page > 1 or '\f' in text:
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


def _extract_file_facts(rows, file_obj):
    text = file_obj.extracted_text or ''
    lower = text.casefold()
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
                _add_fact(
                    rows, file_obj, 'deliverable', f'{discipline}:{slugify(deliverable)}',
                    {'discipline': discipline, 'name': deliverable}, .88, text, start,
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
