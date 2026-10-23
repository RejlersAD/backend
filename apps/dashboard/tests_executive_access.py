"""EXEC-ACCESS-01: real RBAC decisions, response filtering and endpoint gates."""
from copy import deepcopy
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from apps.dashboard.executive_access import TAB_ACTIONS, allowed_tabs, filter_executive_report
from apps.portfolio.views import PortfolioWorkbookView, PortfolioRevenueView, PortfolioOutgoingInvoicesView, PortfolioWorkbookPreviewView, PortfolioWorkbookImportView
from apps.rbac.action_policy import module_action_allowed
from apps.rbac.models import Module, Organization, Permission, Role, RoleModule, RolePermission, UserPermissionOverride, UserProfile, UserRole
from apps.rbac.module_actions import ensure_module_actions


REPORT = {
    'schema_version': '1.0', 'generated_at': '2026-10-23T00:00:00Z', 'scope': {},
    'kpis': [{'id': 'private-overview'}], 'departments': [{'id': 'finance'}],
    'actions': [{'id': 'private-action'}], 'portfolio': {'projects': [{'name': 'private-project'}]},
    'financial_performance': {'secret': 'finance'},
    'portfolio_performance': {'status': 'available', 'health': {'count': 2},
                              'workbook': {'secret': 'workbook'}, 'revenue_dashboard': {'secret': 'revenue'}},
    'commercial_performance': {'status': 'restricted'},
}


class ExecutiveTabAccessTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('tab-reader', email='tab-reader@example.test')
        org = Organization.objects.create(code='tab-access', name='Tab access')
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'organization': org})
        self.profile.roles.clear()
        self.role = Role.objects.create(code='tab-reader', name='Tab reader', level=3)
        UserRole.objects.create(user_profile=self.profile, role=self.role)
        self.module, _ = Module.objects.get_or_create(code='executive_dashboard', defaults={'name': 'Executive'})
        ensure_module_actions(Module, Permission, module_ids=[self.module.pk])
        RoleModule.objects.get_or_create(role=self.role, module=self.module)
        RolePermission.objects.get_or_create(role=self.role, permission=self.module.permissions.get(action='read'))
        self.permissions = {}
        for tab, action in TAB_ACTIONS.items():
            self.permissions[tab], _ = Permission.objects.get_or_create(
                code=f'executive_dashboard.{action}', defaults={'module': self.module, 'name': tab, 'action': action})
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def grant(self, tab):
        RolePermission.objects.get_or_create(role=self.role, permission=self.permissions[tab])

    def override(self, tab, allowed):
        return UserPermissionOverride.objects.create(user_profile=self.profile, permission=self.permissions[tab], allowed=allowed)

    def report(self, **params):
        with patch('apps.dashboard.executive_views.build_executive_dashboard', return_value=deepcopy(REPORT)):
            return self.client.get('/api/v1/dashboard/executive/', params)

    def test_broad_read_does_not_grant_new_tabs_or_leak_report_data(self):
        response = self.report()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['allowed_tabs'], ['commercial', 'workforce', 'risk'])
        self.assertEqual(response.data['departments'], [])
        self.assertEqual(response.data['actions'], [])
        self.assertNotIn('financial_performance', response.data)
        self.assertNotIn('portfolio_performance', response.data)

    def test_each_role_grant_is_independent(self):
        for tab in TAB_ACTIONS:
            with self.subTest(tab=tab):
                RolePermission.objects.filter(role=self.role, permission__in=self.permissions.values()).delete()
                self.grant(tab)
                self.assertEqual([t for t in allowed_tabs(self.user) if t in TAB_ACTIONS], [tab])

    def test_user_allow_deny_and_inherit_keep_role_membership(self):
        self.grant('overview')
        denied = self.override('overview', False)
        self.override('financial', True)
        self.assertNotIn('overview', allowed_tabs(self.user))
        self.assertIn('financial', allowed_tabs(self.user))
        denied.delete()
        self.assertIn('overview', allowed_tabs(self.user))
        self.assertTrue(self.profile.roles.filter(pk=self.role.pk).exists())

    def test_tab_deny_does_not_deny_executive_read(self):
        self.override('financial', False)
        self.assertTrue(module_action_allowed(self.user, 'executive_dashboard', 'read'))

    def test_superuser_explicit_tab_deny_wins(self):
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        self.override('financial', False)
        self.assertNotIn('financial', allowed_tabs(self.user))
        self.assertIn('overview', allowed_tabs(self.user))

    def test_inactive_role_or_permission_does_not_grant_tab(self):
        self.grant('overview')
        self.role.is_active = False
        self.role.save(update_fields=['is_active'])
        self.assertNotIn('overview', allowed_tabs(self.user))
        self.role.is_active = True
        self.role.save(update_fields=['is_active'])
        p = self.permissions['overview']
        p.is_active = False
        p.save(update_fields=['is_active'])
        self.assertNotIn('overview', allowed_tabs(self.user))

    def test_selected_tab_denial_and_invalid_tab(self):
        self.assertEqual(self.report(tab='financial').status_code, 403)
        self.assertEqual(self.report(tab='unknown').status_code, 400)
        self.grant('financial')
        self.assertEqual(self.report(tab='financial').status_code, 200)

    def test_tab_grant_cannot_replace_executive_read(self):
        self.grant('financial')
        read = self.module.permissions.get(action='read')
        UserPermissionOverride.objects.create(user_profile=self.profile, permission=read, allowed=False)
        self.assertEqual(self.report().status_code, 403)

    def test_overview_keeps_summary_but_withholds_portfolio_workbook(self):
        self.grant('overview')
        data = self.report().data
        self.assertEqual(data['kpis'], REPORT['kpis'])
        self.assertEqual(data['portfolio_performance']['health'], {'count': 2})
        self.assertNotIn('workbook', data['portfolio_performance'])
        self.assertNotIn('revenue_dashboard', data['portfolio_performance'])
        self.assertNotIn('financial_performance', data)

    def test_financial_only_does_not_expose_overview_records(self):
        self.grant('financial')
        response = self.report()
        self.assertEqual(response.data['financial_performance'], {'secret': 'finance'})
        self.assertEqual(response.data['kpis'], [])
        self.assertEqual(response.data['portfolio']['projects'], [])
        self.assertEqual(response['Cache-Control'], 'private, no-store')

    def test_shared_invoice_readers_require_overview_or_financial(self):
        for path in ('receivables', 'customer-invoices'):
            self.assertEqual(self.client.get(f'/api/v1/dashboard/executive/{path}/').status_code, 403)
        self.grant('overview')
        with patch('apps.dashboard.executive_views.build_receivables_dashboard', return_value={'status': 'restricted'}) as builder:
            self.assertEqual(self.client.get('/api/v1/dashboard/executive/receivables/').status_code, 200)
            builder.assert_called_once()

    def test_portfolio_readers_cannot_bypass_tab_deny(self):
        self.override('portfolio', False)
        for view in (PortfolioWorkbookView, PortfolioRevenueView, PortfolioOutgoingInvoicesView):
            request = APIRequestFactory().get('/api/v1/dashboard/executive/portfolio-workbook/')
            force_authenticate(request, self.user)
            self.assertEqual(view.as_view()(request).status_code, 403)
        self.grant('portfolio')
        UserPermissionOverride.objects.filter(user_profile=self.profile).delete()
        request = APIRequestFactory().get('/api/v1/dashboard/executive/portfolio-workbook/')
        force_authenticate(request, self.user)
        with patch('apps.portfolio.views.build_workbook_report', return_value={'status': 'restricted'}):
            self.assertEqual(PortfolioWorkbookView.as_view()(request).status_code, 200)

    def test_portfolio_upload_and_preview_cannot_bypass_explicit_tab_deny(self):
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        self.override('portfolio', False)
        for view in (PortfolioWorkbookPreviewView, PortfolioWorkbookImportView):
            request = APIRequestFactory().post('/api/v1/dashboard/executive/portfolio-workbook/preview/', {})
            force_authenticate(request, self.user)
            self.assertEqual(view.as_view()(request).status_code, 403)

    def test_revocation_filters_next_response_and_does_not_mutate_builder_report(self):
        self.grant('portfolio')
        original = deepcopy(REPORT)
        self.assertIn('workbook', filter_executive_report(original, self.user)['portfolio_performance'])
        self.override('portfolio', False)
        self.assertNotIn('portfolio_performance', filter_executive_report(original, self.user))
        self.assertEqual(original, REPORT)

    def test_registration_is_idempotent_and_grants_nobody(self):
        register = import_module('apps.rbac.migrations.0065_executive_tab_permissions').register_tabs
        before = list(RolePermission.objects.values_list('role_id', 'permission_id'))
        editor = SimpleNamespace(connection=connection)
        register(apps, editor)
        register(apps, editor)
        self.assertEqual(list(RolePermission.objects.values_list('role_id', 'permission_id')), before)
        self.assertEqual(Permission.objects.filter(code__in=[p.code for p in self.permissions.values()]).count(), 3)
