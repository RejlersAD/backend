"""Also validate databases which applied 0053 before its reference-key repair."""
from importlib import import_module

from django.db import migrations


ensure_override_reference_keys = import_module(
    'apps.rbac.migrations.0053_user_permission_overrides'
).ensure_override_reference_keys


class Migration(migrations.Migration):
    dependencies = [('rbac', '0056_instrument_hub_view')]
    operations = [migrations.RunPython(ensure_override_reference_keys, migrations.RunPython.noop)]
