"""
Celery Background Tasks — P&ID Verification
============================================
Task pipeline (all chained in a single async job):
  1. Segment document into drawings
  2. For each drawing: extract → build graph → run rule engine → save findings
  3. Generate Excel & PDF reports → upload to S3
  4. Update document status = completed (or failed)
"""
import logging
import os
import tempfile

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Soft-coded constants
# ---------------------------------------------------------------------------

# Set to True to block P&ID quality checks until the project has at least one
# completed legend sheet (or legend_knowledge_data is populated from a prior
# extraction).  Set to False to allow quality checks without a legend.
LEGEND_REQUIRED_FOR_QC = True


@shared_task(
    bind=True,
    name='pid_verification.process_document',
    max_retries=2,
    default_retry_delay=30,
    soft_time_limit=540,   # 9 min soft limit
    time_limit=600,        # 10 min hard limit
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
    from apps.pid_verification.models import PIDVDocument, PIDVDrawing, PIDVFinding
    from apps.pid_verification.services.segmentation  import segment_document
    from apps.pid_verification.services.extraction    import extract_drawing
    from apps.pid_verification.services.graph_builder import build_graph
    from apps.pid_verification.services.rule_engine   import run_rules
    from apps.pid_verification.services.export_service import (
        generate_excel, generate_pdf, upload_to_s3
    )

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

    # ── V2 ORCHESTRATOR: Configuration-driven processing pipeline ────────
    USE_V2_ORCHESTRATOR = True  # Feature flag - set to False to use legacy pipeline
    
    if USE_V2_ORCHESTRATOR:
        try:
            from apps.pid_verification.services.orchestrator import (
                PipelineOrchestrator,
                PipelineContext,
                update_processing_progress,
            )
            
            logger.info('[PIDVTask] Using V2 Orchestrator for document_id=%s', document_id)
            
            # Initialize pipeline context
            pipeline_context = PipelineContext(
                document_id=str(doc.document_id),
                document=doc,
                project=doc.project if doc.project_id else None,
                user_context=context,
            )
            
            # Execute orchestrated pipeline
            orchestrator = PipelineOrchestrator()
            result_context = orchestrator.execute(pipeline_context)
            
            # Check for critical failure
            if result_context.has_critical_failure():
                doc.status = PIDVDocument.Status.FAILED
                doc.error_message = 'Critical processing stage failed. Check logs for details.'
                doc.save(update_fields=['status', 'error_message', 'updated_at'])
                logger.error('[PIDVTask] Pipeline failed for document_id=%s', document_id)
                return
            
            # Persist findings from all sources
            from apps.pid_verification.models import PIDVDrawing, PIDVFinding
            
            # Get or create drawing (assuming single drawing for now)
            if result_context.segments:
                seg = result_context.segments[0]
                drawing_obj, _ = PIDVDrawing.objects.get_or_create(
                    document=doc,
                    drawing_id=seg.drawing_id,
                    defaults={
                        'title': seg.title,
                        'page_index': seg.page_index,
                        'metadata': seg.metadata,
                    },
                )
                
                # Clear existing findings
                drawing_obj.findings.all().delete()
                
                # Store extraction and comparison results in metadata
                metadata = drawing_obj.metadata or {}
                metadata['extraction_summary'] = result_context.get_stage_result('extraction').data if result_context.get_stage_result('extraction') else {}
                metadata['comparison_summary'] = result_context.get_stage_result('comparison_engine').data if result_context.get_stage_result('comparison_engine') else {}
                metadata['processing_duration'] = result_context.get_total_duration()
                drawing_obj.metadata = metadata
                drawing_obj.save(update_fields=['metadata'])
                
                # Merge all findings
                all_findings = (
                    result_context.rule_findings + 
                    result_context.comparison_findings + 
                    result_context.ai_findings
                )
                
                # Bulk create findings
                bulk = []
                for sl, rf in enumerate(all_findings, start=1):
                    bulk.append(PIDVFinding(
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
                    ))
                
                if bulk:
                    PIDVFinding.objects.bulk_create(bulk, batch_size=500)
                    logger.info('[PIDVTask] Created %d findings for drawing_id=%s', len(bulk), seg.drawing_id)
            
            # Mark document as completed
            doc.status = PIDVDocument.Status.COMPLETED
            doc.save(update_fields=['status', 'updated_at'])
            logger.info('[PIDVTask] V2 Pipeline completed successfully for document_id=%s', document_id)
            return
            
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
            # Clear any previous findings (re-process idempotency)
            drawing_obj.findings.all().delete()

            # ── 4. Extract elements ───────────────────────────────────────
            extraction = extract_drawing(file_path, page_index=seg.page_index, legend_data=project_legend)

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
                from apps.pid_verification.services.comparison_engine import run_all_comparisons
                
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
                    instrument_index_data=instrument_index_data
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
                from apps.pid_verification.services.rule_engine import RuleFinding
                
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
                            direction=None,
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
                        from apps.pid_verification.services.ai_analysis import (
                            run_openai_analysis,
                            run_claude_analysis,
                            run_hybrid_analysis,
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
                        
                        # Route to appropriate AI service
                        if analysis_mode == 'enhanced_openai':
                            openai_key = context.get('openai_api_key')
                            if openai_key:
                                ai_findings = run_openai_analysis(drawing_data, openai_key)
                        
                        elif analysis_mode == 'deep_claude':
                            claude_key = context.get('claude_api_key')
                            if claude_key:
                                ai_findings = run_claude_analysis(drawing_data, claude_key)
                        
                        elif analysis_mode == 'hybrid':
                            openai_key = context.get('openai_api_key')
                            claude_key = context.get('claude_api_key')
                            if openai_key and claude_key:
                                ai_findings = run_hybrid_analysis(drawing_data, openai_key, claude_key)
                        
                        logger.info(
                            '[PIDVTask] AI analysis completed: %d findings from %s',
                            len(ai_findings), analysis_mode
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
        doc.save(update_fields=['status', 'excel_s3_url', 'pdf_s3_url', 'updated_at'])
        logger.info('[PIDVTask] Completed document_id=%s  total_findings=%d', document_id, all_findings_count)

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
      2. S3 FileField     →  .name  holds the S3 key → download to tmp file
      3. Explicit s3_path →  download to tmp file
    Raises ValueError when no source is available.
    """
    if doc.original_file:
        # Try local path first (works for FileSystemStorage and ResilientMediaStorage)
        try:
            path = doc.original_file.path
            if path:
                return path
        except NotImplementedError:
            pass  # S3Boto3Storage raises NotImplementedError for .path

        # For S3-backed FileField, .name is the S3 object key
        s3_key = getattr(doc.original_file, 'name', None)
        if s3_key:
            logger.info('[PIDVTask] Downloading file from S3 key: %s', s3_key)
            return _download_from_s3(s3_key)

    # Explicit s3_path field (legacy / manually set)
    if doc.s3_path:
        logger.info('[PIDVTask] Downloading file from explicit s3_path: %s', doc.s3_path)
        return _download_from_s3(doc.s3_path)

    raise ValueError(f'No file path available for document {doc.document_id}')


def _download_from_s3(s3_key: str) -> str:
    """Download an S3 object to a temp file and return its path."""
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
