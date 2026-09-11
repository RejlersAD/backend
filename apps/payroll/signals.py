"""Apply request deltas without replacing imported leave history."""
from decimal import Decimal
from django.db.models import F
from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver
from .models import LeaveRequest, EmployeeLeaveRecord


def contribution(request):
    if not request or request.status != 'APPROVED' or request.leave_type.category != 'annual':
        return {}
    from .services.leave_approval import working_days_by_year
    return {(request.employee_code, year): days for year, days in working_days_by_year(request).items()}


@receiver(pre_save, sender=LeaveRequest)
def remember_leave_contribution(sender, instance, raw=False, **kwargs):
    if raw:
        return
    previous = sender.objects.select_related('leave_type').filter(pk=instance.pk).first() if instance.pk else None
    instance._previous_leave_contribution = contribution(previous)


def apply_delta(before, after):
    for code, year in before.keys() | after.keys():
        change = after.get((code, year), Decimal('0')) - before.get((code, year), Decimal('0'))
        if change:
            EmployeeLeaveRecord.objects.filter(employee_code=code, year=year).update(
                total_taken=F('total_taken') + change,
                leave_balance=F('leave_balance') - change,
            )


@receiver(post_save, sender=LeaveRequest)
def update_leave_taken_on_approval(sender, instance, raw=False, **kwargs):
    if not raw:
        # Read persisted fields so save(update_fields=...) cannot apply unsaved changes.
        persisted = sender.objects.select_related('leave_type').get(pk=instance.pk)
        apply_delta(getattr(instance, '_previous_leave_contribution', {}), contribution(persisted))


@receiver(post_delete, sender=LeaveRequest)
def update_leave_taken_on_delete(sender, instance, **kwargs):
    apply_delta(contribution(instance), {})
