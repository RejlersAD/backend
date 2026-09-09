from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.sales.models import SalesEmailIntake


@override_settings(SALES_EMAIL_INTAKE_WEBHOOK_KEY='test-webhook-key')
class SalesEmailIntakeTests(TestCase):
    endpoint = '/api/v1/sales/email-intake/'

    def setUp(self):
        self.client = APIClient()
        self.payload = {
            'source_message_id': 'AAMk-test-message-id',
            'internet_message_id': '<message@example.com>',
            'subject': 'RFQ for engineering services',
            'sender_name': 'Client Contact',
            'sender_email': 'client@example.com',
            'received_at': '2026-09-09T08:30:00Z',
            'body_preview': 'Please provide your proposal.',
            'has_attachments': True,
            'importance': 'Normal',
        }

    def test_rejects_invalid_webhook_key(self):
        response = self.client.post(self.endpoint, self.payload, format='json')

        self.assertEqual(response.status_code, 403)
        self.assertEqual(SalesEmailIntake.objects.count(), 0)

    def test_accepts_once_and_reports_retries_as_duplicates(self):
        headers = {'HTTP_X_RADAI_WEBHOOK_KEY': 'test-webhook-key'}

        created = self.client.post(
            self.endpoint,
            self.payload,
            format='json',
            **headers,
        )
        duplicate = self.client.post(
            self.endpoint,
            self.payload,
            format='json',
            **headers,
        )

        self.assertEqual(created.status_code, 201)
        self.assertFalse(created.data['duplicate'])
        self.assertEqual(duplicate.status_code, 200)
        self.assertTrue(duplicate.data['duplicate'])
        self.assertEqual(SalesEmailIntake.objects.count(), 1)
