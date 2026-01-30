"""
Sales Admin Configuration
Django Admin interface for Sales Management
"""

from django.contrib import admin
from .models import Client, Contact, Deal, Quote, SalesActivity, SalesForecast


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ('client_code', 'company_name', 'industry_type', 'client_tier', 'health_score', 'churn_risk', 'status', 'created_at')
    list_filter = ('industry_type', 'client_tier', 'status', 'churn_risk', 'created_at')
    search_fields = ('client_code', 'company_name', 'email', 'website')
    readonly_fields = ('id', 'created_at', 'updated_at')
    raw_id_fields = ('account_manager',)


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'email', 'client', 'job_title', 'is_primary', 'created_at')
    list_filter = ('is_primary', 'role_type', 'is_active', 'created_at')
    search_fields = ('first_name', 'last_name', 'email', 'phone', 'client__company_name')
    readonly_fields = ('id', 'created_at', 'updated_at')
    raw_id_fields = ('client',)


@admin.register(Deal)
class DealAdmin(admin.ModelAdmin):
    list_display = ('deal_name', 'client', 'estimated_value', 'stage', 'ai_win_probability', 'expected_close_date', 'created_at')
    list_filter = ('stage', 'priority', 'created_at', 'expected_close_date')
    search_fields = ('deal_name', 'description', 'client__company_name')
    readonly_fields = ('id', 'created_at', 'updated_at')
    raw_id_fields = ('client', 'owner')


@admin.register(Quote)
class QuoteAdmin(admin.ModelAdmin):
    list_display = ('quote_number', 'deal', 'total_amount', 'status', 'valid_until', 'version', 'created_at')
    list_filter = ('status', 'created_at', 'valid_until')
    search_fields = ('quote_number', 'deal__deal_name', 'deal__client__company_name')
    readonly_fields = ('id', 'quote_number', 'created_at', 'updated_at')
    raw_id_fields = ('deal',)


@admin.register(SalesActivity)
class SalesActivityAdmin(admin.ModelAdmin):
    list_display = ('activity_type', 'subject', 'client', 'deal', 'activity_date', 'created_at')
    list_filter = ('activity_type', 'activity_date', 'created_at')
    search_fields = ('subject', 'notes', 'client__company_name', 'deal__deal_name')
    readonly_fields = ('id', 'created_at', 'updated_at')
    raw_id_fields = ('client', 'deal', 'contact')
    date_hierarchy = 'activity_date'


@admin.register(SalesForecast)
class SalesForecastAdmin(admin.ModelAdmin):
    list_display = ('forecast_period', 'forecast_date', 'predicted_revenue', 'actual_revenue', 'accuracy', 'created_at')
    list_filter = ('forecast_date', 'created_at')
    search_fields = ('forecast_period',)
    readonly_fields = ('id', 'created_at', 'updated_at')
    date_hierarchy = 'forecast_date'

