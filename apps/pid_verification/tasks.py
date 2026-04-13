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
def process_pid_document(self, document_id: str):
    """
    Main background task.
    Receives the string form of PIDVDocument.document_id (UUID).
    """
    from apps.pid_verification.models import PIDVDocument, PIDVDrawing, PIDVFinding
    from apps.pid_verification.services.segmentation  import segment_document
    from apps.pid_verification.services.extraction    import extract_drawing
    from apps.pid_verification.services.graph_builder import build_graph
    from apps.pid_verification.services.rule_engine   import run_rules
    from apps.pid_verification.services.export_service import (
        generate_excel, generate_pdf, upload_to_s3
    )

    logger.info('[PIDVTask] Starting processing for document_id=%s', document_id)

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

            # ── 7. Persist findings ───────────────────────────────────────
            bulk = []
            for sl, rf in enumerate(rule_findings, start=1):
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

        # ── 8. Generate & upload reports ──────────────────────────────────
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
