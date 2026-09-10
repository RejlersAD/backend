"""Audit the assignable catalogue without changing role grants."""
import json

from django.core.management.base import BaseCommand
from django.core.cache import cache

from apps.rbac.models import Module, RoleModule, UserProfile, _sync_module_catalogue
from apps.rbac.rbac_config import ALL_MODULES_CATALOGUE, is_module_enabled
from apps.rbac.service_catalogue import SERVICE_MODULES


class Command(BaseCommand):
    help = 'Report missing/inactive service modules and unsafe default enquiry grants.'
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument('--sync', action='store_true', help='Create missing catalogue entries and refresh module caches; preserve role assignments.')
        parser.add_argument('--output', help='Write the JSON audit to this file as well as stdout.')

    def handle(self, *args, **options):
        if options['sync']:
            _sync_module_catalogue()
            cache.delete_many([f'user_modules_{pk}' for pk in UserProfile.objects.values_list('pk', flat=True)])
        configured = {item['code'] for item in ALL_MODULES_CATALOGUE}
        stored = set(Module.objects.values_list('code', flat=True))
        report = {
            'catalogue_count': len(configured),
            'database_count': len(stored),
            'missing': sorted(configured - stored),
            'retired': sorted(stored & {'finance', 'sales'}),
            'uncatalogued': sorted(stored - configured - {'finance', 'sales'}),
            'inactive': sorted(Module.objects.filter(code__in=configured, is_active=False).values_list('code', flat=True)),
            'feature_disabled': sorted(code for code in configured if not is_module_enabled(code)),
            'default_role_has_enquiry_management': RoleModule.objects.filter(role__code='default', module__code='enquiry_management').exists(),
            'assignable_business_groups': {
                parent: [code for code, _, family, _ in SERVICE_MODULES if family == parent]
                for parent in ('finance', 'sales')
            },
        }
        report['assignable_business_groups']['project_control'] = ['project_control', 'planning_package']
        result = json.dumps(report, indent=2)
        self.stdout.write(result)
        if options['output']:
            from pathlib import Path
            Path(options['output']).write_text(result + '\n', encoding='utf-8')
