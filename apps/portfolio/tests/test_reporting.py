from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.project_models import Project
from apps.portfolio.models import PortfolioRow, PortfolioSnapshot, PortfolioSource
from apps.portfolio.reporting import build_workbook_report
from apps.rbac.models import (
    Module, Organization, Permission, Role, RoleModule, RolePermission,
    UserPermissionOverride, UserProfile, UserRole,
)
from apps.rbac.module_actions import ensure_module_actions


class PortfolioReportingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('portfolio-reader', email='portfolio@example.test')
        org, _ = Organization.objects.get_or_create(code='portfolio-test', defaults={'name': 'Portfolio test'})
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'organization': org})
        self.profile.roles.clear()
        self.role = Role.objects.create(code='portfolio-reader-test', name='Portfolio reader', level=3)
        UserRole.objects.create(user_profile=self.profile, role=self.role)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = '/api/v1/dashboard/executive/portfolio-workbook/'

    def grant(self, *codes):
        for code in codes:
            module, _ = Module.objects.get_or_create(code=code, defaults={'name': code})
            ensure_module_actions(Module, Permission, module_ids=[module.pk])
            RoleModule.objects.get_or_create(role=self.role, module=module)
            for permission in module.permissions.filter(action='read', is_active=True):
                RolePermission.objects.get_or_create(role=self.role, permission=permission)

    def source(self, **kwargs):
        source = PortfolioSource.objects.create()
        snapshot = PortfolioSnapshot.objects.create(
            source=source, sha256='a' * 64, parser_version='test', file_name='portfolio.xlsx',
            reporting_date=timezone.localdate(), row_count=2, **kwargs)
        source.active_snapshot = snapshot
        source.save(update_fields=['active_snapshot'])
        return source, snapshot

    def row(self, snapshot, subproject='P-1', **kwargs):
        fields = {
            'project_code': 'P', 'subproject_code': subproject, 'source_row': 8, 'title': 'Project item',
            'currency': 'USD', 'include_without_pt': True, 'include_with_pt': True,
            'contract_value_aed': Decimal('1000.25'), 'recognized_revenue_aed': Decimal('0'),
            'period_revenue_aed': Decimal('0'), 'backlog_without_pt_aed': Decimal('1000.25'),
            'backlog_with_pt_aed': Decimal('1000.25'), 'overclaim_aed': Decimal('0'),
            'poc_pct': Decimal('0'), 'eddr_pct': None,
            'extra': {'forecasts': [{'period': '2026-10-01', 'revenue_aed': '123.45'}]},
        }
        fields.update(kwargs)
        return PortfolioRow.objects.create(snapshot=snapshot, **fields)

    def test_endpoint_and_source_grants_are_independent(self):
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.grant('executive_dashboard')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'restricted')
        self.assertIsNone(response.data['source'])
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertIn(self.client.post(self.url, {}).status_code, [403, 405])
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.url).status_code, [401, 403])

    def test_no_snapshot_is_not_zero_and_filters_are_validated(self):
        self.grant('executive_dashboard', 'project_control')
        data = self.client.get(self.url).data
        self.assertEqual(data['status'], 'unavailable')
        self.assertFalse(data['can_upload'])
        self.assertEqual(self.client.get(self.url, {'limit': 201}).status_code, 400)
        self.assertEqual(self.client.get(self.url, {'offset': -1}).status_code, 400)

    def test_upload_capability_before_first_import_and_with_a_snapshot(self):
        self.grant('executive_dashboard', 'project_control')
        permission = Permission.objects.get(module__code='project_control', action='update')
        RolePermission.objects.create(role=self.role, permission=permission)
        self.assertFalse(self.client.get(self.url).data['can_upload'])
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])
        self.assertTrue(self.client.get(self.url).data['can_upload'])
        self.source()
        self.assertTrue(self.client.get(self.url).data['can_upload'])
        UserPermissionOverride.objects.create(user_profile=self.profile, permission=permission, allowed=False)
        self.assertFalse(self.client.get(self.url).data['can_upload'])

    def test_saved_aed_zero_and_missing_progress_are_distinct(self):
        self.grant('executive_dashboard', 'project_control')
        _, snapshot = self.source()
        Project.objects.create(code='P', name='Registered parent', owner=self.user)
        self.row(snapshot)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = response.data
        self.assertEqual(data['status'], 'available')
        self.assertEqual(data['totals']['contract_value_aed']['value'], '1000.25')
        self.assertEqual(data['totals']['recognized_revenue_aed']['value'], '0.00')
        self.assertEqual(Decimal(data['rows'][0]['poc_pct']), 0)
        self.assertIsNone(data['rows'][0]['eddr_pct'])
        self.assertEqual(data['rows'][0]['currency'], 'USD')
        self.assertEqual(data['forecast'][0]['revenue_aed'], '123.45')

    def test_partial_amounts_withhold_totals_and_forecast(self):
        self.grant('project_control')
        _, snapshot = self.source()
        Project.objects.create(code='P', name='Registered parent', owner=self.user)
        self.row(snapshot)
        self.row(snapshot, 'P-2', recognized_revenue_aed=None, extra={})
        data = build_workbook_report(self.user)
        self.assertEqual(data['status'], 'partial')
        self.assertIsNone(data['totals']['recognized_revenue_aed']['value'])
        self.assertEqual(data['totals']['recognized_revenue_aed']['missing_count'], 1)
        self.assertEqual(data['totals']['contract_value_aed']['value'], '2000.50')
        self.assertIsNone(data['forecast'][0]['revenue_aed'])
        self.assertEqual(data['forecast'][0]['missing_count'], 1)

    def test_inclusion_flags_define_separate_totals(self):
        self.grant('project_control')
        _, snapshot = self.source()
        Project.objects.create(code='P', name='Parent', owner=self.user)
        self.row(snapshot)
        self.row(snapshot, 'P-PT', include_without_pt=False, include_with_pt=True)
        data = build_workbook_report(self.user)
        self.assertEqual(data['row_count'], 2)
        self.assertEqual(data['totals']['contract_value_aed']['value'], '1000.25')
        self.assertEqual(data['totals']['backlog_with_pt_aed']['value'], '2000.50')
        self.row(snapshot, 'P-UNKNOWN', include_without_pt=None)
        data = build_workbook_report(self.user)
        self.assertIsNone(data['totals']['contract_value_aed']['value'])

    def test_exact_hidden_or_deleted_subproject_does_not_inherit_parent_access(self):
        self.grant('project_control')
        _, snapshot = self.source()
        parent = Project.objects.create(code='P', name='Parent', owner=self.user)
        Project.objects.create(code='P-1', name='Hidden item')
        Project.objects.create(code='P-DELETED', name='Deleted item', is_deleted=True)
        self.row(snapshot)
        self.row(snapshot, 'P-DELETED')
        self.row(snapshot, 'P-VISIBLE')
        self.row(snapshot, 'UNMATCHED', project_code='OTHER')
        with patch('apps.project_control.access.accessible_enterprise_projects',
                   return_value=Project.objects.filter(pk=parent.pk)):
            data = build_workbook_report(self.user)
        self.assertEqual([row['subproject_code'] for row in data['rows']], ['P-VISIBLE'])
        self.assertEqual(data['totals']['contract_value_aed']['value'], '1000.25')

    def test_admin_unmatched_rows_still_require_source_grant_and_honor_deny(self):
        self.grant('project_control')
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        _, snapshot = self.source()
        self.row(snapshot)
        self.assertEqual(build_workbook_report(self.user)['row_count'], 1)
        permission = Permission.objects.get(module__code='project_control', action='read')
        UserPermissionOverride.objects.create(user_profile=self.profile, permission=permission, allowed=False)
        self.assertEqual(build_workbook_report(self.user)['status'], 'restricted')

    def test_filters_and_pagination_keep_totals_over_full_filtered_scope(self):
        self.grant('project_control')
        _, snapshot = self.source()
        Project.objects.create(code='P', name='Parent', owner=self.user)
        self.row(snapshot, pm='PM1', business_unit='DE')
        self.row(snapshot, 'P-2', pm='PM1', business_unit='DE')
        self.row(snapshot, 'P-3', pm='PM2', business_unit='FEED')
        data = build_workbook_report(self.user, pm='pm1', limit=1)
        self.assertEqual(data['row_count'], 2)
        self.assertEqual(data['returned_rows'], 1)
        self.assertTrue(data['truncated'])
        self.assertEqual(data['totals']['contract_value_aed']['value'], '2000.50')

    def test_failed_sync_preserves_visible_last_good_snapshot(self):
        self.grant('project_control')
        source, snapshot = self.source()
        Project.objects.create(code='P', name='Parent', owner=self.user)
        self.row(snapshot)
        source.last_error = 'SharePoint returned HTTP 403.'
        source.save(update_fields=['last_error'])
        snapshot.reporting_date = timezone.localdate() - timedelta(days=30)
        snapshot.save(update_fields=['reporting_date'])
        data = build_workbook_report(self.user)
        self.assertEqual(data['status'], 'partial')
        self.assertEqual(data['source']['sync_status'], 'error')
        self.assertTrue(data['source']['is_stale'])
        self.assertEqual(data['totals']['contract_value_aed']['value'], '1000.25')

    def test_database_failure_is_not_an_empty_success(self):
        self.grant('project_control')
        with patch('apps.portfolio.models.PortfolioSource.objects.select_related', side_effect=RuntimeError('test')):
            data = build_workbook_report(self.user)
        self.assertEqual(data['status'], 'error')
        self.assertIsNone(data['row_count'])

    def test_local_import_is_not_a_successful_or_overdue_sharepoint_sync(self):
        self.grant('project_control')
        source, snapshot = self.source()
        Project.objects.create(code='P', name='Parent', owner=self.user)
        self.row(snapshot)
        source.last_success_at = timezone.now() - timedelta(hours=12)
        source.save(update_fields=['last_success_at'])
        with override_settings(PORTFOLIO_SYNC_ENABLED=True):
            data = build_workbook_report(self.user)
        self.assertEqual(data['source']['kind'], 'manual')
        self.assertEqual(data['source']['sync_status'], 'never')
        self.assertIsNone(data['source']['last_success_at'])
        self.assertFalse(data['source']['sync_overdue'])
