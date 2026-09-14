"""Deployment check: report guarded endpoints and fail on business route gaps."""
import json
from types import SimpleNamespace
from django.core.management.base import BaseCommand, CommandError
from django.urls import URLResolver, get_resolver
from apps.rbac.action_policy import ROUTE_MODULES, route_module, operation_action, request_module
from apps.rbac.route_guard import ModuleActionGuardMixin


INFRASTRUCTURE_APPS = {'api', 'core', 'users', 'dashboard', 'notifications', 'activity', 'usage_tracking', 'rbac'}


class Command(BaseCommand):
    help = 'Verify action guards for registered business endpoints; optionally write their manifest.'

    def add_arguments(self, parser):
        parser.add_argument('--output')

    def handle(self, *args, **options):
        from apps.rbac.rbac_config import ALL_MODULES_CATALOGUE
        codes = {module['code'] for module in ALL_MODULES_CATALOGUE}
        unknown = set(ROUTE_MODULES.values()) - codes
        rows, failures = [], []
        def walk(patterns, prefix=''):
            for pattern in patterns:
                route = prefix + str(pattern.pattern).lstrip('^')
                if isinstance(pattern, URLResolver):
                    walk(pattern.url_patterns, route)
                    continue
                callback = pattern.callback
                cls = getattr(callback, 'cls', None)
                identity = cls.__module__ if cls else getattr(callback, '__module__', '')
                app = identity.split('.')[1] if identity.startswith('apps.') else ''
                module = route_module(route)
                if not module and (not app or app in INFRASTRUCTURE_APPS):
                    continue
                guarded = bool(cls and issubclass(cls, ModuleActionGuardMixin))
                operations = getattr(callback, 'actions', {})
                policies = {}
                modules = {}
                methods = operations or {method: cls.__name__ for method in ('get', 'post', 'put', 'patch', 'delete', 'head') if hasattr(cls, method)}
                for method, name in methods.items():
                    view = object.__new__(cls)
                    if operations:
                        view.action = name
                    request = SimpleNamespace(method=method.upper(), path=route, resolver_match=SimpleNamespace(url_name=pattern.name))
                    policies[method] = operation_action(request, view)
                    modules[method] = request_module(request, view)
                rows.append({'route': route, 'module': module, 'view': identity + '.' + (cls.__name__ if cls else callback.__name__),
                             'guarded': guarded, 'operations': operations, 'actions': policies, 'action_modules': modules})
                if any(action is None for method, action in policies.items() if method != 'options'):
                    failures.append(route + ' (unclassified operation)')
                if not guarded or not module:
                    failures.append(route)
        walk(get_resolver().url_patterns)
        if options['output']:
            from pathlib import Path
            Path(options['output']).write_text(json.dumps(rows, indent=2), encoding='utf-8')
        if unknown or failures:
            raise CommandError(f'Unknown modules: {sorted(unknown)}; unguarded business routes: {failures}')
        self.stdout.write(self.style.SUCCESS(f'{len(rows)} business routes guarded; module codes valid.'))
