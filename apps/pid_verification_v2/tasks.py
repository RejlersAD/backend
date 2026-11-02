"""
Celery Background Tasks — P&ID Verification
============================================
Task pipeline (all chained in a single async job):
  1. Segment document into drawings
  2. For each drawing: extract → build graph → run rule engine → save findings
  3. Generate Excel & PDF reports → upload to S3
  4. Update document status = completed (or failed)
"""
from apps.core.ai_consumer_clients import provider_available, provider_api_key
import logging
import os
import re
import shutil
import tempfile

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.utils import timezone

from apps.pid_verification_v2.services.processing_config import TASK_CONFIG

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
# processed sequentially inside a single task. Sequential per-page OCR/
# extraction does not scale: a real 28-page P&ID set was measured to still
# be mid-extraction after the FULL 30-minute soft time limit expired,
# because each page runs a multi-pass OCR strategy (multiple DPI levels ×
# image variants × Tesseract configs). Fanning out lets multiple worker
# processes extract different pages concurrently, and gives each page its
# own bounded time budget instead of one giant task accumulating every
# page's cost sequentially.
MULTI_PAGE_PARALLEL_THRESHOLD = 2


@shared_task(
    bind=True,
    name='pid_verification_v2.process_document',
    max_retries=2,
    default_retry_delay=30,
    # Soft-coded from processing_config.TASK_CONFIG — previously hardcoded to
    # 540s/600s (9/10 min), which was tuned for single-page documents. Since
    # this task now processes EVERY page of a multi-page P&ID PDF in one run,
    # it needs the full budget already documented (and intended) for this
    # task in TASK_CONFIG.
    soft_time_limit=TASK_CONFIG['soft_time_limit'],  # 30 min soft limit
    time_limit=TASK_CONFIG['time_limit'],            # 35 min hard limit
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
    from apps.pid_verification_v2.models import PIDVDocument, PIDVDrawing, PIDVFinding
    from apps.pid_verification_v2.services.segmentation  import segment_document
    from apps.pid_verification_v2.services.extraction    import extract_drawing
    from apps.pid_verification_v2.services.graph_builder import build_graph
    from apps.pid_verification_v2.services.rule_engine   import run_rules
    from apps.pid_verification_v2.services.export_service import (
        generate_excel, generate_pdf, upload_to_s3
    )

    logger.info('[PIDVTask] Starting processing for document_id=%s with context=%s', document_id, context)

    # ── 1. Load document ──────────────────────────────────────────────────
    try:
        doc = PIDVDocument.objects.get(document_id=document_id)
    except PIDVDocument.DoesNotExist as dne:
        # BUG FIX (29 Oct 2026): previously this logged and RETURNED, so an
        # upload whose task executed before the row was visible to the
        # worker's connection (observed live: doc fff30a03 stuck at
        # 'uploaded' forever — task ran 4ms after dispatch and saw no row)
        # was silently never processed. Retry briefly instead of giving up;
        # also drop the worker's DB connection first so a stale transaction
        # snapshot on a reused connection can't keep hiding the new row.
        from django.db import connections
        for conn in connections.all():
            conn.close()
        if self.request.retries < (self.max_retries or 0):
            logger.warning(
                '[PIDVTask] Document %s not found yet (attempt %d) — retrying in 30s',
                document_id, self.request.retries + 1,
            )
            raise self.retry(exc=dne, countdown=30)
        logger.error(
            '[PIDVTask] Document %s not found after %d retries — giving up',
            document_id, self.max_retries,
        )
        return

    doc.status = PIDVDocument.Status.PROCESSING
    doc.save(update_fields=['status', 'updated_at'])

    # ── Legend quality gate ───────────────────────────────────────────────
    # Do not start P&ID quality checking until the project has at least one
    # completed legend sheet OR legend_knowledge_data is already populated
    # (e.g. loaded from S3 cache on a previous upload).
    if LEGEND_REQUIRED_FOR_QC and doc.project_id:
        from apps.pid_verification_v2.models import PIDVReferenceData
        project = doc.project
        # 29 Oct 2026: an uploaded Excel legend register (data_type
        # 'legend_pid', e.g. Borouge_PID_Legend_Register.xlsx) is a valid
        # legend input alongside PDF legend sheets — it feeds the legend
        # lookup via legend_bridge / ComparisonEngineStage.
        legend_ready = (
            project.legend_knowledge_data is not None
            or project.legend_sheets.filter(status='completed').exists()
            or project.reference_data.filter(
                data_type=PIDVReferenceData.DataType.LEGEND_PID,
                status=PIDVReferenceData.Status.COMPLETED,
            ).exclude(parsed_data__isnull=True).exists()
        )
        if not legend_ready:
            doc.status        = PIDVDocument.Status.LEGEND_PENDING
            doc.error_message = (
                'Legend symbols have not been extracted yet.  '
                'Please upload a legend sheet (PDF) or a legend register '
                '(Excel) for this project and wait for extraction to '
                'complete before running the quality check.'
            )
            doc.save(update_fields=['status', 'error_message', 'updated_at'])
            logger.warning(
                '[PIDVTask] Blocked document_id=%s — no legend for project=%s',
                document_id, doc.project_id,
            )
            return   # not a failure — user must upload a legend first

    # ── V2 ORCHESTRATOR: Configuration-driven processing pipeline ────────
    USE_V2_ORCHESTRATOR = True  # Feature flag - set to False to use legacy pipeline

    if USE_V2_ORCHESTRATOR:
        try:
            from apps.pid_verification_v2.services.orchestrator import (
                PipelineOrchestrator,
                PipelineContext,
                update_processing_progress,
            )
            from apps.pid_verification_v2.services.processing_config import get_stage_config

            logger.info('[PIDVTask] Using V2 Orchestrator for document_id=%s', document_id)

            # ── Load reference symbol pictures ONCE for this whole document
            # run — not once per page. Reused by every page (inline or
            # fanned-out) instead of every page re-reading/re-encoding the
            # same images from storage. Always fetched fresh from
            # LegendSymbolImage (never a hardcoded count), so this scales to
            # any library size automatically. Only bothers fetching when a
            # Claude key is actually present — Vision won't run without one.
            _symbol_images = []
            if provider_available('anthropic', context.get('claude_api_key')) and doc.project_id:
                from apps.pid_verification_v2.services.legend_bridge import get_symbol_images_for_project
                try:
                    _symbol_images = get_symbol_images_for_project(doc.project)
                    logger.info(
                        '[PIDVTask] Loaded %d reference symbol image(s) for document_id=%s',
                        len(_symbol_images), document_id,
                    )
                except Exception:
                    logger.warning('[PIDVTask] Could not load symbol images (non-fatal)', exc_info=True)

            # Reprocess idempotency for the legend/symbol bridge — clear this
            # document's previous PIDVComparisonFinding rows ONCE, up front,
            # before any page (inline or fanned-out) writes fresh ones. Must
            # happen here rather than inside LegendSymbolBridgeStage itself:
            # under the parallel per-page fan-out, that stage runs once per
            # page in a SEPARATE task each — deleting there would wipe out
            # whichever other page's findings were already written.
            if doc.project_id:
                from apps.pid_verification_v2.models import PIDVComparisonFinding
                PIDVComparisonFinding.objects.filter(
                    project=doc.project,
                    finding_type=PIDVComparisonFinding.FindingType.SYMBOL_LEGEND_MATCH,
                    evidence__document_id=str(doc.document_id),
                ).delete()

            # ── Peek at page count first ──────────────────────────────────
            # Large multi-page documents are fanned out to parallel per-page
            # subtasks (see MULTI_PAGE_PARALLEL_THRESHOLD) instead of being
            # processed sequentially in this single task, which cannot
            # realistically finish 20+ pages of OCR/extraction within any
            # sane Celery time limit.
            from dataclasses import asdict as _asdict

            _peek_file_path = _resolve_file_path(doc)
            _peek_segments  = segment_document(str(doc.document_id), _peek_file_path)
            logger.info('[PIDVTask] %d page(s) segmented for document_id=%s', len(_peek_segments), document_id)

            if len(_peek_segments) > MULTI_PAGE_PARALLEL_THRESHOLD:
                from celery import chord

                header = [
                    process_pid_page.s(str(doc.document_id), _asdict(seg), context, _symbol_images)
                    for seg in _peek_segments
                ]
                chord(header)(finalize_pid_document.s(str(doc.document_id)))
                logger.info(
                    '[PIDVTask] Dispatched %d parallel page-task(s) for document_id=%s',
                    len(_peek_segments), document_id,
                )
                return  # doc stays PROCESSING; finalize_pid_document completes it

            # Small document (<= threshold pages) — process inline, same as
            # before.
            pipeline_context = PipelineContext(
                document_id=str(doc.document_id),
                document=doc,
                project=doc.project if doc.project_id else None,
                user_context=context,
                symbol_images=_symbol_images,
            )
            
            # Execute orchestrated pipeline
            orchestrator = PipelineOrchestrator()
            result_context = orchestrator.execute(pipeline_context)
            
            # Check for critical failure
            if result_context.has_critical_failure():
                doc.status = PIDVDocument.Status.FAILED
                # Surface the actual failed stage's error (e.g. extraction's
                # NoExtractionMethodAvailableError: "Please install
                # Tesseract OR add a Claude API key...") instead of a
                # generic message — this is exactly the "don't show
                # completed with 0 results, show a clear error" requirement.
                _failed_result = next(
                    (r for r in result_context.stage_results
                     if not r.success and get_stage_config(r.stage_id).critical),
                    None,
                )
                doc.error_message = (
                    _failed_result.error if _failed_result and _failed_result.error
                    else 'Critical processing stage failed. Check logs for details.'
                )
                doc.save(update_fields=['status', 'error_message', 'updated_at'])
                logger.error('[PIDVTask] Pipeline failed for document_id=%s: %s', document_id, doc.error_message)
                return
            
            # Create/update ONE PIDVDrawing per page/segment (soft-coded —
            # a multi-page P&ID PDF is segmented into N pages by
            # SegmentationStage; each page must get its own drawing row so
            # the frontend page-switcher shows all of them, not just page 1).
            total_findings_created = 0
            for seg in result_context.segments:
                total_findings_created += _persist_segment_result(doc, seg, result_context)

            logger.info(
                '[PIDVTask] Persisted %d drawing(s) / %d total finding(s) for document_id=%s',
                len(result_context.segments), total_findings_created, document_id,
            )

            _finalize_cross_reference(doc, document_id)

            # Mark document as completed
            doc.status = PIDVDocument.Status.COMPLETED
            doc.error_message = ''  # clear stale gate/legend messages from earlier attempts
            doc.save(update_fields=['status', 'error_message', 'updated_at'])
            logger.info('[PIDVTask] V2 Pipeline completed successfully for document_id=%s', document_id)

            # Result caching (S3/local) — never fatal: a caching failure
            # shouldn't turn a successful analysis into a failed one. Same
            # pattern as apps.pid_verification.tasks._finalize_document.
            try:
                from apps.pid_verification_v2.services.results_cache import save_results_cache
                save_results_cache(doc, doc.file_hash)
            except Exception:
                logger.exception('[PIDVTask] Result cache write failed for document_id=%s (non-fatal)', document_id)

            return
            
        except SoftTimeLimitExceeded:
            # Do NOT fall back to the legacy pipeline here — the soft time
            # limit firing means the task is almost out of its time budget,
            # so re-running the whole extraction from scratch in the legacy
            # pipeline would only guarantee a hard SIGKILL with no chance to
            # mark the document FAILED. Propagate to the outer handler below,
            # which marks the document FAILED and lets Celery retry cleanly.
            raise
        except Exception as orch_exc:
            logger.error(
                '[PIDVTask] V2 Orchestrator failed for document_id=%s: %s',
                document_id, orch_exc, exc_info=True
            )
            # Fall through to legacy pipeline
            logger.warning('[PIDVTask] Falling back to legacy pipeline')
    
    # ── LEGACY PIPELINE (V1 Logic) — Fallback if orchestrator disabled ───
    try:
        # ── 2. Resolve file path ──────────────────────────────────────────
        file_path = _resolve_file_path(doc)

        # ── 3. Segment into drawings ──────────────────────────────────────
        segments = segment_document(str(doc.document_id), file_path)
        logger.info('[PIDVTask] %d drawing(s) segmented', len(segments))

        # ── 3b. Resolve per-project legend (project legend → global fallback) ──
        project_legend = None
        if doc.project_id and doc.project and doc.project.legend_knowledge_data:
            project_legend = doc.project.legend_knowledge_data
            logger.info('[PIDVTask] Using per-project legend for project=%s', doc.project.project_id)

        # Reference symbol pictures — loaded ONCE for the whole document
        # (same reasoning as the V2 orchestrator path above), reused for
        # every page in the loop below instead of re-fetched per page.
        _legacy_symbol_images = []
        _legacy_pdf_bytes = None
        if provider_available('anthropic', context.get('claude_api_key')) and doc.project_id:
            from apps.pid_verification_v2.services.legend_bridge import get_symbol_images_for_project
            try:
                _legacy_symbol_images = get_symbol_images_for_project(doc.project)
            except Exception:
                logger.warning('[PIDVTask] Could not load symbol images (non-fatal)', exc_info=True)
            try:
                with open(file_path, 'rb') as _fh:
                    _legacy_pdf_bytes = _fh.read()
            except Exception:
                logger.warning('[PIDVTask] Could not read file for Vision (non-fatal)', exc_info=True)

        all_findings_count = 0

        for seg in segments:
            # Save drawing record (idempotent via get_or_create)
            drawing_obj, _ = PIDVDrawing.objects.get_or_create(
                document=doc,
                drawing_id=seg.drawing_id,
                defaults={
                    'title':      seg.title,
                    'page_index': seg.page_index,
                    'metadata':   seg.metadata,
                }
            )
            # Title-block refresh on reprocess (bug fix 29 Oct 2026 —
            # same as _persist_segment_result): get_or_create keeps the
            # first run's row, so refresh title + title_block here too.
            _upd = []
            if seg.title and seg.title != drawing_obj.title:
                drawing_obj.title = seg.title
                _upd.append('title')
            _tb = (seg.metadata or {}).get('title_block') if isinstance(seg.metadata, dict) else None
            if _tb:
                _md = drawing_obj.metadata or {}
                if _md.get('title_block') != _tb:
                    _md['title_block'] = _tb
                    drawing_obj.metadata = _md
                    _upd.append('metadata')
            if _upd:
                drawing_obj.save(update_fields=list(set(_upd)))
            # Sheet registry: one PDF page = one P&ID (30 Oct 2026).
            try:
                from apps.pid_verification_v2.services.sheets import sync_sheet_for_drawing
                sync_sheet_for_drawing(doc, drawing_obj)
            except Exception:
                logger.warning('[PIDVTask] Sheet sync failed (non-fatal)', exc_info=True)
            # Clear any previous findings (re-process idempotency)
            drawing_obj.findings.all().delete()

            # ── 4. Extract elements (hybrid Tesseract + AI Vision) ─────────
            _legacy_extraction_provider = 'claude' if provider_available('anthropic', context.get('claude_api_key')) else 'openai'
            _legacy_extraction_key = context.get('claude_api_key') if _legacy_extraction_provider == 'claude' else context.get('openai_api_key')
            extraction = extract_drawing(
                file_path, page_index=seg.page_index, legend_data=project_legend,
                api_key=_legacy_extraction_key, provider=_legacy_extraction_provider,
            )

            # Persist extraction diagnostics per drawing for frontend transparency.
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
                # Multi-angle pipeline designations (H + V combined, deduplicated)
                'line_tags': len(extraction.get('line_tags', [])),
                'line_tags_multi_angle': sum(
                    1 for lt in extraction.get('line_tags', []) if lt.get('multi_angle')
                ),
            }
            metadata = drawing_obj.metadata or {}
            metadata['extraction_summary'] = extraction_summary
            # Real tag anchor coordinates for v2 smart overlay (soft-coded, additive).
            tag_positions = extraction.get('tag_positions', {})
            if tag_positions:
                metadata['tag_positions'] = tag_positions
            # Pipeline line designations with orientation info (H/V multi-angle).
            line_tags = extraction.get('line_tags', [])
            if line_tags:
                metadata['line_tags'] = line_tags
            # Red-colored annotations (revision marks, HOLDs, scope-cloud items).
            red_annotations = extraction.get('red_annotations', [])
            if red_annotations:
                metadata['red_annotations'] = red_annotations
            drawing_obj.metadata = metadata
            drawing_obj.save(update_fields=['metadata'])

            # ── 5. Build graph ────────────────────────────────────────────
            graph = build_graph(extraction)

            # ── 6. Run deterministic rule engine ─────────────────────────
            rule_findings = run_rules(extraction, graph)
            
            # ── 6b. V2 COMPARISON ENGINE — Cross-document comparison ─────
            # Run comparison-based analysis (V2 feature)
            comparison_findings = []
            try:
                from apps.pid_verification_v2.services.comparison_engine import run_all_comparisons
                
                # Fetch reference data for comparison
                legend_data = project_legend  # Already resolved above
                line_list_data = []
                equipment_list_data = []
                instrument_index_data = []
                
                # Fetch Line List data if available
                if doc.project_id and doc.project:
                    # TODO: Load actual line list from database/Excel
                    # For now, use empty list - will be populated when Line List import is implemented
                    logger.info('[PIDVTask] Line List comparison: No reference data available yet')
                
                # Fetch Equipment List data if available
                if doc.project_id and doc.project:
                    # TODO: Load actual equipment list from database
                    # For now, use empty list
                    logger.info('[PIDVTask] Equipment List comparison: No reference data available yet')
                
                # Fetch Instrument Index data if available
                if doc.project_id and doc.project:
                    # TODO: Load actual instrument index from database
                    # For now, use empty list
                    logger.info('[PIDVTask] Instrument Index comparison: No reference data available yet')
                
                # Run all 4 comparison types
                logger.info('[PIDVTask] Running V2 comparison engine for drawing_id=%s', seg.drawing_id)
                comparison_results = run_all_comparisons(
                    extraction=extraction,
                    legend_data=legend_data,
                    line_list_data=line_list_data,
                    equipment_list_data=equipment_list_data,
                    instrument_index_data=instrument_index_data,
                    ai_api_key=context.get('claude_api_key'),
                )
                
                # Store comparison results in drawing metadata for frontend access
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
                
                # Convert comparison findings to RuleFinding format
                from apps.pid_verification_v2.services.rule_engine import RuleFinding
                
                for comp_type, result in comparison_results.items():
                    for finding in result.findings:
                        # Generate rule ID based on comparison type and category
                        rule_prefix = {
                            'legend': 'LGN',
                            'linelist': 'LSZ',
                            'equipment': 'EQP',
                            'instrument': 'IMS'
                        }.get(comp_type, 'CMP')
                        
                        category_suffix = {
                            'missing': '001',
                            'extra': '002',
                            'mismatch': '003'
                        }.get(finding.category, '999')
                        
                        rule_id = f'{rule_prefix}-{category_suffix}'
                        
                        comparison_findings.append(RuleFinding(
                            category=comp_type,
                            rule_id=rule_id,
                            issue_observed=finding.issue_observed,
                            action_required=f'Review and resolve {finding.category} discrepancy',
                            evidence=finding.evidence,
                            direction='N/A',
                            severity=finding.severity
                        ))
                
                logger.info(
                    '[PIDVTask] V2 Comparison complete: %d comparison findings generated',
                    len(comparison_findings)
                )
                
            except Exception as comp_exc:
                logger.error(
                    '[PIDVTask] Comparison engine failed: %s',
                    str(comp_exc), exc_info=True
                )
                # Store error in metadata but continue processing
                metadata = drawing_obj.metadata or {}
                metadata['comparison_error'] = {
                    'error': str(comp_exc),
                    'timestamp': str(timezone.now())
                }
                drawing_obj.metadata = metadata
                drawing_obj.save(update_fields=['metadata'])
            
            # ── 6c. AI Analysis (BYOK) — Optional enhancement ────────────
            ai_findings = []
            if context:
                analysis_mode = context.get('analysis_mode', 'standard')
                
                if analysis_mode != 'standard':
                    logger.info(
                        '[PIDVTask] Running AI analysis mode=%s for drawing_id=%s',
                        analysis_mode, seg.drawing_id
                    )
                    
                    try:
                        from apps.pid_verification_v2.services.ai_analysis import (
                            run_openai_analysis,
                            run_hybrid_analysis,
                            to_rule_findings,
                        )
                        from apps.pid_verification_v2.services.legend_bridge import (
                            run_page_vision_analysis, SYMBOL_BATCH_SIZE,
                        )

                        # Prepare drawing data for AI analysis
                        drawing_data = {
                            'instruments': extraction.get('instruments', []),
                            'valves': extraction.get('valves', []),
                            'equipment': extraction.get('equipment', []),
                            'tags': extraction.get('tags', []),
                            'line_tags': extraction.get('line_tags', []),
                            'line_sizes': extraction.get('line_sizes', []),
                            'notes': extraction.get('notes', []),
                        }

                        # This page's rendered image — required for the
                        # deep_claude/hybrid Claude leg (real Vision, not
                        # text-only). enhanced_openai stays text-only.
                        page_image_b64 = None
                        if analysis_mode in ('deep_claude', 'hybrid') and _legacy_pdf_bytes is not None:
                            try:
                                from apps.pid_checker_v2.services.vision_extractor import (
                                    _render_single_page, _prepare_image_b64, VISION_OVERVIEW_MAX_DIMENSION_PX,
                                )
                                page_img = _render_single_page(_legacy_pdf_bytes, seg.page_index)
                                page_image_b64 = _prepare_image_b64(page_img, VISION_OVERVIEW_MAX_DIMENSION_PX)
                            except Exception:
                                logger.warning('[PIDVTask] Could not render page image for Vision', exc_info=True)

                        raw_findings = []
                        symbols = []
                        # Route to appropriate AI service
                        if analysis_mode == 'enhanced_openai':
                            openai_key = provider_api_key('openai', fallback=lambda: (context.get('openai_api_key')))
                            if openai_key:
                                raw_findings = run_openai_analysis(drawing_data, openai_key)['findings']

                        elif analysis_mode == 'deep_claude':
                            claude_key = provider_api_key('anthropic', fallback=lambda: (context.get('claude_api_key')))
                            if claude_key and page_image_b64:
                                result = run_page_vision_analysis(
                                    drawing_data, claude_key, page_image_b64,
                                    symbol_images=_legacy_symbol_images,
                                )
                                if result:
                                    raw_findings = result['findings']
                                    symbols = result['symbols']

                        elif analysis_mode == 'hybrid':
                            openai_key = provider_api_key('openai', fallback=lambda: (context.get('openai_api_key')))
                            claude_key = provider_api_key('anthropic', fallback=lambda: (context.get('claude_api_key')))
                            if openai_key and claude_key:
                                result = run_hybrid_analysis(
                                    drawing_data, openai_key, claude_key,
                                    page_image_b64=page_image_b64,
                                    symbol_images=_legacy_symbol_images[:SYMBOL_BATCH_SIZE],
                                )
                                raw_findings = result['findings']
                                symbols = result['symbols']

                        ai_findings = to_rule_findings(raw_findings)

                        logger.info(
                            '[PIDVTask] AI analysis completed: %d findings, %d symbols from %s',
                            len(ai_findings), len(symbols), analysis_mode
                        )
                    
                    except Exception as ai_exc:
                        logger.error(
                            '[PIDVTask] AI analysis failed for mode=%s: %s',
                            analysis_mode, str(ai_exc), exc_info=True
                        )
                        # Continue processing with rule-based findings only
                        # Store error in drawing metadata for user visibility
                        metadata = drawing_obj.metadata or {}
                        metadata['ai_analysis_error'] = {
                            'mode': analysis_mode,
                            'error': str(ai_exc),
                            'timestamp': str(timezone.now())
                        }
                        drawing_obj.metadata = metadata
                        drawing_obj.save(update_fields=['metadata'])
            
            # ── 7. Merge rule-based, comparison, and AI findings ─────────
            # Soft-coded: V2 comparison findings + AI findings are additive to rule-based findings
            all_findings = rule_findings + comparison_findings + ai_findings

            # ── 8. Persist findings ───────────────────────────────────────
            bulk = []
            for sl, rf in enumerate(all_findings, start=1):
                bulk.append(PIDVFinding(
                    drawing         = drawing_obj,
                    sl_no           = sl,
                    category        = rf.category,
                    rule_id         = rf.rule_id,
                    issue_observed  = rf.issue_observed,
                    action_required = rf.action_required,
                    evidence        = rf.evidence,
                    direction       = rf.direction,
                    severity        = rf.severity,
                    status          = 'open',
                ))
            PIDVFinding.objects.bulk_create(bulk)
            all_findings_count += len(bulk)
            logger.info('[PIDVTask] Drawing %s → %d findings', seg.drawing_id, len(bulk))

        # ── 9. Generate & upload reports ──────────────────────────────────
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
        doc.error_message = ''  # clear stale gate/legend messages from earlier attempts
        doc.save(update_fields=['status', 'error_message', 'excel_s3_url', 'pdf_s3_url', 'updated_at'])
        logger.info('[PIDVTask] Completed document_id=%s  total_findings=%d', document_id, all_findings_count)

    except Exception as exc:
        logger.exception('[PIDVTask] Processing failed for document_id=%s: %s', document_id, exc)
        doc.status        = PIDVDocument.Status.FAILED
        doc.error_message = (
            'Processing took longer than the allowed time limit for this document '
            '(likely a large, multi-page P&ID set). The job will be retried automatically.'
            if isinstance(exc, SoftTimeLimitExceeded) else str(exc)
        )
        doc.save(update_fields=['status', 'error_message', 'updated_at'])
        raise self.retry(exc=exc)


def _persist_segment_result(doc, seg, result_context) -> int:
    """
    Create/update the PIDVDrawing row for ONE page/segment and persist its
    findings (rule + comparison + AI engines).

    Shared by both the inline (small document) V2 orchestrator path and the
    parallel per-page Celery task (`process_pid_page`, large documents), so
    persistence shape stays identical no matter which path actually ran the
    extraction/rules/comparison for this page.

    Returns the number of findings created for this page.
    """
    from apps.pid_verification_v2.models import PIDVDrawing, PIDVFinding

    drawing_obj, _ = PIDVDrawing.objects.get_or_create(
        document=doc,
        drawing_id=seg.drawing_id,
        defaults={
            'title': seg.title,
            'page_index': seg.page_index,
            'metadata': seg.metadata,
        },
    )

    # ── Title-block refresh on REPROCESS (bug fix 29 Oct 2026) ──────────
    # get_or_create() above keeps the FIRST run's row untouched, so a drawing
    # segmented before the title-block fix kept the misread title ('AP7E')
    # and never gained metadata['title_block']. Segmentation runs fresh on
    # every process/reprocess (process_pid_document always re-segments from
    # the stored PDF), so persist the freshly computed title-block fields
    # into the existing row here.
    update_fields = []
    if seg.title and seg.title != drawing_obj.title:
        drawing_obj.title = seg.title
        update_fields.append('title')
    _tb = (seg.metadata or {}).get('title_block') if isinstance(seg.metadata, dict) else None
    if _tb:
        _md = drawing_obj.metadata or {}
        if _md.get('title_block') != _tb:
            _md['title_block'] = _tb
            drawing_obj.metadata = _md
            update_fields.append('metadata')
    if update_fields:
        drawing_obj.save(update_fields=list(set(update_fields)))

    # Sheet registry: one PDF page = one P&ID (30 Oct 2026).
    try:
        from apps.pid_verification_v2.services.sheets import sync_sheet_for_drawing
        sync_sheet_for_drawing(doc, drawing_obj)
    except Exception:
        logger.warning('[PIDVTask] Sheet sync failed (non-fatal)', exc_info=True)

    # Clear existing findings for this page (re-process idempotency)
    drawing_obj.findings.all().delete()

    seg_bucket = result_context.segment_data.get(seg.drawing_id, {})

    # Store extraction/comparison results + overlay coordinate data in this
    # page's metadata (mirrors legacy pipeline shape so the frontend's
    # Drawing Layout overlay works identically).
    metadata = drawing_obj.metadata or {}
    metadata['extraction_summary'] = seg_bucket.get('extraction_summary', {})
    metadata['comparison_summary'] = seg_bucket.get('comparison_summary', {})
    metadata['processing_duration'] = result_context.get_total_duration()

    extraction = seg_bucket.get('extraction', {}) or {}
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

    # ── Cross-reference: tag-occurrence index + continuation markers ─────
    # Mirrors apps.pid_verification's own _process_one_page (V1) — see its
    # comment for the full rationale. Feeds
    # apps.pid_verification_v2.services.cross_reference: group_multi_page_
    # tags() needs one PIDVTagIndex row per tag OCCURRENCE,
    # detect_continuation_refs() needs this page's raw text while it's
    # already in memory. Non-fatal — a failure here never loses this
    # page's rule/comparison/AI findings above.
    try:
        from apps.pid_verification_v2.models import PIDVTagIndex
        from apps.pid_verification_v2.services.comparison_engine import normalize_tag
        from apps.pid_verification_v2.services.cross_reference import detect_continuation_refs

        # BUG FIX: clear this page's PREVIOUS tag-index rows before writing
        # new ones — see V1's matching comment (apps.pid_verification.
        # tasks._process_one_page) for the full rationale: without this,
        # re-processing accumulates stale rows and eventually reports a
        # tag as appearing on more pages than it actually does.
        PIDVTagIndex.objects.filter(document=doc, page_number=seg.page_index).delete()

        tag_index_rows = []
        for item in extraction.get('instruments', []):
            raw = item.get('tag') or ''
            if raw:
                # strip_leading_digit_noise=True only for instruments — see
                # V1's matching comment (normalize_tag's own docstring
                # scopes this flag to instrument-tag PDF-bleed artifacts
                # only; applying it to line tags would strip a real pipe
                # size, a false match).
                tag_index_rows.append(PIDVTagIndex(
                    document=doc, page_number=seg.page_index,
                    tag=normalize_tag(raw, strip_leading_digit_noise=True),
                    raw_tag=raw, tag_type='instrument',
                ))
        for item in extraction.get('valves', []):
            raw = item.get('tag') or ''
            if raw:
                tag_index_rows.append(PIDVTagIndex(
                    document=doc, page_number=seg.page_index,
                    tag=normalize_tag(raw), raw_tag=raw, tag_type='valve',
                ))
        for item in extraction.get('equipment', []):
            raw = item.get('tag') or ''
            if raw:
                tag_index_rows.append(PIDVTagIndex(
                    document=doc, page_number=seg.page_index,
                    tag=normalize_tag(raw), raw_tag=raw, tag_type='equipment',
                ))
        for item in extraction.get('line_tags', []):
            raw = item.get('text') or ''
            if raw:
                tag_index_rows.append(PIDVTagIndex(
                    document=doc, page_number=seg.page_index,
                    tag=normalize_tag(raw), raw_tag=raw, tag_type='line',
                ))
        if tag_index_rows:
            PIDVTagIndex.objects.bulk_create(tag_index_rows)

        raw_text = extraction.get('raw_text', '') or ''
        continuation_refs = detect_continuation_refs(raw_text)
        if continuation_refs:
            metadata = drawing_obj.metadata or {}
            metadata['continuation_refs'] = continuation_refs
            drawing_obj.metadata = metadata
            drawing_obj.save(update_fields=['metadata'])
    except Exception:
        logger.warning(
            '[PIDVTask] Cross-reference indexing failed for drawing_id=%s (non-fatal)',
            seg.drawing_id, exc_info=True,
        )

    # ── Extraction items table (engine prompt §4) ────────────────────────
    # Persist this page's extraction into PIDVExtractionItem (lines /
    # instruments / equipment / connectors / notes). Non-fatal — a failure
    # here never loses the findings above.
    _layout_out: list = []  # receives the Linde layout for project rules
    try:
        from apps.pid_verification_v2.services.extraction_items import persist_extraction_items
        from apps.pid_verification_v2.services.legend_bridge import get_legend_lookup_fields

        _user = getattr(doc, 'uploaded_by', None)
        _legend_fields = get_legend_lookup_fields(_user, project=doc.project if doc.project_id else None)
        _cont_refs = (drawing_obj.metadata or {}).get('continuation_refs', [])
        _file_path = getattr(result_context, 'file_path', None) or _resolve_file_path(doc)
        persist_extraction_items(
            doc, drawing_obj, extraction,
            continuation_refs=_cont_refs,
            legend_fields=_legend_fields,
            file_path=_file_path,
            layout_out=_layout_out,
        )
    except Exception:
        logger.warning(
            '[PIDVTask] Extraction-item persistence failed for drawing_id=%s (non-fatal)',
            seg.drawing_id, exc_info=True,
        )

    # Merge findings for THIS page only
    rule_findings = list(seg_bucket.get('rule_findings', []))
    page_findings = (
        rule_findings +
        seg_bucket.get('comparison_findings', []) +
        seg_bucket.get('ai_findings', [])
    )

    # ── Linde-layout post-processing of rule findings (29 Oct 2026) ──────
    # When the Linde layout reader succeeded, the sheet's notes/connectors
    # are structured extraction items — RED-001's red-text groupings are
    # superseded (they duplicate note items) and LN-001/LN-002 code checks
    # validate against the uploaded legend register instead of the static
    # project list.
    layout = _layout_out[0] if _layout_out else None
    register_codes = {}
    if layout is not None and doc.project_id:
        from apps.pid_verification_v2.services.legend_register import get_register_codes
        register_codes = get_register_codes(doc.project)

    if layout is not None:
        def _code_in_register(code):
            cu = (code or '').strip().upper()
            return any(cu in {c.upper() for c in tbl} for tbl in register_codes.values())

        filtered = []
        for rf in rule_findings:
            if rf.rule_id == 'RED-001':
                continue  # superseded by structured note items + BRG rules
            if rf.rule_id in ('LN-001', 'LN-002') and register_codes:
                # keep only codes genuinely unknown to the legend register
                m = re.search(r"'([A-Za-z0-9]+)'", rf.evidence or rf.issue_observed or '')
                if m and _code_in_register(m.group(1)):
                    continue
            filtered.append(rf)
        page_findings = filtered + page_findings[len(rule_findings):]

        # ── Borouge project rules (BRG-001…007, see services/project_rules)
        try:
            from apps.pid_verification_v2.services.project_rules import run_borouge_rules
            from apps.pid_verification_v2.services.legend_register import get_register_documents
            from apps.pid_verification_v2.services.rule_engine import RuleFinding

            for prf in run_borouge_rules(layout, register_codes, get_register_documents(doc.project),
                                         project=doc.project):
                rf = RuleFinding(
                    category='project_rule',
                    rule_id=prf['rule_id'],
                    issue_observed=prf['issue_observed'],
                    action_required=prf['action_required'],
                    evidence=prf.get('evidence', ''),
                    severity=prf.get('severity', 'major'),
                )
                rf._item_texts = prf.get('item_texts', [])
                rf._confidence = prf.get('confidence')
                page_findings.append(rf)
        except Exception:
            logger.warning('[PIDVTask] Project rules failed for drawing_id=%s (non-fatal)',
                           seg.drawing_id, exc_info=True)

    # Unique sequential numbering AFTER sorting (bug fix 29 Oct 2026:
    # cross-reference findings previously restarted at 1 → duplicate F1/F2).
    page_findings.sort(key=lambda f: (f.rule_id, f.issue_observed))

    # Entity linking: resolve each finding's referenced extraction items to
    # entity_id + bbox (§3/§7d), and stamp the items' flag + finding_ids.
    from apps.pid_verification_v2.models import PIDVExtractionItem
    page_items = list(PIDVExtractionItem.objects.filter(document=doc, page=drawing_obj.page_index))
    items_by_text = {}
    for it in page_items:
        items_by_text.setdefault(it.text.strip(), []).append(it)
        # Whitespace-normalised alias: rule item_texts come from the layout
        # parser whose spacing can differ from the stored item text (30 Oct
        # 2026 — FB / A0JU01 findings missed their entity link over this).
        items_by_text.setdefault(re.sub(r'\s+', '', it.text), []).append(it)

    bulk = []
    created_links = []  # (finding_index_in_bulk, [item])
    for sl, rf in enumerate(page_findings, start=1):
        finding = PIDVFinding(
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
        linked_items = []
        seen_item_ids = set()
        for txt in getattr(rf, '_item_texts', []) or []:
            t = str(txt).strip()
            for hit in items_by_text.get(t, []) or items_by_text.get(re.sub(r'\s+', '', t), []):
                if hit.id not in seen_item_ids:
                    seen_item_ids.add(hit.id)
                    linked_items.append(hit)
        if linked_items:
            finding.entity_id = linked_items[0].id
            finding.bbox = linked_items[0].bbox
        elif getattr(rf, 'evidence', None) and _layout_out and _layout_out[0]:
            # §3 goal "no drawing coordinates = 0": no linked item — fall
            # back to the bbox of the evidence text on the text layer.
            ev = str(rf.evidence).strip()
            ev_parts = [ev] + [p.strip() for p in ev.split('|')] if ' | ' in ev else [ev]
            for tl in _layout_out[0].get('_text_lines', []):
                if any(ep and ep in tl.get('text', '') for ep in ev_parts):
                    finding.bbox = [tl['x'], tl['y'], tl['x1'], tl['y1']]
                    break
        bulk.append(finding)
        created_links.append((sl, linked_items))

    if bulk:
        PIDVFinding.objects.bulk_create(bulk, batch_size=500)
        logger.info('[PIDVTask] Created %d findings for drawing_id=%s', len(bulk), seg.drawing_id)

    # Backfill finding_ids + flag on the linked extraction items.
    try:
        sl_to_finding = {f.sl_no: f for f in bulk}
        for sl, linked in created_links:
            if not linked:
                continue
            f = sl_to_finding.get(sl)
            if not f or not f.id:
                continue
            flag_val = f.severity if f.severity in ('query', 'minor', 'major', 'critical') else 'flagged'
            for it in linked:
                ids = list(it.finding_ids or [])
                if f.id not in ids:
                    ids.append(f.id)
                    it.finding_ids = ids
                    it.flag = flag_val
                    it.save(update_fields=['finding_ids', 'flag', 'updated_at'])
    except Exception:
        logger.warning('[PIDVTask] Finding↔item linking failed (non-fatal)', exc_info=True)

    # ── report_meta for the AI-PID-02 comment register (spec §2.5) ──────
    # sheet_label + grid + tag + reference + checklist items, computed once
    # per page (the grid zones are read from the PDF text layer; findings
    # without coordinates keep '-'). Non-fatal.
    try:
        if bulk:
            from apps.pid_verification_v2.services.checklist_map import RULE_TO_ITEMS
            from apps.pid_verification_v2.services.grid_zones import read_grid_zones, grid_label
            import fitz as _fitz

            _pdf_path = getattr(result_context, 'file_path', None) or _resolve_file_path(doc)
            _tb = (drawing_obj.metadata or {}).get('title_block') or {}
            _no = _tb.get('drawing_number') or drawing_obj.title or seg.drawing_id
            _sheet_label = f'{_no} (p.{drawing_obj.page_index + 1})'
            with _fitz.open(_pdf_path) as _pdf:
                _zones = read_grid_zones(_pdf[drawing_obj.page_index]) if drawing_obj.page_index < _pdf.page_count else None
            for f in bulk:
                tag_m = re.search(r'\b[A-Z]{1,4}-\d{3,5}[A-Z]?\b', f.evidence or '')
                centre = None
                if isinstance(f.bbox, list) and len(f.bbox) == 4:
                    centre = ((f.bbox[0] + f.bbox[2]) / 2, (f.bbox[1] + f.bbox[3]) / 2)
                f.report_meta = {
                    'sheet_label': _sheet_label,
                    'grid': grid_label(_zones, *centre) if centre else '-',
                    'tag': tag_m.group(0) if tag_m else (f.evidence or '').strip()[:40] or '-',
                    'finding_text': f.issue_observed,
                    'reference': f'AI-PID-02 item {" / ".join(RULE_TO_ITEMS.get(f.rule_id, [])) or "—"} · rule {f.rule_id}',
                    'suggested_correction': f.action_required,
                    'confidence': 'High' if f.severity in ('critical', 'major') else ('Medium' if f.severity == 'query' else 'Low'),
                    'ai_result': 'Non-Conformance' if f.severity in ('critical', 'major') else 'Query',
                    'checklist_items': RULE_TO_ITEMS.get(f.rule_id, []),
                }
            PIDVFinding.objects.bulk_update(bulk, ['report_meta'], batch_size=500)
    except Exception:
        logger.warning('[PIDVTask] report_meta fill failed (non-fatal)', exc_info=True)

    return len(bulk)


def _finalize_cross_reference(doc, document_id: str) -> None:
    """Cross-reference: continuation markers — runs ONCE per document,
    called from BOTH completion paths (the inline pipeline above and
    finalize_pid_document below), now that every page's PIDVTagIndex rows
    and continuation_refs (drawing.metadata) exist — see
    _persist_segment_result's own comment for why those are written
    per-page instead of returned.

    Multi-page tag occurrences (formerly XREF-001 findings) are not
    findings: they are computed on demand from PIDVTagIndex by the list
    serializer for the "Tags across sheets" table in Drawing sets.

    Continuation markers persist as regular PIDVFinding rows
    (category='cross_reference') so the existing Cross-Reference UI panel
    (PIDVerification.jsx / PIDVerificationV2.jsx — both already built,
    waiting for exactly this data, no new UI needed) picks them up with no
    separate fetch. Mirrors apps.pid_verification.tasks._finalize_document
    exactly — non-fatal, a failure here never blocks the document from
    completing.
    """
    try:
        from apps.pid_verification_v2.models import PIDVFinding
        from apps.pid_verification_v2.services.cross_reference import group_multi_page_tags, resolve_continuations

        tag_rows = list(doc.tag_index.values('tag', 'raw_tag', 'page_number', 'tag_type'))
        multi_page_tags = group_multi_page_tags(tag_rows)

        drawings = list(doc.drawings.all())
        known_pages = {d.page_index for d in drawings}
        page_refs = {
            d.page_index: (d.metadata or {}).get('continuation_refs', [])
            for d in drawings
            if (d.metadata or {}).get('continuation_refs')
        }
        continuations = resolve_continuations(page_refs, known_pages)

        xref_bulk = []
        if drawings and continuations:
            anchor_drawing = drawings[0]
            # BUG FIX (29 Oct 2026): numbering must continue after the
            # page's existing findings — restarting at 1 produced duplicate
            # F1/F1, F2/F2 numbers in the report.
            sl_offset = (
                PIDVFinding.objects.filter(drawing=anchor_drawing)
                .order_by('-sl_no').values_list('sl_no', flat=True).first() or 0
            )
            # XREF-001 (multi-page tag occurrences) is NOT a finding: it is
            # cross-sheet information shown in the "Tags across sheets"
            # table in Drawing sets (30 Oct 2026 — 105 info-noise rows on
            # the Borouge Package 2 acceptance document). The same data is
            # computed on demand from PIDVTagIndex by the list serializer.
            for c in continuations:
                # XREF-004 ('unresolvable') is noise: continuation markers
                # are extraction items now (PIDVExtractionItem
                # kind='connectors'), not findings (29 Oct 2026, ×42 on the
                # acceptance sheet).
                if c['status'] == 'unresolvable':
                    continue
                rule_id = {'confirmed': 'XREF-002', 'missing': 'XREF-003'}[c['status']]
                to_display = str(c['to_page'] + 1) if c['to_page'] is not None else (c['matched_text'] or '?')
                xref_bulk.append(PIDVFinding(
                    drawing=anchor_drawing,
                    sl_no=sl_offset + len(xref_bulk) + 1,
                    category='cross_reference',
                    rule_id=rule_id,
                    issue_observed=(
                        f"Continuation marker on page {c['from_page'] + 1} "
                        f"({c['pattern']}: {c['matched_text']}) references sheet {to_display}"
                    ),
                    action_required=(
                        'None — target sheet exists.' if c['status'] == 'confirmed'
                        else 'Referenced sheet was not found in this document — verify the sheet number or that all pages were uploaded.' if c['status'] == 'missing'
                        else 'References a separate drawing — verify manually.'
                    ),
                    evidence=c['matched_text'],
                    direction='N/A',
                    severity='info',
                    status='open',
                ))
        if xref_bulk:
            PIDVFinding.objects.bulk_create(xref_bulk, batch_size=500)
        logger.info(
            '[PIDVTask] Cross-reference: %d multi-page tag(s), %d continuation marker(s) for document_id=%s',
            len(multi_page_tags), len(continuations), document_id,
        )
    except Exception:
        logger.warning(
            '[PIDVTask] Cross-reference finalize failed for document_id=%s (non-fatal)',
            document_id, exc_info=True,
        )


@shared_task(
    bind=True,
    name='pid_verification_v2.process_page',
    max_retries=1,
    default_retry_delay=15,
    # Soft-coded, tuned for ONE page (the budget the whole pipeline used to
    # assume before multi-page documents existed) rather than the whole
    # document — see MULTI_PAGE_PARALLEL_THRESHOLD.
    # 8 min was too short for real-world dense/scanned P&ID pages: the
    # extraction stage's multi-DPI (150/300/450) + multi-angle Tesseract OCR
    # passes reproducibly took the full 480s and got killed even after fixing
    # worker CPU thread-oversubscription (2026-07-26 live test — every page
    # hit exactly the soft limit, none completed). Matches the 900s/960s
    # pattern already used for legend/reference-data OCR tasks in this file.
    soft_time_limit=900,  # 15 min soft limit per page
    time_limit=960,       # 16 min hard limit per page
)
def process_pid_page(self, document_id: str, segment_dict: dict, context: dict = None, symbol_images: list = None):
    """
    Process exactly ONE page/segment of a multi-page P&ID document:
    extraction → graph → rule engine → comparison engine → AI analysis →
    legend/symbol bridge → persist PIDVDrawing + findings for that page only.

    Dispatched as one member of a Celery chord (one subtask per page) from
    `process_pid_document` when a document has more pages than
    MULTI_PAGE_PARALLEL_THRESHOLD, so large multi-page P&ID sets are
    processed with true parallelism across worker processes instead of one
    task looping through every page sequentially (which cannot finish 20+
    pages of multi-pass OCR within any sane Celery time limit).

    `symbol_images` is loaded ONCE by `process_pid_document` (see
    services/legend_bridge.get_symbol_images_for_project) and passed to
    every page-task unchanged — avoids each of N page-tasks independently
    re-reading/re-encoding the same reference-picture library from storage.

    Never raises — any failure is captured and returned as
    {'success': False, 'error': ...} so a single bad/slow page can't abort
    the whole chord/document; `finalize_pid_document` decides overall
    document status from the aggregate of these results.
    """
    context = context or {}
    symbol_images = symbol_images or []
    from apps.pid_verification_v2.models import PIDVDocument
    from apps.pid_verification_v2.services.segmentation import SegmentedDrawing
    from apps.pid_verification_v2.services.orchestrator import (
        PipelineContext, ExtractionStage, GraphBuildingStage,
        RuleEngineStage, ComparisonEngineStage, AIAnalysisStage, LegendSymbolBridgeStage,
    )

    seg = SegmentedDrawing(**segment_dict)

    try:
        doc = PIDVDocument.objects.get(document_id=document_id)
    except PIDVDocument.DoesNotExist:
        logger.error('[PIDVPage] Document %s not found for page %s', document_id, seg.drawing_id)
        return {'drawing_id': seg.drawing_id, 'success': False, 'error': 'Document not found'}

    try:
        file_path = _resolve_file_path(doc)

        pipeline_context = PipelineContext(
            document_id=str(document_id),
            document=doc,
            project=doc.project if doc.project_id else None,
            user_context=context,
            file_path=file_path,
            segments=[seg],
            symbol_images=symbol_images,
        )

        for stage_cls in (ExtractionStage, GraphBuildingStage, RuleEngineStage, ComparisonEngineStage,
                          AIAnalysisStage, LegendSymbolBridgeStage):
            stage = stage_cls()
            result = stage.execute(pipeline_context)
            pipeline_context.add_result(result)
            if not result.success and stage.config.critical:
                raise RuntimeError(f'{stage.stage_id} failed: {result.error}')

        findings_count = _persist_segment_result(doc, seg, pipeline_context)
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
    name='pid_verification_v2.finalize_document',
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
    single page failed).
    """
    from apps.pid_verification_v2.models import PIDVDocument
    from apps.pid_verification_v2.services.export_service import (
        generate_excel, generate_pdf, upload_to_s3
    )

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

    try:
        doc.refresh_from_db()

        _finalize_cross_reference(doc, document_id)

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
    except Exception as exc:
        logger.error(
            '[PIDVFinalize] Report generation failed for document_id=%s: %s',
            document_id, exc, exc_info=True,
        )

    doc.status = PIDVDocument.Status.COMPLETED
    update_fields = ['status', 'excel_s3_url', 'pdf_s3_url', 'updated_at']
    if failed:
        doc.error_message = (
            f'Completed \u2014 {len(failed)} of {total} page(s) failed to process (see logs).'
        )
    else:
        doc.error_message = ''  # clear stale gate/legend messages from earlier attempts
    update_fields.append('error_message')
    doc.save(update_fields=update_fields)
    logger.info('[PIDVFinalize] Completed document_id=%s (%d/%d pages ok)', document_id, succeeded, total)

    # Result caching (S3/local) — never fatal: a caching failure shouldn't
    # turn a successful analysis into a failed one. Same pattern as
    # apps.pid_verification.tasks._finalize_document.
    try:
        from apps.pid_verification_v2.services.results_cache import save_results_cache
        save_results_cache(doc, doc.file_hash)
    except Exception:
        logger.exception('[PIDVFinalize] Result cache write failed for document_id=%s (non-fatal)', document_id)

    # Cross-drawing checks over the project's current sheets (spec A2) —
    # runs on every upload/re-run completion; also on delete (views.py).
    if doc.project_id:
        try:
            from apps.pid_verification_v2.services.cross_checks import run_cross_checks
            run_cross_checks(doc.project)
        except Exception:
            logger.exception('[PIDVFinalize] Cross checks failed for document_id=%s (non-fatal)', document_id)


def _resolve_file_path(doc) -> str:
    """
    Return a local filesystem path for the document file.

    Resolution order (soft-coded to handle all storage backends):
      1. Local FileField  →  .path  (e.g. FileSystemStorage / ResilientMediaStorage)
      2. S3 FileField     →  .name  holds the S3 key → download to tmp file
      3. Explicit s3_path →  download to tmp file
    Raises ValueError when no source is available.
    """
    if doc.original_file:
        # Storage-agnostic: local fast path, else stream to a temp file
        # (FileField.path raises NotImplementedError under S3 — 30 Oct 2026).
        from apps.core.file_access import local_file_path
        return local_file_path(doc.original_file)

    # Explicit s3_path field (legacy / manually set)
    if doc.s3_path:
        logger.info('[PIDVTask] Downloading file from explicit s3_path: %s', doc.s3_path)
        return _download_from_s3(doc.s3_path)

    raise ValueError(f'No file path available for document {doc.document_id}')


def _download_from_s3(s3_key: str) -> str:
    """Download an S3 object to a temp file and return its path.

    Only used for legacy/explicit `s3_path` fields that store a raw,
    already-fully-qualified S3 key (no storage `location` prefix to resolve).
    For FileField values, prefer `_download_field_file()` instead, which
    resolves the key via the field's own storage backend.
    """
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


def _download_field_file(field_file) -> str:
    """Download a Django FieldFile to a local temp file via its own storage
    backend (e.g. S3Boto3Storage), so any storage `location` prefix is
    resolved correctly regardless of backend (S3 or local filesystem).
    """
    name = field_file.name
    ext  = name.rsplit('.', 1)[-1] if '.' in name else 'bin'
    tmp  = tempfile.NamedTemporaryFile(delete=False, suffix=f'.{ext}')
    with field_file.storage.open(name, 'rb') as src:
        shutil.copyfileobj(src, tmp)
    tmp.flush()
    tmp.close()
    return tmp.name


# ===========================================================================
# Legend Sheet Extraction Task
# ===========================================================================

@shared_task(
    bind=True,
    name='pid_verification_v2.extract_legend_sheet',
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
    from apps.pid_verification_v2.models import PIDVLegendSheet
    from apps.pid_verification_v2.services.legend_extractor import (
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
            # Storage-agnostic (FileField.path raises under S3).
            from apps.core.file_access import local_file_path
            tmp_path = local_file_path(sheet.original_file)
            need_cleanup = tmp_path.startswith(tempfile.gettempdir())
        elif sheet.s3_path:
            tmp_path  = _download_from_s3(sheet.s3_path)
            need_cleanup = True
        else:
            raise ValueError(f'No file available for legend_id={legend_id}')

        # ── S3 cache lookup ────────────────────────────────────────────────
        # Compute the file hash first so we can skip AI extraction if this
        # exact file was already processed and cached in S3.
        from apps.pid_verification_v2.services.legend_cache import (
            compute_file_hash as _cache_hash,
            lookup_s3_cache as _cache_get,
            write_s3_cache as _cache_put,
        )
        from apps.pid_verification_v2.services.legend_extractor import _render_pages_to_b64 as _render_pages
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
            from apps.pid_verification_v2.services.export_service import upload_to_s3 as _s3_upload
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
                from apps.pid_verification_v2.services.export_service import upload_to_s3 as _s3_upload
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
                from apps.pid_verification_v2.services.instrument_extractor import extract_instrument_symbols
                from apps.pid_verification_v2.services.instrument_registry import save_instrument_symbols as _save_instr
                instr_data   = extract_instrument_symbols(tmp_path, use_ai=True, pages_b64=_shared_pages_b64)
                instr_count  = _save_instr(sheet, instr_data)
                logger.info('[LegendTask] Saved %d instrument symbols for project=%s', instr_count, sheet.project_id)

                # Persist instrument symbols JSON to S3 for future reference
                try:
                    import json as _json
                    from apps.pid_verification_v2.services.export_service import upload_to_s3 as _s3_upload
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

# Soft-coded: keywords used to detect the true header row among the first
# several rows of a PDF-extracted table. Real-world engineering documents
# often have title-block / cover rows (company name, doc no., revision)
# before the actual column-header row, so header row 0 cannot be assumed.
PDF_HEADER_KEYWORDS = {
    'tag', 'no', 'number', 'size', 'service', 'line', 'spec', 'specification',
    'code', 'from', 'to', 'description', 'equipment', 'instrument', 'type',
    'range', 'design', 'operating', 'pressure', 'temperature', 'material',
    'insulation', 'test', 'fabrication', 'installation', 'rev', 'revision',
    'qty', 'phase', 'remarks', 'name', 'duty', 'fluid', 'class', 'rating',
    'dimension', 'moc', 'sno', 'tagno',
}

# How many leading rows of the first table to scan when searching for the
# real header row (title-block/cover rows come before it).
PDF_HEADER_SEARCH_WINDOW = 8


def _unrotate_180(text: str) -> str:
    """
    Undo a 180°-rotated vertical header label.

    pdfplumber sometimes extracts vertically-rotated table header text with
    both reversed character order AND reversed line order, e.g.
    'REBMUN\\nLAIRES' -> 'SERIAL\\nNUMBER'.
    """
    lines = text.split('\n')
    return '\n'.join(line[::-1] for line in lines[::-1])


def _pdf_header_keyword_score(cells) -> int:
    """Count cells whose cleaned text matches a known header keyword."""
    score = 0
    for cell in cells:
        cleaned = re.sub(r'[^a-z]', '', str(cell or '').lower())
        if cleaned and (
            cleaned in PDF_HEADER_KEYWORDS
            or any(kw in cleaned for kw in PDF_HEADER_KEYWORDS if len(kw) > 3)
        ):
            score += 1
    return score


# ── Instrument Index PDF: grouped multi-row record reconstruction ─────────
# Instrument/F&G index PDFs (e.g. ADOC/DORSCH-style forms) render each
# instrument as THREE physical table rows per page (a preceding "type" row,
# the main data row carrying the serial number, and a continuation row for
# wrapped multi-line cells). pdfplumber also bleeds the first record's values
# into the header row/cells, so the header must be identified by matching
# the FIRST LINE of each header cell rather than the whole (corrupted) cell.

def _first_line(cell) -> str:
    if not cell:
        return ''
    return str(cell).split('\n')[0].strip()


def _is_instrument_record_start(row) -> bool:
    """A data row that begins a new instrument record — its first column is
    a serial number (e.g. '441', '446\\nET ROOM')."""
    if not row:
        return False
    return bool(re.match(r'^\d+[A-Za-z]*$', _first_line(row[0])))


def _merge_instrument_cell(*cells) -> str:
    """Combine values from a record's grouped rows (type/main/continuation)
    for one column, preserving all distinct non-empty text so wrapped
    multi-line data isn't silently dropped."""
    seen = []
    for cell in cells:
        text = str(cell).strip() if cell else ''
        if text and text not in seen:
            seen.append(text)
    return ' | '.join(seen)


def _parse_instrument_index_pdf(tmp_path):
    """
    Parse an instrument/F&G index PDF whose records span 3 physical rows.
    Returns a list of flat row-dicts (one per instrument record).
    """
    import pdfplumber

    all_rows = []

    with pdfplumber.open(tmp_path) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            if not tables:
                continue
            table = tables[0]
            if not table or len(table) < 3:
                continue

            # Locate the header row by matching keywords against the FIRST
            # LINE of each cell (ignores bled-in continuation-row noise).
            header_idx, header_score = 0, -1
            search_rows = table[:min(PDF_HEADER_SEARCH_WINDOW, len(table))]
            for idx, row in enumerate(search_rows):
                score = _pdf_header_keyword_score([_first_line(c) for c in row])
                if score > header_score:
                    header_idx, header_score = idx, score

            if header_score < 2:
                continue  # No recognizable header on this page — skip it

            header_row = table[header_idx]
            sub_header_row = table[header_idx + 1] if header_idx + 1 < len(table) else []

            page_columns = []
            for i in range(len(header_row)):
                label = _first_line(header_row[i])
                if not label and i < len(sub_header_row):
                    label = _first_line(sub_header_row[i])
                page_columns.append(label if label else f'Column_{i}')

            data_rows = table[header_idx + 2:]
            n = len(data_rows)
            record_starts = [i for i, row in enumerate(data_rows) if _is_instrument_record_start(row)]

            for pos, start in enumerate(record_starts):
                main_row = data_rows[start]
                type_row = data_rows[start - 1] if start - 1 >= 0 and (start - 1) not in record_starts else None
                next_start = record_starts[pos + 1] if pos + 1 < len(record_starts) else n
                cont_row = data_rows[start + 1] if start + 1 < next_start else None

                merged = {}
                for i, col_name in enumerate(page_columns):
                    values = []
                    if type_row is not None and i < len(type_row):
                        values.append(type_row[i])
                    if i < len(main_row):
                        values.append(main_row[i])
                    if cont_row is not None and i < len(cont_row):
                        values.append(cont_row[i])
                    merged[col_name] = _merge_instrument_cell(*values)

                if any(merged.values()):
                    all_rows.append(merged)

    return all_rows


@shared_task(
    bind=True,
    name='pid_verification_v2.parse_reference_data',
    max_retries=2,
    default_retry_delay=15,
    soft_time_limit=900,   # 15-min soft limit — accommodates large multi-page
                           # PDF reference files (e.g. Instrument Index PDFs
                           # with many pages of complex pdfplumber table
                           # extraction); was 180s, too short for real-world files
    time_limit=960,        # 16-min hard limit
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
    from apps.pid_verification_v2.models import PIDVReferenceData
    
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
            # Storage-agnostic (FileField.path raises under S3).
            from apps.core.file_access import local_file_path
            tmp_path = local_file_path(ref_data.original_file)
            need_cleanup = tmp_path.startswith(tempfile.gettempdir())
        elif ref_data.s3_path:
            tmp_path = _download_from_s3(ref_data.s3_path)
            need_cleanup = True
        else:
            raise ValueError(f'No file available for reference_id={reference_id}')
        
        # ── Parse file using pandas or pdfplumber ──────────────────────────
        import pandas as pd
        from pathlib import Path
        
        file_ext = Path(ref_data.file_name).suffix.lower()
        
        if file_ext in ['.xlsx', '.xls'] and ref_data.data_type == PIDVReferenceData.DataType.LEGEND_PID:
            # Legend registers are multi-sheet workbooks (Index / Codes /
            # ID Letters / Notes / Documents / Drawing Register / …).
            # Reading only the first sheet (pandas default) captured just
            # the cover page — observed 29 Oct 2026 on
            # Borouge_PID_Legend_Register.xlsx (34 rows of contents instead
            # of the 619-row Codes table). Read ALL sheets and store them
            # keyed by sheet name.
            all_sheets = pd.read_excel(tmp_path, sheet_name=None, header=None)
            _HDR_HINTS = {
                'code', 'description', 'category', 'drawing', 'title', 'doc',
                'no', 'no.', 'rev', 'status', 'letter', 'symbol', 'id',
                'document', 'date', 'note',
            }

            def _sheet_records(raw_df):
                """Detect the true header row (cover/title rows precede it)
                and return records with real column names."""
                import re as _re
                best_idx = None
                for idx in range(min(12, len(raw_df))):
                    cells = [str(c).strip().lower() for c in raw_df.iloc[idx].tolist()]
                    hits = sum(1 for c in cells if c and (
                        c in _HDR_HINTS
                        or any(h in c for h in ('code', 'desc', 'title', 'drawing', 'doc', 'rev', 'status'))
                    ))
                    if hits >= 2:
                        best_idx = idx
                        break
                if best_idx is None:
                    return []
                header = [
                    str(c).strip() or f'col_{i}'
                    for i, c in enumerate(raw_df.iloc[best_idx].tolist())
                ]
                body = raw_df.iloc[best_idx + 1:]
                body.columns = header
                return body.fillna('').to_dict(orient='records')

            parsed_data = {
                'sheets': {
                    name: _sheet_records(sheet_df)
                    for name, sheet_df in all_sheets.items()
                }
            }
            ref_data.parsed_data = parsed_data
            ref_data.metadata = {
                'row_count': sum(len(v) for v in parsed_data['sheets'].values()),
                'columns': [],
                'sheets': {name: len(v) for name, v in parsed_data['sheets'].items()},
                'file_size_bytes': os.path.getsize(tmp_path),
            }
            ref_data.status = PIDVReferenceData.Status.COMPLETED
            ref_data.error_message = ''
            ref_data.save(update_fields=['parsed_data', 'metadata', 'status', 'error_message', 'updated_at'])
            logger.info(
                '[ReferenceDataTask] Legend register %s parsed: sheets=%s',
                reference_id, ref_data.metadata['sheets'],
            )
            return
        elif file_ext in ['.xlsx', '.xls']:
            df = pd.read_excel(tmp_path)
        elif file_ext == '.csv':
            df = pd.read_csv(tmp_path)
        elif file_ext == '.pdf' and ref_data.data_type == PIDVReferenceData.DataType.INSTRUMENT_INDEX:
            # Instrument index PDFs use a 3-row-per-record layout (see
            # _parse_instrument_index_pdf docstring) that the generic
            # single/two-row header logic below cannot handle correctly.
            instrument_rows = _parse_instrument_index_pdf(tmp_path)
            if not instrument_rows:
                raise ValueError(f'No valid records extracted from PDF: {ref_data.file_name}')
            df = pd.DataFrame(instrument_rows)
        elif file_ext == '.pdf':
            # Extract tables from PDF using pdfplumber
            import pdfplumber
            
            all_rows = []
            columns = None
            header_orientation = 'normal'  # 'normal' | 'rotated'
            
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
                        
                        if columns is None:
                            # Search the first few rows of the FIRST table for
                            # the true header row. Real-world documents often
                            # have title-block/cover rows (company name, doc
                            # no., revision) before the actual column headers,
                            # and headers are sometimes extracted as
                            # 180°-rotated vertical text.
                            best_idx, best_score = 0, -1
                            best_orientation, best_cells = 'normal', None
                            search_rows = table[:min(PDF_HEADER_SEARCH_WINDOW, len(table) - 1)]
                            
                            for idx, row in enumerate(search_rows):
                                cells = [str(c).strip() if c else '' for c in row]
                                normal_score = _pdf_header_keyword_score(cells)
                                if normal_score > best_score:
                                    best_idx, best_score = idx, normal_score
                                    best_orientation, best_cells = 'normal', cells
                                
                                rotated_cells = [_unrotate_180(c) if c else '' for c in cells]
                                rotated_score = _pdf_header_keyword_score(rotated_cells)
                                if rotated_score > best_score:
                                    best_idx, best_score = idx, rotated_score
                                    best_orientation, best_cells = 'rotated', rotated_cells
                            
                            if best_score < 2 or best_cells is None:
                                # No confident header found — fall back to
                                # the original assumption (first row = header).
                                best_idx, best_orientation = 0, 'normal'
                                best_cells = [str(c).strip() if c else '' for c in table[0]]
                            
                            columns = [c if c else f'Column_{i}' for i, c in enumerate(best_cells)]
                            header_orientation = best_orientation
                            # Rows before the detected header are title-block
                            # noise — discard them along with the header itself.
                            data_rows = table[best_idx + 1:]
                        else:
                            # Subsequent pages: check if first row is a
                            # repeated header (accounting for orientation).
                            first_row = [str(cell).strip() if cell else '' for cell in table[0]]
                            if header_orientation == 'rotated':
                                first_row_check = [_unrotate_180(c) if c else '' for c in first_row]
                            else:
                                first_row_check = first_row
                            
                            if first_row_check == columns:
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
    name='pid_verification_v2.run_ai_checks',
    max_retries=2,
    default_retry_delay=60,
    # UPDATED: raised from 30/35 min to 35/40 min — same reasoning as
    # process_pid_document's TASK_CONFIG (extended thinking adds latency).
    soft_time_limit=2100,   # 35 min soft limit (for large P&ID sets)
    time_limit=2400,        # 40 min hard limit
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
    from apps.pid_verification_v2.models import PIDVAICheckRun, PIDVProject, PIDVDocument, PIDVDrawing, PIDVReferenceData
    from apps.pid_verification_v2.ai_extraction import PIDExtractionEngine
    from apps.pid_verification_v2.ai_checks import AutoCheckExecutor
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
        openai_key = provider_api_key('openai', fallback=lambda: (context.get('openai_api_key')))
        claude_key = provider_api_key('anthropic', fallback=lambda: (context.get('claude_api_key')))
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
        
        for drawing in drawings[:10]:  # Limit to 10 sheets for initial implementation
            try:
                # Download image from S3 — via the field's own storage
                # backend (_download_field_file), NOT a raw boto3 client
                # keyed on doc.original_file.name alone. That was the exact
                # 2026-08-27 HeadObject 404 bug (see _download_field_file's
                # docstring): .name is relative to the storage's `location`
                # prefix (e.g. MediaStorage.location = 'media'), so a raw
                # S3 key built from .name alone 404s on the real object.
                doc = drawing.document
                if doc.original_file:
                    tmp_file_path = _download_field_file(doc.original_file)
                elif doc.s3_path:
                    tmp_file_path = _download_from_s3(doc.s3_path)
                else:
                    raise ValueError(f'No file source available for document {doc.document_id}')

                # Extract using vision API
                logger.info('[AICheckTask] Extracting sheet: %s (page %d)', drawing.title, drawing.page_index)

                extraction_result = extractor.extract_all(tmp_file_path, sheet_number=drawing.title)

                all_equipment.extend(extraction_result.get('equipment', []))
                all_lines.extend(extraction_result.get('lines', []))
                all_instruments.extend(extraction_result.get('instruments', []))

                sheets_processed += 1
                api_calls_made += 3  # equipment + lines + instruments

                # Cleanup
                os.unlink(tmp_file_path)
                
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
        
        # TODO: AUTO check reconciliation (extracted vs reference_data) and
        # ASSIST check generation (steps 3-4 of the workflow described in the
        # PIDVAICheckRun docstring) are not implemented yet. Until they are,
        # mark the run FAILED with a clear message rather than silently
        # reporting a fake "completed" result with no check_results.
        check_run.status = PIDVAICheckRun.Status.FAILED
        check_run.error_message = (
            'AI check execution (AUTO/ASSIST reconciliation) is not yet implemented. '
            'P&ID element extraction completed successfully and is stored in extracted_data.'
        )
        check_run.processing_metadata = {
            'sheets_processed': sheets_processed,
            'api_calls_made':   api_calls_made,
            'processing_time_seconds': round(time.time() - start_time, 1),
        }
        check_run.save(update_fields=['status', 'error_message', 'processing_metadata', 'updated_at'])
        logger.warning('[AICheckTask] run_id=%s: extraction complete but check reconciliation is not yet implemented', run_id)
    
    except Exception as exc:
        logger.exception('[AICheckTask] AI check run failed for run_id=%s: %s', run_id, exc)
        try:
            check_run = PIDVAICheckRun.objects.get(run_id=run_id)
            check_run.status = PIDVAICheckRun.Status.FAILED
            check_run.error_message = str(exc)
            check_run.save(update_fields=['status', 'error_message', 'updated_at'])
        except PIDVAICheckRun.DoesNotExist:
            pass
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# MULTI-LAYER EXTRACTION TASK (New V2 Feature)
# ---------------------------------------------------------------------------

@shared_task(
    bind=True,
    name='pid_verification_v2.extract_project_files',
    max_retries=1,
    default_retry_delay=60,
    soft_time_limit=3600,   # 60 min soft limit (multi-file extraction can be slow)
    time_limit=3900,        # 65 min hard limit
)
def extract_project_files_async(
    self,
    project_id: str,
    extraction_mode: str = 'balanced',
    user_api_key: str = None,
    vision_provider: str = 'openai',
    user_id: int = None,
):
    """
    Asynchronous multi-layer extraction task for all files in a project.
    
    Extracts data from:
      - P&ID drawings
      - Legend sheets
      - Equipment lists
      - Line lists
      - PMS (Piping Material Specifications)
    
    Using:
      - Layer 1: Free OCR (Tesseract, PyMuPDF, pdfplumber)
      - Layer 2: ML OCR fallback (EasyOCR, PaddleOCR)
      - Layer 3: Vision AI with BYOK (OpenAI/Claude/Gemini)
    
    Args:
        project_id: UUID of PIDVProject
        extraction_mode: 'fast' | 'balanced' | 'deep' | 'vision_only'
        user_api_key: User's BYOK API key (optional)
        vision_provider: 'openai' | 'claude' | 'gemini'
        user_id: ID of user who initiated extraction
    
    Returns:
        {
            'success': bool,
            'project_id': str,
            'total_files': int,
            'total_pages': int,
            'total_cost_usd': float,
            'summary': {...},
            'error': str (if failed)
        }
    """
    logger.info(
        f"[Task] Starting multi-layer extraction for project {project_id} "
        f"(mode: {extraction_mode}, provider: {vision_provider})"
    )
    
    try:
        from apps.pid_verification_v2.services.extraction_orchestrator import MultiLayerExtractionOrchestrator
        from apps.pid_verification_v2.models import PIDVProject
        
        # Validate project exists
        try:
            project = PIDVProject.objects.get(project_id=project_id)
        except PIDVProject.DoesNotExist:
            error_msg = f"Project {project_id} not found"
            logger.error(f"[Task] {error_msg}")
            return {
                'success': False,
                'error': error_msg,
            }
        
        # Initialize orchestrator
        orchestrator = MultiLayerExtractionOrchestrator(
            project_id=project_id,
            extraction_mode=extraction_mode,
            user_api_key=user_api_key,
            vision_provider=vision_provider,
            user_id=user_id,
        )
        
        # Run extraction
        result = orchestrator.extract_all_project_files()
        
        # Check for errors
        if 'error' in result:
            logger.error(f"[Task] Extraction failed: {result['error']}")
            return {
                'success': False,
                'error': result['error'],
            }
        
        logger.info(
            f"[Task] Extraction complete: {result['total_files']} files, "
            f"{result['total_pages']} pages, ${result['total_cost_usd']:.4f}"
        )
        
        return {
            'success': True,
            'project_id': project_id,
            'total_files': result['total_files'],
            'total_pages': result['total_pages'],
            'total_cost_usd': result['total_cost_usd'],
            'processing_time': result['total_processing_time'],
            'summary': result['summary'],
        }
    
    except Exception as e:
        error_msg = f"Extraction task failed: {str(e)}"
        logger.exception(f"[Task] {error_msg}")
        
        # Retry if this is a transient error
        if self.request.retries < self.max_retries:
            logger.info(f"[Task] Retrying in {self.default_retry_delay}s...")
            raise self.retry(exc=e)
        
        return {
            'success': False,
            'error': error_msg,
        }
        
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
