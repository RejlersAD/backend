"""Read-only review of the one-time legacy action grant migration."""
import json
from importlib import import_module
from django.core.management.base import BaseCommand
from apps.rbac.models import AuditLog, Permission, RoleModule, RolePermission


class Command(BaseCommand):
    help = 'Show which legacy module-only role assignments migration 0055 will make explicit.'

    def handle(self, *args, **options):
        capabilities = import_module('apps.rbac.migrations.0055_explicit_legacy_module_actions').LEGACY_ACTIONS
        reviewed = set(AuditLog.objects.filter(resource_type='Role', metadata__audit_source='role_access_review').values_list('resource_id', flat=True))
        explicit = set(RolePermission.objects.values_list('role_id', 'permission__module_id'))
        rows = []
        for grant in RoleModule.objects.filter(role__is_active=True, module__is_active=True).exclude(role__code='super_admin').select_related('role', 'module'):
            if grant.role_id in reviewed or (grant.role_id, grant.module_id) in explicit:
                continue
            count = Permission.objects.filter(module=grant.module, is_active=True,
                                              action__in=capabilities.get(grant.module.code, [])).count()
            if count:
                rows.append({'role': grant.role.code, 'module': grant.module.code, 'grants': count})
        self.stdout.write(json.dumps({'module_assignments': len(rows), 'action_grants': sum(row['grants'] for row in rows), 'plan': rows}, indent=2))
