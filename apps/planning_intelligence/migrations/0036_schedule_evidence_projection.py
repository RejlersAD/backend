import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('planning_intelligence', '0035_evidence_immutability')]
    operations = [
        migrations.AddField(model_name='scheduleversion', name='evidence_graph',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name='schedule_projections', to='planning_intelligence.evidencegraph')),
        migrations.AddField(model_name='scheduleversion', name='evidence_graph_revision', field=models.PositiveIntegerField(blank=True, null=True)),
        migrations.AddField(model_name='scheduleversion', name='evidence_input_snapshot', field=models.JSONField(blank=True, default=dict)),
    ]
