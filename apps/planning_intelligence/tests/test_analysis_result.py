"""An analysed SOW must explain missing activities and allow safe retries."""
from copy import deepcopy
import json
from unittest.mock import Mock, patch

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from ..models import PlanningFile, Schedule, ScheduleActivity, ScheduleVersion
from ..services import byok_crypto, project_ai
from . import test_simple_planning as fixture


@override_settings(ROOT_URLCONF='apps.planning_intelligence.tests.test_simple_planning')
class AnalysisResultTests(TestCase):
    setUp = fixture.SimplePlanningTests.setUp
    read = fixture.SimplePlanningTests.read
    save = fixture.SimplePlanningTests.save
    action = fixture.SimplePlanningTests.action
    task = fixture.SimplePlanningTests.task

    def source(self):
        return PlanningFile.objects.create(
            project=self.project, category='sow', file='tests/scope.pdf',
            original_filename='Scope.pdf', parse_status='done', uploaded_by=self.owner,
            extracted_text='The CONTRACTOR shall prepare the Fire and Gas Mapping Report.\n'
                           'The CONTRACTOR must maintain document records.',
        )

    def analyse_without_provider(self):
        with patch('apps.planning_intelligence.services.claude_client.get_claude_config', return_value=None):
            return self.action('analyse', self.read()['revision'])

    def test_missing_provider_explains_requirements_without_activities_and_preserves_findings(self):
        self.source()
        plan = self.analyse_without_provider()
        outcome = plan['analysis_result']
        self.assertEqual(outcome['status'], 'no_activities')
        self.assertEqual(outcome['activity_count'], 0)
        self.assertEqual(outcome['requirement_count'], 2)
        self.assertEqual(outcome['code'], 'provider_not_configured')
        self.assertEqual(outcome['next_action'], 'ai_settings')
        self.assertEqual(plan['tasks'], [])
        self.assertTrue(any(item['code'] == 'analysis_no_activities' for item in plan['warnings']))
        self.assertEqual(self.project.intelligence_runs.get().facts.filter(fact_type='requirement').count(), 2)
        self.assertFalse(self.project.schedules.exists())

    def test_existing_empty_analysis_gets_diagnostic_without_writing_or_leaking_to_history(self):
        self.source()
        self.analyse_without_provider()
        self.project.refresh_from_db()
        self.project.simple_planning_state.pop('extraction_summary')
        self.project.save(update_fields=['simple_planning_state'])
        before = deepcopy(self.project.simple_planning_state)
        with CaptureQueriesContext(connection) as queries:
            plan = self.read()
        self.assertEqual(plan['analysis_result']['code'], 'provider_not_configured')
        self.assertEqual(plan['analysis_result']['requirement_count'], 2)
        self.assertFalse(any(query['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for query in queries))
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, before)

        schedule = Schedule.objects.create(project=self.project, name='Earlier schedule', code='EARLIER',
                                           planned_start=self.project.effective_date)
        version = ScheduleVersion.objects.create(schedule=schedule, version=1)
        ScheduleActivity.objects.create(version=version, external_id='EARLIER-01', name='Earlier work', duration_days=2)
        historical = self.client.get(self.url, {'version_id': version.pk})
        self.assertEqual(historical.status_code, 200)
        self.assertIsNone(historical.data.get('analysis_result'))
        self.assertFalse(any(item['code'] == 'analysis_no_activities' for item in historical.data['warnings']))

    def test_empty_saved_analysis_retries_same_inputs_and_creates_only_quoted_deliverable(self):
        source = self.source()
        previous = self.analyse_without_provider()
        response = {'text': json.dumps({'facts': [{
            'type': 'deliverable', 'value': 'Fire and Gas Mapping Report',
            'source_file_id': source.pk, 'quote': source.extracted_text.splitlines()[0],
            'quote_start': 0, 'discipline': None,
        }]}), 'stop_reason': 'end_turn'}
        with patch('apps.planning_intelligence.services.claude_client.get_claude_config', return_value={'enabled': True}), \
                patch('apps.planning_intelligence.services.claude_client.call_claude', return_value=response) as ai:
            plan = self.action('analyse', previous['revision'])
        ai.assert_called_once()
        self.assertEqual(plan['revision'], previous['revision'] + 1)
        self.assertEqual(self.project.intelligence_runs.count(), 2)
        self.assertEqual([task['title'] for task in plan['tasks']], ['Fire and Gas Mapping Report'])
        self.assertEqual(plan['analysis_result']['status'], 'activities_created')
        self.assertIsNone(plan['tasks'][0]['duration_days'])
        self.assertIsNone(plan['tasks'][0]['planned_start_date'])
        self.assertFalse(any(item['code'] == 'analysis_no_activities' for item in plan['warnings']))

    def test_populated_saved_plan_is_not_reanalysed_without_explicit_rebuild(self):
        self.source()
        saved = self.save()
        with patch('apps.planning_intelligence.services.simple_planning.run_document_intelligence') as analyse:
            plan = self.action('analyse', saved['revision'])
        analyse.assert_not_called()
        self.assertEqual(plan['revision'], saved['revision'])
        self.assertEqual(plan['tasks'], saved['tasks'])

    def test_empty_retry_discards_proposal_and_calculation_metadata_from_deleted_tasks(self):
        self.source()
        populated = self.save()
        empty = self.save(tasks=[], revision=populated['revision'])
        self.project.refresh_from_db()
        derived = {
            'schedule_proposal': {'mode': 'source_only'},
            'duration_review': {'summary': 'Earlier duration review'},
            'assumptions': ['Earlier schedule assumption'],
            'calculation_run_id': 123,
        }
        self.project.simple_planning_state.update(derived)
        self.project.save(update_fields=['simple_planning_state'])
        with patch('apps.planning_intelligence.services.claude_client.get_claude_config', return_value=None):
            plan = self.action('analyse', empty['revision'])
        self.project.refresh_from_db()
        for key in derived:
            self.assertNotIn(key, self.project.simple_planning_state)
        self.assertNotIn('schedule_proposal', plan)
        self.assertEqual(plan['assumptions'], [])
        self.assertEqual(plan['analysis_result']['code'], 'provider_not_configured')

    def test_failed_ai_analysis_explains_retry_instead_of_successful_schedule(self):
        self.source()
        with patch('apps.planning_intelligence.services.claude_client.get_claude_config', return_value={'enabled': True}), \
                patch('apps.planning_intelligence.services.claude_client.call_claude', side_effect=RuntimeError('Unavailable')):
            plan = self.action('analyse', 0)
        self.assertEqual(plan['analysis_result']['code'], 'ai_analysis_incomplete')
        self.assertEqual(plan['analysis_result']['next_action'], 'retry_analysis')
        self.assertEqual(plan['analysis_result']['requirement_count'], 2)
        self.assertEqual(plan['tasks'], [])

    def test_completed_ai_with_no_deliverables_does_not_turn_requirements_into_activities(self):
        self.source()
        with patch('apps.planning_intelligence.services.claude_client.get_claude_config', return_value={'enabled': True}), \
                patch('apps.planning_intelligence.services.claude_client.call_claude',
                      return_value={'text': '{"facts": []}', 'stop_reason': 'end_turn'}):
            plan = self.action('analyse', 0)
        self.assertEqual(plan['analysis_result']['code'], 'source_activities_not_recovered')
        self.assertEqual(plan['analysis_result']['next_action'], 'review_sources')
        self.assertEqual(plan['analysis_result']['ai_status'], 'complete')
        self.assertEqual(plan['tasks'], [])

    def test_gemini_outage_survives_saved_run_and_successful_retry_creates_quoted_activity(self):
        source = self.source()
        key = 'gemini-synthetic-private-key'
        self.project.ai_settings = {'provider': 'gemini', 'api_key_provider': 'gemini',
            'enabled': True, 'api_key_encrypted': byok_crypto.encrypt_api_key(key),
            'model': project_ai.DEFAULT_GEMINI_MODEL}
        self.project.save(update_fields=['ai_settings'])
        unavailable = Mock(status_code=503)
        unavailable.json.return_value = {'error': {'message': key}}
        success = Mock(status_code=200)
        success.json.return_value = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [
            {'text': json.dumps({'facts': [{'type': 'deliverable', 'value': 'Fire and Gas Mapping Report',
                'source_file_id': source.pk, 'quote': source.extracted_text.splitlines()[0],
                'quote_start': 0, 'discipline': None}]})}]}}]}
        with patch.object(project_ai, 'GEMINI_BYOK_ENABLED', True), \
                patch.object(project_ai.time, 'sleep'), \
                patch.object(project_ai.requests, 'post', side_effect=[unavailable, unavailable, success]) as post:
            failed = self.action('analyse', 0)
            self.assertEqual(failed['analysis_result']['ai_error_code'], 'provider_unavailable')
            self.assertEqual(failed['analysis_result']['ai_http_status'], 503)
            self.assertEqual(failed['analysis_result']['next_action'], 'retry_analysis')
            self.assertIn('temporarily unavailable', failed['analysis_result']['message'])
            self.assertNotIn(key, json.dumps(failed))
            self.assertEqual(self.read()['analysis_result'], failed['analysis_result'])
            plan = self.action('analyse', failed['revision'])
        self.assertEqual(post.call_count, 3)
        self.assertEqual(plan['analysis_result']['status'], 'activities_created')
        self.assertEqual([task['title'] for task in plan['tasks']], ['Fire and Gas Mapping Report'])
        self.assertIsNone(plan['tasks'][0]['duration_days'])
        self.assertIsNone(plan['tasks'][0]['planned_start_date'])
