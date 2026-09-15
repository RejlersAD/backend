"""Verify execution target keys for installations already through EPC 0009."""
from importlib import import_module

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('project_control', '0009_verify_procurement_reference_keys')]
    operations = [migrations.RunPython(
        import_module('apps.project_control.migrations.0008_epc_execution').ensure_execution_reference_keys,
        migrations.RunPython.noop,
    )]
