"""
I/O List Extraction Results Cache
==================================
Stores a JSON snapshot of a document's extraction results — one blob per
document at ``io_list_cache/<document_id>/results.json`` — via Django's
storage abstraction (apps.core.storage_backends.IOListResultsCacheStorage),
so this transparently uses S3 in any environment with USE_S3=True and
local disk otherwise, exactly like every other storage-backed feature in
this codebase. No raw boto3 calls needed. Same pattern as
apps.pid_verification.services.results_cache.

Purpose: a fast-path for VIEWING an already-completed document — one
S3/disk read instead of the extracted_rows/extracted_comments DB queries.
It is NEVER consulted before running an extraction: "Upload & Extract"
(views.py's create()) and "Re-extract" (re_extract()) must always run a
genuinely fresh extraction, no exceptions — see those methods' own
docstrings for the confirmed bugs a pre-extraction cache check caused
here previously. Both only ever WRITE to this cache, unconditionally,
right after an extraction finishes; save_results_cache()'s overwrite-in-
place naturally retires whatever the previous cache entry held, so there
is nothing separate to "clear" first on a fresh upload, a different scan
mode, or a re-extract — every fresh write already replaces it.

The database (IOListDocument / IOListExtractedRow / IOListExtractedComment)
remains the source of truth. A cache miss, a corrupt entry, or a stale
entry (file_hash/project_id no longer matching the document's current
state — see load_results_cache) always falls back to the normal DB-backed
serializer response, never an error.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

CACHE_FILE_NAME = 'results.json'


def _cache_path(document_id) -> str:
    return f'{document_id}/{CACHE_FILE_NAME}'


def build_cache_payload(doc, file_hash: str, scan_mode: str = '') -> dict:
    """Assemble the cache JSON from a just-completed document's current DB
    state, reusing the same serializer the detail endpoint returns (so the
    cached shape never drifts from what's actually stored).

    scan_mode: 'quick' | 'thorough' | '' (empty for a plain io_list table
    document, or an io_list document extracted before this field existed —
    scan mode is only meaningful for pid_drawing/Vision extraction).
    """
    from ..serializers import IOListDocumentDetailSerializer

    data = IOListDocumentDetailSerializer(doc).data
    return {
        'document_id': doc.id,
        'project_id': doc.project_id,
        'file_hash': file_hash,
        'scan_mode': scan_mode or '',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'document_type': data.get('document_type'),
        'extracted_rows': data.get('extracted_rows', []),
        'extracted_comments': data.get('extracted_comments', []),
        'legend_findings': data.get('legend_findings', []),
        'extraction_stats': data.get('extraction_stats', {}),
    }


def save_results_cache(doc, file_hash: str, scan_mode: str = '') -> dict:
    """Snapshot a just-completed document's results to the cache. Never
    raises — a cache-write failure must never fail the extraction request
    itself; logged and swallowed, leaving the (source-of-truth) DB write
    as the only thing that mattered for this request to succeed."""
    from apps.core.storage_backends import IOListResultsCacheStorage
    from django.core.files.base import ContentFile

    try:
        payload = build_cache_payload(doc, file_hash, scan_mode)
        content = json.dumps(payload, default=str).encode('utf-8')
        storage = IOListResultsCacheStorage()
        storage.save(_cache_path(doc.id), ContentFile(content))
        logger.info(
            '[IOListResultsCache] Saved cache for document_id=%s scan_mode=%r hash=%s',
            doc.id, scan_mode, file_hash[:12] if file_hash else '',
        )
        return payload
    except Exception:
        logger.exception(
            '[IOListResultsCache] Failed to save cache for document_id=%s', doc.id,
        )
        return {}


def load_results_cache(doc) -> dict | None:
    """Return the cached payload dict, or None (a cache MISS, never an
    error) if:
      - no cache file exists yet,
      - it can't be read/parsed (corrupt), or
      - it's STALE — cached file_hash/project_id no longer match this
        document's current pdf_sha256/project_id (the file was
        re-extracted, or the document was moved to a different project,
        since this snapshot was written).
    """
    from apps.core.storage_backends import IOListResultsCacheStorage

    storage = IOListResultsCacheStorage()
    path = _cache_path(doc.id)
    if not storage.exists(path):
        return None
    try:
        with storage.open(path, 'rb') as f:
            payload = json.loads(f.read().decode('utf-8'))
    except Exception:
        logger.exception(
            '[IOListResultsCache] Failed to read cache for document_id=%s', doc.id,
        )
        return None

    if payload.get('file_hash') != doc.pdf_sha256:
        logger.info(
            '[IOListResultsCache] Cache STALE for document_id=%s '
            '(cached hash %s != current %s) — ignoring',
            doc.id, (payload.get('file_hash') or '')[:12], (doc.pdf_sha256 or '')[:12],
        )
        return None
    if payload.get('project_id') != doc.project_id:
        logger.info(
            '[IOListResultsCache] Cache STALE for document_id=%s '
            '(cached project_id %s != current %s) — ignoring',
            doc.id, payload.get('project_id'), doc.project_id,
        )
        return None
    return payload


def clear_results_cache(doc) -> None:
    """Delete the cached snapshot for a document, if any. Exposed for
    explicit call sites even though save_results_cache's overwrite-in-
    place already makes a separate clear step unnecessary for the normal
    upload/re-extract flow."""
    from apps.core.storage_backends import IOListResultsCacheStorage

    storage = IOListResultsCacheStorage()
    path = _cache_path(doc.id)
    if storage.exists(path):
        storage.delete(path)
        logger.info('[IOListResultsCache] Cleared cache for document_id=%s', doc.id)
