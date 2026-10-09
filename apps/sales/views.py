"""
Sales Management Views
DRF ViewSets with AI-powered actions
"""

from rest_framework import viewsets, status, filters, serializers
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError

# RBAC - Module-level access control (soft-coded)
from apps.rbac.permissions import HasModuleAccess, IsAdmin
from apps.rbac.action_policy import module_action_allowed
from django.db import IntegrityError, transaction
from django.contrib.auth import get_user_model
from django.db.models.deletion import ProtectedError
from django.db.models import Q, Count, Sum, Avg, F
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.shortcuts import redirect
from django.http import Http404
from django_filters.rest_framework import DjangoFilterBackend
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode
import secrets

from .models import (
    Client, Contact, Deal, FrameworkAgreement, ProjectHandover, Quote,
    SalesActivity, SalesForecast, SalesMailboxConnection, OpportunityAuditEvent,
)
from .serializers import (
    ClientListSerializer, ClientDetailSerializer, ClientCreateSerializer,
    ContactSerializer, DealListSerializer, DealDetailSerializer, DealCreateSerializer,
    QuoteListSerializer, QuoteDetailSerializer, SalesActivityListSerializer,
    SalesActivityDetailSerializer, SalesForecastSerializer, SalesDashboardSerializer,
    AIInsightSerializer, FrameworkAgreementSerializer, ProjectHandoverSerializer,
    SalesMailboxConnectionSerializer, SalesEmailIntakeSerializer,
    SalesLetterSerializer, SalesLetterCreateSerializer, SalesLetterRegenerateSerializer,
)
from .ai_service import SalesAIService
from .jwt_query_auth import QueryParamJWTAuthentication
from .microsoft_graph import SalesMailboxReadError, SalesMicrosoftGraphService
from .email_permissions import (
    can_create_email_opportunity, require_email_opportunity_access,
    visible_mailbox_connections,
)
from .mailbox_capture import capture_mailbox_message, EmailCaptureConflict
from .mailbox_opportunities import convert_mailbox_message, email_review_token
from apps.rbac.data_visibility_mixin import TeamCollaborationMixin
from apps.rbac.data_visibility_config import build_visibility_filter
from apps.rbac.action_policy import module_action_allowed
from .workflow import (
    _audit, close_opportunity, convert_to_project, decide_award, enter_negotiation, record_bid_decision,
    record_ceo_decision, prepare_letter,
    decide_handover, submit_award, submit_handover_for_acceptance,
    submit_qualification,
)
import logging

logger = logging.getLogger(__name__)


def _opportunity_total(queryset, field):
    """Do not turn an incomplete opportunity pipeline into a zero-valued one."""
    if queryset.filter(Q(**{f'{field}__isnull': True}) | Q(currency='')).exists():
        return None
    return float(queryset.aggregate(total=Sum(field))['total'] or 0)


class SalesMailboxConnectionViewSet(viewsets.ModelViewSet):
    """User-owned delegated Outlook connections and governed admin connections."""

    queryset = SalesMailboxConnection.objects.all()
    serializer_class = SalesMailboxConnectionSerializer
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'sales'

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        if self.action in {'messages', 'message', 'review_assistant', 'convert_to_opportunity', 'capture_message', 'configure_sync', 'list', 'retrieve', 'create', 'update', 'partial_update', 'test_connection'}:
            response['Cache-Control'] = 'private, no-store, max-age=0'
            response['Pragma'] = 'no-cache'
            patch_vary_headers(response, ('Authorization', 'Cookie'))
        return response

    @action(detail=True, methods=['get'])
    def messages(self, request, pk=None):
        connection = self.get_object()
        if (
            set(request.query_params) - {'cursor', 'search'}
            or len(request.query_params.getlist('cursor')) > 1
            or len(request.query_params.getlist('search')) > 1
        ):
            return Response({'detail': 'The email page request is invalid.'}, status=400)
        try:
            result = SalesMicrosoftGraphService(connection).list_messages(
                user_id=request.user.pk, cursor=request.query_params.get('cursor'),
                search=request.query_params.get('search'),
            )
        except SalesMailboxReadError as exc:
            return Response({'detail': str(exc)}, status=exc.status_code)
        except Exception:
            # Provider/configuration failures must not expose payloads or secrets.
            return Response({'detail': 'The emails could not be loaded from Microsoft.'}, status=502)
        return Response(result)

    @action(detail=True, methods=['get'])
    def message(self, request, pk=None):
        connection = self.get_object()
        if (
            set(request.query_params) - {'message_id'}
            or len(request.query_params.getlist('message_id')) != 1
        ):
            return Response({'detail': 'Select a valid email to view.'}, status=400)
        try:
            result = SalesMicrosoftGraphService(connection).get_message(
                request.query_params.get('message_id'),
                allow_ai=True,
                ai_scope_key=f'live:{request.user.pk}:{connection.pk}:{connection.tenant_id}:{connection.mailbox_address}',
            )
        except SalesMailboxReadError as exc:
            return Response({'detail': str(exc)}, status=exc.status_code)
        except Exception:
            return Response({'detail': 'The email could not be loaded from Microsoft.'}, status=502)
        result['can_create_opportunity'] = can_create_email_opportunity(request.user)
        result['can_create_client'] = bool(
            result['can_create_opportunity'] and module_action_allowed(request.user, 'sales_clients', 'create')
        )
        result['source_token'] = email_review_token(connection, request.user, result)
        from .email_customer_matching import enrich_customer_match
        result['extracted_information'] = enrich_customer_match(
            result.get('extracted_information'), request=request,
        )
        return Response(result)

    @action(detail=True, methods=['post'], url_path='review-assistant')
    def review_assistant(self, request, pk=None):
        from .email_review_assistant import (
            assistant_request, require_assistant_configuration, require_assistant_read, review_email_assistant,
        )
        require_assistant_read(request.user)
        connection = self.get_object()
        query = assistant_request(request.data, live=True, query_params=request.query_params)
        require_assistant_configuration()
        context = {}
        try:
            SalesMicrosoftGraphService(connection).get_message(request.data['message_id'], review_context=context)
        except SalesMailboxReadError as exc:
            error = {'detail': str(exc)}
            if 500 <= exc.status_code < 600:
                error['reason'] = 'mailbox_unavailable'
            return Response(error, status=exc.status_code)
        except Exception:
            return Response({'detail': 'The email could not be loaded from Microsoft.',
                             'reason': 'mailbox_unavailable'}, status=502)
        return Response(review_email_assistant(
            context.get('assistant_sources'), query,
            scope_key=f'live:{request.user.pk}:{connection.pk}:{connection.tenant_id}:{connection.mailbox_address}:{request.data["message_id"]}',
        ))

    @action(detail=True, methods=['post'], url_path='convert-to-opportunity')
    def convert_to_opportunity(self, request, pk=None):
        require_email_opportunity_access(request.user)
        connection = self.get_object()
        if request.query_params:
            return Response({'detail': 'The opportunity request is invalid.'}, status=400)
        try:
            opportunity, created = convert_mailbox_message(
                connection=connection, connection_queryset=self.get_queryset,
                user=request.user, data=request.data,
            )
        except SalesMailboxReadError as exc:
            return Response({'detail': str(exc)}, status=exc.status_code)
        except (APIException, Http404):
            raise
        except Exception:
            return Response({'detail': 'The opportunity could not be created. Your reviewed fields have not been changed.'}, status=503)
        return Response({
            'opportunity': DealDetailSerializer(opportunity, context={'request': request}).data,
            'created': created,
        }, status=201 if created else 200)

    @action(detail=True, methods=['post'], url_path='capture-message')
    def capture_message(self, request, pk=None):
        connection = self.get_object()
        if request.query_params:
            return Response({'detail': 'The email capture request is invalid.'}, status=400)
        try:
            intake, created = capture_mailbox_message(
                connection=connection, user=request.user, data=request.data,
            )
        except SalesMailboxReadError as exc:
            return Response({'detail': str(exc)}, status=exc.status_code)
        except (APIException, Http404):
            raise
        except Exception:
            # Never log or reflect Graph bodies, mailbox credentials or IDs.
            return Response({'detail': 'The email could not be saved. Try again later.'}, status=503)
        return Response({
            'intake': SalesEmailIntakeSerializer(intake, context={'request': request}).data,
            'created': created,
        }, status=201 if created else 200)

    def get_permissions(self):
        if self.action == 'oauth_callback':
            return [AllowAny()]
        return super().get_permissions()

    @action(detail=True, methods=['post'], url_path='configure-sync')
    def configure_sync(self, request, pk=None):
        from .mailbox_sync import configure_mailbox_sync, sync_projection
        connection = self.get_object()
        if (
            request.query_params or not isinstance(request.data, dict)
            or set(request.data) not in ({'enabled'}, {'enabled', 'expected_identity'})
            or type(request.data.get('enabled')) is not bool
            or ('expected_identity' in request.data and not isinstance(request.data['expected_identity'], dict))
        ):
            return Response({'detail': 'Provide enabled as true or false and, optionally, the reviewed expected_identity.'}, status=400)
        configure_mailbox_sync(
            connection=connection, user=request.user, enabled=request.data['enabled'],
            expected_identity=request.data.get('expected_identity'),
        )
        connection.refresh_from_db()
        return Response({'enabled': connection.enabled, 'sync': sync_projection(connection)})

    def _is_admin(self):
        return IsAdmin().has_permission(self.request, self)

    def get_queryset(self):
        queryset = visible_mailbox_connections(self.request.user)
        if self.request.query_params.get('mine', '').lower() in {'1', 'true', 'yes'}:
            return queryset.filter(created_by=self.request.user)
        return queryset

    def _require_application_mailbox_write(self, action):
        if not self._is_admin():
            raise PermissionDenied('Only administrators can configure shared mailbox connections.')
        if not all(module_action_allowed(self.request.user, 'sales_email_intake', required)
                   for required in ('read', action)):
            raise PermissionDenied(f'You need read and {action} access to configure a shared mailbox.')

    def create(self, request, *args, **kwargs):
        if isinstance(request.data, dict) and request.data.get('auth_mode') == 'application':
            self._require_application_mailbox_write('create')
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        current = self.get_object()
        if current.auth_mode == 'application' or (
            isinstance(request.data, dict) and request.data.get('auth_mode') == 'application'
        ):
            self._require_application_mailbox_write('update')
        return super().update(request, *args, **kwargs)

    def perform_create(self, serializer):
        auth_mode = serializer.validated_data.get('auth_mode', 'delegated')
        if auth_mode == 'application':
            self._require_application_mailbox_write('create')
        try:
            with transaction.atomic():
                serializer.save(
                    auth_mode=auth_mode,
                    created_by=self.request.user,
                    updated_by=self.request.user,
                )
        except IntegrityError:
            # Concurrent application registrations use one normalized address;
            # recover the existing uniqueness failure without a second record.
            if auth_mode == 'application' and SalesMailboxConnection.objects.filter(
                mailbox_address__iexact=serializer.validated_data['mailbox_address'],
            ).exists():
                raise ValidationError({'mailbox_address': 'This mailbox connection already exists.'}) from None
            raise

    def perform_update(self, serializer):
        with transaction.atomic():
            # Serialize reconfiguration against capture, then repeat validation
            # on the locked instance: capture may have won since is_valid().
            current = self.get_queryset().select_for_update().get(pk=serializer.instance.pk)
            serializer.instance = current
            auth_mode = serializer.validated_data.get('auth_mode', current.auth_mode)
            if current.auth_mode == 'application' or auth_mode == 'application':
                self._require_application_mailbox_write('update')
            serializer.validated_data.update(serializer.validate({**serializer.validated_data, 'auth_mode': auth_mode}))
            serializer.save(auth_mode=auth_mode, updated_by=self.request.user)

    def perform_destroy(self, instance):
        with transaction.atomic():
            current = self.get_queryset().select_for_update().get(pk=instance.pk)
            try:
                current.delete()
            except ProtectedError:
                raise EmailCaptureConflict('This connection has saved emails or sync history and cannot be deleted.') from None

    def _authorization_response(self, request, connection):
        with transaction.atomic():
            connection = self.get_queryset().select_for_update().get(pk=connection.pk)
            if connection.email_intakes.exists() or hasattr(connection, 'sync_state'):
                raise EmailCaptureConflict('This connection has saved emails and cannot be changed to delegated Outlook access.')
            if connection.auth_mode != 'delegated':
                connection.auth_mode = 'delegated'
                connection.save(update_fields=['auth_mode', 'updated_at'])
        nonce = secrets.token_urlsafe(32)
        state_payload = {
            'connection_id': str(connection.id),
            'user_id': str(request.user.id),
            'nonce': nonce,
        }
        cache.set(f'sales-graph-oauth:{nonce}', state_payload, timeout=600)
        state_token = signing.dumps(state_payload, salt='sales-graph-oauth')
        try:
            authorization_url = SalesMicrosoftGraphService(
                connection
            ).delegated_authorization_url(state_token)
        except Exception as exc:
            cache.delete(f'sales-graph-oauth:{nonce}')
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({
            'authorization_url': authorization_url,
            'expires_in_seconds': 600,
        })

    @action(detail=False, methods=['post'], url_path='connect-my-outlook')
    def connect_my_outlook(self, request):
        """Start one-click delegated OAuth using RADAI's central Entra app."""
        try:
            tenant_id, client_id = SalesMicrosoftGraphService.delegated_runtime_configuration()
        except Exception as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        mailbox_address = (request.user.email or '').strip().lower()
        if not mailbox_address:
            return Response(
                {
                    'detail': (
                        'Your RADAI profile needs an email address before '
                        'Outlook can be connected.'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        connection = SalesMailboxConnection.objects.filter(
            created_by=request.user,
            auth_mode='delegated',
        ).order_by('created_at').first()
        if connection is None:
            existing_mailbox = SalesMailboxConnection.objects.filter(
                mailbox_address__iexact=mailbox_address,
            ).first()
            if existing_mailbox:
                if existing_mailbox.created_by_id != request.user.id:
                    return Response(
                        {'detail': 'This mailbox is already connected to another RADAI user.'},
                        status=status.HTTP_409_CONFLICT,
                    )
                connection = existing_mailbox
            else:
                connection = SalesMailboxConnection.objects.create(
                    name='My Outlook account',
                    auth_mode='delegated',
                    tenant_id=tenant_id,
                    client_id=client_id,
                    mailbox_address=mailbox_address,
                    enabled=False,
                    created_by=request.user,
                    updated_by=request.user,
                )

        if connection.email_intakes.exists() or hasattr(connection, 'sync_state'):
            raise EmailCaptureConflict('This connection has saved emails and cannot be changed to delegated Outlook access.')
        changed_fields = []
        for field, value in (
            ('tenant_id', tenant_id),
            ('client_id', client_id),
            ('updated_by', request.user),
        ):
            if getattr(connection, field) != value:
                setattr(connection, field, value)
                changed_fields.append(field)
        if changed_fields:
            connection.save(update_fields=[*changed_fields, 'updated_at'])
        return self._authorization_response(request, connection)

    @action(detail=True, methods=['post'], url_path='test-connection')
    def test_connection(self, request, pk=None):
        result = SalesMicrosoftGraphService(self.get_object()).health_check()
        response_status = status.HTTP_200_OK if result['connected'] else status.HTTP_400_BAD_REQUEST
        if not result['connected']:
            # Provider diagnostics can contain tenant/account identifiers. The
            # setup form already has safe configuration-presence metadata.
            result = {'connected': False, 'error': 'Microsoft could not verify this mailbox. Check its configuration and server access.'}
        return Response(result, status=response_status)

    @action(detail=True, methods=['post'], url_path='connect-outlook')
    def connect_outlook(self, request, pk=None):
        return self._authorization_response(request, self.get_object())

    @action(
        detail=False,
        methods=['get'],
        url_path='oauth/callback',
        url_name='oauth-callback',
    )
    def oauth_callback(self, request):
        frontend_url = str(settings.FRONTEND_URL).rstrip('/')

        def finish(result, reason=''):
            query = urlencode({'outlook': result, 'reason': reason})
            return redirect(f'{frontend_url}/sales?{query}')

        if request.query_params.get('error'):
            return finish('error', 'Microsoft sign-in was cancelled or denied.')
        state_token = request.query_params.get('state', '')
        code = request.query_params.get('code', '')
        if not state_token or not code:
            return finish('error', 'Microsoft sign-in returned an incomplete response.')
        try:
            payload = signing.loads(
                state_token,
                salt='sales-graph-oauth',
                max_age=600,
            )
            nonce = payload['nonce']
            expected = cache.get(f'sales-graph-oauth:{nonce}')
            cache.delete(f'sales-graph-oauth:{nonce}')
            if expected != payload:
                raise signing.BadSignature('OAuth state has expired or was already used.')
            # OAuth is a GET callback, outside the normal write-route atomic
            # guard. Serialize its final identity save with capture as well.
            with transaction.atomic():
                connection = SalesMailboxConnection.objects.select_for_update().get(
                    id=payload['connection_id'],
                    created_by_id=payload['user_id'],
                )
                if connection.email_intakes.exists() or hasattr(connection, 'sync_state'):
                    return finish('error', 'This connection has saved emails and cannot be reconfigured.')
                result = SalesMicrosoftGraphService(
                    connection
                ).complete_delegated_authorization(code)
            if not result['connected']:
                return finish('error', result.get('error', 'Mailbox verification failed.'))
        except Exception as exc:
            logger.warning('Sales Outlook OAuth callback failed: %s', exc)
            return finish('error', str(exc)[:300])
        return finish('connected')

    @action(detail=True, methods=['post'], url_path='disconnect-outlook')
    def disconnect_outlook(self, request, pk=None):
        connection = self.get_object()
        SalesMicrosoftGraphService(connection).disconnect()
        return Response(self.get_serializer(connection).data)


# ==============================================================================
# CLIENT MANAGEMENT VIEWSETS
# ==============================================================================

class ClientViewSet(TeamCollaborationMixin, viewsets.ModelViewSet):
    """
    ViewSet for Client Management (CRM)
    
    Features:
    - Full CRUD for clients
    - AI-powered health scoring
    - Churn prediction
    - Client insights
    
    🔐 SECURITY: Requires 'sales' module access (soft-coded from rbac_config.py)
    """
    
    # Data visibility configuration
    visibility_module_code = 'sales'
    visibility_owner_field = 'account_manager'
    
    queryset = Client.objects.all()
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'sales'
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'industry_type', 'client_tier', 'account_manager']
    search_fields = ['client_code', 'company_name', 'email', 'phone']
    ordering_fields = ['created_at', 'company_name', 'health_score', 'lifetime_value']
    ordering = ['-created_at']

    def perform_destroy(self, instance):
        from .opportunity_workspace import delete_client_with_workspace_guard
        delete_client_with_workspace_guard(instance)
    
    def get_serializer_class(self):
        if self.action == 'retrieve':
            return ClientDetailSerializer
        elif self.action in ['create', 'update', 'partial_update']:
            return ClientCreateSerializer
        return ClientListSerializer
    
    def perform_create(self, serializer):
        """Set account manager to current user if not specified"""
        if not serializer.validated_data.get('account_manager'):
            serializer.save(account_manager=self.request.user)
        else:
            serializer.save()
    
    @action(detail=True, methods=['post'])
    def calculate_health_score(self, request, pk=None):
        """
        Recalculate AI-powered client health score
        """
        client = self.get_object()
        score = client.calculate_health_score()
        
        return Response({
            'success': True,
            'client_id': str(client.id),
            'health_score': score,
            'message': f'Health score recalculated: {score}/100'
        })
    
    @action(detail=True, methods=['get'])
    def churn_prediction(self, request, pk=None):
        """
        AI-powered churn risk prediction
        """
        client = self.get_object()
        prediction = SalesAIService.predict_churn_risk(client)
        
        return Response({
            'success': True,
            'client': {
                'id': str(client.id),
                'company_name': client.company_name,
                'client_code': client.client_code
            },
            'churn_prediction': prediction
        })
    
    @action(detail=True, methods=['get'])
    def insights(self, request, pk=None):
        """
        Generate AI insights for client
        """
        client = self.get_object()
        insights = SalesAIService.generate_insights_summary(client)
        
        return Response({
            'success': True,
            'client_id': str(client.id),
            'insights': insights,
            'generated_at': timezone.now()
        })
    
    @action(detail=False, methods=['get'])
    def at_risk(self, request):
        """
        Get list of at-risk clients (high churn probability)
        """
        clients = self.get_queryset().filter(
            Q(churn_risk='high') | Q(health_score__lt=40)
        ).order_by('health_score')
        
        serializer = self.get_serializer(clients, many=True)
        return Response({
            'success': True,
            'count': clients.count(),
            'clients': serializer.data
        })
    
    @action(detail=False, methods=['get'])
    def top_clients(self, request):
        """
        Get top clients by lifetime value
        """
        limit = int(request.query_params.get('limit', 10))
        clients = self.get_queryset().order_by('-lifetime_value')[:limit]
        
        serializer = self.get_serializer(clients, many=True)
        return Response({
            'success': True,
            'count': clients.count(),
            'clients': serializer.data
        })


class ContactViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Contact Management
    Individual contacts within client organizations
    
    🔐 SECURITY: Requires 'sales' module access (soft-coded from rbac_config.py)
    """
    
    queryset = Contact.objects.all()
    serializer_class = ContactSerializer
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'sales'
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['client', 'role_type', 'is_primary', 'is_active']
    search_fields = ['first_name', 'last_name', 'email', 'job_title']
    
    @action(detail=False, methods=['get'])
    def by_client(self, request):
        """
        Get all contacts for a specific client
        """
        client_id = request.query_params.get('client_id')
        if not client_id:
            return Response({
                'success': False,
                'error': 'client_id parameter required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        contacts = self.get_queryset().filter(client_id=client_id)
        serializer = self.get_serializer(contacts, many=True)
        
        return Response({
            'success': True,
            'count': contacts.count(),
            'contacts': serializer.data
        })


class FrameworkAgreementViewSet(TeamCollaborationMixin, viewsets.ModelViewSet):
    business_approval_actions = {'activate'}
    """Client framework terms, eligibility, rate versions, and value control."""

    visibility_module_code = 'sales'
    visibility_owner_field = 'owner'
    queryset = FrameworkAgreement.objects.select_related('client', 'owner', 'approved_by')
    serializer_class = FrameworkAgreementSerializer
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'sales'
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'client', 'owner', 'currency']
    search_fields = ['framework_number', 'title', 'client__company_name']
    ordering_fields = ['effective_date', 'expiry_date', 'ceiling_value', 'created_at']

    def perform_create(self, serializer):
        serializer.save(owner=serializer.validated_data.get('owner') or self.request.user)

    @action(detail=True, methods=['post'])
    def activate(self, request, pk=None):
        framework = self.get_object()
        from apps.rbac.approval_eligibility import require_configured_approval
        require_configured_approval(request.user, 'sales_frameworks', framework, 'activate')
        if framework.owner_id == request.user.id:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'approver': 'The framework owner cannot approve their own agreement.'})
        if not framework.signed_document:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'signed_document': 'A signed agreement is required before activation.'})
        if not framework.effective_date <= timezone.now().date() <= framework.expiry_date:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'validity': 'The framework is outside its effective dates.'})
        framework.status = 'active'
        framework.approved_by = request.user
        framework.approved_at = timezone.now()
        framework.save(update_fields=['status', 'approved_by', 'approved_at', 'updated_at'])
        return Response(self.get_serializer(framework).data)


# ==============================================================================
# SALES PIPELINE VIEWSETS
# ==============================================================================

class DealViewSet(TeamCollaborationMixin, viewsets.ModelViewSet):
    @action(detail=True, methods=['get', 'post'], url_path='bid-preparation')
    def bid_preparation(self, request, pk=None):
        from .bid_preparation import bid_projection, connect_preparation
        result = (bid_projection(pk, request.user) if request.method == 'GET' else
                  connect_preparation(pk, request.user, request.data))
        return Response(result, headers={'Cache-Control': 'no-store, private'})

    @action(detail=True, methods=['get'], url_path='bid-preparation-candidates')
    def bid_preparation_candidates(self, request, pk=None):
        from .bid_preparation import project_candidates
        return Response(project_candidates(pk, request.user, request.query_params.get('search', ''),
                                           request.query_params.get('page', 1)),
                        headers={'Cache-Control': 'no-store, private'})

    business_approval_actions = {'bid_decision', 'approve_award', 'reject_award'}
    """
    ViewSet for Deal/Opportunity Management
    
    Features:
    - Full CRUD for deals
    - AI win probability
    - Lead scoring
    - Next best action recommendations
    
    🔐 SECURITY: Requires 'sales' module access (soft-coded from rbac_config.py)
    """
    
    # Data visibility configuration
    visibility_module_code = 'sales'
    visibility_owner_field = 'owner'
    
    queryset = Deal.objects.select_related('client', 'owner', 'created_by').prefetch_related('team_members')
    permission_classes = [IsAuthenticated, HasModuleAccess]
    # Header JWT stays the default; the query-param variant exists so the
    # letter PDF preview can load inside an <iframe> (no Authorization header).
    authentication_classes = [QueryParamJWTAuthentication]
    module_required = 'sales'
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['stage', 'priority', 'client', 'owner']
    search_fields = ['deal_code', 'deal_name', 'client__company_name']
    ordering_fields = ['deal_code', 'id', 'created_at', 'expected_close_date', 'estimated_value', 'weighted_value']
    ordering = ['-created_at']

    @action(detail=True, methods=['get'], url_path='workspace')
    def workspace(self, request, pk=None):
        from .opportunity_workspace import workspace_projection
        return Response(workspace_projection(self.get_object(), request.user),
                        headers={'Cache-Control': 'no-store, private'})

    @action(detail=True, methods=['post'], url_path='workspace/setup')
    def workspace_setup(self, request, pk=None):
        from .opportunity_workspace import setup_workspace, workspace_projection
        opportunity = self.get_object()
        if request.data:
            raise ValidationError({'detail': 'Workspace setup uses the saved opportunity and configured destination.'})
        setup_workspace(opportunity, request.user)
        return Response(workspace_projection(opportunity, request.user), status=202)

    @action(detail=True, methods=['patch'], url_path=r'workspace/folders/(?P<folder_key>[^/]+)/tag')
    def workspace_folder_tag(self, request, pk=None, folder_key=None):
        from .folder_tags import save_folder_tag
        if request.query_params:
            raise ValidationError({'detail': 'Folder tags belong to the opportunity category and do not accept storage parameters.'})
        return Response(save_folder_tag(self.get_object().pk, request.user, folder_key, request.data),
                        headers={'Cache-Control': 'no-store, private'})

    @action(detail=True, methods=['get'], url_path=r'workspace/folders/(?P<folder_key>[a-z]+)/files')
    def workspace_files(self, request, pk=None, folder_key=None):
        from .opportunity_workspace import list_workspace_files
        provider = request.query_params.get('storage', 'sharepoint')
        if provider not in ('radai', 'sharepoint'):
            raise ValidationError({'storage': 'Choose RADAI or SharePoint storage.'})
        if provider == 'radai':
            from .private_attachments import list_private_files
            return Response(list_private_files(self.get_object(), request.user, folder_key, request.query_params.get('cursor')))
        return Response(list_workspace_files(self.get_object(), request.user, folder_key, request.query_params.get('cursor')))

    @action(detail=True, methods=['get', 'delete'], url_path=r'workspace/folders/(?P<folder_key>[a-z]+)/files/(?P<file_id>[^/]+)')
    def workspace_file(self, request, pk=None, folder_key=None, file_id=None):
        from .opportunity_workspace import delete_workspace_file, workspace_file_details
        opportunity = self.get_object()
        if request.method == 'DELETE':
            return Response(delete_workspace_file(opportunity, request.user, folder_key, file_id),
                            headers={'Cache-Control': 'no-store, private'})
        return Response(workspace_file_details(opportunity, request.user, folder_key, file_id),
                        headers={'Cache-Control': 'no-store, private'})

    @action(detail=True, methods=['get'], url_path=r'workspace/folders/(?P<folder_key>[a-z]+)/files/(?P<file_id>[^/]+)/versions')
    def workspace_versions(self, request, pk=None, folder_key=None, file_id=None):
        from .opportunity_workspace import list_workspace_file_versions
        return Response(list_workspace_file_versions(self.get_object(), request.user, folder_key, file_id,
                                                      request.query_params.get('cursor')),
                        headers={'Cache-Control': 'no-store, private'})

    @action(detail=True, methods=['get'], url_path=r'workspace/folders/(?P<folder_key>[a-z]+)/files/(?P<file_id>[^/]+)/download')
    def workspace_download(self, request, pk=None, folder_key=None, file_id=None):
        from django.http import FileResponse
        from .opportunity_workspace import download_workspace_file
        content, name = download_workspace_file(self.get_object(), request.user, folder_key, file_id)
        response = FileResponse(content, as_attachment=True, filename=name, content_type='application/octet-stream')
        response['Cache-Control'] = 'no-store, private'
        response['X-Content-Type-Options'] = 'nosniff'
        return response

    @action(detail=True, methods=['post'], url_path=r'workspace/folders/(?P<folder_key>[a-z]+)/upload')
    def workspace_upload(self, request, pk=None, folder_key=None):
        from .opportunity_workspace import upload_workspace_file
        opportunity = self.get_object()
        request_id = serializers.UUIDField().run_validation(request.data.get('upload_request_id'))
        provider = request.data.get('storage', 'sharepoint')
        if provider not in ('radai', 'sharepoint'):
            raise ValidationError({'storage': 'Choose RADAI or SharePoint storage.'})
        if set(request.data) - {'file', 'upload_request_id', 'storage'} or len(request.FILES.getlist('file')) != 1:
            raise ValidationError({'file': 'Upload one file and its upload_request_id.'})
        if provider == 'radai':
            from .private_attachments import upload_private_file
            result, created = upload_private_file(opportunity, request.user, folder_key,
                                                  request.FILES.get('file'), request_id)
            return Response(result, status=201 if created else 200)
        result, created = upload_workspace_file(opportunity, request.user, folder_key,
                                                request.FILES.get('file'), request_id)
        return Response(result, status=201 if created else 200)

    @action(detail=True, methods=['post'], url_path=r'workspace/folders/(?P<folder_key>[a-z]+)/files/(?P<file_id>[^/]+)/versions/upload')
    def workspace_upload_version(self, request, pk=None, folder_key=None, file_id=None):
        from .private_attachments import upload_private_version
        if (set(request.data) - {'file', 'upload_request_id', 'expected_token', 'revision_note'}
                or len(request.FILES.getlist('file')) != 1 or request.query_params):
            raise ValidationError({'file': 'Upload one file, its request ID, current head token and optional revision note.'})
        request_id = serializers.UUIDField().run_validation(request.data.get('upload_request_id'))
        result, created = upload_private_version(
            self.get_object(), request.user, folder_key, file_id, request.FILES.get('file'), request_id,
            request.data.get('expected_token'), request.data.get('revision_note', ''),
        )
        return Response(result, status=201 if created else 200, headers={'Cache-Control': 'no-store, private'})

    @action(detail=True, methods=['get', 'post'], url_path=r'workspace/folders/(?P<folder_key>[a-z]+)/files/(?P<file_id>[^/]+)/classification')
    def workspace_file_classification(self, request, pk=None, folder_key=None, file_id=None):
        from .document_classification import get_document_classification, save_document_classification
        if request.query_params:
            raise ValidationError({'detail': 'Document classification uses the selected opportunity and file.'})
        opportunity = self.get_object()
        if request.method in ('GET', 'HEAD'):
            result = get_document_classification(opportunity.pk, request.user, folder_key, file_id)
        else:
            result = save_document_classification(opportunity.pk, request.user, folder_key, file_id, request.data)
        return Response(result, headers={'Cache-Control': 'no-store, private'})

    @action(detail=True, methods=['post'], url_path=r'workspace/folders/(?P<folder_key>[a-z]+)/files/(?P<file_id>[^/]+)/classification/retry')
    def workspace_file_classification_retry(self, request, pk=None, folder_key=None, file_id=None):
        from .document_classification import retry_document_classification
        if request.query_params:
            raise ValidationError({'detail': 'Classification retry uses the selected opportunity and file.'})
        result = retry_document_classification(
            self.get_object().pk, request.user, folder_key, file_id, request.data,
        )
        return Response(result, headers={'Cache-Control': 'no-store, private'})

    def perform_destroy(self, instance):
        from .opportunity_workspace import delete_opportunity_with_workspace_guard
        delete_opportunity_with_workspace_guard(instance)

    def destroy(self, request, *args, **kwargs):
        # Granular RBAC: deleting an opportunity requires the module's
        # delete action (superusers and granted roles pass).
        if not module_action_allowed(request.user, 'sales_opportunities', 'delete'):
            raise PermissionDenied('You do not have the Sales opportunities delete permission.')
        return super().destroy(request, *args, **kwargs)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        # Computed once per request so list rows do not repeat the RBAC lookup.
        context['sales_can_delete'] = module_action_allowed(
            self.request.user, 'sales_opportunities', 'delete',
        )
        return context
    def get_serializer_class(self):
        if self.action == 'retrieve':
            return DealDetailSerializer
        elif self.action in ['create', 'update', 'partial_update']:
            return DealCreateSerializer
        return DealListSerializer
    
    @transaction.atomic
    def create(self, request, *args, **kwargs):
        from .manual_registration import RegistrationConflict, registration_fingerprint

        data = request.data.copy()
        request_id = None
        if 'registration_request_id' in data:
            request_id = str(serializers.UUIDField().run_validation(data.pop('registration_request_id')))
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        fingerprint = registration_fingerprint(serializer.validated_data)
        if request_id:
            # Serialize retries by this actor without reserving a VF number or
            # depending on an in-memory cache. The audit commits with the Deal.
            get_user_model().objects.select_for_update(no_key=True).get(pk=request.user.pk)
            previous = OpportunityAuditEvent.objects.filter(
                actor=request.user, event_type='opportunity_created',
                data__registration_request_id=request_id,
            ).first()
            if previous:
                opportunity = self.get_queryset().filter(pk=previous.opportunity_id).first()
                if opportunity is None:
                    raise PermissionDenied('The saved opportunity is not available to you.')
                if previous.data.get('registration_fingerprint') != fingerprint:
                    raise RegistrationConflict()
                return Response(DealDetailSerializer(opportunity, context=self.get_serializer_context()).data)
        opportunity = serializer.save()
        _audit(
            opportunity, request.user, 'opportunity_created',
            to_stage=opportunity.stage,
            data={
                'opportunity_source': opportunity.opportunity_source,
                **({'registration_request_id': request_id, 'registration_fingerprint': fingerprint} if request_id else {}),
            },
        )
        return Response(
            DealDetailSerializer(opportunity, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = Deal.objects.select_for_update(no_key=True).get(pk=self.get_object().pk)
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        instance._prefetched_objects_cache = {}
        return Response(DealDetailSerializer(instance, context=self.get_serializer_context()).data)

    @action(detail=False, methods=['get'], url_path='registration-options')
    def registration_options(self, request):
        from .opportunity_registration import visible_opportunity_owners

        if not all(module_action_allowed(request.user, 'sales_opportunities', verb) for verb in ('read', 'create')):
            raise PermissionDenied('You do not have access to register opportunities.')
        return Response({
            'default_owner': request.user.pk,
            'owners': [
                {'id': owner.pk, 'name': owner.get_full_name() or owner.username}
                for owner in visible_opportunity_owners(request.user).order_by('first_name', 'last_name', 'pk')
            ],
            'opportunity_types': [
                {'value': value, 'label': label}
                for value, label in Deal._meta.get_field('opportunity_type').choices
            ],
        })

    @action(detail=False, methods=['post'], url_path='export')
    def export(self, request):
        from .opportunity_export import opportunity_export_ids, opportunity_export_response

        if not all(module_action_allowed(request.user, 'sales_opportunities', verb)
                   for verb in ('read', 'export')):
            raise PermissionDenied('You do not have access to export opportunities.')
        if request.query_params:
            raise ValidationError({'ids': 'Send the selected opportunity IDs in the request body.'})
        identifiers = opportunity_export_ids(request.data)
        return opportunity_export_response(self.get_queryset(), identifiers)

    @action(detail=True, methods=['post'])
    def verify(self, request, pk=None):
        client = self.get_object()
        client.verification_status = 'verified'
        client.verified_by = request.user
        client.verified_at = timezone.now()
        client.save(update_fields=['verification_status', 'verified_by', 'verified_at', 'updated_at'])
        return Response(ClientDetailSerializer(client).data)

    def _detail_response(self, deal, **extra):
        return Response({'success': True, 'opportunity': DealDetailSerializer(deal).data, **extra})

    @action(detail=True, methods=['post'], url_path='submit-qualification')
    def submit_qualification(self, request, pk=None):
        opportunity = submit_qualification(
            self.get_object(),
            request.user,
            special_note=request.data.get('special_note', ''),
        )
        return self._detail_response(
            opportunity,
            warnings=getattr(opportunity, 'submission_warnings', []),
        )

    @action(detail=True, methods=['post'], url_path='bid-decision')
    def bid_decision(self, request, pk=None):
        deal = record_bid_decision(
            self.get_object(), request.user,
            request.data.get('decision'), request.data.get('reason', ''),
        )
        return self._detail_response(
            deal,
            warnings=getattr(deal, 'decision_warnings', []),
        )

    @action(detail=True, methods=['post'], url_path='ceo-decision')
    def ceo_decision(self, request, pk=None):
        deal = record_ceo_decision(
            self.get_object(), request.user,
            request.data.get('decision'), request.data.get('reason', ''),
        )
        return self._detail_response(deal)

    @action(detail=True, methods=['post'], url_path='bid-decision-justification')
    def bid_decision_justification(self, request, pk=None):
        from .bid_justification import draft_bid_justification
        if request.query_params:
            raise ValidationError({'detail': 'Provide the selected decision and text in the request body.'})
        result = draft_bid_justification(request.user, pk, request.data)
        return Response(result, headers={'Cache-Control': 'private, no-store'})

    @action(detail=True, methods=['post'], url_path='prepare-letter')
    def prepare_letter(self, request, pk=None):
        serializer = SalesLetterCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        letter = prepare_letter(
            self.get_object(),
            request.user,
            serializer.validated_data['letter_type'],
            serializer.validated_data.get('custom_data', {}),
        )
        self._attach_letter_files(letter, request.user)
        return Response(SalesLetterSerializer(letter, context={'request': request}).data)

    def _attach_letter_files(self, letter, actor):
        """Best-effort attach of the generated PDF+DOCX to the Correspondence folder."""
        from .letter_attachments import attach_letter_files
        try:
            attach_letter_files(letter, actor)
        except Exception as e:
            logger.error(f"Failed to attach letter {letter.id} files to Correspondence: {e}")

    @action(detail=True, methods=['get'], url_path='letters')
    def list_letters(self, request, pk=None):
        letters = self.get_object().letters.all()
        return Response(SalesLetterSerializer(letters, many=True, context={'request': request}).data)

    @action(detail=True, methods=['post'], url_path='letters/(?P<letter_id>[^/]+)/send')
    def send_letter(self, request, pk=None, letter_id=None):
        deal = self.get_object()
        try:
            letter = deal.letters.get(pk=letter_id)
        except Deal.letters.RelatedObjectDoesNotExist:
            raise ValidationError({'letter': 'Letter not found for this opportunity.'})
        recipient = request.data.get('recipient')
        if not recipient:
            raise ValidationError({'recipient': 'Recipient email is required.'})
        letter.mark_sent(recipient)
        _audit(
            deal, request.user, 'letter_sent',
            reason=f'Sent {letter.get_letter_type_display()} letter to {recipient}',
            data={'letter_id': str(letter.id), 'recipient': recipient},
        )
        return Response(SalesLetterSerializer(letter, context={'request': request}).data)

    @action(detail=True, methods=['get'], url_path='letters/(?P<letter_id>[^/]+)/pdf')
    def download_letter_pdf(self, request, pk=None, letter_id=None):
        """Download the PDF for a generated letter."""
        deal = self.get_object()
        try:
            letter = deal.letters.get(pk=letter_id)
        except Deal.letters.RelatedObjectDoesNotExist:
            raise ValidationError({'letter': 'Letter not found for this opportunity.'})
        
        from .letter_pdf import get_letter_pdf_bytes
        pdf_bytes = get_letter_pdf_bytes(letter)
        
        from django.http import FileResponse
        import io
        response = FileResponse(
            io.BytesIO(pdf_bytes),
            content_type='application/pdf',
            as_attachment=True,
            filename=f"{deal.deal_code}-{letter.letter_type}.pdf"
        )
        return response

    @action(detail=True, methods=['get'], url_path='letters/(?P<letter_id>[^/]+)/pdf/preview')
    def preview_letter_pdf(self, request, pk=None, letter_id=None):
        """Preview the PDF for a generated letter (inline display)."""
        deal = self.get_object()
        try:
            letter = deal.letters.get(pk=letter_id)
        except Deal.letters.RelatedObjectDoesNotExist:
            raise ValidationError({'letter': 'Letter not found for this opportunity.'})
        
        from .letter_pdf import get_letter_pdf_bytes
        try:
            pdf_bytes = get_letter_pdf_bytes(letter)
        except Exception as e:
            logger.error(f"PDF preview generation failed for letter {letter_id}: {e}")
            raise ValidationError({'detail': 'Failed to generate PDF preview. Please try again.'})
        
        from django.http import FileResponse
        import io
        from django.conf import settings
        response = FileResponse(
            io.BytesIO(pdf_bytes),
            content_type='application/pdf',
            as_attachment=False,
            filename=f"{deal.deal_code}-{letter.letter_type}.pdf"
        )
        # Allow iframe embedding
        response['X-Frame-Options'] = 'SAMEORIGIN'
        # Allow iframe from frontend origin
        frontend_origin = getattr(settings, 'FRONTEND_URL', 'http://localhost:5173')
        response['Content-Security-Policy'] = f"frame-ancestors 'self' {frontend_origin}"
        return response

    @action(detail=True, methods=['get'], url_path='letters/(?P<letter_id>[^/]+)/docx')
    def download_letter_docx(self, request, pk=None, letter_id=None):
        """Download the DOCX for a generated letter."""
        deal = self.get_object()
        try:
            letter = deal.letters.get(pk=letter_id)
        except Deal.letters.RelatedObjectDoesNotExist:
            raise ValidationError({'letter': 'Letter not found for this opportunity.'})

        from .letter_docx import get_letter_docx_bytes
        docx_bytes = get_letter_docx_bytes(letter)

        from django.http import FileResponse
        import io
        response = FileResponse(
            io.BytesIO(docx_bytes),
            content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            as_attachment=True,
            filename=f"{deal.deal_code}-{letter.letter_type}.docx"
        )
        return response

    @action(detail=True, methods=['post'], url_path='letters/(?P<letter_id>[^/]+)/regenerate-pdf')
    def regenerate_letter_pdf(self, request, pk=None, letter_id=None):
        """Regenerate PDF+DOCX after letter content edits."""
        deal = self.get_object()
        try:
            letter = deal.letters.get(pk=letter_id)
        except Deal.letters.RelatedObjectDoesNotExist:
            raise ValidationError({'letter': 'Letter not found for this opportunity.'})

        payload = request.data.copy() if hasattr(request.data, 'copy') else dict(request.data or {})
        nested_custom_data = payload.get('custom_data')
        if (
            isinstance(nested_custom_data, dict)
            and not any(k in payload for k in ('subject', 'body'))
            and any(k in nested_custom_data for k in ('subject', 'body', 'custom_data'))
        ):
            logger.warning(
                "SalesLetter regenerate received nested legacy payload; normalizing",
                extra={'deal_id': str(deal.id), 'letter_id': str(letter.id)}
            )
            payload = {
                **payload,
                'subject': nested_custom_data.get('subject', payload.get('subject')),
                'body': nested_custom_data.get('body', payload.get('body')),
                'custom_data': nested_custom_data.get('custom_data', nested_custom_data),
            }

        logger.info(
            "SalesLetter regenerate request payload",
            extra={'deal_id': str(deal.id), 'letter_id': str(letter.id), 'request_data': payload}
        )

        serializer = SalesLetterRegenerateSerializer(data=payload)
        if not serializer.is_valid():
            logger.warning(
                "SalesLetter regenerate validation failed",
                extra={
                    'deal_id': str(deal.id),
                    'letter_id': str(letter.id),
                    'request_data': payload,
                    'serializer_errors': serializer.errors,
                }
            )
            raise ValidationError(serializer.errors)

        data = serializer.validated_data
        if 'subject' in data:
            letter.subject = data['subject']
        if 'body' in data:
            letter.body = data['body']
        if 'custom_data' in data:
            letter.custom_data = data['custom_data']
        letter.save(update_fields=['subject', 'body', 'custom_data', 'updated_at'])

        from .workflow import refresh_letter_files
        refresh_letter_files(letter, request.user)

        # Refresh from DB so serialized custom_data reflects the rev bump.
        letter.refresh_from_db()
        self._attach_letter_files(letter, request.user)
        letter.refresh_from_db()

        return Response(SalesLetterSerializer(letter, context={'request': request}).data)

    @action(detail=True, methods=['patch'], url_path='letters/(?P<letter_id>[^/]+)')
    def update_letter(self, request, pk=None, letter_id=None):
        """Update letter content (subject, body, custom_data)."""
        deal = self.get_object()
        try:
            letter = deal.letters.get(pk=letter_id)
        except Deal.letters.RelatedObjectDoesNotExist:
            raise ValidationError({'letter': 'Letter not found for this opportunity.'})
        
        # Update fields
        updated_fields = []
        if 'subject' in request.data:
            letter.subject = request.data['subject']
            updated_fields.append('subject')
        if 'body' in request.data:
            letter.body = request.data['body']
            updated_fields.append('body')
        if 'custom_data' in request.data:
            letter.custom_data = request.data['custom_data']
            updated_fields.append('custom_data')
        
        if updated_fields:
            updated_fields.append('updated_at')
            letter.save(update_fields=updated_fields)
        
        return Response(SalesLetterSerializer(letter, context={'request': request}).data)

    @action(detail=True, methods=['post'], url_path='proposal-draft-field')
    def proposal_draft_field(self, request, pk=None):
        from .proposal_draft_ai import draft_proposal_field
        if request.query_params:
            raise ValidationError({'detail': 'Provide the proposal field and draft text in the request body.'})
        result = draft_proposal_field(request.user, request.data, opportunity_id=pk)
        return Response(result, headers={'Cache-Control': 'private, no-store'})

    @action(detail=True, methods=['post'])
    def close(self, request, pk=None):
        deal = close_opportunity(
            self.get_object(), request.user,
            outcome=request.data.get('outcome'), reason=request.data.get('reason', ''),
        )
        return self._detail_response(deal)

    @action(detail=True, methods=['post'], url_path='enter-negotiation')
    def enter_negotiation(self, request, pk=None):
        return self._detail_response(enter_negotiation(
            self.get_object(), request.user, request.data.get('reason', ''),
        ))

    @action(detail=True, methods=['post'], url_path='submit-award')
    def submit_award(self, request, pk=None):
        try:
            award_value = Decimal(str(request.data.get('award_value')))
        except Exception as exc:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'award_value': 'Enter a valid award value.'}) from exc
        deal = submit_award(
            self.get_object(), request.user,
            reference=request.data.get('award_reference'),
            award_date=request.data.get('award_date'),
            award_value=award_value,
            handover_data=request.data.get('handover_data'),
        )
        return self._detail_response(deal)

    @action(detail=True, methods=['post'], url_path='approve-award')
    def approve_award(self, request, pk=None):
        return self._detail_response(decide_award(
            self.get_object(), request.user, approved=True, reason=request.data.get('reason', ''),
        ))

    @action(detail=True, methods=['post'], url_path='reject-award')
    def reject_award(self, request, pk=None):
        return self._detail_response(decide_award(
            self.get_object(), request.user, approved=False, reason=request.data.get('reason', ''),
        ))

    @action(detail=True, methods=['post'], url_path='convert-to-project')
    def convert_to_project(self, request, pk=None):
        deal, project, created = convert_to_project(
            self.get_object().pk, request.user,
            project_code=request.data.get('project_code'),
            project_name=request.data.get('project_name'),
        )
        return self._detail_response(deal, project={
            'id': project.id, 'code': project.code, 'name': project.name,
        }, created=created)

    @action(detail=True, methods=['get'], url_path='audit-events')
    def audit_events(self, request, pk=None):
        from .serializers import OpportunityAuditEventSerializer
        deal = self.get_object()
        return Response(OpportunityAuditEventSerializer(deal.audit_events.all(), many=True).data)
    
    @action(detail=True, methods=['post'])
    def calculate_win_probability(self, request, pk=None):
        """
        AI-powered win probability calculation
        """
        deal = self.get_object()
        probability = SalesAIService.calculate_win_probability(deal)
        
        # Update deal with AI probability
        deal.ai_win_probability = int(probability['ai_probability'])
        deal.save(update_fields=['ai_win_probability'])
        
        return Response({
            'success': True,
            'deal_id': str(deal.id),
            'win_probability': probability
        })
    
    @action(detail=True, methods=['post'])
    def score_lead(self, request, pk=None):
        """
        AI-powered lead scoring
        """
        deal = self.get_object()
        
        # Prepare deal data for scoring
        deal_data = {
            'client_employee_count': deal.client.employee_count,
            'industry_type': deal.client.industry_type,
            'estimated_value': float(deal.estimated_value) if deal.estimated_value is not None else None,
            'expected_close_date': deal.expected_close_date,
            'primary_contact_role': deal.client.contacts.filter(is_primary=True).first().role_type if deal.client.contacts.filter(is_primary=True).exists() else 'other'
        }
        
        lead_score = SalesAIService.calculate_lead_score(deal_data)
        
        return Response({
            'success': True,
            'deal_id': str(deal.id),
            'lead_score': lead_score
        })
    
    @action(detail=True, methods=['get'])
    def next_action(self, request, pk=None):
        """
        AI-recommended next best action
        """
        deal = self.get_object()
        recommendation = SalesAIService.recommend_next_action(deal)
        
        return Response({
            'success': True,
            'deal_id': str(deal.id),
            'deal_name': deal.deal_name,
            'current_stage': deal.stage,
            'recommendation': recommendation
        })
    
    @action(detail=False, methods=['get'])
    def pipeline_summary(self, request):
        """
        Get pipeline summary by stage
        """
        pipeline = self.get_queryset().filter(stage__in=['lead', 'qualified', 'proposal', 'negotiation', 'award_pending'])
        
        summary = {
            'total_deals': pipeline.count(),
            'total_value': _opportunity_total(pipeline, 'estimated_value'),
            'weighted_value': _opportunity_total(pipeline, 'weighted_value'),
            'incomplete_opportunity_count': pipeline.filter(Q(estimated_value__isnull=True) | Q(currency='')).count(),
            'by_stage': {},
            'by_priority': {}
        }
        
        # Group by stage
        from .models import DEAL_STAGES
        for stage_key, stage_info in DEAL_STAGES.items():
            if stage_key in ['lead', 'qualified', 'proposal', 'negotiation', 'award_pending']:
                stage_deals = pipeline.filter(stage=stage_key)
                summary['by_stage'][stage_key] = {
                    'name': stage_info['name'],
                    'count': stage_deals.count(),
                    'value': _opportunity_total(stage_deals, 'estimated_value'),
                    'weighted_value': _opportunity_total(stage_deals, 'weighted_value'),
                }
        
        # Group by priority
        for priority in ['critical', 'high', 'medium', 'low']:
            priority_deals = pipeline.filter(priority=priority)
            summary['by_priority'][priority] = {
                'count': priority_deals.count(),
                'value': _opportunity_total(priority_deals, 'estimated_value'),
            }
        
        return Response({
            'success': True,
            'pipeline_summary': summary,
            'generated_at': timezone.now()
        })
    
    @action(detail=False, methods=['post'])
    def move_stage(self, request):
        """
        Move deal to different stage
        """
        return Response({
            'success': False,
            'error': 'Direct stage movement is disabled. Use the governed lifecycle command for the current stage.',
        }, status=status.HTTP_409_CONFLICT)

        # Legacy implementation retained below temporarily for migration
        # compatibility; it is unreachable by design.
        deal_id = request.data.get('deal_id')
        new_stage = request.data.get('stage')
        
        if not deal_id or not new_stage:
            return Response({
                'success': False,
                'error': 'deal_id and stage required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            deal = self.get_queryset().get(id=deal_id)
            old_stage = deal.stage
            deal.stage = new_stage
            deal.save()
            
            # Log activity
            SalesActivity.objects.create(
                client=deal.client,
                deal=deal,
                activity_type='other',
                subject=f'Deal moved from {old_stage} to {new_stage}',
                description=f'Stage change: {old_stage} → {new_stage}',
                performed_by=request.user
            )
            
            return Response({
                'success': True,
                'deal_id': str(deal.id),
                'old_stage': old_stage,
                'new_stage': new_stage,
                'probability': deal.probability
            })
        except Deal.DoesNotExist:
            return Response({
                'success': False,
                'error': 'Deal not found'
            }, status=status.HTTP_404_NOT_FOUND)


class QuoteViewSet(viewsets.ModelViewSet):
    @action(detail=False, methods=['get'], url_path='preparation-opportunities')
    def preparation_opportunities(self, request):
        from .proposal_readiness import preparation_opportunities
        return Response(preparation_opportunities(request.user, request.query_params),
                        headers={'Cache-Control': 'private, no-store'})

    @action(detail=True, methods=['post'], url_path='draft-field')
    def draft_field(self, request, pk=None):
        from .proposal_draft_ai import draft_proposal_field
        if request.query_params:
            raise ValidationError({'detail': 'Provide the proposal field and draft text in the request body.'})
        result = draft_proposal_field(request.user, request.data, quote_id=pk)
        return Response(result, headers={'Cache-Control': 'private, no-store'})

    @action(detail=True, methods=['get'], url_path='preparation')
    def preparation(self, request, pk=None):
        from .bid_preparation import preparation_projection
        return Response(preparation_projection(pk, request.user), headers={'Cache-Control': 'no-store, private'})

    @action(detail=True, methods=['get'], url_path='preparation-sources')
    def preparation_sources(self, request, pk=None):
        from .bid_preparation import preparation_sources
        return Response(preparation_sources(pk, request.user, request.query_params.get('search', ''),
                                            request.query_params.get('page', 1)),
                        headers={'Cache-Control': 'no-store, private'})

    @action(detail=True, methods=['post'], url_path='preparation-preview')
    def preparation_preview(self, request, pk=None):
        from .bid_preparation import preview_preparation
        return Response(preview_preparation(pk, request.user, request.data), headers={'Cache-Control': 'no-store, private'})

    @action(detail=True, methods=['post'], url_path='prepare')
    def prepare(self, request, pk=None):
        from .bid_preparation import apply_preparation
        return Response(apply_preparation(pk, request.user, request.data), headers={'Cache-Control': 'no-store, private'})

    business_approval_actions = {'approve'}
    """
    ViewSet for Quote/Proposal Management
    
    🔐 SECURITY: Requires 'sales' module access (soft-coded from rbac_config.py)
    """
    
    queryset = Quote.objects.select_related('client', 'deal', 'prepared_by').order_by('-created_at', '-id')
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'sales'
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['status', 'client', 'deal']
    search_fields = ['quote_number', 'client__company_name', 'deal__deal_name']

    def get_queryset(self):
        from .bid_preparation import visible_deals
        return super().get_queryset().filter(deal__in=visible_deals(self.request.user))

    @action(detail=False, methods=['post'], url_path='export')
    def export(self, request):
        from .proposal_export import proposal_export_ids, proposal_export_response

        if not all(module_action_allowed(request.user, 'sales_proposals', verb)
                   for verb in ('read', 'export')) or not module_action_allowed(
                       request.user, 'sales_opportunities', 'read'):
            raise PermissionDenied('You do not have access to export proposals.')
        if request.query_params:
            raise ValidationError({'ids': 'Send the selected proposal IDs in the request body.'})
        identifiers = proposal_export_ids(request.data)
        return proposal_export_response(self.get_queryset(), identifiers, request.user)

    @action(detail=True, methods=['get'], url_path='review')
    def review(self, request, pk=None):
        from .proposal_review import review_projection
        return Response(review_projection(pk, request.user, request.query_params.get('document_id'),
                                          request.query_params.get('comments_cursor')),
                        headers={'Cache-Control': 'no-store, private'})

    @action(detail=True, methods=['post'], url_path='review/documents')
    def review_bind(self, request, pk=None):
        from .proposal_review import review_command
        result, created = review_command(pk, request.user, 'bind', request.data)
        return Response(result, status=201 if created else 200, headers={'Cache-Control': 'no-store, private'})

    def _review_pdf(self, request, pk, document_id):
        from django.http import FileResponse
        from .proposal_review import review_content
        content, name = review_content(pk, request.user, document_id)
        response = FileResponse(content, as_attachment=True, filename=name, content_type='application/pdf')
        response['Cache-Control'] = 'no-store, private'
        response['X-Content-Type-Options'] = 'nosniff'
        response['Content-Security-Policy'] = "sandbox; default-src 'none'"
        return response

    @action(detail=True, methods=['get'], url_path=r'review/documents/(?P<document_id>[^/]+)/content')
    def review_content(self, request, pk=None, document_id=None):
        return self._review_pdf(request, pk, document_id)

    @action(detail=True, methods=['get'], url_path=r'review/documents/(?P<document_id>[^/]+)/download')
    def review_download(self, request, pk=None, document_id=None):
        return self._review_pdf(request, pk, document_id)

    @action(detail=True, methods=['post'], url_path=r'review/documents/(?P<document_id>[^/]+)/comments')
    def review_comment(self, request, pk=None, document_id=None):
        from .proposal_review import review_command
        result, created = review_command(pk, request.user, 'comment', request.data, document_id)
        return Response(result, status=201 if created else 200, headers={'Cache-Control': 'no-store, private'})

    @action(detail=True, methods=['post'], url_path=r'review/documents/(?P<document_id>[^/]+)/comments/(?P<comment_id>[^/]+)/resolve')
    def review_resolve(self, request, pk=None, document_id=None, comment_id=None):
        from .proposal_review import review_command
        result, _ = review_command(pk, request.user, 'resolve', request.data, document_id, comment_id)
        return Response(result, headers={'Cache-Control': 'no-store, private'})

    @action(detail=True, methods=['post'], url_path=r'review/documents/(?P<document_id>[^/]+)/submit')
    def review_submit(self, request, pk=None, document_id=None):
        from .proposal_review import review_command
        result, created = review_command(pk, request.user, 'submit', request.data, document_id)
        return Response(result, status=201 if created else 200, headers={'Cache-Control': 'no-store, private'})
    
    def get_serializer_class(self):
        if self.action in ['retrieve', 'create', 'update', 'partial_update']:
            return QuoteDetailSerializer
        return QuoteListSerializer
    
    @transaction.atomic
    def perform_create(self, serializer):
        """Set prepared_by to current user"""
        deal = Deal.objects.select_for_update(no_key=True).get(pk=serializer.validated_data['deal'].pk)
        serializer.validated_data['deal'] = deal
        client = Client.objects.select_for_update(no_key=True).get(pk=deal.client_id)
        if serializer.validated_data['client'].pk != client.pk:
            raise ValidationError({'client': 'Proposal client must match its opportunity.'})
        serializer.validated_data['client'] = client
        serializer.validate(serializer.validated_data)
        quote = serializer.save(prepared_by=self.request.user)
        _audit(
            quote.deal, self.request.user, 'proposal_revision_created',
            data={
                'proposal_id': str(quote.id),
                'proposal_number': quote.quote_number,
                'version': quote.version,
            },
        )

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        quote = Quote.objects.select_for_update().get(pk=self.get_object().pk)
        from .bid_preparation import require_editable
        require_editable(quote)
        if quote.review_documents.exists() and any(
                key in request.data and str(request.data[key]) != str(getattr(quote, key + '_id' if key in {'deal', 'client'} else key))
                for key in ('deal', 'client', 'version', 'quote_number')):
            return Response({'detail': 'Review evidence retains this proposal identity. Create a separate proposal revision.'},
                            status=status.HTTP_409_CONFLICT)
        if self.get_object().status in {'submitted', 'sent', 'viewed', 'won', 'lost'}:
            return Response(
                {'detail': 'Submitted proposal versions are immutable. Create a new revision.'},
                status=status.HTTP_409_CONFLICT,
            )
        serializer = self.get_serializer(quote, data=request.data, partial=kwargs.pop('partial', False))
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response(serializer.data)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        quote = Quote.objects.select_for_update().get(pk=self.get_object().pk)
        from .bid_preparation import quote_has_protected_evidence
        if quote.preparation_revisions.exists():
            return Response({'detail': 'This proposal retains preparation evidence and cannot be deleted.'},
                            status=status.HTTP_409_CONFLICT)
        if quote_has_protected_evidence(quote):
            return Response({'detail': 'This proposal retains approval or submission evidence and cannot be deleted.'},
                            status=status.HTTP_409_CONFLICT)
        if quote.review_documents.exists():
            return Response({'detail': 'This proposal has document review evidence and cannot be deleted.'},
                            status=status.HTTP_409_CONFLICT)
        if quote.status not in {'draft', 'cancelled'}:
            return Response(
                {'detail': 'Only draft or cancelled proposal versions may be deleted.'},
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def approve(self, request, pk=None):
        from rest_framework.exceptions import ValidationError
        quote = Quote.objects.select_for_update().get(pk=self.get_object().pk)
        from .bid_preparation import require_editable
        require_editable(quote)
        from apps.rbac.approval_eligibility import require_configured_approval
        require_configured_approval(request.user, 'sales_proposals', quote, 'approve')
        missing = [name for name, value in [
            ('scope', quote.scope), ('deliverables', quote.deliverables),
            ('estimated_hours', quote.estimated_hours), ('valid_until', quote.valid_until),
        ] if not value]
        if missing:
            raise ValidationError({'missing_fields': missing})
        if quote.total_amount <= 0:
            raise ValidationError({'total_amount': 'Proposed price must be greater than zero.'})
        quote.expected_margin_percent = (
            (quote.total_amount - quote.estimated_cost) / quote.total_amount * 100
        )
        quote.approved_by = request.user
        quote.approved_at = timezone.now()
        quote.status = 'ready_to_submit'
        history = list(quote.approval_history)
        history.append({
            'decision': 'approved', 'actor_id': str(request.user.id),
            'at': quote.approved_at.isoformat(), 'comment': request.data.get('comment', ''),
        })
        quote.approval_history = history
        quote.save()
        _audit(
            quote.deal, request.user, 'proposal_revision_approved',
            reason=request.data.get('comment', ''),
            data={
                'proposal_id': str(quote.id),
                'proposal_number': quote.quote_number,
                'version': quote.version,
            },
        )
        return Response(QuoteDetailSerializer(quote).data)
    
    @action(detail=True, methods=['post'])
    @transaction.atomic
    def send_to_client(self, request, pk=None):
        """
        Mark quote as sent to client
        """
        quote = Quote.objects.select_for_update().get(pk=self.get_object().pk)
        from rest_framework.exceptions import ValidationError
        if quote.status == 'submitted':
            raise ValidationError({'submission': 'This exact proposal version has already been submitted.'})
        if quote.status != 'ready_to_submit' or not quote.approved_at:
            raise ValidationError({'approval': 'Only an approved proposal version can be submitted.'})
        recipient = request.data.get('recipient', '').strip()
        if not recipient:
            raise ValidationError({'recipient': 'The submission recipient is required.'})
        import hashlib
        fingerprint = '|'.join([
            str(quote.id), str(quote.version), str(quote.total_amount),
            quote.currency, quote.approved_at.isoformat(),
        ])
        quote.submitted_version_hash = hashlib.sha256(fingerprint.encode()).hexdigest()
        quote.submission_recipient = recipient
        quote.submission_evidence = request.data.get('evidence', '')
        quote.status = 'submitted'
        quote.sent_date = timezone.now()
        quote.save(update_fields=[
            'submitted_version_hash', 'submission_recipient', 'submission_evidence',
            'status', 'sent_date', 'updated_at',
        ])
        _audit(
            quote.deal, request.user, 'proposal_revision_issued',
            data={
                'proposal_id': str(quote.id),
                'proposal_number': quote.quote_number,
                'version': quote.version,
                'recipient': recipient,
                'evidence': quote.submission_evidence,
            },
        )
        
        # Log activity
        SalesActivity.objects.create(
            client=quote.client,
            deal=quote.deal,
            activity_type='proposal',
            subject=f'Quote {quote.quote_number} sent',
            description=f'Quote sent to client - Total: {quote.total_amount} {quote.currency}',
            performed_by=request.user
        )
        
        return Response({
            'success': True,
            'quote_id': str(quote.id),
            'status': quote.status,
            'sent_date': quote.sent_date
        })
    
    @action(detail=True, methods=['post'])
    @transaction.atomic
    def mark_viewed(self, request, pk=None):
        """
        Mark quote as viewed by client
        """
        quote = Quote.objects.select_for_update().get(pk=self.get_object().pk)
        if quote.status not in {'submitted', 'sent', 'viewed'}:
            raise ValidationError({'status': 'Only a submitted proposal can be marked viewed.'})
        if not quote.viewed_date:
            quote.viewed_date = timezone.now()
        quote.status = 'viewed'
        quote.save()
        
        return Response({
            'success': True,
            'quote_id': str(quote.id),
            'viewed_date': quote.viewed_date
        })


class ProjectHandoverViewSet(viewsets.ModelViewSet):
    business_approval_actions = {'accept', 'return_for_correction'}
    """Formal delivery acceptance queue; records are initiated by approved awards."""

    queryset = ProjectHandover.objects.select_related(
        'opportunity__client', 'proposal', 'owner', 'project_manager', 'accepted_by', 'project',
    )
    serializer_class = ProjectHandoverSerializer
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'sales'
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'owner', 'project_manager']
    search_fields = [
        'opportunity__deal_code', 'opportunity__deal_name',
        'opportunity__client__company_name', 'signed_contract_reference',
    ]
    ordering_fields = ['created_at', 'updated_at', 'contract_value']
    http_method_names = ['get', 'patch', 'post', 'head', 'options']

    def get_queryset(self):
        return super().get_queryset().filter(
            build_visibility_filter(self.request.user, 'sales', owner_field='opportunity__owner')
        )

    def create(self, request, *args, **kwargs):
        return Response(
            {'detail': 'Handovers are initiated automatically from independently approved awards.'},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def update(self, request, *args, **kwargs):
        if self.get_object().status in {'accepted', 'project_created', 'closed'}:
            return Response(
                {'detail': 'Accepted handover records are immutable.'},
                status=status.HTTP_409_CONFLICT,
            )
        return super().update(request, *args, **kwargs)

    @action(detail=True, methods=['post'], url_path='submit-for-acceptance')
    def submit_for_acceptance(self, request, pk=None):
        handover = submit_handover_for_acceptance(self.get_object(), request.user)
        return Response(self.get_serializer(handover).data)

    @action(detail=True, methods=['post'])
    def accept(self, request, pk=None):
        handover = decide_handover(
            self.get_object(), request.user, accepted=True,
            comment=request.data.get('comment', ''),
        )
        return Response(self.get_serializer(handover).data)

    @action(detail=True, methods=['post'])
    def return_for_correction(self, request, pk=None):
        handover = decide_handover(
            self.get_object(), request.user, accepted=False,
            comment=request.data.get('reason', ''),
        )
        return Response(self.get_serializer(handover).data)


class SalesActivityViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Sales Activity Tracking
    
    🔐 SECURITY: Requires 'sales' module access (soft-coded from rbac_config.py)
    """
    
    queryset = SalesActivity.objects.select_related('client', 'deal', 'contact', 'performed_by')
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'sales'
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['activity_type', 'client', 'deal', 'performed_by']
    search_fields = ['subject', 'description', 'outcome']
    ordering_fields = ['activity_date', 'created_at']
    ordering = ['-activity_date']

    def get_queryset(self):
        return super().get_queryset().filter(
            build_visibility_filter(self.request.user, 'sales', owner_field='performed_by')
            | build_visibility_filter(self.request.user, 'sales', owner_field='deal__owner')
            | build_visibility_filter(self.request.user, 'sales', owner_field='client__account_manager')
        ).distinct()
    
    def get_serializer_class(self):
        if self.action == 'retrieve':
            return SalesActivityDetailSerializer
        return SalesActivityListSerializer
    
    def perform_create(self, serializer):
        """Set performed_by to current user"""
        serializer.save(performed_by=self.request.user)
    
    @action(detail=False, methods=['get'])
    def my_activities(self, request):
        """
        Get activities performed by current user
        """
        days = int(request.query_params.get('days', 30))
        start_date = timezone.now() - timedelta(days=days)
        
        activities = self.get_queryset().filter(
            performed_by=request.user,
            activity_date__gte=start_date
        )
        
        serializer = self.get_serializer(activities, many=True)
        
        return Response({
            'success': True,
            'count': activities.count(),
            'period_days': days,
            'activities': serializer.data
        })
    
    @action(detail=False, methods=['get'])
    def upcoming(self, request):
        """
        Get upcoming activities (with follow-up dates)
        """
        activities = self.get_queryset().filter(
            follow_up_date__gte=timezone.now().date(),
            follow_up_date__lte=timezone.now().date() + timedelta(days=7)
        ).order_by('follow_up_date')
        
        serializer = self.get_serializer(activities, many=True)
        
        return Response({
            'success': True,
            'count': activities.count(),
            'activities': serializer.data
        })


# ==============================================================================
# FORECASTING & ANALYTICS VIEWSETS
# ==============================================================================

class SalesForecastViewSet(viewsets.ModelViewSet):
    business_approval_actions = {'approve'}
    """
    ViewSet for Sales Forecasting
    AI-powered revenue predictions
    
    🔐 SECURITY: Requires 'sales' module access (soft-coded from rbac_config.py)
    """
    
    queryset = SalesForecast.objects.all()
    serializer_class = SalesForecastSerializer
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'sales'
    ordering = ['-forecast_date']

    def get_queryset(self):
        return super().get_queryset().filter(
            build_visibility_filter(self.request.user, 'sales', owner_field='generated_by')
        )

    def update(self, request, *args, **kwargs):
        if self.get_object().status in {'approved', 'superseded'}:
            return Response(
                {'detail': 'Approved forecast snapshots are immutable.'},
                status=status.HTTP_409_CONFLICT,
            )
        return super().update(request, *args, **kwargs)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        forecast = self.get_object()
        from apps.rbac.approval_eligibility import require_configured_approval
        require_configured_approval(request.user, 'sales_forecasts', forecast, 'approve')
        if forecast.generated_by_id == request.user.id:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'approver': 'The forecast preparer cannot approve their own snapshot.'})
        SalesForecast.objects.filter(
            forecast_period=forecast.forecast_period, status='approved',
        ).exclude(pk=forecast.pk).update(status='superseded')
        forecast.status = 'approved'
        forecast.approved_by = request.user
        forecast.approved_at = timezone.now()
        forecast.save(update_fields=['status', 'approved_by', 'approved_at', 'updated_at'])
        return Response(self.get_serializer(forecast).data)
    
    @action(detail=False, methods=['post'])
    def generate_forecast(self, request):
        """
        Generate new AI-powered sales forecast
        """
        period = request.data.get('period')  # e.g., "2026-Q2" or "2026-03"
        historical_months = int(request.data.get('historical_months', 6))
        
        if not period:
            return Response({
                'success': False,
                'error': 'period parameter required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            # Generate forecast using AI service
            forecast_data = SalesAIService.generate_sales_forecast(period, historical_months)
            
            # Save to database
            forecast = SalesForecast.objects.create(
                forecast_period=forecast_data['forecast_period'],
                predicted_revenue=Decimal(str(forecast_data['predicted_revenue'])),
                confidence_level=forecast_data['confidence_level'],
                best_case=Decimal(str(forecast_data['best_case'])),
                worst_case=Decimal(str(forecast_data['worst_case'])),
                model_version=forecast_data['model_version'],
                training_data_points=forecast_data['training_data_points'],
                features_used=forecast_data['features_used'],
                forecast_by_stage=forecast_data['forecast_by_stage'],
                forecast_by_service=forecast_data['forecast_by_service'],
                top_deals_considered=forecast_data['top_deals_considered'],
                generated_by=request.user
            )
            
            return Response({
                'success': True,
                'forecast_id': str(forecast.id),
                'forecast': forecast_data,
                'insights': forecast_data.get('insights', [])
            })
        
        except ValidationError:
            raise
        except Exception as e:
            logger.error(f"Forecast generation error: {str(e)}")
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=True, methods=['post'])
    def update_actual(self, request, pk=None):
        """
        Update actual revenue for accuracy tracking
        """
        forecast = self.get_object()
        actual_revenue = request.data.get('actual_revenue')
        
        if not actual_revenue:
            return Response({
                'success': False,
                'error': 'actual_revenue required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        forecast.actual_revenue = Decimal(str(actual_revenue))
        
        # Calculate accuracy
        if forecast.predicted_revenue > 0:
            variance = abs(forecast.actual_revenue - forecast.predicted_revenue)
            forecast.accuracy = float(1 - (variance / forecast.predicted_revenue))
        
        forecast.save()
        
        return Response({
            'success': True,
            'forecast_id': str(forecast.id),
            'predicted_revenue': float(forecast.predicted_revenue),
            'actual_revenue': float(forecast.actual_revenue),
            'accuracy': forecast.accuracy,
            'variance': float(forecast.actual_revenue - forecast.predicted_revenue)
        })


class SalesDashboardViewSet(viewsets.ViewSet):
    """
    Sales Dashboard Analytics
    Comprehensive sales metrics and insights
    
    🔐 SECURITY: Requires 'sales' module access (soft-coded from rbac_config.py)
    """
    
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'sales'
    
    @action(detail=False, methods=['get'])
    def summary(self, request):
        """
        Get comprehensive sales dashboard summary
        """
        # Date ranges
        today = timezone.now().date()
        start_of_month = today.replace(day=1)
        
        # Client metrics
        clients = Client.objects.filter(
            build_visibility_filter(request.user, 'sales', owner_field='account_manager')
        )
        total_clients = clients.count()
        active_clients = clients.filter(status='active').count()
        
        # Deal metrics
        deals = Deal.objects.filter(
            build_visibility_filter(request.user, 'sales', owner_field='owner')
        )
        total_deals = deals.count()
        active_deals = deals.filter(stage__in=['lead', 'qualified', 'proposal', 'negotiation', 'award_pending']).count()
        
        # Pipeline value
        pipeline_value = _opportunity_total(deals.filter(
            stage__in=['lead', 'qualified', 'proposal', 'negotiation', 'award_pending'],
        ), 'weighted_value')
        
        # Won deals this month
        won_mtd = deals.filter(
            stage__in=['awarded', 'converted'],
            actual_close_date__gte=start_of_month
        ).aggregate(Sum('actual_value'))['actual_value__sum'] or Decimal('0')
        
        # Average deal size
        avg_deal_size = deals.aggregate(Avg('estimated_value'))['estimated_value__avg'] or Decimal('0')
        if deals.filter(Q(estimated_value__isnull=True) | Q(currency='')).exists():
            avg_deal_size = None
        
        # Win rate
        closed_deals = deals.filter(stage__in=['awarded', 'converted', 'lost'])
        won_deals = closed_deals.filter(stage__in=['awarded', 'converted']).count()
        win_rate = (won_deals / closed_deals.count() * 100) if closed_deals.count() > 0 else 0
        
        # Average sales cycle
        won_with_dates = deals.filter(
            stage__in=['awarded', 'converted'],
            actual_close_date__isnull=False
        )
        if won_with_dates.exists():
            avg_days = sum([
                (deal.actual_close_date - deal.created_at.date()).days
                for deal in won_with_dates
            ]) / won_with_dates.count()
            avg_sales_cycle_days = int(avg_days)
        else:
            avg_sales_cycle_days = 0
        
        # Top clients
        top_clients = clients.order_by('-lifetime_value')[:5]
        
        # Top deals
        top_deals = deals.exclude(stage__in=['lost', 'no_bid', 'cancelled']).order_by(F('weighted_value').desc(nulls_last=True))[:5]
        
        # Recent activities
        recent_activities = SalesActivity.objects.filter(
            build_visibility_filter(request.user, 'sales', owner_field='performed_by')
            | build_visibility_filter(request.user, 'sales', owner_field='deal__owner')
            | build_visibility_filter(request.user, 'sales', owner_field='client__account_manager')
        ).distinct().order_by('-activity_date')[:10]
        
        # Deals by stage
        from .models import DEAL_STAGES
        deals_by_stage = {}
        for stage_key, stage_info in DEAL_STAGES.items():
            count = deals.filter(stage=stage_key).count()
            if count > 0:
                deals_by_stage[stage_info['name']] = count
        
        # Revenue by industry
        revenue_by_industry = {}
        for client in clients:
            industry = client.get_industry_type_display()
            client_pipeline = _opportunity_total(deals.filter(
                client=client
            ).exclude(stage__in=['lost', 'no_bid', 'cancelled']), 'weighted_value')
            prior = revenue_by_industry.get(industry, 0)
            revenue_by_industry[industry] = None if prior is None or client_pipeline is None else prior + client_pipeline
        
        # Forecast next month
        try:
            next_month_forecast = SalesAIService.generate_sales_forecast('next_month', 6)
            forecast_next_month = next_month_forecast['predicted_revenue']
        except ValidationError:
            forecast_next_month = None
        except Exception:
            forecast_next_month = pipeline_value * 0.7 if pipeline_value is not None else None
        
        # Serialize data
        from .serializers import ClientListSerializer, DealListSerializer, SalesActivityListSerializer
        
        dashboard_data = {
            'total_clients': total_clients,
            'active_clients': active_clients,
            'total_deals': total_deals,
            'active_deals': active_deals,
            'pipeline_value': pipeline_value,
            'won_value_mtd': float(won_mtd),
            'avg_deal_size': float(avg_deal_size) if avg_deal_size is not None else None,
            'win_rate': round(win_rate, 1),
            'avg_sales_cycle_days': avg_sales_cycle_days,
            'top_clients': ClientListSerializer(top_clients, many=True).data,
            'top_deals': DealListSerializer(top_deals, many=True).data,
            'recent_activities': SalesActivityListSerializer(recent_activities, many=True).data,
            'deals_by_stage': deals_by_stage,
            'revenue_by_industry': revenue_by_industry,
            'forecast_next_month': forecast_next_month
        }
        
        return Response({
            'success': True,
            'dashboard': dashboard_data,
            'generated_at': timezone.now()
        })
    
    @action(detail=False, methods=['get'])
    def ai_insights(self, request):
        """
        Get AI-generated insights across all sales data
        """
        insights = []
        
        # Insight 1: At-risk clients
        visible_clients = Client.objects.filter(
            build_visibility_filter(request.user, 'sales', owner_field='account_manager')
        )
        visible_deals = Deal.objects.filter(
            build_visibility_filter(request.user, 'sales', owner_field='owner')
        )
        at_risk_clients = visible_clients.filter(
            Q(churn_risk='high') | Q(health_score__lt=40)
        ).count()
        
        if at_risk_clients > 0:
            insights.append({
                'type': 'churn_warning',
                'title': 'At-Risk Clients Detected',
                'description': f'{at_risk_clients} clients show high churn risk',
                'severity': 'high',
                'action': 'Review and engage at-risk clients immediately',
                'affected_count': at_risk_clients
            })
        
        # Insight 2: Stagnant deals
        stagnant_deals = visible_deals.filter(stage__in=['lead', 'qualified', 'proposal', 'negotiation', 'award_pending']).filter(
            updated_at__lt=timezone.now() - timedelta(days=30)
        ).count()
        
        if stagnant_deals > 0:
            insights.append({
                'type': 'stagnant_pipeline',
                'title': 'Stagnant Deals in Pipeline',
                'description': f'{stagnant_deals} deals inactive for 30+ days',
                'severity': 'medium',
                'action': 'Review and accelerate or close out inactive deals',
                'affected_count': stagnant_deals
            })
        
        # Insight 3: High-value opportunities
        high_value_deals = Deal.objects.filter(
            stage__in=['proposal', 'negotiation'],
            estimated_value__gt=500000
        ).count()
        
        if high_value_deals > 0:
            insights.append({
                'type': 'high_value_opportunity',
                'title': 'High-Value Deals in Progress',
                'description': f'{high_value_deals} deals over $500K in late stage',
                'severity': 'positive',
                'action': 'Focus resources on closing high-value deals',
                'affected_count': high_value_deals
            })
        
        # Insight 4: Win rate trend
        recent_closed = Deal.objects.filter(
            stage__in=['awarded', 'converted', 'lost'],
            actual_close_date__gte=timezone.now().date() - timedelta(days=90)
        )
        
        if recent_closed.count() > 5:
            win_rate = recent_closed.filter(stage__in=['awarded', 'converted']).count() / recent_closed.count() * 100
            
            if win_rate < 30:
                severity = 'high'
                message = 'Win rate below industry average'
            elif win_rate > 50:
                severity = 'positive'
                message = 'Win rate above industry average'
            else:
                severity = 'medium'
                message = 'Win rate within normal range'
            
            insights.append({
                'type': 'win_rate_analysis',
                'title': 'Win Rate Trend (90 days)',
                'description': f'{round(win_rate, 1)}% - {message}',
                'severity': severity,
                'action': 'Review lost deals for patterns' if win_rate < 30 else 'Maintain current strategies',
                'metric_value': round(win_rate, 1)
            })
        
        return Response({
            'success': True,
            'insights_count': len(insights),
            'insights': insights,
            'generated_at': timezone.now(),
            'ai_model_version': SalesAIService.AI_MODEL_VERSION
        })
