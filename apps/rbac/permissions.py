"""
RBAC Permissions - Django Rest Framework Permission Classes
Enterprise-grade permission enforcement
"""
from rest_framework import permissions
from .models import UserProfile


class IsSuperAdmin(permissions.BasePermission):
    """
    Permission class to check if user is super admin
    """
    message = "You must be a super admin to perform this action."
    
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        try:
            profile = request.user.rbac_profile
            return profile.roles.filter(code='super_admin', is_active=True).exists()
        except UserProfile.DoesNotExist:
            return False


class IsAdmin(permissions.BasePermission):
    """
    Permission class to check if user is admin (includes super admin)
    """
    message = "You must be an admin to perform this action."
    
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        try:
            profile = request.user.rbac_profile
            return profile.roles.filter(
                code__in=['super_admin', 'admin'],
                is_active=True
            ).exists()
        except UserProfile.DoesNotExist:
            return False


class HasPermission(permissions.BasePermission):
    """
    Permission class to check if user has specific permission
    Usage: permission_classes = [HasPermission]
           permission_required = 'pid_analysis.create'
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        # Super admin has all permissions
        try:
            profile = request.user.rbac_profile
            if profile.roles.filter(code='super_admin', is_active=True).exists():
                return True
        except UserProfile.DoesNotExist:
            return False
        
        # Check for specific permission
        permission_required = getattr(view, 'permission_required', None)
        if not permission_required:
            return True  # No specific permission required
        
        return profile.has_permission(permission_required)


class HasModuleAccess(permissions.BasePermission):
    """
    Permission class to check if user has access to specific module
    Usage: permission_classes = [HasModuleAccess]
           module_required = 'pid_analysis'
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        # Super admin has all module access
        try:
            profile = request.user.rbac_profile
            if profile.roles.filter(code='super_admin', is_active=True).exists():
                return True
        except UserProfile.DoesNotExist:
            return False
        
        # Check for specific module access
        module_required = getattr(view, 'module_required', None)
        if not module_required:
            return True  # No specific module required
        
        return profile.has_module_access(module_required)


class IsOwnerOrAdmin(permissions.BasePermission):
    """
    Permission class to check if user is owner of object or admin
    """
    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False
        
        # Super admin can access everything
        try:
            profile = request.user.rbac_profile
            if profile.roles.filter(code='super_admin', is_active=True).exists():
                return True
        except UserProfile.DoesNotExist:
            return False
        
        # Check if user is owner
        if hasattr(obj, 'user'):
            return obj.user == request.user
        elif hasattr(obj, 'user_profile'):
            return obj.user_profile.user == request.user
        elif hasattr(obj, 'created_by'):
            return obj.created_by == request.user
        
        return False


class SameOrganization(permissions.BasePermission):
    """
    Permission class to ensure users can only access resources from their organization
    """
    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated
    
    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False
        
        # Super admin can access all organizations
        try:
            user_profile = request.user.rbac_profile
            if user_profile.roles.filter(code='super_admin', is_active=True).exists():
                return True
            
            # Check if object belongs to same organization
            if hasattr(obj, 'organization'):
                return obj.organization == user_profile.organization
            elif hasattr(obj, 'user_profile'):
                return obj.user_profile.organization == user_profile.organization
        except UserProfile.DoesNotExist:
            return False
        
        return False


class CanManageUsers(permissions.BasePermission):
    """
    Permission class for user management operations
    """
    message = "You don't have permission to manage users."
    
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        try:
            profile = request.user.rbac_profile
            
            # Super admin and admin can manage users
            if profile.roles.filter(
                code__in=['super_admin', 'admin'],
                is_active=True
            ).exists():
                return True
            
            # Check specific permission
            return profile.has_permission('users.manage')
        except UserProfile.DoesNotExist:
            return False


class CanManageRoles(permissions.BasePermission):
    """
    Permission class for role management operations
    """
    message = "You don't have permission to manage roles."
    
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        try:
            profile = request.user.rbac_profile
            
            # Only super admin can manage roles
            return profile.roles.filter(code='super_admin', is_active=True).exists()
        except UserProfile.DoesNotExist:
            return False


class ReadOnly(permissions.BasePermission):
    """
    Permission class to allow read-only access
    """
    def has_permission(self, request, view):
        return request.method in permissions.SAFE_METHODS
