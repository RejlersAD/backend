"""
Sales Management Views
DRF ViewSets with AI-powered actions
"""

from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated

# RBAC - Module-level access control (soft-coded)
from apps.rbac.permissions import HasModuleAccess, IsAdmin
from django.db.models import Q, Count, Sum, Avg, F
from django.utils import timezone
from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.shortcuts import redirect
from django_filters.rest_framework import DjangoFilterBackend
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode
import secrets

from .models import (
    Client, Contact, Deal, FrameworkAgreement, ProjectHandover, Quote,
    SalesActivity, SalesForecast, SalesMailboxConnection,
)
from .serializers import (
    ClientListSerializer, ClientDetailSerializer, ClientCreateSerializer,
    ContactSerializer, DealListSerializer, DealDetailSerializer, DealCreateSerializer,
    QuoteListSerializer, QuoteDetailSerializer, SalesActivityListSerializer,
    SalesActivityDetailSerializer, SalesForecastSerializer, SalesDashboardSerializer,
    AIInsightSerializer, FrameworkAgreementSerializer, ProjectHandoverSerializer,
    SalesMailboxConnectionSerializer,
)
from .ai_service import SalesAIService
from .microsoft_graph import SalesMicrosoftGraphService
from apps.rbac.data_visibility_mixin import TeamCollaborationMixin
from .workflow import (
    close_opportunity, convert_to_project, decide_award, enter_negotiation, record_bid_decision,
    decide_handover, submit_award, submit_handover_for_acceptance,
    submit_qualification,
)
import logging

logger = logging.getLogger(__name__)


class SalesMailboxConnectionViewSet(viewsets.ModelViewSet):
    """User-owned delegated Outlook connections and governed admin connections."""

    queryset = SalesMailboxConnection.objects.all()
    serializer_class = SalesMailboxConnectionSerializer
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'sales'

    def get_permissions(self):
        if self.action == 'oauth_callback':
            return [AllowAny()]
        return super().get_permissions()

    def _is_admin(self):
        return IsAdmin().has_permission(self.request, self)

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.query_params.get('mine', '').lower() in {'1', 'true', 'yes'}:
            return queryset.filter(created_by=self.request.user)
        if self._is_admin():
            return queryset
        return queryset.filter(created_by=self.request.user)

    def perform_create(self, serializer):
        auth_mode = serializer.validated_data.get('auth_mode', 'delegated')
        if not self._is_admin():
            auth_mode = 'delegated'
        serializer.save(
            auth_mode=auth_mode,
            created_by=self.request.user,
            updated_by=self.request.user,
        )

    def perform_update(self, serializer):
        auth_mode = serializer.validated_data.get('auth_mode', self.get_object().auth_mode)
        if not self._is_admin():
            auth_mode = 'delegated'
        serializer.save(auth_mode=auth_mode, updated_by=self.request.user)

    @action(detail=True, methods=['post'], url_path='test-connection')
    def test_connection(self, request, pk=None):
        result = SalesMicrosoftGraphService(self.get_object()).health_check()
        response_status = status.HTTP_200_OK if result['connected'] else status.HTTP_400_BAD_REQUEST
        return Response(result, status=response_status)

    @action(detail=True, methods=['post'], url_path='connect-outlook')
    def connect_outlook(self, request, pk=None):
        connection = self.get_object()
        if connection.auth_mode != 'delegated':
            connection.auth_mode = 'delegated'
            connection.save(update_fields=['auth_mode', 'updated_at'])
        nonce = secrets.token_urlsafe(32)
        state_payload = {
            'connection_id': str(connection.id),
            'user_id': str(request.user.id),
            'nonce': nonce,
        }
        cache.set(
            f'sales-graph-oauth:{nonce}',
            state_payload,
            timeout=600,
        )
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
            connection = SalesMailboxConnection.objects.get(
                id=payload['connection_id'],
                created_by_id=payload['user_id'],
            )
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
    
    queryset = Deal.objects.select_related('client', 'owner').prefetch_related('team_members')
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'sales'
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['stage', 'priority', 'client', 'owner']
    search_fields = ['deal_code', 'deal_name', 'client__company_name']
    ordering_fields = ['created_at', 'expected_close_date', 'estimated_value', 'weighted_value']
    ordering = ['-created_at']
    
    def get_serializer_class(self):
        if self.action == 'retrieve':
            return DealDetailSerializer
        elif self.action in ['create', 'update', 'partial_update']:
            return DealCreateSerializer
        return DealListSerializer
    
    def perform_create(self, serializer):
        """Set owner to current user if not specified"""
        if not serializer.validated_data.get('owner'):
            serializer.save(owner=self.request.user)
        else:
            serializer.save()

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
        return self._detail_response(submit_qualification(self.get_object(), request.user))

    @action(detail=True, methods=['post'], url_path='bid-decision')
    def bid_decision(self, request, pk=None):
        deal = record_bid_decision(
            self.get_object(), request.user,
            request.data.get('decision'), request.data.get('reason', ''),
        )
        return self._detail_response(deal)

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
            'estimated_value': float(deal.estimated_value),
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
            'total_value': float(pipeline.aggregate(Sum('estimated_value'))['estimated_value__sum'] or 0),
            'weighted_value': float(pipeline.aggregate(Sum('weighted_value'))['weighted_value__sum'] or 0),
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
                    'value': float(stage_deals.aggregate(Sum('estimated_value'))['estimated_value__sum'] or 0),
                    'weighted_value': float(stage_deals.aggregate(Sum('weighted_value'))['weighted_value__sum'] or 0)
                }
        
        # Group by priority
        for priority in ['critical', 'high', 'medium', 'low']:
            priority_deals = pipeline.filter(priority=priority)
            summary['by_priority'][priority] = {
                'count': priority_deals.count(),
                'value': float(priority_deals.aggregate(Sum('estimated_value'))['estimated_value__sum'] or 0)
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
    """
    ViewSet for Quote/Proposal Management
    
    🔐 SECURITY: Requires 'sales' module access (soft-coded from rbac_config.py)
    """
    
    queryset = Quote.objects.select_related('client', 'deal', 'prepared_by')
    permission_classes = [IsAuthenticated, HasModuleAccess]
    module_required = 'sales'
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['status', 'client', 'deal']
    search_fields = ['quote_number', 'client__company_name', 'deal__deal_name']
    
    def get_serializer_class(self):
        if self.action in ['retrieve', 'create', 'update', 'partial_update']:
            return QuoteDetailSerializer
        return QuoteListSerializer
    
    def perform_create(self, serializer):
        """Set prepared_by to current user"""
        serializer.save(prepared_by=self.request.user)

    def update(self, request, *args, **kwargs):
        if self.get_object().status in {'submitted', 'sent', 'viewed', 'won', 'lost'}:
            return Response(
                {'detail': 'Submitted proposal versions are immutable. Create a new revision.'},
                status=status.HTTP_409_CONFLICT,
            )
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        if self.get_object().status not in {'draft', 'cancelled'}:
            return Response(
                {'detail': 'Only draft or cancelled proposal versions may be deleted.'},
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        from rest_framework.exceptions import ValidationError
        quote = self.get_object()
        if quote.prepared_by_id == request.user.id:
            raise ValidationError({'approver': 'The proposal preparer cannot approve their own version.'})
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
        return Response(QuoteDetailSerializer(quote).data)
    
    @action(detail=True, methods=['post'])
    def send_to_client(self, request, pk=None):
        """
        Mark quote as sent to client
        """
        quote = self.get_object()
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
    def mark_viewed(self, request, pk=None):
        """
        Mark quote as viewed by client
        """
        quote = self.get_object()
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
        clients = Client.objects.all()
        total_clients = clients.count()
        active_clients = clients.filter(status='active').count()
        
        # Deal metrics
        deals = Deal.objects.all()
        total_deals = deals.count()
        active_deals = deals.filter(stage__in=['lead', 'qualified', 'proposal', 'negotiation', 'award_pending']).count()
        
        # Pipeline value
        pipeline_value = deals.filter(stage__in=['lead', 'qualified', 'proposal', 'negotiation', 'award_pending']).aggregate(
            Sum('weighted_value')
        )['weighted_value__sum'] or Decimal('0')
        
        # Won deals this month
        won_mtd = deals.filter(
            stage__in=['awarded', 'converted'],
            actual_close_date__gte=start_of_month
        ).aggregate(Sum('actual_value'))['actual_value__sum'] or Decimal('0')
        
        # Average deal size
        avg_deal_size = deals.aggregate(Avg('estimated_value'))['estimated_value__avg'] or Decimal('0')
        
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
        top_deals = deals.exclude(stage__in=['lost', 'no_bid', 'cancelled']).order_by('-weighted_value')[:5]
        
        # Recent activities
        recent_activities = SalesActivity.objects.order_by('-activity_date')[:10]
        
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
            client_pipeline = deals.filter(
                client=client
            ).exclude(stage__in=['lost', 'no_bid', 'cancelled']).aggregate(
                Sum('weighted_value')
            )['weighted_value__sum'] or Decimal('0')
            revenue_by_industry[industry] = revenue_by_industry.get(industry, 0) + float(client_pipeline)
        
        # Forecast next month
        try:
            next_month_forecast = SalesAIService.generate_sales_forecast('next_month', 6)
            forecast_next_month = next_month_forecast['predicted_revenue']
        except:
            forecast_next_month = float(pipeline_value) * 0.7  # Fallback estimate
        
        # Serialize data
        from .serializers import ClientListSerializer, DealListSerializer, SalesActivityListSerializer
        
        dashboard_data = {
            'total_clients': total_clients,
            'active_clients': active_clients,
            'total_deals': total_deals,
            'active_deals': active_deals,
            'pipeline_value': float(pipeline_value),
            'won_value_mtd': float(won_mtd),
            'avg_deal_size': float(avg_deal_size),
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
        at_risk_clients = Client.objects.filter(
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
        stagnant_deals = Deal.objects.filter(stage__in=['lead', 'qualified', 'proposal', 'negotiation', 'award_pending']).filter(
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
