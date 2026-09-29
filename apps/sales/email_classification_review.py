"""Explicit human classification review for email opportunity commands."""

from collections.abc import Mapping

from django.utils import timezone
from rest_framework import serializers

from .email_classification import LABELS


# These are manual review choices, not new automatic classifier rules.
MANUAL_NON_OPPORTUNITY_LABELS = {
    'promotional_event': 'Promotional / event',
    'system_notification': 'System notification',
}
REVIEW_LABELS = {**LABELS, **MANUAL_NON_OPPORTUNITY_LABELS}


def require_classification_review(data):
    """Validate the attestation without inferring authority from AI suggestions."""
    if not isinstance(data, Mapping) or data.get('classification_confirmed') is not True:
        raise serializers.ValidationError({
            'classification_confirmed': ['Confirm the email classification before creating an opportunity.'],
        })
    code = data.get('classification_code')
    if not isinstance(code, str) or code not in REVIEW_LABELS:
        raise serializers.ValidationError({
            'classification_code': ['Select a valid email classification.'],
        })
    if code in MANUAL_NON_OPPORTUNITY_LABELS:
        raise serializers.ValidationError({
            'classification_code': ['This email classification does not describe an opportunity.'],
        })
    return code


def classification_review_evidence(code, user):
    """Call inside the existing conversion transaction, only for a new record."""
    return {
        'version': 1, 'code': code, 'label': REVIEW_LABELS[code],
        'confirmed_by': str(user.pk), 'confirmed_at': timezone.now().isoformat(),
    }
