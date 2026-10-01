"""Employee identity review without changing approved labour evidence."""
from django.db.models import Q
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.core.shared_record_targets import require_target, search_targets, visible_payload, visible_projects
from apps.rbac.action_policy import module_action_allowed
from .access import can_write_enterprise_project
from .models import ApprovedHourEntry


class ApprovedHoursAdapter:
    key = 'approved_hours'
    label = 'Project labor entries'

    def queryset(self, user):
        return ApprovedHourEntry.objects.filter(is_deleted=False, project__in=visible_projects(user)).select_related('project', 'employee')

    def filter_queryset(self, qs, status, search):
        if status in {'linked', 'unlinked'}:
            qs = qs.filter(employee__isnull=status == 'unlinked')
        if search:
            qs = qs.filter(Q(employee_code__icontains=search) | Q(employee_name__icontains=search)
                           | Q(source_reference__icontains=search) | Q(project__code__icontains=search))
        return qs

    def fingerprint(self, row):
        return {'updated_at': row.updated_at.isoformat(), 'employee_code': row.employee_code,
                'employee_name': row.employee_name, 'project_id': row.project_id,
                'employee_id': str(row.employee_id) if row.employee_id else None,
                'status': row.status, 'work_date': row.work_date.isoformat(), 'source_reference': row.source_reference}

    def describe(self, row, user):
        return {'source_type': self.key, 'id': str(row.pk), 'reference': row.source_reference,
                'label': row.employee_name or row.employee_code,
                'source_values': {'Employee code': row.employee_code, 'Employee name': row.employee_name,
                                  'Work date': row.work_date.isoformat(), 'Entry status': row.status},
                'links': {'project': visible_payload(user, 'project', row.project),
                          'employee': visible_payload(user, 'employee', row.employee, project=row.project)},
                'target_kinds': ['employee'], 'state': 'linked' if row.employee_id else 'unlinked',
                'can_link': bool(not row.employee_id and can_write_enterprise_project(user, row.project)
                                 and module_action_allowed(user, 'project_control', 'update')),
                'warning': 'Linking preserves recorded employee labels, hours, costs and approval evidence.'}

    def require_write(self, row, user):
        if not module_action_allowed(user, 'project_control', 'update') or not can_write_enterprise_project(user, row.project):
            raise PermissionDenied('You cannot reconcile labor entries for this project.')

    def lock_scope(self, row, user, targets):
        from apps.core.project_models import Project
        list(Project.objects.select_for_update().filter(pk=row.project_id))

    def candidates(self, row, user, kind, search):
        if kind != 'employee':
            raise ValidationError({'kind': 'Select an employee.'})
        return search_targets(user, kind, search, project=row.project)

    def apply(self, row, user, targets):
        self.require_write(row, user)
        if set(targets) != {'employee_id'}:
            raise ValidationError({'targets': 'Select exactly one employee.'})
        target = require_target(user, 'employee', targets['employee_id'], project=row.project)
        if row.employee_id and row.employee_id != target.pk:
            raise ValidationError('This entry already has a canonical employee identity.')
        row.employee = target
        row.save(update_fields=['employee', 'updated_at'])


ADAPTERS = {'approved_hours': ApprovedHoursAdapter()}
