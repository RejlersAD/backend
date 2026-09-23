"""Native creation and PR conversion open a separate, eligible PO request."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.exceptions import ValidationError

from apps.notifications.delivery import delivery_issue
from apps.notifications.models import Notification
from apps.notifications.teams import send_teams_approval_assignment
from apps.procurement.models import PurchaseOrder, PurchaseRequisition, Vendor
from apps.procurement.serializers import PurchaseOrderSerializer
from apps.procurement.services.requisition_conversion import RequisitionConversionService
from apps.procurement.services.purchase_order_approvals import record_decision
from apps.rbac.models import UserRole

from .approval_fixtures import grant_approval, set_position


@override_settings(TEAMS_APPROVAL_WEBHOOK_URL='https://teams.example.test/test-only', WEB_PUSH_VAPID_PRIVATE_KEY='')
class PurchaseOrderCreationNotificationRoutingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.sender = get_user_model().objects.create_user('po-route-issuer', email='issuer@po-route.example.test')
        self.ceo = get_user_model().objects.create_user(
            'po-route-ceo', email='ceo@po-route.example.test', first_name='Jarmo', last_name='Suominen',
        )
        grant_approval(self.ceo)
        set_position(self.ceo, 'CEO')
        self.vendor = Vendor.objects.create(vendor_code='PO-ROUTE', name='Approval routing supplier', status='active')
        self.history = [{
            'stage': 'VP Delivery', 'level': 4, 'user_id': 'source-vp',
            'user_name': 'PR Approver', 'status': 'approved',
            'approved_at': '2026-09-01T10:00:00+04:00',
        }]
        self.pr = PurchaseRequisition.objects.create(
            pr_number='RAD-PRJ-PR-0650_2026', issued_by=self.sender, vendor=self.vendor,
            title='Engineering software', product_service='Engineering software', total_price='100.00',
            status='approved', po_applicable=True, approval_workflow_config=deepcopy(self.history),
        )
        self.stage = {'stage': 'Final Management Sign-off', 'level': 0, 'user_id': str(self.ceo.pk)}
        for name in ('send_notification_email', 'send_web_push_notification'):
            task = patch(f'apps.notifications.services.{name}.delay')
            task.start()
            self.addCleanup(task.stop)
        task = patch('apps.notifications.teams.send_teams_approval_assignment.delay')
        self.teams = task.start()
        self.addCleanup(task.stop)

    def serializer(self, **overrides):
        payload = {
            'pr_reference': str(self.pr.pk), 'vendor': str(self.vendor.pk), 'title': self.pr.title,
            'total_amount': '100.00', 'category': 'other', 'status': 'draft', 'approval_log': [self.stage],
        }
        payload.update(overrides)
        return PurchaseOrderSerializer(data=payload, context={'request': SimpleNamespace(user=self.sender)})

    def assert_po_request(self, order):
        notice = Notification.objects.get(metadata__po_id=str(order.pk), metadata__event_type='approval_assignment')
        self.assertEqual(notice.recipient_id, self.ceo.pk)
        self.assertTrue(notice.send_in_app)
        self.assertEqual(notice.metadata['entity_type'], 'purchase_order')
        self.assertEqual(delivery_issue(notice, 'teams'), '')
        self.assertEqual(self.teams.call_count, 1)
        context = self.teams.call_args.args[1]
        self.assertEqual(self.teams.call_args.args[0], notice.pk)
        self.assertEqual(context['request_number'], order.po_number)
        with patch('apps.notifications.teams.requests.post') as post:
            self.assertEqual(send_teams_approval_assignment.run(notice.pk, context), {'status': 'sent'})
            self.assertEqual(post.call_args.kwargs['json']['recipient_email'], self.ceo.email)
            self.assertIn(f'/procurement/orders/{order.pk}', post.call_args.kwargs['json']['action_url'])
        self.assertFalse(Notification.objects.filter(metadata__pr_id=str(self.pr.pk)).exists())
        return notice

    def test_native_linked_draft_keeps_selected_management_stage_and_dispatches_both_channels(self):
        serializer = self.serializer()
        self.assertTrue(serializer.is_valid(), serializer.errors)
        with self.captureOnCommitCallbacks(execute=True):
            order = serializer.save()
        self.assertEqual(order.approval_log[0]['stage'], self.stage['stage'])
        self.assertEqual(order.approval_log[0]['status'], 'Pending')
        self.assertIsNone(order.approved_at)
        self.assert_po_request(order)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.approval_workflow_config, self.history)

    def test_new_native_order_rejects_empty_or_omitted_assignments_without_creating_a_silent_draft(self):
        for omitted in (False, True):
            with self.subTest(omitted=omitted):
                serializer = self.serializer(approval_log=[])
                if omitted:
                    serializer.initial_data.pop('approval_log')
                self.assertFalse(serializer.is_valid())
                self.assertIn('approval_log', serializer.errors)
        self.assertFalse(PurchaseOrder.objects.exists())
        self.teams.assert_not_called()

    def test_explicit_assignment_cannot_use_operations_title_as_ceo(self):
        set_position(self.ceo, 'Chief Operating Officer & VP, Head of Operations & Project Delivery')
        serializer = self.serializer()
        self.assertFalse(serializer.is_valid())
        self.assertIn('configured business position', str(serializer.errors))

    def test_old_unassigned_draft_can_be_configured_and_notified_once_without_fabricating_approval(self):
        order = PurchaseOrder.objects.create(
            po_number='RAD-PRJ-PUR-0650_2026', pr_reference=self.pr, vendor=self.vendor,
            title=self.pr.title, total_amount='100.00', created_by=self.sender, approval_log=[],
        )
        for _ in range(2):
            serializer = PurchaseOrderSerializer(order, data={'approval_log': [self.stage]}, partial=True,
                                                context={'request': SimpleNamespace(user=self.sender)})
            self.assertTrue(serializer.is_valid(), serializer.errors)
            with self.captureOnCommitCallbacks(execute=True):
                order = serializer.save()
        self.assertIsNone(order.approved_at)
        self.assert_po_request(order)

    def test_conversion_retains_pr_history_and_starts_its_own_pending_po_request(self):
        with self.captureOnCommitCallbacks(execute=True):
            requisition, order = RequisitionConversionService.convert(self.pr.pk, self.sender)
        self.assertEqual(requisition.status, 'converted')
        self.assertEqual(requisition.approval_workflow_config, self.history)
        self.assertEqual(order.approval_log[0]['source'], 'purchase_requisition')
        self.assertTrue(order.approval_log[0]['external'])
        self.assertEqual(order.approval_log[-1]['user_id'], str(self.ceo.pk))
        self.assertEqual(order.approval_log[-1]['status'], 'Pending')
        self.assertIsNone(order.approved_at)
        self.assert_po_request(order)

    def test_conversion_cannot_silently_create_an_order_without_an_eligible_final_signatory(self):
        UserRole.objects.filter(user_profile=self.ceo.rbac_profile).delete()
        with self.assertRaisesMessage(ValidationError, 'Purchase Order approval permission'):
            RequisitionConversionService.convert(self.pr.pk, self.sender)
        self.assertFalse(PurchaseOrder.objects.exists())
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'approved')
        self.teams.assert_not_called()

    def test_completed_source_statuses_do_not_block_the_independent_po_decision(self):
        self.pr.approval_workflow_config = [
            {**self.history[0], 'status': value} for value in ('complete', 'completed')
        ]
        self.pr.save(update_fields=['approval_workflow_config'])
        original = deepcopy(self.pr.approval_workflow_config)
        with self.captureOnCommitCallbacks(execute=True):
            _, order = RequisitionConversionService.convert(self.pr.pk, self.sender)
        self.assertEqual([row['status'] for row in order.approval_log[:-1]], ['Approved', 'Approved'])
        self.assertEqual([row['source_status'] for row in order.approval_log[:-1]], ['complete', 'completed'])
        self.assert_po_request(order)
        decided, _ = record_decision(order, self.ceo, 'approve')
        self.assertEqual(decided.approved_by_id, self.ceo.pk)
        self.assertIsNotNone(decided.approved_at)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.approval_workflow_config, original)

    def test_conversion_rejects_ambiguous_default_identity(self):
        other = get_user_model().objects.create_user(
            'po-route-duplicate', email='duplicate@po-route.example.test', first_name='Jarmo', last_name='Suominen',
        )
        grant_approval(other)
        set_position(other, 'CEO')
        with self.assertRaisesMessage(ValidationError, 'one active employee'):
            RequisitionConversionService.convert(self.pr.pk, self.sender)
        self.assertFalse(PurchaseOrder.objects.exists())

    def test_assigned_route_cannot_be_cleared_by_an_ordinary_draft_edit(self):
        serializer = self.serializer()
        self.assertTrue(serializer.is_valid(), serializer.errors)
        order = serializer.save()
        edit = PurchaseOrderSerializer(order, data={'approval_log': []}, partial=True)
        self.assertFalse(edit.is_valid())
        self.assertIn('approval_log', edit.errors)

    def test_stale_empty_draft_save_cannot_erase_a_concurrently_assigned_approver(self):
        order = PurchaseOrder.objects.create(
            po_number='RAD-PRJ-PUR-0650_2026', pr_reference=self.pr, vendor=self.vendor,
            title=self.pr.title, total_amount='100.00', created_by=self.sender, approval_log=[],
        )
        stale = PurchaseOrderSerializer(order, data={'approval_log': []}, partial=True)
        self.assertTrue(stale.is_valid(), stale.errors)
        current = PurchaseOrderSerializer(order, data={'approval_log': [self.stage]}, partial=True)
        self.assertTrue(current.is_valid(), current.errors)
        with self.captureOnCommitCallbacks(execute=True):
            saved = current.save()
        assigned = deepcopy(saved.approval_log)
        with self.assertRaisesMessage(ValidationError, 'assigned while this form was open'):
            stale.save()
        order.refresh_from_db()
        self.assertEqual(order.approval_log, assigned)
        self.assert_po_request(order)
