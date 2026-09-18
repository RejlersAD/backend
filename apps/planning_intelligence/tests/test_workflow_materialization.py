"""Expanded workflows persist as source groups and typed executable activities."""
from copy import deepcopy
from datetime import date
from decimal import Decimal

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from apps.core.project_models import ProjectTask

from ..models import ActivityAssignment, PlanningProject, ScheduleVersion, WorkflowStage, WorkflowTemplate
from ..services.simple_workflow_expansion import expand_workflow_deliverables
from ..services.work_breakdown import WorkBreakdownConflict, materialize_work_breakdown
from . import test_simple_planning as simple_fixture


class WorkflowMaterializationTests(TestCase):
    def setUp(self):
        self.project = PlanningProject.objects.create(
            name='Workflow persistence fixture', effective_date=date(2026, 1, 6),
            planned_end_date=date(2026, 9, 4),
        )
        codes = ['IFR', 'COMPANY_REVIEW', 'IFA', 'COMPANY_APPROVAL', 'FINAL_ISSUE']
        self.context = {'project_id': self.project.pk, 'default_template_id': 1, 'templates': [{
            'id': 1, 'code': 'STANDARD_5_STAGE', 'version': 1, 'project_id': None,
            'stages': [{'code': code, 'name': code.replace('_', ' '), 'sequence': index + 1,
                        'duration_days': [10, 10, 5, 5, 1][index], 'responsible_party': 'COMPANY' if index in (1, 3) else 'Engineer',
                        'activity_type': 'task', 'relationship': 'FS' if index else '', 'lag_days': 0,
                        'progress_weight': 20, 'is_release_gate': True}
                       for index, code in enumerate(codes)],
        }]}

    def parent(self, key='deliverable-a', **values):
        return {'id': key, 'title': 'Exact MATERIAL TAKE-OFF (MTO)', 'discipline': 'electrical',
                'depends_on': [], 'owner': 'Named employee', 'assignee_id': 'employee-id',
                'reviewer': '', 'reviewer_id': None, 'effort_hours': 40,
                'due_date': '2026-02-20', 'due_date_source': 'explicit',
                'priority': 'high', 'task_type': 'deliverable', 'acceptance_criteria': 'Review quantities',
                'source_title': 'Exact MATERIAL TAKE-OFF (MTO)',
                'document_number': 'EL-MTO-001', 'document_revision': 'B',
                'source_references': [{'file_id': 22, 'locator': {'sheet': 'MDR', 'row': 16}}],
                'planned_start_date': '2026-01-06', 'duration_days': 31, **values}

    def draft(self, parents=None):
        deliverables, tasks, _ = expand_workflow_deliverables(parents or [self.parent()], self.context)
        return {'revision': 2, 'deliverables': deliverables, 'tasks': tasks,
                'disciplines': [{'code': 'electrical', 'name': 'Electrical Engineering'},
                                {'code': 'hvac', 'name': 'HVAC'}]}

    def materialize(self, draft):
        return materialize_work_breakdown(self.project, draft, actor=None,
                                         start=self.project.effective_date, token='test-workflow')

    def test_five_leaves_have_dedicated_parent_wbs_and_complete_source_snapshot(self):
        draft = self.draft()
        original = deepcopy(draft)
        version = self.materialize(draft)
        self.assertEqual(draft, original)
        activities = list(version.activities.select_related('wbs_node__parent').order_by('sort_order'))
        self.assertEqual(len(activities), 5)
        self.assertEqual(version.wbs_nodes.count(), 2)
        node = activities[0].wbs_node
        self.assertIsNotNone(node.parent_id)
        self.assertEqual(node.name, self.parent()['title'])
        self.assertEqual(node.parent.name, 'Electrical Engineering')
        self.assertEqual(node.level, node.parent.level + 1)
        self.assertTrue(all(row.wbs_node_id == node.pk for row in activities))
        self.assertFalse(version.activities.filter(name=self.parent()['title']).exists())
        for activity in activities:
            metadata = activity.metadata
            parent = metadata['source_deliverable']
            self.assertEqual(parent['id'], self.parent()['id'])
            self.assertEqual(parent['title'], self.parent()['title'])
            self.assertEqual(parent['wbs_node_id'], node.pk)
            self.assertEqual(parent['parent_wbs_node_id'], node.parent_id)
            self.assertNotEqual(parent['wbs_node_id'], parent['parent_wbs_node_id'])
            self.assertEqual(parent['workflow_task_ids'], [row.external_id for row in activities])
            self.assertEqual(metadata['source_title'], self.parent()['source_title'])
            self.assertEqual(metadata['source_references'], self.parent()['source_references'])
            self.assertEqual(metadata['document_number'], 'EL-MTO-001')
            self.assertEqual(metadata['document_revision'], 'B')
            self.assertEqual(metadata['workflow_template_code'], 'STANDARD_5_STAGE')
            self.assertEqual(metadata['workflow_template_version'], 1)
            self.assertEqual(metadata['workflow_progress_weight'], 20)
            self.assertTrue(metadata['workflow_release_gate'])
        self.assertEqual(activities[0].external_id, self.parent()['id'])
        self.assertEqual(activities[0].responsible_role, 'Engineer')
        self.assertEqual(activities[0].metadata['owner'], 'Named employee')
        self.assertEqual(activities[1].responsible_role, 'COMPANY')
        self.assertEqual(activities[1].metadata['owner'], '')
        self.assertEqual(activities[0].constraint_type, 'start_no_earlier')
        self.assertTrue(all(row.constraint_type == 'none' for row in activities[1:]))
        self.assertEqual(ActivityAssignment.objects.filter(activity__version=version).count(), 1)
        allocation = ActivityAssignment.objects.get(activity__version=version)
        self.assertEqual(allocation.activity_id, activities[0].pk)
        self.assertEqual(allocation.budgeted_hours, Decimal('40'))
        self.assertEqual(activities[0].metadata['assignee_id'], 'employee-id')
        self.assertEqual(activities[0].metadata['due_date'], '2026-02-20')
        self.assertTrue(all(row.metadata['assignee_id'] is None and row.metadata['due_date'] is None for row in activities[1:]))

    def test_duplicate_titles_remain_distinct_source_groups_in_each_discipline(self):
        parents = [self.parent('a'), self.parent('b'), self.parent('c', discipline='hvac')]
        version = self.materialize(self.draft(parents))
        groups = list(version.wbs_nodes.exclude(parent=None))
        self.assertEqual(len(groups), 3)
        self.assertEqual(len({row.code for row in groups}), 3)
        self.assertEqual(version.wbs_nodes.filter(parent=None).count(), 2)
        self.assertTrue(all(row.activities.count() == 5 for row in groups))
        reconstructed = {row.metadata['source_deliverable']['id']: row.metadata['source_deliverable']
                         for row in version.activities.all()}
        self.assertEqual(set(reconstructed), {'a', 'b', 'c'})
        self.assertEqual(len({row['wbs_node_id'] for row in reconstructed.values()}), 3)

    def test_zero_milestones_and_typed_lagged_relationships_survive_persistence(self):
        stages = self.context['templates'][0]['stages']
        stages[0].update(activity_type='start_milestone', duration_days=0)
        stages[1].update(relationship='SS', lag_days=2.5)
        stages[2].update(relationship='FF', lag_days=-1)
        stages[-1].update(activity_type='finish_milestone', duration_days=0)
        draft = self.draft([self.parent('a'), self.parent('b', depends_on=['a'],
                                                      dependency_details=[{'task_id': 'a', 'type': 'FS', 'lag_days': 3,
                                                                           'source': 'planner', 'status': 'confirmed',
                                                                           'rationale': 'Released input', 'source_references': [{'file_id': 3}]}])])
        version = self.materialize(draft)
        activities = list(version.activities.order_by('sort_order'))
        self.assertEqual(activities[0].activity_type, 'start_milestone')
        self.assertEqual(activities[4].activity_type, 'finish_milestone')
        self.assertTrue(activities[0].is_milestone)
        self.assertEqual(activities[0].duration_days, Decimal('0'))
        ss = version.relationships.get(predecessor=activities[0], successor=activities[1])
        self.assertEqual((ss.relationship_type, ss.lag_days), ('SS', Decimal('2.5')))
        self.assertEqual(ss.metadata['source'], 'workflow_template')
        self.assertEqual(ss.metadata['status'], 'proposed')
        ff = version.relationships.get(predecessor=activities[1], successor=activities[2])
        self.assertEqual((ff.relationship_type, ff.lag_days), ('FF', Decimal('-1')))
        cross = version.relationships.get(predecessor=activities[4], successor=activities[5])
        self.assertEqual((cross.relationship_type, cross.lag_days), ('FS', Decimal('3')))
        self.assertEqual(cross.metadata['source'], 'planner')
        self.assertEqual(cross.metadata['rationale'], 'Released input')
        self.assertEqual(cross.metadata['source_references'], [{'file_id': 3}])
        self.assertEqual(cross.metadata['parent_predecessor_id'], 'a')

    def test_nonexpanded_draft_keeps_existing_flat_nodes_owner_and_fs_behavior(self):
        first = self.parent('a')
        second = self.parent('b', depends_on=['a'], owner='', effort_hours=None,
                             dependency_details=[{'task_id': 'a', 'type': 'SS', 'lag_days': 3}])
        version = self.materialize({'revision': 1, 'tasks': [first, second], 'disciplines': []})
        self.assertEqual(version.activities.count(), 2)
        self.assertEqual(version.wbs_nodes.count(), 1)
        self.assertIsNone(version.wbs_nodes.get().parent_id)
        self.assertEqual(version.activities.get(external_id='a').responsible_role, 'Named employee')
        self.assertNotIn('source_deliverable', version.activities.get(external_id='a').metadata)
        relationship = version.relationships.get()
        self.assertEqual((relationship.relationship_type, relationship.lag_days), ('FS', Decimal('0')))

    def test_invalid_workflow_rolls_back_all_schedule_records(self):
        draft = self.draft()
        draft['tasks'][-1]['discipline'] = 'hvac'
        with self.assertRaises(WorkBreakdownConflict):
            self.materialize(draft)
        self.assertFalse(self.project.schedules.exists())
        self.assertFalse(self.project.work_calendars.exists())
        self.assertFalse(ScheduleVersion.objects.exists())

    def test_materialization_is_idempotent_for_existing_editable_version(self):
        draft = self.draft()
        version = self.materialize(draft)
        draft['schedule_version_id'] = version.pk
        self.assertEqual(self.materialize(draft).pk, version.pk)
        self.assertEqual(self.project.schedules.get().versions.count(), 1)
        self.assertEqual(version.activities.count(), 5)


@override_settings(ROOT_URLCONF='apps.planning_intelligence.tests.test_simple_planning')
class WorkflowPlanningIntegrationTests(TestCase):
    # Reuse fixture helpers without inheriting and rerunning the entire suite.
    task = simple_fixture.SimplePlanningTests.task
    read = simple_fixture.SimplePlanningTests.read
    save = simple_fixture.SimplePlanningTests.save
    action = simple_fixture.SimplePlanningTests.action

    def setUp(self):
        simple_fixture.SimplePlanningTests.setUp(self)
        self.template = WorkflowTemplate.objects.create(
            project=self.project, code='STANDARD_5_STAGE', name='Project five stages',
            version=1, status='active',
        )
        self.stages = []
        for index, code in enumerate(['IFR', 'COMPANY_REVIEW', 'IFA', 'COMPANY_APPROVAL', 'FINAL_ISSUE']):
            self.stages.append(WorkflowStage.objects.create(
                template=self.template, sequence=index + 1, code=code, name=code.replace('_', ' '),
                duration_days=[10, 10, 5, 5, 1][index], responsible_party='COMPANY' if index in (1, 3) else 'Engineer',
                relationship_to_previous='FS' if index else '', lag_days=0,
            ))

    def expanded(self):
        saved = self.save()
        preview = self.action('propose-schedule', saved['revision'], workflow_mode='standard_five')
        applied = self.action('apply-schedule', saved['revision'], proposal_token=preview['proposal']['token'])
        return preview, applied

    def test_uniform_preview_is_read_only_signed_and_apply_is_idempotent(self):
        saved = self.save()
        self.project.refresh_from_db()
        before = deepcopy(self.project.simple_planning_state)
        with CaptureQueriesContext(connection) as queries:
            preview = self.action('propose-schedule', saved['revision'], workflow_mode='standard_five')
        self.assertFalse(any(query['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for query in queries.captured_queries))
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, before)
        self.assertEqual(preview['proposal']['task_count'], 5)
        self.assertEqual(preview['proposal']['deliverable_count'], 1)
        self.assertEqual(preview['plan']['deliverables'][0]['title'], 'Test application')
        self.assertEqual(preview['plan']['tasks'][0]['id'], 'task-a')
        self.assertTrue(preview['proposal']['token'])
        invalid = self.client.post(self.url + 'apply-schedule/', {
            'revision': saved['revision'], 'proposal_token': preview['proposal']['token'] + 'tampered',
        }, format='json')
        self.assertEqual(invalid.status_code, 409)
        applied = self.action('apply-schedule', saved['revision'], proposal_token=preview['proposal']['token'])
        repeated = self.action('apply-schedule', saved['revision'], proposal_token=preview['proposal']['token'])
        self.assertEqual(applied['revision'], repeated['revision'])
        self.assertEqual(applied['tasks'], repeated['tasks'])
        self.assertEqual(applied['deliverables'], repeated['deliverables'])
        self.assertFalse(ScheduleVersion.objects.filter(schedule__project=self.project).exists())

    def test_completed_employee_parent_blocks_apply_without_changing_work_history(self):
        saved = self.save()
        self.project.refresh_from_db()
        state = deepcopy(self.project.simple_planning_state)
        record = ProjectTask.objects.create(
            project=self.enterprise, title='Completed deliverable', assigned_to=self.other,
            status='completed', progress_percent=100,
            source_key=f'wbs:{self.project.pk}:task-a',
            metadata={'preview_confirmed_at': state['assignment_token']},
        )
        preview = self.action('propose-schedule', saved['revision'], workflow_mode='standard_five')
        self.assertEqual(preview['proposal']['expansion_blockers'][0]['code'], 'workflow_completed_parent')
        response = self.client.post(self.url + 'apply-schedule/', {
            'revision': saved['revision'], 'proposal_token': preview['proposal']['token'],
        }, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'workflow_expansion_blocked')
        self.project.refresh_from_db()
        record.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, state)
        self.assertEqual(record.status, 'completed')
        self.assertEqual(record.title, 'Completed deliverable')
        self.assertFalse(record.is_deleted)

    def test_employee_completion_after_preview_rejects_apply_and_preserves_original_work(self):
        saved = self.save()
        self.project.refresh_from_db()
        state = deepcopy(self.project.simple_planning_state)
        record = ProjectTask.objects.create(
            project=self.enterprise, title='Original employee deliverable', assigned_to=self.other,
            status='todo', progress_percent=0,
            source_key=f'wbs:{self.project.pk}:task-a',
            metadata={'preview_confirmed_at': state['assignment_token'], 'history_note': 'Keep original work'},
        )
        preview = self.action('propose-schedule', saved['revision'], workflow_mode='standard_five')
        self.assertFalse(preview['proposal'].get('expansion_blockers'))

        # Work Hub progress can change independently of the planning revision.
        record.status = 'completed'
        record.progress_percent = 100
        record.save(update_fields=['status', 'progress_percent'])
        response = self.client.post(self.url + 'apply-schedule/', {
            'revision': saved['revision'], 'proposal_token': preview['proposal']['token'],
        }, format='json')

        self.assertEqual(response.status_code, 409, response.data)
        self.assertIn(response.data['code'], {'simple_plan_proposal_stale', 'workflow_expansion_blocked'})
        self.project.refresh_from_db()
        record.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, state)
        self.assertEqual(record.title, 'Original employee deliverable')
        self.assertEqual(record.status, 'completed')
        self.assertEqual(record.progress_percent, 100)
        self.assertEqual(record.assigned_to_id, self.other.pk)
        self.assertEqual(record.metadata['history_note'], 'Keep original work')
        self.assertFalse(record.is_deleted)
        self.assertEqual(ProjectTask.objects.filter(project=self.enterprise).count(), 1)
        self.assertFalse(ScheduleVersion.objects.filter(schedule__project=self.project).exists())

    def test_editing_retains_all_five_stage_identities_and_typed_relationships(self):
        self.stages[1].relationship_to_previous = 'SS'
        self.stages[1].lag_days = Decimal('2.5')
        self.stages[1].save(update_fields=['relationship_to_previous', 'lag_days'])
        _, applied = self.expanded()
        edited = deepcopy(applied['tasks'])
        ids = [row['id'] for row in edited]
        for task in edited:
            for key in tuple(task):
                if key.startswith('workflow_') or key in {'parent_deliverable_id', 'dependency_details'}:
                    task.pop(key)
        edited[2]['duration_days'] = 4.5
        saved = self.save(edited, revision=applied['revision'])
        self.assertEqual([row['id'] for row in saved['tasks']], ids)
        self.assertEqual(saved['tasks'][2]['duration_days'], 4.5)
        self.assertEqual(saved['tasks'][2]['duration_source'], 'planner')
        relationship = saved['tasks'][1]['dependency_details'][0]
        self.assertEqual((relationship['type'], relationship['lag_days']), ('SS', 2.5))
        self.assertTrue(all(row['parent_deliverable_id'] == 'task-a' for row in saved['tasks']))
        missing = self.client.put(self.url, {'revision': saved['revision'], 'tasks': saved['tasks'][:-1],
                                           'disciplines': saved['disciplines']}, format='json')
        self.assertEqual(missing.status_code, 409, missing.data)
        self.assertEqual(missing.data['code'], 'workflow_five_stages_required')

    def test_submitted_version_history_reconstructs_exact_parent_and_stage_metadata(self):
        saved = self.save([self.task(planned_start_date=self.project.effective_date.isoformat())])
        preview = self.action('propose-schedule', saved['revision'], workflow_mode='standard_five')
        applied = self.action('apply-schedule', saved['revision'], proposal_token=preview['proposal']['token'])
        submitted = self.action('submit', applied['revision'])
        version_id = submitted['version_id']
        version = ScheduleVersion.objects.get(pk=version_id)
        self.assertEqual(version.activities.count(), 5)
        self.assertEqual(version.wbs_nodes.exclude(parent=None).count(), 1)
        edited = deepcopy(submitted['tasks'])
        edited[2]['duration_days'] = 6
        self.save(edited, revision=submitted['revision'])
        history = self.client.get(self.url, {'version_id': version_id})
        self.assertEqual(history.status_code, 200, history.data)
        self.assertTrue(history.data['viewing_history'])
        self.assertEqual(len(history.data['deliverables']), 1)
        parent = history.data['deliverables'][0]
        self.assertEqual(parent['id'], 'task-a')
        self.assertEqual(parent['title'], 'Test application')
        self.assertNotEqual(parent['wbs_node_id'], parent['parent_wbs_node_id'])
        self.assertEqual(len(parent['workflow_task_ids']), 5)
        self.assertEqual([row['workflow_stage_code'] for row in history.data['tasks']],
                         ['IFR', 'COMPANY_REVIEW', 'IFA', 'COMPANY_APPROVAL', 'FINAL_ISSUE'])
        self.assertEqual(history.data['tasks'][2]['duration_days'], 5)
        self.assertEqual(history.data['tasks'][1]['responsible_role'], 'COMPANY')

    def test_preview_and_submitted_cpm_agree_for_ff_and_negative_ss_links(self):
        self.stages[0].duration_days = 1
        self.stages[0].save(update_fields=['duration_days'])
        self.stages[1].duration_days = 10
        self.stages[1].relationship_to_previous = 'FF'
        self.stages[1].save(update_fields=['duration_days', 'relationship_to_previous'])
        self.stages[2].relationship_to_previous = 'SS'
        self.stages[2].lag_days = -20
        self.stages[2].save(update_fields=['relationship_to_previous', 'lag_days'])
        saved = self.save([self.task(planned_start_date=self.project.effective_date.isoformat())])
        preview = self.action('propose-schedule', saved['revision'], workflow_mode='standard_five')
        applied = self.action('apply-schedule', saved['revision'], proposal_token=preview['proposal']['token'])
        tasks = preview['plan']['tasks']
        project_start = self.project.effective_date.isoformat()
        self.assertLess(tasks[1]['planned_start_date'], project_start)
        self.assertLess(tasks[2]['planned_start_date'], project_start)
        submitted = self.action('submit', applied['revision'])
        proposed_dates = {task['id']: (task['planned_start_date'], task['planned_finish_date']) for task in tasks}
        submitted_dates = {task['id']: (task['planned_start_date'], task['planned_finish_date']) for task in submitted['tasks']}
        self.assertEqual(proposed_dates, submitted_dates)

    def test_stage_discipline_change_is_rejected_without_modifying_source_parent(self):
        _, applied = self.expanded()
        self.project.refresh_from_db()
        original = deepcopy(self.project.simple_planning_state)
        changed = deepcopy(applied['tasks'])
        changed[1]['discipline'] = 'electrical'
        response = self.client.put(self.url, {
            'revision': applied['revision'], 'tasks': changed, 'disciplines': applied['disciplines'],
        }, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'workflow_discipline_mismatch')
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, original)
        self.assertEqual(self.read()['deliverables'][0]['discipline'], 'testing')
