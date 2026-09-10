"""PostgreSQL regression tests for the deployed 0012 foreign-key failure.

Each scenario uses a transaction-local schema and rolls back all DDL/data.
The suite can also run through manage.py shell against local PostgreSQL.
"""
import importlib
import unittest
import uuid

from django.db import connection, transaction, ProgrammingError
from django.db.migrations.loader import MigrationLoader


@unittest.skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL foreign-key validation')
class OvertimeMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = importlib.import_module(
            'apps.hr_core.migrations.0012_overtimeconversion_overtimeconversionallocation_and_more')
        cls.state = MigrationLoader(connection).project_state([
            ('hr_core', '0011_reconcile_canonical_employee_identity'),
            ('payroll_engine', '0018_alter_payrollemployee_employee'),
        ])

    def scenario(self, kind):
        schema = 'ot_migration_test_' + uuid.uuid4().hex
        quote = connection.ops.quote_name
        with transaction.atomic():
            try:
                with connection.cursor() as cursor:
                    cursor.execute(f'CREATE SCHEMA {quote(schema)}')
                    cursor.execute(f'SET LOCAL search_path TO {quote(schema)}')
                    for app, name in [('hr_core', 'EmployeeMaster'), ('users', 'User'), ('hr_core', 'OvertimeRequest')]:
                        model = self.state.apps.get_model(app, name)
                        cursor.execute(f'CREATE TABLE {quote(model._meta.db_table)} ({quote(model._meta.pk.column)} {model._meta.pk.db_type(connection)} PRIMARY KEY)')
                    suffix = ', legacy_key integer PRIMARY KEY' if kind == 'other_pk' else ''
                    key = ' PRIMARY KEY' if kind == 'healthy' else ''
                    cursor.execute(f'CREATE TABLE payroll_engine_adjustment (id bigint{key}{suffix})')
                    if kind == 'other_pk':
                        cursor.execute('INSERT INTO payroll_engine_adjustment VALUES (42, 1)')
                    else:
                        cursor.execute('INSERT INTO payroll_engine_adjustment VALUES (42)')
                    if kind == 'duplicate':
                        cursor.execute('INSERT INTO payroll_engine_adjustment VALUES (42)')
                    elif kind == 'null':
                        cursor.execute('INSERT INTO payroll_engine_adjustment VALUES (NULL)')
                if kind in {'duplicate', 'null'}:
                    with self.assertRaisesRegex(RuntimeError, 'duplicate IDs' if kind == 'duplicate' else 'NULL IDs'):
                        with connection.schema_editor() as editor:
                            self.module.Migration('0012', 'hr_core').apply(self.state.clone(), editor)
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT COUNT(*) FROM payroll_engine_adjustment')
                        self.assertEqual(cursor.fetchone()[0], 2)
                    return
                if kind == 'missing':
                    with self.assertRaisesRegex(ProgrammingError, 'no unique constraint'):
                        with transaction.atomic():
                            with connection.cursor() as cursor:
                                cursor.execute('CREATE TABLE before_repair (adjustment_id bigint REFERENCES payroll_engine_adjustment(id))')
                # Execute the complete migration, including deferred FK SQL.
                with connection.schema_editor() as editor:
                    self.module.Migration('0012', 'hr_core').apply(self.state.clone(), editor)
                # Running the preflight again must leave the key unchanged.
                with connection.schema_editor() as editor:
                    self.module.ensure_adjustment_reference_key(self.state.apps, editor)
                with connection.cursor() as cursor:
                    cursor.execute('SELECT id FROM payroll_engine_adjustment')
                    self.assertEqual(cursor.fetchall(), [(42,)])
                    cursor.execute("SELECT COUNT(*) FROM pg_constraint WHERE conrelid = 'hr_core_overtimeconversion'::regclass AND confrelid = 'payroll_engine_adjustment'::regclass AND contype = 'f'")
                    self.assertEqual(cursor.fetchone()[0], 1)
                    cursor.execute("SELECT COUNT(*) FROM pg_index WHERE indrelid = 'payroll_engine_adjustment'::regclass AND indisunique")
                    self.assertEqual(cursor.fetchone()[0], 2 if kind == 'other_pk' else 1)
            finally:
                transaction.set_rollback(True)

    def test_missing_primary_key(self):
        self.scenario('missing')

    def test_existing_primary_key_is_unchanged(self):
        self.scenario('healthy')

    def test_primary_key_on_different_column(self):
        self.scenario('other_pk')

    def test_duplicate_ids_are_not_deleted(self):
        self.scenario('duplicate')

    def test_null_ids_are_not_renumbered(self):
        self.scenario('null')
