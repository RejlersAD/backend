from django.db import migrations
from django.db.models import Count


def copy_payroll_attributes(apps, schema_editor):
    EmployeeMaster = apps.get_model('hr_core', 'EmployeeMaster')
    PayrollEmployee = apps.get_model('payroll_engine', 'PayrollEmployee')
    alias = schema_editor.connection.alias

    unresolved = PayrollEmployee.objects.using(alias).filter(employee_id__isnull=True).count()
    if unresolved:
        raise RuntimeError(
            f'Payroll EmployeeMaster consolidation blocked: {unresolved} payroll row(s) '
            'have no canonical EmployeeMaster.'
        )

    duplicate_ids = list(
        PayrollEmployee.objects.using(alias).filter(is_active=True)
        .values('employee_id').annotate(total=Count('id'))
        .filter(total__gt=1).values_list('employee_id', flat=True)
    )
    if duplicate_ids:
        raise RuntimeError(
            'Payroll EmployeeMaster consolidation blocked: multiple active payroll rows '
            'map to one canonical employee.'
        )

    for source in PayrollEmployee.objects.using(alias).select_related('employee').iterator():
        target = source.employee
        target.payroll_emirates_id = source.emirates_id or ''
        target.payroll_full_name = source.full_name or f'{target.first_name} {target.last_name}'.strip()
        target.payroll_mol_no = source.mol_no or ''
        target.payroll_routing_code = source.routing_code or ''
        target.payroll_discipline = source.discipline or ''
        target.payroll_grade = source.grade or ''
        target.payroll_nationality_group = source.nationality_group or ''
        target.payroll_hours = source.hours
        target.payroll_basic = source.basic
        target.payroll_housing = source.housing
        target.payroll_transport = source.transport
        target.payroll_home_leave = source.home_leave
        target.payroll_payment_mode = source.default_payment_mode
        target.payroll_enabled = source.is_active
        target.payroll_effective_from = source.effective_from
        target.payroll_effective_to = source.effective_to
        target.payroll_notes = source.notes or ''
        target.iban = source.iban or target.iban
        target.bank_name = source.bank_name or target.bank_name
        target.department = source.department or target.department
        target.designation = source.designation or target.designation
        target.save(using=alias, update_fields=[
            'payroll_emirates_id', 'payroll_full_name', 'payroll_mol_no', 'payroll_routing_code',
            'payroll_discipline', 'payroll_grade', 'payroll_nationality_group',
            'payroll_hours', 'payroll_basic', 'payroll_housing', 'payroll_transport',
            'payroll_home_leave', 'payroll_payment_mode', 'payroll_enabled',
            'payroll_effective_from', 'payroll_effective_to', 'payroll_notes',
            'iban', 'bank_name', 'department', 'designation', 'updated_at',
        ])


class Migration(migrations.Migration):
    dependencies = [
        ('hr_core', '0024_employeemaster_payroll_basic_and_more'),
        ('payroll_engine', '0021_reconcile_directory_payroll_roster'),
    ]
    operations = [
        migrations.RunPython(copy_payroll_attributes, migrations.RunPython.noop),
    ]
