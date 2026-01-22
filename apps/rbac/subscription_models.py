"""
Subscription Management Models - Enterprise SaaS Subscription System
Dynamic, soft-coded subscription management with usage tracking
"""
import uuid
from decimal import Decimal
from django.db import models
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone
from apps.core.models import TimeStampedModel
from datetime import timedelta

User = get_user_model()


class SubscriptionPlan(TimeStampedModel):
    """
    Subscription plans (Free, Basic, Professional, Enterprise)
    Soft-coded configuration for easy customization
    """
    BILLING_CYCLE_CHOICES = [
        ('monthly', 'Monthly'),
        ('quarterly', 'Quarterly'),
        ('yearly', 'Yearly'),
        ('lifetime', 'Lifetime'),
    ]
    
    PLAN_TYPE_CHOICES = [
        ('free', 'Free'),
        ('trial', 'Trial'),
        ('basic', 'Basic'),
        ('professional', 'Professional'),
        ('enterprise', 'Enterprise'),
        ('custom', 'Custom'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=50, unique=True, help_text="Unique identifier for API/code")
    display_name = models.CharField(max_length=150, help_text="Customer-facing name")
    description = models.TextField(blank=True)
    
    # Plan Configuration
    plan_type = models.CharField(max_length=20, choices=PLAN_TYPE_CHOICES, default='basic')
    billing_cycle = models.CharField(max_length=20, choices=BILLING_CYCLE_CHOICES, default='monthly')
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, help_text="Price in USD")
    currency = models.CharField(max_length=3, default='USD')
    
    # Trial Configuration
    trial_days = models.IntegerField(default=0, help_text="Days of free trial (0 = no trial)")
    
    # Feature Limits (JSON for flexibility)
    features = models.JSONField(
        default=dict,
        help_text="Dynamic feature configuration: {'module_access': ['crs', 'qhse'], 'limits': {...}}"
    )
    
    # Usage Limits (Soft-coded)
    max_users = models.IntegerField(null=True, blank=True, help_text="Max users allowed (null = unlimited)")
    max_storage_gb = models.IntegerField(null=True, blank=True, help_text="Max storage in GB (null = unlimited)")
    max_api_calls_per_day = models.IntegerField(null=True, blank=True, help_text="Max API calls per day")
    max_projects = models.IntegerField(null=True, blank=True, help_text="Max concurrent projects")
    max_documents = models.IntegerField(null=True, blank=True, help_text="Max documents upload per month")
    
    # Priority & Support
    priority_level = models.IntegerField(default=3, help_text="1=Highest, 5=Lowest")
    support_level = models.CharField(
        max_length=50,
        default='email',
        help_text="email, chat, phone, dedicated"
    )
    
    # Module Access (Dynamic)
    allowed_modules = models.JSONField(
        default=list,
        help_text="List of module codes accessible in this plan"
    )
    
    # Status & Visibility
    is_active = models.BooleanField(default=True)
    is_public = models.BooleanField(default=True, help_text="Visible in public pricing page")
    is_default = models.BooleanField(default=False, help_text="Default plan for new users")
    
    # Display Properties
    badge = models.CharField(max_length=50, blank=True, help_text="e.g., 'Popular', 'Best Value'")
    color_scheme = models.CharField(max_length=50, default='blue', help_text="UI color theme")
    icon = models.CharField(max_length=50, blank=True)
    sort_order = models.IntegerField(default=0, help_text="Display order in UI")
    
    class Meta:
        db_table = 'rbac_subscription_plans'
        verbose_name = 'Subscription Plan'
        verbose_name_plural = 'Subscription Plans'
        ordering = ['sort_order', 'price']
        indexes = [
            models.Index(fields=['code']),
            models.Index(fields=['plan_type']),
            models.Index(fields=['is_active', 'is_public']),
        ]
    
    def __str__(self):
        return f"{self.display_name} - ${self.price}/{self.billing_cycle}"
    
    def clean(self):
        # Only one default plan allowed
        if self.is_default:
            existing_default = SubscriptionPlan.objects.filter(is_default=True).exclude(id=self.id)
            if existing_default.exists():
                raise ValidationError("Only one default plan can be active at a time")
    
    def get_feature(self, feature_key, default=None):
        """Get specific feature value from features JSON"""
        return self.features.get(feature_key, default)
    
    def has_module_access(self, module_code):
        """Check if plan includes specific module"""
        return module_code in self.allowed_modules
    
    def get_price_display(self):
        """Get formatted price display"""
        if self.price == 0:
            return "Free"
        return f"${self.price}/{self.billing_cycle}"


class SubscriptionFeature(TimeStampedModel):
    """
    Individual features that can be enabled/disabled per plan
    Soft-coded feature catalog for maximum flexibility
    """
    FEATURE_TYPE_CHOICES = [
        ('boolean', 'Boolean (On/Off)'),
        ('limit', 'Numeric Limit'),
        ('module', 'Module Access'),
        ('integration', 'Third-party Integration'),
        ('support', 'Support Level'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)
    feature_type = models.CharField(max_length=20, choices=FEATURE_TYPE_CHOICES)
    
    # Configuration
    default_value = models.JSONField(default=dict, help_text="Default value for this feature")
    unit = models.CharField(max_length=50, blank=True, help_text="e.g., 'GB', 'users', 'requests'")
    
    # Display
    icon = models.CharField(max_length=50, blank=True)
    category = models.CharField(max_length=50, default='general')
    is_highlighted = models.BooleanField(default=False, help_text="Show prominently in UI")
    sort_order = models.IntegerField(default=0)
    
    is_active = models.BooleanField(default=True)
    
    class Meta:
        db_table = 'rbac_subscription_features'
        verbose_name = 'Subscription Feature'
        verbose_name_plural = 'Subscription Features'
        ordering = ['category', 'sort_order', 'name']
        indexes = [
            models.Index(fields=['code']),
            models.Index(fields=['feature_type']),
        ]
    
    def __str__(self):
        return self.name


class UserSubscription(TimeStampedModel):
    """
    User subscription instances - tracks active subscriptions
    """
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('trial', 'Trial'),
        ('expired', 'Expired'),
        ('cancelled', 'Cancelled'),
        ('suspended', 'Suspended'),
        ('pending', 'Pending Payment'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='subscriptions')
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT, related_name='subscriptions')
    
    # Subscription Period
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    start_date = models.DateTimeField(default=timezone.now)
    end_date = models.DateTimeField(null=True, blank=True)
    trial_end_date = models.DateTimeField(null=True, blank=True)
    
    # Billing
    is_paid = models.BooleanField(default=False)
    payment_method = models.CharField(max_length=50, blank=True, help_text="stripe, paypal, invoice, etc.")
    last_payment_date = models.DateTimeField(null=True, blank=True)
    next_billing_date = models.DateTimeField(null=True, blank=True)
    
    # Auto-renewal
    auto_renew = models.BooleanField(default=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)
    
    # Custom overrides (for enterprise clients)
    custom_limits = models.JSONField(
        default=dict,
        blank=True,
        help_text="Override plan limits for specific customer"
    )
    custom_features = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional features not in base plan"
    )
    
    # Tracking
    granted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='granted_subscriptions',
        help_text="Admin who granted/modified this subscription"
    )
    
    # Metadata
    notes = models.TextField(blank=True, help_text="Internal notes about this subscription")
    metadata = models.JSONField(default=dict, blank=True, help_text="Additional data (stripe_id, etc.)")
    
    class Meta:
        db_table = 'rbac_user_subscriptions'
        verbose_name = 'User Subscription'
        verbose_name_plural = 'User Subscriptions'
        ordering = ['-start_date']
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['status', 'end_date']),
            models.Index(fields=['next_billing_date']),
        ]
    
    def __str__(self):
        return f"{self.user.email} - {self.plan.name} ({self.status})"
    
    def clean(self):
        # Validate dates
        if self.end_date and self.start_date >= self.end_date:
            raise ValidationError("End date must be after start date")
    
    @property
    def is_trial(self):
        """Check if subscription is in trial period"""
        if self.trial_end_date and timezone.now() < self.trial_end_date:
            return True
        return False
    
    @property
    def is_expired(self):
        """Check if subscription has expired"""
        if self.end_date and timezone.now() > self.end_date:
            return True
        return False
    
    @property
    def days_remaining(self):
        """Get days remaining in subscription"""
        if not self.end_date:
            return None
        delta = self.end_date - timezone.now()
        return max(0, delta.days)
    
    def get_limit(self, limit_key):
        """Get effective limit (custom override or plan default)"""
        # Check custom overrides first
        if limit_key in self.custom_limits:
            return self.custom_limits[limit_key]
        
        # Fall back to plan limit
        return getattr(self.plan, limit_key, None)
    
    def has_feature(self, feature_code):
        """Check if subscription includes specific feature"""
        # Check custom features first
        if feature_code in self.custom_features:
            return self.custom_features[feature_code]
        
        # Check plan features
        return self.plan.get_feature(feature_code, False)
    
    def renew(self, billing_cycle=None):
        """Renew subscription for another billing cycle"""
        if not billing_cycle:
            billing_cycle = self.plan.billing_cycle
        
        # Calculate new dates
        if billing_cycle == 'monthly':
            self.end_date = timezone.now() + timedelta(days=30)
        elif billing_cycle == 'quarterly':
            self.end_date = timezone.now() + timedelta(days=90)
        elif billing_cycle == 'yearly':
            self.end_date = timezone.now() + timedelta(days=365)
        
        self.next_billing_date = self.end_date
        self.status = 'active'
        self.save()
    
    def cancel(self, reason=''):
        """Cancel subscription"""
        self.status = 'cancelled'
        self.cancelled_at = timezone.now()
        self.cancellation_reason = reason
        self.auto_renew = False
        self.save()
    
    def suspend(self):
        """Suspend subscription (non-payment, violation, etc.)"""
        self.status = 'suspended'
        self.save()
    
    def reactivate(self):
        """Reactivate suspended subscription"""
        self.status = 'active'
        self.save()


class UsageTracking(TimeStampedModel):
    """
    Track feature usage to enforce subscription limits
    Aggregated daily/monthly for performance
    """
    METRIC_TYPE_CHOICES = [
        ('storage', 'Storage Usage'),
        ('api_calls', 'API Calls'),
        ('documents', 'Documents'),
        ('projects', 'Projects'),
        ('users', 'Active Users'),
        ('conversions', 'AI Conversions'),
        ('exports', 'Data Exports'),
    ]
    
    PERIOD_CHOICES = [
        ('daily', 'Daily'),
        ('monthly', 'Monthly'),
        ('yearly', 'Yearly'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subscription = models.ForeignKey(UserSubscription, on_delete=models.CASCADE, related_name='usage_logs')
    
    # Tracking
    metric_type = models.CharField(max_length=50, choices=METRIC_TYPE_CHOICES)
    period = models.CharField(max_length=20, choices=PERIOD_CHOICES, default='daily')
    period_start = models.DateField()
    period_end = models.DateField()
    
    # Usage Data
    usage_count = models.BigIntegerField(default=0, help_text="Number of times used")
    usage_value = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0,
        help_text="Numeric value (e.g., GB, bytes)"
    )
    limit_value = models.BigIntegerField(null=True, blank=True, help_text="Limit for this metric")
    
    # Status
    is_over_limit = models.BooleanField(default=False)
    warning_sent = models.BooleanField(default=False, help_text="Warning email sent to user")
    
    # Details
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional tracking data (per-module breakdown, etc.)"
    )
    
    class Meta:
        db_table = 'rbac_usage_tracking'
        verbose_name = 'Usage Tracking'
        verbose_name_plural = 'Usage Tracking'
        ordering = ['-period_start']
        unique_together = [['subscription', 'metric_type', 'period', 'period_start']]
        indexes = [
            models.Index(fields=['subscription', 'metric_type', 'period_start']),
            models.Index(fields=['is_over_limit']),
            models.Index(fields=['period_start', 'period_end']),
        ]
    
    def __str__(self):
        return f"{self.subscription.user.email} - {self.metric_type} ({self.period_start})"
    
    @property
    def usage_percentage(self):
        """Calculate usage percentage of limit"""
        if not self.limit_value or self.limit_value == 0:
            return 0
        return (float(self.usage_count) / float(self.limit_value)) * 100
    
    def check_limit(self):
        """Check if usage exceeds limit"""
        if self.limit_value and self.usage_count >= self.limit_value:
            self.is_over_limit = True
            self.save()
            return False
        return True
    
    def increment(self, count=1, value=0):
        """Increment usage counter"""
        self.usage_count += count
        self.usage_value += Decimal(str(value))
        self.check_limit()
        self.save()


class SubscriptionHistory(TimeStampedModel):
    """
    Audit trail for subscription changes
    """
    ACTION_CHOICES = [
        ('created', 'Created'),
        ('upgraded', 'Upgraded'),
        ('downgraded', 'Downgraded'),
        ('renewed', 'Renewed'),
        ('cancelled', 'Cancelled'),
        ('suspended', 'Suspended'),
        ('reactivated', 'Reactivated'),
        ('expired', 'Expired'),
        ('payment_success', 'Payment Success'),
        ('payment_failed', 'Payment Failed'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subscription = models.ForeignKey(UserSubscription, on_delete=models.CASCADE, related_name='history')
    action = models.CharField(max_length=50, choices=ACTION_CHOICES)
    
    # Change Details
    old_plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='history_old_plan'
    )
    new_plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='history_new_plan'
    )
    
    # Who & Why
    performed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='subscription_changes')
    reason = models.TextField(blank=True)
    
    # Metadata
    changes = models.JSONField(default=dict, help_text="Detailed change log")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    
    class Meta:
        db_table = 'rbac_subscription_history'
        verbose_name = 'Subscription History'
        verbose_name_plural = 'Subscription Histories'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['subscription', 'created_at']),
            models.Index(fields=['action']),
        ]
    
    def __str__(self):
        return f"{self.subscription.user.email} - {self.action} ({self.created_at.strftime('%Y-%m-%d')})"


class SubscriptionInvoice(TimeStampedModel):
    """
    Billing invoices for subscriptions
    """
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('pending', 'Pending'),
        ('paid', 'Paid'),
        ('failed', 'Failed'),
        ('refunded', 'Refunded'),
        ('cancelled', 'Cancelled'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice_number = models.CharField(max_length=50, unique=True)
    subscription = models.ForeignKey(UserSubscription, on_delete=models.CASCADE, related_name='invoices')
    
    # Amounts
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    tax = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='USD')
    
    # Dates
    issue_date = models.DateField(default=timezone.now)
    due_date = models.DateField()
    paid_date = models.DateTimeField(null=True, blank=True)
    
    # Status
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    
    # Payment Details
    payment_method = models.CharField(max_length=50, blank=True)
    transaction_id = models.CharField(max_length=100, blank=True)
    payment_gateway = models.CharField(max_length=50, blank=True, help_text="stripe, paypal, etc.")
    
    # Line Items
    line_items = models.JSONField(
        default=list,
        help_text="Invoice line items: [{'description': '...', 'amount': ...}]"
    )
    
    # Metadata
    notes = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    
    class Meta:
        db_table = 'rbac_subscription_invoices'
        verbose_name = 'Subscription Invoice'
        verbose_name_plural = 'Subscription Invoices'
        ordering = ['-issue_date']
        indexes = [
            models.Index(fields=['subscription', 'status']),
            models.Index(fields=['invoice_number']),
            models.Index(fields=['due_date']),
        ]
    
    def __str__(self):
        return f"Invoice {self.invoice_number} - {self.subscription.user.email}"
    
    def mark_paid(self, transaction_id='', payment_method=''):
        """Mark invoice as paid"""
        self.status = 'paid'
        self.paid_date = timezone.now()
        self.transaction_id = transaction_id
        self.payment_method = payment_method
        self.save()
