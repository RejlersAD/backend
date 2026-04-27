"""
Non-TEFF Bulk Master Index - DRF views.

Additive endpoints (do not touch the existing single-file views):

    POST   /api/v1/non-teff/batch/create/
    POST   /api/v1/non-teff/batch/<batch_id>/upload/
    POST   /api/v1/non-teff/batch/<batch_id>/start/
    GET    /api/v1/non-teff/batch/<batch_id>/status/
    GET    /api/v1/non-teff/batch/<batch_id>/items/
    PATCH  /api/v1/non-teff/batch/<batch_id>/items/<item_id>/
    POST   /api/v1/non-teff/batch/<batch_id>/bulk-update/
    GET    /api/v1/non-teff/batch/<batch_id>/export/
    GET    /api/v1/non-teff/batch/template/      (returns template + taxonomy)

All heavy work runs in a daemon thread (mirroring the existing
``run_extraction_async`` pattern in ``extractor.py`` — no Celery required for
Phase 1).
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import uuid
from typing import Dict, List

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.text import get_valid_filename
from rest_framework import status as http_status
from rest_framework.decorators import (
    api_view,
    parser_classes,
    permission_classes,
)
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from django.http import FileResponse

from .models import NonTeffBatch, NonTeffBatchItem
from .services import document_search, master_index_export, master_index_service

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SOFT-CODED constants
# ---------------------------------------------------------------------------

# Sub-directory under MEDIA_ROOT where batch files are written.
MEDIA_SUBDIR = 'non_teff_batches'

EXPORT_FILENAME_TEMPLATE = 'Master_Index_{plant}.xlsx'
DEFAULT_EXPORT_PLANT = 'BATCH'

# Item page size for the grid view.
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _batch_dir(batch_id) -> str:
    path = os.path.join(settings.MEDIA_ROOT, MEDIA_SUBDIR, str(batch_id))
    os.makedirs(path, exist_ok=True)
    return path


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _extract_batch_payload(body: dict) -> Dict:
    """Validate the batch-create payload and fold in template hints."""
    name = (body.get('name') or '').strip() or f"Batch {uuid.uuid4().hex[:8]}"
    plant = (body.get('plant') or '').strip()
    defaults = body.get('batch_defaults') or {}
    if not isinstance(defaults, dict):
        defaults = {}
    # Merge template hints for any batch_default column not provided
    hints = master_index_service.get_batch_default_hints()
    for key, value in hints.items():
        defaults.setdefault(key, value)
    if plant:
        defaults.setdefault('plant', plant)
    return {'name': name, 'plant': plant, 'batch_defaults': defaults}


def _run_extraction_thread(batch_id: str) -> None:
    """Background worker: populate every pending/uploaded item."""
    try:
        batch = NonTeffBatch.objects.get(batch_id=batch_id)
    except NonTeffBatch.DoesNotExist:
        logger.error('Batch %s vanished before extraction', batch_id)
        return

    batch.status = NonTeffBatch.BATCH_STATUS_PROCESSING
    batch.save(update_fields=['status', 'updated_at'])

    items = list(NonTeffBatchItem.objects.filter(batch=batch).order_by('file_name'))
    ready = failed = 0
    for index, item in enumerate(items, start=1):
        item.status = NonTeffBatchItem.ITEM_STATUS_EXTRACTING
        item.save(update_fields=['status', 'updated_at'])
        try:
            row = master_index_service.build_row(
                row_index=index,
                file_name=item.file_name,
                relative_path=item.relative_path or item.file_name,
                file_path=item.storage_key,
                batch_defaults=batch.batch_defaults or {},
            )
            item.fields = row
            item.status = NonTeffBatchItem.ITEM_STATUS_READY
            item.error = ''
            ready += 1
        except Exception as exc:
            logger.exception('Extraction failed for %s', item.file_name)
            item.status = NonTeffBatchItem.ITEM_STATUS_FAILED
            item.error = str(exc)
            failed += 1
        item.save(update_fields=['fields', 'status', 'error', 'updated_at'])

    batch.ready_files = ready
    batch.failed_files = failed
    batch.status = (
        NonTeffBatch.BATCH_STATUS_READY if ready else NonTeffBatch.BATCH_STATUS_FAILED
    )
    batch.save(update_fields=['ready_files', 'failed_files', 'status', 'updated_at'])


def _serialize_batch(batch: NonTeffBatch) -> dict:
    return {
        'batch_id': str(batch.batch_id),
        'name': batch.name,
        'plant': batch.plant,
        'status': batch.status,
        'total_files': batch.total_files,
        'ready_files': batch.ready_files,
        'failed_files': batch.failed_files,
        'batch_defaults': batch.batch_defaults or {},
        'created_at': batch.created_at.isoformat(),
        'updated_at': batch.updated_at.isoformat(),
    }


def _serialize_item(item: NonTeffBatchItem) -> dict:
    return {
        'item_id': str(item.item_id),
        'batch_id': str(item.batch_id),
        'file_name': item.file_name,
        'relative_path': item.relative_path,
        'size_bytes': item.size_bytes,
        'status': item.status,
        'reviewed': item.reviewed,
        'error': item.error,
        'fields': item.fields or {},
    }


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_batch_template(_request):
    """Return the template + taxonomy so the frontend grid can auto-configure."""
    return Response({
        'template': master_index_service.load_template(),
        'taxonomy': master_index_service.load_taxonomy(),
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_batch(request):
    payload = _extract_batch_payload(request.data or {})
    batch = NonTeffBatch.objects.create(
        name=payload['name'],
        plant=payload['plant'],
        batch_defaults=payload['batch_defaults'],
        status=NonTeffBatch.BATCH_STATUS_DRAFT,
        created_by=request.user if request.user.is_authenticated else None,
    )
    return Response(_serialize_batch(batch), status=http_status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def upload_batch_files(request, batch_id):
    batch = get_object_or_404(NonTeffBatch, batch_id=batch_id)
    uploaded = request.FILES.getlist('files') or request.FILES.getlist('file')
    if not uploaded:
        return Response({'error': 'no files provided'},
                        status=http_status.HTTP_400_BAD_REQUEST)

    limits = master_index_service.get_limits()
    max_bytes = int(limits.get('max_file_size_mb', 500)) * 1024 * 1024
    max_files = int(limits.get('max_files_per_batch', 2000))
    allowed_ext = {e.lower() for e in limits.get('allowed_extensions', [])}

    existing = batch.items.count()
    if existing + len(uploaded) > max_files:
        return Response({'error': f'exceeds max_files_per_batch={max_files}'},
                        status=http_status.HTTP_400_BAD_REQUEST)

    batch.status = NonTeffBatch.BATCH_STATUS_UPLOADING
    batch.save(update_fields=['status', 'updated_at'])

    base_dir = _batch_dir(batch.batch_id)
    created: List[dict] = []
    skipped: List[dict] = []

    # Relative paths (from the directory picker) arrive in a parallel list so
    # we can preserve folder hierarchy inside batch_defaults['source_folder'].
    rel_paths = request.POST.getlist('relative_paths')

    for idx, up in enumerate(uploaded):
        original = up.name
        ext = os.path.splitext(original)[1].lower()
        if allowed_ext and ext not in allowed_ext:
            skipped.append({'file_name': original, 'reason': 'extension not allowed'})
            continue
        if up.size > max_bytes:
            skipped.append({'file_name': original, 'reason': 'exceeds max size'})
            continue

        safe_name = get_valid_filename(original)
        # Namespace with a short uuid to avoid collisions inside the batch dir.
        stored = f"{uuid.uuid4().hex[:8]}_{safe_name}"
        abs_path = os.path.join(base_dir, stored)
        with open(abs_path, 'wb') as dst:
            for chunk in up.chunks():
                dst.write(chunk)

        rel = rel_paths[idx] if idx < len(rel_paths) else original

        with transaction.atomic():
            item = NonTeffBatchItem.objects.create(
                batch=batch,
                file_name=original,
                relative_path=rel,
                storage_key=abs_path,
                size_bytes=up.size,
                sha256=_sha256(abs_path),
                status=NonTeffBatchItem.ITEM_STATUS_UPLOADED,
            )
        created.append(_serialize_item(item))

    batch.total_files = batch.items.count()
    batch.storage_prefix = base_dir
    batch.save(update_fields=['total_files', 'storage_prefix', 'updated_at'])

    return Response({
        'batch': _serialize_batch(batch),
        'created': created,
        'skipped': skipped,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def start_batch(request, batch_id):
    batch = get_object_or_404(NonTeffBatch, batch_id=batch_id)
    if batch.status == NonTeffBatch.BATCH_STATUS_PROCESSING:
        return Response({'error': 'already processing'},
                        status=http_status.HTTP_409_CONFLICT)
    if not batch.items.exists():
        return Response({'error': 'no files uploaded'},
                        status=http_status.HTTP_400_BAD_REQUEST)

    thread = threading.Thread(
        target=_run_extraction_thread,
        args=(str(batch.batch_id),),
        daemon=True,
        name=f"non_teff_batch_{batch.batch_id}",
    )
    thread.start()
    return Response({'started': True, 'batch_id': str(batch.batch_id)})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def batch_status(_request, batch_id):
    batch = get_object_or_404(NonTeffBatch, batch_id=batch_id)
    counts = {
        'pending':    batch.items.filter(status=NonTeffBatchItem.ITEM_STATUS_PENDING).count(),
        'uploaded':   batch.items.filter(status=NonTeffBatchItem.ITEM_STATUS_UPLOADED).count(),
        'extracting': batch.items.filter(status=NonTeffBatchItem.ITEM_STATUS_EXTRACTING).count(),
        'ready':      batch.items.filter(status=NonTeffBatchItem.ITEM_STATUS_READY).count(),
        'failed':     batch.items.filter(status=NonTeffBatchItem.ITEM_STATUS_FAILED).count(),
    }
    return Response({'batch': _serialize_batch(batch), 'counts': counts})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_batch_items(request, batch_id):
    batch = get_object_or_404(NonTeffBatch, batch_id=batch_id)
    try:
        page = max(1, int(request.GET.get('page', '1')))
        page_size = min(MAX_PAGE_SIZE, max(1, int(request.GET.get('page_size', DEFAULT_PAGE_SIZE))))
    except ValueError:
        page, page_size = 1, DEFAULT_PAGE_SIZE

    qs = batch.items.all().order_by('file_name')
    status_filter = request.GET.get('status')
    if status_filter:
        qs = qs.filter(status=status_filter)

    total = qs.count()
    start = (page - 1) * page_size
    items = qs[start:start + page_size]
    return Response({
        'batch_id': str(batch.batch_id),
        'page': page,
        'page_size': page_size,
        'total': total,
        'items': [_serialize_item(i) for i in items],
    })


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def update_batch_item(request, batch_id, item_id):
    item = get_object_or_404(NonTeffBatchItem, batch_id=batch_id, item_id=item_id)
    payload = request.data or {}
    if 'fields' in payload and isinstance(payload['fields'], dict):
        merged = dict(item.fields or {})
        merged.update({k: ('' if v is None else str(v)) for k, v in payload['fields'].items()})
        item.fields = merged
    if 'reviewed' in payload:
        item.reviewed = bool(payload['reviewed'])
    item.save()
    return Response(_serialize_item(item))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def bulk_update_items(request, batch_id):
    """Apply one or more column values to many items at once."""
    batch = get_object_or_404(NonTeffBatch, batch_id=batch_id)
    payload = request.data or {}
    item_ids = payload.get('item_ids') or []
    updates = payload.get('fields') or {}
    if not item_ids or not isinstance(updates, dict):
        return Response({'error': 'item_ids and fields required'},
                        status=http_status.HTTP_400_BAD_REQUEST)

    items = batch.items.filter(item_id__in=item_ids)
    updated = 0
    for item in items:
        merged = dict(item.fields or {})
        merged.update({k: ('' if v is None else str(v)) for k, v in updates.items()})
        item.fields = merged
        item.save(update_fields=['fields', 'updated_at'])
        updated += 1
    return Response({'updated': updated})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def export_batch(_request, batch_id):
    batch = get_object_or_404(NonTeffBatch, batch_id=batch_id)
    items = [
        (i.fields or {})
        for i in batch.items.filter(status=NonTeffBatchItem.ITEM_STATUS_READY).order_by('file_name')
    ]
    data = master_index_export.build_workbook(
        batch_name=batch.name,
        plant=batch.plant or DEFAULT_EXPORT_PLANT,
        items=items,
    )
    filename = EXPORT_FILENAME_TEMPLATE.format(
        plant=(batch.plant or DEFAULT_EXPORT_PLANT).replace(' ', '_')
    )
    response = HttpResponse(
        data,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    batch.status = NonTeffBatch.BATCH_STATUS_EXPORTED
    batch.save(update_fields=['status', 'updated_at'])
    return response


# ---------------------------------------------------------------------------
# DOCUMENT SEARCH CANVAS — additive, read-only endpoints
# ---------------------------------------------------------------------------
# /search/                                          → cross-batch + cross-job text search
# /batch/<batch_id>/items/<item_id>/locate/?q=…     → bbox(es) per page for query
# /batch/<batch_id>/items/<item_id>/page/<n>/image/ → cached PNG of page n
# ---------------------------------------------------------------------------

# Soft-coded mime + cache-control for rendered PDF page images.
_PAGE_IMAGE_MIME = 'image/png'
_PAGE_IMAGE_CACHE_HEADER = 'private, max-age=3600'


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def search_documents(request):
    """Free-text search across batch items (and optionally legacy jobs)."""
    query = (request.GET.get('q') or '').strip()
    batch_id = request.GET.get('batch_id') or None
    kind = request.GET.get('kind') or None  # 'batch' | 'job' | None
    if not query:
        return Response({'query': '', 'total': 0, 'results': []})
    payload = document_search.search_records(query=query, batch_id=batch_id, kind=kind)
    return Response(payload)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def locate_in_item(request, batch_id, item_id):
    """Return per-page bounding boxes (page-size %) for *q* inside an item PDF."""
    item = get_object_or_404(NonTeffBatchItem, batch_id=batch_id, item_id=item_id)
    query = (request.GET.get('q') or '').strip()
    pdf_path, err = document_search.resolve_item_pdf(item)
    if err:
        return Response({'matches': [], 'page_count': 0, 'error': err})
    payload = document_search.locate_in_pdf(pdf_path, query)
    payload.update({
        'item_id': str(item.item_id),
        'batch_id': str(item.batch_id),
        'file_name': item.file_name,
        'query': query,
    })
    return Response(payload)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_item_page_image(request, batch_id, item_id, page_no):
    """Stream a rendered PNG of the requested 1-based page of the item's PDF."""
    item = get_object_or_404(NonTeffBatchItem, batch_id=batch_id, item_id=item_id)
    pdf_path, err = document_search.resolve_item_pdf(item)
    if err:
        return Response({'error': err}, status=http_status.HTTP_400_BAD_REQUEST)
    try:
        page_int = int(page_no)
    except (TypeError, ValueError):
        return Response({'error': 'invalid page number'}, status=http_status.HTTP_400_BAD_REQUEST)
    img_path = document_search.render_pdf_page_png(pdf_path, page_int, str(item.item_id))
    if not img_path or not os.path.exists(img_path):
        return Response({'error': 'page render failed'}, status=http_status.HTTP_404_NOT_FOUND)
    fh = open(img_path, 'rb')
    response = FileResponse(fh, content_type=_PAGE_IMAGE_MIME)
    response['Cache-Control'] = _PAGE_IMAGE_CACHE_HEADER
    return response
