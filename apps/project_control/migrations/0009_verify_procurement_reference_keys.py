"""Verify restored procurement keys on installations already through EPC 0008."""
from importlib import import_module

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('project_control', '0008_epc_execution')]
    operations = [migrations.RunPython(
        import_module('apps.project_control.migrations.0007_epc_foundation').ensure_procurement_reference_keys,
        migrations.RunPython.noop,
    )]
