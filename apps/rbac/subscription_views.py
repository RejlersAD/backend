"""
Subscription ViewSets - DRF ViewSets for Subscription Management
Enterprise-grade subscription APIs with soft-coded configuration
"""
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.utils import timezone
from django.db.models import Count, Sum, Q, Avg
from django.db.models.functions import TruncMonth
from django_filters.rest_framework import DjangoFilterBackend
from datetime import timedelta, date
from decimal import Decimal

from .subscription_models import (
    SubscriptionPlan, SubscriptionFeature, UserSubscription,
    UsageTracking, SubscriptionHistory, SubscriptionInvoice
)
from .subscription_serializers import (
    SubscriptionPlanListSerializer, SubscriptionPlanDetailSerializer,
    SubscriptionPlanCreateSerializer, SubscriptionFeatureSerializer,
    SubscriptionFeatureListSerializer, UserSubscriptionListSerializer,
    UserSubscriptionDetailSerializer, UserSubscriptionCreateSerializer,
    UserSubscriptionUpdateSerializer, UsageTrackingSerializer,
    UsageTrackingSummarySerializer, SubscriptionHistorySerializer,
    SubscriptionInvoiceSerializer, SubscriptionInvoiceCreateSerializer,
    SubscriptionDashboardSerializer, SubscriptionCheckSerializer,
    PlanComparisonSerializer
)
from .subscription_config import (
    SubscriptionRulesEngine, SubscriptionConfigManager,
    get_active_subscription, PLAN_TEMPLATES
)
from .permissions import IsSuperAdmin, IsAdmin
from .utils import create_audit_log
from .pagination import FlexiblePageNumberPagination


# ============================================================================
# SUBSCRIPTION PLAN VIEWSETS
# ============================================================================

class SubscriptionPlanViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing subscription plans
    
    🔐 Permissions:
    - List/Retrieve: Authenticated users (see available plans)
    - Create/Update/Delete: Super Admin only
    
    🎯 Features:
    - Soft-coded plan configuration
    - Public pricing page support
    - Plan comparison
    - Template-based creation
    """
    queryset = SubscriptionPlan.objects.all()
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter, DjangoFilterBackend]
    search_fields = ['name', 'display_name', 'description', 'code']
    ordering_fields = ['price', 'sort_order', 'created_at']
    ordering = ['sort_order', 'price']
    filterset_fields = ['plan_type', 'billing_cycle', 'is_active', 'is_public']
    pagination_class = FlexiblePageNumberPagination
    
    def get_serializer_class(self):
        if self.action == 'list':
            return SubscriptionPlanListSerializer
        elif self.action in ['create', 'update', 'partial_update']:
            return SubscriptionPlanCreateSerializer
        return SubscriptionPlanDetailSerializer
    
    def get_permissions(self):
        """Allow public access to active plans, admin for modifications"""
        if self.action in ['list', 'retrieve', 'public_plans', 'compare']:
            return [AllowAny()]
        return [IsAuthenticated(), IsSuperAdmin()]
    
    def get_queryset(self):
        """Filter based on user permissions"""
        queryset = super().get_queryset()
        
        # Non-admin users see only public active plans
        if not self.request.user.is_authenticated or \
           not getattr(self.request.user, 'userprofile', None) or \
           not self.request.user.userprofile.is_super_admin:
            queryset = queryset.filter(is_active=True, is_public=True)
        
        return queryset
    
    def perform_create(self, serializer):
        plan = serializer.save()
        create_audit_log(
            user=self.request.user,
            action='create',
            resource_type='SubscriptionPlan',
            resource_id=plan.id,
            resource_repr=str(plan),
            ip_address=self.request.META.get('REMOTE_ADDR'),
            user_agent=self.request.META.get('HTTP_USER_AGENT', '')
        )
    
    @action(detail=False, methods=['get'], permission_classes=[AllowAny])
    def public_plans(self, request):
        """Get public-facing plans for pricing page"""
        plans = self.queryset.filter(is_active=True, is_public=True).order_by('sort_order')
        serializer = SubscriptionPlanListSerializer(plans, many=True)
        return Response({
            'plans': serializer.data,
            'recommended_plan': next(
                (p for p in serializer.data if p.get('badge') in ['Popular', 'Recommended']),
                None
            )
        })
    
    @action(detail=False, methods=['post'], permission_classes=[AllowAny])
    def compare(self, request):
        """Compare multiple plans side-by-side"""
        plan_ids = request.data.get('plan_ids', [])
        
        if not plan_ids:
            return Response(
                {'error': 'plan_ids required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        plans = self.queryset.filter(id__in=plan_ids, is_active=True)
        
        # Build feature comparison matrix
        feature_matrix = {}
        for plan in plans:
            for feature_key, feature_value in (plan.features or {}).items():
                if feature_key not in feature_matrix:
                    feature_matrix[feature_key] = {}
                feature_matrix[feature_key][str(plan.id)] = feature_value
        
        serializer = SubscriptionPlanDetailSerializer(plans, many=True)
        
        return Response({
            'plans': serializer.data,
            'feature_matrix': feature_matrix,
            'comparison_date': timezone.now()
        })
    
    @action(detail=False, methods=['post'], permission_classes=[IsAuthenticated, IsSuperAdmin])
    def create_from_template(self, request):
        """Create plan from soft-coded template"""
        template_code = request.data.get('template_code')
        
        if not template_code:
            return Response(
                {'error': 'template_code required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        template = SubscriptionConfigManager.get_plan_template(template_code)
        
        if not template:
            return Response(
                {'error': f'Template {template_code} not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Check if plan already exists
        if SubscriptionPlan.objects.filter(code=template['code']).exists():
            return Response(
                {'error': 'Plan with this code already exists'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Create plan from template
        plan = SubscriptionPlan.objects.create(**template)
        
        create_audit_log(
            user=request.user,
            action='create',
            resource_type='SubscriptionPlan',
            resource_id=plan.id,
            resource_repr=str(plan),
            metadata={'template': template_code},
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', '')
        )
        
        serializer = SubscriptionPlanDetailSerializer(plan)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    
    @action(detail=False, methods=['get'], permission_classes=[IsAuthenticated, IsSuperAdmin])
    def templates(self, request):
        """Get available plan templates"""
        templates = SubscriptionConfigManager.get_all_plans()
        return Response({
            'templates': templates,
            'count': len(templates)
        })
    
    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated, IsSuperAdmin])
    def duplicate(self, request, pk=None):
        """Duplicate existing plan"""
        original_plan = self.get_object()
        
        # Create new code/name
        new_code = f"{original_plan.code}_copy"
        new_name = f"{original_plan.name} (Copy)"
        
        # Duplicate
        new_plan = SubscriptionPlan.objects.create(
            name=new_name,
            code=new_code,
            display_name=f"{original_plan.display_name} (Copy)",
            description=original_plan.description,
            plan_type=original_plan.plan_type,
            billing_cycle=original_plan.billing_cycle,
            price=original_plan.price,
            currency=original_plan.currency,
            trial_days=original_plan.trial_days,
            features=original_plan.features,
            max_users=original_plan.max_users,
            max_storage_gb=original_plan.max_storage_gb,
            max_api_calls_per_day=original_plan.max_api_calls_per_day,
            max_projects=original_plan.max_projects,
            max_documents=original_plan.max_documents,
            priority_level=original_plan.priority_level,
            support_level=original_plan.support_level,
            allowed_modules=original_plan.allowed_modules,
            is_active=False,  # Inactive by default
            is_public=False,
        )
        
        serializer = SubscriptionPlanDetailSerializer(new_plan)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


# ============================================================================
# SUBSCRIPTION FEATURE VIEWSETS
# ============================================================================

class SubscriptionFeatureViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing subscription features
    """
    queryset = SubscriptionFeature.objects.all()
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter, DjangoFilterBackend]
    search_fields = ['name', 'code', 'description']
    ordering_fields = ['sort_order', 'category', 'name']
    ordering = ['category', 'sort_order']
    filterset_fields = ['feature_type', 'category', 'is_active']
    
    def get_serializer_class(self):
        if self.action == 'list':
            return SubscriptionFeatureListSerializer
        return SubscriptionFeatureSerializer
    
    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsSuperAdmin()]
    
    @action(detail=False, methods=['get'])
    def by_category(self, request):
        """Get features grouped by category"""
        features = self.queryset.filter(is_active=True)
        categories = features.values_list('category', flat=True).distinct()
        
        result = {}
        for category in categories:
            cat_features = features.filter(category=category)
            serializer = SubscriptionFeatureListSerializer(cat_features, many=True)
            result[category] = serializer.data
        
        return Response(result)


# ============================================================================
# USER SUBSCRIPTION VIEWSETS
# ============================================================================

class UserSubscriptionViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing user subscriptions
    
    🔐 Permissions:
    - Users can view their own subscriptions
    - Admins can manage all subscriptions
    
    🎯 Features:
    - Soft-coded subscription enforcement
    - Usage tracking integration
    - Auto-renewal management
    - Upgrade/downgrade workflows
    """
    queryset = UserSubscription.objects.select_related('user', 'plan', 'granted_by')
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter, DjangoFilterBackend]
    search_fields = ['user__email', 'user__first_name', 'user__last_name', 'plan__name']
    ordering_fields = ['start_date', 'end_date', 'created_at']
    ordering = ['-created_at']
    filterset_fields = ['status', 'plan', 'auto_renew']
    pagination_class = FlexiblePageNumberPagination
    
    def get_serializer_class(self):
        if self.action == 'list':
            return UserSubscriptionListSerializer
        elif self.action == 'create':
            return UserSubscriptionCreateSerializer
        elif self.action in ['update', 'partial_update']:
            return UserSubscriptionUpdateSerializer
        return UserSubscriptionDetailSerializer
    
    def get_queryset(self):
        """Filter based on user permissions"""
        queryset = super().get_queryset()
        
        # Non-admin users see only their own subscriptions
        if not getattr(self.request.user, 'userprofile', None) or \
           not self.request.user.userprofile.is_admin:
            queryset = queryset.filter(user=self.request.user)
        
        return queryset
    
    def perform_create(self, serializer):
        subscription = serializer.save(granted_by=self.request.user)
        
        # Create history record
        SubscriptionHistory.objects.create(
            subscription=subscription,
            action='created',
            new_plan=subscription.plan,
            performed_by=self.request.user,
            reason='Initial subscription creation',
            ip_address=self.request.META.get('REMOTE_ADDR'),
            user_agent=self.request.META.get('HTTP_USER_AGENT', '')
        )
        
        create_audit_log(
            user=self.request.user,
            action='create',
            resource_type='UserSubscription',
            resource_id=subscription.id,
            resource_repr=str(subscription),
            ip_address=self.request.META.get('REMOTE_ADDR'),
            user_agent=self.request.META.get('HTTP_USER_AGENT', '')
        )
    
    @action(detail=False, methods=['get'])
    def my_subscription(self, request):
        """Get current user's active subscription"""
        subscription = get_active_subscription(request.user)
        
        if not subscription:
            return Response({
                'has_subscription': False,
                'message': 'No active subscription found'
            })
        
        serializer = UserSubscriptionDetailSerializer(subscription)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def upgrade(self, request, pk=None):
        """Upgrade subscription to higher plan"""
        subscription = self.get_object()
        new_plan_id = request.data.get('new_plan_id')
        
        if not new_plan_id:
            return Response(
                {'error': 'new_plan_id required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            new_plan = SubscriptionPlan.objects.get(id=new_plan_id, is_active=True)
        except SubscriptionPlan.DoesNotExist:
            return Response(
                {'error': 'Plan not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Validate upgrade (price should be higher)
        if new_plan.price <= subscription.plan.price:
            return Response(
                {'error': 'New plan must have higher price for upgrade'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        old_plan = subscription.plan
        subscription.plan = new_plan
        subscription.save()
        
        # Record history
        SubscriptionHistory.objects.create(
            subscription=subscription,
            action='upgraded',
            old_plan=old_plan,
            new_plan=new_plan,
            performed_by=request.user,
            reason=request.data.get('reason', 'User initiated upgrade'),
            changes={
                'old_price': float(old_plan.price),
                'new_price': float(new_plan.price),
                'price_diff': float(new_plan.price - old_plan.price)
            },
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', '')
        )
        
        serializer = UserSubscriptionDetailSerializer(subscription)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def downgrade(self, request, pk=None):
        """Downgrade subscription to lower plan"""
        subscription = self.get_object()
        new_plan_id = request.data.get('new_plan_id')
        
        if not new_plan_id:
            return Response(
                {'error': 'new_plan_id required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            new_plan = SubscriptionPlan.objects.get(id=new_plan_id, is_active=True)
        except SubscriptionPlan.DoesNotExist:
            return Response(
                {'error': 'Plan not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        old_plan = subscription.plan
        subscription.plan = new_plan
        subscription.save()
        
        # Record history
        SubscriptionHistory.objects.create(
            subscription=subscription,
            action='downgraded',
            old_plan=old_plan,
            new_plan=new_plan,
            performed_by=request.user,
            reason=request.data.get('reason', 'User initiated downgrade'),
            changes={
                'old_price': float(old_plan.price),
                'new_price': float(new_plan.price),
                'price_diff': float(old_plan.price - new_plan.price)
            },
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', '')
        )
        
        serializer = UserSubscriptionDetailSerializer(subscription)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """Cancel subscription"""
        subscription = self.get_object()
        reason = request.data.get('reason', '')
        
        subscription.cancel(reason=reason)
        
        # Record history
        SubscriptionHistory.objects.create(
            subscription=subscription,
            action='cancelled',
            performed_by=request.user,
            reason=reason,
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', '')
        )
        
        return Response({'message': 'Subscription cancelled successfully'})
    
    @action(detail=True, methods=['post'])
    def renew(self, request, pk=None):
        """Renew subscription"""
        subscription = self.get_object()
        billing_cycle = request.data.get('billing_cycle')
        
        subscription.renew(billing_cycle=billing_cycle)
        
        # Record history
        SubscriptionHistory.objects.create(
            subscription=subscription,
            action='renewed',
            performed_by=request.user,
            reason='Subscription renewed',
            changes={'new_end_date': subscription.end_date.isoformat()},
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', '')
        )
        
        serializer = UserSubscriptionDetailSerializer(subscription)
        return Response(serializer.data)
    
    @action(detail=True, methods=['get'])
    def usage(self, request, pk=None):
        """Get usage statistics for subscription"""
        subscription = self.get_object()
        
        # Get current period usage
        current_period_start = date.today().replace(day=1)
        usage_logs = subscription.usage_logs.filter(
            period_start__gte=current_period_start
        )
        
        serializer = UsageTrackingSerializer(usage_logs, many=True)
        
        return Response({
            'subscription_id': subscription.id,
            'period': 'current_month',
            'usage': serializer.data
        })
    
    @action(detail=True, methods=['post'])
    def check_limit(self, request, pk=None):
        """Check if specific action is allowed"""
        subscription = self.get_object()
        action = request.data.get('action')  # 'create_project', 'upload_file', 'add_user'
        
        if action == 'create_project':
            allowed, message = SubscriptionRulesEngine.can_create_project(subscription)
        elif action == 'upload_file':
            file_size = request.data.get('file_size_mb', 0)
            allowed, message = SubscriptionRulesEngine.can_upload_file(subscription, file_size)
        elif action == 'add_user':
            allowed, message = SubscriptionRulesEngine.can_add_user(subscription)
        else:
            return Response(
                {'error': 'Invalid action'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        return Response({
            'allowed': allowed,
            'message': message,
            'action': action
        })


# ============================================================================
# USAGE TRACKING VIEWSETS
# ============================================================================

class UsageTrackingViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for viewing usage tracking data
    Read-only - usage is tracked automatically
    """
    queryset = UsageTracking.objects.select_related('subscription', 'subscription__user', 'subscription__plan')
    serializer_class = UsageTrackingSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.OrderingFilter, DjangoFilterBackend]
    ordering_fields = ['period_start', 'usage_count', 'created_at']
    ordering = ['-period_start']
    filterset_fields = ['metric_type', 'period', 'is_over_limit']
    pagination_class = FlexiblePageNumberPagination
    
    def get_queryset(self):
        """Filter based on user permissions"""
        queryset = super().get_queryset()
        
        # Non-admin users see only their own usage
        if not getattr(self.request.user, 'userprofile', None) or \
           not self.request.user.userprofile.is_admin:
            queryset = queryset.filter(subscription__user=self.request.user)
        
        return queryset
    
    @action(detail=False, methods=['get'])
    def summary(self, request):
        """Get usage summary for current user"""
        subscription = get_active_subscription(request.user)
        
        if not subscription:
            return Response({'message': 'No active subscription'})
        
        # Get current month usage
        current_month = date.today().replace(day=1)
        usage_logs = UsageTracking.objects.filter(
            subscription=subscription,
            period_start__gte=current_month
        )
        
        summary = []
        for log in usage_logs:
            summary.append({
                'metric_type': log.metric_type,
                'current_usage': log.usage_count,
                'limit': log.limit_value,
                'percentage': log.usage_percentage,
                'status': 'over_limit' if log.is_over_limit else 'ok'
            })
        
        return Response({
            'subscription': str(subscription),
            'period': 'current_month',
            'usage_summary': summary
        })


# ============================================================================
# SUBSCRIPTION HISTORY & AUDIT
# ============================================================================

class SubscriptionHistoryViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for subscription history/audit trail
    Read-only
    """
    queryset = SubscriptionHistory.objects.select_related(
        'subscription', 'old_plan', 'new_plan', 'performed_by'
    )
    serializer_class = SubscriptionHistorySerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.OrderingFilter, DjangoFilterBackend]
    ordering = ['-created_at']
    filterset_fields = ['action', 'subscription']
    pagination_class = FlexiblePageNumberPagination
    
    def get_queryset(self):
        """Filter based on user permissions"""
        queryset = super().get_queryset()
        
        # Non-admin users see only their own history
        if not getattr(self.request.user, 'userprofile', None) or \
           not self.request.user.userprofile.is_admin:
            queryset = queryset.filter(subscription__user=self.request.user)
        
        return queryset


# ============================================================================
# SUBSCRIPTION INVOICE VIEWSETS
# ============================================================================

class SubscriptionInvoiceViewSet(viewsets.ModelViewSet):
    """
    ViewSet for subscription invoices
    """
    queryset = SubscriptionInvoice.objects.select_related('subscription', 'subscription__user')
    serializer_class = SubscriptionInvoiceSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter, DjangoFilterBackend]
    search_fields = ['invoice_number', 'subscription__user__email']
    ordering_fields = ['issue_date', 'due_date', 'total']
    ordering = ['-issue_date']
    filterset_fields = ['status', 'subscription']
    pagination_class = FlexiblePageNumberPagination
    
    def get_serializer_class(self):
        if self.action == 'create':
            return SubscriptionInvoiceCreateSerializer
        return SubscriptionInvoiceSerializer
    
    def get_queryset(self):
        """Filter based on user permissions"""
        queryset = super().get_queryset()
        
        # Non-admin users see only their own invoices
        if not getattr(self.request.user, 'userprofile', None) or \
           not self.request.user.userprofile.is_admin:
            queryset = queryset.filter(subscription__user=self.request.user)
        
        return queryset
    
    @action(detail=True, methods=['post'])
    def mark_paid(self, request, pk=None):
        """Mark invoice as paid"""
        invoice = self.get_object()
        transaction_id = request.data.get('transaction_id', '')
        payment_method = request.data.get('payment_method', '')
        
        invoice.mark_paid(transaction_id=transaction_id, payment_method=payment_method)
        
        serializer = self.get_serializer(invoice)
        return Response(serializer.data)


# ============================================================================
# SUBSCRIPTION DASHBOARD & ANALYTICS
# ============================================================================

class SubscriptionDashboardViewSet(viewsets.ViewSet):
    """
    Dashboard and analytics for subscription management
    Admin only
    """
    permission_classes = [IsAuthenticated, IsAdmin]
    
    @action(detail=False, methods=['get'])
    def stats(self, request):
        """Get overall subscription statistics"""
        total_subs = UserSubscription.objects.count()
        active_subs = UserSubscription.objects.filter(status='active').count()
        trial_subs = UserSubscription.objects.filter(status='trial').count()
        expired_subs = UserSubscription.objects.filter(status='expired').count()
        
        # Revenue calculations
        active_subscriptions = UserSubscription.objects.filter(
            status='active'
        ).select_related('plan')
        
        total_revenue = sum(sub.plan.price for sub in active_subscriptions)
        
        # Monthly revenue (subscriptions ending this month)
        current_month = date.today().replace(day=1)
        next_month = (current_month + timedelta(days=32)).replace(day=1)
        monthly_revenue = UserSubscription.objects.filter(
            next_billing_date__gte=current_month,
            next_billing_date__lt=next_month,
            status='active'
        ).aggregate(total=Sum('plan__price'))['total'] or 0
        
        # Plan distribution
        plan_dist = UserSubscription.objects.filter(
            status='active'
        ).values('plan__name').annotate(count=Count('id'))
        
        return Response({
            'total_subscriptions': total_subs,
            'active_subscriptions': active_subs,
            'trial_subscriptions': trial_subs,
            'expired_subscriptions': expired_subs,
            'total_revenue': float(total_revenue),
            'monthly_revenue': float(monthly_revenue),
            'plan_distribution': {item['plan__name']: item['count'] for item in plan_dist}
        })
    
    @action(detail=False, methods=['get'])
    def revenue_trends(self, request):
        """Get revenue trends over time"""
        months = int(request.query_params.get('months', 6))
        
        # Get subscription history for upgrades
        history = SubscriptionHistory.objects.filter(
            action__in=['created', 'upgraded', 'renewed'],
            created_at__gte=timezone.now() - timedelta(days=months*30)
        ).annotate(
            month=TruncMonth('created_at')
        ).values('month').annotate(
            count=Count('id')
        ).order_by('month')
        
        return Response({
            'period': f'last_{months}_months',
            'data': list(history)
        })
