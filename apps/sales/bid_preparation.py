"""Reviewed Sales-to-Planning preparation commands; no commercial inference."""
import hashlib
import json
from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Max, Q
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from apps.rbac.action_policy import module_action_allowed
from apps.rbac.data_visibility_mixin import build_visibility_filter
from .models import BidPreparation, BidPreparationCommand, Deal, Quote, QuotePreparationRevision
from .opportunity_workspace import require_access
from .workflow import _audit
from .proposal_readiness import client_permits_proposal_preparation


FIELDS = ('scope', 'deliverables', 'assumptions', 'exclusions', 'disciplines', 'estimated_hours', 'risks')
DRAFT_STATES = {'draft', 'scope_development', 'estimation', 'internal_review', 'approval'}


class PreparationConflict(APIException):
    status_code = 409
    default_detail = 'This preparation changed. Refresh and review it before saving.'


def digest(value):
    return hashlib.sha256(json.dumps(value, cls=DjangoJSONEncoder, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def _adapter():
    from apps.planning_intelligence import sales_preparation
    return sales_preparation


def _uuid(value, field):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError({field: 'A valid UUID is required.'}) from None


def _actor(actor, *, lock=False):
    query = get_user_model().objects
    if lock:
        query = query.select_for_update(no_key=True)
    current = query.filter(pk=getattr(actor, 'pk', None), is_active=True).first()
    if current is None:
        raise PermissionDenied('Your current account cannot perform this action.')
    return current


def visible_deals(actor):
    """All users with Sales module access can view all opportunities.
    Owner/account manager/organization do NOT restrict visibility per business policy."""
    return Deal.objects.filter(build_visibility_filter(user=actor, module_code='sales', owner_field='owner'))


def _deal(deal_id, actor, *, write=False, lock=False):
    query = Deal.objects.filter(pk__in=visible_deals(actor).values('pk'), pk=_uuid(deal_id, 'deal_id'))
    if lock:
        query = query.select_for_update(no_key=True, of=('self',))
    deal = query.select_related('client', 'owner').first()
    if deal is None:
        raise NotFound('This opportunity is unavailable.')
    require_access(actor, deal, *(['update'] if write else []))
    return deal


def _quote(quote_id, actor, *, write=False, lock=False):
    actions = ('read', 'update') if write else ('read',)
    if not all(module_action_allowed(actor, 'sales_proposals', action) for action in actions):
        raise PermissionDenied('You do not have access to this proposal action.')
    query = Quote.objects.filter(pk=_uuid(quote_id, 'quote_id'), deal__in=visible_deals(actor))
    if lock:
        query = query.select_for_update(of=('self',))
    quote = query.select_related('deal__client', 'client').first()
    if quote is None:
        raise NotFound('This proposal is unavailable.')
    require_access(actor, quote.deal)
    if quote.client_id != quote.deal.client_id:
        raise PreparationConflict('The proposal client no longer matches its opportunity.')
    return quote


def quote_has_protected_evidence(quote, *, include_history=True):
    return bool(include_history and quote.approval_history) or any((quote.approved_by_id, quote.approved_at,
        quote.submitted_version_hash, quote.sent_date, quote.viewed_date, quote.response_date,
        quote.submission_recipient, quote.submission_evidence))


def quote_editable(quote):
    return quote.status in DRAFT_STATES and not quote_has_protected_evidence(quote, include_history=False)


def require_editable(quote):
    if not quote_editable(quote):
        raise PreparationConflict('Approved or submitted proposal revisions are immutable. Create a new revision.')


def _deal_basis(deal):
    return {'id': str(deal.pk), 'client': str(deal.client_id), 'stage': deal.stage,
            'bid_decision': deal.bid_decision, 'updated_at': deal.updated_at.isoformat(),
            'connection': str(BidPreparation.objects.filter(opportunity=deal).values_list('pk', flat=True).first() or '')}


def _quote_basis(quote):
    return {'id': str(quote.pk), 'deal_id': str(quote.deal_id), 'client_id': str(quote.client_id),
            'version': quote.version, 'status': quote.status, 'updated_at': quote.updated_at.isoformat(),
            'fields': {field: getattr(quote, field) for field in FIELDS}}


def _token(actor, action, basis):
    return signing.dumps({'actor': str(actor.pk), 'action': action, 'basis': digest(basis)},
                         salt='sales-bid-preparation', compress=True)


def _check_token(value, actor, action, basis):
    try:
        data = signing.loads(value, salt='sales-bid-preparation', max_age=3600)
        if data != {'actor': str(actor.pk), 'action': action, 'basis': digest(basis)}:
            raise ValueError()
    except (signing.BadSignature, ValueError, TypeError):
        raise PreparationConflict('The proposal or preparation source changed. Refresh and review before saving.') from None


def _connection(deal, actor, *, lock=False, write=False):
    row = BidPreparation.objects.filter(opportunity=deal).first()
    if not row:
        return None, None
    if row.source_basis.get('client_id') and row.source_basis['client_id'] != str(deal.client_id):
        raise PreparationConflict('The opportunity client changed after preparation was connected. Review its lineage.')
    project = _adapter().require_project(actor, deal, row.planning_project_id, write=write, lock=lock)
    return row, project


def _connection_data(row, project):
    return {'id': str(row.pk), 'planning_project': {'id': str(project.pk), 'name': project.name}}


def _allowed_to_connect(deal, actor):
    return (deal.stage in {'proposal', 'negotiation'} and deal.bid_decision in {'bid', 'conditional_bid'}
            and client_permits_proposal_preparation(deal.client)
            and module_action_allowed(actor, 'sales_opportunities', 'update'))


def _require_connection_scope(deal, actor):
    """Use the same known-organization prerequisite for capability and command."""
    from apps.core.shared_record_targets import organization_id
    organizations = {value for value in (organization_id(deal.owner),
                     organization_id(deal.client.account_manager)) if value}
    if len(organizations) != 1:
        raise PermissionDenied('The opportunity ownership needs an organization review before preparation.')
    if not (actor.is_staff or actor.is_superuser) and organization_id(actor) not in organizations:
        raise PermissionDenied('Your current organization does not permit connecting this preparation.')


def bid_projection(deal_id, actor):
    actor = _actor(actor)
    deal = _deal(deal_id, actor)
    connection = None
    reason = ''
    row = BidPreparation.objects.filter(opportunity=deal).first()
    if row:
        try:
            _, project = _connection(deal, actor)
            connection = _connection_data(row, project)
        except (PermissionDenied, NotFound, ValidationError, PreparationConflict):
            reason = 'The connected Planning workspace is unavailable with your current access.'
    scope_reason = None
    try:
        _require_connection_scope(deal, actor)
    except PermissionDenied as exc:
        scope_reason = str(exc.detail)
    allowed = not row and not scope_reason and _allowed_to_connect(deal, actor)
    planning_read = module_action_allowed(actor, 'planning_package', 'read')
    create = allowed and planning_read and module_action_allowed(actor, 'planning_package', 'create')
    attach = allowed and planning_read and module_action_allowed(actor, 'planning_package', 'update')
    if not row and not (create or attach):
        reason = (scope_reason if scope_reason else 'This client is not currently permitted for new proposals.'
                  if not client_permits_proposal_preparation(deal.client)
                  else 'An approved bid decision is required before connecting preparation.'
                  if deal.stage not in {'proposal', 'negotiation'} or deal.bid_decision not in {'bid', 'conditional_bid'}
                  else 'Your current access does not permit connecting preparation.')
    return {'opportunity': {'id': str(deal.pk), 'code': deal.deal_code, 'name': deal.deal_name,
            'stage': deal.stage, 'bid_decision': deal.bid_decision,
            'client': {'id': str(deal.client_id), 'name': deal.client.company_name}},
            'connection': connection, 'connected': bool(row),
            'source_duration_months': deal.project_duration_months if deal.project_duration_months and deal.project_duration_months > 0 else None,
            'requires_duration': not bool(deal.project_duration_months and deal.project_duration_months > 0),
            'capabilities': {'can_create': bool(create), 'can_attach': bool(attach), 'reason': reason or None},
            'expected_token': _token(actor, 'connect', _deal_basis(deal))}


def _page(query, page, render):
    try:
        page = int(page)
        if page < 1:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValidationError({'page': 'Use a positive page number.'}) from None
    size = 20
    return {'count': query.count(), 'page': page, 'page_size': size,
            'results': [render(row) for row in query[(page - 1) * size:page * size]]}


def project_candidates(deal_id, actor, search='', page=1):
    actor = _actor(actor)
    deal = _deal(deal_id, actor)
    query = _adapter().eligible_projects(actor, deal).exclude(sales_bid_preparation__isnull=False)
    if not isinstance(search, str) or len(search) > 200:
        raise ValidationError({'search': 'Use search text up to 200 characters.'})
    if search.strip():
        query = query.filter(name__icontains=search.strip())
    return _page(query.order_by('name', 'pk'), page, lambda row: {'id': str(row.pk), 'name': row.name})


def _command(data, allowed):
    if not isinstance(data, dict) or set(data) - set(allowed):
        raise ValidationError({'detail': 'The preparation request contains unsupported fields.'})
    request_id = _uuid(data.get('request_id'), 'request_id')
    reason = data.get('reason')
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000 or '\x00' in reason:
        raise ValidationError({'reason': 'Enter a review reason up to 1000 characters.'})
    if not isinstance(data.get('expected_token'), str):
        raise ValidationError({'expected_token': 'Refresh the preparation before saving.'})
    return request_id, reason.strip()


def _prior(actor, request_id, action, request_hash):
    prior = BidPreparationCommand.objects.filter(actor=actor, request_id=request_id).first()
    if prior and (prior.action != action or prior.request_hash != request_hash):
        raise PreparationConflict('This request ID was already used for different preparation content.')
    return prior


@transaction.atomic
def connect_preparation(deal_id, actor, data):
    request_id, reason = _command(data, {'request_id', 'expected_token', 'mode', 'planning_project_id', 'duration_months', 'reason'})
    mode = data.get('mode')
    if (mode not in {'create', 'attach'} or (mode == 'create' and 'planning_project_id' in data)
            or (mode == 'attach' and 'duration_months' in data)):
        raise ValidationError({'mode': 'Choose create or attach; attach uses the selected existing workspace and its duration.'})
    actor = _actor(actor, lock=True)
    deal = _deal(deal_id, actor, write=True, lock=True)
    _require_connection_scope(deal, actor)
    if not client_permits_proposal_preparation(deal.client):
        raise ValidationError({'client': 'This client is not currently permitted for new proposals.'})
    if not _allowed_to_connect(deal, actor):
        raise PreparationConflict('An approved bid decision and active proposal stage are required.')
    planning_action = 'create' if mode == 'create' else 'update'
    if not all(module_action_allowed(actor, 'planning_package', action) for action in ('read', planning_action)):
        raise PermissionDenied('You do not have access to connect Planning preparation.')
    request_hash = digest({'deal_id': str(deal.pk), 'data': data})
    prior = _prior(actor, request_id, 'connect', request_hash)
    if prior:
        row, project = _connection(deal, actor, write=mode == 'attach', lock=True)
        if not row or row.pk != prior.preparation_id:
            raise PreparationConflict('The saved preparation connection changed.')
        return {'bid_preparation': bid_projection(deal.pk, actor), 'replayed': True}
    _check_token(data['expected_token'], actor, 'connect', _deal_basis(deal))
    if BidPreparation.objects.filter(opportunity=deal).exists():
        raise PreparationConflict('This opportunity already has a preparation workspace.')
    project = (_create_project(actor, deal, data) if mode == 'create' else
               _adapter().require_project(actor, deal, data.get('planning_project_id'), write=True, lock=True))
    if BidPreparation.objects.filter(planning_project=project).exists():
        raise PreparationConflict('This Planning workspace is already connected to another opportunity.')
    preparation = BidPreparation.objects.create(opportunity=deal, planning_project=project,
        created_by=actor, reason=reason, source_basis={
            'duration_months': str(project.duration_months),
            'duration_basis': ('opportunity' if deal.project_duration_months and deal.project_duration_months > 0
                               else 'reviewer_assumption') if mode == 'create' else 'existing_workspace',
            'opportunity_name': deal.deal_name,
            'opportunity_updated_at': deal.updated_at.isoformat(), 'client_id': str(deal.client_id)})
    BidPreparationCommand.objects.create(actor=actor, request_id=request_id, action='connect',
        request_hash=request_hash, preparation=preparation)
    _audit(deal, actor, 'bid_preparation_connected', reason=reason,
           data={'preparation_id': str(preparation.pk), 'planning_project_id': str(project.pk), 'mode': mode})
    return {'bid_preparation': bid_projection(deal.pk, actor), 'replayed': False}


def _create_project(actor, deal, data):
    from apps.planning_intelligence.models import PlanningProject
    known = deal.project_duration_months if deal.project_duration_months and deal.project_duration_months > 0 else None
    try:
        duration = Decimal(str(known if known else data.get('duration_months', '')))
        if not duration.is_finite() or not Decimal('0') < duration < Decimal('10000') or duration.as_tuple().exponent < -4:
            raise InvalidOperation()
    except (InvalidOperation, ValueError):
        raise ValidationError({'duration_months': 'Enter a positive draft planning duration in months (up to four decimal places).'}) from None
    if known and 'duration_months' in data and str(data['duration_months']) != str(known):
        raise ValidationError({'duration_months': 'Use the duration already recorded on this opportunity.'})
    # No core.Project is created before its governed award/handover command.
    project = PlanningProject.objects.create(name=deal.deal_name[:255], client=deal.client.company_name,
        location=deal.location, phase=deal.scope_type, scope_summary=deal.description,
        effective_date=deal.expected_start_date, duration_months=duration, created_by=actor)
    _adapter().require_project(actor, deal, project.pk)
    return project


def _source(quote, actor, technical_id, *, lock=False):
    preparation, project = _connection(quote.deal, actor, lock=lock)
    if not preparation:
        raise ValidationError({'preparation': 'Connect a Planning workspace to this opportunity first.'})
    snapshot = _adapter().source_snapshot(actor, quote.deal, project, technical_id, lock=lock)
    return preparation, snapshot


def _preview_basis(quote, preparation, snapshot):
    return {'quote': _quote_basis(quote), 'deal': _deal_basis(quote.deal),
            'preparation': str(preparation.pk), 'source': snapshot['fingerprint']}


def preparation_sources(quote_id, actor, search='', page=1):
    actor = _actor(actor)
    quote = _quote(quote_id, actor)
    preparation, project = _connection(quote.deal, actor)
    if not preparation:
        return {'count': 0, 'page': 1, 'page_size': 20, 'results': []}
    return _adapter().source_candidates(actor, quote.deal, project, search=search, page=page)


def preview_preparation(quote_id, actor, data):
    if not isinstance(data, dict) or set(data) != {'technical_proposal_id'}:
        raise ValidationError({'technical_proposal_id': 'Select one exact technical proposal revision.'})
    actor = _actor(actor)
    quote = _quote(quote_id, actor)
    preparation, snapshot = _source(quote, actor, data['technical_proposal_id'])
    proposed = {field: value for field, value in snapshot['proposed_fields'].items() if field in FIELDS}
    return {'source': snapshot['source'], 'evidence': snapshot['evidence'],
            'proposed_fields': proposed, 'current_fields': {field: getattr(quote, field) for field in FIELDS},
            'supported_fields': list(proposed), 'warnings': snapshot.get('warnings', []),
            'expected_token': _token(actor, 'prepare', _preview_basis(quote, preparation, snapshot))}


def preparation_projection(quote_id, actor):
    actor = _actor(actor)
    quote = _quote(quote_id, actor)
    bid = bid_projection(quote.deal_id, actor)
    history, sources = [], {}
    for capture in quote.preparation_revisions.select_related('created_by').order_by('-revision')[:30]:
        state, source, technical_id = 'unavailable', {}, None
        try:
            if capture.technical_proposal_id not in sources:
                try:
                    sources[capture.technical_proposal_id] = _source(quote, actor, capture.technical_proposal_id)[1]
                except (PermissionDenied, NotFound, ValidationError, PreparationConflict) as exc:
                    sources[capture.technical_proposal_id] = exc
            snapshot = sources[capture.technical_proposal_id]
            if isinstance(snapshot, Exception):
                raise snapshot
            state = 'current' if digest(snapshot['fingerprint']) == capture.source_fingerprint else 'changed'
            source, technical_id = capture.source, str(capture.technical_proposal_id)
        except (PermissionDenied, NotFound, ValidationError, PreparationConflict):
            pass
        history.append({'id': str(capture.pk), 'revision': capture.revision,
            'technical_proposal_id': technical_id, 'source': source, 'source_state': state,
            'selected_fields': capture.selected_fields, 'reason': capture.reason,
            'created_at': capture.created_at.isoformat(),
            'created_by': {'id': str(capture.created_by_id), 'name': capture.created_by.get_full_name() or capture.created_by.username}})
    client_eligible = client_permits_proposal_preparation(quote.client)
    open_bid = quote.deal.stage in {'proposal', 'negotiation'} and quote.deal.bid_decision in {'bid', 'conditional_bid'}
    allowed = bool(quote_editable(quote) and open_bid and client_eligible and bid['connection'] and
                   module_action_allowed(actor, 'sales_proposals', 'update'))
    reason = (None if allowed else 'This client is not currently permitted for new proposals.' if not client_eligible
              else 'This opportunity is no longer open for proposal preparation.' if not open_bid
              else 'Approved or submitted proposal revisions are immutable. Create a new revision.'
              if not quote_editable(quote) else 'Connect an accessible Planning workspace to prepare this proposal.'
              if not bid['connection'] else 'Your current access does not permit preparing this proposal.')
    return {'quote': {'id': str(quote.pk), 'number': quote.quote_number, 'version': quote.version,
            'status': quote.status, 'deal_id': str(quote.deal_id)},
            'bid_preparation': bid, 'connection': bid['connection'], 'history': history,
            'history_count': quote.preparation_revisions.count(),
            'capabilities': {'can_prepare': allowed, 'reason': reason}}


@transaction.atomic
def apply_preparation(quote_id, actor, data):
    request_id, reason = _command(data, {'request_id', 'expected_token', 'technical_proposal_id', 'selected_fields', 'reason'})
    selected = data.get('selected_fields')
    if (not isinstance(selected, list) or not selected or any(not isinstance(x, str) for x in selected)
            or len(selected) != len(set(selected)) or set(selected) - set(FIELDS)):
        raise ValidationError({'selected_fields': 'Choose one or more distinct supported proposal fields.'})
    actor = _actor(actor, lock=True)
    initial = _quote(quote_id, actor, write=True)
    deal = _deal(initial.deal_id, actor, lock=True)
    initial.deal = deal
    preparation, snapshot = _source(initial, actor, data.get('technical_proposal_id'), lock=True)
    quote = _quote(quote_id, actor, write=True, lock=True)
    if quote.deal_id != deal.pk:
        raise PreparationConflict('The proposal opportunity changed. Refresh preparation.')
    quote.deal = deal
    request_hash = digest({'quote_id': str(quote.pk), 'data': data})
    prior = _prior(actor, request_id, 'prepare', request_hash)
    if prior:
        capture = prior.capture
        if (capture.quote_id != quote.pk or capture.preparation_id != preparation.pk
                or capture.technical_proposal_id != int(data['technical_proposal_id'])
                or capture.source_fingerprint != digest(snapshot['fingerprint'])
                or any(getattr(quote, field) != value for field, value in capture.applied_fields.items())
                or quote.preparation_revisions.order_by('-revision').first().pk != capture.pk):
            raise PreparationConflict('The saved capture or its source changed after this request. Refresh preparation.')
        return {'preparation': preparation_projection(quote.pk, actor), 'replayed': True}
    require_editable(quote)
    if not client_permits_proposal_preparation(quote.client):
        raise ValidationError({'client': 'This client is not currently permitted for new proposals.'})
    if deal.stage not in {'proposal', 'negotiation'} or deal.bid_decision not in {'bid', 'conditional_bid'}:
        raise PreparationConflict('This opportunity is no longer open for proposal preparation.')
    _check_token(data['expected_token'], actor, 'prepare', _preview_basis(quote, preparation, snapshot))
    if set(selected) - set(snapshot['proposed_fields']):
        raise ValidationError({'selected_fields': 'The source does not provide every selected field.'})
    before = {field: getattr(quote, field) for field in selected}
    applied = {field: snapshot['proposed_fields'][field] for field in selected}
    for field, value in applied.items():
        setattr(quote, field, value)
    quote.save(update_fields=[*selected, 'updated_at'])
    revision = (quote.preparation_revisions.aggregate(value=Max('revision'))['value'] or 0) + 1
    capture = QuotePreparationRevision.objects.create(quote=quote, preparation=preparation,
        technical_proposal_id=data['technical_proposal_id'], revision=revision,
        source=snapshot['source'], evidence=snapshot['evidence'], source_fingerprint=digest(snapshot['fingerprint']),
        selected_fields=selected, before_fields=before, applied_fields=applied, reason=reason, created_by=actor)
    BidPreparationCommand.objects.create(actor=actor, request_id=request_id, action='prepare',
        request_hash=request_hash, preparation=preparation, capture=capture)
    _audit(deal, actor, 'proposal_preparation_captured', reason=reason, data={
        'proposal_id': str(quote.pk), 'capture_id': str(capture.pk), 'revision': revision,
        'technical_proposal_id': str(data['technical_proposal_id']), 'selected_fields': selected})
    return {'preparation': preparation_projection(quote.pk, actor), 'replayed': False}
