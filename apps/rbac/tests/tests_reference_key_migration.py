"""Actual PostgreSQL migration tests; temporary schemas and all DDL roll back.

Run through manage.py shell with unittest against local PostgreSQL. SQLite skips
these tests because it cannot reproduce PostgreSQL foreign-key prerequisites.
"""
from importlib import import_module
import unittest
import uuid

from django.db import connection, transaction, IntegrityError, DatabaseError
from django.db.migrations.loader import MigrationLoader


@unittest.skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL foreign-key validation')
class OverrideReferenceKeyMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.migration = import_module('apps.rbac.migrations.0053_user_permission_overrides')
        cls.followup = import_module('apps.rbac.migrations.0057_verify_override_reference_keys')
        cls.state = MigrationLoader(connection).project_state([('rbac', '0052_replace_broad_business_grants')])

    def scenario(self, kind):
        schema = 'rbac_fk_test_' + uuid.uuid4().hex
        quote = connection.ops.quote_name
        permission_id, profile_id = uuid.uuid4(), uuid.uuid4()
        with transaction.atomic():
            try:
                with connection.cursor() as cursor:
                    cursor.execute(f'CREATE SCHEMA {quote(schema)}')
                    cursor.execute(f'SET LOCAL search_path TO {quote(schema)}')
                    for table, identity in [('rbac_permissions', permission_id), ('rbac_user_profiles', profile_id)]:
                        key = ' PRIMARY KEY' if kind == 'healthy' or (kind == 'profile_only' and table == 'rbac_permissions') else ''
                        extra = ', legacy_key integer PRIMARY KEY DEFAULT 1' if kind == 'other_pk' else ''
                        cursor.execute(f'CREATE TABLE {quote(table)} (id uuid{key}, label text DEFAULT \'preserved\'{extra})')
                        cursor.execute(f'INSERT INTO {quote(table)} (id) VALUES (%s)', [identity])
                        if kind in {'duplicate', 'null'} and table == 'rbac_user_profiles':
                            cursor.execute(f'INSERT INTO {quote(table)} (id, label) VALUES (%s, %s)', [identity if kind == 'duplicate' else None, 'also preserved'])
                        if kind == 'unique':
                            cursor.execute(f'CREATE UNIQUE INDEX ON {quote(table)} (id)')
                        elif kind == 'partial':
                            cursor.execute(f"CREATE UNIQUE INDEX ON {quote(table)} (id) WHERE label = 'preserved'")
                        elif kind == 'deferrable':
                            cursor.execute(f'ALTER TABLE {quote(table)} ADD UNIQUE (id) DEFERRABLE INITIALLY DEFERRED')
                        elif kind == 'composite':
                            cursor.execute(f'ALTER TABLE {quote(table)} ADD UNIQUE (id, label)')
                    cursor.execute('CREATE TABLE existing_role_grants (permission_id uuid, granted boolean)')
                    cursor.execute('INSERT INTO existing_role_grants VALUES (%s, true)', [permission_id])
                    snapshots = {}
                    for table in ['rbac_permissions', 'rbac_user_profiles', 'existing_role_grants']:
                        cursor.execute(f'SELECT * FROM {quote(table)}')
                        snapshots[table] = cursor.fetchall()
                if kind in {'missing', 'partial', 'deferrable', 'composite'}:
                    with self.assertRaisesRegex(DatabaseError, 'no unique constraint|non-deferrable|deferrable unique'):
                        with transaction.atomic():
                            with connection.cursor() as cursor:
                                cursor.execute('CREATE TABLE before_repair (permission_id uuid REFERENCES rbac_permissions(id))')
                if kind in {'duplicate', 'null'}:
                    with self.assertRaisesRegex(RuntimeError, f'{kind} IDs exist'):
                        with transaction.atomic():
                            with connection.schema_editor() as editor:
                                self.migration.Migration('0053', 'rbac').apply(self.state.clone(), editor)
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT to_regclass('rbac_userpermissionoverride')")
                        self.assertIsNone(cursor.fetchone()[0])
                        cursor.execute("SELECT COUNT(*) FROM pg_constraint WHERE conrelid='rbac_permissions'::regclass AND contype='p'")
                        self.assertEqual(cursor.fetchone()[0], 0)  # permission repair rolled back too
                else:
                    # Apply the entire migration, including deferred FK SQL.
                    with connection.schema_editor() as editor:
                        new_state = self.migration.Migration('0053', 'rbac').apply(self.state.clone(), editor)
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT COUNT(*) FROM pg_constraint WHERE conrelid='rbac_userpermissionoverride'::regclass AND contype='f'")
                        self.assertEqual(cursor.fetchone()[0], 2)
                        cursor.execute('INSERT INTO rbac_userpermissionoverride (created_at, updated_at, allowed, permission_id, user_profile_id) VALUES (NOW(), NOW(), false, %s, %s)', [permission_id, profile_id])
                        cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
                        cursor.execute("SELECT COUNT(*) FROM pg_index WHERE indrelid IN ('rbac_permissions'::regclass, 'rbac_user_profiles'::regclass)")
                        index_count = cursor.fetchone()[0]
                    with self.assertRaises(IntegrityError):
                        with transaction.atomic():
                            with connection.cursor() as cursor:
                                cursor.execute('INSERT INTO rbac_userpermissionoverride (created_at, updated_at, allowed, permission_id, user_profile_id) VALUES (NOW(), NOW(), true, %s, %s)', [uuid.uuid4(), profile_id])
                    with self.assertRaises(IntegrityError):
                        with transaction.atomic():
                            with connection.cursor() as cursor:
                                cursor.execute('INSERT INTO rbac_userpermissionoverride (created_at, updated_at, allowed, permission_id, user_profile_id) VALUES (NOW(), NOW(), true, %s, %s)', [permission_id, profile_id])
                    # Both the repeat preflight and forward migration are no-ops.
                    with connection.schema_editor() as editor:
                        self.migration.ensure_override_reference_keys(self.state.apps, editor)
                        self.followup.Migration('0057', 'rbac').apply(new_state, editor)
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT COUNT(*) FROM pg_index WHERE indrelid IN ('rbac_permissions'::regclass, 'rbac_user_profiles'::regclass)")
                        self.assertEqual(cursor.fetchone()[0], index_count)
                        cursor.execute('SELECT allowed FROM rbac_userpermissionoverride')
                        self.assertEqual(cursor.fetchall(), [(False,)])
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

    def test_missing_profile_key_is_also_repaired(self):
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
