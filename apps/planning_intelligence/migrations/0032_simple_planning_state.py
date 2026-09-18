from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('planning_intelligence', '0031_project_setup_ai_settings')]
    operations = [migrations.AddField(
        model_name='planningproject', name='simple_planning_state',
        field=models.JSONField(blank=True, default=dict),
    )]
