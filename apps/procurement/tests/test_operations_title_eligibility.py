"""An official combined operations title retains the existing approval gates."""

from copy import deepcopy
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.hr_core.models import EmployeeMaster
from apps.procurement.services.approval_eligibility import (
    MODULE_PO, MODULE_PR, eligible_stage_assignee,
)
from apps.procurement.services.requisition_workflow import RequisitionWorkflowService
from apps.rbac.approval_eligibility import has_business_position
from apps.rbac.models import UserProfile, UserRole

from .approval_fixtures import grant_approval, set_position


OFFICIAL_TITLE = 'Chief Operating Officer & VP, Head of Operations & Project Delivery'


@override_settings(TEAMS_APPROVAL_WEBHOOK_URL='', WEB_PUSH_VAPID_PRIVATE_KEY='')
class OperationsTitleEligibilityTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(
            username='operations-title-assignee', email='operations-title@example.test',
        )
        grant_approval(self.actor)
        self.employee = set_position(self.actor, OFFICIAL_TITLE)
        self.stage = {
            'level': 4, 'role': 'VP Delivery', 'stage': 'Level 4 - VP Delivery',
            'user_id': str(self.actor.pk), 'user_email': self.actor.email,
            'status': 'pending', 'assignment_id': 'operations-title-assignment',
        }

    def test_official_title_and_punctuation_variants_match_existing_operations_position(self):
        for title in (
            OFFICIAL_TITLE,
            'Chief Operating Officer and VP, Head of Operations and Project Delivery',
            'Chief Operating Officer & VP - Head of Operations & Project Delivery',
            '  CHIEF OPERATING OFFICER & VP,\n HEAD OF OPERATIONS & PROJECT DELIVERY  ',
        ):
            with self.subTest(title=title):
                EmployeeMaster.objects.filter(pk=self.employee.pk).update(designation=title)
                self.assertTrue(has_business_position(self.actor, 'head_operations_project_delivery'))
                for module in (MODULE_PR, MODULE_PO):
                    self.assertTrue(eligible_stage_assignee(self.actor, self.stage, module))
                self.employee.refresh_from_db()
                self.assertEqual(self.employee.designation, title)

    def test_operations_title_does_not_supply_ceo_or_finance_authority(self):
        for role in ('CEO', 'Financial Approval'):
            with self.subTest(role=role):
                stage = {**self.stage, 'role': role, 'stage': role}
                for module in (MODULE_PR, MODULE_PO):
                    self.assertFalse(eligible_stage_assignee(self.actor, stage, module))
        self.assertFalse(has_business_position(self.actor, 'ceo'))

    def test_generic_or_related_titles_are_not_matched_by_substring(self):
        for title in (
            'VP', 'Vice President', 'Chief Operating Officer',
            f'Assistant to the {OFFICIAL_TITLE}',
            f'Former {OFFICIAL_TITLE}',
            'Chief Operating Officer & VP, Head of Finance & ICT',
        ):
            with self.subTest(title=title):
                EmployeeMaster.objects.filter(pk=self.employee.pk).update(designation=title)
                self.assertFalse(eligible_stage_assignee(self.actor, self.stage, MODULE_PR))

    def test_profile_and_secondary_titles_do_not_override_changed_canonical_position(self):
        UserProfile.objects.filter(user=self.actor).update(job_title=OFFICIAL_TITLE)
        EmployeeMaster.objects.filter(pk=self.employee.pk).update(
            designation='Engineer', job_title_uae=OFFICIAL_TITLE,
        )
        self.assertFalse(eligible_stage_assignee(self.actor, self.stage, MODULE_PR))
        self.assertIsNone(RequisitionWorkflowService._resolve_stage_user(dict(self.stage)))

    def test_revoked_approval_access_still_blocks_the_recipient(self):
        self.assertIsNotNone(RequisitionWorkflowService._resolve_stage_user(dict(self.stage)))
        UserRole.objects.filter(user_profile=self.actor.rbac_profile).delete()
        self.assertFalse(eligible_stage_assignee(self.actor, self.stage, MODULE_PR))
        self.assertIsNone(RequisitionWorkflowService._resolve_stage_user(dict(self.stage)))

    def test_inactive_account_or_employee_still_blocks_the_recipient(self):
        self.actor.is_active = False
        self.actor.save(update_fields=['is_active'])
        self.assertIsNone(RequisitionWorkflowService._resolve_stage_user(dict(self.stage)))
        self.actor.is_active = True
        self.actor.save(update_fields=['is_active'])
        EmployeeMaster.objects.filter(pk=self.employee.pk).update(employment_status='terminated')
        self.assertFalse(eligible_stage_assignee(self.actor, self.stage, MODULE_PR))
        self.assertIsNone(RequisitionWorkflowService._resolve_stage_user(dict(self.stage)))

    def test_full_title_preserves_assignment_and_current_level_requirements(self):
        workflow = [
            {**self.stage, 'level': 0, 'role': 'Procurement Department',
             'stage': 'Procurement Department', 'user_id': 'another-assignee',
             'user_email': 'another-assignee@example.test'},
            dict(self.stage),
        ]
        requisition = SimpleNamespace(
            status='submitted', po_applicable=True, po_number_reference='',
            approval_workflow_config=workflow, current_approval_step=0,
        )
        original = deepcopy(workflow)
        self.assertFalse(RequisitionWorkflowService.can_approve(requisition, self.actor))
        self.assertEqual(workflow, original)
        workflow[0]['status'] = 'approved'
        self.assertTrue(RequisitionWorkflowService.can_approve(requisition, self.actor))
        workflow[1]['user_email'] = 'another-assignee@example.test'
        self.assertFalse(RequisitionWorkflowService.can_approve(requisition, self.actor))
        self.assertEqual(workflow[1]['assignment_id'], 'operations-title-assignment')
