"""Governed Sales opportunity commands and Project Control handover."""

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.core.project_models import Project, ProjectMember
from apps.project_control.models import Estimate

from .models import Deal, OpportunityAuditEvent, ProjectHandover


HANDOVER_REQUIRED_ITEMS = (
    'authorization_to_proceed', 'final_scope', 'client_governance',
    'contract_value', 'approved_hours_budget', 'project_dates',
    'billing_milestones', 'payment_terms', 'risks', 'project_manager',
)


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


def submit_qualification(opportunity, actor):
    if opportunity.stage != 'lead':
        raise ValidationError({'stage': 'Only a lead can be submitted for qualification.'})
    missing = []
    for value, field in [
        (opportunity.scope_type, 'scope_type'),
        (opportunity.submission_due_date, 'submission_due_date'),
        (opportunity.expected_close_date, 'expected_close_date'),
        (opportunity.estimated_value, 'estimated_value'),
        (opportunity.owner_id, 'owner'),
    ]:
        _require(value, field, missing)
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
    _audit(opportunity, actor, 'qualification_submitted', from_stage=old, to_stage=opportunity.stage)
    return opportunity


def record_bid_decision(opportunity, actor, decision, reason=''):
    if opportunity.stage != 'qualified':
        raise ValidationError({'stage': 'Bid decision is available only for a qualified opportunity.'})
    if decision not in {'bid', 'conditional_bid', 'no_bid'}:
        raise ValidationError({'decision': 'Use bid, conditional_bid, or no_bid.'})
    if decision in {'conditional_bid', 'no_bid'} and not reason:
        raise ValidationError({'reason': 'A justification is required for this decision.'})
    approval_value = Decimal(str(getattr(settings, 'SALES_MANAGEMENT_APPROVAL_VALUE', 5000000)))
    requires_management = opportunity.risk_level in {'high', 'critical'} or opportunity.estimated_value >= approval_value
    if requires_management and actor.id == opportunity.owner_id:
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
        reason=reason, data={'decision': decision},
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
        manager = opportunity.nominated_project_manager or opportunity.owner
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


def decide_handover(handover, actor, *, accepted, comment=''):
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
    owner = opportunity.nominated_project_manager or opportunity.owner or actor
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
