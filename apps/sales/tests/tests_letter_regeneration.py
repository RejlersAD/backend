"""Regression coverage for letter output on remote storage."""
from unittest.mock import PropertyMock, patch

from django.contrib.auth import get_user_model
from django.db.models.fields.files import FieldFile
from django.test import TestCase
from rest_framework.test import APIClient

from apps.sales.letter_pdf import regenerate_letter_pdf
from apps.sales.models import Client, Deal, SalesLetter, SalesLetterTemplate
from apps.sales.tests.access_fixtures import grant_sales_actions
from apps.sales.workflow import refresh_letter_files


class LetterRegenerationStorageTests(TestCase):
    def setUp(self):
        actor = get_user_model().objects.create_user(
            username='letter-storage-owner', email='letter-storage-owner@example.test',
        )
        grant_sales_actions(actor, 'sales', 'sales_opportunities', 'sales_clients')
        client = Client.objects.create(
            client_code='LETTER-STORAGE', company_name='Letter Storage', account_manager=actor,
        )
        opportunity = Deal.objects.create(
            deal_code='LETTER-STORAGE', deal_name='Letter storage test',
            client=client, owner=actor, stage='proposal', bid_decision='bid',
        )
        template = SalesLetterTemplate.objects.create(
            letter_type='eoi', subject_template='Test subject', body_template='Test body',
        )
        self.actor = actor
        self.letter = SalesLetter.objects.create(
            opportunity=opportunity, letter_type='eoi', template=template,
            subject='Test subject', body='Test body', generated_by=actor,
        )

    def test_refresh_does_not_require_local_storage_paths(self):
        with patch('apps.sales.letter_pdf.generate_letter_pdf', return_value=b'%PDF-test'), \
             patch('apps.sales.letter_docx.generate_letter_docx', return_value=b'PK-test'), \
             patch.object(FieldFile, 'path', new_callable=PropertyMock,
                          side_effect=NotImplementedError('Storage does not support paths')):
            refresh_letter_files(self.letter, self.actor)
        self.letter.refresh_from_db()
        self.assertEqual(self.letter.custom_data['letterhead']['rev'], 1)
        self.assertTrue(self.letter.pdf_file.name)
        self.assertTrue(self.letter.docx_file.name)

    def test_failed_pdf_regeneration_keeps_previous_file(self):
        with patch('apps.sales.letter_pdf.generate_letter_pdf', return_value=b'%PDF-test'):
            regenerate_letter_pdf(self.letter)
        old_name = self.letter.pdf_file.name
        with patch('apps.sales.letter_pdf.generate_letter_pdf', side_effect=RuntimeError('Render failed')):
            with self.assertRaisesMessage(RuntimeError, 'Render failed'):
                regenerate_letter_pdf(self.letter)
        self.letter.refresh_from_db()
        self.assertEqual(self.letter.pdf_file.name, old_name)
        self.assertTrue(self.letter.pdf_file.storage.exists(old_name))

    def test_regenerate_api_accepts_edited_html_with_existing_attachments(self):
        api = APIClient()
        api.force_authenticate(self.actor)
        url = f'/api/v1/sales/deals/{self.letter.opportunity_id}/letters/{self.letter.pk}/regenerate-pdf/'
        payload = {
            'subject': 'Edited subject',
            'body': '<p>Dear Sir,</p>\n\n<p>Updated letter body.</p>',
            'custom_data': {
                'letterhead': {'rev': '0', 'recipient_name': 'Edited recipient'},
                'attachments': {'version': 1, 'files': []},
            },
        }
        with patch('apps.sales.letter_pdf.generate_letter_pdf', return_value=b'%PDF-test'), \
             patch('apps.sales.letter_docx.generate_letter_docx', return_value=b'PK-test'), \
             patch('apps.sales.views.DealViewSet._attach_letter_files'), \
             patch.object(FieldFile, 'path', new_callable=PropertyMock,
                          side_effect=NotImplementedError('Storage does not support paths')):
            response = api.post(url, payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['subject'], payload['subject'])
        self.assertEqual(response.data['body'], payload['body'])
        self.assertEqual(response.data['custom_data']['letterhead']['rev'], 1)
        self.assertEqual(response.data['custom_data']['attachments']['version'], 1)
