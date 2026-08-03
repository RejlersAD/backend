"""
Dynamic Data Visibility Configuration
Auto-discovers modules and applies smart defaults for future features

FEATURES:
---------
1. Auto-Discovery: Reads modules from database automatically
2. Smart Defaults: Applies sensible defaults for unconfigured modules
3. Convention over Configuration: Uses naming conventions
4. Hot Reload: Updates when new modules are added
5. Override Support: Manual config takes precedence
"""
from typing import Dict, Optional, List
from django.db.models import Q
from django.core.cache import cache


class VisibilityStrategy:
    """Visibility strategies"""
    PERSONAL = 'personal'
    MODULE_TEAM = 'module_team'
    ORGANIZATION = 'organization'
    PUBLIC = 'public'
    CUSTOM = 'custom'


# ============================================================================
# MANUAL CONFIGURATION (Takes precedence over auto-discovery)
# ============================================================================

MANUAL_VISIBILITY_CONFIG = {
    # Modules with specific requirements
    'notifications': {
        'strategy': VisibilityStrategy.PERSONAL,
        'owner_field': 'recipient',
        'description': 'Users see only their own notifications',
    },
    'user_management': {
        'strategy': VisibilityStrategy.ORGANIZATION,
        'description': 'Admins see users in their organization',
    },
}


# ============================================================================
# AUTO-DISCOVERY CONFIGURATION
# ============================================================================

class AutoDiscoveryConfig:
    """Configuration for automatic module discovery"""
    
    # Default strategy for modules not manually configured
    DEFAULT_STRATEGY = VisibilityStrategy.MODULE_TEAM
    
    # Common owner field names (checked in order)
    COMMON_OWNER_FIELDS = [
        'created_by',
        'uploaded_by',
        'owner',
        'user',
        'converted_by',
        'assigned_to',
    ]
    
    # Modules that should be personal by default (pattern matching)
    PERSONAL_PATTERNS = [
        'notification',
        'preference',
        'setting',
        'profile',
        'dashboard',
    ]
    
    # Modules that should be organization-wide (pattern matching)
    ORGANIZATION_PATTERNS = [
        'user',
        'organization',
        'system',
        'admin',
        'audit',
    ]
    
    # Modules that should be team-based (default for most)
    TEAM_PATTERNS = [
        'document',
        'project',
        'report',
        'analysis',
        'converter',
        'management',
    ]


# ============================================================================
# DYNAMIC MODULE DISCOVERY
# ============================================================================

def get_all_active_modules() -> List[Dict]:
    """
    Get all active modules from database
    Returns list of module info dicts
    """
    try:
        from apps.rbac.models import Module
        
        modules = Module.objects.filter(is_active=True).values(
            'id', 'code', 'name', 'description'
        )
        return list(modules)
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Could not load modules from database: {str(e)}")
        return []


def detect_strategy_from_module_code(module_code: str) -> str:
    """
    Auto-detect appropriate strategy based on module code
    Uses pattern matching and conventions
    """
    code_lower = module_code.lower()
    
    # Check personal patterns
    for pattern in AutoDiscoveryConfig.PERSONAL_PATTERNS:
        if pattern in code_lower:
            return VisibilityStrategy.PERSONAL
    
    # Check organization patterns
    for pattern in AutoDiscoveryConfig.ORGANIZATION_PATTERNS:
        if pattern in code_lower:
            return VisibilityStrategy.ORGANIZATION
    
    # Default to team-based for collaboration
    return VisibilityStrategy.MODULE_TEAM


def detect_owner_field_from_model(model_class) -> Optional[str]:
    """
    Auto-detect owner field from model class
    Checks common field names
    """
    if not model_class:
        return None
    
    try:
        # Get all field names from model
        field_names = [f.name for f in model_class._meta.get_fields()]
        
        # Check for common owner fields in priority order
        for field_name in AutoDiscoveryConfig.COMMON_OWNER_FIELDS:
            if field_name in field_names:
                return field_name
        
        return None
    except Exception:
        return None


def build_dynamic_config(module_code: str, model_class=None) -> Dict:
    """
    Build configuration dynamically for a module
    
    Args:
        module_code: Module code (e.g., 'new_feature')
        model_class: Optional model class for field detection
    
    Returns:
        Configuration dict
    """
    # Check manual config first
    if module_code in MANUAL_VISIBILITY_CONFIG:
        return MANUAL_VISIBILITY_CONFIG[module_code]
    
    # Auto-detect strategy
    strategy = detect_strategy_from_module_code(module_code)
    
    # Auto-detect owner field
    owner_field = detect_owner_field_from_model(model_class)
    
    # Build config
    config = {
        'strategy': strategy,
        'module_code': module_code,
        'description': f'Auto-configured: {strategy} strategy',
        'auto_discovered': True,
    }
    
    if owner_field:
        config['owner_field'] = owner_field
    
    return config


def get_all_visibility_configs() -> Dict[str, Dict]:
    """
    Get all visibility configurations (manual + auto-discovered)
    Cached for performance
    
    Returns:
        Dict of module_code -> config
    """
    cache_key = 'visibility_configs_all'
    cached = cache.get(cache_key)
    
    if cached:
        return cached
    
    # Start with manual configs
    all_configs = dict(MANUAL_VISIBILITY_CONFIG)
    
    # Add auto-discovered modules
    active_modules = get_all_active_modules()
    
    for module in active_modules:
        module_code = module['code']
        
        # Skip if manually configured
        if module_code in all_configs:
            continue
        
        # Build dynamic config
        all_configs[module_code] = build_dynamic_config(module_code)
    
    # Cache for 5 minutes
    cache.set(cache_key, all_configs, 300)
    
    return all_configs


def clear_visibility_cache():
    """Clear visibility configuration cache"""
    cache_key = 'visibility_configs_all'
    cache.delete(cache_key)


# ============================================================================
# ADMIN ROLE CODES
# ============================================================================

ADMIN_ROLES = [
    'super_admin',
    'admin',
    'administrator',
]


# ============================================================================
# HELPER FUNCTIONS (Enhanced with dynamic config)
# ============================================================================

def get_visibility_config(module_code: str, model_class=None) -> Optional[Dict]:
    """
    Get visibility configuration for a module
    Auto-creates config if not exists
    
    Args:
        module_code: The module code
        model_class: Optional model class for owner field detection
    
    Returns:
        Configuration dict (never None - always returns something)
    """
    # Try to get from all configs (cached)
    all_configs = get_all_visibility_configs()
    
    if module_code in all_configs:
        return all_configs[module_code]
    
    # Not in cache - build dynamically
    config = build_dynamic_config(module_code, model_class)
    
    # Log auto-discovery
    import logging
    logger = logging.getLogger(__name__)
    logger.info(
        f"[Data Visibility] Auto-configured module '{module_code}' "
        f"with strategy '{config['strategy']}'"
    )
    
    return config


def is_admin_user(user) -> bool:
    """Check if user has admin role (bypasses all restrictions)"""
    try:
        from apps.rbac.models import UserProfile
        profile = UserProfile.objects.filter(user=user, is_deleted=False).first()
        if not profile:
            return False
        
        has_admin_role = profile.roles.filter(
            code__in=ADMIN_ROLES,
            is_active=True
        ).exists()
        
        return has_admin_role or user.is_staff or user.is_superuser
    except Exception:
        return user.is_staff or user.is_superuser


def user_has_module_access(user, module_code: str) -> bool:
    """Check if user has access to a specific module"""
    try:
        from apps.rbac.models import UserProfile
        profile = UserProfile.objects.filter(user=user, is_deleted=False).first()
        if not profile:
            return False
        
        user_modules = profile.get_all_modules()
        return any(module.code == module_code for module in user_modules)
    except Exception:
        return False


def get_users_with_module_access(module_code: str) -> List:
    """Get all users who have access to a specific module"""
    try:
        from apps.rbac.models import UserProfile, Module
        
        module = Module.objects.filter(code=module_code, is_active=True).first()
        if not module:
            return []
        
        profiles = UserProfile.objects.filter(
            is_deleted=False,
            userrole__role__rolemodule__module=module
        ).distinct()
        
        return [profile.user.id for profile in profiles]
    except Exception:
        return []


def build_visibility_filter(
    user,
    module_code: str,
    owner_field: str = None,
    additional_filters: Q = None,
    model_class=None
) -> Q:
    """
    Build Django Q filter for data visibility (Dynamic version)
    
    Args:
        user: Django User object
        module_code: The module code
        owner_field: Field name that stores the owner (auto-detected if None)
        additional_filters: Additional Q filters to apply
        model_class: Model class for owner field auto-detection
    
    Returns:
        Django Q object for filtering queryset
    """
    # Admins see everything
    if is_admin_user(user):
        return Q()
    
    # Get configuration (will auto-discover if needed)
    config = get_visibility_config(module_code, model_class)
    
    # Use provided owner_field or get from config
    if owner_field is None:
        owner_field = config.get('owner_field')
    
    strategy = config['strategy']
    
    # Build filter based on strategy
    filter_q = Q()
    
    if strategy == VisibilityStrategy.PERSONAL:
        if owner_field:
            filter_q = Q(**{owner_field: user})
    
    elif strategy == VisibilityStrategy.MODULE_TEAM:
        if user_has_module_access(user, module_code):
            team_user_ids = get_users_with_module_access(module_code)
            if owner_field:
                filter_q = Q(**{f'{owner_field}__id__in': team_user_ids})
        else:
            if owner_field:
                filter_q = Q(**{owner_field: user})
            else:
                filter_q = Q(pk=None)
    
    elif strategy == VisibilityStrategy.ORGANIZATION:
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
        filter_q = Q()
    
    elif strategy == VisibilityStrategy.CUSTOM:
        if owner_field:
            filter_q = Q(**{owner_field: user})
    
    # Apply additional filters
    if additional_filters:
        filter_q = filter_q & additional_filters
    
    return filter_q


def get_visibility_description(module_code: str) -> str:
    """Get human-readable description of visibility rules"""
    config = get_visibility_config(module_code)
    if not config:
        return "Using default visibility rules"
    
    description = config.get('description', 'Custom visibility rules apply')
    
    if config.get('auto_discovered'):
        description += " (Auto-configured)"
    
    return description


# ============================================================================
# AUDIT & LOGGING
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
    """Log data access for audit trail with soft-coded metadata extraction"""
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
            changes={
                'record_count': record_count,
                'filters': filters_applied,
                'auto_configured': get_visibility_config(module_code).get('auto_discovered', False)
            },
            ip_address=ip_address or '',  # Empty string instead of None
            user_agent=user_agent or '',  # Empty string instead of None
            success=True
        )
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Failed to log data access: {str(e)}")


# ============================================================================
# ADMIN FUNCTIONS
# ============================================================================

def register_module_visibility(module_code: str, config: Dict):
    """
    Manually register a module's visibility configuration
    Useful for overriding auto-discovery
    
    Args:
        module_code: Module code
        config: Configuration dict
    """
    MANUAL_VISIBILITY_CONFIG[module_code] = config
    clear_visibility_cache()
    
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"[Data Visibility] Manually registered config for '{module_code}'")


def get_visibility_report() -> Dict:
    """
    Get comprehensive visibility configuration report
    Useful for debugging and documentation
    
    Returns:
        Report dict with all configurations
    """
    all_configs = get_all_visibility_configs()
    
    report = {
        'total_modules': len(all_configs),
        'manual_configs': len(MANUAL_VISIBILITY_CONFIG),
        'auto_discovered': len([c for c in all_configs.values() if c.get('auto_discovered')]),
        'strategies': {},
        'modules': all_configs,
    }
    
    # Count by strategy
    for config in all_configs.values():
        strategy = config['strategy']
        report['strategies'][strategy] = report['strategies'].get(strategy, 0) + 1
    
    return report


# ============================================================================
# USAGE EXAMPLES
# ============================================================================

"""
EXAMPLE 1: Automatic discovery for new module
----------------------------------------------

# You add a new module in RBAC system: "inventory_management"
# No manual configuration needed!

class InventoryViewSet(TeamCollaborationMixin, viewsets.ModelViewSet):
    visibility_module_code = 'inventory_management'  # Auto-discovers config!
    queryset = Inventory.objects.all()
    
# System automatically:
# 1. Detects it's a "management" module → uses MODULE_TEAM strategy
# 2. Looks for common owner fields (created_by, uploaded_by, etc.)
# 3. Applies smart defaults
# 4. Logs auto-configuration


EXAMPLE 2: Override auto-discovery when needed
-----------------------------------------------

# If auto-discovery isn't right, manually configure:

from apps.rbac.data_visibility_config_dynamic import register_module_visibility

register_module_visibility('special_module', {
    'strategy': VisibilityStrategy.PERSONAL,
    'owner_field': 'special_owner',
    'description': 'Custom requirements',
})


EXAMPLE 3: Get visibility report
---------------------------------

from apps.rbac.data_visibility_config_dynamic import get_visibility_report

report = get_visibility_report()
print(f"Total modules: {report['total_modules']}")
print(f"Auto-discovered: {report['auto_discovered']}")
print(f"Strategies: {report['strategies']}")


EXAMPLE 4: Future-proof ViewSet
--------------------------------

class NewFeatureViewSet(TeamCollaborationMixin, viewsets.ModelViewSet):
    # Just set module code - everything else is automatic!
    visibility_module_code = 'new_feature'
    
    queryset = NewFeature.objects.all()
    serializer_class = NewFeatureSerializer
    
# System will:
# ✅ Auto-detect appropriate strategy based on name
# ✅ Auto-detect owner field from model
# ✅ Apply sensible defaults
# ✅ Cache configuration
# ✅ Log for audit
"""
