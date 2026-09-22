from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('planning_intelligence', '0038_planning_assertion_types')]

    operations = [
        migrations.AddField(
            model_name='planningproject', name='master_schedule_version',
            field=models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name='+', to='planning_intelligence.scheduleversion'),
        ),
        migrations.AddField(
            model_name='planningproject', name='master_schedule_revision',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
