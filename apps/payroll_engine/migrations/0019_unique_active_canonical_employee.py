from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('payroll_engine', '0018_alter_payrollemployee_employee'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='payrollemployee',
            constraint=models.UniqueConstraint(
                fields=('employee',),
                condition=models.Q(employee__isnull=False, is_active=True),
                name='uq_active_payroll_employee_canonical',
            ),
        ),
    ]
