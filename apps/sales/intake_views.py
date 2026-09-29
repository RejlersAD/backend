"""Authenticated machine-to-machine intake for Power Automate email events."""

import re
import secrets
from collections.abc import Mapping
from html import unescape

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.utils.html import strip_tags
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, serializers, status, viewsets
from rest_framework.decorators import (
    action, api_view,
    authentication_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle

from apps.rbac.permissions import HasModuleAccess
from apps.rbac.action_policy import module_action_allowed

from .email_permissions import (
    require_email_opportunity_access, visible_email_clients, visible_email_intakes,
    visible_email_opportunities,
)
from .email_classification_review import classification_review_evidence, require_classification_review
from .mailbox_opportunities import EmailReviewConflict

from .models import Client, Contact, OpportunityAuditEvent, SalesEmailIntake
from .serializers import (
    ClientCreateSerializer, DealCreateSerializer, DealDetailSerializer,
    SalesEmailIntakeSerializer as SalesEmailIntakeDetailSerializer,
)


class SalesEmailIntakeThrottle(SimpleRateThrottle):
    scope = 'sales_email_intake'

    def get_cache_key(self, request, view):
        return self.cache_format % {
            'scope': self.scope,
            'ident': self.get_ident(request),
        }


class SalesEmailWebhookSerializer(serializers.ModelSerializer):
    body = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
    )

    class Meta:
        model = SalesEmailIntake
        fields = [
            'source_message_id', 'internet_message_id', 'subject',
            'sender_name', 'sender_email', 'received_at', 'body_preview',
            'body', 'has_attachments', 'importance',
        ]
        extra_kwargs = {
            'source_message_id': {'validators': []},
        }

    def to_internal_value(self, data):
        if isinstance(data, Mapping) and set(data) - set(self.fields):
            raise serializers.ValidationError({'detail': 'Only legacy email source fields may be submitted.'})
        return super().to_internal_value(data)

    def validate_source_message_id(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('Outlook message ID is required.')
        return value

    def validate(self, attrs):
        full_body = attrs.pop('body', '')
        if full_body:
            body_with_breaks = re.sub(
                r'<\s*(?:br\s*/?|/p|/div|/li)\s*>',
                '\n',
                full_body,
                flags=re.IGNORECASE,
            )
            plain_body = unescape(strip_tags(body_with_breaks))
            attrs['body_preview'] = re.sub(r'\n\s*\n\s*\n+', '\n\n', plain_body).strip()
        return attrs


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

    serializer = SalesEmailWebhookSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    validated = serializer.validated_data
    source_message_id = validated.pop('source_message_id')
    with transaction.atomic():
        intake, created = SalesEmailIntake.objects.get_or_create(
            mailbox_connection=None,
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


class SalesEmailIntakeViewSet(viewsets.ReadOnlyModelViewSet):
    """Protected review queue for email-originated Sales work."""

    queryset = SalesEmailIntake.objects.select_related(
        'opportunity', 'reviewed_by', 'duplicate_of',
    )
    serializer_class = SalesEmailIntakeDetailSerializer
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'sales'
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'importance', 'has_attachments']
    search_fields = [
        'subject', 'sender_name', 'sender_email', 'source_message_id',
        'internet_message_id',
    ]
    ordering_fields = ['received_at', 'created_at', 'status', 'importance']
    ordering = ['-received_at']

    def get_queryset(self):
        return super().get_queryset().filter(
            pk__in=visible_email_intakes(self.request.user).values('pk'),
        )

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'private, no-store, max-age=0'
        response['Pragma'] = 'no-cache'
        patch_vary_headers(response, ('Authorization', 'Cookie'))
        return response

    def _save_resolution(self, intake, *, status_value, note='', duplicate_of=None):
        intake.status = status_value
        intake.reviewed_by = self.request.user
        intake.reviewed_at = timezone.now()
        intake.resolution_note = note.strip()
        intake.duplicate_of = duplicate_of
        intake.save(update_fields=[
            'status', 'reviewed_by', 'reviewed_at', 'resolution_note',
            'duplicate_of', 'updated_at',
        ])
        return Response(self.get_serializer(intake).data)

    @action(detail=True, methods=['post'], url_path='start-review')
    def start_review(self, request, pk=None):
        intake = self.get_object()
        if intake.status in {'converted', 'rejected', 'duplicate'}:
            raise serializers.ValidationError({
                'status': 'A resolved intake cannot be reopened for review.',
            })
        return self._save_resolution(intake, status_value='under_review')

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        note = str(request.data.get('reason', '') or '').strip()
        if not note:
            raise serializers.ValidationError({'reason': 'A rejection reason is required.'})
        intake = self.get_object()
        if intake.status == 'converted':
            raise serializers.ValidationError({
                'status': 'A converted intake cannot be rejected.',
            })
        return self._save_resolution(intake, status_value='rejected', note=note)

    @action(detail=True, methods=['post'], url_path='mark-duplicate')
    def mark_duplicate(self, request, pk=None):
        intake = self.get_object()
        if intake.status == 'converted':
            raise serializers.ValidationError({
                'status': 'A converted intake cannot be marked as duplicate.',
            })
        duplicate_of = None
        duplicate_id = request.data.get('duplicate_of')
        candidates = self.get_queryset().filter(mailbox_connection_id=intake.mailbox_connection_id)
        if duplicate_id:
            try:
                duplicate_id = serializers.UUIDField().run_validation(duplicate_id)
            except serializers.ValidationError:
                raise serializers.ValidationError({'duplicate_of': 'The original intake could not be found.'}) from None
            duplicate_of = candidates.filter(pk=duplicate_id).first()
            if not duplicate_of:
                raise serializers.ValidationError({
                    'duplicate_of': 'The original intake could not be found.',
                })
            if duplicate_of.pk == intake.pk:
                raise serializers.ValidationError({
                    'duplicate_of': 'An intake cannot duplicate itself.',
                })
        else:
            candidates = candidates.exclude(pk=intake.pk)
            same_sender_and_subject = Q(
                sender_email__iexact=intake.sender_email,
                subject__iexact=intake.subject,
            )
            if intake.internet_message_id:
                candidates = candidates.filter(
                    Q(internet_message_id=intake.internet_message_id) |
                    same_sender_and_subject,
                )
            else:
                candidates = candidates.filter(same_sender_and_subject)
            duplicate_of = candidates.order_by('received_at').first()
        note = str(request.data.get('reason', '') or '').strip()
        return self._save_resolution(
            intake,
            status_value='duplicate',
            note=note or 'Marked as a duplicate during Sales review.',
            duplicate_of=duplicate_of,
        )

    @action(detail=True, methods=['post'], url_path='convert-to-opportunity')
    def convert_to_opportunity(self, request, pk=None):
        require_email_opportunity_access(request.user)
        permitted_intake = self.get_object()
        classification_code = require_classification_review(request.data)
        with transaction.atomic():
            # Preserve the scoped queryset, but lock only the intake row. The
            # list/detail joins include nullable relations that PostgreSQL
            # cannot lock through an outer join.
            intake = self.get_queryset().select_related(None).select_for_update().get(pk=permitted_intake.pk)
            if intake.opportunity_id:
                if not visible_email_opportunities(request.user).filter(pk=intake.opportunity_id).exists():
                    raise PermissionDenied('The linked opportunity is not available to you.')
                previous = OpportunityAuditEvent.objects.filter(
                    opportunity_id=intake.opportunity_id,
                    event_type='opportunity_created_from_email',
                    data__source_email_intake_id=str(intake.pk),
                ).first()
                previous_review = previous.data.get('reviewed_classification') if previous else None
                if previous_review and previous_review.get('code') != classification_code:
                    raise EmailReviewConflict(
                        'An opportunity already exists for this email with a different reviewed classification.',
                        code='email_already_converted',
                    )
                return Response({
                    'intake': self.get_serializer(intake).data,
                    'opportunity': DealDetailSerializer(intake.opportunity).data,
                    'created': False,
                })
            if intake.status in {'rejected', 'duplicate'}:
                raise serializers.ValidationError({
                    'status': 'Rejected or duplicate intake cannot be converted.',
                })

            client = None
            client_id = request.data.get('client')
            if client_id:
                try:
                    client_id = serializers.UUIDField().run_validation(client_id)
                except serializers.ValidationError:
                    raise serializers.ValidationError({'client': 'Select an accessible client.'}) from None
                client = visible_email_clients(request.user).filter(pk=client_id).first()
                if not client:
                    raise serializers.ValidationError({
                        'client': 'The selected client could not be found.',
                    })
            else:
                if not module_action_allowed(request.user, 'sales_clients', 'create'):
                    raise PermissionDenied('You do not have access to create clients.')
                new_client = request.data.get('new_client') or {}
                company_name = str(new_client.get('company_name', '')).strip()
                if not company_name:
                    raise serializers.ValidationError({
                        'client': 'Select a client or provide a new client name.',
                    })
                client = visible_email_clients(request.user).filter(
                    company_name__iexact=company_name,
                ).first()
                if not client:
                    client_serializer = ClientCreateSerializer(data={
                        'company_name': company_name,
                        'legal_name': company_name,
                        'industry_type': new_client.get('industry_type') or 'other',
                        'email': new_client.get('email') or '',
                        'phone': new_client.get('phone') or '',
                        'website': new_client.get('website') or '',
                        'country': new_client.get('country') or '',
                        'status': 'prospect',
                        'notes': f'Created from Sales email intake {intake.id}.',
                    })
                    client_serializer.is_valid(raise_exception=True)
                    client = client_serializer.save(account_manager=request.user)

                    contact_email = str(new_client.get('contact_email') or '').strip()
                    if contact_email:
                        name_parts = str(new_client.get('contact_name') or '').strip().split()
                        Contact.objects.create(
                            client=client,
                            first_name=name_parts[0] if name_parts else 'Email',
                            last_name=' '.join(name_parts[1:]) if len(name_parts) > 1 else 'Contact',
                            email=contact_email,
                            phone=new_client.get('phone') or '',
                            role_type='procurement',
                            is_primary=True,
                        )

            payload = {
                'deal_name': request.data.get('deal_name') or intake.subject[:300],
                'client': str(client.id),
                'estimated_value': request.data.get('estimated_value'),
                'currency': request.data.get('currency') or 'AED',
                'expected_close_date': request.data.get('expected_close_date'),
                'submission_due_date': request.data.get('submission_due_date'),
                'scope_type': request.data.get('scope_type') or 'other',
                'location': request.data.get('location') or '',
                'description': request.data.get('description') or intake.body_preview,
                'client_reference': request.data.get('client_reference') or '',
                'opportunity_source': 'client_email',
                'next_action': 'Qualify email-originated opportunity',
                'next_action_date': timezone.now().date(),
                'custom_fields': {
                    'source_email_intake_id': str(intake.id),
                    'source_message_id': intake.source_message_id,
                    'internet_message_id': intake.internet_message_id,
                    'sender_email': intake.sender_email,
                    'received_at': intake.received_at.isoformat(),
                },
            }
            opportunity_serializer = DealCreateSerializer(data=payload)
            opportunity_serializer.is_valid(raise_exception=True)
            opportunity = opportunity_serializer.save(owner=request.user)
            OpportunityAuditEvent.objects.create(
                opportunity=opportunity,
                actor=request.user,
                event_type='opportunity_created_from_email',
                to_stage=opportunity.stage,
                data={
                    'source_email_intake_id': str(intake.id),
                    'source_message_id': intake.source_message_id,
                    'internet_message_id': intake.internet_message_id,
                    'sender_email': intake.sender_email,
                    'received_at': intake.received_at.isoformat(),
                    'reviewed_classification': classification_review_evidence(classification_code, request.user),
                },
            )

            intake.opportunity = opportunity
            intake.status = 'converted'
            intake.reviewed_by = request.user
            intake.reviewed_at = timezone.now()
            intake.resolution_note = str(
                request.data.get('resolution_note', '') or ''
            ).strip()
            intake.save(update_fields=[
                'opportunity', 'status', 'reviewed_by', 'reviewed_at',
                'resolution_note', 'updated_at',
            ])

        return Response({
            'intake': self.get_serializer(intake).data,
            'opportunity': DealDetailSerializer(opportunity).data,
            'created': True,
        }, status=status.HTTP_201_CREATED)
