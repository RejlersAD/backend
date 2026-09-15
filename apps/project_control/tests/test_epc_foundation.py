from datetime import date
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.db import close_old_connections
from django.db.models.deletion import ProtectedError
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.project_models import Project, ProjectMember
from apps.planning_intelligence.models import PlanningProject, Schedule, ScheduleActivity, ScheduleBaseline, ScheduleVersion
from apps.procurement.models import ProjectRelationshipResolution, PurchaseRequisition
from apps.users.models import User
from ..epc_models import IntegratedBaseline, RequisitionWBSLink, WBSActivityLink
from ..epc_views import EpcProjectViewSet
from ..models import BudgetAllocation, ControlAccount, WBSNode
from ..services.epc import (
    EPC_PHASES, associate_requisition, capture_integrated_baseline, delete_activity_link,
    requisition_payload, save_activity_link, setup_epc_project, setup_payload,
)


class EpcFoundationTests(TestCase):
    def setUp(self):
        self.authority = User.objects.create_superuser(username='epc-authority', email='epc-authority@example.test', password='unused')
        self.owner = User.objects.create_user(username='epc-owner', email='epc-owner@example.test')
        self.viewer = User.objects.create_user(username='epc-viewer', email='epc-viewer@example.test')
        self.outsider = User.objects.create_user(username='epc-outsider', email='epc-outsider@example.test')
        self.project = Project.objects.create(code='EPC-001', name='Existing project', owner=self.owner)
        self.other = Project.objects.create(code='OTHER-001', name='Other project', owner=self.outsider)
        ProjectMember.objects.create(project=self.project, user=self.viewer, role='viewer')
        self.values = {'code': 'EPC-001', 'name': 'Full EPC pilot', 'client_name': 'Pilot client',
                       'owner': self.owner.pk, 'start_date': '2026-01-01', 'end_date': '2026-12-31',
                       'currency': 'AED', 'scope_type': 'epc'}

    def setup_foundation(self):
        self.project = setup_epc_project(self.project, self.values, user=self.authority)
        return list(WBSNode.objects.filter(project=self.project).order_by('sort_order'))

    def approved_sources(self):
        nodes = self.setup_foundation()
        planning = PlanningProject.objects.create(enterprise_project=self.project, name='Pilot planning', created_by=self.authority)
        schedule = Schedule.objects.create(project=planning, name='Full EPC', code='MASTER', planned_start=date(2026, 1, 1))
        version = ScheduleVersion.objects.create(schedule=schedule, version=1, status='baselined', created_by=self.authority)
        budgets, activities = [], []
        for index, (node, link_type) in enumerate(zip(nodes, ['engineering', 'procurement', 'construction', 'commissioning'])):
            activity = ScheduleActivity.objects.create(version=version, external_id=f'ACT-{index}', name=node.name,
                planned_start=date(2026, 1, 1), planned_finish=date(2026, 12, 31))
            activities.append(activity)
            save_activity_link(self.project, {'wbs_node': node.pk, 'activity': activity.pk, 'link_type': link_type}, user=self.owner)
            ControlAccount.objects.create(project=self.project, wbs_node=node, code=f'CA-{index}', name=node.name,
                manager=self.owner, baseline_start=date(2026, 1, 1), baseline_finish=date(2026, 12, 31),
                status='active', approved_by=self.authority, approved_at=timezone.now())
            budgets.append(BudgetAllocation.objects.create(project=self.project, wbs_node=node, code=f'B-{index}', name=node.name,
                amount=Decimal('25000'), currency='AED', status='approved', approved_by=self.authority, approved_at=timezone.now()))
        baseline = ScheduleBaseline.objects.create(schedule=schedule, source_version=version, name='Approved schedule',
            data_date=date(2026, 1, 1), approved_by=self.authority, approved_at=timezone.now(),
            snapshot={'activities': [{'id': row.pk, 'external_id': row.external_id, 'planned_finish': '2026-12-31'} for row in activities]})
        self.version, self.baseline, self.budgets, self.activities = version, baseline, budgets, activities
        self.baseline_values = {'schedule_baseline': baseline.pk, 'budget_ids': [row.pk for row in budgets],
                                'name': 'Integrated baseline', 'data_date': '2026-01-01'}

    def api(self, action, method='get', data=None, user=None, project=None):
        project = project or self.project
        request = getattr(APIRequestFactory(), method)(f'/api/v1/project-control/epc-projects/{project.pk}/{action}/', data or {}, format='json')
        if user is not False:
            force_authenticate(request, user=user or self.authority)
        return EpcProjectViewSet.as_view({method: action})(request, pk=str(project.pk))

    def test_setup_validates_and_reuses_existing_project_and_four_roots(self):
        before = Project.objects.count()
        first = self.api('setup', 'post', self.values)
        second = self.api('setup', 'post', self.values)
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertTrue(second.data['ready'])
        self.assertEqual(Project.objects.count(), before)
        self.assertEqual(WBSNode.objects.filter(project=self.project).count(), 4)
        self.assertEqual(set(WBSNode.objects.filter(project=self.project).values_list('code', 'name')), set(EPC_PHASES))

    def test_invalid_dates_owner_currency_scope_and_code_are_atomic(self):
        self.owner.is_active = False
        self.owner.save(update_fields=['is_active'])
        cases = [{'end_date': '2025-12-31'}, {'currency': 'XYZ'}, {'scope_type': 'engineering'},
                 {'code': self.other.code}, {'owner': self.owner.pk}, {'client_name': ''}]
        for override in cases:
            with self.subTest(override=override):
                payload = {**self.values, 'owner': self.authority.pk, **override}
                self.assertEqual(self.api('setup', 'post', payload).status_code, 400)
        self.project.refresh_from_db()
        self.assertEqual(self.project.name, 'Existing project')
        self.assertEqual(WBSNode.objects.filter(project=self.project).count(), 0)

    def test_conflicting_root_does_not_rename_or_partially_update_project(self):
        WBSNode.objects.create(project=self.project, code='EPC-CON', name='Existing unrelated scope')
        self.assertEqual(self.api('setup', 'post', self.values).status_code, 400)
        self.project.refresh_from_db()
        self.assertEqual(self.project.name, 'Existing project')
        self.assertEqual(WBSNode.objects.filter(project=self.project).count(), 1)

    def test_api_read_and_write_permissions(self):
        self.assertEqual(self.api('setup', user=self.viewer).status_code, 200)
        self.assertEqual(self.api('setup', 'post', self.values, user=self.viewer).status_code, 403)
        self.assertEqual(self.api('setup', user=self.outsider).status_code, 404)
        self.assertIn(self.api('setup', user=False).status_code, (401, 403))
        self.assertFalse(self.api('setup', user=self.viewer).data['capabilities']['can_setup'])

    def test_activity_links_reject_foreign_wbs_and_schedule_activity(self):
        self.approved_sources()
        foreign_node = WBSNode.objects.create(project=self.other, code='FOREIGN', name='Other')
        valid = {'wbs_node': self.budgets[0].wbs_node_id, 'activity': self.activities[0].pk, 'link_type': 'engineering'}
        self.assertEqual(self.api('links', 'post', {**valid, 'wbs_node': foreign_node.pk}).status_code, 400)
        self.version.schedule.project.enterprise_project = self.other
        self.version.schedule.project.save(update_fields=['enterprise_project'])
        self.assertEqual(self.api('links', 'post', valid).status_code, 400)

    def test_activity_link_update_delete_and_restore_do_not_duplicate(self):
        self.approved_sources()
        link = WBSActivityLink.objects.get(activity=self.activities[0])
        response = self.api('links', 'post', {'id': link.pk, 'wbs_node': link.wbs_node_id,
            'activity': link.activity_id, 'link_type': 'engineering', 'notes': 'Reviewed mapping'})
        self.assertEqual(response.data['notes'], 'Reviewed mapping')
        self.assertEqual(self.api('links', 'delete', {'id': link.pk}).status_code, 204)
        response = self.api('links', 'post', {'wbs_node': link.wbs_node_id, 'activity': link.activity_id, 'link_type': 'engineering'})
        self.assertEqual(response.data['id'], link.pk)
        self.assertEqual(WBSActivityLink.objects.count(), 4)

    def test_requisition_get_suggests_exact_codes_without_mutating_157_records(self):
        self.setup_foundation()
        PurchaseRequisition.objects.bulk_create([PurchaseRequisition(pr_number=f'LEGACY-{index}',
            project=self.project.code, title='Preserved PR', status='approved') for index in range(157)])
        payload = requisition_payload(self.project, self.authority)
        self.assertEqual(len(payload['results']), 157)
        self.assertTrue(all(row['match_status'] == 'exact_match' for row in payload['results']))
        self.assertEqual(PurchaseRequisition.objects.filter(enterprise_project__isnull=True, status='approved').count(), 157)
        self.assertEqual(RequisitionWBSLink.objects.count(), 0)
        self.assertEqual(ProjectRelationshipResolution.objects.count(), 0)

    def test_explicit_requisition_association_reuses_audit_and_keeps_approval(self):
        nodes = self.setup_foundation()
        pr = PurchaseRequisition.objects.create(pr_number='EXPLICIT-PR', project=self.project.code, status='approved')
        response = self.api('requisitions', 'post', {'requisition': str(pr.pk), 'wbs_node': nodes[1].pk, 'reason': 'Confirmed project and procurement WBS'})
        self.assertEqual(response.status_code, 201, response.data)
        pr.refresh_from_db()
        self.assertEqual(pr.enterprise_project, self.project)
        self.assertEqual(pr.status, 'approved')
        self.assertEqual(ProjectRelationshipResolution.objects.filter(record_id=pr.pk).count(), 1)
        self.assertEqual(RequisitionWBSLink.objects.get(requisition=pr).wbs_node, nodes[1])

    def test_ambiguous_or_unknown_pr_needs_explicit_authority_review(self):
        node = self.setup_foundation()[1]
        pr = PurchaseRequisition.objects.create(pr_number='AMBIGUOUS', project=self.project.code,
            project_details=[{'project_number': self.other.code}])
        values = {'requisition': str(pr.pk), 'wbs_node': node.pk, 'reason': 'Reviewed authoritative contract documents'}
        self.assertEqual(self.api('requisitions', 'post', values).status_code, 400)
        self.assertEqual(self.api('requisitions', 'post', {**values, 'review_confirmed': True}).status_code, 201)

    def test_existing_cross_project_pr_is_never_reassigned_even_with_confirmation(self):
        node = self.setup_foundation()[1]
        pr = PurchaseRequisition.objects.create(pr_number='FOREIGN-PR', enterprise_project=self.other, project=self.other.code)
        values = {'requisition': str(pr.pk), 'wbs_node': node.pk, 'reason': 'Attempted manual reassignment', 'review_confirmed': True}
        self.assertEqual(self.api('requisitions', 'post', values).status_code, 400)
        pr.refresh_from_db()
        self.assertEqual(pr.enterprise_project, self.other)
        self.assertEqual(RequisitionWBSLink.objects.count(), 0)

    def test_requisition_association_requires_commercial_permission(self):
        node = self.setup_foundation()[1]
        pr = PurchaseRequisition.objects.create(pr_number='OWNER-PR', project=self.project.code)
        self.assertEqual(self.api('requisitions', 'post', {'requisition': str(pr.pk), 'wbs_node': node.pk,
            'reason': 'Owner without commercial access'}, user=self.owner).status_code, 403)

    def test_baseline_captures_approved_source_values_and_revisions_without_mutation(self):
        self.approved_sources()
        first = capture_integrated_baseline(self.project, self.baseline_values, user=self.authority)
        original_manifest, checksum = first.manifest, first.checksum
        self.budgets[0].name = 'Later budget description'
        self.budgets[0].save(update_fields=['name'])
        second = capture_integrated_baseline(self.project, self.baseline_values, user=self.authority)
        self.assertEqual((first.revision, second.revision), (1, 2))
        self.assertEqual(first.budget_total, Decimal('100000'))
        first.refresh_from_db()
        self.assertEqual(first.manifest, original_manifest)
        self.assertEqual(first.checksum, checksum)
        self.assertEqual(first.manifest['budgets'][0]['name'], 'Engineering')
        self.assertEqual(second.manifest['budgets'][0]['name'], 'Later budget description')
        self.version.refresh_from_db()
        self.assertEqual(self.version.status, 'baselined')
        self.assertEqual(BudgetAllocation.objects.filter(status='approved').count(), 4)

    def test_baseline_immutable_orm_and_parent_protection(self):
        self.approved_sources()
        row = capture_integrated_baseline(self.project, self.baseline_values, user=self.authority)
        row.name = 'Overwrite'
        with self.assertRaisesMessage(ValueError, 'immutable'):
            row.save()
        with self.assertRaisesMessage(ValueError, 'immutable'):
            IntegratedBaseline.objects.filter(pk=row.pk).update(name='Overwrite')
        with self.assertRaisesMessage(ValueError, 'deleted'):
            row.delete()
        with self.assertRaises(ProtectedError):
            self.baseline.delete()

    def test_baseline_rejects_unapproved_schedule_budget_currency_and_foreign_budget(self):
        self.approved_sources()
        for field, value in [('status', 'draft'), ('approved_by', None), ('currency', 'USD')]:
            budget = self.budgets[0]
            original = getattr(budget, field)
            setattr(budget, field, value)
            budget.save(update_fields=[field])
            self.assertEqual(self.api('baseline', 'post', self.baseline_values).status_code, 400)
            setattr(budget, field, original)
            budget.save(update_fields=[field])
        self.baseline.approved_by = None
        self.baseline.save(update_fields=['approved_by'])
        self.assertEqual(self.api('baseline', 'post', self.baseline_values).status_code, 400)
        self.assertEqual(IntegratedBaseline.objects.count(), 0)

    def test_baseline_requires_complete_shared_scope_and_control_account_approval(self):
        self.approved_sources()
        delete_activity_link(self.project, WBSActivityLink.objects.get(activity=self.activities[0]).pk, user=self.owner)
        self.assertEqual(self.api('baseline', 'post', self.baseline_values).status_code, 400)
        save_activity_link(self.project, {'wbs_node': self.budgets[0].wbs_node_id,
            'activity': self.activities[0].pk, 'link_type': 'engineering'}, user=self.owner)
        ControlAccount.objects.filter(wbs_node=self.budgets[0].wbs_node).update(status='draft')
        self.assertEqual(self.api('baseline', 'post', self.baseline_values).status_code, 400)
        self.assertEqual(IntegratedBaseline.objects.count(), 0)

    def test_baseline_requires_existing_commercial_and_schedule_authority(self):
        self.approved_sources()
        self.assertEqual(self.api('baseline', 'post', self.baseline_values, user=self.owner).status_code, 403)
        self.assertEqual(self.api('baseline', 'post', self.baseline_values, user=self.viewer).status_code, 403)
        with patch('apps.project_control.services.epc.can_final_approve_defaults', return_value=False):
            self.assertEqual(self.api('baseline', 'post', self.baseline_values).status_code, 403)
        self.assertEqual(self.api('baseline', 'post', self.baseline_values).status_code, 201)
        payload = self.api('baseline').data
        self.assertEqual(len(payload['results']), 1)
        self.assertTrue(payload['can_capture'])

    def test_failed_baseline_does_not_consume_revision(self):
        self.approved_sources()
        with patch('apps.project_control.services.epc.IntegratedBaseline.objects.create', side_effect=RuntimeError('storage failure')):
            with self.assertRaises(RuntimeError):
                capture_integrated_baseline(self.project, self.baseline_values, user=self.authority)
        self.assertEqual(capture_integrated_baseline(self.project, self.baseline_values, user=self.authority).revision, 1)


class EpcBaselineConcurrencyTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_simultaneous_first_baselines_receive_distinct_immutable_revisions(self):
        fixture = EpcFoundationTests()
        fixture.setUp()
        fixture.approved_sources()
        barrier = Barrier(2)

        def capture():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                row = capture_integrated_baseline(fixture.project, fixture.baseline_values, user=fixture.authority)
                return row.pk, row.revision
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: capture(), range(2)))
        self.assertEqual(sorted(revision for _, revision in results), [1, 2])
        self.assertEqual(len({identifier for identifier, _ in results}), 2)
        self.assertEqual(IntegratedBaseline.objects.filter(project=fixture.project).count(), 2)
