from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('planning_intelligence', '0015_schedule_default_proposal'),
    ]

    operations = [
        migrations.AddField(
            model_name='planningproject',
            name='planned_end_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='planningproject',
            name='duration_months',
            field=models.DecimalField(decimal_places=4, default=10, max_digits=8),
        ),
    ]
