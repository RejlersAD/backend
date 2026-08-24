"""Object-level access policy for planning workspaces and child records."""
from django.db.models import Q
from rest_framework.permissions import SAFE_METHODS, BasePermission

from .models import PlanningProject


WRITE_ROLES = {'project_manager', 'lead_engineer', 'engineer', 'designer'}


def accessible_projects(user):
    if not user or not user.is_authenticated:
        return PlanningProject.objects.none()
    queryset = PlanningProject.objects.filter(is_deleted=False)
    if user.is_staff or user.is_superuser:
        return queryset
    return queryset.filter(
        Q(enterprise_project__isnull=True, created_by=user)
        | Q(enterprise_project__owner=user)
        | Q(enterprise_project__memberships__user=user, enterprise_project__memberships__is_active=True)
    ).distinct()


def can_access_enterprise_project(user, enterprise_project, *, write=False):
    if enterprise_project is None:
        return True
    if user.is_staff or user.is_superuser or enterprise_project.owner_id == user.id:
        return True
    memberships = enterprise_project.memberships.filter(user=user, is_active=True)
    return memberships.filter(role__in=WRITE_ROLES).exists() if write else memberships.exists()


def can_write_project(user, project):
    if user.is_staff or user.is_superuser:
        return True
    if project.enterprise_project_id:
        return can_access_enterprise_project(user, project.enterprise_project, write=True)
    return project.created_by_id == user.id


def can_final_approve_defaults(user, project):
    """Limit effective default changes to accountable project authorities."""
    if user.is_staff or user.is_superuser:
        return True
    if project.enterprise_project_id:
        enterprise_project = project.enterprise_project
        if enterprise_project.owner_id == user.id:
            return True
        return enterprise_project.memberships.filter(
            user=user, is_active=True, role='project_manager',
        ).exists()
    return project.created_by_id == user.id


def planning_project_for_object(obj):
    if isinstance(obj, PlanningProject):
        return obj
    project = getattr(obj, 'project', None)
    if isinstance(project, PlanningProject):
        return project
    generation = getattr(obj, 'generation', None)
    project = getattr(generation, 'project', None)
    if isinstance(project, PlanningProject):
        return project

    # Relational scheduling objects reach their workspace through a few
    # deliberately short ownership paths. Keeping this resolver duck-typed
    # avoids importing the schedule model module back into access.py.
    calendar = getattr(obj, 'calendar', None)
    project = getattr(calendar, 'project', None)
    if isinstance(project, PlanningProject):
        return project
    schedule = getattr(obj, 'schedule', None)
    project = getattr(schedule, 'project', None)
    if isinstance(project, PlanningProject):
        return project
    version = getattr(obj, 'version', None)
    schedule = getattr(version, 'schedule', None)
    project = getattr(schedule, 'project', None)
    if isinstance(project, PlanningProject):
        return project
    activity = getattr(obj, 'activity', None)
    version = getattr(activity, 'version', None)
    schedule = getattr(version, 'schedule', None)
    project = getattr(schedule, 'project', None)
    if isinstance(project, PlanningProject):
        return project
    source_version = getattr(obj, 'source_version', None)
    schedule = getattr(source_version, 'schedule', None)
    project = getattr(schedule, 'project', None)
    if isinstance(project, PlanningProject):
        return project
    file_obj = getattr(obj, 'file', None)
    project = getattr(file_obj, 'project', None)
    if isinstance(project, PlanningProject):
        return project
    run = getattr(obj, 'run', None)
    return getattr(run, 'project', None)


class PlanningObjectPermission(BasePermission):
    message = 'You do not have permission to modify this planning workspace.'

    def has_object_permission(self, request, view, obj):
        project = planning_project_for_object(obj)
        if project is None:
            return False
        if request.method in SAFE_METHODS:
            return accessible_projects(request.user).filter(pk=project.pk).exists()
        return can_write_project(request.user, project)
