"""Request-scoped audit context; semantic view events take priority over fallbacks."""
from contextvars import ContextVar
from uuid import UUID

from django.db.models import Q

current_audits = ContextVar('current_rbac_audits', default=None)
TELEMETRY_PREFIXES = ('/api/v1/rbac/ai-champion/track/',)
ACTION_LABELS = {
    'role_assign': 'Assign role', 'role_revoke': 'Revoke role',
    'permission_grant': 'Grant permission', 'permission_revoke': 'Revoke permission',
    'password_change': 'Change password', 'password_reset': 'Reset password',
    'mfa_enable': 'Enable MFA', 'mfa_disable': 'Disable MFA',
    'login': 'Sign in', 'logout': 'Sign out',
}


def administrative_audits(queryset):
    """Keep telemetry out of the console, including legacy 'Create track' rows."""
    scope = (
        Q(resource_type__in=[
            'Organization', 'Module', 'Permission', 'Role', 'UserProfile', 'User',
            'AccessRequest', 'UserStorage', 'organizations', 'modules', 'permissions',
            'roles', 'users', 'access-requests', 'storage',
        ])
        | Q(action__in=['login', 'logout', 'role_assign', 'role_revoke',
                       'permission_grant', 'permission_revoke', 'password_change',
                       'password_reset', 'mfa_enable', 'mfa_disable'])
        | Q(metadata__request_path__startswith='/api/v1/rbac/')
    )
    queryset = queryset.filter(scope).exclude(resource_type__iexact='track')
    for prefix in TELEMETRY_PREFIXES:
        queryset = queryset.exclude(metadata__has_key='request_path', metadata__request_path__startswith=prefix)
    return queryset


def request_audit_fields(request, response):
    """Use resolved DRF actions and IDs, never infer the target from path[-2]."""
    match = getattr(request, 'resolver_match', None)
    callback = getattr(match, 'func', None)
    view_class = getattr(callback, 'cls', None)
    queryset = getattr(view_class, 'queryset', None)
    model = getattr(queryset, 'model', None)
    if model is None:
        serializer = getattr(view_class, 'serializer_class', None)
        model = getattr(getattr(serializer, 'Meta', None), 'model', None)
    resource_type = model.__name__ if model else (getattr(match, 'url_name', None) or 'API request')
    action_name = getattr(callback, 'actions', {}).get(request.method.lower(), '')
    standard = {'create': 'create', 'update': 'update', 'partial_update': 'update', 'destroy': 'delete'}
    action = standard.get(action_name, {'DELETE': 'delete', 'PUT': 'update', 'PATCH': 'update'}.get(request.method, 'update'))
    if action_name in standard:
        action_label = f'{action.title()} {resource_type}'
    elif action_name:
        action_label = f'{action_name.replace("_", " ").capitalize()} {resource_type}'
    else:
        action_label = f'{request.method} {resource_type}'

    kwargs = getattr(match, 'kwargs', {})
    identifier = next((kwargs[key] for key in ('pk', 'id', 'user_id', 'role_id') if key in kwargs), None)
    data = getattr(response, 'data', None)
    if identifier is None and response.status_code < 400 and isinstance(data, dict):
        identifier = data.get('id') or data.get('pk')
    resource_id = None
    if identifier is not None:
        try:
            resource_id = UUID(str(identifier))
        except (ValueError, TypeError, AttributeError):
            pass  # Integer IDs belong in metadata; AuditLog.resource_id is UUID-only.
    target = f'{resource_type} #{identifier}' if identifier is not None else request.path
    return {
        'action': action, 'resource_type': resource_type[:100],
        'resource_id': resource_id, 'resource_repr': target[:255],
        'metadata': {
            'request_path': request.path, 'request_method': request.method,
            'response_status': response.status_code, 'view_action': action_name,
            'action_label': action_label, 'target_id': str(identifier) if identifier is not None else None,
            'audit_source': 'request',
        },
    }
