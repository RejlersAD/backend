import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, name='cross_recommendation.sync_s3_snapshot', max_retries=1, default_retry_delay=10)
def sync_s3_snapshot(self):
    try:
        from .services.s3_snapshot import sync_snapshot_to_s3
        result = sync_snapshot_to_s3()
        logger.info('[CrossRecommendation] Snapshot sync result: %s', result)
        return result
    except Exception as exc:
        logger.exception('[CrossRecommendation] Snapshot sync failed: %s', exc)
        raise self.retry(exc=exc)
