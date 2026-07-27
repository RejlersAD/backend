"""Celery tasks for the RADAI Project Planning Application."""
from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name='apps.planning_intelligence.tasks.parse_uploaded_planning_file')
def parse_uploaded_planning_file(file_id):
    """Extracts text from an uploaded PlanningFile in the background so the
    upload request never blocks on PDF/Excel parsing (RADAI global rule)."""
    from .models import PlanningFile
    from .services.parsers import extract_text

    try:
        planning_file = PlanningFile.objects.get(pk=file_id)
    except PlanningFile.DoesNotExist:
        logger.warning('parse_uploaded_planning_file: file %s not found', file_id)
        return {'file_id': file_id, 'error': 'not_found'}

    planning_file.parse_status = 'processing'
    planning_file.save(update_fields=['parse_status', 'updated_at'])

    try:
        planning_file.file.open('rb')
        text, confidence = extract_text(planning_file.file, planning_file.original_filename)
        planning_file.extracted_text = text
        planning_file.confidence_score = confidence
        planning_file.parse_status = 'done' if text else 'failed'
        if not text:
            planning_file.parse_error = 'No text could be extracted from this file.'
        planning_file.save(update_fields=[
            'extracted_text', 'confidence_score', 'parse_status', 'parse_error', 'updated_at',
        ])
    except Exception as exc:  # noqa: BLE001
        logger.warning('parse_uploaded_planning_file failed for %s: %s', file_id, exc)
        planning_file.parse_status = 'failed'
        planning_file.parse_error = str(exc)
        planning_file.save(update_fields=['parse_status', 'parse_error', 'updated_at'])
    finally:
        try:
            planning_file.file.close()
        except Exception:  # noqa: BLE001
            pass

    return {'file_id': planning_file.id, 'parse_status': planning_file.parse_status}
