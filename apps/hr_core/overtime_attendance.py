"""Recorded overtime eligibility, using the Timesheet daily summary table."""
from decimal import Decimal, ROUND_DOWN
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from apps.timesheet import config as ts_config
from apps.timesheet.identity import norm_code
from apps.timesheet.models import DailyAttendanceSummary, BiometricUserMaster
from .models import OvertimeRequest


def recorded_days(employee):
    codes = {norm_code(employee.employee_code)}
    if employee.user_id:
        profile = getattr(employee.user, 'rbac_profile', None)
        if profile and profile.employee_id:
            codes.add(norm_code(profile.employee_id))
        if employee.user.email:
            codes.update(BiometricUserMaster.objects.filter(
                Q(office_email__iexact=employee.user.email) | Q(personal_email__iexact=employee.user.email)
            ).values_list('employee_code', flat=True))
    codes.discard('')
    rows = DailyAttendanceSummary.objects.filter(employee_code__in=codes, date__lte=timezone.localdate()).order_by('date', 'computed_at')
    mode = ts_config.INPUT_MODE
    if mode == 'manual':
        rows = rows.filter(source='manual')
    elif mode != 'hybrid':
        rows = rows.filter(source='biometric')
    days = {}
    for row in rows:
        previous = days.get(row.date)
        if previous and previous.source == 'biometric' and row.source != 'biometric':
            continue
        days[row.date] = row
    result = []
    for day, row in sorted(days.items(), reverse=True):
        if row.open_shift:
            continue
        hours = Decimal(str(row.overtime_hours or 0))
        if not hours.is_finite() or hours <= 0:
            continue
        result.append({'date': day.isoformat(), 'overtime_hours': str(hours.quantize(Decimal('.01'), rounding=ROUND_DOWN)),
                       'worked_hours': str(Decimal(str(row.effective_hours or 0)) + hours)})
    return result


def claimed_dates(employee):
    claimed = set()
    for request in OvertimeRequest.objects.filter(employee=employee, status__in=['pending', 'approved']):
        claimed.update(d['work_date'] for d in request.day_entries) if request.day_entries else claimed.add(request.work_date.isoformat())
    return claimed


def eligible_days(employee):
    claimed = claimed_dates(employee)
    return [row for row in recorded_days(employee) if row['date'] not in claimed]


def validate_request_attendance(request):
    for entry in request.day_entries or [{'work_date': str(request.work_date), 'requested_hours': str(request.approved_hours or request.requested_hours)}]:
        validate_recorded_hours(request.employee, entry['work_date'], entry['requested_hours'])


def validate_recorded_hours(employee, day, hours):
    record = next((r for r in recorded_days(employee) if r['date'] == str(day)), None)
    if not record:
        raise ValidationError({'work_date': 'Select a completed day with recorded overtime in Timesheet.'})
    if Decimal(str(hours)) > Decimal(record['overtime_hours']):
        raise ValidationError({'requested_hours': f"Only {record['overtime_hours']} overtime hours are recorded for this day."})
