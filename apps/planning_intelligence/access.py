"""Object-level access policy for planning workspaces and child records."""
from django.db.models import Q
from django.contrib.auth import get_user_model
from rest_framework.permissions import SAFE_METHODS, BasePermission

from .models import PlanningProject


WRITE_ROLES = {'project_manager', 'lead_engineer', 'engineer', 'designer'}
APPROVAL_ROLES = {'project_manager'}


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
        | Q(technical_proposals__workflow_tasks__assigned_to=user)
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


def proposal_reviewer_users(project):
    """Active organization users who may receive a technical review task.

    Review is deliberately organization-wide: the workflow task grants the
    selected reviewer read access to the proposal's planning workspace. Final
    approval remains restricted to accountable project authorities.
    """
    User = get_user_model()
    return User.objects.filter(is_active=True).order_by('first_name', 'last_name', 'email')


def proposal_approver_users(project):
    """Accountable authorities permitted to approve a technical proposal."""
    User = get_user_model()
    ids = set(User.objects.filter(Q(is_staff=True) | Q(is_superuser=True), is_active=True).values_list('id', flat=True))
    if project.enterprise_project_id:
        enterprise_project = project.enterprise_project
        if enterprise_project.owner_id:
            ids.add(enterprise_project.owner_id)
        ids.update(enterprise_project.memberships.filter(
            is_active=True, role__in=APPROVAL_ROLES,
        ).values_list('user_id', flat=True))
    return User.objects.filter(id__in=ids, is_active=True).order_by('first_name', 'last_name', 'email')


def can_approve_proposal(user, project):
    return bool(user and user.is_authenticated and proposal_approver_users(project).filter(pk=user.pk).exists())


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
    basis = getattr(obj, 'basis', None)
    project = getattr(basis, 'project', None)
    if isinstance(project, PlanningProject):
        return project
    plan = getattr(obj, 'plan', None)
    project = getattr(plan, 'project', None)
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
