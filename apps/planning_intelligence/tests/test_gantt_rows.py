"""Explicit hierarchy edits preserve source versions and leave no live links behind."""
from copy import deepcopy

from django.test import TestCase, override_settings
from django.urls import path
from django.utils import timezone

from apps.rbac.route_guard import secure_module_endpoints
from apps.core.project_models import ProjectTask
from ..models import (Schedule, ScheduleActivity, ScheduleVersion, ScheduleWBSNode, ScheduleBaseline,
                      ScheduleReview, WorkCalendar)
from ..services.planning_boundaries import accepted_input_validation
from ..simple_planning_views import SimplePlanningView
from . import test_gantt_selected_edits as selected_fixture
from . import test_simple_planning as draft_fixture
from . import test_planning_builds as build_fixture


urlpatterns = [path('api/v1/planning-intelligence/projects/<int:project_id>/simple-plan/', SimplePlanningView.as_view()),
    *[path(f'api/v1/planning-intelligence/projects/<int:project_id>/simple-plan/{operation}/',
           SimplePlanningView.as_view(operation=operation)) for operation in
      ('preview-source-import', 'apply-source-import', 'preview-source-logic', 'apply-source-logic',
       'edit-activity', 'edit-row', 'calculate', 'validate', 'submit', 'approve-publish', 'reopen')]]
secure_module_endpoints(urlpatterns)


class RowCommands:
    def edit_row(self, kind, key, action='rename', title='Planner name', revision=None, status=200):
        payload = {'revision': self.read()['revision'] if revision is None else revision,
                   'kind': kind, 'id': str(key), 'action': action}
        if action == 'rename':
            payload['title'] = title
        response = self.client.post(self.url + 'edit-row/', payload, format='json')
        self.assertEqual(response.status_code, status, response.data)
        return response.data


@override_settings(ROOT_URLCONF=__name__)
class DraftRowEditTests(RowCommands, TestCase):
    setUp = draft_fixture.SimplePlanningTests.setUp
    read = draft_fixture.SimplePlanningTests.read
    save = draft_fixture.SimplePlanningTests.save
    task = draft_fixture.SimplePlanningTests.task

    def test_activity_rename_delete_and_reload_clean_all_incident_links(self):
        self.save([self.task('a'), self.task('b', depends_on=['a'],
                   dependency_details=[{'task_id': 'a', 'type': 'SS', 'lag_days': 2}])])
        renamed = self.edit_row('activity', 'a', title='Updated activity')
        self.assertEqual(renamed['tasks'][0]['title'], 'Updated activity')
        self.assertEqual(renamed['tasks'][0]['id'], 'a')
        state = self.edit_row('activity', 'a', action='delete')
        self.assertEqual([task['id'] for task in state['tasks']], ['b'])
        self.assertEqual(state['tasks'][0]['depends_on'], [])
        self.assertEqual(state['tasks'][0]['dependency_details'], [])
        self.assertEqual(self.read()['revision'], state['revision'])

    def test_manual_phase_and_deliverable_rename_keep_ids_then_delete_only_subtree(self):
        self.save([self.task('a', wbs_phase='Design', wbs_deliverable='Drawing'),
                   self.task('b', wbs_phase='Design', wbs_deliverable='Calculation'),
                   self.task('c', wbs_phase='Site', wbs_deliverable='Survey', depends_on=['a'])])
        initial = self.read()
        phase = next(row for row in initial['wbs_nodes'] if row['name'] == 'Design')
        drawing = next(row for row in initial['wbs_nodes'] if row['name'] == 'Drawing')
        state = self.edit_row('wbs', phase['id'], title='Engineering')
        self.assertEqual(next(row for row in state['wbs_nodes'] if row['id'] == phase['id'])['name'], 'Engineering')
        state = self.edit_row('wbs', drawing['id'], title='Final drawing')
        self.assertEqual(next(row for row in state['wbs_nodes'] if row['id'] == drawing['id'])['name'], 'Final drawing')
        # The ordinary activity form must retain the server-owned WBS identity.
        state = self.save(state['tasks'], revision=state['revision'])
        self.assertIn(drawing['id'], [row['id'] for row in state['wbs_nodes']])
        state = self.edit_row('wbs', phase['id'], action='delete')
        self.assertEqual([task['id'] for task in state['tasks']], ['c'])
        self.assertEqual(state['tasks'][0]['depends_on'], [])
        self.assertFalse(any(row['id'] == phase['id'] for row in state['wbs_nodes']))

    def test_workstream_rename_delete_and_duplicate_manual_names(self):
        self.save([self.task('a'), self.task('b', wbs_phase='Design'), self.task('c', wbs_phase='Site')])
        state = self.edit_row('discipline', 'testing', title='Quality assurance')
        self.assertEqual(state['disciplines'][0]['name'], 'Quality assurance')
        site = next(row for row in state['wbs_nodes'] if row['name'] == 'Site')
        self.edit_row('wbs', site['id'], title='Design', status=409)
        state = self.edit_row('wbs', 'draft:testing', action='delete')
        self.assertEqual({task['id'] for task in state['tasks']}, {'b', 'c'})
        state = self.edit_row('discipline', 'testing', action='delete')
        self.assertEqual(state['tasks'], [])
        self.assertEqual(state['wbs_nodes'], [])

    def workflow(self):
        self.save([self.task(f's{index}', depends_on=[f's{index - 1}'] if index else []) for index in range(5)])
        self.project.refresh_from_db()
        state = self.project.simple_planning_state
        parent = {'id': 'drawing', 'title': 'Source drawing', 'discipline': 'testing',
                  'workflow_task_ids': [f's{index}' for index in range(5)]}
        state['deliverables'] = [parent]
        state['workflow_mode'] = 'standard_five'
        for index, task in enumerate(state['tasks']):
            task.update(parent_deliverable_id='drawing', source_deliverable=deepcopy(parent),
                        deliverable='Source drawing', workflow_stage_code=f'S{index}', workflow_stage_sequence=index)
        self.project.save(update_fields=['simple_planning_state'])

    def test_workflow_explicit_stage_delete_updates_parent_and_allows_later_activity_save(self):
        self.workflow()
        state = self.read()
        omitted = deepcopy(state['tasks'][1:])
        omitted[0]['depends_on'] = []
        omitted[0]['dependency_details'] = []
        response = self.client.put(self.url, {'revision': state['revision'], 'tasks': omitted}, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'workflow_five_stages_required')
        state = self.edit_row('activity', 's1', action='delete')
        self.assertEqual(state['deliverables'][0]['workflow_task_ids'], ['s0', 's2', 's3', 's4'])
        self.assertEqual(next(task for task in state['tasks'] if task['id'] == 's2')['depends_on'], [])
        state = self.save(state['tasks'], revision=state['revision'])
        self.assertEqual(len(state['tasks']), 4)
        state = self.edit_row('deliverable', 'drawing', title='Planner drawing')
        self.assertEqual(state['deliverables'][0]['title'], 'Planner drawing')
        self.assertTrue(all(task['source_deliverable']['title'] == 'Planner drawing' for task in state['tasks']))
        state = self.edit_row('deliverable', 'drawing', action='delete')
        self.assertEqual(state['tasks'], [])
        self.assertEqual(state['deliverables'], [])

    def test_missing_foreign_root_stale_and_history_commands_do_not_write(self):
        state = self.save()
        self.project.refresh_from_db()
        before = deepcopy(self.project.simple_planning_state)
        self.edit_row('activity', 'foreign-row', status=404)
        self.edit_row('wbs', 'manual:foreign-node', action='delete', status=404)
        self.edit_row('wbs', f'project:{self.project.pk}', action='delete', status=409)
        self.edit_row('activity', state['tasks'][0]['id'], revision=state['revision'] - 1, status=409)
        for suffix, extra in [('?version_id=1', {}), ('', {'viewing_history': True})]:
            response = self.client.post(self.url + 'edit-row/' + suffix, {
                'revision': state['revision'], 'kind': 'activity', 'id': state['tasks'][0]['id'],
                'action': 'delete', **extra}, format='json')
            self.assertEqual(response.status_code, 409)
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, before)

    def test_viewer_and_submitted_draft_are_read_only(self):
        state = self.save()
        self.client.force_authenticate(self.reviewer)
        self.edit_row('activity', 'task-a', revision=state['revision'], status=403)
        self.client.force_authenticate(self.owner)
        self.project.refresh_from_db()
        self.project.simple_planning_state['state'] = 'submitted'
        self.project.save(update_fields=['simple_planning_state'])
        self.edit_row('activity', 'task-a', revision=state['revision'], status=403)

    def test_delete_withdraws_employee_work_without_erasing_progress_or_unmanaged_work(self):
        self.save()
        assigned = ProjectTask.objects.create(project=self.enterprise, title='Employee work',
            assigned_to=self.reviewer, source_key=f'wbs:{self.project.pk}:task-a',
            status='in_progress', progress_percent=40, metadata={'source': 'work_breakdown'})
        unmanaged = ProjectTask.objects.create(project=self.enterprise, title='Independent work',
            assigned_to=self.reviewer, source_key=f'wbs:{self.project.pk}:unmanaged',
            metadata={'source': 'work_breakdown'})
        self.edit_row('activity', 'task-a', action='delete')
        assigned.refresh_from_db()
        unmanaged.refresh_from_db()
        self.assertTrue(assigned.is_deleted)
        self.assertEqual(assigned.status, 'in_progress')
        self.assertEqual(assigned.progress_percent, 40)
        self.assertFalse(unmanaged.is_deleted)

    def test_last_deliverable_delete_retains_a_shared_persisted_project_wrapper(self):
        calendar = WorkCalendar.objects.create(project=self.project, name='Planner calendar', working_weekdays=[0, 1, 2, 3, 4])
        schedule = Schedule.objects.create(project=self.project, name='Planner schedule', code='PLAN',
            planned_start=self.project.effective_date, default_calendar=calendar, created_by=self.owner)
        version = ScheduleVersion.objects.create(schedule=schedule, version=1, created_by=self.owner)
        root = ScheduleWBSNode.objects.create(version=version, code=self.enterprise.code, name=self.project.name)
        parent = {'id': 'package', 'title': 'Package', 'discipline': 'testing', 'workflow_task_ids': ['first', 'last']}
        for key in parent['workflow_task_ids']:
            ScheduleActivity.objects.create(version=version, wbs_node=root, external_id=key, name=key,
                calendar=calendar, duration_days=2, discipline='testing', metadata={
                    'parent_deliverable_id': 'package', 'source_deliverable': deepcopy(parent),
                    'workflow_stage_code': key.upper(), 'duration_source': 'planner'})
        self.project.master_schedule_version = version
        self.project.save(update_fields=['master_schedule_version'])
        state = self.edit_row('deliverable', 'package', title='Planner package')
        self.assertEqual(state['wbs_nodes'][0]['name'], self.project.name)
        state = self.edit_row('deliverable', 'package', action='delete')
        self.assertEqual(state['tasks'], [])
        self.assertEqual(len(state['wbs_nodes']), 1)
        self.assertEqual(state['wbs_nodes'][0]['name'], self.project.name)


@override_settings(ROOT_URLCONF=__name__)
class SelectedRowEditTests(RowCommands, TestCase):
    setUp = selected_fixture.GanttSelectedEditTests.setUp
    read = selected_fixture.GanttSelectedEditTests.read
    post = selected_fixture.GanttSelectedEditTests.post
    preview = selected_fixture.GanttSelectedEditTests.preview
    apply = selected_fixture.GanttSelectedEditTests.apply

    def test_canonical_rename_clones_source_reuses_revision_and_preserves_evidence(self):
        original = self.read()
        task = original['tasks'][0]
        renamed = self.edit_row('activity', task['id'], title='Planner activity')
        self.assertNotEqual(renamed['version_id'], self.original.pk)
        self.assertEqual(next(row for row in renamed['tasks'] if row['id'] == task['id'])['title'], 'Planner activity')
        node = next(node for node in renamed['wbs_nodes'] if node['name'] == 'Drawing')
        changed = self.edit_row('wbs', node['id'], title='Planner package')
        self.assertEqual(changed['version_id'], renamed['version_id'])
        self.assertEqual(next(row for row in changed['wbs_nodes'] if row['id'] == node['id'])['name'], 'Planner package')
        self.assertFalse(changed['calculation_available'])
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, self.simple_before)
        self.assertEqual(list(self.original.activities.order_by('pk').values()), self.original_rows)
        self.assertEqual(list(self.original.relationships.order_by('pk').values()), self.original_links)
        version = ScheduleVersion.objects.get(pk=changed['version_id'])
        self.assertTrue(accepted_input_validation(version)['ready_for_calculation'])

    def test_delete_activity_removes_links_and_does_not_delete_original_rows(self):
        state = self.read()
        task = next(task for task in state['tasks'] if task['source_activity_id'] == 'A2')
        state = self.edit_row('activity', task['id'], action='delete')
        version = ScheduleVersion.objects.get(pk=state['version_id'])
        deleted = version.activities.get(external_id=task['id'])
        self.assertTrue(deleted.is_deleted)
        self.assertFalse(version.relationships.filter(is_deleted=False, predecessor=deleted).exists())
        self.assertFalse(version.relationships.filter(is_deleted=False, successor=deleted).exists())
        self.assertTrue(all(task['id'] not in row['depends_on'] for row in state['tasks']))
        self.assertEqual(self.original.activities.filter(is_deleted=False).count(), 6)

    def test_delete_wbs_subtree_removes_children_and_leaves_other_project_branches(self):
        state = self.read()
        node = next(node for node in state['wbs_nodes'] if node['name'] == 'Drawing')
        state = self.edit_row('wbs', node['id'], action='delete')
        self.assertEqual([task['source_activity_id'] for task in state['tasks']], ['M1'])
        self.assertFalse(any(row['code'] == node['code'] for row in state['wbs_nodes']))
        version = ScheduleVersion.objects.get(pk=state['version_id'])
        self.assertFalse(version.relationships.filter(is_deleted=False).exists())
        self.assertEqual(self.original.wbs_nodes.filter(is_deleted=False).count(), 2)

    def test_stale_foreign_history_and_pending_review_do_not_clone(self):
        state = self.read()
        count = ScheduleVersion.objects.count()
        self.edit_row('wbs', '9999999', action='delete', status=404)
        self.edit_row('activity', state['tasks'][0]['id'], revision=state['revision'] - 1, status=409)
        response = self.client.post(self.url + f'edit-row/?version_id={self.original.pk}',
            {'revision': state['revision'], 'kind': 'activity', 'id': state['tasks'][0]['id'], 'action': 'delete'}, format='json')
        self.assertEqual(response.status_code, 409)
        ScheduleReview.objects.create(version=self.original, title='Pending', requested_by=self.owner,
                                      requested_at=timezone.now(), status='pending')
        self.edit_row('activity', state['tasks'][0]['id'], status=409)
        self.assertEqual(ScheduleVersion.objects.count(), count)

    def test_persisted_project_root_is_protected_before_and_after_cloning(self):
        state = self.read()
        root = next(node for node in state['wbs_nodes'] if node.get('is_source_project'))
        self.edit_row('wbs', root['id'], action='delete', status=409)
        state = self.edit_row('activity', state['tasks'][0]['id'])
        copied = next(node for node in state['wbs_nodes'] if node['code'] == root['code'])
        result = self.edit_row('wbs', copied['id'], action='delete', status=409)
        self.assertEqual(result['code'], 'gantt_project_root_read_only')

    def test_canonical_workflow_membership_and_titles_survive_partial_delete_reload(self):
        # Select the five source activities under the Drawing WBS, not M1.
        activities = list(self.original.activities.filter(wbs_node__name='Drawing').order_by('sort_order'))
        self.assertEqual(len(activities), 5)
        parent = {'id': 'drawing', 'title': 'Source drawing', 'discipline': activities[0].discipline or 'general',
                  'workflow_task_ids': [row.external_id for row in activities]}
        for row in activities:
            row.metadata = {**row.metadata, 'parent_deliverable_id': 'drawing', 'source_deliverable': deepcopy(parent)}
            row.save(update_fields=['metadata'])
        state = self.read()
        group = next(node for node in state['wbs_nodes'] if node['name'] == 'Drawing')
        state = self.edit_row('wbs', group['id'], title='Container label')
        self.assertEqual(state['deliverables'][0]['title'], 'Source drawing')
        state = self.edit_row('deliverable', 'drawing', title='Planner deliverable')
        self.assertEqual(state['deliverables'][0]['title'], 'Planner deliverable')
        state = self.edit_row('activity', activities[1].external_id, action='delete')
        members = [row.external_id for row in activities if row.pk != activities[1].pk]
        self.assertEqual(state['deliverables'][0]['workflow_task_ids'], members)
        state = self.read()
        self.assertEqual(state['deliverables'][0]['title'], 'Planner deliverable')
        self.assertEqual(state['deliverables'][0]['workflow_task_ids'], members)
        state = self.edit_row('deliverable', 'drawing', action='delete')
        self.assertEqual(state['deliverables'], [])
        self.assertFalse(any(node['name'] == 'Planner deliverable' for node in state['wbs_nodes']))
        self.assertEqual([task['source_activity_id'] for task in state['tasks']], ['M1'])

    def test_baseline_and_viewer_remain_protected(self):
        state = self.read()
        self.client.force_authenticate(self.reviewer)
        self.edit_row('activity', state['tasks'][0]['id'], revision=state['revision'], status=403)
        self.client.force_authenticate(self.owner)
        baseline = ScheduleBaseline.objects.create(schedule=self.original.schedule, source_version=self.original,
            name='Unchanged baseline', approved_by=self.owner, approved_at=timezone.now(), snapshot={'source': 'retained'})
        self.edit_row('activity', state['tasks'][0]['id'], action='delete', status=409)
        baseline.refresh_from_db()
        self.assertEqual(baseline.snapshot, {'source': 'retained'})

    def test_generic_wbs_edit_is_detected_by_both_row_and_cell_editors(self):
        state = self.edit_row('activity', self.read()['tasks'][0]['id'])
        node = state['wbs_nodes'][0]
        ScheduleWBSNode.objects.filter(pk=node['id']).update(name='Uncontrolled change')
        self.edit_row('wbs', node['id'], status=409)
        state = self.read()
        result = self.post('edit-activity', {'revision': state['revision'],
            'task_id': state['tasks'][0]['id'], 'duration_days': 3}, status=409)
        self.assertEqual(result['code'], 'planner_revision_inputs_changed')


@override_settings(ROOT_URLCONF=__name__)
class BuiltWorkflowRowEditTests(RowCommands, TestCase):
    decision = build_fixture.PlanningBuildTests.decision
    preview = build_fixture.PlanningBuildTests.preview
    apply = build_fixture.PlanningBuildTests.apply
    read = draft_fixture.SimplePlanningTests.read

    def setUp(self):
        build_fixture.PlanningBuildTests.setUp(self)
        self.build = self.preview()
        self.original = self.apply(self.build)
        self.project.master_schedule_version = self.original
        self.project.save(update_fields=['master_schedule_version'])
        self.url = f'/api/v1/planning-intelligence/projects/{self.project.pk}/simple-plan/'

    def test_build_deliverable_title_and_membership_override_preserve_accepted_evidence(self):
        original_evidence = deepcopy(self.build.evidence_snapshot)
        state = self.read()
        parent = state['deliverables'][0]
        others = deepcopy(state['deliverables'][1:])
        state = self.edit_row('deliverable', parent['id'], title='Planner package')
        self.assertEqual(next(row for row in state['deliverables'] if row['id'] == parent['id'])['title'], 'Planner package')
        stage = next(task for task in state['tasks'] if str(task.get('parent_deliverable_id')) == str(parent['id']))
        state = self.edit_row('activity', stage['id'], action='delete')
        reloaded = self.read()
        changed = next(row for row in reloaded['deliverables'] if row['id'] == parent['id'])
        self.assertEqual(changed['title'], 'Planner package')
        self.assertEqual(len(changed['workflow_task_ids']), 4)
        self.assertNotIn(stage['id'], changed['workflow_task_ids'])
        for task in reloaded['tasks']:
            self.assertNotIn(stage['id'], task['depends_on'])
        state = self.edit_row('deliverable', parent['id'], action='delete')
        self.assertEqual([row['id'] for row in state['deliverables']], [row['id'] for row in others])
        self.assertEqual(len(state['tasks']), 5)
        self.assertFalse(any(node['name'] == 'Planner package' for node in state['wbs_nodes']))
        self.build.refresh_from_db()
        self.assertEqual(self.build.evidence_snapshot, original_evidence)
        self.assertEqual(self.original.activities.filter(is_deleted=False).count(), 10)
