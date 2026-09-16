"""Bind an additive action check to business API routes after URL registration.

The subclass preserves the original permissions, authentication, throttles and
object checks, including custom get_permissions and @action permission overrides.
"""
from django.urls import URLResolver
from django.db import transaction
from rest_framework.exceptions import NotAuthenticated, PermissionDenied
from rest_framework.views import APIView

from .action_policy import (INDEPENDENT_WORKFLOWS, SELF_SERVICE_ACTIONS,
                            request_action_allowed, operation_action,
                            request_module, route_module, additional_actions, resource_modules,
                            RECORD_SCOPED_ACTIONS, record_workflow_not_denied)


class ModuleActionGuardMixin:
    def dispatch(self, request, *args, **kwargs):
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return super().dispatch(request, *args, **kwargs)
        # Keep eligibility checks and the ensuing decision in one transaction.
        # DRF converts exceptions to responses; explicitly roll back those too.
        with transaction.atomic():
            response = super().dispatch(request, *args, **kwargs)
            if response.status_code >= 400:
                transaction.set_rollback(True)
            return response

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
        # A workflow may authorize a narrowly verified record assignment. The
        # hook must check the current stage; module-wide action grants and all
        # other workflow decisions still use the normal policy below.
        assigned_approval = False
        if scoped and action == 'approve':
            assignment_check = getattr(self, 'record_scoped_approval_allowed', None)
            if callable(assignment_check):
                assigned_approval = assignment_check(request, module)
        independent |= scoped and (action != 'approve' or assigned_approval)
        if module and not action and request.method != 'OPTIONS' and not independent:
            raise PermissionDenied('This operation has no action permission policy.')
        if module and action and not independent:
            if not request.user or not request.user.is_authenticated:
                raise NotAuthenticated()
            for required_module in resource_modules(request, self, module):
                for required_action in {action, *additional_actions(request)}:
                    if not request_action_allowed(request, required_module, required_action):
                        raise PermissionDenied(f'You do not have {required_action} permission for this module.')
        result = super().check_permissions(request)
        if action == 'approve' and request.method not in ('GET', 'HEAD', 'OPTIONS'):
            approved_actions = getattr(self, 'business_approval_actions', ())
            fields_command = bool(set(getattr(self, 'business_approval_fields', ())) & set(request.data))
            if operation not in approved_actions and self.__class__.__name__ not in approved_actions and not fields_command:
                raise PermissionDenied('No verified business approval route is configured for this operation.')
            if getattr(self, 'detail', False) and hasattr(self, 'get_object'):
                obj = self.get_object()
                type(obj).objects.select_for_update(of=('self',)).get(pk=obj.pk)
        self._guard_decision_fields(request, operation)
        return result

    def _guard_decision_fields(self, request, operation):
        """Generic CRUD cannot manufacture or erase a recorded decision."""
        if operation not in {'create', 'update', 'partial_update'}:
            return
        if operation in getattr(self, 'business_approval_actions', ()):
            return  # The explicitly registered command validates its transition.
        decision_fields = {
            'approved_by', 'approved_by_id', 'approved_at', 'approval_status', 'approval',
            'verified_by', 'verified_at', 'verification_status', 'reviewed_by',
            'decided_by', 'decided_at', 'approval_history',
            'approved', 'is_approved', 'accepted_by', 'accepted_at', 'rejected_by', 'rejected_at',
        }
        protected = set(request.data) & decision_fields
        terminal = {'approved', 'rejected', 'denied', 'verified', 'accepted', 'ready_to_submit',
                    'hr_approved', 'finance_approved', 'ignored'}
        current = None
        if 'status' in request.data and operation != 'create':
            current = self.get_object()
            if str(getattr(current, 'status', '')).casefold() in terminal:
                protected.add('status')
        if str(request.data.get('status', '')).casefold() in terminal:
            protected.add('status')
        if not protected or not hasattr(self, 'get_serializer'):
            return
        serializer = self.get_serializer()
        protected = {key for key in protected if key in serializer.fields
                     and not serializer.fields[key].read_only}
        if not protected:
            return
        if current is None and operation != 'create':
            current = self.get_object()
        for key in protected:
            old = getattr(current, key, None) if current is not None else None
            if hasattr(old, 'pk'):
                old = old.pk
            if str(request.data[key]) != str(old):
                raise PermissionDenied('Use the assigned approval workflow to change decision fields.')


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
