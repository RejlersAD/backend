"""
Notification System Views
REST API endpoints for notifications management
"""

from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django.db.models import Q, Count
from django.shortcuts import get_object_or_404

from .models import Notification, NotificationCategory, NotificationPreference, NotificationLog
from .serializers import (
    NotificationSerializer, NotificationListSerializer,
    NotificationCategorySerializer, NotificationPreferenceSerializer,
    NotificationLogSerializer, MarkAsReadSerializer,
    BulkNotificationSerializer, NotificationStatsSerializer
)
from .services import NotificationService


class NotificationViewSet(viewsets.ModelViewSet):
    """
    API endpoints for notifications
    
    list: Get all notifications for current user
    retrieve: Get specific notification
    mark_as_read: Mark notification(s) as read
    mark_all_read: Mark all notifications as read
    unread_count: Get count of unread notifications
    stats: Get notification statistics
    """
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['title', 'message']
    ordering_fields = ['created_at', 'priority', 'is_read']
    ordering = ['-created_at']
    
    def get_serializer_class(self):
        if self.action == 'list':
            return NotificationListSerializer
        return NotificationSerializer
    
    def get_queryset(self):
        """Get notifications for current user only"""
        user = self.request.user
        queryset = Notification.objects.filter(recipient=user).select_related(
            'category', 'sender', 'recipient'
        )
        
        # Filter by status
        status_filter = self.request.query_params.get('status')
        if status_filter:
            if status_filter == 'unread':
                queryset = queryset.filter(is_read=False)
            elif status_filter == 'read':
                queryset = queryset.filter(is_read=True)
        
        # Filter by priority
        priority_filter = self.request.query_params.get('priority')
        if priority_filter:
            queryset = queryset.filter(priority=priority_filter.upper())
        
        # Filter by category
        category_filter = self.request.query_params.get('category')
        if category_filter:
            queryset = queryset.filter(category__name=category_filter)
        
        # Exclude expired
        exclude_expired = self.request.query_params.get('exclude_expired', 'false')
        if exclude_expired.lower() == 'true':
            queryset = queryset.filter(
                Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
            )
        
        return queryset
    
    def retrieve(self, request, *args, **kwargs):
        """Get notification and optionally mark as read"""
        instance = self.get_object()
        
        # Auto-mark as read when retrieved
        auto_read = request.query_params.get('auto_read', 'true')
        if auto_read.lower() == 'true' and not instance.is_read:
            instance.mark_as_read()
            
            # Log action
            NotificationLog.objects.create(
                notification=instance,
                user=request.user,
                action='READ',
                metadata={'auto_read': True}
            )
        
        serializer = self.get_serializer(instance)
        return Response(serializer.data)
    
    @action(detail=False, methods=['post'])
    def mark_as_read(self, request):
        """
        Mark one or more notifications as read
        
        POST data:
        - notification_ids: List of notification IDs (optional, if empty marks all)
        """
        serializer = MarkAsReadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        notification_ids = serializer.validated_data.get('notification_ids', [])
        
        if notification_ids:
            # Mark specific notifications
            notifications = Notification.objects.filter(
                id__in=notification_ids,
                recipient=request.user,
                is_read=False
            )
        else:
            # Mark all unread notifications
            notifications = Notification.objects.filter(
                recipient=request.user,
                is_read=False
            )
        
        count = 0
        for notification in notifications:
            notification.mark_as_read()
            count += 1
            
            # Log action
            NotificationLog.objects.create(
                notification=notification,
                user=request.user,
                action='READ',
                metadata={'bulk_operation': True}
            )
        
        return Response({
            'status': 'success',
            'marked_read': count,
            'message': f'{count} notification(s) marked as read'
        })
    
    @action(detail=False, methods=['get'])
    def unread_count(self, request):
        """Get count of unread notifications"""
        count = Notification.objects.filter(
            recipient=request.user,
            is_read=False,
            status='SENT'
        ).exclude(
            expires_at__lt=timezone.now()
        ).count()
        
        # Count by priority
        by_priority = Notification.objects.filter(
            recipient=request.user,
            is_read=False,
            status='SENT'
        ).values('priority').annotate(count=Count('id'))
        
        return Response({
            'total': count,
            'by_priority': {item['priority']: item['count'] for item in by_priority}
        })
    
    @action(detail=False, methods=['get'])
    def stats(self, request):
        """Get comprehensive notification statistics"""
        user = request.user
        
        # Base queryset
        all_notifications = Notification.objects.filter(recipient=user)
        
        # Counts
        total_count = all_notifications.count()
        unread_count = all_notifications.filter(is_read=False).count()
        read_count = all_notifications.filter(is_read=True).count()
        
        # By priority
        by_priority = all_notifications.values('priority').annotate(
            count=Count('id')
        )
        by_priority_dict = {item['priority']: item['count'] for item in by_priority}
        
        # By category
        by_category = all_notifications.values('category__name').annotate(
            count=Count('id')
        )
        by_category_dict = {item['category__name']: item['count'] for item in by_category}
        
        # By status
        by_status = all_notifications.values('status').annotate(
            count=Count('id')
        )
        by_status_dict = {item['status']: item['count'] for item in by_status}
        
        # Recent notifications (last 5)
        recent = all_notifications.order_by('-created_at')[:5]
        
        data = {
            'total_count': total_count,
            'unread_count': unread_count,
            'read_count': read_count,
            'by_priority': by_priority_dict,
            'by_category': by_category_dict,
            'by_status': by_status_dict,
            'recent_notifications': NotificationListSerializer(recent, many=True).data
        }
        
        return Response(data)
    
    @action(detail=True, methods=['post'])
    def archive(self, request, pk=None):
        """Archive a notification"""
        notification = self.get_object()
        notification.status = 'ARCHIVED'
        notification.save()
        
        # Log action
        NotificationLog.objects.create(
            notification=notification,
            user=request.user,
            action='ARCHIVED'
        )
        
        return Response({
            'status': 'success',
            'message': 'Notification archived'
        })
    
    @action(detail=False, methods=['post'])
    def bulk_create(self, request):
        """
        Create notifications for multiple users
        Requires admin/staff permissions
        """
        if not request.user.is_staff:
            return Response(
                {'error': 'Permission denied'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        serializer = BulkNotificationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        data = serializer.validated_data
        recipient_ids = data.pop('recipient_ids')
        
        from django.contrib.auth import get_user_model
        User = get_user_model()
        recipients = User.objects.filter(id__in=recipient_ids)
        
        if data.get('template_key'):
            # Use template
            created = NotificationService.bulk_notify(
                recipients=recipients,
                template_key=data['template_key'],
                **data.get('metadata', {})
            )
        else:
            # Custom notification
            created = []
            for recipient in recipients:
                notification = NotificationService.create_notification(
                    recipient=recipient,
                    title=data['title'],
                    message=data['message'],
                    priority=data.get('priority', 'NORMAL'),
                    category=data.get('category'),
                    send_email=data.get('send_email', False),
                    action_url=data.get('action_url'),
                    action_label=data.get('action_label')
                )
                created.append(notification)
        
        return Response({
            'status': 'success',
            'created': len(created),
            'message': f'{len(created)} notifications created'
        })


class NotificationCategoryViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoints for notification categories
    Read-only: categories are managed in admin panel
    """
    queryset = NotificationCategory.objects.filter(is_active=True).order_by('name')
    serializer_class = NotificationCategorySerializer
    permission_classes = [IsAuthenticated]


class NotificationPreferenceViewSet(viewsets.ModelViewSet):
    """
    API endpoints for user notification preferences
    """
    serializer_class = NotificationPreferenceSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        """Get preferences for current user only"""
        return NotificationPreference.objects.filter(user=self.request.user)
    
    def get_object(self):
        """Get or create preference for current user"""
        preference, created = NotificationPreference.objects.get_or_create(
            user=self.request.user
        )
        return preference
    
    @action(detail=False, methods=['get'])
    def my_preferences(self, request):
        """Get current user's preferences"""
        preference = self.get_object()
        serializer = self.get_serializer(preference)
        return Response(serializer.data)
    
    @action(detail=False, methods=['post', 'put', 'patch'])
    def update_preferences(self, request):
        """Update current user's preferences"""
        preference = self.get_object()
        serializer = self.get_serializer(
            preference,
            data=request.data,
            partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        
        return Response({
            'status': 'success',
            'message': 'Preferences updated',
            'data': serializer.data
        })


class NotificationLogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoints for notification logs (audit trail)
    Read-only
    """
    serializer_class = NotificationLogSerializer
    permission_classes = [IsAuthenticated]
    ordering = ['-created_at']
    
    def get_queryset(self):
        """Get logs for current user's notifications"""
        return NotificationLog.objects.filter(
            Q(user=self.request.user) | Q(notification__recipient=self.request.user)
        ).select_related('notification', 'user')
