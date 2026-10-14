"""Verify restored reference keys even when creation/save migrations are applied."""
from importlib import import_module

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('spec_customization', '0012_extraction_save')]
    operations = [migrations.RunPython(
        import_module('apps.spec_customization.migrations.0011_projectcreationrequest').ensure_creation_reference_keys,
        migrations.RunPython.noop,
    )]
