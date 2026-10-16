"""Regression coverage for letter output on remote storage."""
from unittest.mock import PropertyMock, patch

from django.contrib.auth import get_user_model
from django.db.models.fields.files import FieldFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

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
        template, _ = SalesLetterTemplate.objects.update_or_create(
            letter_type='eoi',
            defaults={'subject_template': 'Test subject', 'body_template': 'Test body'},
        )
        self.actor = actor
        self.letter = SalesLetter.objects.create(
            opportunity=opportunity, letter_type='eoi', template=template,
            subject='Test subject', body='Test body', generated_by=actor,
            custom_data={'letterhead': {'recipient_name': 'Saved Buyer', 'recipient_email': 'buyer@client.test'}},
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
        self.assertNotIn('attachments', response.data['custom_data'])

    def test_repeated_prepare_reuses_saved_letter_and_rendered_files(self):
        from apps.sales.workflow import prepare_letter
        with patch('apps.sales.letter_pdf.generate_letter_pdf', return_value=b'%PDF-test') as pdf, \
             patch('apps.sales.letter_docx.generate_letter_docx', return_value=b'PK-test') as docx:
            refresh_letter_files(self.letter, self.actor)
            pdf.reset_mock()
            docx.reset_mock()
            first = prepare_letter(self.letter.opportunity, self.actor, 'eoi')
            second = prepare_letter(self.letter.opportunity, self.actor, 'eoi')
        self.assertEqual(first.pk, self.letter.pk)
        self.assertEqual(second.pk, first.pk)
        self.assertEqual(SalesLetter.objects.count(), 1)
        pdf.assert_not_called()
        docx.assert_not_called()

    def test_unchanged_save_does_not_render_or_increment_revision(self):
        with patch('apps.sales.letter_pdf.generate_letter_pdf', return_value=b'%PDF-test'), \
             patch('apps.sales.letter_docx.generate_letter_docx', return_value=b'PK-test'):
            refresh_letter_files(self.letter, self.actor)
        self.letter.refresh_from_db()
        old = (self.letter.pdf_file.name, self.letter.docx_file.name, self.letter.updated_at)
        with patch('apps.sales.letter_pdf.generate_letter_pdf') as pdf, \
             patch('apps.sales.letter_docx.generate_letter_docx') as docx:
            result = refresh_letter_files(self.letter, self.actor)
        self.assertEqual(result.custom_data['letterhead']['rev'], 1)
        self.assertEqual((result.pdf_file.name, result.docx_file.name, result.updated_at), old)
        pdf.assert_not_called()
        docx.assert_not_called()

    def test_new_preparation_renders_only_once_and_preserves_later_edits(self):
        from apps.sales.workflow import prepare_letter
        opportunity = self.letter.opportunity
        self.letter.delete()
        with patch('apps.sales.letter_pdf.generate_letter_pdf', return_value=b'%PDF-test') as pdf, \
             patch('apps.sales.letter_docx.generate_letter_docx', return_value=b'PK-test') as docx:
            first = prepare_letter(opportunity, self.actor, 'eoi', {'letterhead': {'recipient_name': 'Saved Buyer', 'recipient_email': 'buyer@client.test'}})
            second = prepare_letter(opportunity, self.actor, 'eoi')
            self.assertEqual(first.pk, second.pk)
            self.assertEqual(pdf.call_count, 1)
            self.assertEqual(docx.call_count, 1)
            first.body = 'Reviewed manually edited body'
            refresh_letter_files(first, self.actor)
            reopened = prepare_letter(opportunity, self.actor, 'eoi')
        self.assertEqual(reopened.pk, first.pk)
        self.assertEqual(reopened.body, 'Reviewed manually edited body')
        self.assertEqual(SalesLetter.objects.count(), 1)
        self.assertEqual(reopened.custom_data['letterhead']['rev'], 1)
        self.assertEqual(pdf.call_count, 2)

    def test_prepared_letter_attachment_cannot_change_its_own_recipient_context(self):
        from io import BytesIO
        from uuid import uuid4
        from apps.sales.models import OpportunityWorkspace, OpportunityWorkspaceUpload
        from apps.sales.workflow import prepare_letter

        opportunity = self.letter.opportunity
        self.letter.delete()
        with patch('apps.sales.letter_pdf.generate_letter_pdf', return_value=b'%PDF-test') as pdf, \
             patch('apps.sales.letter_docx.generate_letter_docx', return_value=b'PK-test') as docx:
            first = prepare_letter(opportunity, self.actor, 'eoi', {'letterhead': {'recipient_name': 'Saved Buyer', 'recipient_email': 'buyer@client.test'}})
            original_files = (first.pdf_file.name, first.docx_file.name)
            workspace = OpportunityWorkspace.objects.create(opportunity=opportunity)
            upload = OpportunityWorkspaceUpload.objects.create(
                workspace=workspace, request_id=uuid4(), actor=self.actor,
                folder_key='correspondence', name='response.pdf', size=9,
                sha256='a' * 64, provider='radai', status='ready',
            )
            first.custom_data['attachments'] = {'files': [
                {'kind': 'pdf', 'status': 'ready', 'file': {'id': f'radai-{upload.pk}'}},
            ]}
            first.save(update_fields=['custom_data'])
            with patch('apps.sales.private_attachments.download_private_file',
                       return_value=(BytesIO(b'%PDF-test'), upload.name)) as download, \
                 patch('apps.sales.document_classification_content.extract_document_text',
                       return_value=('Contact Name: Internal Sender\nEmail: sender@our-company.test', {})):
                reopened = prepare_letter(opportunity, self.actor, 'eoi')
                repeated = prepare_letter(opportunity, self.actor, 'eoi')
            download.assert_not_called()
        self.assertEqual(reopened.pk, first.pk)
        self.assertEqual(repeated.pk, first.pk)
        self.assertEqual((reopened.pdf_file.name, reopened.docx_file.name), original_files)
        self.assertEqual(repeated.custom_data['letterhead']['rev'], 0)
        self.assertEqual(SalesLetter.objects.count(), 1)
        self.assertEqual(pdf.call_count, 1)
        self.assertEqual(docx.call_count, 1)

    def test_source_metadata_change_without_content_change_keeps_output_version(self):
        from apps.sales.workflow import prepare_letter

        opportunity = self.letter.opportunity
        self.letter.delete()
        fields = {'recipient_name': 'Buyer', 'recipient_email': 'buyer@client.test'}
        with patch('apps.sales.letter_extraction.extract_opportunity_letter_fields') as extract, \
             patch('apps.sales.letter_pdf.generate_letter_pdf', return_value=b'%PDF-test') as pdf, \
             patch('apps.sales.letter_docx.generate_letter_docx', return_value=b'PK-test') as docx:
            extract.return_value = {**fields, '_source': {'upload_id': 'first', 'sha256': 'a' * 64}}
            first = prepare_letter(opportunity, self.actor, 'eoi')
            original_files = (first.pdf_file.name, first.docx_file.name)
            extract.return_value = {**fields, '_source': {'upload_id': 'new-version', 'sha256': 'b' * 64}}
            reopened = prepare_letter(opportunity, self.actor, 'eoi')
            reopened = prepare_letter(opportunity, self.actor, 'eoi')
            reopened = refresh_letter_files(reopened, self.actor)
        self.assertEqual(reopened.pk, first.pk)
        self.assertEqual((reopened.pdf_file.name, reopened.docx_file.name), original_files)
        self.assertEqual(reopened.custom_data['letterhead']['rev'], 0)
        self.assertEqual(reopened.custom_data['letter_extraction']['source']['upload_id'], 'new-version')
        self.assertEqual(pdf.call_count, 1)
        self.assertEqual(docx.call_count, 1)

    def test_changed_content_increments_once_and_retry_is_noop(self):
        with patch('apps.sales.letter_pdf.generate_letter_pdf', return_value=b'%PDF-test') as pdf, \
             patch('apps.sales.letter_docx.generate_letter_docx', return_value=b'PK-test') as docx:
            refresh_letter_files(self.letter, self.actor)
            self.letter.body = 'Reviewed new letter body'
            refresh_letter_files(self.letter, self.actor)
            refresh_letter_files(self.letter, self.actor)
        self.letter.refresh_from_db()
        self.assertEqual(self.letter.custom_data['letterhead']['rev'], 2)
        self.assertEqual(self.letter.body, 'Reviewed new letter body')
        self.assertEqual(pdf.call_count, 2)
        self.assertEqual(docx.call_count, 2)

    def test_changed_context_updates_existing_draft_not_new_letter(self):
        from apps.sales.workflow import prepare_letter
        with patch('apps.sales.letter_pdf.generate_letter_pdf', return_value=b'%PDF-test'), \
             patch('apps.sales.letter_docx.generate_letter_docx', return_value=b'PK-test'):
            first = prepare_letter(self.letter.opportunity, self.actor, 'eoi')
            opportunity = self.letter.opportunity
            opportunity.deal_name = 'Updated opportunity context'
            opportunity.save(update_fields=['deal_name'])
            updated = prepare_letter(opportunity, self.actor, 'eoi')
            repeated = prepare_letter(opportunity, self.actor, 'eoi')
        self.assertEqual(first.pk, updated.pk)
        self.assertEqual(updated.pk, repeated.pk)
        self.assertEqual(SalesLetter.objects.count(), 1)
        self.assertEqual(repeated.custom_data['letterhead']['rev'], 2)

    def test_sent_letter_is_reused_but_changed_context_creates_new_draft(self):
        from apps.sales.workflow import prepare_letter
        opportunity = self.letter.opportunity
        self.letter.delete()
        with patch('apps.sales.letter_pdf.generate_letter_pdf', return_value=b'%PDF-test'), \
             patch('apps.sales.letter_docx.generate_letter_docx', return_value=b'PK-test'):
            sent = prepare_letter(opportunity, self.actor, 'eoi', {'letterhead': {'recipient_name': 'Saved Buyer', 'recipient_email': 'buyer@client.test'}})
            sent.mark_sent('buyer@example.test')
            reopened = prepare_letter(opportunity, self.actor, 'eoi')
            self.assertEqual(reopened.pk, sent.pk)
            opportunity.description = 'Updated tender scope'
            opportunity.save(update_fields=['description'])
            draft = prepare_letter(opportunity, self.actor, 'eoi')
            repeated = prepare_letter(opportunity, self.actor, 'eoi')
        self.assertNotEqual(draft.pk, sent.pk)
        self.assertEqual(repeated.pk, draft.pk)
        self.assertEqual(draft.status, 'draft')
        sent.refresh_from_db()
        self.assertEqual(sent.status, 'sent')
        self.assertEqual(SalesLetter.objects.count(), 2)

    @override_settings(FRONTEND_URL='https://www.radai.ae')
    def test_preview_contract_allows_frontend_iframe(self):
        api = APIClient()
        api.force_authenticate(self.actor)
        letters_url = f'/api/v1/sales/deals/{self.letter.opportunity_id}/letters/'
        listed = api.get(letters_url)
        self.assertEqual(listed.status_code, 200)
        preview_path = listed.data[0]['pdf_preview_url']
        self.assertEqual(
            preview_path,
            f'/api/v1/sales/deals/{self.letter.opportunity_id}/letters/{self.letter.pk}/pdf/preview/',
        )
        api.force_authenticate(user=None)
        token = AccessToken.for_user(self.actor)
        with patch('apps.sales.letter_pdf.get_letter_pdf_bytes', return_value=b'%PDF-test'):
            for method in (api.head, api.get):
                response = method(preview_path, {'token': str(token), 'v': '123'})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response['Content-Type'], 'application/pdf')
                self.assertNotIn('X-Frame-Options', response)
                self.assertIn('https://www.radai.ae', response['Content-Security-Policy'])
                self.assertEqual(response['Cache-Control'], 'private, no-store')
                self.assertEqual(response['Content-Length'], str(len(b'%PDF-test')))
                if method == api.get:
                    self.assertEqual(b''.join(response.streaming_content), b'%PDF-test')
                else:
                    self.assertFalse(response.streaming)
                    self.assertEqual(response.content, b'')
