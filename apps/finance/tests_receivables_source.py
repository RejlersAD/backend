"""Source imports preserve Finance's cells and never rewrite the operational register."""
import io
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from openpyxl import Workbook

from apps.finance.receivables_source_models import ReceivablesSourceRow, ReceivablesSourceSnapshot
from apps.finance.services.receivables_source import (
    SOURCE_HEADERS, get_active_receivables_source, import_receivables_source, read_receivables_source,
)
from apps.invoice_tracker.models import CustomerInvoice


class ReceivablesSourceTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def workbook(self, rows, *, name='finance.xlsx'):
        path = Path(self.directory.name) / name
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = 'External Invoice '
        for column, header in SOURCE_HEADERS.items():
            sheet[f'{column}5'] = header
        for number, row in enumerate(rows, 6):
            values = {'A': f'INV-{number}', 'B': date(2026, 9, 1), 'E': 'Account reference',
                      'F': 'Customer', 'N': date(2026, 9, 20), 'R': 'Pending', **row}
            for column, value in values.items():
                if column != 'formats':
                    sheet[f'{column}{number}'] = value
            for column, number_format in row.get('formats', {}).items():
                sheet[f'{column}{number}'].number_format = number_format
        workbook.save(path)
        workbook.close()
        return path

    def load(self, path, count, **kwargs):
        return import_receivables_source(path, last_row=5 + count, **kwargs)

    def test_source_preserves_monetary_cells_status_and_independent_currencies(self):
        path = self.workbook([
            {'A': 'USD', 'L': 13650, 'M': 50095.5, 'R': 'Overdue',
             'formats': {'L': '[$USD] #,##0.00', 'M': '[$AED] #,##0.00'}},
            {'A': 'PARTIAL', 'L': 1000, 'M': 1000, 'R': 'Paid (partial)', 'Y': 12.34567891,
             'AA': 300, 'formats': {'L': '[$AED] #,##0.00', 'Y': '[$AED] #,##0.00',
                                    'AA': '[$EUR] #,##0.00'}},
            {'A': 'NEW', 'L': 40, 'M': 80, 'R': 'New', 'Y': '#VALUE!', 'AA': None},
            {'A': 'REJECTED', 'L': 60, 'M': '#VALUE!', 'R': 'Rejected', 'N': None},
            {'A': 'CONFLICT', 'L': 10, 'M': 40, 'AE': 'EUR', 'formats': {'L': '[$USD] #,##0.00'}},
        ])
        metadata, rows, report = read_receivables_source(path, last_row=10)
        self.assertEqual(metadata['row_count'], 5)
        self.assertEqual(rows[0]['invoice_amount'], Decimal('13650'))
        self.assertEqual(rows[0]['invoice_amount_aed'], Decimal('50095.5'))
        self.assertEqual(rows[0]['currency'], 'USD')
        self.assertEqual(rows[0]['balance_currency'], '')
        self.assertIsNone(rows[0]['balance_to_be_received'])
        self.assertEqual(rows[1]['payment_status'], 'partial')
        self.assertEqual(rows[1]['raw_payment_status'], 'Paid (partial)')
        self.assertEqual(rows[1]['balance_to_be_received'], Decimal('12.34567891'))
        self.assertEqual(rows[1]['balance_currency'], 'AED')
        self.assertEqual(rows[1]['actual_payment_currency'], 'EUR')
        self.assertEqual(rows[2]['payment_status'], 'new')
        self.assertIsNone(rows[2]['balance_to_be_received'])
        self.assertEqual(rows[2]['currency_status'], 'not_recorded')
        self.assertEqual(rows[3]['payment_status'], 'rejected')
        self.assertIsNone(rows[3]['invoice_amount_aed'])
        self.assertIsNone(rows[3]['due_date'])
        self.assertEqual(rows[4]['currency_status'], 'conflict')
        self.assertEqual(rows[4]['currency'], '')
        self.assertEqual(report['overdue'], {'count': 1, 'amount_aed': '50095.50', 'missing_amount_count': 0})

    def test_dry_run_command_reconciles_without_database_queries(self):
        path = self.workbook([{'L': 100, 'M': 367, 'R': 'Overdue'},
                              {'L': 20, 'M': None, 'R': 'Overdue'}])
        output = io.StringIO()
        with CaptureQueriesContext(connection) as queries:
            call_command('import_receivables_source', str(path), last_row=7, dry_run=True, stdout=output)
        self.assertEqual(len(queries), 0)
        report = json.loads(output.getvalue())
        self.assertTrue(report['dry_run'])
        self.assertFalse(report['activated'])
        self.assertEqual(report['reconciliation']['overdue'],
                         {'count': 2, 'amount_aed': '367.00', 'missing_amount_count': 1})
        self.assertEqual(ReceivablesSourceSnapshot.objects.count(), 0)

    def test_import_never_queries_links_or_changes_operational_invoices(self):
        for number in ('UNIQUE', 'DUPLICATE'):
            invoice = CustomerInvoice(invoice_number=number, payment_status='paid', currency='AED',
                                      invoice_amount=999, invoice_amount_aed=999,
                                      balance_to_be_received=0, actual_payment_received=999)
            invoice.save(_skip_recompute=True)
        before = list(CustomerInvoice.objects.order_by('pk').values())
        path = self.workbook([{'A': 'UNIQUE', 'L': 10, 'M': 36.7, 'R': 'Overdue'},
                              {'A': 'DUPLICATE', 'L': 20, 'M': 20},
                              {'A': 'DUPLICATE', 'L': 30, 'M': 30}])
        with CaptureQueriesContext(connection) as queries, patch.object(
            CustomerInvoice, 'save', side_effect=AssertionError('Operational invoices must not be saved')
        ):
            report = self.load(path, 3)
        self.assertFalse(any('invoice_tracker_customerinvoice' in query['sql'] for query in queries))
        self.assertTrue(report['created'])
        self.assertTrue(report['activated'])
        self.assertEqual(report['reconciliation']['duplicate_invoice_number_count'], 1)
        self.assertEqual(list(CustomerInvoice.objects.order_by('pk').values()), before)
        rows = list(get_active_receivables_source())
        self.assertEqual([row.register_invoice_id for row in rows], [None, None, None])
        self.assertEqual(rows[0].payment_status, 'overdue')
        self.assertEqual(rows[0].invoice_amount_aed, Decimal('36.7'))
        self.assertEqual([row.row_number for row in rows], [6, 7, 8])

    def test_same_source_is_idempotent_and_activation_retains_previous_versions(self):
        self.assertIsNone(get_active_receivables_source())
        first_path = self.workbook([{'L': 10, 'M': 10}])
        first = self.load(first_path, 1)
        pinned = get_active_receivables_source()
        repeated = self.load(first_path, 1)
        self.assertEqual(repeated['snapshot_id'], first['snapshot_id'])
        self.assertFalse(repeated['created'])
        self.assertFalse(repeated['activated'])
        second_path = self.workbook([{'L': 20, 'M': 20}], name='replacement.xlsx')
        second = self.load(second_path, 1)
        self.assertNotEqual(first['snapshot_id'], second['snapshot_id'])
        self.assertEqual(ReceivablesSourceSnapshot.objects.count(), 2)
        self.assertEqual(ReceivablesSourceRow.objects.count(), 2)
        self.assertEqual(ReceivablesSourceSnapshot.objects.filter(is_active=True).count(), 1)
        self.assertEqual(get_active_receivables_source()._receivables_snapshot.pk, second['snapshot_id'])
        self.assertEqual(pinned.get().invoice_amount, Decimal('10'))
        self.assertEqual(get_active_receivables_source().get().invoice_amount, Decimal('20'))
        restored = self.load(first_path, 1)
        self.assertFalse(restored['created'])
        self.assertTrue(restored['activated'])
        self.assertEqual(ReceivablesSourceRow.objects.count(), 2)

    def test_failed_import_keeps_previous_active_snapshot_and_no_partial_rows(self):
        first = self.load(self.workbook([{'L': 10, 'M': 10}]), 1)
        replacement = self.workbook([{'L': 20, 'M': 20}], name='replacement.xlsx')
        with patch.object(ReceivablesSourceRow.objects, 'bulk_create', side_effect=RuntimeError('Write failed')):
            with self.assertRaisesMessage(RuntimeError, 'Write failed'):
                self.load(replacement, 1)
        self.assertEqual(ReceivablesSourceSnapshot.objects.count(), 1)
        self.assertEqual(ReceivablesSourceRow.objects.count(), 1)
        self.assertEqual(get_active_receivables_source()._receivables_snapshot.pk, first['snapshot_id'])

    def test_invalid_bounds_or_headers_cannot_publish_incomplete_source(self):
        path = self.workbook([{'L': 10, 'M': 10}, {'L': 20, 'M': 20}])
        with self.assertRaisesMessage(ValueError, 'after the specified last row'):
            self.load(path, 1)
        with self.assertRaisesMessage(ValueError, 'Only the External Invoice'):
            self.load(path, 2, sheet='Internal Invoice')
        with self.assertRaisesMessage(ValueError, 'immediately follow'):
            self.load(path, 2, first_row=7)
        from openpyxl import load_workbook
        workbook = load_workbook(path)
        workbook.active['M5'] = 'Wrong amount'
        workbook.save(path)
        workbook.close()
        with self.assertRaisesMessage(ValueError, 'Unexpected source header at M5'):
            self.load(path, 2)
        self.assertEqual(ReceivablesSourceSnapshot.objects.count(), 0)

    def test_database_prevents_two_active_snapshots(self):
        first = self.load(self.workbook([{'L': 10, 'M': 10}]), 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ReceivablesSourceSnapshot.objects.create(sha256='a' * 64, file_name='other.xlsx',
                                                     sheet_name='External Invoice ', last_row=6,
                                                     row_count=1, is_active=True)
        self.assertEqual(get_active_receivables_source()._receivables_snapshot.pk, first['snapshot_id'])
