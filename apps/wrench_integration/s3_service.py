"""
Wrench → RADAI → AWS S3 Export Service
=======================================
Exports Wrench transmittals / documents to the configured S3 bucket in two modes:

    BATCH     – exports all available pages once, then marks the job complete.
    REAL-TIME – iterates pages in a continuous loop (re-runnable Celery periodic task),
                picking up from `last_page_exported` so it can be safely re-queued.

Soft-coded knobs (all at module level – never hardcode inline):
"""
import json
import logging
from datetime import datetime, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.utils import timezone as dj_timezone

from .models import WrenchConfig, WrenchS3SyncJob
from .service import get_transmittals, _ensure_token

logger = logging.getLogger(__name__)

# ── Soft-coded configuration ──────────────────────────────────────────────────
# Bucket resolved at runtime from env → no bucket name in source code
_S3_BUCKET_ENV_KEY    = 'WRENCH_S3_BUCKET'       # env var name
_S3_BUCKET_FALLBACK   = 'wrench-radai'            # fallback if env not set
_S3_REGION_ENV_KEY    = 'AWS_S3_REGION_NAME'
_S3_REGION_FALLBACK   = 'us-east-1'

# Pages per batch iteration – keep S3 objects manageable
_RECORDS_PER_S3_OBJECT = 500   # rows serialised per JSON file
_TRANSMITTAL_FETCH_SIZE = 200  # rows fetched per Wrench API call (max 500)

# Real-time polling: stop after this many new pages if no new data found
_REALTIME_IDLE_PAGE_LIMIT = 3


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_bucket() -> str:
    return getattr(settings, _S3_BUCKET_ENV_KEY.replace('_', '').lower(), None) \
        or getattr(settings, 'WRENCH_S3_BUCKET', None) \
        or _S3_BUCKET_FALLBACK


def _get_region() -> str:
    return getattr(settings, 'AWS_S3_REGION_NAME', None) or _S3_REGION_FALLBACK


def _s3_client():
    """Return a boto3 S3 client using Django AWS settings."""
    return boto3.client(
        's3',
        region_name=_get_region(),
        aws_access_key_id=getattr(settings, 'AWS_ACCESS_KEY_ID', None),
        aws_secret_access_key=getattr(settings, 'AWS_SECRET_ACCESS_KEY', None),
    )


def _build_s3_key(job: WrenchS3SyncJob, entity: str, page: int, timestamp: str) -> str:
    """
    Build a deterministic, partition-friendly S3 key:
      <prefix><entity>/year=YYYY/month=MM/day=DD/page_<NNNN>_<timestamp>.json
    """
    prefix = job.s3_prefix.rstrip('/') + '/' if job.s3_prefix else 'wrench/'
    dt = datetime.utcnow()
    return (
        f"{prefix}{entity}/"
        f"year={dt.year}/month={dt.month:02d}/day={dt.day:02d}/"
        f"page_{page:06d}_{timestamp}.json"
    )


def _upload_records(
    s3,
    bucket: str,
    key: str,
    records: list,
    job_id: int,
    page: int,
    entity: str,
) -> None:
    """Serialise `records` to newline-delimited JSON and PUT to S3."""
    payload = json.dumps({
        'source': 'wrench_smartproject',
        'entity': entity,
        'exported_at': datetime.now(timezone.utc).isoformat(),
        'job_id': job_id,
        'page': page,
        'record_count': len(records),
        'records': records,
    }, default=str, ensure_ascii=False)

    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=payload.encode('utf-8'),
        ContentType='application/json',
        Metadata={
            'wrench-job-id': str(job_id),
            'wrench-entity': entity,
            'wrench-page': str(page),
        },
    )
    logger.info('[S3] Uploaded %d records → s3://%s/%s', len(records), bucket, key)


def _mark_job(job: WrenchS3SyncJob, **kwargs):
    """Update job fields and save only those fields."""
    for k, v in kwargs.items():
        setattr(job, k, v)
    job.save(update_fields=list(kwargs.keys()) + ['updated_at'])


# ── Public API ────────────────────────────────────────────────────────────────

def run_batch_export(job: WrenchS3SyncJob) -> WrenchS3SyncJob:
    """
    Batch mode: fetch ALL transmittals from Wrench page-by-page and write to S3.
    The first call fetches page 1 which returns the full dataset (Wrench behaviour);
    we then paginate in-service via the service layer.
    """
    _mark_job(job, status=WrenchS3SyncJob.STATUS_IN_PROGRESS)

    cfg: WrenchConfig = job.config
    if not cfg:
        _mark_job(job,
                  status=WrenchS3SyncJob.STATUS_FAILED,
                  error_message='No active Wrench config linked to job.',
                  completed_at=dj_timezone.now())
        return job

    s3     = _s3_client()
    bucket = _get_bucket()
    ts     = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')

    total_exported = 0
    total_failed   = 0
    page           = 1

    try:
        # First call gives us `total` — use it to drive pagination
        first = get_transmittals(cfg, page=1, page_size=_TRANSMITTAL_FETCH_SIZE)
        total_available = first['total']
        logger.info('[S3 Batch] Total available from Wrench: %d', total_available)

        import math
        total_pages = math.ceil(total_available / _TRANSMITTAL_FETCH_SIZE)

        for page in range(1, total_pages + 1):
            try:
                result = get_transmittals(cfg, page=page, page_size=_TRANSMITTAL_FETCH_SIZE)
                rows = result['transmittals']
                if not rows:
                    break

                key = _build_s3_key(job, job.entity_type or 'transmittals', page, ts)
                _upload_records(s3, bucket, key, rows, job.id, page, job.entity_type)
                total_exported += len(rows)
                _mark_job(job,
                          records_exported=total_exported,
                          records_failed=total_failed,
                          pages_processed=page,
                          last_page_exported=page)

            except (BotoCoreError, ClientError) as s3_err:
                logger.error('[S3 Batch] S3 error on page %d: %s', page, s3_err)
                total_failed += 1
                _mark_job(job, records_failed=total_failed)

            except Exception as wrench_err:
                logger.error('[S3 Batch] Wrench error on page %d: %s', page, wrench_err)
                total_failed += 1
                _mark_job(job, records_failed=total_failed)

        final_status = WrenchS3SyncJob.STATUS_SUCCESS if total_failed == 0 else WrenchS3SyncJob.STATUS_FAILED
        _mark_job(job,
                  status=final_status,
                  records_exported=total_exported,
                  records_failed=total_failed,
                  pages_processed=page,
                  last_page_exported=page,
                  completed_at=dj_timezone.now(),
                  job_details={
                      'bucket': bucket,
                      'prefix': job.s3_prefix,
                      'total_available': total_available,
                      'total_pages': total_pages,
                  })

    except Exception as exc:
        logger.exception('[S3 Batch] Unexpected error: %s', exc)
        _mark_job(job,
                  status=WrenchS3SyncJob.STATUS_FAILED,
                  error_message=str(exc),
                  completed_at=dj_timezone.now())

    return job


def run_realtime_export_tick(job: WrenchS3SyncJob) -> WrenchS3SyncJob:
    """
    Real-time mode tick: fetch the next N pages starting from `last_page_exported + 1`.
    Called repeatedly by a periodic Celery task. Safe to call multiple times — uses
    `last_page_exported` to continue from where it left off.

    A tick processes up to `_TRANSMITTAL_FETCH_SIZE` records per call and exits if
    `_REALTIME_IDLE_PAGE_LIMIT` consecutive pages return no new data.
    """
    if job.status == WrenchS3SyncJob.STATUS_STOPPED:
        logger.info('[S3 Realtime] Job %d is stopped — skipping tick.', job.id)
        return job

    _mark_job(job, status=WrenchS3SyncJob.STATUS_IN_PROGRESS)

    cfg: WrenchConfig = job.config
    if not cfg:
        _mark_job(job,
                  status=WrenchS3SyncJob.STATUS_FAILED,
                  error_message='No active Wrench config linked to job.',
                  completed_at=dj_timezone.now())
        return job

    s3     = _s3_client()
    bucket = _get_bucket()
    ts     = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')

    next_page  = (job.last_page_exported or 0) + 1
    idle_count = 0

    try:
        result = get_transmittals(cfg, page=next_page, page_size=_TRANSMITTAL_FETCH_SIZE)
        rows   = result['transmittals']

        if not rows:
            idle_count += 1
            if idle_count >= _REALTIME_IDLE_PAGE_LIMIT:
                logger.info('[S3 Realtime] Job %d: no new data for %d pages — marking success.',
                            job.id, idle_count)
                _mark_job(job,
                          status=WrenchS3SyncJob.STATUS_SUCCESS,
                          completed_at=dj_timezone.now())
            return job

        key = _build_s3_key(job, job.entity_type or 'transmittals', next_page, ts)
        _upload_records(s3, bucket, key, rows, job.id, next_page, job.entity_type)

        _mark_job(job,
                  records_exported=job.records_exported + len(rows),
                  pages_processed=job.pages_processed + 1,
                  last_page_exported=next_page,
                  status=WrenchS3SyncJob.STATUS_IN_PROGRESS)

    except (BotoCoreError, ClientError) as s3_err:
        logger.error('[S3 Realtime] S3 error: %s', s3_err)
        _mark_job(job,
                  records_failed=job.records_failed + 1,
                  error_message=str(s3_err))

    except Exception as exc:
        logger.exception('[S3 Realtime] Unexpected error: %s', exc)
        _mark_job(job,
                  status=WrenchS3SyncJob.STATUS_FAILED,
                  error_message=str(exc),
                  completed_at=dj_timezone.now())

    return job


def stop_realtime_job(job: WrenchS3SyncJob):
    """Gracefully stop a running real-time job (also revokes Celery task if tracked)."""
    if job.celery_task_id:
        try:
            from celery.app.control import Control
            from config.celery import app as celery_app
            Control(celery_app).revoke(job.celery_task_id, terminate=True)
            logger.info('[S3] Revoked Celery task %s for job %d', job.celery_task_id, job.id)
        except Exception as exc:
            logger.warning('[S3] Could not revoke Celery task: %s', exc)

    _mark_job(job,
              status=WrenchS3SyncJob.STATUS_STOPPED,
              completed_at=dj_timezone.now())
