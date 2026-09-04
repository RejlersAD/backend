import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hr_core', '0007_alter_employeemaster_email_alter_employeemaster_user'),
        ('payroll_engine', '0017_payrollemployee_employee'),
    ]

    operations = [
        migrations.AlterField(
            model_name='payrollemployee',
            name='employee',
            field=models.ForeignKey(
                blank=True,
                help_text='Canonical employee identity for this payroll-domain extension.',
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='payroll_records',
                to='hr_core.employeemaster',
            ),
        ),
    ]
