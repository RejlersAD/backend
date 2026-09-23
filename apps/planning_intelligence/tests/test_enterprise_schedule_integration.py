"""Generated work packages retain their control data through saved schedules."""
from collections import defaultdict
from copy import deepcopy
from datetime import date

from django.test import TestCase, override_settings
from django.utils import timezone

from ..models import CalendarException, ScheduleBaseline
from ..services.cpm import WorkdayCalendar, calculate_schedule_version
from ..services.enterprise_schedule import expand_enterprise_deliverables, validate_enterprise_network
from ..services.published_schedule import published_plan_state
from ..services.schedule_exports import schedule_snapshot
from ..services.simple_planning import _version_tasks
from ..services.work_breakdown import materialize_work_breakdown
from . import test_simple_planning as fixture


@override_settings(ROOT_URLCONF='apps.planning_intelligence.tests.test_simple_planning')
class EnterpriseScheduleIntegrationTests(TestCase):
    task = fixture.SimplePlanningTests.task
    save = fixture.SimplePlanningTests.save
    action = fixture.SimplePlanningTests.action
    read = fixture.SimplePlanningTests.read

    def setUp(self):
        fixture.SimplePlanningTests.setUp(self)
        self.project.name = 'Cooling Water Building'
        self.project.scope_summary = 'Develop civil foundation drawings and structural design calculations.'
        self.project.effective_date = date(2026, 11, 9)
        self.project.planned_end_date = None
        self.project.save(update_fields=['name', 'scope_summary', 'effective_date', 'planned_end_date'])
        self.parents = [{
            'id': 'foundation-drawings', 'title': 'Civil Foundation Drawings',
            'discipline': 'civil', 'wbs_phase': 'Detailed Engineering',
            'wbs_deliverable': 'Cooling Water Building Foundations',
            'depends_on': [], 'dependency_details': [],
        }, {
            'id': 'foundation-calculations', 'title': 'Civil Foundation Design Calculations',
            'discipline': 'civil', 'wbs_phase': 'Detailed Engineering',
            'wbs_deliverable': 'Cooling Water Building Foundations',
            'depends_on': [], 'dependency_details': [],
        }]

    def draft(self):
        parents, tasks, _ = expand_enterprise_deliverables(
            self.parents, {'project_type': 'building', 'complexity': 'standard'},
        )
        return {'revision': 1, 'deliverables': parents, 'tasks': tasks,
                'disciplines': [{'code': 'civil', 'name': 'Civil Engineering'}],
                'workflow_mode': 'enterprise'}

    def materialize(self, draft):
        return materialize_work_breakdown(
            self.project, draft, actor=self.owner,
            start=self.project.effective_date, token='enterprise-persistence-test',
        )

    def save_unexpanded_packages(self):
        self.enterprise.custom_fields = {'project_type': 'building', 'complexity': 'standard'}
        self.enterprise.save(update_fields=['custom_fields'])
        self.project.planned_end_date = date(2027, 4, 30)
        self.project.save(update_fields=['planned_end_date'])
        return self.save(
            [self.task(**parent, effort_hours=None, duration_days=None) for parent in self.parents],
            disciplines=[{'code': 'civil', 'name': 'Civil Engineering'}],
        )

    def test_enterprise_preview_apply_and_existing_editor_save_keep_six_stage_packages(self):
        saved = self.save_unexpanded_packages()
        preview = self.action('propose-schedule', saved['revision'], workflow_mode='enterprise')
        self.assertEqual(preview['proposal']['workflow_mode'], 'enterprise')
        self.assertEqual(preview['proposal']['duration_policy'], 'planning_assumptions')
        self.assertFalse(self.project.schedules.exists())
        self.project.refresh_from_db()
        self.assertEqual(len(self.project.simple_planning_state['tasks']), 2)
        groups = defaultdict(list)
        for task in preview['plan']['tasks']:
            if task.get('parent_deliverable_id'):
                groups[task['parent_deliverable_id']].append(task)
        self.assertEqual(set(groups), {parent['id'] for parent in self.parents})
        self.assertTrue(all(len(rows) == 6 for rows in groups.values()))
        applied = self.action('apply-schedule', saved['revision'], proposal_token=preview['proposal']['token'])
        expected = {row['id']: row for row in applied['tasks']}
        # These are existing editable fields; generation metadata remains server-owned.
        fields = ('id', 'title', 'discipline', 'owner', 'effort_hours', 'depends_on',
                  'dependency_details', 'acceptance_criteria', 'reviewer', 'assignee_id',
                  'reviewer_id', 'task_type', 'due_date', 'priority', 'duration_days',
                  'planned_start_date', 'planned_finish_date', 'constraint_type',
                  'constraint_date', 'wbs_phase', 'wbs_deliverable')
        payload = [{field: row[field] for field in fields if field in row} for row in applied['tasks']]
        resaved = self.save(payload, revision=applied['revision'], disciplines=applied['disciplines'])
        reloaded = self.read()
        self.assertEqual(reloaded['workflow_mode'], 'enterprise')
        self.assertEqual({row['id'] for row in reloaded['tasks']}, set(expected))
        self.assertEqual(len(reloaded['deliverables']), 2)
        for row in reloaded['tasks']:
            for field in ('generation_method', 'task_deliverable', 'progress_measurement'):
                self.assertEqual(row[field], expected[row['id']][field])
            if row.get('parent_deliverable_id'):
                self.assertEqual(row['duration_basis'], expected[row['id']]['duration_basis'])
        validate_enterprise_network(reloaded['tasks'])
        for mode in ({'workflow_mode': 'source_only'}, {}):
            with self.subTest(existing_button_request=mode):
                rebuilt = self.action('propose-schedule', reloaded['revision'], **mode)
                self.assertEqual(rebuilt['proposal']['workflow_mode'], 'enterprise')
                self.assertEqual(rebuilt['proposal']['duration_policy'], 'planning_assumptions')
                self.assertEqual({row['id']: row['duration_days'] for row in rebuilt['plan']['tasks']},
                                 {row['id']: row['duration_days'] for row in reloaded['tasks']})
                validate_enterprise_network(rebuilt['plan']['tasks'])
        self.project.refresh_from_db()
        version = self.materialize(deepcopy(self.project.simple_planning_state))
        calculate_schedule_version(version, requested_by=self.owner)
        history = self.client.get(self.url, {'version_id': version.pk})
        self.assertEqual(history.status_code, 200, history.data)
        self.assertEqual(history.data['workflow_mode'], 'enterprise')
        self.assertEqual({row['id'] for row in history.data['tasks']}, set(expected))
        self.assertEqual(resaved['revision'], reloaded['revision'])

    def test_enterprise_proposal_token_expires_when_type_or_complexity_changes(self):
        saved = self.save_unexpanded_packages()
        for key, value in [('project_type', 'industrial'), ('complexity', 'complex')]:
            with self.subTest(field=key):
                self.enterprise.custom_fields = {'project_type': 'building', 'complexity': 'standard'}
                self.enterprise.save(update_fields=['custom_fields'])
                preview = self.action('propose-schedule', saved['revision'], workflow_mode='enterprise')
                self.enterprise.custom_fields[key] = value
                self.enterprise.save(update_fields=['custom_fields'])
                response = self.client.post(self.url + 'apply-schedule/', {
                    'revision': saved['revision'], 'proposal_token': preview['proposal']['token'],
                }, format='json')
                self.assertEqual(response.status_code, 409, response.data)
                self.assertEqual(response.data['code'], 'simple_plan_proposal_stale')
                self.project.refresh_from_db()
                self.assertEqual(self.project.simple_planning_state['revision'], saved['revision'])
                self.assertEqual(len(self.project.simple_planning_state['tasks']), 2)

    def test_shared_wbs_contains_two_packages_and_only_leaf_tasks_enter_cpm(self):
        draft = self.draft()
        before = deepcopy(draft)
        version = self.materialize(draft)
        self.assertEqual(draft, before)
        self.assertEqual(version.activities.count(), len(draft['tasks']))
        phase = version.wbs_nodes.get(name='Detailed Engineering')
        deliverable = version.wbs_nodes.get(name='Cooling Water Building Foundations', parent=phase)
        packages = list(version.wbs_nodes.filter(parent=deliverable))
        self.assertEqual({node.name for node in packages}, {row['title'] for row in self.parents})
        self.assertEqual(len({node.code for node in packages}), 2)
        for node in packages:
            self.assertGreaterEqual(node.activities.count(), 5)
        self.assertFalse(version.activities.filter(name__in=[
            phase.name, deliverable.name, *(row['title'] for row in self.parents),
        ]).exists())
        self.assertFalse(phase.activities.exists())
        self.assertFalse(deliverable.activities.exists())
        self.assertTrue(all(row.duration_days > 0 for row in version.activities.filter(activity_type='task')))
        validate_enterprise_network(_version_tasks(version))

    def test_duration_deliverable_progress_and_relationships_survive_persistence_and_api_read(self):
        draft = self.draft()
        version = self.materialize(draft)
        calculate_schedule_version(version, requested_by=self.owner)
        restored = {row['id']: row for row in _version_tasks(version)}
        activities = {row.external_id: row for row in version.activities.all()}
        response = self.client.get(self.url, {'version_id': version.pk})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['viewing_history'])
        self.assertEqual(response.data['workflow_mode'], 'enterprise')
        api_tasks = {row['id']: row for row in response.data['tasks']}
        self.assertEqual(set(api_tasks), {row['id'] for row in draft['tasks']})
        for expected in draft['tasks']:
            with self.subTest(task=expected['title']):
                saved = activities[expected['id']]
                for field in ('generation_method', 'task_deliverable', 'progress_measurement'):
                    self.assertEqual(saved.metadata[field], expected[field])
                    self.assertEqual(restored[expected['id']][field], expected[field])
                    self.assertEqual(api_tasks[expected['id']][field], expected[field])
                if expected.get('parent_deliverable_id'):
                    self.assertEqual(saved.metadata['duration_basis'], expected['duration_basis'])
                    self.assertEqual(restored[expected['id']]['duration_basis'], expected['duration_basis'])
                    self.assertEqual(api_tasks[expected['id']]['duration_basis'], expected['duration_basis'])
                    self.assertEqual(api_tasks[expected['id']]['parent_deliverable_id'], expected['parent_deliverable_id'])
                self.assertEqual(set(restored[expected['id']]['depends_on']), set(expected['depends_on']))
                self.assertEqual(set(restored[expected['id']]['successors']), set(expected['successors']))
                self.assertEqual(set(api_tasks[expected['id']]['successors']), set(expected['successors']))
                self.assertEqual(api_tasks[expected['id']]['planned_start_date'], saved.planned_start.isoformat())
                self.assertEqual(api_tasks[expected['id']]['planned_finish_date'], saved.planned_finish.isoformat())
                self.assertTrue(api_tasks[expected['id']]['calculated'])

    def test_generated_parallel_network_calculates_critical_path_and_obeys_calendar(self):
        draft = self.draft()
        groups = defaultdict(list)
        for task in draft['tasks']:
            if task.get('parent_deliverable_id'):
                groups[task['parent_deliverable_id']].append(task)
        # Add a reviewed allowance that creates a known seven-day difference.
        longest, shortest = sorted(groups.values(), key=lambda rows: sum(row['duration_days'] for row in rows), reverse=True)
        longest[0]['duration_days'] += 7
        longest[0]['duration_source'] = 'planner'
        expected_float = sum(row['duration_days'] for row in longest) - sum(row['duration_days'] for row in shortest)
        version = self.materialize(draft)
        calendar = version.schedule.default_calendar
        CalendarException.objects.create(calendar=calendar, date=date(2026, 11, 10),
                                         is_working=False, name='Project holiday')
        run = calculate_schedule_version(version, requested_by=self.owner)
        self.assertEqual(run.status, 'succeeded')
        activities = {row.external_id: row for row in version.activities.all()}
        working = WorkdayCalendar(calendar, self.project.effective_date)
        finish = next(row for row in activities.values() if row.activity_type == 'finish_milestone')
        self.assertEqual(finish.planned_start, working.date_at(sum(row['duration_days'] for row in longest)))
        self.assertEqual(activities[longest[0]['id']].planned_start, self.project.effective_date)
        self.assertEqual(activities[shortest[0]['id']].planned_start, self.project.effective_date)
        self.assertTrue(all(activities[row['id']].is_critical for row in longest))
        self.assertTrue(all(activities[row['id']].total_float_days == expected_float for row in shortest))
        self.assertTrue(all(not activities[row['id']].is_critical for row in shortest))
        for row in activities.values():
            self.assertTrue(working.is_working(row.planned_start))
            self.assertTrue(working.is_working(row.planned_finish))
        for link in version.relationships.select_related('predecessor', 'successor'):
            self.assertEqual(link.relationship_type, 'FS')
            if not link.predecessor.is_milestone:
                self.assertGreater(link.successor.planned_start, link.predecessor.planned_finish)

    def test_published_snapshot_retains_enterprise_control_metadata_policy_and_successors(self):
        draft = self.draft()
        version = self.materialize(draft)
        calculate_schedule_version(version, requested_by=self.owner)
        snapshot = schedule_snapshot(version)
        frozen = {key: snapshot[key] for key in ('version', 'activities', 'relationships', 'wbs')}
        frozen['accepted_inputs'] = snapshot['traceability']
        baseline = ScheduleBaseline.objects.create(
            schedule=version.schedule, source_version=version, name='Enterprise execution baseline',
            approved_by=self.owner, approved_at=timezone.now(), snapshot=frozen,
        )
        original = deepcopy(baseline.snapshot)
        with self.assertNumQueries(0):
            published = published_plan_state(baseline)
        self.assertEqual(published['workflow_mode'], 'enterprise')
        self.assertEqual(published['evidence_policy'], 'planning_assumptions')
        self.assertEqual(published['duration_policy'], 'planning_assumptions')
        self.assertEqual(len(published['deliverables']), 2)
        restored = {row['id']: row for row in published['tasks']}
        for expected in draft['tasks']:
            actual = restored[expected['id']]
            for field in ('generation_method', 'task_deliverable', 'progress_measurement'):
                self.assertEqual(actual[field], expected[field])
            if expected.get('parent_deliverable_id'):
                self.assertEqual(actual['duration_basis'], expected['duration_basis'])
            self.assertEqual(set(actual['depends_on']), set(expected['depends_on']))
            self.assertEqual(set(actual['successors']), set(expected['successors']))
            self.assertEqual(actual['duration_days'], expected['duration_days'])
        validate_enterprise_network(published['tasks'])
        version.activities.update(name='Changed after publication', duration_days=999)
        calendar = version.schedule.default_calendar
        calendar.working_weekdays = [6]
        calendar.save(update_fields=['working_weekdays'])
        self.project.name = 'Renamed after publication'
        self.project.save(update_fields=['name'])
        with self.assertNumQueries(0):
            self.assertEqual(published_plan_state(baseline), published)
        self.assertEqual(baseline.snapshot, original)
        baseline.refresh_from_db()
        self.assertEqual(baseline.snapshot, original)
