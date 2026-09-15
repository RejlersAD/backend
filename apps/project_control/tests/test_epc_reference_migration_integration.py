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
        self.state = ProjectState.from_apps(apps)
        for name in ('epcworkevent', 'epcworkitem', 'wbsactivitylink', 'requisitionwbslink', 'integratedbaseline'):
            self.state.remove_model('project_control', name)
        self.schema = 'epc_reference_' + uuid.uuid4().hex
        self.identities = {}

    def prepare(self, *, requisition_key=False, order_key=False):
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
            for table in ('procurement_requisitions', 'procurement_orders'):
                cursor.execute(f'SELECT * FROM {connection.ops.quote_name(table)} ORDER BY id')
                result[table] = cursor.fetchall()
            return result

    def apply(self, module, number, *, legacy=False):
        migration = module.Migration(number, 'project_control')
        if legacy:
            # Model the previously released migration, before its preflight was
            # added. The dependency graph and schema operations stay identical.
            from django.db.migrations.operations.special import RunPython
            migration.operations = [operation for operation in migration.operations if not isinstance(operation, RunPython)]
        with connection.schema_editor() as editor:
            self.state = migration.apply(self.state, editor)

    def insert_link(self, requisition_id):
        from django.conf import settings
        return self.state.apps.get_model('project_control', 'RequisitionWBSLink').objects.create(
            project_id=self.identities[('core', 'Project')],
            wbs_node_id=self.identities[('project_control', 'WBSNode')],
            linked_by_id=self.identities[tuple(settings.AUTH_USER_MODEL.split('.'))],
            requisition_id=requisition_id, reason='Preserve the imported requisition link',
        )

    def insert_work(self, order_id, code):
        from django.conf import settings
        user_id = self.identities[tuple(settings.AUTH_USER_MODEL.split('.'))]
        return self.state.apps.get_model('project_control', 'EPCWorkItem').objects.create(
            project_id=self.identities[('core', 'Project')],
            wbs_node_id=self.identities[('project_control', 'WBSNode')],
            owner_id=user_id, reviewer_id=user_id, purchase_order_id=order_id,
            code=code, title='Existing order execution', phase='procurement', data_date=date(2026, 1, 1),
        )

    def assert_foreign_keys_and_followup(self, before):
        link = self.insert_link(self.identities[('procurement', 'PurchaseRequisition')])
        work = self.insert_work(self.identities[('procurement', 'PurchaseOrder')], 'WORK-1')
        with connection.cursor() as cursor:
            cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
            cursor.execute("SELECT count(*) FROM pg_constraint WHERE contype='f' AND connamespace=%s::regnamespace", [self.schema])
            self.assertGreater(cursor.fetchone()[0], 20)
        for insert in (lambda: self.insert_link(uuid.uuid4()), lambda: self.insert_work(uuid.uuid4(), 'INVALID')):
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    insert()
                    with connection.cursor() as cursor:
                        cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
        self.apply(self.followup, '0009_verify_procurement_reference_keys')
        self.apply(self.followup, '0009_verify_procurement_reference_keys')
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
