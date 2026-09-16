"""Protect the project responsibilities that later authorize business decisions."""

from rest_framework.exceptions import PermissionDenied

from apps.rbac.approval_eligibility import active_approval_user, project_approval_assignment


def require_assignment_manager(user, project):
    if not active_approval_user(user) or not (
        project_approval_assignment(user, project) or user.is_staff or user.is_superuser
    ):
        raise PermissionDenied('Only the project owner, project manager, or authorized administrator may change project responsibilities.')


def require_owner_change(user, project, owner):
    if project.owner_id == getattr(owner, 'pk', None):
        return
    require_assignment_manager(user, project)
    if getattr(owner, 'pk', None) == user.pk:
        raise PermissionDenied('Another authorized person must assign you as the project owner.')
    if owner is not None and not active_approval_user(owner):
        raise PermissionDenied('Select an active employee as project owner.')


def require_membership_change(user, project, target, role, current=None):
    require_assignment_manager(user, project)
    if not active_approval_user(target):
        raise PermissionDenied('Select an active employee for this project responsibility.')
    if (target.pk == user.pk and role == 'project_manager'
            and not (current and current.role == 'project_manager' and current.is_active)):
        raise PermissionDenied('Another authorized person must assign you as a project manager.')
