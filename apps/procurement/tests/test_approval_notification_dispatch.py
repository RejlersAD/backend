from copy import deepcopy
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase, override_settings

from apps.notifications.models import Notification
from apps.procurement.models import PurchaseOrder, PurchaseRequisition, Vendor
from apps.procurement.services.purchase_order_approvals import (
    _resolve_entry_user,
    notify_assigned_approvers,
)
from apps.procurement.services.requisition_workflow import RequisitionWorkflowService
from apps.rbac.models import Organization, UserProfile


@override_settings(TEAMS_APPROVAL_WEBHOOK_URL='', WEB_PUSH_VAPID_PRIVATE_KEY='')
class ApprovalNotificationDispatchTests(TestCase):
    def setUp(self):
        organization = Organization.objects.create(code='DISPATCH', name='Approval dispatch tests')
        self.people = []
        self.profiles = []
        for name in ('alpha', 'bravo'):
            user = get_user_model().objects.create_user(
                username=f'dispatch-{name}', email=f'{name}@dispatch.example', first_name=name,
            )
            profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': organization})
            profile.status = 'active'
            profile.is_deleted = False
            profile.save(update_fields=['status', 'is_deleted'])
            self.people.append(user)
            self.profiles.append(profile)
        self.pr = PurchaseRequisition.objects.create(
            pr_number='PR-DISPATCH', title='Dispatch test', issued_by=self.people[1],
            status='submitted', po_applicable=False,
            approval_workflow_config=[self.stage(self.people[0])],
        )
        vendor = Vendor.objects.create(vendor_code='DISPATCH', name='Dispatch supplier')
        self.po = PurchaseOrder.objects.create(
            po_number='PO-DISPATCH', title='Dispatch test', vendor=vendor,
            created_by=self.people[1], total_amount='100.00',
            approval_log=[self.stage(self.people[0])],
        )
        patcher = patch(
            'apps.notifications.services.NotificationService.create_notification',
            side_effect=self.save_notification,
        )
        self.create_notification = patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def stage(user, assignment_id='initial'):
        return {
            'level': 0, 'role': 'Procurement Manager', 'stage': 'Procurement Review',
            'user_id': str(user.pk), 'user_email': user.email,
            'approver_email': user.email, 'status': 'pending',
            'assignment_id': assignment_id,
        }

    @staticmethod
    def save_notification(**kwargs):
        return Notification.objects.create(**{
            field: kwargs[field] for field in ('recipient', 'title', 'message', 'metadata')
        })

    def schedule_pr(self):
        RequisitionWorkflowService._notify_level(self.pr, self.pr.approval_workflow_config, 0)

    def test_pr_waits_for_commit_and_drops_reassigned_callback(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.schedule_pr()
            self.create_notification.assert_not_called()
            PurchaseRequisition.objects.filter(pk=self.pr.pk).update(
                approval_workflow_config=[self.stage(self.people[1], 'replacement')],
            )
        self.create_notification.assert_not_called()

    def test_pr_callback_drops_superseded_assignment_even_when_same_employee_returns(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.schedule_pr()
            PurchaseRequisition.objects.filter(pk=self.pr.pk).update(
                approval_workflow_config=[self.stage(self.people[0], 'returning-assignment')],
            )
        self.create_notification.assert_not_called()

    def test_pr_assignment_token_gets_one_alert_after_older_assignment_was_notified(self):
        Notification.objects.create(
            recipient=self.people[0], title='Old assignment', message='Old assignment',
            metadata={'pr_id': str(self.pr.pk), 'approval_level': 0, 'requires_action': True,
                      'assignment_id': 'previous-assignment'},
        )
        with self.captureOnCommitCallbacks(execute=True):
            self.schedule_pr()
        with self.captureOnCommitCallbacks(execute=True):
            self.schedule_pr()
        self.create_notification.assert_called_once()
        self.assertEqual(self.create_notification.call_args.kwargs['metadata']['assignment_id'], 'initial')

    def test_pr_callback_drops_closed_or_deleted_request(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.schedule_pr()
            PurchaseRequisition.objects.filter(pk=self.pr.pk).update(status='rejected')
        self.create_notification.assert_not_called()
        self.pr.status = 'submitted'
        self.pr.save(update_fields=['status'])
        with self.captureOnCommitCallbacks(execute=True):
            self.schedule_pr()
            PurchaseRequisition.objects.filter(pk=self.pr.pk).delete()
        self.create_notification.assert_not_called()

    def test_rolled_back_pr_transition_never_creates_notification(self):
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaises(RuntimeError):
                with transaction.atomic():
                    self.schedule_pr()
                    raise RuntimeError('Rollback before commit')
        self.create_notification.assert_not_called()

    def test_profile_suspension_between_queueing_and_commit_suppresses_pr_alert(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.schedule_pr()
            UserProfile.objects.filter(pk=self.profiles[0].pk).update(status='suspended')
        self.create_notification.assert_not_called()

    def test_disabled_profiles_receive_neither_pr_nor_po_assignment_alerts(self):
        for changes in ({'status': 'suspended'}, {'status': 'inactive'}, {'is_deleted': True}):
            with self.subTest(changes=changes):
                UserProfile.objects.filter(pk=self.profiles[0].pk).update(status='active', is_deleted=False)
                UserProfile.objects.filter(pk=self.profiles[0].pk).update(**changes)
                with self.captureOnCommitCallbacks(execute=True):
                    self.schedule_pr()
                notify_assigned_approvers(self.po)
        self.create_notification.assert_not_called()

    def test_case_variant_duplicate_emails_never_pick_an_arbitrary_recipient(self):
        self.people[1].email = self.people[0].email.upper()
        self.people[1].save(update_fields=['email'])
        stage = self.stage(self.people[0])
        self.assertIsNone(RequisitionWorkflowService._resolve_stage_user(dict(stage)))
        self.assertIsNone(_resolve_entry_user(dict(stage)))
        with self.captureOnCommitCallbacks(execute=True):
            self.schedule_pr()
        notify_assigned_approvers(self.po)
        self.create_notification.assert_not_called()

    def test_po_sender_refreshes_stale_assignment_and_notifies_only_current_employee(self):
        PurchaseOrder.objects.filter(pk=self.po.pk).update(
            approval_log=[self.stage(self.people[1], 'replacement')],
        )
        notify_assigned_approvers(self.po)
        self.create_notification.assert_called_once()
        self.assertEqual(self.create_notification.call_args.kwargs['recipient'].pk, self.people[1].pk)
        self.assertEqual(self.create_notification.call_args.kwargs['metadata']['assignment_id'], 'replacement')

    def test_po_delivery_does_not_overwrite_decision_committed_while_repairing_legacy_id(self):
        stage = self.stage(self.people[0])
        stage['user_id'] = str(self.people[1].pk)
        self.po.approval_log = [stage]
        self.po.save(update_fields=['approval_log'])
        decided = deepcopy(stage)
        decided.update(status='approved', approved_by_email=self.people[0].email)

        def concurrent_decision(**kwargs):
            PurchaseOrder.objects.filter(pk=self.po.pk).update(approval_log=[decided])
            return self.save_notification(**kwargs)

        self.create_notification.side_effect = concurrent_decision
        notify_assigned_approvers(self.po)
        self.po.refresh_from_db()
        self.assertEqual(self.po.approval_log, [decided])
