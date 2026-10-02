from django.db import migrations, models
import django.db.models.deletion


def preserve_reviewed_links(apps, schema_editor):
    if apps.get_model('project_organizer', 'Project').objects.using(schema_editor.connection.alias).filter(enterprise_project__isnull=False).exists():
        raise RuntimeError('Preserve reviewed organizer project links before reversing this migration.')


class Migration(migrations.Migration):
    dependencies = [
        ('project_organizer', '0002_rename_project_org_status_98d3a1_idx_project_org_status_61b226_idx_and_more'),
        ('core', '0001_initial'),
    ]

    operations = [migrations.AddField(
        model_name='project', name='enterprise_project',
        field=models.ForeignKey(
            to='core.project', on_delete=django.db.models.deletion.PROTECT,
            null=True, blank=True, db_constraint=False, related_name='organizer_workspaces',
            help_text='Reviewed enterprise identity; original organizer labels remain unchanged.',
        ),
    ), migrations.RunPython(migrations.RunPython.noop, preserve_reviewed_links)]
