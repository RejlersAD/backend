"""Assigned, sequential salary approval decisions shared by both APIs."""
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from apps.rbac.approval_eligibility import require_approval, has_business_position, approval_access
from .salary_models import SalarySlip, SalarySlipApproval, SalarySlipAuditLog, SalaryStatus, ApprovalStatus


@transaction.atomic
def submit_salary_slip(slip_id, actor):
    """Submit an already configured route; submission records no approval."""
    slip = SalarySlip.objects.select_for_update().get(pk=slip_id)
    if not has_business_position(actor, ('hr_manager', 'hr_admin', 'payroll_admin')):
        raise PermissionDenied('Only the designated HR or payroll position may submit salary approval.')
    if slip.status not in (SalaryStatus.DRAFT, SalaryStatus.GENERATED):
        raise ValidationError('Only draft or generated salary slips can be submitted.')
    rows = list(slip.approvals.select_related('approver').order_by('approval_level'))
    if not rows or any(row.status != ApprovalStatus.PENDING or not row.approver_id for row in rows):
        raise ValidationError('Configure the complete named salary approval route before submission.')
    if any(not (approval_access(row.approver, 'payroll') or approval_access(row.approver, 'finance_salary')) for row in rows):
        raise ValidationError('Every designated salary approver requires active approval access before submission.')
    slip.status = SalaryStatus.PENDING_APPROVAL
    slip.save(update_fields=['status', 'updated_at'])
    SalarySlipAuditLog.objects.create(salary_slip=slip, action='updated', performed_by=actor,
                                      description='Submitted the configured salary approval route.')
    return slip


@transaction.atomic
def decide_salary_slip(slip_id, actor, decision, *, approval_id=None, comment='', module='payroll'):
    slip = SalarySlip.objects.select_for_update().get(pk=slip_id)
    rows = list(SalarySlipApproval.objects.select_for_update().filter(salary_slip=slip).order_by('approval_level'))
    if not rows:
        raise ValidationError('Configure named salary approvers before recording a decision.')
    if decision not in ('approve', 'reject'):
        raise ValidationError('Choose approve or reject.')
    unresolved = [row for row in rows if row.status != ApprovalStatus.APPROVED]
    current = bool(slip.status == SalaryStatus.PENDING_APPROVAL and unresolved
                   and all(row.status in (ApprovalStatus.PENDING, ApprovalStatus.APPROVED) for row in rows))
    stage = unresolved[0] if unresolved else None
    assigned = bool(stage and stage.approver_id == getattr(actor, 'pk', None)
                    and (approval_id is None or str(stage.pk) == str(approval_id)))
    require_approval(actor, module, assigned=assigned, current=current)
    stage.status = ApprovalStatus.APPROVED if decision == 'approve' else ApprovalStatus.REJECTED
    stage.comments = comment
    stage.decision_date = timezone.now()
    stage.save(update_fields=['status', 'comments', 'decision_date', 'updated_at'])
    if decision == 'reject':
        slip.status = SalaryStatus.REJECTED
        slip.rejection_reason = comment
    elif not slip.approvals.exclude(status=ApprovalStatus.APPROVED).exists():
        slip.status = SalaryStatus.APPROVED
        slip.approved_by = actor
        slip.approved_at = timezone.now()
    slip.save()
    SalarySlipAuditLog.objects.create(salary_slip=slip, action='approved' if decision == 'approve' else 'rejected',
        performed_by=actor, description=f'Level {stage.approval_level}: {comment}')
    return slip
