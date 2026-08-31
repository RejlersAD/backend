"""Enterprise project access policy shared by project-control endpoints."""
from django.db.models import Q
from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.core.project_models import Project


WRITE_ROLES = {'project_manager', 'lead_engineer', 'engineer', 'designer'}
COMMERCIAL_MODULES = {
    'project_control', 'finance', 'procurement', 'procurement_orders',
    'procurement_requisitions', 'procurement_receipts',
}


def has_commercial_module_access(user, modules=COMMERCIAL_MODULES):
    if not user or not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return True
    try:
        profile = user.rbac_profile
    except Exception:
        return False
    return any(profile.has_module_access(code) for code in modules)


def can_approve_commercial(user):
    return has_commercial_module_access(user, {'project_control', 'finance'})


def accessible_enterprise_projects(user):
    if not user or not user.is_authenticated:
        return Project.objects.none()
    queryset = Project.objects.filter(is_deleted=False)
    if user.is_staff or user.is_superuser or has_commercial_module_access(user):
        return queryset
    return queryset.filter(
        Q(owner=user) | Q(memberships__user=user, memberships__is_active=True)
    ).distinct()


def can_write_enterprise_project(user, project):
    if user.is_staff or user.is_superuser or project.owner_id == user.id:
        return True
    return project.memberships.filter(user=user, is_active=True, role__in=WRITE_ROLES).exists()


def project_for_object(obj):
    project = getattr(obj, 'project', None)
    if isinstance(project, Project):
        return project
    estimate = getattr(obj, 'estimate', None)
    return getattr(estimate, 'project', None)


class ProjectControlObjectPermission(BasePermission):
    def has_object_permission(self, request, view, obj):
        project = project_for_object(obj)
        if project is None:
            return False
        if request.method in SAFE_METHODS:
            return accessible_enterprise_projects(request.user).filter(pk=project.pk).exists()
        return can_write_enterprise_project(request.user, project)


class CommercialModulePermission(BasePermission):
    """Allow authenticated Project Control, Finance, or Procurement operators."""

    def has_permission(self, request, view):
        return has_commercial_module_access(request.user)
