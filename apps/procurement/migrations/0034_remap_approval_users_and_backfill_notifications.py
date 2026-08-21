import re

from django.db import migrations


PENDING_STATUSES = {'pending', 'in_review'}
ACTIVE_PR_STATUSES = {'submitted', 'in_review'}


def normalize(value):
    return str(value or '').strip().lower()


def stage_level(stage, index):
    explicit = stage.get('level')
    if explicit not in (None, ''):
        try:
            return max(0, int(explicit))
        except (TypeError, ValueError):
            pass
    label = f"{stage.get('stage', '')} {stage.get('role', '')}"
    match = re.search(r'\blevel\s*(\d+)\b', label, re.IGNORECASE)
    return int(match.group(1)) if match else index + 1


def assignment_identity(entry):
    email = normalize(
        entry.get('user_email')
        or entry.get('approver_email')
        or entry.get('email')
    )
    username = normalize(entry.get('username'))
    if not email and '@' in username:
        email = username
    name = normalize(entry.get('user_name') or entry.get('approver'))
    return email, name


def remap_approval_users_and_backfill_notifications(apps, schema_editor):
    User = apps.get_model('users', 'User')
    PurchaseRequisition = apps.get_model('procurement', 'PurchaseRequisition')
    PurchaseOrder = apps.get_model('procurement', 'PurchaseOrder')
    Notification = apps.get_model('notifications', 'Notification')
    NotificationCategory = apps.get_model('notifications', 'NotificationCategory')
    NotificationPreference = apps.get_model('notifications', 'NotificationPreference')

    active_users = list(User.objects.filter(is_active=True))
    users_by_email = {normalize(user.email): user for user in active_users if normalize(user.email)}
    users_by_name_candidates = {}
    for user in active_users:
        full_name = normalize(f'{user.first_name} {user.last_name}')
        if full_name:
            users_by_name_candidates.setdefault(full_name, []).append(user)
    users_by_name = {
        name: candidates[0]
        for name, candidates in users_by_name_candidates.items()
        if len(candidates) == 1
    }

    def resolve(entry):
        email, name = assignment_identity(entry)
        user = users_by_email.get(email) if email else None
        if user is None and name:
            user = users_by_name.get(name)
        if user is None:
            return None
        entry['user_id'] = str(user.pk)
        if email:
            entry['user_email'] = user.email
        return user

    category, _ = NotificationCategory.objects.get_or_create(
        name='APPROVAL',
        defaults={
            'description': 'APPROVAL notifications',
            'icon': 'check-circle',
            'color': 'blue',
            'is_active': True,
        },
    )
    in_app_preferences = dict(
        NotificationPreference.objects.values_list('user_id', 'enable_in_app')
    )

    existing = set()
    for recipient_id, metadata in Notification.objects.values_list('recipient_id', 'metadata').iterator():
        metadata = metadata if isinstance(metadata, dict) else {}
        if metadata.get('pr_id') is not None:
            existing.add((
                'pr', str(metadata.get('pr_id')),
                str(metadata.get('approval_level')),
                str(recipient_id),
            ))
        if metadata.get('po_id') is not None:
            existing.add((
                'po', str(metadata.get('po_id')),
                str(metadata.get('approval_stage')),
                str(recipient_id),
            ))

    repaired_prs = 0
    repaired_pos = 0
    created_notifications = 0

    def create_notification(recipient, sender_id, title, message, action_url, action_label, metadata, key):
        nonlocal created_notifications
        if key in existing:
            return
        enabled = in_app_preferences.get(recipient.pk, True)
        Notification.objects.create(
            recipient_id=recipient.pk,
            sender_id=sender_id,
            title=title,
            message=message,
            category_id=category.pk,
            priority='HIGH',
            status='SENT' if enabled else 'PENDING',
            send_in_app=enabled,
            send_email=False,
            send_sms=False,
            action_url=action_url,
            action_label=action_label,
            metadata=metadata,
        )
        existing.add(key)
        created_notifications += 1

    for pr in PurchaseRequisition.objects.exclude(approval_workflow_config=[]).iterator(chunk_size=200):
        workflow = pr.approval_workflow_config
        if not isinstance(workflow, list):
            continue
        changed = False
        resolved = []
        for index, raw_stage in enumerate(workflow):
            if not isinstance(raw_stage, dict):
                resolved.append((index, raw_stage, None))
                continue
            before = str(raw_stage.get('user_id') or '')
            recipient = resolve(raw_stage)
            changed = changed or (recipient is not None and before != str(recipient.pk))
            resolved.append((index, raw_stage, recipient))
        if changed:
            pr.approval_workflow_config = workflow
            pr.save(update_fields=['approval_workflow_config'])
            repaired_prs += 1

        if normalize(pr.status) not in ACTIVE_PR_STATUSES:
            continue
        pending = [
            item for item in resolved
            if isinstance(item[1], dict) and normalize(item[1].get('status', 'pending')) in PENDING_STATUSES
        ]
        if not pending:
            continue
        current_level = min(stage_level(stage, index) for index, stage, _ in pending)
        for index, stage, recipient in pending:
            if recipient is None or stage_level(stage, index) != current_level:
                continue
            metadata = {
                'pr_id': str(pr.pk),
                'pr_number': pr.pr_number,
                'approval_level': current_level,
            }
            key = ('pr', str(pr.pk), str(current_level), str(recipient.pk))
            create_notification(
                recipient,
                pr.issued_by_id,
                f'PR {pr.pr_number} requires your approval',
                f'You have been assigned as a Level {current_level} approver for Purchase Requisition {pr.pr_number}.',
                f'/procurement/requisitions/{pr.pk}',
                'Review requisition',
                metadata,
                key,
            )

    for order in PurchaseOrder.objects.exclude(approval_log=[]).iterator(chunk_size=200):
        approval_log = order.approval_log
        if not isinstance(approval_log, list):
            continue
        changed = False
        for entry in approval_log:
            if not isinstance(entry, dict):
                continue
            before = str(entry.get('user_id') or '')
            recipient = resolve(entry)
            changed = changed or (recipient is not None and before != str(recipient.pk))
            if recipient is None or normalize(entry.get('status')) != 'pending':
                continue
            stage = str(entry.get('stage') or '')
            metadata = {
                'po_id': str(order.pk),
                'po_number': order.po_number,
                'approval_stage': stage,
            }
            key = ('po', str(order.pk), stage, str(recipient.pk))
            create_notification(
                recipient,
                order.created_by_id,
                f'PO {order.po_number} requires your approval',
                f'You have been assigned for {stage} on Purchase Order {order.po_number}.',
                '/approvals?tab=purchase_order',
                'Open Approval tab',
                metadata,
                key,
            )
        if changed:
            order.approval_log = approval_log
            order.save(update_fields=['approval_log'])
            repaired_pos += 1

    print(
        'Approval notification repair:',
        f'{repaired_prs} requisitions remapped,',
        f'{repaired_pos} purchase orders remapped,',
        f'{created_notifications} notifications created.',
    )


class Migration(migrations.Migration):
    dependencies = [
        ('notifications', '0003_repair_missing_notification_tables'),
        ('procurement', '0033_purchaseorder_approved_at'),
    ]

    operations = [
        migrations.RunPython(
            remap_approval_users_and_backfill_notifications,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
