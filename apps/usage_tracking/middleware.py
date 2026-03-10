"""
Usage Tracking Middleware

SOFT-CODED, NON-INVASIVE DESIGN:
- Automatically captures all API requests
- No modifications to existing views or business logic
- Async logging to avoid performance impact
- Smart feature detection from URL patterns

HOW IT WORKS:
1. Intercepts every request
2. Extracts user, department, feature info
3. Measures processing time
4. Logs usage asynchronously after response
"""

import time
import logging
import json
from threading import Thread
from django.utils import timezone
from django.core.cache import cache
from django.conf import settings

logger = logging.getLogger(__name__)


class UsageTrackingMiddleware:
    """
    Middleware that automatically tracks all API usage without modifying business logic.
    
    Features:
    - Automatic user/department detection
    - Smart feature name extraction from URLs
    - Processing time measurement
    - Token usage tracking (for AI features)
    - Async logging for zero performance impact
    """
    
    # Soft-coded feature mapping - easily extendable
    FEATURE_MAP = {
        '/api/v1/pid-analysis/': 'PID Analysis',
        '/api/v1/pfd/': 'PFD Management',
        '/api/v1/process-datasheet/': 'Process Datasheet',
        '/api/v1/electrical-datasheet/': 'Electrical Datasheet',
        '/api/v1/finance/': 'Finance & Invoicing',
        '/api/v1/sales/': 'Sales & CRM',
        '/api/v1/designiq/': 'DesignIQ',
        '/api/v1/procurement/': 'Procurement',
        '/api/v1/qhse/': 'QHSE Management',
        '/api/v1/ml-detection/': 'ML Detection',
        '/api/v1/users/': 'User Management',
        '/api/v1/rbac/': 'RBAC & Permissions',
        '/api/v1/notifications/': 'Notifications',
        '/api/v1/crs/': 'CRS Documents',
        '/api/auth/': 'Authentication',
        '/api/admin/': 'Admin Panel',
    }
    
    # Endpoints to exclude from tracking (to avoid infinite loops and noise)
    EXCLUDE_PATTERNS = [
        '/api/v1/usage/',  # Usage tracking endpoints themselves
        '/api/v1/activity/',  # Activity tracking
        '/admin/jsi18n/',  # Admin i18n
        '/static/',  # Static files
        '/media/',  # Media files
        '/__debug__/',  # Debug toolbar
        '/api/schema/',  # API schema
        '/api/docs/',  # API documentation
        '/health/',  # Health check
        '/favicon.ico',  # Favicon
    ]
    
    def __init__(self, get_response):
        self.get_response = get_response
        logger.info("[UsageTracking] Middleware initialized")
    
    def __call__(self, request):
        # Check if tracking is enabled
        if not getattr(settings, 'ENABLE_USAGE_TRACKING', True):
            return self.get_response(request)
        
        # Skip excluded endpoints
        if self._should_exclude(request.path):
            return self.get_response(request)
        
        # Skip if user is not authenticated (optional - can track anonymous too)
        if not request.user.is_authenticated:
            return self.get_response(request)
        
        # Start timing
        start_time = time.time()
        
        # Store request start time in request object
        request._usage_tracking_start = start_time
        
        # Process the request
        response = self.get_response(request)
        
        # Calculate processing time
        processing_time = time.time() - start_time
        
        # Log usage asynchronously
        self._log_usage_async(request, response, processing_time)
        
        return response
    
    def _should_exclude(self, path):
        """Check if path should be excluded from tracking"""
        for pattern in self.EXCLUDE_PATTERNS:
            if pattern in path:
                return True
        return False
    
    def _detect_feature_name(self, path):
        """
        Smart feature detection from URL path.
        Uses soft-coded FEATURE_MAP for easy extension.
        """
        for pattern, feature_name in self.FEATURE_MAP.items():
            if path.startswith(pattern):
                return feature_name
        
        # Fallback: extract from path
        parts = path.strip('/').split('/')
        if len(parts) >= 2 and parts[0] == 'api':
            return parts[1].replace('-', ' ').title()
        
        return 'Unknown'
    
    def _extract_tokens_used(self, request, response):
        """
        Extract token count from response or request metadata.
        
        AI features should add 'X-Tokens-Used' header or store in response data.
        """
        # Check response headers
        if hasattr(response, 'headers') and 'X-Tokens-Used' in response.headers:
            try:
                return int(response.headers['X-Tokens-Used'])
            except (ValueError, TypeError):
                pass
        
        # Check request metadata (set by AI views)
        if hasattr(request, 'tokens_used'):
            return request.tokens_used
        
        # Check response data for token info
        if hasattr(response, 'data') and isinstance(response.data, dict):
            return response.data.get('tokens_used', 0)
        
        return 0
    
    def _get_department(self, user):
        """Extract department from user profile"""
        try:
            # Check user profile
            if hasattr(user, 'profile') and hasattr(user.profile, 'department'):
                return user.profile.department
            
            # Check user model directly
            if hasattr(user, 'department'):
                return user.department
            
            # Fallback to groups
            if user.groups.exists():
                return user.groups.first().name
            
        except Exception as e:
            logger.warning(f"[UsageTracking] Could not extract department: {e}")
        
        return 'Unknown'
    
    def _log_usage_async(self, request, response, processing_time):
        """
        Log usage data asynchronously to avoid blocking the response.
        
        Uses threading for immediate async (Celery option below for production).
        """
        # Prepare usage data
        usage_data = {
            'user': request.user,
            'department': self._get_department(request.user),
            'feature_name': self._detect_feature_name(request.path),
            'api_endpoint': request.path,
            'request_type': request.method,
            'tokens_used': self._extract_tokens_used(request, response),
            'processing_time': processing_time,
            'status': self._determine_status(response),
            'status_code': response.status_code,
            'timestamp': timezone.now(),
            'ip_address': self._get_client_ip(request),
            'user_agent': request.META.get('HTTP_USER_AGENT', '')[:500],
            'request_data_size': len(request.body) if hasattr(request, 'body') else 0,
            'response_data_size': len(getattr(response, 'content', b'')),
        }
        
        # Add error message if failed
        if response.status_code >= 400:
            usage_data['error_message'] = self._extract_error_message(response)
        
        # Log async using threading (for immediate deployment)
        # For production, consider Celery task
        thread = Thread(target=self._save_usage_log, args=(usage_data,))
        thread.daemon = True
        thread.start()
    
    def _save_usage_log(self, usage_data):
        """Save usage log to database (runs in background thread)"""
        try:
            from .models import UserUsageLog
            
            # Create log entry
            UserUsageLog.objects.create(**usage_data)
            
            # Increment cached counters for dashboard (fast access)
            self._update_cached_counters(usage_data)
            
            logger.debug(f"[UsageTracking] Logged: {usage_data['user'].username} - {usage_data['feature_name']}")
            
        except Exception as e:
            logger.error(f"[UsageTracking] Failed to save log: {e}", exc_info=True)
    
    def _update_cached_counters(self, usage_data):
        """Update Redis cached counters for fast dashboard access"""
        try:
            today = timezone.now().date().isoformat()
            
            # Department daily counter
            dept_key = f"usage:dept:{usage_data['department']}:{today}"
            cache.incr(dept_key, 1)
            
            # Feature daily counter
            feature_key = f"usage:feature:{usage_data['feature_name']}:{today}"
            cache.incr(feature_key, 1)
            
            # User daily counter
            user_key = f"usage:user:{usage_data['user'].id}:{today}"
            cache.incr(user_key, 1)
            
            # Global daily counter
            global_key = f"usage:global:{today}"
            cache.incr(global_key, 1)
            
        except Exception as e:
            logger.warning(f"[UsageTracking] Failed to update cache: {e}")
    
    def _determine_status(self, response):
        """Determine request status from response"""
        if response.status_code >= 500:
            return 'error'
        elif response.status_code >= 400:
            return 'error'
        elif response.status_code == 408:
            return 'timeout'
        else:
            return 'success'
    
    def _get_client_ip(self, request):
        """Extract client IP address from request"""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip
    
    def _extract_error_message(self, response):
        """Extract error message from response"""
        try:
            if hasattr(response, 'data'):
                if isinstance(response.data, dict):
                    return str(response.data.get('detail') or response.data.get('error', ''))[:500]
                return str(response.data)[:500]
            elif hasattr(response, 'content'):
                return response.content.decode('utf-8')[:500]
        except Exception:
            pass
        return ''


# ============================================================================
# CELERY TASK VERSION (for production with Celery enabled)
# ============================================================================

def create_usage_log_celery(usage_data):
    """
    Celery task version for production use.
    
    To use this instead of threading:
    1. Uncomment the @shared_task decorator
    2. In middleware, replace Thread() with:
       create_usage_log_celery.delay(usage_data)
    """
    # from celery import shared_task
    # 
    # @shared_task
    # def create_usage_log(usage_data):
    #     try:
    #         from .models import UserUsageLog
    #         UserUsageLog.objects.create(**usage_data)
    #     except Exception as e:
    #         logger.error(f"[UsageTracking] Celery task failed: {e}")
    pass
