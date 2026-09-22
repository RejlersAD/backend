"""Verify invoice reference keys where the receivables migration already ran."""
from importlib import import_module

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('finance', '0012_receivables_source_snapshot')]
    operations = [migrations.RunPython(
        import_module('apps.finance.migrations.0012_receivables_source_snapshot').ensure_customer_invoice_reference_key,
        migrations.RunPython.noop,
    )]
