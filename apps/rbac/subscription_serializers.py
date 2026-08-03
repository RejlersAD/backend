"""
Subscription Serializers - DRF Serializers for Subscription Management
"""
from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.utils import timezone
from .subscription_models import (
    SubscriptionPlan, SubscriptionFeature, UserSubscription,
    UsageTracking, SubscriptionHistory, SubscriptionInvoice
)

User = get_user_model()


# ============================================================================
# SUBSCRIPTION PLAN SERIALIZERS
# ============================================================================

class SubscriptionPlanListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for plan listing"""
    price_display = serializers.SerializerMethodField()
    is_recommended = serializers.SerializerMethodField()
    active_subscribers = serializers.SerializerMethodField()
    
    class Meta:
        model = SubscriptionPlan
        fields = [
            'id', 'name', 'code', 'display_name', 'description',
            'plan_type', 'billing_cycle', 'price', 'price_display',
            'badge', 'color_scheme', 'icon', 'sort_order',
            'is_active', 'is_public', 'is_recommended',
            'active_subscribers', 'trial_days'
        ]
    
    def get_price_display(self, obj):
        return obj.get_price_display()
    
    def get_is_recommended(self, obj):
        return obj.badge in ['Popular', 'Best Value', 'Recommended']
    
    def get_active_subscribers(self, obj):
        return obj.subscriptions.filter(status='active').count()


class SubscriptionPlanDetailSerializer(serializers.ModelSerializer):
    """Detailed serializer for single plan view"""
    price_display = serializers.SerializerMethodField()
    features_list = serializers.SerializerMethodField()
    limits_summary = serializers.SerializerMethodField()
    active_subscribers = serializers.SerializerMethodField()
    
    class Meta:
        model = SubscriptionPlan
        fields = '__all__'
    
    def get_price_display(self, obj):
        return obj.get_price_display()
    
    def get_features_list(self, obj):
        """Format features as readable list"""
        features = obj.features or {}
        return [
            {
                'key': key,
                'value': value,
                'enabled': value if isinstance(value, bool) else True
            }
            for key, value in features.items()
        ]
    
    def get_limits_summary(self, obj):
        """Summarize all limits"""
        return {
            'users': obj.max_users or 'Unlimited',
            'storage': f"{obj.max_storage_gb} GB" if obj.max_storage_gb else 'Unlimited',
            'api_calls': obj.max_api_calls_per_day or 'Unlimited',
            'projects': obj.max_projects or 'Unlimited',
            'documents': obj.max_documents or 'Unlimited',
        }
    
    def get_active_subscribers(self, obj):
        return obj.subscriptions.filter(status='active').count()


class SubscriptionPlanCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating/updating plans"""
    
    class Meta:
        model = SubscriptionPlan
        exclude = ['created_at', 'updated_at']
    
    def validate(self, data):
        # Validate price for paid plans
        if data.get('plan_type') != 'free' and data.get('price', 0) <= 0:
            raise serializers.ValidationError({
                'price': 'Paid plans must have price > 0'
            })
        
        # Validate module codes if provided
        allowed_modules = data.get('allowed_modules', [])
        if allowed_modules and allowed_modules != 'ALL':
            from ..rbac.models import Module
            valid_codes = Module.objects.filter(
                code__in=allowed_modules,
                is_active=True
            ).values_list('code', flat=True)
            
            invalid = set(allowed_modules) - set(valid_codes)
            if invalid:
                raise serializers.ValidationError({
                    'allowed_modules': f'Invalid module codes: {invalid}'
                })
        
        return data


# ============================================================================
# SUBSCRIPTION FEATURE SERIALIZERS
# ============================================================================

class SubscriptionFeatureSerializer(serializers.ModelSerializer):
    """Serializer for subscription features"""
    
    class Meta:
        model = SubscriptionFeature
        fields = '__all__'


class SubscriptionFeatureListSerializer(serializers.ModelSerializer):
    """Lightweight feature list"""
    
    class Meta:
        model = SubscriptionFeature
        fields = [
            'id', 'name', 'code', 'description', 'feature_type',
            'category', 'icon', 'is_highlighted', 'unit'
        ]


# ============================================================================
# USER SUBSCRIPTION SERIALIZERS
# ============================================================================

class UserSubscriptionListSerializer(serializers.ModelSerializer):
    """List view for user subscriptions"""
    user_email = serializers.CharField(source='user.email', read_only=True)
    user_name = serializers.SerializerMethodField()
    plan_name = serializers.CharField(source='plan.display_name', read_only=True)
    plan_type = serializers.CharField(source='plan.plan_type', read_only=True)
    days_remaining = serializers.IntegerField(read_only=True)
    is_trial = serializers.BooleanField(read_only=True)
    is_expired = serializers.BooleanField(read_only=True)
    
    class Meta:
        model = UserSubscription
        fields = [
            'id', 'user', 'user_email', 'user_name', 'plan', 'plan_name',
            'plan_type', 'status', 'start_date', 'end_date', 'trial_end_date',
            'days_remaining', 'is_trial', 'is_expired', 'auto_renew',
            'created_at'
        ]
    
    def get_user_name(self, obj):
        return f"{obj.user.first_name} {obj.user.last_name}".strip() or obj.user.email


class UserSubscriptionDetailSerializer(serializers.ModelSerializer):
    """Detailed view of user subscription"""
    user_email = serializers.CharField(source='user.email', read_only=True)
    user_name = serializers.SerializerMethodField()
    plan_details = SubscriptionPlanDetailSerializer(source='plan', read_only=True)
    usage_summary = serializers.SerializerMethodField()
    effective_limits = serializers.SerializerMethodField()
    days_remaining = serializers.IntegerField(read_only=True)
    is_trial = serializers.BooleanField(read_only=True)
    is_expired = serializers.BooleanField(read_only=True)
    granted_by_name = serializers.SerializerMethodField()
    
    class Meta:
        model = UserSubscription
        fields = '__all__'
    
    def get_user_name(self, obj):
        return f"{obj.user.first_name} {obj.user.last_name}".strip() or obj.user.email
    
    def get_granted_by_name(self, obj):
        if obj.granted_by:
            return obj.granted_by.email
        return None
    
    def get_usage_summary(self, obj):
        """Get current usage statistics"""
        from django.db.models import Sum, Max
        
        # Get latest usage for each metric
        latest_usage = obj.usage_logs.values('metric_type').annotate(
            total=Sum('usage_count'),
            last_update=Max('period_end')
        )
        
        return {
            item['metric_type']: {
                'count': item['total'],
                'last_update': item['last_update']
            }
            for item in latest_usage
        }
    
    def get_effective_limits(self, obj):
        """Get effective limits (with custom overrides)"""
        return {
            'max_users': obj.get_limit('max_users') or 'Unlimited',
            'max_storage_gb': obj.get_limit('max_storage_gb') or 'Unlimited',
            'max_api_calls_per_day': obj.get_limit('max_api_calls_per_day') or 'Unlimited',
            'max_projects': obj.get_limit('max_projects') or 'Unlimited',
            'max_documents': obj.get_limit('max_documents') or 'Unlimited',
        }


class UserSubscriptionCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating subscriptions"""
    
    class Meta:
        model = UserSubscription
        fields = [
            'user', 'plan', 'start_date', 'end_date', 'trial_end_date',
            'auto_renew', 'custom_limits', 'custom_features', 'notes'
        ]
    
    def validate(self, data):
        # Check if user already has active subscription
        user = data.get('user')
        if UserSubscription.objects.filter(user=user, status='active').exists():
            raise serializers.ValidationError({
                'user': 'User already has an active subscription'
            })
        
        # Validate dates
        start_date = data.get('start_date', timezone.now())
        end_date = data.get('end_date')
        
        if end_date and start_date >= end_date:
            raise serializers.ValidationError({
                'end_date': 'End date must be after start date'
            })
        
        return data
    
    def create(self, validated_data):
        # Set granted_by from request context
        request = self.context.get('request')
        if request and request.user:
            validated_data['granted_by'] = request.user
        
        # Set default status
        if 'status' not in validated_data:
            validated_data['status'] = 'active'
        
        return super().create(validated_data)


class UserSubscriptionUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating subscriptions"""
    
    class Meta:
        model = UserSubscription
        fields = [
            'plan', 'status', 'end_date', 'auto_renew',
            'custom_limits', 'custom_features', 'notes'
        ]


# ============================================================================
# USAGE TRACKING SERIALIZERS
# ============================================================================

class UsageTrackingSerializer(serializers.ModelSerializer):
    """Serializer for usage tracking"""
    subscription_user = serializers.CharField(source='subscription.user.email', read_only=True)
    usage_percentage = serializers.FloatField(read_only=True)
    is_near_limit = serializers.SerializerMethodField()
    
    class Meta:
        model = UsageTracking
        fields = [
            'id', 'subscription', 'subscription_user', 'metric_type', 'period',
            'period_start', 'period_end', 'usage_count', 'usage_value',
            'limit_value', 'usage_percentage', 'is_over_limit',
            'is_near_limit', 'warning_sent', 'metadata', 'created_at'
        ]
        read_only_fields = ['usage_percentage', 'is_over_limit']
    
    def get_is_near_limit(self, obj):
        """Check if usage is near limit (>80%)"""
        return obj.usage_percentage >= 80


class UsageTrackingSummarySerializer(serializers.Serializer):
    """Summary of usage across all metrics"""
    metric_type = serializers.CharField()
    current_usage = serializers.IntegerField()
    limit = serializers.IntegerField(allow_null=True)
    percentage = serializers.FloatField()
    status = serializers.CharField()
    period = serializers.CharField()


# ============================================================================
# SUBSCRIPTION HISTORY SERIALIZERS
# ============================================================================

class SubscriptionHistorySerializer(serializers.ModelSerializer):
    """Serializer for subscription history/audit trail"""
    user_email = serializers.CharField(source='subscription.user.email', read_only=True)
    old_plan_name = serializers.CharField(source='old_plan.display_name', read_only=True, allow_null=True)
    new_plan_name = serializers.CharField(source='new_plan.display_name', read_only=True, allow_null=True)
    performed_by_email = serializers.CharField(source='performed_by.email', read_only=True, allow_null=True)
    
    class Meta:
        model = SubscriptionHistory
        fields = [
            'id', 'subscription', 'user_email', 'action', 'old_plan',
            'old_plan_name', 'new_plan', 'new_plan_name', 'performed_by',
            'performed_by_email', 'reason', 'changes', 'created_at',
            'ip_address'
        ]
        read_only_fields = fields


# ============================================================================
# SUBSCRIPTION INVOICE SERIALIZERS
# ============================================================================

class SubscriptionInvoiceSerializer(serializers.ModelSerializer):
    """Serializer for subscription invoices"""
    user_email = serializers.CharField(source='subscription.user.email', read_only=True)
    plan_name = serializers.CharField(source='subscription.plan.display_name', read_only=True)
    is_overdue = serializers.SerializerMethodField()
    
    class Meta:
        model = SubscriptionInvoice
        fields = [
            'id', 'invoice_number', 'subscription', 'user_email', 'plan_name',
            'subtotal', 'tax', 'discount', 'total', 'currency',
            'issue_date', 'due_date', 'paid_date', 'status',
            'payment_method', 'transaction_id', 'payment_gateway',
            'line_items', 'notes', 'is_overdue', 'created_at'
        ]
    
    def get_is_overdue(self, obj):
        """Check if invoice is overdue"""
        if obj.status in ['paid', 'cancelled', 'refunded']:
            return False
        return timezone.now().date() > obj.due_date


class SubscriptionInvoiceCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating invoices"""
    
    class Meta:
        model = SubscriptionInvoice
        fields = [
            'subscription', 'subtotal', 'tax', 'discount', 'total',
            'currency', 'issue_date', 'due_date', 'line_items', 'notes'
        ]
    
    def validate(self, data):
        # Auto-generate invoice number if not provided
        if not data.get('invoice_number'):
            # Generate unique invoice number
            from django.utils.crypto import get_random_string
            timestamp = timezone.now().strftime('%Y%m%d')
            random = get_random_string(6, allowed_chars='0123456789')
            data['invoice_number'] = f"INV-{timestamp}-{random}"
        
        # Validate amounts
        subtotal = data.get('subtotal', 0)
        tax = data.get('tax', 0)
        discount = data.get('discount', 0)
        total = data.get('total', 0)
        
        calculated_total = subtotal + tax - discount
        if abs(calculated_total - total) > 0.01:  # Allow small floating point diff
            raise serializers.ValidationError({
                'total': f'Total should be {calculated_total} (subtotal + tax - discount)'
            })
        
        return data


# ============================================================================
# DASHBOARD & ANALYTICS SERIALIZERS
# ============================================================================

class SubscriptionDashboardSerializer(serializers.Serializer):
    """Dashboard stats for subscription management"""
    total_subscriptions = serializers.IntegerField()
    active_subscriptions = serializers.IntegerField()
    trial_subscriptions = serializers.IntegerField()
    expired_subscriptions = serializers.IntegerField()
    total_revenue = serializers.DecimalField(max_digits=15, decimal_places=2)
    monthly_revenue = serializers.DecimalField(max_digits=15, decimal_places=2)
    churn_rate = serializers.FloatField()
    upgrade_rate = serializers.FloatField()
    plan_distribution = serializers.DictField()
    recent_activities = serializers.ListField()


class SubscriptionCheckSerializer(serializers.Serializer):
    """Response for subscription check/validation"""
    has_subscription = serializers.BooleanField()
    is_active = serializers.BooleanField()
    plan_code = serializers.CharField(allow_null=True)
    plan_name = serializers.CharField(allow_null=True)
    can_access_module = serializers.BooleanField()
    can_use_feature = serializers.BooleanField()
    message = serializers.CharField(allow_blank=True)
    upgrade_url = serializers.URLField(allow_null=True)


class PlanComparisonSerializer(serializers.Serializer):
    """Compare multiple plans side-by-side"""
    plans = SubscriptionPlanDetailSerializer(many=True)
    feature_matrix = serializers.DictField()
