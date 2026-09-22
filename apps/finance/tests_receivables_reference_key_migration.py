"""Regression coverage for finance.0012's restored-invoice foreign-key failure.

The PostgreSQL suite creates a unique schema inside a rollback-only transaction.
Its search path excludes public/application schemas, and it accepts only loopback
or the workspace's explicitly local Compose service, postgres_local.
It may run through manage.py shell with unittest against local PostgreSQL; it
does not apply or record migrations in the application's migration history.
"""
from importlib import import_module
import unittest
from uuid import uuid4

from django.apps import apps
from django.db import connection, DatabaseError, IntegrityError, migrations, transaction
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.state import ProjectState
from django.test import SimpleTestCase, override_settings


SOURCE_NAME = '0012_receivables_source_snapshot'
VERIFY_NAME = '0013_verify_customer_invoice_reference_key'
SOURCE = 'apps.finance.migrations.' + SOURCE_NAME
VERIFY = 'apps.finance.migrations.' + VERIFY_NAME
INVOICE_TABLE = 'invoice_tracker_customerinvoice'
LOCAL_POSTGRESQL = (
    connection.vendor == 'postgresql'
    and str(connection.settings_dict.get('HOST', '')) in {'', 'localhost', '127.0.0.1', '::1', 'postgres_local'}
)


class ReceivablesReferenceMigrationGraphTests(SimpleTestCase):
    def test_preflight_runs_before_foreign_keys_and_followup_is_additive(self):
        source, verify = import_module(SOURCE), import_module(VERIFY)
        for module in (source, verify):
            operation = module.Migration.operations[0]
            self.assertIsInstance(operation, migrations.RunPython)
            self.assertIs(operation.code, source.ensure_customer_invoice_reference_key)
            self.assertIs(operation.reverse_code, migrations.RunPython.noop)
            self.assertTrue(module.Migration.atomic)
        self.assertIsInstance(source.Migration.operations[1], migrations.CreateModel)
        self.assertEqual(source.Migration.dependencies, [
            ('finance', '0011_executive_finance_period'),
            ('invoice_tracker', '0004_rename_invoice_tra_account_dab1bb_idx_invoice_tra_account_55b104_idx_and_more'),
        ])
        self.assertEqual(verify.Migration.dependencies, [('finance', SOURCE_NAME)])

    def test_already_applied_source_migration_requires_only_new_verification(self):
        with override_settings(MIGRATION_MODULES={}):
            loader = MigrationLoader(None, ignore_no_migrations=True)
        applied = set(loader.graph.forwards_plan(('finance', SOURCE_NAME)))
        remaining = [node for node in loader.graph.forwards_plan(('finance', VERIFY_NAME)) if node not in applied]
        self.assertEqual(remaining, [('finance', VERIFY_NAME)])


@unittest.skipUnless(LOCAL_POSTGRESQL, 'Requires local PostgreSQL; temporary schemas roll back.')
class ReceivablesReferenceKeyPostgreSQLTests(unittest.TestCase):
    def setUp(self):
        self.source = import_module(SOURCE)
        self.verify = import_module(VERIFY)
        self.state = ProjectState.from_apps(apps)
        for model in ('receivablessourcerow', 'receivablessourcesnapshot'):
            self.state.remove_model('finance', model)
        self.schema = 'finance_invoice_key_test_' + uuid4().hex
        self.atomic = transaction.atomic()
        self.atomic.__enter__()
        self.addCleanup(self.rollback_schema)
        quote = connection.ops.quote_name
        self.sql(f'CREATE SCHEMA {quote(self.schema)}')
        self.sql(f'SET LOCAL search_path TO {quote(self.schema)}')
        self.assertEqual(self.sql('SELECT current_schema()'), [(self.schema,)])

    def rollback_schema(self):
        transaction.set_rollback(True)
        self.atomic.__exit__(None, None, None)
        # No permanent objects or migration-history entries survive a scenario.
        with connection.cursor() as cursor:
            cursor.execute('SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = %s)', [self.schema])
            self.assertFalse(cursor.fetchone()[0])

    @staticmethod
    def sql(statement, params=None):
        with connection.cursor() as cursor:
            cursor.execute(statement, params)
            return cursor.fetchall() if cursor.description else None

    def prepare(self, kind='missing'):
        primary = ' PRIMARY KEY' if kind == 'healthy' else ''
        other_primary = ', legacy_key integer PRIMARY KEY' if kind == 'other_pk' else ''
        self.sql(f'CREATE TABLE {INVOICE_TABLE} (id bigint{primary}, invoice_number text, marker text{other_primary})')
        extra_columns, extra_values = (', legacy_key', ', 1') if kind == 'other_pk' else ('', '')
        self.sql(
            f'INSERT INTO {INVOICE_TABLE} (id, invoice_number, marker{extra_columns}) '
            f'VALUES (101, %s, %s{extra_values})', ['SYNTHETIC-001', 'first'],
        )
        if kind in {'duplicate', 'null'}:
            self.sql(f'INSERT INTO {INVOICE_TABLE} (id, invoice_number, marker) VALUES (%s, %s, %s)',
                     [101 if kind == 'duplicate' else None, 'SYNTHETIC-002', 'second'])
        if kind == 'unique':
            self.sql(f'CREATE UNIQUE INDEX synthetic_invoice_identity ON {INVOICE_TABLE} (id)')
        elif kind == 'partial':
            self.sql(f"CREATE UNIQUE INDEX synthetic_partial_identity ON {INVOICE_TABLE} (id) WHERE marker = 'first'")
        elif kind == 'deferrable':
            self.sql(f'ALTER TABLE {INVOICE_TABLE} ADD UNIQUE (id) DEFERRABLE INITIALLY DEFERRED')
        elif kind == 'composite':
            self.sql(f'ALTER TABLE {INVOICE_TABLE} ADD UNIQUE (id, marker)')
        return self.invoice_rows()

    def invoice_rows(self):
        return self.sql(f'SELECT * FROM {INVOICE_TABLE} ORDER BY marker')

    def invoice_indexes(self):
        return self.sql('''
            SELECT indexrelid, pg_get_indexdef(indexrelid), indisprimary,
                   indisunique, indimmediate, indisvalid
            FROM pg_index WHERE indrelid = to_regclass(%s) ORDER BY indexrelid
        ''', [INVOICE_TABLE])

    def apply(self, module, name, *, legacy=False):
        migration = module.Migration(name, 'finance')
        if legacy:
            # Reproduce exactly the original 0012 before its preflight existed.
            migration.operations = [operation for operation in migration.operations
                                    if not isinstance(operation, migrations.RunPython)]
        # This suite owns the outer transaction and explicit failure savepoints.
        # If deferred SQL raises during schema_editor.__exit__, its internal
        # atomic context would otherwise remain open and obscure the rollback.
        with connection.schema_editor(atomic=False) as editor:
            next_state = migration.apply(self.state.clone(), editor)
        self.state = next_state

    def assert_source_fk_works(self):
        Snapshot = self.state.apps.get_model('finance', 'ReceivablesSourceSnapshot')
        Row = self.state.apps.get_model('finance', 'ReceivablesSourceRow')
        foreign_keys = self.sql('''
            SELECT confrelid = to_regclass(%s), convalidated
            FROM pg_constraint
            WHERE conrelid = to_regclass(%s) AND contype = 'f'
        ''', [INVOICE_TABLE, Row._meta.db_table])
        self.assertEqual(len(foreign_keys), 2)
        self.assertIn((True, True), foreign_keys)
        snapshot = Snapshot.objects.create(
            sha256='a' * 64, file_name='synthetic.xlsx', sheet_name='Sheet1',
            last_row=6, row_count=1,
        )
        row = Row.objects.create(snapshot=snapshot, row_number=6, invoice_number='SYNTHETIC-001',
                                 register_invoice_id=101)
        self.sql('SET CONSTRAINTS ALL IMMEDIATE')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Row.objects.create(snapshot=snapshot, row_number=7, invoice_number='INVALID-REFERENCE',
                               register_invoice_id=999999)
            self.sql('SET CONSTRAINTS ALL IMMEDIATE')
        self.assertEqual(Row.objects.values_list('pk', 'register_invoice_id').get(), (row.pk, 101))
        return row

    def successful_scenario(self, kind):
        before = self.prepare(kind)
        indexes_before = self.invoice_indexes()
        if kind in {'missing', 'partial', 'deferrable', 'composite', 'other_pk'}:
            with self.assertRaisesRegex(DatabaseError, 'no unique constraint|non-deferrable|deferrable unique'):
                with transaction.atomic():
                    self.apply(self.source, SOURCE_NAME, legacy=True)
            self.assertEqual(self.invoice_rows(), before)
        self.apply(self.source, SOURCE_NAME)
        if kind in {'healthy', 'unique'}:
            self.assertEqual(self.invoice_indexes(), indexes_before)
        if kind == 'other_pk':
            self.assertEqual(self.sql('''
                SELECT a.attname FROM pg_constraint c JOIN pg_attribute a
                  ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
                WHERE c.conrelid = to_regclass(%s) AND c.contype = 'p'
            ''', [INVOICE_TABLE]), [('legacy_key',)])
        row = self.assert_source_fk_works()
        indexes_after = self.invoice_indexes()
        with connection.schema_editor() as editor:
            self.source.ensure_customer_invoice_reference_key(self.state.apps, editor)
        self.apply(self.verify, VERIFY_NAME)
        self.apply(self.verify, VERIFY_NAME)
        self.assertEqual(self.invoice_indexes(), indexes_after)
        self.assertEqual(self.invoice_rows(), before)
        row.refresh_from_db()
        self.assertEqual(row.register_invoice_id, 101)

    def test_missing_primary_key_reproduces_then_repairs_the_full_migration(self):
        self.successful_scenario('missing')

    def test_healthy_primary_key_is_preserved_without_redundant_indexes(self):
        self.successful_scenario('healthy')

    def test_existing_unique_index_is_reused(self):
        self.successful_scenario('unique')

    def test_partial_unique_index_does_not_satisfy_the_foreign_key(self):
        self.successful_scenario('partial')

    def test_deferrable_unique_constraint_does_not_satisfy_the_foreign_key(self):
        self.successful_scenario('deferrable')

    def test_composite_unique_constraint_does_not_satisfy_the_foreign_key(self):
        self.successful_scenario('composite')

    def test_primary_key_on_another_column_is_preserved(self):
        self.successful_scenario('other_pk')

    def invalid_scenario(self, kind):
        before = self.prepare(kind)
        indexes_before = self.invoice_indexes()
        with self.assertRaisesRegex(RuntimeError, f'{kind} IDs exist'):
            with transaction.atomic():
                self.apply(self.source, SOURCE_NAME)
        self.assertEqual(self.invoice_rows(), before)
        self.assertEqual(self.invoice_indexes(), indexes_before)
        for model in ('ReceivablesSourceSnapshot', 'ReceivablesSourceRow'):
            table = apps.get_model('finance', model)._meta.db_table
            self.assertEqual(self.sql('SELECT to_regclass(%s)', [table]), [(None,)])

    def test_duplicate_invoice_ids_refuse_repair_without_data_or_schema_mutation(self):
        self.invalid_scenario('duplicate')

    def test_null_invoice_ids_refuse_repair_without_data_or_schema_mutation(self):
        self.invalid_scenario('null')

    def test_followup_preserves_an_installation_with_original_0012_already_applied(self):
        before = self.prepare('healthy')
        self.apply(self.source, SOURCE_NAME, legacy=True)
        row = self.assert_source_fk_works()
        indexes_before = self.invoice_indexes()
        self.apply(self.verify, VERIFY_NAME)
        self.assertEqual(self.invoice_indexes(), indexes_before)
        self.assertEqual(self.invoice_rows(), before)
        row.refresh_from_db()
        self.assertEqual(row.register_invoice_id, 101)
