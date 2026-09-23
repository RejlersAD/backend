"""A native PO association does not finish or interrupt its PR's decisions."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.notifications.delivery import delivery_issue
from apps.notifications.models import Notification
from apps.procurement.models import PurchaseOrder, PurchaseRequisition, Vendor
from apps.procurement.serializers import PurchaseOrderSerializer
from apps.procurement.services.procurement_lifecycle import PREVIOUS_STATUS, delete_order
from apps.procurement.services.requisition_workflow import RequisitionWorkflowService

from .approval_fixtures import grant_approval, set_position


@override_settings(
    TEAMS_APPROVAL_WEBHOOK_URL='https://teams.example.test/isolated-lifecycle',
    WEB_PUSH_VAPID_PRIVATE_KEY='',
)
class NativeOrderRequisitionLifecycleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        for task in (
            'apps.notifications.teams.send_teams_approval_assignment.delay',
            'apps.notifications.services.send_notification_email.delay',
            'apps.notifications.services.send_web_push_notification.delay',
        ):
            patcher = patch(task)
            mocked = patcher.start()
            self.addCleanup(patcher.stop)
            if 'teams.' in task:
                self.teams_task = mocked
        self.users = []
        for index, title in enumerate(('Procurement Manager', 'Procurement Manager', 'CEO')):
            user = get_user_model().objects.create_user(
                f'native-link-{index}', email=f'native-link-{index}@example.test',
            )
            grant_approval(user)
            set_position(user, title)
            profile = user.rbac_profile
            profile.signature_image = f'lifecycle-signature-{index}'
            profile.save(update_fields=['signature_image'])
            self.users.append(user)
        self.first, self.second, self.ceo = self.users
        self.request = SimpleNamespace(user=self.first)
        self.vendor = Vendor.objects.create(vendor_code='NATIVE-LIFECYCLE', name='Native lifecycle supplier')
        self.pr = self.make_pr(100)

    def make_pr(self, sequence, status='draft'):
        return PurchaseRequisition.objects.create(
            pr_number=f'RAD-PRJ-PR-{sequence:04d}_2026', vendor=self.vendor,
            issued_by=self.first, requested_by=self.first, status=status,
            po_applicable=True, total_price='100.00', currency='AED',
            approval_workflow_config=[{
                'stage': f'Level {level} Procurement Review', 'role': 'Procurement Manager',
                'level': level, 'user_id': str(user.pk), 'user_email': user.email,
                'status': 'pending', 'assignment_id': f'pr-stage-{sequence}-{level}',
            } for level, user in enumerate((self.first, self.second))],
            price_remarks_data={'retained_metadata': 'unchanged'},
        )

    def create_order(self, pr=None, *, sequence=None, context=None, approval_log=None):
        pr = pr or self.pr
        data = {
            'pr_reference': str(pr.pk), 'vendor': str(self.vendor.pk),
            'title': 'Independent PO approval', 'total_amount': '100.00',
            'currency': 'AED', 'vat_percentage': '0.00', 'category': 'other', 'status': 'draft',
            'approval_log': approval_log if approval_log is not None else [{
                'stage': 'Final Management Sign-off', 'level': 0, 'user_id': str(self.ceo.pk),
            }],
        }
        if sequence is not None:
            data['po_number'] = f'RAD-PRJ-PUR-{sequence:04d}_2026'
        serializer = PurchaseOrderSerializer(data=data, context={'request': self.request, **(context or {})})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        with self.captureOnCommitCallbacks(execute=True):
            return serializer.save()

    def update_order(self, order, data):
        serializer = PurchaseOrderSerializer(order, data=data, partial=True, context={'request': self.request})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        with self.captureOnCommitCallbacks(execute=True):
            return serializer.save()

    def pr_notice(self, recipient):
        return Notification.objects.get(
            recipient=recipient, metadata__pr_id=str(self.pr.pk),
            metadata__event_type='approval_assignment',
        )

    def test_creation_keeps_pr_notifications_actionable_until_genuine_final_approval(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.pr = RequisitionWorkflowService.submit(self.pr.pk, self.first)
        first_notice = self.pr_notice(self.first)
        original = deepcopy(self.pr.approval_workflow_config)
        order = self.create_order()
        original_po = deepcopy(order.approval_log)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'submitted')
        self.assertEqual(self.pr.current_approval_step, 0)
        self.assertEqual(self.pr.approval_workflow_config, original)
        self.assertEqual(self.pr.po_number_reference, order.po_number)
        self.assertNotIn(PREVIOUS_STATUS, self.pr.price_remarks_data)
        self.assertEqual(delivery_issue(first_notice, 'teams'), '')
        self.assertTrue(RequisitionWorkflowService.can_approve(self.pr, self.first))

        with self.captureOnCommitCallbacks(execute=True):
            self.pr = RequisitionWorkflowService.approve(self.pr.pk, self.first, require_signature=True)
        self.assertEqual(self.pr.status, 'in_review')
        next_notice = self.pr_notice(self.second)
        self.assertEqual(delivery_issue(next_notice, 'teams'), '')
        self.assertTrue(any(call.args[0] == next_notice.pk for call in self.teams_task.call_args_list))

        with self.captureOnCommitCallbacks(execute=True):
            self.pr = RequisitionWorkflowService.approve(self.pr.pk, self.second, require_signature=True)
        self.assertEqual(self.pr.status, 'converted')
        self.assertEqual(self.pr.current_approval_step, 2)
        self.assertEqual(self.pr.approved_by_id, self.second.pk)
        self.assertEqual(self.pr.price_remarks_data[PREVIOUS_STATUS], 'approved')
        for index, user in enumerate((self.first, self.second)):
            stage = self.pr.approval_workflow_config[index]
            self.assertEqual(stage['status'], 'approved')
            self.assertEqual(stage['approved_by_id'], str(user.pk))
            self.assertEqual(stage['signature_user_email'], user.email)
            self.assertEqual(stage['assignment_id'], original[index]['assignment_id'])
            self.assertTrue(stage['approved_at'])
            self.assertTrue(stage['signature'])
        order.refresh_from_db()
        self.assertEqual(order.approval_log, original_po)
        self.assertIsNone(order.approved_at)

    def test_native_creation_preserves_every_unapproved_lifecycle_and_evidence(self):
        for index, status in enumerate(('draft', 'submitted', 'in_review', 'rejected', 'cancelled'), 101):
            with self.subTest(status=status):
                pr = self.make_pr(index, status)
                pr.current_approval_step = 1
                pr.rejection_reason = 'Preserved historical decision context'
                pr.save(update_fields=['current_approval_step', 'rejection_reason'])
                original = deepcopy(pr.approval_workflow_config)
                metadata = deepcopy(pr.price_remarks_data)
                order = self.create_order(pr)
                pr.refresh_from_db()
                self.assertEqual(pr.status, status)
                self.assertEqual(pr.current_approval_step, 1)
                self.assertEqual(pr.approval_workflow_config, original)
                self.assertEqual(pr.price_remarks_data, metadata)
                self.assertEqual(pr.rejection_reason, 'Preserved historical decision context')
                self.assertEqual(pr.po_number_reference, order.po_number)

    def test_approved_pr_still_converts_without_changing_its_completed_evidence(self):
        self.pr.status = 'approved'
        for stage in self.pr.approval_workflow_config:
            stage.update(status='approved', approved_at='2026-09-01T09:00:00Z', signature='retained-signature')
        self.pr.save(update_fields=['status', 'approval_workflow_config'])
        original = deepcopy(self.pr.approval_workflow_config)
        order = self.create_order()
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'converted')
        self.assertEqual(self.pr.approval_workflow_config, original)
        self.assertEqual(self.pr.po_number_reference, order.po_number)
        self.assertEqual(self.pr.price_remarks_data[PREVIOUS_STATUS], 'approved')

    def test_reassignment_and_renumbering_preserve_both_unfinished_prs(self):
        self.pr.status = 'submitted'
        self.pr.save(update_fields=['status'])
        original = deepcopy(self.pr.approval_workflow_config)
        order = self.create_order()
        order = self.update_order(order, {'po_number': 'RAD-PRJ-PUR-0110_2026'})
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'submitted')
        self.assertEqual(self.pr.po_number_reference, order.po_number)
        self.assertEqual(self.pr.approval_workflow_config, original)
        other = self.make_pr(111, 'in_review')
        other_original = deepcopy(other.approval_workflow_config)
        order = self.update_order(order, {'pr_reference': str(other.pk)})
        self.pr.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(self.pr.status, 'submitted')
        self.assertEqual(self.pr.po_number_reference, '')
        self.assertEqual(self.pr.approval_workflow_config, original)
        self.assertEqual(other.status, 'in_review')
        self.assertEqual(other.po_number_reference, order.po_number)
        self.assertEqual(other.approval_workflow_config, other_original)

    def test_deleting_one_or_all_orders_preserves_active_review_and_notifications(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.pr = RequisitionWorkflowService.submit(self.pr.pk, self.first)
        notice = self.pr_notice(self.first)
        original = deepcopy(self.pr.approval_workflow_config)
        first_order = self.create_order()
        second_order = self.create_order(sequence=112)
        with self.captureOnCommitCallbacks(execute=True):
            delete_order(first_order.pk)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'submitted')
        self.assertEqual(self.pr.po_number_reference, second_order.po_number)
        self.assertEqual(self.pr.approval_workflow_config, original)
        self.assertEqual(delivery_issue(notice, 'teams'), '')
        with self.captureOnCommitCallbacks(execute=True):
            delete_order(second_order.pk)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'submitted')
        self.assertEqual(self.pr.po_number_reference, '')
        self.assertEqual(self.pr.approval_workflow_config, original)
        self.assertEqual(delivery_issue(notice, 'teams'), '')

    def test_final_approval_does_not_treat_an_unlinked_reference_string_as_a_po(self):
        self.pr.po_number_reference = 'RAD-PRJ-PUR-0999_2026'
        self.pr.save(update_fields=['po_number_reference'])
        with self.captureOnCommitCallbacks(execute=True):
            RequisitionWorkflowService.submit(self.pr.pk, self.first)
            RequisitionWorkflowService.approve(self.pr.pk, self.first, require_signature=True)
            result = RequisitionWorkflowService.approve(self.pr.pk, self.second, require_signature=True)
        self.assertEqual(result.status, 'approved')
        self.assertFalse(PurchaseOrder.objects.filter(pr_reference=self.pr).exists())

    def test_historical_import_context_keeps_explicit_conversion_without_faking_decisions(self):
        self.pr.status = 'submitted'
        self.pr.save(update_fields=['status'])
        original = deepcopy(self.pr.approval_workflow_config)
        self.create_order(context={'source_document_import': True, 'historical_requisition_conversion': True}, approval_log=[])
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'converted')
        self.assertEqual(self.pr.approval_workflow_config, original)
        self.assertEqual(self.pr.price_remarks_data[PREVIOUS_STATUS], 'submitted')

    def test_originating_source_import_still_defers_conversion(self):
        self.pr.status = 'submitted'
        self.pr.save(update_fields=['status'])
        original = deepcopy(self.pr.approval_workflow_config)
        self.create_order(context={
            'source_document_import': True, 'defer_requisition_conversion': True,
            'historical_requisition_conversion': False,
        }, approval_log=[])
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'submitted')
        self.assertEqual(self.pr.approval_workflow_config, original)
        self.assertEqual(self.pr.po_number_reference, '')
