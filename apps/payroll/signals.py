from django.db.models import Sum
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from .models import LeaveRequest, EmployeeLeaveRecord
from django.utils import timezone


def _recalculate_total_taken(employee_code, year):
    """Recompute total_taken from scratch — sum of every currently-APPROVED
    LeaveRequest for this employee/year — instead of incrementing/decrementing
    a running total. Idempotent regardless of how many times a signal fires.

    Must run post_save/post_delete, not pre_save: this queries the DB for the
    current APPROVED set, so it needs the triggering row's own change already
    committed, or the aggregate lags one save behind.
    """
    try:
        record = EmployeeLeaveRecord.objects.get(employee_code=employee_code, year=year)
    except EmployeeLeaveRecord.DoesNotExist:
        return

    total = LeaveRequest.objects.filter(
        employee_code=employee_code,
        status='APPROVED',
        start_date__year=year,
    ).aggregate(t=Sum('days_requested'))['t'] or 0

    if record.total_taken == total:
        return  # avoid a redundant save when nothing actually changed

    record.total_taken = total
    record.leave_balance = record.total_earned - record.total_taken - record.total_encashed + record.carryforward
    record.save(update_fields=['total_taken', 'leave_balance'])


@receiver(post_save, sender=LeaveRequest)
def update_leave_taken_on_approval(sender, instance, **kwargs):
    year = instance.start_date.year if instance.start_date else timezone.now().year
    _recalculate_total_taken(instance.employee_code, year)


@receiver(post_delete, sender=LeaveRequest)
def update_leave_taken_on_delete(sender, instance, **kwargs):
    if instance.status != 'APPROVED':
        return
    year = instance.start_date.year if instance.start_date else timezone.now().year
    _recalculate_total_taken(instance.employee_code, year)
