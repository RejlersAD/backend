"""
Celery Background Tasks — SLD Verification
============================================
Task pipeline:
  1. Analyse SLD document (segment pages, extract elements, run rules)
  2. Generate Excel & PDF reports → upload to S3
  3. Update document status = completed (or failed)
"""
import logging
import os
import tempfile

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name='sld_verification.process_document',
    max_retries=2,
    default_retry_delay=30,
    soft_time_limit=540,   # 9 min soft limit
    time_limit=600,        # 10 min hard limit
)
def process_sld_document(self, document_id: str):
    """
    Main background task.
    Receives the string form of SLDDocument.document_id (UUID).
    """
    from apps.sld_verification.models import SLDDocument
    from apps.sld_verification.services.analysis import analyse_sld_document
    from apps.sld_verification.services.export_service import (
        generate_excel, generate_pdf, upload_to_s3
    )

    logger.info('[SLDVTask] Starting processing for document_id=%s', document_id)

    # ── 1. Load document ──────────────────────────────────────────────────
    try:
        doc = SLDDocument.objects.get(document_id=document_id)
    except SLDDocument.DoesNotExist:
        logger.error('[SLDVTask] Document %s not found', document_id)
        return

    doc.status = SLDDocument.Status.PROCESSING
    doc.save(update_fields=['status'])

    try:
        # ── 2. Resolve file path ──────────────────────────────────────────
        file_path = _resolve_file_path(doc)

        # ── 3. Run full analysis pipeline ─────────────────────────────────
        analyse_sld_document(str(doc.document_id), file_path)

        # ── 4. Generate & upload reports ──────────────────────────────────
        doc.refresh_from_db()

        excel_bytes = generate_excel(doc)
        if excel_bytes:
            project_slug = str(doc.project.project_id) if doc.project_id else 'unassigned'
            key = f'sld_verification/projects/{project_slug}/reports/{doc.document_id}/findings.xlsx'
            doc.excel_s3_url = upload_to_s3(excel_bytes, key, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

        pdf_bytes = generate_pdf(doc)
        if pdf_bytes:
            project_slug = str(doc.project.project_id) if doc.project_id else 'unassigned'
            key = f'sld_verification/projects/{project_slug}/reports/{doc.document_id}/findings.pdf'
            doc.pdf_s3_url = upload_to_s3(pdf_bytes, key, 'application/pdf')

        doc.status = SLDDocument.Status.COMPLETED
        doc.save(update_fields=['status', 'excel_s3_url', 'pdf_s3_url'])
        logger.info('[SLDVTask] Completed document_id=%s', document_id)

    except Exception as exc:
        logger.exception('[SLDVTask] Processing failed for document_id=%s: %s', document_id, exc)
        doc.status        = SLDDocument.Status.FAILED
        doc.error_message = str(exc)
        doc.save(update_fields=['status', 'error_message'])
        raise self.retry(exc=exc)


def _resolve_file_path(doc) -> str:
    """
    Return a local filesystem path for the document file.

    Resolution order:
      1. Local FileField  →  .path
      2. S3 FileField     →  download to tmp file
      3. Explicit s3_path →  download to tmp file
    """
    if doc.original_file:
        try:
            path = doc.original_file.path
            if path:
                return path
        except NotImplementedError:
            pass

        s3_key = getattr(doc.original_file, 'name', None)
        if s3_key:
            logger.info('[SLDVTask] Downloading file from S3 key: %s', s3_key)
            return _download_from_s3(s3_key)

    if doc.s3_path:
        logger.info('[SLDVTask] Downloading file from explicit s3_path: %s', doc.s3_path)
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
