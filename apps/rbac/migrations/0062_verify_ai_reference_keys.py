"""Verify existing databases which already applied the AI migrations."""
from importlib import import_module

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('rbac', '0061_ai_measurement_pipeline')]
    operations = [migrations.RunPython(
        import_module('apps.rbac.migrations.0060_ai_outcome_evidence').ensure_ai_reference_keys,
        migrations.RunPython.noop,
    )]
