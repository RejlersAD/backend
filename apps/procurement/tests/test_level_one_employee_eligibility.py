"""Level 1 employee selection, delivery, and decisions share one eligibility rule."""

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.hr_core.models import EmployeeMaster
from apps.notifications.delivery import approval_assignment_issue
from apps.notifications.models import Notification
from apps.procurement.models import PurchaseRequisition
from apps.procurement.serializers import PurchaseRequisitionSerializer
from apps.procurement.services.approval_eligibility import (
    MODULE_PO, MODULE_PR, eligible_stage_assignee, is_employee_selected_pr_stage,
)
from apps.procurement.services.requisition_workflow import RequisitionWorkflowService as workflow
from apps.rbac.models import Module, Organization, Permission, UserPermissionOverride, UserProfile
from .approval_fixtures import grant_approval, set_position


@override_settings(TEAMS_APPROVAL_WEBHOOK_URL='', WEB_PUSH_VAPID_PRIVATE_KEY='')
class LevelOneEmployeeEligibilityTests(TestCase):
    def setUp(self):
        org = Organization.objects.create(code='LEVEL1-EMPLOYEES', name='Level 1 employees')
        self.people = []
        for name, title in [('developer', 'Full Stack Developer'), ('designer', 'Designer'),
                            ('procurement', 'Procurement Manager')]:
            user = get_user_model().objects.create_user(name, email=f'{name}@level1.example')
            profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': org})
            profile.status = 'active'
            profile.is_deleted = False
            profile.signature_image = f'signature-{name}'
            profile.save()
            set_position(user, title)
            self.people.append(user)
        self.developer, self.designer, self.procurement = self.people
        # Ordinary employees deliberately have no module-wide approval grants.
        grant_approval(self.procurement)
        self.stage = self.employee_stage(self.developer)
        self.pr = PurchaseRequisition.objects.create(
            pr_number='PR-LEVEL1-EMPLOYEES', title='Employee-selected Level 1',
            issued_by=self.procurement, status='submitted', po_applicable=True,
            approval_workflow_config=[deepcopy(self.stage)],
        )

    @staticmethod
    def employee_stage(user, number=1):
        return {
            'level': 1, 'role': 'Level 1 Approver', 'stage': f'Level 1 - Approver {number} of 2',
            'user_id': str(user.pk), 'user_email': user.email, 'status': 'pending',
            'assignment_id': f'employee-{user.pk}',
        }

    def validate_stage(self, stage, instance=None):
        return PurchaseRequisitionSerializer(instance).validate_approval_workflow_config([stage])[0]

    def notice(self):
        return Notification.objects.create(
            recipient=self.developer, title='Approval request', message='Review request',
            metadata={'requires_action': True, 'pr_id': str(self.pr.pk), 'approval_level': 1,
                      'assignment_id': self.stage['assignment_id']},
        )

    def test_any_active_employee_saves_without_designated_position_or_approval_grant(self):
        for user in (self.developer, self.designer):
            with self.subTest(user=user.username):
                normalized = self.validate_stage(self.employee_stage(user))
                self.assertEqual(normalized['user_id'], str(user.pk))
                self.assertNotIn('business_position', normalized)
                self.assertEqual(normalized['group_mode'], 'all')

    def test_stale_position_does_not_restrict_new_level_one_assignment(self):
        stage = {**self.stage, 'business_position': 'legacy/Position'}
        normalized = self.validate_stage(stage)
        self.assertNotIn('business_position', normalized)
        self.assertTrue(eligible_stage_assignee(self.developer, stage, MODULE_PR))

    def test_edit_preserves_existing_route_metadata_and_assignment_identity(self):
        self.pr.approval_workflow_config[0]['business_position'] = 'project_manager'
        self.pr.save(update_fields=['approval_workflow_config'])
        serializer = PurchaseRequisitionSerializer(
            self.pr, data={'approval_workflow_config': [self.stage]}, partial=True,
            context={'request': SimpleNamespace(user=self.procurement)},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        normalized = serializer.validated_data['approval_workflow_config'][0]
        self.assertEqual(normalized['business_position'], 'project_manager')
        self.assertEqual(normalized['assignment_id'], self.stage['assignment_id'])

    def test_pending_legacy_position_assignment_can_be_replaced_without_changing_route(self):
        self.pr.approval_workflow_config[0]['business_position'] = 'project_manager'
        self.pr.save(update_fields=['approval_workflow_config'])
        serializer = PurchaseRequisitionSerializer(
            self.pr, data={'approval_workflow_config': [self.employee_stage(self.designer)]}, partial=True,
            context={'request': SimpleNamespace(user=self.procurement)},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        updated = serializer.save()
        stage = updated.approval_workflow_config[0]
        self.assertEqual(stage['user_id'], str(self.designer.pk))
        self.assertNotEqual(stage['assignment_id'], self.stage['assignment_id'])
        self.assertTrue(workflow.can_approve(updated, self.designer))
        self.assertFalse(workflow.can_approve(updated, self.developer))

    def test_completed_legacy_position_assignment_cannot_be_replaced(self):
        self.pr.approval_workflow_config[0].update(business_position='project_manager', status='approved')
        self.pr.save(update_fields=['approval_workflow_config'])
        serializer = PurchaseRequisitionSerializer(
            self.pr, data={'approval_workflow_config': [self.employee_stage(self.designer)]}, partial=True,
            context={'request': SimpleNamespace(user=self.procurement)},
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('recorded decision', str(serializer.errors))

    def test_inactive_locked_deleted_and_terminated_employees_are_ineligible(self):
        profile = UserProfile.objects.get(user=self.developer)
        for changes in ({'status': 'suspended'}, {'is_deleted': True},
                        {'locked_until': timezone.now() + timedelta(hours=1)}):
            with self.subTest(changes=changes):
                UserProfile.objects.filter(pk=profile.pk).update(status='active', is_deleted=False, locked_until=None)
                UserProfile.objects.filter(pk=profile.pk).update(**changes)
                self.assertFalse(eligible_stage_assignee(self.developer, self.stage, MODULE_PR))
                with self.assertRaises(ValidationError):
                    self.validate_stage(self.stage)
        UserProfile.objects.filter(pk=profile.pk).update(status='active', is_deleted=False, locked_until=None)
        EmployeeMaster.objects.filter(user=self.developer).update(employment_status='terminated')
        self.assertFalse(eligible_stage_assignee(self.developer, self.stage, MODULE_PR))
        EmployeeMaster.objects.filter(user=self.developer).update(employment_status='active')
        get_user_model().objects.filter(pk=self.developer.pk).update(is_active=False)
        self.assertFalse(eligible_stage_assignee(self.developer, self.stage, MODULE_PR))

    def test_active_radai_account_without_hr_record_matches_employee_directory(self):
        EmployeeMaster.objects.filter(user=self.developer).delete()
        self.assertTrue(eligible_stage_assignee(self.developer, self.stage, MODULE_PR))

    def test_explicit_deny_blocks_selection_decision_and_queued_delivery(self):
        notice = self.notice()
        self.assertEqual(approval_assignment_issue(notice), '')
        permission = Permission.objects.get(code=f'{MODULE_PR}.approve')
        UserPermissionOverride.objects.create(
            user_profile=self.developer.rbac_profile, permission=permission, allowed=False,
        )
        with self.assertRaises(ValidationError):
            self.validate_stage(self.stage)
        self.assertFalse(workflow.can_approve(self.pr, self.developer))
        with self.assertRaises(PermissionDenied):
            workflow.approve(self.pr.pk, self.developer)
        self.assertTrue(approval_assignment_issue(notice))

    def test_disabled_module_blocks_employee_assignment(self):
        Module.objects.filter(code=MODULE_PR).update(is_active=False)
        self.assertFalse(eligible_stage_assignee(self.developer, self.stage, MODULE_PR))

    def test_fixed_role_labels_and_po_stages_do_not_get_employee_exception(self):
        grant_approval(self.developer)
        for overrides in ({'role': 'CEO', 'stage': 'CEO'}, {'stage': 'CEO'},
                          {'level': 4}, {'level': 'invalid'}, {'role': 'Project Manager'}):
            with self.subTest(overrides=overrides):
                stage = {**self.stage, **overrides}
                self.assertFalse(is_employee_selected_pr_stage(stage))
                self.assertFalse(eligible_stage_assignee(self.developer, stage, MODULE_PR))
        self.assertFalse(eligible_stage_assignee(self.developer, self.stage, MODULE_PO))

    def test_employee_receives_notification_and_delivery_rechecks_assignment(self):
        with patch('apps.notifications.services.NotificationService.create_notification') as create:
            with self.captureOnCommitCallbacks(execute=True):
                workflow._notify_level(self.pr, self.pr.approval_workflow_config, 1)
        create.assert_called_once()
        self.assertEqual(create.call_args.kwargs['recipient'].pk, self.developer.pk)
        self.assertTrue(create.call_args.kwargs['send_teams'])
        self.assertEqual(create.call_args.kwargs['teams_context']['approval_level'], 1)
        notice = self.notice()
        self.assertEqual(approval_assignment_issue(notice), '')
        self.pr.approval_workflow_config = [self.employee_stage(self.designer)]
        self.pr.save(update_fields=['approval_workflow_config'])
        self.assertTrue(approval_assignment_issue(notice))

    def test_level_one_waits_for_predecessors_and_all_parallel_employees(self):
        initial = {'level': 0, 'role': 'Procurement Department', 'stage': 'Procurement Review',
                   'user_id': str(self.procurement.pk), 'user_email': self.procurement.email, 'status': 'pending'}
        self.pr.approval_workflow_config = [initial, self.stage, self.employee_stage(self.designer, 2)]
        self.pr.save(update_fields=['approval_workflow_config'])
        self.assertFalse(workflow.can_approve(self.pr, self.developer))
        with self.assertRaises(PermissionDenied):
            workflow.approve(self.pr.pk, self.developer)
        workflow.approve(self.pr.pk, self.procurement)
        updated = workflow.approve(self.pr.pk, self.developer, require_signature=True)
        self.assertNotEqual(updated.status, 'approved')
        self.assertFalse(workflow.can_approve(updated, self.procurement))
        self.assertFalse(workflow.can_approve(updated, self.developer))
        self.assertTrue(workflow.can_approve(updated, self.designer))
        updated = workflow.approve(self.pr.pk, self.designer, require_signature=True)
        self.assertEqual(updated.status, 'approved')
        self.assertEqual(updated.approval_workflow_config[1]['signature'], 'signature-developer')
