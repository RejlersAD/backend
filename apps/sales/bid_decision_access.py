"""Temporary module-access authority for an otherwise unconfigured bid decision."""
from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework.exceptions import NotFound, PermissionDenied

from apps.rbac.action_policy import module_action_allowed, record_workflow_not_denied
from apps.rbac.approval_eligibility import configured_approval_routes, require_configured_approval


BID_DECISION_ROUTE = 'sales_opportunities.Deal.bid_decision'


def use_module_rbac_for_bid_decision():
    if not getattr(settings, 'SALES_BID_DECISION_RBAC_FALLBACK_ENABLED', False):
        return False
    routes = configured_approval_routes()
    # A present but invalid route must never silently relax authority.
    return isinstance(routes, dict) and BID_DECISION_ROUTE not in routes


def require_bid_decision_access(actor, opportunity):
    if not use_module_rbac_for_bid_decision():
        require_configured_approval(
            actor, 'sales_opportunities', opportunity, 'bid_decision',
            accepted_states={'qualified': {'proposal', 'no_bid'}},
        )
        return 'configured_route'

    current = get_user_model().objects.filter(
        pk=getattr(actor, 'pk', None), is_active=True,
    ).select_related('rbac_profile').first()
    if not module_action_allowed(current, 'sales_opportunities', 'read'):
        raise PermissionDenied('Current Opportunity module access is required to record a Go decision.')
    if not record_workflow_not_denied(current, 'sales_opportunities', 'approve'):
        raise PermissionDenied('Go decisions are explicitly denied for your account.')

    # Reuse Sales' current record/organization scope after the command locks Deal.
    from .bid_preparation import visible_deals
    if not visible_deals(current).filter(pk=opportunity.pk).exists():
        raise NotFound('This opportunity is unavailable.')

    # A client can be reassigned after the view's initial lookup. Recheck the
    # known account organizations after the command locks the opportunity so
    # that the action cannot use stale record scope.
    from apps.core.shared_record_targets import organization_id
    organizations = {
        value for value in (
            organization_id(opportunity.owner),
            organization_id(opportunity.client.account_manager),
        ) if value
    }
    if len(organizations) > 1 or (
        organizations
        and not (current.is_staff or current.is_superuser)
        and organization_id(current) not in organizations
    ):
        raise NotFound('This opportunity is unavailable.')
    return 'module_rbac'
