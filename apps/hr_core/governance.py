"""Shared privacy and tamper-evident audit helpers for HR operations."""
import hashlib
import json

from django.db import transaction

from .models import EmployeeMaster, HRAuditEvent


SENSITIVE_KEYS = {'password', 'secret', 'token', 'authorization', 'bank_account_number', 'iban', 'tax_id', 'pan_number'}


def role_codes(user):
    if not user or not user.is_authenticated:
        return set()
    if user.is_superuser:
        return {'super_admin'}
    try:
        return set(user.rbac_profile.roles.filter(is_active=True).values_list('code', flat=True))
    except Exception:
        return set()


def is_hr(user):
    return bool(user and (user.is_staff or user.is_superuser or role_codes(user) & {
        'hr_manager', 'hr_admin', 'human_resource', 'admin', 'super_admin',
    }))


def employee_for_user(user):
    try:
        return user.employee_master
    except (AttributeError, EmployeeMaster.DoesNotExist):
        return None


def is_manager(user):
    employee = employee_for_user(user)
    return bool(employee and employee.direct_reports.exists())


def client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '') if request else ''
    return (forwarded.split(',')[0].strip() if forwarded else request.META.get('REMOTE_ADDR')) if request else None


def _redact(value):
    if isinstance(value, dict):
        return {str(k): ('[REDACTED]' if str(k).lower() in SENSITIVE_KEYS else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def audit(*, actor, action, object_type='', object_id='', employee=None, outcome='success', metadata=None, request=None):
    """Append an event whose hash includes the previous HR audit event hash."""
    safe_metadata = _redact(metadata or {})
    with transaction.atomic():
        previous = HRAuditEvent.objects.select_for_update().order_by('-created_at').first()
        previous_hash = previous.event_hash if previous else ''
        canonical = json.dumps({
            'actor': str(getattr(actor, 'pk', '') or ''), 'action': action,
            'object_type': object_type, 'object_id': str(object_id or ''),
            'employee': str(getattr(employee, 'pk', '') or ''), 'outcome': outcome,
            'metadata': safe_metadata, 'previous_hash': previous_hash,
        }, sort_keys=True, separators=(',', ':'), default=str)
        event_hash = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
        return HRAuditEvent.objects.create(
            actor=actor if getattr(actor, 'is_authenticated', False) else None,
            action=action, object_type=object_type, object_id=str(object_id or ''),
            employee=employee, outcome=outcome, metadata=safe_metadata,
            ip_address=client_ip(request), previous_hash=previous_hash, event_hash=event_hash,
        )
