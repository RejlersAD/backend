from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase
from rest_framework.exceptions import PermissionDenied

from apps.sales.letter_extraction import _eml_fields, extract_letter_fields
from apps.sales.models import SalesEmailIntake
from apps.sales.tests.access_fixtures import grant_sales_actions
from apps.sales.tests.tests_letter_regeneration import LetterRegenerationStorageTests
from apps.sales.workflow import prepare_letter
from apps.sales.workspace_models import OpportunityWorkspace, OpportunityWorkspaceUpload


class ContactParsingTests(SimpleTestCase):
    def test_ariba_event_contact_overrides_system_sender_and_supplier_login(self):
        fields = _eml_fields(BytesIO(
            b'From: Noor Buyer <system@ariba.com>\n'
            b'To: Supplier Director <supplier@example.test>\n'
            b'Date: Mon, 17 Aug 2026 04:38:30 -0700\n'
            b'Message-ID: <transport-123@ariba.com>\n\n'
            b'Example Oil Company has invited you to participate in the following event: EOI.\n\n'
            b'Use the following username to log in: supplier@example.test.\n\n'
            b'If you have questions about this event, contact Noor Buyer via telephone at  '
            b'or via e-mail at Noor.Buyer@client.test.\n\nThank You,\nExample Oil Company\n\n'
            b'Example Oil Company sourcing site, Event Doc123: EOI, Realm: example, '
            b'Message ID: MSG123456, https://example.ariba.com/event\n'))
        self.assertEqual(fields['recipient_name'], 'Noor Buyer')
        self.assertEqual(fields['recipient_email'], 'Noor.Buyer@client.test')
        self.assertEqual(fields['recipient_company'], 'Example Oil Company')
        self.assertEqual(fields['your_ref'], 'SAP Ariba email notifications dated: 17,08,2026 ,Message ID :MSG123456')
        self.assertNotIn('recipient_title', fields)
        self.assertNotIn('recipient_address', fields)

    def test_ariba_contact_labels_and_original_email_reference(self):
        fields = _eml_fields(BytesIO(
            b'From: SAP Ariba <notification@ariba.com>\r\n'
            b'To: Internal Sales <sales@example.test>\r\n'
            b'Date: Wed, 14 Oct 2026 23:30:00 +0000\r\n'
            b'Message-ID: <invitation-123@ariba.com>\r\n'
            b'Content-Type: text/plain; charset=utf-8\r\n\r\n'
            b'Contact Name: Aisha Ali\nPosition: Procurement Manager\n'
            b'Company: Client Engineering LLC\nEmail: aisha@client.test\n'
            b'Address: Tower 2, Abu Dhabi\n'))
        self.assertEqual(fields, {
            'recipient_name': 'Aisha Ali', 'recipient_title': 'Procurement Manager',
            'recipient_company': 'Client Engineering LLC', 'recipient_email': 'aisha@client.test',
            'recipient_address': 'Tower 2, Abu Dhabi',
            'your_ref': 'SAP Ariba email notifications dated: 14,10,2026 ,Message ID :<invitation-123@ariba.com>',
        })

    def test_signature_beats_automation_sender(self):
        fields = extract_letter_fields(
            'Dear Sales Team,\nPlease quote.\nBest regards,\nAisha Ali\n'
            'Procurement Manager\nClient Engineering LLC\naisha@client.test\n'
            'P.O. Box 123, Abu Dhabi', sender_name='Notifications', sender_email='robot@ariba.com')
        self.assertEqual(fields['recipient_name'], 'Aisha Ali')
        self.assertEqual(fields['recipient_email'], 'aisha@client.test')
        self.assertEqual(fields['recipient_company'], 'Client Engineering LLC')
        self.assertIn('Box 123', fields['recipient_address'])

    def test_from_fallback_never_uses_to_or_salutation(self):
        fields = _eml_fields(BytesIO(b'From: Omar Ali <omar@client.test>\n'
            b'To: Sales Director <sales@example.test>\n\nDear Sales Director,\nPlease quote.'))
        self.assertEqual(fields['recipient_name'], 'Omar Ali')
        self.assertEqual(fields['recipient_email'], 'omar@client.test')
        self.assertNotIn('recipient_title', fields)
        self.assertNotIn('your_ref', fields)

    def test_forwarded_signature_is_not_current_contact(self):
        fields = extract_letter_fields('Please quote.\n\nFrom: Older Person <old@client.test>\n'
            'Date: Tue, 1 Sep 2026 10:00:00 +0000\n\nRegards,\nOlder Person\n'
            'Director\nOld Company', sender_name='Current Person', sender_email='current@client.test')
        self.assertNotEqual(fields.get('recipient_name'), 'Older Person')

    def test_missing_values_and_invalid_date_are_not_invented(self):
        fields = extract_letter_fields('Date: unavailable\n\nPlease respond.')
        self.assertEqual(fields, {})

    def test_html_mime_preserves_header_message_id(self):
        fields = _eml_fields(BytesIO(b'From: SAP Ariba <robot@ariba.com>\n'
            b'Date: Wed, 14 Oct 2026 10:00:00 +0000\nMessage-ID: <html@ariba.com>\n'
            b'Content-Type: text/html; charset=utf-8\n\n'
            b'<p>Contact Name: Aisha Ali</p><p>Email: aisha@client.test</p>'))
        self.assertEqual(fields['recipient_name'], 'Aisha Ali')
        self.assertIn('<html@ariba.com>', fields['your_ref'])

    def test_outlook_sent_time_can_supply_reference_date(self):
        import base64
        import json
        from pathlib import Path
        import struct
        import olefile
        from apps.sales.document_classification_msg import extract_msg_text

        fixtures = json.loads(Path(__file__).with_name('fixtures').joinpath('classification-messages.json').read_text('utf-8'))
        stream = BytesIO(base64.b64decode(fixtures['plain']))
        sent = datetime(2026, 10, 14, 9, tzinfo=timezone.utc)
        ticks = int((sent - datetime(1601, 1, 1, tzinfo=timezone.utc)).total_seconds() * 10_000_000)
        with olefile.OleFileIO(stream, write_mode=True) as archive:
            props = bytearray(archive.openstream('__properties_version1.0').read())
            self.assertGreaterEqual(len(props), 48)
            offset = next((offset for offset in range(32, len(props), 16)
                           if struct.unpack_from('<I', props, offset)[0] == 0x00390040), len(props) - 16)
            struct.pack_into('<IIQ', props, offset, 0x00390040, 0, ticks)
            archive.write_stream('__properties_version1.0', bytes(props))
            for path in archive.listdir():
                if len(path) == 1 and path[0].startswith('__substg1.0_007D'):
                    transport = archive.openstream(path).read()
                    transport = transport.replace(b'Date:', b'Xate:').replace('Date:'.encode('utf-16-le'), 'Xate:'.encode('utf-16-le'))
                    archive.write_stream(path, transport)
        payload = stream.getvalue()
        text, error = extract_msg_text(BytesIO(payload), len(payload))
        self.assertEqual(error, '')
        fields = extract_letter_fields(text)
        self.assertIn('14,10,2026', fields['your_ref'])


class LetterExtractionWorkflowTests(LetterRegenerationStorageTests):
    """Reuse the existing synthetic Sales owner and letter setup."""

    def setUp(self):
        super().setUp()
        grant_sales_actions(self.actor, 'sales_email_intake')
        self.deal = self.letter.opportunity
        self.intake = SalesEmailIntake.objects.create(
            opportunity=self.deal, source_message_id='intake-id', internet_message_id='<mail@ariba.com>',
            sender_name='SAP Ariba', sender_email='notification@ariba.com', subject='Invitation',
            received_at=datetime(2026, 10, 15, 10, tzinfo=timezone.utc),
            sent_at=datetime(2026, 10, 14, 9, tzinfo=timezone.utc),
            body_preview='Contact Name: Aisha Ali\nPosition: Buyer\nCompany: Client Ltd\n'
                         'Email: aisha@client.test\nAddress: Abu Dhabi',
        )

    def prepare(self, kind='eoi', custom_data=None):
        with patch('apps.sales.letter_pdf.save_letter_pdf'), patch('apps.sales.letter_docx.save_letter_docx'):
            return prepare_letter(self.deal, self.actor, kind, custom_data)

    def test_intake_fields_are_snapshotted_and_edits_win(self):
        letter = self.prepare(custom_data={'letterhead': {'recipient_title': 'Reviewed title'}})
        head = letter.custom_data['letterhead']
        self.assertEqual(head['recipient_name'], 'Aisha Ali')
        self.assertEqual(head['recipient_title'], 'Reviewed title')
        self.assertIn('14,10,2026', head['your_ref'])
        self.assertIn('<mail@ariba.com>', head['your_ref'])
        self.assertEqual(letter.custom_data['letter_extraction']['source'], {'intake_id': str(self.intake.pk)})
        self.deal.client.refresh_from_db()
        self.assertEqual(self.deal.client.company_name, 'Letter Storage')

    def upload(self):
        workspace = OpportunityWorkspace.objects.create(opportunity=self.deal)
        return OpportunityWorkspaceUpload.objects.create(workspace=workspace, request_id=uuid4(),
            actor=self.actor, folder_key='tender', name='invitation.eml', size=100, sha256='a' * 64,
            provider='radai', status='ready')

    def test_uploaded_email_is_used_and_stream_closed(self):
        self.upload()
        stream = BytesIO(b'From: Uploaded Person <upload@client.test>\n'
            b'Date: Tue, 13 Oct 2026 10:00:00 +0000\nMessage-ID: <uploaded@client.test>\n\nPlease quote.')
        with patch('apps.sales.private_attachments.download_private_file', return_value=(stream, 'invitation.eml')):
            letter = self.prepare()
        self.assertEqual(letter.custom_data['letterhead']['recipient_name'], 'Uploaded Person')
        self.assertIn('13,10,2026', letter.custom_data['letterhead']['your_ref'])
        self.assertTrue(stream.closed)

    def test_prepare_uses_ariba_contact_and_company_instead_of_saved_client(self):
        self.upload()
        stream = BytesIO(b'From: Noor Buyer <system@ariba.com>\n'
            b'Date: Mon, 17 Aug 2026 04:38:30 -0700\nMessage-ID: <transport@ariba.com>\n\n'
            b'Example Oil Company has invited you to participate in the following event: EOI.\n'
            b'Use the following username to log in: supplier@example.test.\n'
            b'If you have questions about this event, contact Noor Buyer via telephone at '
            b'or via e-mail at Noor.Buyer@client.test.\n'
            b'Example Oil Company sourcing site, Event Doc123: EOI, Message ID: MSG123456\n')
        with patch('apps.sales.private_attachments.download_private_file', return_value=(stream, 'invitation.eml')):
            letter = self.prepare()
        head = letter.custom_data['letterhead']
        self.assertEqual(head['recipient_name'], 'Noor Buyer')
        self.assertEqual(head['recipient_email'], 'Noor.Buyer@client.test')
        self.assertEqual(head['recipient_company'], 'Example Oil Company')
        self.assertIn('MSG123456', head['your_ref'])
        self.assertTrue(letter.custom_data['letter_extraction']['source']['upload_id'])

    def test_denied_file_falls_back_to_intake(self):
        self.upload()
        with patch('apps.sales.private_attachments.download_private_file', side_effect=PermissionDenied):
            letter = self.prepare()
        self.assertEqual(letter.custom_data['letterhead']['recipient_name'], 'Aisha Ali')

    def test_email_read_denial_does_not_extract_intake_body(self):
        with patch('apps.sales.letter_extraction.module_action_allowed', return_value=False):
            letter = self.prepare()
        self.assertEqual(letter.custom_data['letterhead']['recipient_name'], '')
        self.assertNotIn('letter_extraction', letter.custom_data)

    def test_both_regret_types_use_same_extraction(self):
        from apps.sales.models import SalesLetterTemplate
        self.deal.stage, self.deal.bid_decision = 'no_bid', 'no_bid'
        self.deal.save()
        for kind in ('regret_expertise', 'regret_manpower'):
            SalesLetterTemplate.objects.update_or_create(letter_type=kind,
                defaults={'subject_template': 'Regret', 'body_template': 'Dear {{ client_contact_name }}'})
            letter = self.prepare(kind)
            self.assertEqual(letter.body, 'Dear Aisha Ali')
