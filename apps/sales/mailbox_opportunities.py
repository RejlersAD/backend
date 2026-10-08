"""Explicit reviewed creation from one scoped live email; no mailbox writes."""

import hashlib
import json
from zoneinfo import ZoneInfo

from django.core import signing
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import serializers
from rest_framework.exceptions import APIException, PermissionDenied

from .email_permissions import (
    require_email_opportunity_access, visible_email_clients, visible_email_opportunities,
)
from .email_classification_review import classification_review_evidence, require_classification_review
from .email_client_resolution import (
    email_customer_intent, require_reviewed_customer_name, resolve_email_customer,
)
from .email_customer_matching import normalize_customer_name
from .email_opportunity_evidence import (
    prevent_duplicate_tender, reviewed_analysis_snapshot, tender_identity,
)
from .microsoft_graph import SalesMicrosoftGraphService
from .models import OpportunityAuditEvent
from .opportunity_registration import visible_opportunity_owners
from .serializers import DealCreateSerializer


REVIEW_SALT = 'sales-mailbox-opportunity-review-v1'
REVIEW_MAX_AGE = 1800
REVIEW_FIELDS = (
    'deal_name', 'client', 'client_reference', 'estimated_value', 'currency',
    'expected_close_date', 'submission_due_date', 'scope_type', 'description',
    'opportunity_type', 'open_date', 'owner',
)
SOURCE_FIELDS = (
    'id', 'subject', 'sender_name', 'sender_email', 'received_at', 'sent_at',
    'body_text', 'body_content', 'to_recipients', 'cc_recipients', 'has_attachments',
    'analysis_source_hash',
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


def _source_digest(message, *, version=2):
    fields = SOURCE_FIELDS if version == 2 else tuple(
        field for field in SOURCE_FIELDS if field != 'analysis_source_hash'
    )
    return _digest({field: message.get(field) for field in fields})


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
        'analysis': reviewed_analysis_snapshot(message.get('extracted_information')),
    }, salt=REVIEW_SALT, compress=True)


def _read_review_token(token, connection, user, message_id):
    if not isinstance(token, str) or not token or len(token) > 48_000:
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


def _reviewed_serializer(data, user, *, legacy_scope=False):
    if not isinstance(data, dict) or set(data) - {
        *REVIEW_FIELDS, 'new_client', 'message_id', 'source_token', 'classification_code', 'classification_confirmed',
    }:
        raise serializers.ValidationError({'detail': 'Only reviewed opportunity fields may be submitted.'})
    classification_code = require_classification_review(data)
    required = ('deal_name',)
    errors = {key: ['This field is required.'] for key in required if data.get(key) in (None, '')}
    if errors:
        raise serializers.ValidationError(errors)
    intent = email_customer_intent(data)
    client = None
    if 'client' in intent:
        client = visible_email_clients(user).filter(pk=intent['client']).first()
        if client is None:
            raise serializers.ValidationError({'client': 'Select an accessible client.'})
    payload = {key: data[key] for key in REVIEW_FIELDS if key in data}
    payload.pop('client', None)
    if client is not None:
        payload['client'] = str(client.pk)
    payload.update({
        'client_reference': data.get('client_reference') or '',
        'scope_type': data.get('scope_type') or '',
        'description': data.get('description') or '',
        'submission_due_date': data.get('submission_due_date') or None,
    })
    serializer = DealCreateSerializer(data=payload, context={'actor': user})
    if client is None:
        # Validate all commercial inputs before source reads or business writes.
        # The atomic resolver supplies the canonical FK immediately before save.
        serializer.fields['client'].required = False
    serializer.is_valid(raise_exception=True)
    normalized = dict(serializer.validated_data)
    if normalized.get('owner') is not None:
        normalized['owner'] = str(normalized['owner'].pk)
    normalized['client'] = str(client.pk) if client is not None else {
        'reviewed_name': normalize_customer_name(intent['new_client']['company_name']),
    }
    historical = dict(normalized)
    if not data.get('scope_type'):
        historical['scope_type'] = 'other'
    legacy_payload_hash = _digest(historical)
    if legacy_scope:
        normalized = historical
    normalized['classification_code'] = classification_code
    normalized['classification_confirmed'] = True
    return serializer, _digest(normalized), legacy_payload_hash, classification_code


def email_open_date(received_at):
    """Use the authoritative received timestamp's Gulf calendar date."""
    if isinstance(received_at, str):
        try:
            received_at = parse_datetime(received_at)
        except (ValueError, TypeError):
            return None
    if received_at is None:
        return None
    if timezone.is_aware(received_at):
        return received_at.astimezone(ZoneInfo('Asia/Dubai')).date()
    return received_at.date()


def convert_mailbox_message(*, connection, connection_queryset, user, data):
    """Deduplicate within this configured connection using a locked audit lookup."""
    require_email_opportunity_access(user)
    serializer, payload_hash, legacy_payload_hash, classification_code = _reviewed_serializer(data, user)
    message_id = data.get('message_id')
    review = _read_review_token(data.get('source_token'), connection, user, message_id)
    analysis_snapshot = reviewed_analysis_snapshot(review.get('analysis'))
    intent = email_customer_intent(data)
    require_reviewed_customer_name(intent, analysis_snapshot)
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
        previous = OpportunityAuditEvent.objects.filter(
            event_type='opportunity_created_from_email',
            data__mailbox_source_hash=source_hash,
        ).first()
        if previous:
            opportunity = visible_email_opportunities(user).filter(pk=previous.opportunity_id).first()
            if opportunity is None:
                raise PermissionDenied('This email already has an opportunity you cannot access.')
            if not visible_email_clients(user).filter(pk=opportunity.client_id).exists():
                raise serializers.ValidationError({'client': 'Select an accessible client.'})
            legacy_same_payload = (
                previous.data.get('reviewed_payload_hash_version', 1) == 1
                and 'reviewed_classification' not in previous.data
                and previous.data.get('reviewed_payload_hash') == legacy_payload_hash
            )
            legacy_same_scope = (
                previous.data.get('reviewed_payload_hash_version', 1) == 2
                and not data.get('scope_type')
                and previous.data.get('reviewed_payload_hash') == _reviewed_serializer(data, user, legacy_scope=True)[1]
            )
            if previous.data.get('reviewed_payload_hash') != payload_hash and not legacy_same_payload and not legacy_same_scope:
                raise EmailReviewConflict(
                    'An opportunity already exists for this email with different reviewed fields.',
                    code='email_already_converted',
                )
            previous_source = previous.data.get('source_content_hash')
            legacy_same_selected = (
                previous.data.get('source_hash_version', 1) == 1
                and previous_source == _source_digest(message, version=1)
            )
            if previous_source != review['source'] and not legacy_same_selected:
                raise EmailReviewConflict(
                    'The email or conversation evidence differs from the recorded opportunity.', code='email_already_converted',
                )
            return opportunity, False
        client, customer_resolution = resolve_email_customer(user, intent)
        prevent_duplicate_tender(
            client=client, reviewed_reference=serializer.validated_data.get('client_reference'),
            snapshot=analysis_snapshot,
        )
        source = {
            'source_mailbox_connection_id': str(connection.pk),
            'source_mailbox_address': connection.mailbox_address,
            'source_message_id': message['id'],
            'sender_email': message.get('sender_email') or '',
            'received_at': message.get('received_at'),
        }
        identity = tender_identity(analysis_snapshot)
        if identity:
            source['email_tender_identity'] = identity
        resolved_owner = serializer.validated_data.get('owner') or user
        if not visible_opportunity_owners(user).filter(pk=resolved_owner.pk).exists():
            raise serializers.ValidationError({'owner': 'Choose an active owner from the Sales Department.'})
        opportunity = serializer.save(
            owner=resolved_owner,
            open_date=serializer.validated_data.get('open_date') or email_open_date(message.get('received_at')),
            client=client, opportunity_source='client_email',
            next_action='Qualify email-originated opportunity',
            next_action_date=timezone.now().date(), custom_fields=source,
        )
        OpportunityAuditEvent.objects.create(
            opportunity=opportunity, actor=user,
            event_type='opportunity_created_from_email', to_stage=opportunity.stage,
            data={
                **source, 'mailbox_source_hash': source_hash,
                'reviewed_payload_hash': payload_hash, 'source_content_hash': review['source'],
                'source_hash_version': 2, 'reviewed_payload_hash_version': 3,
                'reviewed_classification': classification_review_evidence(classification_code, user),
                'reviewed_email_analysis': analysis_snapshot,
                'reviewed_customer_resolution': customer_resolution,
            },
        )
        return opportunity, True
