"""Reference-key repair checks using disposable PostgreSQL schemas only."""
from importlib import import_module
from unittest import skipUnless
from uuid import uuid4

from django.apps import apps
from django.db import connection, migrations, transaction
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase, TestCase, override_settings


FOUNDATION = import_module('apps.project_control.migrations.0007_epc_foundation')
EXECUTION = import_module('apps.project_control.migrations.0008_epc_execution')
VERIFICATION = import_module('apps.project_control.migrations.0009_verify_procurement_reference_keys')
EXECUTION_VERIFICATION = import_module('apps.project_control.migrations.0010_verify_execution_reference_keys')
TABLES = ('procurement_requisitions', 'procurement_orders', 'core_projectmilestone')


class ReferenceKeyMigrationGraphTests(SimpleTestCase):
    def test_preflights_precede_new_foreign_keys_and_follow_up_covers_applied_execution(self):
        for module, preflight in (
            (FOUNDATION, FOUNDATION.ensure_procurement_reference_keys),
            (EXECUTION, EXECUTION.ensure_execution_reference_keys),
            (VERIFICATION, FOUNDATION.ensure_procurement_reference_keys),
            (EXECUTION_VERIFICATION, EXECUTION.ensure_execution_reference_keys),
        ):
            operation = module.Migration.operations[0]
            self.assertIsInstance(operation, migrations.RunPython)
            self.assertIs(operation.code, preflight)
            self.assertIs(operation.reverse_code, migrations.RunPython.noop)
            self.assertTrue(module.Migration.atomic)
        self.assertIn(('project_control', '0007_epc_foundation'), EXECUTION.Migration.dependencies)
        self.assertEqual(VERIFICATION.Migration.dependencies, [('project_control', '0008_epc_execution')])
        self.assertEqual(EXECUTION_VERIFICATION.Migration.dependencies, [('project_control', '0009_verify_procurement_reference_keys')])
        self.assertNotIn(('project_control', '0009_verify_procurement_reference_keys'), FOUNDATION.Migration.dependencies)

    def test_already_applied_epc_history_has_only_the_additive_verification_remaining(self):
        # Build the graph from the committed migration modules even when the
        # lightweight test settings normally disable migrations for model sync.
        with override_settings(MIGRATION_MODULES={}):
            loader = MigrationLoader(None, ignore_no_migrations=True)
        verification = ('project_control', '0009_verify_procurement_reference_keys')
        execution_verification = ('project_control', '0010_verify_execution_reference_keys')
        for latest_applied, expected in (
            (('project_control', '0008_epc_execution'), [verification, execution_verification]),
            (verification, [execution_verification]),
        ):
            with self.subTest(latest_applied=latest_applied):
                applied = set(loader.graph.forwards_plan(latest_applied))
                remaining = [node for node in loader.graph.forwards_plan(execution_verification) if node not in applied]
                self.assertEqual(remaining, expected)


@skipUnless(connection.vendor == 'postgresql', 'Requires a disposable PostgreSQL test database.')
class ReferenceKeyRepairTests(TestCase):
    def setUp(self):
        super().setUp()
        self.schema = 'epc_key_test_' + uuid4().hex
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE SCHEMA {connection.ops.quote_name(self.schema)}')
            cursor.execute(f'SET LOCAL search_path TO {connection.ops.quote_name(self.schema)}')
        # Both CREATE SCHEMA and search_path are rolled back by TestCase.
        # No public/application tables are visible through this search path.

    def sql(self, statement, params=None):
        with connection.cursor() as cursor:
            cursor.execute(statement, params)
            return cursor.fetchall() if cursor.description else None

    def create_targets(self, *, other_primary=False):
        for table in TABLES:
            primary = ' PRIMARY KEY' if other_primary else ''
            self.sql(
                f'CREATE TABLE {connection.ops.quote_name(table)} '
                f'(row_number integer NOT NULL{primary}, id integer, label text NOT NULL)'
            )
            self.sql(
                f'INSERT INTO {connection.ops.quote_name(table)} VALUES '
                '(1, 11, %s), (2, 22, %s)', ['synthetic first row', 'synthetic second row'],
            )

    def rows(self):
        return {
            table: self.sql(f'SELECT row_number, id, label FROM {connection.ops.quote_name(table)} ORDER BY row_number')
            for table in TABLES
        }

    def indexes(self):
        return self.sql('''
            SELECT i.indrelid::regclass::text, i.indexrelid, i.indisprimary,
                   i.indisunique, i.indimmediate
            FROM pg_index i JOIN pg_class c ON c.oid = i.indrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s ORDER BY i.indrelid, i.indexrelid
        ''', [self.schema])

    def repair(self):
        # Match the atomic boundary of the production RunPython migrations,
        # including rollback if a later target cannot be repaired.
        with transaction.atomic(), connection.schema_editor() as editor:
            FOUNDATION.ensure_reference_keys(apps, editor, (
                ('procurement', 'PurchaseRequisition'),
                ('procurement', 'PurchaseOrder'),
                ('core', 'ProjectMilestone'),
            ))

    def assert_referenceable(self):
        self.sql('''
            CREATE TABLE synthetic_epc_references (
                requisition_id integer REFERENCES procurement_requisitions(id),
                order_id integer REFERENCES procurement_orders(id),
                milestone_id integer REFERENCES core_projectmilestone(id)
            )
        ''')
        self.sql('INSERT INTO synthetic_epc_references VALUES (11, 22, 11)')

    def test_missing_id_keys_are_repaired_without_changing_rows_and_are_idempotent(self):
        self.create_targets()
        before = self.rows()
        self.repair()
        repaired_indexes = self.indexes()
        self.assertEqual(len(repaired_indexes), len(TABLES))
        self.assertTrue(all(index[2] for index in repaired_indexes))
        self.repair()
        self.assertEqual(self.indexes(), repaired_indexes)
        self.assertEqual(self.rows(), before)
        self.assert_referenceable()

    def test_existing_immediate_unique_indexes_are_reused(self):
        self.create_targets()
        for table in TABLES:
            self.sql(f'CREATE UNIQUE INDEX {table}_existing_id ON {table} (id)')
        before, indexes = self.rows(), self.indexes()
        self.repair()
        self.assertEqual(self.indexes(), indexes)
        self.assertEqual(self.rows(), before)
        self.assert_referenceable()

    def test_healthy_primary_keys_are_reused(self):
        self.create_targets()
        for table in TABLES:
            self.sql(f'ALTER TABLE {table} ADD PRIMARY KEY (id)')
        before, indexes = self.rows(), self.indexes()
        self.repair()
        self.assertEqual(self.indexes(), indexes)
        self.assertEqual(self.rows(), before)
        self.assert_referenceable()

    def assert_ineligible_index_repaired(self, definition):
        self.create_targets()
        for table in TABLES:
            self.sql(f'CREATE UNIQUE INDEX {table}_ineligible ON {table} {definition}')
        before, indexes = self.rows(), self.indexes()
        self.repair()
        self.assertTrue(set(indexes).issubset(set(self.indexes())))
        self.assertEqual(len(self.indexes()), 2 * len(TABLES))
        self.assertEqual(self.rows(), before)
        self.assert_referenceable()

    def test_partial_unique_indexes_do_not_substitute_for_reference_keys(self):
        self.assert_ineligible_index_repaired('(id) WHERE id > 0')

    def test_composite_unique_indexes_do_not_substitute_for_reference_keys(self):
        self.assert_ineligible_index_repaired('(id, row_number)')

    def test_expression_unique_indexes_do_not_substitute_for_reference_keys(self):
        self.assert_ineligible_index_repaired('(abs(id))')

    def test_unique_indexes_with_included_columns_are_reused(self):
        self.create_targets()
        for table in TABLES:
            self.sql(f'CREATE UNIQUE INDEX {table}_covering_id ON {table} (id) INCLUDE (label)')
        before, indexes = self.rows(), self.indexes()
        self.repair()
        self.assertEqual(self.indexes(), indexes)
        self.assertEqual(self.rows(), before)
        self.assert_referenceable()

    def test_deferrable_unique_constraints_receive_immediate_reference_keys(self):
        self.create_targets()
        for table in TABLES:
            self.sql(f'ALTER TABLE {table} ADD UNIQUE (id) DEFERRABLE INITIALLY DEFERRED')
        before, indexes = self.rows(), self.indexes()
        self.repair()
        self.assertTrue(set(indexes).issubset(set(self.indexes())))
        self.assertEqual(len(self.indexes()), 2 * len(TABLES))
        self.assertEqual(self.rows(), before)
        self.assert_referenceable()

    def test_primary_key_on_other_column_is_preserved_and_id_gets_unique_key(self):
        self.create_targets(other_primary=True)
        before, indexes = self.rows(), self.indexes()
        self.repair()
        repaired = self.indexes()
        self.assertTrue(set(indexes).issubset(set(repaired)))
        self.assertEqual(len(repaired), 2 * len(TABLES))
        self.assertEqual(sum(index[2] for index in repaired), len(TABLES))
        self.assertEqual(self.rows(), before)
        self.assert_referenceable()

    def assert_invalid_ids_roll_back(self, table, *, null=False):
        self.create_targets()
        self.sql(f'UPDATE {table} SET id = %s WHERE row_number = 2', [None if null else 11])
        before, indexes = self.rows(), self.indexes()
        reason = 'null IDs exist' if null else 'duplicate IDs exist'
        with self.assertRaisesMessage(RuntimeError, f'Cannot repair {table}.id: {reason}'):
            self.repair()
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.indexes(), indexes)

    def test_duplicate_requisition_ids_fail_without_changing_rows(self):
        self.assert_invalid_ids_roll_back(TABLES[0])

    def test_null_requisition_ids_fail_without_changing_rows(self):
        self.assert_invalid_ids_roll_back(TABLES[0], null=True)

    def test_duplicate_order_ids_roll_back_prior_requisition_key_repair(self):
        self.assert_invalid_ids_roll_back(TABLES[1])

    def test_null_order_ids_roll_back_prior_requisition_key_repair(self):
        self.assert_invalid_ids_roll_back(TABLES[1], null=True)

    def test_duplicate_milestone_ids_roll_back_prior_procurement_key_repairs(self):
        self.assert_invalid_ids_roll_back(TABLES[2])

    def test_null_milestone_ids_roll_back_prior_procurement_key_repairs(self):
        self.assert_invalid_ids_roll_back(TABLES[2], null=True)
