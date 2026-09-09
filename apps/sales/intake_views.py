"""Authenticated machine-to-machine intake for Power Automate email events."""

import secrets

from django.conf import settings
from django.db import transaction
from rest_framework import serializers, status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle

from .models import SalesEmailIntake


class SalesEmailIntakeThrottle(SimpleRateThrottle):
    scope = 'sales_email_intake'

    def get_cache_key(self, request, view):
        return self.cache_format % {
            'scope': self.scope,
            'ident': self.get_ident(request),
        }


class SalesEmailIntakeSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalesEmailIntake
        fields = [
            'source_message_id', 'internet_message_id', 'subject',
            'sender_name', 'sender_email', 'received_at', 'body_preview',
            'has_attachments', 'importance',
        ]
        extra_kwargs = {
            'source_message_id': {'validators': []},
        }

    def validate_source_message_id(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('Outlook message ID is required.')
        return value


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([SalesEmailIntakeThrottle])
def sales_email_intake(request):
    expected_key = str(
        getattr(settings, 'SALES_EMAIL_INTAKE_WEBHOOK_KEY', '') or ''
    )
    supplied_key = request.headers.get('X-RADAI-Webhook-Key', '')
    if not expected_key:
        return Response(
            {'detail': 'Sales email intake is not configured.'},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if not secrets.compare_digest(supplied_key, expected_key):
        return Response(
            {'detail': 'Invalid webhook credential.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    serializer = SalesEmailIntakeSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    validated = serializer.validated_data
    source_message_id = validated.pop('source_message_id')
    with transaction.atomic():
        intake, created = SalesEmailIntake.objects.get_or_create(
            source_message_id=source_message_id,
            defaults=validated,
        )
    return Response(
        {
            'accepted': True,
            'duplicate': not created,
            'intake_id': str(intake.id),
            'status': intake.status,
        },
        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )
