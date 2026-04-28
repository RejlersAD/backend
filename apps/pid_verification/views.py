"""
P&ID Verification — API Views
===============================
Projects:
  GET    /api/v1/pid-verification/projects/              → list user's projects
  POST   /api/v1/pid-verification/projects/              → create project
  PUT    /api/v1/pid-verification/projects/<project_id>/ → update project
  DELETE /api/v1/pid-verification/projects/<project_id>/ → delete project

Documents:
  POST   /api/v1/pid-verification/upload-pid/            → upload (pass project_id in form)
  GET    /api/v1/pid-verification/status/<document_id>/  → poll status
  GET    /api/v1/pid-verification/results/<document_id>/ → full findings
  GET    /api/v1/pid-verification/export/excel/<document_id>/
  GET    /api/v1/pid-verification/export/pdf/<document_id>/
  GET    /api/v1/pid-verification/list/                  → user document history
  DELETE /api/v1/pid-verification/delete/<document_id>/  → remove document
"""
import logging
import threading
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.db import models
from django.http import HttpResponse
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.rbac.permissions import HasDisciplineAccess
from apps.core.queue_service import RobustQueueService, QueueUnavailableException

from .models import PIDVProject, PIDVDocument, PIDVFinding, PIDVLegendSheet
from .serializers import (
    PIDVProjectSerializer,
    PIDVProjectCreateSerializer,
    PIDVDocumentSerializer,
    PIDVDocumentListSerializer,
    PIDVFindingSerializer,
    PIDVFindingUpdateSerializer,
    UploadSerializer,
)
from .services.consistency import compute_file_hash, check_cache
from .services.legend_knowledge import (
    LEGEND_KNOWLEDGE_PATH,
    build_legend_knowledge,
    load_legend_knowledge,
    save_legend_knowledge,
)

logger = logging.getLogger(__name__)

# SOFT-CODED: Worker availability cache TTL (seconds).
# Adjust via PIDV_WORKER_CHECK_TTL env var.
_WORKER_CHECK_TTL = int(getattr(settings, 'PIDV_WORKER_CHECK_TTL', 60))
_WORKER_CHECK_CACHE_KEY = 'pidv_celery_worker_active'


def _has_active_celery_workers() -> bool:
    """
    Return True if at least one Celery worker is listening.
    Result is cached in Django's cache backend for _WORKER_CHECK_TTL seconds
    to avoid adding latency on every upload request.
    """
    # When tasks are always-eager, they run synchronously — no worker needed.
    if getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False):
        return True

    cached = cache.get(_WORKER_CHECK_CACHE_KEY)
    if cached is not None:
        return cached

    try:
        from celery import current_app as celery_app
        inspector = celery_app.control.inspect(timeout=1.5)
        active = bool(inspector.ping())
    except Exception:
        active = False

    cache.set(_WORKER_CHECK_CACHE_KEY, active, timeout=_WORKER_CHECK_TTL)
    return active


# ===========================================================================
# PROJECT CRUD
# ===========================================================================

@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def projects(request):
    """
    GET  -> list all projects belonging to the authenticated user.
    POST -> create a new project.
    """
    if request.method == "GET":
        qs = PIDVProject.objects.filter(created_by=request.user)
        return Response(PIDVProjectSerializer(qs, many=True).data)

    # POST -- create
    serializer = PIDVProjectCreateSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    project = serializer.save(created_by=request.user)
    return Response(PIDVProjectSerializer(project).data, status=status.HTTP_201_CREATED)


@api_view(["GET", "PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def project_detail(request, project_id):
    """
    GET    -> retrieve a single project by ID.
    PUT    -> update project name / description.
    DELETE -> delete project (documents become project-less, not deleted).
    """
    project = _get_project_or_404(project_id, request.user)
    if project is None:
        return Response({"error": "Project not found"}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response(PIDVProjectSerializer(project).data)

    if request.method == "PUT":
        serializer = PIDVProjectCreateSerializer(project, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(PIDVProjectSerializer(project).data)

    # DELETE
    project.delete()
    return Response({"message": "Project deleted"}, status=status.HTTP_200_OK)


# ===========================================================================
# UPLOAD
# ===========================================================================

@api_view(["POST"])
@permission_classes([IsAuthenticated, HasDisciplineAccess])
@parser_classes([MultiPartParser, FormParser])
def upload_pid(request):
    """
    Accept a P&ID file, optionally associate with a project_id,
    create a PIDVDocument, enqueue background processing.
    
    RBAC: User must have "engineering" or "qa_qc" discipline or be admin.
    Queue: Intelligent fallback to synchronous processing if queue unavailable.
    """
    # Set module requirement for discipline check
    upload_pid.module_required = 'pid_verification'
    serializer = UploadSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    uploaded_file = serializer.validated_data["file"]
    project_id    = serializer.validated_data.get("project_id")

    allowed_ext = {"pdf", "png", "jpg", "jpeg", "tiff", "tif", "dwg"}
    file_ext    = uploaded_file.name.rsplit(".", 1)[-1].lower() if "." in uploaded_file.name else ""
    if file_ext not in allowed_ext:
        return Response(
            {"error": f"Unsupported file type: {file_ext}. Allowed: {', '.join(sorted(allowed_ext))}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Resolve project (must belong to the user)
    project = None
    if project_id:
        project = _get_project_or_404(str(project_id), request.user)
        if project is None:
            return Response({"error": "Project not found or access denied"}, status=status.HTTP_404_NOT_FOUND)

    # Deterministic cache check (only reuse if same project)
    file_hash = compute_file_hash(uploaded_file)
    cached    = check_cache(file_hash)
    if cached and cached.project == project:
        # Do not reuse degraded cache entries (e.g. completed docs with 0 drawings).
        if cached.status == PIDVDocument.Status.COMPLETED and not cached.drawings.exists():
            logger.warning(
                "[PIDVUpload] Ignoring degraded cache hash=%s doc_id=%s (0 drawings)",
                file_hash,
                cached.document_id,
            )
        elif cached.status == PIDVDocument.Status.FAILED:
            logger.warning(
                "[PIDVUpload] Ignoring failed cache hash=%s doc_id=%s",
                file_hash,
                cached.document_id,
            )
        else:
            logger.info("[PIDVUpload] Cache hit hash=%s doc_id=%s", file_hash, cached.document_id)
            return Response(
                {
                    "document_id": str(cached.document_id),
                    "status":      cached.status,
                    "cached":      True,
                    "message":     "Identical file already processed – returning cached results.",
                    "project_id":  str(project.project_id) if project else None,
                },
                status=status.HTTP_200_OK,
            )

    # Create new document record
    doc = PIDVDocument.objects.create(
        file_name     = uploaded_file.name,
        file_hash     = file_hash,
        original_file = uploaded_file,
        uploaded_by   = request.user,
        project       = project,
        status        = PIDVDocument.Status.UPLOADED,
    )

    # Enqueue Celery task with intelligent fallback
    try:
        from .tasks import process_pid_document, _resolve_file_path

        # ── Shared synchronous processing pipeline ─────────────────────────
        def _run_sync_pipeline(doc_id: str) -> None:
            """
            Execute the full P&ID processing pipeline synchronously.
            Used both as the RobustQueueService sync_fallback (when Redis is
            down) AND as a background-thread fallback (when no Celery workers
            are active in production).
            Uses _resolve_file_path from tasks.py so S3-backed files are
            handled identically to the Celery task.
            """
            logger.warning("[PIDVUpload] Running sync pipeline for doc_id=%s", doc_id)
            try:
                from apps.pid_verification.services.segmentation import segment_document
                from apps.pid_verification.services.extraction import extract_drawing
                from apps.pid_verification.services.graph_builder import build_graph
                from apps.pid_verification.services.rule_engine import run_rules
                from apps.pid_verification.models import PIDVDrawing, PIDVFinding

                _doc = PIDVDocument.objects.get(document_id=doc_id)
                _doc.status = PIDVDocument.Status.PROCESSING
                _doc.save(update_fields=["status", "updated_at"])

                # SOFT-CODED: _resolve_file_path handles local filesystem AND
                # S3-backed storage transparently (downloads to tmp when needed).
                file_path = _resolve_file_path(_doc)
                segments = segment_document(str(_doc.document_id), file_path)

                for seg in segments:
                    drawing_obj, _ = PIDVDrawing.objects.get_or_create(
                        document=_doc,
                        drawing_id=seg.drawing_id,
                        defaults={
                            'title': seg.title,
                            'page_index': seg.page_index,
                            'metadata': seg.metadata,
                        },
                    )
                    drawing_obj.findings.all().delete()

                    extraction = extract_drawing(file_path, page_index=seg.page_index)

                    raw_text = extraction.get('raw_text', '') or ''
                    extraction_summary = {
                        'tags': len(extraction.get('tags', [])),
                        'instruments': len(extraction.get('instruments', [])),
                        'valves': len(extraction.get('valves', [])),
                        'equipment': len(extraction.get('equipment', [])),
                        'line_sizes': len(extraction.get('line_sizes', [])),
                        'notes': len(extraction.get('notes', [])),
                        'holds': len(extraction.get('holds', [])),
                        'raw_text_length': len(raw_text),
                        'no_text_detected': len(raw_text.strip()) == 0,
                    }
                    metadata = drawing_obj.metadata or {}
                    metadata['extraction_summary'] = extraction_summary
                    # Real tag anchor coordinates for v2 smart overlay (soft-coded, additive).
                    tag_positions = extraction.get('tag_positions', {})
                    if tag_positions:
                        metadata['tag_positions'] = tag_positions
                    # Pipeline line designations with orientation info (H/V multi-angle).
                    # Soft-coded: mirrors tasks.py to keep both pipelines consistent.
                    line_tags = extraction.get('line_tags', [])
                    if line_tags:
                        metadata['line_tags'] = line_tags
                    drawing_obj.metadata = metadata
                    drawing_obj.save(update_fields=['metadata'])

                    graph = build_graph(extraction)
                    rule_findings = run_rules(extraction, graph)

                    bulk = [
                        PIDVFinding(
                            drawing=drawing_obj,
                            sl_no=sl,
                            category=rf.category,
                            rule_id=rf.rule_id,
                            issue_observed=rf.issue_observed,
                            action_required=rf.action_required,
                            evidence=rf.evidence,
                            direction=rf.direction,
                            severity=rf.severity,
                            status='open',
                        )
                        for sl, rf in enumerate(rule_findings, start=1)
                    ]
                    PIDVFinding.objects.bulk_create(bulk)

                _doc.status = PIDVDocument.Status.COMPLETED
                _doc.save(update_fields=["status", "updated_at"])
                logger.info("[PIDVUpload] Sync pipeline completed for doc_id=%s", doc_id)

            except Exception as exc:
                logger.error("[PIDVUpload] Sync pipeline failed for doc_id=%s: %s", doc_id, exc, exc_info=True)
                try:
                    _doc = PIDVDocument.objects.get(document_id=doc_id)
                    _doc.status = PIDVDocument.Status.FAILED
                    _doc.error_message = f"Sync processing failed: {exc}"
                    _doc.save(update_fields=["status", "error_message", "updated_at"])
                except Exception:
                    pass

        # ── Dispatch: Celery when workers exist, thread otherwise ──────────
        # Soft-coded: PIDV_WORKER_CHECK_ENABLED (default True) controls whether
        # we ping for Celery workers before deciding how to process.
        worker_check_enabled = getattr(settings, 'PIDV_WORKER_CHECK_ENABLED', True)
        use_celery = (not worker_check_enabled) or _has_active_celery_workers()

        if not use_celery:
            # No Celery workers detected – run in a daemon thread so the HTTP
            # response is returned immediately while processing continues.
            logger.info(
                "[PIDVUpload] No active Celery workers detected – "
                "processing doc_id=%s in background thread.", doc.document_id
            )
            t = threading.Thread(
                target=_run_sync_pipeline,
                args=(str(doc.document_id),),
                daemon=True,
                name=f"pidv-sync-{doc.document_id}",
            )
            t.start()
        else:
            # Workers available (or check disabled) – queue via Celery with
            # sync fallback for when Redis itself is unavailable.
            try:
                RobustQueueService.queue_task(
                    process_pid_document,
                    args=(str(doc.document_id),),
                    sync_fallback=_run_sync_pipeline,
                    max_retries=3,
                )
                logger.info("[PIDVUpload] Task queued via Celery: doc_id=%s", doc.document_id)
            except QueueUnavailableException as queue_exc:
                logger.error("[PIDVUpload] Queue unavailable and sync fallback failed: %s", queue_exc)
                doc.status = PIDVDocument.Status.FAILED
                doc.error_message = "Processing service unavailable. Please try again."
                doc.save(update_fields=["status", "error_message", "updated_at"])
                return Response(
                    {"error": "Processing queue unavailable. Please try again shortly."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
    
    except Exception as exc:
        logger.error("[PIDVUpload] Unexpected error setting up task: %s", exc)
        doc.status = PIDVDocument.Status.FAILED
        doc.error_message = f"Failed to start processing: {exc}"
        doc.save(update_fields=["status", "error_message", "updated_at"])
        return Response(
            {"error": "Failed to process document. Please try again."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # Soft-hook: keep cross-feature snapshot updated in background (non-blocking)
    try:
        from apps.cross_recommendation.tasks import sync_s3_snapshot
        exec_result = RobustQueueService.queue_task(
            sync_s3_snapshot,
            max_retries=1
        )
        logger.debug("[PIDVUpload] Queued cross-recommendation snapshot sync")
    except Exception as exc:
        logger.warning("[PIDVUpload] Cross snapshot sync skipped: %s", exc)

    return Response(
        {
            "document_id": str(doc.document_id),
            "status":      doc.status,
            "file_name":   doc.file_name,
            "project_id":  str(project.project_id) if project else None,
            "message":     "File uploaded successfully. Processing started.",
        },
        status=status.HTTP_202_ACCEPTED,
    )


# ===========================================================================
# REPROCESS  (re-run the full quality-check pipeline on an existing document)
# ===========================================================================

# REPROCESS_ALLOWED_STATUSES: only documents in these states can be re-checked.
# Prevents accidentally re-queuing a currently-processing document.
REPROCESS_ALLOWED_STATUSES = {'completed', 'failed', 'legend_pending'}

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def reprocess_document(request, document_id):
    """
    POST /api/v1/pid-verification/reprocess/<document_id>/

    Re-enqueues the full P&ID quality-check pipeline for an already-uploaded
    document — no file upload required.  The original file (local or S3) is
    reused exactly as-is.

    Returns the same shape as upload_pid so the frontend can reuse its
    existing status-polling loop.
    """
    doc = _get_doc_or_404(document_id, request.user)
    if doc is None:
        return Response({"error": "Document not found"}, status=status.HTTP_404_NOT_FOUND)

    if doc.status not in REPROCESS_ALLOWED_STATUSES:
        return Response(
            {"error": f"Document is currently '{doc.status}' — wait for it to finish before re-checking."},
            status=status.HTTP_409_CONFLICT,
        )

    # Reset state so the task pipeline treats this as a fresh run
    doc.status        = 'uploaded'
    doc.error_message = ''
    doc.save(update_fields=['status', 'error_message', 'updated_at'])

    # Enqueue Celery task (with sync-thread fallback if workers unavailable)
    try:
        from .tasks import process_pid_document
        from apps.config.queue_service import RobustQueueService

        def _sync_fallback(doc_id):
            import threading
            t = threading.Thread(
                target=process_pid_document,
                args=(doc_id,),
                daemon=True,
            )
            t.start()

        RobustQueueService.enqueue(
            process_pid_document.delay,
            str(doc.document_id),
            sync_fallback=_sync_fallback,
        )
    except Exception:
        # Last-resort: fire task directly in a daemon thread
        import threading
        from .tasks import process_pid_document
        t = threading.Thread(
            target=process_pid_document,
            args=(str(doc.document_id),),
            daemon=True,
        )
        t.start()

    logger.info("[PIDVReprocess] Queued recheck for document_id=%s user=%s", document_id, request.user)

    return Response(
        {
            "document_id": str(doc.document_id),
            "status":      doc.status,
            "message":     "Re-check queued — the original file will be re-analysed without re-uploading.",
            "file_name":   doc.file_name,
        },
        status=status.HTTP_202_ACCEPTED,
    )


# ===========================================================================
# STATUS / RESULTS / EXPORTS / LIST / DELETE
# ===========================================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_status(request, document_id):
    doc = _get_doc_or_404(document_id, request.user)
    if doc is None:
        return Response({"error": "Document not found"}, status=status.HTTP_404_NOT_FOUND)

    return Response({
        "document_id":   str(doc.document_id),
        "status":        doc.status,
        "file_name":     doc.file_name,
        "error_message": doc.error_message or None,
        "excel_s3_url":  doc.excel_s3_url or None,
        "pdf_s3_url":    doc.pdf_s3_url   or None,
        "project_id":    str(doc.project.project_id) if doc.project else None,
        "updated_at":    doc.updated_at,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_results(request, document_id):
    doc = _get_doc_or_404(document_id, request.user)
    if doc is None:
        return Response({"error": "Document not found"}, status=status.HTTP_404_NOT_FOUND)

    if doc.status not in (PIDVDocument.Status.COMPLETED, PIDVDocument.Status.FAILED):
        return Response({"error": "Processing not yet complete", "status": doc.status}, status=status.HTTP_202_ACCEPTED)

    # Soft-upgrade: backfill tag_positions for drawings that were processed
    # before line-size coordinate extraction was added.  Runs only when the
    # original file is stored locally and PyMuPDF is available.  Non-blocking
    # — any error here is silently ignored so the result is still returned.
    if doc.status == PIDVDocument.Status.COMPLETED and doc.original_file:
        try:
            file_path = doc.original_file.path
            from .services.extraction import _extract_tag_positions
            for drawing in doc.drawings.all():
                meta = drawing.metadata or {}
                existing = meta.get('tag_positions', {})
                # Re-extract when EITHER:
                #   • no line-size keys exist at all, OR
                #   • line-size keys exist but lack the new "all" array
                #     (written by the 3-strategy extractor).
                # Tags (unique IDs) keep their existing single-point coords.
                has_ls_v2 = any(
                    ('"' in k or 'mm' in k.lower() or k.startswith('DN'))
                    and isinstance(v, dict) and 'all' in v
                    for k, v in existing.items()
                )
                if not has_ls_v2:
                    new_pos = _extract_tag_positions(file_path, drawing.page_index)
                    if new_pos:
                        meta['tag_positions'] = new_pos
                        drawing.metadata = meta
                        drawing.save(update_fields=['metadata'])
        except Exception as _bp_exc:
            logger.debug('[PIDVResults] tag_positions backfill skipped: %s', _bp_exc)

    # ── line_tags backfill (soft-coded, additive) ─────────────────────────
    # Documents processed before the cloud-truncation resolution pass was added
    # will have stale line_tags without cloud_truncation_detected flags and will
    # be missing any LSZ-009 findings.  This block re-extracts pipeline designations
    # and creates the missing findings transparently — no user action required.
    # Trigger condition: a drawing has no LSZ-009 finding yet AND either has no
    # line_tags metadata or none have cloud_truncation_detected set.
    # Soft-coded: set PIDV_LINE_TAGS_BACKFILL=false in settings to disable.
    _backfill_enabled = getattr(settings, 'PIDV_LINE_TAGS_BACKFILL', True)
    if _backfill_enabled and doc.status == PIDVDocument.Status.COMPLETED and doc.original_file:
        try:
            from .services.extraction import _extract_pipeline_tags_multi_angle
            from .services.rule_engine import _check_pipeline_tag_duplicates
            from .models import PIDVFinding as _PIDVFinding

            _file_path = doc.original_file.path
            for _drawing in doc.drawings.all():
                # Soft-coded: deduplicate any LSZ-007/008 findings that were added
                # multiple times by previous runs of this backfill block (bug fix).
                # Runs before the guard checks so it cleans up existing data first.
                for _dup_rid in ('LSZ-006', 'LSZ-007', 'LSZ-008'):
                    _all_fids = list(
                        _PIDVFinding.objects.filter(drawing=_drawing, rule_id=_dup_rid)
                        .order_by('id').values_list('id', 'evidence')
                    )
                    _seen_ev: dict = {}
                    _stale_ids: list = []
                    for _fid, _fev in _all_fids:
                        _k = (_fev or '').strip()
                        if _k in _seen_ev:
                            _stale_ids.append(_fid)
                        else:
                            _seen_ev[_k] = _fid
                    if _stale_ids:
                        _PIDVFinding.objects.filter(id__in=_stale_ids).delete()
                        logger.info(
                            '[PIDVResults] backfill dedup: drawing=%s removed %d duplicate %s finding(s)',
                            _drawing.drawing_id, len(_stale_ids), _dup_rid,
                        )

                # Skip drawing if LSZ-009 finding already exists (already backfilled)
                _has_lsz009 = _PIDVFinding.objects.filter(
                    drawing=_drawing, rule_id='LSZ-009'
                ).exists()
                _meta = _drawing.metadata or {}
                _existing_ltags = _meta.get('line_tags', [])
                _has_cloud_flag = any(
                    lt.get('cloud_truncation_detected') for lt in _existing_ltags
                )

                if _has_lsz009 and _has_cloud_flag:
                    continue  # cloud-truncation backfill already done

                # Soft-coded: skip backfill when duplicate-rule findings already exist
                # from the main processing pipeline.  Prevents re-adding LSZ-007/008
                # on every results fetch — which was causing count inflation.
                _has_dup_findings = _PIDVFinding.objects.filter(
                    drawing=_drawing,
                    rule_id__in=['LSZ-006', 'LSZ-007', 'LSZ-008', 'LSZ-009'],
                ).exists()
                if _has_dup_findings:
                    continue  # main pipeline already generated these; skip to avoid duplication

                # Re-extract pipeline tags with the cloud-truncation resolution pass
                _fresh_line_tags = _extract_pipeline_tags_multi_angle(
                    _file_path, _drawing.page_index
                )
                if not _fresh_line_tags:
                    continue

                # Save updated line_tags to metadata
                _meta['line_tags'] = _fresh_line_tags
                _drawing.metadata = _meta
                _drawing.save(update_fields=['metadata'])

                # Generate missing duplicate findings from the fresh line_tags data
                if not _has_lsz009:
                    _new_rfs = _check_pipeline_tag_duplicates({'line_tags': _fresh_line_tags})
                    _new_rfs = [rf for rf in _new_rfs if rf.rule_id in ('LSZ-006', 'LSZ-007', 'LSZ-008', 'LSZ-009')]
                    if _new_rfs:
                        # Determine next serial number
                        _next_sl = (_PIDVFinding.objects.filter(drawing=_drawing)
                                    .order_by('-sl_no').values_list('sl_no', flat=True).first() or 0) + 1
                        _bulk = [
                            _PIDVFinding(
                                drawing         = _drawing,
                                sl_no           = _next_sl + _i,
                                category        = rf.category,
                                rule_id         = rf.rule_id,
                                issue_observed  = rf.issue_observed,
                                action_required = rf.action_required,
                                evidence        = rf.evidence,
                                direction       = rf.direction,
                                severity        = rf.severity,
                                status          = 'open',
                            )
                            for _i, rf in enumerate(_new_rfs)
                        ]
                        _PIDVFinding.objects.bulk_create(_bulk)
                        logger.info(
                            '[PIDVResults] line_tags backfill: drawing=%s added %d finding(s)',
                            _drawing.drawing_id, len(_bulk)
                        )
        except Exception as _lt_exc:
            logger.debug('[PIDVResults] line_tags backfill skipped: %s', _lt_exc)

    return Response(PIDVDocumentSerializer(doc).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def export_excel(request, document_id):
    doc = _get_doc_or_404(document_id, request.user)
    if doc is None:
        return Response({"error": "Document not found"}, status=status.HTTP_404_NOT_FOUND)

    if doc.status != PIDVDocument.Status.COMPLETED:
        return Response({"error": "Document not yet completed"}, status=status.HTTP_400_BAD_REQUEST)

    # Always regenerate in-memory so the response is served from the same origin
    # (avoids S3 CORS errors that occur when axios follows a cross-origin redirect).
    from .services.export_service import generate_excel
    data = generate_excel(doc)
    if not data:
        return Response({"error": "Excel generation failed"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    safe_name = doc.file_name.rsplit(".", 1)[0].replace(" ", "_")
    response = HttpResponse(
        data,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="pidv_findings_{safe_name}.xlsx"'
    return response


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def export_pdf(request, document_id):
    doc = _get_doc_or_404(document_id, request.user)
    if doc is None:
        return Response({"error": "Document not found"}, status=status.HTTP_404_NOT_FOUND)

    if doc.status != PIDVDocument.Status.COMPLETED:
        return Response({"error": "Document not yet completed"}, status=status.HTTP_400_BAD_REQUEST)

    # Always regenerate in-memory so the response is served from the same origin
    # (avoids S3 CORS errors that occur when axios follows a cross-origin redirect).
    from .services.export_service import generate_pdf
    data = generate_pdf(doc)
    if not data:
        return Response({"error": "PDF generation failed"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    safe_name = doc.file_name.rsplit(".", 1)[0].replace(" ", "_")
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="pidv_report_{safe_name}.pdf"'
    return response


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_documents(request):
    """List user documents; optionally filter by project_id."""
    qs = PIDVDocument.objects.filter(uploaded_by=request.user)

    project_id = request.query_params.get("project_id")
    if project_id:
        qs = qs.filter(project__project_id=project_id)

    qs = qs.order_by("-created_at")[:100]
    return Response(PIDVDocumentListSerializer(qs, many=True).data)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def delete_document(request, document_id):
    doc = _get_doc_or_404(document_id, request.user)
    if doc is None:
        return Response({"error": "Document not found"}, status=status.HTTP_404_NOT_FOUND)
    doc.delete()

    # Soft-hook: keep cross-feature snapshot updated in background.
    try:
        from apps.cross_recommendation.tasks import sync_s3_snapshot
        sync_s3_snapshot.delay()
    except Exception as exc:
        logger.warning("[PIDVDelete] Cross snapshot queue skipped: %s", exc)

    return Response({"message": "Document deleted"}, status=status.HTTP_200_OK)


# ===========================================================================
# Legend Knowledge + Accuracy Comparison
# ===========================================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def legend_knowledge(request):
    """Return currently persisted legend knowledge used by extractor."""
    data = load_legend_knowledge()
    return Response({
        "legend_knowledge": data,
        "knowledge_path": str(LEGEND_KNOWLEDGE_PATH),
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated, HasDisciplineAccess])
@parser_classes([MultiPartParser, FormParser])
def build_legend_knowledge_api(request):
    """
    Build/update legend knowledge from uploaded legend files.
    Accepts multipart file field 'file' or 'files'.
    """
    build_legend_knowledge_api.module_required = 'pid_verification'

    uploaded = []
    if request.FILES.get("file"):
        uploaded.append(request.FILES["file"])
    uploaded.extend(request.FILES.getlist("files"))

    if not uploaded:
        return Response({"error": "No legend file uploaded"}, status=status.HTTP_400_BAD_REQUEST)

    temp_paths = []
    try:
        for f in uploaded:
            suffix = Path(f.name).suffix or ".pdf"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                for chunk in f.chunks():
                    tmp.write(chunk)
                temp_paths.append(tmp.name)

        knowledge = build_legend_knowledge(temp_paths)

        # Keep original source file names for traceability in UI.
        source_names = [f.name for f in uploaded]
        knowledge["sources"] = source_names

        target = save_legend_knowledge(knowledge)
        return Response({
            "message": "Legend knowledge updated",
            "legend_knowledge": knowledge,
            "knowledge_path": str(target),
        }, status=status.HTTP_200_OK)
    finally:
        for p in temp_paths:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Per-project legend endpoints
# ---------------------------------------------------------------------------

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def project_legend(request, project_id):
    """
    Return legend knowledge for a specific project.
    Falls back to the global legend if no per-project legend has been built.
    """
    try:
        project = PIDVProject.objects.get(project_id=project_id)
    except PIDVProject.DoesNotExist:
        return Response({"error": "Project not found"}, status=status.HTTP_404_NOT_FOUND)

    if project.legend_knowledge_data:
        return Response({
            "legend_knowledge": project.legend_knowledge_data,
            "scope": "project",
            "legend_built_at": project.legend_built_at,
        })

    # Fall back to global legend
    global_data = load_legend_knowledge()
    return Response({
        "legend_knowledge": global_data,
        "scope": "global",
        "legend_built_at": None,
    })


@api_view(["POST", "DELETE"])
@permission_classes([IsAuthenticated, HasDisciplineAccess])
@parser_classes([MultiPartParser, FormParser])
def project_legend_build(request, project_id):
    """
    POST: Build/update per-project legend knowledge from an uploaded legend sheet.
          Supports any format that PyMuPDF can open (PDF, PNG, JPEG, TIFF, BMP, etc.).
    DELETE: Clear the per-project legend (revert to global legend).
    """
    project_legend_build.module_required = 'pid_verification'

    try:
        project = PIDVProject.objects.get(project_id=project_id)
    except PIDVProject.DoesNotExist:
        return Response({"error": "Project not found"}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        project.legend_knowledge_data = None
        project.legend_built_at = None
        project.save(update_fields=['legend_knowledge_data', 'legend_built_at', 'updated_at'])
        return Response({"message": "Project legend cleared. Will use global legend."})

    # POST — build legend from uploaded file
    uploaded = []
    if request.FILES.get("file"):
        uploaded.append(request.FILES["file"])
    uploaded.extend(request.FILES.getlist("files"))

    if not uploaded:
        return Response({"error": "No legend file uploaded"}, status=status.HTTP_400_BAD_REQUEST)

    temp_paths = []
    try:
        for f in uploaded:
            suffix = Path(f.name).suffix or ".pdf"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                for chunk in f.chunks():
                    tmp.write(chunk)
                temp_paths.append(tmp.name)

        knowledge = build_legend_knowledge(temp_paths)
        knowledge["sources"] = [f.name for f in uploaded]

        from django.utils import timezone
        project.legend_knowledge_data = knowledge
        project.legend_built_at = timezone.now()
        project.save(update_fields=['legend_knowledge_data', 'legend_built_at', 'updated_at'])

        return Response({
            "message": "Project legend knowledge updated",
            "legend_knowledge": knowledge,
            "legend_built_at": project.legend_built_at,
        }, status=status.HTTP_200_OK)
    finally:
        for p in temp_paths:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass


# ===========================================================================
# LEGEND SHEET API  — upload / list / detail
# ===========================================================================

@api_view(["POST"])
@permission_classes([IsAuthenticated, HasDisciplineAccess])
@parser_classes([MultiPartParser, FormParser])
def upload_legend_sheet(request, project_id):
    """
    POST /api/v1/pid-verification/projects/<project_id>/legend-sheets/upload/

    Upload one or more legend sheet files (PDF / PNG / JPEG / TIFF).
    Each file is saved to the configured media/S3 storage and queued for
    AI extraction via extract_legend_sheet_task.

    Form fields:
      file  / files — multipart file upload(s)
    """
    upload_legend_sheet.module_required = 'pid_verification'

    project = _get_project_or_404(project_id, request.user)
    if project is None:
        return Response({'error': 'Project not found'}, status=status.HTTP_404_NOT_FOUND)

    uploaded_files = []
    if request.FILES.get('file'):
        uploaded_files.append(request.FILES['file'])
    uploaded_files.extend(request.FILES.getlist('files'))

    if not uploaded_files:
        return Response({'error': 'No file uploaded'}, status=status.HTTP_400_BAD_REQUEST)

    # Soft-coded: allowed legend file extensions
    ALLOWED_EXTS = {'.pdf', '.png', '.jpg', '.jpeg', '.tiff', '.tif', '.bmp'}

    created_sheets = []
    for f in uploaded_files:
        ext = Path(f.name).suffix.lower()
        if ext not in ALLOWED_EXTS:
            return Response(
                {'error': f'Unsupported file type: {ext}. Allowed: {", ".join(ALLOWED_EXTS)}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        sheet = PIDVLegendSheet.objects.create(
            project      = project,
            file_name    = f.name,
            original_file= f,
            status       = PIDVLegendSheet.Status.PENDING,
            uploaded_by  = request.user,
        )
        created_sheets.append(sheet)

    # Queue extraction tasks — each sheet processed independently
    from .tasks import extract_legend_sheet_task
    for sheet in created_sheets:
        try:
            extract_legend_sheet_task.delay(str(sheet.legend_id))
            logger.info('[LegendUpload] Queued extraction for legend_id=%s', sheet.legend_id)
        except Exception as exc:
            # Worker unavailable — run synchronously in bg thread
            import threading
            threading.Thread(
                target=extract_legend_sheet_task,
                args=(str(sheet.legend_id),),
                daemon=True,
            ).start()
            logger.warning('[LegendUpload] Queue unavailable (%s) — running sync thread', exc)

    result = [
        {
            'legend_id':  str(s.legend_id),
            'file_name':  s.file_name,
            'status':     s.status,
            'created_at': s.created_at,
        }
        for s in created_sheets
    ]
    return Response({'uploaded': result}, status=status.HTTP_201_CREATED)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def project_legend_sheets(request, project_id):
    """
    GET /api/v1/pid-verification/projects/<project_id>/legend-sheets/

    List all legend sheets for a project with their current extraction status
    and summary of extracted categories.
    """
    project = _get_project_or_404(project_id, request.user)
    if project is None:
        return Response({'error': 'Project not found'}, status=status.HTTP_404_NOT_FOUND)

    sheets = PIDVLegendSheet.objects.filter(project=project).order_by('-created_at')
    data = []
    for s in sheets:
        # Build category summary so front-end can show badges without the full blob
        category_counts = {}
        if s.extracted_data:
            for cat in (
                'line_representation', 'line_numbering_piping', 'line_numbering_pipeline',
                'abbreviations_process', 'inline_equipment', 'service_codes',
                'insulation_codes', 'piping_specs', 'instrument_prefixes', 'valve_prefixes',
            ):
                val = s.extracted_data.get(cat)
                if isinstance(val, list):
                    category_counts[cat] = len(val)
                elif isinstance(val, dict):
                    category_counts[cat] = len(val)
                else:
                    category_counts[cat] = 0

            # pid_symbols is the catch-all field for all P&ID visual symbols
            pid_sym_val = s.extracted_data.get('pid_symbols')
            category_counts['pid_symbols'] = len(pid_sym_val) if isinstance(pid_sym_val, list) else 0

        # Count AI-registered instrument symbols (stored in PIDVInstrumentSymbol table)
        from apps.pid_verification.models import PIDVInstrumentSymbol
        instrument_symbols_count = PIDVInstrumentSymbol.objects.filter(legend_sheet=s).count()

        total_symbols = sum(category_counts.values()) + instrument_symbols_count

        data.append({
            'legend_id':              str(s.legend_id),
            'file_name':              s.file_name,
            'status':                 s.status,
            'error_message':          s.error_message or None,
            'extraction_method':      (s.extracted_data or {}).get('extraction_method'),
            'category_counts':        category_counts,
            'instrument_symbols_count': instrument_symbols_count,
            'total_symbols':          total_symbols,
            'uploaded_by':            getattr(s.uploaded_by, 'email', None),
            'created_at':             s.created_at,
            'updated_at':             s.updated_at,
        })

    return Response({'legend_sheets': data, 'total': len(data)})


@api_view(["GET", "DELETE"])
@permission_classes([IsAuthenticated])
def legend_sheet_detail(request, legend_id):
    """
    GET    /api/v1/pid-verification/legend-sheets/<legend_id>/
      — Returns full extracted_data for this sheet.

    DELETE /api/v1/pid-verification/legend-sheets/<legend_id>/
      — Removes the sheet record.  Does NOT update project legend_knowledge_data
        since other sheets may still have contributed to it.
    """
    try:
        sheet = PIDVLegendSheet.objects.get(legend_id=legend_id)
    except PIDVLegendSheet.DoesNotExist:
        return Response({'error': 'Legend sheet not found'}, status=status.HTTP_404_NOT_FOUND)

    # Ownership: either the uploader or staff
    user_obj = getattr(request.user, 'user', request.user)
    if sheet.uploaded_by != request.user and not getattr(user_obj, 'is_staff', False):
        return Response({'error': 'Not authorised'}, status=status.HTTP_403_FORBIDDEN)

    if request.method == 'DELETE':
        sheet.delete()
        return Response({'message': 'Legend sheet deleted'})

    # GET — full detail
    return Response({
        'legend_id':         str(sheet.legend_id),
        'file_name':         sheet.file_name,
        'status':            sheet.status,
        'error_message':     sheet.error_message or None,
        'extracted_data':    sheet.extracted_data,
        'uploaded_by':       getattr(sheet.uploaded_by, 'email', None),
        'created_at':        sheet.created_at,
        'updated_at':        sheet.updated_at,
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def retry_legend_extraction(request, legend_id):
    """
    POST /api/v1/pid-verification/legend-sheets/<legend_id>/retry/

    Re-queue extraction for a failed or completed sheet.
    """
    try:
        sheet = PIDVLegendSheet.objects.get(legend_id=legend_id)
    except PIDVLegendSheet.DoesNotExist:
        return Response({'error': 'Legend sheet not found'}, status=status.HTTP_404_NOT_FOUND)

    user_obj = getattr(request.user, 'user', request.user)
    if sheet.uploaded_by != request.user and not getattr(user_obj, 'is_staff', False):
        return Response({'error': 'Not authorised'}, status=status.HTTP_403_FORBIDDEN)

    sheet.status        = PIDVLegendSheet.Status.PENDING
    sheet.error_message = ''
    sheet.save(update_fields=['status', 'error_message', 'updated_at'])

    from .tasks import extract_legend_sheet_task
    try:
        extract_legend_sheet_task.delay(str(sheet.legend_id))
    except Exception as exc:
        import threading
        threading.Thread(target=extract_legend_sheet_task, args=(str(sheet.legend_id),), daemon=True).start()
        logger.warning('[LegendRetry] Queue unavailable (%s) — running sync thread', exc)

    return Response({'message': 'Extraction re-queued', 'legend_id': str(sheet.legend_id)})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def compare_accuracy(request, document_id):
    doc = _get_doc_or_404(document_id, request.user)
    if doc is None:
        return Response({"error": "Document not found"}, status=status.HTTP_404_NOT_FOUND)

    try:
        from .tasks import process_pid_document
        from .services import extraction as ex
        from .models import PIDVFinding
    except Exception as exc:
        return Response({"error": f"Failed to initialize comparison services: {exc}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # Ensure findings are current before comparison.
    process_pid_document.apply(args=[str(doc.document_id)])
    doc.refresh_from_db()

    raw_text = ex._run_ocr(doc.original_file.path, 0)

    default_ins = set(ex._DEFAULT_INSTRUMENT_PREFIXES)
    default_val = set(ex._DEFAULT_VALVE_PREFIXES)

    def _extract_with_prefixes(text, ins_prefixes, val_prefixes):
        tags = ex._extract_tags(text)
        instruments = []
        valves = []
        for t in tags:
            p = t.split('-')[0]
            if p in ins_prefixes:
                instruments.append({"tag": t, "type": p})
            if p in val_prefixes:
                valves.append({"tag": t, "type": p, "connected": None})
        return {
            "tags": tags,
            "instruments": instruments,
            "valves": valves,
            "equipment": ex._extract_equipment(text),
            "notes": ex._extract_notes(text),
            "holds": ex._extract_holds(text),
            "line_sizes": ex._extract_line_sizes(text),
        }

    before = _extract_with_prefixes(raw_text, default_ins, default_val)

    ex._legend_prefixes.cache_clear()
    after = ex.extract_drawing(doc.original_file.path, 0)
    legend_data = load_legend_knowledge()

    findings = PIDVFinding.objects.filter(drawing__document=doc)
    sev_counts = {s: findings.filter(severity=s).count() for s in ["critical", "major", "minor", "info"]}

    before_summary = {
        "tags": len(before["tags"]),
        "instruments": len(before["instruments"]),
        "valves": len(before["valves"]),
        "equipment": len(before["equipment"]),
        "line_sizes": len(before["line_sizes"]),
        "notes": len(before["notes"]),
        "holds": len(before["holds"]),
    }
    after_summary = {
        "tags": len(after["tags"]),
        "instruments": len(after["instruments"]),
        "valves": len(after["valves"]),
        "equipment": len(after["equipment"]),
        "line_sizes": len(after["line_sizes"]),
        "notes": len(after["notes"]),
        "holds": len(after["holds"]),
    }

    def _uniq_sizes(payload):
        return sorted({x.get("text") for x in payload.get("line_sizes", []) if x.get("text")})

    comparison = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "document": {
            "document_id": str(doc.document_id),
            "file_name": doc.file_name,
            "status": doc.status,
            "drawings": doc.drawings.count(),
        },
        "legend_knowledge": {
            "sources": legend_data.get("sources", []),
            "instrument_prefixes": legend_data.get("instrument_prefixes", []),
            "valve_prefixes": legend_data.get("valve_prefixes", []),
        },
        "before_defaults_only": {
            "summary": before_summary,
            "line_sizes_unique": _uniq_sizes(before),
            "tags": before["tags"],
        },
        "after_legend_backed": {
            "summary": after_summary,
            "line_sizes_unique": _uniq_sizes(after),
            "tags": after["tags"],
        },
        "delta_after_minus_before": {
            k: after_summary[k] - before_summary[k]
            for k in sorted(before_summary.keys())
        },
        "report_findings": {
            "total_findings": findings.count(),
            "severity_counts": sev_counts,
        },
    }

    base = LEGEND_KNOWLEDGE_PATH.parent
    base.mkdir(parents=True, exist_ok=True)
    comp_name = f"comparison_{doc.document_id}_legend_backed.json"
    comparison_path = base / comp_name
    comparison_path.write_text(json.dumps(comparison, indent=2), encoding="utf-8")

    records_path = base / "recognition_records.json"
    if records_path.exists():
        try:
            records = json.loads(records_path.read_text(encoding="utf-8"))
            if not isinstance(records, list):
                records = []
        except Exception:
            records = []
    else:
        records = []

    records.append({
        "timestamp_utc": comparison["timestamp_utc"],
        "document_id": str(doc.document_id),
        "file_name": doc.file_name,
        "legend_sources": legend_data.get("sources", []),
        "summary_after_legend": after_summary,
        "line_sizes_unique_after_legend": comparison["after_legend_backed"]["line_sizes_unique"],
        "findings_total": comparison["report_findings"]["total_findings"],
        "findings_severity": sev_counts,
    })
    records_path.write_text(json.dumps(records, indent=2), encoding="utf-8")

    return Response({
        "message": "Comparison completed",
        "comparison": comparison,
        "comparison_file": str(comparison_path),
        "records_file": str(records_path),
    }, status=status.HTTP_200_OK)


# ===========================================================================
# Tag Naming & Acronym Check
# ===========================================================================

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def check_naming(request, document_id):
    """
    POST /api/v1/pid-verification/check-naming/<document_id>/

    Run the Tag Naming & Acronym Check on the first (or requested) page of a
    previously-uploaded P&ID document.

    Optional JSON body:
      { "page_index": 0,  "run_ai": true }

    - page_index  defaults to 0 (first page / drawing).
    - run_ai      defaults to true.  Pass false to run deterministic checks only
                  (instant, no API cost) — useful for quick previews.

    Returns:
      {
        "naming_issues": [ { rule_id, tag_found, issue_type, description,
                              suggested_fix, severity, location_hint, source }, ... ],
        "total":        int,
        "by_severity":  { "major": N, "minor": N, ... },
        "ai_used":      bool,
        "document_id":  str,
        "file_name":    str,
      }
    """
    doc = _get_doc_or_404(document_id, request.user)
    if doc is None:
        return Response({"error": "Document not found"}, status=status.HTTP_404_NOT_FOUND)

    if not doc.original_file:
        return Response(
            {"error": "Original file not stored — re-upload the document"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Parse optional request body
    body       = request.data if isinstance(request.data, dict) else {}
    page_index = int(body.get("page_index", 0))
    run_ai     = bool(body.get("run_ai", True))

    try:
        file_path = doc.original_file.path
    except Exception:
        return Response(
            {"error": "File path unavailable — storage may be remote"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Re-use already-extracted OCR data from the rule engine if available;
    # otherwise run a fresh lightweight OCR pass.
    try:
        from .services import extraction as ex
        extraction_result = ex.extract_drawing(file_path, page_index)
        tags     = extraction_result.get("tags", [])
        raw_text = extraction_result.get("raw_text", "")
    except Exception as exc:
        logger.warning("[NamingCheck] OCR extraction failed (%s) — proceeding with empty text", exc)
        tags     = []
        raw_text = ""

    from .services.naming_check import check_naming_conventions
    result = check_naming_conventions(
        file_path=file_path,
        page_index=page_index,
        tags=tags,
        raw_text=raw_text,
        run_ai=run_ai,
    )

    result["document_id"] = str(doc.document_id)
    result["file_name"]   = doc.file_name
    result["page_index"]  = page_index

    return Response(result, status=status.HTTP_200_OK)


# ===========================================================================
# Engineer Review — finding overrides
# ===========================================================================

@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def update_finding(request, finding_id):
    """
    PATCH /api/v1/pid-verification/findings/<finding_id>/
    Allows the document owner to override severity and/or status of a finding.
    Clears cached S3 export URLs so the next export regenerates from updated DB data.
    """
    try:
        finding = PIDVFinding.objects.select_related('drawing__document').get(pk=finding_id)
    except PIDVFinding.DoesNotExist:
        return Response({"error": "Finding not found"}, status=status.HTTP_404_NOT_FOUND)

    doc = finding.drawing.document
    if doc.uploaded_by != request.user:
        return Response({"error": "Access denied"}, status=status.HTTP_403_FORBIDDEN)

    serializer = PIDVFindingUpdateSerializer(finding, data=request.data, partial=True)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    serializer.save()

    # Clear cached S3 export URLs so next export regenerates with updated findings
    update_fields = ['updated_at'] if hasattr(doc, 'updated_at') else []
    if doc.excel_s3_url:
        doc.excel_s3_url = ''
        update_fields.append('excel_s3_url')
    if doc.pdf_s3_url:
        doc.pdf_s3_url = ''
        update_fields.append('pdf_s3_url')
    if update_fields:
        doc.save(update_fields=update_fields)

    return Response(PIDVFindingSerializer(finding).data)


# ===========================================================================
# Drawing image renderer
# ===========================================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def drawing_image(request, document_id, page_index):
    """
    Render the specified page of an uploaded P&ID document as a PNG image.
    For PDFs  → PyMuPDF rasterises the page at 2× (150 dpi).
    For images → file served directly (or PIL converts to PNG).

    URL: GET /api/v1/pid-verification/drawing-image/<document_id>/<page_index>/
    """
    doc = _get_doc_or_404(document_id, request.user)
    if doc is None:
        return Response({"error": "Document not found"}, status=status.HTTP_404_NOT_FOUND)

    if not doc.original_file:
        return Response({"error": "Original file not stored"}, status=status.HTTP_404_NOT_FOUND)

    try:
        file_path = doc.original_file.path
    except Exception:
        return Response({"error": "File path unavailable"}, status=status.HTTP_404_NOT_FOUND)

    ext = Path(file_path).suffix.lower().lstrip(".")
    png_data = None

    if ext == "pdf":
        try:
            import fitz  # PyMuPDF
            pdf_doc = fitz.open(file_path)
            if page_index >= len(pdf_doc):
                pdf_doc.close()
                return Response({"error": "Page index out of range"}, status=status.HTTP_400_BAD_REQUEST)
            page = pdf_doc[page_index]
            # 2× zoom → ~150 dpi for typical A1 drawings
            mat = fitz.Matrix(2.0, 2.0)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            png_data = pix.tobytes("png")
            pdf_doc.close()
        except ImportError:
            return Response({"error": "PyMuPDF not available"}, status=status.HTTP_501_NOT_IMPLEMENTED)
        except Exception as exc:
            logger.warning("[PIDVDrawingImage] PDF render failed: %s", exc)
            return Response({"error": "Failed to render PDF page"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    elif ext in {"png", "jpg", "jpeg", "tiff", "tif"}:
        try:
            with open(file_path, "rb") as f:
                raw = f.read()
            if ext == "png":
                png_data = raw
            else:
                from PIL import Image
                import io
                img = Image.open(io.BytesIO(raw)).convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                png_data = buf.getvalue()
        except Exception as exc:
            logger.warning("[PIDVDrawingImage] Image read failed: %s", exc)
            return Response({"error": "Failed to read image file"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    else:
        return Response({"error": f"Unsupported file type: {ext}"}, status=status.HTTP_400_BAD_REQUEST)

    response = HttpResponse(png_data, content_type="image/png")
    response["Cache-Control"] = "private, max-age=3600"
    response["Content-Length"] = len(png_data)
    return response


# ===========================================================================
# Helpers
# ===========================================================================

# ===========================================================================
# Instrument Symbol Registry Views
# ===========================================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def project_instrument_symbols(request, project_id):
    """
    GET /api/v1/pid-verification/projects/<project_id>/instrument-symbols/

    Query params:
      category — filter by one of: control_valve, manual_valve, instrument,
                  instrument_tagging, equipment_numbering, inline_equipment
      search   — substring match on symbol_code or description (case-insensitive)

    Returns:
      { symbols: [...], total: int, category_counts: { cat: count, ... } }
    """
    from apps.pid_verification.models import PIDVInstrumentSymbol
    from apps.pid_verification.services.instrument_registry import get_category_counts

    project = _get_project_or_404(project_id, request.user)
    if project is None:
        return Response({'error': 'Project not found'}, status=status.HTTP_404_NOT_FOUND)

    qs = PIDVInstrumentSymbol.objects.filter(project=project)

    category = request.query_params.get('category', '').strip()
    if category:
        qs = qs.filter(category=category)

    search = request.query_params.get('search', '').strip()
    if search:
        from django.db.models import Q
        qs = qs.filter(Q(symbol_code__icontains=search) | Q(description__icontains=search))

    symbols = []
    for sym in qs.select_related('legend_sheet'):
        symbols.append({
            'symbol_id':        str(sym.symbol_id),
            'symbol_code':      sym.symbol_code,
            'description':      sym.description,
            'category':         sym.category,
            'symbol_type':      sym.symbol_type,
            'drawing_standard': sym.drawing_standard,
            'attributes':       sym.attributes,
            'source':           sym.source,
            'legend_sheet_id':  str(sym.legend_sheet.legend_id) if sym.legend_sheet else None,
            'created_at':       sym.created_at,
            'updated_at':       sym.updated_at,
        })

    return Response({
        'symbols':         symbols,
        'total':           len(symbols),
        'category_counts': get_category_counts(project),
    })


@api_view(["GET", "DELETE"])
@permission_classes([IsAuthenticated])
def instrument_symbol_detail(request, symbol_id):
    """
    GET    /api/v1/pid-verification/instrument-symbols/<symbol_id>/
      — Full detail of a single symbol.

    DELETE /api/v1/pid-verification/instrument-symbols/<symbol_id>/
      — Remove a single symbol record.
    """
    from apps.pid_verification.models import PIDVInstrumentSymbol

    try:
        sym = PIDVInstrumentSymbol.objects.select_related('project', 'legend_sheet').get(symbol_id=symbol_id)
    except PIDVInstrumentSymbol.DoesNotExist:
        return Response({'error': 'Symbol not found'}, status=status.HTTP_404_NOT_FOUND)

    # Ownership check: project owner or staff
    project   = sym.project
    user      = request.user
    user_obj  = getattr(user, 'user', user)
    if project.created_by != user and not getattr(user_obj, 'is_staff', False):
        return Response({'error': 'Not authorised'}, status=status.HTTP_403_FORBIDDEN)

    if request.method == 'DELETE':
        sym.delete()
        return Response({'message': 'Symbol deleted'})

    return Response({
        'symbol_id':        str(sym.symbol_id),
        'symbol_code':      sym.symbol_code,
        'description':      sym.description,
        'category':         sym.category,
        'symbol_type':      sym.symbol_type,
        'drawing_standard': sym.drawing_standard,
        'attributes':       sym.attributes,
        'source':           sym.source,
        'project_id':       str(sym.project.project_id),
        'legend_sheet_id':  str(sym.legend_sheet.legend_id) if sym.legend_sheet else None,
        'created_at':       sym.created_at,
        'updated_at':       sym.updated_at,
    })


# ===========================================================================
# DCS / Instrument Symbol Compliance Analysis
# ===========================================================================

@api_view(["POST"])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def analyze_dcs(request, document_id):
    """
    POST /api/v1/pid-verification/analyze-dcs/<document_id>/

    Two-stage AI analysis (Gemini + OpenAI):
      Stage 1 — Extract 48 instrument symbols from optional legend PDF.
      Stage 2 — Analyze P&ID drawing against extracted symbols, generate findings.

    Multipart form fields:
      legend_file  (optional) : PDF legend/instrument-symbols sheet
      drawing_index (optional): 0-based drawing index to analyze (default: 0)
      replace_existing (optional): "true" to remove previous DCS findings first

    Returns:
      {
        "findings_created": <n>,
        "symbols_extracted": <n>,
        "legend_source": "uploaded | default_isa",
        "findings": [ { category, severity, tag, issue_observed, ... } ]
      }
    """
    doc = _get_doc_or_404(document_id, request.user)
    if doc is None:
        return Response({'error': 'Document not found'}, status=status.HTTP_404_NOT_FOUND)

    if doc.status != PIDVDocument.Status.COMPLETED:
        return Response(
            {'error': f'Document not yet processed (status={doc.status}). Run analysis first.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Resolve which drawing to analyze
    drawing_index  = int(request.data.get('drawing_index', 0))
    replace_flag   = str(request.data.get('replace_existing', 'false')).lower() == 'true'
    legend_file    = request.FILES.get('legend_file')

    drawings = list(doc.drawings.order_by('page_index'))
    if not drawings:
        return Response({'error': 'No drawings found for this document'}, status=status.HTTP_404_NOT_FOUND)

    if drawing_index >= len(drawings):
        drawing_index = 0
    drawing_obj = drawings[drawing_index]

    # Optional: remove previous DCS findings for this drawing only
    if replace_flag:
        drawing_obj.findings.filter(rule_id__startswith='DCS-').delete()
        drawing_obj.findings.filter(rule_id__startswith='BUBBLE-').delete()
        drawing_obj.findings.filter(rule_id__startswith='ISA-').delete()
        drawing_obj.findings.filter(rule_id__startswith='LOOP-').delete()
        drawing_obj.findings.filter(rule_id__startswith='MISSING-').delete()
        # Also remove source=dcs_analysis (used in evidence field as a flag)
        drawing_obj.findings.filter(evidence__startswith='[DCS]').delete()

    # Persist legend file to a temp location if uploaded
    legend_file_path = None
    if legend_file:
        try:
            import tempfile
            suffix = Path(legend_file.name).suffix.lower() or '.pdf'
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            for chunk in legend_file.chunks():
                tmp.write(chunk)
            tmp.close()
            legend_file_path = tmp.name
        except Exception as exc:
            logger.warning('[DCSAnalyze] Failed to save legend tmp file: %s', exc)

    legend_source = 'uploaded' if legend_file_path else 'default_isa'

    # ── Run the two-stage AI analysis ─────────────────────────────────────────
    try:
        from .services.dcs_symbol_analyzer import run_dcs_analysis
        raw_findings = run_dcs_analysis(drawing_obj, legend_file_path=legend_file_path)
    except Exception as exc:
        logger.error('[DCSAnalyze] Analysis failed: %s', exc, exc_info=True)
        return Response({'error': f'Analysis failed: {exc}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    finally:
        # Clean up temp legend file
        if legend_file_path:
            try:
                Path(legend_file_path).unlink(missing_ok=True)
            except Exception:
                pass

    if not raw_findings:
        return Response({
            'findings_created': 0,
            'symbols_extracted': 0,
            'legend_source': legend_source,
            'findings': [],
            'message': 'No DCS/instrument findings detected by AI analysis.',
        })

    # ── Determine next sl_no ──────────────────────────────────────────────────
    max_sl = drawing_obj.findings.aggregate(m=models.Max('sl_no'))['m'] or 0

    # ── Bulk create findings ──────────────────────────────────────────────────
    dcs_cat_remap = {
        'dcs':                 PIDVFinding.Category.TAG,
        'bubble_symbol':       PIDVFinding.Category.TAG,
        'isa_function_letter': PIDVFinding.Category.TAG,
        'loop_consistency':    PIDVFinding.Category.CONNECTIVITY,
        'missing_instrument':  PIDVFinding.Category.TAG,
        'tag':                 PIDVFinding.Category.TAG,
        'connectivity':        PIDVFinding.Category.CONNECTIVITY,
        'valve':               PIDVFinding.Category.VALVE,
        'line_size':           PIDVFinding.Category.LINE_SIZE,
        'notes':               PIDVFinding.Category.NOTES,
    }
    sev_remap = {
        'critical': PIDVFinding.Severity.CRITICAL,
        'major':    PIDVFinding.Severity.MAJOR,
        'minor':    PIDVFinding.Severity.MINOR,
        'info':     PIDVFinding.Severity.INFO,
    }

    bulk = []
    for i, f in enumerate(raw_findings, start=1):
        cat = dcs_cat_remap.get(f.get('category', 'tag'), PIDVFinding.Category.TAG)
        sev = sev_remap.get(f.get('severity', 'major'), PIDVFinding.Severity.MAJOR)
        # Mark evidence with [DCS] prefix so we can later filter these findings
        evidence = f'[DCS] {f.get("evidence", "")}'.strip()[:500]
        bulk.append(PIDVFinding(
            drawing=drawing_obj,
            sl_no=max_sl + i,
            category=cat,
            rule_id=f.get('rule_id', 'DCS-000')[:50],
            issue_observed=f.get('issue_observed', '')[:500],
            action_required=f.get('action_required', 'Review and update drawing')[:500],
            evidence=evidence,
            direction=f.get('direction', 'N/A')[:100],
            severity=sev,
            status=PIDVFinding.FindingStatus.OPEN,
        ))

    PIDVFinding.objects.bulk_create(bulk)
    logger.info('[DCSAnalyze] Created %d DCS findings for drawing=%s', len(bulk), drawing_obj.drawing_id)

    return Response({
        'findings_created': len(bulk),
        'symbols_extracted': len(_ISA_DEFAULT_SYMBOLS_COUNT),
        'legend_source': legend_source,
        'findings': [
            {
                'sl_no':            max_sl + i + 1,
                'category':         f.get('category'),
                'severity':         f.get('severity'),
                'tag':              f.get('tag'),
                'issue_observed':   f.get('issue_observed'),
                'action_required':  f.get('action_required'),
                'evidence':         f.get('evidence'),
                'rule_id':          f.get('rule_id'),
            }
            for i, f in enumerate(raw_findings)
        ],
    })


# Soft-coded: count of the built-in ISA default symbol table for reporting
_ISA_DEFAULT_SYMBOLS_COUNT = list(range(44))   # 44 entries in _ISA_DEFAULT_SYMBOLS


# ===========================================================================

def _get_doc_or_404(document_id: str, user):
    try:
        doc = PIDVDocument.objects.get(document_id=document_id)
        user_obj = getattr(user, "user", user)
        if doc.uploaded_by == user or getattr(user_obj, "is_staff", False):
            return doc
        return None
    except PIDVDocument.DoesNotExist:
        return None


def _get_project_or_404(project_id: str, user):
    try:
        project = PIDVProject.objects.get(project_id=project_id)
        user_obj = getattr(user, "user", user)
        if project.created_by == user or getattr(user_obj, "is_staff", False):
            return project
        return None
    except PIDVProject.DoesNotExist:
        return None
