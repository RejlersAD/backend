"""Reviewed duplicate resolution preserves the selected row and every reference.

API tests use an isolated copy of the in-memory test invoice table. Native
PostgreSQL cases create a private, rollback-only schema and refuse remote hosts.
"""
from decimal import Decimal
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from django.core.cache import cache
from django.db import connection, transaction
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from apps.finance.receivables_source_models import ReceivablesSourceRow, ReceivablesSourceSnapshot
from apps.invoice_tracker import tests_identity_conflicts as identity_helpers
from apps.invoice_tracker.models import CustomerInvoice, InvoiceAttachment, InvoiceDuplicateResolution
from apps.invoice_tracker.services import duplicate_invoices as service
from apps.rbac.models import RolePermission


BASE = '/api/v1/invoice-tracker/invoices/duplicates/'
LOCAL_POSTGRESQL = (
    connection.vendor == 'postgresql'
    and str(connection.settings_dict.get('HOST', '')) in {'', 'localhost', '127.0.0.1', '::1', 'postgres_local'}
)


@override_settings(ROOT_URLCONF='apps.invoice_tracker.tests_collections')
class DuplicateInvoiceAPITests(TestCase):
    grant = identity_helpers.InvoiceIdentityConflictTests.grant
    grant_writes = identity_helpers.InvoiceIdentityConflictTests.grant_writes
    drop_copy = identity_helpers.InvoiceIdentityConflictTests.drop_copy
    table_rows = staticmethod(identity_helpers.InvoiceIdentityConflictTests.table_rows)
    detail_request = identity_helpers.InvoiceIdentityConflictTests.request

    def setUp(self):
        identity_helpers.InvoiceIdentityConflictTests.setUp(self)
        self.grant_writes()
        self.sql(f'ALTER TABLE {self.table} ADD COLUMN legacy_import_note text')
        self.sql(f'UPDATE {self.table} SET legacy_import_note = %s', ['Original restored-column value'])
        self.sql(f'UPDATE {self.table} SET invoice_amount = %s, invoice_amount_aed = %s, '
                 'actual_payment_received = %s, balance_to_be_received = %s WHERE invoice_number = %s',
                 ['750.25', '2755.75', '12.50', '737.75', 'SECOND-RECORD'])
        self.sql(f'INSERT INTO {self.table} SELECT * FROM {self.table} WHERE invoice_number = %s',
                 ['FIRST-RECORD'])
        snapshot = ReceivablesSourceSnapshot.objects.create(
            sha256='a' * 64, file_name='Source remains.xlsx', sheet_name='External Invoice ',
            last_row=7, row_count=2, is_active=True,
        )
        ReceivablesSourceRow.objects.bulk_create([
            ReceivablesSourceRow(snapshot=snapshot, row_number=6, invoice_number='SOURCE-FIRST',
                                 register_invoice_id=688, invoice_amount=Decimal('999')),
            ReceivablesSourceRow(snapshot=snapshot, row_number=7, invoice_number='SOURCE-SECOND',
                                 register_invoice_id=None, invoice_amount=Decimal('123')),
        ])
        self.source_rows = list(ReceivablesSourceRow.objects.order_by('pk').values())
        self.source_snapshots = list(ReceivablesSourceSnapshot.objects.order_by('pk').values())

    @property
    def table(self):
        return connection.ops.quote_name(self.copy_table)

    @staticmethod
    def sql(statement, params=None):
        with connection.cursor() as cursor:
            cursor.execute(statement, params)
            return cursor.fetchall() if cursor.description else None

    def api(self, method, *, params=None, body=None):
        # Dispatch through the real router so DELETE's mapped action and RBAC
        # operation are checked together, rather than manually naming an action.
        return (self.client.get(BASE, params or {}) if method == 'get'
                else self.client.delete(BASE, body or {}, format='json'))

    def review(self, identity=688):
        response = self.api('get', params={'invoice_id': identity})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data['groups'][0]

    @staticmethod
    def keep(group, number='SECOND-RECORD'):
        return next(record for record in group['records'] if record['invoice_number'] == number)

    def resolve(self, group, record=None):
        record = record or self.keep(group)
        return self.api('delete', body={'group_token': group['group_token'], 'keep_token': record['record_token']})

    def assert_references_preserved(self):
        self.assertEqual(self.table_rows(self.original_table), self.original_rows)
        self.assertEqual(list(InvoiceAttachment.objects.order_by('pk').values()), self.attachments)
        self.assertEqual(list(ReceivablesSourceRow.objects.order_by('pk').values()), self.source_rows)
        self.assertEqual(list(ReceivablesSourceSnapshot.objects.order_by('pk').values()), self.source_snapshots)

    def assert_no_resolution(self, before):
        self.assertEqual(self.table_rows(self.copy_table), before)
        self.assertEqual(InvoiceDuplicateResolution.objects.count(), 0)
        self.assert_references_preserved()

    def test_review_lists_each_physical_record_including_identical_copies_without_writes(self):
        before = self.table_rows(self.copy_table)
        with CaptureQueriesContext(connection) as queries:
            group = self.review()
        self.assertEqual(group['invoice_id'], 688)
        self.assertEqual(len(group['records']), 3)
        self.assertEqual(len({record['record_token'] for record in group['records']}), 3)
        self.assertEqual(sum(record['invoice_number'] == 'FIRST-RECORD' for record in group['records']), 2)
        self.assertFalse(any(row['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE '))
                             for row in queries.captured_queries))
        self.assert_no_resolution(before)

    def test_selected_financial_record_survives_and_all_removed_columns_are_archived(self):
        group = self.review()
        with patch('apps.invoice_tracker.models.CustomerInvoice.delete',
                   side_effect=AssertionError('ORM cascades must never resolve duplicates')), \
                patch('django.db.models.fields.files.FieldFile.delete',
                      side_effect=AssertionError('Attachments must remain stored')):
            response = self.resolve(group)
        self.assertEqual(response.status_code, 200, response.data)
        invoice = CustomerInvoice.objects.get(pk=688)
        self.assertEqual(invoice.invoice_number, 'SECOND-RECORD')
        self.assertEqual(invoice.invoice_amount, Decimal('750.25'))
        self.assertEqual(invoice.invoice_amount_aed, Decimal('2755.75'))
        self.assertEqual(invoice.actual_payment_received, Decimal('12.50'))
        self.assertEqual(invoice.balance_to_be_received, Decimal('737.75'))
        self.assertEqual(CustomerInvoice.objects.count(), 2)
        audit = InvoiceDuplicateResolution.objects.get()
        self.assertEqual(audit.invoice_id, 688)
        self.assertEqual(audit.actor_id, str(self.user.pk))
        self.assertEqual(audit.retained_record['invoice_number'], 'SECOND-RECORD')
        self.assertEqual(Decimal(str(audit.retained_record['invoice_amount'])), Decimal('750.25'))
        self.assertEqual(len(audit.removed_records), 2)
        self.assertEqual([row['invoice_number'] for row in audit.removed_records], ['FIRST-RECORD'] * 2)
        for row in [audit.retained_record, *audit.removed_records]:
            self.assertEqual(row['legacy_import_note'], 'Original restored-column value')
            self.assertIn('created_at', row)
            self.assertIn('updated_at', row)
            self.assertEqual(row['id'], 688)
        self.assert_references_preserved()
        detail = self.detail_request('get', 'retrieve')
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data['invoice_number'], 'SECOND-RECORD')
        # A retried confirmation cannot remove the final retained record.
        self.assertEqual(self.resolve(group).status_code, 409)
        self.assertEqual(CustomerInvoice.objects.filter(pk=688).count(), 1)
        self.assertEqual(InvoiceDuplicateResolution.objects.count(), 1)

    def test_user_can_retain_one_of_two_exact_copies(self):
        group = self.review()
        copies = [record for record in group['records'] if record['invoice_number'] == 'FIRST-RECORD']
        response = self.resolve(group, copies[1])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(CustomerInvoice.objects.get(pk=688).invoice_number, 'FIRST-RECORD')
        audit = InvoiceDuplicateResolution.objects.get()
        self.assertCountEqual([row['invoice_number'] for row in audit.removed_records],
                              ['FIRST-RECORD', 'SECOND-RECORD'])
        self.assert_references_preserved()

    def test_changed_contents_reject_stale_review_without_additional_changes(self):
        group = self.review()
        self.sql(f'UPDATE {self.table} SET invoice_amount = %s WHERE invoice_number = %s',
                 ['888.99', 'SECOND-RECORD'])
        before = self.table_rows(self.copy_table)
        self.assertEqual(self.resolve(group).status_code, 409)
        self.assert_no_resolution(before)

    def test_new_group_member_rejects_stale_review(self):
        group = self.review()
        self.sql(f'INSERT INTO {self.table} SELECT * FROM {self.table} WHERE invoice_number = %s',
                 ['SECOND-RECORD'])
        before = self.table_rows(self.copy_table)
        self.assertEqual(self.resolve(group).status_code, 409)
        self.assert_no_resolution(before)

    def test_removed_group_member_rejects_stale_review(self):
        group = self.review()
        locator = 'ctid' if connection.vendor == 'postgresql' else 'rowid'
        self.sql(f'DELETE FROM {self.table} WHERE {locator} = '
                 f'(SELECT {locator} FROM {self.table} WHERE invoice_number = %s LIMIT 1)', ['FIRST-RECORD'])
        before = self.table_rows(self.copy_table)
        self.assertEqual(self.resolve(group).status_code, 409)
        self.assert_no_resolution(before)

    def test_tampered_and_missing_tokens_cannot_delete(self):
        group = self.review()
        keep = self.keep(group)['record_token']
        before = self.table_rows(self.copy_table)
        for body in (
            {}, {'group_token': group['group_token']},
            {'group_token': group['group_token'] + 'invalid', 'keep_token': keep},
            {'group_token': group['group_token'], 'keep_token': keep + 'invalid'},
        ):
            with self.subTest(body=body):
                self.assertEqual(self.api('delete', body=body).status_code, 400)
                self.assert_no_resolution(before)

    def test_expired_review_cannot_delete(self):
        group = self.review()
        before = self.table_rows(self.copy_table)
        future = time.time() + service.REVIEW_TOKEN_MAX_AGE + 5
        with patch('django.core.signing.time.time', return_value=future):
            self.assertEqual(self.resolve(group).status_code, 409)
        self.assert_no_resolution(before)

    def test_tokens_are_bound_to_the_reviewing_user(self):
        group = self.review()
        before = self.table_rows(self.copy_table)
        with self.assertRaises(service.DuplicateReviewError) as caught:
            service.resolve_duplicate_invoices(SimpleNamespace(pk=uuid4(), is_authenticated=True), group['group_token'],
                                               self.keep(group)['record_token'])
        self.assertEqual(caught.exception.status_code, 403)
        self.assertIn('another user', caught.exception.detail)
        self.assert_no_resolution(before)

    def test_keep_token_from_another_group_cannot_select_a_record(self):
        self.sql(f'INSERT INTO {self.table} SELECT * FROM {self.table} WHERE id = 690')
        group, other = self.review(), self.review(690)
        before = self.table_rows(self.copy_table)
        response = self.resolve(group, other['records'][0])
        self.assertEqual(response.status_code, 400, response.data)
        self.assert_no_resolution(before)

    def test_audit_failure_rolls_back_all_deletion(self):
        group = self.review()
        before = self.table_rows(self.copy_table)
        with patch.object(InvoiceDuplicateResolution.objects, 'create', side_effect=RuntimeError('Audit unavailable')):
            with self.assertRaisesMessage(RuntimeError, 'Audit unavailable'):
                self.resolve(group)
        self.assert_no_resolution(before)

    def test_read_permission_does_not_grant_duplicate_deletion(self):
        group = self.review()
        RolePermission.objects.filter(role=self.role, permission__module__code='finance_outgoing',
                                      permission__action='delete').delete()
        cache.clear()
        before = self.table_rows(self.copy_table)
        with CaptureQueriesContext(connection) as queries:
            response = self.resolve(group)
        self.assertEqual(response.status_code, 403, response.data)
        self.assertFalse(any(self.copy_table in row['sql'] for row in queries.captured_queries))
        self.assert_no_resolution(before)

    def test_review_requires_read_access_and_paginates_groups(self):
        self.sql(f'INSERT INTO {self.table} SELECT * FROM {self.table} WHERE id = 690')
        pages = [self.api('get', params={'page': number, 'page_size': 1}) for number in (1, 2)]
        self.assertTrue(all(page.status_code == 200 for page in pages))
        self.assertEqual({page.data['groups'][0]['invoice_id'] for page in pages}, {688, 690})
        self.assertTrue(all(len(page.data['groups']) == 1 for page in pages))
        group = next(page.data['groups'][0] for page in pages if page.data['groups'][0]['invoice_id'] == 688)
        before = self.table_rows(self.copy_table)
        RolePermission.objects.filter(role=self.role, permission__module__code='finance_outgoing',
                                      permission__action='read').delete()
        cache.clear()
        self.assertEqual(self.api('get').status_code, 403)
        self.assertEqual(self.resolve(group).status_code, 403)
        self.assert_no_resolution(before)


@unittest.skipUnless(LOCAL_POSTGRESQL, 'Requires local PostgreSQL; private schema always rolls back.')
class DuplicateInvoicePostgreSQLTests(unittest.TestCase):
    def setUp(self):
        self.schema = 'invoice_duplicate_test_' + uuid4().hex
        self.atomic = transaction.atomic()
        self.atomic.__enter__()
        self.addCleanup(self.rollback_schema)
        quote = connection.ops.quote_name
        self.sql(f'CREATE SCHEMA {quote(self.schema)}')
        self.sql(f'SET LOCAL search_path TO {quote(self.schema)}')
        self.assertEqual(self.sql('SELECT current_schema()'), [(self.schema,)])
        self.table = quote(CustomerInvoice._meta.db_table)
        columns = ', '.join(f'{quote(field.column)} {field.db_type(connection)}'
                            for field in CustomerInvoice._meta.concrete_fields)
        self.sql(f'CREATE TABLE {self.table} ({columns}, legacy_import_note text)')
        attachment_columns = ', '.join(f'{quote(field.column)} {field.db_type(connection)}'
                                       for field in InvoiceAttachment._meta.concrete_fields)
        self.sql(f'CREATE TABLE {quote(InvoiceAttachment._meta.db_table)} ({attachment_columns})')
        with connection.schema_editor(atomic=False) as editor:
            editor.create_model(InvoiceDuplicateResolution)
        CustomerInvoice.objects.bulk_create([
            CustomerInvoice(id=688, invoice_number='KEEP', invoice_amount=Decimal('123.45'), company='Chosen'),
            CustomerInvoice(id=688, invoice_number='REMOVE', invoice_amount=Decimal('987.65'), company='Other'),
        ])
        self.sql(f'UPDATE {self.table} SET legacy_import_note = %s', ['Preserved legacy field'])
        self.sql('CREATE TABLE invoice_duplicate_reference (invoice_id bigint, marker text)')
        self.sql('INSERT INTO invoice_duplicate_reference VALUES (688, %s)', ['Attachment/history reference'])
        self.user = SimpleNamespace(pk=uuid4(), is_authenticated=True)

    @staticmethod
    def sql(statement, params=None):
        with connection.cursor() as cursor:
            cursor.execute(statement, params)
            return cursor.fetchall() if cursor.description else None

    def rollback_schema(self):
        transaction.set_rollback(True)
        self.atomic.__exit__(None, None, None)
        self.assertEqual(self.sql('SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = %s)',
                                  [self.schema]), [(False,)])

    def review(self):
        group = service.list_duplicate_invoices(self.user, invoice_id=688)['groups'][0]
        keep = next(record for record in group['records'] if record['invoice_number'] == 'KEEP')
        return group, keep

    def rows(self):
        return self.sql(f'SELECT ctid::text, xmin::text, * FROM {self.table} ORDER BY invoice_number')

    def test_native_ctid_keeps_selected_physical_row_and_holds_membership_lock(self):
        before = self.rows()
        group, keep = self.review()
        service.resolve_duplicate_invoices(self.user, group['group_token'], keep['record_token'])
        self.assertEqual(self.rows(), [before[0]])
        self.assertEqual(self.sql('SELECT * FROM invoice_duplicate_reference'),
                         [(688, 'Attachment/history reference')])
        audit = InvoiceDuplicateResolution.objects.get()
        self.assertEqual(audit.retained_record['invoice_number'], 'KEEP')
        self.assertEqual(audit.removed_records[0]['invoice_number'], 'REMOVE')
        self.assertEqual(audit.removed_records[0]['legacy_import_note'], 'Preserved legacy field')
        self.assertIn(('ShareRowExclusiveLock',), self.sql(
            'SELECT mode FROM pg_locks WHERE pid=pg_backend_pid() AND relation=to_regclass(%s) AND granted',
            [CustomerInvoice._meta.db_table]))

    def test_post_review_physical_update_is_stale_even_when_values_are_restored(self):
        group, keep = self.review()
        # A PostgreSQL UPDATE changes the physical version even for equal values.
        self.sql(f'UPDATE {self.table} SET invoice_amount=invoice_amount WHERE invoice_number=%s', ['REMOVE'])
        before = self.rows()
        with self.assertRaises(service.DuplicateReviewError) as caught:
            service.resolve_duplicate_invoices(self.user, group['group_token'], keep['record_token'])
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self.rows(), before)
        self.assertFalse(InvoiceDuplicateResolution.objects.exists())

    def test_native_audit_failure_rolls_back_resolution(self):
        group, keep = self.review()
        before = self.rows()
        with patch.object(InvoiceDuplicateResolution.objects, 'create', side_effect=RuntimeError('Audit failed')):
            with self.assertRaisesRegex(RuntimeError, 'Audit failed'):
                service.resolve_duplicate_invoices(self.user, group['group_token'], keep['record_token'])
        self.assertEqual(self.rows(), before)
        self.assertFalse(InvoiceDuplicateResolution.objects.exists())
