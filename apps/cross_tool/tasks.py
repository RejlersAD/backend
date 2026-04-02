"""
Cross-Tool S3 Sync Task
=======================
Writes a unified manifest.json to S3 whenever a document is registered.
Path: cross_tool_registry/manifest.json

Non-critical — failures are logged but never raised to callers.
"""
import json
import logging
from django.conf import settings
from django.utils import timezone
from celery import shared_task

logger = logging.getLogger(__name__)

# SOFT-CODED: manifest S3 key — change here to adjust storage location
S3_MANIFEST_KEY = 'cross_tool_registry/manifest.json'


@shared_task(
    name='cross_tool.sync_registry_to_s3',
    ignore_result=True,
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def sync_registry_to_s3(self):
    """Build registry manifest and write to S3."""
    if not getattr(settings, 'USE_S3', False):
        logger.debug('[CrossTool] USE_S3=False — skipping manifest sync')
        return

    try:
        import boto3
        from .models import CrossToolRegistry

        entries = list(
            CrossToolRegistry.objects.select_related('uploaded_by').values(
                'doc_type', 'doc_id', 'project_id', 'project_name',
                'file_name', 'status', 's3_path', 'registered_at',
                'uploaded_by__email',
            )
        )

        manifest = {
            'generated_at':   timezone.now().isoformat(),
            'total':          len(entries),
            'pid_count':      sum(1 for e in entries if e['doc_type'] == 'pid'),
            'pfd_count':      sum(1 for e in entries if e['doc_type'] == 'pfd'),
            'documents':      entries,
        }

        s3 = boto3.client(
            's3',
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_S3_REGION_NAME,
        )
        s3.put_object(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=S3_MANIFEST_KEY,
            Body=json.dumps(manifest, default=str, indent=2).encode('utf-8'),
            ContentType='application/json',
        )

        CrossToolRegistry.objects.filter(s3_synced=False).update(s3_synced=True)
        logger.info('[CrossTool] S3 manifest synced — %d docs total', len(entries))

    except Exception as exc:
        logger.warning('[CrossTool] S3 manifest sync failed: %s', exc)
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            pass
