from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.project_models import Project
from apps.users.models import User

from ..models import (
    BudgetAllocation, ControlAccount, IntegratedReportingSnapshot,
    ReconciliationRun, ReportingPeriod, WBSNode,
)
from ..views import ProjectAnalyticsViewSet


class PortfolioExceptionDashboardTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='portfolio-owner', email='portfolio-owner@example.com')
        self.other = User.objects.create_user(username='portfolio-other', email='portfolio-other@example.com')
        today = timezone.localdate()
        self.project = Project.objects.create(
            code='PORT-001', name='Visible Exception Project', owner=self.owner,
            status='active', currency='AED', progress=40,
            start_date=today - timedelta(days=120), end_date=today + timedelta(days=120),
        )
        Project.objects.create(
            code='PORT-HIDDEN', name='Hidden Project', owner=self.other,
            status='active', currency='AED', end_date=today - timedelta(days=20),
        )
        wbs = WBSNode.objects.create(project=self.project, code='1.1', name='Engineering')
        BudgetAllocation.objects.create(
            project=self.project, wbs_node=wbs, code='BUD-PORT', name='Control budget',
            amount=Decimal('1000'), currency='AED', status='approved',
        )
        ControlAccount.objects.create(
            project=self.project, wbs_node=wbs, code='CA-PORT', name='Engineering',
            manager=self.owner, baseline_start=today - timedelta(days=120),
            baseline_finish=today + timedelta(days=120), status='active',
        )
        period = ReportingPeriod.objects.create(
            project=self.project, sequence=1, name='Current report',
            start_date=today - timedelta(days=30), end_date=today,
            data_date=today, status='locked', created_by=self.owner,
        )
        reconciliation = ReconciliationRun.objects.create(
            project=self.project, reporting_period=period, run_number=1, status='completed',
            checksum='a' * 64, created_by=self.owner,
        )
        IntegratedReportingSnapshot.objects.create(
            project=self.project, reporting_period=period, reconciliation_run=reconciliation,
            version=1, data_date=today, currency='AED', budget_at_completion=Decimal('1000'),
            planned_value=Decimal('500'), earned_value=Decimal('400'), actual_cost=Decimal('500'),
            progress_pct=Decimal('40'), planned_progress_pct=Decimal('50'),
            cost_variance=Decimal('-100'), schedule_variance=Decimal('-100'),
            cpi=Decimal('0.8000'), spi=Decimal('0.9200'),
            estimate_at_completion=Decimal('1250'), estimate_to_complete=Decimal('750'),
            variance_at_completion=Decimal('-250'), checksum='b' * 64, sealed_by=self.owner,
        )

    def get_dashboard(self, params=None):
        request = APIRequestFactory().get('/test/', params or {})
        force_authenticate(request, user=self.owner)
        response = ProjectAnalyticsViewSet.as_view({'get': 'portfolio_exceptions'})(request)
        response.render()
        return response

    def test_dashboard_ranks_integrated_kpi_exceptions_and_respects_access(self):
        response = self.get_dashboard()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['summary']['total_projects'], 1)
        row = response.data['projects'][0]
        self.assertEqual(row['project']['code'], 'PORT-001')
        self.assertEqual(row['overall_severity'], 'critical')
        codes = {issue['code'] for issue in row['exceptions']}
        self.assertIn('cpi_below_threshold', codes)
        self.assertIn('spi_below_threshold', codes)
        self.assertIn('forecast_overrun', codes)
        self.assertNotIn('missing_active_control_account', codes)

    def test_severity_and_search_filters_apply_to_accessible_projects(self):
        response = self.get_dashboard({'severity': 'critical', 'search': 'PORT-001'})
        self.assertEqual(response.data['summary']['total_projects'], 1)
        hidden = self.get_dashboard({'search': 'PORT-HIDDEN'})
        self.assertEqual(hidden.data['summary']['total_projects'], 0)
