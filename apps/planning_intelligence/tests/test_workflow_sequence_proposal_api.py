"""Workflow scheduling repairs remain explicit previews and preserve planner work."""
from copy import deepcopy
from datetime import date

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from apps.core.project_models import ProjectTask
from apps.hr_core.models import EmployeeMaster

from ..models import PlanningFile, PlanningProject, ScheduleVersion
from ..services.simple_planning import _calendar_record
from ..services.simple_schedule_proposal import proposal_context
from ..services.simple_workflow_expansion import expand_workflow_deliverables
from . import test_simple_planning as simple_fixture
from . import test_workflow_materialization as workflow_fixture


@override_settings(ROOT_URLCONF='apps.planning_intelligence.tests.test_simple_planning')
class WorkflowSequenceProposalAPITests(TestCase):
    task = simple_fixture.SimplePlanningTests.task
    read = simple_fixture.SimplePlanningTests.read
    save = simple_fixture.SimplePlanningTests.save
    action = simple_fixture.SimplePlanningTests.action

    TITLES = [
        'Site Visit Report', 'Electrical Design Basis', 'Electrical Layout Drawing',
        'Cable Routing Drawing', 'Design Audit at 30%', 'Design Audit at 60%',
        'Design Audit at 90%', 'PROJECT CLOSEOUT REPORT', 'MASTER DELIVERABLE REGISTER',
    ]

    def setUp(self):
        workflow_fixture.WorkflowPlanningIntegrationTests.setUp(self)
        self.project.effective_date = date(2026, 1, 6)
        self.project.planned_end_date = date(2026, 9, 4)
        self.project.save(update_fields=['effective_date', 'planned_end_date'])
        self.register = PlanningFile.objects.create(
            project=self.project, category='mdr', original_filename='MDR.xlsx', file='test/sequence-mdr.xlsx',
            parse_status='done', uploaded_by=self.owner,
            extracted_text='--- Sheet: MDR ---\nSL. NO.|DISCIPLINE|DOCUMENT TITLE\n' + '\n'.join(
                f'{index}|ELECTRICAL|{title}' for index, title in enumerate(self.TITLES, start=1)
            ),
        )
        self.analysed = self.action('analyse', 0)

    def preview(self, revision):
        return self.action('propose-schedule', revision, workflow_mode='standard_five')

    def apply(self, preview):
        return self.action('apply-schedule', preview['proposal']['revision'],
                           proposal_token=preview['proposal']['token'])

    def stage_map(self, plan):
        parents = {parent['id']: parent['title'] for parent in plan['deliverables']}
        result = {}
        for task in plan['tasks']:
            result.setdefault(parents[task['parent_deliverable_id']], {})[task['workflow_stage_code']] = task
        return result

    def assert_no_writes(self, queries):
        self.assertFalse([query['sql'] for query in queries
                          if query['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))])

    def legacy_expanded_fixture(self):
        """Represent an already-saved expansion predating phase-window sequencing."""
        self.project.refresh_from_db()
        state = deepcopy(self.project.simple_planning_state)
        context, _ = proposal_context(self.project, state, _calendar_record(self.project))
        context['workflow_mode'] = 'standard_five'
        parents, tasks, _ = expand_workflow_deliverables(state['tasks'], context, state['tasks'])
        self.assertTrue(all(task.get('planned_start_date') is None for task in tasks))
        self.assertEqual(sum(len(task['depends_on']) for task in tasks), len(parents) * 4)
        state.update(deliverables=parents, tasks=tasks, workflow_mode='standard_five',
                     revision=state['revision'] + 1, state='review')
        self.project.simple_planning_state = state
        self.project.save(update_fields=['simple_planning_state'])
        return state

    def assert_phase_sequence(self, plan):
        stages = self.stage_map(plan)
        survey = stages['Site Visit Report']
        basis = stages['Electrical Design Basis']
        drawing = stages['Electrical Layout Drawing']
        peer = stages['Cable Routing Drawing']
        self.assertLess(survey['IFR']['planned_start_date'], basis['IFR']['planned_start_date'])
        self.assertLess(basis['IFR']['planned_start_date'], drawing['IFR']['planned_start_date'])
        self.assertEqual(drawing['IFR']['planned_start_date'], peer['IFR']['planned_start_date'])
        self.assertIn(survey['FINAL_ISSUE']['id'], basis['IFR']['depends_on'])
        self.assertIn(basis['FINAL_ISSUE']['id'], drawing['IFR']['depends_on'])
        self.assertIn(basis['FINAL_ISSUE']['id'], peer['IFR']['depends_on'])
        self.assertNotIn(drawing['FINAL_ISSUE']['id'], peer['IFR']['depends_on'])
        self.assertNotIn(peer['FINAL_ISSUE']['id'], drawing['IFR']['depends_on'])
        audit_dates = [stages[f'Design Audit at {percent}%']['IFR']['planned_start_date']
                       for percent in (30, 60, 90)]
        self.assertEqual(audit_dates, sorted(set(audit_dates)))
        gate = next(link for link in basis['IFR']['dependency_details']
                    if link['task_id'] == survey['FINAL_ISSUE']['id'])
        self.assertEqual((gate['type'], gate['lag_days']), ('FS', 0))
        self.assertEqual(gate['source'], 'deliverable_sequence')
        self.assertEqual(gate['status'], 'proposed')
        self.assertEqual(gate['evidence_type'], 'planning_inference')
        self.assertIn('planned_start_date', survey['IFR']['schedule_generated_fields'])

    def test_fresh_register_preview_has_phase_windows_sparse_gates_and_no_writes(self):
        self.project.refresh_from_db()
        before = deepcopy(self.project.simple_planning_state)
        with CaptureQueriesContext(connection) as queries:
            preview = self.preview(self.analysed['revision'])
        self.assert_no_writes(queries)
        plan = preview['plan']
        self.assertEqual([row['title'] for row in plan['deliverables']], self.TITLES)
        self.assertEqual(len(plan['tasks']), len(self.TITLES) * 5)
        self.assertEqual(len({row['id'] for row in plan['tasks']}), len(self.TITLES) * 5)
        self.assert_phase_sequence(plan)
        self.assertTrue(all(task['source_references'][0]['file_id'] == self.register.pk for task in plan['tasks']))
        self.assertEqual(plan['source_verification']['document_register']['expected_count'], len(self.TITLES))
        self.assertEqual(plan['source_verification']['document_register']['matched_count'], len(self.TITLES))
        self.assertEqual(plan['scheduling_status']['state'], 'proposed')
        self.assertEqual(preview['proposal']['sequence_summary']['internal_relationship_count'], len(self.TITLES) * 4)
        self.assertGreaterEqual(preview['proposal']['sequence_summary']['cross_deliverable_relationship_count'], 3)
        self.assertTrue(all(task['calculation_basis'] == 'draft_cpm' for task in plan['tasks']))
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, before)
        self.assertFalse(ScheduleVersion.objects.filter(schedule__project=self.project).exists())

    def test_existing_unsequenced_expansion_repairs_without_new_ids_or_duplicate_relationships(self):
        state = self.legacy_expanded_fixture()
        original_ids = [task['id'] for task in state['tasks']]
        unsequenced = self.read()
        self.assertTrue(all(task['planned_start_date'] for task in unsequenced['tasks']))
        self.assertEqual(unsequenced['scheduling_status']['state'], 'unsequenced')
        self.assertEqual(unsequenced['scheduling_status']['cross_deliverable_relationship_count'], 0)
        self.assertEqual(unsequenced['scheduling_status']['workflow_start_constraint_count'], 0)
        with CaptureQueriesContext(connection) as queries:
            preview = self.preview(state['revision'])
        self.assert_no_writes(queries)
        self.assert_phase_sequence(preview['plan'])
        self.assertEqual([task['id'] for task in preview['plan']['tasks']], original_ids)
        applied = self.apply(preview)
        self.assertEqual([task['id'] for task in applied['tasks']], original_ids)
        self.assertEqual(len(applied['deliverables']), len(self.TITLES))
        self.assertTrue(all(len(parent['workflow_task_ids']) == 5 for parent in applied['deliverables']))
        self.assertEqual(applied['source_verification']['document_register']['matched_count'], len(self.TITLES))
        repeated = self.preview(applied['revision'])
        fields = ('id', 'parent_deliverable_id', 'workflow_stage_code', 'duration_days',
                  'planned_start_date', 'planned_finish_date', 'depends_on')
        self.assertEqual([{field: task.get(field) for field in fields} for task in repeated['plan']['tasks']],
                         [{field: task.get(field) for field in fields} for task in applied['tasks']])
        for task in repeated['plan']['tasks']:
            self.assertEqual(len(task['depends_on']), len(set(task['depends_on'])))
            details = [(link['task_id'], link['type']) for link in task.get('dependency_details') or []]
            self.assertEqual(len(details), len(set(details)))
        self.assertEqual(repeated['proposal']['relationship_count'], preview['proposal']['relationship_count'])

    def test_manual_child_edits_typed_links_employee_progress_and_explicit_due_date_survive(self):
        state = self.legacy_expanded_fixture()
        stage_map = self.stage_map(state)
        basis_id = stage_map['Electrical Design Basis']['IFR']['id']
        company_id = stage_map['Electrical Design Basis']['COMPANY_REVIEW']['id']
        predecessor_id = stage_map['Site Visit Report']['IFA']['id']
        manual_link = {'task_id': predecessor_id, 'type': 'SS', 'lag_days': 2,
                       'source': 'planner', 'status': 'confirmed', 'rationale': 'Explicit planning decision'}
        for task in state['tasks']:
            if task['id'] == basis_id:
                task['depends_on'] = [predecessor_id]
                task['dependency_details'] = [manual_link]
                task['schedule_generated_fields'] = ['duration_days']
        self.project.simple_planning_state = state
        self.project.save(update_fields=['simple_planning_state'])
        employee = EmployeeMaster.objects.create(
            user=self.other, employee_number='SEQUENCE-EMP', employee_code='SEQUENCE-EMP', emp_code='SEQUENCE-EMP',
            email=self.other.email, first_name='Assigned', last_name='Engineer',
            employment_status='active', join_date=date(2026, 1, 1),
        )
        current = self.read()
        edited = deepcopy(current['tasks'])
        for task in edited:
            if task['id'] == basis_id:
                task.update(assignee_id=employee.user_id, planned_start_date='2026-04-01', due_date='2026-08-20')
            if task['id'] == company_id:
                task.update(duration_days=17, planned_start_date='2026-05-04')
        saved = self.save(edited, current['revision'], disciplines=current['disciplines'])
        assigned = ProjectTask.objects.get(source_key=f'wbs:{self.project.pk}:{basis_id}')
        assigned.status, assigned.progress_percent = 'in_progress', 40
        assigned.save(update_fields=['status', 'progress_percent'])
        preview = self.preview(saved['revision'])
        stages = self.stage_map(preview['plan'])['Electrical Design Basis']
        self.assertEqual(stages['IFR']['planned_start_date'], '2026-04-01')
        self.assertEqual(stages['IFR']['depends_on'], [predecessor_id])
        self.assertEqual(stages['IFR']['dependency_details'], [manual_link])
        self.assertEqual(stages['IFR']['assignee_id'], employee.user_id)
        self.assertEqual(stages['IFR']['project_task_id'], assigned.pk)
        self.assertEqual(stages['IFR']['progress_percent'], 40)
        self.assertEqual(stages['IFR']['due_date'], '2026-08-20')
        self.assertEqual(stages['IFR']['due_date_source'], 'explicit')
        self.assertEqual(stages['COMPANY_REVIEW']['duration_days'], 17)
        self.assertEqual(stages['COMPANY_REVIEW']['duration_source'], 'planner')
        self.assertEqual(stages['COMPANY_REVIEW']['planned_start_date'], '2026-05-04')
        applied = self.apply(preview)
        assigned.refresh_from_db()
        self.assertEqual((assigned.status, assigned.progress_percent), ('in_progress', 40))
        self.assertEqual(assigned.due_date.isoformat(), '2026-08-20')
        self.assertEqual(sum(bool(task.get('assignee_id')) for task in applied['tasks']), 1)

    def test_saved_start_and_duration_edits_recalculate_successor_dates_and_float(self):
        applied = self.apply(self.preview(self.analysed['revision']))
        before = self.stage_map(applied)['Electrical Layout Drawing']
        edited = deepcopy(applied['tasks'])
        for task in edited:
            if task['id'] == before['IFR']['id']:
                task.update(duration_days=15, planned_start_date='2026-08-03')
        saved = self.save(edited, applied['revision'], disciplines=applied['disciplines'])
        after = self.stage_map(saved)['Electrical Layout Drawing']
        self.assertEqual(after['IFR']['planned_start_date'], '2026-08-03')
        self.assertEqual(after['IFR']['duration_days'], 15)
        self.assertGreater(after['COMPANY_REVIEW']['planned_start_date'], before['COMPANY_REVIEW']['planned_start_date'])
        self.assertGreater(after['FINAL_ISSUE']['planned_finish_date'], before['FINAL_ISSUE']['planned_finish_date'])
        self.assertLess(after['IFR']['total_float_days'], before['IFR']['total_float_days'])
        self.assertLess(after['FINAL_ISSUE']['total_float_days'], 0)
        self.assertTrue(after['FINAL_ISSUE']['is_critical'])
        self.assertEqual([task['id'] for task in saved['tasks']], [task['id'] for task in applied['tasks']])
        self.assertEqual(len(saved['deliverables']), len(self.TITLES))
        reloaded = self.stage_map(self.read())['Electrical Layout Drawing']
        self.assertEqual(after['FINAL_ISSUE']['planned_finish_date'], reloaded['FINAL_ISSUE']['planned_finish_date'])
        self.assertEqual(after['FINAL_ISSUE']['total_float_days'], reloaded['FINAL_ISSUE']['total_float_days'])

    def test_reference_schedule_and_requirements_in_another_workspace_are_not_borrowed(self):
        preview = self.preview(self.analysed['revision'])
        other = PlanningProject.objects.create(name='Other workspace', created_by=self.owner)
        reference = PlanningFile.objects.create(
            project=other, category='reference_schedule', original_filename='04 Schedule.pdf',
            file='test/other-schedule.pdf', parse_status='done',
            extracted_text='Activity ID | Activity Name | Duration | Start | Finish\nA1000 | Other work | 165 days | 06-Jan-26 | 04-Sep-26',
        )
        requirements = PlanningFile.objects.create(
            project=other, category='sow', original_filename='Other scope.pdf',
            file='test/other-scope.pdf', parse_status='done',
            extracted_text='Company review period is 3 working days. Completion within 99 weeks after contract award.',
        )
        repeated = self.preview(self.analysed['revision'])
        self.assertEqual(repeated['plan']['tasks'], preview['plan']['tasks'])
        self.assertEqual(repeated['proposal']['source_constraints'], [])
        self.assertEqual(repeated['plan']['source_verification']['schedule_reference']['status'], 'missing')
        self.assertNotIn('reference_schedule_not_imported', {item['code'] for item in repeated['plan']['blockers']})
        self.assertNotIn(reference.pk, {item['id'] for item in repeated['plan']['source_documents']})
        self.assertNotIn(requirements.pk, {item['id'] for item in repeated['plan']['source_documents']})
        applied = self.apply(preview)
        self.assertEqual(len(applied['tasks']), len(self.TITLES) * 5)

    def test_valid_cross_parent_stage_network_is_not_rejected_as_a_parent_cycle(self):
        state = self.legacy_expanded_fixture()
        stages = self.stage_map(state)
        first_id = stages['Site Visit Report']['IFR']['id']
        first_final = stages['Site Visit Report']['FINAL_ISSUE']['id']
        second_id = stages['Electrical Design Basis']['IFR']['id']
        for task in state['tasks']:
            if task['id'] == second_id:
                task['depends_on'].append(first_id)
                task['dependency_details'].append({'task_id': first_id, 'type': 'SS', 'lag_days': 0,
                                                   'source': 'planner', 'status': 'confirmed'})
            if task['id'] == first_final:
                task['depends_on'].append(second_id)
                task['dependency_details'].append({'task_id': second_id, 'type': 'FS', 'lag_days': 0,
                                                   'source': 'planner', 'status': 'confirmed'})
        self.project.simple_planning_state = state
        self.project.save(update_fields=['simple_planning_state'])
        preview = self.preview(state['revision'])
        proposed = {task['id']: task for task in preview['plan']['tasks']}
        self.assertIn(first_id, proposed[second_id]['depends_on'])
        self.assertIn(second_id, proposed[first_final]['depends_on'])
        self.assertEqual(len(proposed), len(self.TITLES) * 5)
        self.assertTrue(all(task['calculated'] for task in proposed.values()))
