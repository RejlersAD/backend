"""In-memory proposal checks; no project records, file storage or database."""
from copy import deepcopy
from datetime import date
from math import ceil
from unittest import TestCase
from unittest.mock import patch

from ..services.cpm import WorkdayCalendar, _edge_weight, _finish_date
from ..services.simple_workflow_expansion import expand_workflow_deliverables
from ..services.workflow_sequence_proposal import sequence_workflow_deliverables
from . import test_simple_workflow_expansion as expansion_fixtures


class WorkflowSequenceProposalTests(TestCase):
    def setUp(self):
        self.enterContext(patch('django.db.backends.base.base.BaseDatabaseWrapper.ensure_connection',
                                side_effect=AssertionError('No database access in workflow sequence tests.')))
        self.fixture = expansion_fixtures.SimpleWorkflowExpansionTests()
        self.fixture.setUp()
        self.context = {**self.fixture.context, 'start_date': date(2026, 1, 6),
                        'finish_date': date(2026, 9, 4), 'files': [],
                        'calendar': {'hours_per_day': 8, 'weekdays': [0, 1, 2, 3, 4]}}
        self.calendar = WorkdayCalendar(None, self.context['start_date'])

    def parents(self, *items):
        return [self.fixture.parent(key, title=title, discipline=discipline,
                                    planned_start_date=None, source_references=[{'file_id': 20, 'locator': {'row': index + 1}}])
                for index, (key, title, discipline) in enumerate(items)]

    def expand(self, *items):
        parents, tasks, _ = expand_workflow_deliverables(self.parents(*items), self.context)
        return parents, tasks

    def dated(self, tasks):
        """Create dated snapshots from known test networks without persistence."""
        rows = deepcopy(tasks)
        durations = {task['id']: 0 if task['is_milestone'] else ceil(task['duration_days']) for task in rows}
        starts, remaining = {}, list(rows)
        while remaining:
            ready = [task for task in remaining if all(key in starts for key in task['depends_on'])]
            if not ready:
                raise AssertionError('The test fixture contains a cycle.')
            for task in ready:
                bounds = []
                for predecessor in task['depends_on']:
                    details = [link for link in task['dependency_details'] if link['task_id'] == predecessor] or [{}]
                    for link in details:
                        bounds.append(starts[predecessor] + _edge_weight(link.get('type', 'FS'),
                                      durations[predecessor], durations[task['id']], link.get('lag_days', 0)))
                if task.get('planned_start_date'):
                    bounds.append(self.calendar.index_of(date.fromisoformat(task['planned_start_date'])))
                start = max(bounds, default=0)
                starts[task['id']] = start
                task['planned_start_date'] = self.calendar.date_at(start).isoformat()
                task['planned_finish_date'] = _finish_date(self.calendar, start, durations[task['id']]).isoformat()
                remaining.remove(task)
        return rows

    def sequence(self, parents, tasks):
        return sequence_workflow_deliverables(parents, tasks, self.dated(tasks), self.context, self.calendar)

    def link(self, task, predecessor, kind='FS', lag=0):
        task['depends_on'].append(predecessor['id'])
        task['dependency_details'].append({'task_id': predecessor['id'], 'type': kind, 'lag_days': lag,
                                           'source': 'planner', 'status': 'confirmed'})
        task['dependency_rationales'][predecessor['id']] = {'status': 'confirmed', 'rationale': 'Existing manual gate'}

    def test_survey_philosophy_and_peer_layout_windows_form_sparse_proposed_gates(self):
        parents, tasks = self.expand(('survey', 'SITE VISIT REPORT', 'general'),
                                     ('basis', 'HSE PHILOSOPHY', 'hse'),
                                     ('layout-a', 'F&G DETECTOR LAYOUT - FAR 0', 'hse'),
                                     ('layout-b', 'F&G DETECTOR LAYOUT - FAR 6', 'hse'))
        originals = deepcopy((parents, tasks, self.context))
        result_parents, result, _, assumptions, _, summary = self.sequence(parents, tasks)
        by_id = {task['id']: task for task in result}
        self.assertEqual([parent['schedule_phase'] for parent in result_parents], ['survey', 'basis', 'engineering', 'engineering'])
        self.assertLess(by_id['survey']['planned_start_date'], by_id['basis']['planned_start_date'])
        self.assertLess(by_id['basis']['planned_start_date'], by_id['layout-a']['planned_start_date'])
        self.assertEqual(by_id['layout-a']['planned_start_date'], by_id['layout-b']['planned_start_date'])
        survey_final, basis_final = tasks[4]['id'], tasks[9]['id']
        self.assertIn(survey_final, by_id['basis']['depends_on'])
        self.assertEqual(by_id['layout-a']['depends_on'], [basis_final])
        self.assertEqual(by_id['layout-b']['depends_on'], [basis_final])
        self.assertNotIn(tasks[14]['id'], by_id['layout-b']['depends_on'])
        self.assertEqual(summary['internal_relationship_count'], 16)
        self.assertEqual(summary['cross_deliverable_relationship_count'], 3)
        self.assertEqual(summary['window_count'], 4)
        self.assertEqual([task['duration_days'] for task in result], [task['duration_days'] for task in tasks])
        self.assertTrue(any('five-stage calendar span' in message for message in assumptions))
        self.assertEqual((parents, tasks, self.context), originals)

    def test_manual_stage_dates_durations_assignments_and_explicit_due_dates_are_retained(self):
        parents, tasks = self.expand(('basis', 'HSE PHILOSOPHY', 'hse'), ('layout', 'DETECTOR LAYOUT', 'hse'))
        tasks[5].update(planned_start_date='2026-05-04', assignee_id='employee-1', owner='Engineer',
                        project_task_id=72, due_date='2026-12-10', due_date_source='explicit', effort_hours=48)
        tasks[7].update(duration_days=8, duration_source='planner', planned_start_date='2026-06-01',
                        reviewer_id='reviewer-1', acceptance_criteria='Review fire coverage')
        result = self.sequence(parents, tasks)
        for index in (5, 7):
            for field in ('planned_start_date', 'duration_days', 'duration_source', 'assignee_id', 'owner',
                          'project_task_id', 'due_date', 'due_date_source', 'effort_hours', 'reviewer_id', 'acceptance_criteria'):
                self.assertEqual(result[1][index].get(field), tasks[index].get(field), (index, field))
        self.assertEqual(result[5]['retained_manual_start_count'], 1)

    def test_generated_parent_start_keeps_provenance_when_expanded_but_manual_start_does_not(self):
        parents = self.parents(('generated', 'DETECTOR LAYOUT', 'hse'), ('manual', 'CABLING LAYOUT', 'hse'))
        for parent in parents:
            parent['planned_start_date'] = '2026-01-06'
        parents[0]['schedule_generated_fields'] = ['planned_start_date']
        expanded_parents, tasks, _ = expand_workflow_deliverables(parents, self.context)
        self.assertIn('planned_start_date', tasks[0]['schedule_generated_fields'])
        self.assertNotIn('planned_start_date', tasks[5]['schedule_generated_fields'])
        result = self.sequence(expanded_parents, tasks)
        self.assertGreater(result[1][0]['planned_start_date'], '2026-01-06')
        self.assertEqual(result[1][5]['planned_start_date'], '2026-01-06')
        self.assertEqual(result[5]['retained_manual_start_count'], 1)

    def test_active_work_does_not_receive_new_windows_or_incoming_gates(self):
        parents, tasks = self.expand(('basis', 'HSE PHILOSOPHY', 'hse'), ('layout', 'DETECTOR LAYOUT', 'hse'))
        tasks[6].update(status='in_progress', progress_percent=15)
        _, result, _, _, warnings, summary = self.sequence(parents, tasks)
        self.assertIsNone(result[5]['planned_start_date'])
        self.assertEqual(result[5]['depends_on'], [])
        self.assertEqual(summary['retained_active_deliverable_count'], 1)
        self.assertTrue(any('work in progress' in warning for warning in warnings))

    def test_upstream_deliverables_cannot_indirectly_move_active_work(self):
        parents, tasks = self.expand(('survey', 'SITE VISIT REPORT', 'general'),
                                     ('basis', 'HSE PHILOSOPHY', 'hse'),
                                     ('layout', 'DETECTOR LAYOUT', 'hse'))
        self.link(tasks[5], tasks[4])
        self.link(tasks[10], tasks[9])
        tasks[11].update(status='in_progress', progress_percent=20)
        before = self.dated(tasks)
        result = self.sequence(parents, tasks)
        after = self.dated(result[1])
        self.assertEqual([(task['planned_start_date'], task['planned_finish_date']) for task in before],
                         [(task['planned_start_date'], task['planned_finish_date']) for task in after])
        self.assertEqual(result[5]['protected_upstream_deliverable_count'], 2)
        self.assertEqual(result[5]['changed_window_count'], 0)
        self.assertEqual(result[5]['added_relationship_count'], 0)

    def test_repeated_proposals_keep_ids_windows_and_links_without_duplicate_notes(self):
        parents, tasks = self.expand(('basis', 'HSE PHILOSOPHY', 'hse'), ('layout', 'DETECTOR LAYOUT', 'hse'))
        first = self.sequence(parents, tasks)
        second = self.sequence(first[0], first[1])
        self.assertEqual(first[1], second[1])
        self.assertEqual(first[0], second[0])
        self.assertEqual(second[5]['added_relationship_count'], 0)
        self.assertEqual(second[5]['changed_window_count'], 0)

    def test_manual_midchain_date_does_not_make_review_windows_drift_on_repeat(self):
        parents, tasks = self.expand(('audit', 'HSE AUDIT REPORT @90% OF ENGINEERING COMPLETION', 'hse'))
        tasks[2]['planned_start_date'] = '2026-02-18'
        first = self.sequence(parents, tasks)
        second = self.sequence(first[0], first[1])
        self.assertEqual(first[1], second[1])
        self.assertEqual(first[0], second[0])
        self.assertEqual(second[1][2]['planned_start_date'], '2026-02-18')
        self.assertEqual(second[5]['changed_window_count'], 0)
        # Changing a real stage estimate invalidates the prior basis.
        changed = deepcopy(second[1])
        changed[1]['duration_days'] = 20
        third = self.sequence(second[0], changed)
        self.assertNotEqual(third[0][0]['sequence_window_basis']['signature'],
                            second[0][0]['sequence_window_basis']['signature'])
        self.assertEqual(third[1][1]['duration_days'], 20)

    def test_valid_interleaved_leaf_network_is_not_rejected_as_a_parent_cycle(self):
        parents, tasks = self.expand(('a', 'HSE PHILOSOPHY', 'hse'), ('b', 'DETECTOR LAYOUT', 'hse'))
        self.link(tasks[5], tasks[0], 'SS', 3)
        self.link(tasks[4], tasks[5], 'FF', 2)
        original_links = deepcopy([(task['depends_on'], task['dependency_details'], task['dependency_rationales']) for task in tasks])
        result = self.sequence(parents, tasks)
        self.assertEqual([(task['depends_on'], task['dependency_details'], task['dependency_rationales']) for task in result[1]], original_links)
        self.assertEqual(result[5]['cross_deliverable_relationship_count'], 2)
        self.assertEqual(result[5]['rejected_cycle_count'], 0)

    def test_conflicting_inferred_gate_is_rejected_against_full_leaf_dag(self):
        parents, tasks = self.expand(('basis', 'HSE PHILOSOPHY', 'hse'), ('layout', 'DETECTOR LAYOUT', 'hse'))
        self.link(tasks[4], tasks[5])
        result = self.sequence(parents, tasks)
        self.assertEqual(result[1][5]['depends_on'], [])
        self.assertEqual(result[1][4]['depends_on'], tasks[4]['depends_on'])
        self.assertEqual(result[5]['rejected_cycle_count'], 1)
        self.assertTrue(any('conflicts with the existing activity sequence' in warning for warning in result[4]))

    def test_planner_removed_gate_is_not_silently_reintroduced(self):
        parents, tasks = self.expand(('basis', 'HSE PHILOSOPHY', 'hse'), ('layout', 'DETECTOR LAYOUT', 'hse'))
        tasks[5]['schedule_generated_fields'].remove('depends_on')
        result = self.sequence(parents, tasks)
        self.assertEqual(result[1][5]['depends_on'], [])
        self.assertEqual(result[5]['cross_deliverable_relationship_count'], 0)

    def test_survey_gate_keeps_source_requirement_citation_but_remains_inference(self):
        self.context['files'] = [{'id': 19, 'filename': 'Scope.pdf', 'category': 'sow',
                                 'text': 'Prior to commencement of design, site survey documents require review.'}]
        parents, tasks = self.expand(('survey', 'SITE VISIT REPORT', 'general'), ('basis', 'HSE PHILOSOPHY', 'hse'))
        result = self.sequence(parents, tasks)
        detail = result[1][5]['dependency_details'][0]
        self.assertEqual(detail['source'], 'deliverable_sequence')
        self.assertEqual(detail['status'], 'proposed')
        self.assertEqual(detail['evidence_type'], 'planning_inference')
        self.assertEqual({reference['file_id'] for reference in detail['source_references']}, {19, 20})
        self.assertEqual(detail['parent_predecessor_id'], 'survey')

    def test_maturity_audits_have_distinct_windows_without_serializing_peer_reports(self):
        parents, tasks = self.expand(*[(str(percent), f'HSE AUDIT REPORT @{percent}% OF ENGINEERING COMPLETION', 'hse')
                                       for percent in (30, 60, 90)])
        result = self.sequence(parents, tasks)
        starts = [result[1][index]['planned_start_date'] for index in (0, 5, 10)]
        self.assertEqual(starts, sorted(set(starts)))
        self.assertEqual(result[5]['cross_deliverable_relationship_count'], 0)
        self.assertEqual(result[5]['internal_relationship_count'], 12)
