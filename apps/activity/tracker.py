"""
Real-time Activity Tracker
Middleware and utilities to track all system activities
"""
from .models import SystemActivity, UserSession
from django.utils import timezone
from django.contrib.contenttypes.models import ContentType
from datetime import timedelta
import json
import logging

logger = logging.getLogger(__name__)


class ActivityTracker:
    """
    Centralized activity tracking system
    Soft-coded and flexible
    """
    
    @staticmethod
    def track(
        activity_type,
        user=None,
        description="",
        category='system_operation',
        severity='normal',
        success=True,
        details=None,
        metadata=None,
        related_object=None,
        duration_ms=None,
        request=None
    ):
        """
        Track any system activity
        
        Usage:
            ActivityTracker.track(
                activity_type='user_login',
                user=request.user,
                description='User logged in successfully',
                category='authentication',
                success=True
            )
        """
        try:
            # Extract request info if provided
            ip_address = None
            user_agent = ''
            
            if request:
                ip_address = ActivityTracker.get_client_ip(request)
                user_agent = request.META.get('HTTP_USER_AGENT', '')
            
            # Get content type for related object
            content_type = None
            object_id = None
            if related_object:
                content_type = ContentType.objects.get_for_model(related_object)
                object_id = related_object.pk
            
            # Create activity record
            activity = SystemActivity.objects.create(
                activity_type=activity_type,
                category=category,
                severity=severity,
                user=user,
                user_email=user.email if user else '',
                user_full_name=user.get_full_name() if user else '',
                ip_address=ip_address,
                user_agent=user_agent,
                description=description,
                details=details or {},
                content_type=content_type,
                object_id=object_id,
                success=success,
                error_message='' if success else details.get('error', ''),
                duration_ms=duration_ms,
                metadata=metadata or {},
            )
            
            # Broadcast via WebSocket if critical
            if severity in ['high', 'critical']:
                ActivityTracker.broadcast_activity(activity)
            
            return activity
            
        except Exception as e:
            logger.error(f"Failed to track activity: {e}")
            return None
    
    @staticmethod
    def get_client_ip(request):
        """Extract client IP from request"""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip
    
    @staticmethod
    def broadcast_activity(activity):
        """Broadcast activity via WebSocket"""
        try:
            from channels.layers import get_channel_layer
            from asgiref.sync import async_to_sync
            
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                'activity_stream',
                {
                    'type': 'activity_update',
                    'activity': {
                        'id': activity.id,
                        'type': activity.activity_type,
                        'description': activity.description,
                        'user': activity.user_email,
                        'severity': activity.severity,
                        'timestamp': activity.timestamp.isoformat(),
                        'success': activity.success,
                    }
                }
            )
        except Exception as e:
            logger.error(f"Failed to broadcast activity: {e}")
    
    @staticmethod
    def update_user_session(user, request, current_page=''):
        """Update or create user session"""
        try:
            session_key = request.session.session_key
            if not session_key:
                return None
            
            ip_address = ActivityTracker.get_client_ip(request)
            user_agent = request.META.get('HTTP_USER_AGENT', '')
            
            # Parse user agent for device info
            device_info = ActivityTracker.parse_user_agent(user_agent)
            
            session, created = UserSession.objects.update_or_create(
                session_key=session_key,
                defaults={
                    'user': user,
                    'ip_address': ip_address,
                    'user_agent': user_agent,
                    'device_type': device_info.get('device', ''),
                    'browser': device_info.get('browser', ''),
                    'os': device_info.get('os', ''),
                    'last_activity': timezone.now(),
                    'current_page': current_page,
                    'is_active': True,
                    'expires_at': timezone.now() + timedelta(hours=24),
                }
            )
            
            return session
            
        except Exception as e:
            logger.error(f"Failed to update user session: {e}")
            return None
    
    @staticmethod
    def parse_user_agent(user_agent):
        """Parse user agent string"""
        # Simple parsing - can be enhanced with user-agents library
        info = {
            'device': 'Desktop',
            'browser': 'Unknown',
            'os': 'Unknown'
        }
        
        user_agent_lower = user_agent.lower()
        
        # Detect mobile
        if any(x in user_agent_lower for x in ['mobile', 'android', 'iphone', 'ipad']):
            info['device'] = 'Mobile'
        elif 'tablet' in user_agent_lower:
            info['device'] = 'Tablet'
        
        # Detect browser
        if 'chrome' in user_agent_lower and 'edge' not in user_agent_lower:
            info['browser'] = 'Chrome'
        elif 'firefox' in user_agent_lower:
            info['browser'] = 'Firefox'
        elif 'safari' in user_agent_lower and 'chrome' not in user_agent_lower:
            info['browser'] = 'Safari'
        elif 'edge' in user_agent_lower:
            info['browser'] = 'Edge'
        
        # Detect OS
        if 'windows' in user_agent_lower:
            info['os'] = 'Windows'
        elif 'mac' in user_agent_lower:
            info['os'] = 'macOS'
        elif 'linux' in user_agent_lower:
            info['os'] = 'Linux'
        elif 'android' in user_agent_lower:
            info['os'] = 'Android'
        elif 'ios' in user_agent_lower or 'iphone' in user_agent_lower:
            info['os'] = 'iOS'
        
        return info
    
    @staticmethod
    def get_active_users():
        """Get currently active users"""
        threshold = timezone.now() - timedelta(minutes=5)
        return UserSession.objects.filter(
            last_activity__gte=threshold,
            is_active=True
        ).select_related('user')
    
    @staticmethod
    def get_recent_activities(limit=50, user=None, category=None):
        """Get recent activities with filters"""
        queryset = SystemActivity.objects.all()
        
        if user:
            queryset = queryset.filter(user=user)
        
        if category:
            queryset = queryset.filter(category=category)
        
        return queryset[:limit]


class ActivityMiddleware:
    """
    Middleware to track all requests and responses
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        # Track request start time
        import time
        start_time = time.time()
        
        # Process request
        response = self.get_response(request)
        
        # Calculate duration
        duration_ms = int((time.time() - start_time) * 1000)
        
        # Track API requests
        if request.path.startswith('/api/') and request.user.is_authenticated:
            ActivityTracker.track(
                activity_type='api_request',
                user=request.user,
                description=f"{request.method} {request.path}",
                category='api',
                severity='info',
                success=response.status_code < 400,
                details={
                    'method': request.method,
                    'path': request.path,
                    'status_code': response.status_code,
                },
                duration_ms=duration_ms,
                request=request
            )
        
        # Update user session
        if request.user.is_authenticated:
            ActivityTracker.update_user_session(
                request.user,
                request,
                current_page=request.path
            )
        
        return response


# Decorator for tracking specific operations
def track_activity(activity_type, description="", category='system_operation', severity='normal'):
    """
    Decorator to track function execution as activity
    
    Usage:
        @track_activity('document_processed', 'Document processed successfully', 'data_management')
        def process_document(doc):
            ...
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            import time
            start_time = time.time()
            
            success = True
            error_message = ""
            
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                success = False
                error_message = str(e)
                raise
            finally:
                duration_ms = int((time.time() - start_time) * 1000)
                
                # Try to get user from request or kwargs
                user = None
                request = kwargs.get('request') or (args[0] if args and hasattr(args[0], 'user') else None)
                if request and hasattr(request, 'user'):
                    user = request.user if request.user.is_authenticated else None
                
                ActivityTracker.track(
                    activity_type=activity_type,
                    user=user,
                    description=description or f"{func.__name__} executed",
                    category=category,
                    severity=severity,
                    success=success,
                    details={'function': func.__name__, 'error': error_message} if not success else {},
                    duration_ms=duration_ms,
                    request=request if hasattr(request, 'META') else None
                )
        
        return wrapper
    return decorator
