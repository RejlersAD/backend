"""Employee separation: resignation or termination, aligned with offboarding.

One service keeps the account, the HR employee record and the offboarding case
in step, whichever screen starts it:

* HR uses "Deactivate account" in the employee directory  -> ``separate_employee``.
  It records the separation type, opens (or reuses) the offboarding case and
  either disables the login now or leaves it until offboarding completes.
* HR completes an offboarding case                        -> ``close_separated_account``.
  The login is disabled and the HR record is marked Exited.

Every change is audited, and the HR team is notified after commit.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.hr_core.models import EmployeeMaster
from apps.notifications.services import NotificationService
from apps.rbac.models import UserProfile, UserRole

from .models import OffboardingRecord
from .rbac import LIFECYCLE_ADMIN_ROLES, LIFECYCLE_HR_ROLES

logger = logging.getLogger(__name__)

SEPARATION_TYPES = {'resignation': 'Resignation', 'termination': 'Termination'}
CLOSED_OFFBOARDING_STATUSES = ('completed', 'cancelled', 'rejected')
REASON_MAX_LENGTH = 2000


def open_offboarding_case(user):
    """The employee's current (not closed) offboarding case, if any."""
    return (OffboardingRecord.objects.select_for_update()
            .filter(user=user).exclude(status__in=CLOSED_OFFBOARDING_STATUSES)
            .order_by('-initiated_date').first())


def _audit(actor, action, user, metadata, request=None):
    from apps.rbac.utils import create_audit_log
    # AuditLog.resource_id is a UUID: use the RBAC profile (as /rbac/users/{id}/deactivate/ does).
    profile = UserProfile.objects.filter(user=user).only('pk').first()
    create_audit_log(
        user=actor, action=action, resource_type='EmployeeSeparation', resource_id=profile.pk if profile else None,
        resource_repr=user.get_full_name() or user.email or str(user.pk), metadata={'user_id': user.pk, **metadata},
        ip_address=request.META.get('REMOTE_ADDR') if request else None,
        user_agent=request.META.get('HTTP_USER_AGENT', '') if request else '',
    )


def _disable_login(user, actor, request=None, *, separation_type=''):
    """Same effect as POST /rbac/users/{id}/deactivate/, plus the separation reason."""
    from apps.hr_core.services import EmployeeService
    if not user.is_active:
        return False
    user.is_active = False
    user.save(update_fields=['is_active'])
    profile = UserProfile.objects.filter(user=user).first()
    if profile:
        profile.status = 'inactive'
        profile.save(update_fields=['status', 'updated_at'])
        EmployeeService.sync_from_rbac_profile(profile, {'status', 'is_active'})
    _audit(actor, 'update', user, {'status_change': 'active -> inactive', 'separation_type': separation_type}, request)
    return True


def _mark_employee(employee, *, status, exit_date):
    """Set the HR record after any profile sync (which would otherwise say 'suspended')."""
    if not employee:
        return
    EmployeeMaster.objects.filter(pk=employee.pk).update(employment_status=status, exit_date=exit_date,
                                                         updated_at=timezone.now())


def _hr_recipients(exclude=None):
    """Active HR / admin users who manage the employee lifecycle."""
    User = get_user_model()
    role_users = UserRole.objects.filter(
        role__code__in=LIFECYCLE_HR_ROLES | LIFECYCLE_ADMIN_ROLES, role__is_active=True,
        user_profile__is_deleted=False, user_profile__user__is_active=True,
    ).values_list('user_profile__user_id', flat=True)
    recipients = User.objects.filter(pk__in=list(role_users), is_active=True)
    if exclude is not None:
        recipients = recipients.exclude(pk=exclude.pk)
    return list(recipients)


def _notify(record_id, *, title, message, event, actor_id=None):
    record = OffboardingRecord.objects.filter(pk=record_id).first()
    if record is None:
        return
    actor = get_user_model().objects.filter(pk=actor_id).first() if actor_id else None
    for recipient in _hr_recipients():
        try:
            NotificationService.create_notification(
                recipient=recipient, sender=actor, title=title, message=message,
                category='APPROVAL', priority='HIGH',
                action_url=f'/hr/onboarding?tab=offboarding&record_id={record.id}',
                action_label='Open offboarding',
                metadata={'offboarding_id': record.id, 'event': event, 'requires_action': True},
            )
        except Exception:  # a notification failure must never undo the separation
            logger.exception('Separation notification failed for offboarding %s', record.id)


def _case_values(user, employee, profile):
    name = ' '.join(filter(None, [getattr(employee, 'first_name', ''), getattr(employee, 'last_name', '')])).strip()
    return {
        'employee_name': name or user.get_full_name() or user.username,
        'employee_email': (getattr(employee, 'email', '') or user.email or f'{user.pk}@unknown.invalid'),
        'employee_id': (getattr(employee, 'employee_number', '') or getattr(profile, 'employee_id', '') or str(user.pk)),
        'position': (getattr(employee, 'job_title_uae', '') or getattr(employee, 'designation', '')
                     or getattr(profile, 'job_title', '') or 'Not recorded'),
        'department': (getattr(employee, 'department', '') or getattr(profile, 'department', '') or 'Not recorded'),
        'branch': (getattr(employee, 'branch', '') or 'RAD'),
    }


@transaction.atomic
def separate_employee(*, actor, user, separation_type, last_working_day, reason='', deactivate_now=None, request=None):
    """Record a resignation or termination and align account, HR record and offboarding.

    ``deactivate_now`` defaults to True for terminations and False for resignations
    (the login then closes when offboarding completes). Returns (record, created, deactivated).
    """
    if separation_type not in SEPARATION_TYPES:
        raise ValidationError({'separation_type': 'Choose resignation or termination.'})
    if user.pk == actor.pk:
        raise ValidationError({'user': 'You cannot deactivate your own account.'})
    if user.is_superuser and not actor.is_superuser:
        raise ValidationError({'user': 'Only a super administrator can deactivate another super administrator.'})
    reason = (reason or '').strip()
    if len(reason) > REASON_MAX_LENGTH:
        raise ValidationError({'reason': f'Keep the reason under {REASON_MAX_LENGTH} characters.'})
    if separation_type == 'termination' and not reason:
        raise ValidationError({'reason': 'A termination needs a recorded reason.'})
    today = timezone.localdate()
    if last_working_day is None:
        raise ValidationError({'last_working_day': 'Enter the last working day.'})
    if last_working_day < today - timedelta(days=365):
        raise ValidationError({'last_working_day': 'The last working day is more than a year in the past.'})
    if deactivate_now is None:
        deactivate_now = separation_type == 'termination'

    user = get_user_model().objects.select_for_update().get(pk=user.pk)
    employee = EmployeeMaster.objects.filter(user=user).first()
    profile = UserProfile.objects.filter(user=user).first()

    record = open_offboarding_case(user)
    created = record is None
    detail = f'{SEPARATION_TYPES[separation_type]}: {reason}' if reason else SEPARATION_TYPES[separation_type]
    if created:
        record = OffboardingRecord.objects.create(
            user=user, canonical_employee=employee, created_by=actor,
            exit_reason=separation_type, exit_reason_detail=reason or None,
            last_working_day=last_working_day, target_completion_date=max(last_working_day, today),
            notice_period_days=max(0, (last_working_day - today).days),
            project_manager_approval_status='not_required', notes=detail,
            **_case_values(user, employee, profile),
        )
    else:
        record.exit_reason = separation_type
        record.exit_reason_detail = reason or record.exit_reason_detail
        record.last_working_day = last_working_day
        record.target_completion_date = max(last_working_day, record.target_completion_date or today)
        record.notes = '\n'.join(filter(None, [record.notes, detail]))
        record.save(update_fields=['exit_reason', 'exit_reason_detail', 'last_working_day',
                                   'target_completion_date', 'notes', 'updated_at'])

    deactivated = _disable_login(user, actor, request, separation_type=separation_type) if deactivate_now else False
    _mark_employee(employee, status='exited' if deactivate_now else 'notice_period', exit_date=last_working_day)
    _audit(actor, 'create' if created else 'update', user, {
        'event': 'separation_recorded', 'separation_type': separation_type,
        'last_working_day': str(last_working_day), 'deactivated_now': bool(deactivate_now),
        'offboarding_id': record.id, 'offboarding_created': created,
    }, request)

    label = SEPARATION_TYPES[separation_type]
    when = 'Account deactivated now.' if deactivate_now else 'Account stays active until offboarding is completed.'
    transaction.on_commit(lambda: _notify(
        record.id, actor_id=actor.pk, event='employee_separation',
        title=f'{label}: {record.employee_name}',
        message=f'{record.employee_name} ({record.department}) · last working day {last_working_day:%d %b %Y}. {when}',
    ), robust=True)
    return record, created, deactivated


def close_separated_account(record, *, actor, request=None):
    """When offboarding completes: disable the login and mark the HR record Exited."""
    user = record.user
    if user is None:
        return False
    deactivated = _disable_login(user, actor, request, separation_type=record.exit_reason)
    employee = EmployeeMaster.objects.filter(user=user).first()
    _mark_employee(employee, status='exited', exit_date=record.last_working_day)
    if deactivated:
        transaction.on_commit(lambda: _notify(
            record.id, actor_id=actor.pk, event='offboarding_account_closed',
            title=f'Account deactivated: {record.employee_name}',
            message=f'Offboarding for {record.employee_name} is complete; the account is now deactivated.',
        ), robust=True)
    return deactivated
