"""Current business assignment and sequence for finance decisions."""
from django.contrib.auth import get_user_model
from apps.rbac.approval_eligibility import approval_access
from .models import InvoiceStatus, ApprovalStatus


def validated_invoice_route(chain, *, actor=None):
    """Reject incomplete routes; never turn a missing lower reviewer into a skip."""
    from rest_framework.exceptions import ValidationError
    if not isinstance(chain, list) or not chain:
        raise ValidationError('Configure a complete named invoice approval chain.')
    result = []
    for entry in chain:
        if not isinstance(entry, dict):
            raise ValidationError('Every approval stage must contain a name, email and numeric level.')
        level = entry.get('level')
        if isinstance(level, bool) or not isinstance(level, int) or level < 0:
            raise ValidationError('Approval levels must be non-negative integers.')
        email = str(entry.get('email') or '').strip()
        if not str(entry.get('name') or '').strip() or not email:
            raise ValidationError('Every approval stage requires a named approver and email; stages cannot be skipped.')
        candidates = list(get_user_model().objects.filter(email__iexact=email, is_active=True)[:2])
        if len(candidates) != 1 or not approval_access(candidates[0], 'finance_incoming'):
            raise ValidationError(f'The approver at level {level} requires a unique active account and invoice approval access.')
        if actor is not None and candidates[0].pk == actor.pk:
            raise ValidationError('Another authorized Finance administrator must configure your own approval assignment.')
        result.append({**entry, 'email': candidates[0].email})
    return result


def invoice_approver(approval):
    matches = list(get_user_model().objects.filter(
        email__iexact=str(approval.approver_email or '').strip(), is_active=True,
    )[:2])
    return matches[0] if len(matches) == 1 else None


def invoice_approval_current(approval):
    if approval.status != ApprovalStatus.PENDING or approval.invoice.status != InvoiceStatus.PENDING_APPROVAL:
        return False
    rows = list(approval.invoice.approvals.all())
    if any(row.status not in (ApprovalStatus.PENDING, ApprovalStatus.APPROVED) for row in rows):
        return False
    return all(row.status == ApprovalStatus.APPROVED for row in rows if row.approval_level < approval.approval_level)


def can_approve_invoice(approval, actor):
    assigned = invoice_approver(approval)
    return bool(assigned and actor and assigned.pk == actor.pk
                and approval_access(actor, 'finance_incoming') and invoice_approval_current(approval))
