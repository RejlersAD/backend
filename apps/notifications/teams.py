"""Private Microsoft Teams delivery for approval assignment notifications."""

import logging

import requests
from celery import shared_task
from django.conf import settings

from .models import Notification, NotificationLog
from .delivery import absolute_action_url, delivery_issue, notification_action_url


logger = logging.getLogger(__name__)


def _display_name(user):
    if user is None:
        return 'Not specified'
    full_name = str(user.get_full_name() or '').strip()
    return full_name or str(getattr(user, 'username', '') or getattr(user, 'email', '') or 'Not specified')


def _absolute_action_url(action_url):
    return absolute_action_url(action_url)


def build_approval_assignment_payload(notification, context=None):
    """Build the stable JSON contract consumed by the RADAI Power Automate flow."""
    context = context or {}
    message_title = str(context.get('title') or 'New approval request assigned')
    event_type = str(context.get('event_type') or 'approval_assignment')
    request_name = str(context.get('request_name') or notification.title or 'Approval request')
    submitted_by = str(context.get('submitted_by') or _display_name(notification.sender))
    description = str(context.get('description') or 'Not specified')
    project_name = str(context.get('project_name') or 'Not specified')
    project_id = str(context.get('project_id') or 'Not specified')
    po_number = str(context.get('po_number') or 'Not issued')
    service = str(context.get('service') or description)
    vendor = str(context.get('vendor') or 'Not specified')
    value = str(context.get('value') or 'Not specified')
    approval_level = context.get('approval_level')
    action_url = _absolute_action_url(notification_action_url(notification))
    recipient_name = _display_name(notification.recipient)
    recipient_email = str(getattr(notification.recipient, 'email', '') or '').strip()
    facts = [
        {'title': 'Request', 'value': request_name},
        {'title': 'PO Number', 'value': po_number},
        {'title': 'Project Name', 'value': project_name},
        {'title': 'Project ID', 'value': project_id},
        {'title': 'Service', 'value': service},
        {'title': 'Description', 'value': description},
        {'title': 'Vendor', 'value': vendor},
        {'title': 'Value', 'value': value},
    ]
    if approval_level is not None:
        facts.append({'title': 'Approval Level', 'value': f'Level {approval_level}'})
    facts.append({'title': 'Submitted By', 'value': submitted_by})
    plain_message = '\n'.join([
        message_title,
        *(f"{fact['title']}: {fact['value']}" for fact in facts),
        f'Open Request: {action_url}',
    ])
    payload = {
        # The native, non-Premium Microsoft Teams webhook trigger requires an
        # Adaptive Card message envelope. The top-level RADAI fields remain so
        # the following Flow-bot action can address recipient_email directly.
        'type': 'message',
        'event_type': event_type,
        'recipient_email': recipient_email,
        'recipient_name': recipient_name,
        'title': message_title,
        'request': request_name,
        'description': description,
        'project_name': project_name,
        'project_id': project_id,
        'po_number': po_number,
        'service': service,
        'vendor': vendor,
        'value': value,
        'currency': str(context.get('currency') or ''),
        'approval_level': approval_level,
        'submitted_by': submitted_by,
        'action_label': notification.action_label or 'Open Request',
        'action_url': action_url,
        'message': plain_message,
        'notification_id': str(notification.pk),
    }
    payload['attachments'] = [{
        'contentType': 'application/vnd.microsoft.card.adaptive',
        'contentUrl': None,
        'content': {
            '$schema': 'http://adaptivecards.io/schemas/adaptive-card.json',
            'type': 'AdaptiveCard',
            'version': '1.4',
            'body': [
                {
                    'type': 'TextBlock',
                    'text': payload['title'],
                    'weight': 'Bolder',
                    'size': 'Medium',
                },
                {
                    'type': 'FactSet',
                    'facts': facts,
                },
            ],
            'actions': [{
                'type': 'Action.OpenUrl',
                'title': payload['action_label'],
                'url': action_url,
            }],
        },
    }]
    return payload


def queue_approval_assignment(notification, context=None):
    """Queue Teams delivery without affecting the in-app approval workflow."""
    if not notification or not getattr(settings, 'TEAMS_APPROVAL_WEBHOOK_URL', ''):
        return False
    if not str(getattr(notification.recipient, 'email', '') or '').strip():
        logger.warning('Teams approval notification %s has no recipient email', notification.pk)
        return False
    try:
        serializable_context = dict(context or {})
        # Legacy callers may still supply a date object. It is no longer part
        # of the Teams contract and must not enter the JSON task payload.
        serializable_context.pop('due_date', None)
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

    # Some legacy production databases contain duplicated notification IDs
    # from an earlier schema drift. Prefer the newest row so one duplicate
    # cannot crash delivery with MultipleObjectsReturned.
    notification = (
        Notification.objects.select_related('recipient', 'sender')
        .filter(pk=notification_id)
        .order_by('-created_at')
        .first()
    )
    if notification is None:
        logger.warning('Teams approval notification %s no longer exists', notification_id)
        return {'status': 'missing'}
    reason = delivery_issue(notification, 'teams')
    if reason:
        NotificationLog.objects.create(notification=notification, action='teams_skipped', details={'reason': reason})
        return {'status': 'skipped', 'reason': reason}
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
