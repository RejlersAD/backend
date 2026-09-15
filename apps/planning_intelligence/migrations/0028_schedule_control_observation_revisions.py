import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('planning_intelligence', '0027_controlled_proposal_workflow')]

    operations = [
        migrations.AddField(
            model_name='schedulecontrolsnapshot', name='revision',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AlterUniqueTogether(
            name='schedulecontrolsnapshot',
            unique_together={('version', 'data_date', 'revision')},
        ),
        migrations.AlterModelOptions(
            name='schedulecontrolsnapshot',
            options={'ordering': ['-data_date', '-revision', '-created_at', '-id']},
        ),
        migrations.AlterField(
            model_name='schedulecontrolsnapshot', name='version',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='control_snapshots',
                to='planning_intelligence.scheduleversion',
            ),
        ),
        migrations.AddConstraint(
            model_name='schedulecontrolsnapshot',
            constraint=models.CheckConstraint(
                check=models.Q(revision__gte=1), name='plan_ctrl_revision_positive',
            ),
        ),
    ]
