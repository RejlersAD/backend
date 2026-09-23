from copy import deepcopy
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.notifications.models import Notification
from .approval_fixtures import grant_approval, set_position
from apps.procurement.services.requisition_workflow import (
    RequisitionWorkflowService,
    notify_requisition_approver_changes,
)
from apps.procurement.tests.test_requisition_workflow_service import FakeRequisition, FakeUser


SERVICE_PATH = 'apps.procurement.services.requisition_workflow'


def requisition(workflow, status='submitted'):
    return FakeRequisition(
        pk='notification-sequence-pr',
        pr_number='PR-SEQUENCE',
        status=status,
        issued_by_id='issuer',
        issued_by=None,
        po_number_reference='',
        po_applicable=False,
        approval_workflow_config=workflow,
        current_approval_step=0,
        items=[],
        total_price=0,
        rejection_reason='',
    )


class RequisitionNotificationSequenceTests(SimpleTestCase):
    def setUp(self):
        authorization = patch(f'{SERVICE_PATH}.eligible_stage_assignee', return_value=True)
        authorization.start()
        self.addCleanup(authorization.stop)
        self.users = {
            key: FakeUser(
                id=key, pk=key, is_superuser=False,
                full_name='Jarmo Suominen' if key == 'ceo' else key,
                email=f'{key}@example.com',
            )
            for key in ('issuer', 'zero', 'one-a', 'one-b', 'two', 'three', 'four', 'ceo', 'replacement')
        }
        # Deliberately unordered, with two approvers at Level 1.
        self.workflow = [
            self.stage(5, 'ceo', role='CEO', user_name='Jarmo Suominen'),
            self.stage(2, 'two'),
            self.stage(1, 'one-b'),
            self.stage(4, 'four'),
            self.stage(0, 'zero'),
            self.stage(3, 'three'),
            self.stage(1, 'one-a'),
        ]
        self.create_notification = self.start_patch(
            'apps.notifications.services.NotificationService.create_notification',
        )
        self.notification_filter = self.start_patch(
            'apps.notifications.models.Notification.objects.filter',
        )
        self.notification_filter.return_value.exclude.return_value = self.notification_filter.return_value
        self.notification_filter.return_value.values_list.return_value = []
        self.start_patch(f'{SERVICE_PATH}.transaction.on_commit', side_effect=lambda callback: callback())
        self.start_patch(
            f'{SERVICE_PATH}.employee_display_name', side_effect=lambda user: user.full_name,
        )
        self.start_patch(
            f'{SERVICE_PATH}.requisition_teams_context',
            side_effect=lambda pr, **kwargs: {'request_name': pr.pr_number, **kwargs},
        )
        self.start_patch(
            f'{SERVICE_PATH}.RequisitionWorkflowService._resolve_stage_user',
            side_effect=lambda stage: self.users.get(stage.get('user_id')),
        )

    def start_patch(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    @staticmethod
    def stage(level, user_id, **kwargs):
        return {'level': level, 'user_id': user_id, 'status': 'pending', **kwargs}

    def notified_levels(self):
        return [
            call.kwargs['metadata']['approval_level']
            for call in self.create_notification.call_args_list
        ]

    def test_unordered_workflow_notifies_zero_through_ceo_after_each_level_finishes(self):
        pr = requisition(self.workflow, status='draft')

        RequisitionWorkflowService._submit_locked(pr, self.users['issuer'])

        self.assertEqual(self.notified_levels(), [0])
        self.assertEqual(pr.current_approval_step, 4)
        with self.assertRaises(PermissionDenied):
            RequisitionWorkflowService._approve_locked(pr, self.users['one-a'])

        RequisitionWorkflowService._approve_locked(pr, self.users['zero'])
        self.assertEqual(self.notified_levels(), [0, 1, 1])
        RequisitionWorkflowService._approve_locked(pr, self.users['one-b'])
        self.assertEqual(self.notified_levels(), [0, 1, 1])
        with self.assertRaises(PermissionDenied):
            RequisitionWorkflowService._approve_locked(pr, self.users['two'])

        for user_id, next_level in [('one-a', 2), ('two', 3), ('three', 4), ('four', 5)]:
            RequisitionWorkflowService._approve_locked(pr, self.users[user_id])
            self.assertEqual(self.notified_levels()[-1], next_level)
            self.assertEqual(
                pr.approval_workflow_config[pr.current_approval_step]['level'], next_level,
            )

        RequisitionWorkflowService._approve_locked(pr, self.users['ceo'])

        self.assertEqual(self.notified_levels(), [0, 1, 1, 2, 3, 4, 5])
        self.assertEqual(pr.status, 'approved')
        self.assertTrue(all(stage['status'] == 'approved' for stage in pr.approval_workflow_config))
        for call in self.create_notification.call_args_list:
            self.assertEqual(call.kwargs['metadata']['event_type'], 'approval_assignment')
            self.assertTrue(call.kwargs['metadata']['requires_action'])
            self.assertTrue(call.kwargs['send_teams'])
            self.assertEqual(
                call.kwargs['teams_context']['approval_level'],
                call.kwargs['metadata']['approval_level'],
            )

    def test_assignment_edits_do_not_notify_draft_or_closed_requests(self):
        for status in ('draft', 'approved', 'rejected', 'converted'):
            with self.subTest(status=status):
                notify_requisition_approver_changes(requisition(self.workflow, status), [])
        self.create_notification.assert_not_called()

    def test_future_assignment_edit_waits_until_its_level_becomes_active(self):
        pr = requisition(self.workflow)
        previous = deepcopy(self.workflow)
        pr.approval_workflow_config[1]['user_id'] = 'replacement'
        self.notification_filter.return_value.values_list.return_value = ['zero']

        notify_requisition_approver_changes(pr, previous)

        self.create_notification.assert_not_called()
        self.assertEqual(self.notification_filter.call_args.kwargs['recipient_id__in'], {'zero'})

    def test_current_assignment_edit_notifies_replacement_at_level_zero(self):
        pr = requisition(self.workflow)
        previous = deepcopy(self.workflow)
        pr.approval_workflow_config[4]['user_id'] = 'replacement'

        notify_requisition_approver_changes(pr, previous)

        self.create_notification.assert_called_once()
        self.assertEqual(self.create_notification.call_args.kwargs['recipient'].id, 'replacement')
        self.assertEqual(self.notified_levels(), [0])

    def test_sender_does_not_send_a_future_level_even_when_requested_directly(self):
        RequisitionWorkflowService._notify_level(requisition(self.workflow), self.workflow, 5)
        self.create_notification.assert_not_called()

    def test_rejected_decision_stops_edits_notifications_and_progression(self):
        pr = requisition(self.workflow)
        RequisitionWorkflowService._reject_locked(
            pr, self.users['zero'], 'The request needs revised scope and pricing.',
        )
        notify_requisition_approver_changes(pr, [])
        RequisitionWorkflowService._notify_level(pr, pr.approval_workflow_config, 1)
        # Also reject inconsistent legacy rows whose top-level status stayed active.
        pr.status = 'submitted'
        notify_requisition_approver_changes(pr, [])
        with self.assertRaisesMessage(ValidationError, 'contains a rejected decision'):
            RequisitionWorkflowService._approve_locked(pr, self.users['one-a'])
        self.create_notification.assert_not_called()


@override_settings(TEAMS_APPROVAL_WEBHOOK_URL='', WEB_PUSH_VAPID_PRIVATE_KEY='')
class RequisitionNotificationDeduplicationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='notification-sequence-approver', email='sequence@example.com',
        )
        grant_approval(self.user)
        set_position(self.user)
        self.workflow = [{'level': 0, 'user_id': str(self.user.pk), 'status': 'pending', 'business_position': 'engineer'}]
        self.pr = requisition(self.workflow)

    def notify(self):
        with (
            patch(f'{SERVICE_PATH}.requisition_teams_context', return_value={'approval_level': 0}),
            patch('apps.notifications.services.NotificationService.create_notification') as create,
            self.captureOnCommitCallbacks(execute=True),
        ):
            RequisitionWorkflowService._notify_level(self.pr, self.workflow, 0)
        return create

    def existing_notification(self, **metadata):
        return Notification.objects.create(
            recipient=self.user,
            title='Previous assignment',
            message='Previous assignment message',
            metadata={'pr_id': str(self.pr.pk), 'approval_level': 0, **metadata},
        )

    def test_legacy_early_assignment_notice_does_not_suppress_real_approval_request(self):
        self.existing_notification(assignment_updated=True)

        create = self.notify()

        create.assert_called_once()
        self.assertEqual(create.call_args.kwargs['recipient'], self.user)
        self.assertEqual(create.call_args.kwargs['metadata']['approval_level'], 0)

    def test_existing_actionable_notifications_remain_deduplicated(self):
        for metadata in ({'requires_action': True}, {'event_type': 'approval_assignment'}):
            with self.subTest(metadata=metadata):
                notification = self.existing_notification(**metadata)
                self.notify().assert_not_called()
                notification.delete()

    def test_returning_active_assignee_gets_new_request_without_renotifying_peer(self):
        replacement = get_user_model().objects.create_user(
            username='replacement-approver', email='replacement@example.com',
        )
        peer = get_user_model().objects.create_user(
            username='same-level-peer', email='peer@example.com',
        )
        for user in (replacement, peer):
            grant_approval(user)
            set_position(user)
        self.workflow.append({'level': 0, 'user_id': str(peer.pk), 'status': 'pending', 'business_position': 'engineer'})

        def save_notification(**kwargs):
            return Notification.objects.create(
                recipient=kwargs['recipient'], title=kwargs['title'],
                message=kwargs['message'], metadata=kwargs['metadata'],
            )

        with (
            patch(f'{SERVICE_PATH}.requisition_teams_context', return_value={'approval_level': 0}),
            patch(
                'apps.notifications.services.NotificationService.create_notification',
                side_effect=save_notification,
            ) as create,
        ):
            with self.captureOnCommitCallbacks(execute=True):
                RequisitionWorkflowService._notify_level(self.pr, self.workflow, 0)
            for assignee in (replacement, self.user):
                previous = deepcopy(self.pr.approval_workflow_config)
                self.pr.approval_workflow_config[0]['user_id'] = str(assignee.pk)
                with self.captureOnCommitCallbacks(execute=True):
                    notify_requisition_approver_changes(self.pr, previous)
            with self.captureOnCommitCallbacks(execute=True):
                notify_requisition_approver_changes(
                    self.pr, deepcopy(self.pr.approval_workflow_config),
                )

        self.assertEqual(
            [call.kwargs['recipient'].pk for call in create.call_args_list],
            [self.user.pk, peer.pk, replacement.pk, self.user.pk],
        )
        self.assertTrue(create.call_args.kwargs['metadata']['assignment_updated'])

    def test_matching_email_does_not_resend_when_migrated_user_id_is_corrected(self):
        self.existing_notification(event_type='approval_assignment')
        self.workflow[0]['user_email'] = self.user.email
        previous = deepcopy(self.workflow)
        previous[0]['user_id'] = 'old-environment-id'
        previous[0]['user_email'] = self.user.email.upper()

        with (
            patch(f'{SERVICE_PATH}.requisition_teams_context', return_value={'approval_level': 0}),
            patch('apps.notifications.services.NotificationService.create_notification') as create,
            self.captureOnCommitCallbacks(execute=True),
        ):
            notify_requisition_approver_changes(self.pr, previous)

        create.assert_not_called()
