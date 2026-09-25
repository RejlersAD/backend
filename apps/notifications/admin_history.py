"""Read-only, metadata-only notification event history for System Health."""
from datetime import timedelta
import re

from django.db.models import Q, Value
from django.db.models.functions import Concat, Lower
from django.utils import timezone
from rest_framework import mixins, serializers, viewsets
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated

from apps.rbac.permissions import IsSuperAdmin
from .models import NotificationCategory, NotificationLog


# These labels describe recorded events, not confirmed recipient delivery.
# Never derive an outcome from the parent's mutable current status.
EVENTS = {
    'created': ('notification', 'recorded', 'Notification created', 'Recorded'),
    'read': ('notification', 'read', 'Notification marked read', 'Marked read'),
    'archived': ('notification', 'archived', 'Notification archived', 'Archived'),
    'email_sent': ('email', 'sent', 'Email send recorded', 'Email send recorded'),
    'email_skipped': ('email', 'skipped', 'Email send skipped', 'Skipped'),
    'teams_sent': ('teams', 'sent', 'Teams request accepted', 'Teams request accepted'),
    'teams_failed': ('teams', 'failed', 'Teams request failed', 'Failed'),
    'teams_skipped': ('teams', 'skipped', 'Teams request skipped', 'Skipped'),
    'web_push_sent': ('web_push', 'sent', 'Push request accepted', 'Push request accepted'),
    'web_push_failed': ('web_push', 'failed', 'Push request failed', 'Failed'),
    'web_push_skipped': ('web_push', 'skipped', 'Push request skipped', 'Skipped'),
}
OTHER_EVENT = ('other', 'unknown', 'Other event', 'Unknown')
REASONS = {
    'recipient_inactive': 'Recipient account is inactive',
    'recipient_changed': 'Recipient changed',
    'notification_archived': 'Notification was archived',
    'notification_expired': 'Notification expired',
    'notification_not_unread': 'Notification is no longer eligible for browser push',
    'email_not_required': 'Email is not required',
    'channel_disabled': 'Channel is disabled',
    'approval_no_longer_current': 'Approval is no longer current',
    'approval_no_longer_assigned': 'Approval is no longer assigned to this recipient',
    'approval_recipient_changed': 'Approval recipient changed',
    'approval_context_invalid': 'Approval context is invalid',
    'approval_context_unsupported': 'Approval context is unsupported',
    'approval_assignment_changed': 'Approval assignment changed',
}


class IntegerQueryField(serializers.IntegerField):
    def to_internal_value(self, data):
        if not re.fullmatch(r'[0-9]+', str(data)):
            self.fail('invalid')
        return super().to_internal_value(data)


class HistoryQuerySerializer(serializers.Serializer):
    hours = serializers.ChoiceField(choices=(24, 168, 720), default=24)
    page = IntegerQueryField(min_value=1, max_value=2147483647, default=1)
    page_size = IntegerQueryField(min_value=1, max_value=100, default=25)
    search = serializers.CharField(max_length=200, allow_blank=True, default='')
    channel = serializers.ChoiceField(
        choices=('notification', 'email', 'teams', 'web_push', 'other'), allow_blank=True, default='')
    outcome = serializers.ChoiceField(
        choices=('recorded', 'sent', 'read', 'archived', 'failed', 'skipped', 'unknown'),
        allow_blank=True, default='')


class NotificationHistorySerializer(serializers.BaseSerializer):
    """An explicit allowlist; no notification content or provider payloads."""

    def to_representation(self, instance):
        key = str(instance.action or '').lower()
        channel, outcome, event_label, outcome_label = EVENTS.get(key, OTHER_EVENT)
        recipient = instance.notification.recipient
        category = instance.notification.category
        category_name = category.name if category else None
        if category_name not in dict(NotificationCategory.CATEGORY_CHOICES):
            category_name = None
        details = instance.details if isinstance(instance.details, dict) else {}
        reason = details.get('reason')
        if channel == 'notification':
            delivery_status, delivery_status_label = 'not_applicable', 'Not applicable'
        else:
            delivery_status, delivery_status_label = outcome, outcome_label
        # Inbox read state is current, independent of this row's event time or
        # transport outcome. Archive and mark-unread do not reliably update status.
        is_read = instance.notification.is_read
        return {
            'id': instance.pk,
            'timestamp': serializers.DateTimeField().to_representation(instance.timestamp),
            'notification_id': instance.notification_id,
            'recipient': {
                'id': str(recipient.pk),
                'name': recipient.get_full_name().strip() or recipient.username,
                'username': recipient.username,
                'email': recipient.email,
            },
            'category': category_name,
            'action': key if key in EVENTS else 'other',
            'event_label': event_label,
            'channel': channel,
            'outcome': outcome,
            'outcome_label': outcome_label,
            'delivery_status': delivery_status,
            'delivery_status_label': delivery_status_label,
            'read_status': 'read' if is_read else 'unread',
            'read_status_label': 'Marked read' if is_read else 'Unread',
            'reason_label': REASONS.get(reason) if outcome == 'skipped' and isinstance(reason, str) else None,
        }


class NotificationHistoryPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = 'page_size'
    max_page_size = 100

    def get_paginated_response(self, data):
        response = super().get_paginated_response(data)
        response.data['timezone'] = timezone.get_current_timezone_name()
        return response


class NotificationHistoryViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """System Health event metadata; never mark read, retry, export or delete."""
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    permission_action = 'read'
    http_method_names = ['get', 'head', 'options']
    serializer_class = NotificationHistorySerializer
    pagination_class = NotificationHistoryPagination
    filter_backends = []

    def get_queryset(self):
        query = HistoryQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        filters = query.validated_data
        cutoff = timezone.now() - timedelta(hours=filters['hours'])
        queryset = NotificationLog.objects.filter(timestamp__gte=cutoff).select_related(
            'notification__recipient', 'notification__category',
        ).only(
            'id', 'timestamp', 'action', 'details', 'notification_id',
            'notification__recipient_id', 'notification__category_id',
            'notification__is_read',
            'notification__recipient__id', 'notification__recipient__first_name',
            'notification__recipient__last_name', 'notification__recipient__username',
            'notification__recipient__email', 'notification__category__name',
        ).annotate(history_action=Lower('action'))
        for name, tuple_index, unknown in (('channel', 0, 'other'), ('outcome', 1, 'unknown')):
            value = filters[name]
            if value == unknown:
                queryset = queryset.exclude(history_action__in=EVENTS)
            elif value:
                queryset = queryset.filter(history_action__in=[
                    key for key, event in EVENTS.items() if event[tuple_index] == value
                ])
        search = filters['search']
        if search:
            queryset = queryset.annotate(recipient_name=Concat(
                'notification__recipient__first_name', Value(' '), 'notification__recipient__last_name',
            ))
            matching = (Q(recipient_name__icontains=search)
                        | Q(notification__recipient__username__icontains=search)
                        | Q(notification__recipient__email__icontains=search))
            # BigAutoField comparisons must stay in PostgreSQL's integer range.
            if search.isdecimal() and len(search) <= 19:
                number = int(search)
                if number <= 9223372036854775807:
                    matching |= Q(notification_id=number)
            queryset = queryset.filter(matching)
        return queryset.order_by('-timestamp', '-pk')
