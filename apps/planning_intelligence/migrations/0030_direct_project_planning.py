from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('planning_intelligence', '0029_planning_project_scope_inputs')]

    operations = [
        migrations.AddField(
            model_name='planningproject', name='planning_mode',
            field=models.CharField(
                max_length=16, default='document',
                choices=[('document', 'Document-led'), ('manual', 'Direct planning')],
            ),
        ),
        migrations.AddField(
            model_name='planningproject', name='manual_work_breakdown',
            field=models.JSONField(default=dict, blank=True),
        ),
    ]
