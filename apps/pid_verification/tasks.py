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

    try:
        # ── 2. Resolve file path ──────────────────────────────────────────
        file_path = _resolve_file_path(doc)

        # ── 3. Segment into drawings ──────────────────────────────────────
        segments = segment_document(str(doc.document_id), file_path)
        logger.info('[PIDVTask] %d drawing(s) segmented', len(segments))

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
            extraction = extract_drawing(file_path, page_index=seg.page_index)

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
    For S3-backed files: download to a temp file and return its path.
    For local files: return the absolute path directly.
    """
    if doc.original_file and hasattr(doc.original_file, 'path'):
        try:
            return doc.original_file.path
        except NotImplementedError:
            pass  # S3-backed storage – fall through to download

    if doc.s3_path:
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
