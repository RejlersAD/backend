"""Linked PR and PO workflows retain independent in-app and Teams notices.

Only the external delivery tasks are mocked. Notification persistence,
eligibility, workflow transitions, serialization and delivery guards are real.
"""

from copy import deepcopy
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.notifications.delivery import approval_assignment_issue, delivery_issue
from apps.notifications.models import Notification
from apps.notifications.serializers import NotificationListSerializer
from apps.notifications.teams import build_approval_assignment_payload, send_teams_approval_assignment
from apps.notifications.views import NotificationViewSet
from apps.procurement.models import PurchaseOrder, PurchaseRequisition, Vendor
from apps.procurement.services.purchase_order_approvals import notify_assigned_approvers, record_decision
from apps.procurement.services.requisition_workflow import RequisitionWorkflowService

from .approval_fixtures import grant_approval, set_position


@override_settings(
    FRONTEND_URL='https://www.radai.ae',
    TEAMS_APPROVAL_WEBHOOK_URL='https://teams-delivery.example.test/test-only',
    WEB_PUSH_VAPID_PRIVATE_KEY='',
)
class PurchaseRequisitionOrderNotificationIsolationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        patcher = patch('apps.notifications.teams.send_teams_approval_assignment.delay')
        self.teams_task = patcher.start()
        self.addCleanup(patcher.stop)
        for task in ('send_notification_email', 'send_web_push_notification'):
            patcher = patch(f'apps.notifications.services.{task}.delay')
            patcher.start()
            self.addCleanup(patcher.stop)

        users = get_user_model()
        self.issuer = users.objects.create_user('linked-issuer', email='issuer@linked.example.test')
        self.approver = users.objects.create_user('linked-approver', email='approver@linked.example.test')
        self.next_approver = users.objects.create_user('linked-next', email='next@linked.example.test')
        for user in (self.issuer, self.approver, self.next_approver):
            grant_approval(user)
            set_position(user, 'Procurement Manager')
            profile = user.rbac_profile
            profile.signature_image = f'test-signature-{user.username}'
            profile.save(update_fields=['signature_image'])
        self.workflow = [self.stage(self.approver, 0), self.stage(self.next_approver, 1)]
        self.requisition = PurchaseRequisition.objects.create(
            pr_number='RAD-PRJ-PR-0426_2026', title='Licence recommendation',
            product_service='Annual engineering software licence',
            description_reason='<p>Review the procurement recommendation.</p>',
            issued_by=self.issuer, status='draft', po_applicable=True,
            total_price='89512.50', currency='AED',
            approval_workflow_config=deepcopy(self.workflow),
        )
        vendor = Vendor.objects.create(vendor_code='LINKED-NOTICES', name='Engineering Software Supplier')
        self.order = PurchaseOrder.objects.create(
            po_number='RAD-PRJ-PUR-0480_2026', title='Annual engineering software order',
            description='<p>Order one annual engineering software licence.</p>',
            vendor=vendor, pr_reference=self.requisition, created_by=self.issuer,
            total_amount='89512.50', currency='AED',
            approval_log=deepcopy(self.workflow),
        )
        self.factory = APIRequestFactory()

    @staticmethod
    def stage(user, level):
        return {
            'level': level, 'role': 'Procurement Manager', 'stage': f'Level {level} Procurement Review',
            'user_id': str(user.pk), 'user_email': user.email, 'approver_email': user.email,
            'status': 'pending', 'assignment_id': f'same-token-across-documents-{level}',
        }

    def notices(self, kind):
        identifier = self.requisition.pk if kind == 'pr' else self.order.pk
        return Notification.objects.filter(
            **{f'metadata__{kind}_id': str(identifier)},
            metadata__event_type='approval_assignment',
        )

    def notify_both(self, *, po_first=False):
        with self.captureOnCommitCallbacks(execute=True):
            if po_first:
                notify_assigned_approvers(self.order)
            self.requisition = RequisitionWorkflowService.submit(self.requisition.pk, self.issuer)
            if not po_first:
                notify_assigned_approvers(self.order)
        return self.notices('pr').get(), self.notices('po').get()

    def queued_context(self, notification):
        return next(
            call.args[1] for call in self.teams_task.call_args_list
            if call.args[0] == notification.pk
        )

    def test_linked_documents_notify_same_approver_independently_in_app_and_teams(self):
        pr_notice, po_notice = self.notify_both()
        self.assertNotEqual(pr_notice.pk, po_notice.pk)
        self.assertEqual(self.teams_task.call_count, 2)
        for notice, kind, entity_type, record in (
            (pr_notice, 'pr', 'purchase_recommendation', self.requisition),
            (po_notice, 'po', 'purchase_order', self.order),
        ):
            with self.subTest(kind=kind):
                self.assertEqual(notice.recipient_id, self.approver.pk)
                self.assertTrue(notice.send_in_app)
                self.assertFalse(notice.is_read)
                self.assertEqual(notice.status, 'SENT')
                self.assertEqual(notice.category.name, 'APPROVAL')
                self.assertEqual(notice.metadata[f'{kind}_id'], str(record.pk))
                self.assertEqual(notice.metadata['entity_type'], entity_type)
                self.assertEqual(notice.metadata['entity_id'], str(record.pk))
                self.assertEqual(notice.metadata['approval_level'], 0)
                self.assertEqual(notice.metadata['assignment_id'], 'same-token-across-documents-0')
                self.assertEqual(approval_assignment_issue(notice), '')
                self.assertEqual(delivery_issue(notice, 'teams'), '')

        pr_context, po_context = self.queued_context(pr_notice), self.queued_context(po_notice)
        self.assertEqual(pr_context['request_name'], f'Purchase Recommendation {self.requisition.pr_number}')
        self.assertIn(self.order.po_number, po_context['request_name'])
        self.assertEqual(pr_context['po_number'], self.order.po_number)
        self.assertEqual(pr_context['description'], 'Review the procurement recommendation.')
        self.assertEqual(po_context['description'], 'Order one annual engineering software licence.')
        self.assertNotIn('po_id', pr_notice.metadata)
        self.assertNotIn('pr_id', po_notice.metadata)
        for notice, context, segment, identifier, entity_type, number, title in (
            (pr_notice, pr_context, 'requisitions', self.requisition.pk, 'purchase_recommendation',
             self.requisition.pr_number, 'Purchase Recommendation approval required'),
            (po_notice, po_context, 'orders', self.order.pk, 'purchase_order',
             self.order.po_number, 'Purchase Order approval required'),
        ):
            expected_url = f'/procurement/{segment}/{identifier}'
            self.assertEqual(NotificationListSerializer(notice).data['action_url'], expected_url)
            payload = build_approval_assignment_payload(notice, context)
            self.assertEqual(context['entity_type'], entity_type)
            self.assertEqual(context['entity_id'], str(identifier))
            self.assertEqual(context['request_number'], number)
            self.assertEqual(payload['entity_type'], entity_type)
            self.assertEqual(payload['entity_id'], str(identifier))
            self.assertEqual(payload['request_number'], number)
            self.assertEqual(payload['title'], title)
            self.assertEqual(payload['action_url'], f'https://www.radai.ae{expected_url}')
            self.assertEqual(payload['recipient_email'], self.approver.email)
            self.assertEqual(payload['attachments'][0]['content']['actions'][0]['url'], payload['action_url'])

    def test_po_notification_does_not_suppress_linked_pr_and_each_workflow_deduplicates_its_own_retries(self):
        pr_notice, po_notice = self.notify_both(po_first=True)
        self.teams_task.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            RequisitionWorkflowService._notify_level(
                self.requisition, self.requisition.approval_workflow_config, 0,
            )
            notify_assigned_approvers(self.order)
            RequisitionWorkflowService.submit(self.requisition.pk, self.issuer)
        self.assertEqual(self.notices('pr').get().pk, pr_notice.pk)
        self.assertEqual(self.notices('po').get().pk, po_notice.pk)
        self.teams_task.assert_not_called()

    def test_persisted_recommendation_notice_with_related_order_id_does_not_suppress_order_assignment(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.requisition = RequisitionWorkflowService.submit(self.requisition.pk, self.issuer)
        pr_notice = self.notices('pr').get()
        # A linked-record reference can be retained by imported or enriched
        # notices; it must not change this notification's explicit identity.
        pr_notice.metadata = {
            **pr_notice.metadata,
            'po_id': str(self.order.pk),
            'approval_stage': self.workflow[0]['stage'],
        }
        pr_notice.save(update_fields=['metadata'])
        self.teams_task.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            notify_assigned_approvers(self.order)

        orders = self.notices('po').filter(metadata__entity_type='purchase_order')
        self.assertEqual(orders.count(), 1)
        po_notice = orders.get()
        self.assertNotEqual(po_notice.pk, pr_notice.pk)
        self.assertEqual(po_notice.metadata['assignment_id'], pr_notice.metadata['assignment_id'])
        self.assertEqual(po_notice.recipient_id, pr_notice.recipient_id)
        self.teams_task.assert_called_once()
        self.assertEqual(self.teams_task.call_args.args[0], po_notice.pk)
        self.assertEqual(self.teams_task.call_args.args[1]['entity_type'], 'purchase_order')

    def test_persisted_order_notice_with_related_recommendation_id_does_not_suppress_recommendation_assignment(self):
        with self.captureOnCommitCallbacks(execute=True):
            notify_assigned_approvers(self.order)
        po_notice = self.notices('po').get()
        po_notice.metadata = {**po_notice.metadata, 'pr_id': str(self.requisition.pk)}
        po_notice.save(update_fields=['metadata'])
        self.teams_task.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            self.requisition = RequisitionWorkflowService.submit(self.requisition.pk, self.issuer)

        recommendations = self.notices('pr').filter(metadata__entity_type='purchase_recommendation')
        self.assertEqual(recommendations.count(), 1)
        pr_notice = recommendations.get()
        self.assertNotEqual(pr_notice.pk, po_notice.pk)
        self.assertEqual(pr_notice.metadata['assignment_id'], po_notice.metadata['assignment_id'])
        self.assertEqual(pr_notice.recipient_id, po_notice.recipient_id)
        self.teams_task.assert_called_once()
        self.assertEqual(self.teams_task.call_args.args[0], pr_notice.pk)
        self.assertEqual(self.teams_task.call_args.args[1]['entity_type'], 'purchase_recommendation')

    def test_existing_untyped_legacy_notices_still_prevent_own_workflow_resends(self):
        pr_notice, po_notice = self.notify_both()
        for notice in (pr_notice, po_notice):
            notice.metadata.pop('entity_type')
            notice.metadata.pop('entity_id')
            notice.save(update_fields=['metadata'])
        self.teams_task.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            RequisitionWorkflowService._notify_level(
                self.requisition, self.requisition.approval_workflow_config, 0,
            )
            notify_assigned_approvers(self.order)

        self.assertEqual(self.notices('pr').get().pk, pr_notice.pk)
        self.assertEqual(self.notices('po').get().pk, po_notice.pk)
        self.teams_task.assert_not_called()

    def test_explicit_document_identity_controls_routing_and_revalidation_with_mixed_references(self):
        pr_notice, po_notice = self.notify_both()
        pr_notice.metadata = {**pr_notice.metadata, 'po_id': str(self.order.pk)}
        po_notice.metadata = {**po_notice.metadata, 'pr_id': str(self.requisition.pk)}
        for notice, segment, identifier in (
            (pr_notice, 'requisitions', self.requisition.pk),
            (po_notice, 'orders', self.order.pk),
        ):
            expected_url = f'/procurement/{segment}/{identifier}'
            self.assertEqual(NotificationListSerializer(notice).data['action_url'], expected_url)
            payload = build_approval_assignment_payload(notice, self.queued_context(notice))
            self.assertEqual(payload['action_url'], f'https://www.radai.ae{expected_url}')

        PurchaseOrder.objects.filter(pk=self.order.pk).update(status='cancelled')
        self.assertEqual(delivery_issue(pr_notice, 'teams'), '')
        self.assertNotEqual(delivery_issue(po_notice, 'teams'), '')
        PurchaseOrder.objects.filter(pk=self.order.pk).update(status='draft')
        PurchaseRequisition.objects.filter(pk=self.requisition.pk).update(status='rejected')
        self.assertNotEqual(delivery_issue(pr_notice, 'teams'), '')
        self.assertEqual(delivery_issue(po_notice, 'teams'), '')

    @patch('apps.notifications.teams.requests.post')
    def test_teams_workers_post_independent_document_messages_after_revalidating_each_assignment(self, post):
        pr_notice, po_notice = self.notify_both()
        for notice in (pr_notice, po_notice):
            result = send_teams_approval_assignment.run(notice.pk, self.queued_context(notice))
            self.assertEqual(result, {'status': 'sent'})
        self.assertEqual(post.call_count, 2)
        messages = {call.kwargs['json']['notification_id']: call.kwargs['json'] for call in post.call_args_list}
        self.assertEqual(
            messages[str(pr_notice.pk)]['action_url'],
            f'https://www.radai.ae/procurement/requisitions/{self.requisition.pk}',
        )
        self.assertEqual(
            messages[str(po_notice.pk)]['action_url'],
            f'https://www.radai.ae/procurement/orders/{self.order.pk}',
        )
        self.assertEqual(messages[str(pr_notice.pk)]['entity_type'], 'purchase_recommendation')
        self.assertEqual(messages[str(po_notice.pk)]['entity_type'], 'purchase_order')

    def test_pr_progression_notifies_only_its_next_level_and_does_not_invalidate_po(self):
        pr_notice, po_notice = self.notify_both()
        self.assertFalse(self.notices('pr').filter(recipient=self.next_approver).exists())
        self.assertFalse(self.notices('po').filter(recipient=self.next_approver).exists())
        self.teams_task.reset_mock()
        actor = get_user_model().objects.get(pk=self.approver.pk)
        with self.captureOnCommitCallbacks(execute=True):
            RequisitionWorkflowService.approve(self.requisition.pk, actor, require_signature=True)
        next_notice = self.notices('pr').get(recipient=self.next_approver)
        self.assertEqual(next_notice.metadata['approval_level'], 1)
        self.assertEqual(self.teams_task.call_count, 1)
        self.assertEqual(self.teams_task.call_args.args[0], next_notice.pk)
        self.assertFalse(self.notices('po').filter(recipient=self.next_approver).exists())
        self.assertNotEqual(delivery_issue(pr_notice, 'teams'), '')
        self.assertEqual(delivery_issue(po_notice, 'teams'), '')
        self.assertTrue(NotificationListSerializer(pr_notice).data['metadata']['approval_obsolete'])
        self.assertTrue(NotificationListSerializer(po_notice).data['metadata']['requires_action'])

    def test_po_progression_notifies_only_its_next_level_and_does_not_invalidate_pr(self):
        pr_notice, po_notice = self.notify_both()
        self.teams_task.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            record_decision(self.order, self.approver, 'approve', require_signature=True)
        next_notice = self.notices('po').get(recipient=self.next_approver)
        self.assertEqual(next_notice.metadata['approval_level'], 1)
        self.assertEqual(self.teams_task.call_count, 1)
        self.assertEqual(self.teams_task.call_args.args[0], next_notice.pk)
        self.assertFalse(self.notices('pr').filter(recipient=self.next_approver).exists())
        self.assertNotEqual(delivery_issue(po_notice, 'teams'), '')
        self.assertEqual(delivery_issue(pr_notice, 'teams'), '')
        self.assertTrue(NotificationListSerializer(po_notice).data['metadata']['approval_obsolete'])
        self.assertTrue(NotificationListSerializer(pr_notice).data['metadata']['requires_action'])

    def test_read_and_delete_requisition_notice_leave_order_notice_unread_and_actionable(self):
        pr_notice, po_notice = self.notify_both()
        request = self.factory.post('/notifications/mark_as_read/', {
            'notification_ids': [pr_notice.pk],
        }, format='json')
        force_authenticate(request, user=self.approver)
        response = NotificationViewSet.as_view({'post': 'mark_as_read'})(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['marked_read'], 1)
        pr_notice.refresh_from_db()
        po_notice.refresh_from_db()
        self.assertTrue(pr_notice.is_read)
        self.assertFalse(po_notice.is_read)
        self.assertEqual(po_notice.status, 'SENT')

        request = self.factory.delete(f'/notifications/{pr_notice.pk}/')
        force_authenticate(request, user=self.approver)
        response = NotificationViewSet.as_view({'delete': 'destroy'})(request, pk=pr_notice.pk)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Notification.objects.filter(pk=pr_notice.pk).exists())
        po_notice.refresh_from_db()
        self.assertFalse(po_notice.is_read)
        self.assertEqual(delivery_issue(po_notice, 'teams'), '')
        self.assertEqual(self.notices('po').count(), 1)
