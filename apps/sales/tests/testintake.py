from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from unittest.mock import patch

from apps.sales.models import Client, Contact, SalesEmailIntake

User = get_user_model()


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

    def test_accepts_full_email_body_and_normalizes_html(self):
        self.payload['body'] = '<p>Company name: ABC Energy LLC</p><p>Budget: AED 850,000</p>'

        response = self.client.post(
            self.endpoint,
            self.payload,
            format='json',
            HTTP_X_RADAI_WEBHOOK_KEY='test-webhook-key',
        )

        self.assertEqual(response.status_code, 201)
        intake = SalesEmailIntake.objects.get()
        self.assertIn('Company name: ABC Energy LLC', intake.body_preview)
        self.assertNotIn('<p>', intake.body_preview)


class SalesEmailIntakeReviewTests(TestCase):
    def setUp(self):
        permission = patch(
            'apps.rbac.permissions.HasModuleAccess.has_permission',
            return_value=True,
        )
        permission.start()
        self.addCleanup(permission.stop)
        self.user = User.objects.create_superuser(
            username='sales-reviewer',
            email='reviewer@example.com',
            password='test-password',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.intake = SalesEmailIntake.objects.create(
            source_message_id='review-message-id',
            internet_message_id='<review@example.com>',
            subject='RFQ for grid study',
            sender_name='Client Contact',
            sender_email='client@example.com',
            received_at='2026-09-09T08:30:00Z',
            body_preview='Please submit a commercial proposal.',
            has_attachments=False,
            importance='Normal',
        )
        self.account = Client.objects.create(
            client_code='CLIENT-001',
            company_name='Example Energy',
            industry_type='energy',
        )

    def test_lists_and_starts_review(self):
        listed = self.client.get('/api/v1/sales/email-intakes/')
        started = self.client.post(
            f'/api/v1/sales/email-intakes/{self.intake.id}/start-review/',
            {},
            format='json',
        )

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(started.status_code, 200)
        self.assertEqual(started.data['status'], 'under_review')
        self.assertEqual(started.data['reviewed_by'], self.user.id)
        self.assertEqual(
            started.data['extracted_information']['request_type'],
            'Request for quotation',
        )
        self.assertEqual(
            started.data['extracted_information']['client_domain'],
            'example.com',
        )

    def test_rejection_requires_a_reason(self):
        response = self.client.post(
            f'/api/v1/sales/email-intakes/{self.intake.id}/reject/',
            {},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.intake.refresh_from_db()
        self.assertEqual(self.intake.status, 'received')

    def test_marks_duplicate_with_original_trace(self):
        original = SalesEmailIntake.objects.create(
            source_message_id='original-message-id',
            subject=self.intake.subject,
            sender_email=self.intake.sender_email,
            received_at='2026-09-08T08:30:00Z',
        )
        response = self.client.post(
            f'/api/v1/sales/email-intakes/{self.intake.id}/mark-duplicate/',
            {'duplicate_of': str(original.id)},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'duplicate')
        self.assertEqual(response.data['duplicate_of'], original.id)

    def test_converts_to_opportunity_once_and_preserves_source(self):
        endpoint = (
            f'/api/v1/sales/email-intakes/{self.intake.id}/convert-to-opportunity/'
        )
        payload = {
            'client': str(self.account.id),
            'deal_name': 'Grid stability engineering study',
            'estimated_value': '250000.00',
            'currency': 'AED',
            'expected_close_date': '2026-10-31',
            'submission_due_date': '2026-09-30',
            'scope_type': 'feed',
        }

        created = self.client.post(endpoint, payload, format='json')
        repeated = self.client.post(endpoint, payload, format='json')

        self.assertEqual(created.status_code, 201)
        self.assertTrue(created.data['created'])
        self.assertEqual(repeated.status_code, 200)
        self.assertFalse(repeated.data['created'])
        self.intake.refresh_from_db()
        self.assertEqual(self.intake.status, 'converted')
        self.assertEqual(
            self.intake.opportunity.custom_fields['source_email_intake_id'],
            str(self.intake.id),
        )

    def test_extracts_email_fields_and_creates_client_with_opportunity(self):
        self.intake.subject = (
            'RFQ-2026-0915 | Grid Stability Study | Proposal Required by 18 Sep 2026'
        )
        self.intake.body_preview = (
            'Company name: ABC Energy LLC\n'
            'Client domain: abcenergy.ae\n'
            'Contact person: Ahmed Hassan\n'
            'Contact email: ahmed.hassan@abcenergy.ae\n'
            'Contact phone: +971 50 123 4567\n'
            'Project location: Abu Dhabi, United Arab Emirates\n'
            'Industry: Energy and Utilities\n'
            'Estimated contract value: AED 850,000\n'
        )
        self.intake.save(update_fields=['subject', 'body_preview'])

        detail = self.client.get(
            f'/api/v1/sales/email-intakes/{self.intake.id}/'
        )
        extracted = detail.data['extracted_information']
        self.assertEqual(extracted['tender_reference'], 'RFQ-2026-0915')
        self.assertEqual(extracted['deadline_date'], '2026-09-18')
        self.assertEqual(extracted['company_name'], 'ABC Energy LLC')
        self.assertEqual(extracted['estimated_value'], '850000')

        response = self.client.post(
            f'/api/v1/sales/email-intakes/{self.intake.id}/convert-to-opportunity/',
            {
                'new_client': {
                    'company_name': extracted['company_name'],
                    'industry_type': extracted['industry_type'],
                    'email': extracted['contact_email'],
                    'phone': extracted['contact_phone'],
                    'website': 'https://abcenergy.ae',
                    'country': 'United Arab Emirates',
                    'contact_name': extracted['contact_name'],
                    'contact_email': extracted['contact_email'],
                },
                'deal_name': self.intake.subject,
                'estimated_value': extracted['estimated_value'],
                'currency': extracted['currency'],
                'expected_close_date': '2026-09-25',
                'submission_due_date': extracted['deadline_date'],
                'scope_type': extracted['scope_type'],
                'client_reference': extracted['tender_reference'],
                'location': extracted['location'],
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        created_client = Client.objects.get(company_name='ABC Energy LLC')
        self.assertEqual(response.data['opportunity']['client'], created_client.id)
        self.assertTrue(
            Contact.objects.filter(
                client=created_client,
                email='ahmed.hassan@abcenergy.ae',
                is_primary=True,
            ).exists()
        )
