from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.core.enquiry_workflow import (
    add_initial_message, add_response, confirm_resolution, escalate_enquiry,
    propose_resolution, route_enquiry, submit_feedback,
)
from apps.core.models import Enquiry, EnquiryActivity, EnquiryFeedback, EnquiryMessage, EnquiryRoutingRule
from apps.notifications.models import Notification
from apps.rbac.models import Organization, UserProfile


class EnquiryWorkflowTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.requester = User.objects.create_user(
            username='requester', email='requester@example.com', password='test-password',
        )
        self.representative = User.objects.create_user(
            username='representative', email='representative@example.com', password='test-password',
        )
        organization = Organization.objects.create(name='Test Organization', code='TEST-ORG')
        UserProfile.objects.create(
            user=self.representative, organization=organization, status='active',
            department='Finance', job_title='Head of Finance', employee_id='TEST-001',
        )
        EnquiryRoutingRule.objects.update_or_create(
            inquiry_type='finance_request',
            defaults={
                'department': 'Finance', 'representative': self.representative,
                'sla_hours': 12, 'is_active': True,
            },
        )

    def test_routes_to_configured_department_and_representative(self):
        enquiry = Enquiry.objects.create(
            name='Requesting User', email=self.requester.email, phone='12345678',
            subject='Payment request help', message='Please review this payment request.',
            inquiry_type='finance_request', requester=self.requester,
        )
        add_initial_message(enquiry)
        route_enquiry(enquiry)

        enquiry.refresh_from_db()
        self.assertEqual(enquiry.department, 'Finance')
        self.assertEqual(enquiry.assigned_to, self.representative)
        self.assertEqual(enquiry.status, 'assigned')
        self.assertIsNotNone(enquiry.due_at)
        self.assertEqual(EnquiryMessage.objects.filter(enquiry=enquiry).count(), 1)
        self.assertTrue(EnquiryActivity.objects.filter(enquiry=enquiry, action='auto_routed').exists())

    def test_public_response_notifies_requester_and_updates_status(self):
        enquiry = Enquiry.objects.create(
            name='Requesting User', email=self.requester.email, phone='12345678',
            subject='Payment request help', message='Please review this payment request.',
            inquiry_type='finance_request', requester=self.requester,
            assigned_to=self.representative, department='Finance', status='assigned',
        )

        add_response(enquiry, actor=self.representative, body='Your request has been reviewed.')

        enquiry.refresh_from_db()
        self.assertEqual(enquiry.status, 'responded')
        self.assertIsNotNone(enquiry.first_response_at)
        self.assertTrue(Notification.objects.filter(
            recipient=self.requester, metadata__enquiry_id=enquiry.pk,
        ).exists())

    def test_internal_note_is_hidden_and_does_not_notify_requester(self):
        enquiry = Enquiry.objects.create(
            name='Requesting User', email=self.requester.email, phone='12345678',
            subject='Payment request help', message='Please review this payment request.',
            inquiry_type='finance_request', requester=self.requester,
            assigned_to=self.representative, department='Finance', status='assigned',
        )

        message = add_response(
            enquiry, actor=self.representative, body='Finance verification pending.', is_internal=True,
        )

        enquiry.refresh_from_db()
        self.assertTrue(message.is_internal)
        self.assertEqual(enquiry.status, 'assigned')
        self.assertFalse(Notification.objects.filter(
            recipient=self.requester, metadata__enquiry_id=enquiry.pk,
        ).exists())

    def test_escalation_resolution_confirmation_and_feedback_closure(self):
        enquiry = Enquiry.objects.create(
            name='Requesting User', email=self.requester.email, phone='12345678',
            subject='Urgent payment support', message='Please resolve this payment issue.',
            inquiry_type='finance_request', requester=self.requester,
            assigned_to=self.representative, department='Finance', status='in_progress',
        )
        escalate_enquiry(enquiry, actor=self.representative, reason='SLA exceeded')
        enquiry.refresh_from_db()
        self.assertEqual(enquiry.status, 'escalated')
        self.assertEqual(enquiry.escalation_level, 1)

        enquiry.approval_required = False
        enquiry.approval_status = 'not_required'
        enquiry.save(update_fields=['approval_required', 'approval_status'])
        propose_resolution(enquiry, actor=self.representative, summary='Payment access restored.')
        confirm_resolution(enquiry, actor=self.requester, accepted=True)
        enquiry.refresh_from_db()
        self.assertEqual(enquiry.status, 'closed')
        self.assertIsNotNone(enquiry.closed_at)
        submit_feedback(enquiry, actor=self.requester, rating=5, comment='Excellent support.')

        enquiry.refresh_from_db()
        self.assertEqual(enquiry.status, 'closed')
        self.assertTrue(EnquiryFeedback.objects.filter(enquiry=enquiry, rating=5).exists())
