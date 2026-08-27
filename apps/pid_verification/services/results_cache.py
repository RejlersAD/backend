"""
P&ID Analysis Results Cache
============================
Stores a JSON snapshot of a document's analysis results — one blob per
document at ``analysis_cache/<document_id>/results.json`` — via Django's
storage abstraction (apps.core.storage_backends.PIDAnalysisCacheStorage),
so this transparently uses S3 in any environment with USE_S3=True and
local disk otherwise, exactly like every other storage-backed feature in
this codebase. No raw boto3 calls needed.

Purpose: skip re-running the (expensive) analysis pipeline when the
underlying file is unchanged. The database (PIDVDrawing / PIDVFinding)
remains the source of truth for VIEWING already-completed results — this
cache is only consulted at reprocess time to decide whether the pipeline
needs to run at all. See reprocess_document() in views.py.
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
    the cached shape never drifts from what's actually stored)."""
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
    # This app doesn't currently extract equipment/instrument tags as a
    # separate concept (only line_tags live in drawing.metadata) — kept as
    # empty lists so the cache shape matches the spec; populated from
    # drawing.metadata automatically if a future extractor adds those keys.
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
    }


def save_results_cache(doc, file_hash: str) -> dict:
    """Snapshot a just-completed document's results to the cache.
    file_overwrite=True on PIDAnalysisCacheStorage means this always
    replaces any previous cache for this document_id. Returns the payload
    that was written (callers don't need to re-read it back)."""
    from apps.core.storage_backends import PIDAnalysisCacheStorage
    from django.core.files.base import ContentFile

    payload = build_cache_payload(doc, file_hash)
    content = json.dumps(payload, default=str).encode('utf-8')

    storage = PIDAnalysisCacheStorage()
    storage.save(_cache_path(doc.document_id), ContentFile(content))
    logger.info(
        '[PIDVResultsCache] Saved cache for document_id=%s (%d findings, hash=%s)',
        doc.document_id, len(payload['findings']), file_hash[:12],
    )
    return payload


def load_results_cache(doc) -> dict | None:
    """Return the cached payload dict, or None if no cache exists or it
    can't be read (corrupt/missing — treated as a cache miss, never an error)."""
    from apps.core.storage_backends import PIDAnalysisCacheStorage

    storage = PIDAnalysisCacheStorage()
    path = _cache_path(doc.document_id)
    if not storage.exists(path):
        return None
    try:
        with storage.open(path, 'rb') as f:
            return json.loads(f.read().decode('utf-8'))
    except Exception:
        logger.exception('[PIDVResultsCache] Failed to read cache for document_id=%s', doc.document_id)
        return None


def clear_results_cache(doc) -> None:
    """Delete the cached snapshot for a document, if any."""
    from apps.core.storage_backends import PIDAnalysisCacheStorage

    storage = PIDAnalysisCacheStorage()
    path = _cache_path(doc.document_id)
    if storage.exists(path):
        storage.delete(path)
        logger.info('[PIDVResultsCache] Cleared cache for document_id=%s', doc.document_id)
