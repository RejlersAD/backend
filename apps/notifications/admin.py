"""
Notification System Admin Configuration
"""

from django.contrib import admin
from django.utils.html import format_html
from django.utils.timezone import localtime
from .models import Notification, NotificationCategory, NotificationPreference, NotificationLog


@admin.register(NotificationCategory)
class NotificationCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'icon_display', 'color_display', 'is_active']
    list_editable = ['is_active']
    list_filter = ['is_active']
    search_fields = ['name', 'description']
    ordering = ['name']
    
    def icon_display(self, obj):
        return format_html(
            '<span style="font-size: 20px;">{}</span>',
            obj.icon
        )
    icon_display.short_description = 'Icon'
    
    def color_display(self, obj):
        return format_html(
            '<span style="display: inline-block; width: 20px; height: 20px; '
            'background-color: {}; border-radius: 3px;"></span>',
            obj.color
        )
    color_display.short_description = 'Color'


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'title_short', 'recipient', 'category',
        'priority_badge', 'status_badge', 'is_read',
        'created_at_formatted', 'action_button'
    ]
    list_filter = [
        'priority', 'status', 'is_read', 'category', 'created_at'
    ]
    search_fields = ['title', 'message', 'recipient__username', 'recipient__email']
    readonly_fields = [
        'id', 'created_at', 'updated_at', 'read_at',
        'email_sent', 'email_sent_at'
    ]
    date_hierarchy = 'created_at'
    list_per_page = 50
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('recipient', 'sender', 'category', 'title', 'message')
        }),
        ('Priority & Status', {
            'fields': ('priority', 'status', 'is_read', 'read_at')
        }),
        ('Delivery Channels', {
            'fields': ('send_in_app', 'send_email', 'send_sms', 'email_sent', 'email_sent_at')
        }),
        ('Action', {
            'fields': ('action_url', 'action_label')
        }),
        ('Metadata', {
            'fields': ('metadata', 'expires_at'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    actions = ['mark_as_read', 'mark_as_unread', 'archive_notifications', 'resend_email']
    
    def title_short(self, obj):
        return obj.title[:50] + '...' if len(obj.title) > 50 else obj.title
    title_short.short_description = 'Title'
    
    def priority_badge(self, obj):
        colors = {
            'LOW': '#6B7280',
            'NORMAL': '#3B82F6',
            'HIGH': '#F59E0B',
            'URGENT': '#EF4444',
            'CRITICAL': '#DC2626'
        }
        return format_html(
            '<span style="background-color: {}; color: white; '
            'padding: 3px 8px; border-radius: 3px; font-size: 11px; font-weight: bold;">{}</span>',
            colors.get(obj.priority, '#6B7280'),
            obj.get_priority_display()
        )
    priority_badge.short_description = 'Priority'
    
    def status_badge(self, obj):
        colors = {
            'PENDING': '#6B7280',
            'SENT': '#10B981',
            'READ': '#3B82F6',
            'FAILED': '#EF4444',
            'ARCHIVED': '#9CA3AF'
        }
        return format_html(
            '<span style="background-color: {}; color: white; '
            'padding: 3px 8px; border-radius: 3px; font-size: 11px;">{}</span>',
            colors.get(obj.status, '#6B7280'),
            obj.get_status_display()
        )
    status_badge.short_description = 'Status'
    
    def created_at_formatted(self, obj):
        return localtime(obj.created_at).strftime('%Y-%m-%d %H:%M')
    created_at_formatted.short_description = 'Created'
    created_at_formatted.admin_order_field = 'created_at'
    
    def action_button(self, obj):
        if obj.action_url:
            return format_html(
                '<a href="{}" target="_blank" style="color: #3B82F6;">🔗 {}</a>',
                obj.action_url,
                obj.action_label or 'View'
            )
        return '-'
    action_button.short_description = 'Action'
    
    def mark_as_read(self, request, queryset):
        count = 0
        for notification in queryset:
            if not notification.is_read:
                notification.mark_as_read()
                count += 1
        self.message_user(request, f'{count} notification(s) marked as read.')
    mark_as_read.short_description = 'Mark selected as read'
    
    def mark_as_unread(self, request, queryset):
        count = queryset.filter(is_read=True).update(is_read=False, read_at=None)
        self.message_user(request, f'{count} notification(s) marked as unread.')
    mark_as_unread.short_description = 'Mark selected as unread'
    
    def archive_notifications(self, request, queryset):
        count = queryset.update(status='ARCHIVED')
        self.message_user(request, f'{count} notification(s) archived.')
    archive_notifications.short_description = 'Archive selected'
    
    def resend_email(self, request, queryset):
        from .services import send_notification_email
        count = 0
        for notification in queryset:
            send_notification_email.delay(notification.id)
            count += 1
        self.message_user(request, f'{count} email(s) queued for resending.')
    resend_email.short_description = 'Resend email for selected'


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = [
        'user', 'enable_email', 'enable_in_app', 'enable_sms',
        'digest_frequency', 'min_priority_email'
    ]
    list_filter = [
        'enable_email', 'enable_in_app', 'enable_sms',
        'digest_frequency', 'min_priority_email'
    ]
    search_fields = ['user__username', 'user__email']
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('User', {
            'fields': ('user',)
        }),
        ('Channels', {
            'fields': (
                'enable_in_app', 'enable_email', 'enable_sms'
            )
        }),
        ('Email Settings', {
            'fields': (
                'digest_frequency',
                'min_priority_email',
                'quiet_hours_enabled',
                'quiet_hours_start',
                'quiet_hours_end'
            )
        }),
        ('SMS Settings', {
            'fields': ('min_priority_sms',)
        }),
        ('Categories', {
            'fields': ('category_preferences',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def min_priority_email(self, obj):
        return obj.get_min_priority_email_display()
    min_priority_email.short_description = 'Min Priority (Email)'


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'notification_id', 'action',
        'timestamp_formatted'
    ]
    list_filter = ['action', 'timestamp']
    search_fields = [
        'notification__title', 'action'
    ]
    readonly_fields = ['notification', 'action', 'details', 'timestamp']
    date_hierarchy = 'timestamp'
    list_per_page = 100
    
    def timestamp_formatted(self, obj):
        return localtime(obj.timestamp).strftime('%Y-%m-%d %H:%M:%S')
    timestamp_formatted.short_description = 'Timestamp'
    timestamp_formatted.admin_order_field = 'timestamp'
    
    def notification_id(self, obj):
        return obj.notification.id
    notification_id.short_description = 'Notification ID'
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
