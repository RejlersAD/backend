"""Background tasks for RBAC operations."""
import logging

from celery import shared_task
from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)


@shared_task(name='rbac.capture_ai_workforce', autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def capture_ai_workforce():
    from .ai_snapshots import capture_workforce
    return {'created': capture_workforce()}


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def send_bulk_welcome_email(self, user_id, temporary_password):
    """Send a bulk-import welcome email outside the HTTP import request."""
    from apps.users.email_service import EmailService

    user = get_user_model().objects.get(pk=user_id)
    sent = EmailService.send_welcome_email(user, temporary_password)
    if not sent:
        raise RuntimeError(f'Welcome email could not be sent to {user.email}')
    logger.info('Bulk welcome email sent to %s', user.email)
    return {'user_id': str(user.id), 'email': user.email, 'sent': True}
