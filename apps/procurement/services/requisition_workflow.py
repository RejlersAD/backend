"""Transactional state transitions for Purchase Requisitions."""

from decimal import Decimal
import re
from django.core.exceptions import MultipleObjectsReturned, ObjectDoesNotExist
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from ..models import PurchaseRequisition
from .requisition_status import canonicalize_pr_status
from .requisition_validation import line_items_total, normalize_line_items
from .employee_display import employee_display_name, normalize_ceo_workflow
from .notification_context import requisition_teams_context
from .approval_integrity import stage_signature_issue
from .approval_eligibility import MODULE_PR, eligible_stage_assignee


def notify_requisition_approver_changes(pr, previous_workflow):
    """Notify actionable assignments after an edit, once per recipient and level."""
    if not getattr(pr, 'pk', None):
        return
    if canonicalize_pr_status(pr.status) not in RequisitionWorkflowService.ACTIVE_REVIEW_STATUSES:
        return
    try:
        workflow = RequisitionWorkflowService._workflow(pr)
        level, active_stages = RequisitionWorkflowService._active_level_stages(pr, workflow)
    except ValidationError:
        return
    previous_level_stages = [
        stage for index, stage in enumerate(previous_workflow or [])
        if isinstance(stage, dict)
        and RequisitionWorkflowService._stage_level(stage, index) == level
    ]
    reassigned_recipient_ids = set()
    for _, stage in active_stages:
        recipient = RequisitionWorkflowService._resolve_stage_user(stage)
        if recipient and not any(
            RequisitionWorkflowService._stage_matches_user(previous_stage, recipient)
            for previous_stage in previous_level_stages
        ):
            reassigned_recipient_ids.add(recipient.pk)
    # Comparing only the changed assignments can miss an existing future
    # approver whose level became active after an edit. The common sender
    # selects the current level and deduplicates real approval requests.
    # A returning assignee still needs a new request after A -> B -> A;
    # bypass historical deduplication only for changed active recipients.
    RequisitionWorkflowService._notify_level(
        pr, workflow, level, reassigned_recipient_ids=reassigned_recipient_ids,
    )


class RequisitionWorkflowService:
    """Single source of truth for PR submission, approval, and rejection."""

    ACTIVE_REVIEW_STATUSES = {'submitted', 'in_review'}

    STAGE_CONFIG = {
        'pm': {
            'labels': ('level 1 approver', 'project manager', 'department manager', 'technical review'),
            'name_field': 'pm_name',
            'signature_field': 'pm_signature',
            'status_field': 'pm_approval_status',
            'timestamp_field': 'pm_approved_at',
        },
        'eng_manager': {
            'labels': ('engineering manager', 'engineering review'),
            'name_field': 'eng_manager_name',
            'signature_field': 'eng_manager_signature',
            'status_field': 'eng_manager_approval_status',
            'timestamp_field': 'eng_manager_approved_at',
        },
        'manager_projects': {
            'labels': ('manager of projects', 'projects manager'),
            'name_field': 'manager_projects_name',
            'signature_field': 'manager_projects_signature',
            'status_field': 'manager_projects_approval_status',
            'timestamp_field': 'manager_projects_approved_at',
        },
        'vp': {
            'labels': ('vp operations', 'vice president'),
            'name_field': 'vp_op_name',
            'signature_field': 'vp_op_signature',
            'status_field': 'vp_op_approval_status',
            'timestamp_field': 'vp_op_approved_at',
        },
    }

    @classmethod
    def _is_super_admin(cls, user):
        if getattr(user, 'is_superuser', False):
            return True

        try:
            return user.rbac_profile.roles.filter(
                code='super_admin',
                is_active=True,
            ).exists()
        except (AttributeError, ObjectDoesNotExist):
            return False

    @classmethod
    def _workflow(cls, pr, *, allow_empty=False):
        workflow = normalize_ceo_workflow(
            pr.approval_workflow_config,
            pr.po_number_reference,
            getattr(pr, 'po_applicable', None),
        )
        if not isinstance(workflow, list) or (not workflow and not allow_empty):
            raise ValidationError({'error': 'A configured approval workflow is required.'})
        if any(not isinstance(stage, dict) for stage in workflow):
            raise ValidationError({'error': 'The approval workflow contains an invalid stage.'})
        return workflow

    @classmethod
    def _stage_key(cls, stage):
        role_text = f"{stage.get('role', '')} {stage.get('stage', '')}".strip().lower()
        for stage_key, config in cls.STAGE_CONFIG.items():
            if any(label in role_text for label in config['labels']):
                return stage_key
        return None

    @classmethod
    def _current_stage(cls, pr, workflow):
        for index, stage in enumerate(workflow):
            stage_status = str(stage.get('status', 'pending')).lower()
            if stage_status in ('pending', 'in_review'):
                pr.current_approval_step = index
                return index, stage
        raise ValidationError({'error': 'No active approval stage awaiting action.'})

    @classmethod
    def _stage_level(cls, stage, index):
        try:
            explicit_level = stage.get('level')
            if explicit_level not in (None, ''):
                return max(0, int(explicit_level))
            # Compatibility for records created while the API was stripping
            # the explicit level field (for example "Level 1 - Approver 2").
            label = f"{stage.get('stage', '')} {stage.get('role', '')}"
            match = re.search(r'\blevel\s*(\d+)\b', label, re.IGNORECASE)
            if match:
                return max(0, int(match.group(1)))
            return index + 1
        except (TypeError, ValueError):
            return index + 1

    @classmethod
    def _active_level_stages(cls, pr, workflow):
        for stage in workflow:
            if str(stage.get('status', 'pending')).strip().lower() == 'approved':
                issue = stage_signature_issue(stage)
                if issue:
                    raise ValidationError({'error': issue})
        if any(
            str(stage.get('status', '')).strip().lower() in ('rejected', 'not_approved', 'declined')
            for stage in workflow
        ):
            raise ValidationError({'error': 'The approval workflow contains a rejected decision.'})
        unresolved = [
            (index, stage)
            for index, stage in enumerate(workflow)
            if str(stage.get('status', 'pending')).strip().lower() != 'approved'
        ]
        if not unresolved:
            raise ValidationError({'error': 'No active approval stage awaiting action.'})
        active_level = min(cls._stage_level(stage, index) for index, stage in unresolved)
        active = [
            (index, stage) for index, stage in unresolved
            if cls._stage_level(stage, index) == active_level
        ]
        if any(
            str(stage.get('status', 'pending')).strip().lower() not in ('pending', 'in_review')
            for _, stage in active
        ):
            raise ValidationError({
                'error': f'Resolve the missing or invalid Level {active_level} approval evidence before continuing.',
            })
        pr.current_approval_step = active[0][0]
        return active_level, active

    @classmethod
    def _actor_stage(cls, active_stages, actor, expected_stage_key=None):
        if not cls._actor_is_active(actor):
            raise PermissionDenied('Only an active employee may record an approval decision.')
        candidates = [
            entry for entry in active_stages
            if not expected_stage_key or cls._stage_key(entry[1]) == expected_stage_key
        ]
        for entry in candidates:
            if cls._stage_matches_user(entry[1], actor) and eligible_stage_assignee(actor, entry[1], MODULE_PR):
                return entry

        stage_name = active_stages[0][1].get('stage') or active_stages[0][1].get('role') or 'current approval level'
        if expected_stage_key and not candidates:
            raise ValidationError({'error': f'{stage_name} must be completed next.'})
        raise PermissionDenied(f'Only an assigned approver may act on {stage_name}.')

    @staticmethod
    def _stage_email(stage):
        email = str(
            stage.get('user_email')
            or stage.get('approver_email')
            or stage.get('email')
            or ''
        ).strip().lower()
        if email:
            return email
        username = str(stage.get('username') or '').strip().lower()
        return username if '@' in username else ''

    @classmethod
    def _actor_is_active(cls, actor):
        """Check account/profile flags without performing a separate user lookup."""
        if actor is None or not getattr(actor, 'is_active', True) or getattr(actor, 'is_deleted', False):
            return False
        try:
            profile = getattr(actor, 'rbac_profile', None)
        except ObjectDoesNotExist:
            profile = None
        return profile is None or (
            str(getattr(profile, 'status', 'active') or '').strip().lower() == 'active'
            and not getattr(profile, 'is_deleted', False)
        )

    @classmethod
    def _stage_matches_user(cls, stage, user):
        """Match migrated assignments by stable email before environment-specific IDs."""
        if not cls._actor_is_active(user):
            return False
        assigned_email = cls._stage_email(stage)
        user_email = str(getattr(user, 'email', '') or '').strip().lower()
        if assigned_email:
            return assigned_email == user_email
        assigned_id = stage.get('user_id') or stage.get('approver_id')
        return bool(assigned_id) and str(assigned_id) == str(user.id)

    @classmethod
    def _resolve_stage_user(cls, stage):
        User = get_user_model()
        assigned_email = cls._stage_email(stage)
        assigned_id = stage.get('user_id') or stage.get('approver_id')
        try:
            if assigned_email:
                # Email is authoritative, but a case-insensitive collision
                # must never select an arbitrary employee.
                recipient = User.objects.get(email__iexact=assigned_email, is_active=True)
            elif assigned_id:
                recipient = User.objects.get(pk=assigned_id, is_active=True)
            else:
                return None
        except (ObjectDoesNotExist, MultipleObjectsReturned, ValueError, TypeError):
            return None
        if not cls._actor_is_active(recipient) or not eligible_stage_assignee(recipient, stage, MODULE_PR):
            return None
        if assigned_email:
            stage['user_id'] = str(recipient.pk)
        return recipient

    @classmethod
    def _stage_assignment_issue(cls, stage):
        """Explain a rejected assignment without repairing identity or access.

        The resolver remains authoritative. This diagnostic is used only after
        it rejects a stage, and follows its email-first account lookup.
        """
        from apps.hr_core.models import EmployeeMaster
        from apps.rbac.action_policy import record_workflow_not_denied
        from apps.rbac.approval_eligibility import approval_access
        from .approval_eligibility import (
            is_employee_selected_pr_stage, position_matches_stage, stage_positions,
        )

        assigned_email = cls._stage_email(stage)
        assigned_id = stage.get('user_id') or stage.get('approver_id')
        if not assigned_email and not assigned_id:
            return 'missing_assignment', 'no employee account is assigned.'
        lookup = {'email__iexact': assigned_email} if assigned_email else {'pk': assigned_id}
        users = get_user_model().objects
        try:
            recipient = users.select_related('rbac_profile').get(**lookup, is_active=True)
        except MultipleObjectsReturned:
            return 'ambiguous_account', 'the assigned email matches multiple active accounts; resolve the duplicate accounts.'
        except ObjectDoesNotExist:
            if users.filter(**lookup).exists():
                return 'inactive_account', 'the assigned employee account is inactive.'
            return 'missing_account', 'the assigned employee account could not be found.'
        except (ValueError, TypeError):
            return 'missing_account', 'the assigned employee account identifier is invalid.'

        if not cls._actor_is_active(recipient):
            return 'inactive_account', 'the assigned employee account or access profile is inactive or deleted.'
        profile = getattr(recipient, 'rbac_profile', None)
        if profile is None:
            return 'missing_access_profile', 'the assigned employee has no access profile for purchase requisition approval.'
        if profile.locked_until and profile.locked_until > timezone.now():
            return 'locked_account', 'the assigned employee account is temporarily locked.'

        employee = EmployeeMaster.objects.filter(user_id=recipient.pk).first()
        if employee is not None and employee.employment_status not in ('active', 'probation', 'notice_period'):
            return 'inactive_employee', 'the assigned employee does not have an active employment status.'
        selected_employee = is_employee_selected_pr_stage(stage)
        permitted = (
            record_workflow_not_denied(recipient, MODULE_PR, 'approve')
            if selected_employee else approval_access(recipient, MODULE_PR)
        )
        if not permitted:
            return 'missing_approval_permission', 'the assigned employee does not have permission to approve this purchase requisition.'
        if not selected_employee:
            if employee is None:
                return 'missing_employee_record', 'the assigned account has no official HR employee record to verify its approval position.'
            if not stage_positions(stage):
                return 'unrecognized_business_position', 'this approval stage has no recognized business position; review its configuration.'
            if not position_matches_stage(recipient, stage):
                return 'business_position_mismatch', 'the assigned employee\'s official HR position does not match this approval stage.'
        return 'assignment_changed', 'the assignment or approval eligibility changed; refresh and review the assigned employee.'

    @classmethod
    def _notify_level(
        cls, pr, workflow, level, force=False, previous_approver='', previous_level=None,
        reassigned_recipient_ids=None,
    ):
        if not getattr(pr, 'pk', None):
            return
        current_status = canonicalize_pr_status(pr.status)
        if not cls._is_awaiting_approval(pr, workflow):
            return
        evidence_recovery = current_status == 'converted'
        try:
            active_level, active_stages = cls._active_level_stages(pr, workflow)
        except ValidationError:
            return
        if level != active_level:
            return
        recipients = {}
        assignment_ids = {}
        for _, stage in active_stages:
            if evidence_recovery and not stage.get('evidence_requested_at'):
                continue
            recipient = cls._resolve_stage_user(stage)
            if recipient:
                recipients[recipient.pk] = recipient
                assignment_ids[recipient.pk] = str(stage.get('assignment_id') or '')
        if not recipients:
            return
        recipient_ids = set(recipients)
        reassigned_recipient_ids = frozenset(reassigned_recipient_ids or ())
        pr_id = pr.pk
        pr_number = pr.pr_number

        def send_notifications():
            from apps.notifications.models import Notification
            from apps.notifications.services import NotificationService
            # Decisions and assignment edits can commit before this callback
            # runs. Never send an alert from an obsolete in-memory route.
            try:
                pr.refresh_from_db()
                current_workflow = cls._workflow(pr)
                if not cls._is_awaiting_approval(pr, current_workflow):
                    return
                current_level, current_stages = cls._active_level_stages(pr, current_workflow)
            except (ObjectDoesNotExist, ValidationError):
                return
            if current_level != level:
                return
            current_recipients = {}
            for _, stage in current_stages:
                if canonicalize_pr_status(pr.status) == 'converted' and not stage.get('evidence_requested_at'):
                    continue
                recipient = cls._resolve_stage_user(stage)
                if (
                    recipient is not None and recipient.pk in recipient_ids
                    and str(stage.get('assignment_id') or '') == assignment_ids[recipient.pk]
                ):
                    current_recipients[recipient.pk] = recipient
            if not current_recipients:
                return
            teams_context = requisition_teams_context(pr, approval_level=level)
            already_notified_ids = set()
            existing_requests = Notification.objects.filter(
                Q(metadata__event_type='approval_assignment')
                | Q(metadata__requires_action=True),
                recipient_id__in=set(current_recipients),
                metadata__pr_id=str(pr_id),
                metadata__approval_level=level,
            ).exclude(
                # A PO may refer to this PR without being a PR approval alert.
                # Checking key existence also retains untyped legacy rows.
                metadata__has_key='entity_type', metadata__entity_type='purchase_order',
            )
            if not force:
                already_notified_ids = set(existing_requests.values_list('recipient_id', flat=True))
            for recipient_id, recipient in current_recipients.items():
                assignment_id = assignment_ids[recipient_id]
                if assignment_id and not force:
                    already_notified = existing_requests.filter(
                        recipient_id=recipient_id, metadata__assignment_id=assignment_id,
                    ).exists()
                else:
                    already_notified = recipient_id in already_notified_ids and recipient_id not in reassigned_recipient_ids
                if already_notified:
                    continue
                NotificationService.create_notification(
                    recipient=recipient,
                    title=(
                        f'Purchase Recommendation {pr_number} approval evidence requested again'
                        if force else f'Purchase Recommendation {pr_number} requires your approval'
                    ),
                    message=(
                        f'Please review and record your Level {level} decision for converted '
                        f'Purchase Recommendation {pr_number}.'
                        if force else
                        (
                            f'Level {previous_level} ({previous_approver}) is approved. Purchase Recommendation '
                            f'{pr_number} is now waiting for your Level {level} decision.'
                            if previous_approver else
                            f'Purchase Recommendation {pr_number} is waiting for your Level {level} decision.'
                        )
                    ),
                    category='APPROVAL',
                    priority='HIGH',
                    action_url=f'/procurement/requisitions/{pr_id}',
                    action_label='Open Request',
                    send_teams=True,
                    teams_context=teams_context,
                    metadata={
                        'entity_type': 'purchase_recommendation',
                        'entity_id': str(pr_id),
                        'request_number': pr_number,
                        'pr_id': str(pr_id),
                        'pr_number': pr_number,
                        'event_type': 'approval_assignment',
                        'approval_level': level,
                        'assignment_id': assignment_id,
                        'approval_evidence_resend': force,
                        'assignment_updated': recipient_id in reassigned_recipient_ids,
                        'requires_action': True,
                    },
                )

        transaction.on_commit(send_notifications)

    @classmethod
    def _enforce_assigned_approver(cls, stage, actor):
        stage_name = stage.get('stage') or stage.get('role') or 'current approval stage'

        if not (stage.get('user_id') or stage.get('approver_id') or cls._stage_email(stage)):
            raise PermissionDenied(f'No approver is assigned to {stage_name}.')
        if not cls._stage_matches_user(stage, actor):
            raise PermissionDenied(f'Only the assigned approver may act on {stage_name}.')

    @classmethod
    def _is_awaiting_approval(cls, pr, workflow):
        status = canonicalize_pr_status(pr.status)
        return status in cls.ACTIVE_REVIEW_STATUSES or (
            status == 'converted' and any(
                str(stage.get('status', 'pending')).strip().lower() in ('pending', 'in_review')
                and bool(stage.get('evidence_requested_at'))
                for stage in workflow
            )
        )

    @classmethod
    def can_approve(cls, pr, actor):
        """Use the decision service's assignment and sequence checks for the UI."""
        if not cls._actor_is_active(actor):
            return False
        current_step = getattr(pr, 'current_approval_step', 0)
        try:
            workflow = cls._workflow(pr)
            if not cls._is_awaiting_approval(pr, workflow):
                return False
            _, active_stages = cls._active_level_stages(pr, workflow)
            _, stage = cls._actor_stage(active_stages, actor)
            return canonicalize_pr_status(pr.status) != 'converted' or bool(stage.get('evidence_requested_at'))
        except (ValidationError, PermissionDenied):
            return False
        finally:
            pr.current_approval_step = current_step

    @classmethod
    def _enforce_expected_stage(cls, stage, expected_stage_key):
        if expected_stage_key and cls._stage_key(stage) != expected_stage_key:
            stage_name = stage.get('stage') or stage.get('role') or 'the current stage'
            raise ValidationError({'error': f'{stage_name} must be completed next.'})

    @classmethod
    def _mirror_fixed_approval(cls, pr, stage, actor, signature, approved_at):
        stage_key = cls._stage_key(stage)
        if not stage_key:
            return

        config = cls.STAGE_CONFIG[stage_key]
        setattr(pr, config['name_field'], actor)
        setattr(pr, config['signature_field'], signature)
        setattr(pr, config['status_field'], 'approved')
        setattr(pr, config['timestamp_field'], approved_at)

    @classmethod
    def _mirror_fixed_rejection(cls, pr, stage):
        stage_key = cls._stage_key(stage)
        if stage_key:
            setattr(pr, cls.STAGE_CONFIG[stage_key]['status_field'], 'not_approved')

    @classmethod
    @transaction.atomic
    def submit(cls, pr_id, actor):
        pr = get_object_or_404(PurchaseRequisition.objects.select_for_update(), pk=pr_id)
        return cls._submit_locked(pr, actor)

    @classmethod
    def _submit_locked(cls, pr, actor):
        if str(pr.issued_by_id) != str(actor.id) and not cls._is_super_admin(actor):
            raise PermissionDenied('Only the requisition issuer may submit this draft.')
        
        current_status = canonicalize_pr_status(pr.status)
        if current_status in cls.ACTIVE_REVIEW_STATUSES:
            return pr
        if current_status != 'draft':
            raise ValidationError({'error': 'Only draft requisitions can be submitted.'})

        normalized_items = normalize_line_items(
            pr.items, (getattr(pr, 'price_remarks_data', None) or {}).get('line_details'),
        )
        calculated_total = line_items_total(normalized_items) if normalized_items else None
        if calculated_total is not None:
            calculated_total = calculated_total.quantize(Decimal('0.01'))
            from .procurement_vat import CONFIRMED_BASES, confirmed_totals
            if getattr(pr, 'vat_basis', 'unconfirmed') in CONFIRMED_BASES:
                calculated_total = confirmed_totals(calculated_total, pr.vat_basis,
                    (pr.price_remarks_data or {}).get('discount_amount', 0))['total_amount']
            pr_total = Decimal(str(pr.total_price or 0)).quantize(Decimal('0.01'))
            
            if pr_total != calculated_total:
                raise ValidationError({
                    'error': f'Total price ({pr_total}) must equal the sum of line items ({calculated_total}) before submission.'
                })
        pr.items = normalized_items

        # Registration is permitted with advisory business/assignment warnings.
        # Actual approval decisions still check identity, access, and sequence.
        workflow = cls._workflow(pr, allow_empty=True)

        # Discard any client-supplied approval state before starting review.
        for index, stage in enumerate(workflow):
            stage['step'] = index + 1
            stage['status'] = 'pending'
            stage['approved_at'] = None
            stage.pop('approved_by_id', None)
            stage.pop('approved_by_name', None)
            stage.pop('approved_by_email', None)
            stage.pop('signature', None)
            stage.pop('signature_user_id', None)
            stage.pop('signature_user_email', None)
            stage.pop('rejected_at', None)
            stage.pop('rejected_by_id', None)
            stage.pop('rejected_by_name', None)
            stage.pop('rejection_reason', None)

        pr.approval_workflow_config = workflow
        first_level = None
        if workflow:
            first_level, _ = cls._active_level_stages(pr, workflow)
        else:
            pr.current_approval_step = 0
        pr.status = 'submitted'
        pr.rejection_reason = ''
        pr.save()
        if first_level is not None:
            cls._notify_level(pr, workflow, first_level)
        return pr

    @classmethod
    @transaction.atomic
    def approve(cls, pr_id, actor, signature='', expected_stage_key=None, require_signature=False):
        pr = get_object_or_404(PurchaseRequisition.objects.select_for_update(), pk=pr_id)
        return cls._approve_locked(pr, actor, signature, expected_stage_key, require_signature)

    @classmethod
    def _approve_locked(cls, pr, actor, signature='', expected_stage_key=None, require_signature=False):
        workflow = cls._workflow(pr)
        current_status = canonicalize_pr_status(pr.status)
        if not cls._is_awaiting_approval(pr, workflow):
            raise ValidationError({'error': 'This requisition is not awaiting approval.'})
        evidence_recovery = current_status == 'converted'
        active_level, active_stages = cls._active_level_stages(pr, workflow)
        current_index, stage = cls._actor_stage(active_stages, actor, expected_stage_key)
        if evidence_recovery and not stage.get('evidence_requested_at'):
            raise ValidationError({'error': 'Approval evidence has not been requested for this stage.'})

        # The authenticated assignee's saved signature is the only signing
        # source. Retain the argument for older internal callers, but never
        # trust client-supplied image bytes as another employee's signature.
        try:
            profile = getattr(actor, 'rbac_profile', None)
        except ObjectDoesNotExist:
            profile = None
        signature = getattr(profile, 'signature_image', '') or ''
        if require_signature and not signature:
            raise ValidationError({
                'error': 'Add your signature in Profile > My Signature before approving.'
            })

        approved_at = timezone.now()
        actor_name = employee_display_name(actor)
        stage['status'] = 'approved'
        stage['approved_at'] = approved_at.isoformat()
        stage['approved_by_id'] = str(actor.id)
        stage['approved_by_name'] = actor_name
        stage['approved_by_email'] = str(getattr(actor, 'email', '') or '').strip().lower()
        stage['signature'] = signature
        stage['signature_user_id'] = str(actor.id)
        stage['signature_user_email'] = stage['approved_by_email']
        cls._mirror_fixed_approval(pr, stage, actor, signature or '', approved_at)

        remaining_current_level = [
            (index, candidate) for index, candidate in enumerate(workflow)
            if cls._stage_level(candidate, index) == active_level
            and str(candidate.get('status', 'pending')).lower() in ('pending', 'in_review')
        ]
        unresolved = [
            (index, candidate) for index, candidate in enumerate(workflow)
            if str(candidate.get('status', 'pending')).strip().lower() != 'approved'
        ]

        notify_next = None
        if not unresolved:
            pr.current_approval_step = len(workflow)
            pr.status = 'converted' if evidence_recovery else 'approved'
            pr.approved_by = actor
            pr.approved_at = approved_at
            orders = getattr(pr, 'purchase_orders', None)
            if not evidence_recovery and orders is not None:
                # Native PO association keeps the PR in review until its own
                # last decision. The PR is already locked; do not invert the
                # PR-before-PO lock order merely to read the current link.
                linked_order = orders.order_by('-created_at', '-pk').first()
                if linked_order:
                    from .procurement_lifecycle import PREVIOUS_STATUS
                    pr.price_remarks_data = dict(pr.price_remarks_data or {})
                    pr.price_remarks_data[PREVIOUS_STATUS] = 'approved'
                    pr.po_number_reference = linked_order.po_number
                    pr.status = 'converted'
        else:
            next_index, next_stage = min(
                remaining_current_level or unresolved,
                key=lambda entry: cls._stage_level(entry[1], entry[0]),
            )
            pr.current_approval_step = next_index
            pr.status = 'converted' if evidence_recovery else 'in_review'
            next_level = cls._stage_level(next_stage, next_index)
            if not remaining_current_level and next_level != active_level:
                if evidence_recovery:
                    notify_next = {'force': True}
                else:
                    notify_next = {'previous_approver': actor_name, 'previous_level': active_level}

        pr.approval_workflow_config = workflow
        pr.save()
        if notify_next is not None:
            cls._notify_level(pr, workflow, next_level, **notify_next)
        return pr

    @classmethod
    @transaction.atomic
    def reject(cls, pr_id, actor, reason, expected_stage_key=None):
        pr = get_object_or_404(PurchaseRequisition.objects.select_for_update(), pk=pr_id)
        return cls._reject_locked(pr, actor, reason, expected_stage_key)

    @classmethod
    def _reject_locked(cls, pr, actor, reason, expected_stage_key=None):
        workflow = cls._workflow(pr)
        current_status = canonicalize_pr_status(pr.status)
        if not cls._is_awaiting_approval(pr, workflow):
            raise ValidationError({'error': 'This requisition is not awaiting approval.'})
        evidence_recovery = current_status == 'converted'

        trimmed_reason = str(reason or '').strip()
        if len(trimmed_reason) < 10:
            raise ValidationError({'error': 'Rejection reason must be at least 10 characters long.'})
        if len(trimmed_reason) > 1000:
            raise ValidationError({'error': 'Rejection reason cannot exceed 1000 characters.'})

        _, active_stages = cls._active_level_stages(pr, workflow)
        _, stage = cls._actor_stage(active_stages, actor, expected_stage_key)
        if evidence_recovery and not stage.get('evidence_requested_at'):
            raise ValidationError({'error': 'Approval evidence has not been requested for this stage.'})

        rejected_at = timezone.now()
        actor_name = employee_display_name(actor)
        stage['status'] = 'rejected'
        stage['rejected_at'] = rejected_at.isoformat()
        stage['rejected_by_id'] = str(actor.id)
        stage['rejected_by_name'] = actor_name
        stage['rejection_reason'] = trimmed_reason
        cls._mirror_fixed_rejection(pr, stage)

        pr.approval_workflow_config = workflow
        pr.status = 'converted' if evidence_recovery else 'rejected'
        pr.rejection_reason = trimmed_reason
        if evidence_recovery:
            # A retrospective rejection must not invalidate or silently delete
            # the linked PO. Stop the recovery queue and preserve the decision.
            for candidate in workflow:
                if str(candidate.get('status', 'pending')).lower() in ('pending', 'in_review'):
                    candidate.pop('evidence_requested_at', None)
                    candidate.pop('evidence_requested_by_id', None)
                    candidate.pop('evidence_requested_by_name', None)
        pr.save()
        return pr

    @classmethod
    @transaction.atomic
    def resend_missing_approvals(cls, pr_id, actor):
        """Reactivate missing audit decisions without changing a converted PR."""
        pr = get_object_or_404(PurchaseRequisition.objects.select_for_update(), pk=pr_id)
        return cls._resend_missing_approvals_locked(pr, actor)

    @classmethod
    def _resend_missing_approvals_locked(cls, pr, actor):
        if canonicalize_pr_status(pr.status) != 'converted':
            raise ValidationError({'error': 'Approval recovery is only available for converted requisitions.'})

        workflow = cls._workflow(pr)
        if any(
            str(stage.get('status', '')).strip().lower() in ('rejected', 'not_approved', 'declined')
            for stage in workflow
        ):
            raise ValidationError({
                'error': 'Approval recovery cannot continue while the workflow contains a rejected decision.'
            })
        unresolved = [
            (index, stage)
            for index, stage in enumerate(workflow)
            if str(stage.get('status', 'pending')).strip().lower()
            in ('pending', 'in_review', 'not_recorded')
        ]
        if not unresolved:
            raise ValidationError({'error': 'This requisition has no missing approval decisions.'})

        assignment_errors = []
        for index, stage in unresolved:
            if cls._resolve_stage_user(stage) is None:
                code, reason = cls._stage_assignment_issue(stage)
                assignment_errors.append({
                    'stage': stage.get('role') or stage.get('stage') or f'Stage {index + 1}',
                    'code': code,
                    'reason': reason,
                })
        if assignment_errors:
            details = ' '.join(f"{issue['stage']}: {issue['reason']}" for issue in assignment_errors)
            raise ValidationError({
                'error': f'Cannot resend approvals. {details}',
                'approval_assignment_errors': assignment_errors,
            })

        requested_at = timezone.now().isoformat()
        requested_by_name = employee_display_name(actor)
        for _, stage in unresolved:
            stage['status'] = 'pending'
            stage['evidence_requested_at'] = requested_at
            stage['evidence_requested_by_id'] = str(actor.id)
            stage['evidence_requested_by_name'] = requested_by_name

        first_level = min(cls._stage_level(stage, index) for index, stage in unresolved)
        pr.approval_workflow_config = workflow
        pr.current_approval_step = next(
            index for index, stage in unresolved
            if cls._stage_level(stage, index) == first_level
        )
        pr.save(update_fields=['approval_workflow_config', 'current_approval_step', 'updated_at'])
        cls._notify_level(pr, workflow, first_level, force=True)
        return pr, len(unresolved)
