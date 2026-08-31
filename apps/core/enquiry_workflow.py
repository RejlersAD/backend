"""Routing, notifications, and lifecycle operations for enquiries."""
from datetime import timedelta

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from apps.core.models import Enquiry, EnquiryActivity, EnquiryFeedback, EnquiryMessage, EnquiryRoutingRule


DEFAULT_ROUTING = {
    'general': ('Customer Service', 24),
    'technical_support': ('IT / Digital', 8),
    'complaint': ('Quality Management', 12),
    'suggestion': ('Corporate Services', 48),
    'partnership': ('Business Development', 24),
    'legal': ('Legal', 24),
    'hr': ('Human Resources', 24),
    'it_request': ('IT / Digital', 8),
    'finance_request': ('Finance', 24),
    'procurement': ('Procurement', 24),
    'facility_request': ('Facilities', 12),
    'other': ('Administration', 24),
}

LEGACY_TYPE_MAP = {
    'password-reset': 'it_request', 'pid-analysis': 'technical_support',
    'pfd-conversion': 'technical_support', 'asset-integrity': 'technical_support',
    'engineering-consulting': 'technical_support', 'digital-twin': 'technical_support',
    'ai-ml-services': 'technical_support', 'general': 'general', 'other': 'other',
}


def normalize_inquiry_type(value):
    raw = str(value or '').strip().lower()
    value = LEGACY_TYPE_MAP.get(raw, raw.replace('-', '_').replace(' ', '_'))
    return value if value in dict(Enquiry.TYPE_CHOICES) else 'other'


def _manager_candidates(department):
    from apps.core.config.enquiry_access_config import user_has_enquiry_access
    from apps.rbac.models import UserProfile

    profiles = list(UserProfile.objects.select_related('user').filter(
        is_deleted=False, status='active', user__is_active=True,
    ))
    department_words = [word.lower() for word in department.replace('/', ' ').split() if len(word) > 2]
    department_profiles = [
        profile for profile in profiles
        if any(word in (profile.department or '').lower() for word in department_words)
    ]
    eligible = [profile for profile in department_profiles if user_has_enquiry_access(profile.user)]
    # A central 9.6 representative is the safe fallback when a department has
    # not yet configured its own representative. This keeps every request owned.
    if not eligible:
        eligible = [profile for profile in profiles if user_has_enquiry_access(profile.user)]
    users = [profile.user for profile in eligible]
    workload = dict(
        Enquiry.objects.filter(assigned_to_id__in=[user.pk for user in users]).exclude(
            status__in=['resolved', 'closed', 'spam'],
        ).values('assigned_to_id').annotate(total=Count('id')).values_list('assigned_to_id', 'total')
    )
    return sorted(users, key=lambda user: (workload.get(user.pk, 0), user.get_full_name() or user.username))


def _email_external_response(enquiry, body):
    try:
        from django.conf import settings
        from django.core.mail import EmailMultiAlternatives
        email = EmailMultiAlternatives(
            subject=f'[RADAI {enquiry.reference}] {enquiry.subject}',
            body=(f'Dear {enquiry.name},\n\n{body}\n\n'
                  f'Reference: {enquiry.reference}\n\nRegards,\nRADAI Team'),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[enquiry.email],
        )
        email.send(fail_silently=True)
    except Exception:
        return


def _notify(user, *, title, message, action_url, priority='NORMAL', metadata=None, sender=None):
    if not user:
        return
    from apps.notifications.services import NotificationService
    NotificationService.create_notification(
        user, title=title, message=message, category='INFO', priority=priority,
        action_url=action_url, action_label='Open Request', metadata=metadata or {}, sender=sender,
    )


@transaction.atomic
def route_enquiry(enquiry, *, actor=None):
    rule = EnquiryRoutingRule.objects.select_related('representative').filter(
        inquiry_type=enquiry.inquiry_type, is_active=True,
    ).first()
    department, sla_hours = DEFAULT_ROUTING.get(enquiry.inquiry_type, DEFAULT_ROUTING['other'])
    representative = None
    if rule:
        department, sla_hours = rule.department, rule.sla_hours
        if rule.representative and rule.representative.is_active:
            representative = rule.representative
    if representative is None:
        candidates = _manager_candidates(department)
        representative = candidates[0] if candidates else None

    enquiry.department = department
    enquiry.due_at = enquiry.created_at + timedelta(hours=sla_hours)
    enquiry.assigned_to = representative
    enquiry.assigned_by = actor if getattr(actor, 'is_authenticated', False) else None
    enquiry.assigned_at = timezone.now() if representative else None
    enquiry.status = 'assigned' if representative else 'new'
    enquiry.approval_required = enquiry.urgency in ('high', 'urgent')
    enquiry.approval_status = 'pending' if enquiry.approval_required else 'not_required'
    enquiry.save(update_fields=[
        'department', 'due_at', 'assigned_to', 'assigned_by', 'assigned_at', 'status',
        'approval_required', 'approval_status', 'updated_at',
    ])
    EnquiryActivity.objects.create(
        enquiry=enquiry, actor=enquiry.assigned_by, action='auto_routed',
        details={'department': department, 'representative_id': representative.pk if representative else None,
                 'sla_hours': sla_hours},
    )
    if representative:
        _notify(
            representative, title=f'New request ENQ-{enquiry.pk:06d}',
            message=f'{enquiry.get_inquiry_type_display()} assigned to you: {enquiry.subject}',
            action_url=f'/admin/enquiries/{enquiry.pk}',
            priority='HIGH' if enquiry.urgency in ('high', 'urgent') else 'NORMAL',
            metadata={'enquiry_id': enquiry.pk, 'department': department},
        )
    return enquiry


def add_initial_message(enquiry):
    return EnquiryMessage.objects.create(
        enquiry=enquiry, author=enquiry.requester, sender_type='requester', body=enquiry.message,
    )


@transaction.atomic
def add_response(enquiry, *, actor, body, is_internal=False, requester_reply=False):
    body = str(body or '').strip()
    if not body:
        raise ValueError('Response message is required.')
    message = EnquiryMessage.objects.create(
        enquiry=enquiry, author=actor,
        sender_type='requester' if requester_reply else 'representative',
        body=body, is_internal=bool(is_internal and not requester_reply),
    )
    now = timezone.now()
    if requester_reply:
        enquiry.status = 'in_progress'
    elif not is_internal:
        enquiry.status = 'responded'
        enquiry.first_response_at = enquiry.first_response_at or now
    enquiry.save(update_fields=['status', 'first_response_at', 'updated_at'])
    EnquiryActivity.objects.create(
        enquiry=enquiry, actor=actor,
        action='requester_replied' if requester_reply else (
            'internal_note_added' if is_internal else 'representative_responded'
        ), details={'message_id': message.pk},
    )
    if not requester_reply and not is_internal and enquiry.requester:
        _notify(
            enquiry.requester, sender=actor, title=f'Response to ENQ-{enquiry.pk:06d}',
            message=f'{enquiry.department or "RADAI"} responded to your request.',
            action_url=f'/my-enquiries/{enquiry.pk}', metadata={'enquiry_id': enquiry.pk},
        )
    elif not requester_reply and not is_internal:
        _email_external_response(enquiry, body)
    return message


@transaction.atomic
def escalate_enquiry(enquiry, *, actor=None, reason='SLA deadline exceeded'):
    if enquiry.status in ('resolved', 'closed', 'spam'):
        return enquiry
    enquiry.escalation_level = min(3, enquiry.escalation_level + 1)
    enquiry.escalated_at = timezone.now()
    enquiry.escalation_reason = str(reason or 'Escalated for review').strip()
    enquiry.status = 'escalated'
    if enquiry.escalation_level >= 2:
        enquiry.urgency = 'urgent'
    elif enquiry.urgency in ('low', 'normal'):
        enquiry.urgency = 'high'
    enquiry.approval_required = True
    if enquiry.approval_status == 'not_required':
        enquiry.approval_status = 'pending'
    enquiry.save(update_fields=[
        'escalation_level', 'escalated_at', 'escalation_reason', 'status', 'urgency',
        'approval_required', 'approval_status', 'updated_at',
    ])
    EnquiryActivity.objects.create(
        enquiry=enquiry, actor=actor, action='enquiry_escalated',
        details={'level': enquiry.escalation_level, 'reason': enquiry.escalation_reason},
    )
    recipients = {user.pk: user for user in _manager_candidates(enquiry.department)}
    if enquiry.assigned_to:
        recipients[enquiry.assigned_to.pk] = enquiry.assigned_to
    for recipient in recipients.values():
        _notify(
            recipient, sender=actor, title=f'Escalated request: {enquiry.reference}',
            message=f'Level {enquiry.escalation_level}: {enquiry.escalation_reason}',
            action_url=f'/admin/enquiries/{enquiry.pk}', priority='HIGH',
            metadata={'enquiry_id': enquiry.pk, 'escalation_level': enquiry.escalation_level},
        )
    return enquiry


@transaction.atomic
def propose_resolution(enquiry, *, actor, summary):
    summary = str(summary or '').strip()
    if not summary:
        raise ValueError('A resolution summary is required.')
    if enquiry.approval_required and enquiry.approval_status != 'approved':
        raise ValueError('Manager approval is required before proposing resolution.')
    enquiry.resolution_summary = summary
    enquiry.resolution_proposed_at = timezone.now()
    enquiry.status = 'pending_confirmation'
    enquiry.save(update_fields=['resolution_summary', 'resolution_proposed_at', 'status', 'updated_at'])
    EnquiryActivity.objects.create(
        enquiry=enquiry, actor=actor, action='resolution_proposed', details={'summary': summary},
    )
    if enquiry.requester:
        _notify(
            enquiry.requester, sender=actor, title=f'Confirm resolution: {enquiry.reference}',
            message='RADAI has proposed a resolution. Please confirm or reopen the request.',
            action_url=f'/my-enquiries/{enquiry.pk}', priority='HIGH',
            metadata={'enquiry_id': enquiry.pk, 'action': 'confirm_resolution'},
        )
    else:
        from django.conf import settings
        feedback_url = f"{getattr(settings, 'FRONTEND_URL', 'https://radai.ae').rstrip('/')}/enquiry/feedback/{enquiry.feedback_token}"
        _email_external_response(enquiry, f'{summary}\n\nConfirm the resolution and leave feedback: {feedback_url}')
    return enquiry


@transaction.atomic
def confirm_resolution(enquiry, *, actor=None, accepted=True, comment=''):
    now = timezone.now()
    if accepted:
        enquiry.status = 'resolved'
        enquiry.resolved_at = now
        enquiry.resolution_confirmed_at = now
        action = 'resolution_confirmed'
    else:
        enquiry.status = 'reopened'
        enquiry.resolved_at = None
        enquiry.resolution_confirmed_at = None
        enquiry.escalation_level = min(3, enquiry.escalation_level + 1)
        enquiry.escalated_at = now
        enquiry.escalation_reason = str(comment or 'Requester rejected the proposed resolution')
        action = 'resolution_rejected'
    enquiry.save(update_fields=[
        'status', 'resolved_at', 'resolution_confirmed_at', 'escalation_level',
        'escalated_at', 'escalation_reason', 'updated_at',
    ])
    EnquiryActivity.objects.create(
        enquiry=enquiry, actor=actor, action=action, details={'comment': str(comment or '')},
    )
    if enquiry.assigned_to:
        _notify(
            enquiry.assigned_to, sender=actor,
            title=f'Resolution {"confirmed" if accepted else "rejected"}: {enquiry.reference}',
            message=str(comment or enquiry.subject), action_url=f'/admin/enquiries/{enquiry.pk}',
            priority='NORMAL' if accepted else 'HIGH', metadata={'enquiry_id': enquiry.pk},
        )
    return enquiry


@transaction.atomic
def submit_feedback(enquiry, *, rating, comment='', would_recommend=None, actor=None):
    try:
        rating = int(rating)
    except (TypeError, ValueError):
        raise ValueError('Rating must be a number from 1 to 5.')
    if rating < 1 or rating > 5:
        raise ValueError('Rating must be between 1 and 5.')
    if enquiry.status not in ('resolved', 'closed'):
        raise ValueError('Feedback is available after the resolution is confirmed.')
    feedback, _ = EnquiryFeedback.objects.update_or_create(
        enquiry=enquiry,
        defaults={
            'submitted_by': actor if getattr(actor, 'is_authenticated', False) else None,
            'rating': rating, 'comment': str(comment or '').strip(),
            'resolution_confirmed': True, 'would_recommend': would_recommend,
        },
    )
    enquiry.status = 'closed'
    enquiry.closed_at = timezone.now()
    enquiry.save(update_fields=['status', 'closed_at', 'updated_at'])
    EnquiryActivity.objects.create(
        enquiry=enquiry, actor=actor if getattr(actor, 'is_authenticated', False) else None,
        action='feedback_submitted', details={'rating': rating, 'feedback_id': feedback.pk},
    )
    if enquiry.assigned_to:
        _notify(
            enquiry.assigned_to, sender=actor if getattr(actor, 'is_authenticated', False) else None,
            title=f'Feedback received: {enquiry.reference}', message=f'{rating}/5 rating received.',
            action_url=f'/admin/enquiries/{enquiry.pk}', metadata={'enquiry_id': enquiry.pk, 'rating': rating},
        )
    return feedback


def process_sla_escalations(now=None):
    """Idempotent scheduled escalation pass; run from Celery beat or the management command."""
    now = now or timezone.now()
    queryset = Enquiry.objects.filter(due_at__lt=now).exclude(
        status__in=['resolved', 'closed', 'spam', 'pending_confirmation'],
    )
    processed = 0
    for enquiry in queryset.select_related('assigned_to'):
        # Escalate once at the deadline, then once per additional 24-hour breach.
        required_level = min(3, 1 + max(0, int((now - enquiry.due_at).total_seconds() // 86400)))
        if enquiry.escalation_level < required_level:
            escalate_enquiry(enquiry, reason=f'SLA overdue by {now - enquiry.due_at}')
            processed += 1
    return processed
