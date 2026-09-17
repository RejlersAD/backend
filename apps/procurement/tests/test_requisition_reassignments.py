"""Pending reassignment through the guarded API preserves decisions and audit."""

from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.procurement.models import PurchaseRequisition
from apps.procurement.serializers import PurchaseRequisitionSerializer
from apps.procurement.services.requisition_reassignments import HISTORY_KEY
from apps.procurement.services.requisition_workflow import RequisitionWorkflowService
from apps.rbac.models import Module, Organization, Permission, Role, RoleModule, UserPermissionOverride, UserProfile, UserRole
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints


urlpatterns = [path('api/v1/procurement/', include('apps.procurement.urls'))]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__, TEAMS_APPROVAL_WEBHOOK_URL='')
class RequisitionReassignmentTests(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.owner, self.editor, self.old, self.replacement, self.reader = [
            User.objects.create_user(name, email=f'{name}@example.test')
            for name in ('assignment-owner', 'assignment-editor', 'old-assignee', 'new-assignee', 'assignment-reader')
        ]
        org, _ = Organization.objects.get_or_create(code='assignments', defaults={'name': 'Assignments'})
        module, _ = Module.objects.get_or_create(code='procurement_requisitions', defaults={'name': 'Purchase Requisitions'})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        self.permissions = {action: Permission.objects.get(module=module, action=action, is_active=True)
                            for action in ('read', 'update')}
        role = Role.objects.create(code='assignment-editors', name='Assignment editors', level=3)
        RoleModule.objects.create(role=role, module=module)
        for user in (self.owner, self.editor, self.old, self.replacement, self.reader):
            profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': org})
            profile.roles.clear()
            profile.status, profile.is_deleted = 'active', False
            profile.save()
            UserRole.objects.create(user_profile=profile, role=role)
            for action, permission in self.permissions.items():
                UserPermissionOverride.objects.create(user_profile=profile, permission=permission,
                                                     allowed=action == 'read' or user in (self.owner, self.editor))
        self.workflow = [
            {'level': 0, 'role': 'Procurement Department', 'user_id': str(self.owner.pk),
             'user_email': self.owner.email, 'status': 'approved', 'signature': 'retained-signature',
             'approved_by_id': str(self.owner.pk), 'approved_by_email': self.owner.email,
             'approved_at': '2026-01-01T12:00:00Z', 'assignment_id': 'completed-assignment', 'custom_audit': 'keep'},
            {'level': 1, 'role': 'Level 1 Approver', 'stage': 'Level 1 Approver 1', 'user_id': str(self.old.pk),
             'user_email': self.old.email, 'status': 'pending', 'assignment_id': 'pending-assignment',
             'approval_label': 'L1-1', 'approval_group': 'level_1', 'group_mode': 'all'},
        ]
        self.pr = PurchaseRequisition.objects.create(
            pr_number='ASSIGN-PR-2026', issued_by=self.owner, status='in_review',
            po_applicable=True, approval_workflow_config=deepcopy(self.workflow), current_approval_step=1,
            price_remarks_data={'untouched': 'commercial note'},
        )
        self.url = f'/api/v1/procurement/requisitions/{self.pr.pk}/'
        self.client = APIClient()
        self.client.force_authenticate(self.editor)
        self.notifications = patch('apps.procurement.serializers.notify_requisition_approver_changes').start()
        self.addCleanup(patch.stopall)

    def command(self, index=1, user=None):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        row = response.data['approval_workflow_config'][index]
        return {**row['reassignment_snapshot'], 'user_id': str((user or self.replacement).pk)}

    def change(self, command=None, **extra):
        command = command or self.command()
        return self.client.patch(self.url, {'approval_reassignments': [command], **extra}, format='json')

    def test_update_granted_nonissuer_changes_pending_assignment_and_commercial_fields(self):
        command = self.command()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.change(command, product_service='Edited service')
        self.assertEqual(response.status_code, 200, response.data)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.approval_workflow_config[0], self.workflow[0])
        row = self.pr.approval_workflow_config[1]
        self.assertEqual(row['user_id'], str(self.replacement.pk))
        self.assertNotEqual(row['assignment_id'], 'pending-assignment')
        self.assertEqual(row['status'], 'pending')
        self.assertIsNone(row['approved_at'])
        self.assertNotIn('signature', row)
        self.assertEqual(self.pr.status, 'in_review')
        self.assertEqual(self.pr.current_approval_step, 1)
        self.assertEqual(self.pr.product_service, 'Edited service')
        audit = self.pr.price_remarks_data[HISTORY_KEY][0]
        self.assertEqual(audit['before'], self.workflow[1])
        self.assertEqual(audit['after'], row)
        self.assertEqual(audit['changed_by_id'], str(self.editor.pk))
        self.notifications.assert_called_once()
        self.assertFalse(RequisitionWorkflowService.can_approve(self.pr, self.old))
        self.assertTrue(RequisitionWorkflowService.can_approve(self.pr, self.replacement))

    def test_reader_or_explicitly_denied_editor_cannot_reassign(self):
        command = self.command()
        for user in (self.reader, self.editor):
            UserPermissionOverride.objects.update_or_create(
                user_profile=user.rbac_profile, permission=self.permissions['update'], defaults={'allowed': False},
            )
            cache.clear()
            self.client.force_authenticate(user)
            detail = self.client.get(self.url)
            self.assertFalse(detail.data['can_reassign_approvers'])
            self.assertEqual(detail.data['reassignable_approval_stage_indices'], [])
            response = self.change(command)
            self.assertEqual(response.status_code, 403, response.data)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.approval_workflow_config, self.workflow)

    def test_completed_approval_cannot_be_selected_or_forged(self):
        command = self.command()
        command.update(stage_index=0)
        response = self.change(command)
        self.assertEqual(response.status_code, 400, response.data)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.approval_workflow_config, self.workflow)

    def test_approved_converted_rejected_records_have_no_reassignment_capability(self):
        command = self.command()
        for status in ('approved', 'converted', 'rejected'):
            self.pr.status = status
            self.pr.save(update_fields=['status'])
            detail = self.client.get(self.url)
            self.assertFalse(detail.data['can_reassign_approvers'])
            response = self.change(command)
            self.assertEqual(response.status_code, 400, response.data)
            self.pr.refresh_from_db()
            self.assertEqual(self.pr.status, status)
            self.assertEqual(self.pr.approval_workflow_config, self.workflow)

    def test_source_active_route_retains_completed_pdf_evidence_and_prior_metadata(self):
        source = 'signed_purchase_requisition_pdf'
        self.workflow[0].update(external=True, source=source, signature_verified=True)
        self.workflow[1].update(external=True, source=source, status='not_recorded', signature_verified=False)
        self.pr.approval_workflow_config = deepcopy(self.workflow)
        verification = {'signed_off': False, 'source_approval_rows': deepcopy(self.workflow), 'document_sha256': 'a' * 64}
        self.pr.price_remarks_data['signed_document_verification'] = verification
        self.pr.save()
        response = self.change()
        self.assertEqual(response.status_code, 200, response.data)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.approval_workflow_config[0], self.workflow[0])
        self.assertEqual(self.pr.price_remarks_data['signed_document_verification'], verification)
        row = self.pr.approval_workflow_config[1]
        self.assertEqual(row['status'], 'pending')
        self.assertNotIn('external', row)
        self.assertNotIn('source', row)
        self.assertNotIn('signature_verified', row)
        self.assertTrue(RequisitionWorkflowService.can_approve(self.pr, self.replacement))

    def test_source_only_draft_needs_evidence_review_not_live_reassignment(self):
        self.pr.status = 'draft'
        for row in self.pr.approval_workflow_config:
            row.update(external=True, source='signed_purchase_requisition_pdf')
        self.pr.save()
        detail = self.client.get(self.url)
        self.assertFalse(detail.data['can_reassign_approvers'])

    def test_source_abbreviated_role_retains_its_business_authority(self):
        from .approval_fixtures import grant_approval, set_position
        grant_approval(self.replacement)
        set_position(self.replacement, 'Manager of Engineering')
        self.pr.approval_workflow_config[1].update(
            role='MoE', role_key='moe', level=2, stage='', external=True,
            source='signed_purchase_requisition_pdf', status='not_recorded', signature_verified=False,
        )
        self.pr.save(update_fields=['approval_workflow_config'])
        response = self.change()
        self.assertEqual(response.status_code, 200, response.data)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.approval_workflow_config[1]['business_position'], 'engineering_manager')
        self.assertTrue(RequisitionWorkflowService.can_approve(self.pr, self.replacement))
        updated = RequisitionWorkflowService.approve(self.pr.pk, self.replacement, expected_stage_key='eng_manager')
        self.assertEqual(updated.eng_manager_name_id, self.replacement.pk)
        self.assertEqual(updated.eng_manager_approval_status, 'approved')
        self.assertEqual(updated.approval_workflow_config[0], self.workflow[0])

    def test_stale_command_cannot_overwrite_concurrently_recorded_decision(self):
        command = self.command()
        serializer = PurchaseRequisitionSerializer(self.pr, data={'approval_reassignments': [command]}, partial=True,
                                                  context={'request': SimpleNamespace(user=self.editor)})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.pr.approval_workflow_config[1].update(status='approved', signature='newly-recorded',
                                                 approved_by_id=str(self.old.pk), approved_at='2026-01-02T12:00:00Z')
        self.pr.save(update_fields=['approval_workflow_config'])
        saved = deepcopy(self.pr.approval_workflow_config)
        with self.assertRaises(ValidationError):
            serializer.save()
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.approval_workflow_config, saved)

    def test_stale_assignment_cycle_and_changed_role_are_rejected(self):
        command = self.command()
        self.assertEqual(self.change(command).status_code, 200)
        self.assertEqual(self.change(self.command(user=self.old)).status_code, 200)
        self.assertEqual(self.change(command).status_code, 400)
        command = self.command()
        self.pr.refresh_from_db()
        self.pr.approval_workflow_config[1]['role'] = 'Replacement role'
        self.pr.save(update_fields=['approval_workflow_config'])
        self.assertEqual(self.change(command).status_code, 400)

    def test_audit_survives_stale_commercial_metadata_and_cannot_be_forged(self):
        self.assertEqual(self.change().status_code, 200)
        self.pr.refresh_from_db()
        audit = deepcopy(self.pr.price_remarks_data[HISTORY_KEY])
        response = self.client.patch(self.url, {'price_remarks_data': {HISTORY_KEY: [{'forged': True}], 'changed': 1}}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.price_remarks_data[HISTORY_KEY], audit)
        self.assertEqual(self.pr.price_remarks_data['changed'], 1)

    def test_new_assignment_cannot_take_client_approval_or_signature(self):
        command = self.command()
        command.update(status='approved', signature='forged', approved_by_id=str(self.replacement.pk))
        response = self.change(command)
        self.assertEqual(response.status_code, 403, response.data)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.approval_workflow_config, self.workflow)

    def test_invalid_user_and_duplicate_stage_commands_fail_atomically(self):
        command = self.command()
        command['user_id'] = 'not-a-user'
        self.assertEqual(self.change(command).status_code, 400)
        command = self.command()
        response = self.client.patch(self.url, {'approval_reassignments': [command, command]}, format='json')
        self.assertEqual(response.status_code, 400)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.approval_workflow_config, self.workflow)

    def test_nonissuer_normal_workflow_edit_respects_update_grant(self):
        response = self.client.patch(self.url, {'approval_workflow_config': deepcopy(self.workflow),
                                              'product_service': 'Ordinary edit'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.product_service, 'Ordinary edit')
        self.assertEqual(self.pr.approval_workflow_config[0]['signature'], self.workflow[0]['signature'])

    def test_same_assignee_does_not_reset_assignment_or_duplicate_audit(self):
        response = self.change(self.command(user=self.old))
        self.assertEqual(response.status_code, 200, response.data)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.approval_workflow_config, self.workflow)
        self.assertNotIn(HISTORY_KEY, self.pr.price_remarks_data)

    def test_full_form_saved_evidence_is_not_an_approval_command_and_cannot_be_forged(self):
        self.pr.price_remarks_data['signed_document_verification'] = {'signed_off': False, 'status': 'approved'}
        self.pr.save(update_fields=['price_remarks_data'])
        incoming = deepcopy(self.workflow)
        incoming[1].update(status='approved', signature='forged', approved_by_id=str(self.replacement.pk))
        response = self.client.patch(self.url, {
            'approval_workflow_config': incoming,
            'price_remarks_data': {'signed_document_verification': {'signed_off': True, 'status': 'approved'}},
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.approval_workflow_config[1]['status'], 'pending')
        self.assertNotIn('signature', self.pr.approval_workflow_config[1])
        self.assertNotIn('approved_by_id', self.pr.approval_workflow_config[1])
        self.assertFalse(self.pr.price_remarks_data['signed_document_verification']['signed_off'])

    def test_position_mismatch_warns_on_reassignment_but_does_not_authorize_approval(self):
        self.pr.approval_workflow_config[1].update(level=2, role='Manager of Engineering', stage='Engineering Review')
        self.pr.save(update_fields=['approval_workflow_config'])
        response = self.change()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(any('configured business position' in warning for warning in response.data['registration_warnings']))
        self.pr.refresh_from_db()
        self.assertFalse(RequisitionWorkflowService.can_approve(self.pr, self.replacement))

    def test_browser_multipart_command_and_commercial_edit_save_atomically(self):
        response = self.client.patch(self.url, {
            'approval_reassignments': json.dumps([self.command()]),
            'product_service': 'Multipart browser edit',
        }, format='multipart')
        self.assertEqual(response.status_code, 200, response.data)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.approval_workflow_config[1]['user_id'], str(self.replacement.pk))
        self.assertEqual(self.pr.approval_workflow_config[0], self.workflow[0])
        self.assertEqual(self.pr.product_service, 'Multipart browser edit')
        self.assertEqual(len(self.pr.price_remarks_data[HISTORY_KEY]), 1)

    def test_malformed_multipart_commands_do_not_save_commercial_edit(self):
        response = self.client.patch(self.url, {
            'approval_reassignments': '[broken JSON', 'product_service': 'Must not save',
        }, format='multipart')
        self.assertEqual(response.status_code, 400, response.data)
        self.pr.refresh_from_db()
        self.assertNotEqual(self.pr.product_service, 'Must not save')
        self.assertEqual(self.pr.approval_workflow_config, self.workflow)
