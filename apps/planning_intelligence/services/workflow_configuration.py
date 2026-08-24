"""Resolution helpers for project workflow configuration."""
from django.db import transaction

from ..models import (
    EngineeringDependencyTemplate, ProjectScheduleConfiguration, WorkflowTemplate,
)


@transaction.atomic
def ensure_project_schedule_configuration(project, *, actor=None):
    """Attach protected corporate defaults to a project when available."""
    existing = ProjectScheduleConfiguration.objects.filter(project=project, is_deleted=False).first()
    if existing:
        return existing, False
    workflow = WorkflowTemplate.objects.filter(
        project__isnull=True, is_system=True, is_default=True, status='active', is_deleted=False,
    ).order_by('-version').first()
    if not workflow:
        return None, False
    dependency = EngineeringDependencyTemplate.objects.filter(
        project__isnull=True, is_system=True, is_default=True, status='active', is_deleted=False,
    ).order_by('-version').first()
    configuration = ProjectScheduleConfiguration.objects.create(
        project=project, workflow_template=workflow, dependency_template=dependency,
        standard_task_count=workflow.stages.filter(is_deleted=False).count(),
        settings={'final_issue_mode': 'task', 'date_authority': 'cpm'}, updated_by=actor,
    )
    return configuration, True


def resolve_workflow_template(configuration, *, discipline='', deliverable=''):
    """Resolve exact deliverable override, then discipline override, then project default."""
    overrides = configuration.overrides.filter(is_deleted=False, is_active=True).select_related('workflow_template')
    if deliverable:
        match = overrides.filter(scope_type='deliverable', scope_key__iexact=deliverable).order_by('priority').first()
        if match:
            return match.workflow_template
    if discipline:
        match = overrides.filter(scope_type='discipline', scope_key__iexact=discipline).order_by('priority').first()
        if match:
            return match.workflow_template
    return configuration.workflow_template
