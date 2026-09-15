"""Execute EPC migrations and deferred foreign keys on restored PostgreSQL tables."""
from datetime import date
from importlib import import_module
from unittest import skipUnless
import uuid

from django.apps import apps
from django.db import connection, DatabaseError, IntegrityError, transaction
from django.db.migrations.state import ProjectState
from django.test import TestCase


@skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL foreign-key validation')
class EpcReferenceMigrationIntegrationTests(TestCase):
    def setUp(self):
        self.assertTrue(str(connection.settings_dict['NAME']).startswith('test_'))
        self.foundation = import_module('apps.project_control.migrations.0007_epc_foundation')
        self.execution = import_module('apps.project_control.migrations.0008_epc_execution')
        self.followup = import_module('apps.project_control.migrations.0009_verify_procurement_reference_keys')
        self.execution_followup = import_module('apps.project_control.migrations.0010_verify_execution_reference_keys')
        self.state = ProjectState.from_apps(apps)
        for name in ('epcworkevent', 'epcworkitem', 'wbsactivitylink', 'requisitionwbslink', 'integratedbaseline'):
            self.state.remove_model('project_control', name)
        self.schema = 'epc_reference_' + uuid.uuid4().hex
        self.identities = {}

    def prepare(self, *, requisition_key=False, order_key=False, missing_keys=()):
        from django.conf import settings
        references = [
            tuple(settings.AUTH_USER_MODEL.split('.')),
            ('core', 'Project'), ('core', 'ProjectMilestone'),
            ('planning_intelligence', 'ScheduleBaseline'), ('planning_intelligence', 'ScheduleActivity'),
            ('planning_intelligence', 'ScheduleControlSnapshot'), ('planning_intelligence', 'ActivityProgressUpdate'),
            ('project_control', 'WBSNode'), ('project_control', 'ProjectDocument'),
            ('procurement', 'PurchaseRequisition'), ('procurement', 'PurchaseOrder'),
        ]
        quote = connection.ops.quote_name
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE SCHEMA {quote(self.schema)}')
            cursor.execute(f'SET LOCAL search_path TO {quote(self.schema)}')
            for label in references:
                model = self.state.apps.get_model(*label)
                kind = model._meta.pk.db_type(connection)
                identity = uuid.uuid4() if kind == 'uuid' else 1
                key = ' PRIMARY KEY'
                if label == ('procurement', 'PurchaseRequisition') and not requisition_key:
                    key = ''
                if label == ('procurement', 'PurchaseOrder') and not order_key:
                    key = ''
                if label in missing_keys:
                    key = ''
                extra = ', pr_reference_id uuid, total numeric(14,2)' if label == ('procurement', 'PurchaseOrder') else ''
                cursor.execute(f'CREATE TABLE {quote(model._meta.db_table)} (id {kind}{key}, marker text NOT NULL{extra})')
                cursor.execute(f'INSERT INTO {quote(model._meta.db_table)} (id, marker) VALUES (%s, %s)', [identity, 'Keep this source row'])
                self.identities[label] = identity
            cursor.execute('UPDATE procurement_orders SET pr_reference_id=%s, total=%s',
                           [self.identities[('procurement', 'PurchaseRequisition')], '1234.56'])
        return self.snapshot()

    def snapshot(self):
        with connection.cursor() as cursor:
            result = {}
            for label in self.identities:
                table = self.state.apps.get_model(*label)._meta.db_table
                cursor.execute(f'SELECT * FROM {connection.ops.quote_name(table)} ORDER BY id')
                result[table] = cursor.fetchall()
            return result

    def drop_reference_key(self, label):
        """Simulate restored tables only inside this test's isolated schema."""
        model = self.state.apps.get_model(*label)
        quote = connection.ops.quote_name
        with connection.cursor() as cursor:
            cursor.execute('''
                SELECT conname FROM pg_constraint
                WHERE connamespace = %s::regnamespace
                  AND conrelid = to_regclass(%s) AND contype = 'p'
            ''', [self.schema, model._meta.db_table])
            constraints = cursor.fetchall()
            self.assertEqual(len(constraints), 1)
            cursor.execute(
                f'ALTER TABLE {quote(model._meta.db_table)} '
                f'DROP CONSTRAINT {quote(constraints[0][0])} CASCADE'
            )

    def execution_reference_labels(self):
        # ScheduleBaseline is referenced by 0007, but not by 0008.
        return [label for label in self.identities if label != ('planning_intelligence', 'ScheduleBaseline')] + [
            ('project_control', 'IntegratedBaseline'),
        ]

    def assert_reference_keys(self, labels):
        """Have PostgreSQL accept and enforce a real FK to each repaired key."""
        quote = connection.ops.quote_name
        for label in labels:
            with self.subTest(reference=label):
                model = self.state.apps.get_model(*label)
                kind = model._meta.pk.db_type(connection)
                probe = 'reference_probe_' + uuid.uuid4().hex
                with connection.cursor() as cursor:
                    cursor.execute(
                        f'CREATE TABLE {quote(probe)} (id {kind} REFERENCES '
                        f'{quote(model._meta.db_table)} ({quote(model._meta.pk.column)}))'
                    )
                with self.assertRaises(IntegrityError):
                    with transaction.atomic():
                        with connection.cursor() as cursor:
                            cursor.execute(f'INSERT INTO {quote(probe)} (id) VALUES (%s)',
                                           [uuid.uuid4() if kind == 'uuid' else 999999])

    def apply(self, module, number, *, legacy=False):
        migration = module.Migration(number, 'project_control')
        if legacy:
            # Model the previously released migration, before its preflight was
            # added. The dependency graph and schema operations stay identical.
            from django.db.migrations.operations.special import RunPython
            migration.operations = [operation for operation in migration.operations if not isinstance(operation, RunPython)]
        # Own the transaction outside the editor: deferred SQL can raise from
        # the editor's __exit__ before it unwinds its own atomic context.
        with transaction.atomic(), connection.schema_editor(atomic=False) as editor:
            next_state = migration.apply(self.state.clone(), editor)
        # A deferred FK may fail while the editor exits. Keep the historical
        # state intact so a rolled-back migration can actually be retried.
        self.state = next_state

    def insert_link(self, requisition_id):
        from django.conf import settings
        return self.state.apps.get_model('project_control', 'RequisitionWBSLink').objects.create(
            project_id=self.identities[('core', 'Project')],
            wbs_node_id=self.identities[('project_control', 'WBSNode')],
            linked_by_id=self.identities[tuple(settings.AUTH_USER_MODEL.split('.'))],
            requisition_id=requisition_id, reason='Preserve the imported requisition link',
        )

    def insert_work(self, order_id, code, **references):
        from django.conf import settings
        user_id = self.identities[tuple(settings.AUTH_USER_MODEL.split('.'))]
        return self.state.apps.get_model('project_control', 'EPCWorkItem').objects.create(
            project_id=self.identities[('core', 'Project')],
            wbs_node_id=self.identities[('project_control', 'WBSNode')],
            owner_id=user_id, reviewer_id=user_id, purchase_order_id=order_id,
            code=code, title='Existing order execution', phase='procurement', data_date=date(2026, 1, 1),
            **references,
        )

    def assert_foreign_keys_and_followup(self, before):
        link = self.insert_link(self.identities[('procurement', 'PurchaseRequisition')])
        work = self.insert_work(self.identities[('procurement', 'PurchaseOrder')], 'WORK-1',
                                milestone_id=self.identities[('core', 'ProjectMilestone')])
        with connection.cursor() as cursor:
            cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
            cursor.execute("SELECT count(*) FROM pg_constraint WHERE contype='f' AND connamespace=%s::regnamespace", [self.schema])
            self.assertGreater(cursor.fetchone()[0], 20)
        milestone_id = self.identities[('core', 'ProjectMilestone')]
        invalid_milestone_id = uuid.uuid4() if isinstance(milestone_id, uuid.UUID) else 999999
        for insert in (
            lambda: self.insert_link(uuid.uuid4()),
            lambda: self.insert_work(uuid.uuid4(), 'INVALID-ORDER'),
            lambda: self.insert_work(None, 'INVALID-MILESTONE', milestone_id=invalid_milestone_id),
        ):
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    insert()
                    with connection.cursor() as cursor:
                        cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
        self.apply(self.followup, '0009_verify_procurement_reference_keys')
        self.apply(self.followup, '0009_verify_procurement_reference_keys')
        self.apply(self.execution_followup, '0010_verify_execution_reference_keys')
        self.apply(self.execution_followup, '0010_verify_execution_reference_keys')
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(self.state.apps.get_model('project_control', 'RequisitionWBSLink').objects.filter(pk=link.pk).exists())
        self.assertTrue(self.state.apps.get_model('project_control', 'EPCWorkItem').objects.filter(pk=work.pk).exists())

    def test_missing_requisition_key_reproduces_then_full_epc_migrations_preserve_rows(self):
        before = self.prepare()
        with self.assertRaisesRegex(DatabaseError, 'no unique constraint'):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute('CREATE TABLE before_repair (pr_id uuid REFERENCES procurement_requisitions(id))')
        self.apply(self.foundation, '0007_epc_foundation')
        self.apply(self.execution, '0008_epc_execution')
        self.assert_foreign_keys_and_followup(before)

    def test_execution_preflight_repairs_order_key_when_foundation_was_already_applied(self):
        before = self.prepare(requisition_key=True)
        self.apply(self.foundation, '0007_epc_foundation', legacy=True)
        with self.assertRaisesRegex(DatabaseError, 'no unique constraint'):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute('CREATE TABLE before_order_repair (po_id uuid REFERENCES procurement_orders(id))')
        self.apply(self.execution, '0008_epc_execution')
        self.assert_foreign_keys_and_followup(before)

    def test_followup_preserves_installations_that_already_applied_both_epc_migrations(self):
        before = self.prepare(requisition_key=True, order_key=True)
        self.apply(self.foundation, '0007_epc_foundation', legacy=True)
        self.apply(self.execution, '0008_epc_execution', legacy=True)
        self.assert_foreign_keys_and_followup(before)

    def test_missing_milestone_key_reproduces_execution_failure_then_preserves_rows(self):
        before = self.prepare(
            requisition_key=True, order_key=True,
            missing_keys=(('core', 'ProjectMilestone'),),
        )
        self.apply(self.foundation, '0007_epc_foundation')
        with self.assertRaisesRegex(DatabaseError, 'no unique constraint.*core_projectmilestone'):
            with transaction.atomic():
                self.apply(self.execution, '0008_epc_execution', legacy=True)
        self.assertEqual(self.snapshot(), before)
        self.apply(self.execution, '0008_epc_execution')
        self.assert_foreign_keys_and_followup(before)

    def test_execution_repairs_every_existing_target_after_foundation_was_applied(self):
        before = self.prepare(requisition_key=True, order_key=True)
        self.apply(self.foundation, '0007_epc_foundation', legacy=True)
        labels = self.execution_reference_labels()
        for label in labels:
            self.drop_reference_key(label)
        self.apply(self.execution, '0008_epc_execution')
        self.assert_reference_keys(labels)
        self.assertEqual(self.snapshot(), before)

    def test_execution_followup_repairs_keys_after_0009_was_already_applied(self):
        before = self.prepare(requisition_key=True, order_key=True)
        self.apply(self.foundation, '0007_epc_foundation', legacy=True)
        self.apply(self.execution, '0008_epc_execution', legacy=True)
        self.apply(self.followup, '0009_verify_procurement_reference_keys')
        work = self.insert_work(self.identities[('procurement', 'PurchaseOrder')], 'RESTORED-WORK',
                                milestone_id=self.identities[('core', 'ProjectMilestone')])
        with connection.cursor() as cursor:
            cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
        labels = self.execution_reference_labels()
        for label in labels:
            self.drop_reference_key(label)
        self.apply(self.execution_followup, '0010_verify_execution_reference_keys')
        self.apply(self.execution_followup, '0010_verify_execution_reference_keys')
        self.assert_reference_keys(labels)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(
            self.state.apps.get_model('project_control', 'EPCWorkItem').objects.get(pk=work.pk).milestone_id,
            self.identities[('core', 'ProjectMilestone')],
        )

    def assert_invalid_milestone_rolls_back(self, *, null_id):
        self.prepare(requisition_key=True, order_key=True, missing_keys=(('core', 'ProjectMilestone'),))
        self.apply(self.foundation, '0007_epc_foundation', legacy=True)
        # PurchaseOrder is repaired before ProjectMilestone. Its new key must
        # roll back if the later milestone validation rejects source identities.
        self.drop_reference_key(('procurement', 'PurchaseOrder'))
        with connection.cursor() as cursor:
            cursor.execute('INSERT INTO core_projectmilestone (id, marker) VALUES (%s, %s)', [
                None if null_id else self.identities[('core', 'ProjectMilestone')], 'Invalid restored identity',
            ])
        before = self.snapshot()
        reason = 'null' if null_id else 'duplicate'
        with self.assertRaisesRegex(RuntimeError, f'core_projectmilestone.id: {reason} IDs exist'):
            with transaction.atomic():
                self.apply(self.execution, '0008_epc_execution')
        self.assertEqual(self.snapshot(), before)
        with connection.cursor() as cursor:
            cursor.execute('''
                SELECT count(*) FROM pg_constraint
                WHERE conrelid = 'procurement_orders'::regclass AND contype IN ('p', 'u')
            ''')
            self.assertEqual(cursor.fetchone()[0], 0)
            for name in ('EPCWorkItem', 'EPCWorkEvent'):
                table = apps.get_model('project_control', name)._meta.db_table
                cursor.execute('SELECT to_regclass(%s)', [table])
                self.assertIsNone(cursor.fetchone()[0])

    def test_duplicate_milestone_ids_roll_back_earlier_repairs(self):
        self.assert_invalid_milestone_rolls_back(null_id=False)

    def test_null_milestone_ids_roll_back_earlier_repairs(self):
        self.assert_invalid_milestone_rolls_back(null_id=True)
