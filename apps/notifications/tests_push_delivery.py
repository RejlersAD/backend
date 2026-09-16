"""Browser deliveries and notification actions stay bound to the current assignee."""

import json
from datetime import timedelta
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import include, path
from django.utils import timezone
from rest_framework.test import APIClient

from apps.notifications.delivery import approval_assignment_issue, safe_action_url
from apps.notifications.models import Notification, NotificationCategory, NotificationLog, NotificationPreference, WebPushSubscription
from apps.notifications.services import NotificationService, send_notification_email, send_web_push_notification
from apps.notifications.teams import send_teams_approval_assignment
from apps.procurement.models import PurchaseOrder, PurchaseRequisition, Vendor
from apps.rbac.models import Organization, UserProfile


urlpatterns = [path('notifications/', include('apps.notifications.urls'))]


class FakeWebPushException(Exception):
    def __init__(self, status):
        super().__init__('Simulated delivery failure')
        self.response = SimpleNamespace(status_code=status)


@override_settings(
    ROOT_URLCONF=__name__, TEAMS_APPROVAL_WEBHOOK_URL='', WEB_PUSH_VAPID_PRIVATE_KEY='',
    WEB_PUSH_VAPID_PUBLIC_KEY='', FRONTEND_URL='https://radai.ae',
)
class NotificationPushDeliveryTests(TestCase):
    def setUp(self):
        org = Organization.objects.create(name='Push audit', code='PUSH-AUDIT')
        self.users = []
        for index in range(3):
            user = get_user_model().objects.create_user(
                username=f'push-audit-{index}', email=f'push-audit-{index}@example.test',
            )
            UserProfile.objects.get_or_create(user=user, defaults={'organization': org})
            self.users.append(user)
        self.recipient, self.other, self.ceo = self.users
        self.ceo.is_superuser = True
        self.ceo.save(update_fields=['is_superuser'])
        self.category = NotificationCategory.objects.get_or_create(name='APPROVAL')[0]
        self.vendor = Vendor.objects.create(vendor_code='PUSH-AUDIT', name='Push audit vendor')
        self.order = PurchaseOrder.objects.create(
            po_number='PO-PUSH-AUDIT', vendor=self.vendor, title='Push approval',
            created_by=self.other, total_amount='100.00',
            approval_log=[self.po_stage(0, self.recipient), self.po_stage(5, self.ceo)],
        )
        self.client = APIClient()
        self.client.force_authenticate(self.recipient)
        module = ModuleType('pywebpush')
        module.WebPushException = FakeWebPushException
        module.webpush = Mock()
        self.webpush = module.webpush
        module_patch = patch.dict('sys.modules', {'pywebpush': module})
        module_patch.start()
        self.addCleanup(module_patch.stop)

    def po_stage(self, level, user, **kwargs):
        return {
            'stage': f'Level {level}', 'level': level, 'user_id': str(user.pk),
            'approver_email': user.email, 'status': 'Pending', 'assignment_id': f'po-{level}-initial',
            **kwargs,
        }

    def subscription(self, user, suffix='a', **kwargs):
        return WebPushSubscription.objects.create(
            user=user, endpoint=f'https://push.example.test/{suffix}', p256dh='browser-key', auth='browser-auth', **kwargs,
        )

    def notification(self, recipient=None, metadata=None, **kwargs):
        return Notification.objects.create(
            recipient=recipient or self.recipient, category=self.category, title='Approval required',
            message='Please review this request.', status=kwargs.pop('status', 'SENT'),
            action_url=kwargs.pop('action_url', f'/procurement/orders/{self.order.pk}'),
            action_label='Review and approve', metadata=metadata or {}, **kwargs,
        )

    def po_notification(self, recipient=None, level=0, **kwargs):
        return self.notification(recipient, {
            'event_type': 'approval_assignment', 'requires_action': True,
            'po_id': str(self.order.pk), 'approval_level': level, 'approval_stage': f'Level {level}',
            'assignment_id': f'po-{level}-initial',
        }, **kwargs)

    def requisition(self, **kwargs):
        return PurchaseRequisition.objects.create(
            pr_number='PR-PUSH-AUDIT', issued_by=self.other, status='submitted', po_applicable=False,
            approval_workflow_config=[
                {'stage': 'Level 0', 'level': 0, 'user_id': str(self.recipient.pk),
                 'user_email': self.recipient.email, 'status': 'pending', 'assignment_id': 'pr-0-initial'},
                {'stage': 'Level 1', 'level': 1, 'user_id': str(self.other.pk),
                 'user_email': self.other.email, 'status': 'pending', 'assignment_id': 'pr-1-initial'},
            ], **kwargs,
        )

    def pr_notification(self, requisition, recipient=None, level=0):
        return self.notification(recipient, {
            'pr_id': str(requisition.pk), 'requires_action': True, 'event_type': 'approval_assignment',
            'approval_level': level, 'assignment_id': f'pr-{level}-initial',
        }, action_url='/wrong-request')

    def test_push_goes_only_to_active_recipient_subscriptions_and_carries_owner_and_request(self):
        mine = self.subscription(self.recipient)
        self.subscription(self.other, 'other')
        self.subscription(self.recipient, 'inactive', is_active=False)
        notification = self.po_notification(action_url='https://untrusted.example.test/approve')

        self.assertEqual(send_web_push_notification.run(notification.pk), {'sent': 1, 'disabled': 0})

        call = self.webpush.call_args.kwargs
        self.assertEqual(call['subscription_info']['endpoint'], mine.endpoint)
        payload = json.loads(call['data'])
        self.assertEqual(payload['recipient_user_id'], str(self.recipient.pk))
        self.assertEqual(payload['url'], f'/procurement/orders/{self.order.pk}')
        self.assertNotIn('approve', payload['url'])
        self.assertTrue(notification.logs.filter(action='web_push_sent', details__subscription_id=mine.pk).exists())

    def test_ceo_and_future_level_never_receive_actionable_push_before_lower_approval(self):
        self.subscription(self.ceo)
        notification = self.po_notification(self.ceo, level=5)
        self.assertIn('skipped', send_web_push_notification.run(notification.pk))
        self.webpush.assert_not_called()

    def test_current_pr_stage_has_canonical_request_url_and_future_level_is_skipped(self):
        pr = self.requisition()
        self.subscription(self.recipient)
        self.subscription(self.other, 'other')
        current = self.pr_notification(pr)
        future = self.pr_notification(pr, self.other, 1)
        self.assertEqual(send_web_push_notification.run(current.pk)['sent'], 1)
        self.assertEqual(json.loads(self.webpush.call_args.kwargs['data'])['url'], f'/procurement/requisitions/{pr.pk}')
        self.assertIn('skipped', send_web_push_notification.run(future.pk))
        self.assertEqual(self.webpush.call_count, 1)

    def test_reassignment_and_return_to_same_po_assignee_invalidates_old_job(self):
        self.subscription(self.recipient)
        notification = self.po_notification()
        for stage in (
            self.po_stage(0, self.other, assignment_id='replacement'),
            self.po_stage(0, self.recipient, assignment_id='returned'),
        ):
            self.order.approval_log[0] = stage
            self.order.save(update_fields=['approval_log'])
            self.assertIn('skipped', send_web_push_notification.run(notification.pk))
        self.webpush.assert_not_called()

    def test_pr_assignment_generation_change_rejects_old_and_legacy_notifications(self):
        pr = self.requisition()
        notification = self.pr_notification(pr)
        pr.approval_workflow_config[0]['assignment_id'] = 'new-assignment'
        pr.save(update_fields=['approval_workflow_config'])
        self.assertEqual(approval_assignment_issue(notification), 'approval_assignment_changed')
        notification.metadata.pop('assignment_id')
        self.assertEqual(approval_assignment_issue(notification), 'approval_assignment_changed')

    def test_old_level_for_same_employee_is_not_delivered_when_their_next_level_opens(self):
        self.order.approval_log = [
            self.po_stage(0, self.recipient, status='Approved'), self.po_stage(1, self.recipient),
        ]
        self.order.save(update_fields=['approval_log'])
        notification = self.po_notification()
        self.assertEqual(approval_assignment_issue(notification), 'approval_assignment_changed')

    def test_completed_cancelled_rejected_or_missing_order_stops_delivery(self):
        self.subscription(self.recipient)
        notification = self.po_notification()
        for status in ('completed', 'cancelled'):
            self.order.status = status
            self.order.save(update_fields=['status'])
            self.assertIn('skipped', send_web_push_notification.run(notification.pk))
        self.order.status = 'draft'
        self.order.approval_log[0]['status'] = 'Rejected'
        self.order.save(update_fields=['status', 'approval_log'])
        self.assertIn('skipped', send_web_push_notification.run(notification.pk))
        self.order.delete()
        self.assertIn('skipped', send_web_push_notification.run(notification.pk))
        self.webpush.assert_not_called()

    def test_inactive_account_or_employee_profile_never_receives_push(self):
        self.subscription(self.recipient)
        notification = self.po_notification()
        for fields in ({'status': 'suspended'}, {'is_deleted': True}):
            UserProfile.objects.filter(user=self.recipient).update(status='active', is_deleted=False)
            UserProfile.objects.filter(user=self.recipient).update(**fields)
            self.assertEqual(send_web_push_notification.run(notification.pk)['skipped'], 'recipient_inactive')
        UserProfile.objects.filter(user=self.recipient).update(status='active', is_deleted=False)
        get_user_model().objects.filter(pk=self.recipient.pk).update(is_active=False)
        self.assertEqual(send_web_push_notification.run(notification.pk)['skipped'], 'recipient_inactive')
        self.webpush.assert_not_called()

    def test_read_archived_expired_and_opted_out_notifications_are_skipped(self):
        self.subscription(self.recipient)
        for fields in (
            {'is_read': True, 'status': 'READ'}, {'status': 'ARCHIVED'},
            {'expires_at': timezone.now() - timedelta(seconds=1)}, {'send_in_app': False},
        ):
            notification = self.notification(**fields)
            self.assertIn('skipped', send_web_push_notification.run(notification.pk))
        notification = self.notification()
        NotificationPreference.objects.filter(user=self.recipient).update(enable_in_app=False)
        self.assertEqual(send_web_push_notification.run(notification.pk)['skipped'], 'channel_disabled')
        self.webpush.assert_not_called()

    def test_expired_endpoint_is_disabled_without_touching_another_user(self):
        mine = self.subscription(self.recipient)
        other = self.subscription(self.other, 'other')
        self.webpush.side_effect = FakeWebPushException(410)
        self.assertEqual(send_web_push_notification.run(self.notification().pk), {'sent': 0, 'disabled': 1})
        mine.refresh_from_db()
        other.refresh_from_db()
        self.assertFalse(mine.is_active)
        self.assertTrue(other.is_active)

    def test_retry_targets_only_transient_failures_and_rechecks_assignment(self):
        good = self.subscription(self.recipient, 'good')
        transient = self.subscription(self.recipient, 'transient')
        self.webpush.side_effect = lambda **kwargs: (
            (_ for _ in ()).throw(FakeWebPushException(503))
            if kwargs['subscription_info']['endpoint'] == transient.endpoint else None
        )
        notification = self.po_notification()
        with patch.object(send_web_push_notification, 'retry', side_effect=RuntimeError('retry queued')) as retry:
            with self.assertRaisesRegex(RuntimeError, 'retry queued'):
                send_web_push_notification.run(notification.pk)
        self.assertEqual(retry.call_args.kwargs['kwargs'], {
            'notification_id': notification.pk, 'subscription_ids': [transient.pk],
        })
        self.assertTrue(notification.logs.filter(action='web_push_sent', details__subscription_id=good.pk).exists())
        self.order.approval_log[0] = self.po_stage(0, self.other, assignment_id='replacement')
        self.order.save(update_fields=['approval_log'])
        self.webpush.reset_mock()
        result = send_web_push_notification.run(notification.pk, subscription_ids=[transient.pk])
        self.assertIn('skipped', result)
        self.webpush.assert_not_called()

    def test_redelivered_task_does_not_resend_to_already_delivered_browser(self):
        self.subscription(self.recipient)
        notification = self.notification()
        send_web_push_notification.run(notification.pk)
        self.assertEqual(send_web_push_notification.run(notification.pk), {'sent': 0, 'disabled': 0})
        self.webpush.assert_called_once()

    def test_reassignment_during_first_browser_delivery_stops_remaining_browser_fanout(self):
        self.subscription(self.recipient, 'first')
        self.subscription(self.recipient, 'second')
        notification = self.po_notification()

        def reassign_during_delivery(**kwargs):
            self.order.approval_log[0] = self.po_stage(0, self.other, assignment_id='replacement')
            self.order.save(update_fields=['approval_log'])

        self.webpush.side_effect = reassign_during_delivery
        result = send_web_push_notification.run(notification.pk)
        self.assertEqual(result, {'sent': 1, 'disabled': 0, 'skipped': 'approval_no_longer_assigned'})
        self.webpush.assert_called_once()

    def test_celery_retry_accepts_original_positional_task_arguments_and_only_retries_failed_browser(self):
        good = self.subscription(self.recipient, 'good')
        transient = self.subscription(self.recipient, 'transient')
        attempts = {}

        def deliver(**kwargs):
            endpoint = kwargs['subscription_info']['endpoint']
            attempts[endpoint] = attempts.get(endpoint, 0) + 1
            if endpoint == transient.endpoint and attempts[endpoint] == 1:
                raise FakeWebPushException(503)

        self.webpush.side_effect = deliver
        notification = self.po_notification()
        result = send_web_push_notification.apply(args=(notification.pk,), throw=False)
        self.assertEqual(result.get(), {'sent': 1, 'disabled': 0})
        self.assertEqual(attempts, {good.endpoint: 1, transient.endpoint: 2})

    def test_transferred_browser_does_not_receive_former_owners_queued_message(self):
        subscription = self.subscription(self.recipient)
        notification = self.po_notification()
        self.client.force_authenticate(self.other)
        response = self.client.post('/notifications/push-subscribe/', {
            'endpoint': subscription.endpoint, 'keys': {'p256dh': 'new-key', 'auth': 'new-auth'},
        }, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['recipient_user_id'], str(self.other.pk))
        self.assertEqual(send_web_push_notification.run(notification.pk)['sent'], 0)
        self.webpush.assert_not_called()
        self.client.force_authenticate(self.recipient)
        response = self.client.post('/notifications/push-unsubscribe/', {'endpoint': subscription.endpoint}, format='json')
        self.assertEqual(response.data['updated'], 0)
        subscription.refresh_from_db()
        self.assertTrue(subscription.is_active)
        self.assertEqual(subscription.user_id, self.other.pk)

    @override_settings(WEB_PUSH_VAPID_PRIVATE_KEY='test-key', TEAMS_APPROVAL_WEBHOOK_URL='https://flow.example.test')
    @patch('apps.notifications.teams.queue_approval_assignment')
    @patch('apps.notifications.services.send_notification_email.delay')
    @patch('apps.notifications.signals.send_web_push_notification.delay')
    def test_all_channels_are_queued_only_after_successful_commit(self, push, email, teams):
        with self.captureOnCommitCallbacks(execute=True):
            with transaction.atomic():
                notification = NotificationService.create_notification(
                    self.recipient, title='After commit', message='Committed', send_email=True, send_teams=True,
                )
                push.assert_not_called()
                email.assert_not_called()
                teams.assert_not_called()
        push.assert_called_once_with(notification.pk)
        email.assert_called_once_with(notification.pk)
        teams.assert_called_once()
        push.reset_mock()
        email.reset_mock()
        teams.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaises(ValueError), transaction.atomic():
                NotificationService.create_notification(
                    self.recipient, title='Rollback', message='Rolled back', send_email=True, send_teams=True,
                )
                raise ValueError('rollback')
        push.assert_not_called()
        email.assert_not_called()
        teams.assert_not_called()

    @override_settings(TEAMS_APPROVAL_WEBHOOK_URL='https://flow.example.test')
    @patch('apps.notifications.teams.requests.post')
    @patch('apps.notifications.services.EmailMultiAlternatives')
    def test_stale_approval_is_suppressed_in_email_and_teams_retries_too(self, email, post):
        notification = self.po_notification(send_email=True)
        self.order.approval_log[0]['status'] = 'Approved'
        self.order.save(update_fields=['approval_log'])
        self.assertEqual(send_teams_approval_assignment.run(notification.pk)['status'], 'skipped')
        self.assertEqual(send_notification_email.run(notification.pk)['status'], 'skipped')
        email.assert_not_called()
        post.assert_not_called()

    def test_read_detail_and_audit_log_actions_succeed_only_for_owner(self):
        notification = self.po_notification()
        foreign = self.notification(self.other)
        response = self.client.get(f'/notifications/{notification.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['is_read'])
        self.assertEqual(response.data['sent_by_email'], False)
        self.assertEqual(self.client.get(f'/notifications/{foreign.pk}/').status_code, 404)
        response = self.client.post('/notifications/mark_as_read/', {'notification_ids': [foreign.pk]}, format='json')
        self.assertEqual(response.data['marked_read'], 0)
        response = self.client.get('/notifications/logs/')
        self.assertEqual(response.status_code, 200)
        items = response.data.get('results', response.data) if isinstance(response.data, dict) else response.data
        self.assertTrue(any(item['notification'] == notification.pk for item in items))
        self.assertFalse(any(item['notification'] == foreign.pk for item in items))
        self.assertEqual(self.client.post(f'/notifications/{notification.pk}/archive/').status_code, 200)

    def test_historical_assignment_stays_readable_without_advertising_an_approval_action(self):
        notification = self.po_notification()
        self.order.approval_log[0]['status'] = 'Approved'
        self.order.save(update_fields=['approval_log'])
        response = self.client.get(f'/notifications/{notification.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['metadata']['requires_action'])
        self.assertTrue(response.data['metadata']['approval_obsolete'])
        self.assertEqual(response.data['action_label'], 'View Request')
        notification.refresh_from_db()
        self.assertTrue(notification.metadata['requires_action'])

    def test_notification_content_and_action_cannot_be_forged_through_normal_update_api(self):
        notification = self.po_notification()
        response = self.client.patch(f'/notifications/{notification.pk}/', {
            'recipient': self.other.pk, 'action_url': 'https://untrusted.example.test', 'metadata': {'requires_action': True},
        }, format='json')
        self.assertEqual(response.status_code, 405)
        self.assertEqual(self.client.post('/notifications/', {'title': 'Forged'}, format='json').status_code, 405)

    def test_anonymous_subscription_and_insecure_endpoint_are_rejected(self):
        payload = {'endpoint': 'http://push.example.test/a', 'keys': {'p256dh': 'key', 'auth': 'auth'}}
        self.assertEqual(self.client.post('/notifications/push-subscribe/', payload, format='json').status_code, 400)
        self.client.force_authenticate(None)
        self.assertIn(self.client.post('/notifications/push-subscribe/', payload, format='json').status_code, (401, 403))

    def test_email_result_does_not_overwrite_in_app_read_or_delivery_state(self):
        notification = self.notification()
        notification.mark_email_sent(success=False, error_message='Transport failed')
        notification.refresh_from_db()
        self.assertEqual(notification.status, 'SENT')
        notification.mark_as_read()
        notification.mark_email_sent(success=True)
        notification.refresh_from_db()
        self.assertEqual(notification.status, 'READ')
        self.assertIsNone(notification.email_error)

    def test_action_links_reject_external_script_and_protocol_relative_targets(self):
        for target in ('https://untrusted.example.test/a', '//untrusted.example.test', '/\\untrusted.example.test', 'javascript:alert(1)'):
            self.assertEqual(safe_action_url(target), '/notifications')
        self.assertEqual(safe_action_url('https://radai.ae/approvals?tab=procurement'), '/approvals?tab=procurement')
