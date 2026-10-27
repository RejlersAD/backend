"""EXEC-ACCESS-03: company-wide data only inside authorized Executive reads.

The scope is server-created, actor-bound and reset even when a builder fails.
It never changes roles, Django administrator flags or source-endpoint grants.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from django.db.models import Q
from rest_framework.exceptions import PermissionDenied

_scope = ContextVar('executive_company_reporting', default=None)
SOURCES = {
    'project_control', 'finance_incoming', 'finance_outgoing', 'finance_overview',
    'sales_opportunities', 'sales_handovers', 'procurement', 'procurement_orders',
    'procurement_requisitions', 'procurement_receipts', 'procurement_vendors',
    'hr_management', 'qhse', 'qhse_detailed', 'qhse_quality', 'process_datasheet',
    'electrical_datasheet',
}
ALIASES = {'sales': 'sales_opportunities', 'finance': 'finance_incoming'}


def company_reporting(user, module=None):
    scope = _scope.get()
    return bool(scope and scope[0] == user.pk and (
        module is None or ALIASES.get(module, module) in scope[1]
    ))


@contextmanager
def company_reporting_scope(user, required_tabs=()):
    from .executive_access import allowed_tabs
    from apps.rbac.models import Permission
    from apps.rbac.rbac_config import is_module_enabled

    tabs = set(allowed_tabs(user))
    if required_tabs and not tabs.intersection(required_tabs):
        raise PermissionDenied('You do not have permission to view this Executive tab.')
    modules = set(Permission.objects.filter(
        module__code__in=SOURCES, module__is_active=True,
        action='read', is_active=True,
    ).values_list('module__code', flat=True)) if tabs else set()
    modules = {code for code in modules if is_module_enabled(code)}
    token = _scope.set((user.pk, modules) if tabs else None)
    try:
        yield
    finally:
        _scope.reset(token)


def company_report(*tabs):
    """Use on Executive GET builders only, after their normal endpoint guards."""
    def decorate(function):
        @wraps(function)
        def wrapped(self, request, *args, **kwargs):
            with company_reporting_scope(request.user, tabs):
                return function(self, request, *args, **kwargs)
        return wrapped
    return decorate


def report_action_allowed(user, module, action):
    from apps.rbac.action_policy import module_action_allowed
    if action == 'read' and company_reporting(user, module):
        return True
    return module_action_allowed(user, module, action)


def report_visibility_filter(*, user, module_code, **kwargs):
    if company_reporting(user, module_code):
        return Q()
    from apps.rbac.data_visibility_mixin import build_visibility_filter
    return build_visibility_filter(user=user, module_code=module_code, **kwargs)


def report_projects(user):
    if company_reporting(user, 'project_control'):
        from apps.core.project_models import Project
        return Project.objects.filter(is_deleted=False)
    from apps.project_control.access import accessible_enterprise_projects
    return accessible_enterprise_projects(user)
