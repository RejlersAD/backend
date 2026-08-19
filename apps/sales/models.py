"""
Sales Management Models
Comprehensive CRM and Pipeline Management with AI-powered insights
"""

from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone
from apps.core.models import TimeStampedModel
import uuid
from decimal import Decimal

User = get_user_model()


# ==============================================================================
# SOFT-CODED CONFIGURATIONS
# ==============================================================================

# Industry Types for Client Classification
INDUSTRY_TYPES = {
    'oil_gas': {'name': 'Oil & Gas', 'icon': 'fire', 'color': 'red'},
    'petrochemical': {'name': 'Petrochemical', 'icon': 'beaker', 'color': 'purple'},
    'power_generation': {'name': 'Power & Utilities', 'icon': 'bolt', 'color': 'yellow'},
    'water_treatment': {'name': 'Water & Wastewater', 'icon': 'water', 'color': 'blue'},
    'manufacturing': {'name': 'Manufacturing', 'icon': 'cog', 'color': 'gray'},
    'construction': {'name': 'Construction & EPC', 'icon': 'building', 'color': 'orange'},
    'mining': {'name': 'Mining & Minerals', 'icon': 'cube', 'color': 'brown'},
    'pharmaceutical': {'name': 'Pharmaceutical', 'icon': 'pill', 'color': 'green'},
    'food_beverage': {'name': 'Food & Beverage', 'icon': 'shopping-cart', 'color': 'lime'},
    'government': {'name': 'Government & Public Sector', 'icon': 'shield', 'color': 'blue'},
    'other': {'name': 'Other', 'icon': 'ellipsis', 'color': 'gray'},
}

# Client Tiers for Segmentation
CLIENT_TIERS = {
    'platinum': {'name': 'Platinum', 'min_revenue': 10000000, 'color': 'gray-400', 'priority': 1},
    'gold': {'name': 'Gold', 'min_revenue': 5000000, 'color': 'yellow-400', 'priority': 2},
    'silver': {'name': 'Silver', 'min_revenue': 1000000, 'color': 'gray-300', 'priority': 3},
    'bronze': {'name': 'Bronze', 'min_revenue': 0, 'color': 'orange-400', 'priority': 4},
}

# Deal Stages (Sales Pipeline)
DEAL_STAGES = {
    'lead': {'name': 'Lead', 'probability': 10, 'color': 'gray', 'order': 1},
    'qualified': {'name': 'Qualified Lead', 'probability': 25, 'color': 'blue', 'order': 2},
    'proposal': {'name': 'Proposal Sent', 'probability': 50, 'color': 'purple', 'order': 3},
    'negotiation': {'name': 'Negotiation', 'probability': 75, 'color': 'yellow', 'order': 4},
    'closed_won': {'name': 'Closed Won', 'probability': 100, 'color': 'green', 'order': 5},
    'closed_lost': {'name': 'Closed Lost', 'probability': 0, 'color': 'red', 'order': 6},
}

# Service Categories
SERVICE_CATEGORIES = {
    'engineering_design': {'name': 'Engineering & Design', 'icon': 'pencil-ruler'},
    'project_management': {'name': 'Project Management', 'icon': 'clipboard-check'},
    'procurement': {'name': 'Procurement Services', 'icon': 'shopping-bag'},
    'construction': {'name': 'Construction & Installation', 'icon': 'hard-hat'},
    'commissioning': {'name': 'Commissioning & Startup', 'icon': 'play'},
    'maintenance': {'name': 'Maintenance & Support', 'icon': 'wrench'},
    'consulting': {'name': 'Consulting & Advisory', 'icon': 'lightbulb'},
    'training': {'name': 'Training & Development', 'icon': 'graduation-cap'},
    'inspection': {'name': 'Inspection & Testing', 'icon': 'search'},
    'software': {'name': 'Software & Digital Solutions', 'icon': 'computer'},
}


# ==============================================================================
# CLIENT MANAGEMENT MODELS
# ==============================================================================

class Client(TimeStampedModel):
    """
    Client/Customer Management (CRM)
    Central repository for all client information
    """
    
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('prospect', 'Prospect'),
        ('former', 'Former Client'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Basic Information
    client_code = models.CharField(max_length=50, unique=True, db_index=True)
    company_name = models.CharField(max_length=300, db_index=True)
    industry_type = models.CharField(max_length=50, choices=[(k, v['name']) for k, v in INDUSTRY_TYPES.items()])
    client_tier = models.CharField(max_length=20, choices=[(k, v['name']) for k, v in CLIENT_TIERS.items()], default='bronze')
    
    # Contact Information
    website = models.URLField(blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)
    
    # Business Details
    tax_id = models.CharField(max_length=100, blank=True)
    registration_number = models.CharField(max_length=100, blank=True)
    employee_count = models.IntegerField(null=True, blank=True)
    annual_revenue = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    
    # Relationship Management
    account_manager = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='managed_clients')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='prospect')
    acquired_date = models.DateField(null=True, blank=True)
    last_contact_date = models.DateField(null=True, blank=True)
    
    # AI-Powered Insights
    health_score = models.IntegerField(default=50, help_text='AI-calculated client health (0-100)')
    churn_risk = models.CharField(max_length=20, choices=[
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
    ], default='low')
    lifetime_value = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    
    # Metadata
    notes = models.TextField(blank=True)
    tags = models.JSONField(default=list, blank=True)  # ["vip", "high-potential", etc.]
    custom_fields = models.JSONField(default=dict, blank=True)  # Flexible custom data
    
    class Meta:
        db_table = 'sales_clients'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['client_code']),
            models.Index(fields=['company_name']),
            models.Index(fields=['industry_type']),
            models.Index(fields=['status']),
        ]
    
    def __str__(self):
        return f"{self.client_code} - {self.company_name}"
    
    def calculate_health_score(self):
        """AI-powered client health score calculation"""
        score = 50  # Base score
        
        # Recent activity boost
        if self.last_contact_date:
            days_since_contact = (timezone.now().date() - self.last_contact_date).days
            if days_since_contact < 30:
                score += 20
            elif days_since_contact < 90:
                score += 10
        
        # Active deals boost
        active_deals = self.deals.filter(stage__in=['qualified', 'proposal', 'negotiation']).count()
        score += min(active_deals * 10, 30)
        
        # Revenue contribution
        if self.lifetime_value > 1000000:
            score += 15
        elif self.lifetime_value > 500000:
            score += 10
        
        self.health_score = min(max(score, 0), 100)
        self.save(update_fields=['health_score'])
        return self.health_score


class Contact(TimeStampedModel):
    """
    Individual contacts within client organizations
    """
    
    ROLE_CHOICES = [
        ('decision_maker', 'Decision Maker'),
        ('influencer', 'Influencer'),
        ('user', 'End User'),
        ('technical', 'Technical Contact'),
        ('procurement', 'Procurement'),
        ('finance', 'Finance'),
        ('other', 'Other'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='contacts')
    
    # Personal Information
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    job_title = models.CharField(max_length=200, blank=True)
    department = models.CharField(max_length=100, blank=True)
    
    # Contact Details
    email = models.EmailField(db_index=True)
    phone = models.CharField(max_length=50, blank=True)
    mobile = models.CharField(max_length=50, blank=True)
    linkedin = models.URLField(blank=True)
    
    # Role & Relationship
    role_type = models.CharField(max_length=20, choices=ROLE_CHOICES, default='other')
    is_primary = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    
    # Metadata
    notes = models.TextField(blank=True)
    preferences = models.JSONField(default=dict, blank=True)  # Communication preferences, etc.
    
    class Meta:
        db_table = 'sales_contacts'
        ordering = ['-is_primary', 'last_name', 'first_name']
        unique_together = ['client', 'email']
    
    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.client.company_name})"
    
    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"


# ==============================================================================
# SALES PIPELINE MODELS
# ==============================================================================

class Deal(TimeStampedModel):
    """
    Sales opportunities and deals
    Tracks the entire sales pipeline
    """
    
    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('critical', 'Critical'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Deal Information
    deal_code = models.CharField(max_length=50, unique=True, db_index=True)
    deal_name = models.CharField(max_length=300)
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='deals')
    
    # Pipeline Management
    stage = models.CharField(max_length=20, choices=[(k, v['name']) for k, v in DEAL_STAGES.items()], default='lead')
    probability = models.IntegerField(default=10, help_text='Win probability (0-100%)')
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='medium')
    
    # Financial
    estimated_value = models.DecimalField(max_digits=15, decimal_places=2)
    weighted_value = models.DecimalField(max_digits=15, decimal_places=2, default=0)  # value * probability
    actual_value = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=10, default='USD')
    
    # Timeline
    expected_close_date = models.DateField()
    actual_close_date = models.DateField(null=True, blank=True)
    next_action_date = models.DateField(null=True, blank=True)
    
    # Ownership
    owner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='deals_owned')
    team_members = models.ManyToManyField(User, related_name='deals_team', blank=True)
    
    # Service Details
    service_categories = models.JSONField(default=list, blank=True)  # List of service category keys
    project_duration_months = models.IntegerField(null=True, blank=True)
    
    # AI Insights
    ai_win_probability = models.IntegerField(null=True, blank=True, help_text='AI-predicted win rate')
    ai_recommended_actions = models.JSONField(default=list, blank=True)
    competitor_analysis = models.JSONField(default=dict, blank=True)
    
    # Loss Analysis (if closed_lost)
    loss_reason = models.TextField(blank=True)
    lost_to_competitor = models.CharField(max_length=200, blank=True)
    
    # Metadata
    description = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    tags = models.JSONField(default=list, blank=True)
    custom_fields = models.JSONField(default=dict, blank=True)
    
    class Meta:
        db_table = 'sales_deals'
        ordering = ['-expected_close_date', '-estimated_value']
        indexes = [
            models.Index(fields=['deal_code']),
            models.Index(fields=['stage']),
            models.Index(fields=['client']),
            models.Index(fields=['expected_close_date']),
        ]
    
    def __str__(self):
        return f"{self.deal_code} - {self.deal_name}"
    
    def save(self, *args, **kwargs):
        # Auto-calculate weighted value
        self.weighted_value = (self.estimated_value * self.probability) / 100
        
        # Auto-set probability based on stage
        if self.stage in DEAL_STAGES:
            self.probability = DEAL_STAGES[self.stage]['probability']
        
        # Set actual close date when closed
        if self.stage in ['closed_won', 'closed_lost'] and not self.actual_close_date:
            self.actual_close_date = timezone.now().date()
        
        super().save(*args, **kwargs)


class Quote(TimeStampedModel):
    """
    Quotes/Proposals sent to clients
    """
    
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('sent', 'Sent to Client'),
        ('viewed', 'Viewed by Client'),
        ('accepted', 'Accepted'),
        ('rejected', 'Rejected'),
        ('expired', 'Expired'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Quote Information
    quote_number = models.CharField(max_length=50, unique=True, db_index=True)
    deal = models.ForeignKey(Deal, on_delete=models.CASCADE, related_name='quotes')
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='quotes')
    
    # Versions and Status
    version = models.IntegerField(default=1)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    
    # Financial
    subtotal = models.DecimalField(max_digits=15, decimal_places=2)
    tax_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=15, decimal_places=2)
    currency = models.CharField(max_length=10, default='USD')
    
    # Timeline
    issue_date = models.DateField(default=timezone.now)
    valid_until = models.DateField()
    sent_date = models.DateTimeField(null=True, blank=True)
    viewed_date = models.DateTimeField(null=True, blank=True)
    response_date = models.DateTimeField(null=True, blank=True)
    
    # Content
    line_items = models.JSONField(default=list, blank=True)
    terms_conditions = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    
    # Files
    pdf_file_path = models.CharField(max_length=500, blank=True)
    
    # Ownership
    prepared_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='quotes_prepared')
    
    class Meta:
        db_table = 'sales_quotes'
        ordering = ['-created_at']
        unique_together = ['quote_number', 'version']
    
    def __str__(self):
        return f"{self.quote_number} v{self.version} - {self.client.company_name}"


class SalesActivity(TimeStampedModel):
    """
    Track all sales activities and interactions
    """
    
    ACTIVITY_TYPE_CHOICES = [
        ('call', 'Phone Call'),
        ('email', 'Email'),
        ('meeting', 'Meeting'),
        ('demo', 'Demo/Presentation'),
        ('site_visit', 'Site Visit'),
        ('proposal', 'Proposal Submitted'),
        ('follow_up', 'Follow-up'),
        ('negotiation', 'Negotiation'),
        ('other', 'Other'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Relationships
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='activities')
    deal = models.ForeignKey(Deal, on_delete=models.CASCADE, null=True, blank=True, related_name='activities')
    contact = models.ForeignKey(Contact, on_delete=models.SET_NULL, null=True, blank=True, related_name='activities')
    
    # Activity Details
    activity_type = models.CharField(max_length=20, choices=ACTIVITY_TYPE_CHOICES)
    subject = models.CharField(max_length=300)
    description = models.TextField(blank=True)
    
    # Timeline
    activity_date = models.DateTimeField(default=timezone.now)
    duration_minutes = models.IntegerField(null=True, blank=True)
    
    # Ownership
    performed_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sales_activities')
    participants = models.ManyToManyField(User, related_name='participated_activities', blank=True)
    
    # Outcome
    outcome = models.TextField(blank=True)
    next_steps = models.TextField(blank=True)
    follow_up_date = models.DateField(null=True, blank=True)
    
    # AI Analysis
    sentiment_score = models.FloatField(null=True, blank=True, help_text='AI-analyzed sentiment (-1 to 1)')
    key_topics = models.JSONField(default=list, blank=True)
    
    class Meta:
        db_table = 'sales_activities'
        ordering = ['-activity_date']
        verbose_name_plural = 'Sales Activities'
    
    def __str__(self):
        return f"{self.get_activity_type_display()} - {self.client.company_name} - {self.activity_date.strftime('%Y-%m-%d')}"


class SalesForecast(TimeStampedModel):
    """
    AI-Generated Sales Forecasts
    Predictive analytics for revenue forecasting
    """
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Forecast Period
    forecast_period = models.CharField(max_length=50)  # e.g., "2026-Q1", "2026-02"
    forecast_date = models.DateField(default=timezone.now)
    
    # Predictions
    predicted_revenue = models.DecimalField(max_digits=15, decimal_places=2)
    confidence_level = models.FloatField(help_text='AI confidence (0-1)')
    best_case = models.DecimalField(max_digits=15, decimal_places=2)
    worst_case = models.DecimalField(max_digits=15, decimal_places=2)
    
    # Actual (for comparison)
    actual_revenue = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    accuracy = models.FloatField(null=True, blank=True)
    
    # AI Model Info
    model_version = models.CharField(max_length=50, default='v1.0')
    training_data_points = models.IntegerField(default=0)
    features_used = models.JSONField(default=list, blank=True)
    
    # Breakdown
    forecast_by_stage = models.JSONField(default=dict, blank=True)
    forecast_by_service = models.JSONField(default=dict, blank=True)
    top_deals_considered = models.JSONField(default=list, blank=True)
    
    # Generated by
    generated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    
    class Meta:
        db_table = 'sales_forecasts'
        ordering = ['-forecast_date']
        unique_together = ['forecast_period', 'forecast_date']
    
    def __str__(self):
        return f"Forecast {self.forecast_period} - ${self.predicted_revenue:,.2f}"
