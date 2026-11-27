"""Talent requisitions: Reporting Manager -> HR approval, then an opening in Talent Acquisition."""
from datetime import date
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from apps.core.enquiry_workflow import decide_requisition_stage, route_enquiry
from apps.core.models import Enquiry, EnquiryActivity
from apps.hr_core.models import RecruitmentCandidate, RecruitmentOpening
from apps.hr_core.recruitment import OpeningSerializer
from apps.hr_core.requisition_handoff import open_requisition_position
from apps.notifications.models import Notification

DETAILS = {
    'reason': 'new_position', 'candidate': 'Aisha Rahman', 'job_title': 'Fullstack Developer',
    'justification': 'Technical proposal', 'num_positions': '3', 'hiring_manager': 'Sanglin',
    'req_department': 'RADAI', 'proposed_salary': '20000', 'target_join_date': '2026-12-12',
}


class RequisitionHandoffTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.requester = User.objects.create_user(username='req-requester', email='req-requester@example.test')
        self.manager = User.objects.create_user(username='req-manager', email='req-manager@example.test')
        self.hr_admin = User.objects.create_user(username='req-hr-admin', email='req-hr-admin@example.test',
                                                 first_name='Rini', last_name='Das')
        self.hr_eligible = [self.hr_admin]
        for target, kwargs in (
                ('apps.core.enquiry_workflow._requester_manager', {'side_effect': lambda enquiry: self.manager}),
                # Nobody holds Head of HR & Administration...
                ('apps.core.enquiry_workflow._manager_candidates', {'return_value': []}),
                # ...so the HR stage falls back to HR Manager / HR Administrator holders with approve access.
                ('apps.rbac.approval_eligibility.position_users',
                 {'side_effect': lambda positions, module: get_user_model().objects.filter(
                     pk__in=[user.pk for user in self.hr_eligible])})):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def requisition(self, **details):
        enquiry = Enquiry.objects.create(
            name='Requesting User', email=self.requester.email, phone='12345678',
            subject='Talent Requisition - Fullstack Developer (RADAI)', message='Please approve.',
            inquiry_type='talent_requisition', requester=self.requester,
            requisition_details={**DETAILS, **details},
        )
        route_enquiry(enquiry)
        enquiry.refresh_from_db()
        return enquiry

    def decide(self, enquiry, actor, decision='approved'):
        decide_requisition_stage(Enquiry.objects.get(pk=enquiry.pk), actor=actor, decision=decision, comment='ok')
        enquiry.refresh_from_db()
        return enquiry

    def test_hr_stage_falls_back_to_hr_positions(self):
        enquiry = self.requisition()
        stages = enquiry.requisition_details['approval_stages']
        self.assertEqual([stage['key'] for stage in stages], ['reporting_manager', 'hr'])
        self.assertEqual(stages[1]['approver_id'], self.hr_admin.pk)
        self.assertEqual(enquiry.assigned_to, self.manager)
        enquiry = self.decide(enquiry, self.manager)
        self.assertEqual((enquiry.assigned_to, enquiry.requisition_details['current_stage']), (self.hr_admin, 1))
        self.assertTrue(Notification.objects.filter(recipient=self.hr_admin, title__startswith='Approval required').exists())

    def test_fully_approved_requisition_lands_in_talent_acquisition(self):
        enquiry = self.decide(self.decide(self.requisition(), self.manager), self.hr_admin)
        self.assertEqual(enquiry.approval_status, 'approved')
        opening = RecruitmentOpening.objects.get(requisition=enquiry)
        self.assertEqual((opening.title, opening.department, opening.positions, opening.hiring_manager,
                          opening.target_join_date, opening.is_open, opening.source),
                         ('Fullstack Developer', 'RADAI', 3, 'Sanglin', date(2026, 12, 12), True, 'requisition'))
        self.assertEqual(enquiry.requisition_details['talent_opening_id'], opening.pk)
        candidate = RecruitmentCandidate.objects.get(name='Aisha Rahman')
        self.assertEqual((candidate.position, candidate.department, candidate.status),
                         ('Fullstack Developer', 'RADAI', 'Selected'))
        self.assertIn(enquiry.reference, candidate.interview_notes)
        self.assertEqual(OpeningSerializer(opening).data['requisition_reference'], enquiry.reference)
        final = Notification.objects.filter(recipient=self.requester, title__startswith='Requisition approved').latest('pk')
        self.assertIn('Talent Acquisition', final.message)
        self.assertTrue(EnquiryActivity.objects.filter(enquiry=enquiry, action='talent_opening_created').exists())

    def test_handoff_runs_once(self):
        enquiry = self.decide(self.decide(self.requisition(), self.manager), self.hr_admin)
        open_requisition_position(enquiry)
        self.assertEqual(RecruitmentOpening.objects.filter(requisition=enquiry).count(), 1)
        self.assertEqual(RecruitmentCandidate.objects.filter(name='Aisha Rahman').count(), 1)

    def test_no_candidate_named_creates_only_the_opening(self):
        enquiry = self.decide(self.decide(self.requisition(candidate='', num_positions='not a number'),
                                          self.manager), self.hr_admin)
        self.assertEqual(RecruitmentOpening.objects.get(requisition=enquiry).positions, 1)
        self.assertFalse(RecruitmentCandidate.objects.exists())

    def test_rejected_or_part_approved_requisitions_do_not_land(self):
        part = self.decide(self.requisition(), self.manager)
        rejected = self.decide(self.decide(self.requisition(), self.manager), self.hr_admin, 'rejected')
        self.assertEqual((part.approval_status, rejected.approval_status), ('pending', 'rejected'))
        self.assertFalse(RecruitmentOpening.objects.exists())

    def test_stuck_hr_stage_is_assigned_once_someone_is_eligible(self):
        self.hr_eligible = []
        enquiry = self.decide(self.requisition(), self.manager)
        self.assertIsNone(enquiry.requisition_details['approval_stages'][1]['approver_id'])
        self.assertIsNone(enquiry.assigned_to)
        out = StringIO()
        call_command('assign_requisition_approvers', stdout=out)
        self.assertIn('still no eligible HR approver', out.getvalue())
        self.hr_eligible = [self.hr_admin]  # an administrator grants HR approval access
        out = StringIO()
        call_command('assign_requisition_approvers', stdout=out)
        self.assertIn('assigned to Rini Das', out.getvalue())
        enquiry.refresh_from_db()
        self.assertEqual(enquiry.assigned_to, self.hr_admin)
        self.assertTrue(Notification.objects.filter(recipient=self.hr_admin, title__startswith='Approval required').exists())
        enquiry = self.decide(enquiry, self.hr_admin)
        self.assertTrue(RecruitmentOpening.objects.filter(requisition=enquiry).exists())
