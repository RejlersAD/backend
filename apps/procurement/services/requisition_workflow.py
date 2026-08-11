"""Transactional state transitions for Purchase Requisitions."""

from decimal import Decimal
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from ..models import PurchaseRequisition
from .requisition_status import canonicalize_pr_status
from .requisition_validation import line_items_total, normalize_line_items


class RequisitionWorkflowService:
    """Single source of truth for PR submission, approval, and rejection."""

    ACTIVE_REVIEW_STATUSES = {'submitted', 'in_review'}

    STAGE_CONFIG = {
        'pm': {
            'labels': ('project manager', 'technical review'),
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
            'labels': ('vp operations', 'vice president', 'procurement manager'),
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
    def _workflow(cls, pr):
        workflow = pr.approval_workflow_config
        if not isinstance(workflow, list) or not workflow:
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
    def _enforce_assigned_approver(cls, stage, actor):
        if cls._is_super_admin(actor):
            return

        assigned_user_id = stage.get('user_id') or stage.get('approver_id')
        stage_name = stage.get('stage') or stage.get('role') or 'current approval stage'

        if not assigned_user_id:
            raise PermissionDenied(f'No approver is assigned to {stage_name}.')
        if str(assigned_user_id) != str(actor.id):
            raise PermissionDenied(f'Only the assigned approver may act on {stage_name}.')

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

        normalized_items = normalize_line_items(pr.items)
        if normalized_items:
            calculated_total = Decimal(str(line_items_total(normalized_items) or 0)).quantize(Decimal('0.01'))
            pr_total = Decimal(str(pr.total_price or 0)).quantize(Decimal('0.01'))
            
            if pr_total != calculated_total:
                raise ValidationError({
                    'error': f'Total price ({pr_total}) must equal the sum of line items ({calculated_total}) before submission.'
                })
            pr.items = normalized_items

        workflow = cls._workflow(pr)

        # Pass 1: Validate all stages before mutating memory
        for index, stage in enumerate(workflow):
            if not (stage.get('user_id') or stage.get('approver_id')):
                raise ValidationError({'error': f'Approval stage {index + 1} has no assigned approver.'})

        # Pass 2: Clean and initialize
        for index, stage in enumerate(workflow):
            stage['step'] = index + 1
            stage['status'] = 'pending'
            stage['approved_at'] = None
            stage.pop('approved_by_id', None)
            stage.pop('approved_by_name', None)
            stage.pop('rejected_at', None)
            stage.pop('rejected_by_id', None)
            stage.pop('rejected_by_name', None)
            stage.pop('rejection_reason', None)

        pr.approval_workflow_config = workflow
        pr.current_approval_step = 0
        pr.status = 'submitted'
        pr.rejection_reason = ''
        pr.save()
        return pr

    @classmethod
    @transaction.atomic
    def approve(cls, pr_id, actor, signature='', expected_stage_key=None):
        pr = get_object_or_404(PurchaseRequisition.objects.select_for_update(), pk=pr_id)
        return cls._approve_locked(pr, actor, signature, expected_stage_key)

    @classmethod
    def _approve_locked(cls, pr, actor, signature='', expected_stage_key=None):
        if canonicalize_pr_status(pr.status) not in cls.ACTIVE_REVIEW_STATUSES:
            raise ValidationError({'error': 'This requisition is not awaiting approval.'})
        if len(signature or '') > 500:
            raise ValidationError({'error': 'Signature cannot exceed 500 characters.'})

        workflow = cls._workflow(pr)
        current_index, stage = cls._current_stage(pr, workflow)
        cls._enforce_expected_stage(stage, expected_stage_key)
        cls._enforce_assigned_approver(stage, actor)

        approved_at = timezone.now()
        actor_name = actor.get_full_name() or getattr(actor, 'username', '') or getattr(actor, 'email', '')
        stage['status'] = 'approved'
        stage['approved_at'] = approved_at.isoformat()
        stage['approved_by_id'] = str(actor.id)
        stage['approved_by_name'] = actor_name
        cls._mirror_fixed_approval(pr, stage, actor, signature or '', approved_at)

        next_index = next(
            (
                index for index in range(current_index + 1, len(workflow))
                if str(workflow[index].get('status', 'pending')).lower() not in ('approved', 'rejected')
            ),
            None,
        )

        if next_index is None:
            pr.current_approval_step = len(workflow)
            pr.status = 'approved'
            pr.approved_by = actor
            pr.approved_at = approved_at
        else:
            pr.current_approval_step = next_index
            pr.status = 'in_review'
            workflow[next_index]['status'] = 'pending'

        pr.approval_workflow_config = workflow
        pr.save()
        return pr

    @classmethod
    @transaction.atomic
    def reject(cls, pr_id, actor, reason, expected_stage_key=None):
        pr = get_object_or_404(PurchaseRequisition.objects.select_for_update(), pk=pr_id)
        return cls._reject_locked(pr, actor, reason, expected_stage_key)

    @classmethod
    def _reject_locked(cls, pr, actor, reason, expected_stage_key=None):
        if canonicalize_pr_status(pr.status) not in cls.ACTIVE_REVIEW_STATUSES:
            raise ValidationError({'error': 'This requisition is not awaiting approval.'})

        trimmed_reason = str(reason or '').strip()
        if len(trimmed_reason) < 10:
            raise ValidationError({'error': 'Rejection reason must be at least 10 characters long.'})
        if len(trimmed_reason) > 1000:
            raise ValidationError({'error': 'Rejection reason cannot exceed 1000 characters.'})

        workflow = cls._workflow(pr)
        _, stage = cls._current_stage(pr, workflow)
        cls._enforce_expected_stage(stage, expected_stage_key)
        cls._enforce_assigned_approver(stage, actor)

        rejected_at = timezone.now()
        actor_name = actor.get_full_name() or getattr(actor, 'username', '') or getattr(actor, 'email', '')
        stage['status'] = 'rejected'
        stage['rejected_at'] = rejected_at.isoformat()
        stage['rejected_by_id'] = str(actor.id)
        stage['rejected_by_name'] = actor_name
        stage['rejection_reason'] = trimmed_reason
        cls._mirror_fixed_rejection(pr, stage)

        pr.approval_workflow_config = workflow
        pr.status = 'rejected'
        pr.rejection_reason = trimmed_reason
        pr.save()
        return pr
