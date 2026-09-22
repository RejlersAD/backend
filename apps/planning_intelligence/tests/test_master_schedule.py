"""The shared canvas preserves evidence, selection concurrency and approvals."""
from copy import deepcopy
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.core.project_models import ProjectMember
from apps.users.models import User
from ..models import PlanningProject, ScheduleVersion, ScheduleBaseline
from ..services.evidence_graph import materialize_accepted_plan
from ..services.planning_provenance import annotate_plan_provenance
from . import test_evidence_graph as graph_fixture
from .test_business_approval_gates import grant_test_approval


class MasterScheduleTests(TestCase):
    setUp = graph_fixture.EvidenceGraphTests.setUp
    refresh = graph_fixture.EvidenceGraphTests.refresh
    fact = graph_fixture.EvidenceGraphTests.fact
    decide = graph_fixture.EvidenceGraphTests.decide
    approve_inputs = graph_fixture.EvidenceGraphTests.approve_inputs

    def endpoint(self, operation=''):
        return f'/api/v1/planning-intelligence/projects/{self.project.pk}/simple-plan/' + (operation + '/' if operation else '')

    def read(self, **params):
        response = self.client.get(self.endpoint(), params)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def activate(self):
        self.approve_inputs()
        url = f'/api/v1/planning-intelligence/projects/{self.project.pk}/evidence-review/materialize/'
        response = self.client.post(url, {'revision': self.graph.revision, 'activate': True, 'master_revision': 0}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return self.read()

    def action(self, operation, state, **payload):
        response = self.client.post(self.endpoint(operation), {'revision': state['revision'], **payload}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_reads_never_activate_or_replace_working_draft(self):
        with CaptureQueriesContext(connection) as queries:
            state = self.read()
        self.assertFalse([query for query in queries if query['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))])
        self.assertFalse(state['canonical_version'])
        self.assertIsNone(state['master_version_id'])
        self.assertFalse(state['planning_profile']['valid'])
        self.assertTrue(state['permissions']['can_generate_plan'])

    def test_accepted_projection_reloads_on_same_canvas_and_can_restore_working_draft(self):
        before = deepcopy(self.project.simple_planning_state)
        state = self.activate()
        self.assertTrue(state['canonical_version'])
        self.assertFalse(state['viewing_history'])
        self.assertFalse(state['permissions']['can_edit'])
        self.assertTrue(state['permissions']['can_generate_plan'])
        self.assertTrue(state['permissions']['can_calculate'])
        self.assertEqual(len(state['tasks']), 2)
        self.assertEqual({row['field_provenance']['duration_days']['type'] for row in state['tasks']}, {'document'})
        self.assertTrue(all(row['planned_start_date'] is None for row in state['tasks']))
        self.assertLess(state['revision'], 2 ** 53)
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, before)
        response = self.client.post(self.endpoint('select-version'), {
            'revision': state['master_revision'], 'version_id': None}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data['canonical_version'])
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, before)
        self.assertEqual(ScheduleVersion.objects.filter(schedule__project=self.project).count(), 1)

    def test_calculate_validate_submit_publish_share_one_version_and_revision_preserves_baseline(self):
        state = self.activate()
        version_id = state['version_id']
        state = self.action('calculate', state)
        self.assertTrue(state['calculation_available'])
        self.assertEqual(state['tasks'][0]['field_provenance']['planned_start_date']['type'], 'calculated')
        state = self.action('validate', state)
        self.assertTrue(state['permissions']['can_submit'], state['blockers'])
        state = self.action('submit', state, approver_id=self.user.pk)
        self.assertEqual(state['state'], 'submitted')
        self.assertEqual(state['version_id'], version_id)
        self.assertTrue(state['permissions']['can_approve_publish'], state['blockers'])
        state = self.action('approve-publish', state, name='Accepted source baseline')
        self.assertEqual(state['state'], 'baselined')
        self.assertTrue(state['permissions']['can_generate_plan'])
        baseline = ScheduleBaseline.objects.get(pk=state['baseline']['id'])
        snapshot = deepcopy(baseline.snapshot)
        state = self.action('reopen', state)
        self.assertNotEqual(state['version_id'], version_id)
        version = ScheduleVersion.objects.get(pk=state['version_id'])
        self.assertEqual(version.parent_version_id, version_id)
        self.assertEqual(version.evidence_graph_id, self.graph.pk)
        self.assertFalse(state['permissions']['can_edit'])
        self.assertTrue(state['permissions']['can_generate_plan'])
        self.assertTrue(state['permissions']['can_calculate'])
        baseline.refresh_from_db()
        self.assertEqual(baseline.snapshot, snapshot)
        history = self.read(version_id=version_id)
        self.assertTrue(history['viewing_history'])
        self.assertFalse(history['canonical_version'])
        self.assertEqual(history['state'], 'baselined')
        self.assertFalse(history['permissions']['can_generate_plan'])

    def test_plan_generation_permission_respects_project_role_and_history(self):
        state = self.activate()
        version_id = state['version_id']
        reviewer = User.objects.create_user(username='build-reader', email='build-reader@example.test')
        grant_test_approval((reviewer,))
        ProjectMember.objects.create(project=self.project.enterprise_project, user=reviewer, role='reviewer')
        self.client.force_authenticate(reviewer)
        self.assertFalse(self.read()['permissions']['can_generate_plan'])
        self.client.force_authenticate(self.user)
        with patch('apps.planning_intelligence.services.master_schedule.module_action_allowed', return_value=False):
            self.assertFalse(self.read()['permissions']['can_generate_plan'])
        response = self.client.post(self.endpoint('select-version'), {
            'revision': state['master_revision'], 'version_id': None}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['permissions']['can_generate_plan'])
        self.assertFalse(self.read(version_id=version_id)['permissions']['can_generate_plan'])

    def test_activation_conflict_rolls_back_materialization(self):
        self.approve_inputs()
        PlanningProject.objects.filter(pk=self.project.pk).update(master_schedule_revision=2)
        response = self.client.post(f'/api/v1/planning-intelligence/projects/{self.project.pk}/evidence-review/materialize/', {
            'revision': self.graph.revision, 'activate': True, 'master_revision': 0}, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertFalse(ScheduleVersion.objects.filter(schedule__project=self.project).exists())

    def test_alternate_route_change_invalidates_command_revision(self):
        state = self.activate()
        version = ScheduleVersion.objects.get(pk=state['version_id'])
        activity = version.activities.first()
        activity.duration_days = 19
        activity.save(update_fields=['duration_days'])
        response = self.client.post(self.endpoint('calculate'), {'revision': state['revision']}, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'master_schedule_revision_conflict')

    def test_canonical_edits_require_evidence_and_cross_project_selection_is_not_visible(self):
        state = self.activate()
        for operation in ('analyse', 'apply-schedule', 'propose-schedule'):
            response = self.client.post(self.endpoint(operation), {'revision': state['revision']}, format='json')
            self.assertEqual(response.status_code, 409, response.data)
        response = self.client.put(self.endpoint(), {'revision': state['revision'], 'tasks': []}, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        other = PlanningProject.objects.create(name='Unrelated project')
        version = ScheduleVersion.objects.get(pk=state['version_id'])
        version.schedule.project = other
        version.schedule.save(update_fields=['project'])
        response = self.client.post(self.endpoint('select-version'), {'revision': state['master_revision'], 'version_id': version.pk}, format='json')
        self.assertEqual(response.status_code, 404, response.data)

    def test_provenance_requires_exact_entity_property_and_value(self):
        state = self.activate()
        first, second = state['tasks']
        first['property_provenance']['duration'] = second['property_provenance']['duration']
        first['duration_source'] = 'unknown'
        annotate_plan_provenance(self.project, state)
        self.assertEqual(first['field_provenance']['duration_days']['type'], 'unknown')
        second['duration_days'] = 99
        annotate_plan_provenance(self.project, state)
        self.assertEqual(second['field_provenance']['duration_days']['type'], 'unknown')
        second['duration_source'] = 'source_document'
        second['duration_evidence'] = {'source_references': [{'file_id': self.file.pk, 'excerpt': 'not a supported value'}]}
        annotate_plan_provenance(self.project, state)
        self.assertEqual(second['field_provenance']['duration_days']['type'], 'unknown')

    def test_materialization_without_activation_remains_backward_compatible(self):
        self.approve_inputs()
        result = materialize_accepted_plan(self.project, self.user, revision=self.graph.revision)
        self.project.refresh_from_db()
        self.assertIsNone(self.project.master_schedule_version_id)
        self.assertTrue(result['created'])

    def test_stale_calendar_hides_leaf_and_summary_calculations(self):
        state = self.action('calculate', self.activate())
        self.assertTrue(state['project_summary']['complete'])
        version = ScheduleVersion.objects.get(pk=state['version_id'])
        calendar = version.schedule.default_calendar
        calendar.hours_per_day = 7
        calendar.save(update_fields=['hours_per_day'])
        state = self.read()
        self.assertFalse(state['calculation_available'])
        self.assertFalse(state['project_summary']['complete'])
        self.assertIsNone(state['project_summary']['planned_start_date'])
        self.assertTrue(all(row['summary']['planned_finish_date'] is None for row in state['wbs_nodes']))
        self.assertTrue(all(row['early_start'] is None and row['total_float_days'] is None for row in state['tasks']))

    def test_legacy_command_refetch_blocks_stale_object_after_selection(self):
        from ..services.simple_planning import save_plan, SimplePlanningError
        self.activate()
        # API activation used another project instance; simulate a concurrent
        # legacy request whose project was loaded before the selection committed.
        self.assertIsNone(self.project.master_schedule_version_id)
        with self.assertRaises(SimplePlanningError) as caught:
            save_plan(self.project, self.user, {'revision': 0, 'tasks': [], 'disciplines': []})
        self.assertEqual(caught.exception.payload['code'], 'master_schedule_accepted_inputs_read_only')
