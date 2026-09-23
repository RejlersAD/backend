"""Effective grants for replacing the shared portfolio source workbook."""
from rest_framework.permissions import BasePermission

from apps.rbac.action_policy import module_action_allowed


def can_upload_workbook(user):
    if not user or not (getattr(user, 'is_staff', False) or getattr(user, 'is_superuser', False)):
        return False
    return all(module_action_allowed(user, module, action) for module, action in (
        ('executive_dashboard', 'read'), ('project_control', 'read'), ('project_control', 'update'),
    ))


class CanUploadPortfolioWorkbook(BasePermission):
    message = 'Portfolio upload requires a RADAI portfolio administrator with Executive Dashboard read and Project Control read and update access.'

    def has_permission(self, request, view):
        return can_upload_workbook(request.user)
