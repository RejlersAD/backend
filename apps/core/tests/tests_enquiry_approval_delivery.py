"""Enquiry tasks use canonical department ownership and current delivery identity."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.core.enquiry_workflow import can_approve_enquiry, escalate_enquiry, notify_enquiry_assignment, route_enquiry
from apps.core.models import Enquiry, EnquiryRoutingRule
from apps.notifications.delivery import approval_assignment_issue, delivery_issue, notification_action_url
from apps.notifications.models import Notification
from apps.notifications.serializers import NotificationSerializer
from apps.procurement.tests.approval_fixtures import grant_approval, set_position
from apps.rbac.models import UserProfile, UserRole


class EnquiryApprovalDeliveryTests(TestCase):
    def setUp(self):
        self.head, self.peer, self.admin, self.no_access = [
            get_user_model().objects.create_user(username=f'enquiry-{index}', email=f'enquiry-{index}@example.test',
                                                 is_superuser=index == 2)
            for index in range(4)
        ]
        for user in (self.head, self.peer, self.admin, self.no_access):
            grant_approval(user, 'enquiry_management')
            set_position(user, 'CEO' if user == self.admin else 'CFO, Rejlers Abu Dhabi')
        UserRole.objects.filter(user_profile=self.no_access.rbac_profile).delete()
        # A display profile claiming headship does not override the HR record.
        UserProfile.objects.filter(user=self.admin).update(
            job_title='Head of Finance', department='Finance', metadata={'is_department_head': True},
        )
        EnquiryRoutingRule.objects.update_or_create(inquiry_type='finance_request', defaults={
            'department': 'Finance', 'representative': self.admin, 'sla_hours': 12, 'is_active': True,
        })

    def enquiry(self, **overrides):
        return Enquiry.objects.create(name='Synthetic requester', email='requester@example.test',
            subject='Synthetic finance request', message='Please review.', inquiry_type='finance_request',
            **{'urgency': 'high', **overrides})

    def assigned(self):
        return self.enquiry(department='Finance', assigned_to=self.head, assigned_at=timezone.now(),
                            status='assigned', approval_required=True, approval_status='pending')

    def notice(self, enquiry):
        notify_enquiry_assignment(enquiry)
        return Notification.objects.get(recipient=enquiry.assigned_to, metadata__enquiry_id=enquiry.pk)

    def test_urgent_route_ignores_profile_head_and_selects_canonical_head_with_access(self):
        enquiry = route_enquiry(self.enquiry())
        self.assertEqual(enquiry.assigned_to, self.head)
        self.assertTrue(can_approve_enquiry(self.head, enquiry))
        self.assertFalse(can_approve_enquiry(self.admin, enquiry))
        notification = Notification.objects.get(metadata__enquiry_id=enquiry.pk)
        self.assertEqual(notification.category.name, 'APPROVAL')
        self.assertEqual(notification.metadata['event_type'], 'enquiry_approval_assignment')
        self.assertEqual(notification.metadata['assigned_at'], enquiry.assigned_at.isoformat())
        self.assertTrue(notification.metadata['requires_action'])
        self.assertEqual(delivery_issue(notification, 'web_push'), '')

    def test_no_eligible_canonical_head_leaves_urgent_enquiry_unassigned(self):
        UserRole.objects.filter(user_profile__user__in=[self.head, self.peer]).delete()
        enquiry = route_enquiry(self.enquiry())
        self.assertIsNone(enquiry.assigned_to)
        self.assertEqual(enquiry.approval_status, 'pending')
        self.assertFalse(Notification.objects.filter(metadata__enquiry_id=enquiry.pk).exists())

    def test_normal_information_routing_keeps_existing_behavior_without_approval_rights(self):
        enquiry = route_enquiry(self.enquiry(urgency='normal'))
        self.assertEqual(enquiry.assigned_to, self.admin)
        notification = Notification.objects.get(metadata__enquiry_id=enquiry.pk)
        self.assertEqual(notification.category.name, 'INFO')
        self.assertFalse(notification.metadata['requires_action'])
        self.assertFalse(can_approve_enquiry(self.admin, enquiry))

    def test_generic_status_edit_cannot_skip_required_approval(self):
        from rest_framework.test import APIRequestFactory, force_authenticate
        from apps.core.views_enquiry import enquiry_detail
        enquiry = self.assigned()
        request = APIRequestFactory().patch('/api/v1/admin/enquiries/',
            {'status': 'pending_confirmation'}, format='json')
        force_authenticate(request, self.admin)
        response = enquiry_detail(request, pk=enquiry.pk)
        self.assertEqual(response.status_code, 400, response.data)
        enquiry.refresh_from_db()
        self.assertEqual(enquiry.status, 'assigned')
        self.assertEqual(enquiry.approval_status, 'pending')

    def test_escalation_sends_approval_only_to_current_assignee_others_receive_information(self):
        enquiry = self.assigned()
        escalate_enquiry(enquiry, reason='Synthetic SLA escalation')
        notices = Notification.objects.filter(metadata__enquiry_id=enquiry.pk)
        self.assertEqual(notices.filter(category__name='APPROVAL').count(), 1)
        self.assertEqual(notices.get(category__name='APPROVAL').recipient_id, self.head.pk)
        peer_notice = notices.get(recipient=self.peer)
        self.assertFalse(peer_notice.metadata['requires_action'])
        self.assertEqual(approval_assignment_issue(peer_notice), '')
        self.assertFalse(notices.filter(recipient=self.admin).exists())

    def test_queued_assignment_rechecks_access_position_and_stage(self):
        enquiry = self.assigned()
        notice = self.notice(enquiry)
        self.assertEqual(approval_assignment_issue(notice), '')
        set_position(self.head, 'Engineer')
        self.assertTrue(delivery_issue(notice, 'web_push'))
        set_position(self.head, 'CFO, Rejlers Abu Dhabi')
        UserRole.objects.filter(user_profile=self.head.rbac_profile).delete()
        self.assertTrue(delivery_issue(notice, 'web_push'))
        grant_approval(self.head, 'enquiry_management')
        Enquiry.objects.filter(pk=enquiry.pk).update(approval_status='approved')
        self.assertTrue(delivery_issue(notice, 'web_push'))
        self.assertFalse(NotificationSerializer(notice).data['metadata']['requires_action'])

    def test_reassignment_to_same_user_does_not_revive_old_notice(self):
        enquiry = self.assigned()
        notice = self.notice(enquiry)
        Enquiry.objects.filter(pk=enquiry.pk).update(assigned_at=enquiry.assigned_at + timedelta(seconds=1))
        self.assertEqual(approval_assignment_issue(notice), 'approval_assignment_changed')
        enquiry.refresh_from_db()
        notify_enquiry_assignment(enquiry)
        current = Notification.objects.filter(metadata__enquiry_id=enquiry.pk).latest('pk')
        self.assertEqual(approval_assignment_issue(current), '')
        current.metadata.pop('assigned_at')
        self.assertEqual(approval_assignment_issue(current), 'approval_context_invalid')

    def test_forged_recipient_and_link_do_not_transfer_business_action(self):
        enquiry = self.assigned()
        notice = self.notice(enquiry)
        notice.action_url = '/admin/users'
        self.assertEqual(notification_action_url(notice), f'/admin/enquiries/{enquiry.pk}')
        notice.recipient = self.admin
        self.assertTrue(approval_assignment_issue(notice))
        self.assertTrue(NotificationSerializer(notice).data['metadata']['approval_obsolete'])
