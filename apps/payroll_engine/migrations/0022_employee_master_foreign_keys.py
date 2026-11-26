import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Count


def copy_employee_relationships(apps, schema_editor):
    Payslip = apps.get_model('payroll_engine', 'Payslip')
    Adjustment = apps.get_model('payroll_engine', 'PayrollAdjustment')
    ComparisonRow = apps.get_model('payroll_engine', 'PayrollComparisonRow')
    Archive = apps.get_model('payroll_engine', 'PayrollConsolidationArchive')
    alias = schema_editor.connection.alias

    for Model, source_name, target_name in (
        (Payslip, 'employee', 'employee_master'),
        (Adjustment, 'employee', 'employee_master'),
        (ComparisonRow, 'payroll_employee', 'payroll_employee_master'),
    ):
        updates = []
        queryset = Model.objects.using(alias).select_related(source_name)
        for row in queryset.iterator():
            source = getattr(row, source_name)
            if source is None:
                continue
            if source.employee_id is None:
                raise RuntimeError(
                    f'Payroll consolidation blocked: {Model.__name__} {row.pk} '
                    'references a payroll employee without EmployeeMaster.'
                )
            setattr(row, f'{target_name}_id', source.employee_id)
            updates.append(row)
        if updates:
            Model.objects.using(alias).bulk_update(updates, [target_name], batch_size=500)

    if Payslip.objects.using(alias).filter(employee_master_id__isnull=True).exists():
        raise RuntimeError('Payroll consolidation blocked: a payslip mapping is missing.')
    if Adjustment.objects.using(alias).filter(employee_master_id__isnull=True).exists():
        raise RuntimeError('Payroll consolidation blocked: an adjustment mapping is missing.')

    duplicate_groups = (
        Payslip.objects.using(alias).values('run_id', 'employee_master_id')
        .annotate(total=Count('id')).filter(total__gt=1)
    )
    for group in duplicate_groups.iterator():
        slips = list(Payslip.objects.using(alias).filter(
            run_id=group['run_id'], employee_master_id=group['employee_master_id'],
        ).order_by('-net_payable', '-gross_earnings', 'pk'))
        for duplicate in slips[1:]:
            snapshot = {
                field.name: str(getattr(duplicate, field.name))
                if getattr(duplicate, field.name) is not None else None
                for field in duplicate._meta.concrete_fields
                if field.name not in {'employee_master'}
            }
            snapshot['line_items'] = [{
                **item, 'amount': str(item['amount']),
            } for item in duplicate.line_items.values(
                'kind', 'component_code', 'label', 'description', 'amount', 'source',
            )]
            Archive.objects.using(alias).create(
                run_id=duplicate.run_id,
                employee_id=duplicate.employee_master_id,
                legacy_payslip_id=duplicate.pk,
                snapshot=snapshot,
            )
            duplicate.delete()


class Migration(migrations.Migration):
    dependencies = [
        ('hr_core', '0025_copy_payroll_employee_attributes'),
        ('payroll_engine', '0021_reconcile_directory_payroll_roster'),
    ]
    operations = [
        migrations.CreateModel(
            name='PayrollConsolidationArchive',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('legacy_payslip_id', models.BigIntegerField()),
                ('snapshot', models.JSONField(default=dict)),
                ('archived_at', models.DateTimeField(auto_now_add=True)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                               related_name='+', to='hr_core.employeemaster')),
                ('run', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                          related_name='consolidation_archives', to='payroll_engine.payrollrun')),
            ],
            options={'db_table': 'payroll_engine_consolidation_archive', 'ordering': ('-archived_at',)},
        ),
        migrations.AddField(
            model_name='payslip', name='employee_master',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name='payroll_payslips', to='hr_core.employeemaster'),
        ),
        migrations.AddField(
            model_name='payrolladjustment', name='employee_master',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE,
                                    related_name='payroll_adjustments', to='hr_core.employeemaster'),
        ),
        migrations.AddField(
            model_name='payrollcomparisonrow', name='payroll_employee_master',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='+', to='hr_core.employeemaster'),
        ),
    ]
