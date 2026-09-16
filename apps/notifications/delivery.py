"""Revalidate queued notifications against the current recipient and approval."""

from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework.exceptions import ValidationError


def safe_action_url(value):
    """Browser notification actions stay within the configured RADAI origin."""
    value = str(value or '').strip()
    if not value or '\\' in value or any(ord(char) < 32 for char in value):
        return '/notifications'
    try:
        parsed = urlsplit(value)
        frontend = urlsplit(str(getattr(settings, 'FRONTEND_URL', '') or ''))
        if parsed.scheme or parsed.netloc:
            if (parsed.scheme, parsed.netloc) != (frontend.scheme, frontend.netloc):
                return '/notifications'
            if parsed.scheme not in ('https', 'http'):
                return '/notifications'
        elif not value.startswith('/') or value.startswith('//'):
            return '/notifications'
        return urlunsplit(('', '', parsed.path or '/', parsed.query, parsed.fragment))
    except ValueError:
        return '/notifications'


def notification_action_url(notification):
    metadata = getattr(notification, 'metadata', None) or {}
    if isinstance(metadata, dict) and _is_approval_assignment(metadata):
        if metadata.get('po_id'):
            return safe_action_url(f"/procurement/orders/{metadata['po_id']}")
        if metadata.get('pr_id'):
            return safe_action_url(f"/procurement/requisitions/{metadata['pr_id']}")
    return safe_action_url(getattr(notification, 'action_url', None))


def absolute_action_url(value):
    frontend = str(getattr(settings, 'FRONTEND_URL', '') or '').rstrip('/')
    return frontend + safe_action_url(value)


def _is_approval_assignment(metadata):
    return bool(metadata.get('requires_action')) or metadata.get('event_type') == 'approval_assignment'


def approval_assignment_issue(notification):
    """Return a skip reason when this particular approval request is obsolete."""
    metadata = getattr(notification, 'metadata', None) or {}
    if not isinstance(metadata, dict) or not _is_approval_assignment(metadata):
        return ''
    # Other modules have their own approval services. Only these two document
    # types are owned by the procurement workflow checked here.
    if not metadata.get('po_id') and not metadata.get('pr_id'):
        return ''
    try:
        expected_level = int(metadata['approval_level'])
        recipient = notification.recipient
        if metadata.get('po_id'):
            from apps.procurement.models import PurchaseOrder
            from apps.procurement.services import purchase_order_approvals as approvals

            order = PurchaseOrder.objects.filter(pk=metadata['po_id']).first()
            if order is None or not approvals.can_approve(order, recipient):
                return 'approval_no_longer_assigned'
            for index, entry in approvals._active_entries(order.approval_log or []):
                if not approvals._entry_matches_user(entry, recipient):
                    continue
                if approvals._entry_level(entry, index) != expected_level:
                    continue
                if str(entry.get('stage') or '') != str(metadata.get('approval_stage') or ''):
                    continue
                if str(entry.get('assignment_id') or '') != str(metadata.get('assignment_id') or ''):
                    continue
                return ''
        else:
            from apps.procurement.models import PurchaseRequisition
            from apps.procurement.services.requisition_workflow import RequisitionWorkflowService as approvals

            requisition = PurchaseRequisition.objects.filter(pk=metadata['pr_id']).first()
            if requisition is None or not approvals.can_approve(requisition, recipient):
                return 'approval_no_longer_assigned'
            workflow = approvals._workflow(requisition)
            active_level, stages = approvals._active_level_stages(requisition, workflow)
            if active_level == expected_level and any(
                approvals._stage_matches_user(stage, recipient)
                and str(stage.get('assignment_id') or '') == str(metadata.get('assignment_id') or '')
                for _, stage in stages
            ):
                return ''
    except (KeyError, TypeError, ValueError, DjangoValidationError, ValidationError):
        return 'approval_context_invalid'
    return 'approval_assignment_changed'


def delivery_issue(notification, channel):
    """Run immediately before each attempted delivery, including retries."""
    recipient = notification.recipient
    if not getattr(recipient, 'is_active', True):
        return 'recipient_inactive'
    try:
        profile = getattr(recipient, 'rbac_profile', None)
    except ObjectDoesNotExist:
        profile = None
    if profile is not None and (profile.is_deleted or profile.status != 'active'):
        return 'recipient_inactive'
    if getattr(notification, 'status', None) == 'ARCHIVED':
        return 'notification_archived'
    expires_at = getattr(notification, 'expires_at', None)
    if expires_at and expires_at <= timezone.now():
        return 'notification_expired'
    if channel == 'web_push' and (
        not notification.send_in_app or notification.is_read or notification.status != 'SENT'
    ):
        return 'notification_not_unread'
    if channel == 'email' and (not notification.send_email or notification.email_sent):
        return 'email_not_required'
    try:
        preferences = getattr(recipient, 'notification_preferences', None)
    except ObjectDoesNotExist:
        preferences = None
    if preferences is not None:
        if channel == 'web_push' and not preferences.enable_in_app:
            return 'channel_disabled'
        if channel == 'email' and not preferences.enable_email:
            return 'channel_disabled'
    return approval_assignment_issue(notification)
