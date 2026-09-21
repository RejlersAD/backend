"""Machine-readable extraction coverage, separate from interpretation confidence."""
from __future__ import annotations

from copy import deepcopy
import hashlib
from collections import Counter


def new_coverage(unit_type='document'):
    return {
        'version': 1, 'status': 'unknown', 'unit_type': unit_type,
        'units_total': 0, 'units_processed': 0, 'units_skipped': 0,
        'units_failed': 0, 'units_empty': 0, 'units_unsupported': 0,
        'characters_extracted': 0, 'characters_retained': 0,
        'text_truncated': False, 'semantic_coverage_verified': False,
        'issues': [], 'units': [],
    }


def record_unit(coverage, locator, text='', *, status='processed', method=None):
    if coverage is None:
        return
    if status == 'processed' and not text.strip():
        status = 'empty'
    coverage['units_total'] += 1
    key = 'units_' + status
    coverage[key] = coverage.get(key, 0) + 1
    coverage['units'].append({**locator, 'status': status, 'characters': len(text), **({'method': method} if method else {})})


def record_issue(coverage, code, message, **locator):
    if coverage is not None:
        coverage['issues'].append({'code': code, 'message': message, **locator})


def finish_coverage(coverage, raw_text, retained_text):
    coverage['characters_extracted'] = len(raw_text)
    coverage['characters_retained'] = len(retained_text)
    coverage['text_sha256'] = hashlib.sha256(retained_text.encode('utf-8')).hexdigest()
    incomplete = coverage['text_truncated'] or any(coverage.get('units_' + state) for state in ('skipped', 'failed', 'empty', 'unsupported')) or bool(coverage['issues'])
    if coverage.get('status') == 'unsupported':
        pass
    elif not retained_text:
        coverage['status'] = 'failed'
    else:
        coverage['status'] = 'partial' if incomplete else 'complete'
    return coverage


def file_coverage(file_obj, recorded=None):
    """Old stored text cannot prove that every source page/sheet was extracted."""
    text = file_obj.extracted_text or ''
    digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
    if recorded and recorded.get('text_sha256') == digest:
        result = deepcopy(recorded)
    else:
        result = new_coverage()
        result.update({
            'status': 'unknown' if file_obj.parse_status == 'done' else file_obj.parse_status,
            'characters_retained': len(text), 'text_sha256': digest,
            'text_truncated': '[truncated]' in text,
        })
        record_issue(result, 'coverage_not_recorded', 'Extraction coverage has not been recorded for this source. Reprocess it to verify page and sheet coverage.')
        if result['text_truncated']:
            result['status'] = 'partial'
            record_issue(result, 'text_truncated', 'Only part of the source text was retained.')
    return {**result, 'file_id': file_obj.pk, 'filename': file_obj.original_filename, 'parse_status': file_obj.parse_status}


def summarize_coverage(files, *, analyzed_file_ids):
    rows = []
    analyzed = set(analyzed_file_ids)
    for file_obj in files:
        profile = getattr(file_obj, 'document_profile', None)
        row = file_coverage(file_obj, getattr(profile, 'extraction_coverage', None))
        row['included_in_analysis'] = file_obj.pk in analyzed
        if not row['included_in_analysis']:
            record_issue(row, 'not_analyzed', 'This active upload was not included in this analysis run.')
        rows.append(row)
    complete = bool(rows) and all(row['status'] == 'complete' and row['included_in_analysis'] for row in rows)
    return {
        'status': 'complete' if complete else 'partial',
        'file_count': len(rows), 'analyzed_file_count': sum(row['included_in_analysis'] for row in rows),
        'complete_file_count': sum(row['status'] == 'complete' and row['included_in_analysis'] for row in rows),
        'semantic_coverage_verified': False,
        'interpretation_status': 'requires_review',
        'files': rows,
    }


def summarize_assertions(facts, processing_coverage, ai_coverage):
    """A successful extraction pass is not complete document understanding."""
    counts = Counter(fact.fact_type for fact in facts if fact.source_file_id is not None)
    counts_by_method = {}
    for fact in facts:
        if fact.source_file_id is not None:
            method = counts_by_method.setdefault(fact.extraction_method, Counter())
            method[fact.fact_type] += 1
    text_complete = processing_coverage.get('status') == 'complete'
    ai_complete = ai_coverage.get('status') == 'complete'
    return {
        'status': 'processed' if text_complete and ai_complete else 'partial',
        'fact_count': sum(counts.values()), 'facts_by_type': dict(sorted(counts.items())),
        'facts_by_method': {key: dict(sorted(value.items())) for key, value in counts_by_method.items()},
        'text_coverage_status': processing_coverage.get('status', 'unknown'),
        'ai_coverage_status': ai_coverage.get('status', 'not_run'),
        'chunks_remaining': ai_coverage.get('chunks_remaining', 0),
        'resume_available': bool(ai_coverage.get('resume_available')),
        'semantic_coverage_verified': False, 'interpretation_status': 'requires_review',
    }
