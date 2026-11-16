"""Project-scoped RADAI Intelligence instructions with optimistic versioning."""
from django.db import transaction
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError

from apps.core.project_models import Project, ProjectAIInstructions
from apps.planning_intelligence.access import can_access_enterprise_project
from apps.project_control.access import can_write_enterprise_project
from apps.rbac.action_policy import module_action_allowed
from apps.rbac.models import AuditLog


FIELDS = (
    'instructions', 'discipline_context', 'engineering_references',
    'response_format', 'terminology', 'evidence_expectations', 'review_requirements',
)
LIMITS = {field: 6000 if field == 'instructions' else 2000 for field in FIELDS}


class InstructionConflict(APIException):
    status_code = 409
    default_detail = 'Project instructions changed. Reload before saving.'


def can_read(user, project):
    return (can_access_enterprise_project(user, project)
            and module_action_allowed(user, 'project_control', 'read'))


def can_edit(user, project):
    return (can_read(user, project)
            and can_access_enterprise_project(user, project, write=True)
            and can_write_enterprise_project(user, project)
            and module_action_allowed(user, 'project_control', 'update'))


def require_read(user, project):
    if not can_read(user, project):
        raise PermissionDenied('Project membership is required.')


def payload(project, record, user):
    return {
        'project_id': project.pk,
        'version': record.version if record else 0,
        'updated_at': record.updated_at.isoformat() if record else None,
        'updated_by_id': record.updated_by_id if record else None,
        'can_edit': can_edit(user, project),
        **{field: getattr(record, field) if record else '' for field in FIELDS},
    }


def validated_fields(data):
    result = {}
    for field in FIELDS:
        value = data.get(field, '')
        if not isinstance(value, str) or len(value) > LIMITS[field] or '\x00' in value:
            raise ValidationError({field: f'Enter plain text of at most {LIMITS[field]} characters.'})
        result[field] = value.strip()
    return result


@transaction.atomic
def save(*, project, user, data, expected_version):
    project = Project.objects.select_for_update().get(pk=project.pk, is_deleted=False)
    if not can_edit(user, project):
        raise PermissionDenied('Project instruction edit access is required.')
    fields = validated_fields(data)
    record = ProjectAIInstructions.objects.select_for_update().filter(project=project).first()
    current_version = record.version if record else 0
    before = {field: getattr(record, field) for field in FIELDS} if record else None
    if record and before == fields:
        return payload(project, record, user)
    if expected_version != current_version:
        raise InstructionConflict()
    if record:
        for field, value in fields.items():
            setattr(record, field, value)
        record.version += 1
        record.updated_by = user
        record.save(update_fields=[*FIELDS, 'version', 'updated_by', 'updated_at'])
        action = 'update'
    else:
        record = ProjectAIInstructions.objects.create(
            project=project, updated_by=user, **fields,
        )
        action = 'create'
    AuditLog.objects.create(
        user=user, user_email=user.email or '', action=action,
        resource_type='ProjectAIInstructions', resource_repr=f'Project {project.code} AI instructions',
        changes={'before': before, 'after': fields},
        metadata={'project_id': project.pk, 'version': record.version},
    )
    return payload(project, record, user)
