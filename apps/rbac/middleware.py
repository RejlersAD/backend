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
            # User doesn't have RBAC profile - allow request but profile will be None
            request.user_profile = None
            return None
        except Exception as e:
            # Log any other exception and allow request to continue
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"RBACMiddleware error for user {request.user}: {str(e)}", exc_info=True)
            request.user_profile = None
            return None
        
        # Only check status if profile exists
        if request.user_profile:
            # Check if user is active
            if request.user_profile.status != 'active':
                return JsonResponse({
                    'error': f'Account is {request.user_profile.status}. Please contact administrator.'
                }, status=403)
            
            # Check if account is locked
            from django.utils import timezone
            if request.user_profile.locked_until and request.user_profile.locked_until > timezone.now():
                return JsonResponse({
                    'error': 'Account is temporarily locked. Please try again later.'
                }, status=403)
        
        return None
    
    def process_view(self, request, view_func, view_args, view_kwargs):
        from .audit_context import current_audits
        request._rbac_audit_entries = []
        request._rbac_previous_audits = current_audits.get()
        current_audits.set(request._rbac_audit_entries)

    def process_response(self, request, response):
        from .audit_context import current_audits, request_audit_fields, TELEMETRY_PREFIXES
        try:
            if request.method not in ['POST', 'PUT', 'PATCH', 'DELETE'] or not request.user.is_authenticated:
                return response
            if request.path.startswith(('/api/v1/auth/', *TELEMETRY_PREFIXES)):
                return response
            fields = request_audit_fields(request, response)
            entries = getattr(request, '_rbac_audit_entries', [])
            if entries:
                # Preserve semantic action/target/changes written by the view.
                # Attach request context instead of emitting a duplicate generic row.
                for entry in entries:
                    entry.metadata = {
                        'request_path': request.path,
                        'request_method': request.method,
                        'response_status': response.status_code,
                        **entry.metadata,
                    }
                    entry.save(update_fields=['metadata'])
            else:
                create_audit_log(
                    user=request.user, **fields,
                    ip_address=request.META.get('REMOTE_ADDR'),
                    user_agent=request.META.get('HTTP_USER_AGENT', ''),
                    success=response.status_code < 400,
                )
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Request audit logging failed')
        finally:
            if hasattr(request, '_rbac_audit_entries'):
                current_audits.set(request._rbac_previous_audits)
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
