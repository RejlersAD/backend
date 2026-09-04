from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.procurement.services.requisition_workflow import RequisitionWorkflowService


class FakeUser(SimpleNamespace):
    def get_full_name(self):
        return self.full_name


class FakeRequisition(SimpleNamespace):
    def save(self, *args, **kwargs):
        self.save_count = getattr(self, 'save_count', 0) + 1


class RequisitionWorkflowServiceTests(SimpleTestCase):
    def setUp(self):
        self.issuer = FakeUser(
            id='issuer',
            is_superuser=False,
            full_name='PR Issuer',
            username='issuer',
            email='issuer@example.com',
        )
        self.pm = FakeUser(
            id='pm-user',
            is_superuser=False,
            full_name='Project Manager',
            username='pm',
            email='pm@example.com',
        )
        self.engineering_manager = FakeUser(
            id='eng-user',
            is_superuser=False,
            full_name='Engineering Manager',
            username='eng',
            email='eng@example.com',
        )
        self.second_level_one = FakeUser(
            id='level-one-2',
            is_superuser=False,
            full_name='Second Level One Approver',
            username='levelone2',
            email='levelone2@example.com',
        )

    def _workflow(self):
        return [
            {'step': 1, 'role': 'Project Manager', 'user_id': self.pm.id, 'status': 'pending'},
            {
                'step': 2,
                'role': 'Engineering Manager',
                'user_id': self.engineering_manager.id,
                'status': 'pending',
            },
        ]

    def _pr(self, status='draft'):
        return FakeRequisition(
            status=status,
            issued_by_id=self.issuer.id,
            approval_workflow_config=self._workflow(),
            po_number_reference='',
            current_approval_step=0,
            items=[],
            total_price=None,
            rejection_reason='',
            approved_by=None,
            approved_at=None,
            pm_name=None,
            pm_signature='',
            pm_approval_status='pending',
            pm_approved_at=None,
            eng_manager_name=None,
            eng_manager_signature='',
            eng_manager_approval_status='pending',
            eng_manager_approved_at=None,
            manager_projects_name=None,
            manager_projects_signature='',
            manager_projects_approval_status='pending',
            manager_projects_approved_at=None,
            vp_op_name=None,
            vp_op_signature='',
            vp_op_approval_status='pending',
            vp_op_approved_at=None,
        )

    def test_submit_initializes_workflow(self):
        pr = self._pr()
        pr.approval_workflow_config[0].update({
            'status': 'approved',
            'approved_by_id': 'forged',
        })

        result = RequisitionWorkflowService._submit_locked(pr, self.issuer)

        self.assertIs(result, pr)
        self.assertEqual(pr.status, 'submitted')
        self.assertEqual(pr.current_approval_step, 0)
        self.assertEqual([stage['status'] for stage in pr.approval_workflow_config], ['pending', 'pending'])
        self.assertNotIn('approved_by_id', pr.approval_workflow_config[0])

    def test_level_zero_is_the_first_active_approval_level(self):
        workflow = [
            {'level': 0, 'role': 'Procurement Department', 'status': 'pending'},
            {'level': 1, 'role': 'Level 1 Approver', 'status': 'pending'},
        ]

        level, stages = RequisitionWorkflowService._active_level_stages(self._pr(), workflow)

        self.assertEqual(level, 0)
        self.assertEqual(stages[0][1]['role'], 'Procurement Department')

    @patch.object(RequisitionWorkflowService, '_notify_level')
    def test_submission_notifies_level_zero_first(self, notify_level):
        pr = self._pr()
        pr.approval_workflow_config = [
            {'level': 0, 'role': 'Procurement Department', 'user_id': self.pm.id},
            {'level': 1, 'role': 'Level 1 Approver', 'user_id': self.engineering_manager.id},
        ]

        RequisitionWorkflowService._submit_locked(pr, self.issuer)

        notify_level.assert_called_once_with(pr, pr.approval_workflow_config, 0)

    @patch.object(RequisitionWorkflowService, '_notify_level')
    def test_approval_notifies_every_new_active_level(self, notify_level):
        pr = self._pr(status='submitted')
        pr.approval_workflow_config = [
            {'level': 0, 'role': 'Procurement Department', 'user_id': self.pm.id, 'status': 'pending'},
            {'level': 1, 'role': 'Level 1 Approver', 'user_id': self.engineering_manager.id, 'status': 'pending'},
        ]

        RequisitionWorkflowService._approve_locked(pr, self.pm)

        notify_level.assert_called_once_with(
            pr,
            pr.approval_workflow_config,
            1,
            previous_approver='Project Manager',
            previous_level=0,
        )

    @patch('apps.notifications.services.NotificationService.create_notification')
    @patch('apps.notifications.models.Notification.objects.filter')
    @patch.object(RequisitionWorkflowService, '_resolve_stage_user')
    @patch(
        'apps.procurement.services.requisition_workflow.transaction.on_commit',
        side_effect=lambda callback: callback(),
    )
    def test_every_approver_at_active_level_receives_notification(
        self,
        _on_commit,
        resolve_stage_user,
        notification_filter,
        create_notification,
    ):
        first = FakeUser(pk='level-one-a', id='level-one-a')
        second = FakeUser(pk='level-one-b', id='level-one-b')
        resolve_stage_user.side_effect = [first, second]
        notification_filter.return_value.values_list.return_value = []
        pr = self._pr(status='submitted')
        pr.pk = 'pr-id'
        pr.pr_number = 'RAD-PRJ-PR-TEST_2026'
        workflow = [
            {'level': 1, 'user_id': first.id, 'status': 'pending'},
            {'level': 1, 'user_id': second.id, 'status': 'pending'},
            {'level': 2, 'user_id': 'later-user', 'status': 'pending'},
        ]

        RequisitionWorkflowService._notify_level(pr, workflow, 1)

        self.assertEqual(create_notification.call_count, 2)
        self.assertEqual(
            {call.kwargs['recipient'].id for call in create_notification.call_args_list},
            {'level-one-a', 'level-one-b'},
        )
        for call in create_notification.call_args_list:
            self.assertEqual(call.kwargs['action_url'], '/procurement/requisitions/pr-id')
            self.assertEqual(call.kwargs['action_label'], 'Open Request')
            self.assertTrue(call.kwargs['send_teams'])

    def test_migrated_assignment_uses_email_when_user_id_changed(self):
        stage = {
            'user_id': 'preproduction-user-id',
            'user_email': 'PM@EXAMPLE.COM',
        }

        self.assertTrue(RequisitionWorkflowService._stage_matches_user(stage, self.pm))
        self.assertFalse(
            RequisitionWorkflowService._stage_matches_user(stage, self.engineering_manager)
        )

    def test_no_po_submission_requires_level_zero_and_jarmo_level_five(self):
        pr = self._pr()
        pr.po_applicable = False

        with self.assertRaisesMessage(ValidationError, 'Level 5 Jarmo Suominen'):
            RequisitionWorkflowService._submit_locked(pr, self.issuer)

        pr.approval_workflow_config = [
            {'level': 0, 'role': 'Procurement Department', 'user_id': 'procurement'},
            {
                'level': 5,
                'role': 'CEO',
                'user_id': 'jarmo',
                'user_name': 'Jarmo Suominen',
            },
        ]
        result = RequisitionWorkflowService._submit_locked(pr, self.issuer)

        self.assertEqual(result.status, 'submitted')

    def test_po_reference_skips_jarmo_level_five_requirement(self):
        pr = self._pr()
        pr.po_applicable = False
        pr.po_number_reference = 'RAD-PRJ-PUR-0461_SEP2026'
        pr.approval_workflow_config = [
            {'level': 0, 'role': 'Procurement Department', 'user_id': 'procurement'},
            {
                'level': 5,
                'role': 'General Manager',
                'user_id': 'jarmo',
                'user_name': 'Jarmo Suominen',
            },
        ]

        result = RequisitionWorkflowService._submit_locked(pr, self.issuer)

        self.assertEqual(result.status, 'submitted')
        self.assertEqual(len(result.approval_workflow_config), 1)
        self.assertEqual(result.approval_workflow_config[0]['role'], 'Procurement Department')

    def test_only_issuer_can_submit(self):
        pr = self._pr()

        with self.assertRaises(PermissionDenied):
            RequisitionWorkflowService._submit_locked(pr, self.pm)

    def test_submission_requires_configured_approvers(self):
        pr = self._pr()
        pr.approval_workflow_config = []

        with self.assertRaisesMessage(ValidationError, 'configured approval workflow is required'):
            RequisitionWorkflowService._submit_locked(pr, self.issuer)

    def test_submission_rejects_line_item_total_mismatch(self):
        pr = self._pr()
        pr.items = [{
            'description': 'Engineering review',
            'quantity': 2,
            'unit_price': 100,
        }]
        pr.total_price = 250

        with self.assertRaises(ValidationError) as caught:
            RequisitionWorkflowService._submit_locked(pr, self.issuer)
        self.assertIn('sum of line items', str(caught.exception.detail['error']))

    def test_submission_retry_is_idempotent(self):
        pr = self._pr(status='submitted')

        result = RequisitionWorkflowService._submit_locked(pr, self.issuer)

        self.assertIs(result, pr)
        self.assertFalse(hasattr(pr, 'save_count'))

    def test_stages_advance_in_order_and_finish_as_approved(self):
        pr = self._pr(status='submitted')

        RequisitionWorkflowService._approve_locked(pr, self.pm, 'pm-signature', 'pm')

        self.assertEqual(pr.status, 'in_review')
        self.assertEqual(pr.current_approval_step, 1)
        self.assertEqual(pr.approval_workflow_config[0]['status'], 'approved')
        self.assertEqual(pr.pm_name, self.pm)
        self.assertEqual(pr.pm_signature, 'pm-signature')
        self.assertEqual(pr.pm_approval_status, 'approved')

        RequisitionWorkflowService._approve_locked(
            pr,
            self.engineering_manager,
            'eng-signature',
            'eng_manager',
        )

        self.assertEqual(pr.status, 'approved')
        self.assertEqual(pr.current_approval_step, 2)
        self.assertEqual(pr.approved_by, self.engineering_manager)
        self.assertEqual(pr.eng_manager_approval_status, 'approved')

    def test_later_stage_cannot_be_approved_early(self):
        pr = self._pr(status='submitted')

        with self.assertRaisesMessage(ValidationError, 'Project Manager must be completed next'):
            RequisitionWorkflowService._approve_locked(
                pr,
                self.engineering_manager,
                expected_stage_key='eng_manager',
            )

    def test_all_level_one_approvers_can_act_in_any_order_and_must_finish(self):
        pr = self._pr(status='submitted')
        pr.approval_workflow_config = [
            {
                'step': 1, 'level': 1, 'role': 'Level 1 Approver',
                'user_id': self.pm.id, 'status': 'pending',
            },
            {
                'step': 2, 'level': 1, 'role': 'Level 1 Approver',
                'user_id': self.second_level_one.id, 'status': 'pending',
            },
            {
                'step': 3, 'level': 2, 'role': 'Engineering Manager',
                'user_id': self.engineering_manager.id, 'status': 'pending',
            },
        ]

        RequisitionWorkflowService._approve_locked(
            pr, self.second_level_one, 'second-signature', 'pm'
        )

        self.assertEqual(pr.status, 'in_review')
        self.assertEqual(pr.current_approval_step, 0)
        self.assertEqual(pr.approval_workflow_config[0]['status'], 'pending')
        self.assertEqual(pr.approval_workflow_config[1]['status'], 'approved')

        with self.assertRaises(ValidationError):
            RequisitionWorkflowService._approve_locked(
                pr, self.engineering_manager, expected_stage_key='eng_manager'
            )

        RequisitionWorkflowService._approve_locked(pr, self.pm, 'first-signature', 'pm')

        self.assertEqual(pr.status, 'in_review')
        self.assertEqual(pr.current_approval_step, 2)

    def test_level_one_rejection_by_any_assigned_member_terminates_workflow(self):
        pr = self._pr(status='submitted')
        pr.approval_workflow_config = [
            {
                'step': 1, 'level': 1, 'role': 'Level 1 Approver',
                'user_id': self.pm.id, 'status': 'pending',
            },
            {
                'step': 2, 'level': 1, 'role': 'Level 1 Approver',
                'user_id': self.second_level_one.id, 'status': 'pending',
            },
        ]

        RequisitionWorkflowService._reject_locked(
            pr,
            self.second_level_one,
            'The requisition needs updated technical details.',
            'pm',
        )

        self.assertEqual(pr.status, 'rejected')
        self.assertEqual(pr.approval_workflow_config[1]['status'], 'rejected')

    def test_rejection_records_stage_audit_and_terminates_workflow(self):
        pr = self._pr(status='submitted')

        RequisitionWorkflowService._reject_locked(
            pr,
            self.pm,
            'The technical specification is incomplete.',
            'pm',
        )

        stage = pr.approval_workflow_config[0]
        self.assertEqual(pr.status, 'rejected')
        self.assertEqual(pr.pm_approval_status, 'not_approved')
        self.assertEqual(stage['status'], 'rejected')
        self.assertEqual(stage['rejected_by_id'], self.pm.id)
        self.assertEqual(stage['rejection_reason'], pr.rejection_reason)

    def test_completed_requisition_cannot_be_approved_again(self):
        pr = self._pr(status='approved')

        with self.assertRaisesMessage(ValidationError, 'not awaiting approval'):
            RequisitionWorkflowService._approve_locked(pr, self.pm)

    def test_converted_requisition_cannot_be_approved_before_recovery(self):
        pr = self._pr(status='converted')

        with self.assertRaisesMessage(ValidationError, 'not awaiting approval'):
            RequisitionWorkflowService._approve_locked(pr, self.pm)

    @patch.object(RequisitionWorkflowService, '_resolve_stage_user')
    @patch.object(RequisitionWorkflowService, '_notify_level')
    def test_resend_reactivates_missing_evidence_and_notifies_first_level(
        self,
        notify_level,
        resolve_stage_user,
    ):
        pr = self._pr(status='converted')
        pr.pk = 'pr-id'
        resolve_stage_user.side_effect = [self.pm, self.engineering_manager]

        result, count = RequisitionWorkflowService._resend_missing_approvals_locked(
            pr,
            self.issuer,
        )

        self.assertIs(result, pr)
        self.assertEqual(count, 2)
        self.assertEqual(pr.status, 'converted')
        self.assertTrue(all(stage.get('evidence_requested_at') for stage in pr.approval_workflow_config))
        notify_level.assert_called_once_with(pr, pr.approval_workflow_config, 1, force=True)

    @patch.object(RequisitionWorkflowService, '_notify_level')
    def test_recovered_approval_preserves_converted_status_and_advances(self, notify_level):
        pr = self._pr(status='converted')
        for stage in pr.approval_workflow_config:
            stage['evidence_requested_at'] = '2026-09-03T12:00:00+04:00'

        RequisitionWorkflowService._approve_locked(pr, self.pm, 'pm-signature', 'pm')

        self.assertEqual(pr.status, 'converted')
        self.assertEqual(pr.approval_workflow_config[0]['status'], 'approved')
        notify_level.assert_called_once_with(pr, pr.approval_workflow_config, 2, force=True)

    def test_recovery_does_not_bypass_a_rejected_decision(self):
        pr = self._pr(status='converted')
        pr.approval_workflow_config[0]['status'] = 'rejected'

        with self.assertRaisesMessage(ValidationError, 'contains a rejected decision'):
            RequisitionWorkflowService._resend_missing_approvals_locked(pr, self.issuer)
