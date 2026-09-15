"""Celery tasks for Project Management background jobs."""
from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name='apps.project_control.tasks.finance_sync_all_projects', ignore_result=True)
def finance_sync_all_projects():
    """Nightly task: rebuild structured project cost ledgers."""
    from .services.finance_sync import sync_all_projects
    try:
        result = sync_all_projects()
        logger.info('finance_sync_all_projects: %s', result)
        return result
    except Exception as exc:  # noqa: BLE001
        logger.exception('finance_sync_all_projects failed: %s', exc)
        return {'error': str(exc)}


@shared_task(name='apps.project_control.tasks.parse_uploaded_document')
def parse_uploaded_document(document_id: int):
    """Phase 1: light-weight metadata pass for an uploaded document.

    Phase 2/4 will replace this with AI extraction; for now it just stamps
    parse_status='done' so the UI shows the file as processed.
    """
    from .models import ProjectDocument
    try:
        doc = ProjectDocument.objects.get(pk=document_id, is_deleted=False)
    except ProjectDocument.DoesNotExist:
        logger.warning('parse_uploaded_document: doc %s not found', document_id)
        return {'document_id': document_id, 'error': 'not_found'}

    doc.parse_status = 'done'
    doc.parsed_data = {
        'phase': 1,
        'note': 'Metadata-only parse — AI extraction lands in Phase 2/4.',
    }
    # A queued metadata pass must not revive or change a document removed meanwhile.
    from django.utils import timezone
    updated = ProjectDocument.objects.filter(pk=doc.pk, is_deleted=False).update(
        parse_status=doc.parse_status, parsed_data=doc.parsed_data, updated_at=timezone.now(),
    )
    if not updated:
        return {'document_id': document_id, 'error': 'not_found'}
    return {'document_id': doc.id, 'parse_status': doc.parse_status}


@shared_task(name='apps.project_control.tasks.compute_daily_cost_snapshot_all', ignore_result=True)
def compute_daily_cost_snapshot_all():
    """Phase 3 placeholder — wired to beat schedule but no-ops until flag flips."""
    from .config import is_phase_enabled
    if not is_phase_enabled('phase_3_evm_forecast'):
        return {'skipped': True, 'reason': 'phase_3_evm_forecast disabled'}
    # Future: iterate projects, persist CostSnapshot rows.
    return {'skipped': True, 'reason': 'implementation pending'}
