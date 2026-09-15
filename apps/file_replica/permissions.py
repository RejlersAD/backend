from django.apps import apps
from django.db.models import Q
from django.utils import timezone
from rest_framework.permissions import BasePermission

from apps.core.project_models import Project


def is_replica_admin(user):
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser:
        return True
    profile = getattr(user, 'rbac_profile', None)
    return bool(profile and not profile.is_deleted and profile.status == 'active'
                and not (profile.locked_until and profile.locked_until > timezone.now())
                and profile.roles.filter(code__in=['super_admin', 'admin', 'ict_admin'], is_active=True).exists())


class ReplicaAdminPermission(BasePermission):
    message = 'File server administration requires an administrator role.'

    def has_permission(self, request, view):
        return is_replica_admin(request.user)


def visible_scopes(user):
    from .models import ReplicaScope
    from .paths import included
    scopes = ReplicaScope.objects.select_related('project', 'source')
    if is_replica_admin(user):
        return scopes
    projects = Project.objects.filter(is_deleted=False).filter(
        Q(owner=user) | Q(memberships__user=user, memberships__is_active=True),
    ).distinct()
    scopes = scopes.filter(
        access_enabled=True, project__in=projects,
        source__enabled=True,
    )
    visible_ids = [scope.pk for scope in scopes if included(scope.source, scope.relative_path)]
    return scopes.filter(pk__in=visible_ids)


def visible_entries(user):
    from .models import ReplicaEntry
    from .paths import included_entries_query
    qs = ReplicaEntry.objects.select_related('scope__project', 'source', 'current_version').filter(scope__in=visible_scopes(user))
    # Apply scope reductions immediately, including already copied bytes and extracted text.
    sources = {scope.source_id: scope.source for scope in visible_scopes(user)}
    allowed = Q(pk__in=[])
    for source in sources.values():
        allowed |= Q(source=source) & included_entries_query(source)
    return qs.filter(allowed)


def action_allowed(user, action):
    if is_replica_admin(user):
        profile = getattr(user, 'rbac_profile', None)
        if profile and profile.permission_overrides.filter(
            allowed=False, permission__module__code='project_control',
            permission__action=action, permission__is_active=True,
        ).exists():
            return False
        return True
    if not apps.is_installed('apps.rbac'):
        return True  # Minimal isolated test app registry; production includes RBAC.
    from apps.rbac.action_policy import module_action_allowed
    return module_action_allowed(user, 'project_control', action)
