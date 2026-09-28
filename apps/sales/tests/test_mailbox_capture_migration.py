"""Execute real 0008 DDL on historical 0007 models in private temporary SQLite.

This does not use Django's model-sync test database or certify PostgreSQL/full
migration history. Historical dependency tables are created from MigrationLoader
state; the migration under test itself runs its actual apply/unapply operations.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from uuid import uuid4

from django.db import ConnectionHandler, connections
from django.db.migrations.loader import MigrationLoader
from django.test import override_settings


class MailboxCaptureMigrationTests(TestCase):
    # No default database setup or application-database queries are needed.
    databases = frozenset()

    def setUp(self):
        self.directory = TemporaryDirectory(prefix='radai-capture-migration-')
        self.addCleanup(self.directory.cleanup)
        self.alias = f'capture_migration_{uuid4().hex}'
        handler = ConnectionHandler({'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': str(Path(self.directory.name) / 'historical.sqlite3'),
        }})
        self.db = handler['default']
        self.db.alias = self.alias
        connections.databases[self.alias] = self.db.settings_dict
        connections[self.alias] = self.db
        self.addCleanup(self.close_database)
        with override_settings(MIGRATION_MODULES={}):
            loader = MigrationLoader(None)
            self.before = loader.project_state([
                ('sales', '0007_salesemailintake_duplicate_of_and_more'),
            ])
        module = import_module('apps.sales.migrations.0008_mailbox_scoped_capture')
        self.migration = module.Migration('0008_mailbox_scoped_capture', 'sales')
        with self.db.schema_editor() as editor:
            for model in self.before.apps.get_models():
                if model._meta.managed and not model._meta.proxy:
                    editor.create_model(model)
        self.seed_legacy_sources()

    def close_database(self):
        self.db.close()
        del connections[self.alias]
        connections.databases.pop(self.alias, None)

    def seed_legacy_sources(self):
        registry = self.before.apps
        user_model = registry.get_model('users', 'User')
        self.reviewer = user_model.objects.using(self.alias).create(
            username='synthetic-migration-reviewer', email='reviewer@example.test', password='!',
        )
        client = registry.get_model('sales', 'Client').objects.using(self.alias).create(
            client_code='MIGRATION-CLIENT', company_name='Synthetic Migration Client', industry_type='other',
        )
        opportunity = registry.get_model('sales', 'Deal').objects.using(self.alias).create(
            deal_code='MIGRATION-DEAL', deal_name='Synthetic retained opportunity', client_id=client.pk,
            estimated_value=Decimal('1234.56'), currency='AED', expected_close_date=date(2026, 12, 1),
        )
        self.mailbox = registry.get_model('sales', 'SalesMailboxConnection').objects.using(self.alias).create(
            name='Synthetic migration mailbox', tenant_id='synthetic-tenant', client_id='synthetic-client',
            mailbox_address='migration@example.test', auth_mode='application', created_by_id=self.reviewer.pk,
        )
        old_intake = registry.get_model('sales', 'SalesEmailIntake')
        first = None
        for index, status in enumerate(('received', 'under_review', 'converted', 'rejected', 'duplicate')):
            intake = old_intake.objects.using(self.alias).create(
                source_message_id=f'legacy-immutable-{index}', internet_message_id=f'<legacy-{index}@example.test>',
                subject=f'Synthetic legacy subject {index}', sender_name='Synthetic sender',
                sender_email='sender@example.test', received_at=datetime(2026, 9, 1, 8, index, tzinfo=timezone.utc),
                body_preview=f'Synthetic retained body {index}\nSecond paragraph.', has_attachments=index % 2 == 0,
                importance='high', status=status, reviewed_by_id=self.reviewer.pk,
                reviewed_at=datetime(2026, 9, 2, 10, index, tzinfo=timezone.utc),
                resolution_note=f'Synthetic original review {index}',
                opportunity_id=opportunity.pk if status == 'converted' else None,
                duplicate_of_id=first.pk if status == 'duplicate' else None,
            )
            first = first or intake
        self.original = list(old_intake.objects.using(self.alias).order_by('source_message_id').values())
        self.old_fields = [field.attname for field in old_intake._meta.concrete_fields]

    def apply_migration(self):
        with self.db.schema_editor() as editor:
            return self.migration.apply(self.before.clone(), editor)

    def assert_legacy_preserved(self, registry):
        rows = list(registry.get_model('sales', 'SalesEmailIntake').objects.using(self.alias)
                    .filter(source_message_id__startswith='legacy-immutable-')
                    .order_by('source_message_id').values(*self.old_fields))
        self.assertEqual(rows, self.original)

    def schema_snapshot(self):
        with self.db.cursor() as cursor:
            cursor.execute('SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name')
            return cursor.fetchall()

    def test_actual_forward_preserves_all_legacy_fields_and_reverse_without_captures_succeeds(self):
        after = self.apply_migration()
        self.assert_legacy_preserved(after.apps)
        intake = after.apps.get_model('sales', 'SalesEmailIntake')
        for row in intake.objects.using(self.alias).all():
            self.assertIsNone(row.mailbox_connection_id)
            self.assertIsNone(row.captured_by_id)
            self.assertEqual(row.source_mailbox_address, '')
            self.assertEqual(row.source_tenant_id, '')
            self.assertEqual(row.conversation_id, '')
        with self.db.schema_editor() as editor:
            self.migration.unapply(self.before.clone(), editor)
        self.assert_legacy_preserved(self.before.apps)
        with self.db.cursor() as cursor:
            columns = {column.name for column in self.db.introspection.get_table_description(cursor, intake._meta.db_table)}
            constraints = self.db.introspection.get_constraints(cursor, intake._meta.db_table)
        self.assertNotIn('mailbox_connection_id', columns)
        self.assertTrue(any(item['unique'] and item['columns'] == ['source_message_id']
                            for item in constraints.values()))

    def test_actual_reverse_with_capture_refuses_before_any_ddl_or_evidence_loss(self):
        after = self.apply_migration()
        intake = after.apps.get_model('sales', 'SalesEmailIntake')
        captured = intake.objects.using(self.alias).create(
            mailbox_connection_id=self.mailbox.pk, captured_by_id=self.reviewer.pk,
            source_mailbox_address='migration@example.test', source_tenant_id='synthetic-tenant',
            source_message_id='captured-immutable-message', conversation_id='synthetic-conversation',
            subject='Synthetic captured source', sender_email='sender@example.test',
            received_at=datetime(2026, 9, 28, 8, tzinfo=timezone.utc), body_preview='Synthetic saved evidence.',
        )
        original_capture = intake.objects.using(self.alias).values().get(pk=captured.pk)
        original_schema = self.schema_snapshot()
        statements = []

        def record_statement(execute, sql, params, many, context):
            statements.append(sql.lstrip().split()[0].upper())
            return execute(sql, params, many, context)

        with self.db.execute_wrapper(record_statement):
            with self.assertRaisesRegex(RuntimeError, 'Cannot reverse Sales mailbox capture'):
                with self.db.schema_editor() as editor:
                    self.migration.unapply(self.before.clone(), editor)
        self.assertFalse({'CREATE', 'ALTER', 'DROP', 'RENAME'} & set(statements))
        self.assertEqual(self.schema_snapshot(), original_schema)
        self.assertEqual(intake.objects.using(self.alias).values().get(pk=captured.pk), original_capture)
        self.assert_legacy_preserved(after.apps)
