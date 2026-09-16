from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.exceptions import ObjectDoesNotExist
from django.test import SimpleTestCase
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.procurement.services.requisition_workflow import RequisitionWorkflowService
from apps.procurement.tests.test_requisition_workflow_service import FakeRequisition, FakeUser


SERVICE_PATH = 'apps.procurement.services.requisition_workflow'


class RequisitionSignerIntegrityTests(SimpleTestCase):
    def setUp(self):
        self.approvers = [
            self.user('richa', 'Richa Thomas'),
            self.user('pm', 'Project Manager'),
            self.user('engineering', 'Engineering Manager'),
            self.user('projects', 'Manager of Projects'),
            self.user('vp', 'VP Operations'),
            self.user('ceo', 'Jarmo Suominen', is_superuser=True),
        ]
        self.admin = self.user('admin', 'System Administrator')
        self.admin.rbac_profile.roles.filter.return_value.exists.return_value = True
        self.roles = [
            'Procurement Manager', 'Level 1 Approver', 'Engineering Manager',
            'Manager of Projects', 'VP Operations', 'CEO',
        ]
        display = patch(f'{SERVICE_PATH}.employee_display_name', side_effect=lambda user: user.full_name)
        display.start()
        self.addCleanup(display.stop)

    @staticmethod
    def user(identifier, name, is_superuser=False):
        roles = Mock()
        roles.filter.return_value.exists.return_value = False
        return FakeUser(
            id=identifier, pk=identifier, full_name=name, is_superuser=is_superuser,
            email=f'{identifier}@example.com', is_active=True,
            rbac_profile=SimpleNamespace(signature_image=f'{identifier}-saved-signature', roles=roles),
        )

    def pr(self, active_level=0, status='submitted'):
        return FakeRequisition(
            status=status, po_number_reference='', po_applicable=False,
            current_approval_step=active_level, rejection_reason='',
            vp_op_name=None, vp_op_signature='', vp_op_approval_status='pending',
            approval_workflow_config=[
                {
                    'level': level, 'role': role, 'user_id': actor.id,
                    'user_email': actor.email, 'user_name': actor.full_name,
                    'status': 'approved' if level < active_level else 'pending',
                }
                for level, (role, actor) in enumerate(zip(self.roles, self.approvers))
            ],
        )

    def test_ceo_cannot_approve_or_reject_any_earlier_level_even_as_superuser(self):
        ceo = self.approvers[5]
        for level in range(5):
            for decision in ('approve', 'reject'):
                with self.subTest(level=level, decision=decision):
                    pr = self.pr(active_level=level)
                    before = deepcopy(pr.approval_workflow_config)
                    self.assertFalse(RequisitionWorkflowService.can_approve(pr, ceo))
                    with self.assertRaises(PermissionDenied):
                        if decision == 'approve':
                            RequisitionWorkflowService._approve_locked(pr, ceo, require_signature=True)
                        else:
                            RequisitionWorkflowService._reject_locked(pr, ceo, 'Please revise the request.')
                    self.assertEqual(pr.approval_workflow_config, before)
                    self.assertFalse(hasattr(pr, 'save_count'))

    def test_rbac_super_admin_cannot_stamp_or_reject_another_persons_row(self):
        for decision in ('approve', 'reject'):
            with self.subTest(decision=decision):
                pr = self.pr()
                before = deepcopy(pr.approval_workflow_config)
                self.assertFalse(RequisitionWorkflowService.can_approve(pr, self.admin))
                with self.assertRaises(PermissionDenied):
                    if decision == 'approve':
                        RequisitionWorkflowService._approve_locked(
                            pr, self.admin, signature='richa-supplied-signature', require_signature=True,
                        )
                    else:
                        RequisitionWorkflowService._reject_locked(pr, self.admin, 'Please revise the request.')
                self.assertEqual(pr.approval_workflow_config, before)
                self.assertFalse(hasattr(pr, 'save_count'))

    def test_inactive_account_or_suspended_deleted_profile_cannot_decide(self):
        restrictions = (
            ('account', 'is_active', False),
            ('account', 'is_deleted', True),
            ('profile', 'status', 'suspended'),
            ('profile', 'status', 'inactive'),
            ('profile', 'is_deleted', True),
        )
        for target, field, value in restrictions:
            for decision in ('approve', 'reject'):
                with self.subTest(target=target, field=field, value=value, decision=decision):
                    actor = self.user('richa', 'Richa Thomas', is_superuser=True)
                    setattr(actor if target == 'account' else actor.rbac_profile, field, value)
                    pr = self.pr()
                    before = deepcopy(pr.approval_workflow_config)
                    self.assertFalse(RequisitionWorkflowService._stage_matches_user(before[0], actor))
                    self.assertFalse(RequisitionWorkflowService.can_approve(pr, actor))
                    with self.assertRaisesMessage(PermissionDenied, 'Only an active employee'):
                        if decision == 'approve':
                            RequisitionWorkflowService._approve_locked(pr, actor, require_signature=True)
                        else:
                            RequisitionWorkflowService._reject_locked(pr, actor, 'Please revise the request.')
                    self.assertEqual(pr.approval_workflow_config, before)
                    self.assertFalse(hasattr(pr, 'save_count'))

    def test_admin_assigned_to_active_peer_row_signs_only_their_own_row(self):
        pr = self.pr()
        pr.approval_workflow_config.insert(1, {
            'level': 0, 'role': 'Procurement Reviewer', 'user_id': self.admin.id,
            'user_email': self.admin.email, 'status': 'pending',
        })
        self.admin.is_superuser = True
        richa_before = deepcopy(pr.approval_workflow_config[0])

        RequisitionWorkflowService._approve_locked(pr, self.admin, require_signature=True)

        self.assertEqual(pr.approval_workflow_config[0], richa_before)
        signed = pr.approval_workflow_config[1]
        self.assertEqual(signed['approved_by_id'], self.admin.id)
        self.assertEqual(signed['signature'], self.admin.rbac_profile.signature_image)
        self.assertEqual(pr.current_approval_step, 0)

    def test_richa_uses_saved_signature_and_provenance_without_writing_vp_fields(self):
        pr = self.pr()
        richa = self.approvers[0]
        self.assertTrue(RequisitionWorkflowService.can_approve(pr, richa))

        RequisitionWorkflowService._approve_locked(
            pr, richa, signature=self.approvers[5].rbac_profile.signature_image, require_signature=True,
        )

        signed = pr.approval_workflow_config[0]
        self.assertEqual(signed['approved_by_id'], richa.id)
        self.assertEqual(signed['approved_by_name'], 'Richa Thomas')
        self.assertEqual(signed['approved_by_email'], richa.email)
        self.assertEqual(signed['signature_user_id'], richa.id)
        self.assertEqual(signed['signature_user_email'], richa.email)
        self.assertEqual(signed['signature'], 'richa-saved-signature')
        self.assertIsNone(pr.vp_op_name)
        self.assertEqual(pr.vp_op_signature, '')
        self.assertEqual(pr.vp_op_approval_status, 'pending')
        richa.rbac_profile.signature_image = 'updated-profile-signature'
        self.assertEqual(signed['signature'], 'richa-saved-signature')

    def test_client_signature_cannot_replace_a_missing_saved_signature(self):
        pr = self.pr()
        before = deepcopy(pr.approval_workflow_config)
        self.approvers[0].rbac_profile.signature_image = ''

        with self.assertRaisesMessage(ValidationError, 'Add your signature in Profile'):
            RequisitionWorkflowService._approve_locked(
                pr, self.approvers[0], signature='forged-client-signature', require_signature=True,
            )

        self.assertEqual(pr.approval_workflow_config, before)
        self.assertFalse(hasattr(pr, 'save_count'))

    def test_ceo_can_sign_own_row_after_all_previous_levels_are_approved(self):
        pr = self.pr(active_level=5)
        previous = deepcopy(pr.approval_workflow_config[:5])
        ceo = self.approvers[5]
        self.assertTrue(RequisitionWorkflowService.can_approve(pr, ceo))

        RequisitionWorkflowService._approve_locked(pr, ceo, require_signature=True)

        self.assertEqual(pr.status, 'approved')
        self.assertEqual(pr.approval_workflow_config[:5], previous)
        self.assertEqual(pr.approval_workflow_config[5]['approved_by_id'], ceo.id)
        self.assertEqual(pr.approval_workflow_config[5]['signature'], 'ceo-saved-signature')

    def test_unresolved_lower_status_cannot_be_skipped(self):
        for status in ('not_recorded', 'skipped', 'unknown', ''):
            with self.subTest(status=status):
                pr = self.pr(active_level=5)
                pr.approval_workflow_config[0]['status'] = status
                self.assertFalse(RequisitionWorkflowService.can_approve(pr, self.approvers[5]))
                with self.assertRaisesMessage(ValidationError, 'Level 0 approval evidence'):
                    RequisitionWorkflowService._approve_locked(pr, self.approvers[5], require_signature=True)
                self.assertEqual(pr.approval_workflow_config[0]['status'], status)

    def test_approval_does_not_silently_activate_or_complete_unknown_next_status(self):
        pr = self.pr()
        pr.approval_workflow_config[1]['status'] = 'not_recorded'

        RequisitionWorkflowService._approve_locked(pr, self.approvers[0], require_signature=True)

        self.assertEqual(pr.status, 'in_review')
        self.assertEqual(pr.current_approval_step, 1)
        self.assertEqual(pr.approval_workflow_config[1]['status'], 'not_recorded')
        self.assertFalse(RequisitionWorkflowService.can_approve(pr, self.approvers[2]))

    def test_known_mismatched_previous_signer_blocks_progress_and_keeps_evidence(self):
        for metadata in (
            {'approved_by_id': 'ceo'},
            {'approved_by_email': 'ceo@example.com'},
            {'signature_user_id': 'ceo'},
            {'signature_user_email': 'ceo@example.com'},
        ):
            with self.subTest(metadata=metadata):
                pr = self.pr(active_level=1)
                pr.approval_workflow_config[0].update(metadata)
                before = deepcopy(pr.approval_workflow_config)
                self.assertFalse(RequisitionWorkflowService.can_approve(pr, self.approvers[1]))
                with self.assertRaisesMessage(ValidationError, 'Approval review is required'):
                    RequisitionWorkflowService._approve_locked(pr, self.approvers[1], require_signature=True)
                self.assertEqual(pr.approval_workflow_config, before)
                pr.pk = 'persisted-pr'
                with patch(f'{SERVICE_PATH}.RequisitionWorkflowService._resolve_stage_user') as resolve:
                    RequisitionWorkflowService._notify_level(pr, pr.approval_workflow_config, 1)
                    resolve.assert_not_called()

    def test_reviewed_source_document_evidence_is_not_reinterpreted_as_account_signature(self):
        pr = self.pr(active_level=1)
        pr.approval_workflow_config[0].update({
            'external': True, 'source': 'signed_purchase_requisition_pdf',
            'approved_by_id': 'source-reviewer', 'signature_source': 'original_pdf',
            'signature_verified': True,
        })
        original = deepcopy(pr.approval_workflow_config[0])

        RequisitionWorkflowService._approve_locked(pr, self.approvers[1], require_signature=True)

        self.assertEqual(pr.approval_workflow_config[0], original)
        self.assertEqual(pr.approval_workflow_config[1]['approved_by_id'], self.approvers[1].id)

    def test_converted_request_requires_recovery_on_the_selected_stage(self):
        pr = self.pr(status='converted')
        pr.approval_workflow_config[1]['level'] = 0
        pr.approval_workflow_config[1]['evidence_requested_at'] = '2026-09-16T10:00:00Z'
        self.assertFalse(RequisitionWorkflowService.can_approve(pr, self.approvers[0]))
        self.assertTrue(RequisitionWorkflowService.can_approve(pr, self.approvers[1]))
        with self.assertRaisesMessage(ValidationError, 'has not been requested for this stage'):
            RequisitionWorkflowService._approve_locked(pr, self.approvers[0], require_signature=True)
        with self.assertRaisesMessage(ValidationError, 'has not been requested for this stage'):
            RequisitionWorkflowService._reject_locked(pr, self.approvers[0], 'Please revise the request.')

        RequisitionWorkflowService._approve_locked(pr, self.approvers[1], require_signature=True)

        self.assertEqual(pr.status, 'converted')
        self.assertEqual(pr.approval_workflow_config[0]['status'], 'pending')
        self.assertEqual(pr.approval_workflow_config[1]['status'], 'approved')

    def test_closed_or_unrequested_converted_request_is_not_actionable(self):
        for status in ('draft', 'approved', 'rejected', 'converted'):
            with self.subTest(status=status):
                self.assertFalse(RequisitionWorkflowService.can_approve(self.pr(status=status), self.approvers[0]))

    @patch(f'{SERVICE_PATH}.get_user_model')
    def test_missing_assigned_email_never_falls_back_to_an_unrelated_id(self, get_user_model):
        users = get_user_model.return_value.objects
        users.get.side_effect = ObjectDoesNotExist
        stage = {'user_email': 'missing-richa@example.com', 'user_id': self.approvers[5].id}

        self.assertIsNone(RequisitionWorkflowService._resolve_stage_user(stage))

        users.get.assert_called_once_with(email__iexact='missing-richa@example.com', is_active=True)
