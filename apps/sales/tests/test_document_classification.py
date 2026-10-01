"""Synthetic source, provider, authorization and durable classification regressions."""
from datetime import timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.sales.classification_models import OpportunityDocumentClassificationRun as Run
from apps.sales.document_classification import (
    ClassificationConflict, _claim, classification_projection, due_classification_ids,
    get_document_classification, retry_document_classification, run_document_classification,
    save_document_classification,
)
from apps.sales.document_classification_content import extract_document_text, rule_suggestion, type_catalog
from apps.sales.models import OpportunityAuditEvent, OpportunityWorkspaceUpload
from apps.sales.tests.test_private_attachments import PrivateFixtures
from apps.sales.tests.test_opportunity_workspace import CONFIG
from apps.sales.tasks import dispatch_document_classifications


class DocumentExtractionTests(SimpleTestCase):
    def test_document_provider_uses_document_system_instructions(self):
        from apps.sales.email_ai_provider import analyze_document_sources
        configuration = SimpleNamespace(enabled=True, error_code='', provider='openai', model='synthetic-model',
                                        max_output_tokens=1000)
        response = SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(
            content='{"document_type":"unclassified"}', refusal=None, tool_calls=None, function_call=None))], usage=None)
        with patch('apps.sales.email_ai_provider._configuration', return_value=configuration), \
                patch('apps.sales.email_ai_provider._openai_client') as client:
            client.return_value.__enter__.return_value.chat.completions.create.return_value = response
            result = analyze_document_sources({'content': 'Untrusted document'}, {
                'type': 'object', 'additionalProperties': False, 'properties': {}, 'required': []})
        self.assertEqual(result['status'], 'completed')
        arguments = client.return_value.__enter__.return_value.chat.completions.create.call_args.kwargs
        self.assertIn('Classify a document business purpose', arguments['messages'][0]['content'])
        self.assertNotIn('Extract reviewable commercial email', arguments['messages'][0]['content'])
        self.assertFalse(arguments['store'])
        self.assertEqual(arguments['max_completion_tokens'], 800)

    def test_explicit_taxonomy_rules_and_ambiguous_titles(self):
        for name, expected in [('Technical_Proposal.docx', 'technical_proposal'), ('PBG.pdf', 'pbg'),
                               ('Sales-to-Delivery Handover.pdf', 'sales_delivery_handover'),
                               ('Incoming Mail.msg', 'incoming_mail'), ('Bid Review.xlsx', 'bid_review')]:
            self.assertEqual(rule_suggestion(name, '')[0], expected)
        self.assertEqual(rule_suggestion('Technical and Commercial Proposal.docx', '')[0], 'unclassified')
        self.assertEqual(rule_suggestion('Technical Proposal Commercial Proposal.docx', '')[0], 'unclassified')
        self.assertEqual(rule_suggestion('document.msg', 'From: someone@example.test\nTo: sales@example.test')[0], 'unclassified')
        self.assertEqual(len(type_catalog()), 27)

    def archive(self, name, content):
        stream = BytesIO()
        with ZipFile(stream, 'w', ZIP_DEFLATED) as archive:
            archive.writestr(name, content)
        stream.seek(0)
        return stream

    def test_passive_docx_xlsx_pptx_text_and_pointer_restore(self):
        for name, member, text in [('file.docx', 'word/document.xml', 'Technical Proposal'),
                                  ('file.xlsx', 'xl/sharedStrings.xml', 'Costing Sheet'),
                                  ('file.pptx', 'ppt/slides/slide1.xml', 'KOM Presentation')]:
            stream = self.archive(member, '<root><t>' + text + '</t><f>EXTERNAL()</f></root>')
            stream.seek(3)
            self.assertEqual(extract_document_text(stream, name, len(stream.getvalue())), (text, ''))
            self.assertEqual(stream.tell(), 3)

    def test_corrupt_entity_archive_and_source_budget_fail_closed(self):
        self.assertEqual(extract_document_text(BytesIO(b'not zip'), 'file.docx', 7)[1], 'invalid_document')
        stream = self.archive('word/document.xml', '<!DOCTYPE a [<!ENTITY x "private">]><a>&x;</a>')
        self.assertEqual(extract_document_text(stream, 'file.docx', len(stream.getvalue()))[1], 'unsafe_xml')
        stream = self.archive('word/document.xml', '<a>' + 'x' * (4 * 1024 * 1024) + '</a>')
        self.assertEqual(extract_document_text(stream, 'file.docx', len(stream.getvalue()))[1], 'archive_limit')
        source = BytesIO(b'private')
        source.seek(2)
        self.assertEqual(extract_document_text(source, 'large.pdf', 33 * 1024 * 1024), ('', 'source_too_large'))
        self.assertEqual(source.tell(), 2)

    def test_real_pdf_extraction_and_unsupported_bytes(self):
        import fitz
        with fitz.open() as pdf:
            pdf.new_page().insert_text((72, 72), 'Letter of Award')
            value = pdf.tobytes()
        text, error = extract_document_text(BytesIO(value), 'award.pdf', len(value))
        self.assertIn('Letter of Award', text)
        self.assertEqual(error, '')
        self.assertEqual(extract_document_text(BytesIO(b'garbage'), 'invalid.pdf', 7)[1], 'invalid_document')
        self.assertEqual(extract_document_text(BytesIO(b'\x00\x00'), 'binary.txt', 2)[1], 'unsupported_text_encoding')


@override_settings(**CONFIG, SALES_EMAIL_AI_ENABLED=False)
class DocumentClassificationTests(PrivateFixtures, TestCase):
    def setUp(self):
        super().setUp()
        self.provider = patch('apps.sales.document_classification.analyze_document_sources',
                              side_effect=AssertionError('No live provider calls in tests')).start()
        self.addCleanup(patch.stopall)
        self.config = patch('apps.sales.document_classification.email_ai_configuration', return_value={
            'ready': False, 'enabled': False, 'provider': 'openai', 'model': '', 'error_code': 'disabled'}).start()
        patch('apps.sales.document_classification.email_ai_cache_identity', return_value='synthetic-configuration').start()

    def ready(self, name='Technical Proposal.txt', body=b'Technical Proposal\nOriginal scope'):
        response = self.upload(name=name, content=body)
        self.assertEqual(response.status_code, 201, response.data)
        upload = OpportunityWorkspaceUpload.objects.get(pk=response.data['id'][6:])
        return upload, Run.objects.get(upload=upload)

    def save_type(self, upload, kind='contract', revision=0, request_id=None):
        return save_document_classification(self.opportunity.pk, self.actor, upload.folder_key,
            'radai-' + str(upload.pk), {'document_type': kind, 'expected_revision': revision,
                                      'request_id': str(request_id or uuid4())})

    def save_tag(self, upload, tag='Client specific scope', revision=0, request_id=None, **extra):
        return save_document_classification(self.opportunity.pk, self.actor, upload.folder_key,
            'radai-' + str(upload.pk), {'custom_tag': tag, 'expected_revision': revision,
                                      'request_id': str(request_id or uuid4()), **extra})

    def enable_ai(self, proposal=None, callback=None):
        self.config.return_value = {'ready': True, 'enabled': True, 'provider': 'anthropic',
                                    'model': 'synthetic-model', 'error_code': ''}
        self.provider.side_effect = callback
        self.provider.return_value = {'status': 'completed', 'error_code': '', 'provider': 'anthropic',
            'model': 'synthetic-model', 'proposal': proposal or {
                'document_type': 'discipline_input', 'source': 'content', 'excerpt': 'Civil team quantities'}}

    def test_upload_commits_durable_intent_rules_are_not_ai_and_replay_one_intent(self):
        upload, run = self.ready()
        self.assertIn(run.pk, due_classification_ids())
        self.assertEqual(run_document_classification(run.pk), {'processed': True, 'status': 'completed'})
        result = classification_projection(upload, self.actor)
        self.assertEqual((result['document_type'], result['origin'], result['ai_status']),
                         ('technical_proposal', 'rule', 'not_needed'))
        self.provider.assert_not_called()
        self.assertFalse(run_document_classification(run.pk)['processed'])
        self.assertEqual(OpportunityAuditEvent.objects.filter(event_type='document_classification_finished').count(), 1)

    def test_broker_failure_retains_sql_work_and_expired_claim_is_recovered(self):
        upload, run = self.ready()
        with patch('apps.sales.tasks.classify_opportunity_document.delay', side_effect=RuntimeError('offline')):
            self.assertEqual(dispatch_document_classifications(), {'dispatched': 0})
        self.assertIn(run.pk, due_classification_ids())
        first = _claim(run.pk)
        self.assertIsNotNone(first)
        self.assertIsNone(_claim(run.pk))
        Run.objects.filter(pk=run.pk).update(lease_until=timezone.now() - timedelta(seconds=1))
        self.assertTrue(run_document_classification(run.pk)['processed'])
        run.refresh_from_db()
        self.assertEqual(run.attempts, 2)

    def test_unavailable_ai_and_unsupported_content_are_truthful(self):
        upload, run = self.ready('unknown.bin', b'opaque bytes')
        run_document_classification(run.pk)
        result = classification_projection(upload, self.actor)
        self.assertEqual((result['origin'], result['document_type'], result['ai_status']),
                         ('unclassified', 'unclassified', 'disabled'))
        self.assertEqual(result['extraction_code'], 'unsupported_format')
        self.provider.assert_not_called()

    def test_confirm_retry_and_stale_command_preserve_human_choice(self):
        upload, run = self.ready()
        request_id = uuid4()
        first = self.save_type(upload, request_id=request_id)
        self.assertEqual((first['classification']['document_type'], first['classification']['origin']), ('contract', 'confirmed'))
        self.assertTrue(self.save_type(upload, request_id=request_id)['replayed'])
        with self.assertRaises(ClassificationConflict):
            self.save_type(upload, 'insurance', request_id=request_id)
        with self.assertRaises(ClassificationConflict):
            self.save_type(upload, 'insurance')
        run_document_classification(run.pk)
        self.assertEqual(classification_projection(upload, self.actor)['document_type'], 'contract')
        retry = retry_document_classification(self.opportunity.pk, self.actor, upload.folder_key,
            'radai-' + str(upload.pk), {'expected_revision': 1, 'request_id': str(uuid4())})
        self.assertEqual(retry['classification']['status'], 'queued')
        self.assertEqual(retry['classification']['origin'], 'confirmed')

    def test_ai_suggestion_is_evidenced_not_confirmed_and_manual_race_wins(self):
        upload, run = self.ready('input.txt', b'Civil team quantities')
        def during_call(*args, **kwargs):
            self.save_type(upload, 'bid_review')
            return {'status': 'completed', 'error_code': '', 'provider': 'anthropic', 'model': 'synthetic-model',
                    'proposal': {'document_type': 'discipline_input', 'source': 'content', 'excerpt': 'Civil team quantities'}}
        self.enable_ai(callback=during_call)
        run_document_classification(run.pk)
        result = classification_projection(upload, self.actor)
        self.assertEqual((result['document_type'], result['origin'], result['suggested_type']),
                         ('bid_review', 'confirmed', 'discipline_input'))
        self.assertEqual(result['ai_status'], 'completed')

    def test_invalid_ai_evidence_and_guessed_mail_direction_are_not_used(self):
        for proposal in ({'document_type': 'contract', 'source': 'content', 'excerpt': 'Invented'},
                         {'document_type': 'incoming_mail', 'source': 'content', 'excerpt': 'Civil team quantities'},
                         {'document_type': 'approve', 'source': 'content', 'excerpt': 'Civil team quantities'}):
            upload, run = self.ready(str(uuid4()) + '.txt', b'Civil team quantities')
            self.enable_ai(proposal)
            run_document_classification(run.pk)
            result = classification_projection(upload, self.actor)
            self.assertEqual(result['document_type'], 'unclassified')
            self.assertEqual(result['error_code'], 'invalid_evidence')
            self.assertEqual(result['status'], 'failed')

    def test_provider_failure_is_safe_and_durable_retry_is_bounded(self):
        upload, run = self.ready('input.txt', b'Civil team quantities')
        self.enable_ai()
        self.provider.return_value = {'status': 'failed', 'error_code': 'provider_timeout',
                                     'provider': 'anthropic', 'model': 'synthetic-model', 'proposal': None}
        for attempt in range(3):
            Run.objects.filter(pk=run.pk).update(next_attempt_at=timezone.now() - timedelta(seconds=1))
            run_document_classification(run.pk)
        run.refresh_from_db()
        self.assertEqual((run.attempts, run.status, run.next_attempt_at), (3, 'failed', None))
        self.assertNotIn(run.pk, due_classification_ids())
        self.assertEqual(classification_projection(upload, self.actor)['ai_status'], 'failed')

    def test_current_authority_checked_before_source_and_before_final_write(self):
        upload, run = self.ready('input.txt', b'Civil team quantities')
        self.actor.is_active = False
        self.actor.save(update_fields=['is_active'])
        with patch('apps.sales.private_attachments.download_private_file') as download:
            self.assertFalse(run_document_classification(run.pk)['processed'])
            download.assert_not_called()
        run.refresh_from_db()
        self.assertEqual(run.status, 'blocked')
        with self.assertRaises(PermissionDenied):
            self.save_type(upload)

    def test_permission_revocation_during_ai_discards_source_result(self):
        upload, run = self.ready('input.txt', b'Civil team quantities')
        def revoke(*args, **kwargs):
            type(self.actor).objects.filter(pk=self.actor.pk).update(is_active=False)
            return {'status': 'completed', 'error_code': '', 'provider': 'anthropic', 'model': 'synthetic-model',
                    'proposal': {'document_type': 'discipline_input', 'source': 'content', 'excerpt': 'Civil team quantities'}}
        self.enable_ai(callback=revoke)
        run_document_classification(run.pk)
        run.refresh_from_db()
        self.assertEqual((run.status, run.suggested_type, run.evidence), ('blocked', 'unclassified', []))

    def test_changed_storage_blocks_filename_only_job_before_any_content_read(self):
        upload, run = self.ready()
        with override_settings(SALES_ATTACHMENT_ROOT=self.private_root.name + '/changed'), \
                patch('apps.sales.document_classification.SOURCE_BYTES', 1), \
                patch('apps.sales.private_attachments.download_private_file') as download:
            self.assertFalse(run_document_classification(run.pk)['processed'])
        download.assert_not_called()
        run.refresh_from_db()
        self.assertEqual((run.status, run.suggested_type), ('blocked', 'unclassified'))

    def test_storage_change_during_ai_discards_suggestion(self):
        upload, run = self.ready('input.txt', b'Civil team quantities')
        changed = override_settings(SALES_ATTACHMENT_ROOT=self.private_root.name + '/changed')
        def change_storage(*args, **kwargs):
            changed.enable()
            return {'status': 'completed', 'error_code': '', 'provider': 'anthropic', 'model': 'synthetic-model',
                    'proposal': {'document_type': 'discipline_input', 'source': 'content', 'excerpt': 'Civil team quantities'}}
        self.enable_ai(callback=change_storage)
        try:
            run_document_classification(run.pk)
        finally:
            changed.disable()
        run.refresh_from_db()
        self.assertEqual((run.status, run.suggested_type, run.evidence), ('blocked', 'unclassified', []))

    def test_storage_tampering_never_reaches_ai_and_failed_audit_rolls_back(self):
        upload, run = self.ready('input.txt', b'Civil team quantities')
        self.enable_ai()
        from apps.sales.private_attachments import _identity
        storage = _identity(upload)
        with storage.open(upload.storage_name, 'wb') as source:
            source.write(b'altered')
        run_document_classification(run.pk)
        self.provider.assert_not_called()
        run.refresh_from_db()
        self.assertEqual(run.error_code, 'processing_unavailable')
        with patch('apps.sales.document_classification._audit', side_effect=RuntimeError('audit offline')):
            with self.assertRaises(RuntimeError):
                self.save_type(upload)
        self.assertEqual(classification_projection(upload, self.actor)['revision'], 0)

    def test_content_evidence_hidden_without_export_and_wrong_scope_rejected(self):
        upload, run = self.ready()
        run_document_classification(run.pk)
        with patch('apps.sales.document_classification.workspace_allowed', side_effect=lambda actor, *actions: 'export' not in actions):
            result = classification_projection(upload, self.actor)
        self.assertEqual(result['document_type'], 'technical_proposal')
        self.assertEqual(result['evidence'], [])
        self.assertFalse(result['can_retry'])
        with self.assertRaises(PermissionDenied):
            get_document_classification(self.opportunity.pk, self.other, upload.folder_key, 'radai-' + str(upload.pk))

    def test_classification_get_is_read_only_and_catalog_has_no_approval_effects(self):
        upload, run = self.ready()
        stage = self.opportunity.stage
        result = get_document_classification(self.opportunity.pk, self.actor, upload.folder_key, 'radai-' + str(upload.pk))
        self.assertEqual(result['classification']['status'], 'queued')
        self.assertEqual(len(result['type_catalog']), 27)
        self.save_type(upload, 'loa')
        self.opportunity.refresh_from_db()
        self.assertEqual(self.opportunity.stage, stage)

    def test_http_get_confirm_denial_and_stale_responses(self):
        upload, run = self.ready()
        url = self.url + 'folders/proposal/files/radai-' + str(upload.pk) + '/classification/'
        response = self.api.get(url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response['Cache-Control'], 'no-store, private')
        payload = {'document_type': 'pbg', 'expected_revision': 0, 'request_id': str(uuid4())}
        saved = self.api.post(url, payload, format='json')
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(saved.data['classification']['origin'], 'confirmed')
        self.assertEqual(self.api.post(url, {**payload, 'request_id': str(uuid4())}, format='json').status_code, 409)
        self.api.force_authenticate(self.other)
        self.assertIn(self.api.get(url).status_code, (403, 404))

    def test_guarded_head_requires_only_read_and_never_mutates_classification(self):
        from rest_framework.test import APIRequestFactory, force_authenticate
        from apps.rbac.action_policy import module_action_allowed
        from apps.rbac.models import RolePermission
        from apps.rbac.route_guard import ModuleActionGuardMixin
        from apps.sales.classification_models import (
            OpportunityDocumentClassification, OpportunityDocumentClassificationCommand,
        )
        from apps.sales.views import DealViewSet

        upload, run = self.ready()
        url = self.url + 'folders/proposal/files/radai-' + str(upload.pk) + '/classification/'
        RolePermission.objects.filter(permission__module__code__in=['sales', 'sales_opportunities']).exclude(
            permission__action='read').delete()
        actor = type(self.actor).objects.get(pk=self.actor.pk)
        self.assertTrue(module_action_allowed(actor, 'sales_opportunities', 'read'))
        self.assertFalse(module_action_allowed(actor, 'sales_opportunities', 'update'))
        guarded = type('DealViewSet', (ModuleActionGuardMixin, DealViewSet), {'__module__': 'apps.sales.views'})
        callback = guarded.as_view({'get': 'workspace_file_classification', 'post': 'workspace_file_classification'})
        before = list(Run.objects.values())
        audit_count = OpportunityAuditEvent.objects.count()
        request = APIRequestFactory().head(url)
        force_authenticate(request, actor)
        response = callback(request, pk=self.opportunity.pk, folder_key='proposal', file_id='radai-' + str(upload.pk))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response['Cache-Control'], 'no-store, private')
        self.assertFalse(OpportunityDocumentClassification.objects.exists())
        self.assertFalse(OpportunityDocumentClassificationCommand.objects.exists())
        self.assertEqual(list(Run.objects.values()), before)
        self.assertEqual(OpportunityAuditEvent.objects.count(), audit_count)
        request = APIRequestFactory().post(url, {
            'document_type': 'contract', 'expected_revision': 0, 'request_id': str(uuid4()),
        }, format='json')
        force_authenticate(request, actor)
        denied = callback(request, pk=self.opportunity.pk, folder_key='proposal', file_id='radai-' + str(upload.pk))
        self.assertEqual(denied.status_code, 403)
        self.assertFalse(OpportunityDocumentClassificationCommand.objects.exists())

    def test_custom_tag_is_independent_normalized_and_clear_preserves_confirmed_type(self):
        upload, run = self.ready()
        self.save_type(upload, 'technical_proposal')
        tagged = self.save_tag(upload, '  Cafe\u0301 scope  ', revision=1)['classification']
        self.assertEqual((tagged['custom_tag'], tagged['document_type'], tagged['origin'], tagged['revision']),
                         ('Café scope', 'technical_proposal', 'confirmed', 2))
        typed = self.save_type(upload, 'commercial_proposal', revision=2)['classification']
        self.assertEqual(typed['custom_tag'], 'Café scope')
        cleared = self.save_tag(upload, '', revision=3)['classification']
        self.assertEqual((cleared['custom_tag'], cleared['document_type'], cleared['revision']),
                         ('', 'commercial_proposal', 4))
        event = OpportunityAuditEvent.objects.filter(event_type='document_custom_tag_changed').latest('occurred_at')
        self.assertEqual(event.data['before_custom_tag'], 'Café scope')
        self.assertEqual(event.data['custom_tag'], '')
        self.assertEqual(event.data['before'], event.data['document_type'])

    def test_tag_only_does_not_confirm_type_or_queue_and_timestamp_records_edit(self):
        upload, run = self.ready('unknown.txt', b'Uncertain content')
        Run.objects.filter(pk=run.pk).delete()
        before = classification_projection(upload, self.actor)
        self.assertEqual(before['custom_tag'], '')
        tagged = self.save_tag(upload)['classification']
        self.assertEqual((tagged['origin'], tagged['document_type'], tagged['label']),
                         ('unclassified', 'unclassified', 'Unclassified'))
        self.assertIsNone(tagged['confirmed_scope'])
        self.assertIsNotNone(tagged['updated_at'])
        self.assertFalse(Run.objects.exists())
        self.assertFalse(OpportunityAuditEvent.objects.filter(event_type='document_type_confirmed').exists())
        self.assertEqual(OpportunityAuditEvent.objects.filter(event_type='document_custom_tag_changed').count(), 1)

    def test_custom_tag_retry_identity_stale_revision_and_combined_update(self):
        upload, run = self.ready()
        request_id = uuid4()
        self.save_tag(upload, request_id=request_id)
        self.assertTrue(self.save_tag(upload, request_id=request_id)['replayed'])
        with self.assertRaises(ClassificationConflict):
            self.save_tag(upload, 'Different', request_id=request_id)
        with self.assertRaises(ClassificationConflict):
            self.save_tag(upload, 'Different')
        combined = self.save_tag(upload, 'Reviewed bid', revision=1, document_type='bid_review')['classification']
        self.assertEqual((combined['custom_tag'], combined['document_type'], combined['revision']),
                         ('Reviewed bid', 'bid_review', 2))
        self.assertEqual(OpportunityAuditEvent.objects.filter(event_type='document_custom_tag_changed').count(), 1)

    def test_custom_tag_validation_and_actual_http_metadata_save(self):
        import json
        upload, run = self.ready()
        url = self.url + 'folders/proposal/files/radai-' + str(upload.pk) + '/classification/'
        for value in (None, True, 4, ['label'], {'label': 'x'}, 'x' * 81, 'line\nbreak', 'nul\x00', 'line\u2028break', '\ud800'):
            with self.subTest(tag=repr(value)):
                response = self.api.post(url, json.dumps({'custom_tag': value, 'expected_revision': 0,
                                              'request_id': str(uuid4())}), content_type='application/json')
                self.assertEqual(response.status_code, 400, response.data)
        response = self.api.post(url, {'custom_tag': 'x' * 80, 'expected_revision': 0,
                                      'request_id': str(uuid4())}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['classification']['custom_tag'], 'x' * 80)
        self.assertEqual(self.api.get(url).data['classification']['custom_tag'], 'x' * 80)

    def test_custom_tag_survives_late_ai_and_new_revision_without_entering_provider_input(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        upload, run = self.ready('input.txt', b'Civil team quantities')
        def during_call(*args, **kwargs):
            self.save_tag(upload, 'Private manual context')
            return {'status': 'completed', 'error_code': '', 'provider': 'anthropic', 'model': 'synthetic-model',
                    'proposal': {'document_type': 'discipline_input', 'source': 'content', 'excerpt': 'Civil team quantities'}}
        self.enable_ai(callback=during_call)
        run_document_classification(run.pk)
        tagged = classification_projection(upload, self.actor)
        self.assertEqual((tagged['custom_tag'], tagged['document_type'], tagged['origin']),
                         ('Private manual context', 'discipline_input', 'ai'))
        self.assertNotIn('Private manual context', str(self.provider.call_args))
        url = self.url + 'folders/proposal/files/radai-' + str(upload.pk) + '/'
        detail = self.api.get(url).data
        version = self.api.post(url + 'versions/upload/', {
            'file': SimpleUploadedFile('Revised.txt', b'Civil team revised quantities'),
            'upload_request_id': str(uuid4()), 'expected_token': detail['head_token'], 'revision_note': 'Synthetic revision',
        }, format='multipart')
        self.assertEqual(version.status_code, 201, version.data)
        self.assertEqual(version.data['classification']['custom_tag'], 'Private manual context')
        self.assertEqual(self.api.get(url).data['classification']['custom_tag'], 'Private manual context')
        stale = self.api.post(url + 'classification/', {'custom_tag': 'Old revision edit', 'expected_revision': 1,
                                                       'request_id': str(uuid4())}, format='json')
        self.assertEqual(stale.status_code, 409)

    def test_custom_tag_denial_and_audit_failure_preserve_existing_label(self):
        upload, run = self.ready()
        self.save_tag(upload, 'Retained')
        with patch('apps.sales.document_classification._audit', side_effect=RuntimeError('audit offline')):
            with self.assertRaises(RuntimeError):
                self.save_tag(upload, 'Lost change', revision=1)
        self.assertEqual(classification_projection(upload, self.actor)['custom_tag'], 'Retained')
        self.actor.is_active = False
        self.actor.save(update_fields=['is_active'])
        with self.assertRaises(PermissionDenied):
            self.save_tag(upload, 'Denied', revision=1)
        self.assertEqual(classification_projection(upload)['custom_tag'], 'Retained')
