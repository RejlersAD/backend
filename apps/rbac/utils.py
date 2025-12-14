"""
RBAC Utility Functions
"""
from .models import AuditLog


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
    """
    try:
        profile = user.rbac_profile
        modules = profile.get_all_modules()
        return [m.code for m in modules]
    except:
        return []


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
