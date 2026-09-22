"""Build application, calendar precision and risk decisions at API boundaries."""
from copy import deepcopy
from django.test import TestCase, SimpleTestCase
from django.core.exceptions import ValidationError
from ..models import PlanningRiskRecord, ScheduleVersion, ScheduleBaseline
from ..services.calendar_intervals import calendar_intervals_error
from ..services.planning_boundaries import calculation_inputs_current, accepted_input_validation
from ..services.planning_registers import seed_build_risks
from . import test_planning_builds as fixture


class CalendarIntervalsTests(SimpleTestCase):
    def test_missing_shifts_are_not_assumed_and_explicit_shifts_preserve_hours(self):
        value = {'working_weekdays': [0], 'hours_per_day': 8, 'exceptions': []}
        self.assertIsNone(calendar_intervals_error(value))
        self.assertNotIn('working_times', value)
        value['working_times'] = {'0': [{'from': '07:30:00', 'to': '11:30:00'}, {'from': '12:30:00', 'to': '16:30:00'}]}
        self.assertIsNone(calendar_intervals_error(value))
        value['working_times']['0'][1]['from'] = '11:00:00'
        self.assertIn('nonoverlapping', calendar_intervals_error(value))

    def test_unknown_days_nonworking_exceptions_and_wrong_totals_are_rejected(self):
        value = {'working_weekdays': [0], 'hours_per_day': 8, 'working_times': {'1': []}}
        self.assertIsNotNone(calendar_intervals_error(value))
        value['working_times'] = {'0': [{'from': '08:00:00', 'to': '17:00:00'}]}
        self.assertIn('total', calendar_intervals_error(value))
        value['working_times'] = {}
        value['exceptions'] = [{'is_working': False, 'working_times': [{'from': '08:00:00', 'to': '16:00:00'}]}]
        self.assertIn('Nonworking', calendar_intervals_error(value))

    def test_nonworking_exception_cannot_hide_invalid_hours_behind_empty_intervals(self):
        from ..services.evidence_schema import validate_value
        value = {'working_weekdays': [0], 'hours_per_day': 8, 'timezone': 'Asia/Dubai',
                 'exceptions': [{'date': '2026-11-03', 'is_working': False, 'working_hours': 'not-a-number'}]}
        self.assertIsNotNone(validate_value('calendar', value))
        for hours in ('NaN', 'Infinity', -1, 8):
            value['exceptions'][0]['working_hours'] = hours
            self.assertIsNotNone(validate_value('calendar', value))
        value['exceptions'][0]['working_hours'] = 0
        self.assertIsNone(validate_value('calendar', value))


class PlanningReleaseTwoTests(TestCase):
    setUp = fixture.PlanningBuildTests.setUp
    decision = fixture.PlanningBuildTests.decision
    preview = fixture.PlanningBuildTests.preview

    def endpoint(self, path):
        return f'/api/v1/planning-intelligence/projects/{self.project.pk}/{path}/'

    def apply(self, build=None, revision=0):
        build = build or self.preview()
        response = self.client.post(self.endpoint(f'planning-builds/{build.pk}/apply'), {
            'fingerprint': build.fingerprint, 'master_revision': revision, 'reason': 'Use the reviewed build.'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return ScheduleVersion.objects.get(pk=response.data['version_id'])

    def state(self):
        response = self.client.get(self.endpoint('simple-plan'))
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def action(self, operation, **extra):
        state = self.state()
        response = self.client.post(self.endpoint(f'simple-plan/{operation}'), {'revision': state['revision'], **extra}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_apply_calculate_validate_publish_preserves_rule_provenance_and_baseline(self):
        original = deepcopy(self.project.simple_planning_state)
        version = self.apply()
        state = self.state()
        self.assertTrue(state['canonical_version'])
        self.assertEqual(state['version_id'], version.pk)
        self.assertTrue(state['permissions']['can_calculate'], state.get('blockers'))
        self.assertEqual({row['field_provenance']['duration_days']['type'] for row in state['tasks']}, {'approved_rule'})
        self.assertEqual(len(state['resource_requirements']), 10)
        self.assertEqual(len(state['deliverables']), 2)
        self.assertEqual([sum(row.get('parent_deliverable_id') == item['id'] for row in state['tasks'])
                          for item in state['deliverables']], [5, 5])
        state = self.action('calculate')
        self.assertTrue(state['calculation_available'])
        self.assertTrue(all(item['summary']['complete'] for item in state['deliverables']))
        state = self.action('validate')
        self.assertTrue(state['permissions']['can_submit'], state['blockers'])
        self.action('submit', approver_id=self.owner.pk)
        self.action('approve-publish')
        baseline = ScheduleBaseline.objects.get(source_version=version)
        self.assertEqual(baseline.snapshot['accepted_inputs']['planning_build']['id'], str(version.planning_build_id))
        self.assertIn('risk_register', baseline.snapshot)
        snapshot = deepcopy(baseline.snapshot)
        state = self.action('reopen')
        clone = ScheduleVersion.objects.get(pk=state['version_id'])
        self.assertEqual(clone.planning_build_id, version.planning_build_id)
        baseline.refresh_from_db()
        self.assertEqual(baseline.snapshot, snapshot)
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, original)

    def test_stale_selection_and_foreign_project_cannot_apply(self):
        build = self.preview()
        url = self.endpoint(f'planning-builds/{build.pk}/apply')
        payload = {'fingerprint': build.fingerprint, 'master_revision': 99, 'reason': 'Reviewed.'}
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertFalse(ScheduleVersion.objects.exists())
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.post(url, {**payload, 'master_revision': 0}, format='json').status_code, 404)
        self.assertFalse(ScheduleVersion.objects.exists())

    def test_advanced_revision_preserves_approved_build_bindings_and_logic(self):
        from ..services.planning_builds import build_input_validation
        version = self.apply()
        url = f'/api/v1/planning-intelligence/schedules/{version.schedule_id}/create-version/'
        response = self.client.post(url, {'change_summary': 'Revised planning review.'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        clone = ScheduleVersion.objects.get(pk=response.data['id'])
        self.assertEqual(clone.planning_build_id, version.planning_build_id)
        self.assertEqual(clone.evidence_graph_id, version.evidence_graph_id)
        self.assertFalse([issue for issue in build_input_validation(clone) if issue['severity'] == 'error'])

    def test_removing_evidence_binding_cannot_downgrade_an_applied_build_to_legacy(self):
        version = self.apply()
        ScheduleVersion.objects.filter(pk=version.pk).update(evidence_graph=None, evidence_graph_revision=None)
        version.refresh_from_db()
        readiness = accepted_input_validation(version)
        self.assertFalse(readiness['ready_for_calculation'])
        self.assertTrue(any(item['code'] == 'planning_build_evidence_binding' for item in readiness['issues']))

    def test_risk_updates_do_not_change_cpm_inputs_and_source_remains_immutable(self):
        version = self.apply()
        self.action('calculate')
        version.refresh_from_db()
        response = self.client.post(self.endpoint('risk-register'), {'version_id': version.pk,
            'title': 'Vendor inspection access', 'description': 'Site access requires confirmation.', 'reason': 'Planning review.'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        item = response.data['item']
        self.assertIsNone(item['priority'])
        self.assertTrue(calculation_inputs_current(version))
        update = {'version_id': version.pk, 'item_id': item['id'], 'revision': item['revision'],
                  'owner_id': self.engineer.pk, 'priority': 'high', 'response': 'Confirm access with the site.', 'reason': 'Owner accepted the action.'}
        response = self.client.patch(self.endpoint('risk-register'), update, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['item']['revision'], 2)
        self.assertEqual(self.client.patch(self.endpoint('risk-register'), update, format='json').status_code, 400)
        record = PlanningRiskRecord.objects.get(pk=item['id'])
        record.description = 'Changed source claim'
        with self.assertRaises(ValidationError):
            record.save()
        self.assertTrue(calculation_inputs_current(version))

    def test_risk_owner_source_and_closure_are_validated(self):
        version = self.apply()
        url = self.endpoint('risk-register')
        payload = {'version_id': version.pk, 'title': 'Access risk', 'description': 'Restricted access.', 'reason': 'Reviewed.', 'owner_id': self.outsider.pk}
        self.assertEqual(self.client.post(url, payload, format='json').status_code, 400)
        payload['owner_id'] = self.engineer.pk
        item = self.client.post(url, payload, format='json').data['item']
        update = {'version_id': version.pk, 'item_id': item['id'], 'revision': 1, 'status': 'closed', 'reason': 'Reviewed.'}
        self.assertEqual(self.client.patch(url, update, format='json').status_code, 400)
        self.assertEqual(self.client.patch(url, {**update, 'title': 'Changed source'}, format='json').status_code, 400)
        response = self.client.patch(url, {**update, 'resolution': 'Site permission was received.'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['item']['status'], 'closed')

    def test_source_risk_titles_and_planner_corrections_remain_distinguishable(self):
        version = self.apply()
        # Exercise projection of already accepted risk values. No source node
        # or immutable build is edited by this isolated seeding test.
        build = version.planning_build
        build.plan = {**build.plan, 'risks': [
            {'id': 'source-risk', 'value': {'text': 'Vendor access may be delayed.'}, 'lineage': {'type': 'document_evidence'}},
            {'id': 'planner-risk', 'value': {'text': 'Revised access mitigation required.'}, 'lineage': {'type': 'approved_planning_input'}},
        ]}
        seed_build_risks(version)
        self.assertEqual(version.planning_risks.get(source_key='source-risk').title, 'Vendor access may be delayed.')
        self.assertEqual(version.planning_risks.get(source_key='source-risk').provenance['type'], 'document')
        corrected = version.planning_risks.get(source_key='planner-risk')
        self.assertEqual(corrected.provenance['type'], 'planner')
        self.assertIsNone(corrected.priority)
