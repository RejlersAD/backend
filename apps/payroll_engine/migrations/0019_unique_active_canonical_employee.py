from django.db import migrations, models
from django.db.models import Count


def deactivate_duplicate_active_identities(apps, schema_editor):
    """Make the additive constraint safe for historical production data.

    Payslips and all other history remain attached to their original payroll
    records. Only duplicate records are made inactive so future runs cannot
    materialise the same canonical person more than once.
    """
    PayrollEmployee = apps.get_model('payroll_engine', 'PayrollEmployee')
    EmployeeMaster = apps.get_model('hr_core', 'EmployeeMaster')
    duplicate_ids = list(
        PayrollEmployee.objects.using(schema_editor.connection.alias)
        .filter(is_active=True, employee_id__isnull=False)
        .values('employee_id')
        .annotate(total=Count('id'))
        .filter(total__gt=1)
        .values_list('employee_id', flat=True)
    )

    for employee_id in duplicate_ids:
        records = list(
            PayrollEmployee.objects.using(schema_editor.connection.alias)
            .filter(is_active=True, employee_id=employee_id)
            .order_by('id')
        )
        canonical_number = (
            EmployeeMaster.objects.using(schema_editor.connection.alias)
            .filter(pk=employee_id)
            .values_list('employee_number', flat=True)
            .first()
            or ''
        ).strip().casefold()
        matching = [
            record for record in records
            if (record.employee_no or '').strip().casefold() == canonical_number
        ]
        survivor = matching[0] if len(matching) == 1 else records[0]
        PayrollEmployee.objects.using(schema_editor.connection.alias).filter(
            pk__in=[record.pk for record in records if record.pk != survivor.pk],
        ).update(is_active=False)


class Migration(migrations.Migration):
    dependencies = [
        ('payroll_engine', '0018_alter_payrollemployee_employee'),
    ]

    operations = [
        migrations.RunPython(
            deactivate_duplicate_active_identities,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name='payrollemployee',
            constraint=models.UniqueConstraint(
                fields=('employee',),
                condition=models.Q(employee__isnull=False, is_active=True),
                name='uq_active_payroll_employee_canonical',
            ),
        ),
    ]
