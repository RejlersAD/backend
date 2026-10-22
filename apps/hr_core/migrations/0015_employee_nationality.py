from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('hr_core', '0014_overtimerequest_day_entries')]
    operations = [migrations.AddField(model_name='employeemaster', name='nationality',
        field=models.CharField(blank=True, max_length=100, help_text='Citizenship; distinct from country of residence.'))]
