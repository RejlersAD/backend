from importlib import import_module

from django.apps import apps as django_apps
from django.db import connection
from django.test import TransactionTestCase

from ..models import DailyFieldUpdate


reconciliation = import_module(
    'apps.planning_intelligence.migrations.0019_reconcile_schedule_schema'
)


class ScheduleSchemaReconciliationTests(TransactionTestCase):
    reset_sequences = True

    def test_reconciliation_recreates_a_missing_schedule_table(self):
        table_name = DailyFieldUpdate._meta.db_table
        self.assertIn(table_name, connection.introspection.table_names())

        with connection.schema_editor() as schema_editor:
            schema_editor.delete_model(DailyFieldUpdate)
        self.assertNotIn(table_name, connection.introspection.table_names())

        with connection.schema_editor() as schema_editor:
            reconciliation.create_missing_schedule_tables(django_apps, schema_editor)

        self.assertIn(table_name, connection.introspection.table_names())
        self.assertEqual(DailyFieldUpdate.objects.count(), 0)
