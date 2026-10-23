from django.db import migrations, models


def preserve_originals(apps, schema_editor):
    apps.get_model('payroll', 'LeaveRequest').objects.using(schema_editor.connection.alias).update(
        requested_start_date=models.F('start_date'), requested_end_date=models.F('end_date'), requested_days=models.F('days_requested'))


class Migration(migrations.Migration):
    dependencies = [('payroll', '0024_hr_department_leave')]
    operations = [
        migrations.AddField(model_name='leaverequest', name='requested_start_date', field=models.DateField(null=True, blank=True)),
        migrations.AddField(model_name='leaverequest', name='requested_end_date', field=models.DateField(null=True, blank=True)),
        migrations.AddField(model_name='leaverequest', name='requested_days', field=models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)),
        migrations.AddField(model_name='leaverequest', name='hr_balance_override', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='leaverequest', name='hr_override_reason', field=models.TextField(blank=True)),
        migrations.RunPython(preserve_originals, migrations.RunPython.noop),
    ]
