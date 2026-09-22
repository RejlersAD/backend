"""Sparse edits preserve selected source versions while creating a planner draft."""
from copy import deepcopy
from datetime import date
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import path
from django.utils import timezone

from apps.rbac.route_guard import secure_module_endpoints
from ..models import Schedule, WorkCalendar, ScheduleActivity, ScheduleVersion, ScheduleReview, ScheduleBaseline
from ..services.gantt_editing import SCHEMA
from ..services.planning_boundaries import accepted_input_validation
from ..services.source_schedule_logic import verified_logic_payload
from ..simple_planning_views import SimplePlanningView
from . import test_source_schedule_logic as fixture
from . import test_simple_planning as simple_fixture


urlpatterns = [path('api/v1/planning-intelligence/projects/<int:project_id>/simple-plan/', SimplePlanningView.as_view()),
    *[path(f'api/v1/planning-intelligence/projects/<int:project_id>/simple-plan/{operation}/',
           SimplePlanningView.as_view(operation=operation)) for operation in
      ('preview-source-import', 'apply-source-import', 'preview-source-logic', 'apply-source-logic',
       'edit-activity', 'calculate', 'validate', 'submit', 'approve-publish', 'reopen')]]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class GanttSelectedEditTests(TestCase):
    post = fixture.SourceScheduleLogicTests.post
    preview = fixture.SourceScheduleLogicTests.preview
    apply = fixture.SourceScheduleLogicTests.apply

    def setUp(self):
        parse = fixture.project_document_plan
        def with_preserved_draft(project):
            project.simple_planning_state = {'revision': 12, 'state': 'review',
                'tasks': [{'id': f'preserved-mdr-{index}', 'title': f'Preserved register activity {index}',
                           'discipline': 'general', 'duration_days': None, 'depends_on': []} for index in range(220)],
                'assignment_token': 'preserved-mdr'}
            project.save(update_fields=['simple_planning_state'])
            return parse(project)
        with patch.object(fixture, 'project_document_plan', side_effect=with_preserved_draft):
            fixture.SourceScheduleLogicTests.setUp(self)
        self.simple_before = deepcopy(self.project.simple_planning_state)
        created = self.apply()
        self.original = ScheduleVersion.objects.get(pk=created['schedule_version_id'])
        self.original_rows = list(self.original.activities.order_by('pk').values())
        self.original_links = list(self.original.relationships.order_by('pk').values())

    def read(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def edit(self, code='A1', status=200, **patch):
        current = self.read()
        row = next(row for row in current['tasks'] if row['source_activity_id'] == code)
        return self.post('edit-activity', {'revision': current['revision'], 'task_id': row['id'], **patch}, status)

    def test_first_selected_edit_clones_all_rows_preserves_original_and_independent_draft(self):
        saved = self.edit(duration_days=4)
        self.project.refresh_from_db()
        changed = self.project.master_schedule_version
        self.assertNotEqual(changed.pk, self.original.pk)
        self.assertEqual(changed.parent_version_id, self.original.pk)
        self.assertEqual(changed.activities.count(), self.original.activities.count())
        self.assertEqual(changed.wbs_nodes.count(), self.original.wbs_nodes.count())
        self.assertEqual(changed.evidence_input_snapshot['schema'], SCHEMA)
        self.assertEqual(self.project.simple_planning_state, self.simple_before)
        self.assertEqual(list(self.original.activities.order_by('pk').values()), self.original_rows)
        self.assertEqual(list(self.original.relationships.order_by('pk').values()), self.original_links)
        self.assertIsNotNone(verified_logic_payload(self.original))
        task = next(row for row in saved['tasks'] if row['source_activity_id'] == 'A1')
        self.assertEqual(task['activity_code'], 'A1')
        self.assertEqual(task['duration_days'], 4)
        self.assertEqual(task['duration_source'], 'planner')
        self.assertEqual(task['source_start_date'], '2026-11-09')
        self.assertEqual(task['source_finish_date'], '2026-11-10')
        self.assertFalse(task['calculated'])
        self.assertIsNone(task['total_float_days'])
        self.assertTrue(saved['permissions']['can_edit_gantt'])
        self.assertTrue(saved['permissions']['can_calculate'])
        self.assertFalse(saved['permissions']['can_approve_publish'])
        again = self.edit(duration_days=3)
        self.assertEqual(again['version_id'], changed.pk)
        self.assertEqual(changed.schedule.versions.count(), 2)
        self.assertEqual(next(row for row in self.read()['tasks'] if row['source_activity_id'] == 'A1')['duration_days'], 3)

    def test_finish_anchor_recalculates_cpm_without_approving_or_mutating_source(self):
        edited = self.edit(timing_edit={'field': 'finish', 'value': '2026-11-11'})
        row = next(row for row in edited['tasks'] if row['source_activity_id'] == 'A1')
        self.assertEqual(row['planned_finish_date'], '2026-11-11')
        self.assertIsNone(row['planned_start_date'])
        self.assertEqual(row['planner_timing']['anchor'], 'finish')
        calculated = self.post('calculate', {'revision': edited['revision']})
        row = next(row for row in calculated['tasks'] if row['source_activity_id'] == 'A1')
        self.assertTrue(row['calculated'])
        self.assertEqual(row['planned_start_date'], '2026-11-10')
        self.assertEqual(row['planned_finish_date'], '2026-11-11')
        self.assertEqual(row['source_finish_date'], '2026-11-10')
        self.assertEqual(row['constraint_type'], 'must_finish')
        self.assertFalse(calculated['permissions']['can_approve_publish'])
        self.assertFalse(ScheduleBaseline.objects.exists())
        self.assertFalse(ScheduleReview.objects.exists())
        self.assertEqual(list(self.original.activities.order_by('pk').values()), self.original_rows)

    def test_null_duration_remains_missing_on_reload_and_blocks_calculation(self):
        edited = self.edit(duration_days=None)
        row = next(row for row in edited['tasks'] if row['source_activity_id'] == 'A1')
        self.assertIsNone(row['duration_days'])
        self.assertEqual(row['duration_source'], 'missing_source')
        self.assertFalse(edited['permissions']['can_calculate'])
        self.assertIsNone(next(row for row in self.read()['tasks'] if row['source_activity_id'] == 'A1')['duration_days'])
        self.post('calculate', {'revision': edited['revision']}, status=409)
        fixed = self.edit(duration_days=2)
        self.assertTrue(fixed['permissions']['can_calculate'])

    def test_typed_links_multiple_types_then_later_edit_and_removal_are_supported(self):
        state = self.read()
        keys = {row['source_activity_id']: row['id'] for row in state['tasks']}
        saved = self.edit('A2', dependency_details=[{'task_id': keys['A1'], 'type': 'SS', 'lag_days': 1},
                                                   {'task_id': keys['A1'], 'type': 'FF', 'lag_days': 2}])
        row = next(row for row in saved['tasks'] if row['source_activity_id'] == 'A2')
        self.assertEqual(row['depends_on'], [keys['A1']])
        self.assertEqual({link['type'] for link in row['dependency_details']}, {'SS', 'FF'})
        self.assertTrue(all(link['source'] == 'planner' for link in row['dependency_details']))
        self.edit('A2', duration_days=3)
        saved = self.edit('A2', dependency_details=[])
        self.assertEqual(next(row for row in saved['tasks'] if row['source_activity_id'] == 'A2')['depends_on'], [])
        self.assertEqual(list(self.original.relationships.order_by('pk').values()), self.original_links)

    def test_cycles_self_links_duplicate_types_stale_and_invalid_duration_are_atomic(self):
        state = self.read()
        keys = {row['source_activity_id']: row['id'] for row in state['tasks']}
        count = ScheduleVersion.objects.count()
        for links in ([{'task_id': keys['A5'], 'type': 'FS', 'lag_days': 0}],
                      [{'task_id': keys['A1'], 'type': 'FS', 'lag_days': 0}],
                      [{'task_id': keys['M1'], 'type': 'FS', 'lag_days': 0}] * 2):
            self.edit(dependency_details=links, status=409)
        self.edit(duration_days=0, status=409)
        self.edit(duration_days=99999999, status=400)
        self.edit('M1', duration_days=1, status=409)
        self.post('edit-activity', {'revision': state['revision'] - 1, 'task_id': keys['A1'], 'duration_days': 4}, status=409)
        self.assertEqual(ScheduleVersion.objects.count(), count)
        self.assertEqual(list(self.original.activities.order_by('pk').values()), self.original_rows)

    def test_viewer_history_pending_review_and_baseline_cannot_be_edited(self):
        state = self.read()
        task_id = state['tasks'][0]['id']
        self.client.force_authenticate(self.reviewer)
        self.post('edit-activity', {'revision': state['revision'], 'task_id': task_id, 'duration_days': 4}, status=403)
        self.client.force_authenticate(self.owner)
        response = self.client.post(self.url + f'edit-activity/?version_id={self.original.pk}',
            {'revision': state['revision'], 'task_id': task_id, 'duration_days': 4}, format='json')
        self.assertEqual(response.status_code, 409)
        review = ScheduleReview.objects.create(version=self.original, title='Pending', requested_by=self.owner,
                                               requested_at=timezone.now(), status='pending')
        self.edit(duration_days=4, status=409)
        review.status = 'cancelled'
        review.save()
        ScheduleBaseline.objects.create(schedule=self.original.schedule, source_version=self.original,
            name='Protected baseline', approved_by=self.owner, approved_at=timezone.now(), snapshot={})
        state = self.read()
        self.assertFalse(state['permissions'].get('can_edit_gantt', False))
        self.post('edit-activity', {'revision': state['revision'], 'task_id': task_id, 'duration_days': 4}, status=409)

    def test_uncontrolled_edit_cannot_be_blessed_by_another_gantt_change(self):
        self.edit(duration_days=3)
        self.project.refresh_from_db()
        version = self.project.master_schedule_version
        task = version.activities.order_by('pk').first()
        ScheduleActivity.objects.filter(pk=task.pk).update(duration_days=8)
        readiness = accepted_input_validation(version)
        self.assertFalse(readiness['ready_for_calculation'])
        self.assertIn('planner_revision_inputs_changed', {row['code'] for row in readiness['issues']})
        result = self.edit(duration_days=4, status=409)
        self.assertEqual(result['code'], 'planner_revision_inputs_changed')
        task.refresh_from_db()
        self.assertEqual(task.duration_days, 8)

    def test_sparse_bulk_edit_is_one_revision_and_invalid_member_rolls_back_everything(self):
        state = self.read()
        keys = {row['source_activity_id']: row['id'] for row in state['tasks']}
        result = self.post('edit-activity', {'revision': state['revision'], 'updates': [
            {'task_id': keys['A1'], 'duration_days': 3},
            {'task_id': keys['A2'], 'dependency_details': [{'task_id': keys['A1'], 'type': 'SS', 'lag_days': 1}]},
            {'task_id': keys['M1'], 'timing_edit': {'field': 'finish', 'value': '2026-11-19'}},
        ]})
        self.assertEqual(result['planner_revision']['edited_activity_count'], 3)
        version_id = result['version_id']
        revision = result['revision']
        self.post('edit-activity', {'revision': revision, 'updates': [
            {'task_id': keys['A1'], 'duration_days': 7},
            {'task_id': 'other-project-activity', 'duration_days': 2},
        ]}, status=404)
        reloaded = self.read()
        self.assertEqual(reloaded['version_id'], version_id)
        self.assertEqual(reloaded['revision'], revision)
        self.assertEqual(next(row for row in reloaded['tasks'] if row['source_activity_id'] == 'A1')['duration_days'], 3)


@override_settings(ROOT_URLCONF=__name__)
class GanttLegacyApprovalTests(TestCase):
    read = simple_fixture.SimplePlanningTests.read
    post = fixture.SourceScheduleLogicTests.post

    def setUp(self):
        simple_fixture.SimplePlanningTests.setUp(self)
        calendar = WorkCalendar.objects.create(project=self.project, name='Declared calendar', is_default=True,
                                              working_weekdays=[0, 1, 2, 3, 4])
        schedule = Schedule.objects.create(project=self.project, name='Manual schedule', code='MANUAL',
            planned_start=self.project.effective_date, default_calendar=calendar, created_by=self.owner)
        version = ScheduleVersion.objects.create(schedule=schedule, version=1, created_by=self.owner)
        ScheduleActivity.objects.create(version=version, external_id='MANUAL-A', name='Manual activity',
            duration_days=2, calendar=calendar, metadata={'duration_source': 'planner'})
        self.project.master_schedule_version = version
        self.project.save(update_fields=['master_schedule_version'])

    def test_manual_planner_revision_keeps_normal_review_and_approval_workflow(self):
        state = self.read()
        state = self.post('edit-activity', {'revision': state['revision'], 'task_id': 'MANUAL-A', 'duration_days': 3})
        self.assertFalse(state['permissions']['can_approve_publish'])
        self.assertFalse(ScheduleReview.objects.exists())
        state = self.post('calculate', {'revision': state['revision']})
        state = self.post('validate', {'revision': state['revision']})
        self.assertTrue(state['permissions']['can_submit'])
        self.assertFalse(ScheduleBaseline.objects.exists())
        state = self.post('submit', {'revision': state['revision'], 'approver_id': self.owner.pk})
        self.assertEqual(ScheduleReview.objects.get().status, 'pending')
        state = self.post('approve-publish', {'revision': state['revision'], 'name': 'Reviewed Gantt changes'})
        self.assertEqual(state['state'], 'baselined')
        baseline = ScheduleBaseline.objects.get()
        self.assertEqual(baseline.snapshot['activities'][0]['duration_days'], '3.00')
        frozen = deepcopy(baseline.snapshot)
        state = self.post('reopen', {'revision': state['revision']})
        self.assertTrue(state['permissions']['can_edit_gantt'])
        state = self.post('edit-activity', {'revision': state['revision'], 'task_id': 'MANUAL-A', 'duration_days': 4})
        self.assertTrue(state['permissions']['can_calculate'])
        state = self.post('calculate', {'revision': state['revision']})
        self.assertEqual(state['tasks'][0]['duration_days'], 4)
        baseline.refresh_from_db()
        self.assertEqual(baseline.snapshot, frozen)

    def test_unrelated_or_cyclic_ancestry_cannot_authorize_planner_revision(self):
        state = self.read()
        state = self.post('edit-activity', {'revision': state['revision'], 'task_id': 'MANUAL-A', 'duration_days': 3})
        version = ScheduleVersion.objects.get(pk=state['version_id'])
        original_snapshot = deepcopy(version.evidence_input_snapshot)
        unrelated = ScheduleVersion.objects.create(schedule=version.schedule, version=999, created_by=self.owner)
        version.evidence_input_snapshot['parent_version_id'] = unrelated.pk
        version.save(update_fields=['evidence_input_snapshot'])
        result = accepted_input_validation(version)
        self.assertFalse(result['ready_for_calculation'])
        self.assertIn('planner_revision_parent_invalid', {row['code'] for row in result['issues']})
        version.evidence_input_snapshot = original_snapshot
        version.parent_version = version
        version.save(update_fields=['parent_version', 'evidence_input_snapshot'])
        version = ScheduleVersion.objects.get(pk=version.pk)
        result = accepted_input_validation(version)
        self.assertFalse(result['ready_for_calculation'])
        self.assertIn('planner_revision_parent_invalid', {row['code'] for row in result['issues']})
