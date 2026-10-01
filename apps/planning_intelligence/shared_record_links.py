"""Reviewed canonical identities for planning workspaces and named resources."""
from django.db.models import Q
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.core.shared_record_targets import require_target, search_targets, visible_payload
from apps.rbac.action_policy import module_action_allowed
from .access import accessible_projects, can_access_enterprise_project, can_write_project
from .models import PlanningProject, ScheduleResource, ScheduleVersion
from .services.resource_planning import resource_is_locked


def _project_link(row, user):
    if not row.enterprise_project_id:
        return None
    from apps.core.project_models import Project
    return visible_payload(user, 'project', Project.objects.filter(pk=row.enterprise_project_id).first())


class PlanningProjectAdapter:
    key = 'planning_project'
    label = 'Planning workspaces'

    def queryset(self, user):
        qs = accessible_projects(user)
        return qs if module_action_allowed(user, 'planning_package', 'read') else qs.none()

    def filter_queryset(self, qs, status, search):
        if status in {'linked', 'unlinked'}:
            qs = qs.filter(enterprise_project__isnull=status == 'unlinked')
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(client__icontains=search))
        return qs

    def fingerprint(self, row):
        return {'updated_at': row.updated_at.isoformat(), 'name': row.name, 'client': row.client,
                'project_id': row.enterprise_project_id, 'created_by_id': row.created_by_id}

    def describe(self, row, user):
        linked_project = row.enterprise_project if row.enterprise_project_id else None
        client = visible_payload(user, 'client', linked_project.client) if linked_project and linked_project.client_id else None
        return {'source_type': self.key, 'id': str(row.pk), 'reference': str(row.pk), 'label': row.name,
                'source_values': {'Project name': row.name, 'Client': row.client},
                'links': {'project': _project_link(row, user), 'client': client}, 'target_kinds': ['project'],
                'state': 'linked' if row.enterprise_project_id else 'unlinked',
                'can_link': bool(not row.enterprise_project_id and can_write_project(user, row)
                                 and module_action_allowed(user, 'planning_package', 'update'))}

    def require_write(self, row, user):
        if not module_action_allowed(user, 'planning_package', 'update') or not can_write_project(user, row):
            raise PermissionDenied('You cannot reconcile this planning workspace.')

    def lock_scope(self, row, user, targets):
        from apps.core.project_models import Project
        identifiers = {row.enterprise_project_id} if row.enterprise_project_id else set()
        if targets.get('project_id'):
            identifiers.add(require_target(user, 'project', targets['project_id']).pk)
        list(Project.objects.select_for_update().filter(pk__in=identifiers).order_by('pk'))

    def candidates(self, row, user, kind, search):
        if kind != 'project':
            raise ValidationError({'kind': 'Select a project.'})
        return search_targets(user, kind, search)

    def apply(self, row, user, targets):
        self.require_write(row, user)
        if set(targets) != {'project_id'}:
            raise ValidationError({'targets': 'Select exactly one enterprise project.'})
        target = require_target(user, 'project', targets['project_id'])
        from apps.core.project_models import Project
        target = Project.objects.select_for_update().get(pk=target.pk)
        if not can_access_enterprise_project(user, target, write=True):
            raise PermissionDenied('Project write access is required to link this workspace.')
        if row.enterprise_project_id and row.enterprise_project_id != target.pk:
            raise ValidationError('This workspace already has a permanent enterprise-project identity.')
        if PlanningProject.objects.filter(enterprise_project=target).exclude(pk=row.pk).exists():
            raise ValidationError('This enterprise project already has a planning workspace.')
        from .sales_preparation import validate_bound_enterprise_project
        validate_bound_enterprise_project(row, target)
        row.enterprise_project = target
        row.save(update_fields=['enterprise_project', 'updated_at'])


class ScheduleResourceAdapter:
    key = 'schedule_resource'
    label = 'Planning resources'

    def queryset(self, user):
        qs = ScheduleResource.objects.filter(is_deleted=False, project__in=accessible_projects(user)).select_related('project', 'employee')
        return qs if module_action_allowed(user, 'planning_package', 'read') else qs.none()

    def filter_queryset(self, qs, status, search):
        if status in {'linked', 'unlinked'}:
            qs = qs.filter(employee__isnull=status == 'unlinked')
        if search:
            qs = qs.filter(Q(code__icontains=search) | Q(name__icontains=search) | Q(project__name__icontains=search))
        return qs

    def fingerprint(self, row):
        return {'updated_at': row.updated_at.isoformat(), 'project_id': row.project_id,
                'enterprise_project_id': row.project.enterprise_project_id, 'code': row.code, 'name': row.name,
                'resource_type': row.resource_type, 'employee_id': str(row.employee_id) if row.employee_id else None,
                'locked': resource_is_locked(row)}

    def describe(self, row, user):
        project = row.project.enterprise_project if row.project.enterprise_project_id else None
        can_link = bool(row.resource_type == 'labor' and project and not row.employee_id
                        and not resource_is_locked(row) and can_write_project(user, row.project)
                        and module_action_allowed(user, 'planning_package', 'update'))
        state = 'linked' if row.employee_id else 'not_applicable'
        warning = 'Role and crew resources need no employee. Link only when this resource represents a named person.'
        if row.resource_type == 'labor' and not project:
            warning = 'Link the planning workspace to its enterprise project before choosing an employee.'
        elif resource_is_locked(row) and not row.employee_id:
            warning = 'This resource belongs to an immutable schedule; create a revised resource for a named employee.'
        return {'source_type': self.key, 'id': str(row.pk), 'reference': row.code, 'label': row.name,
                'source_values': {'Resource code': row.code, 'Resource name': row.name, 'Type': row.resource_type},
                'links': {'project': visible_payload(user, 'project', project),
                          'employee': visible_payload(user, 'employee', row.employee, project=project)},
                'target_kinds': ['employee'] if row.resource_type == 'labor' else [],
                'state': state, 'can_link': can_link, 'warning': warning}

    def require_write(self, row, user):
        if not module_action_allowed(user, 'planning_package', 'update') or not can_write_project(user, row.project):
            raise PermissionDenied('You cannot reconcile this resource.')

    def lock_scope(self, row, user, targets):
        from apps.core.project_models import Project
        list(Project.objects.select_for_update().filter(pk=row.project.enterprise_project_id))

    def candidates(self, row, user, kind, search):
        if kind != 'employee' or row.resource_type != 'labor':
            raise ValidationError({'kind': 'Only labor resources can reference an employee.'})
        return search_targets(user, kind, search, project=row.project.enterprise_project)

    def apply(self, row, user, targets):
        self.require_write(row, user)
        if set(targets) != {'employee_id'} or row.resource_type != 'labor':
            raise ValidationError({'targets': 'Select one employee for a labor resource.'})
        list(ScheduleVersion.objects.select_for_update().filter(schedule__project=row.project).order_by('pk'))
        if resource_is_locked(row):
            raise ValidationError('A resource used by an immutable schedule cannot receive a new employee identity.')
        target = require_target(user, 'employee', targets['employee_id'], project=row.project.enterprise_project)
        if row.employee_id and row.employee_id != target.pk:
            raise ValidationError('This resource already has an employee identity. Create a revised resource.')
        row.employee = target
        row.save(update_fields=['employee', 'updated_at'])
        # The current catalog is an input to calculated versions. Preserve every
        # approved snapshot and require recalculation of mutable versions.
        ScheduleVersion.objects.filter(activities__assignments__resource=row,
            activities__assignments__is_deleted=False, status='calculated').update(status='draft', calculated_at=None)


ADAPTERS = {'planning_project': PlanningProjectAdapter(), 'schedule_resource': ScheduleResourceAdapter()}
