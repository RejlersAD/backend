"""Timestamp preconditions; required once a rejected review has been revised."""

from rest_framework import serializers
from rest_framework.exceptions import APIException


class RequisitionTimestampField(serializers.DateTimeField):
    def get_value(self, dictionary):
        # An explicitly blank multipart precondition must not become an
        # omitted optional field through DRF's HTML form normalization.
        return dictionary.get(self.field_name, serializers.empty)


class StaleRequisition(APIException):
    status_code = 409
    default_code = 'stale_requisition'

    def __init__(self):
        super().__init__({
            'code': self.default_code,
            'error': (
                'This purchase recommendation changed since you opened it. '
                'Reload the latest version before continuing.'
            ),
        })


def check_requisition_precondition(requisition, expected_updated_at=serializers.empty):
    """Compare under the caller's row lock before any mutation or delivery.

    Older integrations may omit the token only before the first reopened round.
    It covers changes that advance updated_at, not an immutable business revision.
    """
    if expected_updated_at is serializers.empty:
        if (getattr(requisition, 'price_remarks_data', None) or {}).get('approval_revision_history'):
            raise serializers.ValidationError({
                'expected_updated_at': 'Reload this revised requisition before changing it.',
            })
        return
    try:
        expected_updated_at = RequisitionTimestampField().run_validation(expected_updated_at)
    except serializers.ValidationError as error:
        raise serializers.ValidationError({'expected_updated_at': error.detail}) from error
    if requisition.updated_at != expected_updated_at:
        raise StaleRequisition()
