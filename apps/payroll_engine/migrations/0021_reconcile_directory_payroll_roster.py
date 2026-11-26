from decimal import Decimal

from django.db import migrations


def reconcile_directory_payroll_roster(apps, schema_editor):
    """Make EmployeeMaster authoritative without deleting payroll history."""
    PayrollEmployee = apps.get_model('payroll_engine', 'PayrollEmployee')
    EmployeeMaster = apps.get_model('hr_core', 'EmployeeMaster')
    PayrollRun = apps.get_model('payroll_engine', 'PayrollRun')
    Payslip = apps.get_model('payroll_engine', 'Payslip')
    alias = schema_editor.connection.alias

    aliases = {}
    for employee in EmployeeMaster.objects.using(alias).all().iterator():
        for value in (employee.employee_number, employee.employee_code, employee.emp_code):
            key = str(value or '').strip().casefold()
            if key:
                aliases.setdefault(key, set()).add(employee.pk)

    active_owner = {
        employee_id: payroll_id
        for employee_id, payroll_id in PayrollEmployee.objects.using(alias)
        .filter(is_active=True, employee_id__isnull=False)
        .values_list('employee_id', 'pk')
    }
    for payroll in PayrollEmployee.objects.using(alias).filter(employee_id__isnull=True).order_by('pk'):
        matches = aliases.get(str(payroll.employee_no or '').strip().casefold(), set())
        employee_id = next(iter(matches)) if len(matches) == 1 else None
        if employee_id and employee_id not in active_owner:
            payroll.employee_id = employee_id
            payroll.save(update_fields=['employee'])
            if payroll.is_active:
                active_owner[employee_id] = payroll.pk
        elif payroll.is_active:
            payroll.is_active = False
            payroll.save(update_fields=['is_active'])

    invalid_employee_ids = PayrollEmployee.objects.using(alias).filter(
        employee_id__isnull=True,
    ).values_list('pk', flat=True)
    affected_runs = list(Payslip.objects.using(alias).filter(
        run__status='draft',
    ).filter(
        employee_id__in=PayrollEmployee.objects.using(alias).filter(
            is_active=False,
        ).values_list('pk', flat=True),
    ).values_list('run_id', flat=True).distinct())
    affected_runs += list(Payslip.objects.using(alias).filter(
        run__status='draft', employee_id__in=invalid_employee_ids,
    ).values_list('run_id', flat=True).distinct())

    Payslip.objects.using(alias).filter(
        run__status='draft',
        employee_id__in=PayrollEmployee.objects.using(alias).filter(
            is_active=False,
        ).values_list('pk', flat=True),
    ).delete()
    Payslip.objects.using(alias).filter(
        run__status='draft', employee_id__in=invalid_employee_ids,
    ).delete()

    zero = Decimal('0.00')
    for run in PayrollRun.objects.using(alias).filter(pk__in=set(affected_runs)):
        slips = Payslip.objects.using(alias).filter(run_id=run.pk)
        gross = deductions = net = hours = zero
        count = 0
        for slip in slips.iterator():
            gross += slip.gross_earnings or zero
            deductions += slip.total_deductions or zero
            net += slip.net_payable or zero
            hours += slip.hours or zero
            count += 1
        run.employee_count = count
        run.total_gross = gross
        run.total_deductions = deductions
        run.total_net = net
        run.total_hours = hours
        run.total_days = (hours / Decimal('9')).quantize(Decimal('0.01'))
        run.save(update_fields=[
            'employee_count', 'total_gross', 'total_deductions', 'total_net',
            'total_hours', 'total_days',
        ])


class Migration(migrations.Migration):
    dependencies = [
        ('payroll_engine', '0020_add_attendance_snapshot'),
    ]

    operations = [
        migrations.RunPython(
            reconcile_directory_payroll_roster,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
