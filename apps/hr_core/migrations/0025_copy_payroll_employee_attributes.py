import json

from django.db import migrations
from django.db.models import Count


def copy_payroll_attributes(apps, schema_editor):
    EmployeeMaster = apps.get_model('hr_core', 'EmployeeMaster')
    PayrollEmployee = apps.get_model('payroll_engine', 'PayrollEmployee')
    alias = schema_editor.connection.alias

    Payslip = apps.get_model('payroll_engine', 'Payslip')
    Adjustment = apps.get_model('payroll_engine', 'PayrollAdjustment')

    # The approved authority rule is strict: an employee absent from the
    # Employee Directory must not survive in payroll. Preserve a complete JSON
    # audit snapshot before removing orphan payroll data.
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('''
            CREATE TABLE IF NOT EXISTS payroll_engine_orphan_archive (
                id BIGSERIAL PRIMARY KEY,
                employee_no VARCHAR(50) NOT NULL,
                snapshot JSONB NOT NULL,
                archived_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        ''')
    else:
        schema_editor.execute('''
            CREATE TABLE IF NOT EXISTS payroll_engine_orphan_archive (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_no VARCHAR(50) NOT NULL,
                snapshot TEXT NOT NULL,
                archived_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        ''')

    for orphan in PayrollEmployee.objects.using(alias).filter(employee_id__isnull=True).iterator():
        payslips = list(Payslip.objects.using(alias).filter(employee_id=orphan.pk).values())
        for payslip in payslips:
            payslip['line_items'] = list(
                apps.get_model('payroll_engine', 'PayslipLineItem').objects.using(alias)
                .filter(payslip_id=payslip['id']).values()
            )
        snapshot = json.dumps({
            'payroll_employee': {
                field.name: getattr(orphan, field.name)
                for field in orphan._meta.concrete_fields
            },
            'payslips': payslips,
            'adjustments': list(
                Adjustment.objects.using(alias).filter(employee_id=orphan.pk).values()
            ),
            'reason': 'No canonical EmployeeMaster matched during consolidation',
        }, default=str)
        with schema_editor.connection.cursor() as cursor:
            if schema_editor.connection.vendor == 'postgresql':
                cursor.execute(
                    'INSERT INTO payroll_engine_orphan_archive (employee_no, snapshot) '
                    'VALUES (%s, %s::jsonb)', [orphan.employee_no, snapshot],
                )
            else:
                cursor.execute(
                    'INSERT INTO payroll_engine_orphan_archive (employee_no, snapshot) '
                    'VALUES (%s, %s)', [orphan.employee_no, snapshot],
                )
        Payslip.objects.using(alias).filter(employee_id=orphan.pk).delete()
        Adjustment.objects.using(alias).filter(employee_id=orphan.pk).delete()
        orphan.delete()

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
