"""Optional invoice references: rollback-only local PostgreSQL and memory SQLite.

PostgreSQL uses temporary schemas without a public search path and refuses
remote hosts. This suite never applies migrations to application tables/history.
"""
from copy import deepcopy
from importlib import import_module
import unittest
from uuid import uuid4

from django.apps import apps
from django.db import connection, connections, IntegrityError, migrations, models, transaction
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.state import ProjectState
from django.test import SimpleTestCase, override_settings


SOURCE_NAME = '0012_receivables_source_snapshot'
VERIFY_NAME = '0013_verify_customer_invoice_reference_key'
DETACH_NAME = '0014_detach_receivables_invoice_reference'
INVOICE_TABLE = 'invoice_tracker_customerinvoice'
ROW_TABLE = 'finance_receivablessourcerow'
SNAPSHOT_TABLE = 'finance_receivablessourcesnapshot'
LOCAL_POSTGRESQL = (
    connection.vendor == 'postgresql'
    and str(connection.settings_dict.get('HOST', '')) in {'', 'localhost', '127.0.0.1', '::1', 'postgres_local'}
)


def migration(name):
    return import_module('apps.finance.migrations.' + name).Migration(name, 'finance')


def base_state():
    state = ProjectState.from_apps(apps)
    for model in ('receivablessourcerow', 'receivablessourcesnapshot'):
        state.remove_model('finance', model)
    return state


def source_migration(*, legacy=False):
    source = migration(SOURCE_NAME)
    if legacy:
        source.operations = deepcopy(source.operations)
        for operation in source.operations:
            if isinstance(operation, migrations.CreateModel) and operation.name == 'ReceivablesSourceRow':
                operation.fields = [
                    ('register_invoice', models.ForeignKey(
                        'invoice_tracker.CustomerInvoice', on_delete=models.SET_NULL,
                        null=True, blank=True, related_name='+',
                    )) if name == 'register_invoice_id' else (name, field)
                    for name, field in operation.fields
                ]
    return source


def apply_migration(database, state, operation):
    # PostgreSQL owns an outer rollback-only transaction; SQLite's editor must
    # manage its own transactions and FK handling during table rebuilds.
    with database.schema_editor(atomic=database.vendor != 'postgresql') as editor:
        return operation.apply(state.clone(), editor)


class ReceivablesReferenceMigrationGraphTests(SimpleTestCase):
    def test_source_preserves_the_column_without_an_invoice_foreign_key(self):
        source = migration(SOURCE_NAME)
        self.assertTrue(all(isinstance(operation, migrations.CreateModel) for operation in source.operations))
        row = next(operation for operation in source.operations if operation.name == 'ReceivablesSourceRow')
        field = dict(row.fields)['register_invoice_id']
        self.assertIsInstance(field, models.BigIntegerField)
        self.assertTrue(field.null)
        self.assertTrue(field.db_index)
        self.assertEqual(migration(VERIFY_NAME).operations, [])
        self.assertEqual(source.dependencies, [
            ('finance', '0011_executive_finance_period'),
            ('invoice_tracker', '0004_rename_invoice_tra_account_dab1bb_idx_invoice_tra_account_55b104_idx_and_more'),
        ])

    def test_applied_source_and_verification_need_only_the_additive_detach(self):
        with override_settings(MIGRATION_MODULES={}):
            loader = MigrationLoader(None, ignore_no_migrations=True)
        for latest, expected in (
            (SOURCE_NAME, [('finance', VERIFY_NAME), ('finance', DETACH_NAME)]),
            (VERIFY_NAME, [('finance', DETACH_NAME)]),
        ):
            with self.subTest(latest=latest):
                applied = set(loader.graph.forwards_plan(('finance', latest)))
                remaining = [node for node in loader.graph.forwards_plan(('finance', DETACH_NAME)) if node not in applied]
                self.assertEqual(remaining, expected)


@unittest.skipUnless(LOCAL_POSTGRESQL, 'Requires local PostgreSQL; temporary schemas roll back.')
class ReceivablesReferenceKeyPostgreSQLTests(unittest.TestCase):
    def setUp(self):
        self.state = base_state()
        self.schema = 'finance_invoice_reference_test_' + uuid4().hex
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
        with connection.cursor() as cursor:
            cursor.execute('SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = %s)', [self.schema])
            self.assertFalse(cursor.fetchone()[0])

    @staticmethod
    def sql(statement, params=None):
        with connection.cursor() as cursor:
            cursor.execute(statement, params)
            return cursor.fetchall() if cursor.description else None

    def prepare_invoices(self, kind):
        primary = ' PRIMARY KEY' if kind == 'healthy' else ''
        self.sql(f'CREATE TABLE {INVOICE_TABLE} (id bigint{primary}, invoice_number text, marker text)')
        records = [(101, 'SYNTHETIC-001', 'first')]
        if kind == 'duplicate':
            records.append((101, 'SYNTHETIC-002', 'conflicting identity'))
        elif kind == 'null':
            records.append((None, 'SYNTHETIC-002', 'null identity'))
        elif kind == 'invoice_number_conflict':
            records.append((102, 'SYNTHETIC-001', 'conflicting invoice number'))
        elif kind == 'exact_duplicate':
            records.append(records[0])
        for row in records:
            self.sql(f'INSERT INTO {INVOICE_TABLE} VALUES (%s, %s, %s)', row)

    def invoice_snapshot(self):
        return (
            self.sql(f'SELECT * FROM {INVOICE_TABLE} ORDER BY id NULLS FIRST, invoice_number, marker'),
            self.sql('SELECT indexrelid, pg_get_indexdef(indexrelid) FROM pg_index WHERE indrelid=to_regclass(%s) ORDER BY indexrelid', [INVOICE_TABLE]),
            self.sql('SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid=to_regclass(%s) ORDER BY conname', [INVOICE_TABLE]),
            self.sql('SELECT attname, atttypid, attnotnull FROM pg_attribute WHERE attrelid=to_regclass(%s) AND attnum>0 ORDER BY attnum', [INVOICE_TABLE]),
        )

    def apply(self, name):
        self.state = apply_migration(connection, self.state, migration(name))

    def create_source_rows(self):
        Snapshot = self.state.apps.get_model('finance', 'ReceivablesSourceSnapshot')
        Row = self.state.apps.get_model('finance', 'ReceivablesSourceRow')
        snapshot = Snapshot.objects.create(
            sha256='a' * 64, file_name='synthetic.xlsx', sheet_name='Sheet1', last_row=8, row_count=3,
        )
        for number, reference in enumerate((101, None), 6):
            Row.objects.create(snapshot=snapshot, row_number=number, invoice_number=f'SYNTHETIC-{number}', register_invoice_id=reference)
        return snapshot

    def assert_only_snapshot_fk_remains(self):
        self.assertEqual(self.sql('''
            SELECT confrelid::regclass::text FROM pg_constraint
            WHERE conrelid=to_regclass(%s) AND contype='f'
        ''', [ROW_TABLE]), [(SNAPSHOT_TABLE,)])

    def assert_source_constraints_survive(self, snapshot):
        Row = self.state.apps.get_model('finance', 'ReceivablesSourceRow')
        Row.objects.create(snapshot=snapshot, row_number=8, invoice_number='UNMATCHED', register_invoice_id=999999)
        self.sql('SET CONSTRAINTS ALL IMMEDIATE')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Row.objects.create(snapshot_id=999999, row_number=9, invoice_number='INVALID-SNAPSHOT')
            self.sql('SET CONSTRAINTS ALL IMMEDIATE')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Row.objects.create(snapshot=snapshot, row_number=6, invoice_number='DUPLICATE-ROW')

    def fresh_scenario(self, kind):
        self.prepare_invoices(kind)
        before = self.invoice_snapshot()
        self.apply(SOURCE_NAME)
        self.apply(VERIFY_NAME)
        self.apply(DETACH_NAME)
        snapshot = self.create_source_rows()
        self.assert_only_snapshot_fk_remains()
        self.assert_source_constraints_survive(snapshot)
        self.apply(DETACH_NAME)
        self.assertEqual(self.invoice_snapshot(), before)

    def test_missing_invoice_primary_key_does_not_block_source_migration(self):
        self.fresh_scenario('missing')

    def test_duplicate_invoice_ids_are_preserved(self):
        self.fresh_scenario('duplicate')

    def test_exact_duplicate_invoice_rows_are_preserved(self):
        self.fresh_scenario('exact_duplicate')

    def test_null_invoice_ids_are_preserved(self):
        self.fresh_scenario('null')

    def test_conflicting_invoice_numbers_are_preserved(self):
        self.fresh_scenario('invoice_number_conflict')

    def test_healthy_invoice_schema_is_preserved(self):
        self.fresh_scenario('healthy')

    def test_legacy_foreign_key_is_detached_without_changing_rows_or_other_constraints(self):
        self.prepare_invoices('healthy')
        before = self.invoice_snapshot()
        initial = self.state.clone()
        self.state = apply_migration(connection, initial, source_migration(legacy=True))
        snapshot = self.create_source_rows()
        # Model existing committed fixtures while keeping the whole scenario
        # rollback-only: drain the inserts' deferred FK trigger events first.
        self.sql('SET CONSTRAINTS ALL IMMEDIATE')
        rows_before = self.sql(f'SELECT * FROM {ROW_TABLE} ORDER BY id')
        indexes_before = self.sql('SELECT indexrelid, pg_get_indexdef(indexrelid) FROM pg_index WHERE indrelid=to_regclass(%s) ORDER BY indexrelid', [ROW_TABLE])
        self.assertEqual(self.sql("SELECT COUNT(*) FROM pg_constraint WHERE conrelid=to_regclass(%s) AND contype='f'", [ROW_TABLE]), [(2,)])
        # Already-applied databases load patched migration state but retain the
        # former foreign key physically until 0014 executes.
        self.state = source_migration().mutate_state(initial, preserve=True)
        self.apply(VERIFY_NAME)
        self.apply(DETACH_NAME)
        self.apply(DETACH_NAME)
        self.assert_only_snapshot_fk_remains()
        self.assertEqual(self.sql(f'SELECT * FROM {ROW_TABLE} ORDER BY id'), rows_before)
        self.assertEqual(self.sql('SELECT indexrelid, pg_get_indexdef(indexrelid) FROM pg_index WHERE indrelid=to_regclass(%s) ORDER BY indexrelid', [ROW_TABLE]), indexes_before)
        self.assertEqual(self.invoice_snapshot(), before)
        snapshot = self.state.apps.get_model('finance', 'ReceivablesSourceSnapshot').objects.get(pk=snapshot.pk)
        self.assert_source_constraints_survive(snapshot)


class ReceivablesReferenceSQLiteTests(unittest.TestCase):
    def test_legacy_sqlite_table_rebuild_preserves_values_and_snapshot_constraints(self):
        alias = 'receivables_reference_test_' + uuid4().hex
        connections.databases[alias] = {
            'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:', 'OPTIONS': {},
            'TIME_ZONE': None, 'CONN_MAX_AGE': 0, 'CONN_HEALTH_CHECKS': False,
            'AUTOCOMMIT': True, 'ATOMIC_REQUESTS': False, 'TEST': {},
        }
        database = connections[alias]
        try:
            with database.cursor() as cursor:
                cursor.execute(f'CREATE TABLE {INVOICE_TABLE} (id bigint PRIMARY KEY, invoice_number text)')
                cursor.execute(f"INSERT INTO {INVOICE_TABLE} VALUES (101, 'SYNTHETIC-001')")
            initial = base_state()
            legacy_state = apply_migration(database, initial, source_migration(legacy=True))
            Snapshot = legacy_state.apps.get_model('finance', 'ReceivablesSourceSnapshot')
            Row = legacy_state.apps.get_model('finance', 'ReceivablesSourceRow')
            snapshot = Snapshot.objects.using(alias).create(
                sha256='b' * 64, file_name='synthetic.xlsx', sheet_name='Sheet1', last_row=6, row_count=1,
            )
            Row.objects.using(alias).create(snapshot=snapshot, row_number=6, invoice_number='SYNTHETIC-001', register_invoice_id=101)
            with database.cursor() as cursor:
                cursor.execute(f'SELECT * FROM {ROW_TABLE}')
                before = cursor.fetchall()
                cursor.execute(f'CREATE UNIQUE INDEX synthetic_invoice_number_unique ON {ROW_TABLE}(invoice_number)')
                cursor.execute('CREATE TABLE synthetic_source_events (row_id bigint)')
                cursor.execute(f'''CREATE TRIGGER synthetic_source_insert AFTER INSERT ON {ROW_TABLE}
                    BEGIN INSERT INTO synthetic_source_events(row_id) VALUES (NEW.id); END''')
            state = source_migration().mutate_state(initial, preserve=True)
            for _ in range(2):
                state = apply_migration(database, state, migration(DETACH_NAME))
            with database.cursor() as cursor:
                cursor.execute(f'SELECT * FROM {ROW_TABLE}')
                self.assertEqual(cursor.fetchall(), before)
                cursor.execute(f'PRAGMA foreign_key_list({ROW_TABLE})')
                self.assertEqual([(row[2], row[3]) for row in cursor.fetchall()], [(SNAPSHOT_TABLE, 'snapshot_id')])
                cursor.execute(f'SELECT * FROM {INVOICE_TABLE}')
                self.assertEqual(cursor.fetchall(), [(101, 'SYNTHETIC-001')])
                cursor.execute("SELECT name FROM sqlite_master WHERE name IN ('synthetic_invoice_number_unique', 'synthetic_source_insert') ORDER BY name")
                self.assertEqual(cursor.fetchall(), [('synthetic_invoice_number_unique',), ('synthetic_source_insert',)])
            Row = state.apps.get_model('finance', 'ReceivablesSourceRow')
            added = Row.objects.using(alias).create(snapshot_id=snapshot.pk, row_number=7, invoice_number='UNMATCHED', register_invoice_id=999999)
            with database.cursor() as cursor:
                cursor.execute('SELECT row_id FROM synthetic_source_events')
                self.assertEqual(cursor.fetchall(), [(added.pk,)])
            with self.assertRaises(IntegrityError):
                Row.objects.using(alias).create(snapshot_id=snapshot.pk, row_number=9, invoice_number='UNMATCHED')
            with self.assertRaises(IntegrityError):
                Row.objects.using(alias).create(snapshot_id=snapshot.pk, row_number=6, invoice_number='DUPLICATE-ROW')
            with self.assertRaises(IntegrityError):
                Row.objects.using(alias).create(snapshot_id=999999, row_number=8, invoice_number='INVALID-SNAPSHOT')
        finally:
            database.close()
            del connections[alias]
            del connections.databases[alias]
