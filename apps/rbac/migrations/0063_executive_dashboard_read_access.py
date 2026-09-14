"""Register executive overview without granting access to any existing role."""
from django.db import migrations


def register_executive_dashboard(apps, schema_editor):
    Module = apps.get_model('rbac', 'Module')
    Permission = apps.get_model('rbac', 'Permission')
    alias = schema_editor.connection.alias
    module, _ = Module.objects.using(alias).get_or_create(
        code='executive_dashboard', defaults={
            'name': 'Executive overview', 'icon': 'ChartBar', 'order': 0,
            'description': 'Read-only executive overview of separately authorized operational registers',
            'is_active': True,
        },
    )
    Permission.objects.using(alias).get_or_create(
        code='executive_dashboard.read', defaults={
            'module': module, 'action': 'read', 'name': 'Executive overview: View',
            'description': 'View the executive dashboard; each source retains its own read grant.',
            'is_active': True,
        },
    )


class Migration(migrations.Migration):
    dependencies = [('rbac', '0062_verify_ai_reference_keys')]
    operations = [migrations.RunPython(register_executive_dashboard, migrations.RunPython.noop)]
