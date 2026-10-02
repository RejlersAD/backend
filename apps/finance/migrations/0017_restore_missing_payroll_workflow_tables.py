"""Restore declared workflow tables missing after divergent historical 0010.

Current migration state already retains the 0007 models. Some databases applied
an older 0010 that removed their tables. Healthy tables are validated and kept;
missing tables are created empty. No workflow, approval, notification or payroll
data is generated. This forward repair intentionally never drops retained data.
"""
from django.db import migrations

from config.migration_schema import ensure_declared_table


def restore_declared_tables(apps, schema_editor):
    for name in ('PayrollWorkflow', 'WorkflowNotificationLog'):
        ensure_declared_table(apps.get_model('finance', name), schema_editor)


class Migration(migrations.Migration):
    dependencies = [('finance', '0016_receivables_source_identity')]
    operations = [migrations.RunPython(restore_declared_tables)]
