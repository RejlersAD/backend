"""
RBAC Utility Functions
"""
from .models import AuditLog
from .rbac_config import is_module_enabled


def create_audit_log(user, action, resource_type, resource_id=None, resource_repr='',
                     changes=None, metadata=None, ip_address=None, user_agent='',
                     success=True, error_message=''):
    """
    Create an audit log entry
    """
    return AuditLog.objects.create(
        user=user,
        user_email=user.email if user else 'system',
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_repr=resource_repr,
        changes=changes or {},
        metadata=metadata or {},
        ip_address=ip_address,
        user_agent=user_agent,
        success=success,
        error_message=error_message
    )


def get_user_permissions(user):
    """
    Get all permissions for a user
    Returns list of permission codes
    """
    try:
        profile = user.rbac_profile
        permissions = profile.get_all_permissions()
        return [p.code for p in permissions]
    except:
        return []


def get_user_modules(user):
    """
    Get all accessible modules for a user
    Returns list of module codes
    SOFT-CODED: Filters out modules disabled by MODULE_FEATURE_FLAGS in rbac_config.py
    
    Args:
        user: Can be either User or UserProfile object
    
    Returns:
        list: List of module codes the user has access to
    """
    try:
        # Handle both User and UserProfile objects
        from apps.rbac.models import UserProfile
        if isinstance(user, UserProfile):
            profile = user
        else:
            profile = user.rbac_profile
        
        modules = profile.get_all_modules()
        # Filter out disabled modules
        enabled_modules = [m.code for m in modules if is_module_enabled(m.code)]
        return enabled_modules
    except:
        return []


def check_user_has_module_access(user, module_code):
    """
    Check if user has access to a specific module
    SOFT-CODED: Returns False if module is disabled by feature flag
    
    Args:
        user: Can be either User or UserProfile object
        module_code: Module code to check access for
    
    Returns:
        bool: True if user has access, False otherwise
    """
    # Check if module is globally disabled
    if not is_module_enabled(module_code):
        return False
    
    # Handle both User and UserProfile objects
    from apps.rbac.models import UserProfile
    if isinstance(user, UserProfile):
        # It's a UserProfile - check the linked User's is_superuser
        if hasattr(user, 'user') and user.user.is_superuser:
            return True
        profile = user
    else:
        # It's a User object
        if user.is_superuser:
            return True
        # Get the profile
        try:
            profile = user.rbac_profile
        except:
            return False
    
    # Get modules for the profile
    try:
        modules = profile.get_all_modules()
        enabled_modules = [m.code for m in modules if is_module_enabled(m.code)]
        return module_code in enabled_modules
    except:
        return False


def get_user_accessible_features(user):
    """
    Get list of features/routes user has access to based on their modules
    Returns: dict with feature codes and their accessible status
    """
    user_modules = get_user_modules(user)
    
    # Map modules to frontend feature routes
    feature_map = {
        'PID': {
            'code': 'PID',
            'name': 'P&ID Design Verification',
            'route': '/pid/upload',
            'accessible': 'PID' in user_modules
        },
        'PFD': {
            'code': 'PFD',
            'name': 'PFD to P&ID Converter',
            'route': '/pfd/upload',
            'accessible': 'PFD' in user_modules
        },
        'CRS': {
            'code': 'CRS',
            'name': 'CRS Document Management',
            'route': '/crs/documents',
            'accessible': 'CRS' in user_modules
        },
        'PROJECT_CONTROL': {
            'code': 'PROJECT_CONTROL',
            'name': 'Project Control',
            'route': '/projects',
            'accessible': 'PROJECT_CONTROL' in user_modules
        }
    }
    
    return feature_map


def check_permission(user, permission_code):
    """
    Check if user has specific permission
    """
    try:
        profile = user.rbac_profile
        return profile.has_permission(permission_code)
    except:
        return False


def check_module_access(user, module_code):
    """
    Check if user has access to specific module
    """
    try:
        profile = user.rbac_profile
        return profile.has_module_access(module_code)
    except:
        return False
