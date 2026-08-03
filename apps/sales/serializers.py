"""
Sales Serializers
DRF serializers for Sales Management API
"""

from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import Client, Contact, Deal, Quote, SalesActivity, SalesForecast

User = get_user_model()


# ==============================================================================
# USER SERIALIZERS
# ==============================================================================

class UserBasicSerializer(serializers.ModelSerializer):
    """Basic user info for relationships"""
    full_name = serializers.SerializerMethodField()
    
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'full_name']
        read_only_fields = fields
    
    def get_full_name(self, obj):
        return f"{obj.first_name} {obj.last_name}".strip() or obj.username


# ==============================================================================
# CLIENT SERIALIZERS
# ==============================================================================

class ContactSerializer(serializers.ModelSerializer):
    """Contact serializer"""
    full_name = serializers.ReadOnlyField()
    
    class Meta:
        model = Contact
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']


class ClientListSerializer(serializers.ModelSerializer):
    """Lightweight client list serializer"""
    account_manager_name = serializers.CharField(source='account_manager.get_full_name', read_only=True)
    primary_contact = serializers.SerializerMethodField()
    active_deals_count = serializers.SerializerMethodField()
    total_deal_value = serializers.SerializerMethodField()
    
    class Meta:
        model = Client
        fields = [
            'id', 'client_code', 'company_name', 'industry_type', 'client_tier',
            'status', 'account_manager', 'account_manager_name', 'health_score',
            'churn_risk', 'lifetime_value', 'last_contact_date', 'created_at',
            'primary_contact', 'active_deals_count', 'total_deal_value', 'tags'
        ]
        read_only_fields = ['id', 'created_at', 'health_score']
    
    def get_primary_contact(self, obj):
        contact = obj.contacts.filter(is_primary=True).first()
        return contact.full_name if contact else None
    
    def get_active_deals_count(self, obj):
        return obj.deals.exclude(stage__in=['closed_won', 'closed_lost']).count()
    
    def get_total_deal_value(self, obj):
        from django.db.models import Sum
        total = obj.deals.exclude(stage='closed_lost').aggregate(Sum('estimated_value'))
        return total['estimated_value__sum'] or 0


class ClientDetailSerializer(serializers.ModelSerializer):
    """Detailed client serializer with all relationships"""
    account_manager_details = UserBasicSerializer(source='account_manager', read_only=True)
    contacts = ContactSerializer(many=True, read_only=True)
    recent_activities = serializers.SerializerMethodField()
    deals_summary = serializers.SerializerMethodField()
    
    class Meta:
        model = Client
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at', 'health_score', 'lifetime_value']
    
    def get_recent_activities(self, obj):
        activities = obj.activities.all()[:5]
        return SalesActivityListSerializer(activities, many=True).data
    
    def get_deals_summary(self, obj):
        from django.db.models import Sum, Count
        deals = obj.deals.all()
        return {
            'total_count': deals.count(),
            'active_count': deals.exclude(stage__in=['closed_won', 'closed_lost']).count(),
            'won_count': deals.filter(stage='closed_won').count(),
            'lost_count': deals.filter(stage='closed_lost').count(),
            'total_value': deals.aggregate(Sum('estimated_value'))['estimated_value__sum'] or 0,
            'pipeline_value': deals.exclude(stage__in=['closed_won', 'closed_lost']).aggregate(Sum('weighted_value'))['weighted_value__sum'] or 0,
        }


class ClientCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating clients"""
    
    class Meta:
        model = Client
        exclude = ['health_score', 'churn_risk', 'lifetime_value', 'created_at', 'updated_at']
    
    def create(self, validated_data):
        # Auto-generate client code if not provided
        if not validated_data.get('client_code'):
            from django.utils.crypto import get_random_string
            prefix = validated_data['company_name'][:3].upper()
            validated_data['client_code'] = f"CLT-{prefix}-{get_random_string(6, '0123456789')}"
        
        return super().create(validated_data)


# ==============================================================================
# DEAL SERIALIZERS
# ==============================================================================

class DealListSerializer(serializers.ModelSerializer):
    """Lightweight deal list serializer"""
    client_name = serializers.CharField(source='client.company_name', read_only=True)
    owner_name = serializers.CharField(source='owner.get_full_name', read_only=True)
    stage_display = serializers.SerializerMethodField()
    days_in_stage = serializers.SerializerMethodField()
    
    class Meta:
        model = Deal
        fields = [
            'id', 'deal_code', 'deal_name', 'client', 'client_name', 'stage',
            'stage_display', 'probability', 'priority', 'estimated_value',
            'weighted_value', 'currency', 'expected_close_date', 'owner',
            'owner_name', 'ai_win_probability', 'created_at', 'days_in_stage'
        ]
        read_only_fields = ['id', 'weighted_value', 'created_at']
    
    def get_stage_display(self, obj):
        from .models import DEAL_STAGES
        return DEAL_STAGES.get(obj.stage, {}).get('name', obj.stage)
    
    def get_days_in_stage(self, obj):
        from django.utils import timezone
        return (timezone.now().date() - obj.updated_at.date()).days


class DealDetailSerializer(serializers.ModelSerializer):
    """Detailed deal serializer"""
    client_details = ClientListSerializer(source='client', read_only=True)
    owner_details = UserBasicSerializer(source='owner', read_only=True)
    team_members_details = UserBasicSerializer(source='team_members', many=True, read_only=True)
    quotes = serializers.SerializerMethodField()
    activities = serializers.SerializerMethodField()
    stage_history = serializers.SerializerMethodField()
    
    class Meta:
        model = Deal
        fields = '__all__'
        read_only_fields = ['id', 'weighted_value', 'ai_win_probability', 'created_at', 'updated_at']
    
    def get_quotes(self, obj):
        quotes = obj.quotes.all()[:5]
        return QuoteListSerializer(quotes, many=True).data
    
    def get_activities(self, obj):
        activities = obj.activities.all()[:10]
        return SalesActivityListSerializer(activities, many=True).data
    
    def get_stage_history(self, obj):
        # This would track stage changes - implement with signals or audit log
        return []


class DealCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating deals"""
    
    class Meta:
        model = Deal
        exclude = ['weighted_value', 'ai_win_probability', 'ai_recommended_actions', 'created_at', 'updated_at']
    
    def create(self, validated_data):
        # Auto-generate deal code if not provided
        if not validated_data.get('deal_code'):
            from django.utils.crypto import get_random_string
            client_prefix = validated_data['client'].client_code[:3]
            validated_data['deal_code'] = f"DEAL-{client_prefix}-{get_random_string(6, '0123456789')}"
        
        # Extract team_members if provided
        team_members = validated_data.pop('team_members', [])
        
        deal = super().create(validated_data)
        
        if team_members:
            deal.team_members.set(team_members)
        
        return deal


# ==============================================================================
# QUOTE SERIALIZERS
# ==============================================================================

class QuoteListSerializer(serializers.ModelSerializer):
    """Lightweight quote list serializer"""
    client_name = serializers.CharField(source='client.company_name', read_only=True)
    deal_name = serializers.CharField(source='deal.deal_name', read_only=True)
    prepared_by_name = serializers.CharField(source='prepared_by.get_full_name', read_only=True)
    days_until_expiry = serializers.SerializerMethodField()
    
    class Meta:
        model = Quote
        fields = [
            'id', 'quote_number', 'version', 'deal', 'deal_name', 'client',
            'client_name', 'status', 'total_amount', 'currency', 'issue_date',
            'valid_until', 'prepared_by', 'prepared_by_name', 'created_at',
            'days_until_expiry'
        ]
        read_only_fields = ['id', 'created_at']
    
    def get_days_until_expiry(self, obj):
        from django.utils import timezone
        if obj.valid_until:
            delta = obj.valid_until - timezone.now().date()
            return delta.days
        return None


class QuoteDetailSerializer(serializers.ModelSerializer):
    """Detailed quote serializer"""
    client_details = ClientListSerializer(source='client', read_only=True)
    deal_details = DealListSerializer(source='deal', read_only=True)
    prepared_by_details = UserBasicSerializer(source='prepared_by', read_only=True)
    
    class Meta:
        model = Quote
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']


# ==============================================================================
# ACTIVITY SERIALIZERS
# ==============================================================================

class SalesActivityListSerializer(serializers.ModelSerializer):
    """Lightweight activity list serializer"""
    client_name = serializers.CharField(source='client.company_name', read_only=True)
    deal_name = serializers.CharField(source='deal.deal_name', read_only=True, allow_null=True)
    contact_name = serializers.CharField(source='contact.full_name', read_only=True, allow_null=True)
    performed_by_name = serializers.CharField(source='performed_by.get_full_name', read_only=True)
    activity_type_display = serializers.CharField(source='get_activity_type_display', read_only=True)
    
    class Meta:
        model = SalesActivity
        fields = [
            'id', 'activity_type', 'activity_type_display', 'subject', 'client',
            'client_name', 'deal', 'deal_name', 'contact', 'contact_name',
            'activity_date', 'duration_minutes', 'performed_by', 'performed_by_name',
            'outcome', 'follow_up_date', 'sentiment_score', 'created_at'
        ]
        read_only_fields = ['id', 'sentiment_score', 'created_at']


class SalesActivityDetailSerializer(serializers.ModelSerializer):
    """Detailed activity serializer"""
    client_details = ClientListSerializer(source='client', read_only=True)
    deal_details = DealListSerializer(source='deal', read_only=True, allow_null=True)
    contact_details = ContactSerializer(source='contact', read_only=True, allow_null=True)
    performed_by_details = UserBasicSerializer(source='performed_by', read_only=True)
    participants_details = UserBasicSerializer(source='participants', many=True, read_only=True)
    
    class Meta:
        model = SalesActivity
        fields = '__all__'
        read_only_fields = ['id', 'sentiment_score', 'key_topics', 'created_at', 'updated_at']


# ==============================================================================
# FORECAST SERIALIZERS
# ==============================================================================

class SalesForecastSerializer(serializers.ModelSerializer):
    """Sales forecast serializer"""
    generated_by_name = serializers.CharField(source='generated_by.get_full_name', read_only=True)
    variance = serializers.SerializerMethodField()
    
    class Meta:
        model = SalesForecast
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at', 'accuracy']
    
    def get_variance(self, obj):
        if obj.actual_revenue and obj.predicted_revenue:
            return float(obj.actual_revenue - obj.predicted_revenue)
        return None


# ==============================================================================
# DASHBOARD & ANALYTICS SERIALIZERS
# ==============================================================================

class SalesDashboardSerializer(serializers.Serializer):
    """Dashboard summary statistics"""
    total_clients = serializers.IntegerField()
    active_clients = serializers.IntegerField()
    total_deals = serializers.IntegerField()
    active_deals = serializers.IntegerField()
    pipeline_value = serializers.DecimalField(max_digits=15, decimal_places=2)
    won_value_mtd = serializers.DecimalField(max_digits=15, decimal_places=2)
    avg_deal_size = serializers.DecimalField(max_digits=15, decimal_places=2)
    win_rate = serializers.FloatField()
    avg_sales_cycle_days = serializers.IntegerField()
    top_clients = ClientListSerializer(many=True)
    top_deals = DealListSerializer(many=True)
    recent_activities = SalesActivityListSerializer(many=True)
    deals_by_stage = serializers.DictField()
    revenue_by_industry = serializers.DictField()
    forecast_next_month = serializers.DecimalField(max_digits=15, decimal_places=2)


class AIInsightSerializer(serializers.Serializer):
    """AI-generated insights"""
    insight_type = serializers.CharField()
    title = serializers.CharField()
    description = serializers.CharField()
    confidence = serializers.FloatField()
    recommendation = serializers.CharField()
    action_items = serializers.ListField(child=serializers.CharField())
    impact = serializers.CharField()
    related_entities = serializers.DictField()
