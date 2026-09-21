"""Source-only duration review survives the real planning save/preview boundary."""
from copy import deepcopy
import hashlib

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from apps.core.project_models import ProjectTask

from ..models import PlanningFile, ScheduleVersion
from ..services.simple_planning import _dated_tasks, _seed_task, SimplePlanningError
from . import test_simple_planning as simple_fixture
from . import test_workflow_materialization as workflow_fixture
from .test_source_timing_constraints import printed_text
from .test_source_date_read_model import evidence as date_evidence


@override_settings(ROOT_URLCONF='apps.planning_intelligence.tests.test_simple_planning')
class SourceDurationPlanningTests(TestCase):
    task = simple_fixture.SimplePlanningTests.task
    read = simple_fixture.SimplePlanningTests.read
    save = simple_fixture.SimplePlanningTests.save
    action = simple_fixture.SimplePlanningTests.action

    def setUp(self):
        workflow_fixture.WorkflowPlanningIntegrationTests.setUp(self)

    def test_read_displays_stored_source_dates_without_calculating_or_writing_them(self):
        self.save([self.task('source', duration_days=None)])
        self.project.refresh_from_db()
        state = deepcopy(self.project.simple_planning_state)
        state['tasks'][0].update(duration_days=5, duration_source='source_document',
                                 duration_calendar_verified=False, duration_evidence=date_evidence(),
                                 planned_start_date=None, planned_finish_date=None)
        self.project.simple_planning_state = state
        self.project.save(update_fields=['simple_planning_state'])
        before = deepcopy(state)
        with CaptureQueriesContext(connection) as queries:
            read = self.read()
        task = read['tasks'][0]
        self.assertEqual(task['source_start_date'], '2026-02-23')
        self.assertEqual(task['source_finish_date'], '2026-03-02')
        self.assertEqual(task['source_date_status'], 'extracted')
        self.assertIsNone(task['planned_start_date'])
        self.assertIsNone(task['planned_finish_date'])
        self.assertIsNone(task['total_float_days'])
        self.assertFalse(task['calculated'])
        self.assertFalse(read['calculation_available'])
        self.assertFalse([row for row in queries if row['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))])
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, before)

    def test_save_ignores_client_forged_source_display_dates(self):
        saved = self.save([self.task('source', duration_days=None)])
        task = saved['tasks'][0]
        task.update(source_start_date='2026-01-06', source_finish_date='2026-09-04',
                    source_date_status='extracted')
        result = self.save([task], saved['revision'])
        self.assertIsNone(result['tasks'][0]['source_start_date'])
        self.assertIsNone(result['tasks'][0]['source_finish_date'])
        self.assertEqual(result['tasks'][0]['source_date_status'], 'not_specified')
        self.project.refresh_from_db()
        self.assertNotIn('source_start_date', self.project.simple_planning_state['tasks'][0])

    def test_seed_and_save_do_not_turn_missing_duration_or_effort_into_a_template(self):
        task = self.task(duration_days=None, effort_hours=80)
        self.assertIsNone(_seed_task(task)['duration_days'])
        saved = self.save([task])
        self.assertIsNone(saved['tasks'][0]['duration_days'])
        self.assertIsNone(saved['tasks'][0]['planned_start_date'])
        self.assertIsNone(saved['tasks'][0]['planned_finish_date'])
        roundtrip = self.save(saved['tasks'], saved['revision'])
        self.assertIsNone(roundtrip['tasks'][0]['duration_days'])
        self.assertFalse(roundtrip['calculation_available'])
        self.assertEqual(roundtrip['tasks'][0]['effort_hours'], 80)

    def test_unknown_duration_propagates_to_successors_without_inventing_one_day_dates(self):
        tasks = [self.task('missing', duration_days=None),
                 self.task('next', duration_days=3, depends_on=['missing']),
                 self.task('unrelated', duration_days=2)]
        dated = {row['id']: row for row in _dated_tasks(self.project, tasks)}
        for key in ('missing', 'next'):
            self.assertIsNone(dated[key]['planned_start_date'])
            self.assertIsNone(dated[key]['planned_finish_date'])
            self.assertIsNone(dated[key]['total_float_days'])
            self.assertFalse(dated[key]['calculated'])
        self.assertIsNotNone(dated['unrelated']['planned_finish_date'])
        self.assertIsNone(dated['unrelated']['total_float_days'])
        self.assertEqual(tasks[1]['duration_days'], 3)

    def test_missing_durations_do_not_hide_cycles_or_invalid_predecessors(self):
        for tasks in ([self.task('a', duration_days=None, depends_on=['b']),
                       self.task('b', duration_days=None, depends_on=['a'])],
                      [self.task('a', duration_days=None, depends_on=['unknown'])]):
            with self.subTest(tasks=tasks), self.assertRaises(SimplePlanningError):
                _dated_tasks(self.project, tasks)

    def test_source_duration_with_unverified_calendar_does_not_claim_calculated_dates(self):
        source = self.task('source', duration_days=5, duration_source='source_document',
                           duration_calendar_verified=False, planned_start_date='2026-02-23')
        tasks = [source, self.task('next', duration_days=2, depends_on=['source'])]
        dated = _dated_tasks(self.project, tasks)
        self.assertEqual(dated[0]['duration_days'], 5)
        self.assertTrue(all(row['planned_start_date'] is None and row['planned_finish_date'] is None for row in dated))
        self.assertEqual(source['planned_start_date'], '2026-02-23')

    def test_all_known_source_durations_cannot_publish_on_an_unverified_fallback_calendar(self):
        saved = self.save()
        self.project.refresh_from_db()
        state = deepcopy(self.project.simple_planning_state)
        state['tasks'][0].update(duration_source='source_document', duration_calendar_verified=False)
        self.project.simple_planning_state = state
        self.project.save(update_fields=['simple_planning_state'])
        self.assertFalse(self.project.files.exists())  # Guard is independent of file-category checks.
        read = self.read()
        self.assertIn('source_calendar_unverified', {row['code'] for row in read['blockers']})
        self.assertFalse(read['permissions']['can_submit'])
        response = self.client.post(self.url + 'submit/', {'revision': saved['revision']}, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertIn('source_calendar_unverified', {row['code'] for row in response.data['blockers']})
        self.assertFalse(ScheduleVersion.objects.filter(schedule__project=self.project).exists())

    def test_signed_proposal_can_apply_unknowns_without_creating_a_schedule_version(self):
        saved = self.save([self.task(title='No duration in the MDR', duration_days=None, effort_hours=40)])
        self.project.refresh_from_db()
        before = deepcopy(self.project.simple_planning_state)
        with CaptureQueriesContext(connection) as queries:
            preview = self.action('propose-schedule', saved['revision'], workflow_mode='standard_five')
        self.assertFalse([row for row in queries if row['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))])
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, before)
        self.assertEqual(len(preview['plan']['tasks']), 5)
        self.assertTrue(all(row['duration_days'] is None for row in preview['plan']['tasks']))
        self.assertIsNone(preview['plan']['deliverables'][0]['duration_days'])
        applied = self.action('apply-schedule', saved['revision'], proposal_token=preview['proposal']['token'])
        self.assertEqual(applied['revision'], saved['revision'] + 1)
        self.assertTrue(all(row['duration_days'] is None for row in applied['tasks']))
        self.assertTrue(all(row['planned_finish_date'] is None for row in applied['tasks']))
        self.assertIsNone(applied['deliverables'][0]['duration_days'])
        self.assertFalse(ScheduleVersion.objects.filter(schedule__project=self.project).exists())
        again = self.action('apply-schedule', saved['revision'], proposal_token=preview['proposal']['token'])
        self.assertEqual(again['revision'], applied['revision'])

    def test_rebuilding_an_expanded_source_only_workflow_never_restores_template_estimates(self):
        saved = self.save([self.task(title='No source timing', duration_days=None)])
        first = self.action('propose-schedule', saved['revision'], workflow_mode='standard_five')
        applied = self.action('apply-schedule', saved['revision'], proposal_token=first['proposal']['token'])
        second = self.action('propose-schedule', applied['revision'], workflow_mode='standard_five')
        fields = ('id', 'duration_days', 'duration_source', 'depends_on', 'planned_start_date', 'planned_finish_date')
        self.assertEqual([{key: row.get(key) for key in fields} for row in applied['tasks']],
                         [{key: row.get(key) for key in fields} for row in second['plan']['tasks']])
        self.assertEqual(second['proposal']['relationship_count'], 4)

    def test_same_named_printed_package_does_not_supply_unlinked_workflow_stage_durations(self):
        PlanningFile.objects.create(project=self.project, category='reference_schedule', file='test/schedule.pdf',
            original_filename='Schedule.pdf', parse_status='done', extracted_text=printed_text(), uploaded_by=self.owner)
        saved = self.save([self.task(title='MASTER DELIVERABLE REGISTER', duration_days=None)])
        preview = self.action('propose-schedule', saved['revision'], workflow_mode='standard_five')
        tasks = preview['plan']['tasks']
        self.assertEqual([row['duration_days'] for row in tasks], [None] * 5)
        self.assertTrue(all(row['planned_start_date'] is None and row['planned_finish_date'] is None for row in tasks))
        self.assertEqual(preview['proposal']['duration_review']['source_document_count'], 0)
        self.assertEqual(preview['proposal']['duration_review']['missing_source_count'], 5)

    def test_saved_canvas_exposes_current_duration_review_without_stale_source_badges(self):
        source = PlanningFile.objects.create(project=self.project, category='reference_schedule', file='test/schedule.pdf',
            original_filename='Schedule.pdf', parse_status='done', extracted_text=printed_text(), uploaded_by=self.owner)
        saved = self.save([self.task(title='MASTER DELIVERABLE REGISTER', duration_days=None)])
        preview = self.action('propose-schedule', saved['revision'], workflow_mode='standard_five')
        expanded = self.action('apply-schedule', saved['revision'], proposal_token=preview['proposal']['token'])
        # This test starts from server-held, explicit source pointers. A parent
        # title alone no longer authorizes copying timing into its five stages.
        self.project.refresh_from_db()
        state = deepcopy(self.project.simple_planning_state)
        reference = {'file_id': source.pk,
                     'extracted_text_sha256': hashlib.sha256(printed_text().encode('utf-8')).hexdigest()}
        for task, activity_id in zip(state['tasks'], ['GEN_1850', 'GEN_1860', 'GEN_1870', 'GEN_1880']):
            task.update(source_activity_id=activity_id, source_references=[deepcopy(reference)])
        state['deliverables'][0]['source_references'] = [{**reference, 'locator': {'line': 4}}]
        self.project.simple_planning_state = state
        self.project.save(update_fields=['simple_planning_state'])
        preview = self.action('propose-schedule', expanded['revision'], workflow_mode='source_only')
        applied = self.action('apply-schedule', expanded['revision'], proposal_token=preview['proposal']['token'])
        review = applied['duration_review']
        self.assertEqual(applied['deliverables'][0]['duration_days'], 40)
        self.assertEqual(applied['deliverables'][0]['duration_source'], 'source_document')
        self.assertIsNone(applied['deliverables'][0]['summary']['duration_days'])
        self.assertEqual(review['total_count'], 5)
        self.assertEqual(review['source_document_count'], 4)
        self.assertEqual(review['missing_source_count'], 1)
        self.assertEqual([row['duration_days'] for row in review['rows']], [5, 10, 10, 5, None])
        self.assertEqual(self.read()['duration_review'], review)

        edited = deepcopy(applied['tasks'])
        edited[0]['duration_days'] = 7  # A planner override cannot retain the source-verified badge.
        updated = self.save(edited, applied['revision'])
        current = updated['duration_review']
        self.assertEqual(current['source_document_count'], 3)
        self.assertEqual(current['manual_unverified_count'], 1)
        self.assertEqual(current['missing_source_count'], 1)
        self.assertEqual(current['rows'][0]['duration_days'], 7)
        self.assertEqual(current['rows'][0]['status'], 'manual_unverified')
        self.assertEqual(current['rows'][0]['duration_source'], 'planner')
        self.assertIsNone(current['rows'][-1]['duration_days'])
        self.assertIsNone(updated['tasks'][-1]['planned_finish_date'])
        package = current['package_reviews'][0]
        # The separate activity-sum review cannot infer a package source link
        # merely from the children's shared deliverable title.
        self.assertIsNone(package['source_original_duration_days'])
        self.assertEqual(package['source_references'], [])
        self.assertFalse(package['duration_complete'])
        self.assertIsNone(package['duration_days'])
        self.assertEqual(package['missing_duration_count'], 1)
        with CaptureQueriesContext(connection) as queries:
            reloaded = self.read()
        self.assertEqual(reloaded['duration_review'], current)
        self.assertFalse([row for row in queries if row['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))])

    def test_proposal_rejects_tampering_even_when_every_duration_is_unknown(self):
        saved = self.save([self.task(duration_days=None)])
        preview = self.action('propose-schedule', saved['revision'], workflow_mode='standard_five')
        response = self.client.post(self.url + 'apply-schedule/', {
            'revision': saved['revision'], 'proposal_token': preview['proposal']['token'] + 'x',
        }, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.read()['revision'], saved['revision'])

    def test_started_flat_assignment_cannot_be_converted_into_five_new_stages(self):
        saved = self.save()
        self.project.refresh_from_db()
        before = deepcopy(self.project.simple_planning_state)
        assignment = ProjectTask.objects.create(
            project=self.enterprise, title='Deliverable already in progress', assigned_to=self.other,
            status='in_progress', progress_percent=20,
            source_key=f'wbs:{self.project.pk}:task-a',
            metadata={'preview_confirmed_at': before['assignment_token']},
        )
        preview = self.action('propose-schedule', saved['revision'], workflow_mode='standard_five')
        self.assertIn('workflow_started_parent', {row['code'] for row in preview['proposal']['expansion_blockers']})
        response = self.client.post(self.url + 'apply-schedule/', {
            'revision': saved['revision'], 'proposal_token': preview['proposal']['token'],
        }, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'workflow_expansion_blocked')
        self.project.refresh_from_db()
        assignment.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, before)
        self.assertEqual(assignment.title, 'Deliverable already in progress')
        self.assertEqual(assignment.progress_percent, 20)
        self.assertFalse(assignment.is_deleted)
