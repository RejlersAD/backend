from django.db import migrations, models
import django.db.models.deletion


def preserve_reviewed_links(apps, schema_editor):
    if apps.get_model('planning_intelligence', 'ScheduleResource').objects.using(schema_editor.connection.alias).filter(employee__isnull=False).exists():
        raise RuntimeError('Preserve reviewed resource employee links before reversing this migration.')


class Migration(migrations.Migration):
    dependencies = [
        ('planning_intelligence', '0048_schedulelogicreview'),
        ('hr_core', '0014_overtimerequest_day_entries'),
    ]

    operations = [migrations.AddField(
        model_name='scheduleresource', name='employee',
        field=models.ForeignKey(
            to='hr_core.employeemaster', on_delete=django.db.models.deletion.PROTECT,
            null=True, blank=True, related_name='planning_resources',
            help_text='Optional named employee; an unlinked labor resource may describe a role or crew.',
        ),
    ), migrations.RunPython(migrations.RunPython.noop, preserve_reviewed_links)]
