import datetime as dt

from django.test import TestCase
from rest_framework.test import APIClient

from apps.users.models import User

from ..models import (
    PlanningGeneration, PlanningProject, ProposalExportRecord, ProposalWorkflowTask, Schedule,
    ScheduleActivity, ScheduleVersion, TechnicalProposal, WorkCalendar,
)


class TechnicalProposalAPITests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='proposal-owner', email='proposal@example.com', password='test',
        )
        self.outsider = User.objects.create_user(
            username='proposal-outsider', email='outsider@example.com', password='test',
        )
        self.reviewer = User.objects.create_user(
            username='proposal-reviewer', email='reviewer@example.com', password='test', is_staff=True,
        )
        self.approver = User.objects.create_user(
            username='proposal-approver', email='approver@example.com', password='test', is_staff=True,
        )
        self.project = PlanningProject.objects.create(
            name='Mubarraz Accommodation Inspection Schedule', client='ADNOC Offshore',
            phase='FEED', effective_date=dt.date(2026, 8, 24), created_by=self.owner,
        )
        self.generation = PlanningGeneration.objects.create(
            project=self.project, version=1, generated_by=self.owner,
            intelligence={'scope_summary': 'Inspect and assess the offshore accommodation facilities.'},
            narrative='A controlled inspection and engineering programme will be delivered.',
            eddr=[{'document_number': 'RPT-001', 'title': 'Inspection Report'}],
            validation=[{'severity': 'info', 'message': 'Schedule logic validated'}],
        )
        self.calendar = WorkCalendar.objects.create(
            project=self.project, name='5 Day', working_weekdays=[0, 1, 2, 3, 4], is_default=True,
        )
        self.schedule = Schedule.objects.create(
            project=self.project, name='Master Schedule', code='MASTER',
            planned_start=dt.date(2026, 8, 24), default_calendar=self.calendar,
            created_by=self.owner,
        )
        self.version = ScheduleVersion.objects.create(
            schedule=self.schedule, version=4, status='calculated',
            source_generation=self.generation, calculated_finish=dt.date(2027, 3, 5),
            created_by=self.owner,
        )
        ScheduleActivity.objects.create(
            version=self.version, calendar=self.calendar, external_id='M-001',
            name='Inspection complete', activity_type='finish_milestone', duration_days=0,
            planned_start=dt.date(2027, 3, 5), planned_finish=dt.date(2027, 3, 5),
            is_critical=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def create_proposal(self):
        response = self.client.post(
            '/api/v1/planning-intelligence/technical-proposals/',
            {'project': self.project.id, 'title': 'Mubarraz Technical Proposal'}, format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response

    def test_create_freezes_schedule_and_document_intelligence_snapshot(self):
        response = self.create_proposal()

        self.assertEqual(response.data['revision'], 1)
        self.assertEqual(response.data['schedule_version'], self.version.id)
        self.assertEqual(response.data['snapshot']['schedule']['activity_count'], 1)
        self.assertEqual(response.data['snapshot']['schedule']['calculated_finish'], '2027-03-05')
        self.assertEqual(len(response.data['sections']), 48)
        section_keys = {section['key'] for section in response.data['sections']}
        self.assertTrue({
            'qualification_statement', 'compliance_matrix', 'experience',
            'key_personnel', 'solution_architecture', 'ai_methodology',
            'mlops', 'security_governance', 'manpower_histogram',
            'progress_curve', 'monthly_progress', 'subcontractors',
            'hse_statistics', 'certifications', 'business_venture',
            'investment_plan', 'manufacturing_entity',
        }.issubset(section_keys))
        self.assertTrue(all(section['included'] for section in response.data['sections']))
        compliance_section = next(
            section for section in response.data['sections']
            if section['key'] == 'compliance_matrix'
        )
        self.assertEqual(len(compliance_section['data']), 34)
        summary_section = next(
            section for section in response.data['sections']
            if section['key'] == 'executive_summary'
        )
        self.assertEqual(summary_section['group'], 'Method Statement / Execution Plan')
        summary = summary_section['content']
        self.assertNotIn('#', summary)
        self.assertIn('1 activities', summary)
        self.assertIn('5 March 2027', summary)
        self.assertNotIn('AI', summary)

    def test_controlled_workflow_assigns_tasks_separates_roles_and_stores_issued_files(self):
        proposal_id = self.create_proposal().data['id']
        edit = self.client.patch(
            f'/api/v1/planning-intelligence/technical-proposals/{proposal_id}/',
            {'client_name': 'ADNOC'}, format='json',
        )
        self.assertEqual(edit.status_code, 200)

        submitted = self.client.post(
            f'/api/v1/planning-intelligence/technical-proposals/{proposal_id}/submit-review/',
            {'reviewer': self.reviewer.id, 'due_date': '2026-09-01'}, format='json',
        )
        self.assertEqual(submitted.status_code, 200, submitted.data)
        self.assertEqual(submitted.data['status'], 'internal_review')
        self.assertEqual(submitted.data['reviewer'], self.reviewer.id)
        self.assertIsNone(submitted.data['checked_by'])

        author_edit = self.client.patch(
            f'/api/v1/planning-intelligence/technical-proposals/{proposal_id}/',
            {'client_name': 'Changed during review'}, format='json',
        )
        self.assertEqual(author_edit.status_code, 400)

        reviewer_client = APIClient(); reviewer_client.force_authenticate(self.reviewer)
        reviewed = reviewer_client.post(
            f'/api/v1/planning-intelligence/technical-proposals/{proposal_id}/review-decision/',
            {'decision': 'complete', 'approver': self.approver.id, 'due_date': '2026-09-03', 'comments': 'Technically complete.'},
            format='json',
        )
        self.assertEqual(reviewed.status_code, 200, reviewed.data)
        self.assertEqual(reviewed.data['status'], 'approval_review')
        self.assertEqual(reviewed.data['checked_by'], self.reviewer.id)

        approver_client = APIClient(); approver_client.force_authenticate(self.approver)
        approved = approver_client.post(
            f'/api/v1/planning-intelligence/technical-proposals/{proposal_id}/approval-decision/',
            {'decision': 'approve', 'comments': 'Approved for issue.'}, format='json',
        )
        self.assertEqual(approved.status_code, 200, approved.data)
        self.assertEqual(approved.data['status'], 'approved')
        self.assertEqual(approved.data['approved_by'], self.approver.id)

        issued = approver_client.post(
            f'/api/v1/planning-intelligence/technical-proposals/{proposal_id}/issue/', {}, format='json',
        )
        self.assertEqual(issued.status_code, 200, issued.data)
        self.assertEqual(issued.data['status'], 'issued')
        self.assertEqual(len(issued.data['issued_files']), 2)
        self.assertEqual(
            set(ProposalExportRecord.objects.filter(proposal_id=proposal_id, is_issued_artifact=True).values_list('export_format', flat=True)),
            {'pdf', 'docx'},
        )
        self.assertEqual(ProposalWorkflowTask.objects.filter(proposal_id=proposal_id, status='completed').count(), 2)

        locked = self.client.patch(
            f'/api/v1/planning-intelligence/technical-proposals/{proposal_id}/',
            {'title': 'Changed after issue'}, format='json',
        )
        self.assertEqual(locked.status_code, 400)
        self.assertEqual(TechnicalProposal.objects.get(pk=proposal_id).status, 'issued')

    def test_pdf_and_docx_exports_are_audited(self):
        proposal_id = self.create_proposal().data['id']

        pdf = self.client.get(
            f'/api/v1/planning-intelligence/technical-proposals/{proposal_id}/export/?export_format=pdf',
        )
        docx = self.client.get(
            f'/api/v1/planning-intelligence/technical-proposals/{proposal_id}/export/?export_format=docx',
        )

        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf['Content-Type'], 'application/pdf')
        self.assertTrue(pdf.content.startswith(b'%PDF'))
        self.assertEqual(docx.status_code, 200)
        self.assertEqual(docx['Content-Type'], 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
        self.assertTrue(docx.content.startswith(b'PK'))
        self.assertEqual(ProposalExportRecord.objects.filter(proposal_id=proposal_id).count(), 2)
        self.assertTrue(all(len(row.sha256) == 64 for row in ProposalExportRecord.objects.all()))

    def test_author_cannot_be_selected_as_reviewer_or_approver(self):
        proposal_id = self.create_proposal().data['id']
        self_review = self.client.post(
            f'/api/v1/planning-intelligence/technical-proposals/{proposal_id}/submit-review/',
            {'reviewer': self.owner.id}, format='json',
        )
        self.assertEqual(self_review.status_code, 400)

        submitted = self.client.post(
            f'/api/v1/planning-intelligence/technical-proposals/{proposal_id}/submit-review/',
            {'reviewer': self.reviewer.id}, format='json',
        )
        self.assertEqual(submitted.status_code, 200, submitted.data)
        self.owner.is_staff = True
        self.owner.save(update_fields=['is_staff'])
        reviewer_client = APIClient(); reviewer_client.force_authenticate(self.reviewer)
        self_approval = reviewer_client.post(
            f'/api/v1/planning-intelligence/technical-proposals/{proposal_id}/review-decision/',
            {'decision': 'complete', 'approver': self.owner.id}, format='json',
        )
        self.assertEqual(self_approval.status_code, 400)

    def test_reviewer_can_be_selected_from_any_department_and_receives_access(self):
        proposal_id = self.create_proposal().data['id']
        submitted = self.client.post(
            f'/api/v1/planning-intelligence/technical-proposals/{proposal_id}/submit-review/',
            {'reviewer': self.outsider.id, 'due_date': '2026-09-01'}, format='json',
        )
        self.assertEqual(submitted.status_code, 200, submitted.data)

        reviewer_client = APIClient(); reviewer_client.force_authenticate(self.outsider)
        detail = reviewer_client.get(
            f'/api/v1/planning-intelligence/technical-proposals/{proposal_id}/',
        )
        self.assertEqual(detail.status_code, 200, detail.data)

    def test_active_reviewer_and_approver_assignments_can_be_amended(self):
        proposal_id = self.create_proposal().data['id']
        submitted = self.client.post(
            f'/api/v1/planning-intelligence/technical-proposals/{proposal_id}/submit-review/',
            {'reviewer': self.reviewer.id, 'due_date': '2026-09-01'}, format='json',
        )
        self.assertEqual(submitted.status_code, 200, submitted.data)

        reassigned_review = self.client.post(
            f'/api/v1/planning-intelligence/technical-proposals/{proposal_id}/reassign-reviewer/',
            {'reviewer': self.outsider.id, 'due_date': '2026-09-02', 'comments': 'Corrected assignment.'},
            format='json',
        )
        self.assertEqual(reassigned_review.status_code, 200, reassigned_review.data)
        self.assertEqual(reassigned_review.data['reviewer'], self.outsider.id)
        self.assertEqual(
            ProposalWorkflowTask.objects.filter(proposal_id=proposal_id, task_type='review', status='cancelled').count(), 1,
        )

        reviewer_client = APIClient(); reviewer_client.force_authenticate(self.outsider)
        completed = reviewer_client.post(
            f'/api/v1/planning-intelligence/technical-proposals/{proposal_id}/review-decision/',
            {'decision': 'complete', 'approver': self.approver.id, 'due_date': '2026-09-03'},
            format='json',
        )
        self.assertEqual(completed.status_code, 200, completed.data)

        reassigned_approval = reviewer_client.post(
            f'/api/v1/planning-intelligence/technical-proposals/{proposal_id}/reassign-approver/',
            {'approver': self.reviewer.id, 'due_date': '2026-09-04', 'comments': 'Corrected approver.'},
            format='json',
        )
        self.assertEqual(reassigned_approval.status_code, 200, reassigned_approval.data)
        self.assertEqual(reassigned_approval.data['approver'], self.reviewer.id)
        self.assertEqual(
            ProposalWorkflowTask.objects.filter(proposal_id=proposal_id, task_type='approval', status='cancelled').count(), 1,
        )

    def test_pdf_preview_is_inline_and_does_not_create_export_audit_record(self):
        proposal_id = self.create_proposal().data['id']

        preview = self.client.get(
            f'/api/v1/planning-intelligence/technical-proposals/{proposal_id}/export/?export_format=pdf&preview=1',
        )

        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview['Content-Type'], 'application/pdf')
        self.assertTrue(preview['Content-Disposition'].startswith('inline;'))
        self.assertTrue(preview.content.startswith(b'%PDF'))
        self.assertFalse(ProposalExportRecord.objects.filter(proposal_id=proposal_id).exists())

    def test_proposals_are_hidden_from_outsiders(self):
        self.create_proposal()
        outsider_client = APIClient()
        outsider_client.force_authenticate(self.outsider)
        response = outsider_client.get(
            f'/api/v1/planning-intelligence/technical-proposals/?project={self.project.id}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['results'], [])
