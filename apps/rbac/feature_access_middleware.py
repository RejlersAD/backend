"""
Feature Access Middleware
Automatically checks if user has access to requested features based on assigned modules
"""
from django.http import JsonResponse
from apps.rbac.models import UserProfile

class FeatureAccessMiddleware:
    """
    Middleware to check feature access based on user's assigned modules
    """
    
    # Map feature routes to required module codes
    FEATURE_MODULE_MAP = {
        '/pid/': ['PID'],
        '/pfd/': ['PFD'],
        '/crs/': ['CRS'],
        '/projects/': ['PROJECT_CONTROL'],
        '/project-control/': ['PROJECT_CONTROL'],
    }
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        # Skip middleware for certain paths
        if self._should_skip(request.path):
            return self.get_response(request)
        
        # Check if user is authenticated
        if not request.user.is_authenticated:
            return self.get_response(request)
        
        # Super admins bypass all checks
        if request.user.is_superuser:
            return self.get_response(request)
        
        # Check feature access
        for route_pattern, required_modules in self.FEATURE_MODULE_MAP.items():
            if request.path.startswith(route_pattern):
                if not self._has_module_access(request.user, required_modules):
                    return JsonResponse({
                        'error': 'Access Denied',
                        'message': f'You do not have access to this feature. Required modules: {", ".join(required_modules)}',
                        'required_modules': required_modules
                    }, status=403)
        
        return self.get_response(request)
    
    def _should_skip(self, path):
        """Skip middleware for these paths"""
        skip_paths = [
            '/admin/',
            '/api/auth/',
            '/api/users/me/',
            '/api/rbac/',
            '/static/',
            '/media/',
            '/api/schema/',
            '/api/swagger/',
        ]
        return any(path.startswith(skip_path) for skip_path in skip_paths)
    
    def _has_module_access(self, user, required_modules):
        """Check if user has access to required modules"""
        try:
            profile = UserProfile.objects.get(user=user, is_deleted=False)
            
            # Get user's modules through roles
            user_module_codes = set()
            for role in profile.roles.filter(is_active=True):
                user_module_codes.update(
                    role.modules.filter(is_active=True).values_list('code', flat=True)
                )
            
            # Check if user has all required modules
            return all(module in user_module_codes for module in required_modules)
            
        except UserProfile.DoesNotExist:
            return False
