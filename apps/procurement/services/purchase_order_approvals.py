"""Employee-backed Purchase Order approval assignment and decisions."""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.rbac.models import UserProfile
from .employee_display import employee_display_name


TECHNICAL_STAGE = 'Technical Approval'
FINANCIAL_STAGE = 'Financial Approval'
MANAGEMENT_STAGE = 'Final Management Sign-off'
JARMO_NAME = 'Jarmo Suominen'


def _entry_email(entry):
    return str(entry.get('approver_email') or entry.get('user_email') or entry.get('email') or '').strip().lower()


def _entry_matches_user(entry, user):
    """Prefer email because numeric user IDs are different in each environment."""
    assigned_email = _entry_email(entry)
    user_email = str(getattr(user, 'email', '') or '').strip().lower()
    if assigned_email and user_email:
        return assigned_email == user_email
    return bool(entry.get('user_id')) and str(entry.get('user_id')) == str(user.id)


def _entry_level(entry, index):
    """Return a stable numeric level for new and legacy approval logs."""
    try:
        return max(0, int(entry.get('level')))
    except (TypeError, ValueError):
        return index


def _active_entries(workflow):
    """Only the first pending approval level may be actioned."""
    pending = [
        (index, entry) for index, entry in enumerate(workflow)
        if str(entry.get('status') or 'pending').lower() == 'pending'
    ]
    if not pending:
        return []
    active_level = min(_entry_level(entry, index) for index, entry in pending)
    return [(index, entry) for index, entry in pending if _entry_level(entry, index) == active_level]


def _resolve_entry_user(entry):
    User = get_user_model()
    assigned_email = _entry_email(entry)
    if assigned_email:
        recipient = User.objects.filter(email__iexact=assigned_email, is_active=True).first()
        if recipient:
            entry['user_id'] = str(recipient.pk)
            return recipient
    if entry.get('user_id'):
        return User.objects.filter(pk=entry['user_id'], is_active=True).first()
    return None


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
    for index, entry in enumerate(incoming):
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
            'level': _entry_level(entry, index),
            'user_id': user_id,
            'approver': employee_display_name(user),
            'approver_email': user.email,
            'status': previous.get('status', 'Pending') if same_assignee else 'Pending',
            'date': previous.get('date', '') if same_assignee else '',
            'approved_at': previous.get('approved_at', previous.get('date', '')) if same_assignee else '',
            'comments': previous.get('comments', '') if same_assignee else '',
        })
    return normalized


def notify_assigned_approvers(order, previous_approver='', previous_level=None):
    """Notify only the active PO level; later levels remain locked."""
    from apps.notifications.models import Notification
    from apps.notifications.services import NotificationService

    workflow = list(order.approval_log or [])
    entries = [
        (index, entry) for index, entry in _active_entries(workflow)
        if entry.get('user_id') or _entry_email(entry)
    ]
    repaired = False
    for index, entry in entries:
        old_user_id = str(entry.get('user_id') or '')
        recipient = _resolve_entry_user(entry)
        if recipient is None:
            continue
        repaired = repaired or old_user_id != str(recipient.pk)
        metadata = {
            'po_id': str(order.id),
            'po_number': order.po_number,
            'approval_stage': entry.get('stage'),
            'approval_level': _entry_level(entry, index),
            'requires_action': True,
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
            message=(
                f'Level {previous_level} ({previous_approver}) is approved. Purchase Order '
                f'{order.po_number} is now waiting for your {entry.get("stage")} decision.'
                if previous_approver else
                f'Purchase Order {order.po_number} is waiting for your {entry.get("stage")} decision.'
            ),
            category='APPROVAL',
            priority='HIGH',
            action_url=f'/procurement/orders/{order.id}',
            action_label='Open Request',
            send_teams=True,
            teams_context={
                'request_name': f'Purchase Order {order.po_number}',
                'submitted_by': employee_display_name(order.created_by) if getattr(order, 'created_by', None) else 'Not specified',
                'description': (
                    getattr(order, 'description', None)
                    or getattr(order, 'title', None)
                    or 'Not specified'
                ),
                'project_name': (
                    getattr(getattr(order, 'project', None), 'project_name', None)
                    or getattr(getattr(order, 'enterprise_project', None), 'name', None)
                    or getattr(order, 'project_number', None) or 'Not specified'
                ),
                'project_id': (
                    getattr(order, 'project_number', None)
                    or getattr(getattr(order, 'enterprise_project', None), 'code', None)
                    or 'Not specified'
                ),
                'due_date': getattr(order, 'expected_delivery', None) or getattr(order, 'end_date', None),
            },
            metadata=metadata,
        )
    if repaired:
        order.approval_log = list(order.approval_log or [])
        order.save(update_fields=['approval_log', 'updated_at'])


def notify_purchase_order_created(order):
    """Notify selected Buyer References and the CEO when a PO is created."""
    from apps.notifications.models import Notification
    from apps.notifications.services import NotificationService

    buyer_entries = []
    contact_people = order.contact_persons if isinstance(order.contact_persons, dict) else {}
    for entry in contact_people.get('buyer_references') or []:
        if isinstance(entry, dict):
            buyer_entries.append(dict(entry))
    if order.buyer_reference_email:
        buyer_entries.append({
            'email': order.buyer_reference_email,
            'name': order.buyer_reference_pm,
        })

    recipients = []
    for entry in buyer_entries:
        recipient = _resolve_entry_user(entry)
        if recipient:
            recipients.append(('buyer_reference', recipient))
    jarmo = _jarmo_user()
    if jarmo:
        recipients.append(('ceo', jarmo))

    seen = set()
    for role, recipient in recipients:
        identity = str(getattr(recipient, 'pk', None) or getattr(recipient, 'email', '')).lower()
        if not identity or identity in seen:
            continue
        seen.add(identity)
        metadata = {
            'event_type': 'po_created',
            'po_id': str(order.id),
            'po_number': order.po_number,
            'recipient_role': role,
        }
        if Notification.objects.filter(
            recipient=recipient,
            metadata__event_type='po_created',
            metadata__po_id=str(order.id),
        ).exists():
            continue
        NotificationService.create_notification(
            recipient=recipient,
            sender=order.created_by,
            title=f'Purchase Order {order.po_number} created',
            message=f'Purchase Order {order.po_number} has been created and is available for review.',
            category='PROCUREMENT',
            priority='HIGH',
            action_url=f'/procurement/orders/{order.id}',
            action_label='Open Purchase Order',
            send_teams=True,
            teams_context={
                'event_type': 'purchase_order_created',
                'title': 'New purchase order created',
                'request_name': f'Purchase Order {order.po_number}',
                'submitted_by': employee_display_name(order.created_by) if order.created_by else 'Not specified',
                'due_date': order.expected_delivery or order.end_date,
            },
            metadata=metadata,
        )


def pending_entries_for(user, queryset):
    results = []
    for order in queryset:
        for index, entry in _active_entries(list(order.approval_log or [])):
            if not _entry_matches_user(entry, user):
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
    for index, entry in _active_entries(workflow):
        if not _entry_matches_user(entry, actor):
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
        locked.approved_by_name = employee_display_name(actor)
        locked.approved_date = timezone.localtime(decision_at).date()
        locked.approved_at = decision_at
        update_fields.extend(['approved_by', 'approved_by_name', 'approved_date', 'approved_at'])
    locked.save(update_fields=update_fields)
    if decision == 'approve' and _active_entries(workflow):
        transaction.on_commit(
            lambda: notify_assigned_approvers(
                locked,
                previous_approver=employee_display_name(actor),
                previous_level=_entry_level(entry, index),
            ),
            robust=True,
        )
    return locked, entry
