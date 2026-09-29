"""Bind reviewed email proposals to their source without granting AI authority."""

import hashlib
import json
import re

from django.core import signing
from django.core.serializers.json import DjangoJSONEncoder
from rest_framework import serializers

from .models import Deal, OpportunityAuditEvent


MAX_SNAPSHOT_BYTES = 24_000
SAVED_REVIEW_SALT = 'sales-saved-email-opportunity-review-v1'
REVIEW_MAX_AGE = 1800
SNAPSHOT_FIELDS = (
    'detection_version', 'title', 'customer_name', 'organization_name', 'customer_domain',
    'classification', 'request_type_code', 'request_type', 'tender_reference',
    'procurement_reference', 'pr_reference', 'purchase_requisition_reference', 'package_description',
    'estimated_value', 'currency', 'expected_award_date', 'scope_type', 'project_name',
    'source_portal', 'scope_summary', 'agreement_reference', 'correspondence_reference', 'action_deadlines',
    'due_date', 'due_date_text', 'deadline_at',
    'deadline_time', 'deadline_timezone', 'deadline_utc', 'deadline', 'tender_status',
    'evidence', 'field_sources', 'warnings', 'ai_review',
)


def source_digest(value):
    return hashlib.sha256(json.dumps(
        value, cls=DjangoJSONEncoder, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')).hexdigest()


def reviewed_analysis_snapshot(information):
    """Take only server-created analysis, never browser-supplied evidence."""
    if not isinstance(information, dict):
        return {}
    snapshot = {key: information[key] for key in SNAPSHOT_FIELDS if key in information}
    try:
        encoded = json.dumps(snapshot, cls=DjangoJSONEncoder, separators=(',', ':'))
    except (TypeError, ValueError):
        raise serializers.ValidationError({
            'source_token': 'The email analysis could not be retained for review. Reload the email.',
        }) from None
    # Do not silently truncate evidence into a different interpretation.
    if len(encoded.encode('utf-8')) > MAX_SNAPSHOT_BYTES:
        raise serializers.ValidationError({
            'source_token': 'The email analysis exceeds the review evidence limit. Narrow the source review before continuing.',
        })
    return snapshot


def saved_email_review_token(intake, user, source_hash, information):
    return signing.dumps({
        'actor': str(user.pk), 'intake': str(intake.pk), 'source': source_hash,
        'analysis': reviewed_analysis_snapshot(information),
    }, salt=SAVED_REVIEW_SALT, compress=True)


def read_saved_email_review(token, intake, user, source_hash):
    """Legacy manual clients may omit a token, but gain no reviewed AI evidence."""
    if token is None:
        return {}
    if not isinstance(token, str) or not token or len(token) > 48_000:
        raise serializers.ValidationError({'source_token': 'Reload this email before creating an opportunity.'})
    from .mailbox_opportunities import EmailReviewConflict, EmailReviewExpired
    try:
        review = signing.loads(token, salt=SAVED_REVIEW_SALT, max_age=REVIEW_MAX_AGE)
    except signing.SignatureExpired:
        raise EmailReviewExpired() from None
    except (signing.BadSignature, ValueError, TypeError):
        raise serializers.ValidationError({'source_token': 'The email review is invalid. Reload the email.'}) from None
    if (not isinstance(review, dict) or review.get('actor') != str(user.pk)
            or review.get('intake') != str(intake.pk) or review.get('source') != source_hash):
        raise EmailReviewConflict()
    return reviewed_analysis_snapshot(review.get('analysis'))


def prevent_duplicate_tender(*, client, reviewed_reference, snapshot):
    """Caller holds the canonical client lock in the conversion transaction.

    A source-backed exact tender reference within the same canonical client is
    a duplicate warning, never authority to link or modify an existing deal.
    Existing unrelated manual references cannot become inferred tender IDs.
    """
    reference = snapshot.get('tender_reference') if isinstance(snapshot, dict) else None
    if (not isinstance(reference, str) or not reference.strip() or len(reference) > 120
            or reference.strip().casefold() != str(reviewed_reference or '').strip().casefold()):
        return
    evidence = snapshot.get('evidence', {})
    if not isinstance(evidence, dict) or not evidence.get('tender_reference'):
        return
    reference = reference.strip()
    if not re.search(r'\d', reference):
        return
    portal = snapshot.get('source_portal')
    portal = portal.strip().casefold() if isinstance(portal, str) else ''
    different_portal_ids = set()
    duplicate = False
    # Immutable source audit remains authoritative after ordinary Deal edits.
    # In particular, changing mutable custom_fields cannot invent a different
    # portal to bypass the duplicate warning for a reviewed source identity.
    events = OpportunityAuditEvent.objects.filter(
        opportunity__client=client, event_type='opportunity_created_from_email',
        data__reviewed_email_analysis__tender_reference__iexact=reference,
    ).values('opportunity_id', 'data')
    for event in events.iterator(chunk_size=100):
        previous = event['data'].get('reviewed_email_analysis', {})
        previous_portal = previous.get('source_portal')
        previous_portal = previous_portal.strip().casefold() if isinstance(previous_portal, str) else ''
        if portal and previous_portal and portal != previous_portal:
            different_portal_ids.add(event['opportunity_id'])
            continue
        duplicate = True
        break
    if not duplicate:
        # Historical/manual opportunities with the exact reviewed reference are
        # also potential matches. Proven distinct portals may reuse a code.
        duplicate = Deal.objects.filter(client=client, client_reference__iexact=reference).exclude(
            pk__in=different_portal_ids,
        ).exists()
    if not duplicate:
        return
    from .mailbox_opportunities import EmailReviewConflict
    raise EmailReviewConflict(
        'An opportunity may already exist for this client and tender reference. '
        'Review the existing opportunity before creating another.',
        code='email_tender_already_exists',
    )


def tender_identity(snapshot):
    if not isinstance(snapshot, dict) or not snapshot.get('tender_reference'):
        return {}
    return {key: snapshot[key] for key in ('tender_reference', 'source_portal') if snapshot.get(key)}
