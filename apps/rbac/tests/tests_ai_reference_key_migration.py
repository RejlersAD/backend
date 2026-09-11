"""Actual PostgreSQL migration tests; temporary schemas and all DDL roll back.

Run with Django's test command against local PostgreSQL. SQLite skips
these tests because it cannot reproduce PostgreSQL foreign-key prerequisites.
"""
from importlib import import_module
import unittest
import uuid

from django.db import connection, transaction, IntegrityError, DatabaseError
from django.db.migrations.loader import MigrationLoader


@unittest.skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL foreign-key validation')
class AIReferenceKeyMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.migration = import_module('apps.rbac.migrations.0060_ai_outcome_evidence')
        cls.followup = import_module('apps.rbac.migrations.0062_verify_ai_reference_keys')
        cls.state = MigrationLoader(connection).project_state([('rbac', '0059_monthly_champion_publication')])

    def scenario(self, kind, invalid_model="organization"):
        schema = 'rbac_fk_test_' + uuid.uuid4().hex
        quote = connection.ops.quote_name
        module_id, organization_id = uuid.uuid4(), uuid.uuid4()
        user_model = self.state.apps.get_model('users', 'User')
        user_table = user_model._meta.db_table
        invalid_table = {'module': 'rbac_modules', 'organization': 'rbac_organizations', 'user': user_table}[invalid_model]
        with transaction.atomic():
            try:
                with connection.cursor() as cursor:
                    cursor.execute(f'CREATE SCHEMA {quote(schema)}')
                    cursor.execute(f'SET LOCAL search_path TO {quote(schema)}')
                    for table, identity in [('rbac_modules', module_id), ('rbac_organizations', organization_id), (user_table, 1)]:
                        key = ' PRIMARY KEY' if kind == 'healthy' or (kind == 'profile_only' and table == 'rbac_modules') else ''
                        extra = ', legacy_key integer PRIMARY KEY DEFAULT 1' if kind == 'other_pk' else ''
                        identity_type = user_model._meta.pk.db_type(connection) if table == user_table else 'uuid'
                        cursor.execute(f'CREATE TABLE {quote(table)} (id {identity_type}{key}, label text DEFAULT \'preserved\'{extra})')
                        cursor.execute(f'INSERT INTO {quote(table)} (id) VALUES (%s)', [identity])
                        if kind in {'duplicate', 'null'} and table == invalid_table:
                            cursor.execute(f'INSERT INTO {quote(table)} (id, label) VALUES (%s, %s)', [identity if kind == 'duplicate' else None, 'also preserved'])
                        if kind == 'unique':
                            cursor.execute(f'CREATE UNIQUE INDEX ON {quote(table)} (id)')
                        elif kind == 'partial':
                            cursor.execute(f"CREATE UNIQUE INDEX ON {quote(table)} (id) WHERE label = 'preserved'")
                        elif kind == 'deferrable':
                            cursor.execute(f'ALTER TABLE {quote(table)} ADD UNIQUE (id) DEFERRABLE INITIALLY DEFERRED')
                        elif kind == 'composite':
                            cursor.execute(f'ALTER TABLE {quote(table)} ADD UNIQUE (id, label)')
                    cursor.execute('CREATE TABLE existing_role_grants (module_id uuid, granted boolean)')
                    cursor.execute('INSERT INTO existing_role_grants VALUES (%s, true)', [module_id])
                    snapshots = {}
                    for table in ['rbac_modules', 'rbac_organizations', user_table, 'existing_role_grants']:
                        cursor.execute(f'SELECT * FROM {quote(table)}')
                        snapshots[table] = cursor.fetchall()
                if kind in {'missing', 'partial', 'deferrable', 'composite'}:
                    with self.assertRaisesRegex(DatabaseError, 'no unique constraint|non-deferrable|deferrable unique'):
                        with transaction.atomic():
                            with connection.cursor() as cursor:
                                cursor.execute('CREATE TABLE before_repair (module_id uuid REFERENCES rbac_modules(id))')
                if kind in {'duplicate', 'null'}:
                    with self.assertRaisesRegex(RuntimeError, f'{kind} IDs exist'):
                        with transaction.atomic():
                            with connection.schema_editor() as editor:
                                self.migration.Migration('0060', 'rbac').apply(self.state.clone(), editor)
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT to_regclass('rbac_aioutcomeevidence')")
                        self.assertIsNone(cursor.fetchone()[0])
                        cursor.execute("SELECT COUNT(*) FROM pg_constraint WHERE conrelid='rbac_modules'::regclass AND contype='p'")
                        self.assertEqual(cursor.fetchone()[0], 0)  # module repair rolled back too
                else:
                    # Apply the entire migration, including deferred FK SQL.
                    with connection.schema_editor() as editor:
                        new_state = self.migration.Migration('0060', 'rbac').apply(self.state.clone(), editor)
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT COUNT(*) FROM pg_constraint WHERE conrelid='rbac_aioutcomeevidence'::regclass AND contype='f'")
                        self.assertEqual(cursor.fetchone()[0], 4)
                        cursor.execute("SELECT COUNT(*) FROM pg_index WHERE indrelid IN ('rbac_modules'::regclass, 'rbac_organizations'::regclass)")
                        index_count = cursor.fetchone()[0]
                        cursor.execute('CREATE TABLE after_repair (module_id uuid REFERENCES rbac_modules(id))')
                        cursor.execute('INSERT INTO after_repair VALUES (%s)', [module_id])
                    with self.assertRaises(IntegrityError):
                        with transaction.atomic():
                            with connection.cursor() as cursor:
                                cursor.execute('INSERT INTO after_repair VALUES (%s)', [uuid.uuid4()])
                                cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
                    # Both the repeat preflight and forward migration are no-ops.
                    with connection.schema_editor() as editor:
                        self.migration.ensure_ai_reference_keys(self.state.apps, editor)
                        editor.create_model(new_state.apps.get_model('rbac', 'AIUsageLog'))
                        measurement = import_module('apps.rbac.migrations.0061_ai_measurement_pipeline')
                        new_state = measurement.Migration('0061', 'rbac').apply(new_state, editor)
                        self.followup.Migration('0062', 'rbac').apply(new_state, editor)
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT COUNT(*) FROM pg_index WHERE indrelid IN ('rbac_modules'::regclass, 'rbac_organizations'::regclass)")
                        self.assertEqual(cursor.fetchone()[0], index_count)
                        cursor.execute('SELECT module_id FROM after_repair')
                        self.assertEqual(cursor.fetchall(), [(module_id,)])
                with connection.cursor() as cursor:
                    for table, rows in snapshots.items():
                        cursor.execute(f'SELECT * FROM {quote(table)}')
                        self.assertEqual(cursor.fetchall(), rows)
            finally:
                transaction.set_rollback(True)

    def test_missing_keys_reproduce_and_fix_the_deployment_failure(self):
        self.scenario('missing')

    def test_healthy_keys_are_unchanged(self):
        self.scenario('healthy')

    def test_missing_organization_and_user_keys_are_also_repaired(self):
        self.scenario('profile_only')

    def test_existing_unique_indexes_are_reused(self):
        self.scenario('unique')

    def test_primary_key_on_another_column_is_preserved(self):
        self.scenario('other_pk')

    def test_partial_unique_indexes_do_not_satisfy_foreign_keys(self):
        self.scenario('partial')

    def test_deferrable_unique_constraints_do_not_satisfy_foreign_keys(self):
        self.scenario('deferrable')

    def test_composite_keys_do_not_satisfy_a_single_column_reference(self):
        self.scenario('composite')

    def test_duplicate_ids_stop_without_changing_data(self):
        self.scenario('duplicate')

    def test_null_ids_stop_without_changing_data(self):
        self.scenario('null')

    def test_duplicate_module_ids_stop_without_changing_data(self):
        self.scenario('duplicate', 'module')

    def test_null_module_ids_stop_without_changing_data(self):
        self.scenario('null', 'module')

    def test_duplicate_user_ids_stop_without_changing_data(self):
        self.scenario('duplicate', 'user')

    def test_null_user_ids_stop_without_changing_data(self):
        self.scenario('null', 'user')
