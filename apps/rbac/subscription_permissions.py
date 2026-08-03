"""
Subscription Permissions & Middleware
Dynamic subscription-based access control
"""
from rest_framework import permissions
from .subscription_models import UserSubscription
from .subscription_config import SubscriptionRulesEngine, get_active_subscription
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)


# ============================================================================
# SUBSCRIPTION PERMISSION CLASSES
# ============================================================================

class HasActiveSubscription(permissions.BasePermission):
    """
    Permission class to check if user has active subscription
    """
    message = "You need an active subscription to access this resource"
    
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        subscription = get_active_subscription(request.user)
        
        if not subscription:
            return False
        
        # Check if subscription is truly active
        if subscription.status not in ['active', 'trial']:
            self.message = "Your subscription is not active. Please renew."
            return False
        
        # Check if expired
        if subscription.is_expired:
            self.message = "Your subscription has expired. Please renew."
            return False
        
        return True


class HasSubscriptionFeature(permissions.BasePermission):
    """
    Permission class to check if user's subscription includes specific feature
    
    Usage in ViewSet:
        permission_classes = [IsAuthenticated, HasSubscriptionFeature]
        required_feature = 'ai_features'  # Set this in ViewSet
    """
    message = "Your subscription plan does not include this feature"
    
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        # Get required feature from view
        required_feature = getattr(view, 'required_feature', None)
        if not required_feature:
            logger.warning(f"View {view.__class__.__name__} missing required_feature attribute")
            return True  # Allow if not configured
        
        subscription = get_active_subscription(request.user)
        
        if not subscription:
            self.message = "You need an active subscription to access this feature"
            return False
        
        # Check feature access
        allowed, message = SubscriptionRulesEngine.can_use_feature(
            subscription,
            required_feature
        )
        
        if not allowed:
            self.message = message
            return False
        
        return True


class HasModuleAccess(permissions.BasePermission):
    """
    Permission class to check if user's subscription includes module access
    
    Usage in ViewSet:
        permission_classes = [IsAuthenticated, HasModuleAccess]
        required_module = 'pfd_converter'  # Set this in ViewSet
    """
    message = "Your subscription plan does not include access to this module"
    
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        # Super admins bypass subscription checks
        if hasattr(request.user, 'userprofile') and request.user.userprofile.is_super_admin:
            return True
        
        # Get required module from view
        required_module = getattr(view, 'required_module', None)
        if not required_module:
            return True  # Allow if not configured
        
        subscription = get_active_subscription(request.user)
        
        if not subscription:
            self.message = "You need an active subscription to access this module"
            return False
        
        # Check module access
        if not SubscriptionRulesEngine.can_access_module(subscription, required_module):
            self.message = f"Your plan does not include access to {required_module}. Upgrade to unlock."
            return False
        
        return True


class SubscriptionUsageLimit(permissions.BasePermission):
    """
    Permission class to enforce usage limits
    
    Usage in ViewSet:
        permission_classes = [IsAuthenticated, SubscriptionUsageLimit]
        limit_check_action = 'create_project'  # Set this for create actions
    """
    message = "You have reached your subscription limit"
    
    def has_permission(self, request, view):
        # Only check on create actions
        if request.method not in ['POST', 'PUT', 'PATCH']:
            return True
        
        if not request.user or not request.user.is_authenticated:
            return False
        
        # Super admins bypass limits
        if hasattr(request.user, 'userprofile') and request.user.userprofile.is_super_admin:
            return True
        
        limit_action = getattr(view, 'limit_check_action', None)
        if not limit_action:
            return True  # No limit check configured
        
        subscription = get_active_subscription(request.user)
        
        if not subscription:
            self.message = "You need an active subscription"
            return False
        
        # Check specific limit
        if limit_action == 'create_project':
            allowed, message = SubscriptionRulesEngine.can_create_project(subscription)
        elif limit_action == 'add_user':
            allowed, message = SubscriptionRulesEngine.can_add_user(subscription)
        elif limit_action == 'upload_file':
            # Get file size from request if available
            file_size = request.data.get('file_size_mb', 0)
            allowed, message = SubscriptionRulesEngine.can_upload_file(subscription, file_size)
        else:
            return True  # Unknown action, allow
        
        if not allowed:
            self.message = message
            return False
        
        return True


# ============================================================================
# SUBSCRIPTION MIDDLEWARE
# ============================================================================

class SubscriptionCheckMiddleware:
    """
    Middleware to check subscription status on every request
    Adds subscription info to request object
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        # Add subscription to request
        if request.user and request.user.is_authenticated:
            request.subscription = get_active_subscription(request.user)
            
            # Add helper methods
            request.has_feature = lambda feature: self._has_feature(request, feature)
            request.has_module = lambda module: self._has_module(request, module)
            request.check_limit = lambda action: self._check_limit(request, action)
        else:
            request.subscription = None
        
        response = self.get_response(request)
        return response
    
    def _has_feature(self, request, feature_code):
        """Helper to check feature access"""
        if not request.subscription:
            return False
        
        allowed, _ = SubscriptionRulesEngine.can_use_feature(
            request.subscription,
            feature_code
        )
        return allowed
    
    def _has_module(self, request, module_code):
        """Helper to check module access"""
        if not request.subscription:
            return False
        
        return SubscriptionRulesEngine.can_access_module(
            request.subscription,
            module_code
        )
    
    def _check_limit(self, request, action):
        """Helper to check usage limits"""
        if not request.subscription:
            return False, "No active subscription"
        
        if action == 'create_project':
            return SubscriptionRulesEngine.can_create_project(request.subscription)
        elif action == 'add_user':
            return SubscriptionRulesEngine.can_add_user(request.subscription)
        else:
            return True, ""


# ============================================================================
# SUBSCRIPTION DECORATORS
# ============================================================================

def require_subscription(feature_code=None, module_code=None):
    """
    Decorator to require active subscription for view/function
    
    Usage:
        @require_subscription(feature_code='ai_features')
        def my_view(request):
            ...
        
        @require_subscription(module_code='pfd_converter')
        def another_view(request):
            ...
    """
    def decorator(func):
        def wrapper(request, *args, **kwargs):
            if not request.user or not request.user.is_authenticated:
                from django.http import JsonResponse
                return JsonResponse(
                    {'error': 'Authentication required'},
                    status=401
                )
            
            subscription = get_active_subscription(request.user)
            
            if not subscription:
                from django.http import JsonResponse
                return JsonResponse(
                    {'error': 'Active subscription required'},
                    status=403
                )
            
            # Check feature if specified
            if feature_code:
                allowed, message = SubscriptionRulesEngine.can_use_feature(
                    subscription,
                    feature_code
                )
                if not allowed:
                    from django.http import JsonResponse
                    return JsonResponse(
                        {'error': message},
                        status=403
                    )
            
            # Check module if specified
            if module_code:
                if not SubscriptionRulesEngine.can_access_module(subscription, module_code):
                    from django.http import JsonResponse
                    return JsonResponse(
                        {'error': f'Module {module_code} not available in your plan'},
                        status=403
                    )
            
            return func(request, *args, **kwargs)
        
        return wrapper
    return decorator


def check_usage_limit(action):
    """
    Decorator to check usage limits before executing action
    
    Usage:
        @check_usage_limit('create_project')
        def create_project_view(request):
            ...
    """
    def decorator(func):
        def wrapper(request, *args, **kwargs):
            if not request.user or not request.user.is_authenticated:
                from django.http import JsonResponse
                return JsonResponse(
                    {'error': 'Authentication required'},
                    status=401
                )
            
            subscription = get_active_subscription(request.user)
            
            if not subscription:
                from django.http import JsonResponse
                return JsonResponse(
                    {'error': 'Active subscription required'},
                    status=403
                )
            
            # Check limit based on action
            if action == 'create_project':
                allowed, message = SubscriptionRulesEngine.can_create_project(subscription)
            elif action == 'add_user':
                allowed, message = SubscriptionRulesEngine.can_add_user(subscription)
            elif action == 'upload_file':
                file_size = request.data.get('file_size_mb', 0)
                allowed, message = SubscriptionRulesEngine.can_upload_file(subscription, file_size)
            else:
                allowed, message = True, ""
            
            if not allowed:
                from django.http import JsonResponse
                return JsonResponse(
                    {'error': message, 'limit_reached': True},
                    status=403
                )
            
            return func(request, *args, **kwargs)
        
        return wrapper
    return decorator


# ============================================================================
# SUBSCRIPTION HELPERS FOR VIEWS
# ============================================================================

def get_subscription_context(user):
    """
    Get subscription context for templates
    Returns dict with subscription info
    """
    subscription = get_active_subscription(user)
    
    if not subscription:
        return {
            'has_subscription': False,
            'plan_name': 'Free',
            'features': {},
            'limits': {},
        }
    
    return {
        'has_subscription': True,
        'subscription': subscription,
        'plan_name': subscription.plan.display_name,
        'plan_code': subscription.plan.code,
        'is_trial': subscription.is_trial,
        'days_remaining': subscription.days_remaining,
        'features': subscription.plan.features,
        'limits': {
            'users': subscription.get_limit('max_users'),
            'storage': subscription.get_limit('max_storage_gb'),
            'projects': subscription.get_limit('max_projects'),
            'documents': subscription.get_limit('max_documents'),
            'api_calls': subscription.get_limit('max_api_calls_per_day'),
        },
        'allowed_modules': subscription.plan.allowed_modules,
    }
