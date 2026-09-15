"""Evidence, authorization and rollback checks against the disposable EPC pilot."""
from datetime import timedelta
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.project_models import ProjectMilestone
from apps.core.project_views import ProjectMilestoneViewSet
from apps.planning_intelligence.models import ActivityProgressUpdate, DailyFieldUpdate, ScheduleActivity, ScheduleControlSnapshot
from apps.procurement.models import Receipt
from ..execution_models import EPCWorkEvent, EPCWorkItem
from ..models import WBSNode
from ..services.execution import accept_work, material_readiness, review_work
from . import test_epc_execution as pilot


class EpcExecutionAdversarialTests(TestCase):
    setUp = pilot.EpcExecutionTests.setUp
    post_existing = pilot.EpcExecutionTests.post_existing
    review = pilot.EpcExecutionTests.review
    complete = pilot.EpcExecutionTests.complete

    def assert_no_completion(self):
        self.assertFalse(ActivityProgressUpdate.objects.exists())
        self.assertFalse(ScheduleControlSnapshot.objects.exists())
        self.assertFalse(EPCWorkEvent.objects.filter(action='accepted').exists())
        self.milestone.refresh_from_db()
        self.assertFalse(self.milestone.is_completed)

    def test_bulk_progress_rejects_mixed_batch_before_posting_any_row(self):
        unlinked = ScheduleActivity.objects.create(version=self.version, external_id='UNLINKED', name='Unlinked activity')
        self.client.force_authenticate(self.owner)
        response = self.client.post(f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/progress/', {
            'data_date': self.data_date.isoformat(),
            'updates': [{'activity': unlinked.pk, 'physical_progress_pct': '50'},
                        {'activity': self.activities[0].pk, 'physical_progress_pct': '100'}],
        }, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('EPC', str(response.data))
        self.assert_no_completion()

    def test_daily_field_approval_cannot_bypass_linked_work_acceptance(self):
        update = DailyFieldUpdate.objects.create(version=self.version, activity=self.activities[0],
            report_date=self.data_date, status='submitted', physical_progress_pct=100,
            reported_by=self.owner, submitted_at=timezone.now())
        self.client.force_authenticate(self.owner)
        response = self.client.post(f'/api/v1/planning-intelligence/daily-field-updates/{update.pk}/approve/',
                                    {'comment': 'Manual progress bypass attempt'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('EPC', str(response.data))
        update.refresh_from_db()
        self.assertEqual(update.status, 'submitted')
        self.assertIsNone(update.reviewed_at)
        self.assertIsNone(update.applied_progress_update_id)
        self.assert_no_completion()

    def test_core_milestone_patch_cannot_complete_linked_work_without_acceptance(self):
        request = APIRequestFactory().patch('/milestones/', {
            'is_completed': True, 'completed_date': self.data_date.isoformat(),
        }, format='json')
        force_authenticate(request, user=self.authority)
        response = ProjectMilestoneViewSet.as_view({'patch': 'partial_update'})(request, pk=str(self.milestone.pk))
        self.assertEqual(response.status_code, 400, response.data)
        self.assert_no_completion()

    def test_acceptance_event_failure_rolls_back_progress_snapshot_and_milestone(self):
        item = self.items[-1]
        item.predecessors.clear()
        reviewed = self.review(item)
        schedule = self.version.schedule
        previous_date = schedule.data_date
        with patch.object(EPCWorkEvent.objects, 'create', side_effect=RuntimeError('Audit persistence failed')):
            with self.assertRaisesMessage(RuntimeError, 'Audit persistence failed'):
                accept_work(reviewed, user=self.authority, note='Acceptance must be atomic with its audit')
        self.assert_no_completion()
        item.refresh_from_db()
        schedule.refresh_from_db()
        self.assertEqual(item.status, 'reviewed')
        self.assertEqual(item.acceptance_manifest, {})
        self.assertIsNone(item.accepted_at)
        self.assertEqual(schedule.data_date, previous_date)
        self.assertEqual(list(item.events.values_list('action', flat=True)), ['submitted', 'reviewed'])

    def test_multiple_partial_receipts_count_only_accepted_quality_checked_quantities(self):
        self.receipt.status = 'partial'
        self.receipt.items_received = [{'code': 'MATERIAL-01', 'received_qty': '6', 'accepted_qty': '4'}]
        self.receipt.save(update_fields=['status', 'items_received', 'updated_at'])
        self.assertFalse(material_readiness(self.items[1])['ready'])
        second = Receipt.objects.create(receipt_number='PILOT-GR-SECOND', purchase_order=self.order, status='partial',
            items_received=[{'code': 'MATERIAL-01', 'received_qty': '8', 'accepted_qty': '6'}],
            certificates_received=['MTC'], heat_numbers=['HEAT-02'], inspector_name='Second inspector',
            quality_check_passed=True, dimensional_check_passed=True, visual_inspection_passed=True, material_verification_passed=True)
        readiness = material_readiness(self.items[1])
        self.assertTrue(readiness['ready'], readiness)
        self.assertEqual({row['id'] for row in readiness['receipts']}, {str(self.receipt.pk), str(second.pk)})
        self.complete(self.items[0])
        self.assertEqual(self.complete(self.items[1]).status, 'accepted')

    def test_po_delivery_status_without_dated_approval_is_not_material_readiness(self):
        original_actor, original_date = self.order.approved_by, self.order.approved_at
        cases = [(None, original_date), (original_actor, None),
                 (original_actor, timezone.now() + timedelta(days=1))]
        for actor, approved_at in cases:
            with self.subTest(actor=actor, approved_at=approved_at):
                self.order.approved_by, self.order.approved_at = actor, approved_at
                self.order.save(update_fields=['approved_by', 'approved_at', 'updated_at'])
                readiness = material_readiness(self.items[1])
                self.assertFalse(readiness['ready'], readiness)
        self.order.approved_by, self.order.approved_at = original_actor, original_date
        self.order.save(update_fields=['approved_by', 'approved_at', 'updated_at'])
        self.assertTrue(material_readiness(self.items[1])['ready'])
        self.assert_no_completion()

    def test_material_receipt_changed_after_review_requires_another_review(self):
        self.complete(self.items[0])
        item = self.review(self.items[1])
        self.receipt.heat_numbers = ['CHANGED-HEAT']
        self.receipt.save(update_fields=['heat_numbers', 'updated_at'])
        self.assertTrue(material_readiness(item)['ready'])
        with self.assertRaises(ValidationError):
            accept_work(item, user=self.authority, note='Current quantities do not prove review of changed traceability')
        item.refresh_from_db()
        self.assertEqual(item.status, 'reviewed')
        self.assertEqual(ScheduleControlSnapshot.objects.count(), 1)
        self.assertFalse(ActivityProgressUpdate.objects.filter(activity=self.activities[1]).exists())
        item = review_work(item, user=self.reviewer, decision='return', note='Changed material evidence needs review')
        accepted = self.complete(item)
        self.assertEqual(accepted.status, 'accepted')

    def test_changed_po_requirements_cannot_silently_reuse_material_review(self):
        self.complete(self.items[0])
        item = self.review(self.items[1])
        self.order.items = [{'code': 'MATERIAL-01', 'qty': '5'}]
        self.order.save(update_fields=['items', 'updated_at'])
        self.assertTrue(material_readiness(item)['ready'])
        with self.assertRaises(ValidationError):
            accept_work(item, user=self.authority, note='Order scope changed since review')
        self.assertFalse(ActivityProgressUpdate.objects.filter(activity=self.activities[1]).exists())

    def test_post_review_criteria_correction_cannot_reuse_old_review(self):
        item = self.review(self.items[0])
        EPCWorkItem.objects.filter(pk=item.pk).update(acceptance_criteria=['Additional witnessed pressure test'])
        with self.assertRaises(ValidationError):
            accept_work(item, user=self.authority, note='Old confirmation does not cover revised criteria')
        self.assert_no_completion()

    def test_archived_wbs_cannot_receive_acceptance(self):
        item = self.review(self.items[0])
        WBSNode.objects.filter(pk=item.wbs_node_id).update(is_deleted=True)
        with self.assertRaises(ValidationError):
            accept_work(item, user=self.authority, note='Archived work scope')
        self.assert_no_completion()

    def test_foreign_planning_project_cannot_receive_project_acceptance(self):
        item = self.review(self.items[0])
        planning = self.version.schedule.project
        planning.enterprise_project = self.other
        planning.save(update_fields=['enterprise_project'])
        with self.assertRaises(ValidationError):
            accept_work(item, user=self.authority, note='Schedule was moved to another project')
        self.assert_no_completion()

    def test_foreign_milestone_cannot_be_completed_by_acceptance(self):
        item = self.items[-1]
        item.predecessors.clear()
        reviewed = self.review(item)
        ProjectMilestone.objects.filter(pk=self.milestone.pk).update(project=self.other)
        with self.assertRaises(ValidationError):
            accept_work(reviewed, user=self.authority, note='Milestone association changed')
        self.assert_no_completion()

    def test_acceptance_preserves_previous_reported_cost_hours_and_actual_start(self):
        previous_date = self.data_date - timedelta(days=1)
        progress = ActivityProgressUpdate.objects.create(version=self.version, activity=self.activities[0],
            data_date=previous_date, physical_progress_pct=40, actual_start=previous_date,
            actual_cost=Decimal('4321.50'), actual_hours=Decimal('27.50'), reported_by=self.owner)
        accepted = self.complete(self.items[0])
        posted = accepted.progress_update
        self.assertEqual(posted.physical_progress_pct, Decimal('100'))
        self.assertEqual(posted.actual_cost, Decimal('4321.50'))
        self.assertEqual(posted.actual_hours, Decimal('27.50'))
        self.assertEqual(posted.actual_start, previous_date)
        self.assertEqual(posted.actual_finish, self.data_date)
        progress.refresh_from_db()
        self.assertEqual(progress.physical_progress_pct, Decimal('40'))
        self.assertEqual(accepted.acceptance_manifest['previous_progress'], '40.00')

    def test_event_history_cannot_be_rewritten_or_deleted_through_normal_orm(self):
        accepted = self.complete(self.items[0])
        event = accepted.events.get(action='accepted')
        original = event.payload
        for mutation in [lambda: EPCWorkEvent.objects.filter(pk=event.pk).update(payload={}),
                         lambda: EPCWorkEvent.objects.filter(pk=event.pk).delete(),
                         lambda: event.delete()]:
            with self.subTest(mutation=mutation):
                with self.assertRaises(ValueError):
                    mutation()
        event.note = 'Rewritten decision'
        with self.assertRaises(ValueError):
            event.save()
        event.refresh_from_db()
        self.assertEqual(event.payload, original)
        accepted.title = 'Rewritten accepted work'
        for mutation in [lambda: accepted.save(),
                         lambda: EPCWorkItem.objects.filter(pk=accepted.pk).update(title=accepted.title),
                         lambda: EPCWorkItem.objects.bulk_update([accepted], ['title']),
                         lambda: EPCWorkItem.objects.bulk_create([accepted], update_conflicts=True,
                                                                 update_fields=['title'], unique_fields=['id'])]:
            with self.subTest(accepted_work_mutation=mutation):
                with self.assertRaises(ValueError):
                    mutation()
        accepted.refresh_from_db()
        self.assertEqual(accepted.title, 'Engineering')


class EpcAcceptanceConcurrencyTests(TransactionTestCase):
    setUp = pilot.EpcExecutionTests.setUp
    post_existing = pilot.EpcExecutionTests.post_existing
    review = pilot.EpcExecutionTests.review
    complete = pilot.EpcExecutionTests.complete

    @skipUnlessDBFeature('has_select_for_update')
    def test_simultaneous_acceptance_posts_one_progress_observation_and_event(self):
        item = self.review(self.items[0])
        barrier = Barrier(2)

        def accept_once():
            close_old_connections()
            try:
                current = EPCWorkItem.objects.get(pk=item.pk)
                barrier.wait(timeout=10)
                accepted = accept_work(current, user=self.authority, note='Concurrent delivery retry')
                return accepted.pk, accepted.progress_update_id, accepted.control_snapshot_id
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(accept_once) for _ in range(2)]
            results = [future.result(timeout=20) for future in futures]
        self.assertEqual(results[0], results[1])
        self.assertEqual(ActivityProgressUpdate.objects.filter(activity=item.activity).count(), 1)
        self.assertEqual(ScheduleControlSnapshot.objects.filter(version=self.version).count(), 1)
        self.assertEqual(EPCWorkEvent.objects.filter(work_item=item, action='accepted').count(), 1)
