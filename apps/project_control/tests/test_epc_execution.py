"""A disposable full EPC pilot exercises existing source approval workflows."""
from datetime import date, timedelta
from decimal import Decimal
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIClient

from apps.core.project_models import Project, ProjectMember, ProjectMilestone
from apps.planning_intelligence.models import (
    ActivityAssignment, ActivityProgressUpdate, ActivityRelationship, PlanningProject, Schedule,
    ScheduleActivity, ScheduleBaseline, ScheduleControlSnapshot, ScheduleResource, ScheduleVersion, WorkCalendar,
)
from apps.planning_intelligence.services.cpm import calculate_schedule_version
from apps.planning_intelligence.services.schedule_approval import approve_schedule_version
from apps.planning_intelligence.services.trustworthy_scheduling import approve_schedule_assurance, run_schedule_assurance
from apps.procurement.models import PurchaseOrder, Receipt, Vendor
from apps.procurement.services.purchase_order_approvals import FINANCIAL_STAGE, TECHNICAL_STAGE, record_decision
from apps.rbac.models import Organization, UserProfile
from apps.users.models import User
from ..epc_models import IntegratedBaseline
from ..execution_models import EPCWorkEvent, EPCWorkItem
from ..models import ApprovedHourEntry, BudgetAllocation, ControlAccount, IntegratedReportingSnapshot, ProjectDocument, ReportingPeriod, WBSNode
from ..services.epc import capture_integrated_baseline, save_activity_link, setup_epc_project
from ..services.execution import accept_work, action_blockers, material_readiness, review_work, submit_work


class EpcExecutionTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        storage = override_settings(MEDIA_ROOT=self.directory.name, TEAMS_APPROVAL_WEBHOOK_URL='')
        storage.enable()
        self.addCleanup(storage.disable)
        self.authority = User.objects.create_superuser(username='pilot-authority', email='pilot-authority@example.test', password='unused')
        self.owner = User.objects.create_user(username='pilot-owner', email='pilot-owner@example.test')
        self.reviewer = User.objects.create_user(username='pilot-reviewer', email='pilot-reviewer@example.test')
        self.outsider = User.objects.create_user(username='pilot-outsider', email='pilot-outsider@example.test')
        self.project = Project.objects.create(code='EPC-PILOT', name='Full EPC pilot', owner=self.owner)
        self.other = Project.objects.create(code='OTHER-PILOT', name='Other project', owner=self.outsider)
        ProjectMember.objects.create(project=self.project, user=self.reviewer, role='reviewer')
        self.client = APIClient()
        self.data_date = timezone.localdate()
        self.project = setup_epc_project(self.project, {'code': self.project.code, 'name': self.project.name,
            'client_name': 'Pilot EPC client', 'owner': self.owner.pk, 'start_date': '2026-01-01',
            'end_date': '2026-12-31', 'currency': 'AED', 'scope_type': 'epc'}, user=self.authority)
        self.nodes = list(WBSNode.objects.filter(project=self.project).order_by('sort_order'))
        planning = PlanningProject.objects.create(enterprise_project=self.project, name='Full EPC planning', created_by=self.owner)
        calendar = WorkCalendar.objects.create(project=planning, name='Pilot weekdays', working_weekdays=[0, 1, 2, 3, 4], is_default=True)
        schedule = Schedule.objects.create(project=planning, name='Full EPC schedule', code='MASTER',
            planned_start=date(2026, 1, 1), data_date=date(2026, 1, 1), default_calendar=calendar, created_by=self.owner)
        self.version = ScheduleVersion.objects.create(schedule=schedule, version=1, created_by=self.owner)
        resource = ScheduleResource.objects.create(project=planning, code='CREW', name='EPC crew', resource_type='labor', capacity_units_per_day=200)
        self.activities, self.budgets, self.accounts = [], [], []
        for index, phase in enumerate(['engineering', 'procurement', 'construction', 'commissioning']):
            node = self.nodes[index]
            activity = ScheduleActivity.objects.create(version=self.version, calendar=calendar,
                external_id=f'{phase.upper()}-01', name=phase.title(), duration_days=2, sort_order=index)
            ActivityAssignment.objects.create(activity=activity, resource=resource, planned_units=250, budgeted_hours=250, budgeted_cost=25000)
            if self.activities:
                ActivityRelationship.objects.create(version=self.version, predecessor=self.activities[-1], successor=activity)
            self.activities.append(activity)
            save_activity_link(self.project, {'wbs_node': node.pk, 'activity': activity.pk, 'link_type': phase}, user=self.owner)
            budget = BudgetAllocation.objects.create(project=self.project, wbs_node=node, code=f'B-{index}', name=phase.title(), amount=25000, currency='AED')
            self.post_existing(f'budget-allocations/{budget.pk}/approve/', user=self.authority)
            self.budgets.append(budget)
            account = ControlAccount.objects.create(project=self.project, wbs_node=node, code=f'CA-{index}', name=phase.title(),
                manager=self.owner, baseline_start=date(2026, 1, 1), baseline_finish=date(2026, 12, 31), created_by=self.owner)
            self.post_existing(f'control-accounts/{account.pk}/submit/', user=self.owner)
            self.post_existing(f'control-accounts/{account.pk}/approve/', user=self.authority)
            self.accounts.append(account)
        calculate_schedule_version(self.version, requested_by=self.owner)
        run_schedule_assurance(self.version)
        approve_schedule_assurance(self.version, self.owner)
        approve_schedule_version(self.version, self.owner)
        self.client.force_authenticate(self.owner)
        response = self.client.post(f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/baseline/', {'name': 'Approved full EPC'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        saved_baseline = ScheduleBaseline.objects.get(pk=response.data['id'])
        self.baseline = capture_integrated_baseline(self.project, {'schedule_baseline': saved_baseline.pk,
            'budget_ids': [row.pk for row in self.budgets], 'name': 'Full EPC baseline', 'data_date': '2026-01-01'}, user=self.authority)
        self.document = ProjectDocument.objects.create(project=self.project, kind='drawing', title='Approved pilot evidence',
            file=SimpleUploadedFile('pilot-evidence.txt', b'Pilot completion criteria and test evidence'),
            original_filename='pilot-evidence.txt', size_bytes=43, content_type='text/plain', uploaded_by=self.owner)
        vendor = Vendor.objects.create(vendor_code='PILOT-VENDOR', name='Pilot vendor', status='active')
        self.order = PurchaseOrder.objects.create(po_number='PILOT-PO', vendor=vendor, title='Pilot materials',
            category='other', enterprise_project=self.project, created_by=self.owner,
            status='draft', currency='AED', total_amount=1000,
            items=[{'code': 'MATERIAL-01', 'qty': '10'}], required_certifications=['MTC'],
            heat_numbers_required=True, ndt_requirements='not required', approval_log=[
                {'stage': TECHNICAL_STAGE, 'level': 0, 'user_id': str(self.owner.pk),
                 'approver_email': self.owner.email, 'status': 'Pending'},
                {'stage': FINANCIAL_STAGE, 'level': 1, 'user_id': str(self.authority.pk),
                 'approver_email': self.authority.email, 'status': 'Pending'},
            ])
        # The real approval service requires a stored signature and records the
        # assigned actors/dates. This tiny image exists only in this test DB.
        signature = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a0z0AAAAASUVORK5CYII='
        organization = Organization.objects.create(name='Isolated pilot organization', code='EPC-PILOT-ORG')
        for signer in (self.owner, self.authority):
            profile, _ = UserProfile.objects.get_or_create(user=signer, defaults={'organization': organization})
            profile.signature_image = signature
            profile.status = 'active'
            profile.save(update_fields=['signature_image', 'status'])
            signer.rbac_profile = profile
        record_decision(self.order, self.owner, 'approve', stage=TECHNICAL_STAGE,
                        comment='Technical requirements checked.', require_signature=True)
        self.order, _ = record_decision(self.order, self.authority, 'approve', stage=FINANCIAL_STAGE,
                                       comment='Commercial terms approved.', require_signature=True)
        self.assertEqual(self.order.approved_by_id, self.authority.pk)
        self.assertIsNotNone(self.order.approved_at)
        # Issuance follows the completed approval; it does not create approval.
        self.order.status = 'sent'
        self.order.save(update_fields=['status', 'updated_at'])
        self.receipt = Receipt.objects.create(receipt_number='PILOT-GR', purchase_order=self.order, status='accepted',
            items_received=[{'code': 'MATERIAL-01', 'received_qty': '10', 'accepted_qty': '10'}],
            certificates_received=['MTC'], heat_numbers=['HEAT-01'], inspector_name='Pilot inspector',
            quality_check_passed=True, dimensional_check_passed=True, visual_inspection_passed=True, material_verification_passed=True)
        self.milestone = ProjectMilestone.objects.create(project=self.project, name='Commissioning accepted', target_date=self.data_date)
        self.items = []
        for index, phase in enumerate(['engineering', 'procurement', 'construction', 'commissioning']):
            item = EPCWorkItem.objects.create(project=self.project, code=f'WORK-{index}', title=phase.title(), phase=phase,
                wbs_node=self.nodes[index], owner=self.owner, reviewer=self.reviewer, activity=self.activities[index],
                baseline=self.baseline, requires_materials=phase in ('procurement', 'construction'),
                purchase_order=self.order if phase in ('procurement', 'construction') else None,
                milestone=self.milestone if phase == 'commissioning' else None,
                acceptance_criteria=['Recorded deliverable or test satisfies the agreed criterion'],
                evidence_note='Review the attached pilot completion evidence.', data_date=self.data_date)
            item.documents.add(self.document)
            if self.items:
                item.predecessors.add(self.items[-1])
            self.items.append(item)

    def post_existing(self, path, data=None, *, user):
        self.client.force_authenticate(user)
        response = self.client.post('/api/v1/project-control/' + path, data or {}, format='json')
        self.assertIn(response.status_code, (200, 201), response.data)
        return response

    def review(self, item):
        item = submit_work(item, user=self.owner)
        return review_work(item, user=self.reviewer, decision='approve', note='All criteria checked against project evidence.', criteria_confirmed=True)

    def complete(self, item):
        return accept_work(self.review(item), user=self.authority, note='Accepted against approved baseline and reviewed evidence.')

    def test_full_pilot_approved_sources_to_four_acceptances_and_reporting_seal(self):
        snapshots = []
        for index, item in enumerate(self.items, start=1):
            accepted = self.complete(item)
            snapshots.append(accepted.control_snapshot)
            self.assertEqual(accepted.status, 'accepted')
            self.assertEqual(accepted.progress_update.physical_progress_pct, Decimal('100'))
            self.assertEqual(accepted.control_snapshot.revision, index)
            self.assertEqual(accepted.control_snapshot.progress_pct, Decimal(index * 25))
            self.assertEqual(accepted.acceptance_manifest['baseline_checksum'], self.baseline.checksum)
        first_values = snapshots[0].payload
        first_id = snapshots[0].pk
        retried = accept_work(self.items[-1], user=self.authority, note='Retry after response delivery')
        self.assertEqual(retried.control_snapshot_id, snapshots[-1].pk)
        self.assertEqual(ScheduleControlSnapshot.objects.filter(version=self.version).count(), 4)
        self.assertEqual(ActivityProgressUpdate.objects.filter(version=self.version).count(), 4)
        self.assertEqual(EPCWorkEvent.objects.filter(action='accepted').count(), 4)
        snapshots[0].refresh_from_db()
        self.assertEqual(snapshots[0].pk, first_id)
        self.assertEqual(snapshots[0].progress_pct, Decimal('25'))
        self.assertEqual(snapshots[0].payload, first_values)
        self.milestone.refresh_from_db()
        self.assertTrue(self.milestone.is_completed)
        self.assertEqual(self.milestone.completed_date, self.data_date)
        period = ReportingPeriod.objects.create(project=self.project, sequence=1, name='Pilot completion',
            start_date=self.data_date.replace(day=1), end_date=self.data_date, data_date=self.data_date, created_by=self.owner)
        for index, account in enumerate(self.accounts):
            hour = ApprovedHourEntry.objects.create(project=self.project, control_account=account, reporting_period=period,
                employee_code=f'PILOT-{index}', work_date=self.data_date, hours=250, hourly_cost_rate=100,
                currency='AED', source_reference=f'PILOT-TIMESHEET-{index}', created_by=self.owner)
            self.post_existing(f'approved-hours/{hour.pk}/submit/', user=self.owner)
            self.post_existing(f'approved-hours/{hour.pk}/approve/', user=self.authority)
        self.post_existing(f'reporting-periods/{period.pk}/submit/', user=self.owner)
        response = self.post_existing(f'reporting-periods/{period.pk}/lock/', user=self.authority)
        sealed = IntegratedReportingSnapshot.objects.get(pk=response.data['snapshot']['id'])
        self.assertEqual(sealed.budget_at_completion, Decimal('100000'))
        self.assertEqual(sealed.earned_value, Decimal('100000'))
        self.assertEqual(sealed.actual_cost, Decimal('100000'))
        self.assertEqual(sealed.progress_pct, Decimal('100'))
        self.assertEqual(sealed.cpi, Decimal('1'))
        self.assertEqual(sealed.source_manifest['schedule_control_snapshot_id'], snapshots[-1].pk)
        self.assertEqual(sealed.source_manifest['schedule_control_snapshot_revision'], 4)
        self.assertEqual(sealed.source_manifest['integrated_baseline_id'], self.baseline.pk)
        self.assertEqual(sealed.source_manifest['integrated_baseline_revision'], self.baseline.revision)
        self.assertEqual(sealed.source_manifest['integrated_baseline_checksum'], self.baseline.checksum)
        self.assertEqual(len(sealed.source_manifest['epc_acceptances']), 4)

    def test_unreviewed_work_cannot_post_progress_or_complete_milestone(self):
        with self.assertRaises(ValidationError):
            accept_work(self.items[0], user=self.authority, note='Attempt without review')
        self.assertFalse(ActivityProgressUpdate.objects.exists())
        self.assertFalse(ScheduleControlSnapshot.objects.exists())
        self.assertFalse(EPCWorkEvent.objects.exists())

    def test_future_effective_baseline_cannot_accept_work_before_its_data_date(self):
        future = capture_integrated_baseline(self.project, {
            'schedule_baseline': self.baseline.schedule_baseline_id,
            'budget_ids': [row.pk for row in self.budgets],
            'name': 'Future approved integrated baseline',
            'data_date': (self.data_date + timedelta(days=1)).isoformat(),
        }, user=self.authority)
        item = self.items[0]
        item.baseline = future
        item.save(update_fields=['baseline', 'updated_at'])
        reviewed = self.review(item)
        with self.assertRaisesMessage(ValidationError, 'takes effect after the work data date'):
            accept_work(reviewed, user=self.authority, note='Must not post against a future reporting basis.')
        reviewed.refresh_from_db()
        self.assertEqual(reviewed.status, 'reviewed')
        self.assertFalse(ActivityProgressUpdate.objects.exists())
        self.assertFalse(ScheduleControlSnapshot.objects.exists())
        self.assertFalse(EPCWorkEvent.objects.filter(action='accepted').exists())

    def test_partial_receipt_and_missing_predecessor_block_acceptance(self):
        procurement = self.review(self.items[1])
        with self.assertRaises(ValidationError):
            accept_work(procurement, user=self.authority, note='Dependency incomplete')
        self.complete(self.items[0])
        self.receipt.items_received[0]['accepted_qty'] = '4'
        self.receipt.status = 'partial'
        self.receipt.save(update_fields=['items_received', 'status', 'updated_at'])
        self.assertFalse(material_readiness(procurement)['ready'])
        with self.assertRaises(ValidationError):
            accept_work(procurement, user=self.authority, note='Partial materials')
        self.assertEqual(ScheduleControlSnapshot.objects.count(), 1)

    def test_document_change_after_review_requires_review_again(self):
        item = self.review(self.items[0])
        self.document.original_filename = 'Changed-evidence.txt'
        self.document.save(update_fields=['original_filename', 'updated_at'])
        with self.assertRaises(ValidationError):
            accept_work(item, user=self.authority, note='Evidence was changed')
        self.assertFalse(ScheduleControlSnapshot.objects.exists())

    def test_review_requires_assigned_independent_reviewer_and_explicit_confirmation(self):
        item = submit_work(self.items[0], user=self.owner)
        with self.assertRaises(PermissionDenied):
            review_work(item, user=self.owner, decision='approve', note='Self review', criteria_confirmed=True)
        with self.assertRaises(ValidationError):
            review_work(item, user=self.reviewer, decision='approve', note='No criteria confirmation', criteria_confirmed=False)
        item.refresh_from_db()
        self.assertEqual(item.status, 'submitted')

    def test_foreign_document_and_foreign_purchase_order_cannot_enable_acceptance(self):
        self.document.project = self.other
        self.document.save(update_fields=['project'])
        with self.assertRaises(ValidationError):
            submit_work(self.items[0], user=self.owner)
        self.order.enterprise_project = self.other
        self.order.save(update_fields=['enterprise_project'])
        self.assertFalse(material_readiness(self.items[1])['ready'])

    def test_future_or_malformed_receipts_are_not_material_acceptance(self):
        self.receipt.items_received = [{'code': 'MATERIAL-01', 'received_qty': '10', 'accepted_qty': 'NaN'}]
        self.receipt.save(update_fields=['items_received'])
        self.assertFalse(material_readiness(self.items[1])['ready'])

    def test_return_to_draft_keeps_audit_and_requires_new_submission(self):
        item = self.review(self.items[0])
        item = review_work(item, user=self.reviewer, decision='return', note='Clarify the supporting evidence')
        self.assertEqual(item.status, 'draft')
        self.assertEqual(item.review_manifest, {})
        self.assertEqual(list(item.events.values_list('action', flat=True)), ['submitted', 'reviewed', 'returned'])
        with self.assertRaises(ValidationError):
            accept_work(item, user=self.authority, note='Review returned')

    def test_api_form_validates_people_scope_and_persists_created_event(self):
        path = '/api/v1/project-control/epc-work-items/'
        self.client.force_authenticate(self.owner)
        payload = {'project': self.project.pk, 'code': 'NEW-DRAFT', 'title': 'Draft deliverable',
            'phase': 'engineering', 'wbs_node': self.nodes[0].pk, 'owner': self.owner.pk,
            'reviewer': self.reviewer.pk, 'documents': [self.document.pk],
            'acceptance_criteria': ['Approved deliverable evidence'], 'evidence_note': 'Evidence to review',
            'data_date': self.data_date.isoformat()}
        rejected = self.client.post(path, {**payload, 'reviewer': self.owner.pk}, format='json')
        self.assertEqual(rejected.status_code, 400)
        rejected = self.client.post(path, {**payload, 'reviewer': self.outsider.pk}, format='json')
        self.assertEqual(rejected.status_code, 400)
        accepted = self.client.post(path, payload, format='json')
        self.assertEqual(accepted.status_code, 201, accepted.data)
        row = EPCWorkItem.objects.get(pk=accepted.data['id'])
        self.assertEqual(row.status, 'draft')
        self.assertEqual(list(row.events.values_list('action', flat=True)), ['created'])
        self.assertFalse(accepted.data['can_accept'])
        self.client.force_authenticate(self.reviewer)
        self.assertEqual(self.client.post(path, {**payload, 'code': 'VIEWER-CREATE'}, format='json').status_code, 403)

    def test_api_routes_enforce_workflow_permissions_and_accepted_readonly(self):
        item = self.items[0]
        path = f'/api/v1/project-control/epc-work-items/{item.pk}/'
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(path).status_code, 404)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.post(path + 'submit/').status_code, 200)
        self.assertEqual(self.client.post(path + 'review/', {'decision': 'approve', 'note': 'Owner attempting review', 'criteria_confirmed': True}, format='json').status_code, 403)
        self.client.force_authenticate(self.reviewer)
        reviewed = self.client.post(path + 'review/', {'decision': 'approve', 'note': 'Independent criteria confirmed', 'criteria_confirmed': True}, format='json')
        self.assertEqual(reviewed.status_code, 200, reviewed.data)
        self.assertEqual(self.client.post(path + 'accept/', {'note': 'Reviewer lacks acceptance authority'}, format='json').status_code, 403)
        self.client.force_authenticate(self.authority)
        accepted = self.client.post(path + 'accept/', {'note': 'Authority accepts reviewed deliverable'}, format='json')
        self.assertEqual(accepted.status_code, 200, accepted.data)
        self.assertEqual(self.client.patch(path, {'title': 'Rewrite accepted record'}, format='json').status_code, 400)
        self.assertEqual(self.client.post(path + 'review/', {'decision': 'return', 'note': 'Attempt to return accepted work'}, format='json').status_code, 403)
        self.assertEqual(ScheduleControlSnapshot.objects.count(), 1)

    def test_api_rejects_predecessor_cycles_and_cross_project_document(self):
        self.client.force_authenticate(self.authority)
        path = f'/api/v1/project-control/epc-work-items/{self.items[0].pk}/'
        cycle = self.client.patch(path, {'predecessors': [self.items[-1].pk]}, format='json')
        self.assertEqual(cycle.status_code, 400)
        self.assertFalse(self.items[0].predecessors.exists())
        self.document.project = self.other
        self.document.save(update_fields=['project'])
        response = self.client.patch(path, {'documents': [self.document.pk]}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_unknown_or_malformed_receipt_requirements_do_not_become_ready(self):
        self.order.ndt_requirements = 'Determine after inspection'
        self.order.save(update_fields=['ndt_requirements'])
        self.assertFalse(material_readiness(self.items[1])['ready'])
        self.order.ndt_requirements = 'not required'
        self.order.required_certifications = {'MTC': True}
        self.order.save(update_fields=['ndt_requirements', 'required_certifications'])
        self.assertFalse(material_readiness(self.items[1])['ready'])

    def reporting_period(self):
        return ReportingPeriod.objects.create(project=self.project, sequence=1, name='Baseline reporting test',
            start_date=self.data_date.replace(day=1), end_date=self.data_date, data_date=self.data_date, created_by=self.owner)

    def test_later_approved_budget_does_not_change_frozen_reporting_bac(self):
        self.complete(self.items[0])
        correction = BudgetAllocation.objects.create(project=self.project, wbs_node=self.nodes[0],
            code='LATER-BUDGET', name='Later separately approved budget', amount=5000, currency='AED')
        self.post_existing(f'budget-allocations/{correction.pk}/approve/', user=self.authority)
        period = self.reporting_period()
        self.post_existing(f'reporting-periods/{period.pk}/submit/', user=self.owner)
        response = self.post_existing(f'reporting-periods/{period.pk}/lock/', user=self.authority)
        sealed = IntegratedReportingSnapshot.objects.get(pk=response.data['snapshot']['id'])
        self.assertEqual(sealed.budget_at_completion, Decimal('100000'))
        self.assertEqual(sealed.earned_value, Decimal('25000'))
        self.assertEqual(sealed.source_manifest['integrated_baseline_id'], self.baseline.pk)
        self.assertEqual(len(sealed.source_manifest['epc_acceptances']), 1)

    def test_reporting_with_epc_baseline_requires_observation_from_its_exact_version(self):
        self.project.progress = 87
        self.project.save(update_fields=['progress'])
        other_version = ScheduleVersion.objects.create(schedule=self.version.schedule, version=2,
            status='approved', created_by=self.authority)
        ScheduleControlSnapshot.objects.create(version=other_version, data_date=self.data_date,
            progress_pct=99, planned_progress_pct=100, captured_by=self.authority)
        period = self.reporting_period()
        self.post_existing(f'reporting-periods/{period.pk}/submit/', user=self.owner)
        self.client.force_authenticate(self.authority)
        response = self.client.post(f'/api/v1/project-control/reporting-periods/{period.pk}/lock/')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(IntegratedReportingSnapshot.objects.count(), 0)
        period.refresh_from_db()
        self.assertEqual(period.status, 'submitted')

    def test_reporting_uses_latest_effective_baseline_not_future_revision(self):
        self.complete(self.items[0])
        correction = BudgetAllocation.objects.create(project=self.project, wbs_node=self.nodes[0],
            code='REBASE-BUDGET', name='Approved rebased allowance', amount=5000, currency='AED')
        self.post_existing(f'budget-allocations/{correction.pk}/approve/', user=self.authority)
        approved_budget_ids = [row.pk for row in self.budgets] + [correction.pk]
        revised = capture_integrated_baseline(self.project, {'schedule_baseline': self.baseline.schedule_baseline_id,
            'budget_ids': approved_budget_ids, 'name': 'Effective revised baseline', 'data_date': self.data_date.isoformat()}, user=self.authority)
        future = capture_integrated_baseline(self.project, {'schedule_baseline': self.baseline.schedule_baseline_id,
            'budget_ids': approved_budget_ids, 'name': 'Future baseline', 'data_date': (self.data_date + timedelta(days=1)).isoformat()}, user=self.authority)
        self.assertGreater(future.revision, revised.revision)
        period = self.reporting_period()
        self.post_existing(f'reporting-periods/{period.pk}/submit/', user=self.owner)
        response = self.post_existing(f'reporting-periods/{period.pk}/lock/', user=self.authority)
        sealed = IntegratedReportingSnapshot.objects.get(pk=response.data['snapshot']['id'])
        self.assertEqual(sealed.budget_at_completion, Decimal('105000'))
        self.assertEqual(sealed.source_manifest['integrated_baseline_id'], revised.pk)
        self.assertEqual(sealed.source_manifest['integrated_baseline_revision'], revised.revision)
