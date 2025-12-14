"""
RBAC Middleware - Enforce permissions at request level
"""
from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin
from .models import UserProfile, AuditLog
from .utils import create_audit_log


class RBACMiddleware(MiddlewareMixin):
    """
    Middleware to enforce RBAC and log requests
    """
    EXEMPT_PATHS = [
        '/api/v1/auth/',
        '/admin/',
        '/api/v1/health/',
        '/static/',
        '/media/',
    ]
    
    def process_request(self, request):
        """Process incoming requests"""
        # Skip exempt paths
        for path in self.EXEMPT_PATHS:
            if request.path.startswith(path):
                return None
        
        # Check if user is authenticated
        if not request.user.is_authenticated:
            return None  # Let DRF authentication handle this
        
        # Attach user profile to request for easy access
        try:
            request.user_profile = request.user.rbac_profile
        except UserProfile.DoesNotExist:
            return JsonResponse({
                'error': 'User profile not found. Please contact administrator.'
            }, status=403)
        
        # Check if user is active
        if request.user_profile.status != 'active':
            return JsonResponse({
                'error': f'Account is {request.user_profile.status}. Please contact administrator.'
            }, status=403)
        
        # Check if account is locked
        if request.user_profile.locked_until and request.user_profile.locked_until > timezone.now():
            return JsonResponse({
                'error': 'Account is temporarily locked. Please try again later.'
            }, status=403)
        
        return None
    
    def process_response(self, request, response):
        """Log important requests"""
        # Log write operations (POST, PUT, PATCH, DELETE)
        if request.method in ['POST', 'PUT', 'PATCH', 'DELETE'] and request.user.is_authenticated:
            # Skip auth endpoints
            if not request.path.startswith('/api/v1/auth/'):
                try:
                    action_map = {
                        'POST': 'create',
                        'PUT': 'update',
                        'PATCH': 'update',
                        'DELETE': 'delete'
                    }
                    
                    # Extract resource type from URL
                    path_parts = request.path.strip('/').split('/')
                    resource_type = path_parts[-2] if len(path_parts) >= 2 else 'unknown'
                    
                    create_audit_log(
                        user=request.user,
                        action=action_map.get(request.method, 'unknown'),
                        resource_type=resource_type,
                        ip_address=request.META.get('REMOTE_ADDR'),
                        user_agent=request.META.get('HTTP_USER_AGENT', ''),
                        success=response.status_code < 400
                    )
                except Exception:
                    pass  # Don't fail request if logging fails
        
        return response


class LoginTrackingMiddleware(MiddlewareMixin):
    """
    Middleware to track login attempts and IP addresses
    """
    def process_request(self, request):
        if request.path == '/api/v1/auth/login/' and request.method == 'POST':
            # Store IP address for login tracking
            request.login_ip = request.META.get('REMOTE_ADDR')
        return None
