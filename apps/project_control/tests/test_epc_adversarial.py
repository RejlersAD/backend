"""Adversarial EPC checks; all business fixtures live only in Django's test DB."""
import hashlib
import json
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.planning_intelligence.models import PlanningProject, Schedule, ScheduleActivity, ScheduleBaseline, ScheduleVersion
from apps.procurement.models import (
    Budget as ProcurementBudget, Project as ProcurementProject,
    ProjectRelationshipResolution, PurchaseOrder, PurchaseRequisition, Vendor,
)
from ..epc_models import IntegratedBaseline, RequisitionWBSLink, WBSActivityLink
from ..models import BudgetAllocation, ControlAccount, WBSNode
from ..services.epc import associate_requisition, capture_integrated_baseline, save_activity_link
from ..views import BudgetAllocationViewSet, WBSNodeViewSet
from . import test_epc_foundation as foundation


class EpcAdversarialTests(TestCase):
    # Reuse setup helpers, without inheriting or rediscovering the foundation tests.
    setUp = foundation.EpcFoundationTests.setUp
    setup_foundation = foundation.EpcFoundationTests.setup_foundation
    approved_sources = foundation.EpcFoundationTests.approved_sources
    api = foundation.EpcFoundationTests.api

    def wbs_api(self, node=None, *, method='patch', data=None, user=None):
        action = {'patch': 'partial_update', 'post': 'create', 'delete': 'destroy'}[method]
        request = getattr(APIRequestFactory(), method)('/wbs/', data or {}, format='json')
        force_authenticate(request, user=user or self.owner)
        return WBSNodeViewSet.as_view({method: action})(request, **({'pk': str(node.pk)} if node else {}))

    def purchase_order(self, requisition, *, suffix='', **fields):
        vendor, _ = Vendor.objects.get_or_create(vendor_code='ADVERSARIAL', defaults={'name': 'Test vendor'})
        return PurchaseOrder.objects.create(
            po_number=f'PO-{requisition.pr_number}{suffix}', vendor=vendor,
            pr_reference=requisition, title='Preserved order', category='other',
            total_amount=Decimal('1234.50'), currency='AED', status='sent', **fields,
        )

    def association_values(self, requisition):
        return {'requisition': str(requisition.pk), 'wbs_node': self.nodes[1].pk,
                'reason': 'Reviewed original project and procurement scope', 'review_confirmed': True}

    def test_saved_baseline_activity_identity_survives_later_source_edits(self):
        self.approved_sources()
        activity = self.activities[0]
        saved_code = activity.external_id
        # Simulate a legacy correction outside the immutable schedule API.
        ScheduleActivity.objects.filter(pk=activity.pk).update(
            external_id='LATER-CODE', name='Later source title', planned_finish=date(2027, 1, 15),
        )
        row = capture_integrated_baseline(self.project, self.baseline_values, user=self.authority)
        mapped = next(item for item in row.manifest['activity_links'] if item['activity'] == activity.pk)
        saved = next(item for item in row.manifest['schedule_baseline']['snapshot']['activities'] if item['id'] == activity.pk)
        self.assertEqual(mapped['external_id'], saved_code)
        self.assertEqual(saved['external_id'], saved_code)
        self.assertEqual(saved['planned_finish'], '2026-12-31')

    def test_duplicate_saved_activity_ids_cannot_pass_scope_equality(self):
        self.approved_sources()
        self.baseline.snapshot['activities'].append(dict(self.baseline.snapshot['activities'][0]))
        self.baseline.save(update_fields=['snapshot'])
        response = self.api('baseline', 'post', self.baseline_values)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(IntegratedBaseline.objects.count(), 0)

    def test_saved_scope_ignores_unmapped_new_activity_but_rejects_extra_mapping(self):
        self.approved_sources()
        extra = ScheduleActivity.objects.create(version=self.version, external_id='LATER-SCOPE', name='Later activity')
        first = capture_integrated_baseline(self.project, self.baseline_values, user=self.authority)
        self.assertNotIn(extra.pk, [item['activity'] for item in first.manifest['activity_links']])
        save_activity_link(self.project, {'activity': extra.pk, 'wbs_node': self.budgets[0].wbs_node_id,
                                        'link_type': 'engineering'}, user=self.owner)
        response = self.api('baseline', 'post', self.baseline_values)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(list(IntegratedBaseline.objects.values_list('revision', flat=True)), [1])

    def test_baseline_rejects_foreign_source_version_even_if_link_ids_match(self):
        self.approved_sources()
        foreign = PlanningProject.objects.create(enterprise_project=self.other, name='Foreign plan', created_by=self.authority)
        schedule = Schedule.objects.create(project=foreign, name='Foreign schedule', code='FOREIGN', planned_start=date(2026, 1, 1))
        # Legacy FK corruption can disagree with baseline.schedule while preserving saved IDs.
        ScheduleVersion.objects.filter(pk=self.version.pk).update(schedule=schedule)
        response = self.api('baseline', 'post', self.baseline_values)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(IntegratedBaseline.objects.exists())

    def test_baseline_rejects_foreign_wbs_even_when_all_source_sets_agree(self):
        self.approved_sources()
        foreign = WBSNode.objects.create(project=self.other, code='FOREIGN-WBS', name='Foreign WBS')
        original = self.budgets[0].wbs_node_id
        BudgetAllocation.objects.filter(pk=self.budgets[0].pk).update(wbs_node=foreign)
        ControlAccount.objects.filter(wbs_node_id=original).update(wbs_node=foreign)
        WBSActivityLink.objects.filter(activity=self.activities[0]).update(wbs_node=foreign)
        response = self.api('baseline', 'post', self.baseline_values)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(IntegratedBaseline.objects.exists())

    def test_baseline_rejects_archived_sources_and_earlier_data_date_without_revision(self):
        self.approved_sources()
        for source in [self.version, self.baseline, self.budgets[0]]:
            with self.subTest(source=type(source).__name__):
                type(source).objects.filter(pk=source.pk).update(is_deleted=True)
                response = self.api('baseline', 'post', self.baseline_values)
                self.assertEqual(response.status_code, 400, response.data)
                type(source).objects.filter(pk=source.pk).update(is_deleted=False)
        response = self.api('baseline', 'post', {**self.baseline_values, 'data_date': '2025-12-31'})
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(IntegratedBaseline.objects.exists())
        self.assertEqual(capture_integrated_baseline(self.project, self.baseline_values, user=self.authority).revision, 1)

    def test_baseline_rejects_foreign_archived_and_cyclic_legacy_wbs_ancestry(self):
        self.approved_sources()
        original = self.budgets[0].wbs_node_id
        leaf = WBSNode.objects.create(project=self.project, code='LEAF', name='Mapped leaf')
        foreign = WBSNode.objects.create(project=self.other, code='FOREIGN', name='Foreign parent')
        archived = WBSNode.objects.create(project=self.project, code='ARCHIVED', name='Archived parent', is_deleted=True)
        BudgetAllocation.objects.filter(pk=self.budgets[0].pk).update(wbs_node=leaf)
        ControlAccount.objects.filter(wbs_node_id=original).update(wbs_node=leaf)
        WBSActivityLink.objects.filter(activity=self.activities[0]).update(wbs_node=leaf)
        for parent in [foreign, archived, leaf]:
            with self.subTest(parent=parent.code):
                WBSNode.objects.filter(pk=leaf.pk).update(parent=parent)
                response = self.api('baseline', 'post', self.baseline_values)
                self.assertEqual(response.status_code, 400, response.data)
                self.assertFalse(IntegratedBaseline.objects.exists())
        WBSNode.objects.filter(pk=leaf.pk).update(parent_id=original)
        self.assertEqual(capture_integrated_baseline(self.project, self.baseline_values, user=self.authority).revision, 1)

    def test_approved_control_budget_remains_basis_when_procurement_budget_changes(self):
        self.approved_sources()
        master = ProcurementProject.objects.create(project_number=self.project.code, project_name='Pilot', enterprise_project=self.project)
        source = ProcurementBudget.objects.create(project=master, category='other', allocated_amount=Decimal('25000'), currency='AED')
        BudgetAllocation.objects.filter(pk=self.budgets[0].pk).update(source_budget=source)
        source.allocated_amount = Decimal('900000')
        source.currency = 'USD'
        source.save(update_fields=['allocated_amount', 'currency'])
        row = capture_integrated_baseline(self.project, self.baseline_values, user=self.authority)
        self.assertEqual(row.budget_total, Decimal('100000'))
        self.assertEqual(row.currency, 'AED')
        self.assertEqual(Decimal(row.manifest['budgets'][0]['amount']), Decimal('25000'))

    def test_existing_budget_api_cannot_change_approved_amount_or_wbs(self):
        self.approved_sources()
        allocation = self.budgets[0]
        for data in [{'amount': '99000'}, {'wbs_node': self.budgets[1].wbs_node_id}]:
            with self.subTest(data=data):
                request = APIRequestFactory().patch('/budget/', data, format='json')
                force_authenticate(request, user=self.authority)
                response = BudgetAllocationViewSet.as_view({'patch': 'partial_update'})(request, pk=str(allocation.pk))
                self.assertEqual(response.status_code, 400, response.data)
        allocation.refresh_from_db()
        self.assertEqual(allocation.amount, Decimal('25000'))
        self.assertEqual(allocation.wbs_node_id, self.activities[0].enterprise_wbs_link.wbs_node_id)

    def test_manifest_checksum_and_original_sources_stay_stable_after_live_edits(self):
        self.approved_sources()
        row = capture_integrated_baseline(self.project, self.baseline_values, user=self.authority)
        original = json.loads(json.dumps(row.manifest))
        checksum = hashlib.sha256(json.dumps(original, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(row.checksum, checksum)
        BudgetAllocation.objects.filter(pk=self.budgets[0].pk).update(amount=Decimal('50000'))
        ScheduleBaseline.objects.filter(pk=self.baseline.pk).update(snapshot={'activities': []})
        WBSNode.objects.filter(pk=self.budgets[0].wbs_node_id).update(name='Later correction')
        row.refresh_from_db()
        self.assertEqual(row.manifest, original)
        self.assertEqual(row.checksum, checksum)
        self.assertEqual(row.budget_total, Decimal('100000'))

    def test_pr_propagation_rejects_all_conflicting_po_project_references_atomically(self):
        self.nodes = self.setup_foundation()
        master = ProcurementProject.objects.create(project_number=self.other.code, project_name='Unresolved foreign master')
        foreign_budget = ProcurementBudget.objects.create(project=master, category='other', allocated_amount=Decimal('1000'))
        cases = [
            {'enterprise_project': self.other}, {'project_number': self.other.code},
            {'project': master}, {'budget_allocation': foreign_budget}, {'rad_project_no': self.other.code},
        ]
        for index, conflict in enumerate(cases):
            with self.subTest(conflict=conflict):
                pr = PurchaseRequisition.objects.create(pr_number=f'CONFLICT-{index}', project=self.project.code, status='approved')
                order = self.purchase_order(pr, **conflict)
                before = order.enterprise_project_id
                response = self.api('requisitions', 'post', self.association_values(pr))
                self.assertEqual(response.status_code, 400, response.data)
                pr.refresh_from_db()
                order.refresh_from_db()
                self.assertIsNone(pr.enterprise_project_id)
                self.assertEqual(pr.status, 'approved')
                self.assertEqual(order.enterprise_project_id, before)
                self.assertEqual(order.status, 'sent')
                self.assertEqual(order.total_amount, Decimal('1234.50'))
                self.assertFalse(RequisitionWBSLink.objects.filter(requisition=pr).exists())
        self.assertEqual(ProjectRelationshipResolution.objects.count(), 0)

    def test_explicit_pr_link_propagates_only_its_orders_and_keeps_approval_evidence(self):
        self.nodes = self.setup_foundation()
        pr = PurchaseRequisition.objects.create(pr_number='MATCHED', project=self.project.code, status='approved')
        unrelated = PurchaseRequisition.objects.create(pr_number='UNRELATED', project=self.project.code)
        approval_time = timezone.now()
        order = self.purchase_order(pr, project_number=f'  {self.project.code.lower()}  ',
                                    approved_by=self.authority, approved_at=approval_time)
        other_order = self.purchase_order(unrelated)
        response = self.api('requisitions', 'post', self.association_values(pr))
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['resolution']['propagated'], 1)
        order.refresh_from_db()
        pr.refresh_from_db()
        other_order.refresh_from_db()
        self.assertEqual((pr.enterprise_project_id, order.enterprise_project_id), (self.project.pk, self.project.pk))
        self.assertIsNone(other_order.enterprise_project_id)
        self.assertEqual((pr.status, order.status), ('approved', 'sent'))
        self.assertEqual(order.approved_at, approval_time)
        self.assertEqual(order.approved_by, self.authority)
        self.assertEqual(order.total_amount, Decimal('1234.50'))
        self.assertEqual(set(ProjectRelationshipResolution.objects.values_list('resolution', flat=True)), {'manual', 'propagated'})
        second = self.api('requisitions', 'post', self.association_values(pr))
        self.assertEqual(second.status_code, 201, second.data)
        self.assertFalse(second.data['resolution']['changed'])
        self.assertEqual(second.data['resolution']['propagated'], 0)
        self.assertEqual(ProjectRelationshipResolution.objects.count(), 2)
        self.assertEqual(RequisitionWBSLink.objects.count(), 1)

    def test_failed_link_save_rolls_back_pr_po_and_relationship_audits(self):
        self.nodes = self.setup_foundation()
        pr = PurchaseRequisition.objects.create(pr_number='ROLLBACK', project=self.project.code, status='approved')
        order = self.purchase_order(pr)
        with patch.object(RequisitionWBSLink, 'save', side_effect=RuntimeError('Simulated link persistence failure')):
            with self.assertRaisesMessage(RuntimeError, 'persistence failure'):
                associate_requisition(self.project, self.association_values(pr), user=self.authority)
        pr.refresh_from_db()
        order.refresh_from_db()
        self.assertIsNone(pr.enterprise_project_id)
        self.assertIsNone(order.enterprise_project_id)
        self.assertEqual(pr.status, 'approved')
        self.assertEqual(order.status, 'sent')
        self.assertFalse(ProjectRelationshipResolution.objects.exists())
        self.assertFalse(RequisitionWBSLink.objects.exists())

    def test_wbs_api_cannot_move_owned_node_into_inaccessible_project(self):
        node = self.setup_foundation()[0]
        response = self.wbs_api(node, data={'project': self.other.pk})
        self.assertEqual(response.status_code, 400, response.data)
        node.refresh_from_db()
        self.assertEqual(node.project_id, self.project.pk)

    def test_wbs_api_rejects_foreign_archived_self_and_descendant_parents(self):
        node = self.setup_foundation()[0]
        foreign = WBSNode.objects.create(project=self.other, code='FOREIGN', name='Other scope')
        archived = WBSNode.objects.create(project=self.project, code='ARCHIVED', name='Archived', is_deleted=True)
        child = WBSNode.objects.create(project=self.project, parent=node, code='CHILD', name='Child')
        grandchild = WBSNode.objects.create(project=self.project, parent=child, code='GRANDCHILD', name='Grandchild')
        for parent in [foreign, archived, node, grandchild]:
            with self.subTest(parent=parent.code):
                response = self.wbs_api(node, data={'parent': parent.pk})
                self.assertEqual(response.status_code, 400, response.data)
        node.refresh_from_db()
        self.assertIsNone(node.parent_id)
        response = self.wbs_api(method='post', data={'project': self.project.pk, 'parent': foreign.pk, 'code': 'BAD', 'name': 'Invalid new node'})
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(WBSNode.objects.filter(project=self.project, code='BAD').exists())

    def test_wbs_api_rejects_a_preexisting_cycle_in_parent_ancestry(self):
        node, first, second, _ = self.setup_foundation()
        WBSNode.objects.filter(pk=first.pk).update(parent=second)
        WBSNode.objects.filter(pk=second.pk).update(parent=first)
        response = self.wbs_api(node, data={'parent': first.pk})
        self.assertEqual(response.status_code, 400, response.data)
        node.refresh_from_db()
        self.assertIsNone(node.parent_id)

    def test_wbs_api_allows_unapproved_scope_edits_and_preserves_viewer_permissions(self):
        nodes = self.setup_foundation()
        node = WBSNode.objects.create(project=self.project, code='DRAFT', name='Draft work package')
        response = self.wbs_api(node, data={'parent': nodes[0].pk, 'name': 'Reviewed scope', 'sort_order': 7})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['parent'], nodes[0].pk)
        self.assertEqual(response.data['name'], 'Reviewed scope')
        self.assertEqual(self.wbs_api(node, data={'name': 'Viewer edit'}, user=self.viewer).status_code, 403)

    def test_wbs_approved_scope_is_frozen_but_display_order_remains_editable(self):
        self.approved_sources()
        node = self.budgets[0].wbs_node
        for data in [{'code': 'RENAMED'}, {'name': 'Different scope'}, {'parent': self.budgets[1].wbs_node_id}]:
            with self.subTest(data=data):
                response = self.wbs_api(node, data=data)
                self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self.wbs_api(node, data={'sort_order': 99}).status_code, 200)

    def test_manifest_only_wbs_scope_cannot_be_renamed_or_deleted(self):
        self.approved_sources()
        node = WBSNode.objects.create(project=self.project, code='INCLUDED', name='Unallocated recorded scope')
        baseline = capture_integrated_baseline(self.project, self.baseline_values, user=self.authority)
        self.assertIn(node.pk, [row['id'] for row in baseline.manifest['wbs']])
        self.assertEqual(self.wbs_api(node, data={'name': 'Changed after sealing'}).status_code, 400)
        response = self.wbs_api(node, method='delete')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertTrue(WBSNode.objects.filter(pk=node.pk, is_deleted=False).exists())
