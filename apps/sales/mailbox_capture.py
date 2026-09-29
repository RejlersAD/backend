"""Scoped, retry-safe capture of an authoritative incoming mailbox message."""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import serializers
from rest_framework.exceptions import APIException

from .email_permissions import require_email_capture_access, visible_mailbox_connections
from .microsoft_graph import SalesMailboxReadError, SalesMicrosoftGraphService
from .models import SalesEmailIntake


class EmailCaptureConflict(APIException):
    status_code = 409
    default_detail = 'The mailbox configuration changed. Refresh before saving the email.'
    default_code = 'mailbox_capture_changed'


class CaptureRequestSerializer(serializers.Serializer):
    message_id = serializers.RegexField(r'\A[A-Za-z0-9_+=/-]+\Z', max_length=512, trim_whitespace=False)

    def to_internal_value(self, data):
        if (
            not isinstance(data, dict) or set(data) != {'message_id'}
            or not isinstance(data['message_id'], str)
        ):
            raise serializers.ValidationError({'detail': 'Provide only the selected email message_id.'})
        return super().to_internal_value(data)


class CapturedSourceSerializer(serializers.Serializer):
    subject = serializers.CharField(max_length=500, allow_blank=True, trim_whitespace=False)
    sender_name = serializers.CharField(max_length=255, allow_blank=True, trim_whitespace=False)
    sender_email = serializers.EmailField(max_length=254)
    received_at = serializers.DateTimeField()
    sent_at = serializers.DateTimeField(required=False, allow_null=True, default=None)
    body_preview = serializers.CharField(max_length=1_000_000, allow_blank=True, trim_whitespace=False)
    has_attachments = serializers.BooleanField()
    importance = serializers.CharField(max_length=20, allow_blank=True, trim_whitespace=False)
    conversation_id = serializers.CharField(max_length=512, trim_whitespace=False)
    internet_message_id = serializers.CharField(max_length=512, allow_blank=True, trim_whitespace=False)


def mailbox_identity(connection):
    return (
        connection.auth_mode, connection.tenant_id, connection.client_id,
        connection.mailbox_address,
    )


def validate_captured_message(message):
    """Validate the private Graph projection; never accept a browser source body."""
    source = CapturedSourceSerializer(data={
        key: message.get('body_text' if key == 'body_preview' else key)
        for key in CapturedSourceSerializer().fields
    })
    valid_stamps = True
    for key in ('received_at', 'sent_at'):
        raw = message.get(key)
        # Historical/provider sources may lack sent time. An explicit invalid
        # value must not become an assumed timezone or a received-time fallback.
        if key == 'sent_at' and raw is None:
            continue
        try:
            stamp = parse_datetime(raw) if isinstance(raw, str) else None
            valid_stamps = valid_stamps and stamp is not None and timezone.is_aware(stamp)
        except (ValueError, TypeError, OverflowError):
            valid_stamps = False
    if not valid_stamps or not source.is_valid():
        # Do not reflect provider values in serializer errors or application logs.
        raise SalesMailboxReadError('Microsoft returned incomplete or unsupported email content. Nothing was saved.', code='unsupported_source')
    return source.validated_data


def store_captured_message(*, connection, user, message, expected_identity=None):
    """Store a server-fetched projection under the same authority as manual capture.

    Sync invokes this inside its own connection/lease transaction so saved source
    and its durable work item commit together. It is not an HTTP input contract.
    """
    source = validate_captured_message(message)
    expected_identity = expected_identity or mailbox_identity(connection)
    with transaction.atomic():
        current_user = get_user_model().objects.get(pk=user.pk)
        require_email_capture_access(current_user)
        current = get_object_or_404(
            visible_mailbox_connections(current_user).select_for_update(), pk=connection.pk,
        )
        # Re-evaluate visibility after waiting for the row lock as ownership may
        # have changed while the Graph request or another command was running.
        current_user = get_user_model().objects.get(pk=user.pk)
        require_email_capture_access(current_user)
        get_object_or_404(visible_mailbox_connections(current_user), pk=current.pk)
        if mailbox_identity(current) != expected_identity:
            raise EmailCaptureConflict()
        if current.email_intakes.exclude(
            source_mailbox_address=current.mailbox_address,
            source_tenant_id=current.tenant_id,
        ).exists():
            raise EmailCaptureConflict()
        return SalesEmailIntake.objects.get_or_create(
            mailbox_connection=current,
            source_message_id=message['id'],
            defaults={
                **source,
                'source_mailbox_address': current.mailbox_address,
                'source_tenant_id': current.tenant_id,
                'captured_by': current_user,
            },
        )


def capture_mailbox_message(*, connection, user, data):
    """Capture one source once. Retry never changes source or review decisions."""
    require_email_capture_access(user)
    request = CaptureRequestSerializer(data=data)
    request.is_valid(raise_exception=True)
    expected_identity = mailbox_identity(connection)
    get_object_or_404(visible_mailbox_connections(user), pk=connection.pk)
    message = SalesMicrosoftGraphService(connection).get_message_for_capture(
        request.validated_data['message_id'],
    )
    return store_captured_message(
        connection=connection, user=user, message=message, expected_identity=expected_identity,
    )
