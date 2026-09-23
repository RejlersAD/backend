"""Execution and integrity contracts for generated engineering schedules."""
from collections import defaultdict, deque
from copy import deepcopy
from unittest import TestCase

from ..services.enterprise_schedule import (
    expand_enterprise_deliverables,
    validate_enterprise_network,
)


class EnterpriseScheduleTests(TestCase):
    def parent(self, key='foundation', **values):
        return {
            'id': key,
            'title': 'Civil Foundation Design',
            'discipline': 'civil',
            'wbs_phase': 'engineering',
            'wbs_deliverable': 'Civil Foundation Design',
            'depends_on': [],
            'dependency_details': [],
            **values,
        }

    def expand(self, parents=None, **context):
        return expand_enterprise_deliverables(
            parents or [self.parent()],
            {'project_type': 'industrial', 'complexity': 'standard', **context},
        )

    @staticmethod
    def groups(tasks):
        groups = defaultdict(list)
        for row in tasks:
            if row.get('parent_deliverable_id'):
                groups[row['parent_deliverable_id']].append(row)
        return groups

    @staticmethod
    def reachable(tasks, start_id):
        outgoing = defaultdict(set)
        for row in tasks:
            for predecessor in row.get('depends_on', []):
                outgoing[predecessor].add(row['id'])
        reached, queue = set(), deque([start_id])
        while queue:
            for successor in outgoing[queue.popleft()]:
                if successor not in reached:
                    reached.add(successor)
                    queue.append(successor)
        return reached

    @staticmethod
    def replace_predecessors(tasks, task_id, predecessors):
        """Maintain both relationship views while deliberately corrupting logic."""
        task = next(row for row in tasks if row['id'] == task_id)
        task['depends_on'] = list(predecessors)
        task['dependency_details'] = [
            {'task_id': predecessor, 'type': 'FS', 'lag_days': 0}
            for predecessor in predecessors
        ]
        for row in tasks:
            row['successors'] = [
                successor['id'] for successor in tasks
                if row['id'] in successor.get('depends_on', [])
            ]

    def test_wbs_identity_is_preserved_and_each_deliverable_has_executable_tasks(self):
        parents = [self.parent(), self.parent(
            'pump', title='Cooling Water Pump Procurement', discipline='mechanical',
            wbs_phase='procurement', wbs_deliverable='Cooling Water Pump Package',
        )]
        original = deepcopy(parents)
        deliverables, tasks, _ = self.expand(parents)
        self.assertEqual(parents, original)
        self.assertEqual({row['id'] for row in deliverables}, {'foundation', 'pump'})
        groups = self.groups(tasks)
        for parent in parents:
            with self.subTest(deliverable=parent['title']):
                output = next(row for row in deliverables if row['id'] == parent['id'])
                for key in ('title', 'discipline', 'wbs_phase', 'wbs_deliverable'):
                    self.assertEqual(output[key], parent[key])
                children = groups[parent['id']]
                self.assertGreaterEqual(len(children), 5)
                self.assertEqual(len({row['title'] for row in children}), len(children))
                self.assertTrue(all(row['title'] != parent['title'] for row in children))
                self.assertTrue(all(parent['title'] in row['title'] for row in children))
                self.assertTrue(all(row.get('discipline') and row.get('deliverable') for row in children))
                self.assertTrue(all(row.get('workflow_stage_code') for row in children))
                self.assertTrue(all(float(row['duration_days']) > 0 for row in children))
                self.assertTrue(all(float(row['workflow_progress_weight']) > 0 for row in children))
                self.assertAlmostEqual(sum(float(row['workflow_progress_weight']) for row in children), 100, places=2)

    def test_generated_work_has_varied_estimates_and_no_numbered_demo_names(self):
        _, tasks, _ = self.expand()
        work = self.groups(tasks)['foundation']
        self.assertGreater(len({row['duration_days'] for row in work}), 1)
        self.assertGreater(max(row['duration_days'] for row in work), 1)
        for row in work:
            self.assertNotRegex(row['title'].lower(), r'^(?:activity|task|phase)\s*\d+\b')
            self.assertEqual(row['duration_source'], 'proposed')
            self.assertGreaterEqual(row.get('progress_percent', 0), 0)

    def test_blank_editor_wbs_fields_receive_a_valid_deliverable_path(self):
        parents, tasks, _ = self.expand([self.parent(wbs_phase='', wbs_deliverable='')])
        self.assertTrue(parents[0]['wbs_phase'])
        self.assertEqual(parents[0]['wbs_deliverable'], 'Civil Foundation Design')
        for row in self.groups(tasks)['foundation']:
            self.assertTrue(row['wbs_phase'])
            self.assertEqual(row['wbs_deliverable'], parents[0]['wbs_deliverable'])

    def test_all_workflows_are_sequential_fs_and_have_consistent_successor_lists(self):
        _, tasks, _ = self.expand()
        groups = self.groups(tasks)
        for children in groups.values():
            internal_links = []
            ids = {row['id'] for row in children}
            for task in children:
                internal_links.extend((detail['task_id'], task['id'], detail['type'])
                                      for detail in task['dependency_details']
                                      if detail['task_id'] in ids)
            self.assertGreaterEqual(len(internal_links), len(children) - 1)
            self.assertTrue(all(kind == 'FS' for _, _, kind in internal_links))
            first = next(row for row in children if not ids.intersection(row['depends_on']))
            self.assertTrue(ids - {first['id']} <= self.reachable(tasks, first['id']))
        for row in tasks:
            expected = {other['id'] for other in tasks if row['id'] in other['depends_on']}
            self.assertEqual(set(row['successors']), expected)

    def test_only_project_milestones_are_open_and_every_task_is_reachable(self):
        _, tasks, _ = self.expand([
            self.parent(), self.parent('structure', title='Structural Steel Design', discipline='structural'),
        ])
        quality = validate_enterprise_network(tasks)
        self.assertTrue(quality['valid'])
        self.assertEqual(quality['task_count'], len(tasks))
        self.assertEqual(quality['relationship_count'], sum(len(row['depends_on']) for row in tasks))
        starts = [row for row in tasks if not row['depends_on']]
        finishes = [row for row in tasks if not row['successors']]
        self.assertEqual(len(starts), 1)
        self.assertEqual(len(finishes), 1)
        self.assertEqual(starts[0]['activity_type'], 'start_milestone')
        self.assertEqual(finishes[0]['activity_type'], 'finish_milestone')
        self.assertEqual(starts[0]['duration_days'], 0)
        self.assertEqual(finishes[0]['duration_days'], 0)
        self.assertEqual(self.reachable(tasks, starts[0]['id']), {row['id'] for row in tasks} - {starts[0]['id']})
        self.assertTrue(all(finishes[0]['id'] in self.reachable(tasks, row['id'])
                            for row in tasks if row['id'] != finishes[0]['id']))

    def test_unrelated_disciplines_remain_parallel_instead_of_a_row_order_chain(self):
        _, tasks, _ = self.expand([
            self.parent('architecture', title='Architectural Layout Design', discipline='architectural'),
            self.parent('structure', title='Structural Steel Design', discipline='structural'),
        ], project_type='building')
        groups = self.groups(tasks)
        for left, right in [('architecture', 'structure'), ('structure', 'architecture')]:
            other_ids = {row['id'] for row in groups[right]}
            self.assertTrue(all(not other_ids.intersection(self.reachable(tasks, row['id']))
                                for row in groups[left]))

    def test_explicit_deliverable_fs_link_waits_for_predecessor_completion(self):
        parents = [self.parent(), self.parent(
            'steel', title='Structural Steel Detailed Design', discipline='structural',
            depends_on=['foundation'],
            dependency_details=[{'task_id': 'foundation', 'type': 'FS', 'lag_days': 2}],
        )]
        _, tasks, _ = self.expand(parents)
        groups = self.groups(tasks)
        predecessor_ids = {row['id'] for row in groups['foundation']}
        predecessor_final = next(row for row in groups['foundation']
                                 if not predecessor_ids.intersection(row['successors']))
        successor_ids = {row['id'] for row in groups['steel']}
        successor_first = next(row for row in groups['steel']
                               if not successor_ids.intersection(row['depends_on']))
        cross_link = next(row for row in successor_first['dependency_details']
                          if row['task_id'] in predecessor_ids)
        self.assertEqual(cross_link['task_id'], predecessor_final['id'])
        self.assertEqual(cross_link['type'], 'FS')
        self.assertEqual(cross_link['lag_days'], 2)

    def test_ids_are_repeatable_and_do_not_depend_on_parent_order_or_title(self):
        parents = [self.parent(), self.parent('steel', title='Structural Steel Design', discipline='structural')]
        _, first, _ = self.expand(parents)
        _, repeat, _ = self.expand(parents)
        self.assertEqual(first, repeat)
        renamed = deepcopy(parents)
        renamed[0]['title'] = 'Revised Civil Foundation Design'
        _, reordered, _ = self.expand(list(reversed(renamed)))
        self.assertEqual({row['id'] for row in first}, {row['id'] for row in reordered})
        for parent_id, children in self.groups(first).items():
            self.assertEqual({row['id'] for row in children},
                             {row['id'] for row in self.groups(reordered)[parent_id]})

    def test_complex_work_increases_the_estimate_without_changing_scope_or_identity(self):
        _, simple, _ = self.expand(complexity='simple')
        _, complex_tasks, _ = self.expand(complexity='complex')
        simple_work = self.groups(simple)['foundation']
        complex_work = self.groups(complex_tasks)['foundation']
        self.assertEqual({row['id'] for row in simple_work}, {row['id'] for row in complex_work})
        self.assertEqual({row['title'] for row in simple_work}, {row['title'] for row in complex_work})
        self.assertGreater(sum(row['duration_days'] for row in complex_work),
                           sum(row['duration_days'] for row in simple_work))

    def test_regeneration_preserves_planner_duration_assignment_and_progress(self):
        deliverables, tasks, _ = self.expand()
        work = self.groups(tasks)['foundation']
        edited = work[1]
        edited.update(duration_days=7.5, duration_source='planner', assignee_id='engineer-42',
                      progress_percent=35, status='in_progress', effort_hours=18,
                      project_task_id=82, planned_start_date='2026-10-05',
                      duration_basis={'source': 'planner', 'reason': 'Reviewed engineering work estimate'})
        original = deepcopy(tasks)
        _, regenerated, _ = expand_enterprise_deliverables(
            deliverables, {'project_type': 'industrial', 'complexity': 'complex'}, previous_tasks=tasks,
        )
        self.assertEqual(tasks, original)
        preserved = next(row for row in regenerated if row['id'] == edited['id'])
        for key in ('duration_days', 'duration_source', 'assignee_id', 'progress_percent',
                    'status', 'effort_hours', 'project_task_id', 'planned_start_date', 'duration_basis'):
            self.assertEqual(preserved[key], edited[key], key)
        validate_enterprise_network(regenerated)

    def test_regeneration_reestimates_unstarted_proposals_using_current_complexity(self):
        deliverables, tasks, _ = self.expand(complexity='simple')
        old = {row['id']: deepcopy(row) for row in self.groups(tasks)['foundation']}
        _, regenerated, _ = expand_enterprise_deliverables(
            deliverables, {'project_type': 'industrial', 'complexity': 'complex'}, previous_tasks=tasks,
        )
        work = self.groups(regenerated)['foundation']
        self.assertEqual({row['id'] for row in work}, set(old))
        self.assertGreater(sum(row['duration_days'] for row in work),
                           sum(row['duration_days'] for row in old.values()))
        self.assertTrue(all(row['duration_basis']['complexity'] == 'complex' for row in work))
        self.assertTrue(all(row['duration_source'] == 'proposed' for row in work))
        self.assertEqual({row['id']: row for row in self.groups(tasks)['foundation']}, old)

    def test_regeneration_refreshes_automatic_scope_names_but_retains_planner_names(self):
        deliverables, tasks, _ = self.expand()
        first = self.groups(tasks)['foundation'][0]
        first['title'] = 'Confirm foundation loading with the client structural engineer'
        deliverables[0]['title'] = 'North Area Civil Foundation Design'
        _, regenerated, _ = expand_enterprise_deliverables(
            deliverables, {'project_type': 'industrial', 'complexity': 'standard'}, previous_tasks=tasks,
        )
        for row in self.groups(regenerated)['foundation']:
            if row['id'] == first['id']:
                self.assertEqual(row['title'], first['title'])
            else:
                self.assertIn(deliverables[0]['title'], row['title'])
            self.assertEqual(row['deliverable'], deliverables[0]['title'])

    def test_shared_scope_commissioning_waits_for_precommissioning_release(self):
        parents = [
            self.parent('construction', title='Cooling Water Piping Construction', discipline='mechanical',
                        wbs_deliverable='Cooling Water System'),
            self.parent('precommissioning', title='Cooling Water System Pre-Commissioning', discipline='mechanical',
                        wbs_deliverable='Cooling Water System'),
            self.parent('commissioning', title='Cooling Water System Commissioning', discipline='mechanical',
                        wbs_deliverable='Cooling Water System'),
        ]
        _, tasks, _ = self.expand(parents, project_type='oil_gas')
        groups = self.groups(tasks)
        for predecessor, successor in [('construction', 'precommissioning'), ('precommissioning', 'commissioning')]:
            earlier, later = groups[predecessor], groups[successor]
            self.assertIn(earlier[-1]['id'], later[0]['depends_on'])
            self.assertTrue(any(link['task_id'] == earlier[-1]['id'] and link['type'] == 'FS'
                                for link in later[0]['dependency_details']))

    def test_regeneration_removes_withdrawn_parent_links_but_retains_planner_task_links(self):
        deliverables, tasks, _ = self.expand([
            self.parent(), self.parent('steel', title='Structural Steel Design', discipline='structural',
                                      depends_on=['foundation'], dependency_details=[{
                                          'task_id': 'foundation', 'type': 'FS', 'lag_days': 0,
                                          'source': 'planner', 'status': 'confirmed',
                                      }]),
        ])
        groups = self.groups(tasks)
        predecessor, successor = groups['foundation'], groups['steel']
        old_release = predecessor[-1]['id']
        self.assertIn(old_release, successor[0]['depends_on'])
        planner_predecessor, planner_successor = predecessor[1]['id'], successor[2]['id']
        successor[2]['depends_on'].append(planner_predecessor)
        successor[2]['dependency_details'].append({
            'task_id': planner_predecessor, 'type': 'FS', 'lag_days': 2,
            'source': 'planner', 'status': 'confirmed', 'rationale': 'Reviewed technical input handover',
        })
        successor[2]['dependency_rationales'][planner_predecessor] = {
            'source': 'planner', 'status': 'confirmed', 'rationale': 'Reviewed technical input handover',
        }
        deliverables[1].update(depends_on=[], dependency_details=[])
        _, regenerated, _ = expand_enterprise_deliverables(
            deliverables, {'project_type': 'industrial', 'complexity': 'standard'}, previous_tasks=tasks,
        )
        by_id = {row['id']: row for row in regenerated}
        self.assertNotIn(old_release, by_id[successor[0]['id']]['depends_on'])
        retained = next(link for link in by_id[planner_successor]['dependency_details']
                        if link['task_id'] == planner_predecessor)
        self.assertEqual((retained['type'], retained['lag_days'], retained['source'], retained['status']),
                         ('FS', 2, 'planner', 'confirmed'))
        self.assertEqual(retained['rationale'], 'Reviewed technical input handover')
        validate_enterprise_network(regenerated)

    def test_equal_titles_with_distinct_document_numbers_have_distinct_scoped_task_names(self):
        parents = [self.parent('north', document_number='CIV-FOUND-N01'),
                   self.parent('south', document_number='CIV-FOUND-S01')]
        deliverables, tasks, _ = self.expand(parents)
        self.assertEqual([row['title'] for row in deliverables], [row['title'] for row in parents])
        self.assertEqual(len({row['title'] for row in tasks}), len(tasks))
        groups = self.groups(tasks)
        for parent in parents:
            self.assertGreaterEqual(len(groups[parent['id']]), 5)
            self.assertTrue(all(parent['document_number'] in row['title'] for row in groups[parent['id']]))
            self.assertTrue(all(row['document_number'] == parent['document_number'] for row in groups[parent['id']]))
        validate_enterprise_network(tasks)

    def test_changing_workflow_family_reports_replaced_tasks_before_applying(self):
        deliverables, tasks, _ = self.expand()
        old_ids = {row['id'] for row in self.groups(tasks)['foundation']}
        deliverables[0]['title'] = 'Civil Foundation Construction'
        replaced, new_tasks, _ = expand_enterprise_deliverables(
            deliverables, {'project_type': 'industrial', 'complexity': 'standard'}, previous_tasks=tasks,
        )
        new_ids = {row['id'] for row in self.groups(new_tasks)['foundation']}
        removed = old_ids - new_ids
        self.assertTrue(removed)
        blockers = replaced[0]['expansion_blockers']
        self.assertTrue(blockers)
        affected = {task_id for blocker in blockers for task_id in blocker.get('affected_task_ids', [])}
        self.assertTrue(removed <= affected)
        self.assertIn('workflow_replacement_requires_review', {row['code'] for row in blockers})

    def test_bad_parent_networks_are_rejected_before_generation(self):
        cases = {
            'duplicate IDs': [self.parent(), self.parent()],
            'self link': [self.parent(depends_on=['foundation'])],
            'dangling predecessor': [self.parent(depends_on=['missing'])],
            'cycle': [self.parent(depends_on=['steel']), self.parent('steel', depends_on=['foundation'])],
        }
        for label, parents in cases.items():
            with self.subTest(case=label), self.assertRaises(ValueError):
                self.expand(parents)

    def test_validator_rejects_cycles_dangling_links_self_links_and_orphans(self):
        _, original, _ = self.expand()
        work = self.groups(original)['foundation']
        first_id, second_id = work[0]['id'], work[1]['id']
        mutations = {
            'self link': (first_id, [first_id]),
            'dangling predecessor': (first_id, ['missing-task']),
            'cycle': (first_id, [second_id]),
            'orphan': (second_id, []),
        }
        for label, (task_id, predecessors) in mutations.items():
            with self.subTest(case=label):
                tasks = deepcopy(original)
                self.replace_predecessors(tasks, task_id, predecessors)
                with self.assertRaises(ValueError):
                    validate_enterprise_network(tasks)

    def test_validator_rejects_duplicate_identifiers_and_duplicate_work_in_same_scope(self):
        _, original, _ = self.expand()
        for field in ('id', 'title'):
            with self.subTest(field=field):
                tasks = deepcopy(original)
                work = self.groups(tasks)['foundation']
                work[1][field] = work[0][field]
                with self.assertRaises(ValueError):
                    validate_enterprise_network(tasks)

    def test_validator_rejects_missing_or_nonpositive_work_durations(self):
        _, original, _ = self.expand()
        for duration in (None, 0, -1, float('nan'), float('inf')):
            with self.subTest(duration=duration):
                tasks = deepcopy(original)
                self.groups(tasks)['foundation'][0]['duration_days'] = duration
                with self.assertRaises(ValueError):
                    validate_enterprise_network(tasks)
