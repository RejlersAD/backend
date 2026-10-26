import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def preserve_completed_progress(apps, schema_editor):
    apps.get_model('core', 'ProjectTask').objects.filter(status='completed').update(progress_percent=100)


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0011_enquiryattachment'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(model_name='projecttask', name='title', field=models.CharField(max_length=500)),
        migrations.AlterField(model_name='projecttask', name='estimated_hours', field=models.DecimalField(
            max_digits=12, decimal_places=2, null=True, blank=True,
        )),
        migrations.AddField(model_name='projecttask', name='reviewer', field=models.ForeignKey(
            to=settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.SET_NULL,
            null=True, blank=True, related_name='project_tasks_to_review',
        )),
        migrations.AddField(model_name='projecttask', name='task_type', field=models.CharField(
            max_length=20, choices=[('task', 'Task'), ('deliverable', 'Deliverable')], default='task',
        )),
        migrations.AddField(model_name='projecttask', name='progress_percent', field=models.PositiveSmallIntegerField(
            default=0, validators=[django.core.validators.MaxValueValidator(100)],
        )),
        migrations.AddField(model_name='projecttask', name='source_key', field=models.CharField(
            max_length=160, unique=True, null=True, blank=True,
        )),
        migrations.AddField(model_name='projecttask', name='metadata', field=models.JSONField(default=dict, blank=True)),
        migrations.AddConstraint(model_name='projecttask', constraint=models.CheckConstraint(
            check=models.Q(progress_percent__gte=0, progress_percent__lte=100),
            name='core_project_task_progress_range',
        )),
        migrations.RunPython(preserve_completed_progress, migrations.RunPython.noop),
    ]
