"""PostgreSQL-only smoke checks; all tables live in a disposable test database."""
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.rbac.database_maintenance import quote_identifier
from apps.rbac.database_maintenance_views import database_table_action, database_tables
from apps.rbac.models import AuditLog


@skipUnless(connection.vendor == 'postgresql', 'Requires an isolated PostgreSQL test database.')
@override_settings(DATABASE_MAINTENANCE_ENABLED=True)
class PostgreSQLMaintenanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            'pg-cleanup-superuser', email='pg-cleanup@example.test', is_superuser=True,
        )

    def setUp(self):
        self.factory = APIRequestFactory()
        self.sql('CREATE TABLE cleanup_target (id integer PRIMARY KEY, label text)')
        self.sql("INSERT INTO cleanup_target VALUES (1, 'a'), (2, 'b')")
        self.sql('CREATE TABLE cleanup_neighbor (id integer PRIMARY KEY, label text)')
        self.sql("INSERT INTO cleanup_neighbor VALUES (1, 'untouched')")

    def sql(self, statement):
        with connection.cursor() as cursor:
            cursor.execute(statement)
            return cursor.fetchall() if cursor.description else None

    def inventory(self):
        request = self.factory.get('/api/v1/rbac/admin/database/tables/')
        force_authenticate(request, self.user)
        response = database_tables(request)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['engine'], 'postgresql')
        return {row['name']: row for row in response.data['tables']}

    def action(self, operation='clear', name='public.cleanup_target'):
        request = self.factory.post(
            '/api/v1/rbac/admin/database/tables/action/',
            {'table': name, 'action': operation, 'confirmation': name}, format='json',
        )
        force_authenticate(request, self.user)
        return database_table_action(request)

    def test_inventory_and_clear_audit(self):
        self.sql('ANALYZE cleanup_target')
        row = self.inventory()['public.cleanup_target']
        self.assertTrue(row['can_clear'])
        self.assertTrue(row['can_drop'])
        self.assertTrue(row['row_count_is_estimate'])
        self.assertEqual(row['row_count'], 2)
        response = self.action()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['deleted_rows'], 2)
        self.assertEqual(self.sql('SELECT count(*) FROM cleanup_target')[0][0], 0)
        self.assertEqual(self.sql('SELECT count(*) FROM cleanup_neighbor')[0][0], 1)
        self.assertTrue(AuditLog.objects.filter(
            resource_type='DatabaseTable', resource_repr='public.cleanup_target',
            changes__operation='clear', changes__deleted_rows=2,
        ).exists())

    def test_drop_audit(self):
        response = self.action('drop')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(self.sql("SELECT to_regclass('public.cleanup_target')")[0][0])
        self.assertTrue(AuditLog.objects.filter(
            resource_repr='public.cleanup_target', changes__operation='drop',
        ).exists())

    def test_cross_schema_fk_blocks_both_operations(self):
        self.sql('CREATE SCHEMA cleanup_elsewhere')
        self.sql('CREATE TABLE cleanup_elsewhere.child (id integer PRIMARY KEY, parent_id integer REFERENCES cleanup_target(id) ON DELETE CASCADE)')
        self.sql('INSERT INTO cleanup_elsewhere.child VALUES (1, 1)')
        row = self.inventory()['public.cleanup_target']
        self.assertEqual(row['referenced_by'], ['cleanup_elsewhere.child'])
        for operation in ('clear', 'drop'):
            response = self.action(operation)
            self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(self.sql('SELECT count(*) FROM cleanup_target')[0][0], 2)
        self.assertEqual(self.sql('SELECT count(*) FROM cleanup_elsewhere.child')[0][0], 1)

    def test_custom_trigger_blocks_both_operations(self):
        self.sql("CREATE FUNCTION cleanup_touch() RETURNS trigger LANGUAGE plpgsql AS $$BEGIN DELETE FROM cleanup_neighbor; RETURN OLD; END$$")
        self.sql('CREATE TRIGGER cleanup_touch_trigger BEFORE DELETE ON cleanup_target FOR EACH ROW EXECUTE FUNCTION cleanup_touch()')
        for operation in ('clear', 'drop'):
            response = self.action(operation)
            self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(self.sql('SELECT count(*) FROM cleanup_neighbor')[0][0], 1)

    def test_delete_rule_blocks_both_operations(self):
        self.sql('CREATE RULE cleanup_delete_rule AS ON DELETE TO cleanup_target DO ALSO DELETE FROM cleanup_neighbor')
        for operation in ('clear', 'drop'):
            response = self.action(operation)
            self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(self.sql('SELECT count(*) FROM cleanup_neighbor')[0][0], 1)

    def test_row_security_blocks_both_operations(self):
        self.sql('ALTER TABLE cleanup_target ENABLE ROW LEVEL SECURITY')
        for operation in ('clear', 'drop'):
            response = self.action(operation)
            self.assertEqual(response.status_code, 409, response.data)

    def test_inheritance_blocks_parent_and_child(self):
        self.sql('CREATE TABLE cleanup_descendant () INHERITS (cleanup_target)')
        for name in ('public.cleanup_target', 'public.cleanup_descendant'):
            for operation in ('clear', 'drop'):
                response = self.action(operation, name)
                self.assertEqual(response.status_code, 409, response.data)

    def test_partitioned_table_blocks_both_operations(self):
        self.sql('CREATE TABLE cleanup_partitioned (id integer) PARTITION BY RANGE (id)')
        self.sql('CREATE TABLE cleanup_partition_one PARTITION OF cleanup_partitioned FOR VALUES FROM (0) TO (10)')
        for name in ('public.cleanup_partitioned', 'public.cleanup_partition_one'):
            for operation in ('clear', 'drop'):
                response = self.action(operation, name)
                self.assertEqual(response.status_code, 409, response.data)

    def test_view_dependency_blocks_drop_only(self):
        self.sql('CREATE VIEW cleanup_view AS SELECT * FROM cleanup_target')
        response = self.action('drop')
        self.assertEqual(response.status_code, 409, response.data)
        response = self.action('clear')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.sql('SELECT count(*) FROM cleanup_view')[0][0], 0)

    def test_audit_failure_rolls_back_clear_and_drop(self):
        with patch('apps.rbac.database_maintenance_views.create_audit_log', side_effect=RuntimeError('test audit failure')):
            for operation in ('clear', 'drop'):
                response = self.action(operation)
                self.assertEqual(response.status_code, 503, response.data)
                self.assertEqual(self.sql('SELECT count(*) FROM cleanup_target')[0][0], 2)

    def test_catalog_quotes_embedded_identifier(self):
        name = 'cleanup_odd";DROP TABLE cleanup_neighbor;--'
        quoted = quote_identifier(name)
        self.sql(f'CREATE TABLE {quoted} (id integer)')
        self.sql(f'INSERT INTO {quoted} VALUES (1)')
        identity = 'public."cleanup_odd"";DROP TABLE cleanup_neighbor;--"'
        self.assertIn(identity, self.inventory())
        response = self.action('clear', identity)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.sql('SELECT count(*) FROM cleanup_neighbor')[0][0], 1)
        response = self.action('drop', identity)
        self.assertEqual(response.status_code, 200, response.data)

    def test_enabled_sql_drop_event_trigger_blocks_drop(self):
        self.sql("CREATE FUNCTION cleanup_drop_event() RETURNS event_trigger LANGUAGE plpgsql AS $$BEGIN DELETE FROM cleanup_neighbor; END$$")
        self.sql("CREATE EVENT TRIGGER cleanup_drop_event_trigger ON sql_drop EXECUTE FUNCTION cleanup_drop_event()")
        response = self.action('drop')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(self.sql('SELECT count(*) FROM cleanup_neighbor')[0][0], 1)

    def test_dependency_is_rechecked_under_lock(self):
        import apps.rbac.database_maintenance_views as views
        original = views._find_action_table
        calls = []
        def add_dependency_before_recheck(cursor, name, operation):
            calls.append(name)
            if len(calls) == 2:
                cursor.execute('CREATE TABLE cleanup_late_child (parent_id integer REFERENCES cleanup_target(id) ON DELETE CASCADE)')
                cursor.execute('INSERT INTO cleanup_late_child VALUES (1)')
            return original(cursor, name, operation)
        with patch.object(views, '_find_action_table', side_effect=add_dependency_before_recheck):
            response = self.action()
        self.assertEqual(len(calls), 2)
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(self.sql('SELECT count(*) FROM cleanup_target')[0][0], 2)

    def test_core_table_cannot_be_cleared_or_dropped(self):
        for operation in ('clear', 'drop'):
            response = self.action(operation, 'public.rbac_audit_logs')
            self.assertEqual(response.status_code, 409, response.data)
