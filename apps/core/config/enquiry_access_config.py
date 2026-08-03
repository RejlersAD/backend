"""
Enquiry Access Configuration - SOFT-CODED
==========================================
Centralized configuration for enquiry management access control.

This file defines which users and roles have access to the enquiry management
functionality without hardcoding permissions in the core logic.

RBAC Integration:
- Primary access control: RBAC module 'enquiry_management' (defined in rbac_config.py)
- Users with 'enquiry_management' module access can view/manage enquiries
- ICT Admin role includes this module by default

Special Access:
- Listed users are granted enquiry_management access regardless of role
- Useful for system accounts, support staff, or emergency access
"""

# ─────────────────────────────────────────────────────────────────────────────
# SPECIAL USERS WITH ENQUIRY ACCESS
# Users listed here will be granted 'enquiry_management' module access
# regardless of their assigned role.
# ─────────────────────────────────────────────────────────────────────────────
ENQUIRY_SPECIAL_ACCESS_USERS = [
    'radai@rejlers.ae',          # ICT Admin - system account
]

# ─────────────────────────────────────────────────────────────────────────────
# ROLES WITH ENQUIRY ACCESS (soft-coded from rbac_config.py)
# These roles automatically include 'enquiry_management' module access.
# Defined in backend/apps/rbac/rbac_config.py ROLE_MODULE_POLICY
# ─────────────────────────────────────────────────────────────────────────────
ENQUIRY_ADMIN_ROLES = [
    'super_admin',               # Level 1: Full system access
    'admin',                     # Level 2: General admin access  
    'ict_admin',                 # Level 2: ICT-specific admin access
]

# ─────────────────────────────────────────────────────────────────────────────
# MODULE CODE (defined in rbac_config.py)
# ─────────────────────────────────────────────────────────────────────────────
ENQUIRY_MODULE_CODE = 'enquiry_management'


def user_has_enquiry_access(user) -> bool:
    """
    Check if a user has access to enquiry management.
    
    Returns True if:
    1. User has 'enquiry_management' module via RBAC, OR
    2. User is in ENQUIRY_SPECIAL_ACCESS_USERS list
    
    Args:
        user: Django User object
        
    Returns:
        bool: True if user has enquiry access
    """
    if not user or not user.is_authenticated:
        return False
    
    # Django superuser always has access (emergency override)
    if user.is_superuser:
        return True
    
    # Check special access list (soft-coded)
    if user.email in ENQUIRY_SPECIAL_ACCESS_USERS:
        return True
    
    # Check RBAC module access
    try:
        from apps.rbac.models import UserProfile
        profile = UserProfile.objects.get(user=user, is_deleted=False)
        return profile.has_module_access(ENQUIRY_MODULE_CODE)
    except UserProfile.DoesNotExist:
        return False


def get_enquiry_admin_emails() -> list:
    """
    Get list of all users who should receive enquiry notifications.
    
    Returns:
        list: Email addresses of users with enquiry management access
    """
    from django.contrib.auth import get_user_model
    from apps.rbac.models import UserProfile
    
    User = get_user_model()
    admin_emails = set()
    
    # Add special access users
    admin_emails.update(ENQUIRY_SPECIAL_ACCESS_USERS)
    
    # Add users with enquiry_management module via RBAC
    profiles_with_access = UserProfile.objects.filter(
        is_deleted=False,
        user__is_active=True,
    )
    
    for profile in profiles_with_access:
        if profile.has_module_access(ENQUIRY_MODULE_CODE):
            admin_emails.add(profile.user.email)
    
    return sorted(list(admin_emails))
