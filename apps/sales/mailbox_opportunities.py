"""Explicit reviewed creation from one scoped live email; no mailbox writes."""

import hashlib
import json

from django.core import signing
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import APIException, PermissionDenied

from .email_permissions import (
    require_email_opportunity_access, visible_email_clients, visible_email_opportunities,
)
from .microsoft_graph import SalesMicrosoftGraphService
from .models import OpportunityAuditEvent
from .serializers import DealCreateSerializer


REVIEW_SALT = 'sales-mailbox-opportunity-review-v1'
REVIEW_MAX_AGE = 1800
REVIEW_FIELDS = (
    'deal_name', 'client', 'client_reference', 'estimated_value', 'currency',
    'expected_close_date', 'submission_due_date', 'scope_type', 'description',
)
SOURCE_FIELDS = (
    'id', 'subject', 'sender_name', 'sender_email', 'received_at', 'sent_at',
    'body_text', 'body_content', 'to_recipients', 'cc_recipients', 'has_attachments',
)


class EmailReviewConflict(APIException):
    status_code = 409
    default_detail = 'The email changed during review. Reload it before creating the opportunity.'

    def __init__(self, detail=None, code='email_review_changed'):
        super().__init__({'detail': detail or self.default_detail, 'code': code})


class EmailReviewExpired(APIException):
    status_code = 410
    default_detail = 'The email review has expired. Reload the email before creating the opportunity.'


def _digest(value):
    return hashlib.sha256(json.dumps(
        value, cls=DjangoJSONEncoder, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')).hexdigest()


def _connection_scope(connection):
    return {
        'connection': str(connection.pk), 'mailbox': connection.mailbox_address,
        'tenant': connection.tenant_id, 'client': connection.client_id,
        'mode': connection.auth_mode,
    }


def _source_digest(message):
    return _digest({field: message.get(field) for field in SOURCE_FIELDS})


def _source_identity(connection, message_id):
    # Replacing the app credential/configuration must not create a second source
    # identity for the same mailbox item. Review tokens bind the fuller config.
    return _digest({
        'connection': str(connection.pk), 'tenant': connection.tenant_id.casefold(),
        'mailbox': connection.mailbox_address.casefold(), 'message': message_id,
    })


def email_review_token(connection, user, message):
    return signing.dumps({
        'scope': _digest(_connection_scope(connection)), 'actor': str(user.pk),
        'message': message['id'], 'source': _source_digest(message),
    }, salt=REVIEW_SALT, compress=True)


def _read_review_token(token, connection, user, message_id):
    if not isinstance(token, str) or not token or len(token) > 8000:
        raise serializers.ValidationError({'source_token': 'Reload the email before creating an opportunity.'})
    try:
        review = signing.loads(token, salt=REVIEW_SALT, max_age=REVIEW_MAX_AGE)
    except signing.SignatureExpired:
        raise EmailReviewExpired() from None
    except (signing.BadSignature, ValueError, TypeError):
        raise serializers.ValidationError({'source_token': 'The email review is invalid. Reload the email.'}) from None
    if (
        not isinstance(review, dict) or review.get('actor') != str(user.pk)
        or review.get('scope') != _digest(_connection_scope(connection))
        or review.get('message') != message_id
        or not isinstance(review.get('source'), str)
    ):
        raise serializers.ValidationError({'source_token': 'The email review is invalid. Reload the email.'})
    return review


def _reviewed_serializer(data, user):
    if not isinstance(data, dict) or set(data) - {*REVIEW_FIELDS, 'message_id', 'source_token'}:
        raise serializers.ValidationError({'detail': 'Only reviewed opportunity fields may be submitted.'})
    required = ('deal_name', 'client', 'estimated_value', 'currency', 'expected_close_date')
    errors = {key: ['This field is required.'] for key in required if data.get(key) in (None, '')}
    if errors:
        raise serializers.ValidationError(errors)
    try:
        client_id = serializers.UUIDField().run_validation(data['client'])
    except serializers.ValidationError:
        raise serializers.ValidationError({'client': 'Select an accessible client.'}) from None
    client = visible_email_clients(user).filter(pk=client_id).first()
    if client is None:
        raise serializers.ValidationError({'client': 'Select an accessible client.'})
    payload = {key: data[key] for key in REVIEW_FIELDS if key in data}
    payload.update({
        'client': str(client.pk), 'client_reference': data.get('client_reference') or '',
        'scope_type': data.get('scope_type') or 'other',
        'description': data.get('description') or '',
        'submission_due_date': data.get('submission_due_date') or None,
    })
    serializer = DealCreateSerializer(data=payload)
    serializer.is_valid(raise_exception=True)
    normalized = dict(serializer.validated_data)
    normalized['client'] = str(client.pk)
    return serializer, _digest(normalized)


def convert_mailbox_message(*, connection, connection_queryset, user, data):
    """Deduplicate within this configured connection using a locked audit lookup."""
    require_email_opportunity_access(user)
    serializer, payload_hash = _reviewed_serializer(data, user)
    message_id = data.get('message_id')
    review = _read_review_token(data.get('source_token'), connection, user, message_id)
    # The provider source is fetched with our own mailbox and identity. Client
    # supplied body, subject, mailbox and source evidence are never accepted.
    message = SalesMicrosoftGraphService(connection).get_message(message_id)
    if message['id'] != message_id or _source_digest(message) != review['source']:
        raise EmailReviewConflict()
    source_hash = _source_identity(connection, message['id'])
    with transaction.atomic():
        locked = get_object_or_404(connection_queryset().select_for_update(), pk=connection.pk)
        require_email_opportunity_access(user)
        if _connection_scope(locked) != _connection_scope(connection):
            raise EmailReviewConflict('The mailbox connection changed. Reload the email before continuing.')
        client = visible_email_clients(user).select_for_update().filter(
            pk=serializer.validated_data['client'].pk,
        ).first()
        if client is None:
            raise serializers.ValidationError({'client': 'Select an accessible client.'})
        previous = OpportunityAuditEvent.objects.filter(
            event_type='opportunity_created_from_email',
            data__mailbox_source_hash=source_hash,
        ).first()
        if previous:
            opportunity = visible_email_opportunities(user).filter(pk=previous.opportunity_id).first()
            if opportunity is None:
                raise PermissionDenied('This email already has an opportunity you cannot access.')
            if previous.data.get('reviewed_payload_hash') != payload_hash:
                raise EmailReviewConflict(
                    'An opportunity already exists for this email with different reviewed fields.',
                    code='email_already_converted',
                )
            if previous.data.get('source_content_hash') != review['source']:
                raise EmailReviewConflict(
                    'This email changed after its opportunity was created.', code='email_already_converted',
                )
            return opportunity, False
        source = {
            'source_mailbox_connection_id': str(connection.pk),
            'source_mailbox_address': connection.mailbox_address,
            'source_message_id': message['id'],
            'sender_email': message.get('sender_email') or '',
            'received_at': message.get('received_at'),
        }
        opportunity = serializer.save(
            owner=user, client=client, opportunity_source='client_email',
            next_action='Qualify email-originated opportunity',
            next_action_date=timezone.now().date(), custom_fields=source,
        )
        OpportunityAuditEvent.objects.create(
            opportunity=opportunity, actor=user,
            event_type='opportunity_created_from_email', to_stage=opportunity.stage,
            data={
                **source, 'mailbox_source_hash': source_hash,
                'reviewed_payload_hash': payload_hash, 'source_content_hash': review['source'],
            },
        )
        return opportunity, True
