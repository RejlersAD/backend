"""Expanded workflows persist as source groups and typed executable activities."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
import hashlib
from unittest import TestCase as UnitTestCase

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from apps.core.project_models import ProjectTask
from apps.hr_core.models import EmployeeMaster

from ..models import ActivityAssignment, PlanningAuditEvent, PlanningFile, PlanningProject, ScheduleVersion, WorkflowStage, WorkflowTemplate
from ..services.simple_workflow_expansion import expand_workflow_deliverables
from ..services.draft_source_reconciliation import invalidate_stale_source_evidence
from ..services.source_date_read_model import source_date_fields
from ..services.work_assignment_history import employee_activity, record_task_event, task_snapshot
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

    def source_workflows(self, *, cross_dependency=False):
        """Two independently versioned MDRs, with explicitly requested stages."""
        employee = EmployeeMaster.objects.create(
            user=self.reviewer, employee_number='REBUILD-WORKFLOW', employee_code='REBUILD-WORKFLOW',
            emp_code='REBUILD-WORKFLOW', email=self.reviewer.email, first_name='Workflow', last_name='Engineer',
            employment_status='active', join_date=date(2026, 1, 1),
        )
        sources = [PlanningFile.objects.create(
            project=self.project, category='mdr', file=f'test/workflow-{index}.xlsx',
            original_filename=f'Workflow MDR {index}.xlsx', parse_status='done', uploaded_by=self.owner,
            extracted_text=f'SL. NO.|DISCIPLINE|DOCUMENT TITLE|REVISION\n1|{discipline}|{title}|A',
        ) for index, (discipline, title) in enumerate([
            ('ELECTRICAL', 'Protection relay settings'), ('MECHANICAL', 'Seal flushing plan'),
        ])]
        analysed = self.action('analyse', 0)
        self.assertEqual(len(analysed['tasks']), 2)
        if cross_dependency:
            # This relationship is an explicit planner edit, not a document inference.
            tasks = deepcopy(analysed['tasks'])
            by_file = {row['source_references'][0]['file_id']: row for row in tasks}
            by_file[sources[0].pk]['depends_on'] = [by_file[sources[1].pk]['id']]
            analysed = self.save(tasks, analysed['revision'], disciplines=analysed['disciplines'])
        preview = self.action('propose-schedule', analysed['revision'], workflow_mode='standard_five')
        applied = self.action('apply-schedule', analysed['revision'], proposal_token=preview['proposal']['token'])
        self.assertEqual(len(applied['tasks']), 10)
        parents = {row['source_references'][0]['file_id']: row for row in applied['deliverables']}
        selected = {parent['workflow_task_ids'][index] for parent in parents.values() for index in (0, 2)}
        tasks = deepcopy(applied['tasks'])
        for task in tasks:
            if task['id'] in selected:
                task['assignee_id'] = employee.user_id
        saved = self.save(tasks, applied['revision'], disciplines=applied['disciplines'])
        records = {row.metadata['wbs_task_id']: row for row in ProjectTask.objects.filter(project=self.enterprise)}
        for source, status, progress in ((sources[0], 'in_progress', 35), (sources[1], 'completed', 100)):
            record = records[parents[source.pk]['workflow_task_ids'][0]]
            before = task_snapshot(record)
            record.status, record.progress_percent = status, progress
            record.save(update_fields=['status', 'progress_percent'])
            record_task_event(workspace=self.project, actor=self.reviewer, task=record, action='progress_updated', before=before)
        return sources, saved, parents, records

    def test_unchanged_source_rebuild_retains_workflow_ids_assignments_and_employee_progress(self):
        _, saved, parents, records = self.source_workflows()
        old_ids = [row['id'] for row in saved['tasks']]
        old_parent_ids = {row['id']: row['workflow_task_ids'] for row in parents.values()}
        old_references = {row['id']: deepcopy(row['source_references']) for row in saved['tasks']}
        self.project.refresh_from_db()
        token = self.project.simple_planning_state['assignment_token']
        self.assertIn('schedule_proposal', self.project.simple_planning_state)

        rebuilt = self.action('analyse', saved['revision'], rebuild=True)

        self.assertEqual([row['id'] for row in rebuilt['tasks']], old_ids)
        self.assertEqual({row['id']: row['workflow_task_ids'] for row in rebuilt['deliverables']}, old_parent_ids)
        self.assertEqual({row['id']: row['source_references'] for row in rebuilt['tasks']}, old_references)
        self.assertTrue(all(row['duration_days'] is None for row in rebuilt['tasks']))
        by_id = {row['id']: row for row in rebuilt['tasks']}
        for key, record in records.items():
            record.refresh_from_db()
            self.assertFalse(record.is_deleted)
            self.assertEqual(by_id[key]['project_task_id'], record.pk)
            self.assertEqual(by_id[key]['assignee_id'], self.reviewer.pk)
            self.assertEqual(by_id[key]['progress_percent'], record.progress_percent)
            self.assertEqual(by_id[key]['status'], record.status)
        self.assertEqual(rebuilt['rebuild_summary'], {
            'preserved_workflow_deliverables': 2, 'replaced_workflow_deliverables': 0,
            'archived_tasks': 0, 'new_tasks': 0,
        })
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state['assignment_token'], token)
        self.assertNotIn('schedule_proposal', self.project.simple_planning_state)
        self.assertNotIn('duration_review', self.project.simple_planning_state)
        repeated = self.action('analyse', rebuilt['revision'], rebuild=True)
        self.assertEqual([row['id'] for row in repeated['tasks']], old_ids)
        self.assertEqual(ProjectTask.objects.filter(project=self.enterprise).count(), len(records))
        self.assertFalse(PlanningAuditEvent.objects.filter(project=self.project, action='work_breakdown.assignment_withdrawn').exists())

    def test_mixed_source_rebuild_retires_revised_stages_without_guessing_identity_or_losing_history(self):
        sources, saved, parents, records = self.source_workflows(cross_dependency=True)
        preserved_parent, revised_parent = parents[sources[0].pk], parents[sources[1].pk]
        preserved_ids, retired_ids = preserved_parent['workflow_task_ids'], revised_parent['workflow_task_ids']
        saved_by_id = {row['id']: row for row in saved['tasks']}
        self.assertIn(retired_ids[-1], saved_by_id[preserved_ids[0]]['depends_on'])
        old_event_ids = set(PlanningAuditEvent.objects.filter(project=self.project, action__startswith='work_breakdown.').values_list('pk', flat=True))
        sources[1].extracted_text = sources[1].extracted_text.removesuffix('|A') + '|B'
        sources[1].save(update_fields=['extracted_text', 'updated_at'])

        rebuilt = self.action('analyse', saved['revision'], rebuild=True)

        self.assertEqual(len(rebuilt['tasks']), 6)
        self.assertEqual([row['id'] for row in rebuilt['deliverables']], [preserved_parent['id']])
        self.assertEqual(rebuilt['deliverables'][0]['workflow_task_ids'], preserved_ids)
        new_rows = [row for row in rebuilt['tasks'] if not row.get('parent_deliverable_id')]
        self.assertEqual(len(new_rows), 1)
        replacement = new_rows[0]
        self.assertNotIn(replacement['id'], {row['id'] for row in saved['tasks']})
        self.assertEqual(replacement['title'], revised_parent['title'])
        self.assertEqual(replacement['document_revision'], 'B')
        self.assertIsNone(replacement.get('assignee_id'))
        self.assertIsNone(replacement.get('project_task_id'))
        self.assertIsNone(replacement.get('duration_days'))
        self.assertNotIn('workflow_stage_code', replacement)
        for row in rebuilt['tasks'] + rebuilt['deliverables']:
            self.assertFalse(set(row.get('depends_on') or []) & set(retired_ids))
            self.assertFalse({link['task_id'] for link in row.get('dependency_details') or []} & set(retired_ids))
            self.assertFalse(set(row.get('dependency_rationales') or {}) & set(retired_ids))
        self.assertTrue(any(row['code'] == 'removed_dependencies' for row in rebuilt['warnings']))
        by_id = {row['id']: row for row in rebuilt['tasks']}
        for previous, current in zip(preserved_ids, preserved_ids[1:]):
            self.assertIn(previous, by_id[current]['depends_on'])
        for key, record in records.items():
            record.refresh_from_db()
            self.assertEqual(record.is_deleted, key in retired_ids)
            self.assertEqual(record.assigned_to_id, self.reviewer.pk)
        completed = records[retired_ids[0]]
        self.assertEqual((completed.status, completed.progress_percent), ('completed', 100))
        self.assertIsNotNone(completed.deleted_at)
        withdrawal = PlanningAuditEvent.objects.get(project=self.project, action='work_breakdown.assignment_withdrawn', entity_id=str(completed.pk))
        self.assertEqual(withdrawal.before['progress_percent'], 100)
        self.assertEqual(withdrawal.after['progress_percent'], 100)
        self.assertTrue(withdrawal.after['is_deleted'])
        self.assertTrue(old_event_ids <= set(PlanningAuditEvent.objects.filter(project=self.project).values_list('pk', flat=True)))
        history = employee_activity(self.project, self.reviewer.pk)
        self.assertEqual(history['summary']['current_tasks'], 2)
        self.assertEqual(history['summary']['historical_tasks'], 2)
        historic = next(row for row in history['tasks'] if row['wbs_task_id'] == retired_ids[0])
        self.assertEqual((historic['assignment_state'], historic['status'], historic['progress_percent']), ('historical', 'completed', 100))
        self.assertEqual(rebuilt['rebuild_summary'], {
            'preserved_workflow_deliverables': 1, 'replaced_workflow_deliverables': 1,
            'archived_tasks': 5, 'new_tasks': 1,
        })
        self.project.refresh_from_db()
        self.assertTrue(set(retired_ids) <= set(self.project.simple_planning_state['managed_task_ids']))
        self.assertNotIn('schedule_proposal', self.project.simple_planning_state)
        repeated = self.action('analyse', rebuilt['revision'], rebuild=True)
        self.assertEqual([row['id'] for row in repeated['tasks']], [row['id'] for row in rebuilt['tasks']])
        self.assertEqual(ProjectTask.objects.filter(project=self.enterprise).count(), len(records))
        self.assertEqual(PlanningAuditEvent.objects.filter(project=self.project, action='work_breakdown.assignment_withdrawn').count(), 2)
        edited = deepcopy(repeated['tasks'])
        next(row for row in edited if row['id'] == replacement['id'])['acceptance_criteria'] = 'Verify against the revised register.'
        resaved = self.save(edited, repeated['revision'], disciplines=repeated['disciplines'])
        self.assertEqual([row['id'] for row in resaved['deliverables']], [preserved_parent['id']])
        self.assertEqual(len(resaved['tasks']), 6)
        self.assertEqual(next(row for row in resaved['tasks'] if row['id'] == replacement['id'])['acceptance_criteria'],
                         'Verify against the revised register.')

    def test_revising_every_source_replaces_all_workflow_groups_with_editable_flat_rows(self):
        sources, saved, _, records = self.source_workflows()
        old_ids = {row['id'] for row in saved['tasks']}
        for source in sources:
            source.extracted_text = source.extracted_text.removesuffix('|A') + '|B'
            source.save(update_fields=['extracted_text', 'updated_at'])

        rebuilt = self.action('analyse', saved['revision'], rebuild=True)

        self.assertEqual(rebuilt['deliverables'], [])
        self.assertIsNone(rebuilt['workflow_mode'])
        self.assertEqual(len(rebuilt['tasks']), 2)
        self.assertFalse(old_ids & {row['id'] for row in rebuilt['tasks']})
        for row in rebuilt['tasks']:
            self.assertFalse(row.get('parent_deliverable_id'))
            self.assertFalse(row.get('workflow_stage_code'))
            self.assertIsNone(row.get('assignee_id'))
            self.assertIsNone(row.get('project_task_id'))
            self.assertIsNone(row.get('duration_days'))
            self.assertEqual(row['document_revision'], 'B')
        for record in records.values():
            original_status, original_progress = record.status, record.progress_percent
            record.refresh_from_db()
            self.assertTrue(record.is_deleted)
            self.assertEqual((record.status, record.progress_percent), (original_status, original_progress))
        self.assertEqual(PlanningAuditEvent.objects.filter(project=self.project, action='work_breakdown.assignment_withdrawn').count(), 4)
        self.assertEqual(rebuilt['rebuild_summary'], {
            'preserved_workflow_deliverables': 0, 'replaced_workflow_deliverables': 2,
            'archived_tasks': 10, 'new_tasks': 2,
        })
        edited = deepcopy(rebuilt['tasks'])
        edited[0]['acceptance_criteria'] = 'Check revised issue requirements before assignment.'
        resaved = self.save(edited, rebuilt['revision'], disciplines=rebuilt['disciplines'])
        self.assertEqual(resaved['deliverables'], [])
        self.assertIsNone(resaved['workflow_mode'])
        self.assertEqual([row['id'] for row in resaved['tasks']], [row['id'] for row in rebuilt['tasks']])
        self.assertEqual(resaved['tasks'][0]['acceptance_criteria'], edited[0]['acceptance_criteria'])
        self.assertEqual(ProjectTask.objects.filter(project=self.enterprise).count(), 4)
        self.assertFalse(ProjectTask.objects.filter(project=self.enterprise, is_deleted=False).exists())

    def test_workflow_rebuild_rejects_stale_revision_and_failed_extraction_without_retiring_work(self):
        sources, saved, _, records = self.source_workflows()
        self.project.refresh_from_db()
        before = deepcopy(self.project.simple_planning_state)
        response = self.client.post(self.url + 'analyse/', {'revision': saved['revision'] - 1, 'rebuild': True}, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'simple_plan_revision_conflict')
        sources[0].parse_status = 'failed'
        sources[0].save(update_fields=['parse_status', 'updated_at'])
        response = self.client.post(self.url + 'analyse/', {'revision': saved['revision'], 'rebuild': True}, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'simple_plan_documents_processing')
        sources[0].parse_status, sources[0].extracted_text = 'done', 'The source table could not be recovered.'
        sources[0].save(update_fields=['parse_status', 'extracted_text', 'updated_at'])
        response = self.client.post(self.url + 'analyse/', {'revision': saved['revision'], 'rebuild': True}, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'source_activities_not_recovered')
        for source in sources:
            source.parse_status, source.extracted_text = 'done', 'The source table could not be recovered.'
            source.save(update_fields=['parse_status', 'extracted_text', 'updated_at'])
        response = self.client.post(self.url + 'analyse/', {'revision': saved['revision'], 'rebuild': True}, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'source_activities_not_recovered')
        for source in sources:
            source.is_deleted = True
            source.save(update_fields=['is_deleted', 'updated_at'])
        response = self.client.post(self.url + 'analyse/', {'revision': saved['revision'], 'rebuild': True}, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'source_activities_not_recovered')
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, before)
        for record in records.values():
            status, progress = record.status, record.progress_percent
            record.refresh_from_db()
            self.assertFalse(record.is_deleted)
            self.assertEqual((record.status, record.progress_percent), (status, progress))
        self.assertFalse(PlanningAuditEvent.objects.filter(project=self.project, action='work_breakdown.assignment_withdrawn').exists())

    def test_rebuild_invalidates_revised_schedule_evidence_without_replacing_unchanged_mdr_work(self):
        sources, saved, parents, records = self.source_workflows()
        schedule = PlanningFile.objects.create(
            project=self.project, category='reference_schedule', file='test/workflow-timing.csv',
            original_filename='Workflow timing.csv', parse_status='done', uploaded_by=self.owner,
            extracted_text='Activity ID|Activity Name|Duration|Duration Unit|Start|Finish\n'
                           'EL-10|Protection relay settings - IFR|5|working_days|2026-11-06|2026-11-12',
        )
        reference = {'file_id': schedule.pk, 'project_id': self.project.pk, 'locator': {
            'line': 2, 'extracted_text_sha256': hashlib.sha256(schedule.extracted_text.encode('utf-8')).hexdigest(),
        }}
        evidence = {'activity_specific': True, 'source_references': [reference], 'values': {
            'original_duration_days': 5, 'planned_start_date': '2026-11-06', 'planned_finish_date': '2026-11-12',
        }}
        self.project.refresh_from_db()
        state = deepcopy(self.project.simple_planning_state)
        by_id = {row['id']: row for row in state['tasks']}
        first_ids, second_ids = parents[sources[0].pk]['workflow_task_ids'], parents[sources[1].pk]['workflow_task_ids']
        sourced = by_id[first_ids[0]]
        sourced.update(duration_days=5, duration_source='source_document', duration_calendar_verified=True,
                       planned_start_date='2026-11-06', planned_finish_date='2026-11-12', date_authority='source_document',
                       source_evidence=deepcopy(evidence), duration_evidence=deepcopy(evidence))
        sourced['depends_on'] = [second_ids[4], second_ids[2]]
        sourced['dependency_details'] = [
            {'task_id': second_ids[4], 'type': 'FS', 'lag_days': 0, 'source': 'source_document', 'source_references': [reference]},
            {'task_id': second_ids[2], 'type': 'FS', 'lag_days': 0, 'source': 'planner', 'status': 'confirmed'},
        ]
        manual = by_id[second_ids[0]]
        manual.update(duration_days=7, duration_source='planner', planned_start_date='2026-11-16',
                      planned_start_date_source='planner', duration_evidence=deepcopy(evidence))
        self.project.simple_planning_state = state
        self.project.save(update_fields=['simple_planning_state'])
        schedule.extracted_text = schedule.extracted_text.replace('|5|working_days|', '|9|working_days|').replace('2026-11-12', '2026-11-18')
        schedule.save(update_fields=['extracted_text', 'updated_at'])

        rebuilt = self.action('analyse', saved['revision'], rebuild=True)

        self.assertEqual([row['id'] for row in rebuilt['tasks']], [row['id'] for row in saved['tasks']])
        current = {row['id']: row for row in rebuilt['tasks']}
        sourced = current[first_ids[0]]
        self.assertIsNone(sourced['duration_days'])
        self.assertEqual(sourced['duration_source'], 'missing_source')
        self.assertIsNone(sourced['source_start_date'])
        self.assertIsNone(sourced['source_finish_date'])
        self.assertIsNone(sourced['planned_start_date'])
        self.assertIsNone(sourced['planned_finish_date'])
        self.assertNotIn(second_ids[4], sourced['depends_on'])
        self.assertIn(second_ids[2], sourced['depends_on'])
        self.assertTrue(sourced['sequence_review_required'])
        self.assertEqual(sourced['source_evidence_review']['code'], 'stale_source_evidence')
        self.assertFalse(sourced['duration_calendar_verified'])
        self.assertEqual(sourced['project_task_id'], records[first_ids[0]].pk)
        self.assertEqual(sourced['progress_percent'], 35)
        self.assertEqual(current[second_ids[0]]['duration_days'], 7)
        self.assertEqual(current[second_ids[0]]['duration_source'], 'planner')
        self.project.refresh_from_db()
        stored_manual = next(row for row in self.project.simple_planning_state['tasks'] if row['id'] == second_ids[0])
        self.assertEqual(stored_manual['planned_start_date'], '2026-11-16')
        self.assertEqual(current[second_ids[0]]['progress_percent'], 100)
        self.assertTrue(any(item.get('evidence') == evidence for item in sourced['source_evidence_history']))
        self.assertTrue(all(previous in current[following]['depends_on']
                            for chain in (first_ids, second_ids) for previous, following in zip(chain, chain[1:])))
        for record in records.values():
            record.refresh_from_db()
            self.assertFalse(record.is_deleted)
        # Ordinary edits must not remove the source warning or archived evidence.
        resaved = self.save(deepcopy(rebuilt['tasks']), rebuilt['revision'], disciplines=rebuilt['disciplines'])
        persisted = next(row for row in resaved['tasks'] if row['id'] == first_ids[0])
        self.assertEqual(persisted['source_evidence_review'], sourced['source_evidence_review'])
        self.assertEqual(persisted['source_evidence_history'], sourced['source_evidence_history'])

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
        planned = deepcopy(applied['tasks'])
        for task, duration in zip(planned, [10, 10, 5, 5, 1]):
            task['duration_days'] = duration  # Explicit planner inputs, not template defaults.
        applied = self.save(planned, revision=applied['revision'])
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
        planned = deepcopy(applied['tasks'])
        for task, duration in zip(planned, [1, 10, 5, 5, 1]):
            task['duration_days'] = duration
        applied = self.save(planned, revision=applied['revision'])
        tasks = applied['tasks']
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


class DraftSourceReconciliationTests(UnitTestCase):
    def source(self, **updates):
        return {'id': 41, 'project_id': 7, 'parse_status': 'done', 'is_deleted': False,
                'extracted_text': 'Explicit activity EL-10, duration 5 working days.', **updates}

    def evidence(self, **updates):
        return {'activity_specific': True, 'values': {'original_duration_days': 5,
                'planned_start_date': '2026-11-06', 'planned_finish_date': '2026-11-12'},
                'source_references': [{'file_id': 41, 'project_id': 7,
                    'extracted_text_sha256': hashlib.sha256(self.source()['extracted_text'].encode('utf-8')).hexdigest(),
                    'locator': {'line': 1}}], **updates}

    def row(self, **updates):
        return {'id': 'unchanged-stage', 'assignee_id': 19, 'project_task_id': 22, 'progress_percent': 55,
                'duration_days': 5, 'duration_source': 'source_document', 'duration_calendar_verified': True,
                'planned_start_date': '2026-11-06', 'planned_finish_date': '2026-11-12',
                'date_authority': 'source_document', 'duration_evidence': self.evidence(), 'depends_on': [], **updates}

    def test_exact_current_evidence_including_locator_hash_is_unchanged(self):
        row = self.row()
        reference = row['duration_evidence']['source_references'][0]
        reference['locator']['extracted_text_sha256'] = reference.pop('extracted_text_sha256')
        original = deepcopy(row)
        self.assertEqual(invalidate_stale_source_evidence([row], [self.source()]), 0)
        self.assertEqual(row, original)
        self.assertEqual(source_date_fields(row)['source_date_status'], 'extracted')

    def test_missing_unversioned_revised_or_malformed_sources_invalidate_without_erasing_work_history(self):
        for reason in ('missing', 'deleted', 'changed', 'unversioned', 'wrong_project', 'malformed_locator'):
            with self.subTest(reason=reason):
                row, sources = self.row(), [self.source()]
                reference = row['duration_evidence']['source_references'][0]
                if reason == 'missing':
                    sources = []
                elif reason == 'deleted':
                    sources[0]['is_deleted'] = True
                elif reason == 'changed':
                    sources[0]['extracted_text'] += ' Revised.'
                elif reason == 'unversioned':
                    reference.pop('extracted_text_sha256')
                    reference['filename'] = 'Explicit activity EL-10.pdf'
                elif reason == 'wrong_project':
                    reference['project_id'] = 8
                else:
                    reference['locator'] = 'page 1'
                self.assertEqual(invalidate_stale_source_evidence([row], sources), 1)
                self.assertIsNone(row['duration_days'])
                self.assertIsNone(row['planned_start_date'])
                self.assertIsNone(row['planned_finish_date'])
                self.assertEqual(source_date_fields(row)['source_date_status'], 'not_specified')
                self.assertEqual((row['id'], row['assignee_id'], row['project_task_id'], row['progress_percent']),
                                 ('unchanged-stage', 19, 22, 55))
                self.assertEqual(row['source_evidence_history'][0]['evidence']['values']['original_duration_days'], 5)
                history = deepcopy(row['source_evidence_history'])
                self.assertEqual(invalidate_stale_source_evidence([row], sources), 0)
                self.assertEqual(row['source_evidence_history'], history)

    def test_explicit_planner_values_survive_while_obsolete_document_badges_are_removed(self):
        row = self.row(duration_days=7, duration_source='planner', planned_start_date='2026-11-16',
                       planned_finish_date='2026-11-24', date_authority='planner')
        self.assertEqual(invalidate_stale_source_evidence([row], []), 1)
        self.assertEqual(row['duration_days'], 7)
        self.assertEqual(row['duration_source'], 'planner')
        self.assertEqual(row['planned_start_date'], '2026-11-16')
        self.assertEqual(row['planned_finish_date'], '2026-11-24')
        self.assertEqual(source_date_fields(row)['source_date_status'], 'not_specified')
        self.assertFalse(row['duration_calendar_verified'])
        self.assertEqual(row['source_evidence_review']['status'], 'requires_review')

    def test_only_stale_document_links_are_removed_and_unsupported_rationale_cannot_restore_them(self):
        references = self.evidence()['source_references']
        row = self.row(duration_evidence=None, duration_source='planner', date_authority='planner',
                       depends_on=['old-source', 'manual', 'workflow', 'also-manual'],
                       dependency_details=[
                           {'task_id': 'old-source', 'source': 'source_document', 'source_references': references},
                           {'task_id': 'manual', 'source': 'planner'},
                           {'task_id': 'workflow', 'source': 'workflow_template'},
                           {'task_id': 'also-manual', 'source': 'source_document', 'source_references': references},
                           {'task_id': 'also-manual', 'source': 'manual'},
                       ], dependency_rationales={'old-source': 'An old descriptive note.'})
        self.assertEqual(invalidate_stale_source_evidence([row], []), 1)
        self.assertEqual(row['depends_on'], ['manual', 'workflow', 'also-manual'])
        self.assertEqual([link['source'] for link in row['dependency_details']], ['planner', 'workflow_template', 'manual'])
        self.assertNotIn('old-source', row['dependency_rationales'])
        self.assertTrue(row['sequence_review_required'])
        self.assertEqual(row['dependency_status'], 'not_specified')
        self.assertEqual(len(row['source_evidence_history']), 2)
        self.assertEqual(invalidate_stale_source_evidence([row], []), 0)

    def test_withdrawn_source_link_is_not_recalculated_as_independent_work(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from ..services.cpm import WorkdayCalendar
        from ..services.simple_planning import _dated_tasks

        row = self.row(depends_on=['obsolete'], dependency_details=[{
            'task_id': 'obsolete', 'source': 'source_document',
            'source_references': [{**self.evidence()['source_references'][0], 'file_id': 99}],
        }])
        invalidate_stale_source_evidence([row], [self.source()])
        self.assertEqual(row['depends_on'], [])
        self.assertEqual(row['duration_days'], 5)
        project = SimpleNamespace(effective_date=date(2026, 11, 6), planned_end_date=date(2026, 12, 20))
        with patch('apps.planning_intelligence.services.simple_planning._calendar',
                   return_value=WorkdayCalendar(None, project.effective_date)):
            displayed = _dated_tasks(project, [row])[0]
        self.assertIsNone(displayed['planned_start_date'])
        self.assertIsNone(displayed['planned_finish_date'])
        self.assertIsNone(displayed['total_float_days'])
        self.assertFalse(displayed['calculated'])
