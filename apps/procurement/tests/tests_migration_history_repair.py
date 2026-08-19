from django.test import SimpleTestCase

from apps.procurement.management.commands.repair_procurement_migration_history import (
    EXPECTED_COLUMNS,
    EXPECTED_INDEXES,
    validate_schema,
)


class ProcurementMigrationHistoryRepairTests(SimpleTestCase):
    def valid_snapshot(self):
        columns = dict(EXPECTED_COLUMNS)
        indexes = {
            name: (table, f"CREATE INDEX {name} ON public.{table} USING btree ({fields})")
            for name, (table, fields) in EXPECTED_INDEXES.items()
        }
        return columns, indexes

    def test_accepts_complete_verified_schema(self):
        columns, indexes = self.valid_snapshot()
        self.assertEqual(validate_schema(columns, indexes), [])

    def test_rejects_missing_column(self):
        columns, indexes = self.valid_snapshot()
        columns.pop("management_approval")
        self.assertIn(
            "Missing column procurement_requisitions.management_approval.",
            validate_schema(columns, indexes),
        )

    def test_rejects_wrong_index_definition(self):
        columns, indexes = self.valid_snapshot()
        indexes["procurement_project_cf65fc_idx"] = (
            "procurement_budget",
            "CREATE INDEX procurement_project_cf65fc_idx ON procurement_budget (project_id)",
        )
        self.assertIn(
            "Index procurement_project_cf65fc_idx does not match "
            "procurement_budget(project_id, category).",
            validate_schema(columns, indexes),
        )
