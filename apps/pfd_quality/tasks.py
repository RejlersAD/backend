"""
Celery Background Tasks — PFD Quality Checker
==============================================
Pipeline:
  1. Segment PFD document into drawings (one per PDF page)
  2. For each drawing: extract elements → run rule engine → save findings
  3. Generate Excel & PDF reports
  4. Update document status = completed (or failed)
"""
import logging
import os
import tempfile

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name='pfd_quality.process_document',
    max_retries=2,
    default_retry_delay=30,
    soft_time_limit=540,
    time_limit=600,
)
def process_pfd_document(self, document_id: str):
    """Main background task for PFD quality checking."""
    from apps.pfd_quality.models import PFDQDocument, PFDQDrawing, PFDQFinding
    from apps.pfd_quality.services.segmentation  import segment_document
    from apps.pfd_quality.services.extraction    import extract_drawing
    from apps.pfd_quality.services.rule_engine   import run_rules
    from apps.pfd_quality.services.export_service import generate_excel, generate_pdf, upload_to_s3

    logger.info('[PFDQTask] Starting processing for document_id=%s', document_id)

    try:
        doc = PFDQDocument.objects.get(document_id=document_id)
    except PFDQDocument.DoesNotExist:
        logger.error('[PFDQTask] Document %s not found', document_id)
        return

    doc.status = PFDQDocument.Status.PROCESSING
    doc.save(update_fields=['status', 'updated_at'])

    try:
        file_path = _resolve_file_path(doc)

        segments = segment_document(str(doc.document_id), file_path)
        logger.info('[PFDQTask] %d drawing(s) segmented', len(segments))

        all_findings_count = 0

        for seg in segments:
            drawing_obj, _ = PFDQDrawing.objects.get_or_create(
                document=doc,
                drawing_id=seg.drawing_id,
                defaults={
                    'title':      seg.title,
                    'page_index': seg.page_index,
                    'metadata':   seg.metadata,
                }
            )
            drawing_obj.findings.all().delete()

            extraction  = extract_drawing(file_path, page_index=seg.page_index)
            rule_findings = run_rules(extraction)

            # Persist tag_positions into drawing metadata for frontend overlay markers.
            # Merge rather than replace so any segmentation metadata is preserved.
            tag_positions = extraction.get('tag_positions', {})
            if tag_positions:
                meta = dict(drawing_obj.metadata or {})
                meta['tag_positions'] = tag_positions
                drawing_obj.metadata = meta
                drawing_obj.save(update_fields=['metadata'])

            bulk = []
            for sl, rf in enumerate(rule_findings, start=1):
                bulk.append(PFDQFinding(
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
            PFDQFinding.objects.bulk_create(bulk)
            all_findings_count += len(bulk)
            logger.info('[PFDQTask] Drawing %s → %d findings', seg.drawing_id, len(bulk))

        # Generate & optionally upload reports
        doc.refresh_from_db()

        excel_bytes = generate_excel(doc)
        if excel_bytes:
            project_slug = str(doc.project.project_id) if doc.project_id else 'unassigned'
            key = f'pfd_quality/projects/{project_slug}/reports/{doc.document_id}/findings.xlsx'
            url = upload_to_s3(excel_bytes, key, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            if url:
                doc.excel_s3_url = url

        pdf_bytes = generate_pdf(doc)
        if pdf_bytes:
            project_slug = str(doc.project.project_id) if doc.project_id else 'unassigned'
            key = f'pfd_quality/projects/{project_slug}/reports/{doc.document_id}/findings.pdf'
            url = upload_to_s3(pdf_bytes, key, 'application/pdf')
            if url:
                doc.pdf_s3_url = url

        doc.status = PFDQDocument.Status.COMPLETED
        doc.save(update_fields=['status', 'excel_s3_url', 'pdf_s3_url', 'updated_at'])
        logger.info('[PFDQTask] Completed document_id=%s  total_findings=%d', document_id, all_findings_count)

    except Exception as exc:
        logger.exception('[PFDQTask] Processing failed for document_id=%s: %s', document_id, exc)
        doc.status        = PFDQDocument.Status.FAILED
        doc.error_message = str(exc)
        doc.save(update_fields=['status', 'error_message', 'updated_at'])
        raise self.retry(exc=exc)


def _resolve_file_path(doc) -> str:
    if doc.original_file and hasattr(doc.original_file, 'path'):
        try:
            return doc.original_file.path
        except NotImplementedError:
            pass

    if doc.s3_path:
        return _download_from_s3(doc.s3_path)

    raise ValueError(f'No file path available for document {doc.document_id}')


def _download_from_s3(s3_key: str) -> str:
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
