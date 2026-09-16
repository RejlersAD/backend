"""Assignment, canonical position, effective approval access and stage are conjunctive."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.hr_core.models import EmployeeMaster
from apps.notifications.delivery import approval_assignment_issue
from apps.notifications.models import Notification
from apps.procurement.models import PurchaseOrder, PurchaseRequisition, Vendor
from apps.procurement.services import purchase_order_approvals as po
from apps.procurement.services.requisition_workflow import RequisitionWorkflowService as pr
from apps.procurement.services.approval_eligibility import eligible_stage_assignee, stage_positions, MODULE_PO
from apps.rbac.models import Organization, UserProfile, UserRole
from .approval_fixtures import grant_approval, set_position


@override_settings(TEAMS_APPROVAL_WEBHOOK_URL='', WEB_PUSH_VAPID_PRIVATE_KEY='')
class ProcurementThreeGateTests(TestCase):
    def setUp(self):
        org = Organization.objects.create(name='Three approval gates', code='THREE-GATES')
        self.users = []
        for index, position in enumerate(('Procurement Manager', 'CEO', 'Engineer')):
            user = get_user_model().objects.create_user(
                username=f'gate-user-{index}', email=f'gate-user-{index}@example.test', is_superuser=index == 1,
            )
            profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': org})
            profile.signature_image = f'signature-{index}'
            profile.save(update_fields=['signature_image'])
            grant_approval(user)
            set_position(user, position)
            self.users.append(user)
        self.procurement, self.ceo, self.engineer = self.users
        self.route = [self.stage(self.procurement, 0, 'Procurement'), self.stage(self.ceo, 5, 'CEO')]
        vendor = Vendor.objects.create(vendor_code='THREE-GATES', name='Three gates vendor')
        self.order = PurchaseOrder.objects.create(
            po_number='PO-THREE-GATES', vendor=vendor, title='Position and access', total_amount='10',
            created_by=self.engineer, approval_log=self.route,
        )
        self.requisition = PurchaseRequisition.objects.create(
            pr_number='PR-THREE-GATES', issued_by=self.engineer, status='submitted', po_applicable=True,
            approval_workflow_config=self.route,
        )

    def stage(self, user, level, label):
        return {'level': level, 'stage': label, 'role': label, 'user_id': str(user.pk),
                'approver_email': user.email, 'status': 'pending', 'assignment_id': f'gate-{level}'}

    def test_assigned_position_and_permission_still_cannot_skip_to_ceo(self):
        self.assertTrue(po.can_approve(self.order, self.procurement))
        self.assertTrue(pr.can_approve(self.requisition, self.procurement))
        self.assertFalse(po.can_approve(self.order, self.ceo))
        self.assertFalse(pr.can_approve(self.requisition, self.ceo))
        with self.assertRaises(PermissionDenied):
            po.record_decision(self.order, self.ceo, 'approve')
        with self.assertRaises(PermissionDenied):
            pr.approve(self.requisition.pk, self.ceo)

    def test_missing_effective_permission_blocks_capabilities_actions_and_senders(self):
        UserRole.objects.filter(user_profile=self.procurement.rbac_profile).delete()
        self.assertFalse(po.can_approve(self.order, self.procurement))
        self.assertFalse(pr.can_approve(self.requisition, self.procurement))
        for action in (
            lambda: po.record_decision(self.order, self.procurement, 'approve'),
            lambda: pr.approve(self.requisition.pk, self.procurement),
        ):
            with self.assertRaises(PermissionDenied):
                action()
        with patch('apps.notifications.services.NotificationService.create_notification') as create:
            po.notify_assigned_approvers(self.order)
            with self.captureOnCommitCallbacks(execute=True):
                pr._notify_level(self.requisition, self.route, 0)
            create.assert_not_called()

    def test_parallel_assignment_does_not_authorize_notice_for_another_business_position(self):
        financial = self.stage(self.procurement, 0, 'Financial Approval')
        self.order.approval_log = [self.route[0], financial]
        self.order.save(update_fields=['approval_log'])
        self.assertTrue(po.can_approve(self.order, self.procurement))
        notice = Notification.objects.create(recipient=self.procurement, title='Financial approval', message='Review',
            metadata={'requires_action': True, 'po_id': str(self.order.pk), 'approval_level': 0,
                      'approval_stage': 'Financial Approval', 'assignment_id': financial['assignment_id']})
        self.assertTrue(approval_assignment_issue(notice))

    def test_generic_management_labels_need_explicit_business_position(self):
        for label in ('Department Manager', 'Vice President', 'VP Rejlers Abu Dhabi'):
            with self.subTest(label=label):
                self.assertEqual(stage_positions({'role': label}), ())
                self.assertEqual(stage_positions({'role': label, 'business_position': 'engineer'}), ('engineer',))
        self.assertEqual(stage_positions({'role': 'VP Delivery'}), ('operations',))
        self.assertEqual(stage_positions({'role': 'Vice President Operations'}), ('operations',))

    def test_canonical_position_change_blocks_even_superuser_with_old_profile_title(self):
        self.procurement.is_superuser = True
        self.procurement.save(update_fields=['is_superuser'])
        UserProfile.objects.filter(user=self.procurement).update(job_title='Procurement Manager', department='procurement')
        EmployeeMaster.objects.filter(user=self.procurement).update(designation='CEO')
        self.assertFalse(po.can_approve(self.order, self.procurement))
        self.assertFalse(pr.can_approve(self.requisition, self.procurement))
        with self.assertRaises(PermissionDenied):
            po.record_decision(self.order, self.procurement, 'approve')

    def test_finance_access_or_display_title_does_not_make_an_employee_a_finance_approver(self):
        UserProfile.objects.filter(user=self.engineer).update(department='Finance', job_title='Finance Manager')
        grant_approval(self.engineer, 'finance_salary')
        entry = self.stage(self.engineer, 0, 'Financial Approval')
        self.assertFalse(eligible_stage_assignee(self.engineer, entry, MODULE_PO))
        with self.assertRaises(ValidationError):
            po.normalize_assignments([entry], require_core=False)

    def test_generic_level_requires_configured_position_and_cannot_override_known_business_role(self):
        stage = self.stage(self.engineer, 0, 'Level 0')
        self.assertFalse(eligible_stage_assignee(self.engineer, stage, MODULE_PO))
        stage['business_position'] = 'engineer'
        self.assertTrue(eligible_stage_assignee(self.engineer, stage, MODULE_PO))
        stage['stage'] = 'CEO'
        self.assertFalse(eligible_stage_assignee(self.engineer, stage, MODULE_PO))

    def test_revocation_after_queuing_invalidates_notification_and_preserves_history(self):
        notice = Notification.objects.create(
            recipient=self.procurement, title='PO requires approval', message='Review', status='SENT',
            metadata={'requires_action': True, 'po_id': str(self.order.pk), 'approval_level': 0,
                      'approval_stage': 'Procurement', 'assignment_id': 'gate-0'},
        )
        self.assertEqual(approval_assignment_issue(notice), '')
        UserRole.objects.filter(user_profile=self.procurement.rbac_profile).delete()
        self.assertEqual(approval_assignment_issue(notice), 'approval_no_longer_assigned')
        self.order.refresh_from_db()
        self.assertEqual(self.order.approval_log[0]['status'], 'pending')
