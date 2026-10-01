from django.db import migrations, models
import django.db.models.deletion


def preserve_reviewed_links(apps, schema_editor):
    if apps.get_model('project_control', 'ApprovedHourEntry').objects.using(schema_editor.connection.alias).filter(employee__isnull=False).exists():
        raise RuntimeError('Preserve reviewed hour-entry employee links before reversing this migration.')


class Migration(migrations.Migration):
    dependencies = [
        ('project_control', '0010_verify_execution_reference_keys'),
        ('hr_core', '0014_overtimerequest_day_entries'),
    ]

    operations = [migrations.AddField(
        model_name='approvedhourentry', name='employee',
        field=models.ForeignKey(
            to='hr_core.employeemaster', on_delete=django.db.models.deletion.PROTECT,
            null=True, blank=True, related_name='project_hour_entries',
            help_text='Canonical identity; employee code and name retain the recorded source labels.',
        ),
    ), migrations.RunPython(migrations.RunPython.noop, preserve_reviewed_links)]
