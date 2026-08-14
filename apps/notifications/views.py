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
from django.core.cache import cache
from django.db import connection
import logging

from .models import Notification, NotificationCategory, NotificationPreference, NotificationLog
from .serializers import (
    NotificationSerializer, NotificationListSerializer,
    NotificationCategorySerializer, NotificationPreferenceSerializer,
    NotificationLogSerializer, MarkAsReadSerializer,
    BulkNotificationSerializer, NotificationStatsSerializer
)
from .services import NotificationService

logger = logging.getLogger(__name__)

# Import Data Visibility for Row-Level Security
from apps.rbac.data_visibility_mixin import PersonalDataMixin


class NotificationViewSet(PersonalDataMixin, viewsets.ModelViewSet):
    """
    API endpoints for notifications
    
    🔐 Data Visibility:
    - Every user, including administrators, sees only their own notifications
    - No team sharing
    
    list: Get all notifications for current user
    retrieve: Get specific notification
    mark_as_read: Mark notification(s) as read
    mark_all_read: Mark all notifications as read
    unread_count: Get count of unread notifications
    stats: Get notification statistics
    """
    # Data visibility configuration
    visibility_owner_field = 'recipient'
    
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['title', 'message']
    ordering_fields = ['created_at', 'priority', 'is_read']
    ordering = ['-created_at']
    queryset = Notification.objects.all()
    visibility_logging = False

    def get_queryset(self):
        """Return only the current user's notifications using the recipient index."""
        queryset = Notification.objects.filter(
            recipient_id=self.request.user.id,
        ).select_related('category', 'recipient', 'sender')

        status_filter = self.request.query_params.get('status')
        if status_filter == 'unread':
            queryset = queryset.filter(is_read=False)
        elif status_filter == 'read':
            queryset = queryset.filter(is_read=True)

        priority_filter = self.request.query_params.get('priority')
        if priority_filter:
            queryset = queryset.filter(priority=priority_filter.upper())

        category_filter = self.request.query_params.get('category')
        if category_filter:
            queryset = queryset.filter(category__name=category_filter.upper())

        if self.request.query_params.get('exclude_expired', 'false').lower() == 'true':
            queryset = queryset.filter(
                Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
            )

        return queryset.order_by('-created_at')

    def get_serializer_class(self):
        if self.action == 'list':
            return NotificationListSerializer
        return NotificationSerializer
    
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
        
        # Invalidate cache after marking as read
        cache_key = f'notification_unread_count_{request.user.id}'
        cache.delete(cache_key)
        logger.debug(f'[Notification] Cache invalidated for user {request.user.id}')
        
        return Response({
            'status': 'success',
            'marked_read': count,
            'message': f'{count} notification(s) marked as read'
        })
    
    @action(detail=False, methods=['get'])
    def unread_count(self, request):
        """
        Get count of unread notifications (OPTIMIZED with caching)
        
        Performance Optimizations:
        1. Redis caching (30-second TTL)
        2. Optimized database query using compound index
        3. Single query for all counts using aggregation
        4. Early return on cache hit
        """
        user_id = request.user.id
        cache_key = f'notification_unread_count_{user_id}'
        
        # Try to get from cache first
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            logger.debug(f'[Notification] Cache hit for user {user_id}')
            return Response(cached_result)
        
        try:
            # Get current time once
            now = timezone.now()
            
            # Single optimized query using the compound index (notif_unread_opt)
            # Index: ['recipient', 'is_read', 'status', 'expires_at']
            base_queryset = Notification.objects.filter(
                recipient_id=user_id,  # Use _id for direct FK comparison
                is_read=False,
                status='SENT'
            ).filter(
                Q(expires_at__isnull=True) | Q(expires_at__gt=now)
            )
            
            # Get total count efficiently
            total = base_queryset.count()
            
            # Get priority breakdown in single query (only if needed)
            by_priority = {}
            if total > 0:
                # Use values_list for minimal data transfer
                priority_counts = base_queryset.values('priority').annotate(
                    total=Count('id', distinct=True)
                ).order_by('priority')
                
                by_priority = {item['priority']: item['total'] for item in priority_counts}
            
            result = {
                'unread_count': total,  # Frontend expects 'unread_count' key
                'total': total,  # Keep for backwards compatibility
                'by_priority': by_priority,
                'cached': False
            }
            
            # Cache for 30 seconds to reduce database load
            cache.set(cache_key, result, timeout=30)
            
            logger.debug(f'[Notification] Unread count for user {user_id}: {total}')
            return Response(result)
            
        except Exception as e:
            logger.error(f"[Notification] Unread count error for user {user_id}: {str(e)}", exc_info=True)
            # Return safe default on error
            return Response({
                'unread_count': 0,
                'total': 0,
                'by_priority': {},
                'error': 'Unable to fetch unread count',
                'cached': False
            }, status=status.HTTP_200_OK)  # Return 200 to prevent frontend errors
    
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
