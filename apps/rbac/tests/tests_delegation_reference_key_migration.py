"""Real PostgreSQL DDL regressions; all synthetic schemas/data roll back."""
from importlib import import_module
import unittest
import uuid

from django.conf import settings
from django.db import connection, transaction, DatabaseError, IntegrityError
from django.db.migrations.loader import MigrationLoader


@unittest.skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL foreign-key validation')
class DelegationReferenceKeyMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.migration = import_module('apps.rbac.migrations.0067_role_assignment_windows_and_delegation')
        cls.fields = import_module('apps.rbac.migrations.0068_field_and_row_permissions')
        cls.followup = import_module('apps.rbac.migrations.0069_verify_delegation_reference_keys')
        cls.state = MigrationLoader(connection).project_state([('rbac', '0066_piping_pms_full_module_actions')])

    def scenario(self, kind, invalid_model='role'):
        schema = 'rbac_delegation_fk_' + uuid.uuid4().hex
        quote = connection.ops.quote_name
        role_id, profile_id, assignment_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        user = self.state.apps.get_model(settings.AUTH_USER_MODEL)
        user_table = user._meta.db_table
        invalid_table = {'role': 'rbac_roles', 'profile': 'rbac_user_profiles', 'user': user_table}[invalid_model]
        with transaction.atomic():
            try:
                with connection.cursor() as cursor:
                    cursor.execute(f'CREATE SCHEMA {quote(schema)}')
                    cursor.execute(f'SET LOCAL search_path TO {quote(schema)}')
                    snapshots = {}
                    for table, identity in [('rbac_roles', role_id), ('rbac_user_profiles', profile_id), (user_table, 1)]:
                        key = ' PRIMARY KEY' if kind == 'healthy' or (kind == 'role_only' and table != 'rbac_roles') else ''
                        extra = ', legacy_key integer PRIMARY KEY DEFAULT 1' if kind == 'other_pk' else ''
                        identity_type = user._meta.pk.db_type(connection) if table == user_table else 'uuid'
                        cursor.execute(f"CREATE TABLE {quote(table)} (id {identity_type}{key}, label text DEFAULT 'preserved'{extra})")
                        cursor.execute(f'INSERT INTO {quote(table)} (id) VALUES (%s)', [identity])
                        if kind in {'duplicate', 'null'} and table == invalid_table:
                            cursor.execute(f'INSERT INTO {quote(table)} (id) VALUES (%s)', [identity if kind == 'duplicate' else None])
                        if kind == 'unique':
                            cursor.execute(f'CREATE UNIQUE INDEX ON {quote(table)} (id)')
                        elif kind == 'partial':
                            cursor.execute(f"CREATE UNIQUE INDEX ON {quote(table)} (id) WHERE label = 'preserved'")
                        elif kind == 'deferrable':
                            cursor.execute(f'ALTER TABLE {quote(table)} ADD UNIQUE (id) DEFERRABLE INITIALLY DEFERRED')
                        elif kind == 'composite':
                            cursor.execute(f'ALTER TABLE {quote(table)} ADD UNIQUE (id, label)')
                        cursor.execute(f'SELECT * FROM {quote(table)}')
                        snapshots[table] = cursor.fetchall()
                    cursor.execute('CREATE TABLE rbac_user_roles (id uuid, role_id uuid, user_profile_id uuid, is_primary boolean)')
                    cursor.execute('INSERT INTO rbac_user_roles VALUES (%s,%s,%s,true)', [assignment_id, role_id, profile_id])
                    cursor.execute('CREATE TABLE existing_grants (role_id uuid, allowed boolean)')
                    cursor.execute('INSERT INTO existing_grants VALUES (%s,false)', [role_id])

                if kind in {'role_only', 'missing', 'partial', 'deferrable', 'composite'}:
                    with self.assertRaisesRegex(DatabaseError, 'no unique constraint|non-deferrable|deferrable unique'):
                        with transaction.atomic():
                            with connection.cursor() as cursor:
                                cursor.execute('CREATE TABLE before_repair (role_id uuid REFERENCES rbac_roles(id))')

                if kind in {'duplicate', 'null'}:
                    with self.assertRaisesRegex(RuntimeError, f'{kind} IDs exist'):
                        with transaction.atomic():
                            with connection.schema_editor() as editor:
                                self.migration.Migration('0067', 'rbac').apply(self.state.clone(), editor)
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT to_regclass('rbac_delegations')")
                        self.assertIsNone(cursor.fetchone()[0])
                        cursor.execute("SELECT count(*) FROM pg_constraint WHERE conrelid='rbac_roles'::regclass AND contype='p'")
                        self.assertEqual(cursor.fetchone()[0], 0)
                        cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name='rbac_user_roles'", [schema])
                        self.assertNotIn('effective_from', {row[0] for row in cursor.fetchall()})
                else:
                    with connection.schema_editor() as editor:
                        state67 = self.migration.Migration('0067', 'rbac').apply(self.state.clone(), editor)
                    with connection.schema_editor() as editor:
                        state68 = self.fields.Migration('0068', 'rbac').apply(state67.clone(), editor)
                    with connection.cursor() as cursor:
                        for table, count in [('rbac_delegations', 4), ('rbac_field_permissions', 2), ('rbac_row_level_permissions', 2)]:
                            cursor.execute('SELECT count(*) FROM pg_constraint WHERE conrelid=to_regclass(%s) AND contype=\'f\'', [table])
                            self.assertEqual(cursor.fetchone()[0], count)
                        cursor.execute('SELECT effective_from,expires_at FROM rbac_user_roles')
                        self.assertEqual(cursor.fetchall(), [(None, None)])
                        cursor.execute('INSERT INTO rbac_delegations (id,created_at,updated_at,reason,is_active,delegator_id,delegatee_id,role_id,delegated_by_id) VALUES (%s,NOW(),NOW(),\'\',true,%s,%s,%s,1)',
                                       [uuid.uuid4(), profile_id, profile_id, role_id])
                        cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
                        cursor.execute("SELECT count(*) FROM pg_index WHERE indrelid IN ('rbac_roles'::regclass,'rbac_user_profiles'::regclass,%s::regclass)", [user_table])
                        index_count = cursor.fetchone()[0]
                    with self.assertRaises(IntegrityError):
                        with transaction.atomic():
                            with connection.cursor() as cursor:
                                cursor.execute('UPDATE rbac_delegations SET role_id=%s', [uuid.uuid4()])
                                cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
                    with connection.schema_editor() as editor:
                        self.migration.ensure_delegation_reference_keys(self.state.apps, editor)
                        self.followup.Migration('0069', 'rbac').apply(state68.clone(), editor)
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT count(*) FROM pg_index WHERE indrelid IN ('rbac_roles'::regclass,'rbac_user_profiles'::regclass,%s::regclass)", [user_table])
                        self.assertEqual(cursor.fetchone()[0], index_count)
                    if kind == 'missing':
                        with connection.schema_editor() as editor:
                            self.fields.Migration('0068', 'rbac').unapply(state67.clone(), editor)
                            self.migration.Migration('0067', 'rbac').unapply(self.state.clone(), editor)
                        with connection.cursor() as cursor:
                            cursor.execute("SELECT to_regclass('rbac_delegations')")
                            self.assertIsNone(cursor.fetchone()[0])
                with connection.cursor() as cursor:
                    for table, rows in snapshots.items():
                        cursor.execute(f'SELECT * FROM {quote(table)}')
                        self.assertEqual(cursor.fetchall(), rows)
                    cursor.execute('SELECT id,role_id,user_profile_id,is_primary FROM rbac_user_roles')
                    self.assertEqual(cursor.fetchall(), [(assignment_id, role_id, profile_id, True)])
                    cursor.execute('SELECT role_id,allowed FROM existing_grants')
                    self.assertEqual(cursor.fetchall(), [(role_id, False)])
            finally:
                transaction.set_rollback(True)

    def test_missing_role_key_reproduces_the_deployed_failure(self):
        self.scenario('role_only')

    def test_all_missing_keys_and_reverse_preserve_assignments(self):
        self.scenario('missing')

    def test_healthy_primary_keys_are_unchanged(self):
        self.scenario('healthy')

    def test_eligible_unique_indexes_are_reused(self):
        self.scenario('unique')

    def test_primary_key_on_another_column_is_preserved(self):
        self.scenario('other_pk')

    def test_ineligible_indexes_do_not_satisfy_foreign_keys(self):
        for kind in ('partial', 'deferrable', 'composite'):
            with self.subTest(kind=kind):
                self.scenario(kind)

    def test_duplicate_ids_fail_without_modifying_rows_or_schema(self):
        for model in ('role', 'profile', 'user'):
            with self.subTest(model=model):
                self.scenario('duplicate', model)

    def test_null_ids_fail_without_modifying_rows_or_schema(self):
        for model in ('role', 'profile', 'user'):
            with self.subTest(model=model):
                self.scenario('null', model)
