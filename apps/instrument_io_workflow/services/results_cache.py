"""
I/O List Extraction Results Cache
==================================
This module holds TWO genuinely separate caches — read each function's own
docstring before touching either, they exist for different reasons and
have different, deliberately different keys:

1. THE VIEW CACHE (build_cache_payload / save_results_cache /
   load_results_cache / clear_results_cache below) — a fast-path for
   VIEWING an already-completed document: one S3/disk read at
   ``io_list_cache/<document_id>/results.json`` instead of the
   extracted_rows/extracted_comments DB queries. Keyed by document_id.
   It is NEVER consulted before running an extraction — "Upload & Extract"
   (views.py's create()) and "Re-extract" (re_extract()) must always run a
   genuinely fresh extraction. Both only ever WRITE to it, unconditionally,
   right after an extraction finishes.

2. THE VISION RESULTS CACHE (save_vision_results_cache /
   load_vision_results_cache further down) — a SKIP-VISION-ENTIRELY cache
   for a fresh P&ID drawing upload, keyed by (file_hash, scan_mode,
   extraction_mode) — DELIBERATELY NOT document_id or project_id, so the
   exact same PDF bytes uploaded to any project reuses a prior run's
   output instead of paying for another one. This IS consulted before
   running an extraction, but only from create()'s fresh-upload path (via
   process_io_document's check_cache param) — re_extract() never passes
   check_cache=True, so a deliberate re-extract always bypasses it,
   matching the explicit requirement "Re-extract must ALWAYS run fresh,
   never cached."

   extraction_mode ('ocr' or 'vision_<provider>', see
   vision_extraction_mode() below) is its own key dimension, not just a
   detail inside the cached payload — a confirmed bug had a fresh upload
   WITH a newly-supplied, valid API key still get back a WEAKER prior
   run's local-OCR result for the same file+scan_mode, because extraction
   method wasn't part of the key at all. Keying by extraction_mode makes
   an OCR-era entry a genuine cache MISS for any request made with a key
   (and vice versa, and across providers) — never a silent downgrade.

   IMPORTANT HISTORY — an EARLIER hash-only pre-extraction cache was
   removed from this codebase after a confirmed bug: a brand-new document
   silently returned ANOTHER document's already-completed result whenever
   the same file bytes had been seen before, even with a newly-supplied
   Vision key or a DIFFERENT Quick/Thorough Scan mode than the cached run
   used — because neither scan mode nor anything else distinguishing the
   two runs was ever part of that cache's key (see views.py's create()
   for the original incident description). This cache fixes that specific
   gap (scan_mode and now extraction_mode ARE part of the key) and treats
   project-independence as the deliberate point rather than an accidental
   side effect — but it reintroduces the same CLASS of behaviour (a fresh
   upload can return a previous run's result without calling Vision
   again), so any future change here should re-read that history first.

Both use Django's storage abstraction
(apps.core.storage_backends.IOListResultsCacheStorage), so both
transparently use S3 in any environment with USE_S3=True and local disk
otherwise — no raw boto3 calls needed.

The database (IOListDocument / IOListExtractedRow / IOListExtractedComment)
remains the source of truth for an already-completed document regardless
of either cache. A cache miss, a corrupt entry, or a stale entry always
falls back to running/serving the real thing, never an error.
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


# ─────────────────────────────────────────────────────────────────────
# Vision results cache — see this module's top docstring (section 2) for
# what this is, why it's separate from everything above, and the
# incident history behind the design.
# ─────────────────────────────────────────────────────────────────────
VISION_CACHE_ROOT = 'vision_results'


def vision_extraction_mode(vision_provider: str | None, key_supplied: bool) -> str:
    """The third cache-key dimension, alongside file_hash/scan_mode.

    BUG FIX: without this, a fresh upload WITH a valid, tested API key
    could still get back a PREVIOUS run's local-OCR result for the same
    file+scan_mode — OCR is much weaker than Vision, so a user who just
    supplied a key kept silently seeing the old, worse OCR-era result
    with no Vision call ever made. 'ocr' and 'vision_<provider>' are
    deliberately distinct cache entries so a request made WITH a key can
    never read back an entry written WITHOUT one (or from a different
    provider) — it's a genuine cache MISS in that case, exactly as if the
    file had never been seen before, and a real Vision call runs.
    """
    if key_supplied and vision_provider:
        return f'vision_{vision_provider}'
    return 'ocr'


def _vision_cache_path(file_hash: str, scan_mode: str, extraction_mode: str) -> str:
    # scan_mode defaults to 'quick' (not '') so a caller that forgets to
    # pass it can't collide with, or accidentally read, an entry actually
    # written under a real mode. extraction_mode has no such silent
    # default — every call site now computes it explicitly via
    # vision_extraction_mode() above, since defaulting it wrongly here
    # would silently recreate the exact OCR/Vision collision this exists
    # to prevent.
    return f'{VISION_CACHE_ROOT}/{file_hash}/{scan_mode or "quick"}/{extraction_mode}.json'


def save_vision_results_cache(
    file_hash: str, scan_mode: str, io_rows: list, warnings: list | None = None,
    extraction_mode: str = 'ocr',
) -> None:
    """Cache a P&ID drawing's finished Vision extraction — the canonical
    io_rows list, already deduped/backfilled/filtered exactly as it will
    be persisted — keyed by (file_hash, scan_mode, extraction_mode).
    Called unconditionally from finalize_io_document after every
    successful pid_drawing completion (whether that run actually called
    Vision, or itself came from a cache hit — re-saving identical data is
    harmless), the same overwrite-in-place pattern as save_results_cache
    above: there is nothing separate to invalidate first, a fresh write
    already replaces whatever was there. Never raises — a cache-write
    failure must never fail the extraction request itself.

    extraction_mode: 'ocr' or 'vision_<provider>' — see
    vision_extraction_mode() above. Callers must pass the SAME mode they
    used to check the cache in the first place, so a run made with a key
    never overwrites (or is shadowed by) a no-key run's entry.
    """
    from apps.core.storage_backends import IOListResultsCacheStorage
    from django.core.files.base import ContentFile

    if not file_hash:
        return
    try:
        payload = {
            'file_hash': file_hash,
            'scan_mode': scan_mode or 'quick',
            'extraction_mode': extraction_mode,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'io_rows': io_rows,
            'warnings': warnings or [],
        }
        content = json.dumps(payload, default=str).encode('utf-8')
        storage = IOListResultsCacheStorage()
        storage.save(_vision_cache_path(file_hash, scan_mode, extraction_mode), ContentFile(content))
        logger.info(
            '[IOListVisionCache] Saved cache for hash=%s scan_mode=%r extraction_mode=%r (%d rows)',
            file_hash[:12], scan_mode, extraction_mode, len(io_rows or []),
        )
    except Exception:
        logger.exception(
            '[IOListVisionCache] Failed to save cache for hash=%s scan_mode=%r extraction_mode=%r',
            file_hash[:12] if file_hash else '', scan_mode, extraction_mode,
        )


def load_vision_results_cache(file_hash: str, scan_mode: str, extraction_mode: str = 'ocr') -> dict | None:
    """Return {'io_rows': [...], 'warnings': [...]}, or None (a cache
    MISS, never an error) if no entry exists yet for this exact
    (file_hash, scan_mode, extraction_mode) triple or it can't be read/
    parsed. Only ever called from process_io_document's check_cache path
    and finalize_io_document's compare_best path — see their own
    docstrings; re_extract() never triggers this.

    extraction_mode defaults to 'ocr' only as the SAFEST possible
    fallback for a caller that forgets to pass it — an accidental 'ocr'
    lookup can only ever under-match (a miss where a hit was possible),
    never return a stronger Vision-run's cache to a no-key request or
    vice versa.
    """
    from apps.core.storage_backends import IOListResultsCacheStorage

    if not file_hash:
        return None
    storage = IOListResultsCacheStorage()
    path = _vision_cache_path(file_hash, scan_mode, extraction_mode)
    if not storage.exists(path):
        return None
    try:
        with storage.open(path, 'rb') as f:
            payload = json.loads(f.read().decode('utf-8'))
        return {'io_rows': payload.get('io_rows') or [], 'warnings': payload.get('warnings') or []}
    except Exception:
        logger.exception(
            '[IOListVisionCache] Failed to read cache for hash=%s scan_mode=%r extraction_mode=%r',
            file_hash[:12], scan_mode, extraction_mode,
        )
        return None
