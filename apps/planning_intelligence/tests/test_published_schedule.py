"""Published planning content is read from its immutable snapshot, not today."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from ..models import ScheduleBaseline, ScheduleVersion, PlanningRiskRecord
from ..services.published_schedule import published_plan_state
from . import test_master_schedule as master_fixture
from .test_ms_project_export import exchange_snapshot


def baseline_fixture():
    source = exchange_snapshot()
    first = source['activities'][0]
    first['metadata']['workflow_stage_code'] = 'IFR'
    profile = {'profile_id': 55, 'profile_version': 2, 'approval': {'actor_id': 3, 'reason': 'Approved policy'},
               'definition': {'workflow': {'id': 77, 'code': 'WFL', 'version': 1,
                    'stages': [{'code': 'IFR', 'sequence': 1, 'name': 'Issue for review'}]}}}
    build = {'id': 'frozen-build', 'profile_id': 55, 'profile_selection_revision': 4,
             'profile_fingerprint': 'frozen-profile-hash', 'profile_snapshot': profile,
             'evidence_snapshot': {'accepted_inputs': {'D1': {'identity': 'Equipment register', 'discipline': 'Mechanical'}}},
             'plan': {'activities': [{'id': first['external_id'], 'kind': 'workflow_activity',
                 'source_entity_id': 'D1', 'workflow_stage_code': 'IFR', 'workflow_stage_name': 'Issue for review',
                 'property_lineage': {'duration': {'type': 'approved_planning_rule', 'value': {'value': 1, 'unit': 'working_days'},
                    'rule_id': 'review-duration', 'source_fact': False},
                     'dependencies': {'type': 'planner_input', 'value': [], 'decision': {'reason': 'Independent package'}}}}],
                 'resources': [{'role': 'Engineer', 'quantity': None}]}}
    frozen = {'version': source['version'], 'activities': source['activities'], 'relationships': source['relationships'],
              'wbs': source['wbs'], 'risk_register': source['risk_register'], 'accepted_inputs': {
                  'project_id': 1, 'project_identity': source['project'], 'schedule_identity': source['schedule'],
                  'project_start': '2026-10-05', 'project_finish': '2026-10-30', 'default_calendar_id': 31,
                  'calendars': source['calendars'], 'planning_build': build,
                  'source_documents': [{'id': 42, 'original_filename': 'Original scope.pdf'}]}}
    return SimpleNamespace(pk=99, name='B0', source_version_id=3, schedule_id=2,
                           approved_at=timezone.now(), snapshot=frozen)


class PublishedProjectionTests(SimpleTestCase):
    def test_frozen_workflow_calendar_dates_risks_and_rule_provenance_need_no_database(self):
        baseline = baseline_fixture()
        original = deepcopy(baseline.snapshot)
        result = published_plan_state(baseline)
        self.assertEqual(result['project']['name'], 'Plant <A> & expansion')
        self.assertEqual(result['project']['planned_finish_date'], '2026-10-30')
        self.assertEqual(result['calendar']['working_times']['0'][0]['from'], '08:30:00')
        self.assertEqual(result['tasks'][0]['planned_start_date'], '2026-10-05')
        self.assertEqual(result['tasks'][0]['total_float_days'], -1.25)
        self.assertEqual(result['tasks'][0]['field_provenance']['duration_days']['type'], 'approved_rule')
        self.assertEqual(result['tasks'][0]['field_provenance']['depends_on']['type'], 'planner')
        self.assertEqual(result['tasks'][0]['field_provenance']['workflow_stage_code']['type'], 'approved_rule')
        self.assertEqual(result['tasks'][0]['field_provenance']['planned_start_date']['status'], 'published_baseline')
        self.assertEqual(result['baseline_risk_register'], original['risk_register'])
        self.assertEqual(result['planning_profile']['snapshot']['profile_version'], 2)
        self.assertEqual(result['project_summary']['duration_days'], 6)  # Oct 7 is the frozen holiday; Oct 11 works.
        self.assertEqual(baseline.snapshot, original)

    def test_old_snapshot_missing_calendar_preserves_leaf_dates_without_default_summary_calendar(self):
        baseline = baseline_fixture()
        baseline.snapshot.pop('accepted_inputs')
        result = published_plan_state(baseline)
        self.assertTrue(result['calculation_available'])
        self.assertEqual(result['tasks'][0]['planned_start_date'], '2026-10-05')
        self.assertIsNone(result['project_summary']['duration_days'])
        self.assertIsNone(result['project']['name'])
        self.assertNotIn('working_weekdays', result['calendar'])
        self.assertIn('legacy_baseline_manifest_incomplete', {row['code'] for row in result['warnings']})

    def test_frozen_approved_graph_facts_supply_badges_without_live_node_lookup(self):
        baseline = baseline_fixture()
        activity = baseline.snapshot['activities'][1]
        activity['metadata'].update(evidence_entity_id='source:E2', property_provenance={'duration': 'frozen-duration'})
        baseline.snapshot['accepted_inputs']['evidence_graph'] = {'facts': [{
            'id': 'frozen-duration', 'entity_id': 'source:E2', 'property': 'duration',
            'value': {'value': 1, 'unit': 'working_days'}, 'status': 'accepted', 'provenance_type': 'document_evidence',
            'sources': [{'verbatim': 'Planned duration 1 working day', 'locator': {'page': 4}}],
            'validation': {'quote_verified': True}}]}
        result = published_plan_state(baseline)
        provenance = result['tasks'][1]['field_provenance']['duration_days']
        self.assertEqual(provenance['type'], 'document')
        self.assertEqual(provenance['status'], 'accepted_at_publication')
        baseline.snapshot['activities'][1]['duration_days'] = '2'
        result = published_plan_state(baseline)
        self.assertEqual(result['tasks'][1]['field_provenance']['duration_days']['type'], 'unknown')


class PublishedMasterScheduleTests(TestCase):
    setUp = master_fixture.MasterScheduleTests.setUp
    refresh = master_fixture.MasterScheduleTests.refresh
    fact = master_fixture.MasterScheduleTests.fact
    decide = master_fixture.MasterScheduleTests.decide
    approve_inputs = master_fixture.MasterScheduleTests.approve_inputs
    endpoint = master_fixture.MasterScheduleTests.endpoint
    read = master_fixture.MasterScheduleTests.read
    activate = master_fixture.MasterScheduleTests.activate
    action = master_fixture.MasterScheduleTests.action

    def publish(self):
        state = self.action('calculate', self.activate())
        state = self.action('validate', state)
        state = self.action('submit', state, approver_id=self.user.pk)
        return self.action('approve-publish', state, name='Frozen schedule')

    def test_published_canvas_survives_current_input_changes_and_keeps_live_risks_separate(self):
        before = self.publish()
        version = ScheduleVersion.objects.get(pk=before['version_id'])
        baseline = ScheduleBaseline.objects.get(pk=before['baseline']['id'])
        frozen = deepcopy(baseline.snapshot)
        calendar = version.schedule.default_calendar
        calendar.working_weekdays = [6]
        calendar.hours_per_day = 3
        calendar.save(update_fields=['working_weekdays', 'hours_per_day'])
        self.project.name = 'Renamed today'
        self.project.save(update_fields=['name'])
        PlanningRiskRecord.objects.create(version=version, source_key='planner:new', title='Current risk',
                                           description='A management update after publication')
        with patch('apps.planning_intelligence.services.master_schedule.accepted_input_validation', side_effect=AssertionError('Must not revalidate a published baseline')), \
             patch('apps.planning_intelligence.services.planning_builds.build_provenance', side_effect=AssertionError('Must not use live rule provenance')), \
             CaptureQueriesContext(connection) as queries:
            after = self.read()
        self.assertFalse([row for row in queries if row['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))])
        for key in ('tasks', 'calendar', 'project', 'project_summary', 'wbs_nodes', 'source_documents', 'provenance_summary'):
            self.assertEqual(after[key], before[key], key)
        self.assertTrue(after['calculation_available'])
        self.assertTrue(after['permissions']['can_reopen'])
        self.assertFalse(after['legacy_read_only'])
        self.assertFalse(after['viewing_history'])
        self.assertEqual(after['source_verification']['status'], 'verified')
        self.assertFalse(after['permissions']['can_calculate'])
        self.assertEqual(after['risk_register'][0]['title'], 'Current risk')
        self.assertEqual(after['baseline_risk_register'], [])
        baseline.refresh_from_db()
        self.assertEqual(baseline.snapshot, frozen)

    def test_history_uses_same_frozen_canvas_after_reopening(self):
        published = self.publish()
        reopened = self.action('reopen', published)
        self.assertNotEqual(reopened['version_id'], published['version_id'])
        history = self.read(version_id=published['version_id'])
        self.assertTrue(history['viewing_history'])
        self.assertFalse(history['canonical_version'])
        self.assertFalse(history['permissions']['can_reopen'])
        self.assertEqual(history['tasks'], published['tasks'])
        self.assertEqual(history['project_summary'], published['project_summary'])
