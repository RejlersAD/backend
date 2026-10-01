"""Reviewed project-to-client links, retaining the original customer label."""
from django.db.models import Q
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.rbac.action_policy import module_action_allowed
from .project_assignment_policy import require_assignment_manager
from .shared_record_targets import (
    require_target, search_targets, validate_project_client, visible_payload, visible_projects,
)


class ProjectClientAdapter:
    key = 'project_client'
    label = 'Project clients'

    def queryset(self, user):
        return visible_projects(user).select_related('client', 'owner')

    def filter_queryset(self, queryset, status, search):
        if status == 'unlinked':
            queryset = queryset.filter(client__isnull=True)
        elif status == 'linked':
            queryset = queryset.filter(client__isnull=False)
        if search:
            queryset = queryset.filter(Q(code__icontains=search) | Q(name__icontains=search) | Q(client_name__icontains=search))
        return queryset.order_by('code', 'pk')

    def require_write(self, row, user):
        if not module_action_allowed(user, 'project_control', 'update'):
            raise PermissionDenied('Project update access is required.')
        require_assignment_manager(user, row)

    def describe(self, row, user):
        try:
            self.require_write(row, user)
            can_link = row.client_id is None
        except PermissionDenied:
            can_link = False
        client = visible_payload(user, 'client', row.client)
        return {
            'reference': row.code, 'label': row.name,
            'source_values': {'Client name': row.client_name},
            'links': {'client': client}, 'target_kinds': ['client'],
            'state': 'linked' if row.client_id else 'unlinked', 'can_link': can_link,
            'warning': 'Linked client details require Sales client access.' if row.client_id and not client else '',
        }

    def fingerprint(self, row):
        return {'client': str(row.client_id or ''), 'client_name': row.client_name,
                'updated_at': row.updated_at.isoformat(), 'owner': row.owner_id}

    def candidates(self, row, user, kind, search):
        if kind != 'client':
            raise ValidationError({'kind': 'Select a client.'})
        return search_targets(user, kind, search)

    def apply(self, row, user, targets):
        if set(targets) != {'client_id'}:
            raise ValidationError({'targets': 'Select exactly one client.'})
        client = require_target(user, 'client', targets['client_id'])
        validate_project_client(row, client)
        # An existing Sales relationship is stronger evidence than a name match.
        from apps.sales.models import Deal, ProjectHandover
        existing_clients = set(Deal.objects.filter(converted_project=row).values_list('client_id', flat=True))
        existing_clients.update(ProjectHandover.objects.filter(project=row).values_list('opportunity__client_id', flat=True))
        if existing_clients and existing_clients != {client.pk}:
            raise ValidationError({'client_id': 'This client conflicts with the recorded Sales handover.'})
        if row.client_id:
            if row.client_id != client.pk:
                raise ValidationError({'client_id': 'An existing client link cannot be reassigned here.'})
            return row
        row.client = client
        row.save(update_fields=['client', 'updated_at'])
        return row
