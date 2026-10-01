"""Canonical post-Go opportunity projection and explicit Quote-create eligibility."""
from urllib.parse import urlencode
from uuid import UUID

from django.contrib.auth import get_user_model
from django.core.paginator import EmptyPage, Paginator
from django.db.models import Exists, OuterRef, Q
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.rbac.action_policy import module_action_allowed
from .email_permissions import visible_email_clients
from .models import Quote


GO_DECISIONS = ('bid', 'conditional_bid')
PROPOSAL_CLIENT_STATUSES = frozenset({'active', 'prospect'})
READINESS_PATH = '/api/v1/sales/quotes/preparation-opportunities/'


def _actor(actor):
    current = get_user_model().objects.filter(
        pk=getattr(actor, 'pk', None), is_active=True,
    ).select_related('rbac_profile').first()
    if current is None:
        raise PermissionDenied('Your current account cannot access proposal preparation.')
    return current


def _identifier(value, field):
    try:
        return UUID(str(getattr(value, 'pk', value)))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError({field: 'Select a valid saved record.'}) from None


def client_permits_proposal_preparation(client):
    """User-authorized preparation eligibility; not commercial issue authority."""
    return bool(client and client.status in PROPOSAL_CLIENT_STATUSES and client.new_proposals_permitted)


def _creation_blocker(deal, *, can_create, client_visible):
    if not can_create:
        return 'access', 'Proposal create access is required to start a draft.'
    if deal.stage != 'proposal' or deal.bid_decision not in GO_DECISIONS:
        return 'deal', 'A recorded Bid or Conditional Bid in the Proposal stage is required.'
    if not client_visible:
        return 'client', 'The inherited client is unavailable with your current Sales access.'
    if not client_permits_proposal_preparation(deal.client):
        return 'client', 'This client is not currently permitted for new proposals.'
    return None, None


def require_proposal_creation(actor, deal, client=None):
    """Return fresh actor/Deal/client; callers own any required write locks.

    Accept model objects or canonical IDs. Do not trust objects loaded before a
    user paused in a dialog. A caller holding Deal then Client locks can reuse
    this same check immediately before saving an explicit proposal revision.
    """
    current = _actor(actor)
    if not module_action_allowed(current, 'sales_proposals', 'create'):
        raise PermissionDenied('Proposal create access is required to start a draft.')
    if not module_action_allowed(current, 'sales_opportunities', 'read'):
        raise PermissionDenied('Opportunity read access is required to prepare a proposal.')
    from .bid_preparation import visible_deals
    opportunity = visible_deals(current).select_related('client').filter(pk=_identifier(deal, 'deal')).first()
    if opportunity is None:
        raise NotFound('This opportunity is unavailable.')
    if client is not None and _identifier(client, 'client') != opportunity.client_id:
        raise ValidationError({'client': 'Proposal client must match its opportunity.'})
    client_visible = (module_action_allowed(current, 'sales_clients', 'read')
                      and visible_email_clients(current).filter(pk=opportunity.client_id).exists())
    field, reason = _creation_blocker(opportunity, can_create=True, client_visible=client_visible)
    if reason:
        raise ValidationError({field: reason})
    return current, opportunity, opportunity.client


def _parameters(params):
    if set(params) - {'page', 'page_size', 'search', 'pending_only'}:
        raise ValidationError({'detail': 'Use page, page_size, search and pending_only filters.'})
    try:
        raw_page, raw_size = params.get('page', '1'), params.get('page_size', '100')
        if (isinstance(raw_page, bool) or isinstance(raw_size, bool)
                or not str(raw_page).isdigit() or not str(raw_size).isdigit()):
            raise ValueError
        page, page_size = int(raw_page), int(raw_size)
        if not 1 <= page <= 1_000_000 or not 1 <= page_size <= 500:
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        raise ValidationError({'page': 'Use a positive page and a page_size between 1 and 500.'}) from None
    search = params.get('search', '')
    if not isinstance(search, str) or len(search) > 200 or '\x00' in search:
        raise ValidationError({'search': 'Use search text of at most 200 characters.'})
    pending = params.get('pending_only', 'false')
    if pending not in ('true', 'false', True, False):
        raise ValidationError({'pending_only': 'Use true or false.'})
    return page, page_size, search.strip(), pending in ('true', True)


def preparation_opportunities(actor, params):
    current = _actor(actor)
    if not all(module_action_allowed(current, module, 'read')
               for module in ('sales_proposals', 'sales_opportunities')):
        raise PermissionDenied('Proposal and Opportunity read access are required to view preparation.')
    page, page_size, search, pending_only = _parameters(params)
    from .bid_preparation import visible_deals
    query = visible_deals(current).filter(stage='proposal', bid_decision__in=GO_DECISIONS).annotate(
        has_proposal=Exists(Quote.objects.filter(deal_id=OuterRef('pk'))),
    ).select_related('client', 'owner').order_by('-stage_entered_at', '-id')
    if pending_only:
        query = query.filter(has_proposal=False)
    client_read = module_action_allowed(current, 'sales_clients', 'read')
    clients = visible_email_clients(current) if client_read else None
    if search:
        match = Q(deal_code__icontains=search) | Q(deal_name__icontains=search)
        if client_read:
            match |= Q(client_id__in=clients.values('pk'), client__company_name__icontains=search)
        query = query.filter(match)
    paginator = Paginator(query, page_size)
    try:
        result_page = paginator.page(page)
    except EmptyPage:
        raise NotFound('This preparation page is unavailable.') from None
    rows = list(result_page.object_list)
    visible_client_ids = set(clients.filter(pk__in={row.client_id for row in rows}).values_list('pk', flat=True)) if client_read else set()
    can_create = module_action_allowed(current, 'sales_proposals', 'create')
    results = []
    for deal in rows:
        client_visible = deal.client_id in visible_client_ids
        _, reason = _creation_blocker(deal, can_create=can_create, client_visible=client_visible)
        results.append({
            'id': str(deal.pk), 'deal_code': deal.deal_code, 'deal_name': deal.deal_name,
            'client': str(deal.client_id) if client_visible else None,
            'client_name': deal.client.company_name if client_visible else None,
            'opportunity_type': deal.opportunity_type, 'stage': deal.stage, 'bid_decision': deal.bid_decision,
            'submission_due_date': deal.submission_due_date.isoformat() if deal.submission_due_date else None,
            'owner': str(deal.owner_id) if deal.owner_id else None,
            'owner_name': (deal.owner.get_full_name().strip() or deal.owner.username) if deal.owner else '',
            'service_categories': deal.service_categories, 'currency': deal.currency,
            'updated_at': deal.updated_at.isoformat(), 'has_proposal': deal.has_proposal,
            'can_create_proposal': reason is None, 'blocked_reason': reason,
        })

    def link(number):
        return READINESS_PATH + '?' + urlencode({
            'page': number, 'page_size': page_size, 'search': search,
            'pending_only': 'true' if pending_only else 'false',
        })

    return {'count': paginator.count, 'next': link(page + 1) if result_page.has_next() else None,
            'previous': link(page - 1) if result_page.has_previous() else None, 'results': results}
