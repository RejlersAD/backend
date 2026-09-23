from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.core.project_models import Project
from apps.portfolio.executive import build_revenue_dashboard
from apps.portfolio.models import PortfolioRow, PortfolioSnapshot, PortfolioSource
from apps.rbac.models import (Module, Organization, Permission, Role, RoleModule,
                              RolePermission, UserPermissionOverride, UserProfile, UserRole)
from apps.rbac.module_actions import ensure_module_actions


class RevenueDashboardTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('revenue-reader', email='revenue@example.test')
        org, _ = Organization.objects.get_or_create(code='revenue-test', defaults={'name': 'Revenue test'})
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'organization': org})
        self.profile.roles.clear()
        self.role = Role.objects.create(code='revenue-reader-test', name='Revenue reader', level=3)
        UserRole.objects.create(user_profile=self.profile, role=self.role)
        self.grant('project_control', 'read')

    def grant(self, code, action):
        module, _ = Module.objects.get_or_create(code=code, defaults={'name': code})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        RoleModule.objects.get_or_create(role=self.role, module=module)
        for permission in module.permissions.filter(action=action, is_active=True):
            RolePermission.objects.get_or_create(role=self.role, permission=permission)

    def full_access(self):
        self.grant('executive_dashboard', 'read')
        self.grant('project_control', 'update')
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])

    def source(self, reconciliation=None):
        source = PortfolioSource.objects.create()
        snapshot = PortfolioSnapshot.objects.create(
            source=source, sha256='a' * 64, parser_version='2', file_name='revenue.xlsx',
            reporting_date=timezone.localdate(), row_count=2, reconciliation=reconciliation or {})
        source.active_snapshot = snapshot
        source.save(update_fields=['active_snapshot'])
        return snapshot

    def row(self, snapshot, sub='P-1', **kwargs):
        period = snapshot.reporting_date.isoformat()[:7] + '-01'
        values = {'project_code': 'P', 'subproject_code': sub, 'source_row': 8, 'title': 'Workbook title',
                  'pm': 'PM1', 'business_unit': 'BU1', 'client': 'Client 1', 'currency': 'USD',
                  'include_without_pt': False, 'include_with_pt': False,
                  'period_revenue_aed': Decimal('10'), 'contract_value_aed': Decimal('100'),
                  'backlog_without_pt_aed': Decimal('100'), 'overclaim_aed': Decimal('10'),
                  'poc_pct': Decimal('60'), 'eddr_pct': Decimal('50'),
                  'ld_exposure_aed': Decimal('0'), 'prolongation_cost_aed': Decimal('0'),
                  'extra': {'executive': {'period': period, 'current_forecast_aed': '15',
                            'pm_forecast_aed': '12', 'forecast_variance_aed': '3',
                            'direct_cost_aed': '6', 'actual_manhours': '2'},
                            'history': [{'date': snapshot.reporting_date.isoformat(), 'period_revenue_aed': '10'}],
                            'forecasts': [{'period': period, 'revenue_aed': '15'}]}}
        values.update(kwargs)
        return PortfolioRow.objects.create(snapshot=snapshot, **values)

    @staticmethod
    def metric(data, key):
        return next(row for row in data['kpis'] if row['id'] == key)

    def test_workbook_is_primary_only_when_present_and_restrictions_do_not_fallback(self):
        self.assertFalse(build_revenue_dashboard(self.user)['enabled'])
        self.full_access()
        self.row(self.source())
        report = build_revenue_dashboard(self.user)
        self.assertTrue(report['enabled'])
        self.assertEqual(report['source']['file_name'], 'revenue.xlsx')
        self.assertEqual(report['currency'], 'AED')
        permission = Permission.objects.get(module__code='project_control', action='read')
        UserPermissionOverride.objects.create(user_profile=self.profile, permission=permission, allowed=False)
        report = build_revenue_dashboard(self.user)
        self.assertEqual(report['status'], 'restricted')
        self.assertTrue(report['enabled'])
        self.assertIsNone(report['source'])

    def test_full_source_uses_all_uploaded_identities_without_operational_record_or_flag_filter(self):
        self.full_access()
        snapshot = self.source()
        self.row(snapshot)
        self.row(snapshot, 'P-DELETED')
        self.row(snapshot, 'UNREGISTERED', project_code='UNKNOWN')
        Project.objects.create(code='P-DELETED', name='Deleted old record', is_deleted=True)
        report = build_revenue_dashboard(self.user, limit=1)
        self.assertTrue(report['scope']['full_source'])
        self.assertEqual(report['scope']['label'], 'Uploaded portfolio source')
        self.assertEqual(self.metric(report, 'total_revenue_actual')['value'], '30.00')
        self.assertEqual(self.metric(report, 'variance')['value'], '9.00')
        self.assertEqual(report['projects']['total_rows'], 3)
        self.assertEqual(report['projects']['returned_rows'], 1)
        self.assertTrue(report['projects']['truncated'])
        self.assertEqual(report['breakdowns']['business_unit'][0]['actual_revenue'], '30.00')
        self.assertEqual(report['projects']['rows'][0]['currency'], 'AED')
        self.assertEqual(report['projects']['rows'][0]['original_currency'], 'USD')

    def test_ordinary_scope_and_exact_denies_prevent_global_data_leakage(self):
        snapshot = self.source({'capacity': {'periods': [{'period': '2026-09-01', 'demand_manhours': '9999'}]},
                                'pm_kpi': {'entries': [{'pm': 'PM1', 'overall_ratio': '0.9'}]}})
        parent = Project.objects.create(code='P', name='Visible')
        Project.objects.create(code='P-HIDDEN', name='Hidden')
        self.row(snapshot)
        self.row(snapshot, 'P-HIDDEN', business_unit='SECRET')
        self.row(snapshot, 'UNMATCHED', project_code='OTHER', client='SECRET')
        with patch('apps.project_control.access.accessible_enterprise_projects', return_value=Project.objects.filter(pk=parent.pk)):
            report = build_revenue_dashboard(self.user)
        self.assertEqual(report['projects']['total_rows'], 1)
        self.assertEqual(report['filters']['business_units'], ['BU1'])
        self.assertEqual(report['capacity']['status'], 'restricted_scope')
        self.assertEqual(report['capacity']['rows'], [])
        self.assertIsNone(report['pm_performance']['rows'][0]['kpi'])
        self.full_access()
        permission = Permission.objects.get(module__code='project_control', action='update')
        UserPermissionOverride.objects.create(user_profile=self.profile, permission=permission, allowed=False)
        self.assertFalse(build_revenue_dashboard(self.user)['scope']['full_source'])

    def test_filtered_aggregates_and_choices_are_not_truncated_and_global_capacity_is_withheld(self):
        self.full_access()
        snapshot = self.source({'capacity': {'periods': [{'period': '2026-09-01', 'demand_manhours': '12',
                                                        'gross_capacity_manhours': '20', 'adjusted_capacity_manhours': '15'}]},
                                'risk_history': [{'date': '2026-08-31', 'poc_risk_aed': '12'}],
                                'poc_risk_history': [{'date': '2026-09-04', 'poc_risk_aed': '15'}]})
        self.row(snapshot)
        self.row(snapshot, 'P-2')
        self.row(snapshot, 'P-3', pm='PM2', business_unit='BU2')
        report = build_revenue_dashboard(self.user, pm='pm1', limit=1, offset=1)
        self.assertEqual(self.metric(report, 'current_forecast')['value'], '30.00')
        self.assertEqual(report['projects']['total_rows'], 2)
        self.assertEqual(report['filters']['project_managers'], ['PM1', 'PM2'])
        self.assertEqual(report['capacity']['status'], 'restricted_scope')
        self.assertEqual(report['risks']['history'], [])
        self.assertEqual(report['risks']['poc_history'], [])
        self.assertEqual(report['risks']['history_status'], 'restricted_scope')
        full = build_revenue_dashboard(self.user)
        self.assertEqual(full['capacity']['rows'][0]['gap_manhours'], '3.00')
        self.assertEqual(full['risks']['poc_history'][0]['poc_risk_aed'], '15')

    def test_zero_missing_and_resource_poc_are_separate_and_signed_gap_is_not_positive_exposure(self):
        self.full_access()
        snapshot = self.source()
        self.row(snapshot, period_revenue_aed=0, poc_pct=40, overclaim_aed=0)
        self.row(snapshot, 'RESOURCE', period_revenue_aed=None, overclaim_aed=999, extra={'section': 'resource_deputation'})
        report = build_revenue_dashboard(self.user)
        actual = self.metric(report, 'total_revenue_actual')
        self.assertIsNone(actual['value'])
        self.assertEqual(actual['known_value'], '0.00')
        self.assertEqual(actual['missing_count'], 1)
        self.assertEqual(self.metric(report, 'total_poc_risk')['value'], '0.00')
        self.assertEqual(report['risks']['totals']['net_overclaim']['value'], '-10.00')
        self.assertEqual(report['risks']['total_rows'], 0)

    def test_cached_summary_requires_full_unfiltered_scope_date_and_reconciliation(self):
        self.full_access()
        snapshot = self.source({'executive_summary': {'reporting_date': timezone.localdate().isoformat(),
                            'totals': {'actual_revenue_aed': '10.004', 'backlog_aed': '999'}}})
        self.row(snapshot)
        self.row(snapshot, 'P-2', period_revenue_aed=None, backlog_without_pt_aed=None)
        report = build_revenue_dashboard(self.user)
        actual = self.metric(report, 'total_revenue_actual')
        self.assertEqual(actual['value'], '10.00')
        self.assertEqual(actual['status'], 'partial')
        self.assertEqual(actual['missing_count'], 1)
        self.assertEqual(actual['basis'], 'cached_workbook_summary')
        self.assertIn('backlog', report['reconciliation']['summary_mismatches'])
        self.assertIsNone(self.metric(report, 'total_backlog')['value'])
        self.assertIsNone(self.metric(build_revenue_dashboard(self.user, pm='PM1'), 'total_revenue_actual')['value'])
        snapshot.reconciliation['executive_summary']['reporting_date'] = '2000-01-01'
        snapshot.save(update_fields=['reconciliation'])
        self.assertIsNone(self.metric(build_revenue_dashboard(self.user), 'total_revenue_actual')['value'])

    def test_forecast_uses_latest_monthly_cutoff_not_sum_of_repeated_month_and_preserves_blanks(self):
        self.full_access()
        snapshot = self.source()
        self.row(snapshot, extra={'history': [{'date': '2026-08-21', 'period_revenue_aed': '30'},
                                             {'date': '2026-08-31', 'period_revenue_aed': '50'}],
                                 'forecasts': [{'period': '2026-08-01', 'revenue_aed': '0'},
                                               {'period': '2026-10-01', 'revenue_aed': None}]})
        chart = {row['period']: row for row in build_revenue_dashboard(self.user)['forecast']}
        self.assertEqual(chart['2026-08-01']['actual_revenue'], '50.00')
        self.assertEqual(chart['2026-08-01']['forecast_revenue'], '0.00')
        self.assertIsNone(chart['2026-10-01']['forecast_revenue'])
        self.assertIsNone(chart['2026-08-01']['pm_forecast'])

    def test_invoice_scope_and_historical_kpi_units_are_retained(self):
        self.full_access()
        snapshot = self.source({'pm_kpi': {'period': '2026-06-01', 'period_label': 'KPI - JUN - 2026',
                            'entries': [{'pm': 'PM1', 'invoicing_ratio': '2.4725', 'cpi_ratio': '1'}],
                            'warnings': ['KPI heading and formula periods differ']}})
        first = self.row(snapshot)
        first.extra['invoicing'] = {'included': True, 'invoiced_aed': '20', 'variance_aed': '10',
                                   'comparison_date': '2026-05-31', 'invoice_pct': '200'}
        first.save(update_fields=['extra'])
        self.row(snapshot, 'P-2', extra={'invoicing': {'included': False, 'invoiced_aed': '999'}})
        report = build_revenue_dashboard(self.user)
        self.assertEqual(report['invoicing']['totals']['invoiced_aed']['value'], '20.00')
        self.assertEqual(report['invoicing']['rows'][0]['comparison_date'], '2026-05-31')
        self.assertEqual(report['invoicing']['rows'][0]['invoice_pct'], '200.00')
        self.assertEqual(report['pm_performance']['rows'][0]['kpi']['invoicing_ratio'], '2.4725')
        self.assertEqual(report['pm_performance']['kpi_period'], '2026-06-01')
        self.assertIsNone(build_revenue_dashboard(self.user, pm='PM1')['pm_performance']['rows'][0]['kpi'])

    def test_database_error_is_explicit_and_never_operational_fallback(self):
        with patch('apps.portfolio.models.PortfolioSource.objects.select_related', side_effect=RuntimeError('test')):
            report = build_revenue_dashboard(self.user)
        self.assertEqual(report['status'], 'error')
        self.assertTrue(report['enabled'])
        self.assertIsNone(report['projects']['total_rows'])

    def test_invoice_comparisons_require_one_date_and_only_explicitly_included_rows(self):
        self.full_access()
        snapshot = self.source()
        invoices = [
            ('P-1', 'PM1', True, '2026-08-31', '10', '15', '5'),
            ('P-2', 'PM2', True, '2026-07-31', '20', '30', '10'),
            ('P-EXCLUDED', 'PM1', False, '1900-01-01', '1000', '1000', '1000'),
            ('P-UNKNOWN', 'PM1', None, '1800-01-01', '2000', '2000', '2000'),
        ]
        for sub, pm, included, baseline, revenue, invoiced, variance in invoices:
            self.row(snapshot, sub, pm=pm, extra={'invoicing': {
                'included': included, 'comparison_date': baseline, 'comparison_revenue_aed': revenue,
                'invoiced_aed': invoiced, 'variance_aed': variance, 'balance_aed': '3', 'contract_value_aed': '40'}})
        result = build_revenue_dashboard(self.user)['invoicing']
        self.assertEqual(result['comparison_dates'], ['2026-07-31', '2026-08-31'])
        self.assertEqual(result['coverage'], {'included_rows': 2, 'excluded_rows': 1, 'unknown_inclusion_rows': 1, 'unmatched_rows': 0})
        for key, subtotal in [('comparison_revenue_aed', '30.00'), ('variance_aed', '15.00')]:
            metric = result['totals'][key]
            self.assertIsNone(metric['value'])
            self.assertEqual(metric['known_value'], subtotal)
            self.assertEqual(metric['known_value_label'], 'Mixed-period subtotal')
            self.assertEqual(metric['status'], 'partial')
            self.assertTrue(metric['mixed_comparison_periods'])
            self.assertEqual(metric['missing_count'], 0)
        self.assertEqual(result['totals']['invoiced_aed']['value'], '45.00')
        self.assertEqual(result['totals']['balance_aed']['value'], '6.00')
        self.assertEqual(result['comparison_periods'][0]['totals']['variance_aed']['value'], '10.00')
        self.assertEqual(result['comparison_periods'][1]['totals']['comparison_revenue_aed']['value'], '10.00')
        filtered = build_revenue_dashboard(self.user, pm='PM1')['invoicing']
        self.assertEqual(filtered['comparison_dates'], ['2026-08-31'])
        self.assertEqual(filtered['totals']['comparison_revenue_aed']['value'], '10.00')
        self.assertEqual(filtered['totals']['variance_aed']['value'], '5.00')

    def test_known_invoice_amounts_with_unknown_date_do_not_form_a_comparison_total(self):
        self.full_access()
        snapshot = self.source()
        self.row(snapshot, extra={'invoicing': {'included': True, 'comparison_date': None,
                  'comparison_revenue_aed': '10', 'variance_aed': '5', 'invoiced_aed': '15'}})
        result = build_revenue_dashboard(self.user)['invoicing']
        metric = result['totals']['comparison_revenue_aed']
        self.assertIsNone(metric['value'])
        self.assertEqual(metric['known_value'], '10.00')
        self.assertEqual(metric['unknown_period_count'], 1)
        self.assertEqual(metric['basis'], 'unknown_comparison_period')
        self.assertIsNone(result['comparison_periods'][0]['totals']['variance_aed']['value'])
        self.assertEqual(result['totals']['invoiced_aed']['value'], '15.00')

    def test_invoice_dates_and_amounts_respect_registered_project_visibility(self):
        snapshot = self.source()
        parent = Project.objects.create(code='P', name='Visible')
        Project.objects.create(code='P-HIDDEN', name='Hidden')
        self.row(snapshot, extra={'invoicing': {'included': True, 'comparison_date': '2026-08-31',
                  'comparison_revenue_aed': '10', 'variance_aed': '5'}})
        self.row(snapshot, 'P-HIDDEN', extra={'invoicing': {'included': True, 'comparison_date': '2025-12-31',
                  'comparison_revenue_aed': '90000', 'variance_aed': '80000'}})
        with patch('apps.project_control.access.accessible_enterprise_projects', return_value=Project.objects.filter(pk=parent.pk)):
            result = build_revenue_dashboard(self.user)['invoicing']
        self.assertEqual(result['comparison_dates'], ['2026-08-31'])
        self.assertEqual(result['totals']['comparison_revenue_aed']['value'], '10.00')
        self.assertEqual(result['totals']['variance_aed']['value'], '5.00')
        self.assertEqual(len(result['comparison_periods']), 1)
