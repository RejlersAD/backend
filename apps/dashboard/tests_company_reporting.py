"""Company dashboard parity without administrator or source-command grants."""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from apps.core.project_models import Project
from apps.dashboard.executive_access import TAB_ACTIONS
from apps.dashboard.reporting_scope import (
    SOURCES, company_reporting, company_reporting_scope, report_action_allowed,
)
from apps.finance.services.receivables_dashboard import build_receivables_dashboard
from apps.invoice_tracker.models import CustomerInvoice
from apps.invoice_tracker.views import CustomerInvoiceViewSet
from apps.portfolio.access import can_upload_workbook
from apps.portfolio.executive import build_revenue_dashboard
from apps.portfolio.models import PortfolioRow, PortfolioSnapshot, PortfolioSource
from apps.rbac.action_policy import module_action_allowed
from apps.rbac.models import Module, Organization, Permission, UserPermissionOverride, UserProfile
from apps.rbac.module_actions import ensure_module_actions


def facts(value):
    if isinstance(value, dict):
        return {key: facts(item) for key, item in value.items() if key not in {'generated_at', 'can_upload'}}
    if isinstance(value, list):
        return [facts(item) for item in value]
    return value


class CompanyReportingTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.reader = User.objects.create_user('company-viewer', email='viewer@example.test')
        self.admin = User.objects.create_superuser('company-admin', 'admin@example.test', 'synthetic-only')
        organization = Organization.objects.create(code='company-report-test', name='Company report test')
        self.profile, _ = UserProfile.objects.get_or_create(user=self.reader, defaults={'organization': organization})
        UserProfile.objects.get_or_create(user=self.admin, defaults={'organization': organization})
        self.profile.roles.clear()
        for code in SOURCES | {'executive_dashboard'}:
            module, _ = Module.objects.get_or_create(code=code, defaults={'name': code})
            ensure_module_actions(Module, Permission, module_ids=[module.pk])
        executive = Module.objects.get(code='executive_dashboard')
        self.permissions = {}
        for tab, action in TAB_ACTIONS.items():
            self.permissions[tab], _ = Permission.objects.get_or_create(
                code=f'executive_dashboard.{action}',
                defaults={'module': executive, 'action': action, 'name': action},
            )
        self.assign('portfolio')
        source = PortfolioSource.objects.create()
        snapshot = PortfolioSnapshot.objects.create(
            source=source, sha256='e' * 64, parser_version='company-test', file_name='company.xlsx',
            reporting_date=date(2026, 10, 16), row_count=85,
        )
        source.active_snapshot = snapshot
        source.save(update_fields=['active_snapshot'])
        self.snapshot = snapshot
        # Only 17 identities are registered, reproducing the screenshot gap.
        Project.objects.bulk_create([Project(code=f'P-{i}', name=f'Registered {i}') for i in range(17)])
        PortfolioRow.objects.bulk_create([
            PortfolioRow(snapshot=snapshot, project_code=f'P-{i}', subproject_code=f'P-{i}',
                         title=f'Workbook {i}', source_row=i + 8, currency='AED',
                         period_revenue_aed=Decimal('10'), backlog_without_pt_aed=Decimal('100'),
                         overclaim_aed=Decimal('1'), include_without_pt=True, include_with_pt=True,
                         pm='PM1', business_unit='BU1',
                         extra={'executive': {'period': '2026-10-01', 'current_forecast_aed': '20',
                                               'pm_forecast_aed': '15', 'forecast_variance_aed': '5'}})
            for i in range(85)
        ])
        invoice = CustomerInvoice(invoice_number='COMPANY-INVOICE', rad_project_no='P-84',
                                  invoice_date=date(2026, 10, 1), invoice_amount=Decimal('1000'),
                                  actual_payment_received=Decimal('100'), currency='AED', payment_status='pending')
        invoice.save(_skip_recompute=True)
        from apps.sales.models import Client, Deal
        from apps.hr_core.models import EmployeeMaster
        client = Client.objects.create(client_code='COMPANY-CLIENT', company_name='Company client', account_manager=self.admin)
        Deal.objects.create(deal_code='COMPANY-DEAL', deal_name='Another owner opportunity',
                            client=client, owner=self.admin, stage='qualified', currency='AED',
                            estimated_value=Decimal('123'), expected_close_date=date(2026, 11, 1))
        EmployeeMaster.objects.create(employee_number='COMPANY-EMP', employee_code='COMPANY-EMP',
                                      emp_code='COMPANY-EMP', first_name='Private', last_name='Employee',
                                      employment_status='active', join_date=date(2020, 1, 1))
        self.client = APIClient()
        self.client.force_authenticate(self.reader)

    def assign(self, selected):
        for tab, permission in self.permissions.items():
            UserPermissionOverride.objects.update_or_create(
                user_profile=self.profile, permission=permission, defaults={'allowed': tab == selected},
            )

    def test_portfolio_viewer_sees_same_85_projects_and_totals_as_administrator(self):
        path = '/api/v1/dashboard/executive/portfolio-workbook/revenue/'
        viewer = self.client.get(path)
        self.assertEqual(viewer.status_code, 200)
        self.assertTrue(viewer.data['scope']['full_source'])
        self.assertEqual(viewer.data['scope']['project_count'], 85)
        self.assertFalse(viewer.data['can_upload'])
        self.client.force_authenticate(self.admin)
        admin = self.client.get(path)
        self.assertEqual(admin.status_code, 200)
        self.assertTrue(admin.data['can_upload'])
        self.assertEqual(facts(viewer.data), facts(admin.data))

    def test_each_tab_matches_administrator_data_and_withholds_unassigned_tabs(self):
        keys = {'overview': 'kpis', 'financial': 'financial_performance', 'portfolio': 'portfolio_performance',
                'commercial': 'commercial_performance', 'workforce': 'workforce_performance', 'risk': 'risk_compliance'}
        self.client.force_authenticate(self.admin)
        admin = self.client.get('/api/v1/dashboard/executive/').data
        for tab, key in keys.items():
            with self.subTest(tab=tab):
                self.assign(tab)
                self.client.force_authenticate(self.reader)
                response = self.client.get('/api/v1/dashboard/executive/', {'tab': tab})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data['allowed_tabs'], [tab])
                self.assertEqual(facts(response.data[key]), facts(admin[key]))
                if tab == 'commercial':
                    self.assertEqual(response.data[key]['register']['total_rows'], 1)
                    self.assertIn('COMPANY-DEAL', str(response.data[key]['register']))
                if tab == 'workforce':
                    self.assertNotIn('Private Employee', str(response.data[key]))
                if tab != 'overview':
                    self.assertEqual(response.data['departments'], [])
                for other, other_key in keys.items():
                    if other not in {tab, 'overview', 'portfolio'}:
                        self.assertNotIn(other_key, response.data)

    def test_finance_subreports_match_admin_without_source_grants(self):
        for tab in ('overview', 'financial'):
            self.assign(tab)
            for endpoint in ('receivables', 'customer-invoices'):
                path = f'/api/v1/dashboard/executive/{endpoint}/'
                self.client.force_authenticate(self.reader)
                viewer = self.client.get(path)
                self.client.force_authenticate(self.admin)
                admin = self.client.get(path)
                self.assertEqual(viewer.status_code, 200)
                self.assertEqual(facts(viewer.data), facts(admin.data))

    def test_portfolio_invoice_position_includes_unregistered_workbook_projects(self):
        path = '/api/v1/dashboard/executive/portfolio-workbook/outgoing-invoices/'
        viewer = self.client.get(path)
        self.assertEqual(viewer.status_code, 200)
        self.client.force_authenticate(self.admin)
        admin = self.client.get(path)
        self.assertEqual(facts(viewer.data), facts(admin.data))
        self.assertTrue(viewer.data['rows'])

    def test_filters_use_same_complete_company_source(self):
        path = '/api/v1/dashboard/executive/portfolio-workbook/revenue/'
        for filters in ({'search': 'P-84'}, {'pm': 'PM1', 'business_unit': 'BU1'}):
            self.client.force_authenticate(self.reader)
            viewer = self.client.get(path, filters)
            self.client.force_authenticate(self.admin)
            admin = self.client.get(path, filters)
            self.assertEqual(facts(viewer.data), facts(admin.data))

    def test_scope_does_not_grant_uploads_source_endpoints_or_administrator_flags(self):
        with company_reporting_scope(self.reader, ('portfolio',)):
            self.assertTrue(report_action_allowed(self.reader, 'project_control', 'read'))
            self.assertFalse(module_action_allowed(self.reader, 'project_control', 'read'))
            self.assertFalse(report_action_allowed(self.reader, 'project_control', 'update'))
            self.assertFalse(report_action_allowed(self.reader, 'project_control', 'approve'))
            self.assertFalse(can_upload_workbook(self.reader))
        for operation in ('preview', 'import'):
            response = self.client.post(f'/api/v1/dashboard/executive/portfolio-workbook/{operation}/', {})
            self.assertEqual(response.status_code, 403)
        request = APIRequestFactory().get('/api/v1/invoice-tracker/invoices/')
        force_authenticate(request, self.reader)
        self.assertEqual(CustomerInvoiceViewSet.as_view({'get': 'list'})(request).status_code, 403)
        self.reader.refresh_from_db()
        self.assertFalse(self.reader.is_staff)
        self.assertFalse(self.reader.is_superuser)
        self.assertFalse(self.profile.roles.exists())
        self.assertEqual(build_receivables_dashboard(self.reader)['sources']['receivables']['status'], 'restricted')
        self.assertEqual(build_revenue_dashboard(self.reader)['status'], 'restricted')

    def test_scope_is_actor_bound_and_resets_on_exception(self):
        stranger = get_user_model().objects.create_user('stranger', email='stranger@example.test')
        with self.assertRaises(RuntimeError):
            with company_reporting_scope(self.reader, ('portfolio',)):
                self.assertTrue(company_reporting(self.reader))
                self.assertFalse(company_reporting(stranger))
                raise RuntimeError('Synthetic builder failure')
        self.assertFalse(company_reporting(self.reader))
        self.assertFalse(report_action_allowed(self.reader, 'project_control', 'read'))

    def test_revocation_and_explicit_executive_deny_block_report(self):
        self.assign('financial')
        self.assertEqual(self.client.get('/api/v1/dashboard/executive/portfolio-workbook/revenue/').status_code, 403)
        read = Permission.objects.get(code='executive_dashboard.read')
        UserPermissionOverride.objects.create(user_profile=self.profile, permission=read, allowed=False)
        self.assertEqual(self.client.get('/api/v1/dashboard/executive/').status_code, 403)

    def test_disabled_source_is_not_enabled_by_reporting_scope(self):
        module = Module.objects.get(code='project_control')
        module.is_active = False
        module.save(update_fields=['is_active'])
        with company_reporting_scope(self.reader, ('portfolio',)):
            self.assertFalse(report_action_allowed(self.reader, 'project_control', 'read'))
