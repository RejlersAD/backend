"""Source-bound local pilot: real private storage, no cloud/Graph delivery."""
from email.message import EmailMessage
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from uuid import NAMESPACE_URL, uuid5
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.rbac.route_guard import secure_module_endpoints
from apps.sales.email_archive_files import archive_reviewed_files, sales_test_archive_files
from apps.sales.email_archive_source import payload_from_eml
from apps.sales.intake_views import SalesEmailIntakeViewSet
from apps.sales.models import Client, Deal, OpportunityAuditEvent, OpportunityWorkspaceUpload, SalesEmailIntake
from apps.sales.private_attachments import download_private_file, upload_private_file
from apps.sales.tests.access_fixtures import deny_sales_action, grant_sales_actions
from apps.sales.tests.test_private_attachments import PrivateFixtures
from apps.sales.tests.test_opportunity_workspace import CONFIG

router = DefaultRouter()
router.register('email-intakes', SalesEmailIntakeViewSet, basename='archive-intakes')
urlpatterns = [path('api/v1/sales/email-intake/archive-files/', sales_test_archive_files),
               path('api/v1/sales/', include(router.urls))]
secure_module_endpoints(urlpatterns)
MAILBOX = 'sales@example.test'
KEY = 'synthetic-archive-key'


def original_email(*, inline=False, repeated=False):
    message = EmailMessage()
    message['Subject'] = 'RADAI-TEST RFQ SOURCE-123'
    message['From'] = 'Buyer Name <buyer@example.test>'
    message['To'] = MAILBOX
    message['Message-ID'] = '<source-123@example.test>'
    message['Received'] = 'by delivery.example.test; Tue, 03 Nov 2026 09:00:00 +0000'
    message.set_content('Client Company: Synthetic Client\nRFQ Reference: SOURCE-123\nPlease quote for engineering.')
    message.add_attachment(b'%PDF-synthetic', maintype='application', subtype='pdf', filename='RFQ.pdf')
    if repeated:
        message.add_attachment(b'%PDF-second', maintype='application', subtype='pdf', filename='RFQ.pdf')
    if inline:
        message.add_attachment(b'fake-inline-image', maintype='image', subtype='png',
                               filename='logo.png', disposition='inline')
    return message.as_bytes()


@override_settings(**CONFIG, ROOT_URLCONF=__name__, ENVIRONMENT='testing',
                   SALES_TEST_ARCHIVE_FILES_ENABLED=True, SALES_EMAIL_INTAKE_WEBHOOK_KEY=KEY)
class EmailArchiveFilesTests(PrivateFixtures, TestCase):
    def setUp(self):
        super().setUp()
        grant_sales_actions(self.actor, 'sales_email_intake')
        self.machine = APIClient()
        self.endpoint = '/api/v1/sales/email-intake/archive-files/'
        self.raw = original_email()
        self.payload = payload_from_eml(self.raw, MAILBOX)
        self.intake = SalesEmailIntake.objects.create(**self.payload)

    def reviewed(self):
        self.intake.opportunity = self.opportunity
        self.intake.reviewed_by = self.actor
        self.intake.status = 'converted'
        self.intake.save()
        OpportunityAuditEvent.objects.create(opportunity=self.opportunity, actor=self.actor,
            event_type='opportunity_created_from_email', data={'source_email_intake_id': str(self.intake.pk)})

    def request(self, *, upload=False, key=KEY, raw=None, **extra):
        data = {'source_message_id': self.payload['source_message_id'], **extra}
        if upload:
            data.update(mailbox=MAILBOX, file=SimpleUploadedFile('source.eml', self.raw if raw is None else raw,
                                                               content_type='message/rfc822'))
        return self.machine.post(self.endpoint, data, format='multipart' if upload else 'json',
                                 HTTP_X_RADAI_WEBHOOK_KEY=key)

    def test_unreviewed_source_waits_and_never_creates_files(self):
        response = self.request(upload=True)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['state'], 'awaiting_review')
        self.assertFalse(OpportunityWorkspaceUpload.objects.exists())

    def test_status_probe_rechecks_review_but_does_not_upload(self):
        self.reviewed()
        response = self.request()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['state'], 'ready')
        self.assertFalse(OpportunityWorkspaceUpload.objects.exists())

    def test_routes_original_and_pdf_and_identical_retry_is_one_pair(self):
        self.reviewed()
        for _ in range(2):
            response = self.request(upload=True)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['file_count'], 2)
        uploads = OpportunityWorkspaceUpload.objects.order_by('folder_key')
        self.assertEqual(uploads.count(), 2)
        self.assertEqual(list(uploads.values_list('folder_key', flat=True)), ['correspondence', 'tender'])
        for upload in uploads:
            stream, _ = download_private_file(self.opportunity, self.actor, upload.folder_key, f'radai-{upload.pk}')
            try:
                self.assertEqual(stream.read(), self.raw if upload.folder_key == 'correspondence' else b'%PDF-synthetic')
            finally:
                stream.close()
        self.assertEqual(OpportunityAuditEvent.objects.filter(event_type='workspace_file_uploaded').count(), 2)

    def test_key_and_production_and_disabled_gates(self):
        self.reviewed()
        self.assertEqual(self.request(upload=True, key='wrong').status_code, 403)
        with override_settings(ENVIRONMENT='production'):
            self.assertEqual(self.request(upload=True).status_code, 503)
        with override_settings(SALES_TEST_ARCHIVE_FILES_ENABLED=False):
            self.assertEqual(self.request(upload=True).status_code, 503)
        self.assertFalse(OpportunityWorkspaceUpload.objects.exists())

    def test_current_intake_and_opportunity_permission_denials(self):
        self.reviewed()
        deny_sales_action(self.actor, 'sales_opportunities', 'update')
        response = self.request(upload=True)
        self.assertEqual(response.status_code, 403, response.data)
        self.assertFalse(OpportunityWorkspaceUpload.objects.exists())

    def test_revoked_intake_read_blocks_status_and_files(self):
        self.reviewed()
        deny_sales_action(self.actor, 'sales_email_intake', 'read')
        self.assertEqual(self.request().status_code, 403)
        self.assertFalse(OpportunityWorkspaceUpload.objects.exists())

    def test_inactive_reviewer_and_missing_audit_are_not_authority(self):
        self.reviewed()
        self.actor.is_active = False
        self.actor.save(update_fields=['is_active'])
        self.assertEqual(self.request(upload=True).status_code, 403)
        self.actor.is_active = True
        self.actor.save(update_fields=['is_active'])
        OpportunityAuditEvent.objects.all().delete()
        self.assertEqual(self.request(upload=True).status_code, 403)
        self.assertFalse(OpportunityWorkspaceUpload.objects.exists())

    def test_source_bytes_and_reviewed_metadata_cannot_be_rebound(self):
        self.reviewed()
        self.assertEqual(self.request(upload=True, raw=self.raw + b'changed').status_code, 400)
        self.intake.body_preview = 'Changed reviewed body'
        self.intake.save(update_fields=['body_preview'])
        self.assertEqual(self.request(upload=True).status_code, 400)
        self.assertFalse(OpportunityWorkspaceUpload.objects.exists())

    def test_caller_cannot_choose_target_actor_or_destination(self):
        self.reviewed()
        for key in ('opportunity_id', 'actor_id', 'folder_key', 'filename'):
            response = self.request(upload=True, **{key: 'forbidden'})
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(OpportunityWorkspaceUpload.objects.exists())

    def test_non_test_sources_cannot_enter_the_pilot(self):
        self.payload['source_message_id'] = 'a-real-live-mailbox-source'
        self.assertEqual(self.request(upload=True).status_code, 400)
        self.assertFalse(OpportunityWorkspaceUpload.objects.exists())

    def test_partial_storage_failure_retains_first_upload_and_retry_finishes(self):
        self.reviewed()
        attempts = []

        def fail_second(*args, **kwargs):
            attempts.append(args[2])
            if len(attempts) == 2:
                raise RuntimeError('synthetic storage outage')
            return upload_private_file(*args, **kwargs)

        with patch('apps.sales.email_archive_files.upload_private_file', side_effect=fail_second):
            with self.assertRaisesMessage(RuntimeError, 'synthetic storage outage'):
                archive_reviewed_files(self.payload['source_message_id'], MAILBOX, self.raw)
        self.assertEqual(OpportunityWorkspaceUpload.objects.filter(status='ready').count(), 1)
        response = self.request(upload=True)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(OpportunityWorkspaceUpload.objects.count(), 2)

    def test_inline_images_are_not_tender_files_and_same_names_remain_separate(self):
        self.intake.delete()
        self.raw = original_email(inline=True, repeated=True)
        self.payload = payload_from_eml(self.raw, MAILBOX)
        self.intake = SalesEmailIntake.objects.create(**self.payload)
        self.reviewed()
        response = self.request(upload=True)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['file_count'], 3)
        uploads = OpportunityWorkspaceUpload.objects.filter(folder_key='tender')
        self.assertEqual(uploads.count(), 2)
        self.assertEqual(len(set(uploads.values_list('name', flat=True))), 2)
        self.assertEqual(set(uploads.values_list('version_number', flat=True)), {1})

    def matching_source(self):
        self.opportunity.client_reference = 'SOURCE-123'
        self.opportunity.save()
        self.match_url = f'/api/v1/sales/email-intakes/{self.intake.pk}/'
        detail = self.api.get(self.match_url)
        self.assertEqual(detail.status_code, 200, detail.data)
        return {'opportunity': str(self.opportunity.pk), 'source_token': detail.data['source_token'],
                'classification_code': 'rfq', 'classification_confirmed': True}

    def test_exact_match_requires_human_save_and_retains_one_link_audit_on_retry(self):
        payload = self.matching_source()
        result = self.api.get(self.match_url + 'opportunity-matches/')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['state'], 'unique_match')
        self.intake.refresh_from_db()
        self.assertIsNone(self.intake.opportunity_id)
        for _ in range(2):
            result = self.api.post(self.match_url + 'link-opportunity/', payload, format='json')
            self.assertEqual(result.status_code, 200, result.data)
        self.intake.refresh_from_db()
        self.assertEqual(self.intake.opportunity_id, self.opportunity.pk)
        self.assertEqual(Deal.objects.count(), 1)
        self.assertEqual(OpportunityAuditEvent.objects.filter(event_type='opportunity_linked_from_email').count(), 1)
        self.assertEqual(self.request().data['state'], 'ready')
        self.assertEqual(self.request(upload=True).status_code, 200)
        self.assertEqual(OpportunityWorkspaceUpload.objects.count(), 2)

    def test_ambiguous_reference_requires_review_and_cannot_link(self):
        payload = self.matching_source()
        Deal.objects.create(deal_code='Q-102102', deal_name='Other', client=self.client_record,
                            owner=self.actor, client_reference='SOURCE-123')
        result = self.api.get(self.match_url + 'opportunity-matches/')
        self.assertEqual(result.data['state'], 'requires_review')
        self.assertEqual(self.api.post(self.match_url + 'link-opportunity/', payload, format='json').status_code, 409)

    def test_ambiguous_company_never_suggests_a_link(self):
        self.matching_source()
        Client.objects.create(client_code='DUP', company_name='Synthetic Client',
                              industry_type='other', account_manager=self.other)
        self.assertEqual(self.api.get(self.match_url + 'opportunity-matches/').data['matches'], [])

    def test_stale_source_denied_permission_and_unconfirmed_classification(self):
        payload = self.matching_source()
        self.assertEqual(self.api.post(self.match_url + 'link-opportunity/',
            {**payload, 'classification_confirmed': False}, format='json').status_code, 400)
        original_body = self.intake.body_preview
        self.intake.body_preview += '\nAdditional source text.'
        self.intake.save()
        self.assertEqual(self.api.post(self.match_url + 'link-opportunity/', payload, format='json').status_code, 409)
        self.intake.body_preview = original_body
        self.intake.save()
        deny_sales_action(self.actor, 'sales_opportunities', 'update')
        fresh = self.matching_source()
        self.assertEqual(self.api.post(self.match_url + 'link-opportunity/', fresh, format='json').status_code, 403)
        self.assertFalse(OpportunityAuditEvent.objects.filter(event_type='opportunity_linked_from_email').exists())

    def test_nonpilot_and_rejected_sources_cannot_link(self):
        payload = self.matching_source()
        self.intake.status = 'rejected'
        self.intake.save()
        self.assertEqual(self.api.post(self.match_url + 'link-opportunity/', payload, format='json').status_code, 409)
        with override_settings(ENVIRONMENT='production'):
            self.assertEqual(self.api.get(self.match_url + 'opportunity-matches/').status_code, 404)

    def test_routes_only_unambiguous_content_and_preserves_destinations_on_retry(self):
        self.intake.delete()
        self.raw = routing_email()
        self.payload = payload_from_eml(self.raw, MAILBOX)
        self.intake = SalesEmailIntake.objects.create(**self.payload)
        self.reviewed()
        for _ in range(2):
            response = self.request(upload=True)
            self.assertEqual(response.status_code, 200, response.data)
        folders = list(OpportunityWorkspaceUpload.objects.order_by('folder_key').values_list('folder_key', flat=True))
        self.assertEqual(folders, ['correspondence', 'proposal', 'tender'])
        self.assertIn('attachment_routing_requires_review', response.data['warnings'])

    def test_existing_tender_only_upload_is_not_moved_when_retry_gains_routing(self):
        self.intake.delete()
        self.raw = routing_email()
        self.payload = payload_from_eml(self.raw, MAILBOX)
        self.intake = SalesEmailIntake.objects.create(**self.payload)
        self.reviewed()
        from apps.sales.email_archive_files import _safe_name
        upload_private_file(self.opportunity, self.actor, 'tender',
            SimpleUploadedFile(_safe_name('scope.txt', 1, self.payload['source_message_id']),
                               b'Technical Proposal\nEngineering scope', content_type='text/plain'),
            uuid5(NAMESPACE_URL, f'radai-test-archive:{self.intake.pk}:1'))
        response = self.request(upload=True)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(OpportunityWorkspaceUpload.objects.count(), 3)
        self.assertFalse(OpportunityWorkspaceUpload.objects.filter(folder_key='proposal').exists())

    def test_link_audit_failure_rolls_back_source_and_never_creates_opportunity(self):
        payload = self.matching_source()
        with patch('apps.sales.email_archive_matching.OpportunityAuditEvent.objects.create',
                   side_effect=RuntimeError('synthetic audit outage')):
            with self.assertRaisesMessage(RuntimeError, 'synthetic audit outage'):
                self.api.post(self.match_url + 'link-opportunity/', payload, format='json')
        self.intake.refresh_from_db()
        self.assertIsNone(self.intake.opportunity_id)
        self.assertEqual(Deal.objects.count(), 1)

    def test_six_folder_taxonomy_and_filename_only_fallback(self):
        from email import policy
        from email.parser import BytesParser
        message = BytesParser(policy=policy.default).parsebytes(original_email())
        expected = {
            'mail.txt': ('Incoming Mail', 'correspondence'),
            'invitation.txt': ('Request for tender', 'tender'),
            'scope.txt': ('Technical Proposal', 'proposal'),
            'review.txt': ('Bid Review', 'internal'),
            'receipt.txt': ('Submission Receipt', 'submitted'),
            'notice.txt': ('Letter of Award', 'award'),
            'Commercial_Proposal.txt': ('Unrelated body text', 'tender'),
        }
        for name, (text, _) in expected.items():
            message.add_attachment(text.encode(), maintype='text', subtype='plain', filename=name)
        self.intake.delete()
        self.raw = message.as_bytes()
        self.payload = payload_from_eml(self.raw, MAILBOX)
        self.intake = SalesEmailIntake.objects.create(**self.payload)
        self.reviewed()
        response = self.request(upload=True)
        self.assertEqual(response.status_code, 200, response.data)
        for name, (_, folder) in expected.items():
            upload = OpportunityWorkspaceUpload.objects.get(name__endswith='_' + name)
            self.assertEqual(upload.folder_key, folder)
        self.assertIn('attachment_routing_requires_review', response.data['warnings'])


def routing_email():
    message = EmailMessage()
    message['Subject'] = 'RADAI-TEST RFQ SOURCE-123'
    message['From'] = 'Buyer Name <buyer@example.test>'
    message['To'] = MAILBOX
    message['Message-ID'] = '<routing@example.test>'
    message['Received'] = 'by delivery.example.test; Tue, 03 Nov 2026 09:00:00 +0000'
    message.set_content('Client Company: Synthetic Client\nPlease quote for engineering.')
    message.add_attachment(b'Technical Proposal\nEngineering scope', maintype='text', subtype='plain', filename='scope.txt')
    message.add_attachment(b'Technical Proposal\nCommercial Proposal', maintype='text', subtype='plain', filename='mixed.txt')
    return message.as_bytes()


@skipUnless(connection.vendor == 'postgresql', 'Requires disposable PostgreSQL row locks.')
@override_settings(**CONFIG, ROOT_URLCONF=__name__, ENVIRONMENT='testing',
                   SALES_TEST_ARCHIVE_FILES_ENABLED=True, SALES_EMAIL_INTAKE_WEBHOOK_KEY=KEY)
class ConcurrentArchiveLinkTests(PrivateFixtures, TransactionTestCase):
    def test_competing_confirmations_retain_one_link_and_one_audit(self):
        grant_sales_actions(self.actor, 'sales_email_intake')
        intake = SalesEmailIntake.objects.create(**payload_from_eml(original_email(), MAILBOX))
        self.opportunity.client_reference = 'SOURCE-123'
        self.opportunity.save()
        url = f'/api/v1/sales/email-intakes/{intake.pk}/'
        detail = self.api.get(url)
        self.assertEqual(detail.status_code, 200, detail.data)
        payload = {'opportunity': str(self.opportunity.pk), 'source_token': detail.data['source_token'],
                   'classification_code': 'rfq', 'classification_confirmed': True}
        start = Barrier(2)

        def confirm():
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(self.actor)
                start.wait(timeout=10)
                result = client.post(url + 'link-opportunity/', payload, format='json')
                return result.status_code
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda _: confirm(), range(2)))
        self.assertEqual(results, [200, 200])
        self.assertEqual(OpportunityAuditEvent.objects.filter(event_type='opportunity_linked_from_email').count(), 1)
        intake.refresh_from_db()
        self.assertEqual(intake.opportunity_id, self.opportunity.pk)
        self.assertEqual(Deal.objects.count(), 1)
