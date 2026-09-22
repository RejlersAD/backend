"""Authorised invoice cohorts, honest gaps, and explicitly estimated forecasts."""
from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from importlib import import_module
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.backends.sqlite3.base import DatabaseWrapper
from django.db.migrations.state import ProjectState
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

from apps.finance.services.invoice_performance import _load_workbook_snapshot, aggregate_invoice_days, build_invoice_performance
from apps.finance import tests_receivables_dashboard as receivables_tests
from apps.finance.models import ExecutiveFinancePeriod
from apps.invoice_tracker.models import CustomerInvoice


AS_OF = date(2026, 9, 21)
NOW = datetime(2026, 9, 21, 9, tzinfo=dt_timezone.utc)


class PrivateInvoiceSnapshotTests(SimpleTestCase):
    """Private operator configuration uses synthetic artifacts only."""

    def setUp(self):
        directory = TemporaryDirectory(prefix='executive-private-snapshot-')
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.private_path = root / 'private.json'
        self.local_path = root / 'local.json'
        workbook_path = root / 'summary.json'
        self.snapshot = {'schema_version': '1.0', 'source': {'sha256': 'synthetic-test-digest'},
                         'days': aggregate_invoice_days([{
                             'currency': 'AED', 'category': 'external', 'payment_status': 'paid',
                             'invoice_date': AS_OF, 'invoice_amount': Decimal('100'),
                             'actual_payment_received': Decimal('40'),
                         }])}
        self.private_path.write_text(json.dumps(self.snapshot), encoding='utf-8')
        self.local_path.write_text(json.dumps(self.snapshot), encoding='utf-8')
        workbook_path.write_text(json.dumps({'source': self.snapshot['source'], 'invoice_count': 1}), encoding='utf-8')
        for mock in [patch('apps.finance.services.invoice_performance.SNAPSHOT_PATH', self.local_path),
                     patch('apps.finance.services.workbook_summary.SNAPSHOT_PATH', workbook_path),
                     patch.dict(os.environ, {'EXECUTIVE_INVOICE_PERFORMANCE_SNAPSHOT_PATH': ''})]:
            mock.start()
            self.addCleanup(mock.stop)

    def test_private_path_takes_precedence_over_default_local_artifact(self):
        self.local_path.write_text('invalid local artifact', encoding='utf-8')
        with patch.dict(os.environ, {'EXECUTIVE_INVOICE_PERFORMANCE_SNAPSHOT_PATH': str(self.private_path)}):
            self.assertEqual(_load_workbook_snapshot(), self.snapshot)

    def test_missing_or_mismatched_private_artifact_does_not_use_stale_local_artifact(self):
        with patch.dict(os.environ, {'EXECUTIVE_INVOICE_PERFORMANCE_SNAPSHOT_PATH': str(self.private_path)}):
            self.snapshot['source']['sha256'] = 'different-synthetic-source'
            self.private_path.write_text(json.dumps(self.snapshot), encoding='utf-8')
            self.assertIsNone(_load_workbook_snapshot())
            self.private_path.unlink()
            self.assertIsNone(_load_workbook_snapshot())

    def test_default_ignored_local_artifact_remains_supported(self):
        self.assertEqual(_load_workbook_snapshot(), self.snapshot)


class InvoicePerformanceTests(TestCase):
    grant = receivables_tests.ReceivablesDashboardTests.grant
    deny = receivables_tests.ReceivablesDashboardTests.deny
    ar = receivables_tests.ReceivablesDashboardTests.ar

    def setUp(self):
        receivables_tests.ReceivablesDashboardTests.setUp(self)
        workbook = patch('apps.finance.services.invoice_performance._load_workbook_snapshot', return_value=None, create=True)
        workbook.start()
        self.addCleanup(workbook.stop)

    def invoice(self, name, amount='100', receipt='0', *, when=AS_OF,
                currency='AED', company='Acme', status='pending', category='external', **fields):
        return self.ar(
            name, balance='999999', currency=currency, status=status,
            invoice_date=when, company=company, category=category,
            invoice_amount=None if amount is None else Decimal(amount),
            actual_payment_received=None if receipt is None else Decimal(receipt),
            invoice_amount_aed=Decimal('777777'), grand_total=Decimal('888888'), **fields,
        )

    def report(self, **kwargs):
        return build_invoice_performance(self.user, as_of=kwargs.pop('as_of', AS_OF), **kwargs)

    def approved_period(self, month=date(2026, 9, 1), **fields):
        defaults = {'month': month, 'currency': 'AED', 'status': 'approved',
                    'source_reference': 'Verified Finance period', 'approved_by': self.user,
                    'approved_at': NOW, 'budget_invoiced': Decimal('100')}
        return ExecutiveFinancePeriod.objects.create(**{**defaults, **fields})

    def assert_money(self, metric, amount, *, count=None, known=None, missing=0):
        self.assertEqual(metric['amount'], amount)
        self.assertEqual(metric['known_amount'], amount if known is None else known)
        self.assertEqual(metric['missing_count'], missing)
        self.assertEqual(metric['partial'], bool(missing))
        if count is not None:
            self.assertEqual(metric['count'], count)

    @staticmethod
    def invoice_queries(context):
        return [row['sql'] for row in context.captured_queries
                if CustomerInvoice._meta.db_table.lower() in row['sql'].lower()]

    def test_outgoing_read_is_required_before_any_invoice_read(self):
        self.grant('finance_overview')
        self.invoice('SECRET', company='Secret company')
        with CaptureQueriesContext(connection) as queries:
            result = self.report()
        self.assertEqual(self.invoice_queries(queries), [])
        self.assertEqual(result['status'], 'restricted')
        self.assertIsNone(result['kpis']['ytd_invoiced']['amount'])
        self.assertIsNone(result['kpis']['ytd_invoiced']['known_amount'])
        self.assertNotIn('Secret', str(result))

        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.report()['status'], 'restricted')
        self.assertEqual(self.invoice_queries(queries), [])

    def test_anonymous_and_explicitly_denied_superuser_do_not_read_invoice_source(self):
        self.grant('finance_outgoing')
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        self.deny('finance_outgoing')
        for user in [AnonymousUser(), self.user]:
            with self.subTest(user=str(user)), CaptureQueriesContext(connection) as queries:
                result = build_invoice_performance(user, as_of=AS_OF)
            self.assertEqual(result['status'], 'restricted')
            self.assertEqual(self.invoice_queries(queries), [])

    def test_source_scope_is_complete_and_not_limited_to_a_register_page(self):
        self.grant('finance_outgoing')
        for index in range(25):
            self.invoice(f'ALL-{index}', amount=str(index + 1), receipt='1')
        result = self.report()
        self.assert_money(result['kpis']['ytd_invoiced'], '325.00', count=25)
        self.assert_money(result['kpis']['ytd_received'], '25.00', count=25)
        self.assert_money(result['kpis']['ytd_outstanding'], '300.00', count=25)
        self.assertEqual(result['coverage']['eligible_invoice_count'], 25)

    def test_original_currency_and_company_are_normalized_without_home_currency_fallback(self):
        self.grant('finance_outgoing')
        self.invoice('AED-TRIM', amount='100', receipt='40', currency='aed ', company=' Acme ')
        self.invoice('AED-OTHER', amount='900', receipt='200', company='Other')
        self.invoice('USD-SAME', amount='600', receipt='50', currency='USD')
        aed = self.report(currency=' aed ', company=' Acme ')
        self.assertEqual(aed['currency'], 'AED')
        self.assert_money(aed['kpis']['ytd_invoiced'], '100.00', count=1)
        self.assert_money(aed['kpis']['ytd_received'], '40.00', count=1)
        self.assert_money(aed['kpis']['ytd_outstanding'], '60.00', count=1)
        usd = self.report(currency='usd', company='Acme')
        self.assertEqual(usd['currency'], 'USD')
        self.assert_money(usd['kpis']['ytd_invoiced'], '600.00', count=1)
        self.assert_money(usd['kpis']['ytd_outstanding'], '550.00', count=1)

    def test_paid_invoices_contribute_but_internal_cancelled_and_credit_notes_do_not(self):
        self.grant('finance_outgoing', 'finance_overview')
        self.invoice('PAID', amount='100', receipt='100', status='paid')
        self.invoice('PARTIAL', amount='200', receipt='50', status='partial')
        self.invoice('CANCELLED', amount='1000', receipt='100', status='cancelled')
        self.invoice('CREDIT', amount='2000', receipt='200', status='credit_note')
        self.invoice('INTERNAL', amount='3000', receipt='300', category='internal')
        result = self.report()
        self.assert_money(result['kpis']['ytd_invoiced'], '300.00', count=2)
        self.assert_money(result['kpis']['ytd_received'], '150.00', count=2)
        self.assert_money(result['kpis']['ytd_outstanding'], '150.00', count=2)
        for name in ['internal', 'cancelled', 'credit_note']:
            self.assertEqual(result['coverage'][f'excluded_{name}_count'], 1)
        self.assertEqual(result['kpis']['collection_rate']['value'], 50)
        self.assertEqual(result['operating_margin']['status'], 'unavailable')

    def test_unknown_amount_and_receipt_are_preserved_independently_from_recorded_zero(self):
        self.grant('finance_outgoing')
        self.invoice('KNOWN', amount='100', receipt='40')
        self.invoice('UNKNOWN-RECEIPT', amount='200', receipt=None)
        self.invoice('UNKNOWN-INVOICE', amount=None, receipt='10')
        self.invoice('ZERO', amount='0', receipt='0')
        result = self.report()
        self.assertEqual(result['status'], 'partial')
        self.assert_money(result['kpis']['ytd_invoiced'], None, count=4, known='300.00', missing=1)
        self.assert_money(result['kpis']['ytd_received'], None, count=4, known='50.00', missing=1)
        self.assert_money(result['kpis']['ytd_outstanding'], None, count=4, known='60.00', missing=2)
        self.assertIsNone(result['kpis']['collection_rate']['value'])
        self.assertEqual(result['coverage']['missing_invoice_amount_count'], 1)
        self.assertEqual(result['coverage']['missing_receipt_count'], 1)

    def test_overpayments_do_not_cancel_other_invoice_balances_or_clamp_collection_ratio(self):
        self.grant('finance_outgoing')
        self.invoice('OVERPAYMENT', amount='100', receipt='200')
        self.invoice('OPEN', amount='50', receipt='0')
        result = self.report()
        self.assert_money(result['kpis']['ytd_outstanding'], '50.00', count=2)
        self.assert_money(result['kpis']['ytd_received'], '200.00', count=2)
        self.assertAlmostEqual(result['kpis']['collection_rate']['value'], 200 / 150 * 100, delta=.01)
        self.assertEqual(result['coverage']['overpaid_invoice_count'], 1)

    def test_calendar_ytd_and_rolling_months_use_invoice_dates_and_disclose_excluded_dates(self):
        self.grant('finance_outgoing')
        rows = [
            ('OUTSIDE', '700', '0', date(2025, 9, 30)),
            ('LAST-YEAR', '100', '20', date(2025, 10, 1)),
            ('JANUARY', '200', '50', date(2026, 1, 1)),
            ('AUGUST', '300', '100', date(2026, 8, 31)),
            ('SEPTEMBER', '400', '300', date(2026, 9, 1)),
            ('TODAY', '500', '100', AS_OF),
            ('FUTURE-DAY', '900', '400', date(2026, 9, 22)),
            ('FUTURE-MONTH', '800', '500', date(2026, 10, 1)),
            ('UNKNOWN-DATE', '600', '50', None),
        ]
        for name, amount, receipt, when in rows:
            self.invoice(name, amount, receipt, when=when)
        result = self.report()
        self.assertEqual(result['period_basis'], 'calendar_year')
        self.assertEqual(result['as_of_date'], AS_OF.isoformat())
        self.assert_money(result['kpis']['monthly_invoiced'], '900.00', count=2)
        self.assert_money(result['kpis']['ytd_invoiced'], '1400.00', count=4)
        self.assert_money(result['kpis']['ytd_received'], '550.00', count=4)
        self.assert_money(result['kpis']['ytd_outstanding'], '850.00', count=4)
        self.assertEqual(len(result['monthly']), 12)
        self.assertEqual(result['monthly'][0]['month'], '2025-10')
        self.assertEqual(result['monthly'][-1]['month'], '2026-09')
        self.assert_money(result['monthly'][0]['invoiced'], '100.00', count=1)
        self.assert_money(result['monthly'][-1]['invoiced'], '900.00', count=2)
        self.assertEqual(result['coverage']['missing_invoice_date_count'], 1)
        self.assertEqual(result['coverage']['future_invoice_date_count'], 2)
        self.assertEqual(result['coverage']['outside_window_count'], 1)
        self.assertEqual(result['forecast']['status'], 'unavailable')

    def test_receipts_follow_invoice_cohorts_rather_than_payment_dates(self):
        self.grant('finance_outgoing')
        self.invoice('JUNE-PAID-SEPTEMBER', '100', '100', when=date(2026, 6, 3),
                     status='paid', payment_date=date(2026, 9, 18))
        self.invoice('SEPTEMBER-PARTIAL', '200', '50', payment_date=date(2026, 9, 20))
        months = {row['month']: row for row in self.report()['monthly']}
        self.assert_money(months['2026-06']['received'], '100.00', count=1)
        self.assert_money(months['2026-09']['received'], '50.00', count=1)

    def test_absent_source_is_unknown_but_empty_current_month_with_history_is_zero(self):
        self.grant('finance_outgoing')
        absent = self.report()
        self.assertEqual(absent['status'], 'unavailable')
        self.assertIsNone(absent['kpis']['ytd_invoiced']['amount'])
        self.assertIsNone(absent['kpis']['monthly_invoiced']['amount'])
        self.assertEqual(absent['forecast']['status'], 'unavailable')
        self.invoice('HISTORY', '100', '100', when=date(2026, 1, 1))
        result = self.report()
        self.assert_money(result['kpis']['monthly_invoiced'], '0.00', count=0)
        self.assert_money(result['monthly'][-1]['received'], '0.00', count=0)
        self.assert_money(result['kpis']['ytd_outstanding'], '0.00', count=1)
        self.assertEqual(result['forecast']['status'], 'unavailable', 'An all-zero baseline does not establish forward demand')

    def test_estimated_forecast_uses_three_complete_months_including_empty_months(self):
        self.grant('finance_outgoing')
        self.invoice('PRIOR-HISTORY', '100', '0', when=date(2026, 5, 31))
        self.invoice('JUNE', '100', None, when=date(2026, 6, 15))
        self.invoice('AUGUST', '500', '200', when=date(2026, 8, 15))
        self.invoice('INCOMPLETE-CURRENT-MONTH', '9900', '0')
        forecast = self.report()['forecast']
        self.assertEqual(forecast['status'], 'estimated')
        self.assertTrue(forecast['method'])
        self.assertEqual(forecast['basis_months'], ['2026-06', '2026-07', '2026-08'])
        self.assertEqual(len(forecast['rows']), 12)
        self.assertEqual(forecast['rows'][0]['month'], '2026-10')
        self.assertEqual(forecast['rows'][-1]['month'], '2027-09')
        self.assertTrue(all(Decimal(str(row['value'])) == Decimal('200') for row in forecast['rows']))

    def test_forecast_requires_history_and_complete_baseline_invoice_amounts(self):
        self.grant('finance_outgoing')
        self.invoice('NEW-SOURCE', '300', '0', when=date(2026, 8, 1))
        self.assertEqual(self.report()['forecast']['status'], 'unavailable')
        self.invoice('PRIOR-HISTORY', '100', '0', when=date(2026, 5, 31))
        incomplete = self.invoice('JUNE-MISSING', None, '0', when=date(2026, 6, 1))
        self.assertEqual(self.report()['forecast']['status'], 'unavailable')
        incomplete.invoice_amount = Decimal('300')
        incomplete.save(_skip_recompute=True, update_fields=['invoice_amount'])
        self.assertEqual(self.report()['forecast']['status'], 'estimated')
        self.invoice('UNALLOCATED-DATE', '100', '0', when=None)
        self.assertEqual(self.report()['forecast']['status'], 'unavailable')

    def test_source_failure_withholds_values_without_leaking_internal_error(self):
        self.grant('finance_outgoing')
        with patch('apps.finance.services.invoice_performance._read_invoice_rows', side_effect=RuntimeError('private connection secret')):
            result = self.report()
        self.assertEqual(result['status'], 'error')
        self.assertIsNone(result['kpis']['ytd_invoiced']['amount'])
        self.assertEqual(result['forecast']['status'], 'unavailable')
        self.assertNotIn('private connection secret', str(result))

    def test_reporting_is_read_only_and_does_not_recompute_persisted_invoice_fields(self):
        self.grant('finance_outgoing')
        item = self.invoice('READ-ONLY', '100', '40', status='paid')
        original = CustomerInvoice.objects.filter(pk=item.pk).values().get()
        with CaptureQueriesContext(connection) as queries:
            self.report()
        writes = [row['sql'] for row in queries.captured_queries
                  if row['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE', 'REPLACE', 'ALTER'))]
        self.assertEqual(writes, [])
        self.assertEqual(CustomerInvoice.objects.filter(pk=item.pk).values().get(), original)

    def test_verified_daily_aggregate_matches_full_invoice_scan_without_identity_data(self):
        self.grant('finance_outgoing')
        self.invoice('PRIVATE-IDENTITY-OVERPAID', '100', '200')
        self.invoice('PRIVATE-IDENTITY-OPEN', '50', '0')
        self.invoice('PRIVATE-IDENTITY-UNKNOWN', '200', None)
        self.invoice('OTHER-CURRENCY', '500', '100', currency='USD')
        self.invoice('PRIOR-WINDOW', '700', '0', when=date(2025, 9, 1))
        self.invoice('PRIOR-HISTORY', '100', '0', when=date(2026, 5, 31))
        self.invoice('BASELINE', '600', '200', when=date(2026, 6, 15))
        self.invoice('FUTURE-DAY', '900', '100', when=date(2026, 9, 22))
        self.invoice('NO-DATE', '500', '100', when=None)
        self.invoice('CANCELLED', '9000', '8000', status='cancelled')
        self.invoice('CREDIT', '9000', '8000', status='credit_note')
        self.invoice('INTERNAL', '9000', '8000', category='internal')
        direct = self.report()
        days = aggregate_invoice_days(list(CustomerInvoice.objects.values()))
        self.assertNotIn('PRIVATE-IDENTITY', str(days))
        self.assertTrue(all('company' not in row and 'invoice_number' not in row for row in days))
        snapshot = {'source': {'sha256': 'test', 'snapshot_at': NOW.isoformat()}, 'days': days}
        with patch('apps.finance.services.invoice_performance._load_workbook_snapshot', return_value=snapshot), \
                patch('apps.finance.services.invoice_performance._read_invoice_rows', side_effect=AssertionError('Must use the verified aggregate')):
            aggregate = self.report()
        for key in ['status', 'kpis', 'monthly', 'coverage', 'forecast']:
            self.assertEqual(aggregate[key], direct[key], key)
        self.assertEqual(aggregate['source']['kind'], 'finance_workbook')
        self.assertIsNone(aggregate['source_updated_at'], 'Snapshot generation is not an invoice source update')
        self.assertEqual(aggregate['source']['snapshot_at'], NOW.isoformat())
        self.assertEqual(aggregate['source']['timestamp_basis'], 'snapshot_generation_time_not_invoice_update')

    def test_approved_budget_forecast_and_margin_use_their_distinct_authorised_bases(self):
        self.grant('finance_outgoing', 'finance_overview')
        self.invoice('INVOICED', '1000', '500', when=date(2026, 9, 1))
        self.approved_period(budget_invoiced=Decimal('1200'), recognised_revenue=Decimal('200'),
                             operating_costs=Decimal('50'), actual_through=AS_OF)
        self.approved_period(month=date(2026, 10, 1), forecast_invoiced=Decimal('1800'))
        result = self.report()
        self.assert_money(result['kpis']['ytd_invoiced'], '1000.00', count=1)
        self.assertEqual(result['monthly'][-1]['budget'], '1200.00')
        self.assertEqual(result['monthly'][-1]['operating_margin'], 75)
        self.assertEqual(result['operating_margin']['status'], 'approved')
        self.assertIsNone(result['operating_margin']['ytd']['value'], 'One month cannot establish YTD actuals')
        self.assertEqual(result['forecast']['status'], 'approved')
        self.assertEqual(result['forecast']['rows'][0], {'month': '2026-10', 'value': '1800.00'})
        self.assertTrue(result['forecast']['partial'])
        self.assertTrue(all(row['value'] is None for row in result['forecast']['rows'][1:]))
        historical = self.report(as_of=date(2026, 9, 15))
        self.assertIsNone(historical['monthly'][-1]['operating_margin'], 'Actuals after the requested cutoff remain withheld')

    def test_finance_inputs_need_separate_permission_and_are_withheld_for_one_customer(self):
        self.grant('finance_outgoing')
        self.invoice('INVOICED', '100', '40')
        self.invoice('HISTORY', '100', '0', when=date(2026, 5, 31))
        self.invoice('BASELINE', '100', '0', when=date(2026, 6, 1))
        self.approved_period(budget_invoiced=Decimal('6000'), recognised_revenue=Decimal('200'),
                             operating_costs=Decimal('50'), actual_through=AS_OF)
        for company in ['', 'Acme']:
            if company:
                self.grant('finance_overview')
            with self.subTest(company=company), CaptureQueriesContext(connection) as queries:
                result = self.report(company=company)
            self.assertFalse(any(ExecutiveFinancePeriod._meta.db_table in row['sql'] for row in queries.captured_queries))
            self.assertEqual(result['budget']['status'], 'unavailable' if company else 'restricted')
            self.assertIsNone(result['monthly'][-1]['budget'])
            self.assertIsNone(result['monthly'][-1]['operating_margin'])
            self.assertEqual(result['forecast']['status'], 'unavailable' if company else 'estimated')
            self.assertNotIn('6000', str(result))

    def test_draft_or_different_currency_plans_do_not_become_approved_values(self):
        self.grant('finance_outgoing', 'finance_overview')
        self.invoice('INVOICED', '100', '40')
        self.approved_period(currency='USD', budget_invoiced=Decimal('5000'))
        self.approved_period(status='draft', budget_invoiced=Decimal('6000'))
        result = self.report()
        self.assertEqual(result['budget']['status'], 'unavailable')
        self.assertIsNone(result['monthly'][-1]['budget'])

    def test_imported_future_approval_cannot_bypass_service_read_validation(self):
        self.grant('finance_outgoing', 'finance_overview')
        self.invoice('INVOICED', '100', '40')
        period = self.approved_period(recognised_revenue=Decimal('200'),
                                      operating_costs=Decimal('50'), actual_through=AS_OF)
        ExecutiveFinancePeriod.objects.filter(pk=period.pk).update(approved_at=NOW + timedelta(days=1))
        with patch('apps.finance.services.invoice_performance.timezone.now', return_value=NOW):
            result = self.report()
        self.assertEqual(result['budget']['status'], 'unavailable')
        self.assertEqual(result['operating_margin']['status'], 'unavailable')
        self.assertIsNone(result['monthly'][-1]['budget'])
        self.assertIsNone(result['monthly'][-1]['operating_margin'])

    def test_imported_wrong_month_actuals_are_withheld_even_with_approval_evidence(self):
        self.grant('finance_outgoing', 'finance_overview')
        self.invoice('INVOICED', '100', '40')
        period = self.approved_period(recognised_revenue=Decimal('200'),
                                      operating_costs=Decimal('50'), actual_through=AS_OF)
        ExecutiveFinancePeriod.objects.filter(pk=period.pk).update(actual_through=date(2026, 8, 31))
        result = self.report()
        self.assertEqual(result['budget']['status'], 'approved')
        self.assertEqual(result['operating_margin']['status'], 'unavailable')
        self.assertIsNone(result['monthly'][-1]['operating_margin'])
        self.assertIsNone(result['monthly'][-1]['ytd_operating_margin'])

    def test_ytd_margin_weights_matched_amounts_and_requires_complete_period_coverage(self):
        self.grant('finance_outgoing', 'finance_overview')
        self.invoice('FEBRUARY-INVOICE', '9999', '0', when=date(2026, 2, 1))
        january = self.approved_period(month=date(2026, 1, 1), recognised_revenue=Decimal('100'),
                                       operating_costs=Decimal('90'), actual_through=date(2026, 1, 31))
        self.approved_period(month=date(2026, 2, 1), recognised_revenue=Decimal('900'),
                             operating_costs=Decimal('450'), actual_through=date(2026, 2, 15))
        result = self.report(as_of=date(2026, 2, 15))
        self.assertEqual(result['monthly'][-1]['operating_margin'], 50)
        self.assertEqual(result['monthly'][-1]['ytd_operating_margin'], 46)
        self.assertEqual(result['operating_margin']['ytd']['value'], 46)
        january.actual_through = date(2026, 1, 30)
        january.save(update_fields=['actual_through'])
        incomplete = self.report(as_of=date(2026, 2, 15))
        self.assertIsNone(incomplete['monthly'][-1]['ytd_operating_margin'])
        self.assertEqual(incomplete['operating_margin']['ytd']['status'], 'unavailable')


class ExecutiveFinancePeriodValidationTests(TestCase):
    """Only evidenced approvals and matched actual periods can be published."""

    def setUp(self):
        receivables_tests.ReceivablesDashboardTests.setUp(self)
        clock = patch('apps.finance.reporting_models.timezone.now', return_value=NOW)
        localdate = patch('apps.finance.reporting_models.timezone.localdate', return_value=AS_OF)
        clock.start()
        localdate.start()
        self.addCleanup(clock.stop)
        self.addCleanup(localdate.stop)

    def period(self, **fields):
        defaults = {
            'month': date(2026, 9, 1), 'currency': 'AED', 'budget_invoiced': Decimal('100'),
            'status': 'approved', 'source_reference': 'Finance approval FIN-2026-09',
            'approved_by': self.user, 'approved_at': NOW,
        }
        return ExecutiveFinancePeriod(**{**defaults, **fields})

    def assert_invalid(self, instance, *fields):
        with self.assertRaises(ValidationError) as error:
            instance.full_clean()
        for field in fields:
            self.assertIn(field, error.exception.message_dict)

    def test_period_requires_first_of_month_and_normalizes_valid_currency_on_save(self):
        self.assert_invalid(self.period(month=date(2026, 9, 2)), 'month')
        self.assert_invalid(self.period(currency='AE7'), 'currency')
        period = self.period(currency=' usd ')
        period.save()
        period.refresh_from_db()
        self.assertEqual(period.currency, 'USD')
        self.assert_invalid(self.period(currency='USD'), '__all__')

    def test_approval_requires_source_approver_timestamp_and_at_least_one_amount(self):
        self.assert_invalid(self.period(source_reference='  ', approved_by=None, approved_at=None),
                            'source_reference', 'approved_by', 'approved_at')
        self.assert_invalid(self.period(approved_at=NOW + timedelta(seconds=1)), 'approved_at')
        self.assert_invalid(self.period(budget_invoiced=None), 'status')
        self.period(status='draft', budget_invoiced=None, source_reference='', approved_by=None,
                    approved_at=None).full_clean()

    def test_actual_revenue_and_costs_must_be_supplied_together(self):
        self.assert_invalid(self.period(recognised_revenue=Decimal('100')), 'operating_costs')
        self.assert_invalid(self.period(operating_costs=Decimal('100')), 'operating_costs')
        self.assert_invalid(self.period(actual_through=AS_OF), 'actual_through')

    def test_actual_cutoff_must_be_present_in_same_month_and_not_future(self):
        values = {'recognised_revenue': Decimal('100'), 'operating_costs': Decimal('60')}
        self.assert_invalid(self.period(**values), 'actual_through')
        self.assert_invalid(self.period(**values, actual_through=date(2026, 8, 31)), 'actual_through')
        self.assert_invalid(self.period(**values, actual_through=AS_OF + timedelta(days=1)), 'actual_through')
        self.period(**values, actual_through=AS_OF).full_clean()

    def test_zero_actuals_are_valid_but_negative_amounts_are_not(self):
        self.period(recognised_revenue=Decimal('0'), operating_costs=Decimal('0'),
                    actual_through=AS_OF, budget_invoiced=Decimal('0')).full_clean()
        for field in ['budget_invoiced', 'forecast_invoiced', 'recognised_revenue', 'operating_costs']:
            fields = {'recognised_revenue': Decimal('100'), 'operating_costs': Decimal('60'),
                      'actual_through': AS_OF, field: Decimal('-1')}
            with self.subTest(field=field):
                self.assert_invalid(self.period(**fields), field)

    def test_approved_future_plans_do_not_require_future_actuals(self):
        self.period(month=date(2026, 10, 1), forecast_invoiced=Decimal('1000'),
                    actual_through=None).full_clean()

    def test_migration_round_trip_in_private_sqlite_matches_current_model_state(self):
        migration = import_module('apps.finance.migrations.0011_executive_finance_period').Migration(
            '0011_executive_finance_period', 'finance')
        current = ProjectState.from_apps(apps)
        before = current.clone()
        before.remove_model('finance', 'executivefinanceperiod')
        after = before.clone()
        isolated = DatabaseWrapper({**connection.settings_dict, 'NAME': ':memory:'}, alias='default')
        table = ExecutiveFinancePeriod._meta.db_table
        try:
            with isolated.schema_editor() as editor:
                for operation in migration.operations:
                    previous = after.clone()
                    operation.state_forwards('finance', after)
                    operation.database_forwards('finance', editor, previous, after)
            self.assertEqual(after.models['finance', 'executivefinanceperiod'],
                             current.models['finance', 'executivefinanceperiod'])
            self.assertIn(table, isolated.introspection.table_names())
            with isolated.cursor() as cursor:
                constraints = isolated.introspection.get_constraints(cursor, table)
            self.assertTrue(any(row['unique'] and row['columns'] == ['month', 'currency']
                                for row in constraints.values()))
            with isolated.schema_editor() as editor:
                for operation in reversed(migration.operations):
                    operation.database_backwards('finance', editor, after, before)
            self.assertNotIn(table, isolated.introspection.table_names())
        finally:
            isolated.close()
