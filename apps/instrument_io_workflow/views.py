"""
DRF views for the Instrument IO List Workflow.

Endpoints
─────────
GET    /api/v1/instrument-io-workflow/config/
POST   /api/v1/instrument-io-workflow/documents/                  (multipart upload)
GET    /api/v1/instrument-io-workflow/documents/
GET    /api/v1/instrument-io-workflow/documents/{id}/
DELETE /api/v1/instrument-io-workflow/documents/{id}/
POST   /api/v1/instrument-io-workflow/documents/{id}/re-extract/
GET    /api/v1/instrument-io-workflow/documents/{id}/original-pdf/
GET    /api/v1/instrument-io-workflow/documents/{id}/export-xlsx/?columns=all|relevant
PATCH  /api/v1/instrument-io-workflow/documents/{id}/rows/{row_id}/
POST   /api/v1/instrument-io-workflow/diff/                       ({old_id, new_id})
POST   /api/v1/instrument-io-workflow/vision/test-key/            ({provider, api_key})

GET    /api/v1/instrument-io-workflow/legends/?section=...
POST   /api/v1/instrument-io-workflow/legends/
GET    /api/v1/instrument-io-workflow/legends/{legend_id}/
PATCH  /api/v1/instrument-io-workflow/legends/{legend_id}/
DELETE /api/v1/instrument-io-workflow/legends/{legend_id}/
POST   /api/v1/instrument-io-workflow/legends/{legend_id}/activate/
POST   /api/v1/instrument-io-workflow/legends/add-lookup/         ({section, code, description})
PUT    /api/v1/instrument-io-workflow/legends/edit-lookup/        ({section, code, description})
DELETE /api/v1/instrument-io-workflow/legends/delete-lookup/      ({section, code})
GET    /api/v1/instrument-io-workflow/legends/lookup-sections/

GET    /api/v1/instrument-io-workflow/symbol-images/
POST   /api/v1/instrument-io-workflow/symbol-image/upload/        (multipart: section, symbol_name, image)
DELETE /api/v1/instrument-io-workflow/symbol-image/delete/        (?section=&symbol_name=)
POST   /api/v1/instrument-io-workflow/default-symbol-images/      ({section, symbol_names})
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.http import FileResponse, HttpResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import status, viewsets, generics
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    IOListProject, IOListDocument, IOListExtractedComment, IOListExtractedRow,
    IOListLegendSheet, IOListLegendSymbolImage,
)
from .serializers import (
    IOListProjectSerializer,
    IOListDocumentListSerializer, IOListDocumentDetailSerializer,
    IOListExtractedRowSerializer,
    IOListLegendSheetSerializer, IOListLegendSymbolImageSerializer,
)
from .services.config import (
    ENABLE_VISION_FALLBACK, ENABLE_HASH_CACHE,
    IO_LIST_CANONICAL_COLUMNS, COMMENT_SHEET_COLUMNS, STATUS_CODE_MEANING,
    CHAIN_DEFAULT_MAX_REVISIONS, CHAIN_RISK_THRESHOLDS,
)
from .services.orchestrator import sha256_of
from .services.results_cache import load_results_cache
from .services.revision_diff import diff_revisions
from .excel_export import export_document_to_xlsx

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Config endpoint — what the frontend needs (soft-coded surface)
# ──────────────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def config_view(request):
    return Response({
        'columns': {
            'comments': COMMENT_SHEET_COLUMNS + ['status_meaning',
                                                  'page_number', 'linked_tags'],
            'io_rows':  IO_LIST_CANONICAL_COLUMNS,
        },
        'status_codes': STATUS_CODE_MEANING,
        'features': {
            'vision_fallback': ENABLE_VISION_FALLBACK,
            'hash_cache':      ENABLE_HASH_CACHE,
            'chain_defaults':  {
                'max_revisions':    CHAIN_DEFAULT_MAX_REVISIONS,
                'risk_thresholds':  CHAIN_RISK_THRESHOLDS,
            },
        },
    })


def _page_count(pdf_bytes: bytes) -> int:
    import fitz
    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    try:
        return doc.page_count
    finally:
        doc.close()


# ──────────────────────────────────────────────────────────────────────
# Main viewset — documents (one PDF per row)
# ──────────────────────────────────────────────────────────────────────
class IOListDocumentViewSet(viewsets.ModelViewSet):
    queryset = IOListDocument.objects.all()
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_serializer_class(self):
        if self.action in ('list',):
            return IOListDocumentListSerializer
        return IOListDocumentDetailSerializer

    # ---- list filters ------------------------------------------------
    def get_queryset(self):
        qs = super().get_queryset()
        chain_id = self.request.query_params.get('crs_chain_id')
        doc_no   = self.request.query_params.get('document_number')
        if chain_id:
            qs = qs.filter(crs_chain_id=chain_id)
        if doc_no:
            qs = qs.filter(document_number=doc_no)
        return qs

    # ---- retrieve — check the S3/local results cache first ------------
    def retrieve(self, request, *args, **kwargs):
        """Fast path for VIEWING an already-completed document: one cache
        read instead of the extracted_rows/extracted_comments DB queries
        the normal serializer path would otherwise run. NEVER consulted
        before running an extraction — create()/re_extract() only ever
        WRITE to this cache; this is the one and only READ site. Falls
        back to the normal DB-backed response on ANY cache miss (no
        entry yet, corrupt, or stale — see load_results_cache), so a
        cache problem can never make a document un-viewable."""
        document = self.get_object()
        if document.status == 'completed':
            cached = load_results_cache(document)
            if cached:
                logger.info(
                    "[IOWF] retrieve(): results cache HIT for document %s "
                    "(scan_mode=%r) — serving instantly, skipping "
                    "extracted_rows/comments DB queries",
                    document.id, cached.get('scan_mode'),
                )
                base = IOListDocumentListSerializer(document).data
                try:
                    pdf_url = document.pdf_file.url
                except Exception:
                    pdf_url = None
                base.update({
                    'pdf_url': pdf_url,
                    'extraction_error': document.extraction_error,
                    'extracted_rows': cached.get('extracted_rows', []),
                    'extracted_comments': cached.get('extracted_comments', []),
                })
                return Response(base)
            logger.info(
                "[IOWF] retrieve(): results cache MISS for document %s — "
                "using the normal DB-backed response",
                document.id,
            )
        return super().retrieve(request, *args, **kwargs)

    # ---- lightweight live-progress poll --------------------------------
    @action(detail=True, methods=['get'], url_path='status')
    def status_view(self, request, pk=None):
        """GET /documents/{id}/status/ — a deliberately small, fast poll
        target (no nested extracted_rows/extracted_comments serialization
        — see IOListDocumentDetailSerializer) for the frontend's ~2s
        progress polling loop while a document is 'extracting'.

        Every number here is read live off the actual DB row — nothing
        estimated, animated, or simulated:
          - pages_processed / pages_total: incremented by tasks.py as
            each parallel page/tile-group subtask genuinely finishes.
          - current_rows / current_comments: document.provisional_rows_
            count / provisional_comments_count — how many rows/comments
            THIS run has genuinely persisted so far (tasks.py's
            _persist_provisional_rows_and_comments, called by every
            per-page task as it finishes), reset to 0 at dispatch.
            Deliberately NOT extracted_rows.count()/extracted_comments.
            count() — on a re-extract those still hold the PREVIOUS run's
            rows until persist_extraction wipes and replaces them at the
            very end, so a raw count would show a real but misleading
            number mid-run (confirmed live: 46 -> 66 -> 46 across one
            re-extract). These two fields stay accurate for upload and
            re-extract alike.
          - current_phase: written by tasks.py/orchestrator.py at the
            exact moment each stage actually starts — 'detecting_type',
            'processing_pages', 'linking_comments', 'validating_legend',
            or '' once processing isn't running (see IOListDocument.
            PHASE_CHOICES). Never inferred or guessed here — this
            endpoint only ever echoes back whatever the backend already
            wrote for real.
          - vision_calls_done / vision_calls_total: finer-grained than
            pages_processed/pages_total for the P&ID Vision path — one
            whole page can be several Vision API calls (VISION_PASSES, or
            every tile x pass in Thorough Scan), during which
            pages_processed alone would sit frozen for minutes. Real,
            atomic per-call increments (tasks.py's process_pid_vision_page
            via pid_vision_extractor's on_call_complete). Both 0 for a
            regular I/O List table document or the no-key local-OCR
            fallback — frontend falls back to the page-level numbers when
            vision_calls_total is 0.
          - tokens_used_total: real, cumulative token count summed across
            every Vision API call made so far — straight off each
            response's own usage object (pid_vision_extractor's
            _call_claude/_call_openai), never estimated. 0 for a regular
            I/O List table document or the no-key local-OCR fallback.
          - extraction_started_at: real wall-clock timestamp set once, at
            dispatch, by create()/re_extract() below — never touched again
            during the run (tasks.py only ever uses .update(), which never
            includes this field). Lets the frontend show genuine elapsed
            time / derive an ETA from real deltas, not a client-side fake
            timer.
        """
        document = self.get_object()
        return Response({
            'id': document.id,
            'status': document.status,
            'document_type': document.document_type,
            'current_phase': document.current_phase,
            'pages_processed': document.pages_processed,
            'pages_total': document.pages_total,
            'vision_calls_done': document.vision_calls_done,
            'vision_calls_total': document.vision_calls_total,
            'tokens_used_total': document.tokens_used_total,
            'current_rows': document.provisional_rows_count,
            'current_comments': document.provisional_comments_count,
            'extraction_error': document.extraction_error,
            'extraction_started_at': document.extraction_started_at,
        })

    # ---- create + extract — ALWAYS async (Celery chord, see tasks.py's
    # module docstring), regardless of page count, so every upload gets
    # real per-page progress and a guaranteed auto-open on completion,
    # not just documents over the old page-fanout threshold. ------------
    def create(self, request, *args, **kwargs):
        pdf = request.FILES.get('pdf_file') or request.FILES.get('file')
        if not pdf:
            return Response(
                {'error': 'pdf_file is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        pdf_bytes = pdf.read()
        pdf.seek(0)
        digest = sha256_of(pdf_bytes)

        # Project is REQUIRED — there is no more "Unassigned" bucket.
        # UploadCard always sends 'project' in the multipart body (it's
        # only reachable after selecting/creating one — see
        # IOListWorkflowPage.jsx's own routing), but this is the actual
        # enforcement point: a request with no project id, or one that
        # doesn't resolve to one of THIS user's own projects, is rejected
        # outright rather than silently falling back to project=None.
        project_id = request.data.get('project') or None
        if not project_id:
            return Response(
                {'error': 'Project is required. Please select a project first.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        project = IOListProject.objects.filter(
            pk=project_id, created_by=request.user,
        ).first()
        if not project:
            return Response(
                {'error': 'Project is required. Please select a project first.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        vision_provider = request.data.get('vision_provider') or None
        vision_api_key  = request.data.get('vision_api_key') or None
        thorough        = str(request.data.get('thorough', '')).lower() in ('1', 'true', 'yes')
        logger.info(
            "[IOWF] create(): thorough=%r vision_provider=%r key supplied=%s "
            "— Upload & Extract always runs fresh (no cache consulted)",
            thorough, vision_provider, bool(vision_api_key),
        )

        # NO hash-cache lookup here, deliberately — "Upload & Extract" must
        # ALWAYS run a genuinely fresh extraction, every time, no
        # exceptions. A hash-based cache here caused confirmed bugs: a
        # new/fresh document silently returning ANOTHER document's
        # already-completed result whenever the same file bytes had been
        # seen before (even with a newly-supplied Vision key, or a
        # different Quick/Thorough Scan mode than the cached run used) —
        # neither `project`, `vision_api_key`, nor `thorough` were ever
        # part of the cache key, so any one of them changing between two
        # uploads of the same file was silently ignored.
        #
        # Neither this action nor re_extract() (below) ever caches —
        # services/orchestrator.py's extract_document() no longer has any
        # cache of its own either (the old per-worker _MEMO dict was
        # removed outright, not just bypassed). Caching only ever makes
        # sense for VIEWING an already-completed document, which is a
        # separate, plain DB read (this ViewSet's retrieve()/list(), via
        # the serializers) that never calls extract_document() at all.
        document = IOListDocument.objects.create(
            project_name=request.data.get('project_name', '') or '',
            document_number=request.data.get('document_number', '') or '',
            revision_label=request.data.get('revision_label', '') or '',
            plant=request.data.get('plant', '') or '',
            unit=request.data.get('unit', '') or '',
            crs_chain_id=request.data.get('crs_chain_id', '') or '',
            project=project,
            pdf_file=pdf,
            pdf_sha256=digest,
            status='extracting',
            extraction_started_at=timezone.now(),
            uploaded_by=request.user if request.user.is_authenticated else None,
        )

        # BUG FIX: this used to gate on page_count (and later, also a
        # supplied Vision key) before deciding sync vs async. Now ALWAYS
        # async, unconditionally — a real, explicit requirement: "whether
        # 1 page or 100 pages, the user should always see progress, never
        # a blank waiting screen." The synchronous path had no way to
        # offer that (the whole HTTP response only arrives once
        # everything is already done), and the frontend's progress
        # banner / per-page percentage / auto-open-on-completion
        # (IOListWorkflowPage.jsx) all depend on the async/202 path's
        # pages_processed/pages_total polling. A 1-page document still
        # goes through the exact same chord dispatch — it just finishes
        # its one subtask almost immediately, which the poll picks up on
        # its next 2s tick. page_count is no longer read here at all —
        # process_io_document (tasks.py) classifies pages itself.
        # BUG FIX (superseded — see dispatch_io_document_processing's own
        # docstring in tasks.py): this used to call process_io_document
        # .delay(...) directly here. In EAGER-mode dev (no Redis/broker —
        # see CELERY_TASK_ALWAYS_EAGER in config/settings.py), that runs
        # the ENTIRE chord (every page task + finalize_io_document)
        # synchronously, inline, in THIS request thread, before .delay()
        # even returns. For a small test document that finishes in
        # seconds it was easy to miss; for a real P&ID with BYOK Vision
        # (many per-page API calls) it meant the axios POST itself hung
        # for 20+ minutes — confirmed live — with no response yet to
        # start the frontend's poll loop from. dispatch_io_document_
        # processing() now runs that same .delay() call on a background
        # thread whenever EAGER mode is active, so this request can
        # refresh/return 202 right away and the poll loop takes over from
        # there. In a real deployment (genuine broker + worker) it just
        # calls .delay() directly, unchanged.
        from .tasks import dispatch_io_document_processing
        dispatch_io_document_processing(
            str(document.id), vision_provider, vision_api_key, thorough,
        )
        document.refresh_from_db()
        ser = self.get_serializer(document)
        already_done = document.status in ('completed', 'failed')
        return Response(
            {'cached': False, 'document': ser.data},
            status=status.HTTP_201_CREATED if already_done else status.HTTP_202_ACCEPTED,
        )

    # ---- re-run extraction on an existing PDF ------------------------
    @action(detail=True, methods=['post'], url_path='re-extract')
    def re_extract(self, request, pk=None):
        """Always runs a genuinely fresh extraction — no cache lookup of
        any kind, same as create() above — and, like create(), now
        ALWAYS dispatches async (Celery chord) regardless of page count,
        so a re-extract gets the exact same real per-page progress and
        guaranteed auto-open-on-completion as a fresh upload, not a
        blocking wait with no feedback."""
        document = self.get_object()
        try:
            with document.pdf_file.open('rb'):
                pass  # just confirms the file is actually readable
        except Exception as exc:
            return Response(
                {'error': 'Could not read PDF', 'detail': str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        vision_provider = request.data.get('vision_provider') or None
        vision_api_key  = request.data.get('vision_api_key') or None
        thorough        = str(request.data.get('thorough', '')).lower() in ('1', 'true', 'yes')

        document.status = 'extracting'
        document.extraction_started_at = timezone.now()
        document.save(update_fields=['status', 'extraction_started_at', 'updated_at'])

        # Same EAGER-mode-dev background-thread fix as create() above —
        # see dispatch_io_document_processing's docstring in tasks.py.
        from .tasks import dispatch_io_document_processing
        dispatch_io_document_processing(
            str(document.id), vision_provider, vision_api_key, thorough,
        )
        document.refresh_from_db()
        ser = self.get_serializer(document)
        already_done = document.status in ('completed', 'failed')
        return Response(
            {'cached': False, 'document': ser.data},
            status=status.HTTP_201_CREATED if already_done else status.HTTP_202_ACCEPTED,
        )

    # ---- stream original PDF for an authenticated in-app preview ----
    @action(detail=True, methods=['get'], url_path='original-pdf')
    def original_pdf(self, request, pk=None):
        document = self.get_object()
        if not document.pdf_file:
            raise Http404('Original PDF not found')
        try:
            pdf = document.pdf_file.open('rb')
        except (FileNotFoundError, OSError, ValueError) as exc:
            logger.warning(
                '[IOWF] Original PDF unavailable for doc %s: %s',
                document.id,
                exc,
            )
            raise Http404('Original PDF not found') from exc

        filename = document.pdf_file.name.rsplit('/', 1)[-1]
        return FileResponse(
            pdf,
            content_type='application/pdf',
            as_attachment=False,
            filename=filename,
        )

    # ---- patch a single extracted row --------------------------------
    @action(
        detail=True,
        methods=['patch'],
        url_path=r'rows/(?P<row_pk>[^/.]+)',
        url_name='patch-row',
    )
    def patch_row(self, request, pk=None, row_pk=None):
        """
        PATCH /documents/{doc_id}/rows/{row_id}/

        Body: flat dict — any subset of column keys.
        Special top-level model fields: 'tag_number', 'page_number'.
        All other keys are merged (not replaced) into the row.data JSONField.
        """
        document = self.get_object()
        try:
            row = document.extracted_rows.get(pk=row_pk)
        except IOListExtractedRow.DoesNotExist:
            raise Http404('Row not found')

        payload = request.data
        update_fields = []

        if 'tag_number' in payload:
            row.tag_number = str(payload['tag_number'])
            update_fields.append('tag_number')

        if 'page_number' in payload:
            try:
                row.page_number = int(payload['page_number'])
            except (ValueError, TypeError):
                row.page_number = None
            update_fields.append('page_number')

        # All remaining keys are merged into the data JSONField
        data_updates = {
            k: v for k, v in payload.items()
            if k not in ('tag_number', 'page_number')
        }
        if data_updates:
            row.data = {**(row.data or {}), **data_updates}
            update_fields.append('data')

        if update_fields:
            row.save(update_fields=update_fields)

        return Response(IOListExtractedRowSerializer(row).data)

    # ---- xlsx export -------------------------------------------------
    @action(detail=True, methods=['get'], url_path='export-xlsx')
    def export_xlsx(self, request, pk=None):
        document = self.get_object()
        if document.status != 'completed':
            return Response(
                {'error': 'Document extraction is not complete'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        columns = request.query_params.get('columns', 'all')
        data = export_document_to_xlsx(document, columns=columns)
        filename = (
            f'IOList_{document.document_number or document.id}_'
            f'{document.revision_label or "rev"}.xlsx'
        )
        resp = HttpResponse(
            data,
            content_type=('application/vnd.openxmlformats-'
                          'officedocument.spreadsheetml.sheet'),
        )
        resp['Content-Disposition'] = f'attachment; filename="{filename}"'
        return resp


# ──────────────────────────────────────────────────────────────────────
class IOListProjectViewSet(viewsets.ModelViewSet):
    """
    ViewSet for I/O List Project management.

    Endpoints:
      GET    /api/v1/instrument-io-workflow/projects/          — list all projects
      POST   /api/v1/instrument-io-workflow/projects/          — create new project
      GET    /api/v1/instrument-io-workflow/projects/{id}/     — retrieve project
      PUT    /api/v1/instrument-io-workflow/projects/{id}/     — update project
      PATCH  /api/v1/instrument-io-workflow/projects/{id}/     — partial update
      DELETE /api/v1/instrument-io-workflow/projects/{id}/     — delete project
    """

    queryset = IOListProject.objects.all()
    serializer_class = IOListProjectSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Filter projects by current user with soft-coded query params."""
        qs = super().get_queryset().filter(created_by=self.request.user)

        # Soft-coded filtering via query params
        status_filter = self.request.query_params.get('status')
        category = self.request.query_params.get('category')
        search = self.request.query_params.get('search')

        if status_filter:
            qs = qs.filter(status=status_filter)
        if category:
            qs = qs.filter(category=category)
        if search:
            from django.db.models import Q
            qs = qs.filter(
                Q(project_name__icontains=search) |
                Q(project_code__icontains=search) |
                Q(client__icontains=search)
            )

        # Annotate with document count for efficiency
        from django.db.models import Count
        qs = qs.annotate(document_count=Count('documents'))

        return qs

    @action(detail=True, methods=['get'])
    def documents(self, request, pk=None):
        """
        GET /api/v1/instrument-io-workflow/projects/{id}/documents/
        Return all documents within this project.
        """
        project = self.get_object()
        docs = project.documents.all()
        serializer = IOListDocumentListSerializer(docs, many=True)
        return Response(serializer.data)


# ──────────────────────────────────────────────────────────────────────
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def diff_view(request):
    old_id = request.data.get('old_id')
    new_id = request.data.get('new_id')
    if not old_id or not new_id:
        return Response(
            {'error': 'old_id and new_id are required'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        old_doc = IOListDocument.objects.get(pk=old_id)
        new_doc = IOListDocument.objects.get(pk=new_id)
    except IOListDocument.DoesNotExist:
        raise Http404('One of the documents does not exist')

    def _flatten(doc):
        return [
            {'tag_number': r.tag_number, **(r.data or {})}
            for r in doc.extracted_rows.all()
        ]

    result = diff_revisions(_flatten(old_doc), _flatten(new_doc))
    return Response({
        'old_document_id': old_doc.id,
        'new_document_id': new_doc.id,
        **result,
    })


# ──────────────────────────────────────────────────────────────────────
# BYOK Vision — connectivity check (P&ID drawing extraction)
# ──────────────────────────────────────────────────────────────────────
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def vision_test_key_view(request):
    from .services.pid_vision_extractor import test_api_key

    provider = request.data.get('provider')
    api_key  = request.data.get('api_key')
    if not provider or not api_key:
        return Response(
            {'valid': False, 'message': 'provider and api_key are required'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    valid, message = test_api_key(provider, api_key)
    return Response({'valid': valid, 'message': message})


# ──────────────────────────────────────────────────────────────────────
# Pre-extraction page count — backs the upload form's "Estimated: ~N min
# / ~N tokens" hint under the Quick/Thorough Scan toggle (UploadCard,
# IOListWorkflowPage.jsx), computed from a REAL page count rather than a
# guess. A client-side-only page-count heuristic (regex-scanning the raw
# PDF bytes for /Type/Pages's /Count or individual /Page objects) was
# tried first and rejected — verified against real documents already in
# this system it was badly wrong (28 vs an actual 3 pages on one file,
# nothing detected at all on another whose structure uses compressed
# object streams, common from CAD/engineering-drawing PDF producers).
# PyMuPDF (already the same library services/page_classifier.py trusts
# for the real extraction) opens the file's structure only — no OCR, no
# Vision, no rendering — so this is fast and, unlike the client-side
# attempt, actually correct.
# ──────────────────────────────────────────────────────────────────────
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def pdf_page_count_view(request):
    import fitz

    pdf = request.FILES.get('pdf_file') or request.FILES.get('file')
    if not pdf:
        return Response({'error': 'pdf_file is required'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        doc = fitz.open(stream=pdf.read(), filetype='pdf')
        page_count = doc.page_count
        doc.close()
    except Exception as exc:  # noqa: BLE001
        return Response(
            {'error': f'Could not read PDF: {exc}'}, status=status.HTTP_400_BAD_REQUEST,
        )
    return Response({'page_count': page_count})


# ──────────────────────────────────────────────────────────────────────
# Legend Sheets — CRUD + activate
# ──────────────────────────────────────────────────────────────────────
class IOListLegendSheetListCreateView(generics.ListCreateAPIView):
    # DEFAULT_PAGINATION_CLASS is set globally (config/settings.py) — a
    # generics.ListCreateAPIView would wrap GET's response as
    # {count, next, previous, results: [...]} instead of the bare array
    # ioListLegendService.js/LegendSheetsModal.jsx expect (matches
    # apps.pid_checker_v2's own LegendSheetListCreateView, a plain APIView
    # that never paginates for the same reason). Real symptom hit live:
    # LegendSheetsModal's refresh() does `(rows || []).find(...)` — throws
    # "rows.find is not a function" against a paginated object, caught by
    # its try/catch and shown as the generic "Failed to load legends" toast.
    pagination_class = None
    serializer_class = IOListLegendSheetSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = IOListLegendSheet.objects.filter(created_by=self.request.user)
        section = self.request.query_params.get('section')
        if section:
            qs = qs.filter(section=section)
        return qs


class IOListLegendSheetDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = IOListLegendSheetSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'legend_id'
    lookup_url_kwarg = 'legend_id'

    def get_queryset(self):
        return IOListLegendSheet.objects.filter(created_by=self.request.user)


class IOListLegendActivateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, legend_id):
        legend = get_object_or_404(
            IOListLegendSheet, legend_id=legend_id, created_by=request.user,
        )
        with transaction.atomic():
            IOListLegendSheet.objects.filter(
                created_by=request.user, section=legend.section, is_active=True,
            ).exclude(pk=legend.pk).update(is_active=False)
            legend.is_active = True
            legend.save(update_fields=['is_active', 'updated_at'])
        return Response(IOListLegendSheetSerializer(legend).data)


def _lookup_field(definition: dict):
    """The one field in a legend definition that carries a {code:
    description} lookup table, if any — format-only sections (equipment
    tag conventions etc.) have none."""
    for field in (definition or {}).get('fields', []) or []:
        if isinstance(field.get('lookup'), dict):
            return field
    return None


class LegendLookupAddView(APIView):
    """Add ONE lookup entry to the user's active legend for a section,
    without resending the whole definition — backs the "+ Add to Legend"
    quick-add button on an unrecognised Legend Check finding."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        section = request.data.get('section')
        code = (request.data.get('code') or '').strip()
        description = (request.data.get('description') or '').strip()
        if not section or not code:
            return Response(
                {'error': 'section and code are required'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        legend = IOListLegendSheet.objects.filter(
            created_by=request.user, section=section, is_active=True,
        ).first()
        if not legend:
            return Response(
                {'error': f'No active legend for section "{section}"'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        field = _lookup_field(legend.definition)
        if not field:
            return Response(
                {'error': 'no lookup table to add to'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        field['lookup'][code] = description
        legend.save(update_fields=['definition', 'updated_at'])
        return Response(IOListLegendSheetSerializer(legend).data)


class LegendLookupEditView(APIView):
    permission_classes = [IsAuthenticated]

    def put(self, request):
        section = request.data.get('section')
        code = (request.data.get('code') or '').strip()
        description = (request.data.get('description') or '').strip()
        if not section or not code:
            return Response(
                {'error': 'section and code are required'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        legend = IOListLegendSheet.objects.filter(
            created_by=request.user, section=section, is_active=True,
        ).first()
        if not legend:
            return Response(
                {'error': f'No active legend for section "{section}"'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        field = _lookup_field(legend.definition)
        if not field:
            return Response(
                {'error': 'no lookup table to add to'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        field['lookup'][code] = description
        legend.save(update_fields=['definition', 'updated_at'])
        return Response(IOListLegendSheetSerializer(legend).data)


class LegendLookupDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request):
        section = request.data.get('section')
        code = request.data.get('code')
        if not section or not code:
            return Response(
                {'error': 'section and code are required'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        legend = IOListLegendSheet.objects.filter(
            created_by=request.user, section=section, is_active=True,
        ).first()
        if legend:
            field = _lookup_field(legend.definition)
            if field:
                field['lookup'].pop(code, None)
                legend.save(update_fields=['definition', 'updated_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)


class LegendLookupSectionsView(APIView):
    """Which of the current user's active legends have a lookup table at
    all — several sections are format/regex-only and have no lookup
    field; the frontend's "Add to Legend" Section dropdown filters down to
    only sections a code can actually be added to."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        legends = IOListLegendSheet.objects.filter(
            created_by=request.user, is_active=True,
        )
        sections = [l.section for l in legends if _lookup_field(l.definition)]
        return Response({'sections': sections})


# ──────────────────────────────────────────────────────────────────────
# Legend symbol reference pictures — user-scoped
# ──────────────────────────────────────────────────────────────────────
class SymbolImagesListView(APIView):
    """GET → {images: [...]} — LegendSheetsModal.jsx's refreshSymbolImages
    reads data.images, not a bare array/paginated wrapper.

    Two tiers, in priority order — the second only fills gaps the first
    left:
      1. This user's own uploads (is_default=False).
      2. The shared default library (is_default=True, created_by=None) —
         seeded into the DB by seed_io_default_symbols (management
         command + automatic post_migrate hook — see apps.py), so this
         works even on a fresh server with no user uploads at all.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        section = request.query_params.get('section')

        own_qs = IOListLegendSymbolImage.objects.filter(
            created_by=request.user, is_default=False,
        )
        if section:
            own_qs = own_qs.filter(section=section)
        own_images = list(own_qs)
        covered = {(img.section, img.symbol_name) for img in own_images}

        default_qs = IOListLegendSymbolImage.objects.filter(is_default=True)
        if section:
            default_qs = default_qs.filter(section=section)
        default_images = [
            img for img in default_qs
            if (img.section, img.symbol_name) not in covered
        ]

        return Response({
            'images': IOListLegendSymbolImageSerializer(
                own_images + default_images, many=True, context={'request': request},
            ).data,
        })


class SymbolImageUploadView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        section = request.data.get('section')
        symbol_name = request.data.get('symbol_name')
        image = request.FILES.get('image')
        if not (section and symbol_name and image):
            return Response(
                {'error': 'section, symbol_name and image are required'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        obj, _created = IOListLegendSymbolImage.objects.update_or_create(
            created_by=request.user, section=section, symbol_name=symbol_name,
            defaults={
                'image_file': image,
                'content_type': getattr(image, 'content_type', '') or 'image/png',
            },
        )
        return Response(
            IOListLegendSymbolImageSerializer(obj, context={'request': request}).data,
        )


class SymbolImageDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request):
        section = request.query_params.get('section')
        symbol_name = request.query_params.get('symbol_name')
        if not (section and symbol_name):
            return Response(
                {'error': 'section and symbol_name are required'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        IOListLegendSymbolImage.objects.filter(
            created_by=request.user, section=section, symbol_name=symbol_name,
        ).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class DefaultSymbolImagesView(APIView):
    """Repo-committed default pictures for whichever names the user
    hasn't uploaded their own picture for. Returns {} for any name with
    no default on disk rather than erroring — purely additive."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from .services.default_symbol_images import get_default_symbol_image_url

        section = request.data.get('section') or ''
        names = request.data.get('symbol_names') or []
        result = {}
        for name in names:
            url = get_default_symbol_image_url(section, name)
            if url:
                result[name] = url
        # LegendSheetsModal.jsx reads data?.results, not the bare dict.
        return Response({'results': result})
