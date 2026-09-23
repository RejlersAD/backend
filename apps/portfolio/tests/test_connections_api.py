"""Scope and source consistency at the workbook-to-operational API boundary."""
from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.project_models import Project
from apps.portfolio.executive import build_revenue_dashboard
from apps.rbac.models import Permission, UserPermissionOverride
from . import test_executive as executive_fixtures


class PortfolioConnectionsAPITests(TestCase):
    setUp = executive_fixtures.RevenueDashboardTests.setUp
    grant = executive_fixtures.RevenueDashboardTests.grant
    full_access = executive_fixtures.RevenueDashboardTests.full_access
    source = executive_fixtures.RevenueDashboardTests.source
    row = executive_fixtures.RevenueDashboardTests.row
    url = '/api/v1/dashboard/executive/portfolio-workbook/outgoing-invoices/'

    def client_for_user(self, *, full=True, finance=True):
        if full:
            self.full_access()
        else:
            self.grant('executive_dashboard', 'read')
        if finance:
            self.grant('finance_outgoing', 'read')
        client = APIClient()
        client.force_authenticate(self.user)
        return client

    @patch('apps.portfolio.recorded_invoices.build_recorded_invoices')
    def test_finance_uses_complete_filtered_scope_before_invoice_pagination(self, build):
        client = self.client_for_user()
        snapshot = self.source()
        one = self.row(snapshot)
        two = self.row(snapshot, 'P-2')
        self.row(snapshot, 'P-OTHER', pm='PM2')
        build.return_value = {'status': 'available', 'rows': [], 'total_rows': 5}
        response = client.get(self.url, {'pm': 'pm1', 'snapshot_id': snapshot.pk, 'limit': 1, 'offset': 2})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual({row['id'] for row in build.call_args.args[1]}, {one.pk, two.pk})
        self.assertEqual(build.call_args.kwargs, {'full_source': True, 'limit': 1, 'offset': 2})
        self.assertEqual(response.data['source_snapshot_id'], snapshot.pk)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(response.data['workbook_reporting_date'], snapshot.reporting_date.isoformat())

    @patch('apps.portfolio.recorded_invoices.build_recorded_invoices')
    def test_known_hidden_child_does_not_inherit_its_visible_parents_invoice_scope(self, build):
        client = self.client_for_user(full=False)
        parent = Project.objects.create(code='P', name='Visible parent')
        Project.objects.create(code='P-HIDDEN', name='Hidden child')
        snapshot = self.source()
        visible = self.row(snapshot)
        self.row(snapshot, 'P-HIDDEN')
        self.row(snapshot, 'OTHER', project_code='OTHER')
        build.return_value = {'status': 'available', 'rows': []}
        with patch('apps.project_control.access.accessible_enterprise_projects', return_value=Project.objects.filter(pk=parent.pk)):
            response = client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['id'] for row in build.call_args.args[1]], [visible.pk])
        self.assertFalse(build.call_args.kwargs['full_source'])

    @patch('apps.portfolio.recorded_invoices.build_recorded_invoices')
    def test_explicit_source_and_finance_denials_prevent_any_ledger_access(self, build):
        client = self.client_for_user()
        for module in ('executive_dashboard', 'project_control', 'finance_outgoing'):
            with self.subTest(module=module):
                permission = Permission.objects.get(module__code=module, action='read')
                override = UserPermissionOverride.objects.create(user_profile=self.profile, permission=permission, allowed=False)
                self.assertEqual(client.get(self.url).status_code, 403)
                build.assert_not_called()
                override.delete()
        client.force_authenticate(None)
        self.assertIn(client.get(self.url).status_code, (401, 403))

    @patch('apps.portfolio.recorded_invoices.build_recorded_invoices')
    def test_source_change_and_invalid_filters_do_not_run_invoice_matching(self, build):
        client = self.client_for_user()
        snapshot = self.source()
        self.row(snapshot)
        response = client.get(self.url, {'snapshot_id': snapshot.pk + 1})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'source_changed')
        for params in ({'limit': 201}, {'offset': -1}, {'snapshot_id': 'bad'}):
            self.assertEqual(client.get(self.url, params).status_code, 400)
        build.assert_not_called()

    @patch('apps.portfolio.recorded_invoices.build_recorded_invoices')
    def test_missing_workbook_is_not_a_zero_ledger_and_failures_are_retryable(self, build):
        client = self.client_for_user()
        response = client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'unavailable')
        build.assert_not_called()
        self.row(self.source())
        build.side_effect = DatabaseError('synthetic unavailable register')
        response = client.get(self.url)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(response.data['status'], 'error')

    @patch('apps.portfolio.connections.build_project_connections', side_effect=DatabaseError('synthetic connection failure'))
    def test_operational_connection_failure_keeps_saved_workbook_figures(self, build):
        self.full_access()
        self.row(self.source())
        report = build_revenue_dashboard(self.user)
        self.assertIn(report['status'], ('available', 'partial'))
        self.assertEqual(report['connections']['status'], 'error')
        self.assertEqual(next(metric for metric in report['kpis'] if metric['id'] == 'total_revenue_actual')['value'], '10.00')
