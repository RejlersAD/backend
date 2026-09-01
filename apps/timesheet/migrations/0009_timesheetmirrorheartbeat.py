from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('timesheet', '0008_repair_dailyattendancesummary_schema')]

    operations = [
        migrations.CreateModel(
            name='TimesheetMirrorHeartbeat',
            fields=[
                ('key', models.CharField(default='default', max_length=32, primary_key=True, serialize=False)),
                ('last_seen_at', models.DateTimeField()),
                ('last_event_time', models.DateTimeField(blank=True, null=True)),
            ],
        ),
    ]
