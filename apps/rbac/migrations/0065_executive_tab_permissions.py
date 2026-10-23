"""EXEC-ACCESS-01: additive definitions, no role or individual grants."""
from django.db import migrations, models


def register_tabs(apps, schema_editor):
    Module = apps.get_model('rbac', 'Module')
    Permission = apps.get_model('rbac', 'Permission')
    alias = schema_editor.connection.alias
    module = Module.objects.using(alias).get(code='executive_dashboard')
    for action, label in (
        ('view_overview', 'Overview'),
        ('view_financial', 'Financial performance'),
        ('view_portfolio', 'Project portfolio'),
    ):
        Permission.objects.using(alias).get_or_create(
            code=f'executive_dashboard.{action}', defaults={
                'module_id': module.pk, 'action': action,
                'name': f'Executive: {label}', 'is_active': True,
                'description': 'Tab access; Executive read and source permissions remain required.',
            },
        )


class Migration(migrations.Migration):
    dependencies = [('rbac', '0064_userprofile_digital_stamp')]
    operations = [
        migrations.AlterField(
            model_name='permission', name='action',
            field=models.CharField(max_length=20, choices=[
                ('create', 'Create'), ('read', 'Read'), ('update', 'Update'),
                ('delete', 'Delete'), ('approve', 'Approve'), ('export', 'Export'),
                ('import', 'Import'), ('execute', 'Execute'),
                ('view_overview', 'View Executive Overview'),
                ('view_financial', 'View Executive Financial performance'),
                ('view_portfolio', 'View Executive Project portfolio'),
            ]),
        ),
        migrations.RunPython(register_tabs, migrations.RunPython.noop),
    ]
