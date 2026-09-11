"""Approved leave consumption shared by downstream payroll calculations."""
import calendar
from datetime import date, timedelta
from decimal import Decimal


def monthly_payroll_leave(year, month):
    from apps.payroll.models import LeaveRequest
    from apps.payroll_engine.catalog import LEAVE_CATEGORIES_FOR_PAYROLL
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    result = {}
    requests = LeaveRequest.objects.filter(
        status='APPROVED', start_date__lte=last, end_date__gte=first,
        leave_type__category__in=LEAVE_CATEGORIES_FOR_PAYROLL.values(),
    ).select_related('leave_type')
    for request in requests:
        if not request.employee_code:
            continue
        days = Decimal('0')
        current = max(first, request.start_date)
        while current <= min(last, request.end_date):
            if current.weekday() < 5:
                days += Decimal('0.5') if request.half_day else Decimal('1')
            current += timedelta(days=1)
        row = result.setdefault(request.employee_code, {})
        for field, category in LEAVE_CATEGORIES_FOR_PAYROLL.items():
            if category == request.leave_type.category:
                row[field] = row.get(field, Decimal('0')) + days
    return result
