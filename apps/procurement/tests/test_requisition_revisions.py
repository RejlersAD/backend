"""Rejected PR rounds are retained while editing and fresh approval restart."""

from copy import deepcopy
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.exceptions import ValidationError

from apps.procurement.models import PurchaseRequisition, PurchaseOrder, Vendor
from apps.procurement.services.requisition_revisions import HISTORY_KEY
from apps.procurement.services.requisition_workflow import RequisitionWorkflowService
from apps.rbac.models import Module, UserPermissionOverride, UserRole

from . import test_pr_save_submit_api as fixtures


@override_settings(ROOT_URLCONF=fixtures.__name__,
                   TEAMS_APPROVAL_WEBHOOK_URL='https://teams.example.test/test-only', WEB_PUSH_VAPID_PRIVATE_KEY='')
class RequisitionRevisionAPITests(TestCase):
    setUp = fixtures.PurchaseRequisitionSaveSubmitAPITests.setUp
    save_new = fixtures.PurchaseRequisitionSaveSubmitAPITests.save_new
    request = fixtures.PurchaseRequisitionSaveSubmitAPITests.request
    notices = fixtures.PurchaseRequisitionSaveSubmitAPITests.notices

    def reject_pr(self, *, second_stage=False):
        pr = self.save_new()
        self.assertEqual(self.request('post', pr, action='submit/').status_code, 200)
        self.client.force_authenticate(self.procurement)
        if second_stage:
            self.assertEqual(self.request('post', pr, action='process_dynamic_approval/').status_code, 200)
            self.client.force_authenticate(self.engineer)
        response = self.request('post', pr, {'reason': 'Please correct the scope before submitting again.'},
                                action='process_dynamic_rejection/')
        self.assertEqual(response.status_code, 200, response.data)
        pr.refresh_from_db()
        self.client.force_authenticate(self.issuer)
        return pr

    def reopen(self, pr, token=None):
        return self.request('post', pr, {'expected_updated_at': token or pr.updated_at.isoformat()}, action='reopen/')

    def test_edit_and_resubmit_starts_fresh_review_and_retains_rejected_evidence(self):
        pr = self.reject_pr(second_stage=True)
        previous = deepcopy(pr.approval_workflow_config)
        original_content = pr.product_service
        original_reason = pr.rejection_reason
        original_notices = self.notices(pr).count()
        for task in self.tasks.values():
            task.reset_mock()
        self.assertTrue(self.client.get(f'{fixtures.BASE}{pr.pk}/').data['can_reopen'])
        response = self.reopen(pr)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'draft')
        self.assertFalse(response.data['can_reopen'])
        pr.refresh_from_db()
        archive = deepcopy(pr.price_remarks_data[HISTORY_KEY])
        self.assertEqual(len(archive), 1)
        self.assertEqual(archive[0]['approval_workflow_config'], previous)
        self.assertEqual(archive[0]['rejection_reason'], original_reason)
        self.assertEqual(archive[0]['snapshot']['product_service'], original_content)
        self.assertEqual(archive[0]['snapshot']['status'], 'rejected')
        self.assertEqual(archive[0]['reopened_by_id'], str(self.issuer.pk))
        for old, new in zip(previous, pr.approval_workflow_config):
            self.assertNotEqual(old['assignment_id'], new['assignment_id'])
            self.assertEqual(new['status'], 'pending')
            self.assertEqual(old['user_id'], new['user_id'])
            self.assertNotIn('signature', new)
            self.assertNotIn('rejected_at', new)
        self.assertEqual(self.notices(pr).count(), original_notices)
        for task in self.tasks.values():
            task.assert_not_called()
        edited = self.request('patch', pr, {
            'product_service': 'Corrected purchase scope', 'expected_updated_at': response.data['updated_at'],
            'approval_workflow_config': self.workflow,
        })
        self.assertEqual(edited.status_code, 200, edited.data)
        submitted = self.request('post', pr, {'expected_updated_at': edited.data['updated_at'],
                                            'approval_workflow_config': self.workflow}, action='submit/')
        self.assertEqual(submitted.status_code, 200, submitted.data)
        pr.refresh_from_db()
        self.assertEqual(pr.status, 'submitted')
        self.assertEqual(pr.price_remarks_data[HISTORY_KEY], archive)
        self.assertEqual(self.notices(pr).count(), original_notices + 1)
        self.assertEqual(self.notices(pr).filter(recipient=self.procurement).count(), 2)
        self.tasks['teams'].assert_called_once()
        repeated = self.request('post', pr, {'expected_updated_at': submitted.data['updated_at']}, action='submit/')
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(self.notices(pr).count(), original_notices + 1)
        self.client.force_authenticate(self.procurement)
        decision = self.request('post', pr, {'expected_updated_at': submitted.data['updated_at']}, action='process_dynamic_approval/')
        self.assertEqual(decision.status_code, 200, decision.data)
        self.client.force_authenticate(self.engineer)
        decision = self.request('post', pr, {'expected_updated_at': decision.data['updated_at']}, action='process_dynamic_approval/')
        self.assertEqual(decision.status_code, 200, decision.data)
        self.assertEqual(decision.data['status'], 'approved')

    def test_missing_malformed_stale_and_retried_reopen_preserve_round(self):
        pr = self.reject_pr()
        initial = deepcopy(pr.approval_workflow_config)
        for payload in ({}, {'expected_updated_at': ''}, {'expected_updated_at': None}, {'expected_updated_at': 'invalid'}):
            response = self.request('post', pr, payload, action='reopen/')
            self.assertEqual(response.status_code, 400, response.data)
        stale_token = pr.updated_at.isoformat()
        pr.notes = 'A newer correction'
        pr.save()
        stale = self.reopen(pr, stale_token)
        self.assertEqual(stale.status_code, 409, stale.data)
        pr.refresh_from_db()
        self.assertEqual(pr.status, 'rejected')
        self.assertEqual(pr.approval_workflow_config, initial)
        self.assertNotIn(HISTORY_KEY, pr.price_remarks_data)
        current = pr.updated_at.isoformat()
        self.assertEqual(self.reopen(pr).status_code, 200)
        retry = self.reopen(pr, current)
        self.assertEqual(retry.status_code, 409, retry.data)
        pr.refresh_from_db()
        self.assertEqual(len(pr.price_remarks_data[HISTORY_KEY]), 1)

    def test_nonissuer_reader_and_explicitly_denied_superadmin_cannot_reopen(self):
        pr = self.reject_pr()
        UserRole.objects.create(user_profile=self.engineer.rbac_profile, role=self.editor_role)
        self.client.force_authenticate(self.engineer)
        self.assertFalse(self.client.get(f'{fixtures.BASE}{pr.pk}/').data['can_reopen'])
        self.assertEqual(self.reopen(pr).status_code, 403)
        module = Module.objects.get(code='procurement_requisitions')
        for superuser in (False, True):
            self.issuer.is_superuser = superuser
            self.issuer.save(update_fields=['is_superuser'])
            for permission in module.permissions.filter(action='update'):
                UserPermissionOverride.objects.update_or_create(
                    user_profile=self.issuer.rbac_profile, permission=permission, defaults={'allowed': False},
                )
            cache.clear()
            self.client.force_authenticate(self.issuer)
            self.assertFalse(self.client.get(f'{fixtures.BASE}{pr.pk}/').data['can_reopen'])
            self.assertEqual(self.reopen(pr).status_code, 403)
        pr.refresh_from_db()
        self.assertEqual(pr.status, 'rejected')
        self.assertNotIn(HISTORY_KEY, pr.price_remarks_data)

    def test_admin_may_reopen_with_existing_authority_but_other_states_are_denied(self):
        pr = self.reject_pr()
        self.engineer.is_superuser = True
        self.engineer.save(update_fields=['is_superuser'])
        cache.clear()
        self.client.force_authenticate(self.engineer)
        self.assertEqual(self.reopen(pr).status_code, 200)
        for state in ('draft', 'submitted', 'in_review', 'approved', 'cancelled', 'converted'):
            pr.refresh_from_db()
            pr.status = state
            pr.save()
            response = self.reopen(pr)
            self.assertEqual(response.status_code, 400, response.data)
            pr.refresh_from_db()
            self.assertEqual(pr.status, state)
            self.assertEqual(len(pr.price_remarks_data[HISTORY_KEY]), 1)

    def test_generic_payload_cannot_inject_or_remove_revision_history(self):
        pr = self.save_new(price_remarks_data={HISTORY_KEY: [{'forged': True}]})
        self.assertNotIn(HISTORY_KEY, pr.price_remarks_data)
        pr.status = 'rejected'
        pr.save()
        self.assertEqual(self.reopen(pr).status_code, 200)
        pr.refresh_from_db()
        archive = deepcopy(pr.price_remarks_data[HISTORY_KEY])
        for metadata in ({}, {HISTORY_KEY: []}, {HISTORY_KEY: [{'forged': True}]}):
            response = self.request('patch', pr, {'price_remarks_data': metadata, 'expected_updated_at': pr.updated_at.isoformat()})
            self.assertEqual(response.status_code, 200, response.data)
            pr.refresh_from_db()
            self.assertEqual(pr.price_remarks_data[HISTORY_KEY], archive)
        self.assertEqual(self.request('patch', pr, {'status': 'submitted'}).status_code, 400)

    def test_source_and_fixed_decisions_are_archived_while_files_and_link_stay(self):
        pr = self.reject_pr()
        pr.pm_name = self.engineer
        pr.pm_signature = 'retained-old-signature'
        pr.pm_approval_status = 'approved'
        pr.pm_approved_at = pr.updated_at
        pr.approved_by = self.engineer
        pr.approved_at = pr.updated_at
        pr.review_due_at = pr.updated_at
        pr.resolution_referral = {'target': 'mop', 'remarks': 'Keep discussion evidence'}
        pr.attachments = [{'filename': 'original.pdf', 'storage_key': 'synthetic/original.pdf'}]
        pr.price_remarks_data = {'signed_document_verification': {'signed_off': True}, 'commercial': 'kept'}
        pr.approval_workflow_config[0].update(external=True, source='signed_purchase_requisition_pdf',
                                             signature_verified=True, evidence_document_id='original')
        vendor = Vendor.objects.create(name='Revision test supplier', vendor_code='REVISION-SUPPLIER')
        order = PurchaseOrder.objects.create(po_number='REVISION-PO', vendor=vendor, pr_reference=pr,
                                             title='Existing draft order', status='draft', total_amount='100.00')
        pr.po_number_reference = order.po_number
        pr.save()
        original_approved_at = pr.pm_approved_at.isoformat()
        self.assertEqual(self.reopen(pr).status_code, 200)
        pr.refresh_from_db()
        snapshot = pr.price_remarks_data[HISTORY_KEY][0]['snapshot']
        self.assertEqual(snapshot['pm_signature'], 'retained-old-signature')
        self.assertEqual(snapshot['pm_approved_at'], original_approved_at)
        self.assertEqual(snapshot['approved_at'], original_approved_at)
        self.assertTrue(snapshot['price_remarks_data']['signed_document_verification']['signed_off'])
        self.assertNotIn('signed_document_verification', pr.price_remarks_data)
        self.assertEqual(pr.attachments, snapshot['attachments'])
        self.assertFalse(pr.resolution_referral)
        self.assertIsNone(pr.pm_name)
        self.assertEqual(pr.pm_signature, '')
        self.assertIsNone(pr.approved_by)
        self.assertIsNone(pr.approved_at)
        self.assertIsNone(pr.review_due_at)
        self.assertNotIn('external', pr.approval_workflow_config[0])
        self.assertNotIn('signature_verified', pr.approval_workflow_config[0])
        self.assertEqual(pr.po_number_reference, order.po_number)
        order.refresh_from_db()
        self.assertEqual(order.pr_reference_id, pr.pk)
        self.assertEqual(order.status, 'draft')

    def test_old_decision_tabs_cannot_approve_or_reject_resubmitted_round(self):
        pr = self.reject_pr()
        old_token = pr.updated_at.isoformat()
        self.assertEqual(self.reopen(pr).status_code, 200)
        pr.refresh_from_db()
        self.assertEqual(self.request('post', pr, {'expected_updated_at': pr.updated_at.isoformat()}, action='submit/').status_code, 200)
        pr.refresh_from_db()
        before = deepcopy(pr.approval_workflow_config)
        self.client.force_authenticate(self.procurement)
        for action in ('approve/', 'reject/', 'process_dynamic_approval/', 'process_dynamic_rejection/'):
            for token, expected in ((None, 400), (old_token, 409)):
                payload = {'reason': 'Outdated rejection from an earlier review'}
                if token:
                    payload['expected_updated_at'] = token
                response = self.request('post', pr, payload, action=action)
                self.assertEqual(response.status_code, expected, response.data)
                pr.refresh_from_db()
                self.assertEqual(pr.status, 'submitted')
                self.assertEqual(pr.approval_workflow_config, before)
        self.assertEqual(self.request('post', pr, {'expected_updated_at': pr.updated_at.isoformat()}, action='approve/').status_code, 200)

    def test_failed_write_does_not_partially_append_archive_or_reset_decisions(self):
        pr = self.reject_pr()
        original = deepcopy(pr.approval_workflow_config)
        save = PurchaseRequisition.save
        def fail_after_save(instance, *args, **kwargs):
            save(instance, *args, **kwargs)
            raise ValidationError({'error': 'Synthetic failure after saving the new draft'})
        with patch.object(PurchaseRequisition, 'save', fail_after_save):
            result = self.reopen(pr)
        self.assertEqual(result.status_code, 400, result.data)
        pr.refresh_from_db()
        self.assertEqual(pr.status, 'rejected')
        self.assertEqual(pr.approval_workflow_config, original)
        self.assertNotIn(HISTORY_KEY, pr.price_remarks_data)

    def test_repeated_rounds_append_history_without_nested_archive_copies(self):
        pr = self.reject_pr()
        self.assertEqual(self.reopen(pr).status_code, 200)
        pr.refresh_from_db()
        first = deepcopy(pr.price_remarks_data[HISTORY_KEY][0])
        self.assertEqual(self.request('post', pr, {'expected_updated_at': pr.updated_at.isoformat()}, action='submit/').status_code, 200)
        pr.refresh_from_db()
        self.client.force_authenticate(self.procurement)
        result = self.request('post', pr, {'reason': 'Another correction requested by procurement',
                                         'expected_updated_at': pr.updated_at.isoformat()}, action='reject/')
        self.assertEqual(result.status_code, 200, result.data)
        pr.refresh_from_db()
        self.client.force_authenticate(self.issuer)
        self.assertEqual(self.reopen(pr).status_code, 200)
        pr.refresh_from_db()
        history = pr.price_remarks_data[HISTORY_KEY]
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0], first)
        self.assertNotIn(HISTORY_KEY, history[1]['snapshot']['price_remarks_data'])

    def test_stale_legacy_escalation_cannot_restore_previous_rejected_round(self):
        pr = self.reject_pr()
        token = pr.updated_at.isoformat()
        self.assertEqual(self.reopen(pr).status_code, 200)
        result = self.request('post', pr, {'escalate_to': 'mop', 'escalation_notes': 'Old discussion request',
                                         'expected_updated_at': token}, action='escalate-rejection/')
        self.assertEqual(result.status_code, 409, result.data)
        pr.refresh_from_db()
        self.assertEqual(pr.status, 'draft')
        self.assertFalse(pr.resolution_referral)
        self.assertEqual(len(pr.price_remarks_data[HISTORY_KEY]), 1)

    def test_rejected_content_requires_reopen_and_revised_writes_require_current_token(self):
        pr = self.reject_pr()
        original = pr.product_service
        result = self.request('patch', pr, {'product_service': 'Do not replace rejected content',
                                          'expected_updated_at': pr.updated_at.isoformat()})
        self.assertEqual(result.status_code, 400, result.data)
        pr.refresh_from_db()
        self.assertEqual(pr.product_service, original)
        self.assertEqual(self.reopen(pr).status_code, 200)
        pr.refresh_from_db()
        stale_token = pr.updated_at.isoformat()
        pr.notes = 'A newer draft correction'
        pr.save()
        before = deepcopy(pr.approval_workflow_config)
        for token, expected in ((None, 400), (stale_token, 409)):
            for method, action in (('patch', ''), ('post', 'submit/')):
                payload = {'product_service': 'Outdated editor', 'approval_workflow_config': self.workflow}
                if token:
                    payload['expected_updated_at'] = token
                result = self.request(method, pr, payload, action=action)
                self.assertEqual(result.status_code, expected, result.data)
                pr.refresh_from_db()
                self.assertEqual(pr.status, 'draft')
                self.assertEqual(pr.product_service, original)
                self.assertEqual(pr.approval_workflow_config, before)

    def test_obsolete_approval_notification_callback_cannot_target_new_round(self):
        pr = self.save_new()
        with self.captureOnCommitCallbacks(execute=False) as old_callbacks:
            result = self.client.post(f'{fixtures.BASE}{pr.pk}/submit/', {}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertTrue(old_callbacks)
        self.assertEqual(self.notices(pr).count(), 0)
        self.client.force_authenticate(self.procurement)
        result = self.request('post', pr, {'reason': 'Correct the original purchase scope'}, action='reject/')
        self.assertEqual(result.status_code, 200, result.data)
        pr.refresh_from_db()
        self.client.force_authenticate(self.issuer)
        self.assertEqual(self.reopen(pr).status_code, 200)
        pr.refresh_from_db()
        result = self.request('post', pr, {'expected_updated_at': pr.updated_at.isoformat()}, action='submit/')
        self.assertEqual(result.status_code, 200, result.data)
        notices = list(self.notices(pr).values_list('pk', flat=True))
        self.assertEqual(len(notices), 1)
        for callback in old_callbacks:
            callback()
        self.assertEqual(list(self.notices(pr).values_list('pk', flat=True)), notices)
