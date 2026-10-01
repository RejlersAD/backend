"""Executive readers follow immutable Finance publications, never stale copies."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.dashboard.executive import _build_finance
from apps.dashboard.financial_performance import build_financial_performance
from apps.finance.models import ReceivablesSourceRow, ReceivablesSourceSnapshot
from apps.finance.services import receivables_dashboard
from apps.finance.services.invoice_performance import build_invoice_performance
from apps.finance.tests_receivables_dashboard import ReceivablesDashboardTests


TODAY = date(2026, 10, 1)
NOW = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)


class ExecutiveSourcePipelineTests(TestCase):
    setUp = ReceivablesDashboardTests.setUp
    grant = ReceivablesDashboardTests.grant
    deny = ReceivablesDashboardTests.deny
    ar = ReceivablesDashboardTests.ar

    def snapshot(self, *, active=True, digest='a'):
        return ReceivablesSourceSnapshot.objects.create(
            sha256=digest * 64, file_name='Synthetic Finance.xlsx', sheet_name='External Invoice ',
            first_row=6, last_row=6, row_count=1, is_active=active, imported_at=NOW)

    def row(self, snapshot, number=6, **values):
        return ReceivablesSourceRow.objects.create(**{
            'snapshot': snapshot, 'row_number': number, 'invoice_number': 'SAME-NUMBER',
            'company': 'Acme', 'currency': 'AED', 'currency_status': 'recorded',
            'invoice_amount': Decimal('100'), 'invoice_amount_aed': Decimal('900'),
            'actual_payment_received': Decimal('20'), 'actual_payment_currency': 'AED',
            'balance_to_be_received': Decimal('7'), 'balance_currency': 'AED',
            'payment_status': 'pending', 'invoice_date': TODAY,
            'due_date': TODAY - timedelta(days=61), 'updated_at': NOW, **values,
        })

    def report(self, **values):
        return build_invoice_performance(self.user, as_of=TODAY, **values)

    def activate(self, snapshot):
        ReceivablesSourceSnapshot.objects.filter(is_active=True).update(is_active=False)
        ReceivablesSourceSnapshot.objects.filter(pk=snapshot.pk).update(is_active=True)

    def test_active_publication_beats_stale_json_and_register_without_writes(self):
        self.grant('finance_outgoing')
        source = self.snapshot()
        self.row(source)
        self.row(source, 7, actual_payment_received=None)
        self.ar('OLD', '999999')
        with patch('apps.finance.services.invoice_performance._load_workbook_snapshot') as legacy, \
                CaptureQueriesContext(connection) as queries:
            report = self.report()
        legacy.assert_not_called()
        self.assertEqual(report['source']['snapshot_id'], source.pk)
        self.assertEqual(report['source']['kind'], 'finance_source_snapshot')
        self.assertEqual(report['source']['route'], '/finance')
        self.assertEqual(report['source_updated_at'], NOW.isoformat())
        self.assertEqual(report['kpis']['ytd_invoiced']['amount'], '200.00')
        self.assertEqual(report['kpis']['ytd_received']['known_amount'], '20.00')
        self.assertIsNone(report['kpis']['ytd_received']['amount'])
        self.assertEqual(report['coverage']['source_row_count'], 2)
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for q in queries))

    def test_company_currency_and_receipt_currency_are_independent(self):
        self.grant('finance_outgoing')
        source = self.snapshot()
        self.row(source, actual_payment_currency='USD')
        self.row(source, 7, company='Other', invoice_amount=Decimal('500'))
        self.row(source, 8, currency='USD', invoice_amount=Decimal('50'),
                 actual_payment_currency='USD', actual_payment_received=Decimal('10'))
        report = self.report(company=' Acme ')
        self.assertEqual(report['kpis']['ytd_invoiced']['amount'], '100.00')
        self.assertIsNone(report['kpis']['ytd_received']['amount'])
        self.assertIsNone(report['kpis']['ytd_outstanding']['amount'])
        self.assertEqual(self.report(currency='USD', company='Acme')['kpis']['ytd_invoiced']['amount'], '50.00')
        with patch('apps.finance.services.invoice_performance._read_invoice_rows') as old:
            empty = self.report(company='No match')
        old.assert_not_called()
        self.assertEqual(empty['status'], 'unavailable')
        self.assertEqual(empty['source']['snapshot_id'], source.pk)

    def test_paid_and_exclusion_rules_preserve_source_rows_and_unknown_dates(self):
        self.grant('finance_outgoing')
        source = self.snapshot()
        self.row(source, payment_status='paid')
        self.row(source, 7, payment_status='cancelled')
        self.row(source, 8, payment_status='credit_note')
        self.row(source, 9, invoice_date=None)
        self.row(source, 10, invoice_date=TODAY + timedelta(days=1))
        report = self.report()
        self.assertEqual(report['kpis']['ytd_invoiced']['amount'], '100.00')
        self.assertEqual(report['coverage']['excluded_cancelled_count'], 1)
        self.assertEqual(report['coverage']['excluded_credit_note_count'], 1)
        self.assertEqual(report['coverage']['missing_invoice_date_count'], 1)
        self.assertEqual(report['coverage']['future_invoice_date_count'], 1)

    def test_publication_switch_is_visible_without_operational_reimport(self):
        self.grant('finance_outgoing')
        first = self.snapshot()
        second = self.snapshot(active=False, digest='b')
        self.row(first)
        self.row(second, invoice_amount=Decimal('250'))
        self.assertEqual(self.report()['kpis']['ytd_invoiced']['amount'], '100.00')
        self.activate(second)
        result = self.report()
        self.assertEqual(result['source']['snapshot_id'], second.pk)
        self.assertEqual(result['kpis']['ytd_invoiced']['amount'], '250.00')

    def test_dashboard_keeps_invoice_performance_on_its_pinned_publication(self):
        self.grant('finance_outgoing')
        first = self.snapshot()
        second = self.snapshot(active=False, digest='b')
        self.row(first)
        self.row(second, invoice_amount=Decimal('250'))
        original = receivables_dashboard._summary

        def publish_after_summary(*args, **kwargs):
            result = original(*args, **kwargs)
            self.activate(second)
            return result

        with patch.object(receivables_dashboard, '_summary', side_effect=publish_after_summary):
            result = receivables_dashboard.build_receivables_dashboard(self.user, as_of=TODAY)
        self.assertEqual(result['sources']['receivables']['snapshot_id'], first.pk)
        self.assertEqual(result['invoice_performance']['source']['snapshot_id'], first.pk)
        self.assertEqual(result['invoice_performance']['kpis']['ytd_invoiced']['amount'], '100.00')
        self.assertEqual(self.report()['source']['snapshot_id'], second.pk)

    def test_explicit_deny_never_queries_or_discloses_snapshot(self):
        self.grant('finance_outgoing')
        source = self.snapshot()
        self.row(source)
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        self.deny('finance_outgoing')
        with CaptureQueriesContext(connection) as queries:
            report = self.report()
        self.assertEqual(report['status'], 'restricted')
        self.assertNotIn('Synthetic', str(report))
        self.assertFalse(any('receivablessource' in q['sql'].lower() for q in queries))

    def test_source_failure_never_falls_back_to_stale_data(self):
        self.grant('finance_outgoing')
        self.ar('OLD', '999999')
        with patch('apps.finance.services.receivables_source.get_active_receivables_source', side_effect=RuntimeError('private error')), \
                patch('apps.finance.services.invoice_performance._read_invoice_rows') as old, \
                self.assertLogs('apps.finance.services.invoice_performance', level='ERROR'):
            result = self.report()
        old.assert_not_called()
        self.assertEqual(result['status'], 'error')
        self.assertNotIn('private error', str(result))

    def test_dashboard_source_failure_cannot_reread_and_mix_in_a_different_source(self):
        self.grant('finance_outgoing')
        with patch.object(receivables_dashboard, 'get_active_receivables_source', side_effect=RuntimeError('private')), \
                patch('apps.finance.services.invoice_performance._load_workbook_snapshot') as old, \
                self.assertLogs('apps.finance.services.receivables_dashboard', level='ERROR'):
            report = receivables_dashboard.build_receivables_dashboard(self.user, as_of=TODAY)
        old.assert_not_called()
        self.assertEqual(report['invoice_performance']['status'], 'error')

    def test_executive_aging_uses_finance_status_formula_and_cached_publication(self):
        self.grant('finance_outgoing')
        source = self.snapshot()
        self.row(source, payment_status='overdue', due_date=TODAY + timedelta(days=1))
        self.row(source, 7, payment_status='partial', balance_to_be_received=Decimal('7'))
        self.row(source, 8, payment_status='paid')
        self.row(source, 9, invoice_amount=Decimal('-5'))
        context = {'allowed_modules': {'finance_outgoing'}, 'generated_at': NOW}
        section = _build_finance(self.user, context)
        amounts = next(row for row in section['metrics'] if row['id'] == 'receivables')
        self.assertEqual(amounts['by_currency'], [{'currency': 'AED', 'amount': '102.00'}])
        self.assertEqual(next(row for row in section['metrics'] if row['id'] == 'overdue_receivables')['value'], 1)
        second = self.snapshot(active=False, digest='b')
        self.row(second, invoice_amount=Decimal('999'))
        self.activate(second)
        financial = build_financial_performance(self.user, context, section)
        aging = financial['working_capital']['aging']
        self.assertEqual(aging['source']['snapshot_id'], source.pk)
        self.assertEqual(aging['by_currency'][0]['total'], '102.00')
        self.assertEqual(section['source_timestamp_kind'], 'snapshot_publication')
        self.assertTrue(all(item['route'] == '/finance' for item in section['actions']))

    def test_executive_aging_withholds_only_currency_with_unknown_source_balance(self):
        self.grant('finance_outgoing')
        source = self.snapshot()
        self.row(source, payment_status='partial', balance_currency='USD')
        self.row(source, 7, currency='EUR', invoice_amount=Decimal('30'))
        context = {'allowed_modules': {'finance_outgoing'}, 'generated_at': NOW}
        section = _build_finance(self.user, context)
        row = next(item for item in section['metrics'] if item['id'] == 'receivables')
        self.assertEqual(row['status'], 'partial')
        self.assertEqual(row['by_currency'], [{'currency': 'EUR', 'amount': '30.00'}])
        self.assertEqual(row['incomplete_currencies'], ['AED'])
