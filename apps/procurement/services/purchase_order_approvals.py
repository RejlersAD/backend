"""Employee-backed Purchase Order approval assignment and decisions."""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.rbac.models import UserProfile


TECHNICAL_STAGE = 'Technical Approval'
FINANCIAL_STAGE = 'Financial Approval'
MANAGEMENT_STAGE = 'Final Management Sign-off'
JARMO_NAME = 'Jarmo Suominen'


def _active_profiles(user_ids):
    return {
        str(profile.user_id): profile
        for profile in UserProfile.objects.filter(
            user_id__in=user_ids,
            status='active',
            is_deleted=False,
            user__is_active=True,
        ).select_related('user').prefetch_related('roles__modules')
    }


def is_finance_profile(profile):
    department = (profile.department or '').strip().lower()
    if 'finance' in department or 'financial' in department or 'account' in department:
        return True
    return any(
        (module.code or '').lower().startswith(('finance', 'invoice', 'account'))
        for role in profile.roles.all()
        for module in role.modules.all()
        if role.is_active and module.is_active
    )


def _jarmo_user():
    User = get_user_model()
    return User.objects.filter(
        is_active=True,
        first_name__iexact='Jarmo',
        last_name__iexact='Suominen',
        rbac_profile__status='active',
        rbac_profile__is_deleted=False,
    ).first()


def normalize_assignments(approval_log, existing_log=None, require_core=True, require_management=False):
    """Validate employee assignments and keep decisions server-controlled."""
    incoming = [dict(entry) for entry in (approval_log or []) if isinstance(entry, dict)]
    existing_by_stage = {
        str(entry.get('stage') or ''): entry
        for entry in (existing_log or [])
        if isinstance(entry, dict)
    }

    final_entry = next((entry for entry in incoming if entry.get('stage') == MANAGEMENT_STAGE), None)
    if require_management and final_entry is not None and not final_entry.get('user_id'):
        jarmo = _jarmo_user()
        if jarmo:
            final_entry['user_id'] = str(jarmo.id)

    required = {TECHNICAL_STAGE, FINANCIAL_STAGE} if require_core else set()
    if require_management:
        required.add(MANAGEMENT_STAGE)
    missing = [
        stage for stage in required
        if not any(entry.get('stage') == stage and entry.get('user_id') for entry in incoming)
    ]
    if missing:
        raise ValidationError({'approval_log': f"Select an active employee for: {', '.join(sorted(missing))}."})

    user_ids = {str(entry.get('user_id')) for entry in incoming if entry.get('user_id')}
    profiles = _active_profiles(user_ids)
    if len(profiles) != len(user_ids):
        raise ValidationError({'approval_log': 'Every selected PO approver must be an active RADAI employee.'})

    normalized = []
    for entry in incoming:
        stage = str(entry.get('stage') or '').strip()
        user_id = str(entry.get('user_id') or '').strip()
        if not user_id:
            # Optional unassigned stages are not part of the approval queue.
            continue
        profile = profiles[user_id]
        if stage == FINANCIAL_STAGE and not is_finance_profile(profile):
            raise ValidationError({'approval_log': 'Financial Approval must be assigned to an active Finance employee.'})

        user = profile.user
        previous = existing_by_stage.get(stage) or {}
        same_assignee = str(previous.get('user_id') or '') == user_id
        normalized.append({
            'stage': stage,
            'user_id': user_id,
            'approver': user.get_full_name() or user.email,
            'approver_email': user.email,
            'status': previous.get('status', 'Pending') if same_assignee else 'Pending',
            'date': previous.get('date', '') if same_assignee else '',
            'approved_at': previous.get('approved_at', previous.get('date', '')) if same_assignee else '',
            'comments': previous.get('comments', '') if same_assignee else '',
        })
    return normalized


def notify_assigned_approvers(order):
    """Create one durable in-app notification per PO stage assignment."""
    from apps.notifications.models import Notification
    from apps.notifications.services import NotificationService

    entries = [entry for entry in (order.approval_log or []) if entry.get('user_id')]
    users = get_user_model().objects.filter(
        id__in=[entry['user_id'] for entry in entries], is_active=True,
    ).in_bulk()
    for entry in entries:
        recipient = users.get(entry['user_id'])
        if recipient is None:
            # UUID keys returned by in_bulk can differ from JSON strings.
            recipient = next((user for key, user in users.items() if str(key) == str(entry['user_id'])), None)
        if recipient is None:
            continue
        metadata = {
            'po_id': str(order.id),
            'po_number': order.po_number,
            'approval_stage': entry.get('stage'),
        }
        if Notification.objects.filter(
            recipient=recipient,
            metadata__po_id=str(order.id),
            metadata__approval_stage=entry.get('stage'),
        ).exists():
            continue
        NotificationService.create_notification(
            recipient=recipient,
            sender=order.created_by,
            title=f'PO {order.po_number} requires your approval',
            message=f"You have been assigned for {entry.get('stage')} on Purchase Order {order.po_number}.",
            category='APPROVAL',
            priority='HIGH',
            action_url='/approvals?tab=purchase_order',
            action_label='Open Approval tab',
            metadata=metadata,
        )


def pending_entries_for(user, queryset):
    results = []
    for order in queryset:
        for index, entry in enumerate(order.approval_log or []):
            if str(entry.get('user_id') or '') != str(user.id):
                continue
            if str(entry.get('status') or '').lower() != 'pending':
                continue
            results.append((order, index, entry))
    return results


@transaction.atomic
def record_decision(order, actor, decision, stage='', comment=''):
    from apps.procurement.models import PurchaseOrder

    locked = PurchaseOrder.objects.select_for_update().select_related('created_by').get(pk=order.pk)
    workflow = [dict(entry) for entry in (locked.approval_log or [])]
    candidate = None
    for index, entry in enumerate(workflow):
        if str(entry.get('user_id') or '') != str(actor.id):
            continue
        if stage and str(entry.get('stage') or '') != str(stage):
            continue
        if str(entry.get('status') or '').lower() == 'pending':
            candidate = (index, entry)
            break
    if candidate is None:
        raise PermissionDenied('This Purchase Order has no pending approval assigned to you for the selected stage.')

    index, entry = candidate
    decision_at = timezone.now()
    entry['status'] = 'Approved' if decision == 'approve' else 'Rejected'
    entry['date'] = decision_at.isoformat()
    entry['approved_at'] = decision_at.isoformat() if decision == 'approve' else ''
    entry['decided_at'] = decision_at.isoformat()
    entry['comments'] = str(comment or '').strip()
    workflow[index] = entry
    locked.approval_log = workflow

    update_fields = ['approval_log', 'updated_at']
    assigned_entries = [item for item in workflow if item.get('user_id')]
    if assigned_entries and all(str(item.get('status')).lower() == 'approved' for item in assigned_entries):
        locked.approved_by = actor
        locked.approved_by_name = actor.get_full_name() or actor.email
        locked.approved_date = timezone.localtime(decision_at).date()
        locked.approved_at = decision_at
        update_fields.extend(['approved_by', 'approved_by_name', 'approved_date', 'approved_at'])
    locked.save(update_fields=update_fields)
    return locked, entry
