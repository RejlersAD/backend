"""Real private PDFs, synthetic identities, isolated review/approval boundaries."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import hashlib
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

import fitz
from django.db import close_old_connections, connection
from django.db.models import Q
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.rbac.action_policy import operation_action
from apps.rbac.models import RolePermission
from apps.rbac.route_guard import ModuleActionGuardMixin
from apps.sales.models import (Quote, OpportunityAuditEvent, OpportunityWorkspaceUpload,
                               ProposalReviewDocument, ProposalReviewComment, ProposalReviewCommand)
from apps.sales.proposal_review import ReviewError, _pdf_pages, review_command, review_content, review_projection
from apps.sales.tests.access_fixtures import grant_sales_actions
from apps.sales.tests.test_opportunity_workspace import CONFIG
from apps.sales.tests.test_private_attachments import PrivateFixtures
from apps.sales.views import QuoteViewSet


def pdf_bytes(pages=2):
    with fitz.open() as pdf:
        for index in range(pages):
            pdf.new_page().insert_text((72, 72), f'Synthetic proposal page {index + 1}')
        return pdf.tobytes()


def large_pdf_bytes():
    with fitz.open(stream=pdf_bytes(), filetype='pdf') as pdf:
        content = pdf[0].get_contents()[0]
        # Valid PDF comments retain original document bytes while making the
        # fixture larger than the previous independent review/upload ceiling.
        padding = b'\n% synthetic retained source evidence\n' * 310000
        pdf.update_stream(content, pdf.xref_stream(content) + padding, compress=False)
        return pdf.tobytes(deflate=False)


class ProposalPdfDiskValidationTests(SimpleTestCase):
    def test_large_pdf_uses_bounded_reads_and_preserves_caller_stream(self):
        class BoundedRead(BytesIO):
            def read(self, size=-1):
                if not 0 <= size <= 64 * 1024:
                    raise AssertionError('PDF validation attempted an unbounded input read.')
                return super().read(size)

        data = large_pdf_bytes()
        self.assertGreater(len(data), 10 * 1024 * 1024)
        with TemporaryDirectory(prefix='radai-pdf-validation-test-') as scratch:
            with BoundedRead(data) as content:
                content.seek(17)
                with patch('apps.sales.proposal_review.TemporaryDirectory',
                           side_effect=lambda **kwargs: TemporaryDirectory(dir=scratch, **kwargs)):
                    self.assertEqual(_pdf_pages(content), 2)
                self.assertEqual(content.tell(), 17)
                self.assertFalse(content.closed)
            self.assertEqual(list(Path(scratch).iterdir()), [])

    def test_invalid_and_repaired_pdf_restore_position_and_remove_temporary_files(self):
        valid = pdf_bytes()
        repaired = valid[:valid.index(b'xref\n')]
        with fitz.open(stream=repaired, filetype='pdf') as pdf:
            self.assertTrue(pdf.is_repaired)
        with TemporaryDirectory(prefix='radai-pdf-validation-test-') as scratch:
            for data in (b'not a PDF', b'%PDF-1.7\ninvalid', repaired):
                with self.subTest(data=data[:20]), BytesIO(data) as content:
                    content.seek(2)
                    with patch('apps.sales.proposal_review.TemporaryDirectory',
                               side_effect=lambda **kwargs: TemporaryDirectory(dir=scratch, **kwargs)):
                        with self.assertRaises(ValidationError):
                            _pdf_pages(content)
                    self.assertEqual(content.tell(), 2)
                    self.assertFalse(content.closed)
                    self.assertEqual(list(Path(scratch).iterdir()), [])

    def test_temporary_disk_failure_is_recoverable_and_preserves_caller_stream(self):
        with TemporaryDirectory(prefix='radai-pdf-validation-test-') as scratch:
            with BytesIO(pdf_bytes()) as content:
                content.seek(3)
                with patch('apps.sales.proposal_review.TemporaryDirectory',
                           side_effect=lambda **kwargs: TemporaryDirectory(dir=scratch, **kwargs)), \
                        patch('apps.sales.proposal_review.Path.open', side_effect=OSError('synthetic disk failure')):
                    with self.assertRaises(ReviewError) as error:
                        _pdf_pages(content)
                self.assertEqual(error.exception.status_code, 503)
                self.assertEqual(str(error.exception.detail['code']), 'pdf_validation_unavailable')
                self.assertEqual(content.tell(), 3)
                self.assertFalse(content.closed)
            self.assertEqual(list(Path(scratch).iterdir()), [])


class ReviewFixtures(PrivateFixtures):
    def setUp(self):
        super().setUp()
        grant_sales_actions(self.actor, 'sales_proposals')
        self.quote = Quote.objects.create(quote_number='REVIEW-SYNTHETIC-1', deal=self.opportunity,
            client=self.client_record, subtotal='100.00', total_amount='100.00', currency='AED',
            valid_until=date.today() + timedelta(days=30), prepared_by=self.actor)
        self.review_url = f'/api/v1/sales/quotes/{self.quote.pk}/review/'
        self.pdf = pdf_bytes()

    def bind(self, name='Proposal.pdf', content=None, **overrides):
        uploaded = self.upload(name=name, content=self.pdf if content is None else content)
        self.assertEqual(uploaded.status_code, 201, uploaded.data)
        state = self.api.get(self.review_url).data
        data = {'file_id': uploaded.data['id'], 'request_id': str(uuid4()),
                'expected_document_id': state['selected_document']['id'] if state['selected_document'] else None,
                'expected_quote_updated_at': state['quote']['updated_at'], **overrides}
        return self.api.post(self.review_url + 'documents/', data, format='json')

    def document(self):
        response = self.bind()
        self.assertEqual(response.status_code, 201, response.data)
        return response.data['document_id']

    def post(self, document_id, suffix, **data):
        state = self.api.get(self.review_url, {'document_id': document_id}).data
        return self.api.post(self.review_url + f'documents/{document_id}/{suffix}/', {
            'request_id': str(uuid4()), 'expected_version': state['selected_document']['feedback_version'], **data,
        }, format='json')


@override_settings(**CONFIG)
class ProposalReviewTests(ReviewFixtures, TestCase):
    def test_large_compressed_pdf_binds_and_returns_exact_original_review_bytes(self):
        original = large_pdf_bytes()
        self.assertGreater(len(original), 10 * 1024 * 1024)
        response = self.bind(name='Large proposal.pdf', content=original)
        self.assertEqual(response.status_code, 201, response.data)
        attempt = OpportunityWorkspaceUpload.objects.get()
        document = ProposalReviewDocument.objects.get(pk=response.data['document_id'])
        self.assertEqual(attempt.storage_encoding, 'gzip')
        self.assertLess(attempt.stored_size, attempt.size)
        self.assertEqual(document.size, len(original))
        self.assertEqual(document.sha256, hashlib.sha256(original).hexdigest())
        self.assertEqual(document.page_count, 2)
        for suffix in ('content', 'download'):
            result = self.api.get(self.review_url + f'documents/{document.pk}/{suffix}/')
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result['Content-Type'], 'application/pdf')
            self.assertEqual(b''.join(result.streaming_content), original)
        self.quote.refresh_from_db()
        self.assertEqual(self.quote.status, 'draft')
        self.assertIsNone(self.quote.approved_at)

    def test_empty_projection_does_not_create_or_trust_legacy_url(self):
        self.quote.pdf_file_path = 'https://untrusted.test/fake.pdf'
        self.quote.save()
        state = self.api.get(self.review_url)
        self.assertEqual(state.status_code, 200)
        self.assertIsNone(state.data['selected_document'])
        self.assertEqual(state.data['counts'], {'all': 0, 'open': 0, 'resolved': 0})
        self.assertTrue(state.data['capabilities']['can_bind'])
        self.assertFalse(state.data['capabilities']['can_preview'])
        self.assertNotIn('untrusted', str(state.data))
        self.assertFalse(ProposalReviewDocument.objects.exists())
        self.assertFalse(ProposalReviewCommand.objects.exists())

    def test_bind_real_pdf_metadata_and_protected_content_without_graph(self):
        document_id = self.document()
        state = self.api.get(self.review_url).data
        document = state['selected_document']
        self.assertEqual(document['page_count'], 2)
        self.assertEqual(document['revision'], 1)
        self.assertTrue(document['is_current'])
        self.assertEqual(document['created_by']['id'], str(self.actor.pk))
        for suffix in ('content', 'download'):
            response = self.api.get(self.review_url + f'documents/{document_id}/{suffix}/')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(b''.join(response.streaming_content), self.pdf)
            self.assertEqual(response['Content-Type'], 'application/pdf')
            self.assertIn('attachment', response['Content-Disposition'])
            self.assertEqual(response['Cache-Control'], 'no-store, private')
            self.assertIn('sandbox', response['Content-Security-Policy'])
        self.assertEqual(OpportunityWorkspaceUpload.objects.count(), 1)
        self.quote.refresh_from_db()
        self.assertEqual(self.quote.status, 'draft')

    def test_non_pdf_encrypted_and_oversized_page_counts_cannot_bind(self):
        for index, content in enumerate((b'not a PDF', pdf_bytes(501))):
            result = self.bind(name=f'Invalid-{index}.pdf', content=content)
            self.assertEqual(result.status_code, 400, result.data)
        with fitz.open(stream=self.pdf, filetype='pdf') as pdf:
            encrypted = pdf.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw='synthetic', user_pw='synthetic')
        self.assertEqual(self.bind(name='Encrypted.pdf', content=encrypted).status_code, 400)
        self.assertFalse(ProposalReviewDocument.objects.exists())

    def test_binding_rejects_foreign_folder_record_source_and_arbitrary_url(self):
        uploaded = self.upload(name='Source.pdf', content=self.pdf)
        state = self.api.get(self.review_url).data
        attempt = OpportunityWorkspaceUpload.objects.get()
        data = {'file_id': uploaded.data['id'], 'request_id': str(uuid4()),
                'expected_document_id': None, 'expected_quote_updated_at': state['quote']['updated_at']}
        for field, value in [('folder_key', 'tender'), ('provider', 'sharepoint')]:
            old = getattr(attempt, field)
            setattr(attempt, field, value)
            attempt.save(update_fields=[field])
            self.assertEqual(self.api.post(self.review_url + 'documents/', data, format='json').status_code, 404)
            setattr(attempt, field, old)
            attempt.save(update_fields=[field])
        self.assertEqual(self.api.post(self.review_url + 'documents/', {**data, 'file_id': 'https://untrusted.test/a.pdf'}, format='json').status_code, 404)
        other = Quote.objects.create(quote_number='OTHER', deal=self.opportunity, client=self.client_record,
                                    subtotal=1, total_amount=1, valid_until=date.today())
        document_id = self.document()
        response = self.api.get(f'/api/v1/sales/quotes/{other.pk}/review/', {'document_id': document_id})
        self.assertEqual(response.status_code, 404)

    def test_comments_anchors_replies_required_change_resolve_and_counts(self):
        document_id = self.document()
        anchor = {'rects': [{'x': .1, 'y': .2, 'width': .3, 'height': .04}], 'quote': 'Synthetic proposal'}
        comment = self.post(document_id, 'comments', body='Clarify the scope.', kind='required_change',
                            page_number=2, anchor=anchor, context='Scope')
        self.assertEqual(comment.status_code, 201, comment.data)
        parent_id = comment.data['comment_id']
        reply = self.post(document_id, 'comments', body='Added the scope clarification.', parent_id=parent_id)
        self.assertEqual(reply.status_code, 201)
        self.assertEqual(self.post(document_id, 'comments', body='Nested', parent_id=reply.data['comment_id']).status_code, 400)
        resolved = self.post(document_id, f'comments/{parent_id}/resolve', is_resolved=True)
        self.assertEqual(resolved.status_code, 200)
        state = self.api.get(self.review_url).data
        self.assertEqual(state['counts'], {'all': 1, 'open': 0, 'resolved': 1})
        self.assertEqual([row['id'] for row in state['comments']], [parent_id, reply.data['comment_id']])
        self.assertTrue(state['comments'][0]['is_resolved'])
        self.assertEqual(state['comments'][1]['anchor'], anchor)
        self.assertEqual(state['comments'][1]['kind'], 'required_change')
        self.assertEqual(self.post(document_id, f'comments/{parent_id}/resolve', is_resolved=False).status_code, 200)

    def test_invalid_anchors_pages_text_and_unknown_fields_rejected(self):
        document_id = self.document()
        invalid = [{'page_number': 3}, {'page_number': True}, {'body': ' '}, {'kind': 'approved'},
                   {'body': 'x' * 4001}, {'approved_by': str(self.actor.pk)},
                   {'page_number': 1, 'anchor': {'rects': [{'x': .9, 'y': 0, 'width': .5, 'height': .1}], 'quote': 'x'}},
                   {'anchor': {'rects': [], 'quote': 'x'}}]
        for extra in invalid:
            self.assertEqual(self.post(document_id, 'comments', **{'body': 'Comment', **extra}).status_code, 400, extra)
        self.assertFalse(ProposalReviewComment.objects.exists())

    def test_command_replay_payload_actor_scope_and_stale_version(self):
        document_id = self.document()
        data = {'body': 'One comment.', 'request_id': str(uuid4()), 'expected_version': 1}
        url = self.review_url + f'documents/{document_id}/comments/'
        first = self.api.post(url, data, format='json')
        second = self.api.post(url, data, format='json')
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.data['replayed'])
        self.assertEqual(first.data['comment_id'], second.data['comment_id'])
        self.assertEqual(self.api.post(url, {**data, 'body': 'Different'}, format='json').status_code, 409)
        self.assertEqual(self.api.post(url, {**data, 'request_id': str(uuid4())}, format='json').status_code, 409)
        grant_sales_actions(self.other, 'sales', 'sales_opportunities', 'sales_proposals')
        self.api.force_authenticate(self.other)
        self.assertEqual(self.api.post(url, data, format='json').status_code, 409)
        self.assertEqual(ProposalReviewComment.objects.count(), 1)

    def test_review_submissions_remain_internal_and_historical_threads_read_only(self):
        first_id = self.document()
        self.post(first_id, 'comments', body='First document note.')
        result = self.post(first_id, 'submit', outcome='request_changes', note='Please address scope feedback.')
        self.assertEqual(result.status_code, 201)
        self.quote.refresh_from_db()
        self.assertEqual(self.quote.status, 'draft')
        self.assertIsNone(self.quote.approved_at)
        self.assertEqual(self.quote.approval_history, [])
        new = self.bind(name='Revised.pdf')
        self.assertEqual(new.status_code, 201)
        history = self.api.get(self.review_url, {'document_id': first_id}).data
        self.assertEqual(history['submissions'][0]['outcome'], 'request_changes')
        self.assertFalse(history['capabilities']['can_comment'])
        self.assertFalse(history['capabilities']['can_resolve'])
        self.assertFalse(history['capabilities']['can_submit'])
        self.assertIn('Historical', history['capabilities']['deny_reason'])
        self.assertEqual(self.post(first_id, 'comments', body='Late').status_code, 409)
        self.assertEqual(self.api.get(self.review_url).data['comments'], [])

    def test_approved_or_submitted_binding_rejected_and_evidence_protected(self):
        document_id = self.document()
        for state in ('ready_to_submit', 'submitted', 'accepted'):
            self.quote.status = state
            self.quote.save()
            self.assertEqual(self.bind(name=state + '.pdf').status_code, 409)
        self.quote.status = 'draft'
        self.quote.approved_at = timezone.now()
        self.quote.save()
        self.assertEqual(self.bind(name='Approved.pdf').status_code, 409)
        self.assertEqual(self.api.delete(f'/api/v1/sales/quotes/{self.quote.pk}/').status_code, 409)
        self.assertEqual(self.api.patch(f'/api/v1/sales/quotes/{self.quote.pk}/', {'version': 2}, format='json').status_code, 409)
        self.assertTrue(ProposalReviewDocument.objects.filter(pk=document_id).exists())

    def test_late_approval_during_pdf_read_cannot_bind(self):
        from apps.sales.proposal_review import _pdf_pages
        def parse_and_approve(content):
            count = _pdf_pages(content)
            Quote.objects.filter(pk=self.quote.pk).update(approved_at=timezone.now())
            return count
        with patch('apps.sales.proposal_review._pdf_pages', side_effect=parse_and_approve):
            response = self.bind()
        self.assertEqual(response.status_code, 409)
        self.assertFalse(ProposalReviewDocument.objects.exists())

    def test_audit_failure_rolls_back_document_comment_and_command(self):
        with patch('apps.sales.proposal_review._audit', side_effect=RuntimeError('synthetic audit failure')):
            with self.assertRaises(RuntimeError):
                self.bind()
        self.assertFalse(ProposalReviewDocument.objects.exists())
        self.assertFalse(ProposalReviewCommand.objects.exists())
        self.assertEqual(OpportunityWorkspaceUpload.objects.count(), 1)
        document_id = self.bind(name='Retry.pdf').data['document_id']
        with patch('apps.sales.proposal_review._audit', side_effect=RuntimeError('synthetic audit failure')):
            with self.assertRaises(RuntimeError):
                self.post(document_id, 'comments', body='Unsaved feedback')
        self.assertFalse(ProposalReviewComment.objects.exists())
        self.assertEqual(ProposalReviewDocument.objects.get(pk=document_id).feedback_version, 1)

    def test_read_create_update_export_denials_and_foreign_deal_visibility(self):
        document_id = self.document()
        RolePermission.objects.filter(permission__module__code='sales_opportunities', permission__action='export').delete()
        state = self.api.get(self.review_url)
        self.assertEqual(state.status_code, 200)
        self.assertFalse(state.data['capabilities']['can_preview'])
        self.assertEqual(self.api.get(self.review_url + f'documents/{document_id}/content/').status_code, 403)
        RolePermission.objects.filter(permission__module__code='sales_proposals', permission__action='create').delete()
        self.assertEqual(self.post(document_id, 'comments', body='Denied').status_code, 403)
        RolePermission.objects.filter(permission__module__code='sales_proposals', permission__action='update').delete()
        self.assertEqual(self.post(document_id, 'submit', outcome='reviewed').status_code, 403)
        with patch('apps.sales.proposal_review.build_visibility_filter', return_value=Q(pk=uuid4())):
            self.assertEqual(self.api.get(self.review_url).status_code, 404)
        RolePermission.objects.filter(permission__module__code='sales_proposals', permission__action='read').delete()
        self.assertEqual(self.api.get(self.review_url).status_code, 403)

    def test_download_closes_buffer_after_late_proposal_permission_loss(self):
        from apps.sales.proposal_review import download_private_file
        document_id = self.document()
        buffers = []
        def read_and_revoke(*args):
            result = download_private_file(*args)
            buffers.append(result[0])
            RolePermission.objects.filter(permission__module__code='sales_proposals', permission__action='read').delete()
            return result
        with patch('apps.sales.proposal_review.download_private_file', side_effect=read_and_revoke):
            with self.assertRaises(PermissionDenied):
                review_content(self.quote.pk, self.actor, document_id)
        self.assertTrue(buffers[0].closed)

    def test_comment_pagination_is_chronological_bound_and_invalidated_by_changes(self):
        document_id = self.document()
        document = ProposalReviewDocument.objects.get(pk=document_id)
        ProposalReviewComment.objects.bulk_create([ProposalReviewComment(document=document, body=str(i), author=self.actor) for i in range(101)])
        first = self.api.get(self.review_url).data
        self.assertEqual(len(first['comments']), 100)
        self.assertEqual(first['counts']['all'], 101)
        cursor = first['next_comments_cursor']
        second = self.api.get(self.review_url, {'comments_cursor': cursor}).data
        self.assertEqual(len(second['comments']), 1)
        self.assertLessEqual(first['comments'][-1]['created_at'], second['comments'][0]['created_at'])
        self.post(document_id, 'comments', body='New')
        self.assertEqual(self.api.get(self.review_url, {'comments_cursor': cursor}).status_code, 409)
        self.assertEqual(self.api.get(self.review_url, {'comments_cursor': 'forged'}).status_code, 409)

    def test_production_action_guard_does_not_require_unseeded_proposal_export(self):
        document_id = self.document()
        guarded = type('QuoteViewSet', (ModuleActionGuardMixin, QuoteViewSet), {'__module__': QuoteViewSet.__module__})
        request = APIRequestFactory().get(self.review_url)
        force_authenticate(request, user=self.actor)
        self.assertEqual(guarded.as_view({'get': 'review'})(request, pk=str(self.quote.pk)).status_code, 200)
        for name, action in [('review_content', 'read'), ('review_download', 'read'), ('review_bind', 'update'),
                             ('review_comment', 'create'), ('review_resolve', 'update'), ('review_submit', 'update')]:
            view = QuoteViewSet()
            view.action = name
            self.assertEqual(operation_action(request, view), action)
        RolePermission.objects.filter(permission__module__code='sales_opportunities', permission__action='export').delete()
        request = APIRequestFactory().get(self.review_url)
        force_authenticate(request, user=self.actor)
        self.assertEqual(guarded.as_view({'get': 'review_content'})(request, pk=str(self.quote.pk), document_id=document_id).status_code, 403)

    def test_binding_retry_and_stale_current_document_or_quote_are_rejected(self):
        uploaded = self.upload(name='Replay.pdf', content=self.pdf)
        state = self.api.get(self.review_url).data
        data = {'file_id': uploaded.data['id'], 'request_id': str(uuid4()),
                'expected_document_id': None, 'expected_quote_updated_at': state['quote']['updated_at']}
        url = self.review_url + 'documents/'
        first = self.api.post(url, data, format='json')
        replay = self.api.post(url, data, format='json')
        self.assertEqual(first.status_code, 201)
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.data['replayed'])
        self.assertEqual(ProposalReviewDocument.objects.count(), 1)
        self.assertEqual(self.api.post(url, {**data, 'request_id': str(uuid4())}, format='json').status_code, 409)
        self.assertEqual(self.bind(name='Stale.pdf', expected_quote_updated_at='2000-01-01T00:00:00+00:00').status_code, 409)

    def test_source_integrity_or_identity_change_blocks_bound_content_and_feedback(self):
        document_id = self.document()
        attempt = OpportunityWorkspaceUpload.objects.get()
        attempt.sha256 = '0' * 64
        attempt.save(update_fields=['sha256'])
        response = self.api.get(self.review_url + f'documents/{document_id}/content/')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.post(document_id, 'comments', body='Unverified source').status_code, 409)
        self.assertFalse(ProposalReviewComment.objects.exists())

    def test_read_detects_feedback_mutation_during_projection(self):
        from apps.sales.proposal_review import _comment_data
        document_id = self.document()
        self.post(document_id, 'comments', body='Original')
        def change_while_projecting(comment):
            value = _comment_data(comment)
            ProposalReviewDocument.objects.filter(pk=document_id).update(feedback_version=99)
            return value
        with patch('apps.sales.proposal_review._comment_data', side_effect=change_while_projecting):
            with self.assertRaises(ReviewError) as error:
                review_projection(self.quote.pk, self.actor)
        self.assertEqual(str(error.exception.detail['code']), 'stale_review')

    def test_selected_document_outside_recent_window_is_included_without_fake_history(self):
        document_id = self.document()
        first = ProposalReviewDocument.objects.get(pk=document_id)
        ProposalReviewDocument.objects.bulk_create([ProposalReviewDocument(quote=self.quote,
            attachment=first.attachment, revision=i, name=first.name, sha256=first.sha256,
            size=first.size, page_count=first.page_count, created_by=self.actor) for i in range(2, 103)])
        state = self.api.get(self.review_url, {'document_id': document_id}).data
        self.assertEqual(state['documents_count'], 102)
        self.assertEqual(len(state['documents']), 101)
        self.assertEqual(state['selected_document']['id'], document_id)
        self.assertIn(document_id, [row['id'] for row in state['documents']])


@override_settings(**CONFIG)
class ProposalReviewConcurrencyTests(ReviewFixtures, TransactionTestCase):
    @skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL row locks.')
    def test_competing_feedback_versions_commit_one_comment_and_audit(self):
        document_id = self.document()
        barrier = Barrier(2)
        def create_note(index):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                try:
                    review_command(str(self.quote.pk), self.actor, 'comment', {
                        'request_id': str(uuid4()), 'expected_version': 1, 'body': f'Note {index}'}, document_id)
                    return 'created'
                except ReviewError as exc:
                    return str(exc.detail['code'])
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create_note, range(2)))
        self.assertCountEqual(results, ['created', 'stale_review'])
        self.assertEqual(ProposalReviewComment.objects.count(), 1)
        self.assertEqual(OpportunityAuditEvent.objects.filter(event_type='proposal_review_comment').count(), 1)
