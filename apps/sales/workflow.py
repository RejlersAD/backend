"""Governed Sales opportunity commands and Project Control handover."""

import logging
import hashlib
import json

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.core.project_models import Project, ProjectMember
from apps.project_control.models import Estimate
from apps.notifications.services import NotificationService
from apps.rbac.models import UserProfile

from .models import Deal, OpportunityAuditEvent, ProjectHandover, SalesLetter, SalesLetterTemplate
from .workspace_models import OpportunityWorkspaceUpload


HANDOVER_REQUIRED_ITEMS = (
    'authorization_to_proceed', 'final_scope', 'client_governance',
    'contract_value', 'approved_hours_budget', 'project_dates',
    'billing_milestones', 'payment_terms', 'risks', 'project_manager',
)


def require_project_manager(manager):
    from apps.rbac.approval_eligibility import approval_access, has_business_position
    if not manager or not has_business_position(manager, ('project_manager',)):
        raise ValidationError({'nominated_project_manager': 'Nominate an active employee holding the Project Manager business position before handover.'})
    if not approval_access(manager, 'sales_handovers'):
        raise ValidationError({'nominated_project_manager': 'The nominated Project Manager needs current handover approval access.'})
    return manager


def _audit(opportunity, actor, event_type, *, from_stage='', to_stage='', reason='', data=None):
    return OpportunityAuditEvent.objects.create(
        opportunity=opportunity,
        actor=actor,
        event_type=event_type,
        from_stage=from_stage,
        to_stage=to_stage,
        reason=reason,
        data=data or {},
    )


def _require(value, field, missing):
    if value in (None, '', [], {}):
        missing.append(field)


def _sales_notification_recipients(opportunity):
    """Resolve explicit opportunity assignments, not every Sales module user."""
    recipients = {
        user.pk: user
        for user in opportunity.team_members.filter(is_active=True)
    }
    if opportunity.owner_id and opportunity.owner.is_active:
        recipients[opportunity.owner_id] = opportunity.owner
        owner_profile = UserProfile.objects.filter(
            user_id=opportunity.owner_id,
            is_deleted=False,
            status='active',
        ).select_related('manager__user').first()
        manager_profile = owner_profile.manager if owner_profile else None
        if (
            manager_profile
            and not manager_profile.is_deleted
            and manager_profile.status == 'active'
            and manager_profile.user.is_active
        ):
            recipients[manager_profile.user_id] = manager_profile.user
    return list(recipients.values())


def _notify_sales_qualification(opportunity, actor, special_note=''):
    recipients = _sales_notification_recipients(opportunity)
    if not recipients:
        return []
    notifications = NotificationService.bulk_notify(
        recipients,
        title='New Opportunity Submitted',
        message=f'Opportunity {opportunity.deal_name} has been submitted for Internal Sales Review.',
        category='INFO',
        priority='NORMAL',
        action_label='Open Opportunity Record',
        action_url=f'/sales/opportunities?record={opportunity.pk}',
        sender=actor,
        force_in_app=True,
        metadata={
            'module': 'sales',
            'department': 'sales',
            'event_type': 'qualification_submitted',
            'action_type': 'sales_qualification_review',
            'popup_required': True,
            'persistent_until_action': True,
            'opportunity_id': str(opportunity.pk),
            'deal_code': opportunity.deal_code,
            'special_note': special_note,
        },
    )
    if len(notifications) != len(recipients):
        raise ValidationError({
            'notification': 'The opportunity could not be submitted because reviewer notifications were not saved.',
        })
    return notifications


@transaction.atomic
def submit_qualification(opportunity, actor, special_note=''):
    opportunity = Deal.objects.select_for_update().get(pk=opportunity.pk)
    if opportunity.stage != 'lead':
        raise ValidationError({'stage': 'Only a lead can be submitted for qualification.'})
    note = str(special_note or '').strip()
    if len(note) > 1000:
        raise ValidationError({'special_note': 'Special note must be 1000 characters or fewer.'})
    missing = []
    for value, field in [
        (opportunity.submission_due_date, 'submission_due_date'),
        (opportunity.owner_id, 'owner'),
    ]:
        _require(value, field, missing)
    warnings = []
    if not opportunity.scope_type:
        warnings.append({
            'field': 'scope_type',
            'message': 'Scope type has not been provided. You may continue with the submission.',
        })
    has_required_attachment = OpportunityWorkspaceUpload.objects.filter(
        workspace__opportunity=opportunity,
        status='ready',
    ).exists()
    if not has_required_attachment:
        warnings.append({
            'field': 'required_attachment',
            'message': 'No attachment has been provided. You may continue with the submission.',
        })
    if missing:
        raise ValidationError({'missing_fields': missing})
    if opportunity.framework_id:
        if opportunity.framework.client_id != opportunity.client_id:
            raise ValidationError({'framework': 'The framework belongs to a different client.'})
        if not opportunity.framework.is_eligible:
            raise ValidationError({
                'framework': 'Only an active framework within its effective dates can be used.',
            })
    old = opportunity.stage
    opportunity.stage = 'qualified'
    opportunity.stage_entered_at = timezone.now()
    opportunity.save()
    _audit(
        opportunity,
        actor,
        'qualification_submitted',
        from_stage=old,
        to_stage=opportunity.stage,
        reason=note,
        data={'special_note': note} if note else {},
    )
    _notify_sales_qualification(opportunity, actor, note)
    opportunity.submission_warnings = warnings
    return opportunity


@transaction.atomic
def record_bid_decision(opportunity, actor, decision, reason=''):
    opportunity = Deal.objects.select_for_update().get(pk=opportunity.pk)
    from .bid_decision_access import require_bid_decision_access
    decision_authority = require_bid_decision_access(actor, opportunity)
    if opportunity.stage != 'qualified':
        raise ValidationError({'stage': 'Bid decision is available only for a qualified opportunity.'})
    if decision not in {'bid', 'conditional_bid', 'no_bid'}:
        raise ValidationError({'decision': 'Use bid, conditional_bid, or no_bid.'})
    if decision in {'conditional_bid', 'no_bid'} and not reason:
        raise ValidationError({'reason': 'A justification is required for this decision.'})
    # Warning only: record the Go / No-go decision and flag incomplete commercials.
    missing_commercials = opportunity.estimated_value is None or not opportunity.currency
    opportunity.decision_warnings = (
        ['Complete the estimated value and currency to support downstream proposal and award controls.']
        if missing_commercials else []
    )
    approval_value = Decimal(str(getattr(settings, 'SALES_MANAGEMENT_APPROVAL_VALUE', 5000000)))
    requires_management = opportunity.risk_level in {'high', 'critical'} or (
        opportunity.estimated_value is not None and opportunity.estimated_value >= approval_value
    )
    if decision_authority == 'configured_route' and requires_management and actor.id == opportunity.owner_id:
        raise ValidationError({
            'approver': 'A high-risk or high-value bid decision requires independent management approval.',
        })
    old = opportunity.stage
    opportunity.bid_decision = decision
    opportunity.bid_decision_reason = reason
    opportunity.bid_decided_by = actor
    opportunity.bid_decided_at = timezone.now()
    opportunity.stage = 'no_bid' if decision == 'no_bid' else 'proposal'
    opportunity.stage_entered_at = timezone.now()
    opportunity.save()
    _audit(
        opportunity, actor, 'bid_decision', from_stage=old, to_stage=opportunity.stage,
        reason=reason, data={'decision': decision, 'decision_authority': decision_authority},
    )
    return opportunity


@transaction.atomic
def record_ceo_decision(opportunity, actor, decision, reason=''):
    opportunity = Deal.objects.select_for_update().get(pk=opportunity.pk)
    from apps.rbac.approval_eligibility import approval_access, has_business_position
    if not approval_access(actor, 'sales_opportunities'):
        raise PermissionDenied('Your current access does not permit CEO opportunity decisions.')
    if not has_business_position(actor, ('ceo',)):
        raise PermissionDenied('Only the CEO can record this lifecycle decision.')
    if opportunity.stage != 'proposal' or opportunity.bid_decision not in {'bid', 'conditional_bid'}:
        raise ValidationError({'stage': 'CEO decision is available only after a Bid or Conditional Bid enters proposal stage.'})
    value = str(decision or '').strip().lower()
    if value in {'go', 'approved', 'approve', 'yes'}:
        approved = True
    elif value in {'no_go', 'nogo', 'rejected', 'reject', 'no'}:
        approved = False
    else:
        raise ValidationError({'decision': 'Use go or no_go.'})
    note = str(reason or '').strip()
    if not approved and not note:
        raise ValidationError({'reason': 'A reason is required when CEO marks no_go.'})
    old_stage = opportunity.stage
    if not approved:
        opportunity.bid_decision = 'no_bid'
        opportunity.bid_decision_reason = note
        opportunity.bid_decided_by = actor
        opportunity.bid_decided_at = timezone.now()
        opportunity.stage = 'no_bid'
        opportunity.stage_entered_at = timezone.now()
    opportunity.save()
    _audit(
        opportunity,
        actor,
        'ceo_gate_decision',
        from_stage=old_stage,
        to_stage=opportunity.stage,
        reason=note,
        data={'decision': 'approved' if approved else 'rejected'},
    )
    return opportunity


def close_opportunity(opportunity, actor, *, outcome, reason):
    if opportunity.stage in {'awarded', 'converted', 'lost', 'no_bid', 'cancelled'}:
        raise ValidationError({'stage': 'This opportunity is already closed.'})
    if outcome not in {'lost', 'cancelled'}:
        raise ValidationError({'outcome': 'Use lost or cancelled. No-bid is controlled by the bid decision.'})
    if not reason:
        raise ValidationError({'reason': 'A close or loss reason is required.'})
    old = opportunity.stage
    opportunity.stage = outcome
    opportunity.stage_entered_at = timezone.now()
    opportunity.loss_reason = reason
    opportunity.save()
    _audit(
        opportunity, actor, 'opportunity_closed', from_stage=old, to_stage=outcome,
        reason=reason, data={'outcome': outcome},
    )
    return opportunity


def enter_negotiation(opportunity, actor, reason=''):
    if opportunity.stage != 'proposal':
        raise ValidationError({'stage': 'Only an opportunity with an issued proposal can enter negotiation.'})
    if not opportunity.quotes.filter(status__in=['submitted', 'sent', 'viewed', 'accepted']).exists():
        raise ValidationError({'proposal': 'At least one issued proposal revision is required.'})
    old = opportunity.stage
    opportunity.stage = 'negotiation'
    opportunity.stage_entered_at = timezone.now()
    opportunity.save()
    _audit(opportunity, actor, 'negotiation_entered', from_stage=old, to_stage=opportunity.stage, reason=reason)
    return opportunity


def submit_award(opportunity, actor, *, reference, award_date, award_value, handover_data=None):
    if opportunity.stage != 'negotiation':
        raise ValidationError({'stage': 'Only an opportunity in negotiation can be submitted for award approval.'})
    missing = []
    for value, field in [(reference, 'award_reference'), (award_date, 'award_date'), (award_value, 'award_value')]:
        _require(value, field, missing)
    if missing:
        raise ValidationError({'missing_fields': missing})
    if award_value <= 0:
        raise ValidationError({'award_value': 'Award value must be greater than zero.'})
    old = opportunity.stage
    opportunity.award_reference = reference
    opportunity.award_date = award_date
    opportunity.award_value = award_value
    opportunity.handover_data = handover_data or {}
    opportunity.award_status = 'pending'
    opportunity.award_submitted_by = actor
    opportunity.award_submitted_at = timezone.now()
    opportunity.award_approved_by = None
    opportunity.award_approved_at = None
    opportunity.award_rejection_reason = ''
    opportunity.stage = 'award_pending'
    opportunity.stage_entered_at = timezone.now()
    opportunity.save()
    _audit(opportunity, actor, 'award_submitted', from_stage=old, to_stage=opportunity.stage, data={
        'award_reference': reference, 'award_value': str(award_value), 'currency': opportunity.currency,
    })
    return opportunity


@transaction.atomic
def decide_award(opportunity, actor, *, approved, reason=''):
    opportunity = Deal.objects.select_for_update().get(pk=opportunity.pk)
    from apps.rbac.approval_eligibility import require_configured_approval
    require_configured_approval(actor, 'sales_opportunities', opportunity,
                                'approve_award' if approved else 'reject_award')
    if opportunity.stage != 'award_pending' or opportunity.award_status != 'pending':
        raise ValidationError({'stage': 'This opportunity is not awaiting award approval.'})
    if opportunity.award_submitted_by_id == actor.id:
        raise ValidationError({'approver': 'The award submitter cannot approve or reject their own submission.'})
    old = opportunity.stage
    if approved:
        opportunity.award_status = 'approved'
        opportunity.award_approved_by = actor
        opportunity.award_approved_at = timezone.now()
        opportunity.award_rejection_reason = ''
        opportunity.actual_value = opportunity.award_value
        opportunity.stage = 'awarded'
        event_type = 'award_approved'
    else:
        if not reason:
            raise ValidationError({'reason': 'A rejection reason is required.'})
        opportunity.award_status = 'rejected'
        opportunity.award_rejection_reason = reason
        opportunity.stage = 'negotiation'
        event_type = 'award_rejected'
    opportunity.stage_entered_at = timezone.now()
    opportunity.save()
    _audit(opportunity, actor, event_type, from_stage=old, to_stage=opportunity.stage, reason=reason)
    if approved:
        proposal = opportunity.quotes.filter(
            status__in=['submitted', 'sent', 'viewed', 'accepted', 'won'],
        ).order_by('-version', '-created_at').first()
        if not proposal:
            raise ValidationError({'proposal': 'An approved submitted proposal is required to initiate handover.'})
        manager = require_project_manager(opportunity.nominated_project_manager)
        ProjectHandover.objects.get_or_create(
            opportunity=opportunity,
            defaults={
                'proposal': proposal,
                'owner': opportunity.owner,
                'project_manager': manager,
                'signed_contract_reference': opportunity.award_reference,
                'contract_value': opportunity.award_value,
                'currency': opportunity.currency,
                'contract_start_date': opportunity.expected_start_date,
                'checklist': opportunity.handover_data.get('checklist', {}),
                'billing_milestones': opportunity.handover_data.get('billing_milestones', []),
                'delivery_data': opportunity.handover_data,
            },
        )
    return opportunity


def submit_handover_for_acceptance(handover, actor):
    require_project_manager(handover.project_manager)
    if actor.id != handover.owner_id:
        raise ValidationError({'owner': 'Only the handover owner can submit it for acceptance.'})
    if handover.status not in {
        'initiated', 'contract_verification', 'delivery_preparation',
        'commercial_review', 'meeting', 'returned',
    }:
        raise ValidationError({'status': 'This handover cannot be submitted from its current status.'})
    missing = [key for key in HANDOVER_REQUIRED_ITEMS if not handover.checklist.get(key)]
    if missing:
        raise ValidationError({'missing_checklist_items': missing})
    handover.status = 'acceptance_pending'
    handover.returned_reason = ''
    handover.save(update_fields=['status', 'returned_reason', 'updated_at'])
    _audit(handover.opportunity, actor, 'handover_submitted', data={'handover_id': str(handover.id)})
    return handover


@transaction.atomic
def decide_handover(handover, actor, *, accepted, comment=''):
    handover = ProjectHandover.objects.select_for_update().get(pk=handover.pk)
    require_project_manager(handover.project_manager)
    from apps.rbac.approval_eligibility import require_approval
    require_approval(actor, 'sales_handovers', assigned=actor.pk == handover.project_manager_id,
                     current=handover.status == 'acceptance_pending')
    if handover.status != 'acceptance_pending':
        raise ValidationError({'status': 'The handover is not awaiting delivery acceptance.'})
    if actor.id != handover.project_manager_id:
        raise ValidationError({'project_manager': 'Only the nominated Project Manager can decide the handover.'})
    if accepted:
        handover.status = 'accepted'
        handover.accepted_by = actor
        handover.accepted_at = timezone.now()
        handover.acceptance_comment = comment
        event_type = 'handover_accepted'
    else:
        if not comment:
            raise ValidationError({'comment': 'A correction reason is required.'})
        handover.status = 'returned'
        handover.returned_reason = comment
        event_type = 'handover_returned'
    handover.save()
    _audit(handover.opportunity, actor, event_type, reason=comment, data={'handover_id': str(handover.id)})
    return handover


@transaction.atomic
def convert_to_project(opportunity_id, actor, *, project_code, project_name=None):
    # Do not join the nullable converted_project relation into SELECT FOR UPDATE;
    # PostgreSQL cannot lock the nullable side of that outer join.
    opportunity = Deal.objects.select_for_update().select_related('client').get(pk=opportunity_id)
    if opportunity.converted_project_id:
        return opportunity, opportunity.converted_project, False
    if opportunity.stage != 'awarded' or opportunity.award_status != 'approved':
        raise ValidationError({'award': 'An independently approved award is required before conversion.'})
    try:
        handover = opportunity.project_handover
    except ProjectHandover.DoesNotExist as exc:
        raise ValidationError({'handover': 'A formal project handover is required before conversion.'}) from exc
    if handover.status != 'accepted':
        raise ValidationError({'handover': 'The nominated Project Manager must accept the complete handover.'})
    if not project_code:
        raise ValidationError({'project_code': 'A reserved project code is required.'})
    if Project.objects.filter(code=project_code).exists():
        raise ValidationError({'project_code': 'This project code is already in use.'})

    start = opportunity.expected_start_date or opportunity.award_date
    end = start + timedelta(days=30 * opportunity.project_duration_months) if start and opportunity.project_duration_months else None
    owner = require_project_manager(handover.project_manager)
    project = Project.objects.create(
        code=project_code,
        name=project_name or opportunity.deal_name,
        description=opportunity.description,
        status='planning',
        owner=owner,
        start_date=start,
        end_date=end,
        contract_value=opportunity.award_value,
        currency=opportunity.currency,
        scope_type=opportunity.scope_type,
        client_name=opportunity.client.company_name,
        client=opportunity.client,
        location=opportunity.location,
        tags=opportunity.tags,
        custom_fields={
            **opportunity.handover_data,
            'source_opportunity_id': str(opportunity.id),
            'source_opportunity_code': opportunity.deal_code,
            'award_reference': opportunity.award_reference,
            'mobilisation_status': 'pending',
        },
    )
    ProjectMember.objects.get_or_create(project=project, user=owner, defaults={'role': 'project_manager'})
    Estimate.objects.create(
        project=project,
        version=1,
        kind='awarded',
        source='manual',
        status='approved',
        title=f'Awarded value from {opportunity.deal_code}',
        currency=opportunity.currency,
        total_amount=opportunity.award_value,
        snapshot_date=opportunity.award_date,
        notes=f'Created during approved Sales handover. Award reference: {opportunity.award_reference}',
        created_by=actor,
    )
    old = opportunity.stage
    opportunity.stage = 'converted'
    opportunity.stage_entered_at = timezone.now()
    opportunity.converted_project = project
    opportunity.converted_by = actor
    opportunity.converted_at = timezone.now()
    opportunity.save()
    handover.project = project
    handover.status = 'closed'
    handover.save(update_fields=['project', 'status', 'updated_at'])
    _audit(opportunity, actor, 'project_converted', from_stage=old, to_stage='converted', data={
        'project_id': project.id, 'project_code': project.code,
    })
    return opportunity, project, True


def _render_letter_template(template, context):
    """Render a Django template string with the given context."""
    from django.template import Template, Context
    from django.template.exceptions import TemplateSyntaxError
    try:
        tpl = Template(template)
        return tpl.render(Context(context))
    except TemplateSyntaxError as exc:
        raise ValidationError({'template': f'Template syntax error: {exc}'})


def _get_letter_context(opportunity, custom_data=None):
    """Build the rendering context for letter templates."""
    from .letter_context import build_letter_context
    return build_letter_context(opportunity, custom_data=custom_data)


def _letter_fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _stable_letter_context(context, preparation=False):
    context = dict(context)
    for key in ('current_date', 'today', 'logo_path', 'logo_uri', 'logo_data_uri', 'attachments', 'letterhead_manual_fields'):
        context.pop(key, None)
    head = dict(context.get('letterhead') or {})
    ignored = ('rev', 'confidential', 'confidential_date', 'our_ref') if preparation else ('rev', 'confidential')
    for key in ignored:
        head.pop(key, None)
    context['letterhead'] = head
    extraction = context.pop('letter_extraction', None)
    if preparation and isinstance(extraction, dict):
        source = extraction.get('source') or {}
        context['letter_extraction'] = {
            'source': {key: source[key] for key in ('upload_id', 'sha256', 'intake_id') if key in source},
            'fields': extraction.get('fields') or [],
        }
    return context


def _letter_content_fingerprint(letter):
    return _letter_fingerprint({
        'subject': letter.subject, 'body': letter.body,
        'context': _stable_letter_context(_get_letter_context(letter.opportunity, letter.custom_data)),
    })


@transaction.atomic
def prepare_letter(opportunity, actor, letter_type, custom_data=None):
    """
    Generate a letter after Go/No-Go decision.
    
    Rules:
    - EOI: Only when bid_decision in ('bid', 'conditional_bid')
    - Regret Expertise: Only when bid_decision == 'no_bid'
    - Regret Manpower: Only when bid_decision == 'no_bid'
    """
    opportunity = Deal.objects.select_for_update().get(pk=opportunity.pk)
    # Validate opportunity state
    if opportunity.stage not in {'proposal', 'no_bid'}:
        raise ValidationError({'stage': 'Letter preparation is only available after bid decision.'})
    
    # Validate letter type against bid decision
    bid_decision = opportunity.bid_decision
    
    if letter_type == 'eoi':
        if bid_decision not in {'bid', 'conditional_bid'}:
            raise ValidationError({'letter_type': 'EOI letter requires a Bid or Conditional Bid decision.'})
        if opportunity.stage != 'proposal':
            raise ValidationError({'stage': 'EOI letter requires opportunity in Proposal stage.'})
    elif letter_type in {'regret_expertise', 'regret_manpower'}:
        if bid_decision != 'no_bid':
            raise ValidationError({'letter_type': 'Regret letters require a No-Bid decision.'})
        if opportunity.stage != 'no_bid':
            raise ValidationError({'stage': 'Regret letters require opportunity in No-Bid stage.'})
    else:
        raise ValidationError({'letter_type': 'Invalid letter type.'})
    
    # Get active template
    try:
        template = SalesLetterTemplate.objects.get(letter_type=letter_type, is_active=True)
    except SalesLetterTemplate.DoesNotExist:
        raise ValidationError({'template': f'No active template found for {letter_type}.'})
    
    # Build context and render; persist the computed letterhead so the editor
    # pre-fills and later regenerations reuse edited values.
    from .letter_extraction import extract_opportunity_letter_fields, require_recipient
    has_overrides = bool(custom_data)
    custom_data = dict(custom_data or {})
    custom_data.pop('letter_extraction', None)
    extracted = extract_opportunity_letter_fields(opportunity, actor)
    source = extracted.pop('_source', None)
    if source:
        custom_data['letter_extraction'] = {'source': source, 'fields': list(extracted), 'values': dict(extracted)}
    overrides = custom_data.get('letterhead') or {}
    if overrides:
        custom_data['letterhead_manual_fields'] = sorted(
            key for key in overrides if key.startswith('recipient_') or key == 'your_ref')
    custom_data['letterhead'] = {**extracted, **(custom_data.get('letterhead') or {})}
    existing = opportunity.letters.filter(letter_type=letter_type).exclude(status='archived').first()
    # Validate before context defaults, reference allocation, reuse or file I/O.
    # A saved named recipient remains usable when reopening a reviewed letter.
    saved_head = ((existing.custom_data or {}).get('letterhead') or {}) if existing else {}
    require_recipient({**saved_head, **custom_data['letterhead']})
    if not extracted.get('recipient_name') and existing:
        custom_data['letterhead'] = {**saved_head, **custom_data['letterhead']}
    context = _get_letter_context(opportunity, custom_data)
    preparation_basis = {
        'template_id': template.pk, 'template_version': template.version,
        'subject_template': template.subject_template, 'body_template': template.body_template,
        'context': _stable_letter_context(context, preparation=True),
    }
    fingerprint = _letter_fingerprint(preparation_basis)
    existing = opportunity.letters.filter(letter_type=letter_type).exclude(status='archived').first()
    previous_extraction = ((existing.custom_data or {}).get('letter_extraction') or {}) if existing else {}
    if (existing and existing.status == 'draft' and not has_overrides
            and (existing.pdf_file or existing.docx_file or existing.preparation_fingerprint)):
        # Reconcile automatic source facts before cache reuse without replacing
        # reviewed wording, explicit recipient overrides or intentional clears.
        saved_data = dict(existing.custom_data or {})
        saved_head = dict(saved_data.get('letterhead') or {})
        manual_values = saved_data.get('letterhead_manual_fields') or []
        manual = {value for value in manual_values if isinstance(value, str)} if isinstance(manual_values, list) else set()
        previously_detected = (previous_extraction.get('fields') or []) if isinstance(previous_extraction, dict) else []
        previous_values = (previous_extraction.get('values') or {}) if isinstance(previous_extraction, dict) else {}
        for key, value in previous_values.items():
            if saved_head.get(key) != value:
                manual.add(key)
        same_contact = all(
            not str(saved_head.get(key) or '').strip() or not extracted.get(key)
            or (key in previously_detected and key not in manual)
            or str(saved_head[key]).strip().casefold() == str(extracted[key]).strip().casefold()
            for key in ('recipient_name', 'recipient_email')
        )
        missing = {}
        if source and same_contact:
            for key in ('recipient_name', 'recipient_title', 'recipient_company',
                        'recipient_email', 'recipient_address', 'your_ref'):
                if key not in manual and (key in previously_detected or not str(saved_head.get(key) or '').strip()):
                    value = extracted.get(key, '')
                    if saved_head.get(key, '') != value:
                        missing[key] = value
        if (extracted.get('recipient_company') and 'recipient_company' not in manual
                and 'recipient_company' not in previously_detected and opportunity.client
                and saved_head.get('recipient_company') == opportunity.client.company_name
                and saved_head.get('recipient_company') != extracted['recipient_company']):
            # This is the known client-master fallback, not a previously
            # extracted company. Use the explicit inviting company in the file.
            missing['recipient_company'] = extracted['recipient_company']
        if same_contact and missing:
            saved_head.update(missing)
            saved_data['letterhead'] = saved_head
            if source:
                saved_data['letter_extraction'] = {
                    'source': source, 'fields': list(extracted), 'values': dict(extracted),
                    'retained_fields': sorted(key for key in manual if key in extracted),
                }
            existing.custom_data = saved_data
            existing.preparation_fingerprint = fingerprint
            return refresh_letter_files(existing, actor, automatic_fields=tuple(missing))
        if source:
            saved_data['letter_extraction'] = {
                'source': source, 'fields': list(extracted), 'values': dict(extracted),
                'retained_fields': sorted(key for key in extracted if saved_head.get(key, '') != extracted[key]),
            }
            if saved_data != existing.custom_data:
                existing.custom_data = saved_data
                existing.save(update_fields=['custom_data'])
            if not same_contact:
                existing.preparation_fingerprint = fingerprint
                existing.save(update_fields=['preparation_fingerprint'])
                return existing
    if existing and (existing.pdf_file or existing.docx_file or existing.preparation_fingerprint):
        require_recipient((existing.custom_data or {}).get('letterhead') or {})
    if existing and existing.preparation_fingerprint == fingerprint:
        current_render = _letter_content_fingerprint(existing)
        if existing.pdf_file and existing.docx_file and existing.rendered_fingerprint != current_render:
            existing.rendered_fingerprint = current_render
            existing.save(update_fields=['rendered_fingerprint'])
        return existing
    if existing and existing.preparation_fingerprint:
        # A re-upload can change source IDs/hashes without changing any letter
        # fields. Compare with the saved provenance before deciding to render;
        # keep the original file's provenance and any reviewed manual edits.
        previous_source_context = dict(preparation_basis['context'])
        previous_source_context.pop('letter_extraction', None)
        if previous_extraction:
            previous_source_context['letter_extraction'] = previous_extraction
        if existing.preparation_fingerprint == _letter_fingerprint({
            **preparation_basis, 'context': _stable_letter_context(previous_source_context, preparation=True),
        }):
            existing.preparation_fingerprint = fingerprint
            updated_fields = ['preparation_fingerprint']
            current_render = _letter_content_fingerprint(existing)
            if existing.pdf_file and existing.docx_file and existing.rendered_fingerprint != current_render:
                existing.rendered_fingerprint = current_render
                updated_fields.append('rendered_fingerprint')
            existing.save(update_fields=updated_fields)
            return existing
    # Adopt historical letters on first reopen without overwriting reviewed text.
    if (existing and not existing.preparation_fingerprint and not has_overrides
            and existing.pdf_file and existing.docx_file):
        existing.preparation_fingerprint = fingerprint
        existing.save(update_fields=['preparation_fingerprint'])
        return existing
    stored_custom_data = dict(custom_data or {})
    stored_custom_data['letterhead'] = context['letterhead']
    subject = _render_letter_template(template.subject_template, context)
    body = _render_letter_template(template.body_template, context)

    if existing and existing.status == 'draft':
        old_head = (existing.custom_data or {}).get('letterhead') or {}
        manual = (existing.custom_data or {}).get('letterhead_manual_fields') or []
        for key in ('our_ref', 'confidential_date', 'rev', 'confidential', *manual):
            if key in old_head:
                stored_custom_data['letterhead'][key] = old_head[key]
        existing.subject, existing.body = subject, body
        existing.custom_data = {**existing.custom_data, **stored_custom_data}
        existing.template = template
        existing.preparation_fingerprint = fingerprint
        return refresh_letter_files(existing, actor, automatic_fields=tuple(extracted))

    # Create letter record
    letter = SalesLetter.objects.create(
        opportunity=opportunity,
        letter_type=letter_type,
        template=template,
        subject=subject,
        body=body,
        generated_by=actor,
        custom_data=stored_custom_data,
        preparation_fingerprint=fingerprint,
    )

    # Generate PDF
    try:
        from .letter_pdf import save_letter_pdf
        save_letter_pdf(letter)
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to generate PDF for letter {letter.id}: {e}")
    # Generate DOCX
    try:
        from .letter_docx import save_letter_docx
        save_letter_docx(letter)
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to generate DOCX for letter {letter.id}: {e}")
    if letter.pdf_file and letter.docx_file:
        letter.rendered_fingerprint = _letter_content_fingerprint(letter)
        letter.save(update_fields=['rendered_fingerprint'])
    _audit(
        opportunity,
        actor,
        'letter_generated',
        reason=f'Generated {letter.get_letter_type_display()} letter',
        data={
            'letter_id': str(letter.id),
            'letter_type': letter_type,
            'template_version': template.version,
            'custom_data_keys': list(stored_custom_data.keys()),
        },
    )

    return letter


@transaction.atomic
def refresh_letter_files(letter, actor, custom_data=None, *, automatic_fields=()):
    """Regenerate PDF+DOCX for an edited letter and bump the revision marker.

    Correspondence-folder attach is performed by the caller after this
    transaction commits.
    """
    Deal.objects.select_for_update().get(pk=letter.opportunity_id)
    stored = SalesLetter.objects.select_for_update().get(pk=letter.pk)
    if custom_data is not None:
        letter.custom_data = custom_data
    data = dict(letter.custom_data or {})
    if not automatic_fields and 'letter_extraction' in (stored.custom_data or {}):
        data['letter_extraction'] = stored.custom_data['letter_extraction']
    # Attachment outcomes and revision markers are server-owned, not editor input.
    if 'attachments' in (stored.custom_data or {}):
        data['attachments'] = stored.custom_data['attachments']
    else:
        data.pop('attachments', None)
    head = {**((stored.custom_data or {}).get('letterhead') or {}), **(data.get('letterhead') or {})}
    from .letter_extraction import require_recipient
    require_recipient(head)
    manual_values = (stored.custom_data or {}).get('letterhead_manual_fields') or []
    manual = {value for value in manual_values if isinstance(value, str)} if isinstance(manual_values, list) else set()
    for key in ('recipient_name', 'recipient_title', 'recipient_company', 'recipient_email', 'recipient_address', 'your_ref'):
        if key in head and head.get(key) != ((stored.custom_data or {}).get('letterhead') or {}).get(key) and key not in automatic_fields:
            manual.add(key)
    if manual:
        data['letterhead_manual_fields'] = sorted(manual)
    else:
        data.pop('letterhead_manual_fields', None)
    head['rev'] = ((stored.custom_data or {}).get('letterhead') or {}).get('rev', 0)
    if 'confidential' in ((stored.custom_data or {}).get('letterhead') or {}):
        head['confidential'] = stored.custom_data['letterhead']['confidential']
    data['letterhead'] = head
    letter.custom_data = data
    fingerprint = _letter_content_fingerprint(letter)
    previous = stored.rendered_fingerprint or _letter_content_fingerprint(stored)
    legacy_context = _stable_letter_context(_get_letter_context(stored.opportunity, stored.custom_data))
    if 'letter_extraction' in (stored.custom_data or {}):
        legacy_context['letter_extraction'] = stored.custom_data['letter_extraction']
    legacy = _letter_fingerprint({'subject': stored.subject, 'body': stored.body, 'context': legacy_context})
    unchanged = fingerprint == previous or (
        previous == legacy and fingerprint == _letter_content_fingerprint(stored))
    if stored.pdf_file and stored.docx_file and unchanged:
        if data != stored.custom_data:
            stored.custom_data = data
            stored.save(update_fields=['custom_data'])
        if stored.preparation_fingerprint != letter.preparation_fingerprint:
            stored.preparation_fingerprint = letter.preparation_fingerprint
            stored.save(update_fields=['preparation_fingerprint'])
        return stored
    letterhead = dict((letter.custom_data or {}).get('letterhead') or {})
    letterhead['rev'] = int(letterhead.get('rev') or 0) + 1
    letterhead['confidential'] = f"{letterhead.get('confidential_date', '')} / Rev {letterhead['rev']}"
    letter.custom_data = {**(letter.custom_data or {}), 'letterhead': letterhead}
    letter.save(update_fields=['subject', 'body', 'custom_data', 'template',
                              'preparation_fingerprint', 'updated_at'])

    from .letter_docx import regenerate_letter_docx
    from .letter_pdf import regenerate_letter_pdf
    regenerate_letter_pdf(letter)
    regenerate_letter_docx(letter)
    letter.rendered_fingerprint = fingerprint
    letter.save(update_fields=['rendered_fingerprint'])

    _audit(
        letter.opportunity,
        actor,
        'letter_pdf_regenerated',
        reason=f'Regenerated files for {letter.get_letter_type_display()} letter',
        data={'letter_id': str(letter.id), 'rev': letterhead['rev']},
    )
    return letter
