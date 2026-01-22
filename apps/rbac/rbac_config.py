"""
RBAC Configuration
Centralized configuration for Role-Based Access Control system
All RBAC settings in one place using soft coding principles
"""

# Module Assignment Strategy
MODULE_ASSIGNMENT_CONFIG = {
    'strategy': 'role_based',  # 'role_based' or 'direct'
    'create_custom_roles': True,  # Create custom roles for module-based assignments
    'custom_role_prefix': 'custom_',
    'custom_role_level': 10,  # Level for custom roles
    'clear_existing_on_update': True,  # Clear existing module assignments when updating
    'assign_permissions_automatically': True,  # Auto-assign all module permissions
    'fallback_to_default_role': True,  # Assign default role if no roles specified
}

# Default Role Settings
DEFAULT_ROLE_CONFIG = {
    'code': 'user',
    'name': 'Regular User',
    'level': 100,
    'auto_assign_on_creation': True,
}

# Admin Role Detection
ADMIN_ROLE_CODES = ['super_admin', 'admin', 'administrator']
SUPERADMIN_ROLE_CODES = ['super_admin', 'superadmin']

# Module Access Rules
MODULE_ACCESS_RULES = {
    'check_role_first': True,  # Check role-based access first
    'check_direct_assignment': True,  # Then check direct module assignment
    'admin_has_all_access': True,  # Admins bypass module checks
    'superadmin_has_all_access': True,  # Super admins bypass all checks
}

# Audit Logging
AUDIT_CONFIG = {
    'log_role_assignments': True,
    'log_module_assignments': True,
    'log_permission_changes': True,
    'log_access_denials': True,
    'log_module_access_checks': True,  # Detailed logging for debugging
}

# User Profile Settings
USER_PROFILE_CONFIG = {
    'require_organization': False,  # Organization is optional
    'auto_create_profile': True,  # Auto-create profile for existing users
    'default_status': 'active',
    'require_email_verification': False,  # Email verification optional
}

# Module Categories (for UI grouping)
MODULE_CATEGORIES = {
    'core': {
        'name': 'Core Modules',
        'description': 'Essential system modules',
        'icon': '🔧',
        'order': 1
    },
    'engineering': {
        'name': 'Engineering',
        'description': 'Engineering and design modules',
        'icon': '⚙️',
        'order': 2
    },
    'business': {
        'name': 'Business Operations',
        'description': 'Finance, procurement, and business modules',
        'icon': '💼',
        'order': 3
    },
    'compliance': {
        'name': 'QHSE & Compliance',
        'description': 'Quality, health, safety, and environment',
        'icon': '🛡️',
        'order': 4
    },
    'admin': {
        'name': 'Administration',
        'description': 'System administration modules',
        'icon': '👨‍💼',
        'order': 5
    }
}

# Error Messages
ERROR_MESSAGES = {
    'no_roles': 'User has no roles assigned. Please assign at least one role.',
    'no_modules': 'User has no accessible modules. Please assign modules or roles.',
    'module_not_found': 'Requested module not found or inactive.',
    'access_denied': 'You do not have access to this module.',
    'invalid_role': 'Invalid or inactive role specified.',
    'role_level_insufficient': 'Your role level is insufficient for this action.',
}

# Success Messages
SUCCESS_MESSAGES = {
    'role_assigned': 'Role successfully assigned to user.',
    'module_assigned': 'Module access granted successfully.',
    'permission_granted': 'Permission granted successfully.',
    'user_created': 'User created successfully with assigned roles and modules.',
}

def get_custom_role_code(email):
    """Generate custom role code from email"""
    username = email.split('@')[0]
    return f"{MODULE_ASSIGNMENT_CONFIG['custom_role_prefix']}{username}"

def get_custom_role_name(first_name, last_name):
    """Generate custom role name from user details"""
    full_name = f"{first_name} {last_name}".strip()
    return f"Custom Role - {full_name}" if full_name else "Custom Role"

def should_create_custom_role():
    """Check if custom roles should be created for module assignments"""
    return MODULE_ASSIGNMENT_CONFIG['create_custom_roles']

def is_admin_role(role_code):
    """Check if a role code represents an admin role"""
    return role_code.lower() in ADMIN_ROLE_CODES

def is_superadmin_role(role_code):
    """Check if a role code represents a super admin role"""
    return role_code.lower() in SUPERADMIN_ROLE_CODES
