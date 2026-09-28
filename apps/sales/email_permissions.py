"""Explicit authority and existing canonical visibility for email conversion."""

from rest_framework.exceptions import PermissionDenied

from apps.rbac.action_policy import module_action_allowed
from apps.rbac.data_visibility_mixin import build_visibility_filter

from .models import Client, Deal


EMAIL_OPPORTUNITY_ACTIONS = (
    ('sales_email_intake', 'read'),
    ('sales_email_intake', 'create'),
    ('sales_opportunities', 'read'),
    ('sales_opportunities', 'create'),
    ('sales_clients', 'read'),
)


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
