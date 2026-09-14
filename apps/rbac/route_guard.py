"""Bind an additive action check to business API routes after URL registration.

The subclass preserves the original permissions, authentication, throttles and
object checks, including custom get_permissions and @action permission overrides.
"""
from django.urls import URLResolver
from rest_framework.exceptions import NotAuthenticated, PermissionDenied
from rest_framework.views import APIView

from .action_policy import (INDEPENDENT_WORKFLOWS, SELF_SERVICE_ACTIONS,
                            request_action_allowed, operation_action,
                            request_module, route_module, additional_actions, resource_modules,
                            RECORD_SCOPED_ACTIONS, record_workflow_not_denied)


class ModuleActionGuardMixin:
    def check_permissions(self, request):
        identity = (self.__class__.__module__, self.__class__.__name__)
        operation = getattr(self, 'action', '')
        independent = identity in INDEPENDENT_WORKFLOWS or operation in SELF_SERVICE_ACTIONS.get(identity[1], set())
        # The invoice preview handler authenticates its legacy iframe token and
        # calls HasModuleAccess itself after authentication.
        independent |= identity == ('apps.finance.views', 'InvoiceViewSet') and operation == 'preview'
        module = request_module(request, self)
        action = operation_action(request, self)
        scoped = operation in RECORD_SCOPED_ACTIONS.get(identity, set())
        if scoped and not request.user.is_authenticated:
            raise NotAuthenticated()
        if scoped and not record_workflow_not_denied(request.user, module, action):
            raise PermissionDenied('This action is denied for your assigned workflow.')
        independent |= scoped
        if module and not action and request.method != 'OPTIONS' and not independent:
            raise PermissionDenied('This operation has no action permission policy.')
        if module and action and not independent:
            if not request.user or not request.user.is_authenticated:
                raise NotAuthenticated()
            for required_module in resource_modules(request, self, module):
                for required_action in {action, *additional_actions(request)}:
                    if not request_action_allowed(request, required_module, required_action):
                        raise PermissionDenied(f'You do not have {required_action} permission for this module.')
        return super().check_permissions(request)


def secure_module_endpoints(patterns, prefix=''):
    """Called once after includes are registered; idempotent for shared routers."""
    for pattern in patterns:
        route = prefix + str(pattern.pattern).lstrip('^')
        if isinstance(pattern, URLResolver):
            secure_module_endpoints(pattern.url_patterns, route)
            continue
        if not route_module(route):
            continue
        callback = pattern.callback
        cls = getattr(callback, 'cls', None)
        if cls and issubclass(cls, ModuleActionGuardMixin):
            continue
        if cls and issubclass(cls, APIView):
            guarded = type(cls.__name__, (ModuleActionGuardMixin, cls), {'__module__': cls.__module__})
            kwargs = getattr(callback, 'initkwargs', {})
            if hasattr(callback, 'actions'):
                pattern.callback = guarded.as_view(callback.actions, **kwargs)
            else:
                pattern.callback = guarded.as_view(**kwargs)
        else:
            # Plain Django business handlers must authenticate as well. DRF
            # accepts their HttpResponse/FileResponse without changing content.
            def handler(self, request, *args, _callback=callback, **kwargs):
                return _callback(request._request, *args, **kwargs)
            guarded = type(callback.__name__, (ModuleActionGuardMixin, APIView), {
                '__module__': callback.__module__,
                **{method: handler for method in ('get', 'post', 'put', 'patch', 'delete', 'head')},
            })
            pattern.callback = guarded.as_view()
