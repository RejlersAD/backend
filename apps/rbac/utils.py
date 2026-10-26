"""
RBAC Utility Functions
"""
from django.utils import timezone
from .models import AuditLog, FieldPermission, RowLevelPermission
from .rbac_config import is_module_enabled


def create_audit_log(user, action, resource_type, resource_id=None, resource_repr='',
                     changes=None, metadata=None, ip_address=None, user_agent='',
                     success=True, error_message=''):
    """
    Create an audit log entry
    """
    entry = AuditLog.objects.create(
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
    from .audit_context import current_audits
    entries = current_audits.get()
    if entries is not None:
        entries.append(entry)
    return entry


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


def get_field_permissions(user, model_name):
    """
    Return dict mapping field_name -> {'can_read': bool, 'can_write': bool}
    for all FieldPermission entries applicable to the user.
    User-specific overrides take precedence over role-level grants.
    """
    try:
        profile = user.rbac_profile
    except Exception:
        return {}

    now = timezone.now()
    # Role-level field permissions (via effective roles)
    role_ids = list(profile._effective_role_ids())
    role_perms = FieldPermission.objects.filter(
        role_id__in=role_ids,
        model_name=model_name,
        is_active=True,
    )
    # User-specific overrides
    user_perms = FieldPermission.objects.filter(
        user_profile=profile,
        model_name=model_name,
        is_active=True,
    )

    result = {}
    # Start with role-level grants
    for fp in role_perms:
        result[fp.field_name] = {'can_read': fp.can_read, 'can_write': fp.can_write}
    # User overrides take precedence
    for fp in user_perms:
        result[fp.field_name] = {'can_read': fp.can_read, 'can_write': fp.can_write}
    return result


def filter_serializer_fields(serializer, user, model_name):
    """
    Remove fields from a serializer that the user cannot read.
    Returns the serializer with filtered fields.
    """
    if not hasattr(serializer, 'fields'):
        return serializer
    perms = get_field_permissions(user, model_name)
    if not perms:
        return serializer
    fields_to_remove = []
    for field_name in serializer.fields:
        if field_name in perms and not perms[field_name]['can_read']:
            fields_to_remove.append(field_name)
    for field_name in fields_to_remove:
        serializer.fields.pop(field_name, None)
    return serializer


def filter_validated_data(validated_data, user, model_name):
    """
    Remove fields from validated_data that the user cannot write.
    Returns filtered dict.
    """
    perms = get_field_permissions(user, model_name)
    if not perms:
        return validated_data
    return {
        k: v for k, v in validated_data.items()
        if k not in perms or perms[k]['can_write']
    }


def apply_row_level_filter(queryset, user, model_name):
    """
    Apply row-level permissions to a queryset based on the user's roles.
    Returns the filtered queryset.
    Super admins see all rows.
    """
    try:
        profile = user.rbac_profile
    except Exception:
        return queryset.none()

    if profile.is_super_admin():
        return queryset

    now = timezone.now()
    role_ids = list(profile._effective_role_ids())

    # Collect all row-level permissions for this user's roles
    rlp_role = RowLevelPermission.objects.filter(
        role_id__in=role_ids,
        model_name=model_name,
        is_active=True,
    )
    rlp_user = RowLevelPermission.objects.filter(
        user_profile=profile,
        model_name=model_name,
        is_active=True,
    )

    # Combine filters with OR (any matching grant allows access)
    from django.db.models import Q
    combined_q = Q()

    for rlp in rlp_role:
        combined_q |= _build_row_filter(rlp, user, profile)

    for rlp in rlp_user:
        combined_q |= _build_row_filter(rlp, user, profile)

    if combined_q:
        return queryset.filter(combined_q)
    # No row-level permissions defined -> no access (deny by default)
    return queryset.none()


def _build_row_filter(rlp, user, profile):
    """Build a Django Q filter from a RowLevelPermission."""
    from django.db.models import Q
    field = rlp.filter_field
    ftype = rlp.filter_type
    value = rlp.filter_value

    if ftype == 'exact':
        if value:
            return Q(**{field: value})
        return Q()
    elif ftype == 'org_user':
        return Q(**{field: profile.organization_id})
    elif ftype == 'org_department':
        return Q(**{field: profile.organization_id})
    elif ftype == 'user_owner':
        return Q(**{field: user.id})
    return Q()
