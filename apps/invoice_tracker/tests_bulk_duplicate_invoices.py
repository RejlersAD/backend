"""Bulk duplicate resolution is atomic across all explicitly selected keepers.

API cases use the existing isolated invoice-table copy. Native PostgreSQL cases
use private schemas that roll back, with the existing local-host safety guard.
"""
from decimal import Decimal
import time
import unittest
from unittest.mock import patch

from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from apps.finance.receivables_source_models import ReceivablesSourceRow, ReceivablesSourceSnapshot
from apps.invoice_tracker import tests_duplicate_invoices as fixtures
from apps.invoice_tracker.models import CustomerInvoice, InvoiceAttachment, InvoiceDuplicateResolution
from apps.invoice_tracker.services import duplicate_invoices as service
from apps.rbac.models import RolePermission


BULK_URL = '/api/v1/invoice-tracker/invoices/duplicates/bulk/'


def selection(group, invoice_number):
    record = next(record for record in group['records'] if record['invoice_number'] == invoice_number)
    return {'group_token': group['group_token'], 'keep_token': record['record_token']}


@override_settings(ROOT_URLCONF='apps.invoice_tracker.tests_collections')
class BulkDuplicateInvoiceAPITests(TestCase):
    grant = fixtures.DuplicateInvoiceAPITests.grant
    grant_writes = fixtures.DuplicateInvoiceAPITests.grant_writes
    drop_copy = fixtures.DuplicateInvoiceAPITests.drop_copy
    table_rows = staticmethod(fixtures.DuplicateInvoiceAPITests.table_rows)
    detail_request = fixtures.DuplicateInvoiceAPITests.detail_request
    table = fixtures.DuplicateInvoiceAPITests.table
    sql = staticmethod(fixtures.DuplicateInvoiceAPITests.sql)
    api = fixtures.DuplicateInvoiceAPITests.api
    review = fixtures.DuplicateInvoiceAPITests.review
    assert_references_preserved = fixtures.DuplicateInvoiceAPITests.assert_references_preserved
    assert_no_resolution = fixtures.DuplicateInvoiceAPITests.assert_no_resolution

    def setUp(self):
        fixtures.DuplicateInvoiceAPITests.setUp(self)
        self.sql(f'UPDATE {self.table} SET invoice_number=%s, invoice_amount=%s, '
                 'invoice_amount_aed=%s, actual_payment_received=%s WHERE id=690',
                 ['KEEP-690', '312.34', '1146.25', '25.12'])
        CustomerInvoice.objects.bulk_create([
            CustomerInvoice(id=690, invoice_number='REMOVE-690', invoice_amount=Decimal('999.99'),
                            invoice_amount_aed=Decimal('3674.96'), company='Other financial record'),
        ])
        InvoiceAttachment.objects.create(invoice_id=690, file='synthetic/group-690.pdf',
                                         original_filename='group-690.pdf')
        snapshot = ReceivablesSourceSnapshot.objects.get()
        ReceivablesSourceRow.objects.create(snapshot=snapshot, row_number=8, invoice_number='SOURCE-690',
                                            register_invoice_id=690, invoice_amount=Decimal('9999.99'))
        snapshot.last_row, snapshot.row_count = 8, 3
        snapshot.save(update_fields=['last_row', 'row_count'])
        self.attachments = list(InvoiceAttachment.objects.order_by('pk').values())
        self.source_rows = list(ReceivablesSourceRow.objects.order_by('pk').values())
        self.source_snapshots = list(ReceivablesSourceSnapshot.objects.order_by('pk').values())

    def selections(self):
        return [selection(self.review(), 'SECOND-RECORD'), selection(self.review(690), 'KEEP-690')]

    def bulk(self, selections):
        return self.client.delete(BULK_URL, {'selections': selections}, format='json')

    def test_bulk_resolves_selected_groups_preserving_financials_references_and_full_archives(self):
        # Input order is intentionally different from numeric invoice order.
        selections = list(reversed(self.selections()))
        with patch('apps.invoice_tracker.models.CustomerInvoice.delete',
                   side_effect=AssertionError('Bulk resolution must not use ORM cascades')), \
                patch('django.db.models.fields.files.FieldFile.delete',
                      side_effect=AssertionError('Attachments must not be removed')):
            response = self.bulk(selections)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['resolved_count'], 2)
        self.assertEqual(response.data['removed_count'], 3)
        self.assertEqual([result['invoice_id'] for result in response.data['results']], [690, 688])
        self.assertIn('no-store', response['Cache-Control'])
        first = CustomerInvoice.objects.get(pk=688)
        second = CustomerInvoice.objects.get(pk=690)
        self.assertEqual((first.invoice_number, first.invoice_amount, first.actual_payment_received),
                         ('SECOND-RECORD', Decimal('750.25'), Decimal('12.50')))
        self.assertEqual((second.invoice_number, second.invoice_amount, second.invoice_amount_aed,
                          second.actual_payment_received),
                         ('KEEP-690', Decimal('312.34'), Decimal('1146.25'), Decimal('25.12')))
        self.assertEqual(CustomerInvoice.objects.count(), 2)
        audits = {audit.invoice_id: audit for audit in InvoiceDuplicateResolution.objects.all()}
        self.assertEqual(set(audits), {688, 690})
        self.assertEqual([row['invoice_number'] for row in audits[688].removed_records], ['FIRST-RECORD'] * 2)
        self.assertEqual(audits[690].removed_records[0]['invoice_number'], 'REMOVE-690')
        for audit in audits.values():
            self.assertEqual(audit.actor_id, str(self.user.pk))
            self.assertIn('legacy_import_note', audit.retained_record)
            self.assertTrue(all('legacy_import_note' in row for row in audit.removed_records))
        self.assert_references_preserved()
        for identity in (688, 690):
            self.assertEqual(self.detail_request('get', 'retrieve', identity=identity).status_code, 200)
        # Replaying a successful batch cannot delete its last remaining records.
        self.assertEqual(self.bulk(selections).status_code, 409)
        self.assertEqual(CustomerInvoice.objects.count(), 2)
        self.assertEqual(InvoiceDuplicateResolution.objects.count(), 2)

    def test_review_keys_are_stable_across_refreshed_tokens_and_change_with_record_contents(self):
        first = self.review()
        later = time.time() + 120
        with patch('django.core.signing.time.time', return_value=later):
            refreshed = self.review()
        self.assertEqual(first['review_key'], refreshed['review_key'])
        self.assertNotEqual(first['group_token'], refreshed['group_token'])
        self.assertEqual({row['record_key'] for row in first['records']},
                         {row['record_key'] for row in refreshed['records']})
        self.assertEqual(len({row['record_key'] for row in first['records']}), 3)
        self.sql(f'UPDATE {self.table} SET remarks=%s WHERE invoice_number=%s',
                 ['Reviewed data changed', 'SECOND-RECORD'])
        changed = self.review()
        self.assertNotEqual(first['review_key'], changed['review_key'])
        previous = next(row for row in first['records'] if row['invoice_number'] == 'SECOND-RECORD')
        current = next(row for row in changed['records'] if row['invoice_number'] == 'SECOND-RECORD')
        self.assertNotEqual(previous['record_key'], current['record_key'])

    def test_capabilities_and_read_delete_permissions_use_actual_bulk_router(self):
        selections = self.selections()
        review = self.api('get')
        self.assertEqual(review.data['limits']['bulk_groups'], 50)
        self.assertTrue(review.data['capabilities']['bulk_resolve'])
        before = self.table_rows(self.copy_table)
        RolePermission.objects.filter(role=self.role, permission__module__code='finance_outgoing',
                                      permission__action='delete').delete()
        cache.clear()
        self.assertFalse(self.api('get').data['capabilities']['bulk_resolve'])
        with CaptureQueriesContext(connection) as queries:
            response = self.bulk(selections)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(any(self.copy_table in row['sql'] for row in queries.captured_queries))
        self.assert_no_resolution(before)
        self.grant_writes()
        RolePermission.objects.filter(role=self.role, permission__module__code='finance_outgoing',
                                      permission__action='read').delete()
        cache.clear()
        self.assertEqual(self.bulk(selections).status_code, 403)
        self.assert_no_resolution(before)

    def test_empty_over_limit_duplicate_ids_and_bad_selection_shapes_reject_entire_batch(self):
        selections = self.selections()
        before = self.table_rows(self.copy_table)
        for invalid in ([], selections[:1] * 51, [selections[0], selections[0]],
                        [selections[0], {}], 'not-a-list'):
            with self.subTest(selection_shape=type(invalid).__name__, length=len(invalid)):
                self.assertEqual(self.bulk(invalid).status_code, 400)
                self.assert_no_resolution(before)

    def test_tampered_token_in_later_group_cannot_resolve_earlier_valid_group(self):
        selections = self.selections()
        selections[1]['keep_token'] += 'invalid'
        before = self.table_rows(self.copy_table)
        self.assertEqual(self.bulk(selections).status_code, 400)
        self.assert_no_resolution(before)

    def test_expired_later_selection_cannot_resolve_a_fresh_selection(self):
        expired = selection(self.review(690), 'KEEP-690')
        future = time.time() + service.REVIEW_TOKEN_MAX_AGE + 5
        with patch('django.core.signing.time.time', return_value=future):
            fresh = selection(self.review(), 'SECOND-RECORD')
            before = self.table_rows(self.copy_table)
            self.assertEqual(self.bulk([fresh, expired]).status_code, 409)
        self.assert_no_resolution(before)

    def test_stale_later_group_aborts_every_group_before_any_archive_is_attempted(self):
        selections = self.selections()
        self.sql(f'UPDATE {self.table} SET invoice_amount=%s WHERE invoice_number=%s', ['777.77', 'REMOVE-690'])
        before = self.table_rows(self.copy_table)
        with patch.object(InvoiceDuplicateResolution.objects, 'create') as archive:
            response = self.bulk(selections)
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['stale_invoice_ids'], [690])
        archive.assert_not_called()
        self.assert_no_resolution(before)

    def test_all_stale_selected_groups_are_reported_without_changes(self):
        selections = self.selections()
        self.sql(f'UPDATE {self.table} SET remarks=%s WHERE id IN (688, 690)', ['Changed since review'])
        before = self.table_rows(self.copy_table)
        response = self.bulk(selections)
        self.assertEqual(response.status_code, 409, response.data)
        self.assertCountEqual(response.data['stale_invoice_ids'], [688, 690])
        self.assert_no_resolution(before)

    def test_new_member_in_later_group_invalidates_entire_batch(self):
        selections = self.selections()
        self.sql(f'INSERT INTO {self.table} SELECT * FROM {self.table} WHERE invoice_number=%s', ['REMOVE-690'])
        before = self.table_rows(self.copy_table)
        self.assertEqual(self.bulk(selections).status_code, 409)
        self.assert_no_resolution(before)

    def test_second_archive_failure_rolls_back_first_archive_and_all_deletions(self):
        selections = self.selections()
        before = self.table_rows(self.copy_table)
        create = InvoiceDuplicateResolution.objects.create
        calls = []

        def fail_second(**values):
            calls.append(values['invoice_id'])
            if len(calls) == 2:
                raise RuntimeError('Second archive unavailable')
            return create(**values)

        with patch.object(InvoiceDuplicateResolution.objects, 'create', side_effect=fail_second):
            with self.assertRaisesMessage(RuntimeError, 'Second archive unavailable'):
                self.bulk(selections)
        self.assertEqual(calls, [688, 690])
        self.assert_no_resolution(before)

    def test_later_delete_failure_restores_earlier_deleted_rows_and_all_archives(self):
        selections = self.selections()
        before = self.table_rows(self.copy_table)
        deleted = []

        def fail_later_delete(execute, sql, params, many, context):
            if sql.lstrip().upper().startswith('DELETE FROM') and self.copy_table in sql:
                if params[0] == 690:
                    raise RuntimeError('Later physical deletion failed')
                result = execute(sql, params, many, context)
                deleted.append(params[0])
                return result
            return execute(sql, params, many, context)

        with connection.execute_wrapper(fail_later_delete):
            with self.assertRaisesMessage(RuntimeError, 'Later physical deletion failed'):
                self.bulk(selections)
        self.assertEqual(deleted, [688, 688])
        self.assert_no_resolution(before)


@unittest.skipUnless(fixtures.LOCAL_POSTGRESQL, 'Requires local PostgreSQL; private schema always rolls back.')
class BulkDuplicateInvoicePostgreSQLTests(unittest.TestCase):
    sql = staticmethod(fixtures.DuplicateInvoicePostgreSQLTests.sql)
    rollback_schema = fixtures.DuplicateInvoicePostgreSQLTests.rollback_schema
    rows = fixtures.DuplicateInvoicePostgreSQLTests.rows

    def setUp(self):
        fixtures.DuplicateInvoicePostgreSQLTests.setUp(self)
        CustomerInvoice.objects.bulk_create([
            CustomerInvoice(id=690, invoice_number='KEEP-690', invoice_amount=Decimal('312.34')),
            CustomerInvoice(id=690, invoice_number='REMOVE-690', invoice_amount=Decimal('999.99')),
        ])
        self.sql('INSERT INTO invoice_duplicate_reference VALUES (690, %s)', ['Second reference'])

    def selections(self):
        groups = service.list_duplicate_invoices(self.user)['groups']
        return [selection(group, 'KEEP' if group['invoice_id'] == 688 else 'KEEP-690') for group in groups]

    def assert_references(self):
        self.assertEqual(self.sql('SELECT * FROM invoice_duplicate_reference ORDER BY invoice_id'),
                         [(688, 'Attachment/history reference'), (690, 'Second reference')])

    def test_bulk_retains_both_selected_physical_rows_and_locks_table_once(self):
        before = self.rows()
        selections = self.selections()
        with CaptureQueriesContext(connection) as queries:
            result = service.resolve_duplicate_invoice_groups(self.user, selections)
        self.assertEqual((result['resolved_count'], result['removed_count']), (2, 2))
        self.assertEqual(self.rows(), [row for row in before if row[3] in {'KEEP', 'KEEP-690'}])
        locks = [row['sql'] for row in queries.captured_queries if 'LOCK TABLE' in row['sql'].upper()]
        self.assertEqual(len(locks), 1)
        self.assertIn(('ShareRowExclusiveLock',), self.sql(
            'SELECT mode FROM pg_locks WHERE pid=pg_backend_pid() AND relation=to_regclass(%s) AND granted',
            [CustomerInvoice._meta.db_table]))
        self.assertEqual(InvoiceDuplicateResolution.objects.count(), 2)
        self.assert_references()

    def test_native_stale_second_group_prevents_any_first_group_deletion(self):
        selections = self.selections()
        self.sql(f'UPDATE {self.table} SET invoice_amount=invoice_amount WHERE id=690')
        before = self.rows()
        with self.assertRaises(service.DuplicateReviewError) as caught:
            service.resolve_duplicate_invoice_groups(self.user, selections)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.stale_invoice_ids, [690])
        self.assertEqual(self.rows(), before)
        self.assertFalse(InvoiceDuplicateResolution.objects.exists())
        self.assert_references()

    def test_native_second_archive_failure_rolls_back_whole_batch(self):
        selections = self.selections()
        before = self.rows()
        create = InvoiceDuplicateResolution.objects.create
        calls = []

        def fail_second(**values):
            calls.append(values['invoice_id'])
            if len(calls) == 2:
                raise RuntimeError('Second archive failed')
            return create(**values)

        with patch.object(InvoiceDuplicateResolution.objects, 'create', side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, 'Second archive failed'):
                service.resolve_duplicate_invoice_groups(self.user, selections)
        self.assertEqual(calls, [688, 690])
        self.assertEqual(self.rows(), before)
        self.assertFalse(InvoiceDuplicateResolution.objects.exists())
        self.assert_references()

    def test_native_later_delete_failure_restores_original_ctids_and_archives(self):
        selections = self.selections()
        before = self.rows()
        deleted = []

        def fail_later_delete(execute, sql, params, many, context):
            if sql.lstrip().upper().startswith('DELETE FROM') and CustomerInvoice._meta.db_table in sql:
                if params[0] == 690:
                    raise RuntimeError('Later physical deletion failed')
                result = execute(sql, params, many, context)
                deleted.append(params[0])
                return result
            return execute(sql, params, many, context)

        with connection.execute_wrapper(fail_later_delete):
            with self.assertRaisesRegex(RuntimeError, 'Later physical deletion failed'):
                service.resolve_duplicate_invoice_groups(self.user, selections)
        self.assertEqual(deleted, [688])
        self.assertEqual(self.rows(), before)
        self.assertFalse(InvoiceDuplicateResolution.objects.exists())
        self.assert_references()
