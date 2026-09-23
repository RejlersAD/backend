"""Scheduled portfolio synchronization with bounded retries for Graph throttling."""
from celery import shared_task

from .sync import TransientGraphError, sync_enabled, sync_sharepoint


@shared_task(bind=True, max_retries=3, ignore_result=True)
def sync_portfolio_sharepoint(self):
    if not sync_enabled():
        return {'status': 'disabled'}
    try:
        return sync_sharepoint()
    except TransientGraphError as exc:
        delay = exc.retry_after or min(300, 60 * 2 ** self.request.retries)
        raise self.retry(exc=exc, countdown=delay, max_retries=3)
