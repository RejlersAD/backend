"""Project history must retain who actually assigned and performed the work."""
from copy import deepcopy
from unittest.mock import patch

from apps.core.project_models import Project, ProjectMember, ProjectTask
from apps.planning_intelligence.models import PlanningAuditEvent, PlanningProject
from apps.planning_intelligence.services.work_assignment_history import task_snapshot
from .test_work_assignments import WorkAssignmentFixture


class WorkAssignmentHistoryTests(WorkAssignmentFixture):
    def history(self, user_id=None, *, client=None):
        return (client or self.client).get(
            f'/api/v1/planning-intelligence/projects/{self.project.pk}/employee-activity/',
            {'user_id': self.worker.user_id if user_id is None else user_id},
        )

    def test_read_shows_actual_assignment_actor_date_and_is_read_only(self):
        self.assign()
        event = PlanningAuditEvent.objects.get(action='work_breakdown.assignment_created')
        before = list(PlanningAuditEvent.objects.values())
        response = self.history()
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data
        self.assertEqual(data['employee']['user_id'], self.worker.user_id)
        self.assertEqual(data['project']['id'], self.enterprise.pk)
        self.assertEqual(data['summary']['current_tasks'], 1)
        row = data['tasks'][0]
        self.assertEqual(row['assigned_by']['user_id'], self.owner.pk)
        self.assertEqual(row['assigned_at'], event.created_at.isoformat())
        self.assertEqual(row['project_task_id'], ProjectTask.objects.get().pk)
        self.assertEqual([entry['action'] for entry in data['activity']], ['assigned'])
        self.assertEqual(data, self.history().data)
        self.assertEqual(before, list(PlanningAuditEvent.objects.values()))
        self.assertNotIn('salary', str(data))

    def test_progress_reassignment_preserves_previous_employee_history(self):
        draft = self.assign()
        initial = deepcopy(self.history().data)
        self.assertEqual(self.progress(self.worker_client, status='in_progress', progress=30).status_code, 200)
        draft['tasks'][0]['assignee_id'] = self.other_worker.user_id
        self.assertEqual(self.save(draft).status_code, 200)
        after_reassign = deepcopy(self.history().data)
        self.worker_client.force_authenticate(self.other_worker.user)
        self.assertEqual(self.progress(self.worker_client, status='completed').status_code, 200)
        old = self.history().data
        self.assertEqual(old, after_reassign)
        row = old['tasks'][0]
        self.assertEqual(row['assignment_state'], 'historical')
        self.assertEqual(row['status'], 'in_progress')
        self.assertEqual(row['progress_percent'], 30)
        self.assertEqual(row['assigned_at'], initial['tasks'][0]['assigned_at'])
        self.assertEqual(old['activity'][-1], initial['activity'][0])
        self.assertEqual(old['summary']['current_tasks'], 0)
        self.assertEqual(old['summary']['historical_tasks'], 1)
        new = self.history(self.other_worker.user_id).data
        self.assertEqual(new['tasks'][0]['status'], 'completed')
        self.assertEqual(new['activity'][0]['action'], 'completed')

    def test_withdrawn_assignment_and_review_history_keep_responsibility(self):
        draft = self.assign(task_type='deliverable', reviewer=True)
        self.assertEqual(self.progress(self.worker_client, status='review', progress=100).status_code, 200)
        self.assertEqual(self.progress(self.reviewer_client, status='in_progress').status_code, 200)
        reviewer = self.history(self.reviewer.user_id).data
        self.assertEqual(reviewer['tasks'][0]['role'], 'reviewer')
        self.assertEqual(reviewer['activity'][0]['action'], 'review_returned')
        self.assertEqual(reviewer['activity'][0]['actor']['user_id'], self.reviewer.user_id)
        draft['tasks'][0]['assignee_id'] = None
        self.assertEqual(self.save(draft).status_code, 200)
        for employee in (self.worker, self.reviewer):
            data = self.history(employee.user_id).data
            self.assertEqual(data['summary']['current_tasks'], 0)
            self.assertEqual(data['tasks'][0]['assignment_state'], 'historical')
            self.assertEqual(data['activity'][0]['action'], 'unassigned')

    def test_repeated_save_and_noop_progress_do_not_duplicate_activity(self):
        draft = self.assign()
        initial = self.history().data
        self.assertEqual(self.save(draft).status_code, 200)
        self.assertEqual(self.progress(self.worker_client, status='todo', progress=0).status_code, 200)
        self.assertEqual(initial, self.history().data)

    def test_legacy_saved_draft_and_progress_audits_reconstruct_without_backfill(self):
        draft = self.assign()
        PlanningAuditEvent.objects.filter(action__startswith='work_breakdown.assignment_').delete()
        saved = PlanningAuditEvent.objects.get(action='work_breakdown.saved')
        saved.metadata.pop('task_audit_version')
        saved.save(update_fields=['metadata'])
        self.assertEqual(self.progress(self.worker_client, status='in_progress', progress=25).status_code, 200)
        progress = PlanningAuditEvent.objects.get(action='work_breakdown.progress_updated')
        progress.before = {key: progress.before[key] for key in ('status', 'progress_percent')}
        progress.after = {key: progress.after[key] for key in ('status', 'progress_percent')}
        progress.metadata = {}
        progress.save(update_fields=['before', 'after', 'metadata'])
        before = list(PlanningAuditEvent.objects.values())
        data = self.history().data
        self.assertEqual(data['tasks'][0]['assigned_at'], saved.created_at.isoformat())
        self.assertEqual(data['tasks'][0]['assigned_by']['user_id'], saved.actor_id)
        self.assertEqual([event['action'] for event in data['activity']], ['status_changed', 'assigned'])
        self.assertEqual(data['activity'][0]['after']['progress_percent'], 25)
        self.assertEqual(before, list(PlanningAuditEvent.objects.values()))
        draft['tasks'][0]['priority'] = 'critical'
        self.assertEqual(self.save(draft).status_code, 200)
        self.assertEqual(self.history().data['tasks'][0]['assigned_at'], saved.created_at.isoformat())

    def test_missing_audit_evidence_does_not_invent_assigned_at_or_actor(self):
        self.assign()
        PlanningAuditEvent.objects.filter(action__startswith='work_breakdown.').delete()
        data = self.history().data
        self.assertIsNone(data['tasks'][0]['assigned_at'])
        self.assertIsNone(data['tasks'][0]['assigned_by'])
        self.assertEqual(data['activity'], [])

    def test_deleted_actor_keeps_the_name_recorded_in_assignment_audit(self):
        self.assign()
        event = PlanningAuditEvent.objects.get(action='work_breakdown.assignment_created')
        recorded_name = event.metadata['actor_name']
        # SET_NULL is the audit FK behavior when an actor account is deleted.
        event.actor = None
        event.save(update_fields=['actor'])
        data = self.history().data
        expected = {'user_id': None, 'name': recorded_name}
        self.assertEqual(data['activity'][0]['actor'], expected)
        self.assertEqual(data['tasks'][0]['assigned_by'], expected)

    def test_project_scope_canonical_identity_and_read_permissions(self):
        self.assign()
        self.assertEqual(self.history(self.owner.pk).status_code, 404)
        self.assertEqual(self.history(self.other_worker.user_id).status_code, 404)
        for invalid in ('', 'text', '-1', '0', '9' * 50):
            self.assertEqual(self.history(invalid).status_code, 400)
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.history().status_code, 404)
        ProjectMember.objects.create(project=self.enterprise, user=self.outsider, role='engineer')
        self.assertEqual(self.history().status_code, 200)
        self.assertIn(self.history(client=self.worker_client).status_code, (403, 404))

    def test_other_project_tasks_and_events_are_never_exposed(self):
        self.assign()
        initial = self.history().data
        other = Project.objects.create(code='OTHER', name='Other private project', owner=self.owner)
        workspace = PlanningProject.objects.create(enterprise_project=other, name='Other workspace', created_by=self.owner)
        task = ProjectTask.objects.create(project=other, title='Private deliverable', assigned_to=self.worker.user,
                                          source_key=f'wbs:{workspace.pk}:private',
                                          metadata={'source': 'work_breakdown', 'wbs_task_id': 'private'})
        PlanningAuditEvent.objects.create(project=workspace, actor=self.owner,
                                          action='work_breakdown.assignment_created', entity_type='ProjectTask',
                                          entity_id=str(task.pk), after=task_snapshot(task))
        self.assertEqual(initial, self.history().data)

    def test_task_audit_failure_rolls_back_assignment_and_failed_progress(self):
        draft = self.read()
        draft['tasks'][0]['assignee_id'] = self.worker.user_id
        with patch('apps.planning_intelligence.services.work_assignment_history.record_event', side_effect=RuntimeError('audit failure')):
            with self.assertRaises(RuntimeError):
                self.save(draft)
        self.assertFalse(ProjectTask.objects.exists())
        self.assign()
        initial = self.history().data
        with patch('apps.planning_intelligence.services.audit.record_event', side_effect=RuntimeError('audit failure')):
            with self.assertRaises(RuntimeError):
                self.progress(self.worker_client, status='in_progress', progress=50)
        self.assertEqual(initial, self.history().data)
