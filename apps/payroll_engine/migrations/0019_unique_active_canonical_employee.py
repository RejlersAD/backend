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
    alias = schema_editor.connection.alias

    if schema_editor.connection.vendor == 'postgresql':
        payroll_table = schema_editor.quote_name(PayrollEmployee._meta.db_table)
        employee_table = schema_editor.quote_name(EmployeeMaster._meta.db_table)
        # The window ranking handles every duplicate group in one statement
        # and prefers the row
        # whose employee number agrees with the canonical EmployeeMaster.
        schema_editor.execute(f'''
            WITH ranked AS (
                SELECT payroll.id,
                       ROW_NUMBER() OVER (
                           PARTITION BY payroll.employee_id
                           ORDER BY
                               CASE WHEN LOWER(TRIM(payroll.employee_no)) =
                                         LOWER(TRIM(employee.employee_number))
                                    THEN 0 ELSE 1 END,
                               payroll.id
                       ) AS duplicate_rank
                FROM {payroll_table} AS payroll
                LEFT JOIN {employee_table} AS employee
                  ON employee.id = payroll.employee_id
                WHERE payroll.is_active = TRUE
                  AND payroll.employee_id IS NOT NULL
            )
            UPDATE {payroll_table} AS payroll
               SET is_active = FALSE
              FROM ranked
             WHERE payroll.id = ranked.id
               AND ranked.duplicate_rank > 1
        ''')
    else:
        _deactivate_duplicates_with_orm(
            PayrollEmployee, EmployeeMaster, alias,
        )

    remaining = (
        PayrollEmployee.objects.using(alias)
        .filter(is_active=True, employee_id__isnull=False)
        .values('employee_id')
        .annotate(total=Count('id'))
        .filter(total__gt=1)
        .count()
    )
    if remaining:
        raise RuntimeError(
            f'Payroll identity cleanup left {remaining} duplicate active canonical group(s); '
            'the unique constraint was not attempted.'
        )


def _deactivate_duplicates_with_orm(PayrollEmployee, EmployeeMaster, alias):
    """Portable fallback used by the isolated SQLite migration harness."""
    duplicate_ids = list(
        PayrollEmployee.objects.using(alias)
        .filter(is_active=True, employee_id__isnull=False)
        .values('employee_id')
        .annotate(total=Count('id'))
        .filter(total__gt=1)
        .values_list('employee_id', flat=True)
    )

    for employee_id in duplicate_ids:
        records = list(
            PayrollEmployee.objects.using(alias)
            .filter(is_active=True, employee_id=employee_id)
            .order_by('id')
        )
        canonical_number = (
            EmployeeMaster.objects.using(alias)
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
        PayrollEmployee.objects.using(alias).filter(
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
