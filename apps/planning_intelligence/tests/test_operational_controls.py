"""Operational publication is approved, reproducible and isolated from plan editing."""
from copy import deepcopy
from datetime import date
from unittest.mock import patch

from django.db import DatabaseError, connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.project_models import Project, ProjectMember
from apps.project_control.models import CostLedgerEntry, ReportingPeriod
from apps.users.models import User
from ..models import OperationalControlReport, OperationalEarningPolicy, PlanningAuditEvent, ScheduleBaseline
from . import test_scheduling_engine as fixture


class OperationalControlTests(fixture.ScheduleFixture):
    def setUp(self):
        super().setUp()
        self.enterprise = Project.objects.create(code='OPS-1', name='Operational test', owner=self.owner)
        self.project.enterprise_project = self.enterprise
        self.project.save(update_fields=['enterprise_project'])
        self.manager = User.objects.create_user(username='ops-reviewer', email='ops-review@example.test')
        ProjectMember.objects.create(project=self.enterprise, user=self.manager, role='project_manager')
        fixture.grant_planning_test_actions((self.owner, self.manager, self.outsider), ('read', 'create', 'update', 'approve'))
        self.a = self.activity('A', 4)
        self.b = self.activity('B', 2)
        self.snapshot = {'version': {'id': self.version.pk}, 'wbs': [], 'activities': [
            {'id': self.a.pk, 'external_id': 'A', 'name': 'Design', 'calendar': self.calendar.pk,
             'activity_type': 'task', 'duration_days': '4', 'planned_start': '2026-08-24',
             'planned_finish': '2026-08-27', 'constraint_type': 'none'},
            {'id': self.b.pk, 'external_id': 'B', 'name': 'Review', 'calendar': self.calendar.pk,
             'activity_type': 'task', 'duration_days': '2', 'planned_start': '2026-08-28',
             'planned_finish': '2026-08-31', 'constraint_type': 'none'}],
            'relationships': [{'predecessor': self.a.pk, 'successor': self.b.pk, 'relationship_type': 'FS', 'lag_days': '0'}],
            'accepted_inputs': {'project_start': '2026-08-24', 'project_finish': '2026-08-31',
                'default_calendar_id': self.calendar.pk, 'calendars': [{
                    'id': self.calendar.pk, 'working_weekdays': [0, 1, 2, 3, 4], 'hours_per_day': '8', 'exceptions': []}]}}
        self.baseline = ScheduleBaseline.objects.create(schedule=self.schedule, source_version=self.version,
            name='Approved plan', snapshot=self.snapshot, approved_by=self.owner, approved_at=timezone.now())
        self.period = ReportingPeriod.objects.create(project=self.enterprise, sequence=1, name='Week 35',
            start_date=date(2026, 8, 24), end_date=date(2026, 8, 30), data_date=date(2026, 8, 26))
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.url = f'/api/v1/planning-intelligence/projects/{self.project.pk}/operational-controls/'

    def command(self, action, expected=200, **data):
        response = self.client.post(self.url, {'action': action, **data}, format='json')
        self.assertEqual(response.status_code, expected, response.data)
        return response.data

    def policy(self):
        state = self.command('create_policy', baseline_id=self.baseline.pk, name='Reported physical progress',
            definition={'currency': None, 'activities': [
                {'activity_id': self.a.pk, 'method': 'manual_percent', 'weight': '70'},
                {'activity_id': self.b.pk, 'method': 'zero_hundred', 'weight': '30'}]})
        policy = state['policies'][0]
        self.client.force_authenticate(self.manager)
        self.command('approve_policy', policy_id=policy['id'], revision=policy['revision'], reason='Approved measurement basis')
        self.client.force_authenticate(self.owner)
        return policy['id']

    def report(self):
        policy = self.policy()
        return self.command('create_report', baseline_id=self.baseline.pk, policy_id=policy,
            reporting_period_id=self.period.pk)['report']

    def save(self, report):
        return self.command('save_report', report_id=report['id'], revision=report['revision'], observations=[
            {'activity_id': self.a.pk, 'actual_start': '2026-08-24', 'physical_progress_pct': '25',
             'remaining_duration_days': '3', 'evidence': 'Signed weekly progress report p2'},
            {'activity_id': self.b.pk, 'physical_progress_pct': '0', 'remaining_duration_days': '2',
             'evidence': 'Weekly report: review not started'}])['report']

    def submitted(self):
        report = self.save(self.report())
        return self.command('submit_report', report_id=report['id'], revision=report['revision'],
            source_fingerprint=report['source_fingerprint'])['report']

    def published(self):
        report = self.submitted()
        self.client.force_authenticate(self.manager)
        return self.command('publish_report', report_id=report['id'], revision=report['revision'],
            source_fingerprint=report['source_fingerprint'], reason='Weekly evidence reviewed')['report']

    def source_cost(self):
        return CostLedgerEntry.objects.create(project=self.enterprise, entry_key='ops-test-cost', entry_type='actual',
            amount='999.17', currency='AED', entry_date=date(2026, 8, 25), status='posted', source_reference='Sensitive cost')

    def test_policy_requires_independent_approved_method_and_retains_unknown_budgets(self):
        state = self.command('create_policy', baseline_id=self.baseline.pk, name='Partial evidence',
            definition={'activities': [{'activity_id': self.a.pk, 'method': 'manual_percent'}]})
        policy = state['policies'][0]
        self.command('approve_policy', expected=403, policy_id=policy['id'], revision=policy['revision'], reason='Own approval')
        self.client.force_authenticate(self.manager)
        approved = self.command('approve_policy', policy_id=policy['id'], revision=policy['revision'], reason='Accepted partial coverage')['policies'][0]
        self.assertEqual(approved['status'], 'approved')
        self.assertNotIn('budget', approved['definition']['activities'][0])

    def test_publication_requires_independent_authority_and_freezes_history(self):
        report = self.submitted()
        self.command('publish_report', expected=403, report_id=report['id'], revision=report['revision'],
            source_fingerprint=report['source_fingerprint'], reason='Self review')
        self.client.force_authenticate(self.manager)
        state = self.command('publish_report', report_id=report['id'], revision=report['revision'],
            source_fingerprint=report['source_fingerprint'], reason='Reviewed')
        published = deepcopy(state['report'])
        self.source_cost()
        self.a.name = 'Changed after reporting'
        self.a.save(update_fields=['name'])
        response = self.client.get(self.url, {'report_id': report['id']})
        self.assertEqual(response.data['report'], published)
        self.assertEqual(response.data['curves'], state['curves'])
        self.baseline.refresh_from_db()
        self.assertEqual(self.baseline.snapshot, self.snapshot)
        self.period.refresh_from_db()
        self.assertEqual(self.period.status, 'open')  # Operational publication never seals finance.

    def test_sources_change_requires_reapproval_not_refresh_and_publish(self):
        report = self.submitted()
        self.source_cost()
        self.client.force_authenticate(self.manager)
        error = self.command('publish_report', expected=409, report_id=report['id'], revision=report['revision'],
            source_fingerprint=report['source_fingerprint'], reason='Reviewed')
        self.assertEqual(error['code'], 'operational_sources_changed')
        fresh = self.client.get(self.url, {'report_id': report['id']}).data['report']
        error = self.command('publish_report', expected=409, report_id=report['id'], revision=report['revision'],
            source_fingerprint=fresh['source_fingerprint'], reason='Reviewed')
        self.assertEqual(error['code'], 'operational_submission_stale')

    def test_changed_calculation_rules_require_review_and_published_progress_remains_frozen(self):
        report = self.submitted()
        self.assertEqual(report['preview']['metrics']['remaining_progress_pct'], '82.50')
        self.client.force_authenticate(self.manager)
        with patch('apps.planning_intelligence.services.operational_controls.RULE_VERSION', 'operational-controls/future'):
            self.command('publish_report', expected=409, report_id=report['id'], revision=report['revision'],
                source_fingerprint=report['source_fingerprint'], reason='Reviewed older calculation')
        published = self.command('publish_report', report_id=report['id'], revision=report['revision'],
            source_fingerprint=report['source_fingerprint'], reason='Reviewed current calculation')['report']
        with patch('apps.planning_intelligence.services.operational_controls.RULE_VERSION', 'operational-controls/future'):
            retained = self.client.get(self.url, {'report_id': report['id']}).data['report']
        self.assertEqual(retained, published)
        self.assertEqual(retained['rule_version'], 'operational-controls/1.1')

    def test_correction_preserves_original_and_curves_use_only_published_observations(self):
        report = self.published()
        stored = deepcopy(OperationalControlReport.objects.get(pk=report['id']).publication)
        self.client.force_authenticate(self.owner)
        state = self.command('correction_report', report_id=report['id'], reason='Late approved site evidence')
        correction = state['report']
        self.assertNotEqual(correction['id'], report['id'])
        self.assertEqual(correction['supersedes_id'], report['id'])
        self.assertEqual(state['curves'][0]['report_id'], report['id'])
        self.assertEqual(OperationalControlReport.objects.get(pk=report['id']).publication, stored)
        self.command('save_report', expected=409, report_id=report['id'], revision=report['revision'], observations=[])

    def test_revision_dates_evidence_and_foreign_activity_are_validated(self):
        report = self.report()
        self.command('save_report', expected=409, report_id=report['id'], revision=999, observations=[])
        for observation in [
            {'activity_id': self.a.pk, 'actual_start': '2026-08-27', 'evidence': 'Future'},
            {'activity_id': self.a.pk, 'remaining_duration_days': '2'},
            {'activity_id': 9999999, 'evidence': 'Foreign'},
            {'activity_id': self.a.pk, 'actual_finish': '2026-08-25', 'evidence': 'Missing start'},
            {'activity_id': self.a.pk, 'remaining_duration_days': 'NaN', 'evidence': 'Invalid'},
        ]:
            self.command('save_report', expected=400, report_id=report['id'], revision=report['revision'], observations=[observation])

    def test_period_date_change_invalidates_submission(self):
        report = self.submitted()
        self.period.data_date = date(2026, 8, 27)
        self.period.save(update_fields=['data_date'])
        self.client.force_authenticate(self.manager)
        error = self.command('publish_report', expected=409, report_id=report['id'], revision=report['revision'],
            source_fingerprint=report['source_fingerprint'], reason='Reviewed')
        self.assertEqual(error['code'], 'operational_period_changed')

    def test_read_is_read_only_and_does_not_expose_commercial_values(self):
        report = self.report()
        self.source_cost()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url, {'report_id': report['id']})
        self.assertEqual(response.status_code, 200)
        self.assertFalse([row for row in queries if row['sql'].lstrip().upper().startswith(('UPDATE', 'INSERT', 'DELETE'))])
        self.assertFalse(response.data['permissions']['can_view_costs'])
        self.assertNotIn('999.17', str(response.data))
        self.assertNotIn('Sensitive cost', str(response.data))
        self.assertNotIn('manifest', response.data['report']['source_actuals'])
        self.assertIsNone(response.data['report']['preview']['metrics']['actual_cost'])

    def test_foreign_project_is_hidden_and_existing_period_reused(self):
        report = self.report()
        self.command('create_report', expected=400, baseline_id=self.baseline.pk, policy_id=report['policy_id'], reporting_period_id=self.period.pk)
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url, {'action': 'save_report', 'report_id': report['id'], 'revision': 1, 'observations': []}, format='json').status_code, 404)

    def test_unknown_methods_and_cross_baseline_rules_are_rejected(self):
        for rule in [{'activity_id': self.a.pk, 'method': 'guess'},
                     {'activity_id': 999999, 'method': 'zero_hundred'},
                     {'activity_id': self.a.pk, 'method': 'quantity', 'planned_quantity': 0, 'quantity_unit': 'm'}]:
            self.command('create_policy', expected=400, baseline_id=self.baseline.pk, name='Invalid', definition={'activities': [rule]})

    def test_prior_editor_cannot_publish_even_after_someone_else_saves_and_submits(self):
        report = self.report()
        self.client.force_authenticate(self.manager)
        report = self.save(report)
        self.client.force_authenticate(self.owner)
        report = self.save(report)
        report = self.command('submit_report', report_id=report['id'], revision=report['revision'],
            source_fingerprint=report['source_fingerprint'])['report']
        self.client.force_authenticate(self.manager)
        self.command('publish_report', expected=403, report_id=report['id'], revision=report['revision'],
            source_fingerprint=report['source_fingerprint'], reason='Cannot approve own contribution')

    def test_empty_table_rows_cannot_be_submitted_as_a_measured_update(self):
        report = self.report()
        report = self.command('save_report', report_id=report['id'], revision=report['revision'],
            observations=[{'activity_id': self.a.pk, 'physical_progress_pct': None}])['report']
        self.command('submit_report', expected=400, report_id=report['id'], revision=report['revision'],
            source_fingerprint=report['source_fingerprint'])

    def test_commercial_policy_does_not_leak_through_general_planning_audit(self):
        self.owner.is_staff = True
        self.owner.save(update_fields=['is_staff'])
        self.command('create_policy', baseline_id=self.baseline.pk, name='Approved control basis',
            definition={'currency': 'AED', 'activities': [{'activity_id': self.a.pk,
                'method': 'manual_percent', 'budget': '89123.125', 'pv_method': 'working_day_linear'}]})
        event = PlanningAuditEvent.objects.get(project=self.project, action='operational.create_policy')
        self.assertNotIn('89123.125', str(event.after))
        self.assertIn('definition_fingerprint', event.after)
        self.owner.is_staff = False
        self.owner.save(update_fields=['is_staff'])
        self.client.force_authenticate(self.owner)
        self.assertNotIn('89123.125', str(self.client.get(self.url).data))

    def test_published_reports_and_approved_policies_protected_in_postgresql(self):
        report = self.published()
        if connection.vendor != 'postgresql':
            self.skipTest('Database trigger certification requires PostgreSQL.')
        for queryset, change in [
            (OperationalControlReport.objects.filter(pk=report['id']), {'observations': []}),
            (OperationalEarningPolicy.objects.filter(pk=report['policy_id']), {'definition': {}}),
        ]:
            with self.assertRaises(DatabaseError), transaction.atomic():
                queryset.update(**change)
