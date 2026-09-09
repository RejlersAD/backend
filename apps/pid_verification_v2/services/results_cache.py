"""
P&ID Verification V2 Analysis Results Cache
=============================================
Stores a JSON snapshot of a document's analysis results — one blob per
document at ``analysis_cache_v2/<document_id>/results.json`` — via
Django's storage abstraction (apps.core.storage_backends.
PIDVerificationV2AnalysisCacheStorage), so this transparently uses S3 in
any environment with USE_S3=True and local disk otherwise, exactly like
every other storage-backed feature in this codebase. No raw boto3 calls
needed.

Own independent module, not shared with apps.pid_verification's own
results_cache.py — same isolation convention this codebase already uses
throughout (e.g. instrument_io_workflow never imports pid_checker_v2's
services at runtime), even though the two modules' logic is intentionally
near-identical.

Purpose: a fast-path for VIEWING an already-completed document — one
S3/disk read instead of the doc.drawings.all() DB query (plus both
backfill passes) get_results() would otherwise run. It is NEVER consulted
before running an analysis: "Start AI Analysis" (upload_pid, on a hash
match) and "Re-analyze" (reprocess_document) must always run a genuinely
fresh analysis, no exceptions. Both only ever WRITE to this cache,
unconditionally, right after an analysis finishes (see tasks.py) —
file_overwrite=True naturally retires whatever the previous entry held.

The database (PIDVDrawing / PIDVFinding) remains the source of truth. A
cache miss, a corrupt entry, or a stale entry (file_hash no longer
matching the document's current state) always falls back to the normal
DB-backed response, never an error.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

CACHE_FILE_NAME = 'results.json'

# Simple severity-weighted score for the cache snapshot only — a summary
# figure alongside the cached findings, not wired into the main UI/serializer.
_SEVERITY_PENALTY = {'critical': 10, 'major': 5, 'minor': 2, 'info': 1}


def _cache_path(document_id) -> str:
    return f'{document_id}/{CACHE_FILE_NAME}'


def _quality_score(findings: list[dict]) -> int:
    penalty = sum(_SEVERITY_PENALTY.get(f.get('severity', ''), 1) for f in findings)
    return max(0, 100 - penalty)


def build_cache_payload(doc, file_hash: str) -> dict:
    """Assemble the cache JSON from a just-completed document's current DB
    state, reusing the same serializer the /results endpoint returns (so
    the cached shape never drifts from what's actually stored).

    'full_data' holds that serializer output verbatim — get_results()'s
    cache-fast-path returns it directly as the response body, so a cache
    hit is byte-for-byte what a fresh DB-backed call would have produced
    at the moment this was written. The other top-level keys (findings,
    line_tags, equipment, instruments, quality_score) are kept alongside
    it as a summary, matching apps.pid_verification's own cache shape.
    """
    from ..serializers import PIDVDocumentSerializer

    data = PIDVDocumentSerializer(doc).data
    drawings = data.get('drawings') or []

    findings = [
        {**issue, 'drawing_id': drawing.get('drawing_id')}
        for drawing in drawings
        for issue in (drawing.get('issues') or [])
    ]
    line_tags = [
        tag
        for drawing in drawings
        for tag in (drawing.get('metadata') or {}).get('line_tags', [])
    ]
    equipment = [
        item
        for drawing in drawings
        for item in (drawing.get('metadata') or {}).get('equipment_tags', [])
    ]
    instruments = [
        item
        for drawing in drawings
        for item in (drawing.get('metadata') or {}).get('instrument_tags', [])
    ]

    return {
        'document_id': str(doc.document_id),
        'file_hash': file_hash,
        'analysis_timestamp': datetime.now(timezone.utc).isoformat(),
        'findings': findings,
        'line_tags': line_tags,
        'equipment': equipment,
        'instruments': instruments,
        'quality_score': _quality_score(findings),
        'full_data': data,
    }


def save_results_cache(doc, file_hash: str) -> dict:
    """Snapshot a just-completed document's results to the cache.
    file_overwrite=True on PIDVerificationV2AnalysisCacheStorage means
    this always replaces any previous cache for this document_id. Returns
    the payload that was written (callers don't need to re-read it back)."""
    from apps.core.storage_backends import PIDVerificationV2AnalysisCacheStorage
    from django.core.files.base import ContentFile

    payload = build_cache_payload(doc, file_hash)
    content = json.dumps(payload, default=str).encode('utf-8')

    storage = PIDVerificationV2AnalysisCacheStorage()
    storage.save(_cache_path(doc.document_id), ContentFile(content))
    logger.info(
        '[PIDVV2ResultsCache] Saved cache for document_id=%s (%d findings, hash=%s)',
        doc.document_id, len(payload['findings']), file_hash[:12],
    )
    return payload


def load_results_cache(doc) -> dict | None:
    """Return the cached payload dict, or None if no cache exists or it
    can't be read (corrupt/missing — treated as a cache miss, never an error)."""
    from apps.core.storage_backends import PIDVerificationV2AnalysisCacheStorage

    storage = PIDVerificationV2AnalysisCacheStorage()
    path = _cache_path(doc.document_id)
    if not storage.exists(path):
        return None
    try:
        with storage.open(path, 'rb') as f:
            return json.loads(f.read().decode('utf-8'))
    except Exception:
        logger.exception('[PIDVV2ResultsCache] Failed to read cache for document_id=%s', doc.document_id)
        return None


def clear_results_cache(doc) -> None:
    """Delete the cached snapshot for a document, if any."""
    from apps.core.storage_backends import PIDVerificationV2AnalysisCacheStorage

    storage = PIDVerificationV2AnalysisCacheStorage()
    path = _cache_path(doc.document_id)
    if storage.exists(path):
        storage.delete(path)
        logger.info('[PIDVV2ResultsCache] Cleared cache for document_id=%s', doc.document_id)
