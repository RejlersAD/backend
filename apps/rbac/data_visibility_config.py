"""
Data Visibility Configuration
Smart Row-Level Security (RLS) system for department-based data access

PHILOSOPHY:
-----------
1. Personal Isolation by Default: Users see only their own data
2. Module-Based Collaboration: Users with same module access see each other's data
3. Role-Based Override: Admins/Super Admins see everything
4. Soft-Coded: Easy to customize per app/module

EXAMPLE SCENARIOS:
-----------------
1. CRS Documents:
   - Regular user: Sees only documents they uploaded
   - Users with CRS module: See all CRS documents (team collaboration)
   - Admin: Sees everything

2. QHSE Projects:
   - Regular user: Sees only their assigned projects
   - Users with QHSE module: See all QHSE projects (department collaboration)
   - Admin: Sees everything

3. Finance Records:
   - Regular user: Blocked (no access)
   - Users with Finance module: See all finance records (department access)
   - Admin: Sees everything
"""
from typing import Dict, List, Optional
from django.db.models import Q


# ============================================================================
# DATA VISIBILITY STRATEGIES
# ============================================================================

class VisibilityStrategy:
    """Base class for visibility strategies"""
    PERSONAL = 'personal'  # User sees only their own data
    MODULE_TEAM = 'module_team'  # Users with same module see each other's data
    ORGANIZATION = 'organization'  # All users in same organization see data
    PUBLIC = 'public'  # Everyone sees everything (use with caution)
    CUSTOM = 'custom'  # Custom filtering logic


# ============================================================================
# MODULE VISIBILITY CONFIGURATION
# ============================================================================

DATA_VISIBILITY_CONFIG = {
    # CRS Documents Module
    'crs_documents': {
        'strategy': VisibilityStrategy.MODULE_TEAM,
        'module_code': 'crs_documents',
        'owner_field': 'uploaded_by',  # Field that tracks record owner
        'description': 'CRS users can see all CRS documents for collaboration',
        'exceptions': {
            'archived': VisibilityStrategy.PERSONAL,  # Archived docs only visible to owner
        }
    },
    
    # QHSE Module
    'qhse': {
        'strategy': VisibilityStrategy.MODULE_TEAM,
        'module_code': 'qhse',
        'owner_field': None,  # No single owner, team-based by default
        'description': 'QHSE team members see all QHSE projects for collaboration',
        'project_manager_field': 'project_manager',  # Additional access for PMs
        'quality_eng_field': 'project_quality_eng',  # Additional access for QEs
    },
    
    # Finance Module
    'finance': {
        'strategy': VisibilityStrategy.MODULE_TEAM,
        'module_code': 'finance',
        'owner_field': 'created_by',
        'description': 'Finance team sees all financial records',
    },
    
    # PFD Converter Module
    'pfd_converter': {
        'strategy': VisibilityStrategy.MODULE_TEAM,
        'module_code': 'pfd_converter',
        'owner_field': 'uploaded_by',
        'description': 'PFD users can see all PFD documents',
    },
    
    # PID Analysis Module
    'pid_analysis': {
        'strategy': VisibilityStrategy.MODULE_TEAM,
        'module_code': 'pid_analysis',
        'owner_field': 'converted_by',
        'description': 'PID users can see all PID conversions',
    },
    
    # Project Management
    'project_management': {
        'strategy': VisibilityStrategy.CUSTOM,
        'owner_field': 'owner',
        'description': 'Users see projects they own or are members of',
        'custom_filter': 'project_member_filter',  # See below
    },
    
    # Procurement Module
    'procurement': {
        'strategy': VisibilityStrategy.MODULE_TEAM,
        'module_code': 'procurement',
        'owner_field': 'created_by',
        'description': 'Procurement team sees all procurement records',
    },
    
    # DesignIQ Module
    'designiq': {
        'strategy': VisibilityStrategy.PERSONAL,
        'owner_field': 'created_by',
        'description': 'Design projects are personal unless shared',
        'allow_team_sharing': True,  # Can be extended for team projects
    },
    
    # File Storage
    'file_storage': {
        'strategy': VisibilityStrategy.ORGANIZATION,
        'owner_field': 'uploaded_by',
        'description': 'Files visible within organization',
    },
    
    # Notifications
    'notifications': {
        'strategy': VisibilityStrategy.PERSONAL,
        'owner_field': 'recipient',
        'description': 'Users see only their own notifications',
    },
    
    # User Management (Admin only)
    'user_management': {
        'strategy': VisibilityStrategy.ORGANIZATION,
        'description': 'Admins see users in their organization',
    },
}


# ============================================================================
# ADMIN ROLE CODES (Users with these roles bypass all restrictions)
# ============================================================================

ADMIN_ROLES = [
    'super_admin',
    'admin',
    'administrator',
]


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_visibility_config(module_code: str) -> Optional[Dict]:
    """
    Get visibility configuration for a module
    
    Args:
        module_code: The module code (e.g., 'crs_documents', 'qhse')
    
    Returns:
        Configuration dict or None if not found
    """
    return DATA_VISIBILITY_CONFIG.get(module_code)


def is_admin_user(user) -> bool:
    """
    Check if user has admin role (bypasses all restrictions)
    
    Args:
        user: Django User object
    
    Returns:
        True if user is admin, False otherwise
    """
    try:
        from apps.rbac.models import UserProfile
        profile = UserProfile.objects.filter(user=user, is_deleted=False).first()
        if not profile:
            return False
        
        # Check if user has any admin role
        has_admin_role = profile.roles.filter(
            code__in=ADMIN_ROLES,
            is_active=True
        ).exists()
        
        return has_admin_role or user.is_staff or user.is_superuser
    except Exception:
        return user.is_staff or user.is_superuser


def user_has_module_access(user, module_code: str) -> bool:
    """
    Check if user has access to a specific module
    
    Args:
        user: Django User object
        module_code: The module code to check
    
    Returns:
        True if user has access, False otherwise
    """
    try:
        from apps.rbac.models import UserProfile
        profile = UserProfile.objects.filter(user=user, is_deleted=False).first()
        if not profile:
            return False
        
        # Get all modules user has access to
        user_modules = profile.get_all_modules()
        return any(module.code == module_code for module in user_modules)
    except Exception:
        return False


def get_users_with_module_access(module_code: str) -> List:
    """
    Get all users who have access to a specific module
    
    Args:
        module_code: The module code
    
    Returns:
        List of User IDs
    """
    try:
        from apps.rbac.models import UserProfile, Module
        
        # Get the module
        module = Module.objects.filter(code=module_code, is_active=True).first()
        if not module:
            return []
        
        # Get all profiles with access to this module
        profiles = UserProfile.objects.filter(
            is_deleted=False,
            userrole__role__rolemodule__module=module
        ).distinct()
        
        return [profile.user.id for profile in profiles]
    except Exception:
        return []


def build_visibility_filter(user, module_code: str, owner_field: str = None, additional_filters: Q = None) -> Q:
    """
    Build Django Q filter for data visibility based on configuration
    
    Args:
        user: Django User object
        module_code: The module code
        owner_field: Field name that stores the owner/creator (e.g., 'uploaded_by', 'created_by')
        additional_filters: Additional Q filters to apply
    
    Returns:
        Django Q object for filtering queryset
    """
    # Admins see everything
    if is_admin_user(user):
        return Q()  # No filtering (returns all)
    
    # Get configuration
    config = get_visibility_config(module_code)
    if not config:
        # Default to personal visibility if no config
        if owner_field:
            return Q(**{owner_field: user})
        return Q()  # Return all if no owner field
    
    strategy = config['strategy']
    owner_field = owner_field or config.get('owner_field')
    
    # Build filter based on strategy
    filter_q = Q()
    
    if strategy == VisibilityStrategy.PERSONAL:
        # User sees only their own data
        if owner_field:
            filter_q = Q(**{owner_field: user})
    
    elif strategy == VisibilityStrategy.MODULE_TEAM:
        # Users with same module access see each other's data
        if user_has_module_access(user, module_code):
            # User has module access - see all data from module team
            team_user_ids = get_users_with_module_access(module_code)
            if owner_field:
                filter_q = Q(**{f'{owner_field}__id__in': team_user_ids})
            # If no owner field, return all (team sees everything)
        else:
            # User doesn't have module access
            if owner_field:
                filter_q = Q(**{owner_field: user})  # Fallback to personal
            else:
                filter_q = Q(pk=None)  # Return nothing
    
    elif strategy == VisibilityStrategy.ORGANIZATION:
        # All users in same organization see data
        try:
            from apps.rbac.models import UserProfile
            profile = UserProfile.objects.filter(user=user, is_deleted=False).first()
            if profile and owner_field:
                org_users = UserProfile.objects.filter(
                    organization=profile.organization,
                    is_deleted=False
                ).values_list('user_id', flat=True)
                filter_q = Q(**{f'{owner_field}__id__in': org_users})
        except Exception:
            if owner_field:
                filter_q = Q(**{owner_field: user})
    
    elif strategy == VisibilityStrategy.PUBLIC:
        # Everyone sees everything
        filter_q = Q()
    
    elif strategy == VisibilityStrategy.CUSTOM:
        # Custom logic (implement in view)
        if owner_field:
            filter_q = Q(**{owner_field: user})
    
    # Apply additional filters
    if additional_filters:
        filter_q = filter_q & additional_filters
    
    return filter_q


def get_visibility_description(module_code: str) -> str:
    """
    Get human-readable description of visibility rules for a module
    
    Args:
        module_code: The module code
    
    Returns:
        Description string
    """
    config = get_visibility_config(module_code)
    if not config:
        return "No visibility rules configured"
    
    return config.get('description', 'Custom visibility rules apply')


# ============================================================================
# CUSTOM FILTER FUNCTIONS
# ============================================================================

def project_member_filter(user, queryset):
    """
    Custom filter for project management
    Users see projects they own or are members of
    """
    return queryset.filter(
        Q(owner=user) | Q(team_members=user)
    ).distinct()


# ============================================================================
# AUDIT & COMPLIANCE
# ============================================================================

def get_user_agent_from_request(request=None):
    """
    Safely extract user agent from request
    Soft-coded with multiple fallback options
    
    Args:
        request: Django request object (optional)
        
    Returns:
        str: User agent string or empty string as safe default
    """
    if not request:
        return ''
    
    # Try multiple headers (soft-coded list of possible headers)
    headers_to_check = [
        'HTTP_USER_AGENT',
        'User-Agent',
        'user-agent',
    ]
    
    for header in headers_to_check:
        user_agent = request.META.get(header, '')
        if user_agent:
            return user_agent[:500]  # Truncate to reasonable length
    
    return ''  # Safe default - empty string instead of None


def log_data_access(user, module_code: str, record_count: int, filters_applied: str = None, request=None):
    """
    Log data access for audit trail
    
    Args:
        user: Django User object
        module_code: Module being accessed
        record_count: Number of records returned
        filters_applied: Description of filters applied
        request: Optional request object for capturing user_agent and IP
    """
    try:
        from apps.rbac.models import AuditLog
        
        # Soft-coded: Extract metadata safely with fallbacks
        user_agent = get_user_agent_from_request(request)
        ip_address = request.META.get('REMOTE_ADDR', '') if request else ''
        
        AuditLog.objects.create(
            user=user,
            user_email=user.email,
            action='data_access',
            resource_type=module_code,
            resource_id=None,
            changes={'record_count': record_count, 'filters': filters_applied},
            ip_address=ip_address or '',  # Empty string instead of None
            user_agent=user_agent or '',  # Empty string instead of None
            success=True
        )
    except Exception as e:
        # Don't fail requests due to logging errors
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Failed to log data access: {str(e)}")


# ============================================================================
# USAGE EXAMPLES
# ============================================================================

"""
EXAMPLE 1: Apply in ViewSet get_queryset()
------------------------------------------

from apps.rbac.data_visibility_config import build_visibility_filter

class CRSDocumentViewSet(viewsets.ModelViewSet):
    def get_queryset(self):
        queryset = CRSDocument.objects.all()
        
        # Apply visibility filter
        visibility_filter = build_visibility_filter(
            user=self.request.user,
            module_code='crs_documents',
            owner_field='uploaded_by'
        )
        
        return queryset.filter(visibility_filter)


EXAMPLE 2: Check user access
-----------------------------

from apps.rbac.data_visibility_config import user_has_module_access, is_admin_user

def my_view(request):
    if is_admin_user(request.user):
        # Admin access - show everything
        pass
    elif user_has_module_access(request.user, 'qhse'):
        # User has QHSE module access
        pass
    else:
        # No access
        return Response({'error': 'No access'}, status=403)


EXAMPLE 3: Get team members
----------------------------

from apps.rbac.data_visibility_config import get_users_with_module_access

qhse_team_ids = get_users_with_module_access('qhse')
qhse_team = User.objects.filter(id__in=qhse_team_ids)
"""
