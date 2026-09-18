"""Pure workflow previews preserve source identity and existing employee work."""
from copy import deepcopy
from unittest import TestCase

from ..services.simple_workflow_expansion import (
    WorkflowExpansionError, expand_workflow_deliverables,
)


class SimpleWorkflowExpansionTests(TestCase):
    def setUp(self):
        codes = ['IFR', 'COMPANY_REVIEW', 'IFA', 'COMPANY_APPROVAL', 'FINAL_ISSUE']
        durations = [10, 10, 5, 5, 1]
        roles = ['Discipline Engineer', 'COMPANY', 'Discipline Engineer', 'COMPANY', 'Document Control']
        self.template = {
            'id': 1, 'code': 'STANDARD_5_STAGE', 'version': 1, 'project_id': None,
            'stages': [{'code': code, 'name': code.replace('_', ' '), 'sequence': index + 1,
                        'duration_days': durations[index], 'responsible_party': roles[index],
                        'activity_type': 'task', 'relationship': '' if index == 0 else 'FS',
                        'lag_days': 0, 'progress_weight': 20, 'is_release_gate': True}
                       for index, code in enumerate(codes)],
        }
        self.context = {'project_id': 24, 'default_template_id': 1, 'templates': [self.template],
                        'overrides': [], 'workflow_mode': 'standard_five'}

    def parent(self, key='a', **values):
        return {
            'id': key, 'title': 'MATERIAL TAKE-OFF (MTO)', 'discipline': 'electrical',
            'document_number': 'ELE-MTO-01', 'document_revision': 'B',
            'source_title': 'MATERIAL TAKE-OFF (MTO)',
            'source_references': [{'file_id': 22, 'locator': {'sheet': 'Sheet1', 'row': 5}}],
            'depends_on': [], 'duration_days': 31, 'duration_source': 'proposed',
            'planned_start_date': '2026-01-06', **values,
        }

    def test_220_exact_source_parents_expand_to_1100_distinct_stages_without_mutation(self):
        parents = [self.parent(str(index), title=f'Deliverable {index}',
                               source_references=[{'file_id': 22, 'locator': {'row': index + 5}}])
                   for index in range(220)]
        parents[219]['title'] = parents[218]['title']
        original, context = deepcopy(parents), deepcopy(self.context)
        deliverables, tasks, warnings = expand_workflow_deliverables(parents, self.context)
        self.assertEqual(parents, original)
        self.assertEqual(self.context, context)
        self.assertEqual([row['title'] for row in deliverables], [row['title'] for row in parents])
        self.assertEqual(len(tasks), 1100)
        self.assertEqual(len({row['id'] for row in tasks}), 1100)
        self.assertEqual(len(deliverables), 220)
        self.assertEqual(tasks[0]['id'], parents[0]['id'])
        self.assertEqual([row['duration_days'] for row in tasks[:5]], [10, 10, 5, 5, 1])
        self.assertEqual(tasks[1]['depends_on'], [tasks[0]['id']])
        self.assertEqual(tasks[1]['responsible_role'], 'COMPANY')
        self.assertEqual(tasks[1]['workflow_progress_weight'], 20)
        self.assertTrue(tasks[1]['workflow_release_gate'])
        self.assertEqual(tasks[4]['source_parent_values']['title'], parents[0]['title'])
        self.assertEqual(tasks[4]['source_references'], parents[0]['source_references'])
        self.assertTrue(all(row['duration_source'] == 'proposed' for row in tasks))
        self.assertIn('do not reproduce', warnings[0])

    def test_uniform_mode_ignores_discipline_and_deliverable_family_overrides(self):
        alternate = deepcopy(self.template)
        alternate.update(id=2, code='DRAWING')
        alternate['stages'][0]['code'] = 'INPUT_FREEZE'
        self.context['templates'].append(alternate)
        self.context['overrides'] = [
            {'scope_type': 'discipline', 'scope_key': 'electrical', 'workflow_template_id': 2},
            {'scope_type': 'deliverable', 'scope_key': 'MATERIAL TAKE-OFF (MTO)', 'workflow_template_id': 2},
        ]
        _, tasks, _ = expand_workflow_deliverables([self.parent()], self.context)
        self.assertEqual({row['workflow_template_code'] for row in tasks}, {'STANDARD_5_STAGE'})

    def test_incompatible_selected_template_uses_project_standard_before_system(self):
        custom = deepcopy(self.template)
        custom.update(id=3, project_id=24, version=2)
        custom['stages'][0]['duration_days'] = 6
        incompatible = {'id': 2, 'code': 'PLAN_PROCEDURE', 'stages': self.template['stages'][:3]}
        self.context.update(default_template_id=2, templates=[self.template, incompatible, custom])
        _, tasks, _ = expand_workflow_deliverables([self.parent()], self.context)
        self.assertEqual(tasks[0]['workflow_template_id'], 3)
        self.assertEqual(tasks[0]['duration_days'], 6)

    def test_selected_custom_template_with_exact_uniform_gates_is_respected(self):
        custom = deepcopy(self.template)
        custom.update(id=2, code='PROJECT_REVIEW', project_id=24)
        custom['stages'][0]['duration_days'] = 7.5
        self.context.update(default_template_id=2, templates=[self.template, custom])
        _, tasks, _ = expand_workflow_deliverables([self.parent()], self.context)
        self.assertEqual(tasks[0]['workflow_template_code'], 'PROJECT_REVIEW')
        self.assertEqual(tasks[0]['duration_days'], 7.5)

    def test_malformed_standard_template_is_rejected_without_padding_or_reordering(self):
        self.template['stages'].pop()
        with self.assertRaises(WorkflowExpansionError) as raised:
            expand_workflow_deliverables([self.parent()], self.context)
        self.assertEqual(raised.exception.code, 'workflow_five_stages_required')
        self.setUp()
        self.template['stages'][0]['code'], self.template['stages'][1]['code'] = 'COMPANY_REVIEW', 'IFR'
        with self.assertRaises(WorkflowExpansionError) as raised:
            expand_workflow_deliverables([self.parent()], self.context)
        self.assertEqual(raised.exception.code, 'workflow_stage_codes_invalid')

    def test_missing_standard_template_cannot_silently_invent_a_workflow(self):
        self.context['templates'] = []
        with self.assertRaises(WorkflowExpansionError) as raised:
            expand_workflow_deliverables([self.parent()], self.context)
        self.assertEqual(raised.exception.code, 'workflow_template_unavailable')

    def test_parent_assignment_effort_and_due_date_move_only_to_first_stage(self):
        parent = self.parent(assignee_id='employee-1', owner='Assigned Employee', effort_hours=40,
                             reviewer_id='reviewer-1', due_date='2026-02-03', acceptance_criteria='Check quantity',
                             status='in_progress', progress_percent=20, project_task_id=72)
        _, tasks, warnings = expand_workflow_deliverables([parent], self.context)
        self.assertEqual(tasks[0]['id'], parent['id'])
        self.assertEqual(tasks[0]['project_task_id'], 72)
        self.assertEqual(tasks[0]['progress_percent'], 20)
        self.assertEqual(tasks[0]['effort_hours'], 40)
        self.assertEqual(tasks[0]['due_date_source'], 'explicit')
        self.assertTrue(all(row['assignee_id'] is None and row['effort_hours'] is None and row['due_date'] is None for row in tasks[1:]))
        self.assertTrue(all(row['planned_start_date'] is None for row in tasks[1:]))
        self.assertNotIn('project_task_id', tasks[1])
        self.assertTrue(any('first stage only' in warning for warning in warnings))

    def test_completed_parent_has_machine_readable_apply_blocker(self):
        deliverables, _, warnings = expand_workflow_deliverables(
            [self.parent()], self.context, previous_tasks=[self.parent(status='completed', progress_percent=100)],
        )
        self.assertEqual(deliverables[0]['expansion_blockers'][0]['code'], 'workflow_completed_parent')
        self.assertTrue(any('must be blocked' in warning for warning in warnings))

    def test_existing_stage_edits_and_employee_history_survive_reexpansion(self):
        parents, tasks, _ = expand_workflow_deliverables([self.parent()], self.context)
        tasks[0].update(status='completed', progress_percent=100, project_task_id=72)
        tasks[1].update(title='Custom review', duration_days=5, duration_source='planner',
                        planned_start_date='2026-03-02', assignee_id='reviewer', effort_hours=12,
                        due_date='2026-04-01', due_date_source='explicit', depends_on=[],
                        dependency_details=[], dependency_rationales={})
        snapshot = deepcopy(tasks)
        self.template['version'] = 2
        self.template['stages'][1]['duration_days'] = 20
        new_parents, new_tasks, _ = expand_workflow_deliverables(parents, self.context, previous_tasks=tasks)
        self.assertEqual([row['id'] for row in tasks], [row['id'] for row in new_tasks])
        self.assertEqual(tasks, snapshot)
        self.assertEqual(new_tasks[1]['title'], 'Custom review')
        self.assertEqual(new_tasks[1]['duration_days'], 5)
        self.assertEqual(new_tasks[1]['duration_source'], 'planner')
        self.assertEqual(new_tasks[1]['depends_on'], [])
        self.assertEqual(new_tasks[1]['assignee_id'], 'reviewer')
        self.assertEqual(new_tasks[0]['project_task_id'], 72)
        self.assertEqual(new_parents[0]['expansion_blockers'], [])

    def test_fs_parent_link_maps_final_to_first_and_preserves_evidence(self):
        first, second = self.parent('a'), self.parent('b', depends_on=['a'], dependency_rationales={
            'a': {'status': 'confirmed', 'rationale': 'Planner reviewed', 'source_references': [{'file_id': 2}]},
        })
        _, tasks, _ = expand_workflow_deliverables([first, second], self.context)
        self.assertEqual(tasks[5]['depends_on'], [tasks[4]['id']])
        link = tasks[5]['dependency_details'][0]
        self.assertEqual((link['type'], link['lag_days']), ('FS', 0))
        self.assertEqual(link['status'], 'confirmed')
        self.assertEqual(link['source_references'], [{'file_id': 2}])
        self.assertEqual(link['parent_predecessor_id'], 'a')

    def test_typed_parent_and_internal_links_preserve_endpoint_semantics_and_lag(self):
        self.template['stages'][0].update(activity_type='start_milestone', duration_days=0)
        self.template['stages'][1].update(relationship='SS', lag_days=2.5)
        parents = [self.parent('a')]
        for key, kind, lag in [('b', 'SS', 2), ('c', 'FF', -1), ('d', 'SF', 3)]:
            parents.append(self.parent(key, depends_on=['a'], dependency_details=[{'task_id': 'a', 'type': kind, 'lag_days': lag}]))
        _, tasks, _ = expand_workflow_deliverables(parents, self.context)
        self.assertTrue(tasks[0]['is_milestone'])
        self.assertEqual(tasks[0]['duration_days'], 0)
        self.assertEqual(tasks[1]['dependency_details'][0]['type'], 'SS')
        self.assertEqual(tasks[1]['dependency_details'][0]['lag_days'], 2.5)
        self.assertEqual(tasks[5]['dependency_details'][0]['task_id'], 'a')
        self.assertEqual(tasks[14]['dependency_details'][-1]['task_id'], tasks[4]['id'])
        self.assertEqual(tasks[14]['dependency_details'][-1]['type'], 'FF')
        self.assertEqual(tasks[19]['dependency_details'][-1]['task_id'], 'a')
        self.assertEqual(tasks[19]['dependency_details'][-1]['lag_days'], 3)

    def test_cycles_unknown_predecessors_and_duplicate_parent_ids_are_rejected(self):
        for parents in ([self.parent('a', depends_on=['b']), self.parent('b', depends_on=['a'])],
                        [self.parent(depends_on=['missing'])], [self.parent(), self.parent()]):
            with self.subTest(parents=parents):
                with self.assertRaises(WorkflowExpansionError):
                    expand_workflow_deliverables(parents, self.context)

    def test_stage_ids_are_stable_when_parent_title_or_order_changes(self):
        parents = [self.parent('a'), self.parent('b')]
        first, _, _ = expand_workflow_deliverables(parents, self.context)
        parents[0]['title'] = 'Exact revised source title'
        parents.reverse()
        second, _, _ = expand_workflow_deliverables(parents, self.context)
        self.assertEqual({row['id']: row['workflow_task_ids'] for row in first},
                         {row['id']: row['workflow_task_ids'] for row in second})

    def test_expansion_is_bounded_to_2000_activities(self):
        with self.assertRaises(WorkflowExpansionError) as raised:
            expand_workflow_deliverables([self.parent(str(index)) for index in range(401)], self.context)
        self.assertEqual(raised.exception.code, 'workflow_activity_limit')

    def test_overlong_generated_stage_title_is_rejected_without_truncating_source(self):
        parent = self.parent(title='x' * 500)
        original = deepcopy(parent)
        with self.assertRaises(WorkflowExpansionError) as raised:
            expand_workflow_deliverables([parent], self.context)
        self.assertEqual(raised.exception.code, 'workflow_activity_title_limit')
        self.assertEqual(parent, original)
        for stage in self.template['stages']:
            stage['activity_name_template'] = '{deliverable}'
        deliverables, tasks, _ = expand_workflow_deliverables([parent], self.context)
        self.assertEqual(deliverables[0]['title'], parent['title'])
        self.assertTrue(all(len(task['title']) == 500 for task in tasks))

    def test_new_parent_can_depend_on_an_existing_stage_including_reused_ifr_id(self):
        parents, stages, _ = expand_workflow_deliverables([self.parent('existing')], self.context)
        for predecessor in (stages[0]['id'], stages[1]['id']):
            with self.subTest(predecessor=predecessor):
                new_parent = self.parent('new', depends_on=[predecessor])
                _, expanded, _ = expand_workflow_deliverables(
                    [*parents, new_parent], self.context, previous_tasks=[*stages, new_parent],
                )
                first = next(task for task in expanded if task['id'] == 'new')
                self.assertEqual(first['depends_on'], [predecessor])
                self.assertEqual(first['dependency_details'][0]['type'], 'FS')
                self.assertEqual(first['dependency_details'][0]['parent_predecessor_id'], 'existing')
                self.assertEqual([task['depends_on'] for task in expanded[:5]], [task['depends_on'] for task in stages])

    def test_existing_stage_dependency_on_new_parent_waits_for_final_issue(self):
        parents, stages, _ = expand_workflow_deliverables([self.parent('existing')], self.context)
        new_parent = self.parent('new')
        stages[1]['depends_on'].append('new')
        stages[1]['dependency_details'].append({'task_id': 'new', 'type': 'FS', 'lag_days': 2,
                                               'source': 'planner', 'status': 'confirmed'})
        stages[1]['dependency_rationales']['new'] = {'status': 'confirmed', 'rationale': 'Needs completed package'}
        _, expanded, _ = expand_workflow_deliverables(
            [*parents, new_parent], self.context, previous_tasks=[*stages, new_parent],
        )
        new_final = next(task for task in expanded if task['parent_deliverable_id'] == 'new' and task['workflow_stage_code'] == 'FINAL_ISSUE')
        waiting = expanded[1]
        self.assertIn(new_final['id'], waiting['depends_on'])
        self.assertNotIn('new', waiting['depends_on'])
        link = next(row for row in waiting['dependency_details'] if row['task_id'] == new_final['id'])
        self.assertEqual((link['type'], link['lag_days'], link['status']), ('FS', 2, 'confirmed'))
        self.assertEqual(waiting['dependency_rationales'][new_final['id']]['rationale'], 'Needs completed package')

    def test_mixed_network_preserves_typed_start_finish_endpoints_in_both_directions(self):
        parents, stages, _ = expand_workflow_deliverables([self.parent('existing')], self.context)
        new_parent = self.parent('new', depends_on=[stages[1]['id']], dependency_details=[
            {'task_id': stages[1]['id'], 'type': 'SS', 'lag_days': 3},
        ])
        stages[2]['depends_on'].append('new')
        stages[2]['dependency_details'].extend([
            {'task_id': 'new', 'type': 'FS', 'lag_days': 1},
            {'task_id': 'new', 'type': 'SS', 'lag_days': 2},
        ])
        _, expanded, _ = expand_workflow_deliverables(
            [*parents, new_parent], self.context, previous_tasks=[*stages, new_parent],
        )
        new_chain = [task for task in expanded if task['parent_deliverable_id'] == 'new']
        self.assertEqual(new_chain[0]['dependency_details'][0]['task_id'], stages[1]['id'])
        self.assertEqual(new_chain[0]['dependency_details'][0]['type'], 'SS')
        links = {(row['task_id'], row['type'], row['lag_days']) for row in expanded[2]['dependency_details']}
        self.assertIn((new_chain[-1]['id'], 'FS', 1), links)
        self.assertIn((new_chain[0]['id'], 'SS', 2), links)
        # Existing explicit stage-to-stage links still target their exact stage.
        self.assertIn(stages[1]['id'], expanded[2]['depends_on'])
