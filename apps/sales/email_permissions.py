"""Explicit authority and existing canonical visibility for email conversion."""

from types import SimpleNamespace

from django.db.models import Q
from rest_framework.exceptions import PermissionDenied

from apps.rbac.action_policy import module_action_allowed
from apps.rbac.data_visibility_mixin import build_visibility_filter
from apps.rbac.permissions import IsAdmin

from .models import Client, Deal, SalesEmailIntake, SalesMailboxConnection


EMAIL_OPPORTUNITY_ACTIONS = (
    ('sales_email_intake', 'read'),
    ('sales_email_intake', 'create'),
    ('sales_opportunities', 'read'),
    ('sales_opportunities', 'create'),
    ('sales_clients', 'read'),
)


def visible_mailbox_connections(user):
    """Reuse the existing mailbox owner/admin rule; action grants stay separate."""
    queryset = SalesMailboxConnection.objects.all()
    if not user or not user.is_authenticated or not user.is_active:
        return queryset.none()
    if IsAdmin().has_permission(SimpleNamespace(user=user), None):
        return queryset
    return queryset.filter(created_by=user)


def visible_email_intakes(user):
    """Keep legacy visibility while captured evidence follows its mailbox."""
    queryset = SalesEmailIntake.objects.all()
    if not user or not user.is_authenticated or not user.is_active:
        return queryset.none()
    return queryset.filter(
        Q(mailbox_connection__isnull=True)
        | Q(mailbox_connection_id__in=visible_mailbox_connections(user).values('pk'))
    )


def require_email_capture_access(user):
    if (
        not user or not user.is_authenticated or not user.is_active
        or not all(module_action_allowed(user, 'sales_email_intake', action) for action in ('read', 'create'))
    ):
        raise PermissionDenied('You do not have access to capture emails from this mailbox.')


def can_create_email_opportunity(user):
    return all(module_action_allowed(user, module, action) for module, action in EMAIL_OPPORTUNITY_ACTIONS)


def require_email_opportunity_access(user):
    if not can_create_email_opportunity(user):
        raise PermissionDenied('You do not have access to create opportunities from email.')


def visible_email_clients(user):
    return Client.objects.filter(build_visibility_filter(
        user=user, module_code='sales', owner_field='account_manager',
    ))


def visible_email_opportunities(user):
    return Deal.objects.filter(build_visibility_filter(
        user=user, module_code='sales', owner_field='owner',
    ))
