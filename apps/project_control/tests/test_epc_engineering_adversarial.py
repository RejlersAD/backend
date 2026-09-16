"""Keep engineering ownership separate from tracked external EPC dependencies."""
from datetime import date
from decimal import Decimal
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.project_models import Project, ProjectMember
from apps.core.project_views import ProjectViewSet
from apps.planning_intelligence.models import (
    ActivityProgressUpdate, PlanningProject, Schedule, ScheduleActivity,
    ScheduleBaseline, ScheduleControlSnapshot, ScheduleVersion,
)
from apps.users.models import User
from apps.procurement.tests.approval_fixtures import grant_approval, set_position
from ..epc_models import IntegratedBaseline
from ..execution_models import EPCWorkEvent, EPCWorkItem
from ..models import (
    ApprovedHourEntry, BudgetAllocation, ControlAccount, CostAllocation, CostLedgerEntry,
    IntegratedReportingSnapshot, ProjectDocument, ReportingPeriod, WBSNode,
)
from ..services.actuals import create_integrated_snapshot, reconcile_reporting_period
from ..services.epc import EPC_PHASES, capture_integrated_baseline, save_activity_link
from ..services.execution import accept_work, review_work, submit_work
from ..views import (
    ApprovedHourEntryViewSet, BudgetAllocationViewSet, ControlAccountViewSet, CostAllocationViewSet,
)


class EngineeringBoundaryAdversarialTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        storage = override_settings(MEDIA_ROOT=self.directory.name)
        storage.enable()
        self.addCleanup(storage.disable)
        self.owner = User.objects.create_user(username='engineering-owner', email='engineering-owner@example.test')
        self.reviewer = User.objects.create_user(username='engineering-reviewer', email='engineering-reviewer@example.test')
        self.viewer = User.objects.create_user(username='engineering-viewer', email='engineering-viewer@example.test')
        self.authority = User.objects.create_superuser(username='engineering-authority', email='engineering-authority@example.test', password='unused')
        self.project = Project.objects.create(code='ENG-SCOPE-TEST', name='Isolated engineering scope',
            owner=self.owner, scope_type='detailed_engineering', client_name='Test client', currency='AED',
            start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        ProjectMember.objects.create(project=self.project, user=self.reviewer, role='reviewer')
        ProjectMember.objects.create(project=self.project, user=self.authority, role='project_manager')
        for user in (self.authority, self.reviewer):
            grant_approval(user, 'project_control', 'planning_package')
            set_position(user)
        ProjectMember.objects.create(project=self.project, user=self.viewer, role='viewer')
        self.nodes = [WBSNode.objects.create(project=self.project, code=code, name=name, sort_order=index)
                      for index, (code, name) in enumerate(EPC_PHASES)]
        self.owned, self.external = self.nodes[:2]
        self.data_date = date(2026, 7, 31)
        self.period = ReportingPeriod.objects.create(project=self.project, sequence=1, name='Engineering July',
            start_date=date(2026, 7, 1), end_date=self.data_date, data_date=self.data_date, created_by=self.owner)

    def call(self, viewset, method, action, data=None, *, pk=None, user=None):
        request = getattr(APIRequestFactory(), method)('/isolated-engineering/', data or {}, format='json')
        force_authenticate(request, user=user or self.authority)
        return viewset.as_view({method: action})(request, **({'pk': str(pk)} if pk else {}))

    def budget(self, node, code, *, status='draft'):
        return BudgetAllocation.objects.create(project=self.project, wbs_node=node, code=code, name=code,
            amount=Decimal('10000'), currency='AED', status=status,
            approved_by=self.authority if status == 'approved' else None,
            approved_at=timezone.now() if status == 'approved' else None)

    def account(self, node, code, *, status='active'):
        return ControlAccount.objects.create(project=self.project, wbs_node=node, code=code, name=code,
            manager=self.owner, baseline_start=date(2026, 1, 1), baseline_finish=date(2026, 12, 31),
            status=status, submitted_by=self.owner,
            approved_by=self.authority if status == 'active' else None,
            approved_at=timezone.now() if status == 'active' else None)

    def hour(self, account, reference, *, status='submitted'):
        return ApprovedHourEntry.objects.create(project=self.project, control_account=account,
            reporting_period=self.period, employee_code='TEST-EMP', work_date=self.data_date,
            hours=8, hourly_cost_rate=100, currency='AED', source_reference=reference,
            status=status, submitted_by=self.owner, submitted_at=timezone.now())

    def baseline_sources(self):
        self.owned_budget = self.budget(self.owned, 'OWNED-APPROVED', status='approved')
        self.owned_account = self.account(self.owned, 'OWNED-CA')
        planning = PlanningProject.objects.create(enterprise_project=self.project, name='Engineering with dependencies', created_by=self.owner)
        schedule = Schedule.objects.create(project=planning, name='Engineering interfaces', code='ENG-PLAN',
            planned_start=date(2026, 1, 1), data_date=date(2026, 1, 1))
        self.version = ScheduleVersion.objects.create(schedule=schedule, version=1, status='baselined', created_by=self.owner)
        self.activities = []
        for node, phase in zip(self.nodes, ['engineering', 'procurement', 'construction', 'commissioning']):
            activity = ScheduleActivity.objects.create(version=self.version, external_id=phase.upper(), name=node.name,
                planned_start=date(2026, 1, 1), planned_finish=date(2026, 12, 31), duration_days=10)
            save_activity_link(self.project, {'activity': activity.pk, 'wbs_node': node.pk, 'link_type': phase}, user=self.owner)
            self.activities.append(activity)
        baseline = ScheduleBaseline.objects.create(schedule=schedule, source_version=self.version,
            name='Saved engineering interfaces', data_date=date(2026, 1, 1), approved_by=self.authority,
            approved_at=timezone.now(), snapshot={'activities': [
                {'id': activity.pk, 'external_id': activity.external_id, 'planned_start': '2026-01-01',
                 'planned_finish': '2026-12-31', 'duration_days': 10} for activity in self.activities]})
        self.baseline = capture_integrated_baseline(self.project, {'schedule_baseline': baseline.pk,
            'budget_ids': [self.owned_budget.pk], 'name': 'Engineering ownership only', 'data_date': '2026-01-01'}, user=self.authority)

    def test_legacy_external_budget_cannot_be_approved_into_engineering_ledger(self):
        budget = self.budget(self.external, 'LEGACY-EXTERNAL')
        response = self.call(BudgetAllocationViewSet, 'post', 'approve', pk=budget.pk)
        self.assertEqual(response.status_code, 400, response.data)
        budget.refresh_from_db()
        self.assertEqual(budget.status, 'draft')
        self.assertIsNone(budget.approved_at)
        self.assertFalse(CostLedgerEntry.objects.filter(project=self.project).exists())

    def test_legacy_external_control_account_cannot_be_activated(self):
        self.budget(self.external, 'LEGACY-EXTERNAL-APPROVED', status='approved')
        account = self.account(self.external, 'LEGACY-EXTERNAL-CA', status='submitted')
        response = self.call(ControlAccountViewSet, 'post', 'approve', pk=account.pk)
        self.assertEqual(response.status_code, 400, response.data)
        account.refresh_from_db()
        self.assertEqual(account.status, 'submitted')
        self.assertIsNone(account.approved_at)

    def test_legacy_external_hours_cannot_be_approved(self):
        account = self.account(self.external, 'EXTERNAL-LEGACY-ACTIVE')
        hour = self.hour(account, 'EXTERNAL-HOURS')
        response = self.call(ApprovedHourEntryViewSet, 'post', 'approve', pk=hour.pk)
        self.assertEqual(response.status_code, 400, response.data)
        hour.refresh_from_db()
        self.assertEqual(hour.status, 'submitted')
        self.assertIsNone(hour.approved_at)
        self.assertEqual(hour.labor_actual_cost, Decimal('0'))

    def test_legacy_external_manual_cost_cannot_be_approved(self):
        cost = CostAllocation.objects.create(project=self.project, wbs_node=self.external, source_type='manual',
            source_id='EXTERNAL-LEGACY', amount=Decimal('9500'), currency='AED', allocated_by=self.owner)
        response = self.call(CostAllocationViewSet, 'post', 'approve', pk=cost.pk)
        self.assertEqual(response.status_code, 400, response.data)
        cost.refresh_from_db()
        self.assertEqual(cost.status, 'draft')
        self.assertIsNone(cost.approved_at)
        self.assertFalse(CostLedgerEntry.objects.filter(project=self.project).exists())

    def test_financial_draft_cannot_be_moved_from_owned_to_external_wbs(self):
        budget = self.budget(self.owned, 'OWNED-DRAFT')
        account = self.account(self.owned, 'OWNED-DRAFT-CA', status='draft')
        external_account = self.account(self.external, 'EXTERNAL-CA')
        owned_child = WBSNode.objects.create(project=self.project, parent=self.owned, code='ENG-ACTIVE', name='Owned active package')
        owned_active = self.account(owned_child, 'OWNED-ACTIVE-CA')
        hour = self.hour(owned_active, 'OWNED-DRAFT-HOUR', status='draft')
        cost = CostAllocation.objects.create(project=self.project, wbs_node=self.owned, source_type='manual',
            source_id='OWNED-DRAFT-COST', amount=Decimal('100'), currency='AED', allocated_by=self.owner)
        cases = [(BudgetAllocationViewSet, budget, {'wbs_node': self.external.pk}),
                 (ControlAccountViewSet, account, {'wbs_node': self.external.pk}),
                 (ApprovedHourEntryViewSet, hour, {'control_account': external_account.pk}),
                 (CostAllocationViewSet, cost, {'wbs_node': self.external.pk})]
        for viewset, row, data in cases:
            with self.subTest(view=viewset.__name__):
                response = self.call(viewset, 'patch', 'partial_update', data, pk=row.pk)
                self.assertEqual(response.status_code, 400, response.data)
                row.refresh_from_db()
                self.assertEqual(row.control_account_id if isinstance(row, ApprovedHourEntry) else row.wbs_node_id,
                                 owned_active.pk if isinstance(row, ApprovedHourEntry) else self.owned.pk)

    def test_new_financial_records_cannot_target_external_dependencies(self):
        external_account = self.account(self.external, 'CREATE-EXTERNAL-CA')
        external_child = WBSNode.objects.create(project=self.project, parent=self.external, code='PROC-NEW', name='External package')
        common = {'project': self.project.pk, 'wbs_node': self.external.pk}
        cases = [
            (BudgetAllocationViewSet, {**common, 'code': 'EXTERNAL-NEW-BUDGET', 'name': 'External budget', 'amount': '100', 'currency': 'AED'}, 'wbs_node'),
            (ControlAccountViewSet, {**common, 'wbs_node': external_child.pk, 'code': 'EXTERNAL-NEW-CA', 'name': 'External control',
             'manager': self.owner.pk, 'baseline_start': '2026-01-01', 'baseline_finish': '2026-12-31'}, 'wbs_node'),
            (ApprovedHourEntryViewSet, {'project': self.project.pk, 'control_account': external_account.pk,
             'reporting_period': self.period.pk, 'employee_code': 'NEW-EMP', 'work_date': str(self.data_date),
             'hours': '8', 'hourly_cost_rate': '100', 'currency': 'AED', 'source_reference': 'NEW-EXTERNAL-HOURS'}, 'control_account'),
            (CostAllocationViewSet, {**common, 'source_type': 'manual', 'source_id': 'NEW-EXTERNAL-COST', 'amount': '100'}, 'wbs_node'),
        ]
        for viewset, data, field in cases:
            with self.subTest(view=viewset.__name__):
                response = self.call(viewset, 'post', 'create', data)
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn(field, response.data)
                self.assertIn('Engineering', str(response.data))

    def test_owned_engineering_budget_and_hours_still_use_existing_approval_routes(self):
        budget = self.budget(self.owned, 'VALID-ENGINEERING')
        response = self.call(BudgetAllocationViewSet, 'post', 'approve', pk=budget.pk)
        self.assertEqual(response.status_code, 200, response.data)
        account = self.account(self.owned, 'VALID-ENGINEERING-CA', status='submitted')
        response = self.call(ControlAccountViewSet, 'post', 'approve', pk=account.pk)
        self.assertEqual(response.status_code, 200, response.data)
        hour = self.hour(account, 'VALID-ENGINEERING-HOURS')
        response = self.call(ApprovedHourEntryViewSet, 'post', 'approve', pk=hour.pk)
        self.assertEqual(response.status_code, 200, response.data)
        hour.refresh_from_db()
        self.assertEqual(hour.labor_actual_cost, Decimal('800'))

    def test_core_project_viewer_cannot_change_delivery_scope(self):
        response = self.call(ProjectViewSet, 'patch', 'partial_update', {'scope_type': 'epc'},
                             pk=self.project.pk, user=self.viewer)
        self.assertEqual(response.status_code, 403, response.data)
        self.project.refresh_from_db()
        self.assertEqual(self.project.scope_type, 'detailed_engineering')

    def test_core_scope_change_cannot_reclassify_an_existing_baseline(self):
        self.baseline_sources()
        checksum = self.baseline.checksum
        response = self.call(ProjectViewSet, 'patch', 'partial_update', {'scope_type': 'epc'}, pk=self.project.pk)
        self.assertEqual(response.status_code, 400, response.data)
        self.project.refresh_from_db()
        self.baseline.refresh_from_db()
        self.assertEqual(self.project.scope_type, 'detailed_engineering')
        self.assertEqual(self.baseline.checksum, checksum)
        unchanged = self.call(ProjectViewSet, 'patch', 'partial_update', {'description': 'Clarified project notes'},
                              pk=self.project.pk, user=self.owner)
        self.assertEqual(unchanged.status_code, 200, unchanged.data)
        self.assertEqual(unchanged.data['scope_type'], 'detailed_engineering')

    def test_core_scope_change_cannot_reclassify_accepted_work_without_a_baseline(self):
        EPCWorkItem.objects.create(project=self.project, code='LEGACY-ACCEPTED', title='Accepted engineering history',
            phase='engineering', wbs_node=self.owned, owner=self.owner, reviewer=self.reviewer,
            data_date=self.data_date, status='accepted', acceptance_criteria=['Recorded historical acceptance'],
            accepted_by=self.authority, accepted_at=timezone.now())
        response = self.call(ProjectViewSet, 'patch', 'partial_update', {'scope_type': 'epc'}, pk=self.project.pk)
        self.assertEqual(response.status_code, 400, response.data)
        self.project.refresh_from_db()
        self.assertEqual(self.project.scope_type, 'detailed_engineering')

    def test_core_scope_change_cannot_relabel_published_schedule_observations(self):
        planning = PlanningProject.objects.create(enterprise_project=self.project, name='Historical engineering', created_by=self.owner)
        schedule = Schedule.objects.create(project=planning, name='Historical plan', code='HISTORY', planned_start=date(2026, 1, 1))
        version = ScheduleVersion.objects.create(schedule=schedule, version=1, status='baselined', created_by=self.owner)
        observation = ScheduleControlSnapshot.objects.create(version=version, data_date=self.data_date,
            progress_pct=40, payload={'control_scope': {'scope_type': 'detailed_engineering'}}, captured_by=self.authority)
        response = self.call(ProjectViewSet, 'patch', 'partial_update', {'scope_type': 'epc'}, pk=self.project.pk)
        self.assertEqual(response.status_code, 400, response.data)
        observation.refresh_from_db()
        self.assertEqual(observation.progress_pct, Decimal('40'))
        self.assertEqual(observation.payload['control_scope']['scope_type'], 'detailed_engineering')

    def test_external_work_cannot_earn_acceptance_even_when_inserted_as_legacy_draft(self):
        self.baseline_sources()
        document = ProjectDocument.objects.create(project=self.project, kind='report', title='External interface evidence',
            file=SimpleUploadedFile('interface.txt', b'External interface evidence only'), original_filename='interface.txt')
        item = EPCWorkItem.objects.create(project=self.project, code='EXTERNAL-WORK', title='External procurement dependency',
            phase='procurement', wbs_node=self.external, owner=self.owner, reviewer=self.reviewer,
            activity=self.activities[1], baseline=self.baseline, data_date=self.data_date,
            acceptance_criteria=['Interface tracking is not owned delivery'], evidence_note='External interface evidence.')
        item.documents.add(document)
        try:
            item = submit_work(item, user=self.owner)
            item = review_work(item, user=self.reviewer, decision='approve', note='Reviewed interface information', criteria_confirmed=True)
        except ValidationError:
            pass  # An earlier transition may enforce the same ownership boundary.
        with self.assertRaises(ValidationError):
            accept_work(item, user=self.authority, note='External progress cannot earn Engineering completion')
        self.assertFalse(ActivityProgressUpdate.objects.exists())
        self.assertFalse(ScheduleControlSnapshot.objects.exists())
        self.assertFalse(EPCWorkEvent.objects.filter(action='accepted').exists())

    def test_engineering_reporting_rejects_legacy_or_mismatched_schedule_ownership(self):
        self.baseline_sources()
        scopes = [None, {'ready': True, 'scope_type': 'epc', 'owned_activity_ids': [row.pk for row in self.activities]},
                  {'ready': True, 'scope_type': 'detailed_engineering', 'owned_activity_ids': [self.activities[1].pk]}]
        for index, scope in enumerate(scopes, start=1):
            with self.subTest(scope=scope):
                ScheduleControlSnapshot.objects.create(version=self.version, data_date=self.data_date, revision=index,
                    progress_pct=100, planned_progress_pct=100, payload={'control_scope': scope} if scope else {},
                    captured_by=self.authority)
                self.period.status = 'open'
                self.period.save(update_fields=['status'])
                reconciliation = reconcile_reporting_period(self.period, user=self.authority)
                self.assertEqual(reconciliation.status, 'completed', reconciliation.exceptions)
                self.period.status = 'submitted'
                self.period.save(update_fields=['status'])
                with self.assertRaises(ValueError):
                    create_integrated_snapshot(self.period, user=self.authority)
                self.assertFalse(IntegratedReportingSnapshot.objects.exists())
        self.assertEqual(IntegratedBaseline.objects.count(), 1)

    def test_posted_external_actuals_require_reconciliation_review(self):
        account = self.account(self.external, 'EXTERNAL-POSTED-CA')
        entry = CostLedgerEntry.objects.create(project=self.project, wbs_node=self.external,
            control_account=account, reporting_period=self.period, entry_key='legacy-external-adjustment',
            entry_type='adjustment', amount=Decimal('7500'), currency='AED', source_type='manual',
            source_id='EXTERNAL-LEGACY', source_reference='External dependency correction',
            entry_date=self.data_date, status='posted')
        reconciliation = reconcile_reporting_period(self.period, user=self.authority)
        self.assertEqual(reconciliation.status, 'exceptions', reconciliation.source_manifest)
        self.assertGreater(reconciliation.exception_count, 0)
        self.assertIn('Engineering', str(reconciliation.exceptions))
        entry.refresh_from_db()
        self.assertEqual(entry.amount, Decimal('7500'))
        self.period.status = 'submitted'
        self.period.save(update_fields=['status'])
        with self.assertRaises(ValueError):
            create_integrated_snapshot(self.period, user=self.authority)
        self.assertFalse(IntegratedReportingSnapshot.objects.exists())

    def test_engineering_period_cannot_seal_without_an_effective_owned_baseline(self):
        self.budget(self.owned, 'PRE-BASELINE', status='approved')
        self.account(self.owned, 'PRE-BASELINE-CA')
        reconciliation = reconcile_reporting_period(self.period, user=self.authority)
        self.assertEqual(reconciliation.status, 'completed', reconciliation.exceptions)
        self.period.status = 'submitted'
        self.period.save(update_fields=['status'])
        with self.assertRaises(ValueError):
            create_integrated_snapshot(self.period, user=self.authority)
        self.assertFalse(IntegratedReportingSnapshot.objects.exists())
