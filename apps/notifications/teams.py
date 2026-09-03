"""Private Microsoft Teams delivery for approval assignment notifications."""

import logging
from datetime import date, datetime

import requests
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import Notification, NotificationLog


logger = logging.getLogger(__name__)


def _display_name(user):
    if user is None:
        return 'Not specified'
    full_name = str(user.get_full_name() or '').strip()
    return full_name or str(getattr(user, 'username', '') or getattr(user, 'email', '') or 'Not specified')


def _format_due_date(value):
    if not value:
        return 'Not specified'
    if isinstance(value, datetime):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        value = value.date()
    if isinstance(value, date):
        return value.strftime('%d-%b-%Y')
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).strftime('%d-%b-%Y')
    except (TypeError, ValueError):
        return str(value)


def _absolute_action_url(action_url):
    action_url = str(action_url or '').strip()
    if action_url.startswith(('https://', 'http://')):
        return action_url
    frontend_url = str(getattr(settings, 'FRONTEND_URL', '') or '').rstrip('/')
    return f"{frontend_url}/{action_url.lstrip('/')}" if action_url else frontend_url


def build_approval_assignment_payload(notification, context=None):
    """Build the stable JSON contract consumed by the RADAI Power Automate flow."""
    context = context or {}
    request_name = str(context.get('request_name') or notification.title or 'Approval request')
    submitted_by = str(context.get('submitted_by') or _display_name(notification.sender))
    due_date = _format_due_date(context.get('due_date'))
    action_url = _absolute_action_url(notification.action_url)
    recipient_name = _display_name(notification.recipient)
    recipient_email = str(getattr(notification.recipient, 'email', '') or '').strip()
    plain_message = (
        'New approval request assigned\n'
        f'Request: {request_name}\n'
        f'Submitted By: {submitted_by}\n'
        f'Due Date: {due_date}\n'
        f'Open Request: {action_url}'
    )
    return {
        'event_type': 'approval_assignment',
        'recipient_email': recipient_email,
        'recipient_name': recipient_name,
        'title': 'New approval request assigned',
        'request': request_name,
        'submitted_by': submitted_by,
        'due_date': due_date,
        'action_label': notification.action_label or 'Open Request',
        'action_url': action_url,
        'message': plain_message,
        'notification_id': str(notification.pk),
    }


def queue_approval_assignment(notification, context=None):
    """Queue Teams delivery without affecting the in-app approval workflow."""
    if not notification or not getattr(settings, 'TEAMS_APPROVAL_WEBHOOK_URL', ''):
        return False
    if not str(getattr(notification.recipient, 'email', '') or '').strip():
        logger.warning('Teams approval notification %s has no recipient email', notification.pk)
        return False
    try:
        serializable_context = dict(context or {})
        serializable_context['due_date'] = _format_due_date(serializable_context.get('due_date'))
        send_teams_approval_assignment.delay(notification.pk, serializable_context)
        return True
    except Exception:
        logger.exception('Unable to queue Teams approval notification %s', notification.pk)
        return False


@shared_task(bind=True, max_retries=3)
def send_teams_approval_assignment(self, notification_id, context=None):
    """POST an assignment to Power Automate, which sends a private Flow-bot chat."""
    webhook_url = str(getattr(settings, 'TEAMS_APPROVAL_WEBHOOK_URL', '') or '').strip()
    if not webhook_url:
        return {'status': 'disabled'}

    notification = Notification.objects.select_related('recipient', 'sender').get(pk=notification_id)
    payload = build_approval_assignment_payload(notification, context)
    try:
        response = requests.post(
            webhook_url,
            json=payload,
            timeout=max(1, int(getattr(settings, 'TEAMS_APPROVAL_WEBHOOK_TIMEOUT', 10))),
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        NotificationLog.objects.create(
            notification=notification,
            action='teams_failed',
            details={'error': str(exc)[:500]},
        )
        raise self.retry(exc=exc, countdown=min(60, 5 * (2 ** self.request.retries)))

    NotificationLog.objects.create(
        notification=notification,
        action='teams_sent',
        details={'recipient_email': payload['recipient_email']},
    )
    return {'status': 'sent'}
