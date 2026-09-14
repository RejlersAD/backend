"""Permission boundaries, currency integrity and unknown-data behavior."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from importlib import import_module
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.apps import apps
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.project_models import Project, ProjectMilestone
from apps.dashboard.executive import _build_finance, _build_sales, _build_procurement, _money_rows
from apps.dashboard.executive_people import build_people_departments
from apps.finance.models import Invoice
from apps.invoice_tracker.models import CustomerInvoice
from apps.rbac.models import (
    Module, Organization, Permission, Role, RoleModule, RolePermission,
    UserPermissionOverride, UserProfile, UserRole,
)
from apps.rbac.module_actions import ensure_module_actions
from apps.sales.models import Client, Deal


class ExecutiveDashboardTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            'executive-reader', email='executive-reader@example.test',
        )
        organization, _ = Organization.objects.get_or_create(
            code='executive-tests', defaults={'name': 'Executive tests'},
        )
        self.profile, _ = UserProfile.objects.get_or_create(
            user=self.user, defaults={'organization': organization},
        )
        # Explicit test role; do not rely on registration's default grants.
        self.profile.roles.clear()
        self.role = Role.objects.create(code='executive-test-reader', name='Executive test reader', level=3)
        UserRole.objects.create(user_profile=self.profile, role=self.role)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = '/api/v1/dashboard/executive/'

    def grant(self, *codes):
        for code in codes:
            module, _ = Module.objects.get_or_create(code=code, defaults={'name': code})
            ensure_module_actions(Module, Permission, module_ids=[module.pk])
            RoleModule.objects.get_or_create(role=self.role, module=module)
            for permission in module.permissions.filter(action='read', is_active=True):
                RolePermission.objects.get_or_create(role=self.role, permission=permission)

    def response(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, getattr(response, 'data', None))
        return response

    def test_authentication_and_dedicated_read_grant_are_required(self):
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.url).status_code, [401, 403])

    def test_executive_grant_does_not_grant_underlying_sources(self):
        self.grant('executive_dashboard')
        response = self.response()
        data = response.data
        self.assertTrue(all(section['status'] == 'restricted' for section in data['departments']))
        self.assertEqual(data['actions'], [])
        self.assertIsNone(data['portfolio']['counts'])
        self.assertEqual(data['coverage']['available_departments'], 0)
        self.assertTrue(all(kpi['value'] is None for kpi in data['kpis']))
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertFalse(data['scope']['consolidated'])
        self.assertEqual(data['workforce']['status'], 'restricted')
        self.assertEqual(data['workforce']['allocation'], [])
        self.assertTrue(all(section['source_updated_at'] is None for section in data['departments']))
        portfolio_tab = data['portfolio_performance']
        self.assertEqual(portfolio_tab['status'], 'restricted')
        self.assertIsNone(portfolio_tab['register']['total_rows'])
        self.assertIsNone(portfolio_tab['health']['counts'])
        self.assertEqual(portfolio_tab['register']['projects'], [])
        self.assertEqual(portfolio_tab['concentration']['by_currency'], [])

    def test_explicit_deny_revokes_source_and_endpoint_even_for_superuser(self):
        self.grant('executive_dashboard', 'project_control')
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        source_permission = Permission.objects.filter(module__code='project_control', action='read').first()
        UserPermissionOverride.objects.create(user_profile=self.profile, permission=source_permission, allowed=False)
        self.assertEqual(self.response().data['portfolio']['status'], 'restricted')
        executive_permission = Permission.objects.filter(module__code='executive_dashboard', action='read').first()
        UserPermissionOverride.objects.create(user_profile=self.profile, permission=executive_permission, allowed=False)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_snapshot_does_not_offer_writes(self):
        self.grant('executive_dashboard')
        self.assertEqual(self.client.post(self.url, {}).status_code, 405)

    def test_projects_use_governed_exceptions_and_exclude_deleted(self):
        self.grant('executive_dashboard', 'project_control')
        Project.objects.create(code='EX-001', name='Missing reporting', owner=self.user, status='active',
                               client_name='Recorded client', contract_value=Decimal('2000.50'), currency='USD',
                               custom_fields={'forecast_margin': 75, 'business_unit': 'Uncontrolled label'})
        Project.objects.create(code='EX-DELETED', name='Deleted project', owner=self.user, status='active', is_deleted=True)
        data = self.response().data
        self.assertEqual(data['portfolio']['counts']['total'], 1)
        self.assertEqual(data['portfolio']['counts']['at_risk'], 1)
        project = data['portfolio']['projects'][0]
        self.assertEqual(project['health'], 'high')
        self.assertIsNone(project['data_date'])
        self.assertGreater(project['exception_count'], 0)
        self.assertEqual(project['client_name'], 'Recorded client')
        self.assertEqual(project['contract_value'], '2000.50')
        self.assertEqual(project['currency'], 'USD')
        self.assertIsNone(project['business_unit'])
        self.assertIsNone(project['schedule_variance'])
        self.assertIsNone(project['forecast_margin'])
        self.assertIsNone(next(kpi for kpi in data['kpis'] if kpi['id'] == 'revenue')['value'])

    def test_empty_project_register_is_known_zero_not_an_error(self):
        self.grant('executive_dashboard', 'project_control')
        data = self.response().data
        self.assertEqual(data['portfolio']['status'], 'available')
        self.assertEqual(data['portfolio']['counts']['at_risk'], 0)
        self.assertEqual(next(kpi for kpi in data['kpis'] if kpi['id'] == 'projects_at_risk')['value'], 0)

    def test_source_failure_is_explicit_and_other_departments_survive(self):
        self.grant('executive_dashboard', 'project_control')
        with self.assertLogs('apps.dashboard.executive', level='ERROR'):
            with patch('apps.dashboard.executive._build_projects', side_effect=RuntimeError('source failed')):
                data = self.response().data
        self.assertEqual(data['portfolio']['status'], 'error')
        self.assertIsNone(data['portfolio']['counts'])
        self.assertEqual(len(data['departments']), 7)
        self.assertEqual(next(kpi for kpi in data['kpis'] if kpi['id'] == 'projects_at_risk')['status'], 'error')
        self.assertNotIn('source failed', str(data))

    def test_pipeline_aggregates_all_accessible_rows_by_currency(self):
        account = Client.objects.create(client_code='EX-CLIENT', company_name='Sample account', account_manager=self.user)
        for index, currency in enumerate(['USD', 'AED']):
            Deal.objects.create(deal_code=f'EX-DEAL-{index}', deal_name='Scoped opportunity', client=account,
                                owner=self.user, estimated_value=Decimal('100.00'), currency=currency,
                                stage='proposal', expected_close_date=timezone.localdate())
        other = get_user_model().objects.create_user('other-sales-reader', email='other-sales@example.test')
        Deal.objects.create(deal_code='EX-HIDDEN', deal_name='Other owner', client=account,
                            owner=other, estimated_value=Decimal('9000.00'), currency='USD',
                            expected_close_date=timezone.localdate())
        recorded_update = timezone.now() - timedelta(days=2)
        Deal.objects.filter(owner=self.user).update(updated_at=recorded_update)
        context = {'allowed_modules': {'sales_opportunities'}, 'generated_at': timezone.now()}
        result = _build_sales(self.user, context)
        pipeline = next(row for row in result['metrics'] if row['id'] == 'weighted_pipeline')
        self.assertIsNone(pipeline['value'])
        self.assertEqual(pipeline['by_currency'], [{'currency': 'AED', 'amount': '50.00'}, {'currency': 'USD', 'amount': '50.00'}])
        self.assertEqual(result['metrics'][0]['value'], 2)
        self.assertEqual(result['pipeline_stages'], [{
            'stage': 'proposal', 'label': 'Proposal & Estimate', 'count': 2,
            'by_currency': [
                {'currency': 'AED', 'amount': '100.00', 'weighted_amount': '50.00'},
                {'currency': 'USD', 'amount': '100.00', 'weighted_amount': '50.00'},
            ],
        }])
        self.assertEqual(result['source_updated_at'], recorded_update.isoformat())
        self.assertEqual(result['source_timestamp_kind'], 'latest_record_update')

    def test_finance_respects_separate_register_grants_and_due_dates(self):
        CustomerInvoice.objects.create(invoice_number='EX-AR', currency='USD', grand_total=100,
                                       invoice_amount=100, balance_to_be_received=100,
                                       due_date=timezone.localdate() - timedelta(days=1))
        Invoice.objects.create(invoice_number='EX-AP', total_amount=999, currency='AED', submitted_by=self.user)
        result = _build_finance(self.user, {'allowed_modules': {'finance_outgoing'}, 'generated_at': timezone.now()})
        rows = {row['id']: row for row in result['metrics']}
        self.assertEqual(rows['payables']['status'], 'restricted')
        self.assertIsNone(rows['payables']['value'])
        self.assertEqual(rows['receivables']['by_currency'], [{'currency': 'USD', 'amount': '100.00'}])
        self.assertEqual(rows['overdue_receivables']['value'], 1)

    def test_people_without_grants_never_query_sources(self):
        with patch('apps.dashboard.executive_people.apps.get_model') as get_model:
            sections = build_people_departments(self.user, {'allowed_modules': set(), 'generated_at': timezone.now()})
        get_model.assert_not_called()
        self.assertTrue(all(section['status'] == 'restricted' for section in sections))

    def test_missing_currency_never_becomes_aed_and_case_variants_combine(self):
        for index, currency in enumerate(['', 'AED', 'usd', 'USD']):
            CustomerInvoice.objects.create(invoice_number=f'EX-FX-{index}', currency=currency,
                                           balance_to_be_received=Decimal('10.00'))
        rows = _money_rows(CustomerInvoice.objects.all(), 'balance_to_be_received')
        self.assertEqual(rows, [
            {'currency': 'AED', 'amount': '10.00'},
            {'currency': 'UNSPECIFIED', 'amount': '10.00'},
            {'currency': 'USD', 'amount': '20.00'},
        ])
        result = _build_finance(self.user, {'allowed_modules': {'finance_outgoing'}, 'generated_at': timezone.now()})
        self.assertEqual(result['metrics'][0]['by_currency'], rows)

    def test_missing_invoice_balance_withholds_total_instead_of_partial_sum(self):
        CustomerInvoice.objects.create(invoice_number='EX-KNOWN', currency='USD', balance_to_be_received=10)
        CustomerInvoice.objects.create(invoice_number='EX-UNKNOWN', currency='USD', balance_to_be_received=None)
        result = _build_finance(self.user, {'allowed_modules': {'finance_outgoing'}, 'generated_at': timezone.now()})
        row = result['metrics'][0]
        self.assertEqual(row['status'], 'unavailable')
        self.assertIsNone(row['value'])
        self.assertNotIn('by_currency', row)

    def test_endpoint_performs_no_database_writes(self):
        self.grant('executive_dashboard', 'project_control', 'sales_opportunities', 'finance_outgoing')
        with CaptureQueriesContext(connection) as queries:
            self.response()
        writes = [row['sql'] for row in queries if row['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))]
        self.assertEqual(writes, [])

    def test_migration_registers_read_access_idempotently_without_role_grants(self):
        migrate = import_module('apps.rbac.migrations.0063_executive_dashboard_read_access').register_executive_dashboard
        role_grants = RoleModule.objects.count()
        schema_editor = SimpleNamespace(connection=connection)
        migrate(apps, schema_editor)
        migrate(apps, schema_editor)
        self.assertTrue(Module.objects.filter(code='executive_dashboard').exists())
        self.assertTrue(Permission.objects.filter(code='executive_dashboard.read', action='read').exists())
        self.assertEqual(RoleModule.objects.count(), role_grants)

    def test_hr_uses_employee_master_without_exposing_people(self):
        from apps.hr_core.models import EmployeeMaster
        today = timezone.localdate()
        fixtures = [
            ('EX-ACTIVE', {'join_date': today - timedelta(days=40), 'department': 'Engineering'}),
            ('EX-NEW', {'join_date': today - timedelta(days=5), 'department': 'Engineering'}),
            ('EX-NO-DEPT', {'join_date': today - timedelta(days=40), 'department': '   '}),
            ('EX-TEST', {'join_date': today, 'is_test_person': True}),
            ('EX-FUTURE', {'join_date': today + timedelta(days=3)}),
            ('EX-EXITED', {'join_date': today - timedelta(days=100), 'exit_date': today - timedelta(days=1)}),
        ]
        for number, fields in fixtures:
            EmployeeMaster.objects.create(employee_number=number, employee_code=number, emp_code=number, first_name='Private', last_name='Person',
                                          employment_status='active', **fields)
        sections = build_people_departments(self.user, {'allowed_modules': {'hr_management'}, 'generated_at': timezone.now()})
        hr = next(section for section in sections if section['id'] == 'hr')
        self.assertEqual(hr['status'], 'available')
        rows = {row['id']: row for row in hr['metrics']}
        self.assertEqual(rows['headcount']['value'], 3)
        self.assertEqual(rows['joiners_30d']['value'], 1)
        self.assertNotIn('Private', str(hr))
        self.assertNotIn('EX-ACTIVE', str(hr))
        self.assertEqual(hr['workforce_by_department'], [
            {'department': 'Engineering', 'headcount': 2}, {'department': 'Unassigned', 'headcount': 1},
        ])
        self.grant('executive_dashboard', 'hr_management')
        workforce = self.response().data['workforce']
        self.assertEqual(workforce['unit'], 'people')
        self.assertEqual(workforce['allocation'], [{'label': 'Engineering', 'count': 2}, {'label': 'Unassigned', 'count': 1}])
        self.assertIsNone(workforce['critical_roles'])
        self.assertIsNone(workforce['capacity_gap'])
        self.assertNotIn('Private', str(workforce))

    def test_quality_actions_are_not_safety_incidents_and_zero_is_preserved(self):
        from apps.qhse.models import QHSERunningProject
        QHSERunningProject.objects.create(sr_no=1, project_no='EX-QHSE', project_title='Quality project',
                                         client='Sample', project_manager='Recorded manager', cars_open=2,
                                         cars_delayed_closing_no_days=5)
        QHSERunningProject.objects.create(sr_no=2, project_no='EX-INACTIVE', project_title='Inactive',
                                         client='Sample', project_manager='Recorded manager', cars_open=20, is_active=False)
        sections = build_people_departments(self.user, {'allowed_modules': {'qhse'}, 'generated_at': timezone.now()})
        qhse = next(section for section in sections if section['id'] == 'qhse')
        rows = {row['id']: row for row in qhse['metrics']}
        self.assertEqual(rows['open_cars']['value'], 2)
        self.assertEqual(rows['overdue_car_projects']['value'], 1)
        self.assertEqual(rows['delayed_audit_projects']['value'], 0)
        self.assertIsNone(rows['safety_incidents']['value'])
        self.assertEqual(rows['safety_incidents']['status'], 'unavailable')

    def test_procurement_excludes_unlinked_and_deleted_project_records(self):
        from apps.procurement.models import PurchaseOrder, Vendor
        self.grant('procurement_orders')
        project = Project.objects.create(code='EX-PROC', name='Linked project', owner=self.user)
        deleted = Project.objects.create(code='EX-PROC-DELETED', name='Deleted project', owner=self.user, is_deleted=True)
        vendor = Vendor.objects.create(name='Sample supplier', vendor_code='EX-VENDOR')
        for code, parent in [('EX-PO', project), ('EX-UNLINKED', None), ('EX-PO-DELETED', deleted)]:
            PurchaseOrder.objects.create(po_number=code, enterprise_project=parent, vendor=vendor,
                                         status='sent', total_amount=100, currency='USD', created_by=self.user,
                                         expected_delivery=timezone.localdate() - timedelta(days=2))
        result = _build_procurement(self.user, {'allowed_modules': {'procurement_orders'}, 'generated_at': timezone.now()})
        rows = {row['id']: row for row in result['metrics']}
        self.assertEqual(rows['overdue_deliveries']['value'], 1)
        self.assertEqual(rows['po_commitments']['by_currency'], [{'currency': 'USD', 'amount': '100.00'}])
        self.assertEqual(len(result['actions']), 1)

    def test_financial_headlines_do_not_infer_profit_cash_or_dso(self):
        self.grant('executive_dashboard')
        financial = self.response().data['financial_performance']
        self.assertEqual([row['id'] for row in financial['kpis']], [
            'revenue_ytd', 'operating_profit', 'operating_margin', 'cash_position', 'dso',
        ])
        self.assertTrue(all(row['value'] is None and row['status'] == 'unavailable' for row in financial['kpis']))
        self.assertFalse(financial['controls']['ledger_connected'])
        self.assertEqual(financial['working_capital']['aging']['status'], 'restricted')
        self.assertEqual(financial['actions'], [])
        receivables = financial['working_capital']['metrics'][0]
        self.assertEqual(receivables['status'], 'restricted')
        self.assertIsNone(receivables['route'])
        self.assertEqual(receivables['by_currency'], [])

    def test_receivables_aging_exact_due_date_boundaries_and_currency(self):
        self.grant('executive_dashboard', 'finance_outgoing')
        today = timezone.localdate()
        for index, (days, amount) in enumerate([(-1, 10), (0, 20), (1, 30), (30, 40), (31, 50), (60, 60), (61, 70), (None, 80)]):
            CustomerInvoice.objects.create(invoice_number=f'EX-AGE-{index}', currency='aed' if index == 1 else 'AED',
                                           due_date=None if days is None else today - timedelta(days=days),
                                           balance_to_be_received=amount)
        CustomerInvoice.objects.create(invoice_number='EX-AGE-USD', currency='USD', due_date=today, balance_to_be_received=90)
        for status in ['paid', 'cancelled']:
            settled = CustomerInvoice.objects.create(invoice_number=f'EX-AGE-{status}', currency='AED', payment_status=status,
                                                     due_date=today - timedelta(days=90), balance_to_be_received=999)
            # Imported registers may retain a positive amount after settlement;
            # read reporting must still honor the recorded settled status.
            CustomerInvoice.objects.filter(pk=settled.pk).update(payment_status=status)
        financial = self.response().data['financial_performance']
        aging = financial['working_capital']['aging']
        rows = {row['currency']: row for row in aging['by_currency']}
        self.assertEqual(aging['status'], 'available')
        self.assertEqual(aging['invoice_count'], 9)
        self.assertEqual(rows['AED']['total'], '360.00')
        self.assertEqual({row['id']: row['amount'] for row in rows['AED']['buckets']}, {
            'current': '30.00', 'days_1_30': '70.00', 'days_31_60': '110.00',
            'over60': '70.00', 'unknown_due_date': '80.00',
        })
        self.assertEqual(rows['USD']['total'], '90.00')
        self.assertEqual(rows['AED']['unknown_due_date_count'], 1)
        metrics = {row['id']: row for row in financial['working_capital']['metrics']}
        self.assertEqual(metrics['receivables_over60']['by_currency'], [
            {'currency': 'AED', 'amount': '70.00'}, {'currency': 'USD', 'amount': '0.00'},
        ])
        self.assertEqual(metrics['receivables']['route'], '/finance/outgoing-invoices')

    def test_missing_receivable_balance_withholds_only_affected_currency(self):
        self.grant('executive_dashboard', 'finance_outgoing')
        today = timezone.localdate()
        for code, currency, balance in [('KNOWN', 'AED', 50), ('MISSING', 'aed', None), ('USD', 'USD', 100), ('UNSPECIFIED', '', 40)]:
            CustomerInvoice.objects.create(invoice_number=f'EX-PARTIAL-{code}', currency=currency,
                                           due_date=today, balance_to_be_received=balance)
        financial = self.response().data['financial_performance']
        aging = financial['working_capital']['aging']
        self.assertEqual(aging['status'], 'incomplete')
        self.assertEqual(aging['missing_balance_count'], 1)
        rows = {row['currency']: row for row in aging['by_currency']}
        self.assertIsNone(rows['AED']['total'])
        self.assertTrue(all(row['amount'] is None for row in rows['AED']['buckets']))
        self.assertEqual(rows['USD']['total'], '100.00')
        self.assertEqual(rows['UNSPECIFIED']['total'], '40.00')
        receivables = financial['working_capital']['metrics'][0]
        self.assertEqual(receivables['status'], 'partial')
        self.assertEqual(receivables['incomplete_currencies'], ['AED'])
        self.assertEqual(receivables['by_currency'], [
            {'currency': 'UNSPECIFIED', 'amount': '40.00'}, {'currency': 'USD', 'amount': '100.00'},
        ])

    def test_no_outstanding_balances_has_no_inferred_currency(self):
        self.grant('executive_dashboard', 'finance_outgoing')
        for index, (status, balance) in enumerate([('paid', None), ('cancelled', None), ('pending', 0), ('pending', -10)]):
            CustomerInvoice.objects.create(invoice_number=f'EX-NONE-{index}', currency='USD', payment_status=status,
                                           balance_to_be_received=balance)
        aging = self.response().data['financial_performance']['working_capital']['aging']
        self.assertEqual(aging['status'], 'available')
        self.assertEqual(aging['by_currency'], [])
        self.assertEqual(aging['invoice_count'], 0)
        self.assertEqual(aging['missing_balance_count'], 0)

    def test_financial_aging_respects_explicit_outgoing_deny(self):
        self.grant('executive_dashboard', 'finance_outgoing')
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        CustomerInvoice.objects.create(invoice_number='EX-DENIED-AR', currency='USD', balance_to_be_received=100)
        permission = Permission.objects.filter(module__code='finance_outgoing', action='read').first()
        UserPermissionOverride.objects.create(user_profile=self.profile, permission=permission, allowed=False)
        aging = self.response().data['financial_performance']['working_capital']['aging']
        self.assertEqual(aging['status'], 'restricted')
        self.assertEqual(aging['by_currency'], [])
        self.assertIsNone(aging['invoice_count'])
        self.assertIsNone(aging['missing_balance_count'])
        self.assertIsNone(aging['source_updated_at'])

    def test_financial_source_failure_does_not_become_zero_or_break_overview(self):
        self.grant('executive_dashboard', 'finance_outgoing')
        with self.assertLogs('apps.dashboard.financial_performance', level='ERROR'):
            with patch('apps.dashboard.financial_performance._aging', side_effect=RuntimeError('private source problem')):
                report = self.response().data
        aging = report['financial_performance']['working_capital']['aging']
        self.assertEqual(aging['status'], 'error')
        self.assertIsNone(aging['missing_balance_count'])
        self.assertIsNone(aging['invoice_count'])
        self.assertEqual(aging['by_currency'], [])
        self.assertEqual(len(report['departments']), 7)
        self.assertNotIn('private source problem', str(report))

    def test_portfolio_contracts_use_open_scope_and_complete_original_currencies(self):
        self.grant('executive_dashboard', 'project_control')
        for code, status, currency, amount in [
            ('USD-A', 'active', 'USD', 100), ('USD-B', 'planning', 'usd', 200),
            ('AED-A', 'on_hold', 'AED', 50), ('AED-B', 'active', 'AED', None),
            ('UNKNOWN', 'active', '', 10), ('EUR-ZERO', 'planning', 'EUR', 0),
            ('CLOSED', 'completed', 'USD', 9999), ('CANCELLED', 'cancelled', 'USD', 9999),
        ]:
            Project.objects.create(code=f'PF-{code}', name=code, status=status, currency=currency,
                                   contract_value=amount, owner=self.user, client_name='Recorded client')
        report = self.response().data
        portfolio = report['portfolio_performance']
        self.assertEqual(report['portfolio']['counts']['total'], 8)  # Overview scope is unchanged.
        self.assertEqual(portfolio['register']['total_rows'], 6)
        kpis = {row['id']: row for row in portfolio['kpis']}
        self.assertEqual(kpis['active_projects']['value'], 3)
        self.assertEqual(kpis['contract_value']['status'], 'partial')
        self.assertEqual(kpis['contract_value']['by_currency'], [
            {'currency': 'EUR', 'amount': '0.00'}, {'currency': 'USD', 'amount': '300.00'},
        ])
        self.assertEqual(kpis['contract_value']['incomplete_currencies'], ['AED', 'UNSPECIFIED'])
        self.assertEqual(kpis['contract_value']['missing_contract_count'], 1)
        self.assertTrue(all(kpis[key]['value'] is None and kpis[key]['status'] == 'unavailable'
                            for key in ['forecast_margin', 'schedule_confidence', 'revenue_remaining']))
        self.assertEqual(portfolio['health']['counts']['total'], 6)
        self.assertEqual(portfolio['health']['counts']['clear'], 0)
        self.assertTrue(all(row['health'] == 'high' and row['phase'] is None for row in portfolio['register']['projects']))
        self.assertTrue(all(row['impact'] is None for row in portfolio['actions']))
        self.assertEqual(portfolio['delivery_capacity']['status'], 'unavailable')
        self.assertEqual(portfolio['delivery_outlook']['scatter'], [])

    def test_portfolio_concentration_needs_complete_positive_client_denominator(self):
        self.grant('executive_dashboard', 'project_control')
        for code, currency, client, amount in [
            ('A', 'USD', 'Client A', 100), ('B', 'usd', 'Client B', 300),
            ('C', 'AED', 'Client C', 500), ('D', 'AED', 'Client D', None),
            ('E', 'EUR', 'Client A', 0), ('F', 'GBP', '', 100),
        ]:
            Project.objects.create(code=f'PF-CON-{code}', name=code, status='active', currency=currency,
                                   contract_value=amount, client_name=client, owner=self.user)
        concentration = self.response().data['portfolio_performance']['concentration']
        rows = {row['currency']: row for row in concentration['by_currency']}
        self.assertEqual(concentration['status'], 'partial')
        self.assertEqual(rows['USD']['total'], '400.00')
        self.assertEqual(rows['USD']['top_client'], {'label': 'Client B', 'amount': '300.00', 'share_pct': 75.0})
        self.assertEqual(rows['USD']['top_five_projects']['share_pct'], 100.0)
        self.assertIsNone(rows['AED']['total'])
        self.assertIsNone(rows['AED']['top_client'])
        self.assertIsNone(rows['AED']['top_five_projects'])
        self.assertEqual(rows['EUR']['total'], '0.00')
        self.assertIsNone(rows['EUR']['top_client'])
        self.assertIsNone(rows['GBP']['top_client'])
        self.assertEqual(rows['GBP']['top_five_projects']['share_pct'], 100.0)

    def test_portfolio_milestones_preserve_dates_scope_and_unknown_accountability(self):
        self.grant('executive_dashboard', 'project_control')
        today = timezone.localdate()
        project = Project.objects.create(code='PF-MAIN', name='Accessible project', owner=self.user, status='active')
        hidden = Project.objects.create(code='PF-HIDDEN', name='Hidden source', owner=self.user, status='active')
        for index, days in enumerate([-1, 0, 30, 31]):
            ProjectMilestone.objects.create(project=project, name=f'Target {index}', target_date=today + timedelta(days=days))
        ProjectMilestone.objects.create(project=project, name='Completed', target_date=today - timedelta(days=10), is_completed=True)
        ProjectMilestone.objects.create(project=project, name='Deleted', target_date=today - timedelta(days=11), is_deleted=True)
        ProjectMilestone.objects.create(project=hidden, name='Hidden milestone', target_date=today - timedelta(days=12))
        # Preserve any narrower future/shared project row policy for ALL contributors.
        with patch('apps.project_control.access.accessible_enterprise_projects', return_value=Project.objects.filter(pk=project.pk)):
            portfolio = self.response().data['portfolio_performance']
        milestones = portfolio['milestones']
        self.assertEqual(milestones['status'], 'available')
        self.assertEqual(milestones['total_rows'], 3)
        self.assertEqual([row['status'] for row in milestones['rows']], ['overdue', 'due_today', 'upcoming'])
        metrics = {row['id']: row for row in milestones['metrics']}
        self.assertEqual(metrics['overdue']['value'], 1)
        self.assertEqual(metrics['due_30d']['value'], 2)
        self.assertIsNone(metrics['at_risk']['value'])
        for row in milestones['rows']:
            self.assertIsNone(row['owner'])
            self.assertIsNone(row['readiness'])
            self.assertEqual(row['project_owner'], self.user.email)
            self.assertIn('view=plan-baseline', row['route'])
        self.assertEqual(portfolio['register']['projects'][0]['next_milestone']['name'], 'Target 0')
        self.assertNotIn('Hidden', str(portfolio))

    def test_portfolio_preview_caps_do_not_cap_aggregates_or_repeat_governance_read(self):
        self.grant('executive_dashboard', 'project_control')
        Project.objects.bulk_create([
            Project(code=f'PF-CAP-{index:03}', name=f'Project {index}', owner=self.user,
                    status='planning', currency='USD', contract_value=1)
            for index in range(205)
        ])
        from apps.project_control.services.portfolio_exceptions import build_portfolio_exception_dashboard
        with patch('apps.project_control.services.portfolio_exceptions.build_portfolio_exception_dashboard',
                   wraps=build_portfolio_exception_dashboard) as builder:
            portfolio = self.response().data['portfolio_performance']
        self.assertEqual(builder.call_count, 1)
        self.assertEqual(portfolio['register']['total_rows'], 205)
        self.assertEqual(portfolio['register']['returned_rows'], 200)
        self.assertTrue(portfolio['register']['truncated'])
        self.assertEqual(portfolio['kpis'][1]['by_currency'], [{'currency': 'USD', 'amount': '205.00'}])
        self.assertEqual(portfolio['health']['counts']['high'], 205)
        self.assertEqual(portfolio['action_count'], 410)
        self.assertEqual(portfolio['actions_returned'], 50)
        self.assertTrue(portfolio['actions_truncated'])

    def test_portfolio_errors_do_not_create_clear_projects_or_zero_milestones(self):
        self.grant('executive_dashboard', 'project_control')
        Project.objects.create(code='PF-ERROR', name='Available register', owner=self.user, status='active')
        with self.assertLogs('apps.dashboard', level='ERROR'):
            with patch('apps.dashboard.executive._build_projects', side_effect=RuntimeError('private governance failure')):
                with patch('apps.dashboard.portfolio_performance._milestones', side_effect=RuntimeError('private milestone failure')):
                    report = self.response().data
        portfolio = report['portfolio_performance']
        self.assertEqual(portfolio['register']['status'], 'available')
        self.assertEqual(portfolio['health']['status'], 'error')
        self.assertEqual(portfolio['health']['counts']['unknown'], 1)
        self.assertEqual(portfolio['health']['counts']['clear'], 0)
        self.assertEqual(portfolio['register']['projects'][0]['health'], 'unknown')
        self.assertIsNone(portfolio['register']['projects'][0]['exception_count'])
        self.assertEqual(portfolio['milestones']['status'], 'error')
        self.assertIsNone(portfolio['milestones']['total_rows'])
        self.assertIsNone(portfolio['milestones']['metrics'][0]['value'])
        self.assertIsNone(portfolio['action_count'])
        self.assertEqual(portfolio['actions_status'], 'error')
        self.assertEqual(len(report['financial_performance']['kpis']), 5)
        self.assertNotIn('private governance failure', str(report))
        self.assertNotIn('private milestone failure', str(report))

    def test_portfolio_empty_register_is_observed_zero_with_no_currency(self):
        self.grant('executive_dashboard', 'project_control')
        portfolio = self.response().data['portfolio_performance']
        self.assertEqual(portfolio['status'], 'available')
        self.assertEqual(portfolio['register']['total_rows'], 0)
        self.assertEqual(portfolio['kpis'][0]['value'], 0)
        self.assertEqual(portfolio['kpis'][1]['by_currency'], [])
        self.assertEqual(portfolio['health']['counts']['total'], 0)
        self.assertEqual(portfolio['milestones']['total_rows'], 0)
        self.assertEqual(portfolio['milestones']['metrics'][0]['value'], 0)
        self.assertEqual(portfolio['concentration']['by_currency'], [])

    def test_portfolio_source_deny_does_not_query_project_or_milestone_registers(self):
        from apps.dashboard.portfolio_performance import build_portfolio_performance
        context = {'allowed_modules': set(), 'generated_at': timezone.now()}
        with CaptureQueriesContext(connection) as queries:
            portfolio = build_portfolio_performance(self.user, context, {'status': 'restricted'})
        self.assertEqual(len(queries), 0)
        self.assertEqual(portfolio['register']['status'], 'restricted')
        self.assertIsNone(portfolio['kpis'][0]['value'])
        self.assertIsNone(portfolio['kpis'][1]['route'])

    def _commercial_deal(self, code, *, stage='proposal', currency='USD', amount=100,
                         owner=None, client=None, **fields):
        if client is None:
            client, _ = Client.objects.get_or_create(client_code='CM-CLIENT', defaults={
                'company_name': 'Recorded client', 'account_manager': self.user,
            })
        return Deal.objects.create(deal_code=code, deal_name=code, stage=stage, currency=currency,
                                   estimated_value=Decimal(str(amount)), expected_close_date=timezone.localdate(),
                                   owner=owner or self.user, client=client, **fields)

    def test_commercial_scope_currency_and_probability_are_recorded_not_predicted(self):
        self.grant('executive_dashboard', 'sales_opportunities')
        self._commercial_deal('CM-LEAD', stage='lead', currency='USD', amount=100)
        self._commercial_deal('CM-QUAL', stage='qualified', currency='usd', amount=200)
        self._commercial_deal('CM-EUR', stage='lead', currency='EUR', amount=0)
        self._commercial_deal('CM-UNKNOWN', stage='qualified', currency='', amount=999)
        self._commercial_deal('CM-CLOSED', stage='awarded', currency='USD', amount=9999)
        other = get_user_model().objects.create_user('cm-other', email='cm-other@example.test')
        self._commercial_deal('CM-HIDDEN', owner=other, stage='qualified', currency='GBP', amount=8888)
        commercial = self.response().data['commercial_performance']
        self.assertEqual(commercial['status'], 'available')
        self.assertEqual(commercial['currencies'], ['EUR', 'UNSPECIFIED', 'USD'])
        self.assertEqual(commercial['register']['total_rows'], 4)
        self.assertEqual(commercial['register']['total_rows_by_currency'], {'USD': 2, 'EUR': 1, 'UNSPECIFIED': 1})
        metrics = {row['id']: row for row in commercial['kpis']}
        self.assertEqual(metrics['qualified_pipeline']['status'], 'partial')
        self.assertEqual(metrics['qualified_pipeline']['incomplete_currencies'], ['UNSPECIFIED'])
        self.assertEqual(metrics['qualified_pipeline']['by_currency'], [
            {'currency': 'EUR', 'amount': '0.00'}, {'currency': 'USD', 'amount': '200.00'},
        ])
        self.assertEqual(metrics['weighted_pipeline']['by_currency'], [
            {'currency': 'EUR', 'amount': '0.00'}, {'currency': 'USD', 'amount': '60.00'},
        ])
        self.assertIsNone(metrics['framework_backlog']['value'])
        qualified = next(row for row in commercial['register']['opportunities'] if row['code'] == 'CM-QUAL')
        self.assertEqual(qualified['probability'], 25)
        self.assertIn('recorded stage', qualified['probability_basis'])
        self.assertIsNone(qualified['probability_evidence'])
        self.assertIsNone(qualified['expected_award_date'])
        self.assertIsNone(qualified['bid_owner'])
        self.assertIsNone(qualified['submission_status'])
        self.assertIsNone(qualified['business_unit'])
        self.assertNotIn('CM-HIDDEN', str(commercial))
        self.assertTrue(all(row['impact'] is None for row in commercial['actions']))
        self.assertEqual(commercial['pipeline_outlook']['series'], [])
        self.assertEqual(commercial['pipeline_movement']['series'], [])
        self.assertEqual(commercial['resource_demand']['status'], 'unavailable')

    def test_commercial_calendar_uses_proposal_target_dates_not_submission_readiness(self):
        self.grant('executive_dashboard', 'sales_opportunities')
        today = timezone.localdate()
        for index, days in enumerate([-1, 0, 30, 31]):
            self._commercial_deal(f'CM-DATE-{index}', submission_due_date=today + timedelta(days=days),
                                  currency='EUR' if days == 0 else 'USD')
        self._commercial_deal('CM-DATE-QUAL', stage='qualified', submission_due_date=today)
        self._commercial_deal('CM-DATE-CLOSED', stage='awarded', submission_due_date=today)
        self._commercial_deal('CM-DATE-MISSING')
        commercial = self.response().data['commercial_performance']
        calendar = commercial['bid_calendar']
        self.assertEqual(calendar['total_rows'], 3)
        self.assertEqual(calendar['total_rows_by_currency'], {'USD': 2, 'EUR': 1})
        self.assertEqual([row['status'] for row in calendar['rows']], ['past_target_date', 'due_today', 'upcoming'])
        self.assertTrue(all(row['readiness'] is None and row['bid_owner'] is None for row in calendar['rows']))
        self.assertTrue(all(row['owner'] == self.user.email for row in calendar['rows']))
        self.assertTrue(all('/sales/opportunities?record=' in row['route'] for row in calendar['rows']))
        self.assertEqual(next(row for row in commercial['kpis'] if row['id'] == 'proposals_due_30d')['value'], 2)
        quality = {row['id']: row for row in commercial['commercial_quality']['metrics']}
        self.assertEqual(quality['missing_submission_date']['value'], 1)
        passed = next(row for row in commercial['actions'] if 'submission_target_passed' in row['id'])
        self.assertIn('does not establish whether', passed['detail'])

    def test_commercial_win_rate_uses_dated_closed_cohort_and_preserves_zero(self):
        self.grant('executive_dashboard', 'sales_opportunities')
        today = timezone.localdate()
        for code, stage, days in [
            ('WIN', 'awarded', 0), ('CONVERTED', 'converted', 364), ('LOSS', 'lost', 10),
            ('OLD', 'awarded', 365), ('NOBID', 'no_bid', 0), ('CANCEL', 'cancelled', 0),
        ]:
            self._commercial_deal(f'CM-RATE-{code}', stage=stage, actual_close_date=today - timedelta(days=days))
        commercial = self.response().data['commercial_performance']
        rate = next(row for row in commercial['kpis'] if row['id'] == 'win_rate')
        self.assertEqual(rate['status'], 'available')
        self.assertEqual(rate['value'], 66.67)
        self.assertEqual((rate['numerator'], rate['denominator']), (2, 3))
        self.assertEqual(rate['period_start'], (today - timedelta(days=364)).isoformat())
        self.assertEqual(rate['period_end'], today.isoformat())
        Deal.objects.filter(stage__in=['awarded', 'converted']).update(actual_close_date=today - timedelta(days=400))
        rate = next(row for row in self.response().data['commercial_performance']['kpis'] if row['id'] == 'win_rate')
        self.assertEqual(rate['status'], 'available')
        self.assertEqual(rate['value'], 0)
        Deal.objects.filter(stage='lost').update(actual_close_date=None)
        rate = next(row for row in self.response().data['commercial_performance']['kpis'] if row['id'] == 'win_rate')
        self.assertEqual(rate['status'], 'unavailable')
        self.assertIsNone(rate['value'])
        self.assertEqual(rate['missing_close_date_count'], 1)
        Deal.objects.filter(stage='lost').update(actual_close_date=today + timedelta(days=1))
        rate = next(row for row in self.response().data['commercial_performance']['kpis'] if row['id'] == 'win_rate')
        self.assertEqual(rate['future_close_date_count'], 1)
        self.assertIsNone(rate['value'])

    def test_commercial_inconsistent_weighting_withholds_affected_currency_only(self):
        self.grant('executive_dashboard', 'sales_opportunities')
        bad = self._commercial_deal('CM-WEIGHT-BAD', currency='AED', amount=100)
        Deal.objects.filter(pk=bad.pk).update(probability=125)
        mismatch = self._commercial_deal('CM-WEIGHT-MISMATCH', currency='EUR', amount=100)
        Deal.objects.filter(pk=mismatch.pk).update(weighted_value=88)
        self._commercial_deal('CM-WEIGHT-GOOD', currency='USD', amount=100)
        commercial = self.response().data['commercial_performance']
        weighted = next(row for row in commercial['kpis'] if row['id'] == 'weighted_pipeline')
        self.assertEqual(weighted['status'], 'partial')
        self.assertEqual(weighted['incomplete_currencies'], ['AED', 'EUR'])
        self.assertEqual(weighted['by_currency'], [{'currency': 'USD', 'amount': '50.00'}])
        quality = {row['id']: row['value'] for row in commercial['commercial_quality']['metrics']}
        self.assertEqual(quality['invalid_probability'], 1)
        self.assertEqual(quality['weighted_value_mismatches'], 1)
        bad_row = next(row for row in commercial['register']['opportunities'] if row['code'] == 'CM-WEIGHT-BAD')
        self.assertIsNone(bad_row['probability'])
        # Protect against a future nullable import field without changing schema.
        from apps.dashboard.commercial_performance import _amount_groups
        source = [SimpleNamespace(currency='USD', estimated_value=Decimal('100')),
                  SimpleNamespace(currency='usd', estimated_value=None),
                  SimpleNamespace(currency='EUR', estimated_value=Decimal('0'))]
        groups = {row['currency']: row for row in _amount_groups(source, 'estimated_value')}
        self.assertIsNone(groups['USD']['total'])
        self.assertEqual(groups['USD']['missing_value_count'], 1)
        self.assertEqual(groups['EUR']['total'], '0.00')

    def test_commercial_client_concentration_uses_identity_and_complete_denominators(self):
        self.grant('executive_dashboard', 'sales_opportunities')
        a = Client.objects.create(client_code='CM-A', company_name='Same name', account_manager=self.user)
        b = Client.objects.create(client_code='CM-B', company_name='Same name', account_manager=self.user)
        unnamed = Client.objects.create(client_code='CM-UNNAMED', company_name='', account_manager=self.user)
        self._commercial_deal('CM-CON-A', client=a, currency='USD', amount=100)
        self._commercial_deal('CM-CON-B', client=b, currency='usd', amount=300)
        self._commercial_deal('CM-CON-ZERO', client=a, currency='EUR', amount=0)
        self._commercial_deal('CM-CON-UNNAMED', client=unnamed, currency='GBP', amount=100)
        self._commercial_deal('CM-CON-UNKNOWN', client=a, currency='', amount=1000)
        rows = {row['currency']: row for row in self.response().data['commercial_performance']['client_concentration']['by_currency']}
        self.assertEqual(len(rows['USD']['clients']), 2)
        self.assertEqual(rows['USD']['total'], '400.00')
        self.assertEqual(rows['USD']['top_client_share'], 75)
        self.assertEqual(rows['EUR']['total'], '0.00')
        self.assertIsNone(rows['EUR']['top_client_share'])
        self.assertIsNone(rows['GBP']['top_client_share'])
        self.assertIsNone(rows['UNSPECIFIED']['total'])
        self.assertTrue(all(row['amount'] is None and row['share_pct'] is None for row in rows['UNSPECIFIED']['clients']))

    def test_commercial_caps_keep_full_currency_counts_and_pipeline_totals(self):
        self.grant('executive_dashboard', 'sales_opportunities')
        client = Client.objects.create(client_code='CM-CAP', company_name='Client', account_manager=self.user)
        today = timezone.localdate()
        Deal.objects.bulk_create([
            Deal(deal_code=f'CM-CAP-{index:03}', deal_name=f'Opportunity {index}', client=client, owner=self.user,
                 stage='proposal', estimated_value=Decimal('100'), weighted_value=Decimal('50'), probability=50,
                 currency='USD' if index < 200 else 'EUR', expected_close_date=today, submission_due_date=today)
            for index in range(205)
        ])
        commercial = self.response().data['commercial_performance']
        self.assertEqual(commercial['currencies'], ['EUR', 'USD'])
        self.assertEqual(commercial['register']['total_rows_by_currency'], {'USD': 200, 'EUR': 5})
        self.assertEqual(commercial['register']['returned_rows'], 200)
        self.assertTrue(commercial['register']['truncated'])
        self.assertEqual(commercial['bid_calendar']['total_rows_by_currency'], {'USD': 200, 'EUR': 5})
        self.assertEqual(commercial['bid_calendar']['returned_rows'], 50)
        self.assertTrue(commercial['bid_calendar']['truncated'])
        self.assertEqual(commercial['actions_returned'], 50)
        self.assertTrue(commercial['actions_truncated'])
        self.assertEqual(commercial['action_count'], 410)
        self.assertEqual(commercial['kpis'][0]['by_currency'], [
            {'currency': 'EUR', 'amount': '500.00'}, {'currency': 'USD', 'amount': '20000.00'},
        ])

    def test_commercial_source_and_handoff_grants_are_separate_and_deny_wins(self):
        self.grant('executive_dashboard', 'sales_opportunities')
        self._commercial_deal('CM-HANDOFF', stage='awarded', award_status='approved')
        handoff = self.response().data['commercial_performance']['won_handoff']
        self.assertEqual(handoff['count'], 1)
        self.assertIsNone(handoff['route'])
        self.grant('sales_handovers')
        self.assertEqual(self.response().data['commercial_performance']['won_handoff']['route'], '/sales/project-handovers')
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        permission = Permission.objects.get(module__code='sales_handovers', action='read')
        UserPermissionOverride.objects.create(user_profile=self.profile, permission=permission, allowed=False)
        self.assertIsNone(self.response().data['commercial_performance']['won_handoff']['route'])
        permission = Permission.objects.get(module__code='sales_opportunities', action='read')
        UserPermissionOverride.objects.create(user_profile=self.profile, permission=permission, allowed=False)
        with patch('apps.dashboard.commercial_performance._read_deals', side_effect=AssertionError('Must not query denied source')):
            commercial = self.response().data['commercial_performance']
        self.assertEqual(commercial['status'], 'restricted')
        self.assertEqual(commercial['register']['opportunities'], [])
        self.assertIsNone(commercial['won_handoff']['count'])

    def test_commercial_empty_source_and_failed_source_are_distinct(self):
        self.grant('executive_dashboard', 'sales_opportunities')
        empty = self.response().data['commercial_performance']
        self.assertEqual(empty['register']['total_rows'], 0)
        self.assertEqual(empty['register']['total_rows_by_currency'], {})
        self.assertEqual(empty['kpis'][0]['by_currency'], [])
        self.assertEqual(empty['kpis'][3]['value'], 0)
        self.assertEqual(empty['commercial_quality']['metrics'][0]['value'], 0)
        self.assertIsNone(empty['kpis'][2]['value'])  # No outcomes means undefined ratio, not 0%.
        with self.assertLogs('apps.dashboard.commercial_performance', level='ERROR'):
            with patch('apps.dashboard.commercial_performance._read_deals', side_effect=RuntimeError('private CRM failure')):
                report = self.response().data
        failed = report['commercial_performance']
        self.assertEqual(failed['status'], 'error')
        self.assertIsNone(failed['register']['total_rows'])
        self.assertIsNone(failed['kpis'][3]['value'])
        self.assertIsNone(failed['commercial_quality']['metrics'][0]['value'])
        self.assertEqual(len(report['financial_performance']['kpis']), 5)
        self.assertEqual(len(report['portfolio_performance']['kpis']), 5)
        self.assertNotIn('private CRM failure', str(report))

    def _workforce_employee(self, code, **fields):
        from apps.hr_core.models import EmployeeMaster
        defaults = {'first_name': 'ConfidentialGivenName', 'last_name': 'ConfidentialFamilyName',
                    'employment_status': 'active', 'join_date': timezone.localdate() - timedelta(days=100)}
        defaults.update(fields)
        return EmployeeMaster.objects.create(employee_number=code, employee_code=code, emp_code=code, **defaults)

    def test_workforce_current_headcount_matches_canonical_rules_and_contains_only_aggregates(self):
        self.grant('executive_dashboard', 'hr_management')
        today = timezone.localdate()
        for code, fields in [
            ('WF-ACT', {'department': 'Engineering', 'office': 'Abu Dhabi', 'business_unit': 'Energy', 'branch': 'RAD'}),
            ('WF-NOTICE', {'department': ' Engineering ', 'office': 'Abu Dhabi', 'business_unit': 'Energy',
                           'branch': 'RAD', 'employment_status': 'notice_period', 'protected_identity': True,
                           'current_base_salary': Decimal('4321987.12')}),
            ('WF-LASTDAY', {'department': 'Engineering', 'office': 'Abu Dhabi', 'business_unit': 'Energy',
                            'branch': 'RAD', 'exit_date': today}),
            ('WF-PROB', {'department': 'Finance', 'office': 'Mumbai', 'business_unit': 'Corporate',
                         'branch': 'RIN', 'employment_status': 'probation'}),
            ('WF-SUSP', {'department': ' \t ', 'office': ' ', 'business_unit': '', 'employment_status': 'suspended'}),
            ('WF-FUTURE', {'join_date': today + timedelta(days=1)}),
            ('WF-EXITED', {'exit_date': today - timedelta(days=1)}),
            ('WF-STATUS', {'employment_status': 'exited'}),
            ('WF-TEST', {'is_test_person': True}),
        ]:
            self._workforce_employee(code, **fields)
        report = self.response().data
        workforce = report['workforce_performance']
        self.assertEqual(workforce['status'], 'available')
        self.assertTrue(workforce['scope']['aggregate_only'])
        self.assertEqual(workforce['kpis'][0]['value'], 5)
        previous_hr = next(section for section in report['departments'] if section['id'] == 'hr')
        self.assertEqual(workforce['kpis'][0]['value'], previous_hr['metrics'][0]['value'])
        self.assertEqual({row['department']: row['headcount'] for row in workforce['capacity_plan']['rows']},
                         {'Engineering': 3, 'Finance': 1, 'Unassigned': 1})
        for dimension in ['office', 'branch', 'business_unit']:
            distribution = workforce['distribution'][dimension]
            self.assertEqual(distribution['total'], 5)
            self.assertEqual(sum(row['count'] for row in distribution['rows']), 5)
        self.assertEqual(workforce['retention_mobility']['metrics'][0]['value'], 1)
        self.assertEqual(workforce['distribution']['employment_type']['status'], 'unavailable')
        self.assertIsNone(workforce['distribution']['employment_type']['total'])
        self.assertTrue(all(row['value'] is None and row['status'] == 'unavailable' for row in workforce['kpis'][1:]))
        for row in workforce['capacity_plan']['rows']:
            self.assertTrue(all(row[key] is None for key in ['fte', 'billable_utilisation', 'committed_demand',
                                                            'weighted_demand', 'capacity_gap', 'critical_roles', 'owner']))
            self.assertEqual(row['health'], 'unavailable')
        for secret in ['ConfidentialGivenName', 'ConfidentialFamilyName', 'WF-NOTICE', '4321987.12', 'protected_identity']:
            self.assertNotIn(secret, str(workforce))
        self.assertEqual(workforce['critical_roles']['rows'], [])
        self.assertEqual(workforce['project_coverage_risk']['status'], 'unavailable')

    def test_workforce_movement_uses_recorded_dates_exact_boundaries_not_headcount_history(self):
        from apps.dashboard.workforce_performance import build_workforce_performance
        as_of = date(2026, 1, 15)
        records = [
            ('WF-MONTH-START', date(2025, 8, 1), None),
            ('WF-BEFORE', date(2025, 7, 31), None),
            ('WF-30-START', date(2025, 12, 17), None),
            ('WF-30-BEFORE', date(2025, 12, 16), None),
            ('WF-TODAY', as_of, None),
            ('WF-FUTURE-DATE', date(2026, 1, 16), None),
            ('WF-LEAVER', date(2025, 8, 5), date(2025, 12, 17)),
            ('WF-EXIT-TODAY', date(2025, 7, 1), as_of),
            ('WF-INVALID', date(2026, 1, 10), date(2026, 1, 9)),
        ]
        for code, joined, exited in records:
            self._workforce_employee(code, join_date=joined, exit_date=exited)
        self._workforce_employee('WF-TEST-MOVE', join_date=as_of, exit_date=as_of, is_test_person=True)
        context = {'allowed_modules': {'hr_management'}, 'generated_at': timezone.make_aware(datetime(2026, 1, 15, 12))}
        report = build_workforce_performance(self.user, context)
        movement = report['workforce_movement']
        self.assertEqual(movement['period_start'], '2025-08-01')
        self.assertEqual(movement['period_end'], '2026-01-15')
        self.assertEqual([row['month'] for row in movement['series']],
                         ['2025-08-01', '2025-09-01', '2025-10-01', '2025-11-01', '2025-12-01', '2026-01-01'])
        months = {row['month']: row for row in movement['series']}
        self.assertEqual(months['2025-08-01']['joiners'], 2)
        self.assertEqual(months['2025-09-01']['joiners'], 0)
        self.assertEqual(months['2025-12-01']['joiners'], 2)
        self.assertEqual(months['2025-12-01']['exits'], 1)
        self.assertEqual(months['2026-01-01']['joiners'], 2)
        self.assertEqual(months['2026-01-01']['exits'], 1)
        metrics = {row['id']: row for row in movement['metrics']}
        self.assertEqual(metrics['joiners_30d']['value'], 3)
        self.assertEqual(metrics['recorded_exits_30d']['value'], 2)
        self.assertEqual(metrics['joiners_30d']['period_start'], '2025-12-17')
        quality = {row['id']: row for row in report['data_quality']['metrics']}
        self.assertEqual(quality['invalid_lifecycle_dates']['value'], 1)
        self.assertIsNone(next(row for row in report['kpis'] if row['id'] == 'voluntary_turnover')['value'])
        self.assertEqual(report['supply_demand_outlook']['series'], [])

    def test_workforce_empty_source_unknown_but_observed_no_current_people_is_zero(self):
        self.grant('executive_dashboard', 'hr_management')
        self._workforce_employee('WF-ONLY-TEST', is_test_person=True)
        empty = self.response().data['workforce_performance']
        self.assertEqual(empty['status'], 'unavailable')
        self.assertIsNone(empty['kpis'][0]['value'])
        self.assertIsNone(empty['distribution']['office']['total'])
        self.assertEqual(empty['workforce_movement']['series'], [])
        self._workforce_employee('WF-FORMER', employment_status='exited', exit_date=timezone.localdate() - timedelta(days=1))
        observed = self.response().data['workforce_performance']
        self.assertEqual(observed['status'], 'available')
        self.assertEqual(observed['kpis'][0]['value'], 0)
        self.assertEqual(observed['capacity_plan']['rows'], [])
        self.assertEqual(observed['capacity_plan']['total_rows'], 0)
        self.assertEqual(observed['distribution']['office']['rows'], [])
        self.assertEqual(observed['distribution']['office']['total'], 0)
        self.assertEqual(observed['retention_mobility']['metrics'][0]['value'], 0)
        self.assertEqual(len(observed['workforce_movement']['series']), 6)

    def test_workforce_aggregations_do_not_read_identity_salary_or_talent_fields(self):
        from apps.dashboard.workforce_performance import build_workforce_performance
        self._workforce_employee('WF-PRIVATE', department='Unassigned', office=' \t ', business_unit='Unit',
                                 current_base_salary=Decimal('7654321.98'), engineer_profile={'private_notes': 'Protected detail'})
        context = {'allowed_modules': {'hr_management'}, 'generated_at': timezone.now()}
        with CaptureQueriesContext(connection) as queries:
            report = build_workforce_performance(self.user, context)
        statements = '\n'.join(row['sql'] for row in queries).lower()
        for field in ['first_name', 'last_name', 'employee_number', 'employee_code', 'email', 'current_base_salary',
                      'bank_account_number', 'protected_identity', 'engineer_profile', 'hr_talent_assessment']:
            self.assertNotIn(field, statements)
        self.assertNotIn('7654321.98', str(report))
        self.assertNotIn('Protected detail', str(report))
        quality = {row['id']: row['value'] for row in report['data_quality']['metrics']}
        self.assertEqual(quality['missing_department'], 0)  # Literal label is not a missing field.
        self.assertEqual(quality['missing_office'], 1)
        self.assertEqual(report['actions'][0]['owner'], 'HR')
        self.assertTrue(all(row['impact'] is None for row in report['actions']))

    def test_workforce_aggregate_caps_do_not_cap_headcount_or_distribution_totals(self):
        from apps.hr_core.models import EmployeeMaster
        self.grant('executive_dashboard', 'hr_management')
        EmployeeMaster.objects.bulk_create([
            EmployeeMaster(employee_number=f'WF-CAP-{index}', employee_code=f'WF-CAP-{index}', emp_code=f'WF-CAP-{index}',
                           first_name='Private', last_name='Worker', join_date=timezone.localdate(),
                           department=f'Department {index:03}', office='Office', business_unit='Unit', branch='RAD')
            for index in range(205)
        ])
        report = self.response().data['workforce_performance']
        self.assertEqual(report['kpis'][0]['value'], 205)
        self.assertEqual(report['capacity_plan']['total_rows'], 205)
        self.assertEqual(report['capacity_plan']['returned_rows'], 200)
        self.assertTrue(report['capacity_plan']['truncated'])
        self.assertEqual(report['distribution']['office']['total'], 205)
        self.assertEqual(report['distribution']['office']['rows'], [{'label': 'Office', 'count': 205}])
        self.assertEqual(report['action_count'], 0)
        self.assertNotIn('Private', str(report))

    def test_workforce_source_deny_prevents_aggregate_reads_even_for_superuser(self):
        self.grant('executive_dashboard', 'hr_management')
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        permission = Permission.objects.get(module__code='hr_management', action='read')
        UserPermissionOverride.objects.create(user_profile=self.profile, permission=permission, allowed=False)
        with patch('apps.dashboard.workforce_performance._read_workforce', side_effect=AssertionError('Denied source was queried')):
            report = self.response().data['workforce_performance']
        self.assertEqual(report['status'], 'restricted')
        self.assertIsNone(report['kpis'][0]['value'])
        self.assertIsNone(report['kpis'][0]['route'])
        self.assertEqual(report['capacity_plan']['rows'], [])
        self.assertEqual(report['distribution']['office']['rows'], [])
        self.assertIsNone(report['retention_mobility']['metrics'][0]['value'])
        self.assertEqual(report['actions'], [])

    def test_workforce_source_failure_preserves_other_tabs_and_unknown_not_zero(self):
        self.grant('executive_dashboard', 'hr_management')
        self._workforce_employee('WF-ERROR-SOURCE')
        with self.assertLogs('apps.dashboard.workforce_performance', level='ERROR'):
            with patch('apps.dashboard.workforce_performance._read_workforce', side_effect=RuntimeError('private personnel problem')):
                report = self.response().data
        workforce = report['workforce_performance']
        self.assertEqual(workforce['status'], 'error')
        self.assertEqual(workforce['actions_status'], 'error')
        self.assertIsNone(workforce['action_count'])
        self.assertIsNone(workforce['capacity_plan']['total_rows'])
        self.assertIsNone(workforce['distribution']['branch']['total'])
        self.assertIsNone(workforce['workforce_movement']['metrics'][0]['value'])
        self.assertIsNone(workforce['data_quality']['metrics'][0]['value'])
        self.assertEqual(report['workforce']['status'], 'available')
        self.assertEqual(len(report['commercial_performance']['kpis']), 5)
        self.assertEqual(len(report['portfolio_performance']['kpis']), 5)
        self.assertEqual(len(report['financial_performance']['kpis']), 5)
        self.assertNotIn('private personnel problem', str(report))

    def _risk_project(self, number=1, **fields):
        from apps.qhse.models import QHSERunningProject
        defaults = {'project_title': 'Recorded assurance project', 'client': 'Recorded client',
                    'project_manager': 'Recorded project manager'}
        defaults.update(fields)
        return QHSERunningProject.objects.create(sr_no=number, project_no=f'RISK-{number:03}', **defaults)

    def _risk_audit(self, project, number=1, **fields):
        from apps.qhse.models import QHSEAudit
        defaults = {'audit_type': 'PROJECT', 'audit_date': timezone.localdate(),
                    'auditor': 'Recorded auditor', 'status': 'SCHEDULED'}
        defaults.update(fields)
        return QHSEAudit.objects.create(project=project, audit_number=number, **defaults)

    def test_risk_project_counters_are_not_enterprise_risks_or_safety_events(self):
        self.grant('executive_dashboard', 'qhse_quality')
        project = self._risk_project(cars_open=8, cars_delayed_closing_no_days=12,
                                     obs_open=4, obs_delayed_closing_no_days=3, delay_in_audits_no_days=5,
                                     project_audit_1=timezone.localdate() - timedelta(days=10))
        self._risk_project(2, cars_open=2, cars_delayed_closing_no_days=0,
                           obs_open=0, obs_delayed_closing_no_days=20)
        self._risk_project(3, is_active=False, cars_open=900, cars_delayed_closing_no_days=9)
        self._risk_audit(project, findings='Private narrative with 9 open findings', status='COMPLETED')
        response = self.response().data
        risk = response['risk_compliance']
        self.assertEqual(risk['status'], 'available')
        self.assertTrue(all(item['value'] is None and item['status'] == 'unavailable' for item in risk['kpis']))
        self.assertTrue(all(item['value'] is None and item['status'] == 'unavailable'
                            for item in risk['qhse_performance']['metrics']))
        counts = {item['id']: item['value'] for item in risk['qhse_performance']['project_metrics']}
        self.assertEqual(counts, {'open_cars': 10, 'delayed_car_projects': 1, 'open_observations': 4,
                                  'delayed_observation_projects': 1, 'delayed_audit_projects': 1})
        self.assertEqual(risk['register']['risks'], [])
        self.assertIsNone(risk['register']['total_rows'])
        self.assertEqual(risk['source_followups']['total_rows'], 3)
        self.assertTrue(all(row['due_date'] is None and row['owner_label'] == 'Project manager'
                            for row in risk['source_followups']['rows']))
        self.assertEqual(risk['compliance_calendar']['total_rows'], 0)  # Raw project audit dates are not audits.
        self.assertEqual(risk['risk_concentration']['by_currency'], [])
        self.assertEqual(risk['heatmap']['cells'], [])
        self.assertNotIn('Private narrative', str(risk))
        # The new area grant must not silently broaden the existing Overview QHSE branch.
        self.assertEqual(next(row for row in response['departments'] if row['id'] == 'qhse')['status'], 'restricted')

    def test_risk_audit_calendar_respects_dates_states_and_recorded_roles(self):
        from apps.dashboard.risk_compliance import build_risk_compliance
        as_of = date(2026, 9, 14)
        project = self._risk_project()
        cases = [(1, -1, 'SCHEDULED'), (2, 0, 'SCHEDULED'), (3, 30, 'SCHEDULED'),
                 (4, 31, 'SCHEDULED'), (5, -8, 'COMPLETED'), (6, -9, 'CANCELLED'),
                 (7, 40, 'DELAYED')]
        for number, offset, status in cases:
            self._risk_audit(project, number, audit_date=as_of + timedelta(days=offset), status=status)
        inactive = self._risk_project(2, is_active=False)
        self._risk_audit(inactive, audit_date=as_of - timedelta(days=1))
        context = {'allowed_modules': {'qhse_quality'}, 'generated_at': timezone.make_aware(datetime(2026, 9, 14, 12))}
        risk = build_risk_compliance(self.user, context)
        calendar = risk['compliance_calendar']
        self.assertEqual(calendar['total_rows'], 3)
        self.assertEqual([row['date_status'] for row in calendar['rows']], ['past_target_date', 'due_today', 'upcoming'])
        self.assertTrue(all(row['owner'] is None and row['recorded_auditor'] == 'Recorded auditor'
                            and row['route'] == '/qhse/general/quality' for row in calendar['rows']))
        self.assertEqual(calendar['period_end'], '2026-10-14')
        counts = {item['id']: item['value'] for item in risk['audit_controls']['metrics']}
        self.assertEqual(counts['scheduled_audits'], 4)
        self.assertEqual(counts['delayed_audits'], 1)
        self.assertEqual(counts['completed_audits'], 1)
        self.assertIsNone(counts['open_audit_findings'])
        self.assertEqual(risk['action_count'], 2)  # Past scheduled target plus recorded delayed audit.
        self.assertTrue(all(row['due_date'] is None and row['impact'] is None and row['owner'] == 'QHSE'
                            and row['source_target_date'] is not None for row in risk['actions']))
        self.assertTrue(all(row['owner_label'] == 'Auditor' for row in risk['source_followups']['rows']))

    def test_risk_empty_audit_source_is_zero_but_absent_project_coverage_is_unknown(self):
        self.grant('executive_dashboard', 'qhse_quality')
        risk = self.response().data['risk_compliance']
        self.assertEqual(risk['status'], 'partial')
        self.assertFalse(risk['coverage_complete'])
        self.assertEqual(risk['qhse_performance']['status'], 'unavailable')
        self.assertTrue(all(row['value'] is None for row in risk['qhse_performance']['project_metrics']))
        self.assertEqual(risk['audit_controls']['status'], 'available')
        self.assertEqual([row['value'] for row in risk['audit_controls']['metrics'][:3]], [0, 0, 0])
        self.assertEqual(risk['compliance_calendar']['total_rows'], 0)
        self.assertEqual(risk['source_followups']['total_rows'], 0)
        self.assertEqual(risk['action_count'], 0)
        self._risk_project()
        risk = self.response().data['risk_compliance']
        self.assertEqual(risk['status'], 'available')
        self.assertTrue(risk['coverage_complete'])
        self.assertTrue(all(row['value'] == 0 for row in risk['qhse_performance']['project_metrics']))

    def test_risk_sources_need_separate_reads_and_quality_deny_wins_for_superuser(self):
        self.grant('executive_dashboard')
        with patch('apps.dashboard.risk_compliance.apps.get_model', side_effect=AssertionError('Denied source read')):
            from apps.dashboard.risk_compliance import build_risk_compliance
            risk = build_risk_compliance(self.user, {'allowed_modules': set(), 'generated_at': timezone.now()})
        self.assertEqual(risk['status'], 'restricted')
        self.assertIsNone(risk['action_count'])
        self.assertIsNone(risk['source_followups']['total_rows'])
        self.assertTrue(all(row['route'] is None for row in risk['sources']))
        self._risk_project(cars_open=1)
        self.grant('qhse')
        risk = self.response().data['risk_compliance']
        self.assertEqual(risk['qhse_performance']['status'], 'available')
        self.assertEqual(risk['qhse_performance']['route'], '/qhse')
        self.assertEqual(risk['audit_controls']['status'], 'restricted')
        self.assertIsNone(risk['compliance_calendar']['route'])
        self.grant('qhse_quality')
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        permission = Permission.objects.get(module__code='qhse_quality', action='read')
        UserPermissionOverride.objects.create(user_profile=self.profile, permission=permission, allowed=False)
        with patch('apps.dashboard.risk_compliance._audits', side_effect=AssertionError('Denied audit source')):
            risk = self.response().data['risk_compliance']
        self.assertEqual(risk['status'], 'partial')
        self.assertEqual(risk['qhse_performance']['status'], 'available')
        self.assertEqual(risk['audit_controls']['status'], 'restricted')
        self.assertIsNone(risk['audit_controls']['route'])

    def test_risk_source_failure_preserves_independent_sources_and_prior_tabs(self):
        self.grant('executive_dashboard', 'qhse_quality')
        self._risk_project(cars_open=1, cars_delayed_closing_no_days=2)
        with self.assertLogs('apps.dashboard.risk_compliance', level='ERROR'):
            with patch('apps.dashboard.risk_compliance._audits', side_effect=RuntimeError('Private audit issue')):
                report = self.response().data
        risk = report['risk_compliance']
        self.assertEqual(risk['status'], 'partial')
        self.assertEqual(risk['qhse_performance']['project_metrics'][0]['value'], 1)
        self.assertEqual(risk['audit_controls']['status'], 'error')
        self.assertIsNone(risk['audit_controls']['metrics'][0]['value'])
        self.assertIsNone(risk['compliance_calendar']['total_rows'])
        self.assertEqual(risk['action_count'], 1)
        self.assertFalse(risk['source_followups']['coverage_complete'])
        self.assertNotIn('Private audit issue', str(report))
        for tab in ['financial_performance', 'portfolio_performance', 'commercial_performance', 'workforce_performance']:
            self.assertEqual(len(report[tab]['kpis']), 5)
        with self.assertLogs('apps.dashboard.risk_compliance', level='ERROR'):
            with patch('apps.dashboard.risk_compliance._audits', side_effect=RuntimeError('Failed')):
                with patch('apps.dashboard.risk_compliance._project_quality', side_effect=RuntimeError('Failed')):
                    failed = self.response().data['risk_compliance']
        self.assertEqual(failed['status'], 'error')
        self.assertIsNone(failed['action_count'])
        self.assertIsNone(failed['source_followups']['total_rows'])
        self.assertEqual(failed['actions'], [])

    def test_risk_invalid_project_counter_withholds_metrics_without_losing_audits(self):
        from apps.qhse.models import QHSERunningProject
        self.grant('executive_dashboard', 'qhse_quality')
        project = self._risk_project(cars_open=5)
        QHSERunningProject.objects.filter(pk=project.pk).update(cars_open=-1)
        with self.assertLogs('apps.dashboard.risk_compliance', level='ERROR'):
            risk = self.response().data['risk_compliance']
        self.assertEqual(risk['status'], 'partial')
        self.assertEqual(risk['qhse_performance']['status'], 'error')
        self.assertTrue(all(row['value'] is None for row in risk['qhse_performance']['project_metrics']))
        self.assertEqual(risk['audit_controls']['metrics'][0]['value'], 0)

    def test_risk_caps_preserve_full_source_counts(self):
        from apps.dashboard.risk_compliance import build_risk_compliance
        from apps.qhse.models import QHSEAudit, QHSERunningProject
        QHSERunningProject.objects.bulk_create([
            QHSERunningProject(sr_no=index, project_no=f'RISK-CAP-{index}', project_title='Project',
                               client='Client', project_manager='Manager', cars_open=1,
                               cars_delayed_closing_no_days=1)
            for index in range(1, 206)
        ])
        project = QHSERunningProject.objects.first()
        QHSEAudit.objects.bulk_create([
            QHSEAudit(project=project, audit_type='PROJECT', audit_number=index,
                      audit_date=timezone.localdate() - timedelta(days=1), auditor='Auditor')
            for index in range(1, 56)
        ])
        risk = build_risk_compliance(self.user, {'allowed_modules': {'qhse_quality'}, 'generated_at': timezone.now()})
        self.assertEqual(risk['source_followups']['total_rows'], 260)
        self.assertEqual(risk['source_followups']['returned_rows'], 200)
        self.assertTrue(risk['source_followups']['truncated'])
        self.assertEqual(risk['action_count'], 260)
        self.assertEqual(risk['actions_returned'], 50)
        self.assertTrue(risk['actions_truncated'])
        self.assertEqual(risk['compliance_calendar']['total_rows'], 55)
        self.assertEqual(risk['compliance_calendar']['returned_rows'], 50)
        self.assertTrue(risk['compliance_calendar']['truncated'])
        self.assertEqual(risk['qhse_performance']['project_metrics'][0]['value'], 205)

    def test_risk_does_not_query_disabled_spotchecks_or_private_narrative_fields(self):
        from apps.dashboard.risk_compliance import build_risk_compliance
        project = self._risk_project(remarks='Confidential project narrative')
        self._risk_audit(project, findings='Confidential audit narrative')
        with CaptureQueriesContext(connection) as queries:
            risk = build_risk_compliance(self.user, {'allowed_modules': {'qhse_quality'}, 'generated_at': timezone.now()})
        statements = '\n'.join(row['sql'] for row in queries).lower()
        for field in ['"findings"', '"remarks"', 'spot_check', '"project_audit_1"', '"cost_of_poor_quality_aed"']:
            self.assertNotIn(field, statements)
        self.assertNotIn('Confidential', str(risk))
        self.assertTrue(all(row['source_timestamp_kind'] == 'latest_record_update' for row in risk['sources']))
