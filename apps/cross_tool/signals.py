"""
Cross-Tool Signals
==================
Auto-registers PIDVDocument and PFDQDocument into CrossToolRegistry
on every save (create or update).

SOFT-CODED: Zero changes to pid_verification or pfd_quality apps.
Uses try/except ImportError so this app degrades gracefully if either
source app is absent.
"""
import logging
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


# ── P&ID QC ──────────────────────────────────────────────────────────────────
try:
    from apps.pid_verification.models import PIDVDocument

    @receiver(post_save, sender=PIDVDocument)
    def _register_pid_document(sender, instance, **kwargs):
        from .models import CrossToolRegistry
        from .tasks import sync_registry_to_s3
        try:
            CrossToolRegistry.objects.update_or_create(
                doc_type='pid',
                doc_id=instance.document_id,
                defaults={
                    'project_id':   instance.project.project_id   if instance.project else None,
                    'project_name': instance.project.project_name if instance.project else '',
                    'file_name':    instance.file_name or '',
                    'status':       instance.status   or 'uploaded',
                    'uploaded_by':  instance.uploaded_by,
                    's3_path':      instance.s3_path  or '',
                    's3_synced':    False,
                },
            )
            # Async S3 manifest refresh (non-blocking)
            try:
                sync_registry_to_s3.delay()
            except Exception:
                pass  # Celery unavailable — sync deferred
        except Exception as exc:
            logger.warning('[CrossTool] Failed to register PIDVDocument %s: %s', instance.document_id, exc)

except ImportError:
    logger.debug('[CrossTool] apps.pid_verification not installed — skipping PID signal')


# ── PFD QC ───────────────────────────────────────────────────────────────────
try:
    from apps.pfd_quality.models import PFDQDocument

    @receiver(post_save, sender=PFDQDocument)
    def _register_pfd_document(sender, instance, **kwargs):
        from .models import CrossToolRegistry
        from .tasks import sync_registry_to_s3
        try:
            CrossToolRegistry.objects.update_or_create(
                doc_type='pfd',
                doc_id=instance.document_id,
                defaults={
                    'project_id':   instance.project.project_id   if instance.project else None,
                    'project_name': instance.project.project_name if instance.project else '',
                    'file_name':    instance.file_name or '',
                    'status':       instance.status   or 'uploaded',
                    'uploaded_by':  instance.uploaded_by,
                    's3_path':      instance.s3_path  or '',
                    's3_synced':    False,
                },
            )
            try:
                sync_registry_to_s3.delay()
            except Exception:
                pass
        except Exception as exc:
            logger.warning('[CrossTool] Failed to register PFDQDocument %s: %s', instance.document_id, exc)

except ImportError:
    logger.debug('[CrossTool] apps.pfd_quality not installed — skipping PFD signal')
