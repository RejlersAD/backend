"""Rollback-only PostgreSQL regression for 0014 on restored schemas.

Run with unittest after django.setup(); no application tables/history are changed.
"""
from importlib import import_module
import unittest
from uuid import uuid4

from django.conf import settings
from django.db import connection, transaction, DatabaseError, IntegrityError
from django.db.migrations.loader import MigrationLoader


LOCAL_POSTGRESQL = (
    connection.vendor == 'postgresql'
    and str(connection.settings_dict.get('HOST', '')) in
    {'', 'localhost', '127.0.0.1', '::1', 'postgres_local'}
)


@unittest.skipUnless(LOCAL_POSTGRESQL, 'Requires local PostgreSQL; temporary schemas roll back')
class ReferenceIntelligenceKeyMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = import_module('apps.spec_customization.migrations.0014_reference_intelligence')
        cls.state = MigrationLoader(None).project_state([
            ('spec_customization', '0013_verify_project_reference_keys'),
        ])

    def sql(self, statement, params=None):
        with connection.cursor() as cursor:
            cursor.execute(statement, params)
            return cursor.fetchall() if cursor.description else None

    def scenario(self, kind, invalid_target=0):
        schema = 's3d_reference_key_test_' + uuid4().hex
        quote = connection.ops.quote_name
        labels = [('spec_customization', 'MatchingWorkbookSet'),
                  ('spec_customization', 'PaperSpecExtractionJob'),
                  tuple(settings.AUTH_USER_MODEL.split('.'))]
        models = [self.state.apps.get_model(*label) for label in labels]
        with transaction.atomic():
            try:
                self.sql(f'CREATE SCHEMA {quote(schema)}')
                self.sql(f'SET LOCAL search_path TO {quote(schema)}')
                self.assertEqual(self.sql('SELECT current_schema()'), [(schema,)])
                snapshots = {}
                for index, model in enumerate(models):
                    table = model._meta.db_table
                    key = ' PRIMARY KEY' if kind == 'healthy' else ''
                    self.sql(f'CREATE TABLE {quote(table)} (id {model._meta.pk.db_type(connection)}{key}, marker text)')
                    identity = uuid4() if model._meta.pk.get_internal_type() == 'UUIDField' else 101
                    self.sql(f'INSERT INTO {quote(table)} VALUES (%s, %s)', [identity, 'preserved'])
                    if kind in {'duplicate', 'null'} and index == invalid_target:
                        self.sql(f'INSERT INTO {quote(table)} VALUES (%s, %s)',
                                 [identity if kind == 'duplicate' else None, 'also preserved'])
                    if kind == 'unique':
                        self.sql(f'CREATE UNIQUE INDEX ON {quote(table)} (id)')
                    snapshots[table] = self.sql(f'SELECT * FROM {quote(table)} ORDER BY marker')
                workbook_table = quote(models[0]._meta.db_table)
                if kind == 'missing':
                    with self.assertRaisesRegex(DatabaseError, 'no unique constraint'):
                        with transaction.atomic():
                            self.sql(f'CREATE TABLE before_repair (reference_id uuid REFERENCES {workbook_table}(id))')
                operation = self.module.Migration('0014_reference_intelligence', 'spec_customization')
                if kind in {'duplicate', 'null'}:
                    with self.assertRaisesRegex(RuntimeError, f'{kind} IDs exist'):
                        with transaction.atomic():
                            with connection.schema_editor() as editor:
                                operation.apply(self.state.clone(), editor)
                    self.assertEqual(self.sql("SELECT to_regclass('spec_customization_componentmatchdecision')"), [(None,)])
                    self.assertEqual(self.sql('SELECT COUNT(*) FROM pg_constraint WHERE contype = %s AND connamespace = to_regnamespace(%s)', ['p', schema]), [(0,)])
                else:
                    # Execute every operation and the schema editor's deferred FKs.
                    with connection.schema_editor() as editor:
                        operation.apply(self.state.clone(), editor)
                    self.assertEqual(self.sql("SELECT COUNT(*) FROM pg_constraint WHERE conrelid = 'spec_customization_componentmatchdecision'::regclass AND contype = 'f'"), [(3,)])
                    before = self.sql('SELECT indexrelid FROM pg_index WHERE indrelid = to_regclass(%s)', [models[0]._meta.db_table])
                    with connection.schema_editor() as editor:
                        self.module.ensure_reference_intelligence_keys(self.state.apps, editor)
                    self.assertEqual(before, self.sql('SELECT indexrelid FROM pg_index WHERE indrelid = to_regclass(%s)', [models[0]._meta.db_table]))
                    self.sql(f'CREATE TABLE after_repair (reference_id uuid REFERENCES {workbook_table}(id))')
                    self.sql(f'INSERT INTO after_repair SELECT id FROM {workbook_table}')
                    with self.assertRaises(IntegrityError), transaction.atomic():
                        self.sql('INSERT INTO after_repair VALUES (%s)', [uuid4()])
                for table, rows in snapshots.items():
                    self.assertEqual(rows, self.sql(f'SELECT id, marker FROM {quote(table)} ORDER BY marker'))
            finally:
                transaction.set_rollback(True)
        self.assertEqual(self.sql('SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = %s)', [schema]), [(False,)])

    def test_missing_keys_reproduce_failure_then_full_migration_succeeds(self):
        self.scenario('missing')

    def test_healthy_keys_are_preserved(self):
        self.scenario('healthy')

    def test_existing_unique_indexes_are_preserved(self):
        self.scenario('unique')

    def test_duplicate_ids_block_and_roll_back_all_repairs(self):
        for target in range(3):
            with self.subTest(target=target):
                self.scenario('duplicate', target)

    def test_null_ids_block_and_roll_back_all_repairs(self):
        for target in range(3):
            with self.subTest(target=target):
                self.scenario('null', target)
