import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('payroll_engine', '0023_copy_employee_master_relationships')]
    operations = [
        migrations.RemoveConstraint(model_name='payslip', name='uq_payslip_run_employee'),
        migrations.RemoveField(model_name='payslip', name='employee'),
        migrations.RemoveField(model_name='payrolladjustment', name='employee'),
        migrations.RemoveField(model_name='payrollcomparisonrow', name='payroll_employee'),
        migrations.RenameField(model_name='payslip', old_name='employee_master', new_name='employee'),
        migrations.RenameField(model_name='payrolladjustment', old_name='employee_master', new_name='employee'),
        migrations.RenameField(model_name='payrollcomparisonrow', old_name='payroll_employee_master', new_name='payroll_employee'),
        migrations.AlterField(
            model_name='payslip', name='employee',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                    related_name='payroll_payslips', to='hr_core.employeemaster'),
        ),
        migrations.AlterField(
            model_name='payrolladjustment', name='employee',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                    related_name='payroll_adjustments', to='hr_core.employeemaster'),
        ),
        migrations.AddConstraint(
            model_name='payslip',
            constraint=models.UniqueConstraint(fields=('run', 'employee'), name='uq_payslip_run_employee'),
        ),
        migrations.DeleteModel(name='PayrollEmployee'),
    ]
