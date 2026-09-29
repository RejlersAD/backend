"""Apply/reverse actual 0010 DDL on private historical 0009 SQLite tables."""

from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from uuid import uuid4

from django.db import ConnectionHandler, connections
from django.db.migrations.loader import MigrationLoader
from django.test import override_settings


class MailboxSentTimeMigrationTests(TestCase):
    databases = frozenset()
    retained_models = (
        'SalesEmailIntake', 'SalesMailboxConnection', 'SalesMailboxSyncState',
        'SalesMailboxSyncFolder', 'SalesMailboxSyncItem', 'Client', 'Deal',
    )

    def setUp(self):
        self.directory = TemporaryDirectory(prefix='radai-sent-time-migration-')
        self.addCleanup(self.directory.cleanup)
        self.alias = f'sent_time_migration_{uuid4().hex}'
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
            self.before = MigrationLoader(None).project_state([('sales', '0009_automatic_mailbox_sync')])
        module = import_module('apps.sales.migrations.0010_email_sent_at')
        self.migration = module.Migration('0010_email_sent_at', 'sales')
        with self.db.schema_editor() as editor:
            for model in self.before.apps.get_models():
                if model._meta.managed and not model._meta.proxy:
                    editor.create_model(model)
        self.seed_sources()
        self.original = self.rows(self.before.apps)

    def close_database(self):
        self.db.close()
        del connections[self.alias]
        connections.databases.pop(self.alias, None)

    def seed_sources(self):
        registry = self.before.apps

        def create(model_name, **data):
            return registry.get_model('sales', model_name).objects.using(self.alias).create(**data)

        actor = registry.get_model('users', 'User').objects.using(self.alias).create(
            username='synthetic-sent-time', email='reviewer@example.test', password='!',
        )
        client = create('Client', client_code='SENT-TIME-CLIENT', company_name='Synthetic Customer', industry_type='other')
        opportunity = create(
            'Deal', deal_code='SENT-TIME-DEAL', deal_name='Synthetic retained opportunity',
            client_id=client.pk, estimated_value='1250.10', currency='AED', expected_close_date='2026-12-01',
        )
        mailbox = create(
            'SalesMailboxConnection', name='Synthetic mailbox', tenant_id='synthetic-tenant', client_id='synthetic-app',
            mailbox_address='sales@example.test', auth_mode='application', enabled=True, created_by_id=actor.pk,
        )
        create(
            'SalesMailboxConnection', name='Synthetic disabled mailbox', tenant_id='synthetic-tenant',
            client_id='synthetic-app', mailbox_address='disabled@example.test', auth_mode='application', enabled=False,
        )
        create(
            'SalesEmailIntake', source_message_id='synthetic-legacy', subject='Synthetic legacy',
            sender_email='buyer@customer.test', received_at=datetime(2026, 9, 27, 8, tzinfo=timezone.utc),
            body_preview='Retained legacy text.', status='under_review', reviewed_by_id=actor.pk,
            resolution_note='Retained legacy review.',
        )
        self.captured = create(
            'SalesEmailIntake', source_message_id='synthetic-captured', subject='Synthetic captured',
            sender_email='buyer@customer.test', received_at=datetime(2026, 9, 28, 8, tzinfo=timezone.utc),
            body_preview='Retained captured evidence.', mailbox_connection_id=mailbox.pk,
            source_mailbox_address=mailbox.mailbox_address, source_tenant_id=mailbox.tenant_id,
            conversation_id='synthetic-conversation', captured_by_id=actor.pk,
            status='converted', reviewed_by_id=actor.pk, opportunity_id=opportunity.pk,
            reviewed_at=datetime(2026, 9, 28, 10, tzinfo=timezone.utc), resolution_note='Retained review.',
        )
        state = create(
            'SalesMailboxSyncState', connection_id=mailbox.pk, authorized_by_id=actor.pk,
            identity={'mailbox_address': mailbox.mailbox_address}, status='queued',
            folder_cursor='synthetic-checkpoint',
        )
        create('SalesMailboxSyncFolder', sync_id=state.pk, folder_id='synthetic-folder', cursor='synthetic-folder-checkpoint')
        create('SalesMailboxSyncItem', sync_id=state.pk, message_id='synthetic-pending', status='pending')
        create(
            'SalesMailboxSyncItem', sync_id=state.pk, message_id=self.captured.source_message_id,
            status='captured', intake_id=self.captured.pk,
        )

    def rows(self, registry, *, include_sent=False):
        snapshots = {}
        for name in self.retained_models:
            model = registry.get_model('sales', name)
            fields = [field.attname for field in model._meta.concrete_fields if include_sent or field.name != 'sent_at']
            snapshots[name] = list(model.objects.using(self.alias).order_by('pk').values(*fields))
        return snapshots

    def schema_snapshot(self):
        with self.db.cursor() as cursor:
            cursor.execute('SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name')
            return cursor.fetchall()

    def apply_migration(self):
        with self.db.schema_editor() as editor:
            return self.migration.apply(self.before.clone(), editor)

    def test_forward_preserves_legacy_capture_review_and_sync_and_null_only_reverse_is_safe(self):
        schema = self.schema_snapshot()
        after = self.apply_migration()
        source = after.apps.get_model('sales', 'SalesEmailIntake')
        self.assertTrue(source._meta.get_field('sent_at').null)
        self.assertEqual(source.objects.using(self.alias).filter(sent_at__isnull=True).count(), 2)
        self.assertEqual(self.rows(after.apps), self.original)
        with self.db.schema_editor() as editor:
            self.migration.unapply(self.before.clone(), editor)
        self.assertEqual(self.schema_snapshot(), schema)
        self.assertEqual(self.rows(self.before.apps), self.original)

    def test_populated_sent_evidence_refuses_reverse_before_ddl_and_preserves_all_rows(self):
        after = self.apply_migration()
        source = after.apps.get_model('sales', 'SalesEmailIntake')
        stamp = datetime(2026, 9, 28, 7, 58, tzinfo=timezone.utc)
        source.objects.using(self.alias).filter(pk=self.captured.pk).update(sent_at=stamp)
        self.assertEqual(source.objects.using(self.alias).get(pk=self.captured.pk).sent_at, stamp)
        self.assertEqual(self.rows(after.apps), self.original)
        before_rows = self.rows(after.apps, include_sent=True)
        before_schema = self.schema_snapshot()
        statements = []

        def record_statement(execute, sql, params, many, context):
            statements.append(sql.lstrip().split()[0].upper())
            return execute(sql, params, many, context)

        with self.db.execute_wrapper(record_statement):
            with self.assertRaisesRegex(RuntimeError, 'Cannot reverse Sales email sent-time storage'):
                with self.db.schema_editor() as editor:
                    self.migration.unapply(self.before.clone(), editor)
        self.assertFalse({'CREATE', 'ALTER', 'DROP', 'RENAME'} & set(statements))
        self.assertEqual(self.schema_snapshot(), before_schema)
        self.assertEqual(self.rows(after.apps, include_sent=True), before_rows)
