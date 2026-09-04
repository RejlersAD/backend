"""
Celery Background Tasks — P&ID Verification
============================================
Task pipeline:
  1. Segment document into drawings (one per PDF page)
  2. Small documents (<= MULTI_PAGE_PARALLEL_THRESHOLD pages): processed
     inline, one page at a time, in this task.
     Large documents: fanned out to parallel per-page subtasks
     (process_pid_page, one Celery task per page) via a chord, mirroring
     apps.pid_verification_v2 — sequential per-page processing cannot
     finish a 32-50 page P&ID set within any sane Celery time limit.
  3. Per page: extract -> build graph -> run rule engine -> comparison
     engine -> AI Vision analysis (BYOK) -> legend/symbol bridge -> save
     findings.
  4. Generate Excel & PDF reports -> upload to S3 (finalize step).
  5. Update document status = completed (or failed).
"""
import logging
import os
import shutil
import tempfile

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.utils import timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Soft-coded constants
# ---------------------------------------------------------------------------

# Set to True to block P&ID quality checks until the project has at least one
# completed legend sheet (or legend_knowledge_data is populated from a prior
# extraction).  Set to False to allow quality checks without a legend.
LEGEND_REQUIRED_FOR_QC = True

# Documents segmented into MORE pages than this are fanned out across
# parallel Celery subtasks (one per page, via a chord) instead of being
# processed sequentially inside a single task — mirrors
# apps.pid_verification_v2.tasks.MULTI_PAGE_PARALLEL_THRESHOLD exactly (same
# reasoning: a 28+ page P&ID set cannot realistically finish sequential
# multi-pass OCR within any sane Celery time limit).
MULTI_PAGE_PARALLEL_THRESHOLD = 2


@shared_task(
    bind=True,
    name='pid_verification.process_document',
    max_retries=2,
    default_retry_delay=30,
    # Soft-coded to match apps.pid_verification_v2's top-level task budget —
    # was 540s/600s (9/10 min), tuned back when this task only ever
    # processed a single page. Documents over MULTI_PAGE_PARALLEL_THRESHOLD
    # now fan out to process_pid_page (their own bounded per-page budget)
    # almost immediately, but small documents still run their full
    # extraction/Vision pipeline inline in THIS task, so it needs the same
    # generous budget process_pid_page gets.
    soft_time_limit=1800,  # 30 min soft limit
    time_limit=2100,       # 35 min hard limit
)
def process_pid_document(self, document_id: str, context: dict = None):
    """
    Main background task.
    Receives the string form of PIDVDocument.document_id (UUID).

    Args:
        document_id: UUID string of the PIDVDocument
        context: Optional dict with BYOK settings:
                 - analysis_mode: 'standard' | 'enhanced_openai' | 'deep_claude' | 'hybrid'
                 - openai_api_key: User-provided OpenAI API key
                 - claude_api_key: User-provided Claude API key
    """
    context = context or {}
    from apps.pid_verification.models import PIDVDocument
    from apps.pid_verification.services.segmentation import segment_document

    logger.info('[PIDVTask] Starting processing for document_id=%s with context=%s', document_id, context)

    # ── 1. Load document ──────────────────────────────────────────────────
    try:
        doc = PIDVDocument.objects.get(document_id=document_id)
    except PIDVDocument.DoesNotExist:
        logger.error('[PIDVTask] Document %s not found', document_id)
        return

    doc.status = PIDVDocument.Status.PROCESSING
    doc.save(update_fields=['status', 'updated_at'])

    # ── Legend quality gate ───────────────────────────────────────────────
    # Do not start P&ID quality checking until the project has at least one
    # completed legend sheet OR legend_knowledge_data is already populated
    # (e.g. loaded from S3 cache on a previous upload).
    if LEGEND_REQUIRED_FOR_QC and doc.project_id:
        project = doc.project
        legend_ready = (
            project.legend_knowledge_data is not None
            or project.legend_sheets.filter(status='completed').exists()
        )
        # Fallback: the project-level legend-PDF upload above was deprecated
        # in favor of pid_checker_v2's per-user LegendSheetsModal (structured
        # rule sheets — no AI-extraction step, so "active" is the equivalent
        # of "completed" here). Without this, the gate is a dead end, since
        # nothing in the current UI can satisfy the checks above.
        if not legend_ready and doc.uploaded_by_id:
            from apps.pid_checker_v2.models import PidCheckerV2LegendSheet
            legend_ready = PidCheckerV2LegendSheet.objects.filter(
                created_by_id=doc.uploaded_by_id, is_active=True,
            ).exists()
        if not legend_ready:
            doc.status        = PIDVDocument.Status.LEGEND_PENDING
            doc.error_message = (
                'Legend symbols have not been extracted yet.  '
                'Please upload a legend sheet for this project and wait for '
                'extraction to complete before running the quality check.'
            )
            doc.save(update_fields=['status', 'error_message', 'updated_at'])
            logger.warning(
                '[PIDVTask] Blocked document_id=%s — no legend for project=%s',
                document_id, doc.project_id,
            )
            return   # not a failure — user must upload a legend first

    # NOTE: this task used to try apps.pid_verification.services.orchestrator
    # (a stage-based pipeline) first, falling back to a "legacy" per-page
    # loop only if the orchestrator raised. In practice the orchestrator
    # NEVER raised — it just silently processed context.segments[0] only
    # (see ExtractionStage's own comment: "Extract from first segment,
    # multi-segment support can be added") — so pages 2+ of every
    # multi-page document were dropped with no error, and the correct
    # multi-page "legacy" loop below was never actually reached. That
    # orchestrator is left in place (unused) rather than deleted, in case
    # someone wants to finish it properly later; this task no longer calls
    # it. The loop below (already page-complete) is now the only path, and
    # is used both for small documents (inline, right here) and — via
    # process_pid_page — for the parallel per-page fan-out on large ones.

    # ── Reference symbol pictures — loaded ONCE for this whole document
    # run, reused for every page (inline or fanned-out) instead of every
    # page re-reading/re-encoding the same images from storage. Always
    # fetched fresh from LegendSymbolImage (never a hardcoded count), so
    # this scales to any library size automatically. Only bothers fetching
    # when a Claude key is actually present — Vision won't run without one.
    _symbol_images = []
    if context.get('claude_api_key') and doc.project_id:
        _symbol_images = _load_symbol_images_v1(doc.project)
        logger.info(
            '[PIDVTask] Loaded %d reference symbol image(s) for document_id=%s',
            len(_symbol_images), document_id,
        )

    try:
        # ── Resolve file path ────────────────────────────────────────────
        file_path = _resolve_file_path(doc)

        # ── Segment into pages ───────────────────────────────────────────
        segments = segment_document(str(doc.document_id), file_path)
        logger.info('[PIDVTask] %d page(s) segmented for document_id=%s', len(segments), document_id)

        # ── Resolve per-project legend (project legend → global fallback) ──
        project_legend = None
        if doc.project_id and doc.project and doc.project.legend_knowledge_data:
            project_legend = doc.project.legend_knowledge_data
            logger.info('[PIDVTask] Using per-project legend for project=%s', doc.project.project_id)

        # ── Large documents: fan out to parallel per-page subtasks ────────
        # Sequential per-page OCR/extraction does not scale — mirrors
        # apps.pid_verification_v2.tasks.process_pid_document exactly.
        if len(segments) > MULTI_PAGE_PARALLEL_THRESHOLD:
            from celery import chord
            from dataclasses import asdict as _asdict

            header = [
                process_pid_page.s(str(doc.document_id), _asdict(seg), context, _symbol_images)
                for seg in segments
            ]
            chord(header)(finalize_pid_document.s(str(doc.document_id)))
            logger.info(
                '[PIDVTask] Dispatched %d parallel page-task(s) for document_id=%s',
                len(segments), document_id,
            )
            return  # doc stays PROCESSING; finalize_pid_document completes it

        # ── Small document (<= threshold pages) — process inline ──────────
        with open(file_path, 'rb') as _fh:
            pdf_bytes = _fh.read()

        for seg in segments:
            _process_one_page(doc, seg, file_path, pdf_bytes, project_legend, context, _symbol_images)

        _finalize_document(doc, document_id)

    except Exception as exc:
        logger.exception('[PIDVTask] Processing failed for document_id=%s: %s', document_id, exc)
        doc.status        = PIDVDocument.Status.FAILED
        doc.error_message = str(exc)
        doc.save(update_fields=['status', 'error_message', 'updated_at'])
        raise self.retry(exc=exc)


def _resolve_file_path(doc) -> str:
    """
    Return a local filesystem path for the document file.

    Resolution order (soft-coded to handle all storage backends):
      1. Local FileField  →  .path  (e.g. FileSystemStorage / ResilientMediaStorage)
      2. Any other FileField backend (S3 or otherwise) → read through the
         field's OWN storage object (see _download_via_storage below).
      3. Explicit s3_path →  download to tmp file (legacy path, no Storage
         object available — see _download_from_s3)
    Raises ValueError when no source is available.

    IMPORTANT — 2026-08-27 postmortem: step 2 used to hand-roll S3 access
    with a raw boto3 client, keyed on doc.original_file.name alone. That
    silently ignored the configured storage backend's `location` prefix
    (e.g. MediaStorage.location = 'media' — see apps/core/storage_backends.py)
    — Django's FileField.name is relative to that location, NOT a full S3
    key. So every single download 404'd on HeadObject: it asked S3 for
    "<upload_to_path>/<file>" when the real object was at
    "media/<upload_to_path>/<file>". Going through
    doc.original_file.storage (the exact same Storage instance that
    performed the original upload) makes this correct automatically, for
    whichever storage backend is actually configured — no prefix, bucket,
    region, or credential logic to keep in sync by hand ever again.
    """
    if doc.original_file:
        # Try local path first (works for FileSystemStorage and ResilientMediaStorage)
        try:
            path = doc.original_file.path
            if path:
                return path
        except NotImplementedError:
            pass  # S3Boto3Storage raises NotImplementedError for .path

        if doc.original_file.name:
            logger.info('[PIDVTask] Downloading file via storage backend: %s', doc.original_file.name)
            return _download_via_storage(doc.original_file)

    # Explicit s3_path field (legacy / manually set) — no Storage object
    # attached to a plain CharField, so this one genuinely does need a raw
    # boto3 client; s3_key here is assumed to already be a full bucket key.
    if doc.s3_path:
        logger.info('[PIDVTask] Downloading file from explicit s3_path: %s', doc.s3_path)
        return _download_from_s3(doc.s3_path)

    raise ValueError(f'No file path available for document {doc.document_id}')


def _download_via_storage(file_field) -> str:
    """Download a FileField's content to a temp file through its OWN
    storage backend — correctly handles bucket/region/credentials/location
    prefix for whichever backend is configured (S3Boto3Storage,
    FileSystemStorage, etc.), instead of reconstructing S3 access by hand."""
    ext = file_field.name.rsplit('.', 1)[-1] if '.' in file_field.name else 'bin'
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f'.{ext}')
    with file_field.open('rb') as src:
        shutil.copyfileobj(src, tmp)
    tmp.flush()
    tmp.close()
    return tmp.name


def _download_from_s3(s3_key: str) -> str:
    """Download an S3 object to a temp file and return its path. Only used
    for the legacy explicit doc.s3_path field (a plain CharField with no
    Storage object attached) — s3_key is assumed to already be a full,
    correct bucket key, unlike a FileField's .name (see _resolve_file_path's
    docstring for why that distinction matters)."""
    import boto3
    bucket = os.environ.get('AWS_STORAGE_BUCKET_NAME', '')
    region = os.environ.get('AWS_S3_REGION_NAME', 'us-east-1')
    ext    = s3_key.rsplit('.', 1)[-1] if '.' in s3_key else 'bin'

    s3  = boto3.client('s3', region_name=region)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f'.{ext}')
    s3.download_fileobj(bucket, s3_key, tmp)
    tmp.flush()
    tmp.close()
    return tmp.name


# ===========================================================================
# Multi-page processing — one page at a time, shared by the inline path
# (small documents) and process_pid_page (large documents, parallel fan-out)
# ===========================================================================

def _load_symbol_images_v1(project) -> list:
    """Reference symbol pictures for a V1 project — loaded ONCE per document
    run (see process_pid_document) and reused for every page, instead of
    every page independently re-reading/re-encoding the same images.

    Unlike apps.pid_verification_v2's equivalent bridge helper, no
    project-name-matching indirection is needed here: LegendSymbolImage.project
    is a direct FK into THIS app's PIDVProject (see
    apps.pid_checker_v2.models.LegendSymbolImage). Always queried fresh from
    the DB (never a hardcoded count), so this scales to any library size —
    uploading symbol #501 needs no code change here.
    """
    if project is None:
        return []
    try:
        import base64
        from apps.pid_checker_v2.views import _get_symbol_images_with_fallback

        rows = _get_symbol_images_with_fallback(str(project.project_id))
        images = []
        for row in rows:
            if not row.image_file:
                continue
            row.image_file.open('rb')
            try:
                data = row.image_file.read()
            finally:
                row.image_file.close()
            images.append({
                'symbol_type': row.symbol_name,
                'b64': base64.b64encode(data).decode('ascii'),
            })
        return images
    except Exception:
        logger.warning('[PIDVTask] Could not load symbol images (non-fatal)', exc_info=True)
        return []


def _fetch_reference_data_v1(project, data_type: str) -> list:
    """Latest completed PIDVReferenceData rows for `project`/`data_type`,
    normalized to the canonical field names the comparison engine expects.
    Reuses apps.pid_verification_v2's column-alias normalizer directly (pure
    function, no V2-model dependency) instead of duplicating it — this is
    also what actually implements the Line List / Equipment List /
    Instrument Index comparisons, which were previously TODO-stubbed here
    (hardcoded to empty lists on every run)."""
    if not project:
        return []
    from apps.pid_verification.models import PIDVReferenceData
    from apps.pid_verification_v2.services.orchestrator import _normalize_reference_rows

    ref = (
        PIDVReferenceData.objects
        .filter(project=project, data_type=data_type, status=PIDVReferenceData.Status.COMPLETED)
        .exclude(parsed_data__isnull=True)
        .order_by('-created_at')
        .first()
    )
    if not ref or not ref.parsed_data:
        return []
    return _normalize_reference_rows(data_type, ref.parsed_data)


def _bridge_xref_to_rule_findings(xref: dict) -> list:
    """Convert legend_bridge.cross_reference()'s {'linked','text_only',
    'symbol_only'} output into RuleFinding objects so they persist through
    the same PIDVFinding path as every other finding source. V1 has no
    PIDVComparisonFinding-equivalent table (that's V2-only), so these are
    stored as regular findings on the page's drawing — already cleared and
    recreated on every reprocess by _process_one_page(), so no separate
    document-level idempotency step is needed (unlike V2's version, which
    needed one because its findings live in a project-scoped table)."""
    from apps.pid_verification.services.rule_engine import RuleFinding

    out = []
    for item in xref.get('linked', []):
        out.append(RuleFinding(
            category='legend',
            rule_id='LGN-004',
            issue_observed=(
                f"Confirmed: {item['tag']} → {item['description']} — text tag and a visually "
                f"identified symbol ('{item['symbol']['symbol_type']}') agree."
            ),
            action_required='No action — confirmed match.',
            evidence=f"tag={item['tag']} code={item['code']} section={item['section']} symbol={item['symbol']['symbol_type']}",
            direction='N/A',
            severity='info',
        ))
    for item in xref.get('text_only', []):
        out.append(RuleFinding(
            category='legend',
            rule_id='LGN-005',
            issue_observed=f"Legend text match: {item['tag']} → {item['description']}",
            action_required='Informational — no symbol visually confirmed.',
            evidence=f"tag={item['tag']} code={item['code']} section={item['section']}",
            direction='N/A',
            severity='info',
        ))
    for sym in xref.get('symbol_only', []):
        out.append(RuleFinding(
            category='legend',
            rule_id='LGN-006',
            issue_observed=f"Symbol identified: {sym['symbol_type']} ({sym.get('confidence', 'low')} confidence)",
            action_required='Informational — no matching legend text tag found.',
            evidence=f"symbol={sym['symbol_type']} location={sym.get('location', 'unspecified')}",
            direction='N/A',
            severity='info',
        ))
    return out


def _process_one_page(doc, seg, file_path: str, pdf_bytes: bytes, project_legend,
                       context: dict, symbol_images: list) -> int:
    """
    Process exactly ONE page/segment: extract -> build graph -> run rule
    engine -> comparison engine -> AI Vision analysis (BYOK, real image) ->
    legend/symbol bridge -> persist PIDVDrawing + findings for that page only.

    Shared by both the inline (small document) path and the per-page
    fan-out task (process_pid_page, large documents), so persistence shape
    stays identical no matter which path ran this page. Never raises for
    the AI/comparison/bridge sub-steps — each is independently best-effort
    and logs+continues on failure, so one bad step never loses the page's
    rule-engine findings.

    Returns the number of findings created for this page.
    """
    from apps.pid_verification.models import PIDVDrawing, PIDVFinding
    from apps.pid_verification.services.extraction import extract_drawing
    from apps.pid_verification.services.graph_builder import build_graph
    from apps.pid_verification.services.rule_engine import run_rules, RuleFinding

    drawing_obj, _ = PIDVDrawing.objects.get_or_create(
        document=doc,
        drawing_id=seg.drawing_id,
        defaults={'title': seg.title, 'page_index': seg.page_index, 'metadata': seg.metadata},
    )
    # Clear any previous findings (re-process idempotency) — this also
    # covers the legend/symbol bridge findings added below, since those are
    # persisted as regular PIDVFinding rows on this same drawing.
    drawing_obj.findings.all().delete()

    # ── Extract (hybrid Tesseract + AI Vision) ───────────────────────────
    # extract_drawing() runs Tesseract (if installed) AND Vision (if a BYOK
    # key is present) and merges both. Raises NoExtractionMethodAvailableError
    # only when NEITHER is available; deliberately left uncaught here so it
    # propagates to process_pid_document's outer except-handler (inline
    # path) or process_pid_page's (fan-out path), both of which surface it
    # as a clear FAILED status + error_message instead of a silently
    # "completed" document with zero findings.
    extraction_api_key = (context or {}).get('claude_api_key') or (context or {}).get('openai_api_key')
    extraction_provider = 'claude' if (context or {}).get('claude_api_key') else 'openai'
    extraction = extract_drawing(
        file_path, page_index=seg.page_index, legend_data=project_legend,
        api_key=extraction_api_key, provider=extraction_provider,
    )
    _ext_info = extraction.get('extraction_info') or {}
    if _ext_info.get('message'):
        metadata = drawing_obj.metadata or {}
        metadata['extraction_method_info'] = _ext_info
        drawing_obj.metadata = metadata
        drawing_obj.save(update_fields=['metadata'])

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
        'line_tags': len(extraction.get('line_tags', [])),
        'line_tags_multi_angle': sum(1 for lt in extraction.get('line_tags', []) if lt.get('multi_angle')),
    }
    metadata = drawing_obj.metadata or {}
    metadata['extraction_summary'] = extraction_summary
    tag_positions = extraction.get('tag_positions', {})
    if tag_positions:
        metadata['tag_positions'] = tag_positions
    line_tags = extraction.get('line_tags', [])
    if line_tags:
        metadata['line_tags'] = line_tags
    red_annotations = extraction.get('red_annotations', [])
    if red_annotations:
        metadata['red_annotations'] = red_annotations
    drawing_obj.metadata = metadata
    drawing_obj.save(update_fields=['metadata'])

    # ── Graph + deterministic rule engine ────────────────────────────────
    graph = build_graph(extraction)
    rule_findings = run_rules(extraction, graph)

    # ── Comparison engine (Legend / Line List / Equipment / Instrument) ──
    comparison_findings = []
    try:
        from apps.pid_verification.services.comparison_engine import run_all_comparisons

        comparison_results = run_all_comparisons(
            extraction=extraction,
            legend_data=project_legend,
            line_list_data=_fetch_reference_data_v1(doc.project, 'line_list'),
            equipment_list_data=_fetch_reference_data_v1(doc.project, 'equipment_list'),
            instrument_index_data=_fetch_reference_data_v1(doc.project, 'instrument_index'),
        )

        metadata = drawing_obj.metadata or {}
        metadata['comparison_results'] = {
            comp_type: {
                'total_pid_items': result.total_pid_items,
                'total_ref_items': result.total_ref_items,
                'matched_count': result.matched_count,
                'missing_count': result.missing_count,
                'extra_count': result.extra_count,
                'mismatch_count': result.mismatch_count,
                'summary': result.summary,
            }
            for comp_type, result in comparison_results.items()
        }
        drawing_obj.metadata = metadata
        drawing_obj.save(update_fields=['metadata'])

        for comp_type, result in comparison_results.items():
            for finding in result.findings:
                rule_prefix = {'legend': 'LGN', 'linelist': 'LSZ', 'equipment': 'EQP', 'instrument': 'IMS'}.get(comp_type, 'CMP')
                category_suffix = {'missing': '001', 'extra': '002', 'mismatch': '003'}.get(finding.category, '999')
                comparison_findings.append(RuleFinding(
                    category=comp_type,
                    rule_id=f'{rule_prefix}-{category_suffix}',
                    issue_observed=finding.issue_observed,
                    action_required=f'Review and resolve {finding.category} discrepancy',
                    evidence=finding.evidence,
                    direction='N/A',
                    severity=finding.severity,
                ))
    except Exception as comp_exc:
        logger.error('[PIDVTask] Comparison engine failed for drawing_id=%s: %s', seg.drawing_id, comp_exc, exc_info=True)
        metadata = drawing_obj.metadata or {}
        metadata['comparison_error'] = {'error': str(comp_exc), 'timestamp': str(timezone.now())}
        drawing_obj.metadata = metadata
        drawing_obj.save(update_fields=['metadata'])

    # ── AI Vision analysis (BYOK) — real page image, one page at a time ──
    ai_findings = []
    ai_symbols = []
    analysis_mode = (context or {}).get('analysis_mode', 'standard')
    if analysis_mode != 'standard':
        try:
            from apps.pid_verification_v2.services.ai_analysis import (
                run_openai_analysis, run_hybrid_analysis, to_rule_findings,
            )
            from apps.pid_verification_v2.services.legend_bridge import (
                run_page_vision_analysis, SYMBOL_BATCH_SIZE,
            )
            from apps.pid_verification_v2.services.orchestrator import _page_worth_vision

            drawing_data = {
                'instruments': extraction.get('instruments', []),
                'valves': extraction.get('valves', []),
                'equipment': extraction.get('equipment', []),
                'tags': extraction.get('tags', []),
                'line_tags': extraction.get('line_tags', []),
                'line_sizes': extraction.get('line_sizes', []),
                'notes': extraction.get('notes', []),
            }

            openai_key = context.get('openai_api_key')
            claude_key = context.get('claude_api_key')
            raw_findings = []

            if analysis_mode == 'enhanced_openai' and openai_key:
                raw_findings = run_openai_analysis(drawing_data, openai_key)['findings']

            elif analysis_mode in ('deep_claude', 'hybrid') and claude_key \
                    and _page_worth_vision(extraction_summary, seg.title):
                page_image_b64 = None
                try:
                    from apps.pid_checker_v2.services.vision_extractor import (
                        _render_single_page, _prepare_image_b64, VISION_OVERVIEW_MAX_DIMENSION_PX,
                    )
                    page_img = _render_single_page(pdf_bytes, seg.page_index)
                    page_image_b64 = _prepare_image_b64(page_img, VISION_OVERVIEW_MAX_DIMENSION_PX)
                except Exception:
                    logger.warning(
                        '[PIDVTask] Could not render page image for Vision, drawing_id=%s',
                        seg.drawing_id, exc_info=True,
                    )

                if page_image_b64:
                    if analysis_mode == 'deep_claude':
                        result = run_page_vision_analysis(
                            drawing_data, claude_key, page_image_b64, symbol_images=symbol_images,
                        )
                        if result:
                            raw_findings = result['findings']
                            ai_symbols = result['symbols']
                    elif analysis_mode == 'hybrid' and openai_key:
                        result = run_hybrid_analysis(
                            drawing_data, openai_key, claude_key,
                            page_image_b64=page_image_b64,
                            symbol_images=symbol_images[:SYMBOL_BATCH_SIZE],
                        )
                        raw_findings = result['findings']
                        ai_symbols = result['symbols']

            ai_findings = to_rule_findings(raw_findings)
            logger.info(
                '[PIDVTask] AI analysis: %d finding(s), %d symbol(s) for drawing_id=%s (mode=%s)',
                len(ai_findings), len(ai_symbols), seg.drawing_id, analysis_mode,
            )
        except Exception as ai_exc:
            logger.error(
                '[PIDVTask] AI analysis failed for drawing_id=%s mode=%s: %s',
                seg.drawing_id, analysis_mode, ai_exc, exc_info=True,
            )
            metadata = drawing_obj.metadata or {}
            metadata['ai_analysis_error'] = {'mode': analysis_mode, 'error': str(ai_exc), 'timestamp': str(timezone.now())}
            drawing_obj.metadata = metadata
            drawing_obj.save(update_fields=['metadata'])

    # ── Legend Sheets / Symbol Images bridge (apps.pid_checker_v2) ───────
    # Text-matches this page's extracted tags against pid_checker_v2's
    # legend lookup tables and cross-references them with whichever
    # symbols the Vision call above identified for THIS page — reuses
    # apps.pid_verification_v2.services.legend_bridge's pure functions
    # directly (no V2-model dependency) rather than duplicating them here.
    bridge_findings = []
    try:
        from apps.pid_verification_v2.services.legend_bridge import (
            get_legend_lookup_fields, match_text_against_legend, cross_reference,
        )
        fields = get_legend_lookup_fields(doc.uploaded_by)
        text_matches = match_text_against_legend(extraction, fields)
        symbol_result = {'symbols': ai_symbols} if ai_symbols else None
        xref = cross_reference(text_matches, symbol_result)
        bridge_findings = _bridge_xref_to_rule_findings(xref)
    except Exception:
        logger.warning(
            '[PIDVTask] Legend/symbol bridge failed for drawing_id=%s (non-fatal)',
            seg.drawing_id, exc_info=True,
        )

    # ── Merge + persist ────────────────────────────────────────────────
    all_findings = rule_findings + comparison_findings + ai_findings + bridge_findings
    bulk = [
        PIDVFinding(
            drawing=drawing_obj, sl_no=sl, category=rf.category, rule_id=rf.rule_id,
            issue_observed=rf.issue_observed, action_required=rf.action_required,
            evidence=rf.evidence, direction=rf.direction, severity=rf.severity, status='open',
        )
        for sl, rf in enumerate(all_findings, start=1)
    ]
    if bulk:
        PIDVFinding.objects.bulk_create(bulk, batch_size=500)
    logger.info(
        '[PIDVTask] Drawing %s -> %d findings (rule=%d comparison=%d ai=%d bridge=%d)',
        seg.drawing_id, len(bulk), len(rule_findings), len(comparison_findings),
        len(ai_findings), len(bridge_findings),
    )
    return len(bulk)


def _finalize_document(doc, document_id: str) -> None:
    """Generate Excel/PDF reports, mark the document COMPLETED, and write
    the results cache — shared by the inline path and finalize_pid_document
    (the chord callback for the large-document fan-out)."""
    from apps.pid_verification.models import PIDVDocument
    from apps.pid_verification.services.export_service import generate_excel, generate_pdf, upload_to_s3

    doc.refresh_from_db()

    excel_bytes = generate_excel(doc)
    if excel_bytes:
        project_slug = str(doc.project.project_id) if doc.project_id else 'unassigned'
        key = f'pid_verification/projects/{project_slug}/reports/{doc.document_id}/findings.xlsx'
        doc.excel_s3_url = upload_to_s3(excel_bytes, key, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    pdf_bytes = generate_pdf(doc)
    if pdf_bytes:
        project_slug = str(doc.project.project_id) if doc.project_id else 'unassigned'
        key = f'pid_verification/projects/{project_slug}/reports/{doc.document_id}/findings.pdf'
        doc.pdf_s3_url = upload_to_s3(pdf_bytes, key, 'application/pdf')

    doc.status = PIDVDocument.Status.COMPLETED
    doc.save(update_fields=['status', 'excel_s3_url', 'pdf_s3_url', 'updated_at'])
    logger.info('[PIDVTask] Completed document_id=%s', document_id)

    # Result caching (S3/local) — never fatal: a caching failure shouldn't
    # turn a successful analysis into a failed one.
    try:
        from apps.pid_verification.services.results_cache import save_results_cache
        save_results_cache(doc, doc.file_hash)
    except Exception:
        logger.exception('[PIDVTask] Result cache write failed for document_id=%s (non-fatal)', document_id)


@shared_task(
    bind=True,
    name='pid_verification.process_page',
    max_retries=1,
    default_retry_delay=15,
    # Soft-coded, tuned for ONE page — mirrors
    # apps.pid_verification_v2.tasks.process_pid_page exactly (same
    # multi-DPI/multi-angle OCR cost per page).
    soft_time_limit=900,  # 15 min soft limit per page
    time_limit=960,       # 16 min hard limit per page
)
def process_pid_page(self, document_id: str, segment_dict: dict, context: dict = None, symbol_images: list = None):
    """
    Process exactly ONE page/segment of a multi-page P&ID document.

    Dispatched as one member of a Celery chord (one subtask per page) from
    `process_pid_document` when a document has more pages than
    MULTI_PAGE_PARALLEL_THRESHOLD, so large multi-page P&ID sets are
    processed with true parallelism instead of one task looping through
    every page sequentially (which cannot finish 32-50 pages of multi-pass
    OCR within any sane Celery time limit) — mirrors
    apps.pid_verification_v2.tasks.process_pid_page.

    `symbol_images` is loaded ONCE by `process_pid_document` and passed to
    every page-task unchanged — avoids each of N page-tasks independently
    re-reading/re-encoding the same reference-picture library from storage.

    Never raises — any failure is captured and returned as
    {'success': False, 'error': ...} so a single bad/slow page can't abort
    the whole chord/document; `finalize_pid_document` decides overall
    document status from the aggregate of these results.
    """
    context = context or {}
    symbol_images = symbol_images or []
    from apps.pid_verification.models import PIDVDocument
    from apps.pid_verification.services.segmentation import SegmentedDrawing

    seg = SegmentedDrawing(**segment_dict)

    try:
        doc = PIDVDocument.objects.get(document_id=document_id)
    except PIDVDocument.DoesNotExist:
        logger.error('[PIDVPage] Document %s not found for page %s', document_id, seg.drawing_id)
        return {'drawing_id': seg.drawing_id, 'success': False, 'error': 'Document not found'}

    try:
        file_path = _resolve_file_path(doc)
        with open(file_path, 'rb') as fh:
            pdf_bytes = fh.read()

        project_legend = None
        if doc.project_id and doc.project and doc.project.legend_knowledge_data:
            project_legend = doc.project.legend_knowledge_data

        findings_count = _process_one_page(doc, seg, file_path, pdf_bytes, project_legend, context, symbol_images)
        logger.info('[PIDVPage] Page %s completed: %d finding(s)', seg.drawing_id, findings_count)
        return {'drawing_id': seg.drawing_id, 'success': True, 'findings_count': findings_count}

    except SoftTimeLimitExceeded:
        logger.error(
            '[PIDVPage] Soft time limit exceeded for page %s (document_id=%s)',
            seg.drawing_id, document_id,
        )
        return {'drawing_id': seg.drawing_id, 'success': False, 'error': 'Page processing timed out'}
    except Exception as exc:
        logger.error('[PIDVPage] Failed processing page %s: %s', seg.drawing_id, exc, exc_info=True)
        return {'drawing_id': seg.drawing_id, 'success': False, 'error': str(exc)}


@shared_task(
    bind=True,
    name='pid_verification.finalize_document',
    max_retries=1,
    default_retry_delay=15,
    soft_time_limit=600,  # 10 min — report generation only, not extraction
    time_limit=660,
)
def finalize_pid_document(self, page_results, document_id: str):
    """
    Celery chord callback — runs once ALL per-page subtasks dispatched by
    `process_pid_document`'s parallel fan-out have finished. Generates the
    Excel/PDF reports and marks the document COMPLETED (or FAILED if every
    single page failed) — mirrors
    apps.pid_verification_v2.tasks.finalize_pid_document.
    """
    from apps.pid_verification.models import PIDVDocument

    try:
        doc = PIDVDocument.objects.get(document_id=document_id)
    except PIDVDocument.DoesNotExist:
        logger.error('[PIDVFinalize] Document %s not found', document_id)
        return

    total     = len(page_results)
    succeeded = sum(1 for r in page_results if r.get('success'))
    failed    = [r for r in page_results if not r.get('success')]

    logger.info('[PIDVFinalize] document_id=%s pages_ok=%d/%d', document_id, succeeded, total)
    if failed:
        logger.warning(
            '[PIDVFinalize] %d/%d page(s) failed for document_id=%s: %s',
            len(failed), total, document_id, [f.get('error') for f in failed],
        )

    if total > 0 and succeeded == 0:
        doc.status = PIDVDocument.Status.FAILED
        doc.error_message = (
            f'All {total} page(s) failed to process. '
            f'First error: {failed[0].get("error", "unknown")}'
        )
        doc.save(update_fields=['status', 'error_message', 'updated_at'])
        return

    _finalize_document(doc, document_id)

    if failed:
        doc.error_message = (
            f'Completed — {len(failed)} of {total} page(s) failed to process (see logs).'
        )
        doc.save(update_fields=['error_message', 'updated_at'])


# ===========================================================================
# Legend Sheet Extraction Task
# ===========================================================================

@shared_task(
    bind=True,
    name='pid_verification.extract_legend_sheet',
    max_retries=2,
    default_retry_delay=20,
    soft_time_limit=900,   # 15-min soft limit — accommodates 30+ page legend sheets
    time_limit=960,        # 16-min hard limit (was 4 min; increased for large PDFs)
)
def extract_legend_sheet_task(self, legend_id: str):
    """
    Background task: extract structured data from an uploaded legend sheet.

    Pipeline:
      1. Load PIDVLegendSheet record.
      2. Resolve the file path (local storage or S3 download).
      3. Run extract_legend_sheet() — text pass first, AI Vision fallback.
      4. Merge results into parent PIDVProject.legend_knowledge_data.
      5. Update PIDVLegendSheet.status → completed / failed.
    """
    from apps.pid_verification.models import PIDVLegendSheet
    from apps.pid_verification.services.legend_extractor import (
        extract_legend_sheet,
        merge_into_project_legend,
    )

    logger.info('[LegendTask] Starting extraction for legend_id=%s', legend_id)

    try:
        sheet = PIDVLegendSheet.objects.get(legend_id=legend_id)
    except PIDVLegendSheet.DoesNotExist:
        logger.error('[LegendTask] Legend sheet %s not found', legend_id)
        return

    sheet.status = PIDVLegendSheet.Status.PROCESSING
    sheet.save(update_fields=['status', 'updated_at'])

    tmp_path = None
    try:
        # ── Resolve file path ──────────────────────────────────────────────
        if sheet.original_file:
            try:
                tmp_path = sheet.original_file.path
                need_cleanup = False
            except NotImplementedError:
                # S3-backed storage — download to temp file
                s3_key     = getattr(sheet.original_file, 'name', None)
                tmp_path   = _download_from_s3(s3_key) if s3_key else None
                need_cleanup = True
        elif sheet.s3_path:
            tmp_path  = _download_from_s3(sheet.s3_path)
            need_cleanup = True
        else:
            raise ValueError(f'No file available for legend_id={legend_id}')

        # ── S3 cache lookup ────────────────────────────────────────────────
        # Compute the file hash first so we can skip AI extraction if this
        # exact file was already processed and cached in S3.
        from apps.pid_verification.services.legend_cache import (
            compute_file_hash as _cache_hash,
            lookup_s3_cache as _cache_get,
            write_s3_cache as _cache_put,
        )
        from apps.pid_verification.services.legend_extractor import _render_pages_to_b64 as _render_pages
        _file_hash = _cache_hash(tmp_path)
        logger.info('[LegendTask] File hash=%.16s for legend_id=%s', _file_hash, legend_id)

        # ── Render pages ONCE and share across both extractors ─────────────
        # Rendering at 3× DPI is the costliest step (~23 s for 16 pages).
        # Rendering once and passing pages_b64 to both legend and instrument
        # extractors avoids the ~23 s duplicate render that was the #2 bottleneck.
        _shared_pages_b64 = _render_pages(tmp_path)
        logger.info('[LegendTask] Pre-rendered %d pages (shared across extractors)', len(_shared_pages_b64))

        _cached_extraction = _cache_get(_file_hash)
        if _cached_extraction is not None:
            extracted = _cached_extraction
            extracted['extraction_method'] = 's3_cache'
            logger.info(
                '[LegendTask] Cache HIT — skipping AI extraction for legend_id=%s  items=%d',
                legend_id,
                sum(len(v) for v in extracted.values() if isinstance(v, list)),
            )
        else:
            # ── Extract (AI pipeline, pages pre-rendered) ──────────────────
            extracted = extract_legend_sheet(tmp_path, use_ai=True, pages_b64=_shared_pages_b64)
            logger.info(
                '[LegendTask] Extraction done for legend_id=%s  method=%s  categories=%d',
                legend_id,
                extracted.get('extraction_method', 'unknown'),
                sum(1 for k in extracted if isinstance(extracted[k], (list, dict)) and extracted[k]),
            )
            # Write result to S3 cache so future uploads of the same file skip AI
            _cache_put(_file_hash, extracted)

        sheet.extracted_data = extracted
        sheet.status         = PIDVLegendSheet.Status.COMPLETED
        sheet.error_message  = ''
        sheet.save(update_fields=['extracted_data', 'status', 'error_message', 'updated_at'])

        # ── Persist extracted data to S3 for future reference ──────────────
        # Non-fatal: S3 may not be configured in local dev.
        try:
            import json as _json
            from apps.pid_verification.services.export_service import upload_to_s3 as _s3_upload
            _s3_key = f'pid_verification/legend_sheets/{sheet.project_id}/{legend_id}/extracted_data.json'
            _s3_url = _s3_upload(
                _json.dumps(extracted, indent=2, ensure_ascii=False).encode('utf-8'),
                _s3_key,
                'application/json',
            )
            if _s3_url:
                logger.info('[LegendTask] Uploaded extracted_data to S3: %s', _s3_url)
        except Exception as _s3_exc:
            logger.debug('[LegendTask] S3 upload of extracted_data skipped (non-fatal): %s', _s3_exc)

        # ── Merge into project legend knowledge ────────────────────────────
        if sheet.project_id and extracted:
            sheet.project.refresh_from_db(fields=['legend_knowledge_data'])
            updated_knowledge = merge_into_project_legend(sheet.project, extracted)
            from django.utils import timezone
            sheet.project.legend_knowledge_data = updated_knowledge
            sheet.project.legend_built_at       = timezone.now()
            sheet.project.save(update_fields=['legend_knowledge_data', 'legend_built_at', 'updated_at'])
            logger.info('[LegendTask] Merged legend into project=%s', sheet.project_id)

            # ── Persist merged project knowledge to S3 ─────────────────────
            try:
                import json as _json
                from apps.pid_verification.services.export_service import upload_to_s3 as _s3_upload
                _s3_key = f'pid_verification/projects/{sheet.project_id}/legend_knowledge.json'
                _s3_url = _s3_upload(
                    _json.dumps(updated_knowledge, indent=2, ensure_ascii=False).encode('utf-8'),
                    _s3_key,
                    'application/json',
                )
                if _s3_url:
                    logger.info('[LegendTask] Uploaded merged legend knowledge to S3: %s', _s3_url)
            except Exception as _s3_exc:
                logger.debug('[LegendTask] S3 upload of legend_knowledge skipped (non-fatal): %s', _s3_exc)

        # ── Populate instrument symbol registry ────────────────────────────
        # Run as an independent step so a failure here does NOT abort legend extraction.
        # Pass pre-rendered pages_b64 so the instrument extractor skips its own render.
        if tmp_path and sheet.project_id:
            try:
                from apps.pid_verification.services.instrument_extractor import extract_instrument_symbols
                from apps.pid_verification.services.instrument_registry import save_instrument_symbols as _save_instr
                instr_data   = extract_instrument_symbols(tmp_path, use_ai=True, pages_b64=_shared_pages_b64)
                instr_count  = _save_instr(sheet, instr_data)
                logger.info('[LegendTask] Saved %d instrument symbols for project=%s', instr_count, sheet.project_id)

                # Persist instrument symbols JSON to S3 for future reference
                try:
                    import json as _json
                    from apps.pid_verification.services.export_service import upload_to_s3 as _s3_upload
                    _s3_key = f'pid_verification/legend_sheets/{sheet.project_id}/{legend_id}/instrument_symbols.json'
                    _s3_url = _s3_upload(
                        _json.dumps(instr_data, indent=2, ensure_ascii=False).encode('utf-8'),
                        _s3_key,
                        'application/json',
                    )
                    if _s3_url:
                        logger.info('[LegendTask] Uploaded instrument symbols to S3: %s', _s3_url)
                except Exception as _s3_exc:
                    logger.debug('[LegendTask] S3 upload of instrument symbols skipped (non-fatal): %s', _s3_exc)
            except Exception as instr_exc:
                logger.warning('[LegendTask] Instrument registry population failed (non-fatal): %s', instr_exc)

    except Exception as exc:
        logger.exception('[LegendTask] Extraction failed for legend_id=%s: %s', legend_id, exc)
        sheet.status        = PIDVLegendSheet.Status.FAILED
        sheet.error_message = str(exc)
        sheet.save(update_fields=['status', 'error_message', 'updated_at'])
        raise self.retry(exc=exc)
    finally:
        if tmp_path and need_cleanup:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ===========================================================================
# Reference Data Parsing Task — Excel/CSV line list, equipment list, etc.
# ===========================================================================

@shared_task(
    bind=True,
    name='pid_verification.parse_reference_data',
    max_retries=2,
    default_retry_delay=15,
    soft_time_limit=180,   # 3-min soft limit
    time_limit=240,        # 4-min hard limit
)
def parse_reference_data_task(self, reference_id: str):
    """
    Background task: parse Excel/CSV reference data files.
    
    Pipeline:
      1. Load PIDVReferenceData record.
      2. Resolve the file path (local storage or S3 download).
      3. Parse Excel/CSV using pandas.
      4. Save parsed data to `parsed_data` JSONField.
      5. Update status → completed / failed.
    """
    from apps.pid_verification.models import PIDVReferenceData
    
    logger.info('[ReferenceDataTask] Starting parsing for reference_id=%s', reference_id)
    
    try:
        ref_data = PIDVReferenceData.objects.get(reference_id=reference_id)
    except PIDVReferenceData.DoesNotExist:
        logger.error('[ReferenceDataTask] Reference data %s not found', reference_id)
        return
    
    ref_data.status = PIDVReferenceData.Status.PROCESSING
    ref_data.save(update_fields=['status', 'updated_at'])
    
    tmp_path = None
    need_cleanup = False
    
    try:
        # ── Resolve file path ──────────────────────────────────────────────
        if ref_data.original_file:
            try:
                tmp_path = ref_data.original_file.path
                need_cleanup = False
            except NotImplementedError:
                # S3-backed storage — download to temp file
                s3_key = getattr(ref_data.original_file, 'name', None)
                tmp_path = _download_from_s3(s3_key) if s3_key else None
                need_cleanup = True
        elif ref_data.s3_path:
            tmp_path = _download_from_s3(ref_data.s3_path)
            need_cleanup = True
        else:
            raise ValueError(f'No file available for reference_id={reference_id}')
        
        # ── Parse file using pandas or pdfplumber ──────────────────────────
        import pandas as pd
        from pathlib import Path
        
        file_ext = Path(ref_data.file_name).suffix.lower()
        
        if file_ext in ['.xlsx', '.xls']:
            df = pd.read_excel(tmp_path)
        elif file_ext == '.csv':
            df = pd.read_csv(tmp_path)
        elif file_ext == '.pdf':
            # Extract tables from PDF using pdfplumber
            import pdfplumber
            
            all_rows = []
            columns = None
            
            with pdfplumber.open(tmp_path) as pdf:
                for page_num, page in enumerate(pdf.pages):
                    tables = page.extract_tables()
                    
                    if not tables:
                        logger.warning(
                            '[ReferenceDataTask] No tables found on page %d of reference_id=%s',
                            page_num + 1, reference_id
                        )
                        continue
                    
                    # Process first table on each page
                    for table in tables:
                        if not table or len(table) < 2:  # Need header + at least 1 row
                            continue
                        
                        # First row is header (on first page only)
                        if columns is None:
                            columns = [str(cell).strip() if cell else f'Column_{i}' 
                                      for i, cell in enumerate(table[0])]
                            data_rows = table[1:]
                        else:
                            # Subsequent pages: check if first row is header or data
                            first_row = [str(cell).strip() if cell else '' for cell in table[0]]
                            if first_row == columns:
                                # It's a repeated header, skip it
                                data_rows = table[1:]
                            else:
                                # It's data
                                data_rows = table
                        
                        # Add rows
                        for row in data_rows:
                            if row and any(cell for cell in row):  # Skip empty rows
                                all_rows.append([str(cell).strip() if cell else '' for cell in row])
            
            if not columns or not all_rows:
                raise ValueError(f'No valid tables extracted from PDF: {ref_data.file_name}')
            
            # Convert to DataFrame
            df = pd.DataFrame(all_rows, columns=columns)
        else:
            raise ValueError(f'Unsupported file extension for parsing: {file_ext}')
        
        # Convert DataFrame to list of dicts
        parsed_data = df.fillna('').to_dict(orient='records')
        
        # Build metadata
        metadata = {
            'row_count': len(df),
            'columns': list(df.columns),
            'file_size_bytes': os.path.getsize(tmp_path) if tmp_path else 0,
            'file_type': file_ext,
            'extraction_method': 'pdfplumber' if file_ext == '.pdf' else 'pandas',
        }
        
        # Save results
        ref_data.parsed_data = parsed_data
        ref_data.metadata = metadata
        ref_data.status = PIDVReferenceData.Status.COMPLETED
        ref_data.error_message = ''
        ref_data.save(update_fields=['parsed_data', 'metadata', 'status', 'error_message', 'updated_at'])
        
        logger.info(
            '[ReferenceDataTask] Parsing done for reference_id=%s  rows=%d  columns=%d',
            reference_id, metadata['row_count'], len(metadata['columns']),
        )
        
    except Exception as exc:
        logger.exception('[ReferenceDataTask] Parsing failed for reference_id=%s: %s', reference_id, exc)
        ref_data.status = PIDVReferenceData.Status.FAILED
        ref_data.error_message = str(exc)
        ref_data.save(update_fields=['status', 'error_message', 'updated_at'])
        raise self.retry(exc=exc)
    finally:
        if tmp_path and need_cleanup:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# AI-Powered P&ID Extraction and Checking Tasks
# ---------------------------------------------------------------------------

@shared_task(
    bind=True,
    name='pid_verification.run_ai_checks',
    max_retries=2,
    default_retry_delay=60,
    soft_time_limit=1800,   # 30 min soft limit (for large P&ID sets)
    time_limit=2100,        # 35 min hard limit
)
def run_ai_checks_task(self, run_id: str, context: dict = None):
    """
    AI-Powered P&ID Check Task
    
    Workflow:
      1. Load project with all uploaded P&IDs and reference data
      2. Extract equipment, lines, instruments from P&IDs using vision APIs
      3. Run AUTO checks (two-way reconciliation)
      4. Generate ASSIST check findings
      5. Store results in PIDVAICheckRun
    
    Args:
        run_id: UUID string of PIDVAICheckRun
        context: Dict with:
                 - analysis_mode: 'standard' | 'enhanced_openai' | 'deep_claude' | 'hybrid'
                 - openai_api_key: User-provided OpenAI API key
                 - claude_api_key: User-provided Claude API key
    """
    from apps.pid_verification.models import PIDVAICheckRun, PIDVProject, PIDVDocument, PIDVDrawing, PIDVReferenceData
    from apps.pid_verification.ai_extraction import PIDExtractionEngine
    from apps.pid_verification.ai_checks import AutoCheckExecutor
    import boto3
    from django.conf import settings
    import time
    
    context = context or {}
    start_time = time.time()
    
    logger.info('[AICheckTask] Starting AI check run for run_id=%s', run_id)
    
    try:
        # Load check run
        check_run = PIDVAICheckRun.objects.get(run_id=run_id)
        check_run.status = PIDVAICheckRun.Status.EXTRACTING
        check_run.save(update_fields=['status', 'updated_at'])
        
        project = check_run.project
        analysis_mode = context.get('analysis_mode', 'hybrid')
        openai_key = context.get('openai_api_key')
        claude_key = context.get('claude_api_key')
        
        # Initialize extraction engine
        extractor = PIDExtractionEngine(
            openai_key=openai_key,
            claude_key=claude_key,
            mode=analysis_mode
        )
        
        # Get all drawings from project
        drawings = PIDVDrawing.objects.filter(
            document__project=project,
            document__status=PIDVDocument.Status.COMPLETED
        ).select_related('document')
        
        if not drawings.exists():
            raise ValueError("No completed P&ID drawings found in project")
        
        logger.info('[AICheckTask] Found %d drawings to process', drawings.count())
        
        # Extract elements from each drawing
        all_equipment = []
        all_lines = []
        all_instruments = []
        sheets_processed = 0
        api_calls_made = 0
        
        s3_client = boto3.client('s3')
        
        for drawing in drawings[:10]:  # Limit to 10 sheets for initial implementation
            try:
                # Download image from S3
                doc = drawing.document
                bucket = settings.AWS_STORAGE_BUCKET_NAME
                key = doc.original_file.name if hasattr(doc.original_file, 'name') else doc.s3_path
                
                # Download to temp file
                tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
                s3_client.download_file(bucket, key, tmp_file.name)
                
                # Extract using vision API
                logger.info('[AICheckTask] Extracting sheet: %s (page %d)', drawing.title, drawing.page_index)
                
                extraction_result = extractor.extract_all(tmp_file.name, sheet_number=drawing.title)
                
                all_equipment.extend(extraction_result.get('equipment', []))
                all_lines.extend(extraction_result.get('lines', []))
                all_instruments.extend(extraction_result.get('instruments', []))
                
                sheets_processed += 1
                api_calls_made += 3  # equipment + lines + instruments
                
                # Cleanup
                os.unlink(tmp_file.name)
                
                logger.info('[AICheckTask] Extracted %d equipment, %d lines, %d instruments from sheet %s',
                           len(extraction_result.get('equipment', [])),
                           len(extraction_result.get('lines', [])),
                           len(extraction_result.get('instruments', [])),
                           drawing.title)
                
            except Exception as exc:
                logger.exception('[AICheckTask] Failed to extract from drawing %s: %s', drawing.drawing_id, exc)
                continue
        
        # Store extracted data
        extracted_data = {
            'equipment': all_equipment,
            'lines': all_lines,
            'instruments': all_instruments,
            'overview': {
                'sheet_count': sheets_processed,
                'equipment_count': len(all_equipment),
                'line_count': len(all_lines),
                'instrument_count': len(all_instruments),
            }
        }
        
        check_run.extracted_data = extracted_data
        check_run.status = PIDVAICheckRun.Status.CHECKING
        check_run.save(update_fields=['extracted_data', 'status', 'updated_at'])
        
        logger.info('[AICheckTask] Extraction complete: %d equipment, %d lines, %d instruments',
                   len(all_equipment), len(all_lines), len(all_instruments))
        
        # Load reference data
        reference_data = {}
        ref_data_qs = PIDVReferenceData.objects.filter(
            project=project,
            status=PIDVReferenceData.Status.COMPLETED
        )
        
        for ref_data in ref_data_qs:
            data_type = ref_data.data_type
            reference_data[data_type] = ref_data.parsed_data or []
        
        # Load legend knowledge from project
        if project.legend_knowledge_data:
            reference_data['legend_knowledge'] = project.legend_knowledge_data
        
        logger.info('[AICheckTask] Loaded reference data: %s', list(reference_data.keys()))
        
        # Run AUTO checks
        auto_checker = AutoCheckExecutor()
        check_results = auto_checker.run_all_checks(extracted_data, reference_data)
        
        # Calculate summary statistics
        total_checks = len(check_results)
        auto_count = sum(1 for r in check_results if r.get('check_id', '').startswith('AUTO'))
        pass_count = sum(1 for r in check_results if r.get('result') == 'Pass')
        fail_count = sum(1 for r in check_results if r.get('result') == 'Fail')
        warning_count = sum(1 for r in check_results if r.get('result') == 'Warning')
        
        summary_stats = {
            'total_checks': total_checks,
            'auto_count': auto_count,
            'assist_count': 0,  # Not implemented yet
            'human_count': 0,   # Not implemented yet
            'pass_count': pass_count,
            'fail_count': fail_count,
            'warning_count': warning_count,
        }
        
        # Calculate processing metadata
        processing_time = time.time() - start_time
        estimated_cost = sheets_processed * 0.35  # $0.35 per sheet in hybrid mode
        
        processing_metadata = {
            'sheets_processed': sheets_processed,
            'api_calls_made': api_calls_made,
            'total_cost_usd': round(estimated_cost, 2),
            'processing_time_seconds': round(processing_time, 1),
        }
        
        # Save final results
        check_run.check_results = check_results
        check_run.summary_stats = summary_stats
        check_run.processing_metadata = processing_metadata
        check_run.status = PIDVAICheckRun.Status.COMPLETED
        check_run.completed_at = timezone.now()
        check_run.save(update_fields=[
            'check_results', 'summary_stats', 'processing_metadata',
            'status', 'completed_at', 'updated_at'
        ])
        
        logger.info('[AICheckTask] AI check run complete for run_id=%s  checks=%d  pass=%d  fail=%d  time=%.1fs',
                   run_id, total_checks, pass_count, fail_count, processing_time)
        
    except Exception as exc:
        logger.exception('[AICheckTask] AI check run failed for run_id=%s: %s', run_id, exc)
        try:
            check_run = PIDVAICheckRun.objects.get(run_id=run_id)
            check_run.status = PIDVAICheckRun.Status.FAILED
            check_run.error_message = str(exc)
            check_run.save(update_fields=['status', 'error_message', 'updated_at'])
        except Exception:
            pass
        raise self.retry(exc=exc)
