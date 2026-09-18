"""Document-free departmental plans retain task ownership and approval boundaries."""
from copy import deepcopy
from datetime import date
from unittest.mock import patch

from apps.core.project_models import ProjectMember, ProjectTask

from ..models import DocumentIntelligenceRun, PlanningAuditEvent, PlanningFile, ScheduleBaseline, ScheduleVersion
from .test_work_assignments import WorkAssignmentFixture


class ManualWorkBreakdownTests(WorkAssignmentFixture):
    def setUp(self):
        super().setUp()
        # The fixture's employees and access policy are reused, not its evidence.
        DocumentIntelligenceRun.objects.all().delete()
        PlanningFile.objects.all().delete()
        self.project.planning_mode = 'manual'
        self.project.scope_summary = 'Prepare and launch the internal software release.'
        self.project.phase = 'Phase 1'
        self.project.effective_date = date(2026, 9, 21)
        self.project.planned_end_date = date(2026, 12, 20)
        self.project.save()
        self.url = f'/api/v1/planning-intelligence/projects/{self.project.pk}/manual-work-breakdown/'
        self.project_url = f'/api/v1/planning-intelligence/projects/{self.project.pk}/'

    def task(self, **overrides):
        return {
            'id': 'test-release', 'discipline': 'quality_assurance', 'title': 'Accept the software release',
            'owner': '', 'effort_hours': 16, 'depends_on': [], 'acceptance_criteria': 'All critical tests pass.',
            'reviewer': '', 'task_type': 'deliverable', 'assignee_id': self.worker.user_id,
            'reviewer_id': self.reviewer.user_id, 'due_date': '2026-12-18', 'priority': 'high',
            **overrides,
        }

    def test_read_requires_no_uploads_and_does_not_create_intelligence_or_draft(self):
        count = PlanningAuditEvent.objects.count()
        state = self.read()
        self.assertEqual(state['planning_mode'], 'manual')
        self.assertIsNone(state['intelligence_run_id'])
        self.assertEqual(state['tasks'], [])
        self.assertEqual(state['source_documents'], [])
        self.assertEqual(state['revision'], 0)
        self.project.refresh_from_db()
        self.assertEqual(self.project.manual_work_breakdown, {})
        self.assertFalse(DocumentIntelligenceRun.objects.exists())
        self.assertFalse(self.project.schedules.exists())
        self.assertEqual(PlanningAuditEvent.objects.count(), count)

    def test_save_publishes_real_employee_task_and_preserves_progress_on_edit(self):
        state = self.read()
        state['tasks'] = [self.task()]
        response = self.save(state)
        self.assertEqual(response.status_code, 200, response.data)
        record = ProjectTask.objects.get(is_deleted=False)
        self.assertEqual(self.my_tasks(self.worker_client)[0]['id'], record.pk)
        self.assertIsNone(record.metadata['intelligence_run_id'])
        self.assertEqual(record.metadata['planning_mode'], 'manual')
        self.assertEqual(self.progress(self.worker_client, status='in_progress', progress=40).status_code, 200)
        state = self.read()
        self.assertEqual(state['tasks'][0]['progress_percent'], 40)
        state['tasks'][0]['title'] = 'Accept the reviewed software release'
        updated = self.save(state)
        self.assertEqual(updated.status_code, 200, updated.data)
        record.refresh_from_db()
        self.assertEqual(record.progress_percent, 40)
        self.assertEqual(record.status, 'in_progress')
        self.assertFalse(DocumentIntelligenceRun.objects.exists())

    def test_advance_creates_named_workstreams_dates_calendar_and_editable_schedule(self):
        state = self.read()
        state['disciplines'] = [{'code': 'quality_assurance', 'name': 'Release acceptance'}]
        state['tasks'] = [self.task(duration_days=2, planned_start_date='2026-12-16')]
        result = self.save(state, advance=True)
        self.assertEqual(result.status_code, 200, result.data)
        version = ScheduleVersion.objects.get(pk=result.data['schedule_version_id'])
        self.assertEqual(version.status, 'draft')
        self.assertEqual(version.wbs_nodes.get().name, 'Release acceptance')
        self.assertEqual(version.schedule.default_calendar.working_weekdays, [0, 1, 2, 3, 4])
        activity = version.activities.get()
        self.assertEqual(activity.duration_days, 2)
        self.assertEqual(activity.constraint_type, 'start_no_earlier')
        self.assertEqual(activity.constraint_date, date(2026, 12, 16))
        self.assertFalse(activity.metadata['duration_pending'])
        self.assertEqual(activity.metadata['source'], 'manual_work_breakdown')
        self.assertIsNone(activity.metadata['intelligence_run_id'])
        self.assertFalse(version.calculation_runs.exists())
        self.assertFalse(ScheduleBaseline.objects.exists())
        again = self.save(result.data, advance=True)
        self.assertEqual(again.status_code, 200, again.data)
        self.assertEqual(again.data['schedule_version_id'], version.pk)

    def test_no_document_evidence_is_accepted_from_client_and_references_are_not_reviewed(self):
        self.source('optional.txt', 'other', 'Optional background, not analyzed.')
        state = self.read()
        self.assertEqual(state['source_documents'][0]['status'], 'reference')
        state['tasks'] = [self.task(source_references=[{'file_id': 991, 'excerpt': 'Claimed evidence'}])]
        result = self.save(state)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['tasks'][0]['source_references'], [])
        self.assertFalse(DocumentIntelligenceRun.objects.exists())

    def test_revision_conflicts_and_invalid_dependencies_do_not_change_assignments(self):
        stale = self.read()
        state = deepcopy(stale)
        state['tasks'] = [self.task()]
        self.assertEqual(self.save(state).status_code, 200)
        stale['tasks'] = [self.task(assignee_id=self.other_worker.user_id)]
        self.assertEqual(self.save(stale).status_code, 409)
        current = self.read()
        current['tasks'][0]['depends_on'] = ['missing']
        self.assertEqual(self.save(current).status_code, 400)
        self.assertEqual(ProjectTask.objects.get(is_deleted=False).assigned_to_id, self.worker.user_id)

    def test_readiness_and_empty_task_checks_roll_back_assignment_creation(self):
        state = self.read()
        state['tasks'] = [self.task()]
        self.project.scope_summary = ''
        self.project.save(update_fields=['scope_summary'])
        response = self.save(state, advance=True)
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'work_breakdown_inputs_required')
        self.assertFalse(ProjectTask.objects.exists())
        self.project.scope_summary = 'Internal project'
        self.project.save(update_fields=['scope_summary'])
        state['tasks'] = []
        self.assertEqual(self.save(state, advance=True).status_code, 409)
        self.assertEqual(self.save(state).status_code, 200)

    def test_modes_are_explicit_and_cannot_switch_after_saved_work(self):
        self.project.planning_mode = 'document'
        self.project.save(update_fields=['planning_mode'])
        self.assertEqual(self.client.get(self.url).status_code, 409)
        document_url = self.url.replace('manual-work-breakdown', 'work-breakdown')
        self.assertEqual(self.client.get(document_url).status_code, 409)
        result = self.client.patch(self.project_url, {'planning_mode': 'manual'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        state = self.read()
        state['tasks'] = [self.task()]
        self.assertEqual(self.save(state).status_code, 200)
        response = self.client.patch(self.project_url, {'planning_mode': 'document'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('planning_mode', response.data)

    def test_outsider_and_nonmanager_cannot_create_employee_work(self):
        state = self.read()
        state['tasks'] = [self.task()]
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.save(state).status_code, 404)
        ProjectMember.objects.create(project=self.enterprise, user=self.outsider, role='engineer')
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.save(state).status_code, 403)
        self.assertFalse(ProjectTask.objects.exists())

    def test_updated_scope_dates_create_new_draft_and_keep_previous_version(self):
        state = self.read()
        state['tasks'] = [self.task(duration_days=2)]
        first = self.save(state, advance=True)
        self.assertEqual(first.status_code, 200, first.data)
        changed = self.client.patch(self.project_url, {'effective_date': '2026-09-28'}, format='json')
        self.assertEqual(changed.status_code, 200, changed.data)
        result = self.save(first.data, advance=True)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertNotEqual(result.data['schedule_version_id'], first.data['schedule_version_id'])
        current = ScheduleVersion.objects.get(pk=result.data['schedule_version_id'])
        self.assertEqual(current.schedule.planned_start, date(2026, 9, 28))
        self.assertEqual(current.parent_version_id, first.data['schedule_version_id'])

    def test_snapshot_cannot_be_overwritten_through_general_project_patch(self):
        response = self.client.patch(self.project_url, {
            'manual_work_breakdown': {'revision': 999, 'tasks': [self.task()]},
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.project.refresh_from_db()
        self.assertEqual(self.project.manual_work_breakdown, {})
        self.assertNotIn('manual_work_breakdown', response.data)
        self.assertFalse(ProjectTask.objects.exists())

    def test_audit_failure_rolls_back_schedule_draft_and_employee_assignment(self):
        state = self.read()
        state['tasks'] = [self.task()]
        with patch('apps.planning_intelligence.services.work_breakdown.record_event', side_effect=RuntimeError('audit down')):
            with self.assertRaises(RuntimeError):
                self.save(state, advance=True)
        self.project.refresh_from_db()
        self.assertEqual(self.project.manual_work_breakdown, {})
        self.assertFalse(self.project.schedules.exists())
        self.assertFalse(ProjectTask.objects.exists())
