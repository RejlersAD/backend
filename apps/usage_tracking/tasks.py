"""Celery tasks for usage tracking."""
import logging

from celery import shared_task

from . import config as cfg

logger = logging.getLogger(__name__)


@shared_task(name='usage_tracking.apply_log_retention')
def apply_log_retention():
    """Nightly: roll old usage_log / system_activity rows into daily summaries and delete them."""
    if not cfg.LOG_RETENTION_ENABLED:
        logger.info('log retention skipped: LOG_RETENTION_ENABLED is false')
        return {'skipped': 'disabled'}
    from .retention import apply_retention
    return {'results': apply_retention()}
