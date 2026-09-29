"""
Sales Admin Configuration
Django Admin interface for Sales Management
"""

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db import transaction

from apps.rbac.action_policy import module_action_allowed

from .email_permissions import visible_email_intakes
from .models import (
    Client, Contact, Deal, Quote, SalesActivity, SalesEmailIntake, SalesForecast,
)


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


@admin.register(SalesEmailIntake)
class SalesEmailIntakeAdmin(admin.ModelAdmin):
    list_display = (
        'subject', 'sender_email', 'received_at', 'status',
        'reviewed_by',
    )
    list_filter = ('status', 'importance', 'has_attachments', 'received_at')
    search_fields = (
        'subject', 'sender_name', 'sender_email', 'source_message_id',
        'internet_message_id',
    )
    readonly_fields = (
        'id', 'source_message_id', 'internet_message_id', 'subject',
        'sender_name', 'sender_email', 'received_at', 'sent_at', 'body_preview',
        'has_attachments', 'importance', 'created_at', 'updated_at',
        'mailbox_connection', 'source_mailbox_address', 'source_tenant_id',
        'conversation_id', 'captured_by',
    )
    raw_id_fields = ('opportunity', 'reviewed_by')
    show_full_result_count = False

    def _can_read(self, request):
        return bool(
            super().has_view_permission(request)
            and module_action_allowed(request.user, 'sales_email_intake', 'read')
        )

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if not self._can_read(request):
            return queryset.none()
        return queryset.filter(pk__in=visible_email_intakes(request.user).values('pk'))

    def has_module_permission(self, request):
        return super().has_module_permission(request) and self._can_read(request)

    def has_view_permission(self, request, obj=None):
        return self._can_read(request) and (
            obj is None or visible_email_intakes(request.user).filter(pk=obj.pk).exists()
        )

    def has_add_permission(self, request):
        # Source evidence is created only by the guarded intake commands.
        return False

    def has_change_permission(self, request, obj=None):
        return bool(
            super().has_change_permission(request, obj)
            and self.has_view_permission(request, obj)
            and (obj is None or obj.mailbox_connection_id is None)
        )

    def has_delete_permission(self, request, obj=None):
        return bool(
            super().has_delete_permission(request, obj)
            and self.has_view_permission(request, obj)
            and (obj is None or obj.mailbox_connection_id is None)
        )

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.mailbox_connection_id is not None:
            return tuple(field.name for field in self.model._meta.concrete_fields)
        return super().get_readonly_fields(request, obj)

    def get_fields(self, request, obj=None):
        fields = super().get_fields(request, obj)
        if obj is not None and obj.mailbox_connection_id is not None:
            # A historical relation can outlive permission/ownership changes.
            # Do not render its __str__ or admin URL outside the target's scope.
            return [field for field in fields if field not in {'opportunity', 'duplicate_of'}]
        return fields

    def _duplicate_queryset(self, request, obj=None):
        queryset = visible_email_intakes(request.user).filter(
            mailbox_connection_id=obj.mailbox_connection_id if obj is not None else None,
        )
        return queryset.exclude(pk=obj.pk) if obj is not None else queryset

    def get_form(self, request, obj=None, change=False, **kwargs):
        form = super().get_form(request, obj, change=change, **kwargs)
        if 'duplicate_of' in form.base_fields:
            # A scoped select avoids raw-ID widgets resolving hidden row labels.
            form.base_fields['duplicate_of'].queryset = self._duplicate_queryset(request, obj)
        return form

    def save_model(self, request, obj, form, change):
        if not change:
            raise PermissionDenied('Email source records must be created through intake.')
        with transaction.atomic():
            current = self.get_queryset(request).select_for_update().filter(pk=obj.pk).first()
            if current is None or not self.has_change_permission(request, current):
                raise PermissionDenied('This email source cannot be changed in admin.')
            if obj.duplicate_of_id and not self._duplicate_queryset(request, current).filter(pk=obj.duplicate_of_id).exists():
                raise PermissionDenied('Select an accessible duplicate from the same source scope.')
            # Preserve immutable metadata even when a legacy form was loaded
            # before another request or passes unexpected model attributes.
            for name in self.readonly_fields:
                field = self.model._meta.get_field(name)
                setattr(obj, field.attname, getattr(current, field.attname))
            super().save_model(request, obj, form, change)

    def delete_model(self, request, obj):
        self.delete_queryset(request, self.model.objects.filter(pk=obj.pk))

    def delete_queryset(self, request, queryset):
        with transaction.atomic():
            ids = list(queryset.values_list('pk', flat=True))
            current = list(self.get_queryset(request).select_for_update().filter(pk__in=ids))
            if len(current) != len(ids) or any(not self.has_delete_permission(request, obj) for obj in current):
                raise PermissionDenied('Captured or inaccessible emails cannot be deleted in admin.')
            self.model.objects.filter(pk__in=ids).delete()

