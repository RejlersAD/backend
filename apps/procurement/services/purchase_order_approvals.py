"""Employee-backed Purchase Order approval assignment and decisions."""

from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import MultipleObjectsReturned, ObjectDoesNotExist
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.rbac.models import UserProfile
from .approval_integrity import stage_signature_issue
from .employee_display import employee_display_name
from .notification_context import purchase_order_teams_context
from .approval_eligibility import MODULE_PO, eligible_stage_assignee, position_matches_stage
from .purchase_order_approval_artwork import DEFAULT_APPROVAL_STAMP_REFERENCE
from .purchase_order_content import purchase_order_content_fingerprint, purchase_order_content_issue


TECHNICAL_STAGE = 'Technical Approval'
FINANCIAL_STAGE = 'Financial Approval'
MANAGEMENT_STAGE = 'Final Management Sign-off'
JARMO_NAME = 'Jarmo Suominen'
ACTIONABLE_ORDER_STATUSES = {'draft', 'sent', 'acknowledged', 'in_progress', 'partially_received'}


def _order_is_actionable(order):
    return (
        str(getattr(order, 'status', 'draft') or '').strip().lower() in ACTIONABLE_ORDER_STATUSES
        and not purchase_order_content_issue(order)
    )


def _active_actor_profile(actor, *, refresh=False):
    if not actor or not getattr(actor, 'is_active', False):
        return None
    if refresh:
        return UserProfile.objects.select_related('user').filter(
            user_id=actor.pk,
            user__is_active=True,
            status='active',
            is_deleted=False,
        ).first()
    # The relation is cached on the request user, so serializer/queue checks do
    # not add one profile query per PO. The decision write always refreshes it.
    try:
        profile = actor.rbac_profile
    except (AttributeError, ObjectDoesNotExist):
        return None
    if profile.status != 'active' or profile.is_deleted:
        return None
    return profile


def _entry_email(entry):
    return str(entry.get('approver_email') or entry.get('user_email') or entry.get('email') or '').strip().lower()


def _entry_matches_user(entry, user):
    """Prefer email because numeric user IDs are different in each environment."""
    if not getattr(user, 'is_active', True):
        return False
    assigned_email = _entry_email(entry)
    user_email = str(getattr(user, 'email', '') or '').strip().lower()
    if assigned_email:
        return bool(user_email) and assigned_email == user_email
    return bool(entry.get('user_id')) and str(entry.get('user_id')) == str(user.id)


def _entry_level(entry, index):
    """Return a stable numeric level for new and legacy approval logs."""
    try:
        return max(0, int(entry.get('level')))
    except (TypeError, ValueError):
        return index


def _active_entries(workflow):
    """Every earlier level must be approved before a later one may be actioned."""
    if any(not isinstance(entry, dict) for entry in workflow):
        return []
    if any(
        str(entry.get('status') or '').strip().lower() == 'approved' and stage_signature_issue(entry)
        for entry in workflow
    ):
        return []
    if any(
        str(entry.get('status') or '').strip().lower() in {'rejected', 'not_approved', 'declined'}
        for entry in workflow
    ):
        return []
    unresolved = [
        (index, entry) for index, entry in enumerate(workflow)
        if str(entry.get('status') or 'pending').strip().lower() != 'approved'
    ]
    if not unresolved:
        return []
    active_level = min(_entry_level(entry, index) for index, entry in unresolved)
    active_entries = [
        (index, entry) for index, entry in unresolved
        if _entry_level(entry, index) == active_level
    ]
    if any(
        str(entry.get('status') or 'pending').strip().lower() not in {'pending', 'in_review'}
        for _, entry in active_entries
    ):
        return []
    return active_entries


def _resolve_entry_user(entry):
    User = get_user_model()
    assigned_email = _entry_email(entry)
    try:
        if assigned_email:
            recipient = User.objects.get(email__iexact=assigned_email, is_active=True)
        elif entry.get('user_id'):
            recipient = User.objects.get(pk=entry['user_id'], is_active=True)
        else:
            return None
    except (ObjectDoesNotExist, MultipleObjectsReturned, ValueError, TypeError):
        return None
    # Resolve one active employee; never choose an arbitrary case-variant
    # email match or notify an account whose employee profile was suspended.
    if _active_actor_profile(recipient) is None:
        return None
    if assigned_email:
        entry['user_id'] = str(recipient.pk)
    return recipient


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
    return position_matches_stage(profile.user, {'stage': FINANCIAL_STAGE})


def _jarmo_user():
    User = get_user_model()
    candidates = list(User.objects.filter(
        is_active=True,
        first_name__iexact='Jarmo',
        last_name__iexact='Suominen',
        rbac_profile__status='active',
        rbac_profile__is_deleted=False,
    )[:2])
    return candidates[0] if len(candidates) == 1 else None


def default_management_assignment():
    """Use the existing PO final signatory, without borrowing PR decisions."""
    recipient = _jarmo_user()
    if recipient is None:
        raise ValidationError({'error': (
            'The configured PO final signatory could not be resolved to one active employee. '
            'Select an eligible final signatory in the Purchase Order form before creating this order.'
        )})
    stage = {'stage': MANAGEMENT_STAGE, 'level': 0, 'user_id': str(recipient.pk)}
    if not eligible_stage_assignee(recipient, stage, MODULE_PO):
        raise ValidationError({'error': (
            'The configured PO final signatory must have the official CEO position and '
            'Purchase Order approval permission. Review the employee assignment before conversion.'
        )})
    return normalize_assignments([stage], require_core=False, require_management=True)


def normalize_assignments(approval_log, existing_log=None, require_core=True, require_management=False):
    """Validate employee assignments and keep decisions server-controlled."""
    incoming = [dict(entry) for entry in (approval_log or []) if isinstance(entry, dict)]

    def assignment_key(entry, index):
        # Match the route guard's stage comparison: cosmetic label changes
        # must never discard a recorded decision or its content fingerprint.
        return (str(entry.get('stage') or '').strip().lower(), _entry_level(entry, index),
                str(entry.get('user_id') or '').strip())

    existing_by_assignment = {}
    for index, entry in enumerate(existing_log or []):
        if not isinstance(entry, dict) or not entry.get('user_id'):
            continue
        key = assignment_key(entry, index)
        if key in existing_by_assignment:
            raise ValidationError({'approval_log': (
                'The saved approval route contains duplicate assignments. '
                'Approval review is required before editing this route.'
            )})
        existing_by_assignment[key] = entry

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

    incoming_assignments = set()
    for index, entry in enumerate(incoming):
        if not entry.get('user_id'):
            continue
        key = assignment_key(entry, index)
        if key in incoming_assignments:
            raise ValidationError({'approval_log': (
                'Duplicate approval assignments are not allowed. '
                'Select each approver only once for the same stage and level.'
            )})
        incoming_assignments.add(key)

    user_ids = {str(entry.get('user_id')).strip() for entry in incoming if entry.get('user_id')}
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
        if not eligible_stage_assignee(profile.user, entry, MODULE_PO):
            raise ValidationError({'approval_log': (
                f'{stage or "Approval stage"} requires an employee in the configured business position '
                'with Purchase Order approval permission.'
            )})

        user = profile.user
        level = _entry_level(entry, index)
        previous = existing_by_assignment.get(assignment_key(entry, index)) or {}
        same_assignee = bool(previous)
        normalized.append({
            'stage': stage,
            **({'business_position': entry['business_position']} if entry.get('business_position') else {}),
            'level': level,
            'user_id': user_id,
            'approver': employee_display_name(user),
            'approver_email': user.email,
            # A reassignment needs a new alert even if this person held the
            # same stage earlier. Keep legacy assignments unversioned until
            # changed so an ordinary edit does not resend their old alerts.
            'assignment_id': previous.get('assignment_id', '') if same_assignee else str(uuid4()),
            'status': previous.get('status', 'Pending') if same_assignee else 'Pending',
            'date': previous.get('date', '') if same_assignee else '',
            'approved_at': previous.get('approved_at', previous.get('date', '')) if same_assignee else '',
            'comments': previous.get('comments', '') if same_assignee else '',
            'signature': previous.get('signature', '') if same_assignee else '',
            **{
                field: previous[field]
                for field in (
                    'decided_at', 'decided_by_id', 'decided_by_email', 'decided_by_name',
                    'approved_by_id', 'approved_by_email', 'approved_by_name',
                    'rejected_by_id', 'rejected_by_email', 'rejected_by_name',
                    'signature_user_id', 'signature_user_email', 'content_fingerprint',
                )
                if same_assignee and field in previous
            },
        })
    return normalized


def notify_assigned_approvers(order, previous_approver='', previous_level=None):
    """Notify only the active PO level; later levels remain locked."""
    from apps.notifications.models import Notification
    from apps.notifications.services import NotificationService

    try:
        order.refresh_from_db()
    except ObjectDoesNotExist:
        return
    if not _order_is_actionable(order):
        return
    workflow = [dict(entry) for entry in (order.approval_log or [])]
    entries = [
        (index, entry) for index, entry in _active_entries(workflow)
        if entry.get('user_id') or _entry_email(entry)
    ]
    teams_context = None
    for index, entry in entries:
        recipient = _resolve_entry_user(entry)
        if recipient is None or not eligible_stage_assignee(recipient, entry, MODULE_PO):
            continue
        metadata = {
            'event_type': 'approval_assignment',
            'entity_type': 'purchase_order',
            'entity_id': str(order.id),
            'request_number': order.po_number,
            'po_id': str(order.id),
            'po_number': order.po_number,
            'approval_stage': entry.get('stage'),
            'approval_level': _entry_level(entry, index),
            'assignment_id': entry.get('assignment_id', ''),
            'requires_action': True,
        }
        previous_notifications = Notification.objects.filter(
            recipient=recipient,
            metadata__po_id=str(order.id),
            metadata__approval_stage=entry.get('stage'),
            metadata__approval_level=_entry_level(entry, index),
            metadata__requires_action=True,
        ).exclude(
            # A PR may refer to this PO without being a PO approval alert.
            # Checking key existence also retains untyped legacy rows.
            metadata__has_key='entity_type', metadata__entity_type='purchase_recommendation',
        )
        if entry.get('assignment_id'):
            previous_notifications = previous_notifications.filter(
                metadata__assignment_id=entry['assignment_id'],
            )
        if previous_notifications.exists():
            continue
        if teams_context is None:
            teams_context = purchase_order_teams_context(order)
        NotificationService.create_notification(
            recipient=recipient,
            sender=order.created_by,
            title=f'Purchase Order {order.po_number} requires your approval',
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
            teams_context={**teams_context, 'approval_level': _entry_level(entry, index)},
            metadata=metadata,
        )
    # Notification delivery must not save the approval log: another decision
    # can commit while a notification is being prepared. The locked decision
    # service repairs migrated IDs when it records the actual approval.


def notify_purchase_order_created(order):
    """Send buyer FYIs without notifying approvers before their active level."""
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
    seen = set()
    teams_context = None
    for role, recipient in recipients:
        identity = str(getattr(recipient, 'pk', None) or getattr(recipient, 'email', '')).lower()
        if not identity or identity in seen:
            continue
        seen.add(identity)
        if any(
            _entry_matches_user(entry, recipient)
            for entry in (getattr(order, 'approval_log', None) or [])
        ):
            continue
        metadata = {
            'event_type': 'po_created',
            'entity_type': 'purchase_order',
            'entity_id': str(order.id),
            'request_number': order.po_number,
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
        if teams_context is None:
            teams_context = purchase_order_teams_context(order)
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
                **teams_context,
                'event_type': 'purchase_order_created',
                'title': 'New purchase order created',
            },
            metadata=metadata,
        )


def can_approve(order, actor):
    """Share assignment eligibility between the PO display and decision API."""
    if not _order_is_actionable(order) or _active_actor_profile(actor) is None:
        return False
    return any(
        _entry_matches_user(entry, actor) and eligible_stage_assignee(actor, entry, MODULE_PO)
        for _, entry in _active_entries(list(order.approval_log or []))
    )


def pending_entries_for(user, queryset):
    results = []
    if _active_actor_profile(user) is None:
        return results
    for order in queryset:
        if not _order_is_actionable(order):
            continue
        for index, entry in _active_entries(list(order.approval_log or [])):
            if not _entry_matches_user(entry, user) or not eligible_stage_assignee(user, entry, MODULE_PO):
                continue
            results.append((order, index, entry))
    return results


@transaction.atomic
def record_decision(order, actor, decision, stage='', comment='', require_signature=False):
    from apps.procurement.models import PurchaseOrder

    if decision not in {'approve', 'reject'}:
        raise ValidationError('Choose approve or reject for the assigned approval stage.')
    # Refresh the canonical profile instead of using a possibly stale related
    # object on request.user. Administrator status never substitutes for an
    # active employee assignment or somebody else's saved signature.
    profile = _active_actor_profile(actor, refresh=True)
    if profile is None:
        raise PermissionDenied('Only an active RADAI employee may record a Purchase Order decision.')
    actor = profile.user
    locked = PurchaseOrder.objects.select_for_update(of=('self',)).select_related('created_by').get(pk=order.pk)
    content_issue = purchase_order_content_issue(locked)
    if content_issue:
        raise ValidationError(content_issue)
    if not _order_is_actionable(locked):
        raise ValidationError('This Purchase Order is not open for approval decisions.')
    workflow = [dict(entry) for entry in (locked.approval_log or [])]
    candidate = None
    for index, entry in _active_entries(workflow):
        if not _entry_matches_user(entry, actor) or not eligible_stage_assignee(actor, entry, MODULE_PO):
            continue
        if stage and str(entry.get('stage') or '') != str(stage):
            continue
        candidate = (index, entry)
        break
    if candidate is None:
        raise PermissionDenied('This Purchase Order has no pending approval assigned to you for the selected stage.')

    index, entry = candidate
    decision_at = timezone.now()
    signature = ''
    if decision == 'approve':
        signature = str(profile.signature_image or '').strip()
        if require_signature and not signature:
            raise ValidationError('Add your signature in Profile > My Signature before approving.')
    actor_name = employee_display_name(actor)
    actor_email = str(actor.email or '').strip()
    # Assignment display and decision evidence must identify the person who
    # actually authenticated, including when a migrated user ID was repaired.
    entry['user_id'] = str(actor.pk)
    entry['approver'] = actor_name
    entry['approver_email'] = actor_email
    if 'user_email' in entry:
        entry['user_email'] = actor_email
    entry['status'] = 'Approved' if decision == 'approve' else 'Rejected'
    entry['date'] = decision_at.isoformat()
    entry['approved_at'] = decision_at.isoformat() if decision == 'approve' else ''
    entry['decided_at'] = decision_at.isoformat()
    entry['decided_by_id'] = str(actor.pk)
    entry['decided_by_email'] = actor_email
    entry['decided_by_name'] = actor_name
    entry['comments'] = str(comment or '').strip()
    entry['signature'] = signature
    entry['signature_user_id'] = str(actor.pk) if signature else ''
    entry['signature_user_email'] = actor_email if signature else ''
    if decision == 'approve':
        entry['approved_by_id'] = str(actor.pk)
        entry['content_fingerprint'] = purchase_order_content_fingerprint(locked)
        entry['approved_by_email'] = actor_email
        entry['approved_by_name'] = actor_name
        for field in ('rejected_by_id', 'rejected_by_email', 'rejected_by_name'):
            entry.pop(field, None)
    else:
        entry['rejected_by_id'] = str(actor.pk)
        entry['rejected_by_email'] = actor_email
        entry['rejected_by_name'] = actor_name
        for field in ('approved_by_id', 'approved_by_email', 'approved_by_name'):
            entry.pop(field, None)
    workflow[index] = entry
    locked.approval_log = workflow

    update_fields = ['approval_log', 'updated_at']
    # Email-only migrated assignments and unresolved evidence still belong to
    # the route. A missing local user ID must never make a later level vanish
    # from the final-approval check. Reviewed source rows already marked
    # approved remain valid history without needing an internal assignment.
    if workflow and all(str(item.get('status') or '').strip().lower() == 'approved' for item in workflow):
        locked.approved_by = actor
        locked.approved_by_name = actor_name
        locked.approved_by_title = str(getattr(profile, 'job_title', '') or '').strip()
        locked.approved_date = timezone.localtime(decision_at).date()
        locked.approved_at = decision_at
        locked.approval_signature = signature
        locked.approval_stamp = DEFAULT_APPROVAL_STAMP_REFERENCE
        update_fields.extend([
            'approved_by', 'approved_by_name', 'approved_by_title',
            'approved_date', 'approved_at', 'approval_signature', 'approval_stamp',
        ])
    locked.save(update_fields=update_fields)
    next_entries = _active_entries(workflow)
    if (
        decision == 'approve'
        and next_entries
        and _entry_level(next_entries[0][1], next_entries[0][0]) > _entry_level(entry, index)
    ):
        transaction.on_commit(
            lambda: notify_assigned_approvers(
                locked,
                previous_approver=actor_name,
                previous_level=_entry_level(entry, index),
            ),
            robust=True,
        )
    return locked, entry
