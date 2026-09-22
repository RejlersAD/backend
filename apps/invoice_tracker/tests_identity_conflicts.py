"""Ambiguous legacy invoice IDs must never select or mutate an arbitrary row.

Each test clones the test database's invoice table without its constraints and
temporarily points the model at that isolated copy. Application tables, schema,
and invoice identities are never altered by the duplicate-ID fixture.
"""
from unittest.mock import patch
from uuid import uuid4

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.db.models.expressions import Col
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.invoice_tracker import tests_collections as collection_helpers
from apps.invoice_tracker.models import CustomerInvoice, InvoiceAttachment
from apps.invoice_tracker.views import CustomerInvoiceViewSet
from apps.rbac.models import Permission, RolePermission


BASE = '/api/v1/invoice-tracker/invoices/'


class InvoiceIdentityConflictTests(TestCase):
    grant = collection_helpers.CollectionsTests.grant

    def setUp(self):
        collection_helpers.CollectionsTests.setUp(self)
        self.grant()
        self.factory = APIRequestFactory()
        self.original_table = CustomerInvoice._meta.db_table
        self.copy_table = 'invoice_identity_test_' + uuid4().hex
        for identity, number, company in (
            (688, 'FIRST-RECORD', 'First company'),
            (689, 'SECOND-RECORD', 'Second company'),
            (690, 'UNIQUE-RECORD', 'Unique company'),
        ):
            invoice = CustomerInvoice(id=identity, invoice_number=number, company=company,
                                      invoice_amount=100, invoice_amount_aed=100,
                                      actual_payment_received=0, payment_status='pending')
            invoice.save(_skip_recompute=True)
        InvoiceAttachment.objects.create(invoice_id=688, file='synthetic/first.pdf',
                                         original_filename='first.pdf')
        InvoiceAttachment.objects.create(invoice_id=689, file='synthetic/second.pdf',
                                         original_filename='second.pdf')
        self.original_rows = self.table_rows(self.original_table)
        quote = connection.ops.quote_name
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE TABLE {quote(self.copy_table)} AS SELECT * FROM {quote(self.original_table)}')
            cursor.execute(f'UPDATE {quote(self.copy_table)} SET id = 688 WHERE id = 689')
        self.addCleanup(self.drop_copy)
        # Materialize cached aliases before changing model metadata, so patch
        # restoration cannot accidentally retain aliases to the copied table.
        for field in CustomerInvoice._meta.concrete_fields:
            _ = field.cached_col
        table_patch = patch.object(CustomerInvoice._meta, 'db_table', self.copy_table)
        table_patch.start()
        self.addCleanup(table_patch.stop)
        # Model fields cache the default SQL column alias after the seed writes.
        # Keep those aliases on the copied table, then restore every cached Col.
        for field in CustomerInvoice._meta.concrete_fields:
            column_patch = patch.object(field, 'cached_col', Col(self.copy_table, field))
            column_patch.start()
            self.addCleanup(column_patch.stop)
        queryset_patch = patch.object(
            CustomerInvoiceViewSet, 'queryset', CustomerInvoice.objects.all().prefetch_related('attachments'),
        )
        queryset_patch.start()
        self.addCleanup(queryset_patch.stop)
        self.duplicate_rows = self.table_rows(self.copy_table)
        self.attachments = list(InvoiceAttachment.objects.order_by('pk').values())

    def drop_copy(self):
        with connection.cursor() as cursor:
            cursor.execute(f'DROP TABLE {connection.ops.quote_name(self.copy_table)}')

    @staticmethod
    def table_rows(table):
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT * FROM {connection.ops.quote_name(table)} ORDER BY invoice_number')
            return cursor.fetchall()

    def grant_writes(self):
        for permission in Permission.objects.filter(
            module__code='finance_outgoing', action__in=['create', 'update', 'delete'], is_active=True,
        ):
            RolePermission.objects.get_or_create(role=self.role, permission=permission)
        cache.clear()

    def request(self, method, action, *, identity=688, query='', data=None, multipart=False):
        suffix = {'recompute': 'recompute/', 'upload_attachment': 'upload-attachment/'}.get(action, '')
        url = f'{BASE}{identity}/{suffix}{query}'
        request = getattr(self.factory, method)(url, data or {}, format='multipart' if multipart else 'json')
        force_authenticate(request, self.user)
        return CustomerInvoiceViewSet.as_view({method: action})(request, pk=str(identity))

    def assert_preserved(self):
        self.assertEqual(self.table_rows(self.copy_table), self.duplicate_rows)
        self.assertEqual(self.table_rows(self.original_table), self.original_rows)
        self.assertEqual(list(InvoiceAttachment.objects.order_by('pk').values()), self.attachments)

    def assert_conflict(self, response):
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'invoice_identity_conflict')
        self.assertIn('shared by multiple records', str(response.data['detail']))
        response.render()
        self.assertTrue(response['Content-Type'].startswith('application/json'))

    def test_real_duplicate_id_returns_conflict_before_serialization(self):
        # The copied physical table, not a mocked manager, reproduces the legacy
        # violation of Django's primary-key assumption.
        with self.assertRaises(CustomerInvoice.MultipleObjectsReturned):
            CustomerInvoice.objects.get(pk=688)
        with patch('apps.invoice_tracker.serializers.CustomerInvoiceSerializer.to_representation',
                   side_effect=AssertionError('An ambiguous invoice must not be serialized')):
            self.assert_conflict(self.request('get', 'retrieve'))
        self.assert_preserved()

    def test_filters_cannot_hide_duplicate_identity_on_detail_reads(self):
        for query in ('?company=First%20company', '?search=FIRST-RECORD', '?company=No%20match'):
            with self.subTest(query=query):
                self.assert_conflict(self.request('get', 'retrieve', query=query))
        self.assert_preserved()

    def test_every_invoice_write_route_rejects_duplicates_without_side_effects(self):
        self.grant_writes()
        for method, action, data, multipart in (
            ('put', 'update', {'invoice_number': 'CHANGED', 'remarks': 'Unsafe update'}, False),
            ('patch', 'partial_update', {'remarks': 'Unsafe update'}, False),
            ('delete', 'destroy', {}, False),
            ('post', 'recompute', {}, False),
            ('post', 'upload_attachment', {'file': SimpleUploadedFile('new.pdf', b'synthetic')}, True),
        ):
            with self.subTest(action=action), CaptureQueriesContext(connection) as queries, \
                    patch('apps.invoice_tracker.models.CustomerInvoice.save',
                          side_effect=AssertionError('An ambiguous invoice must not be saved')), \
                    patch('django.db.models.fields.files.FieldFile.delete',
                          side_effect=AssertionError('Attachments must not be deleted')), \
                    patch('django.db.models.fields.files.FieldFile.save',
                          side_effect=AssertionError('Attachments must not be written')):
                response = self.request(method, action, query='?company=First%20company',
                                        data=data, multipart=multipart)
                self.assert_conflict(response)
            self.assertFalse(any(row['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE '))
                                 for row in queries.captured_queries))
            self.assert_preserved()

    def test_read_only_user_is_denied_before_identity_lookup_on_writes(self):
        for method, action in (('put', 'update'), ('patch', 'partial_update'), ('delete', 'destroy'),
                               ('post', 'recompute'), ('post', 'upload_attachment')):
            with self.subTest(action=action), CaptureQueriesContext(connection) as queries:
                response = self.request(method, action, data={'remarks': 'Denied'})
                self.assertEqual(response.status_code, 403, response.data)
            self.assertFalse(any(self.copy_table in row['sql'] for row in queries.captured_queries))
        self.assert_preserved()

    def test_unique_and_missing_ids_keep_existing_detail_behavior(self):
        response = self.request('get', 'retrieve', identity=690)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['invoice_number'], 'UNIQUE-RECORD')
        self.assertEqual(self.request('get', 'retrieve', identity=690,
                                      query='?company=No%20match').status_code, 404)
        self.assertEqual(self.request('get', 'retrieve', identity=999999).status_code, 404)
        self.assertEqual(self.request('get', 'retrieve', identity='invalid').status_code, 404)
        self.assert_preserved()

    def test_unique_invoice_still_allows_authorized_update(self):
        self.grant_writes()
        response = self.request('patch', 'partial_update', identity=690, data={'remarks': 'Reviewed'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(CustomerInvoice.objects.get(pk=690).remarks, 'Reviewed')
        self.assertEqual(list(CustomerInvoice.objects.filter(pk=688).order_by('invoice_number')
                              .values_list('invoice_number', 'remarks')),
                         [('FIRST-RECORD', ''), ('SECOND-RECORD', '')])
        self.assertEqual(self.table_rows(self.original_table), self.original_rows)
        self.assertEqual(list(InvoiceAttachment.objects.order_by('pk').values()), self.attachments)
