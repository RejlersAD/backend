"""Effective grants for replacing the shared portfolio source workbook."""
from rest_framework.permissions import BasePermission

from apps.rbac.action_policy import module_action_allowed


def can_upload_workbook(user):
    if not user or not user.is_authenticated or not user.is_active:
        return False
    profile = getattr(user, 'rbac_profile', None)
    # Promoting an existing account assigns its role without changing Django's
    # staff flags. Use the same active Super Administrator identity as RBAC.
    administrator = (user.is_staff or user.is_superuser
                     or bool(profile and profile.is_super_admin()))
    if not administrator:
        return False
    return all(module_action_allowed(user, module, action) for module, action in (
        ('executive_dashboard', 'read'), ('project_control', 'read'), ('project_control', 'update'),
    ))


class CanUploadPortfolioWorkbook(BasePermission):
    message = 'Portfolio upload requires a RADAI staff administrator or active Super Administrator with Executive Dashboard read and Project Control read and update access.'

    def has_permission(self, request, view):
        return can_upload_workbook(request.user)
