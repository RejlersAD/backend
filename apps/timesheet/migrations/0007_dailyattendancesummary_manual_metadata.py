from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('timesheet', '0006_dailyattendancesummary_source')]

    operations = [
        migrations.AddField(model_name='dailyattendancesummary', name='employee_name', field=models.CharField(blank=True, default='', max_length=255)),
        migrations.AddField(model_name='dailyattendancesummary', name='department', field=models.CharField(blank=True, default='', max_length=255)),
        migrations.AddField(model_name='dailyattendancesummary', name='attendance_status', field=models.CharField(blank=True, default='present', max_length=32)),
        migrations.AddField(model_name='dailyattendancesummary', name='time_in', field=models.TimeField(blank=True, null=True)),
        migrations.AddField(model_name='dailyattendancesummary', name='time_out', field=models.TimeField(blank=True, null=True)),
        migrations.AddField(model_name='dailyattendancesummary', name='overtime_hours', field=models.FloatField(default=0.0)),
    ]
