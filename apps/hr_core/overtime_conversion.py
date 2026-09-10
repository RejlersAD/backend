"""Apply an approved OT benefit once, preserving its approval and source hours."""
import uuid
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from .models import EmployeeMaster, OvertimeRequest, OvertimeConversion, OvertimeConversionAllocation
from .overtime import is_final_reviewer


@transaction.atomic
def apply_benefit(request_id, actor, data):
    if not is_final_reviewer(actor):
        raise PermissionDenied('HR or Finance approval access is required.')
    employee_id = OvertimeRequest.objects.get(pk=request_id).employee_id
    employee = EmployeeMaster.objects.select_for_update().get(pk=employee_id)
    ot = OvertimeRequest.objects.select_for_update().get(pk=request_id)
    if ot.status != 'approved' or not ot.approved_hours or ot.approved_hours <= 0:
        raise ValidationError('Only approved overtime hours can be applied.')
    if ot.conversion_allocations.exists():
        return ot  # Safe retry: never issue the benefit twice.
    from .overtime_attendance import validate_request_attendance
    validate_request_attendance(ot)
    method = data.get('type') or ot.compensation_type
    if method not in {'cash', 'day_off'}:
        raise ValidationError('Select Encashment or Off day.')
    if ot.compensation_type and method != ot.compensation_type:
        raise ValidationError('Use the benefit type requested by the employee.')
    try:
        year, month = int(data.get('year')), int(data.get('month'))
        if not 2000 <= year <= 2100 or not 1 <= month <= 12:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValidationError('Select a valid year and month.')
    values = dict(employee=employee, method=method, hours=ot.approved_hours,
                  converted_by=actor, idempotency_key=uuid.uuid4(), target_year=year, target_month=month)
    if method == 'cash':
        from apps.payroll_engine.services.adjustment_period import require_current_or_future
        require_current_or_future(year, month)
        from apps.payroll_engine.models import PayrollEmployee, PayrollAdjustment, PayrollRun, PayslipLineItem
        from apps.payroll_engine import catalog
        from apps.payroll_engine.services.calculator import recompute_payslip_totals, recompute_run_totals
        try:
            multiplier = Decimal(str(data.get('multiplier')))
        except (InvalidOperation, ValueError, TypeError):
            raise ValidationError('Select 1.25 or 1.50 as the OT multiplier.')
        if multiplier not in {Decimal('1.25'), Decimal('1.50')}:
            raise ValidationError('Select 1.25 or 1.50 as the OT multiplier.')
        payroll = list(PayrollEmployee.objects.select_for_update().filter(employee=employee, is_active=True))
        if len(payroll) != 1 or payroll[0].basic <= 0:
            raise ValidationError('A unique active payroll employee with a basic salary is required.')
        payroll = payroll[0]
        run = PayrollRun.objects.select_for_update().filter(year=year, month=month).first()
        if run and run.status != catalog.Status.DRAFT:
            raise ValidationError('Select a draft payroll month. This run has already entered approval.')
        slip = run.payslips.select_for_update().filter(employee=payroll).first() if run else None
        if run and not slip:
            raise ValidationError('This employee is not included in the selected payroll run.')
        rate = payroll.basic / Decimal('30') / Decimal('8')
        amount = (rate * ot.approved_hours * multiplier).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        adjustment = PayrollAdjustment.objects.create(employee=payroll, target_year=year, target_month=month,
            kind=catalog.LineItemKind.EARNING, component_code='overtime', label='Overtime',
            description=f'OT {ot.pk}: {ot.approved_hours} hours x {multiplier}', amount=amount, created_by=actor)
        if slip:
            PayslipLineItem.objects.create(payslip=slip, kind=adjustment.kind, component_code='overtime',
                label='Overtime', description=adjustment.description, amount=amount, source=catalog.LineItemSource.ADJUSTMENT)
            adjustment.status = catalog.AdjustmentStatus.APPLIED
            adjustment.applied_to = slip
            adjustment.applied_at = timezone.now()
            adjustment.save()
            recompute_payslip_totals(slip)
            slip.save()
            recompute_run_totals(run)
            run.save()
        values.update(basic_salary=payroll.basic, multiplier=multiplier,
                      hourly_rate=rate.quantize(Decimal('.01')), cash_amount=amount, payroll_adjustment=adjustment)
    else:
        from apps.payroll.models import EmployeeLeaveRecord
        record = EmployeeLeaveRecord.objects.select_for_update().filter(employee_code__iexact=employee.employee_code, year=year).first()
        if not record:
            raise ValidationError('An annual leave record is required for the selected year.')
        days = (ot.approved_hours / Decimal('8')).quantize(Decimal('.0001'))
        # Existing accrual calculations preserve carryforward as an adjustment balance.
        record.carryforward += days
        record.leave_balance += days
        record.save(update_fields=['carryforward', 'leave_balance'])
        from django.db.models import F
        record.monthly_breakdown.update(balance=F('balance') + days)
        values.update(hours_per_day=Decimal('8'), days_credited=days)
    conversion = OvertimeConversion.objects.create(**values)
    OvertimeConversionAllocation.objects.create(conversion=conversion, request=ot, hours=ot.approved_hours)
    ot.compensation_type = method
    ot.save(update_fields=['compensation_type', 'updated_at'])
    return ot
