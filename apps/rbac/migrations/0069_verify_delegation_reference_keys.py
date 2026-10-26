"""Verify reference keys on databases which already applied RBAC 0067/0068."""
from importlib import import_module

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('rbac', '0068_field_and_row_permissions')]
    operations = [migrations.RunPython(
        import_module('apps.rbac.migrations.0067_role_assignment_windows_and_delegation').ensure_delegation_reference_keys,
        migrations.RunPython.noop,
    )]
