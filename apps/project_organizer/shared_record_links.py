"""Reviewed enterprise links for existing cross-tool workspaces."""
from django.db.models import Q
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.core.shared_record_targets import require_target, search_targets, visible_payload
from apps.project_control.access import can_write_enterprise_project
from .views import _filtered_queryset, _user_can_modify


class OrganizerProjectAdapter:
    key = 'organizer_project'
    label = 'Tool project workspaces'

    def queryset(self, user):
        return _filtered_queryset(user)

    def filter_queryset(self, qs, status, search):
        if status in {'linked', 'unlinked'}:
            qs = qs.filter(enterprise_project__isnull=status == 'unlinked')
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(code__icontains=search) | Q(client__icontains=search))
        return qs

    def fingerprint(self, row):
        return {'updated_at': row.updated_at.isoformat(), 'name': row.name, 'code': row.code,
                'client': row.client, 'project_id': row.enterprise_project_id, 'created_by_id': row.created_by_id}

    def describe(self, row, user):
        from apps.core.project_models import Project
        project = Project.objects.filter(pk=row.enterprise_project_id).first() if row.enterprise_project_id else None
        project_link = visible_payload(user, 'project', project)
        client_link = visible_payload(user, 'client', project.client) if project_link and project.client_id else None
        return {'source_type': self.key, 'id': str(row.pk), 'reference': row.code or str(row.pk), 'label': row.name,
                'source_values': {'Project code': row.code, 'Project name': row.name, 'Client': row.client},
                'links': {'project': project_link, 'client': client_link}, 'target_kinds': ['project'],
                'state': 'linked' if row.enterprise_project_id else 'unlinked',
                'can_link': bool(not row.enterprise_project_id and _user_can_modify(user, row)),
                'warning': 'Tool workspace labels and activity history remain unchanged.'}

    def require_write(self, row, user):
        if not _user_can_modify(user, row):
            raise PermissionDenied('Only this workspace owner or an authorized administrator can reconcile its identity.')

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
        if not can_write_enterprise_project(user, target) or target.is_deleted:
            raise PermissionDenied('Project write access is required to link this workspace.')
        if row.enterprise_project_id and row.enterprise_project_id != target.pk:
            raise ValidationError('This workspace already has an enterprise-project identity.')
        row.enterprise_project = target
        row.save(update_fields=['enterprise_project', 'updated_at'])


ADAPTERS = {'organizer_project': OrganizerProjectAdapter()}
