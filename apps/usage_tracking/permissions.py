"""
Usage Tracking Permissions

Security layer for usage tracking APIs:
- Admins can see all data
- Department heads can see their department
- Users can see only their own data
"""

from rest_framework import permissions


class IsAdminOrOwn(permissions.BasePermission):
    """
    Admin can see all usage data.
    Regular users can only see their own usage data.
    """
    
    def has_permission(self, request, view):
        # Must be authenticated
        return request.user and request.user.is_authenticated
    
    def has_object_permission(self, request, view, obj):
        # Admins can see everything
        if request.user.is_staff or request.user.is_superuser:
            return True
        
        # Users can see their own data
        if hasattr(obj, 'user'):
            return obj.user == request.user
        
        return False


class IsAdminOrDepartmentHead(permissions.BasePermission):
    """
    Admin can see all departments.
    Department heads can see their own department data.
    """
    
    def has_permission(self, request, view):
        # Must be authenticated
        if not (request.user and request.user.is_authenticated):
            return False
        
        # Admins have full access
        if request.user.is_staff or request.user.is_superuser:
            return True
        
        # Check if user is a department head
        return self._is_department_head(request.user)
    
    def has_object_permission(self, request, view, obj):
        # Admins can see everything
        if request.user.is_staff or request.user.is_superuser:
            return True
        
        # Department heads can see their department's data
        if hasattr(obj, 'department'):
            user_department = self._get_user_department(request.user)
            return obj.department == user_department
        
        return False
    
    def _is_department_head(self, user):
        """Check if user is a department head"""
        # Check user role
        if hasattr(user, 'role') and 'head' in user.role.lower():
            return True
        
        # Check user groups
        if user.groups.filter(name__icontains='head').exists():
            return True
        
        # Check custom permission
        if user.has_perm('usage_tracking.view_department_usage'):
            return True
        
        return False
    
    def _get_user_department(self, user):
        """Extract user's department"""
        if hasattr(user, 'profile') and hasattr(user.profile, 'department'):
            return user.profile.department
        
        if hasattr(user, 'department'):
            return user.department
        
        if user.groups.exists():
            return user.groups.first().name
        
        return 'Unknown'


class IsAdminOnly(permissions.BasePermission):
    """
    Only admins can access global usage statistics and sales reports.
    """
    
    def has_permission(self, request, view):
        return (
            request.user and 
            request.user.is_authenticated and 
            (request.user.is_staff or request.user.is_superuser)
        )


class CanViewUsageData(permissions.BasePermission):
    """
    Custom permission that checks if user can view usage data.
    
    Permissions hierarchy:
    1. Superuser/Staff - Can see everything
    2. Department Head - Can see department data
    3. Regular User - Can see own data only
    """
    
    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        
        # Check action-specific permissions
        action = getattr(view, 'action', None)
        
        if action in ['list', 'retrieve']:
            return True  # Authenticated users can at least try
        
        if action in ['summary', 'sales_report', 'department_usage', 'feature_usage']:
            # These require admin or department head
            return (
                request.user.is_staff or 
                request.user.is_superuser or
                self._is_department_head(request.user)
            )
        
        return False
    
    def _is_department_head(self, user):
        """Check if user is a department head"""
        if hasattr(user, 'role') and 'head' in user.role.lower():
            return True
        
        if user.groups.filter(name__icontains='head').exists():
            return True
        
        if user.has_perm('usage_tracking.view_department_usage'):
            return True
        
        return False
