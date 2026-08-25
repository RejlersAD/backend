import datetime as dt
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.project_models import Project, ProjectMember
from apps.users.models import User

from ..models import (
    ActivityAssignment, ActivityRelationship, CalendarException, PlanningGeneration, PlanningProject,
    IntegrationDelivery, IntegrationEndpoint, PlanningJob, Schedule, ScheduleActivity,
    ScheduleControlSnapshot, ScheduleExportRecord, ScheduleResource, ScheduleVersion, WorkCalendar,
)
from ..services.cpm import SchedulingError, calculate_schedule_version
from ..services.schedule_materializer import materialize_generation


class ScheduleFixture(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='planner', email='planner@example.com', password='test')
        self.outsider = User.objects.create_user(username='outsider2', email='outsider2@example.com', password='test')
        self.project = PlanningProject.objects.create(
            name='FEED Schedule', effective_date=dt.date(2026, 8, 24), created_by=self.owner,
        )
        self.calendar = WorkCalendar.objects.create(
            project=self.project, name='5 Day', working_weekdays=[0, 1, 2, 3, 4], is_default=True,
        )
        self.schedule = Schedule.objects.create(
            project=self.project, name='Master', code='MASTER', planned_start=dt.date(2026, 8, 24),
            default_calendar=self.calendar, created_by=self.owner,
        )
        self.version = ScheduleVersion.objects.create(schedule=self.schedule, version=1, created_by=self.owner)

    def activity(self, code, duration, **kwargs):
        return ScheduleActivity.objects.create(
            version=self.version, calendar=self.calendar, external_id=code, name=code,
            duration_days=duration, sort_order=self.version.activities.count(), **kwargs,
        )

    def link(self, predecessor, successor, kind='FS', lag=0):
        return ActivityRelationship.objects.create(
            version=self.version, predecessor=predecessor, successor=successor,
            relationship_type=kind, lag_days=lag,
        )


class CalendarAwareCPMTests(ScheduleFixture):
    def test_finish_start_network_skips_weekends_and_calendar_exceptions(self):
        CalendarException.objects.create(
            calendar=self.calendar, date=dt.date(2026, 8, 25), is_working=False, name='Holiday',
        )
        first = self.activity('A', 2)
        second = self.activity('B', 3)
        self.link(first, second)

        run = calculate_schedule_version(self.version, requested_by=self.owner)
        first.refresh_from_db()
        second.refresh_from_db()

        self.assertEqual(run.status, 'succeeded')
        self.assertEqual(first.planned_start, dt.date(2026, 8, 24))
        self.assertEqual(first.planned_finish, dt.date(2026, 8, 26))
        self.assertEqual(second.planned_start, dt.date(2026, 8, 27))
        self.assertEqual(second.planned_finish, dt.date(2026, 8, 31))
        self.assertTrue(first.is_critical)
        self.assertTrue(second.is_critical)

    def test_branching_network_calculates_critical_path_and_float(self):
        start = self.activity('START', 0, activity_type='start_milestone')
        long_branch = self.activity('LONG', 4)
        short_branch = self.activity('SHORT', 1)
        finish = self.activity('FINISH', 0, activity_type='finish_milestone')
        self.link(start, long_branch)
        self.link(start, short_branch)
        self.link(long_branch, finish)
        self.link(short_branch, finish)

        calculate_schedule_version(self.version)
        long_branch.refresh_from_db()
        short_branch.refresh_from_db()
        finish.refresh_from_db()

        self.assertEqual(long_branch.total_float_days, 0)
        self.assertEqual(short_branch.total_float_days, 3)
        self.assertTrue(long_branch.is_critical)
        self.assertFalse(short_branch.is_critical)
        self.assertEqual(finish.planned_start, dt.date(2026, 8, 28))

    def test_ss_ff_and_sf_relationships_are_supported(self):
        anchor = self.activity('A', 5)
        ss = self.activity('SS', 2)
        ff = self.activity('FF', 2)
        sf = self.activity('SF', 2)
        self.link(anchor, ss, 'SS', 2)
        self.link(anchor, ff, 'FF', 0)
        self.link(anchor, sf, 'SF', 6)

        calculate_schedule_version(self.version)
        ss.refresh_from_db()
        ff.refresh_from_db()
        sf.refresh_from_db()

        self.assertEqual(ss.planned_start, dt.date(2026, 8, 26))
        self.assertEqual(ff.planned_start, dt.date(2026, 8, 27))
        self.assertEqual(sf.planned_start, dt.date(2026, 8, 28))

    def test_cycle_is_rejected_and_failed_run_is_retained(self):
        first = self.activity('A', 1)
        second = self.activity('B', 1)
        self.link(first, second)
        self.link(second, first)

        with self.assertRaises(SchedulingError) as caught:
            calculate_schedule_version(self.version)

        self.assertEqual(caught.exception.code, 'dependency_cycle')
        run = self.version.calculation_runs.get()
        self.assertEqual(run.status, 'failed')
        self.assertEqual(run.issues[0]['code'], 'dependency_cycle')

    def test_finish_constraint_reports_negative_float(self):
        activity = self.activity(
            'CONSTRAINED', 3, constraint_type='finish_no_later',
            constraint_date=dt.date(2026, 8, 25),
        )

        run = calculate_schedule_version(self.version)
        activity.refresh_from_db()

        self.assertEqual(activity.total_float_days, -1)
        self.assertTrue(activity.is_critical)
        self.assertEqual(run.issues[0]['code'], 'constraint_violation')


class MaterializationAndAPITests(ScheduleFixture):
    def test_generation_materializes_wbs_logic_resources_and_is_idempotent(self):
        # Use a separate workspace because this fixture already owns MASTER.
        project = PlanningProject.objects.create(
            name='Generated FEED', effective_date=dt.date(2026, 8, 24), created_by=self.owner,
        )
        generation = PlanningGeneration.objects.create(
            project=project, version=1, generated_by=self.owner,
            wbs=[
                {'code': '1', 'name': 'Project', 'level': 0, 'parent_code': None},
                {'code': '1.1', 'name': 'Process', 'level': 1, 'parent_code': '1', 'discipline': 'process'},
            ],
            activities=[
                {'id': 'A', 'wbs_code': '1.1', 'name': 'Prepare', 'original_duration_days': 2,
                 'responsible_role': 'Process Engineer', 'predecessors': []},
                {'id': 'B', 'wbs_code': '1.1', 'name': 'Review', 'original_duration_days': 1,
                 'responsible_role': 'Lead Engineer', 'predecessors': [{'id': 'A', 'type': 'FS', 'lag_days': 0}]},
            ],
        )

        version, run, issues = materialize_generation(generation, requested_by=self.owner)
        same_version, same_run, _ = materialize_generation(generation, requested_by=self.owner)

        self.assertEqual(version.pk, same_version.pk)
        self.assertEqual(run.pk, same_run.pk)
        self.assertEqual(version.wbs_nodes.count(), 2)
        self.assertEqual(version.activities.count(), 2)
        self.assertEqual(version.relationships.count(), 1)
        self.assertEqual(project.schedule_resources.count(), 2)
        self.assertEqual(run.status, 'succeeded')
        self.assertEqual(issues, [])

    def test_schedule_endpoints_are_scoped_to_workspace_owner(self):
        owner_client = APIClient()
        owner_client.force_authenticate(self.owner)
        outsider_client = APIClient()
        outsider_client.force_authenticate(self.outsider)

        owner_response = owner_client.get('/api/v1/planning-intelligence/schedules/')
        outsider_response = outsider_client.get('/api/v1/planning-intelligence/schedules/')

        self.assertEqual(owner_response.status_code, 200)
        self.assertEqual(len(owner_response.data['results']), 1)
        self.assertEqual(outsider_response.status_code, 200)
        self.assertEqual(len(outsider_response.data['results']), 0)

    def test_baseline_captures_immutable_snapshot(self):
        self.activity('A', 2)
        calculate_schedule_version(self.version)
        client = APIClient()
        client.force_authenticate(self.owner)

        response = client.post(
            f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/baseline/',
            {'name': 'Contract Baseline'}, format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.version.refresh_from_db()
        self.assertEqual(self.version.status, 'baselined')
        self.assertEqual(response.data['snapshot']['activities'][0]['external_id'], 'A')

    def test_workspace_returns_complete_scoped_planner_payload(self):
        first = self.activity('A', 2)
        second = self.activity('B', 1)
        self.link(first, second)
        owner_client = APIClient()
        owner_client.force_authenticate(self.owner)
        outsider_client = APIClient()
        outsider_client.force_authenticate(self.outsider)

        response = owner_client.get(
            f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/workspace/',
        )
        outsider_response = outsider_client.get(
            f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/workspace/',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['project']['id'], self.project.id)
        self.assertEqual(len(response.data['activities']), 2)
        self.assertEqual(len(response.data['relationships']), 1)
        self.assertIn('deliverable_summaries', response.data)
        self.assertIn('scheduling_configuration', response.data)
        self.assertIn('generation_validation', response.data)
        self.assertIn('dependency_assumptions', response.data)
        self.assertTrue(response.data['can_edit'])
        self.assertEqual(outsider_response.status_code, 404)

    def test_bulk_activity_edit_uses_optimistic_version_lock(self):
        activity = self.activity('A', 2)
        client = APIClient()
        client.force_authenticate(self.owner)
        initial_timestamp = self.version.updated_at

        response = client.patch(
            f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/bulk-activities/',
            {
                'expected_updated_at': initial_timestamp.isoformat(),
                'activities': [{'id': activity.id, 'name': 'Updated activity', 'duration_days': 4}],
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        activity.refresh_from_db()
        self.version.refresh_from_db()
        self.assertEqual(activity.name, 'Updated activity')
        self.assertEqual(activity.duration_days, 4)
        self.assertEqual(self.version.status, 'draft')
        self.assertGreater(self.version.updated_at, initial_timestamp)

        stale_response = client.patch(
            f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/bulk-activities/',
            {
                'expected_updated_at': initial_timestamp.isoformat(),
                'activities': [{'id': activity.id, 'name': 'Stale overwrite'}],
            },
            format='json',
        )
        self.assertEqual(stale_response.status_code, 409)
        self.assertEqual(stale_response.data['code'], 'version_conflict')
        activity.refresh_from_db()
        self.assertEqual(activity.name, 'Updated activity')

    def test_calculated_version_can_be_approved_then_baselined(self):
        self.activity('A', 2)
        calculate_schedule_version(self.version)
        client = APIClient()
        client.force_authenticate(self.owner)

        approve_response = client.post(
            f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/approve/',
        )
        baseline_response = client.post(
            f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/baseline/',
            {'name': 'Approved Baseline'}, format='json',
        )

        self.assertEqual(approve_response.status_code, 200)
        self.assertEqual(approve_response.data['status'], 'approved')
        self.assertEqual(baseline_response.status_code, 201)
        self.version.refresh_from_db()
        self.assertEqual(self.version.status, 'baselined')

    def test_approval_is_blocked_by_unconfirmed_engineering_gate(self):
        generation = PlanningGeneration.objects.create(
            project=self.project, version=1, generated_by=self.owner,
            logic_matrix=[{
                'activity_id': 'B', 'predecessor_id': 'A', 'type': 'FS',
                'source': 'dependency_template', 'requires_confirmation': True,
            }],
        )
        self.version.source_generation = generation
        self.version.save(update_fields=['source_generation', 'updated_at'])
        self.activity('A', 1)
        calculate_schedule_version(self.version)
        client = APIClient()
        client.force_authenticate(self.owner)

        response = client.post(
            f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/approve/',
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'schedule_assurance_blocked')
        self.assertEqual(response.data['unconfirmed_gate_count'], 1)

    def test_progress_update_builds_evm_forecast_and_s_curve(self):
        activity = self.activity('A', 2)
        resource = ScheduleResource.objects.create(
            project=self.project, code='ENG', name='Engineer', unit_cost=10,
        )
        ActivityAssignment.objects.create(
            activity=activity, resource=resource, planned_units=10,
            budgeted_hours=10, budgeted_cost=100,
        )
        calculate_schedule_version(self.version)
        client = APIClient()
        client.force_authenticate(self.owner)

        response = client.post(
            f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/progress/',
            {
                'data_date': '2026-08-24',
                'updates': [{
                    'activity': activity.id, 'physical_progress_pct': 50,
                    'remaining_duration_days': 1, 'actual_start': '2026-08-24',
                    'actual_hours': 4, 'actual_cost': 40, 'forecast_finish': '2026-08-25',
                }],
            }, format='json',
        )

        self.assertEqual(response.status_code, 200)
        controls = response.data['controls']
        self.assertEqual(controls['bac'], Decimal('100.00'))
        self.assertEqual(controls['planned_value'], Decimal('50.00'))
        self.assertEqual(controls['earned_value'], Decimal('50.00'))
        self.assertEqual(controls['actual_cost'], Decimal('40.00'))
        self.assertEqual(controls['spi'], Decimal('1.0000'))
        self.assertEqual(controls['cpi'], Decimal('1.2500'))
        self.assertEqual(controls['eac'], Decimal('80.00'))
        self.assertGreaterEqual(len(controls['curve']), 2)
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.data_date, dt.date(2026, 8, 24))

    def test_controls_are_scoped_and_snapshot_is_captured(self):
        activity = self.activity('A', 1)
        calculate_schedule_version(self.version)
        owner_client = APIClient()
        owner_client.force_authenticate(self.owner)
        outsider_client = APIClient()
        outsider_client.force_authenticate(self.outsider)
        owner_client.post(
            f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/progress/',
            {'data_date': '2026-08-24', 'updates': [{'activity': activity.id, 'physical_progress_pct': 100}]},
            format='json',
        )

        response = owner_client.post(
            f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/capture-controls/',
            {'data_date': '2026-08-24'}, format='json',
        )
        outsider_response = outsider_client.get(
            f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/controls/',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['data_date'], '2026-08-24')
        self.assertTrue(ScheduleControlSnapshot.objects.filter(version=self.version).exists())
        self.assertEqual(outsider_response.status_code, 404)

    def test_progress_rejects_invalid_actual_finish(self):
        activity = self.activity('A', 1)
        client = APIClient()
        client.force_authenticate(self.owner)

        response = client.post(
            f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/progress/',
            {
                'data_date': '2026-08-24',
                'updates': [{
                    'activity': activity.id, 'physical_progress_pct': 50,
                    'actual_start': '2026-08-24', 'actual_finish': '2026-08-24',
                }],
            }, format='json',
        )

        self.assertEqual(response.status_code, 400)


class GovernanceAPITests(ScheduleFixture):
    def setUp(self):
        super().setUp()
        self.reviewer = User.objects.create_user(
            username='reviewer', email='reviewer@example.com', password='test',
        )
        enterprise = Project.objects.create(name='Governed Project', code='GOV-001', owner=self.owner)
        ProjectMember.objects.create(project=enterprise, user=self.reviewer, role='reviewer')
        self.project.enterprise_project = enterprise
        self.project.save(update_fields=['enterprise_project', 'updated_at'])
        self.activity_row = self.activity('GOV-A', 2)
        calculate_schedule_version(self.version)
        self.owner_client = APIClient()
        self.owner_client.force_authenticate(self.owner)
        self.reviewer_client = APIClient()
        self.reviewer_client.force_authenticate(self.reviewer)

    def test_governance_item_supports_reviewer_discussion_without_schedule_write_access(self):
        create_response = self.owner_client.post(
            f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/governance-items/',
            {
                'item_type': 'change_request', 'title': 'Move design review',
                'description': 'Client requested a later review.', 'priority': 'high',
                'activity': self.activity_row.id, 'owner': self.reviewer.id,
                'schedule_impact_days': 3, 'cost_impact': 5000,
            }, format='json',
        )
        reviewer_create_response = self.reviewer_client.post(
            f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/governance-items/',
            {'item_type': 'action', 'title': 'Unauthorized item'}, format='json',
        )
        comment_response = self.reviewer_client.post(
            f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/governance-comments/',
            {'item': create_response.data['id'], 'body': 'Reviewed with the client.'}, format='json',
        )
        dashboard = self.reviewer_client.get(
            f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/governance/',
        )

        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(reviewer_create_response.status_code, 403)
        self.assertEqual(comment_response.status_code, 201)
        self.assertEqual(dashboard.status_code, 200)
        self.assertFalse(dashboard.data['can_manage'])
        self.assertEqual(dashboard.data['summary']['open_items'], 1)
        self.assertEqual(dashboard.data['summary']['unresolved_comments'], 1)
        self.assertEqual(dashboard.data['items'][0]['comments'][0]['author']['id'], self.reviewer.id)

    def test_multi_reviewer_workflow_approves_calculated_version(self):
        review_response = self.owner_client.post(
            f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/reviews/',
            {
                'title': 'Level 4 Schedule Approval', 'description': 'Formal baseline review.',
                'reviewer_ids': [self.reviewer.id], 'due_date': '2026-08-28',
            }, format='json',
        )
        decision_response = self.reviewer_client.post(
            f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/review-decision/',
            {'review_id': review_response.data['id'], 'decision': 'approved', 'comment': 'Accepted.'},
            format='json',
        )

        self.assertEqual(review_response.status_code, 201)
        self.assertEqual(decision_response.status_code, 200)
        self.assertEqual(decision_response.data['status'], 'approved')
        self.version.refresh_from_db()
        self.assertEqual(self.version.status, 'approved')

    def test_changes_requested_requires_comment_and_outsider_cannot_access_review(self):
        review = self.owner_client.post(
            f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/reviews/',
            {'title': 'Schedule Review', 'reviewer_ids': [self.reviewer.id]}, format='json',
        )
        missing_comment = self.reviewer_client.post(
            f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/review-decision/',
            {'review_id': review.data['id'], 'decision': 'changes_requested'}, format='json',
        )
        outsider_client = APIClient()
        outsider_client.force_authenticate(self.outsider)
        outsider_response = outsider_client.get(
            f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/governance/',
        )

        self.assertEqual(missing_comment.status_code, 400)
        self.assertEqual(outsider_response.status_code, 404)


class IntegrationAndEnterpriseAPITests(ScheduleFixture):
    def setUp(self):
        super().setUp()
        self.activity_row = self.activity('EXP-A', 2)
        calculate_schedule_version(self.version)
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def test_relational_schedule_exports_are_audited(self):
        for export_format in ('json', 'csv', 'xlsx', 'xer'):
            response = self.client.get(
                f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}/export/',
                {'export_format': export_format},
            )
            self.assertEqual(response.status_code, 200)
            self.assertGreater(len(response.content), 10)
            self.assertIn(f'.{export_format}', response['Content-Disposition'])
            self.assertEqual(len(response['X-Content-SHA256']), 64)
        self.assertEqual(ScheduleExportRecord.objects.filter(version=self.version).count(), 4)

    @override_settings(PLANNING_INTEGRATION_ENCRYPTION_KEY='integration-test-key')
    @patch('apps.planning_intelligence.enterprise_views.deliver_schedule_integration.delay')
    def test_integration_credentials_are_encrypted_and_publish_is_idempotent(self, mocked_delay):
        endpoint_response = self.client.post(
            '/api/v1/planning-intelligence/integration-endpoints/',
            {
                'project': self.project.id, 'name': 'Corporate Data Lake',
                'target_url': 'https://example.com/radai-hook', 'export_format': 'json',
                'auth_type': 'hmac_sha256', 'secret': 'top-secret-value',
                'event_types': ['schedule.published'], 'timeout_seconds': 10,
            }, format='json',
        )
        endpoint = IntegrationEndpoint.objects.get()
        payload = {
            'version': self.version.id, 'event_type': 'schedule.published',
            'idempotency_key': 'publish-v1',
        }
        first = self.client.post(
            f'/api/v1/planning-intelligence/integration-endpoints/{endpoint.id}/publish/', payload, format='json',
        )
        second = self.client.post(
            f'/api/v1/planning-intelligence/integration-endpoints/{endpoint.id}/publish/', payload, format='json',
        )

        self.assertEqual(endpoint_response.status_code, 201)
        self.assertTrue(endpoint_response.data['secret_configured'])
        self.assertNotIn('top-secret-value', endpoint.secret_encrypted)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(IntegrationDelivery.objects.count(), 1)
        mocked_delay.assert_called_once()

    def test_private_integration_url_is_rejected_by_delivery_guard(self):
        from ..services.integration_delivery import validate_public_https_url

        with self.assertRaises(ValueError):
            validate_public_https_url('https://127.0.0.1/hook')
        with self.assertRaises(ValueError):
            validate_public_https_url('http://example.com/hook')

    def test_portfolio_readiness_and_confirmed_retention_cleanup(self):
        export = ScheduleExportRecord.objects.create(
            version=self.version, export_format='json', filename='old.json',
            size_bytes=20, sha256='a' * 64, requested_by=self.owner,
        )
        old_date = timezone.now() - dt.timedelta(days=400)
        ScheduleExportRecord.objects.filter(pk=export.pk).update(created_at=old_date)

        portfolio = self.client.get('/api/v1/planning-intelligence/enterprise/portfolio/')
        readiness = self.client.get(
            '/api/v1/planning-intelligence/enterprise/readiness/', {'project': self.project.id},
        )
        policy = self.client.put(
            f'/api/v1/planning-intelligence/enterprise/retention/?project={self.project.id}',
            {'export_history_days': 365}, format='json',
        )
        preview = self.client.post(
            '/api/v1/planning-intelligence/enterprise/retention-cleanup/',
            {'project': self.project.id, 'execute': False}, format='json',
        )
        execute = self.client.post(
            '/api/v1/planning-intelligence/enterprise/retention-cleanup/',
            {'project': self.project.id, 'execute': True, 'confirmation': self.project.name}, format='json',
        )

        self.assertEqual(portfolio.status_code, 200)
        self.assertEqual(portfolio.data['summary']['project_count'], 1)
        self.assertEqual(readiness.status_code, 200)
        self.assertEqual(policy.status_code, 200)
        self.assertEqual(preview.data['eligible']['exports'], 1)
        self.assertEqual(execute.status_code, 200)
        export.refresh_from_db()
        self.assertTrue(export.is_deleted)
