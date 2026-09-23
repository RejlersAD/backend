"""Persisted PR approval notifications across creation, submission and decisions.

The workflow and NotificationService are real. Only external Celery delivery
boundaries are mocked, so recipient eligibility and saved notification state
are exercised together without sending mail or Microsoft Teams messages.
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.test import TestCase, override_settings

from apps.hr_core.models import EmployeeMaster
from apps.notifications.delivery import approval_assignment_issue
from apps.notifications.models import Notification
from apps.procurement.serializers import PurchaseRequisitionSerializer
from apps.procurement.services.requisition_workflow import RequisitionWorkflowService
from apps.rbac.models import UserProfile
from apps.timesheet.models import BiometricUserMaster

from .approval_fixtures import grant_approval, set_position


@override_settings(
    TEAMS_APPROVAL_WEBHOOK_URL='https://teams-delivery.example.test/test-only',
    WEB_PUSH_VAPID_PRIVATE_KEY='',
)
class PurchaseRequisitionCreationNotificationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        # Keep Teams' real queue function and all notification persistence logic.
        # The mocked task boundary guarantees this suite cannot send live alerts.
        patcher = patch('apps.notifications.teams.send_teams_approval_assignment.delay')
        self.teams_task = patcher.start()
        self.addCleanup(patcher.stop)
        for task in ('send_notification_email', 'send_web_push_notification'):
            patcher = patch(f'apps.notifications.services.{task}.delay')
            patcher.start()
            self.addCleanup(patcher.stop)

        users = get_user_model()
        self.issuer = users.objects.create_user('pr-notice-issuer', email='issuer@pr-notice.example.test')
        self.procurement = users.objects.create_user('pr-notice-procurement', email='procurement@pr-notice.example.test')
        self.level_one = users.objects.create_user('pr-notice-engineer', email='engineer@pr-notice.example.test')
        for user, position in (
            (self.issuer, 'Engineer'),
            (self.procurement, 'Procurement Manager'),
            (self.level_one, 'Engineer'),
        ):
            grant_approval(user, 'procurement_requisitions')
            set_position(user, position)

    def create_requisition(self, **changes):
        payload = {
            'pr_number': 'PR-CREATION-NOTIFICATION',
            'requisition_type': 'general',
            'po_applicable': True,
            'product_service': 'Foundation geotechnical investigation',
            'approval_workflow_config': [{
                'level': 0, 'role': 'Procurement Department',
                'user_id': str(self.procurement.pk), 'user_email': self.procurement.email,
            }, {
                'level': 1, 'role': 'Level 1 Approver',
                'user_id': str(self.level_one.pk), 'user_email': self.level_one.email,
            }],
        }
        payload.update(changes)
        serializer = PurchaseRequisitionSerializer(
            data=payload, context={'request': SimpleNamespace(user=self.issuer)},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        return serializer.save()

    @staticmethod
    def notices(requisition):
        return Notification.objects.filter(
            metadata__pr_id=str(requisition.pk), metadata__event_type='approval_assignment',
        )

    def submit(self, requisition):
        with self.captureOnCommitCallbacks(execute=True):
            return RequisitionWorkflowService.submit(requisition.pk, self.issuer)

    def test_draft_creation_does_not_send_approval_requests(self):
        with self.captureOnCommitCallbacks(execute=True):
            requisition = self.create_requisition()
        requisition.refresh_from_db()
        self.assertEqual(requisition.status, 'draft')
        self.assertFalse(self.notices(requisition).exists())
        self.teams_task.assert_not_called()

    def test_submit_persists_current_level_in_app_notice_and_queues_teams_after_commit(self):
        requisition = self.create_requisition()
        with self.captureOnCommitCallbacks(execute=True):
            submitted = RequisitionWorkflowService.submit(requisition.pk, self.issuer)
            self.assertEqual(submitted.status, 'submitted')
            self.assertFalse(self.notices(requisition).exists())
            self.teams_task.assert_not_called()

        notice = self.notices(requisition).get()
        self.assertEqual(notice.recipient_id, self.procurement.pk)
        self.assertEqual(notice.category.name, 'APPROVAL')
        self.assertEqual(notice.status, 'SENT')
        self.assertTrue(notice.send_in_app)
        self.assertFalse(notice.is_read)
        self.assertEqual(notice.metadata['approval_level'], 0)
        self.assertTrue(notice.metadata['requires_action'])
        self.assertEqual(approval_assignment_issue(notice), '')
        self.teams_task.assert_called_once()
        notice_id, context = self.teams_task.call_args.args
        self.assertEqual(notice_id, notice.pk)
        self.assertEqual(context['approval_level'], 0)
        self.assertIn(requisition.pr_number, context['request_name'])
        self.assertFalse(self.notices(requisition).filter(recipient=self.level_one).exists())

    def test_repeated_submit_does_not_duplicate_notification_or_teams_delivery(self):
        requisition = self.create_requisition()
        self.submit(requisition)
        first_notice_id = self.notices(requisition).get().pk
        self.teams_task.reset_mock()
        self.submit(requisition)
        self.assertEqual(self.notices(requisition).get().pk, first_notice_id)
        self.teams_task.assert_not_called()

    def test_level_one_receives_real_notice_only_after_level_zero_approval_commits(self):
        requisition = self.create_requisition()
        self.submit(requisition)
        self.teams_task.reset_mock()
        profile = self.procurement.rbac_profile
        profile.refresh_from_db()
        profile.signature_image = 'test-procurement-signature'
        profile.save(update_fields=['signature_image'])
        # A subsequent approval request authenticates a fresh user instance.
        procurement_actor = get_user_model().objects.get(pk=self.procurement.pk)

        with self.captureOnCommitCallbacks(execute=True):
            RequisitionWorkflowService.approve(requisition.pk, procurement_actor, require_signature=True)
            self.assertFalse(self.notices(requisition).filter(recipient=self.level_one).exists())
            self.teams_task.assert_not_called()

        notice = self.notices(requisition).get(recipient=self.level_one)
        self.assertEqual(notice.metadata['approval_level'], 1)
        self.assertEqual(notice.status, 'SENT')
        self.assertTrue(notice.send_in_app)
        self.assertEqual(approval_assignment_issue(notice), '')
        self.assertEqual(self.notices(requisition).count(), 2)
        self.teams_task.assert_called_once()
        self.assertEqual(self.teams_task.call_args.args[0], notice.pk)
        self.assertEqual(self.teams_task.call_args.args[1]['approval_level'], 1)

    def test_rolled_back_submit_cannot_persist_or_queue_approval_notifications(self):
        requisition = self.create_requisition()
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaisesMessage(RuntimeError, 'rollback submission'):
                with transaction.atomic():
                    RequisitionWorkflowService.submit(requisition.pk, self.issuer)
                    raise RuntimeError('rollback submission')
        requisition.refresh_from_db()
        self.assertEqual(requisition.status, 'draft')
        self.assertFalse(self.notices(requisition).exists())
        self.teams_task.assert_not_called()

    def test_teams_broker_failure_does_not_hide_the_saved_in_app_notice(self):
        requisition = self.create_requisition()
        self.teams_task.side_effect = RuntimeError('test-only broker unavailable')
        with self.assertLogs('apps.notifications.teams', level='ERROR'):
            self.submit(requisition)
        notice = self.notices(requisition).get(recipient=self.procurement)
        self.assertEqual(notice.status, 'SENT')
        self.assertTrue(notice.send_in_app)
        self.assertFalse(notice.is_read)
        self.teams_task.assert_called_once()

    def test_blank_level_zero_designation_warns_and_suppresses_both_channels(self):
        # An RBAC grant alone cannot confer the missing procurement position.
        set_position(self.procurement, '')
        requisition = self.create_requisition()
        warnings = PurchaseRequisitionSerializer(requisition).get_registration_warnings(requisition)
        self.assertTrue(any('Level 0' in warning and 'business position' in warning for warning in warnings), warnings)

        self.submit(requisition)
        requisition.refresh_from_db()
        self.assertEqual(requisition.status, 'submitted')
        self.assertFalse(RequisitionWorkflowService.can_approve(requisition, self.procurement))
        # Level 1 remains pending too; it cannot bypass the unresolved Level 0.
        self.assertFalse(RequisitionWorkflowService.can_approve(requisition, self.level_one))
        self.assertFalse(self.notices(requisition).exists())
        self.teams_task.assert_not_called()

    def profile_loaded_before_hr_position_update(self):
        employee = EmployeeMaster.objects.get(user=self.procurement)
        employee.designation = ''
        employee.job_title_uae = ''
        employee.job_title_finland = ''
        employee.save(update_fields=['designation', 'job_title_uae', 'job_title_finland'])
        stale_profile = UserProfile.objects.get(user=self.procurement)
        self.assertEqual(stale_profile.job_title, '')

        # HR saves the official position after the signature request loaded its
        # profile. Real EmployeeMaster signals update the database profile too.
        employee.designation = 'Procurement Manager'
        employee.job_title_uae = 'Procurement Manager'
        employee.save(update_fields=['designation', 'job_title_uae'])
        self.assertEqual(UserProfile.objects.get(pk=stale_profile.pk).job_title, 'Procurement Manager')
        self.assertEqual(stale_profile.job_title, '')
        return employee, stale_profile

    def test_stale_signature_save_preserves_canonical_position_and_approval_notifications(self):
        employee, stale_profile = self.profile_loaded_before_hr_position_update()
        stale_profile.signature_image = 'test-only-stale-profile-signature'
        stale_profile.save(update_fields=['signature_image'])
        requisition = self.create_requisition()
        self.submit(requisition)

        employee.refresh_from_db()
        self.assertEqual(employee.designation, 'Procurement Manager')
        self.assertEqual(employee.job_title_uae, 'Procurement Manager')
        notice = self.notices(requisition).get(recipient=self.procurement)
        self.assertEqual(notice.status, 'SENT')
        self.assertTrue(notice.send_in_app)
        self.assertFalse(notice.is_read)
        self.teams_task.assert_called_once()
        self.assertEqual(self.teams_task.call_args.args[0], notice.pk)

    def test_partial_employee_id_sync_uses_saved_position_and_preserves_approval_notifications(self):
        employee, stale_profile = self.profile_loaded_before_hr_position_update()
        stale_profile.employee_id = 'PR-NOTICE-CANONICAL'
        stale_profile.save(update_fields=['employee_id'])
        employee.refresh_from_db()
        self.assertEqual(employee.employee_number, 'PR-NOTICE-CANONICAL')
        self.assertEqual(employee.designation, 'Procurement Manager')
        biometric = BiometricUserMaster.objects.get(employee_code='PR-NOTICE-CANONICAL')
        self.assertEqual(biometric.designation, 'Procurement Manager')

        requisition = self.create_requisition()
        self.submit(requisition)
        notice = self.notices(requisition).get(recipient=self.procurement)
        self.assertEqual(notice.status, 'SENT')
        self.teams_task.assert_called_once()
        self.assertEqual(self.teams_task.call_args.args[0], notice.pk)
