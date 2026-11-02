"""
3D Scene Cache — P&ID Verification V2
=====================================

One JSON blob per (document, page) at
``analysis_cache_v2/<document_id>/scene3d_page<N>.json``, next to the
results cache (same storage backend, same conventions). The scene depends
on the document's extraction + findings, so the payload carries the
document's ``file_hash`` as the run id: a re-run rewrites the results and
the hash no longer matches, retiring stale scenes automatically. Scenes
are computed lazily on the first request for a sheet (under 1 s per
sheet).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile

logger = logging.getLogger(__name__)


def get_pdf_bytes(doc) -> bytes:
    """The document's PDF bytes, cached on local disk keyed by content hash
    (file_hash plays the ETag role): a 76-sheet PDF is downloaded from S3
    once, not on every sheet's scene request (30 Oct 2026)."""
    cache_dir = os.path.join(tempfile.gettempdir(), 'pidchk')
    cache_path = os.path.join(cache_dir, f'{doc.document_id}_{doc.file_hash or "nohash"}.pdf')
    try:
        if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
            with open(cache_path, 'rb') as f:
                return f.read()
    except OSError:
        pass
    from apps.core.file_access import read_file_bytes
    data = read_file_bytes(doc.original_file)
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cache_path, 'wb') as f:
            f.write(data)
    except OSError:
        logger.debug('[PIDVScene3D] pdf byte cache write failed (non-fatal)', exc_info=True)
    return data


# Bump when the scene build changes — cached scenes from an older builder
# must not be served (30 Oct 2026: tiers/ghosts/labels rework).
SCENE_VERSION = 2


def _cache_path(document_id, page: int) -> str:
    return f'{document_id}/scene3d_v{SCENE_VERSION}_page{page}.json'


def load_scene3d_cache(doc, page: int) -> dict | None:
    """Cached scene dict, or None on miss / corrupt / stale run."""
    from apps.core.storage_backends import PIDVerificationV2AnalysisCacheStorage

    storage = PIDVerificationV2AnalysisCacheStorage()
    path = _cache_path(doc.document_id, page)
    if not storage.exists(path):
        return None
    try:
        with storage.open(path, 'rb') as f:
            payload = json.loads(f.read().decode('utf-8'))
    except Exception:
        logger.exception('[PIDVScene3D] Failed to read cache doc=%s page=%s', doc.document_id, page)
        return None
    if payload.get('file_hash') != doc.file_hash:
        return None  # a newer run retired this scene
    return payload.get('scene')


def save_scene3d_cache(doc, page: int, scene: dict) -> None:
    from apps.core.storage_backends import PIDVerificationV2AnalysisCacheStorage
    from django.core.files.base import ContentFile

    storage = PIDVerificationV2AnalysisCacheStorage()
    payload = {
        'document_id': str(doc.document_id),
        'file_hash': doc.file_hash,
        'page': page,
        'scene': scene,
    }
    storage.save(
        _cache_path(doc.document_id, page),
        ContentFile(json.dumps(payload, default=str).encode('utf-8')),
    )
    logger.info('[PIDVScene3D] Cached scene doc=%s page=%s', doc.document_id, page)
