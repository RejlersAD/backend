"""Converted PR recovery explains failed gates without rewriting audit history."""

from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.hr_core.models import EmployeeMaster
from apps.procurement.models import PurchaseRequisition
from apps.procurement.services.requisition_workflow import RequisitionWorkflowService
from apps.rbac.models import Permission, UserPermissionOverride, UserProfile, UserRole

from .approval_fixtures import grant_approval, set_position


@override_settings(TEAMS_APPROVAL_WEBHOOK_URL='', WEB_PUSH_VAPID_PRIVATE_KEY='')
class RequisitionRecoveryDiagnosticTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.issuer = users.objects.create_user('recovery-issuer', email='issuer@recovery.example.test')
        self.operations = users.objects.create_user('recovery-operations', email='operations@recovery.example.test')
        self.ceo = users.objects.create_user('recovery-ceo', email='ceo@recovery.example.test')
        for user, title in (
            (self.operations, 'Chief Operating Officer & VP, Head of Operations & Project Delivery'),
            (self.ceo, 'CEO'),
        ):
            grant_approval(user)
            set_position(user, title)
        self.approved = {
            'level': 0, 'role': 'Procurement', 'stage': 'Procurement Review',
            'user_id': 'historical-user-id', 'user_email': 'former-employee@example.test',
            'status': 'approved', 'assignment_id': 'approved-assignment',
            'approved_by_id': 'historical-user-id', 'approved_at': '2026-09-01T09:00:00Z',
            'signature': 'saved-approval-evidence',
        }
        self.pr = PurchaseRequisition.objects.create(
            pr_number='PR-RECOVERY-DIAGNOSTICS', issued_by=self.issuer,
            status='converted', po_applicable=False, current_approval_step=0,
            approval_workflow_config=[
                deepcopy(self.approved),
                self.stage(self.operations, 4, 'VP Delivery'),
                self.stage(self.ceo, 5, 'CEO'),
            ],
        )

    @staticmethod
    def stage(user, level, role):
        return {
            'level': level, 'role': role, 'stage': f'Level {level} - {role}',
            'user_id': str(user.pk), 'user_email': user.email,
            'assignment_id': f'recovery-assignment-{level}', 'status': 'not_recorded',
        }

    def assert_recovery_error(self, code, label='CEO'):
        self.pr.save(update_fields=['approval_workflow_config'])
        original = deepcopy(self.pr.approval_workflow_config)
        previous_step = self.pr.current_approval_step
        previous_updated_at = self.pr.updated_at
        with patch.object(RequisitionWorkflowService, '_notify_level') as notify:
            with patch.object(self.pr, 'save', wraps=self.pr.save) as save:
                with self.assertRaises(ValidationError) as caught:
                    RequisitionWorkflowService._resend_missing_approvals_locked(self.pr, self.issuer)
                save.assert_not_called()
            notify.assert_not_called()
        self.assertEqual(self.pr.approval_workflow_config, original)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.approval_workflow_config, original)
        self.assertEqual(self.pr.approval_workflow_config[0], self.approved)
        self.assertEqual(self.pr.current_approval_step, previous_step)
        self.assertEqual(self.pr.updated_at, previous_updated_at)
        self.assertEqual(self.pr.status, 'converted')
        detail = caught.exception.detail
        self.assertEqual(len(detail['approval_assignment_errors']), 1)
        issue = detail['approval_assignment_errors'][0]
        self.assertEqual(issue['code'], code)
        self.assertEqual(issue['stage'], label)
        self.assertIn(f'{label}:', detail['error'])
        self.assertIn(issue['reason'], detail['error'])
        return str(detail['error'])

    @patch.object(RequisitionWorkflowService, '_notify_level')
    def test_official_operations_title_resends_only_unresolved_stages_without_reassigning_ceo(self, notify):
        original = deepcopy(self.pr.approval_workflow_config)
        result, count = RequisitionWorkflowService.resend_missing_approvals(self.pr.pk, self.issuer)
        self.assertEqual(count, 2)
        self.assertEqual(result.status, 'converted')
        self.assertEqual(result.current_approval_step, 1)
        self.assertEqual(result.approval_workflow_config[0], self.approved)
        for before, after in zip(original[1:], result.approval_workflow_config[1:]):
            self.assertEqual(after['user_id'], before['user_id'])
            self.assertEqual(after['user_email'], before['user_email'])
            self.assertEqual(after['assignment_id'], before['assignment_id'])
            self.assertEqual(after['status'], 'pending')
            self.assertTrue(after['evidence_requested_at'])
        notify.assert_called_once_with(result, result.approval_workflow_config, 4, force=True)

    def test_wrong_ceo_position_is_not_reported_as_an_inactive_employee(self):
        EmployeeMaster.objects.filter(user=self.ceo).update(designation='Engineer')
        error = self.assert_recovery_error('business_position_mismatch')
        self.assertIn('official HR position', error)
        self.assertNotIn('inactive', error)
        self.assertNotIn('VP Delivery:', error)

    def test_missing_approval_grant_is_reported_as_permission_not_inactivity(self):
        UserRole.objects.filter(user_profile=self.ceo.rbac_profile).delete()
        error = self.assert_recovery_error('missing_approval_permission')
        self.assertIn('permission', error)
        self.assertNotIn('inactive', error)

    def test_missing_assignment_is_distinguished_from_a_missing_account(self):
        self.pr.approval_workflow_config[2].update(user_id='', user_email='')
        self.assert_recovery_error('missing_assignment')

    def test_missing_authoritative_email_does_not_fall_back_to_a_valid_user_id(self):
        self.pr.approval_workflow_config[2]['user_email'] = 'not-registered@example.test'
        self.assert_recovery_error('missing_account')

    def test_invalid_legacy_user_id_is_reported_without_a_database_error(self):
        self.pr.approval_workflow_config[2].update(user_email='', user_id='invalid-user-id')
        self.assert_recovery_error('missing_account')

    def test_inactive_account_is_reported_as_inactive(self):
        get_user_model().objects.filter(pk=self.ceo.pk).update(is_active=False)
        self.assert_recovery_error('inactive_account')

    def test_duplicate_case_insensitive_active_accounts_are_reported_as_ambiguous(self):
        get_user_model().objects.create_user('duplicate-recovery-ceo', email=self.ceo.email.upper())
        self.assert_recovery_error('ambiguous_account')

    def test_locked_account_is_distinguished_from_missing_approval_permission(self):
        UserProfile.objects.filter(user=self.ceo).update(locked_until=timezone.now() + timedelta(hours=1))
        self.assert_recovery_error('locked_account')

    def test_inactive_employment_is_distinguished_from_position_mismatch(self):
        EmployeeMaster.objects.filter(user=self.ceo).update(employment_status='terminated')
        self.assert_recovery_error('inactive_employee')

    def test_missing_canonical_employee_record_does_not_imply_the_account_is_inactive(self):
        EmployeeMaster.objects.filter(user=self.ceo).delete()
        error = self.assert_recovery_error('missing_employee_record')
        self.assertNotIn('inactive', error)

    def test_failed_recovery_does_not_persist_resolved_legacy_ids_or_existing_decisions(self):
        self.pr.approval_workflow_config[1]['user_id'] = 'legacy-operations-user-id'
        EmployeeMaster.objects.filter(user=self.ceo).update(designation='Engineer')
        self.assert_recovery_error('business_position_mismatch')
        self.assertEqual(self.pr.approval_workflow_config[1]['user_id'], 'legacy-operations-user-id')

    @patch.object(RequisitionWorkflowService, '_notify_level')
    def test_selected_level_one_employee_does_not_require_a_module_wide_approval_grant(self, notify):
        self.pr.approval_workflow_config[1] = self.stage(self.operations, 1, 'Level 1 Approver')
        self.pr.approval_workflow_config[1]['stage'] = 'Level 1'
        self.pr.save(update_fields=['approval_workflow_config'])
        UserRole.objects.filter(user_profile=self.operations.rbac_profile).delete()
        result, count = RequisitionWorkflowService.resend_missing_approvals(self.pr.pk, self.issuer)
        self.assertEqual(count, 2)
        notify.assert_called_once_with(result, result.approval_workflow_config, 1, force=True)

    def test_selected_level_one_employee_explicit_deny_is_reported_as_permission(self):
        self.pr.approval_workflow_config[1] = self.stage(self.operations, 1, 'Level 1 Approver')
        self.pr.approval_workflow_config[1]['stage'] = 'Level 1'
        permission = Permission.objects.filter(module__code='procurement_requisitions', action='approve').first()
        UserPermissionOverride.objects.create(
            user_profile=self.operations.rbac_profile, permission=permission, allowed=False,
        )
        self.assert_recovery_error('missing_approval_permission', label='Level 1 Approver')
