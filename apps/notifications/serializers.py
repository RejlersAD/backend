"""
Notification System Serializers
Provides REST API serialization for notifications, preferences, and categories
"""

from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import Notification, NotificationCategory, NotificationPreference, NotificationLog

User = get_user_model()


class UserMinimalSerializer(serializers.ModelSerializer):
    """Minimal user info for notifications"""
    full_name = serializers.SerializerMethodField()
    
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'full_name']
    
    def get_full_name(self, obj):
        return f"{obj.first_name} {obj.last_name}".strip() or obj.username


class NotificationCategorySerializer(serializers.ModelSerializer):
    """Category serializer with icon and color"""
    
    class Meta:
        model = NotificationCategory
        fields = [
            'id', 'name', 'description', 'icon', 
            'color', 'is_active', 'order'
        ]


class NotificationSerializer(serializers.ModelSerializer):
    """Main notification serializer"""
    recipient = UserMinimalSerializer(read_only=True)
    sender = UserMinimalSerializer(read_only=True)
    category_detail = NotificationCategorySerializer(source='category', read_only=True)
    
    # Computed fields
    time_ago = serializers.SerializerMethodField()
    is_expired = serializers.SerializerMethodField()
    priority_display = serializers.CharField(source='get_priority_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    
    class Meta:
        model = Notification
        fields = [
            'id', 'recipient', 'sender', 'category', 'category_detail',
            'title', 'message', 'priority', 'priority_display',
            'status', 'status_display', 'is_read', 'read_at',
            'sent_by_email', 'sent_by_sms', 'sent_by_in_app',
            'action_url', 'action_label', 'metadata',
            'created_at', 'updated_at', 'expires_at',
            'time_ago', 'is_expired'
        ]
        read_only_fields = [
            'id', 'status', 'is_read', 'read_at',
            'sent_by_email', 'sent_by_sms', 'sent_by_in_app',
            'created_at', 'updated_at'
        ]
    
    def get_time_ago(self, obj):
        """Human-readable time since notification"""
        from django.utils.timesince import timesince
        return timesince(obj.created_at) + " ago"
    
    def get_is_expired(self, obj):
        """Check if notification is expired"""
        if not obj.expires_at:
            return False
        from django.utils import timezone
        return obj.expires_at < timezone.now()


class NotificationListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for notification lists"""
    category_name = serializers.CharField(source='category.name', read_only=True)
    category_icon = serializers.CharField(source='category.icon', read_only=True)
    time_ago = serializers.SerializerMethodField()
    
    class Meta:
        model = Notification
        fields = [
            'id', 'title', 'message', 'priority', 
            'category_name', 'category_icon',
            'is_read', 'created_at', 'time_ago',
            'action_url', 'action_label'
        ]
    
    def get_time_ago(self, obj):
        from django.utils.timesince import timesince
        return timesince(obj.created_at)


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    """User notification preferences serializer"""
    
    class Meta:
        model = NotificationPreference
        fields = [
            'id', 'user',
            'enable_email', 'enable_in_app', 'enable_sms',
            'email_digest_frequency', 'quiet_hours_start', 'quiet_hours_end',
            'minimum_priority_email', 'minimum_priority_sms',
            'enabled_categories', 'disabled_categories',
            'auto_mark_read_after_seconds',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'user', 'created_at', 'updated_at']


class NotificationLogSerializer(serializers.ModelSerializer):
    """Notification log/audit trail serializer"""
    user = UserMinimalSerializer(read_only=True)
    
    class Meta:
        model = NotificationLog
        fields = [
            'id', 'notification', 'user', 'action',
            'metadata', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']


class MarkAsReadSerializer(serializers.Serializer):
    """Serializer for marking notifications as read"""
    notification_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        help_text="List of notification IDs to mark as read. If empty, marks all as read."
    )


class BulkNotificationSerializer(serializers.Serializer):
    """Serializer for creating bulk notifications"""
    recipient_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=True,
        help_text="List of user IDs to send notification to"
    )
    template_key = serializers.CharField(
        required=False,
        help_text="Template key from NotificationService.TEMPLATES"
    )
    title = serializers.CharField(
        max_length=255,
        required=False,
        help_text="Custom notification title (if not using template)"
    )
    message = serializers.CharField(
        required=False,
        help_text="Custom notification message (if not using template)"
    )
    priority = serializers.ChoiceField(
        choices=Notification.PRIORITY_CHOICES,
        default='NORMAL'
    )
    category = serializers.PrimaryKeyRelatedField(
        queryset=NotificationCategory.objects.all(),
        required=False
    )
    send_email = serializers.BooleanField(default=False)
    action_url = serializers.URLField(required=False, allow_blank=True)
    action_label = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True
    )
    
    def validate(self, data):
        """Ensure either template or custom title/message is provided"""
        if not data.get('template_key') and not (data.get('title') and data.get('message')):
            raise serializers.ValidationError(
                "Either provide template_key or both title and message"
            )
        return data


class NotificationStatsSerializer(serializers.Serializer):
    """Serializer for notification statistics"""
    total_count = serializers.IntegerField()
    unread_count = serializers.IntegerField()
    read_count = serializers.IntegerField()
    by_priority = serializers.DictField()
    by_category = serializers.DictField()
    by_status = serializers.DictField()
    recent_notifications = NotificationListSerializer(many=True)
