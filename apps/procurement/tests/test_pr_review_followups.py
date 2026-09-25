"""Saved source reviews and explicit project references retain their boundaries."""

from copy import deepcopy
from datetime import timedelta
import json
import hashlib
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.procurement.models import PurchaseRequisition
from apps.procurement.serializers import PurchaseRequisitionSerializer
from apps.procurement.services.pr_project_references import normalize_project_references
from apps.procurement.services.pr_review_display import default_level_zero_approver
from apps.procurement.services.pr_source_approval_review import (
    SourceApprovalReviewError, normalize_source_approval_review, prepare_source_approval_review,
)
from apps.procurement.services.requisition_concurrency import StaleRequisition
from apps.procurement.services.signed_pr_pdf_import import SignedPRImportError, preview_signed_pr_pdf
from apps.rbac.models import Organization, UserProfile

from . import test_pr_source_approval_review as annotation_fixtures
from . import test_requisition_source_approval_edits as source_fixtures
from . import test_paired_signed_import as pair_fixtures


class ImportReviewFollowupTests(TestCase):
    _patch = annotation_fixtures.PRSourceApprovalReviewTests._patch
    _grant_api_access = annotation_fixtures.PRSourceApprovalReviewTests._grant_api_access
    _excel_record = annotation_fixtures.PRSourceApprovalReviewTests._excel_record
    _use_three_signed_source_rows = annotation_fixtures.PRSourceApprovalReviewTests._use_three_signed_source_rows
    setUp = annotation_fixtures.PRSourceApprovalReviewTests.setUp
    import_document = annotation_fixtures.PRSourceApprovalReviewTests.import_document
    save_review = annotation_fixtures.PRSourceApprovalReviewTests.save_review

    def test_reviewed_csv_roundtrip_retains_all_refs_and_original_ocr(self):
        review = {**self.reviewed, 'project_number': ' 5901001, 5901002,5901001 '}
        result = self.import_document(manual_overrides=review)
        pr = PurchaseRequisition.objects.get(pk=result['pr_id'])
        self.assertEqual(pr.project, '5901001, 5901002')
        self.assertEqual(result['project_numbers'], ['5901001', '5901002'])
        self.assertEqual([row['project_number'] for row in pr.project_details], result['project_numbers'])
        verification = pr.price_remarks_data['signed_document_verification']
        self.assertEqual(verification['source_fields']['project_numbers'], ['5900985'])
        self.assertEqual(verification['approved_fields']['project_numbers'], result['project_numbers'])
        serialized = PurchaseRequisitionSerializer(pr).data
        self.assertEqual(serialized['project_numbers'], result['project_numbers'])

    def test_csv_validates_without_truncating_or_storing_files(self):
        for value in ('x' * 201, '5901001\n5901002', ['5901001'], None):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.import_document(manual_overrides={**self.reviewed, 'project_number': value})
        self.assertFalse(PurchaseRequisition.objects.exists())
        self.storage.save.assert_not_called()
        self.assertEqual(normalize_project_references('PRJ-a, prj-A, PRJ-b'), ['PRJ-a', 'PRJ-b'])

    def test_deliberate_attachment_project_edit_roundtrips_with_audit_and_retry(self):
        pr = self._excel_record()
        before = {key: deepcopy(getattr(pr, key)) for key in ('total_price', 'items', 'currency', 'product_service')}
        command = {'project_number': '5901001, 5901002', 'expected_updated_at': pr.updated_at.isoformat()}
        result = self.import_document(existing=pr, reviewed_project_references=command)
        pr.refresh_from_db()
        self.assertEqual({key: getattr(pr, key) for key in before}, before)
        self.assertEqual(result['project_numbers'], ['5901001', '5901002'])
        history = deepcopy(pr.price_remarks_data['project_reference_reviews'])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['reviewed_by_id'], str(self.reviewer.pk))
        self.assertEqual(history[0]['document_sha256'], hashlib.sha256(self.source).hexdigest())
        preview = preview_signed_pr_pdf(self.source, filename='signed.pdf')
        self.assertEqual(preview['project_numbers'], result['project_numbers'])
        self.assertEqual(preview['requisition_updated_at'], pr.updated_at.isoformat())
        self.assertEqual(preview['extracted_data']['project_numbers'], ['5900985'])
        self.import_document(existing=pr, reviewed_project_references=command)
        pr.refresh_from_db()
        self.assertEqual(pr.price_remarks_data['project_reference_reviews'], history)

    def test_stale_project_command_cannot_mutate_record_or_source(self):
        pr = self._excel_record()
        before = PurchaseRequisition.objects.filter(pk=pr.pk).values().get()
        with self.assertRaises(StaleRequisition):
            self.import_document(existing=pr, reviewed_project_references={
                'project_number': '5901001, 5901002',
                'expected_updated_at': (pr.updated_at - timedelta(seconds=1)).isoformat(),
            })
        self.assertEqual(PurchaseRequisition.objects.filter(pk=pr.pk).values().get(), before)
        self.storage.save.assert_not_called()

    def test_native_project_only_edit_derives_consistent_details(self):
        pr = self._excel_record()
        serializer = PurchaseRequisitionSerializer(pr, data={'project': '5901001, 5901002',
            'expected_updated_at': pr.updated_at.isoformat()}, partial=True,
            context={'request': SimpleNamespace(user=self.issuer)})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        self.assertEqual(serializer.data['project_numbers'], ['5901001', '5901002'])
        self.assertEqual(serializer.data['project'], '5901001, 5901002')

    def test_legacy_csv_projection_retains_all_codes_over_department_fallback(self):
        pr = self._excel_record()
        pr.project, pr.project_details, pr.project_department = 'PRJ-001, PRJ-002, PRJ-001', [], 'Department 5900985'
        pr.save(update_fields=['project', 'project_details', 'project_department'])
        self.assertEqual(PurchaseRequisitionSerializer(pr).data['project_numbers'], ['PRJ-001', 'PRJ-002'])
        self.assertEqual(preview_signed_pr_pdf(self.source, filename='signed.pdf')['project_numbers'], ['PRJ-001', 'PRJ-002'])

    def unknown_pm(self):
        self.evidence['approver_names']['pm'] = '00123'
        self.evidence['approval_rows'][0]['name'] = '00123'

    def test_signed_off_unknown_name_can_be_corrected_with_note_without_new_signature(self):
        self.unknown_pm()
        review = {'approval_labels': {'pm': 'Reviewed'}, 'additional_approver': None,
                  'approver_notes': {'pm': 'Source handwriting identifies the project director.'}}
        result = self.save_review(review, approvals={'pm': 'Corrected Director'})
        pr = PurchaseRequisition.objects.get(pk=result['pr_id'])
        self.assertTrue(result['document_signed_off'])
        evidence = pr.price_remarks_data['signed_approval_evidence']
        self.assertEqual(evidence['rows'][0]['name'], '00123')
        self.assertEqual(pr.approval_workflow_config[0]['user_name'], 'Corrected Director')
        self.assertTrue(pr.approval_workflow_config[0]['signature_verified'])
        audit = pr.price_remarks_data['source_approval_reviews'][0]
        self.assertEqual((audit['before'], audit['after']), ('00123', 'Corrected Director'))
        self.assertFalse(audit['signature_verified'])

    def test_unknown_name_correction_requires_note_and_known_signed_name_is_protected(self):
        with self.assertRaises(SignedPRImportError):
            self.save_review(approvals={'pm': 'Different Known Person'})
        self.unknown_pm()
        with self.assertRaises(SignedPRImportError):
            self.save_review(approvals={'pm': 'Corrected Director'})
        self.assertFalse(PurchaseRequisition.objects.exists())
        self.storage.save.assert_not_called()

    def test_richa_level_is_zero_in_new_source_review_without_new_workflow_stage(self):
        self.evidence['approver_names']['pm'] = 'Richa Thomas'
        self.evidence['approval_rows'][0]['name'] = 'Richa Thomas'
        result = self.save_review(expected={'approval_labels': {'pm': '0'}, 'additional_approver': None})
        self.assertEqual(result['source_approval_review']['approval_labels']['pm'], '0')
        pr = PurchaseRequisition.objects.get(pk=result['pr_id'])
        self.assertEqual(len(pr.approval_workflow_config), 3)
        self.assertEqual(pr.approval_workflow_config[0]['role'], 'PD')

    def test_detail_exposes_saved_additional_without_inserting_approval_history(self):
        result = self.save_review()
        pr = PurchaseRequisition.objects.get(pk=result['pr_id'])
        before = deepcopy(pr.approval_workflow_config)
        result = PurchaseRequisitionSerializer(pr).data
        self.assertEqual(result['source_approval_review'], annotation_fixtures.REVIEW)
        self.assertEqual(result['approval_hierarchy'], before)


@override_settings(ROOT_URLCONF=source_fixtures.__name__, MEDIA_URL='/media/')
class SavedSourceReviewFollowupTests(TestCase):
    setUp = source_fixtures.RequisitionSourceApprovalEditTests.setUp
    grant = source_fixtures.RequisitionSourceApprovalEditTests.grant
    payload = source_fixtures.RequisitionSourceApprovalEditTests.payload
    snapshot = source_fixtures.RequisitionSourceApprovalEditTests.snapshot
    save_rows = source_fixtures.RequisitionSourceApprovalEditTests.save_rows

    def test_unknown_name_level_note_correction_preserves_signature_and_syncs_annotation(self):
        self.rows[0]['user_name'] = 'Unknown Approver'
        self.save_rows(self.rows)
        signature = {key: self.rows[0][key] for key in ('signature_verified', 'signature_source', 'approved_at', 'status')}
        response = self.client.post(self.url, self.payload(0, approver_name='Known Director', approval_label='L1',
            signature_verified=False, approval_date='', special_note='Reviewed the original handwriting.'), format='json')
        self.assertEqual(response.status_code, 200, response.data)
        row = response.data['price_remarks_data']['signed_document_verification']['source_approval_rows'][0]
        self.assertEqual({key: row[key] for key in signature}, signature)
        self.assertEqual(row['approval_label'], 'L1')
        self.assertEqual(response.data['source_approval_review']['approval_labels']['pm'], 'L1')
        self.assertEqual(response.data['source_approval_review']['approver_notes']['pm'], row['special_note'])
        self.assertEqual(response.data['status'], 'draft')

    def test_note_required_and_complete_known_signer_stays_protected(self):
        before = self.snapshot()
        payload = self.payload()
        payload.pop('special_note')
        response = self.client.post(self.url, payload, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self.snapshot(), before)
        response = self.client.post(self.url, self.payload(0, signature_verified=False, approval_date=''), format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(self.snapshot(), before)

    def test_richa_unknown_name_correction_forces_zero_label_only(self):
        self.rows[0]['user_name'] = '123'
        self.save_rows(self.rows)
        response = self.client.post(self.url, self.payload(0, approver_name='Richa Hannah Thomas',
            approval_label='99', signature_verified=False, approval_date=''), format='json')
        self.assertEqual(response.status_code, 200, response.data)
        row = response.data['price_remarks_data']['signed_document_verification']['source_approval_rows'][0]
        self.assertEqual(row['approval_label'], '0')
        self.assertEqual(row['signature_source'], 'original_pdf')
        self.assertEqual(response.data['status'], 'draft')

    def test_legacy_richa_source_row_projects_zero_without_rewriting_evidence(self):
        self.rows[0]['user_name'] = 'Richa'
        self.rows[0]['approval_label'] = 'Historical label'
        self.save_rows(self.rows)
        self.pr.price_remarks_data['signed_approval_evidence'].pop('reviewed_approver_names', None)
        self.pr.save(update_fields=['price_remarks_data'])
        before = self.snapshot()
        response = PurchaseRequisitionSerializer(self.pr).data
        self.assertEqual(response['source_approval_review']['approval_labels']['pm'], '0')
        self.assertEqual(self.snapshot(), before)

    def seed_additional(self):
        self.seed_review = {**deepcopy(annotation_fixtures.REVIEW),
                            'approver_notes': {'vp': 'Initial unknown source Level reviewed.'}}
        _, envelope = prepare_source_approval_review(self.pr.price_remarks_data, source_fixtures.DIGEST,
            self.owner, self.seed_review, annotation_fixtures.EMPTY_REVIEW)
        self.pr.price_remarks_data['signed_approval_evidence']['source_approval_review'] = envelope
        self.pr.save(update_fields=['price_remarks_data', 'updated_at'])

    def review_payload(self, review=None):
        self.pr.refresh_from_db()
        return {'document_sha256': source_fixtures.DIGEST, 'expected_updated_at': self.pr.updated_at.isoformat(),
                'source_approval_review': deepcopy(review or self.seed_review),
                'expected_source_approval_review': deepcopy(self.seed_review)}

    def test_saved_additional_edit_requires_note_and_roundtrips_without_authority_change(self):
        self.seed_additional()
        before = self.snapshot()
        changed = deepcopy(annotation_fixtures.REVIEW)
        changed['additional_approver'].update(name='Corrected Additional', approval_label='6')
        response = self.client.post(self.detail_url + 'source-review/', self.review_payload(changed), format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self.snapshot(), before)
        changed['additional_approver']['special_note'] = 'Checked the signature caption in the PDF.'
        payload = self.review_payload(changed)
        response = self.client.post(self.detail_url + 'source-review/', payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['source_approval_review'], changed)
        after = self.snapshot()
        for key in ('approval_workflow_config', 'status', 'approved_by_id', 'approved_at', 'total_price'):
            self.assertEqual(after[key], before[key])
        again = self.client.post(self.detail_url + 'source-review/', payload, format='json')
        self.assertEqual(again.status_code, 200, again.data)
        self.assertEqual(self.snapshot(), after)

    def test_saved_annotation_requires_owner_update_grant_and_current_source(self):
        self.seed_additional()
        before = self.snapshot()
        payload = self.review_payload()
        self.client.force_authenticate(self.reader)
        response = self.client.post(self.detail_url + 'source-review/', payload, format='json')
        self.assertEqual(response.status_code, 403, response.data)
        self.client.force_authenticate(self.owner)
        self.grant(self.owner, 'update', False)
        response = self.client.post(self.detail_url + 'source-review/', payload, format='json')
        self.assertEqual(response.status_code, 403, response.data)
        self.grant(self.owner, 'update', True)
        payload['document_sha256'] = 'a' * 64
        response = self.client.post(self.detail_url + 'source-review/', payload, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(self.snapshot(), before)

    def test_saved_annotation_stale_version_and_storage_failure_leave_no_changes(self):
        self.seed_additional()
        before = self.snapshot()
        changed = deepcopy(annotation_fixtures.REVIEW)
        changed['approval_labels']['pm'] = 'Changed'
        payload = self.review_payload(changed)
        payload['expected_updated_at'] = (self.pr.updated_at - timedelta(seconds=1)).isoformat()
        response = self.client.post(self.detail_url + 'source-review/', payload, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(self.snapshot(), before)
        payload = self.review_payload(changed)
        self.storage_open.side_effect = OSError('isolated storage failure')
        response = self.client.post(self.detail_url + 'source-review/', payload, format='json')
        self.assertEqual(response.status_code, 503, response.data)
        self.assertEqual(self.snapshot(), before)

    def test_notes_reject_unknown_role_forged_authority_and_overlength(self):
        for value in ({'approver_notes': {'ceo': 'No'}}, {'approver_notes': {'pm': 1}},
                      {'approver_notes': {'pm': 'x' * 2001}},
                      {'additional_approver': {'name': 'Person', 'signature_verified': False, 'approved': True}}):
            with self.subTest(value=value), self.assertRaises(SourceApprovalReviewError):
                normalize_source_approval_review(value)

    def test_noop_saved_review_still_requires_well_formed_timestamp(self):
        self.seed_additional()
        before = self.snapshot()
        for value in (None, '', 'not-a-timestamp'):
            with self.subTest(value=value):
                payload = self.review_payload()
                payload['expected_updated_at'] = value
                response = self.client.post(self.detail_url + 'source-review/', payload, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertEqual(self.snapshot(), before)
        self.storage_open.assert_not_called()

    def test_saved_annotation_cannot_bypass_unknown_level_note_requirement(self):
        self.rows[3]['user_name'] = 'Unknown'
        self.save_rows(self.rows)
        self.seed_additional()
        changed = deepcopy(annotation_fixtures.REVIEW)
        changed['approval_labels']['vp'] = 'Reviewed level'
        before = self.snapshot()
        response = self.client.post(self.detail_url + 'source-review/', self.review_payload(changed), format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self.snapshot(), before)
        changed['approver_notes'] = {'vp': 'The level label was checked on the original PDF.'}
        response = self.client.post(self.detail_url + 'source-review/', self.review_payload(changed), format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['source_approval_review'], changed)


@override_settings(ROOT_URLCONF=pair_fixtures.originating.__name__)
class PairedProjectReviewTests(TestCase):
    setUp = pair_fixtures.PairedSignedImportTests.setUp
    grant = pair_fixtures.PairedSignedImportTests.grant
    revoke = pair_fixtures.PairedSignedImportTests.revoke
    source_files = pair_fixtures.PairedSignedImportTests.source_files
    upload = pair_fixtures.PairedSignedImportTests.upload

    def test_pair_attachment_project_ack_and_cached_retry_preserve_po_and_audit(self):
        first = self.upload()
        self.assertEqual(first.status_code, 201, first.data)
        pr = PurchaseRequisition.objects.get()
        options = {'create_new': 'false', 'attach_only': 'true', 'expected_pr_number': pr.pr_number,
                   'reviewed_project_references': json.dumps({'project_number': 'PRJ-001, PRJ-002',
                       'expected_updated_at': pr.updated_at.isoformat()})}
        changed = self.upload(**options)
        self.assertEqual(changed.status_code, 200, changed.data)
        self.assertEqual(changed.data['project_numbers'], ['PRJ-001', 'PRJ-002'])
        pr.refresh_from_db()
        before = PurchaseRequisition.objects.values().get()
        files = self.source_files()
        replay = self.upload(**options)
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(replay.data['operation'], 'already_imported')
        self.assertEqual(replay.data['project_numbers'], ['PRJ-001', 'PRJ-002'])
        self.assertEqual(PurchaseRequisition.objects.values().get(), before)
        self.assertEqual(self.source_files(), files)
        self.assertEqual(len(pr.price_remarks_data['project_reference_reviews']), 1)

    def test_attachment_project_command_requires_update_grant(self):
        first = self.upload()
        self.assertEqual(first.status_code, 201, first.data)
        pr = PurchaseRequisition.objects.get()
        before = PurchaseRequisition.objects.values().get()
        files = self.source_files()
        self.revoke('procurement_requisitions', 'update')
        for paired in (False, True):
            response = self.upload(paired=paired, create_new='false', attach_only='true', expected_pr_number=pr.pr_number,
                reviewed_project_references=json.dumps({'project_number': 'PRJ-001, PRJ-002',
                    'expected_updated_at': pr.updated_at.isoformat()}))
            self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(PurchaseRequisition.objects.values().get(), before)
        self.assertEqual(self.source_files(), files)


class LevelZeroDirectoryTests(TestCase):
    def person(self, username, first_name='Richa', last_name='Hannah Thomas', **kwargs):
        kwargs.setdefault('email', f'{username}@example.test')
        user = get_user_model().objects.create_user(username, first_name=first_name, last_name=last_name, **kwargs)
        organization, _ = Organization.objects.get_or_create(code='level-zero', defaults={'name': 'Level zero test'})
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': organization})
        profile.status, profile.is_deleted = 'active', False
        profile.save()
        return user

    def test_unique_active_richa_resolves_without_fabricating_approval(self):
        person = self.person('richa-directory', email='directory-richa@example.test')
        reference = default_level_zero_approver()
        self.assertEqual(reference['id'], str(person.pk))
        self.assertEqual(reference['level'], 0)
        self.assertEqual(reference['status'], 'not_recorded')
        self.assertNotIn('signature_verified', reference)
        self.assertFalse(PurchaseRequisition.objects.exists())

    def test_missing_inactive_and_ambiguous_directory_records_remain_unresolved(self):
        self.assertIsNone(default_level_zero_approver())
        first = self.person('inactive-richa', is_active=False)
        self.assertIsNone(default_level_zero_approver())
        first.is_active = True
        first.save(update_fields=['is_active'])
        self.person('other-richa', email='different@example.test')
        self.assertIsNone(default_level_zero_approver())

    def test_stable_record_email_is_preferred_only_for_named_active_richa(self):
        self.person('alternate-richa', email='alternate@example.test')
        preferred = self.person('preferred-richa', email='richa@rejlers.ae')
        self.assertEqual(default_level_zero_approver()['id'], str(preferred.pk))
        preferred.first_name = 'Different'
        preferred.save(update_fields=['first_name'])
        self.assertNotEqual(default_level_zero_approver()['id'], str(preferred.pk))
