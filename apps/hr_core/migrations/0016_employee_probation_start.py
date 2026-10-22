from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('hr_core', '0015_employee_nationality')]
    operations = [migrations.AddField(model_name='employeemaster', name='probation_start_date',
        field=models.DateField(blank=True, null=True, help_text='Probation start; defaults to joining date.'))]
