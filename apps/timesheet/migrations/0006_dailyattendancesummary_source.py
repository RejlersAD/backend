from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('timesheet', '0005_remove_dailyattendancesummary_ts_daily_sum_unique_code_date_and_more')]

    operations = [
        migrations.AddField(
            model_name='dailyattendancesummary',
            name='source',
            field=models.CharField(
                choices=[('biometric', 'Biometric'), ('manual', 'Manual upload')],
                db_index=True,
                default='biometric',
                max_length=16,
            ),
        ),
        migrations.AlterUniqueTogether(
            name='dailyattendancesummary',
            unique_together={('employee_code', 'date', 'source')},
        ),
    ]
