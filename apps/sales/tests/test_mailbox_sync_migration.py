"""Real 0008-to-0009 DDL in private SQLite, independent of model-sync tests."""

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


class MailboxSyncMigrationTests(TestCase):
    databases = frozenset()
    sync_models = ('SalesMailboxSyncState', 'SalesMailboxSyncFolder', 'SalesMailboxSyncItem')

    def setUp(self):
        self.directory = TemporaryDirectory(prefix='radai-sync-migration-')
        self.addCleanup(self.directory.cleanup)
        self.alias = f'sync_migration_{uuid4().hex}'
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
            self.before = loader.project_state([('sales', '0008_mailbox_scoped_capture')])
        module = import_module('apps.sales.migrations.0009_automatic_mailbox_sync')
        self.migration = module.Migration('0009_automatic_mailbox_sync', 'sales')
        with self.db.schema_editor() as editor:
            for model in self.before.apps.get_models():
                if model._meta.managed and not model._meta.proxy:
                    editor.create_model(model)
        self.seed_sources()

    def close_database(self):
        self.db.close()
        del connections[self.alias]
        connections.databases.pop(self.alias, None)

    def seed_sources(self):
        registry = self.before.apps
        self.actor = registry.get_model('users', 'User').objects.using(self.alias).create(
            username='synthetic-sync-migration', email='reviewer@example.test', password='!',
        )
        client = registry.get_model('sales', 'Client').objects.using(self.alias).create(
            client_code='SYNC-MIGRATION-CLIENT', company_name='Synthetic Client', industry_type='other',
        )
        opportunity = registry.get_model('sales', 'Deal').objects.using(self.alias).create(
            deal_code='SYNC-MIGRATION-DEAL', deal_name='Synthetic retained opportunity', client_id=client.pk,
            estimated_value=Decimal('1250.10'), currency='AED', expected_close_date=date(2026, 12, 1),
        )
        mailbox_model = registry.get_model('sales', 'SalesMailboxConnection')
        for enabled in (False, True):
            mailbox = mailbox_model.objects.using(self.alias).create(
                name=f'Synthetic mailbox {enabled}', tenant_id='synthetic-tenant', client_id='synthetic-client',
                mailbox_address=f'migration-{int(enabled)}@example.test', auth_mode='application',
                enabled=enabled, created_by_id=self.actor.pk,
            )
            if enabled:
                self.mailbox = mailbox
        source = registry.get_model('sales', 'SalesEmailIntake')
        self.legacy = source.objects.using(self.alias).create(
            source_message_id='synthetic-legacy-message', subject='Synthetic legacy source',
            sender_email='sender@example.test', received_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            body_preview='Synthetic original legacy source.', status='under_review',
            reviewed_by_id=self.actor.pk, resolution_note='Synthetic review remains unchanged.',
        )
        self.captured = source.objects.using(self.alias).create(
            source_message_id='synthetic-captured-message', internet_message_id='<synthetic@example.test>',
            mailbox_connection_id=self.mailbox.pk, captured_by_id=self.actor.pk,
            source_mailbox_address=self.mailbox.mailbox_address, source_tenant_id=self.mailbox.tenant_id,
            conversation_id='synthetic-conversation', subject='Synthetic retained capture',
            sender_name='Synthetic sender', sender_email='sender@example.test',
            received_at=datetime(2026, 9, 2, tzinfo=timezone.utc), body_preview='Synthetic captured evidence.',
            has_attachments=True, importance='high', status='converted', opportunity_id=opportunity.pk,
            reviewed_by_id=self.actor.pk, reviewed_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
            resolution_note='Synthetic accepted review.',
        )
        self.original_intakes = list(source.objects.using(self.alias).order_by('pk').values())
        self.original_connections = list(mailbox_model.objects.using(self.alias).order_by('pk').values())

    def apply_migration(self):
        with self.db.schema_editor() as editor:
            return self.migration.apply(self.before.clone(), editor)

    def assert_existing_data_preserved(self, registry):
        self.assertEqual(
            list(registry.get_model('sales', 'SalesEmailIntake').objects.using(self.alias).order_by('pk').values()),
            self.original_intakes,
        )
        self.assertEqual(
            list(registry.get_model('sales', 'SalesMailboxConnection').objects.using(self.alias).order_by('pk').values()),
            self.original_connections,
        )

    def schema_snapshot(self):
        with self.db.cursor() as cursor:
            cursor.execute('SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name')
            return cursor.fetchall()

    def test_forward_preserves_captured_and_legacy_sources_without_enabling_sync_and_clean_reverse_succeeds(self):
        before_schema = self.schema_snapshot()
        after = self.apply_migration()
        self.assert_existing_data_preserved(after.apps)
        for name in self.sync_models:
            self.assertEqual(after.apps.get_model('sales', name).objects.using(self.alias).count(), 0)
        with self.db.schema_editor() as editor:
            self.migration.unapply(self.before.clone(), editor)
        self.assert_existing_data_preserved(self.before.apps)
        self.assertEqual(self.schema_snapshot(), before_schema)

    def test_reverse_with_paused_state_refuses_before_ddl_and_preserves_authority_queue_and_checkpoints(self):
        after = self.apply_migration()
        registry = after.apps
        state = registry.get_model('sales', 'SalesMailboxSyncState').objects.using(self.alias).create(
            connection_id=self.mailbox.pk, authorized_by_id=self.actor.pk, status='paused',
            identity={'mailbox_address': self.mailbox.mailbox_address},
            folder_cursor='synthetic-private-checkpoint',
        )
        registry.get_model('sales', 'SalesMailboxSyncFolder').objects.using(self.alias).create(
            sync_id=state.pk, folder_id='synthetic-folder', cursor='synthetic-private-folder-checkpoint',
        )
        item_model = registry.get_model('sales', 'SalesMailboxSyncItem')
        item_model.objects.using(self.alias).create(sync_id=state.pk, message_id='synthetic-pending', status='pending')
        item_model.objects.using(self.alias).create(
            sync_id=state.pk, message_id=self.captured.source_message_id, status='captured', intake_id=self.captured.pk,
        )
        original = {
            name: list(registry.get_model('sales', name).objects.using(self.alias).order_by('pk').values())
            for name in self.sync_models
        }
        before_schema = self.schema_snapshot()
        statements = []

        def record_statement(execute, sql, params, many, context):
            statements.append(sql.lstrip().split()[0].upper())
            return execute(sql, params, many, context)

        with self.db.execute_wrapper(record_statement):
            with self.assertRaisesRegex(RuntimeError, 'Cannot reverse Sales mailbox sync'):
                with self.db.schema_editor() as editor:
                    self.migration.unapply(self.before.clone(), editor)
        self.assertFalse({'CREATE', 'ALTER', 'DROP', 'RENAME'} & set(statements))
        self.assertEqual(self.schema_snapshot(), before_schema)
        self.assert_existing_data_preserved(registry)
        for name in self.sync_models:
            self.assertEqual(
                list(registry.get_model('sales', name).objects.using(self.alias).order_by('pk').values()),
                original[name],
            )
