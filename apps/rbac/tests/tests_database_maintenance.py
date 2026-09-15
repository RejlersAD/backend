"""Database-cleaning API checks against disposable tables in the test database.

Run with --settings=config.settings_database_maintenance_test for isolated SQLite coverage.
The raw tables below are created inside TestCase transactions and rolled back.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import OperationalError, connection, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.rbac.audit_context import current_audits
from apps.rbac.database_maintenance_views import database_table_action, database_tables
from apps.rbac.models import (
    AuditLog, Organization, Role, RoleModule, RolePermission,
    UserPermissionOverride, UserProfile, UserRole,
)
from apps.rbac.utils import create_audit_log
from apps.usage_tracking.models import UsageLog


@override_settings(DATABASE_MAINTENANCE_ENABLED=False)
class DatabaseMaintenanceDisabledTests(SimpleTestCase):
    def test_disabled_inventory_and_actions_never_access_the_database(self):
        factory = APIRequestFactory()
        with patch('apps.rbac.database_maintenance_views.table_catalog') as catalog, patch(
            'apps.rbac.database_maintenance_views.create_audit_log'
        ) as audit:
            for authenticated in (False, True):
                for method, path, view in (
                    ('get', '/api/v1/rbac/admin/database/tables/', database_tables),
                    ('post', '/api/v1/rbac/admin/database/tables/action/', database_table_action),
                ):
                    with self.subTest(authenticated=authenticated, method=method):
                        request = getattr(factory, method)(path, {}, format='json')
                        if authenticated:
                            force_authenticate(request, user=SimpleNamespace(
                                is_authenticated=True, is_active=True, is_superuser=True,
                            ))
                        response = view(request)
                        self.assertEqual(response.status_code, 404)
                        self.assertEqual(str(response.data['detail']), 'Database cleaning is temporarily disabled.')
            catalog.assert_not_called()
            audit.assert_not_called()


@override_settings(DATABASE_MAINTENANCE_ENABLED=True)
class DatabaseMaintenanceTests(TestCase):
    table = 'console_cleanup_items'
    neighbor = 'console_cleanup_neighbor'
    child = 'console_cleanup_children'

    @classmethod
    def setUpTestData(cls):
        cls.superuser = get_user_model().objects.create_user(
            'database-superuser', email='database-admin@example.test', is_superuser=True,
        )
        cls.organization = Organization.objects.create(
            name='Database maintenance test', code='database-maintenance-test',
        )

    def setUp(self):
        self.factory = APIRequestFactory()
        for table in (self.table, self.neighbor):
            with connection.cursor() as cursor:
                cursor.execute(
                    f'CREATE TABLE {connection.ops.quote_name(table)} '
                    '(id integer PRIMARY KEY, label varchar(100))'
                )
                cursor.executemany(
                    f'INSERT INTO {connection.ops.quote_name(table)} (id, label) VALUES (%s, %s)',
                    [(1, 'first'), (2, 'second')],
                )

    def rows(self, table=None):
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT * FROM {connection.ops.quote_name(table or self.table)} ORDER BY id')
            return cursor.fetchall()

    def inventory(self, user=None):
        request = self.factory.get('/api/v1/rbac/admin/database/tables/')
        force_authenticate(request, self.superuser if user is None else user)
        return database_tables(request)

    def action(self, action='clear', table=None, user=None, **overrides):
        table = self.table if table is None else table
        payload = {'table': table, 'action': action, 'confirmation': table}
        payload.update(overrides)
        request = self.factory.post(
            '/api/v1/rbac/admin/database/tables/action/', payload, format='json',
        )
        force_authenticate(request, self.superuser if user is None else user)
        return database_table_action(request)

    def role_user(self, code, *, active_role=True):
        user = get_user_model().objects.create_user(
            f'database-{code}', email=f'database-role-{code}@example.test',
        )
        profile, _ = UserProfile.objects.get_or_create(
            user=user,
            defaults={'organization': self.organization, 'employee_id': f'DATABASE-{code.upper()}'},
        )
        role, _ = Role.objects.get_or_create(
            code=code, defaults={'name': f'Database {code}', 'is_active': active_role},
        )
        if role.is_active != active_role:
            role.is_active = active_role
            role.save(update_fields=['is_active'])
        profile.roles.add(role)
        return user

    def add_child_table(self, *, with_row=False):
        with connection.cursor() as cursor:
            cursor.execute(
                f'CREATE TABLE {connection.ops.quote_name(self.child)} '
                '(id integer PRIMARY KEY, item_id integer, '
                f'FOREIGN KEY (item_id) REFERENCES {connection.ops.quote_name(self.table)} (id) '
                'ON DELETE CASCADE)'
            )
            if with_row:
                cursor.execute(
                    f'INSERT INTO {connection.ops.quote_name(self.child)} (id, item_id) VALUES (1, 1)'
                )

    def test_inventory_includes_disposable_and_core_tables_with_capabilities(self):
        response = self.inventory()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['database'], 'default')
        self.assertEqual(response.data['engine'], connection.vendor)
        self.assertTrue(response.data['can_manage'])
        tables = {row['name']: row for row in response.data['tables']}
        self.assertIn(self.table, tables)
        self.assertIn(self.neighbor, tables)
        row = tables[self.table]
        self.assertEqual(row['row_count'], 2)
        self.assertFalse(row['row_count_is_estimate'])
        self.assertFalse(row['managed'])
        self.assertIsNone(row['model'])
        self.assertEqual(row['referenced_by'], [])
        self.assertTrue(row['can_clear'])
        self.assertTrue(row['can_drop'])
        self.assertFalse(row['clear_blocked_reason'])
        self.assertFalse(row['drop_blocked_reason'])
        for model in (get_user_model(), Organization, Role, UserProfile, AuditLog):
            with self.subTest(model=model.__name__):
                protected = tables[model._meta.db_table]
                self.assertTrue(protected['managed'])
                self.assertTrue(protected['model'])
                self.assertFalse(protected['can_clear'])
                self.assertFalse(protected['can_drop'])
                self.assertTrue(protected['clear_blocked_reason'])
                self.assertTrue(protected['drop_blocked_reason'])
        # Application-owned tables remain available when no dependency blocks them.
        application_table = tables[UsageLog._meta.db_table]
        self.assertTrue(application_table['managed'])
        self.assertEqual(application_table['model'], UsageLog._meta.label)
        self.assertEqual(application_table['referenced_by'], [])
        self.assertTrue(application_table['can_clear'])
        self.assertTrue(application_table['can_drop'])

    def test_anonymous_users_cannot_list_or_mutate(self):
        list_request = self.factory.get('/database/tables/')
        action_request = self.factory.post(
            '/database/actions/',
            {'table': self.table, 'action': 'clear', 'confirmation': self.table},
            format='json',
        )
        self.assertIn(database_tables(list_request).status_code, (401, 403))
        self.assertIn(database_table_action(action_request).status_code, (401, 403))
        self.assertEqual(len(self.rows()), 2)

    def test_ordinary_and_staff_users_cannot_list_or_mutate(self):
        for is_staff in (False, True):
            with self.subTest(is_staff=is_staff):
                user = get_user_model().objects.create_user(
                    f'database-ordinary-{is_staff}',
                    email=f'database-ordinary-{is_staff}@example.test', is_staff=is_staff,
                )
                self.assertEqual(self.inventory(user=user).status_code, 403)
                self.assertEqual(self.action(user=user).status_code, 403)
                self.assertEqual(self.action(action='drop', user=user).status_code, 403)
        self.assertEqual(len(self.rows()), 2)

    def test_admin_and_ict_admin_can_only_view_inventory(self):
        for code in ('admin', 'ict_admin'):
            with self.subTest(role=code):
                user = self.role_user(code)
                response = self.inventory(user=user)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertFalse(response.data['can_manage'])
                row = next(row for row in response.data['tables'] if row['name'] == self.table)
                self.assertFalse(row['can_clear'])
                self.assertFalse(row['can_drop'])
                self.assertEqual(self.action(user=user).status_code, 403)
                self.assertEqual(self.action(action='drop', user=user).status_code, 403)
        self.assertEqual(len(self.rows()), 2)

    def test_active_rbac_super_admin_can_clean_database(self):
        user = self.role_user('super_admin')
        self.assertFalse(user.is_superuser)
        inventory = self.inventory(user=user)
        self.assertEqual(inventory.status_code, 200)
        self.assertTrue(inventory.data['can_manage'])
        response = self.action(user=user)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.rows(), [])

    def test_inactive_super_admin_role_does_not_grant_access(self):
        user = self.role_user('super_admin', active_role=False)
        self.assertEqual(self.inventory(user=user).status_code, 403)
        self.assertEqual(self.action(user=user).status_code, 403)
        self.assertEqual(len(self.rows()), 2)

    def test_inactive_superuser_is_denied_even_with_force_authentication(self):
        self.superuser.is_active = False
        self.superuser.save(update_fields=['is_active'])
        self.assertEqual(self.inventory().status_code, 403)
        self.assertEqual(self.action().status_code, 403)
        self.assertEqual(self.action(action='drop').status_code, 403)
        self.assertEqual(len(self.rows()), 2)

    def test_suspended_deleted_and_locked_admin_profiles_are_denied(self):
        user = self.role_user('super_admin')
        invalid_states = (
            {'status': 'suspended'},
            {'is_deleted': True},
            {'locked_until': timezone.now() + timedelta(hours=1)},
        )
        for is_superuser in (False, True):
            get_user_model().objects.filter(pk=user.pk).update(is_superuser=is_superuser)
            for invalid in invalid_states:
                with self.subTest(is_superuser=is_superuser, invalid=invalid):
                    state = {'status': 'active', 'is_deleted': False, 'locked_until': None}
                    state.update(invalid)
                    UserProfile.objects.filter(user=user).update(**state)
                    fresh_user = get_user_model().objects.get(pk=user.pk)
                    self.assertEqual(self.inventory(user=fresh_user).status_code, 403)
                    for action in ('clear', 'drop'):
                        self.assertEqual(self.action(action=action, user=fresh_user).status_code, 403)
        self.assertEqual(len(self.rows()), 2)

    def test_expired_profile_lock_does_not_block_an_active_super_admin(self):
        user = self.role_user('super_admin')
        UserProfile.objects.filter(user=user).update(locked_until=timezone.now() - timedelta(minutes=1))
        user = get_user_model().objects.get(pk=user.pk)
        self.assertEqual(self.inventory(user=user).status_code, 200)
        response = self.action(user=user)
        self.assertEqual(response.status_code, 200, response.data)

    def test_clear_removes_only_selected_rows_and_preserves_schema(self):
        audit_count = AuditLog.objects.count()
        response = self.action()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['success'])
        self.assertEqual(response.data['table'], self.table)
        self.assertEqual(response.data['action'], 'clear')
        self.assertEqual(response.data['deleted_rows'], 2)
        self.assertTrue(response.data['message'])
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.rows(self.neighbor), [(1, 'first'), (2, 'second')])
        with connection.cursor() as cursor:
            cursor.execute(
                f'INSERT INTO {connection.ops.quote_name(self.table)} (id, label) VALUES (%s, %s)',
                [3, 'schema remains usable'],
            )
        self.assertEqual(self.rows(), [(3, 'schema remains usable')])
        self.assertEqual(AuditLog.objects.count(), audit_count + 1)
        entry = AuditLog.objects.latest('timestamp')
        self.assertEqual(entry.user_id, self.superuser.pk)
        self.assertTrue(entry.success)
        self.assertIn(self.table, str(entry.resource_repr) + str(entry.metadata) + str(entry.changes))

    def test_drop_removes_only_selected_table_and_records_audit(self):
        audit_count = AuditLog.objects.count()
        response = self.action(action='drop')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['success'])
        self.assertEqual(response.data['table'], self.table)
        self.assertEqual(response.data['action'], 'drop')
        self.assertIn('deleted_rows', response.data)
        self.assertTrue(response.data['message'])
        self.assertNotIn(self.table, connection.introspection.table_names())
        self.assertEqual(self.rows(self.neighbor), [(1, 'first'), (2, 'second')])
        self.assertEqual(AuditLog.objects.count(), audit_count + 1)

    def test_clear_empty_table_succeeds_with_zero_deleted_rows(self):
        self.assertEqual(self.action().status_code, 200)
        response = self.action()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['deleted_rows'], 0)

    def test_exact_typed_confirmation_is_required_for_both_actions(self):
        audit_count = AuditLog.objects.count()
        for action in ('clear', 'drop'):
            for confirmation in ('', 'wrong table', self.table.upper(), f' {self.table} ', None, True, []):
                with self.subTest(action=action, confirmation=confirmation):
                    response = self.action(action=action, confirmation=confirmation)
                    self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(AuditLog.objects.count(), audit_count)

    def test_invalid_json_fields_are_rejected_without_changing_data(self):
        payloads = [
            {},
            [],
            {'action': 'clear', 'confirmation': self.table},
            {'table': self.table, 'confirmation': self.table},
            {'table': self.table, 'action': 'clear'},
        ]
        payloads.extend(
            {'table': value, 'action': 'clear', 'confirmation': value}
            for value in (None, 42, True, [], {}, '')
        )
        payloads.extend(
            {'table': self.table, 'action': value, 'confirmation': self.table}
            for value in ('truncate', 'delete', 'DROP', None, [], {})
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                request = self.factory.post('/database/actions/', payload, format='json')
                force_authenticate(request, self.superuser)
                response = database_table_action(request)
                self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(len(self.rows()), 2)

    def test_unknown_and_injection_table_names_never_execute_sql(self):
        missing = self.action(table='console_cleanup_missing')
        self.assertEqual(missing.status_code, 404, missing.data)
        names = (
            f'{self.table}; DROP TABLE {self.neighbor}; --',
            f'{self.table}"; DROP TABLE "{self.neighbor}"; --',
            "' OR 1=1 --",
        )
        for table in names:
            for action in ('clear', 'drop'):
                with self.subTest(table=table, action=action):
                    response = self.action(table=table, action=action)
                    self.assertIn(response.status_code, (400, 404), response.data)
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(len(self.rows(self.neighbor)), 2)

    def test_core_tables_are_protected_from_clear_and_drop(self):
        protected_models = (get_user_model(), Organization, Role, UserProfile, AuditLog)
        for model in protected_models:
            before = model.objects.count()
            for action in ('clear', 'drop'):
                with self.subTest(table=model._meta.db_table, action=action):
                    response = self.action(table=model._meta.db_table, action=action)
                    self.assertEqual(response.status_code, 409, response.data)
                    self.assertEqual(model.objects.count(), before)

    def test_explicit_permission_and_role_through_tables_are_protected(self):
        inventory = self.inventory()
        self.assertEqual(inventory.status_code, 200, inventory.data)
        tables = {row['name']: row for row in inventory.data['tables']}
        for model in (RoleModule, RolePermission, UserRole, UserPermissionOverride):
            table = model._meta.db_table
            with self.subTest(table=table):
                self.assertFalse(tables[table]['can_clear'])
                self.assertFalse(tables[table]['can_drop'])
                for action in ('clear', 'drop'):
                    response = self.action(table=table, action=action)
                    self.assertEqual(response.status_code, 409, response.data)

    def test_migration_history_table_is_protected_even_without_registered_model(self):
        with connection.cursor() as cursor:
            cursor.execute('CREATE TABLE IF NOT EXISTS django_migrations (id integer PRIMARY KEY)')
        row = next(row for row in self.inventory().data['tables'] if row['name'] == 'django_migrations')
        self.assertFalse(row['can_clear'])
        self.assertFalse(row['can_drop'])
        for action in ('clear', 'drop'):
            response = self.action(table='django_migrations', action=action)
            self.assertEqual(response.status_code, 409, response.data)
        self.assertIn('django_migrations', connection.introspection.table_names())

    def test_empty_incoming_foreign_key_blocks_both_actions(self):
        self.add_child_table()
        row = next(row for row in self.inventory().data['tables'] if row['name'] == self.table)
        self.assertIn(self.child, row['referenced_by'])
        self.assertFalse(row['can_clear'])
        self.assertFalse(row['can_drop'])
        self.assertTrue(row['clear_blocked_reason'])
        self.assertTrue(row['drop_blocked_reason'])
        for action in ('clear', 'drop'):
            response = self.action(action=action)
            self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(self.rows(self.child), [])

    def test_cascade_foreign_key_never_deletes_related_rows(self):
        self.add_child_table(with_row=True)
        for action in ('clear', 'drop'):
            response = self.action(action=action)
            self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(self.rows(self.child), [(1, 1)])
        self.assertEqual(len(self.rows()), 2)

    def test_mixed_case_reference_cannot_bypass_foreign_key_protection(self):
        with connection.cursor() as cursor:
            cursor.execute(
                f'CREATE TABLE {connection.ops.quote_name(self.child)} '
                '(id integer PRIMARY KEY, item_id integer, '
                f'FOREIGN KEY (item_id) REFERENCES {self.table.upper()} (id) ON DELETE CASCADE)'
            )
            cursor.execute(
                f'INSERT INTO {connection.ops.quote_name(self.child)} (id, item_id) VALUES (1, 1)'
            )
        row = next(row for row in self.inventory().data['tables'] if row['name'] == self.table)
        self.assertIn(self.child, row['referenced_by'])
        for action in ('clear', 'drop'):
            response = self.action(action=action)
            self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(self.rows(self.child), [(1, 1)])

    def test_sqlite_virtual_and_shadow_tables_cannot_be_mutated(self):
        table = 'console_cleanup_search'
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(f'CREATE VIRTUAL TABLE {table} USING fts5(content)')
                cursor.execute(f'INSERT INTO {table} (content) VALUES (%s)', ['search content'])
        except OperationalError as exc:
            if 'no such module' in str(exc):
                self.skipTest('SQLite build does not include FTS5')
            raise
        inventory = self.inventory()
        self.assertEqual(inventory.status_code, 200, inventory.data)
        matching = [row for row in inventory.data['tables'] if row['name'].startswith(table)]
        self.assertGreater(len(matching), 1)
        for row in matching:
            with self.subTest(table=row['name']):
                self.assertFalse(row['can_clear'])
                self.assertFalse(row['can_drop'])
                for action in ('clear', 'drop'):
                    response = self.action(table=row['name'], action=action)
                    self.assertEqual(response.status_code, 409, response.data)
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT content FROM {table} WHERE {table} MATCH %s', ['search'])
            self.assertEqual(cursor.fetchall(), [('search content',)])

    def test_known_table_with_embedded_quotes_is_safely_addressed(self):
        table = 'console_cleanup_"quoted; table'
        quoted = '"console_cleanup_""quoted; table"'
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE TABLE {quoted} (id integer PRIMARY KEY)')
            cursor.execute(f'INSERT INTO {quoted} (id) VALUES (1)')
        row = next(row for row in self.inventory().data['tables'] if row['name'] == table)
        self.assertEqual(row['row_count'], 1)
        response = self.action(table=table)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['deleted_rows'], 1)
        response = self.action(table=table, action='drop')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn(table, connection.introspection.table_names())
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(len(self.rows(self.neighbor)), 2)

    def test_sqlite_view_blocks_drop_and_keeps_view_usable(self):
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE VIEW console_cleanup_view AS SELECT * FROM {self.table}')
        row = next(row for row in self.inventory().data['tables'] if row['name'] == self.table)
        self.assertTrue(row['can_clear'])
        self.assertFalse(row['can_drop'])
        self.assertIn('console_cleanup_view', row['drop_blocked_reason'])
        response = self.action(action='drop')
        self.assertEqual(response.status_code, 409, response.data)
        with connection.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM console_cleanup_view')
            self.assertEqual(cursor.fetchone()[0], 2)

    def test_custom_delete_trigger_cannot_change_other_tables(self):
        with connection.cursor() as cursor:
            cursor.execute(
                f'CREATE TRIGGER console_cleanup_trigger AFTER DELETE ON {self.table} '
                f'BEGIN DELETE FROM {self.neighbor}; END'
            )
        row = next(row for row in self.inventory().data['tables'] if row['name'] == self.table)
        self.assertFalse(row['can_clear'])
        self.assertFalse(row['can_drop'])
        for action in ('clear', 'drop'):
            response = self.action(action=action)
            self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(len(self.rows(self.neighbor)), 2)

    def test_outgoing_foreign_key_does_not_block_selected_child_table(self):
        self.add_child_table(with_row=True)
        response = self.action(table=self.child)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['deleted_rows'], 1)
        self.assertEqual(self.rows(self.child), [])
        response = self.action(table=self.child, action='drop')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn(self.child, connection.introspection.table_names())
        self.assertEqual(len(self.rows()), 2)

    def test_self_referencing_table_is_conservatively_blocked(self):
        table = 'console_cleanup_self_reference'
        quoted = connection.ops.quote_name(table)
        with connection.cursor() as cursor:
            cursor.execute(
                f'CREATE TABLE {quoted} (id integer PRIMARY KEY, parent_id integer, '
                f'FOREIGN KEY (parent_id) REFERENCES {quoted} (id))'
            )
        for action in ('clear', 'drop'):
            response = self.action(table=table, action=action)
            self.assertEqual(response.status_code, 409, response.data)
        self.assertIn(table, connection.introspection.table_names())

    def test_audit_failure_aborts_clear_and_drop(self):
        for action in ('clear', 'drop'):
            with self.subTest(action=action):
                with patch(
                    'apps.rbac.database_maintenance_views.create_audit_log',
                    side_effect=RuntimeError('audit provider private failure'),
                ):
                    response = self.action(action=action)
                self.assertEqual(response.status_code, 503, response.data)
                self.assertNotIn('audit provider private failure', str(response.data))
                self.assertEqual(self.rows(), [(1, 'first'), (2, 'second')])
                self.assertEqual(len(self.rows(self.neighbor)), 2)

    def test_audit_and_table_changes_are_rolled_back_together(self):
        def write_audit_then_fail(*args, **kwargs):
            create_audit_log(*args, **kwargs)
            raise RuntimeError('failure after audit insert')

        before = AuditLog.objects.count()
        prior_audit = object()
        audits = [prior_audit]
        token = current_audits.set(audits)
        self.addCleanup(current_audits.reset, token)
        for action in ('clear', 'drop'):
            with self.subTest(action=action):
                with patch(
                    'apps.rbac.database_maintenance_views.create_audit_log',
                    side_effect=write_audit_then_fail,
                ):
                    response = self.action(action=action)
                self.assertEqual(response.status_code, 503, response.data)
                self.assertEqual(AuditLog.objects.count(), before)
                self.assertEqual(self.rows(), [(1, 'first'), (2, 'second')])
                self.assertEqual(audits, [prior_audit])
