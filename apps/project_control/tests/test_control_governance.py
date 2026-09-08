from datetime import date
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.project_models import Project, ProjectMember
from apps.users.models import User

from ..models import BudgetAllocation, ControlAccount, ReportingPeriod, ReportingPeriodAudit, WBSNode
from ..views import ControlAccountViewSet, ReportingPeriodViewSet


class ControlGovernanceApiTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='control-owner', email='control-owner@example.com')
        self.manager = User.objects.create_user(username='control-manager', email='control-manager@example.com')
        self.controller = User.objects.create_superuser(
            username='control-approver', email='control-approver@example.com', password='unused',
        )
        self.project = Project.objects.create(
            code='GOV-001', name='Governed Project', owner=self.owner, currency='AED',
            start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
        )
        ProjectMember.objects.create(project=self.project, user=self.manager, role='engineer')
        self.wbs = WBSNode.objects.create(project=self.project, code='1.1', name='Engineering')
        BudgetAllocation.objects.create(
            project=self.project, wbs_node=self.wbs, code='BUD-ENG', name='Engineering budget',
            amount=Decimal('250000'), currency='AED', status='approved', approved_by=self.controller,
        )
        self.factory = APIRequestFactory()

    def call(self, viewset, method, action, user, data=None, pk=None):
        path = '/test/' if pk is None else f'/test/{pk}/'
        request = getattr(self.factory, method)(path, data or {}, format='json')
        force_authenticate(request, user=user)
        response = viewset.as_view({method: action})(request, **({'pk': pk} if pk else {}))
        response.render()
        return response

    def test_control_account_requires_submission_and_independent_approval(self):
        created = self.call(ControlAccountViewSet, 'post', 'create', self.owner, {
            'project': self.project.id,
            'wbs_node': self.wbs.id,
            'code': 'CA-ENG',
            'name': 'Engineering Control Account',
            'manager': self.manager.id,
            'earned_value_method': 'weighted_milestone',
            'baseline_start': '2026-01-01',
            'baseline_finish': '2026-09-30',
        })
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data['status'], 'draft')
        self.assertEqual(Decimal(created.data['approved_budget']), Decimal('250000'))

        submitted = self.call(
            ControlAccountViewSet, 'post', 'submit', self.owner, pk=created.data['id'],
        )
        self.assertEqual(submitted.status_code, 200, submitted.data)
        self.assertEqual(submitted.data['status'], 'submitted')

        approved = self.call(
            ControlAccountViewSet, 'post', 'approve', self.controller, pk=created.data['id'],
        )
        self.assertEqual(approved.status_code, 200, approved.data)
        self.assertEqual(approved.data['status'], 'active')
        immutable = self.call(
            ControlAccountViewSet, 'patch', 'partial_update', self.controller,
            {'name': 'Changed after approval'}, pk=created.data['id'],
        )
        self.assertEqual(immutable.status_code, 400)

    def test_control_account_cannot_activate_without_approved_wbs_budget(self):
        other_wbs = WBSNode.objects.create(project=self.project, code='1.2', name='Procurement')
        account = ControlAccount.objects.create(
            project=self.project, wbs_node=other_wbs, code='CA-PROC', name='Procurement',
            manager=self.manager, baseline_start=date(2026, 1, 1), baseline_finish=date(2026, 6, 30),
            status='submitted', submitted_by=self.owner,
        )
        response = self.call(ControlAccountViewSet, 'post', 'approve', self.controller, pk=account.id)
        self.assertEqual(response.status_code, 400)
        self.assertIn('approved_budget', response.data)

    def test_reporting_period_lock_and_reopen_are_audited(self):
        created = self.call(ReportingPeriodViewSet, 'post', 'create', self.owner, {
            'project': self.project.id,
            'sequence': 1,
            'name': 'January 2026',
            'start_date': '2026-01-01',
            'end_date': '2026-01-31',
            'data_date': '2026-01-31',
        })
        self.assertEqual(created.status_code, 201, created.data)
        period_id = created.data['id']
        self.assertTrue(created.data['is_entry_allowed'])

        submitted = self.call(ReportingPeriodViewSet, 'post', 'submit', self.owner, pk=period_id)
        self.assertEqual(submitted.data['status'], 'submitted')
        self.assertFalse(submitted.data['is_entry_allowed'])

        locked = self.call(ReportingPeriodViewSet, 'post', 'lock', self.controller, pk=period_id)
        self.assertEqual(locked.status_code, 200, locked.data)
        self.assertEqual(locked.data['status'], 'locked')
        immutable = self.call(
            ReportingPeriodViewSet, 'patch', 'partial_update', self.controller,
            {'data_date': '2026-01-30'}, pk=period_id,
        )
        self.assertEqual(immutable.status_code, 400)

        reopened = self.call(
            ReportingPeriodViewSet, 'post', 'reopen', self.controller,
            {'reason': 'Approved correction to late actual cost.'}, pk=period_id,
        )
        self.assertEqual(reopened.status_code, 200, reopened.data)
        self.assertEqual(reopened.data['status'], 'reopened')
        self.assertTrue(reopened.data['is_entry_allowed'])

        history = self.call(ReportingPeriodViewSet, 'get', 'history', self.controller, pk=period_id)
        self.assertEqual(history.status_code, 200)
        self.assertEqual([row['action'] for row in history.data], ['created', 'submitted', 'locked', 'reopened'])
        self.assertEqual(ReportingPeriodAudit.objects.filter(period_id=period_id).count(), 4)

    def test_project_reporting_periods_cannot_overlap(self):
        ReportingPeriod.objects.create(
            project=self.project, sequence=1, name='January 2026',
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 31),
            data_date=date(2026, 1, 31), status='submitted', created_by=self.owner,
        )
        response = self.call(ReportingPeriodViewSet, 'post', 'create', self.owner, {
            'project': self.project.id,
            'sequence': 2,
            'name': 'Overlap',
            'start_date': '2026-01-15',
            'end_date': '2026-02-15',
            'data_date': '2026-02-15',
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn('start_date', response.data)
