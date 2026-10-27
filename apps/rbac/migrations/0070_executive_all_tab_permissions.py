"""EXEC-ACCESS-02: all six tabs are assignable; no live grants are rewritten."""
from django.db import migrations, models


def register_tabs(apps, schema_editor):
    Module = apps.get_model('rbac', 'Module')
    Permission = apps.get_model('rbac', 'Permission')
    alias = schema_editor.connection.alias
    module = Module.objects.using(alias).get(code='executive_dashboard')
    for action, label in (
        ('view_commercial', 'Commercial pipeline'),
        ('view_workforce', 'Workforce'),
        ('view_risk', 'Risk & Compliance'),
    ):
        Permission.objects.using(alias).get_or_create(
            code=f'executive_dashboard.{action}', defaults={
                'module_id': module.pk, 'action': action,
                'name': f'Executive: {label}', 'is_active': True,
                'description': 'View this Executive tab; source and record restrictions still apply.',
            },
        )


class Migration(migrations.Migration):
    dependencies = [('rbac', '0069_verify_delegation_reference_keys')]
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
                ('view_commercial', 'View Executive Commercial pipeline'),
                ('view_workforce', 'View Executive Workforce'),
                ('view_risk', 'View Executive Risk & Compliance'),
            ]),
        ),
        migrations.RunPython(register_tabs, migrations.RunPython.noop),
    ]
