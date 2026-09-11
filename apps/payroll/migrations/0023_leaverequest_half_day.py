from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('payroll', '0022_leaverequest_canonical_employee_and_more')]
    operations = [migrations.AddField(model_name='leaverequest', name='half_day', field=models.BooleanField(default=False))]
