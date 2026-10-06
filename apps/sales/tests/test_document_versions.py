"""Immutable private revisions, guarded commands, failures and real lock races."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.rbac.models import RolePermission
from apps.rbac.route_guard import ModuleActionGuardMixin
from apps.sales.attachment_storage import attachment_storage
from apps.sales.models import (Deal, OpportunityAuditEvent, OpportunityDocument, OpportunityWorkspaceUpload,
                               OpportunityDocumentClassificationRun, ProposalReviewDocument)
from apps.sales.opportunity_workspace import WorkspaceAPIError
from apps.sales.private_attachments import upload_private_version
from apps.sales.tests.test_opportunity_workspace import CONFIG
from apps.sales.tests.test_private_attachments import PrivateFixtures
from apps.sales.tests.test_proposal_review import ReviewFixtures, pdf_bytes
from apps.sales.views import DealViewSet


class VersionFixtures(PrivateFixtures):
    def version(self, first, *, content=b'new revision', name='Scope revision.pdf', request_id=None, token=None, note='Updated scope', **extra):
        return self.api.post(self.file_url(first) + 'versions/upload/', {
            'file': SimpleUploadedFile(name, content, content_type='application/pdf'),
            'upload_request_id': str(request_id or uuid4()),
            'expected_token': first.data['head_token'] if token is None else token,
            'revision_note': note, **extra,
        }, format='multipart')


@override_settings(**CONFIG)
class DocumentVersionTests(VersionFixtures, TestCase):
    def test_explicit_revision_keeps_original_bytes_one_current_document_and_real_history(self):
        first = self.upload()
        second = self.version(first)
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(second.data['document_id'], first.data['document_id'])
        self.assertNotEqual(second.data['id'], first.data['id'])
        self.assertEqual((second.data['version'], second.data['document_name']), ('2', 'Scope.pdf'))
        listing = self.api.get(self.url + 'folders/proposal/files/', {'storage': 'radai'})
        self.assertEqual(listing.data['item_count'], 2)
        self.assertEqual([row['id'] for row in listing.data['files'][:2]], [second.data['id'], first.data['id']])
        state = self.api.get(self.url).data['radai_storage']
        self.assertEqual(next(row['item_count'] for row in state['folders'] if row['key'] == 'proposal'), 2)
        history = self.api.get(self.file_url(first) + 'versions/').data['versions']
        self.assertEqual([(row['id'], row['is_current']) for row in history], [('2', True), ('1', False)])
        self.assertEqual(history[0]['file_id'], second.data['id'])
        self.assertEqual(history[0]['revision_note'], 'Updated scope')
        self.assertEqual(history[0]['mime_type'], 'application/pdf')
        for result, content in ((first, b'synthetic attachment'), (second, b'new revision')):
            response = self.api.get(self.file_url(result) + 'download/')
            self.assertEqual(b''.join(response.streaming_content), content)
        detail = self.api.get(self.file_url(first)).data
        self.assertFalse(detail['is_current'])
        self.assertEqual(detail['head_file_id'], second.data['id'])
        self.assertEqual(detail['head_token'], second.data['head_token'])
        self.assertEqual(OpportunityDocumentClassificationRun.objects.count(), 2)

    def test_same_name_still_conflicts_until_explicit_revision_command(self):
        first = self.upload()
        self.assertEqual(self.upload(content=b'new').status_code, 409)
        self.assertEqual(self.version(first, name='Scope.pdf').status_code, 201)
        self.assertEqual(self.upload(content=b'another').status_code, 409)

    def test_retry_returns_same_revision_even_after_a_later_head_without_duplicate_audit(self):
        first, request_id = self.upload(), uuid4()
        second = self.version(first, request_id=request_id)
        third = self.version(second, content=b'third')
        self.assertEqual(third.status_code, 201, third.data)
        retry = self.version(first, request_id=request_id)
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertEqual(retry.data['id'], second.data['id'])
        self.assertFalse(retry.data['is_current'])
        self.assertEqual(retry.data['head_file_id'], third.data['id'])
        self.assertEqual(OpportunityWorkspaceUpload.objects.count(), 3)
        self.assertEqual(OpportunityAuditEvent.objects.filter(event_type='workspace_file_uploaded').count(), 3)

    def test_folder_upload_retries_preserve_initial_and_revision_identity_after_later_head(self):
        initial_request, revision_request = uuid4(), uuid4()
        first = self.upload(request_id=initial_request)
        second = self.upload(request_id=revision_request, content=b'folder revision')
        self.assertEqual(second.status_code, 201, second.data)
        third = self.version(second, content=b'later explicit revision')
        self.assertEqual(third.status_code, 201, third.data)
        for request_id, content, original in (
                (initial_request, b'synthetic attachment', first),
                (revision_request, b'folder revision', second)):
            replay = self.upload(request_id=request_id, content=content)
            self.assertEqual(replay.status_code, 200, replay.data)
            self.assertEqual(replay.data['id'], original.data['id'])
            self.assertFalse(replay.data['is_current'])
            self.assertEqual(replay.data['head_file_id'], third.data['id'])
        self.assertEqual(OpportunityWorkspaceUpload.objects.count(), 3)
        self.assertEqual(OpportunityAuditEvent.objects.filter(event_type='workspace_file_uploaded').count(), 3)

    def test_uncertain_folder_revision_retries_reserved_intent_without_duplicate_version(self):
        first, request_id = self.upload(), uuid4()
        storage, fingerprint = attachment_storage()
        with patch('apps.sales.private_attachments._storage', return_value=(storage, fingerprint)):
            with patch.object(storage, 'save', side_effect=OSError('Synthetic storage failure')):
                failed = self.upload(request_id=request_id, content=b'folder revision')
            self.assertEqual(failed.status_code, 424, failed.data)
            attempt = OpportunityWorkspaceUpload.objects.get(request_id=request_id)
            self.assertEqual(attempt.status, 'uncertain')
            self.assertEqual(str(attempt.previous_upload_id), first.data['id'][6:])
            replay = self.upload(request_id=request_id, content=b'folder revision')
        self.assertEqual(replay.status_code, 201, replay.data)
        self.assertEqual(replay.data['id'], 'radai-' + str(attempt.pk))
        self.assertEqual(replay.data['version'], '2')
        self.assertEqual(OpportunityWorkspaceUpload.objects.count(), 2)
        self.assertEqual(OpportunityAuditEvent.objects.filter(event_type='workspace_file_uploaded').count(), 2)

    def test_stale_request_does_not_store_a_new_object_or_revision(self):
        first = self.upload()
        self.version(first)
        stale = self.version(first, content=b'stale')
        self.assertEqual((stale.status_code, str(stale.data['code'])), (409, 'version_stale'))
        self.assertEqual(OpportunityWorkspaceUpload.objects.count(), 2)
        self.assertEqual(len(list(Path(self.private_root.name).rglob('original'))), 2)

    def test_retry_identity_rejects_content_note_token_group_or_initial_upload_reuse(self):
        first, request_id = self.upload(), uuid4()
        second = self.version(first, request_id=request_id)
        for kwargs in ({'content': b'changed'}, {'note': 'changed'}, {'token': second.data['head_token']}):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.version(first, request_id=request_id, **kwargs).status_code, 409)
        other = self.upload(name='Other.pdf')
        self.assertEqual(self.version(other, request_id=request_id).status_code, 409)
        self.assertEqual(self.upload(request_id, name='Scope revision.pdf', content=b'new revision').status_code, 409)
        original = OpportunityWorkspaceUpload.objects.get(pk=first.data['id'][6:])
        self.assertEqual(self.version(first, request_id=original.request_id, name=original.name,
                                      content=b'synthetic attachment').status_code, 409)

    def test_version_requires_current_read_create_update_and_replay_rechecks_revocation(self):
        first, request_id = self.upload(), uuid4()
        self.version(first, request_id=request_id)
        for action in ('create', 'update', 'read'):
            permission = RolePermission.objects.filter(permission__module__code='sales_opportunities', permission__action=action).first()
            old_pk = permission.pk
            permission.delete()
            self.assertEqual(self.version(first, request_id=request_id).status_code, 403)
            permission.pk = old_pk
            permission.save(force_insert=True)

    def test_history_and_exact_download_keep_record_folder_and_export_scope(self):
        first = self.upload()
        second = self.version(first)
        hidden = Deal.objects.create(deal_code='VERSION-HIDDEN', deal_name='Hidden', client=self.client_record, owner=self.other)
        for result in (first, second):
            url = self.file_url(result)
            for candidate in (url.replace('/proposal/', '/tender/'), url.replace(str(self.opportunity.pk), str(hidden.pk))):
                for suffix in ('', 'versions/', 'download/'):
                    self.assertEqual(self.api.get(candidate + suffix).status_code, 404)
            self.assertEqual(self.api.get(url + 'versions/', {'cursor': 'forged'}).status_code, 400)
        RolePermission.objects.filter(permission__module__code='sales_opportunities', permission__action='export').delete()
        self.assertEqual(self.api.get(self.file_url(first) + 'versions/').status_code, 200)
        self.assertEqual(self.api.get(self.file_url(first) + 'download/').status_code, 403)

    def test_validation_never_creates_a_revision(self):
        first = self.upload()
        for kwargs in ({'note': 'x' * 1001}, {'token': ''}, {'content': b''}, {'untrusted_field': 'x'}):
            self.assertEqual(self.version(first, **kwargs).status_code, 400)
        self.assertEqual(OpportunityWorkspaceUpload.objects.count(), 1)

    def test_nul_revision_note_is_rejected_before_intent_or_file_write(self):
        first = self.upload()
        started_before = OpportunityAuditEvent.objects.filter(event_type='workspace_upload_started').count()
        with patch('apps.sales.private_attachments._upload_prepared', side_effect=AssertionError('Invalid notes must not reserve storage')):
            result = self.version(first, note='Changed\x00scope')
        self.assertEqual(result.status_code, 400, result.data)
        self.assertIn('revision_note', result.data)
        self.assertEqual(OpportunityWorkspaceUpload.objects.count(), 1)
        self.assertIsNone(OpportunityDocument.objects.get().pending_upload_id)
        self.assertEqual(OpportunityAuditEvent.objects.filter(event_type='workspace_upload_started').count(), started_before)
        self.assertEqual(len(list(Path(self.private_root.name).rglob('original'))), 1)

    def test_uncertain_storage_keeps_old_head_and_exact_retry_recovers_existing_intent(self):
        first, request_id = self.upload(), uuid4()
        storage, fingerprint = attachment_storage()
        with patch('apps.sales.private_attachments.attachment_storage', return_value=(storage, fingerprint)):
            with patch.object(storage, 'save', side_effect=OSError('Synthetic storage fault')):
                failed = self.version(first, request_id=request_id)
            self.assertEqual(failed.status_code, 424)
            document = OpportunityDocument.objects.get()
            self.assertEqual(str(document.head_upload_id), first.data['id'][6:])
            self.assertIsNotNone(document.pending_upload_id)
            self.assertEqual(self.version(first).status_code, 409)
            retry = self.version(first, request_id=request_id)
        self.assertEqual(retry.status_code, 201, retry.data)
        document.refresh_from_db()
        self.assertIsNone(document.pending_upload_id)
        self.assertEqual(str(document.head_upload_id), retry.data['id'][6:])

    def test_final_audit_failure_rolls_back_head_and_classification_but_retains_recoverable_bytes(self):
        from apps.sales.workflow import _audit
        first, request_id = self.upload(), uuid4()
        def fail_final(opportunity, actor, event, **kwargs):
            if event == 'workspace_file_uploaded':
                raise RuntimeError('Synthetic final audit failure')
            return _audit(opportunity, actor, event, **kwargs)
        with patch('apps.sales.private_attachments._audit', side_effect=fail_final):
            self.assertEqual(self.version(first, request_id=request_id).status_code, 424)
        document = OpportunityDocument.objects.get()
        self.assertEqual(str(document.head_upload_id), first.data['id'][6:])
        self.assertEqual(OpportunityDocumentClassificationRun.objects.count(), 1)
        self.assertEqual(OpportunityAuditEvent.objects.filter(event_type='workspace_file_uploaded').count(), 1)
        storage, fingerprint = attachment_storage()
        with patch('apps.sales.private_attachments.attachment_storage', return_value=(storage, fingerprint)), \
                patch.object(storage, 'save', side_effect=AssertionError('Retained bytes must not be replaced')):
            self.assertEqual(self.version(first, request_id=request_id).status_code, 201)

    def test_legacy_reads_do_not_materialize_but_explicit_version_retains_original_identity(self):
        first = self.upload()
        upload = OpportunityWorkspaceUpload.objects.get()
        OpportunityDocumentClassificationRun.objects.all().delete()
        OpportunityWorkspaceUpload.objects.filter(pk=upload.pk).update(document=None)
        OpportunityDocument.objects.all().delete()
        for suffix in ('', 'versions/', 'download/'):
            response = self.api.get(self.file_url(first) + suffix)
            self.assertEqual(response.status_code, 200)
            if response.streaming:
                b''.join(response.streaming_content)
        self.assertFalse(OpportunityDocument.objects.exists())
        second = self.version(first)
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(second.data['document_id'], first.data['document_id'])

    def test_delete_head_promotes_previous_and_upload_reuses_document_as_new_version(self):
        first = self.upload()
        second = self.version(first, content=b'v2 content')
        self.assertEqual(second.status_code, 201, second.data)
        removed = self.api.delete(self.file_url(second))
        self.assertEqual(removed.status_code, 200, removed.data)
        self.assertEqual(removed.data['deleted_id'], second.data['id'])
        self.assertEqual(removed.data['current']['id'], first.data['id'])

        listing = self.api.get(self.url + 'folders/proposal/files/', {'storage': 'radai'})
        self.assertEqual([row['id'] for row in listing.data['files']], [first.data['id']])

        reupload = self.upload(content=b'v3 from upload', request_id=uuid4(), name='Scope.pdf')
        self.assertEqual(reupload.status_code, 201, reupload.data)
        self.assertEqual(reupload.data['version'], '3')
        self.assertEqual(reupload.data['document_id'], first.data['document_id'])

        history = self.api.get(self.file_url(first) + 'versions/').data['versions']
        self.assertEqual([row['id'] for row in history], ['3', '1'])


@override_settings(**CONFIG)
class ProposalVersionEvidenceTests(ReviewFixtures, TestCase):
    def test_new_file_revision_never_rebinds_submitted_review_evidence_or_comments(self):
        bound = self.bind()
        self.assertEqual(bound.status_code, 201, bound.data)
        document = ProposalReviewDocument.objects.get(pk=bound.data['document_id'])
        comment = self.post(document.pk, 'comments', body='Retain the reviewed source scope.')
        self.assertEqual(comment.status_code, 201, comment.data)
        submitted = self.post(document.pk, 'submit', outcome='request_changes', note='Clarify scope before issue.')
        self.assertEqual(submitted.status_code, 201, submitted.data)
        document.refresh_from_db()
        original = document.attachment
        before = (document.attachment_id, document.sha256, document.size, document.page_count)
        feedback_before = (document.feedback_version, list(document.commands.values('id', 'outcome', 'note')))
        detail = self.api.get(self.url + f'folders/proposal/files/radai-{original.pk}/').data
        response = self.api.post(self.url + f'folders/proposal/files/radai-{original.pk}/versions/upload/', {
            'file': SimpleUploadedFile('Proposal revised.pdf', pdf_bytes(3), content_type='application/pdf'),
            'upload_request_id': str(uuid4()), 'expected_token': detail['head_token'], 'revision_note': 'New technical draft',
        }, format='multipart')
        self.assertEqual(response.status_code, 201, response.data)
        document.refresh_from_db()
        self.assertEqual((document.attachment_id, document.sha256, document.size, document.page_count), before)
        self.assertEqual((document.feedback_version, list(document.commands.values('id', 'outcome', 'note'))), feedback_before)
        self.assertTrue(document.comments.filter(pk=comment.data['comment_id'], body='Retain the reviewed source scope.').exists())
        for suffix in ('content', 'download'):
            saved = self.api.get(self.review_url + f'documents/{document.pk}/{suffix}/')
            self.assertEqual(b''.join(saved.streaming_content), self.pdf)


@override_settings(**CONFIG)
class DocumentVersionDurabilityTests(VersionFixtures, TransactionTestCase):
    def _fixture_teardown(self):
        if connection.vendor != 'postgresql':
            return super()._fixture_teardown()
        # The full-domain harness truncates hundreds of tables. Slow disposable
        # disk/checkpoints can exceed the 20-second application-query budget.
        # Extend only cleanup; retain the contention test's query/lock timeouts.
        with connection.cursor() as cursor:
            cursor.execute('SHOW statement_timeout')
            original = cursor.fetchone()[0]
            cursor.execute("SELECT set_config('statement_timeout', '120s', false)")
        try:
            return super()._fixture_teardown()
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('statement_timeout', %s, false)", [original])

    @skipUnless(connection.vendor == 'postgresql', 'PostgreSQL nullable-join row-lock verification')
    def test_folder_upload_can_create_and_reupload_same_name_with_document_join(self):
        self.upload_url = self.url + 'folders/tender/upload/'
        initial_request, revision_request = uuid4(), uuid4()
        first = self.upload(name='Tender Scope.pdf', content=b'first tender scope', request_id=initial_request)
        self.assertEqual(first.status_code, 201, first.data)

        second = self.upload(name='Tender Scope.pdf', content=b'revised tender scope', request_id=revision_request)
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(second.data['document_id'], first.data['document_id'])
        self.assertEqual(second.data['version'], '2')
        for request_id, content, original in (
                (initial_request, b'first tender scope', first),
                (revision_request, b'revised tender scope', second)):
            replay = self.upload(name='Tender Scope.pdf', content=content, request_id=request_id)
            self.assertEqual(replay.status_code, 200, replay.data)
            self.assertEqual(replay.data['id'], original.data['id'])
            download = self.api.get(self.url + 'folders/tender/files/' + replay.data['id'] + '/download/')
            self.assertEqual(b''.join(download.streaming_content), content)
        self.assertEqual(OpportunityWorkspaceUpload.objects.count(), 2)
        self.assertEqual(OpportunityAuditEvent.objects.filter(event_type='workspace_file_uploaded').count(), 2)

    def test_guarded_version_endpoint_denies_missing_create_without_side_effects(self):
        first = self.upload()
        RolePermission.objects.filter(permission__module__code='sales_opportunities', permission__action='create').delete()
        guarded = type('DealViewSet', (ModuleActionGuardMixin, DealViewSet), {'__module__': 'apps.sales.views'})
        request = APIRequestFactory().post(self.file_url(first) + 'versions/upload/', {
            'file': SimpleUploadedFile('Scope.pdf', b'revision two'), 'upload_request_id': str(uuid4()),
            'expected_token': first.data['head_token'],
        }, format='multipart')
        force_authenticate(request, self.actor)
        response = guarded.as_view({'post': 'workspace_upload_version'})(
            request, pk=self.opportunity.pk, folder_key='proposal', file_id=first.data['id'])
        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(OpportunityWorkspaceUpload.objects.count(), 1)
        self.assertIsNone(OpportunityDocument.objects.get().pending_upload_id)

    def test_guarded_version_intent_commits_before_storage_io(self):
        first = self.upload()
        storage, fingerprint = attachment_storage()
        save = storage.save
        def assert_committed(name, content, **kwargs):
            self.assertFalse(connection.in_atomic_block)
            document = OpportunityDocument.objects.get()
            self.assertEqual(document.pending_upload.status, 'uploading')
            self.assertEqual(document.head_upload.version_number, 1)
            return save(name, content, **kwargs)
        guarded = type('DealViewSet', (ModuleActionGuardMixin, DealViewSet), {'__module__': 'apps.sales.views'})
        callback = guarded.as_view({'post': 'workspace_upload_version'})
        request = APIRequestFactory().post(self.file_url(first) + 'versions/upload/', {
            'file': SimpleUploadedFile('Scope.pdf', b'revision two'), 'upload_request_id': str(uuid4()),
            'expected_token': first.data['head_token'],
        }, format='multipart')
        force_authenticate(request, self.actor)
        with patch('apps.sales.private_attachments.attachment_storage', return_value=(storage, fingerprint)), \
                patch.object(storage, 'save', side_effect=assert_committed):
            response = callback(request, pk=self.opportunity.pk, folder_key='proposal', file_id=first.data['id'])
        self.assertEqual(response.status_code, 201, response.data)

    @skipUnless(connection.vendor == 'postgresql', 'PostgreSQL row-lock verification')
    def test_competing_version_writers_reserve_only_one_new_revision(self):
        first, barrier = self.upload(), Barrier(2)
        def run(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    upload_private_version(self.opportunity, self.actor, 'proposal', first.data['id'],
                        SimpleUploadedFile('Scope.pdf', str(index).encode()), uuid4(), first.data['head_token'])
                    return 201
                except WorkspaceAPIError as exc:
                    return exc.status_code
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run, index) for index in range(2)]
            self.assertEqual(sorted(future.result(timeout=30) for future in futures), [201, 409])
        self.assertEqual(OpportunityWorkspaceUpload.objects.count(), 2)
        self.assertEqual(OpportunityDocument.objects.get().head_upload.version_number, 2)
        self.assertEqual(OpportunityAuditEvent.objects.filter(event_type='workspace_file_uploaded').count(), 2)
        self.assertEqual(len(list(Path(self.private_root.name).rglob('original'))), 2)
