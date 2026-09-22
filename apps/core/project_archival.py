"""Archive enterprise projects without deleting protected planning history."""
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied

from apps.rbac.approval_eligibility import active_approval_user, project_approval_assignment
from .project_models import Project


@transaction.atomic
def archive_project(project_id, actor):
    from apps.planning_intelligence.models import PlanningProject
    from apps.planning_intelligence.services.audit import record_event

    # Match planning mutations' workspace-before-enterprise lock ordering.
    workspaces = list(PlanningProject.objects.select_for_update().filter(
        enterprise_project_id=project_id, is_deleted=False).order_by('pk'))
    project = Project.objects.select_for_update().filter(pk=project_id, is_deleted=False).first()
    if project is None:
        raise NotFound('This project is already archived or no longer available.')
    if not active_approval_user(actor) or not (
        actor.is_staff or actor.is_superuser or project_approval_assignment(actor, project)
    ):
        raise PermissionDenied('Only the project owner, project manager, or authorized administrator may archive this project.')

    archived_at = timezone.now()
    for workspace in workspaces:
        workspace.is_deleted, workspace.deleted_at = True, archived_at
        workspace.save(update_fields=['is_deleted', 'deleted_at', 'updated_at'])
        record_event(project=workspace, actor=actor, action='project.archived', entity=project,
            before={'is_deleted': False, 'planning_workspace_is_deleted': False},
            after={'is_deleted': True, 'planning_workspace_is_deleted': True,
                   'archived_at': archived_at.isoformat()},
            metadata={'enterprise_project_id': project.pk, 'planning_workspace_id': workspace.pk,
                      'retention': 'Planning, approvals, reporting and financial history retained.'})
    project.is_deleted, project.deleted_at = True, archived_at
    project.save(update_fields=['is_deleted', 'deleted_at', 'updated_at'])
