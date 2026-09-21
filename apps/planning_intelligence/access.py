"""Object-level access policy for planning workspaces and child records."""
from django.db.models import Q
from django.contrib.auth import get_user_model
from rest_framework.permissions import SAFE_METHODS, BasePermission

from .models import PlanningProject
from apps.rbac.approval_eligibility import active_approval_user, approval_access, project_approval_assignment, require_approval


WRITE_ROLES = {'project_manager', 'lead_engineer', 'engineer', 'designer'}
APPROVAL_ROLES = {'project_manager'}


def accessible_projects(user):
    if not user or not user.is_authenticated:
        return PlanningProject.objects.none()
    queryset = PlanningProject.objects.filter(is_deleted=False).filter(
        Q(enterprise_project__isnull=True) | Q(enterprise_project__is_deleted=False)
    )
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
    if enterprise_project.is_deleted or not user or not user.is_authenticated or not user.is_active:
        return False
    if user.is_staff or user.is_superuser or enterprise_project.owner_id == user.id:
        return True
    memberships = enterprise_project.memberships.filter(user=user, is_active=True)
    return memberships.filter(role__in=WRITE_ROLES).exists() if write else memberships.exists()


def can_write_project(user, project):
    if (not project or project.is_deleted or not user or not user.is_authenticated or not user.is_active
            or project.enterprise_project_id and project.enterprise_project.is_deleted):
        return False
    if user.is_staff or user.is_superuser:
        return True
    if project.enterprise_project_id:
        return can_access_enterprise_project(user, project.enterprise_project, write=True)
    return project.created_by_id == user.id


def can_final_approve_defaults(user, project):
    """Limit effective default changes to accountable project authorities."""
    return bool(project and not project.is_deleted and project.enterprise_project_id
                and not project.enterprise_project.is_deleted
                and project_approval_assignment(user, project.enterprise_project)
                and approval_access(user, 'planning_package'))


def require_planning_approval(user, project, *, current):
    require_approval(user, 'planning_package',
                     assigned=bool(project and not project.is_deleted and project.enterprise_project_id
                                   and not project.enterprise_project.is_deleted
                                   and project_approval_assignment(user, project.enterprise_project)),
                     current=current)


def current_basis(basis):
    return bool(not basis.is_deleted and basis.status in {'draft', 'ready'}
                and not basis.project.schedule_bases.filter(is_deleted=False, version__gt=basis.version).exists())


def current_generation_plan(plan):
    return bool(not plan.is_deleted and plan.status in {'draft', 'ready'}
                and not plan.basis.is_deleted and plan.basis.status == 'approved'
                and not plan.project.schedule_bases.filter(is_deleted=False, version__gt=plan.basis.version).exists()
                and not plan.project.generation_plans.filter(is_deleted=False, version__gt=plan.version).exists())


def proposal_reviewer_users(project):
    """Active organization users who may receive a technical review task.

    Review is deliberately organization-wide: the workflow task grants the
    selected reviewer read access to the proposal's planning workspace. Final
    approval remains restricted to accountable project authorities.
    """
    User = get_user_model()
    ids = [user.pk for user in User.objects.filter(is_active=True)
           if active_approval_user(user) and approval_access(user, 'planning_package')]
    return User.objects.filter(pk__in=ids).order_by('first_name', 'last_name', 'email')


def proposal_approver_users(project):
    """Accountable authorities permitted to approve a technical proposal."""
    User = get_user_model()
    ids = set()
    if project.enterprise_project_id:
        enterprise_project = project.enterprise_project
        if enterprise_project.owner_id:
            ids.add(enterprise_project.owner_id)
        ids.update(enterprise_project.memberships.filter(
            is_active=True, role__in=APPROVAL_ROLES,
        ).values_list('user_id', flat=True))
    ids = [user.pk for user in User.objects.filter(pk__in=ids, is_active=True)
           if can_final_approve_defaults(user, project)]
    return User.objects.filter(id__in=ids).order_by('first_name', 'last_name', 'email')


def can_approve_proposal(user, project):
    return can_final_approve_defaults(user, project)


def can_decide_proposal_task(proposal, user, task=None):
    """Shared current task capability for decisions, UI and message delivery."""
    if not active_approval_user(user) or not approval_access(user, 'planning_package'):
        return False
    if proposal.is_deleted or proposal.project.is_deleted:
        return False
    version = proposal.schedule_version
    if (version is None or version.is_deleted or version.status == 'superseded' or version.schedule.is_deleted
            or version.schedule.versions.filter(is_deleted=False, version__gt=version.version).exists()):
        return False
    task_type = {'internal_review': 'review', 'approval_review': 'approval'}.get(proposal.status)
    if not task_type:
        return False
    current = proposal.workflow_tasks.filter(
        is_deleted=False, status='pending', task_type=task_type,
    ).order_by('-created_at', '-pk').first()
    if current is None or current.assigned_to_id != user.pk or (task and task.pk != current.pk):
        return False
    if task_type == 'review':
        return proposal.reviewer_id == user.pk and proposal.created_by_id != user.pk
    reviewed = proposal.workflow_tasks.filter(
        is_deleted=False, task_type='review', status='completed', assigned_to_id=proposal.checked_by_id,
    ).exists()
    return bool(proposal.approver_id == user.pk and can_approve_proposal(user, proposal.project)
                and user.pk not in (proposal.created_by_id, proposal.checked_by_id)
                and reviewed and proposal.review_completed_at
                and not proposal.workflow_tasks.filter(is_deleted=False, task_type='review', status='pending').exists())


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
