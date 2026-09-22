"""AI sequence proposals must be useful, isolated, traceable and reviewable."""
from copy import deepcopy
from datetime import date
from unittest.mock import patch, MagicMock

from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import path

from apps.core.project_models import ProjectTask
from apps.rbac.route_guard import secure_module_endpoints
from ..models import ScheduleVersion
from ..simple_planning_views import SimplePlanningView
from ..services.cpm import WorkdayCalendar
from ..services.intelligent_sequence import prepare_sequence, _verified_snapshot
from ..services.planning_boundaries import accepted_input_validation
from ..services.schedule_approval import ScheduleApprovalError
from . import test_simple_planning as fixture
from . import test_source_schedule_preview as source_fixture


urlpatterns = [path('api/v1/planning-intelligence/projects/<int:project_id>/simple-plan/', SimplePlanningView.as_view()),
    *[path(f'api/v1/planning-intelligence/projects/<int:project_id>/simple-plan/{operation}/',
        SimplePlanningView.as_view(operation=operation)) for operation in ('propose-intelligent-sequence',
        'apply-intelligent-sequence', 'preview-source-import', 'apply-source-import', 'calculate', 'submit')]]
secure_module_endpoints(urlpatterns)


def response(tasks, links=None):
    return {'activities': [{'id': row['id'], 'duration_days': 3 if row.get('duration_days') is None else None,
                           'rationale': 'Allow time for preparation and review.'} for row in tasks],
            'relationships': links or [], 'warnings': []}


def link(before, after, **changes):
    return {'predecessor_id': before, 'successor_id': after, 'type': 'FS', 'lag_days': 0,
            'rationale': 'The reviewed upstream output is needed before downstream preparation.', **changes}


@override_settings(ROOT_URLCONF=__name__)
class IntelligentSequenceTests(TestCase):
    def setUp(self):
        cache.clear()
        fixture.SimplePlanningTests.setUp(self)
        self.tasks = [fixture.SimplePlanningTests.task(self, 'a', duration_days=None, effort_hours=None),
                      fixture.SimplePlanningTests.task(self, 'b', title='Handover', duration_days=None, effort_hours=None)]
        self.state = fixture.SimplePlanningTests.save(self, self.tasks)

    def post(self, operation, body, status=200):
        result = self.client.post(self.url + operation + '/', body, format='json')
        self.assertEqual(result.status_code, status, result.data)
        return result.data

    def read(self):
        result = self.client.get(self.url)
        self.assertEqual(result.status_code, 200, result.data)
        return result.data

    def preview(self, raw=None, status=200):
        state = self.read()
        raw = raw or response(state['tasks'], [link('a', 'b')])
        with patch('apps.planning_intelligence.services.intelligent_sequence.generate_sequence', return_value=raw):
            return self.post('propose-intelligent-sequence', {'revision': state['revision']}, status)

    def apply(self, preview=None, status=200):
        preview = preview or self.preview()
        return self.post('apply-intelligent-sequence', {'proposal_token': preview['proposal']['token']}, status)

    def test_preview_proposes_connected_dates_without_business_writes_or_false_cpm(self):
        self.project.refresh_from_db()
        original = deepcopy(self.project.simple_planning_state)
        with CaptureQueriesContext(connection) as queries:
            preview = self.preview()
        self.assertFalse([row for row in queries if row['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))])
        first, second = preview['plan']['tasks']
        self.assertGreater(second['planned_start_date'], first['planned_finish_date'])
        self.assertEqual(preview['proposal']['proposed_duration_count'], 2)
        self.assertEqual(preview['proposal']['proposed_relationship_count'], 1)
        self.assertTrue(preview['proposal']['calendar']['proposed'])
        self.assertFalse(first['calculated'])
        self.assertIsNone(first['total_float_days'])
        self.assertEqual(second['field_provenance']['depends_on']['type'], 'proposal')
        self.assertLess(len(preview['proposal']['token']), 4096)
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, original)

    def test_save_selects_exact_preview_and_preserves_draft_employee_history(self):
        assignment = ProjectTask.objects.create(project=self.enterprise, title='Other work',
            assigned_to=self.reviewer, source_key='unrelated-history', status='in_progress', progress_percent=40)
        self.project.refresh_from_db()
        old = deepcopy(self.project.simple_planning_state)
        preview = self.preview()
        with patch('apps.planning_intelligence.services.intelligent_sequence.generate_sequence') as ai:
            result = self.apply(preview)
            ai.assert_not_called()
        self.project.refresh_from_db()
        assignment.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, old)
        self.assertEqual(assignment.progress_percent, 40)
        self.assertEqual(assignment.assigned_to_id, self.reviewer.pk)
        self.assertEqual(str(self.project.planned_end_date), '2026-12-20')
        state = self.read()
        self.assertEqual([{k: v for k, v in task.items() if k != 'wbs_node_id'} for task in state['tasks']],
                         [{k: v for k, v in task.items() if k != 'wbs_node_id'} for task in preview['plan']['tasks']])
        self.assertEqual([node['name'] for node in state['wbs_nodes']], [node['name'] for node in preview['plan']['wbs_nodes']])
        self.assertEqual({task['wbs_node_id'] for task in state['tasks']}, {node['id'] for node in state['wbs_nodes']})
        self.assertTrue(state['permissions']['can_propose_sequence'])
        self.assertFalse(state['permissions']['can_submit'])
        version = ScheduleVersion.objects.get(pk=result['schedule_version_id'])
        self.assertFalse(version.calculated_at)
        self.assertFalse(accepted_input_validation(version)['ready_for_export'])
        self.assertFalse(self.project.work_calendars.exists())
        repeated = self.apply(preview)
        self.assertEqual(repeated['schedule_version_id'], version.pk)
        self.post('calculate', {'revision': state['revision']}, 409)

    def test_document_or_assignment_change_invalidates_preview(self):
        preview = self.preview()
        self.project.scope_summary = 'Revised scope'
        self.project.save(update_fields=['scope_summary'])
        self.assertEqual(self.apply(preview, 409)['code'], 'intelligent_sequence_stale')
        preview = self.preview()
        ProjectTask.objects.create(project=self.enterprise, title='New active assignment', assigned_to=self.reviewer)
        self.assertEqual(self.apply(preview, 409)['code'], 'intelligent_sequence_stale')

    def test_expired_cache_and_other_actor_cannot_apply(self):
        preview = self.preview()
        cache.clear()
        self.assertEqual(self.apply(preview, 409)['code'], 'intelligent_sequence_stale')
        preview = self.preview()
        self.client.force_authenticate(self.reviewer)
        self.apply(preview, 403)

    def test_partial_invalid_and_cyclic_ai_responses_do_not_apply(self):
        self.preview({'activities': [], 'relationships': [], 'warnings': []}, 409)
        malformed = response(self.tasks)
        malformed['activities'][0]['id'] = ['a']
        self.preview(malformed, 409)
        self.preview(response(self.tasks, [link('a', 'b'), link('b', 'a')]), 409)
        self.assertFalse(self.project.schedules.exists())

    def test_provider_unavailable_is_actionable_and_keeps_draft(self):
        from ..services.project_setup_ai import SetupAIUnavailable
        with patch('apps.planning_intelligence.services.claude_client.get_claude_config', return_value=None), patch(
                'apps.planning_intelligence.services.project_setup_ai.generation_credentials',
                side_effect=SetupAIUnavailable('Add your own API key here.')):
            result = self.post('propose-intelligent-sequence', {'revision': self.state['revision']}, 503)
        self.assertEqual(result['code'], 'intelligent_sequence_ai_unavailable')
        self.assertFalse(self.project.schedules.exists())

    def test_saved_proposal_cannot_hide_relational_edits(self):
        version = ScheduleVersion.objects.get(pk=self.apply()['schedule_version_id'])
        self.assertIsNotNone(_verified_snapshot(version))
        activity = version.activities.first()
        activity.constraint_type = 'must_finish'
        activity.constraint_date = date(2026, 12, 1)
        activity.save(update_fields=['constraint_type', 'constraint_date'])
        self.assertIsNone(_verified_snapshot(version))

    def test_personal_openai_connection_takes_precedence_over_project_provider(self):
        import json
        from ..services.intelligent_sequence import _provider
        self.project.ai_settings = {'enabled': True}
        client = MagicMock()
        client.__enter__.return_value = client
        client.chat.completions.create.return_value = MagicMock(usage=None, choices=[MagicMock(
            finish_reason='stop', message=MagicMock(refusal=None, content=json.dumps(response(self.tasks))))])
        with patch('apps.planning_intelligence.services.project_setup_ai._personal_settings', return_value=object()), patch(
                'apps.planning_intelligence.services.project_setup_ai.generation_credentials', return_value=('test-key', 'test-model', True)), patch(
                'apps.planning_intelligence.services.project_setup_ai.openai_client', return_value=client), patch(
                'apps.planning_intelligence.services.claude_client.get_claude_config') as other_provider, patch(
                'apps.rbac.ai_telemetry.record_usage'):
            result = _provider(self.project, self.owner, {})
        other_provider.assert_not_called()
        self.assertEqual(len(result['activities']), 2)


@override_settings(ROOT_URLCONF=__name__)
class SourceIntelligentSequenceTests(TestCase):
    def setUp(self):
        cache.clear()
        source_fixture.SourceSchedulePreviewTests.setUp(self)

    def test_source_master_can_receive_proposal_without_source_mutation(self):
        preview = self.client.post(self.url + 'preview-source-import/', {
            'source_file_id': self.schedule.pk, 'master_revision': 0}, format='json')
        self.assertEqual(preview.status_code, 200, preview.data)
        applied = self.client.post(self.url + 'apply-source-import/', {'proposal_token': preview.data['proposal_token'],
            'reason': 'Applies to this project', 'acknowledge_scope': True}, format='json')
        self.assertEqual(applied.status_code, 200, applied.data)
        source_id = applied.data['schedule_version_id']
        state = self.client.get(self.url).data
        source_tasks = deepcopy(state['tasks'])
        with patch('apps.planning_intelligence.services.intelligent_sequence.generate_sequence', return_value=response(state['tasks'])):
            sequence = self.client.post(self.url + 'propose-intelligent-sequence/', {'revision': state['revision']}, format='json')
        self.assertEqual(sequence.status_code, 200, sequence.data)
        for original, task in zip(source_tasks, sequence.data['plan']['tasks']):
            self.assertEqual(task['duration_days'], original['duration_days'])
            self.assertEqual(task['planned_start_date'], original['source_start_date'])
            self.assertEqual(task['planned_finish_date'], original['source_finish_date'])
            self.assertFalse(task['proposal_timing'])
        saved = self.client.post(self.url + 'apply-intelligent-sequence/',
            {'proposal_token': sequence.data['proposal']['token']}, format='json')
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertNotEqual(saved.data['schedule_version_id'], source_id)
        self.assertEqual(ScheduleVersion.objects.get(pk=source_id).activities.count(), 2)


class SequenceMathTests(TestCase):
    def run_sequence(self, tasks, relationships):
        return prepare_sequence(tasks, response(tasks, relationships), {'engine': WorkdayCalendar(None, date(2026, 11, 6)), 'hours_per_day': 8},
            start_date=date(2026, 11, 6), finish_date=date(2026, 12, 20))

    def test_source_endpoints_anchor_dependency_even_with_different_printed_duration(self):
        tasks = [{'id': 'a', 'title': 'Source task', 'duration_days': 5, 'duration_source': 'source_document',
            'source_start_date': '2026-11-06', 'source_finish_date': '2026-11-20'},
            {'id': 'b', 'title': 'Downstream', 'duration_days': None}]
        result, _ = self.run_sequence(tasks, [link('a', 'b')])
        self.assertEqual(result[0]['duration_days'], 5)
        self.assertEqual(result[1]['planned_start_date'], '2026-11-23')

    def test_calendar_day_duration_and_weekend_source_dates_stay_exact(self):
        tasks = [{'id': 'a', 'title': 'Elapsed wait', 'duration_days': 2, 'duration_source': 'source_document',
                  'duration_unit': 'calendar_days', 'source_start_date': '2026-11-06'},
                 {'id': 'b', 'title': 'Downstream', 'duration_days': None}]
        result, _ = self.run_sequence(tasks, [link('a', 'b')])
        self.assertEqual(result[0]['planned_finish_date'], '2026-11-07')
        self.assertEqual(result[1]['planned_start_date'], '2026-11-09')

    def test_calendar_day_finish_only_backwards_span_preserves_elapsed_days(self):
        tasks = [{'id': 'a', 'title': 'Elapsed wait', 'duration_days': 2, 'duration_source': 'source_document',
                  'duration_unit': 'calendar_days', 'source_finish_date': '2026-11-09'}]
        result, _ = self.run_sequence(tasks, [])
        self.assertEqual(result[0]['planned_start_date'], '2026-11-08')
        self.assertEqual(result[0]['planned_finish_date'], '2026-11-09')

    def test_conflicting_lag_keeps_original_typed_edge(self):
        tasks = [{'id': 'a', 'title': 'A', 'duration_days': 2}, {'id': 'b', 'title': 'B', 'duration_days': 2,
                  'depends_on': ['a'], 'dependency_details': [{'task_id': 'a', 'type': 'FS', 'lag_days': 3}]}]
        result, summary = self.run_sequence(tasks, [link('a', 'b')])
        self.assertEqual(len(result[1]['dependency_details']), 1)
        self.assertEqual(result[1]['dependency_details'][0]['lag_days'], 3)
        self.assertTrue(any('existing FS' in warning for warning in summary['warnings']))

    def test_infeasible_plan_warns_without_shortening_real_duration_or_extending_project(self):
        tasks = [{'id': 'a', 'title': 'Contract work', 'duration_days': 200, 'duration_source': 'manual'}]
        result, summary = self.run_sequence(tasks, [])
        self.assertEqual(result[0]['duration_days'], 200)
        self.assertFalse(summary['fits_project_window'])
        self.assertTrue(summary['warnings'])

    def test_protected_work_receives_no_incoming_new_links(self):
        tasks = [{'id': 'a', 'title': 'A', 'duration_days': 2},
                 {'id': 'b', 'title': 'Started B', 'duration_days': 2, 'progress_percent': 40}]
        result, summary = self.run_sequence(tasks, [link('a', 'b')])
        self.assertEqual(result[1]['depends_on'], [])
        self.assertEqual(summary['proposed_relationship_count'], 0)

    def test_existing_duration_cannot_be_replaced_by_provider(self):
        tasks = [{'id': 'a', 'title': 'A', 'duration_days': 9}]
        raw = response(tasks)
        raw['activities'][0]['duration_days'] = 2
        with self.assertRaises(ScheduleApprovalError):
            prepare_sequence(tasks, raw, {'engine': WorkdayCalendar(None, date(2026, 11, 6))},
                start_date=date(2026, 11, 6), finish_date=date(2026, 12, 20))

    def test_manually_selected_date_survives_when_duration_is_proposed(self):
        tasks = [{'id': 'a', 'title': 'Manually positioned', 'duration_days': 3, 'duration_source': 'proposed',
                  'planned_start_date': '2026-12-01'}]
        result, _ = self.run_sequence(tasks, [])
        self.assertEqual(result[0]['planned_start_date'], '2026-12-01')

    def test_exact_date_constraint_is_preserved_and_used(self):
        tasks = [{'id': 'a', 'title': 'Constrained activity', 'duration_days': 3,
                  'constraint_type': 'must_start', 'constraint_date': '2026-12-01'}]
        result, _ = self.run_sequence(tasks, [])
        self.assertEqual(result[0]['planned_start_date'], '2026-12-01')

    def test_unsupported_precision_is_rejected_without_rounding(self):
        with self.assertRaises(ScheduleApprovalError):
            self.run_sequence([{'id': 'a', 'title': 'Fractional original', 'duration_days': 2.125}], [])
