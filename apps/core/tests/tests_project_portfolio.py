"""Portfolio list facts use approved snapshots and remain inside project access."""
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.project_models import Project, ProjectMember
from apps.core.project_views import ProjectViewSet
from apps.planning_intelligence.models import PlanningProject
from apps.planning_intelligence.schedule_models import Schedule, ScheduleBaseline, ScheduleVersion


class ProjectPortfolioTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(username='portfolio-owner', email='owner@example.test',
                                              first_name='Project', last_name='Owner')
        self.other = User.objects.create_user(username='portfolio-other', email='other@example.test')
        self.admin = User.objects.create_user(username='portfolio-admin', email='admin@example.test', is_staff=True)
        self.factory = APIRequestFactory()
        self.project, self.workspace, self.schedule, self.version = self.project_fixture('PORTFOLIO')

    def project_fixture(self, code, *, owner=None, **fields):
        project = Project.objects.create(
            code=code, name=f'{code} project', client_name='Recorded client',
            owner=owner or self.owner, status='active', start_date=date(2035, 1, 1),
            end_date=date(2035, 12, 31), **fields,
        )
        workspace = PlanningProject.objects.create(enterprise_project=project, name=project.name,
                                                    created_by=self.other)
        schedule = Schedule.objects.create(project=workspace, name=project.name, code=code,
                                            planned_start=date(2035, 1, 1))
        version = ScheduleVersion.objects.create(schedule=schedule, version=1, status='calculated',
                                                  calculated_finish=date(2035, 2, 1))
        workspace.master_schedule_version = version
        workspace.save(update_fields=['master_schedule_version'])
        return project, workspace, schedule, version

    def baseline(self, *, schedule=None, version=None, approved=True, snapshot=None, **fields):
        return ScheduleBaseline.objects.create(
            schedule=schedule or self.schedule, source_version=version or self.version,
            name=f'Baseline {ScheduleBaseline.objects.count() + 1}',
            approved_by=self.owner if approved else None,
            approved_at=timezone.now() if approved else None,
            snapshot=snapshot if snapshot is not None else {
                'accepted_inputs': {'project_start': '2035-01-06'},
                'version': {'calculated_finish': '2035-09-04'},
            }, **fields,
        )

    def get_list(self, user=None, query=''):
        request = self.factory.get('/api/v1/projects/' + query)
        force_authenticate(request, user=user or self.owner)
        return ProjectViewSet.as_view({'get': 'list'})(request)

    def row(self, response, project=None):
        self.assertEqual(response.status_code, 200, response.data)
        return next(row for row in response.data['results'] if row['id'] == (project or self.project).pk)

    def test_compact_fields_keep_owner_distinct_from_unrecorded_creator(self):
        row = self.row(self.get_list())
        self.assertEqual(row['client_name'], 'Recorded client')
        self.assertEqual(row['owner_id'], self.owner.pk)
        self.assertEqual(row['owner_name'], 'Project Owner')
        self.assertIsNotNone(row['updated_at'])
        self.assertIsNone(row['creator_name'])
        self.assertIsNone(row['portfolio']['entity'])
        self.assertFalse(row['portfolio']['missing_owner'])
        self.assertIsNone(row['portfolio']['baseline'])
        self.assertEqual(row['portfolio']['health']['key'], 'needs_review')

    def test_baseline_requires_saved_approval_and_uses_frozen_dates(self):
        self.version.status = 'approved'
        self.version.save(update_fields=['status'])
        unapproved = self.baseline(approved=False)
        self.assertIsNone(self.row(self.get_list())['portfolio']['baseline'])
        ScheduleBaseline.objects.filter(pk=unapproved.pk).update(approved_at=timezone.now())
        self.assertIsNone(self.row(self.get_list())['portfolio']['baseline'])
        ScheduleBaseline.objects.filter(pk=unapproved.pk).update(approved_at=None, approved_by=self.owner)
        self.assertIsNone(self.row(self.get_list())['portfolio']['baseline'])
        original = self.baseline()
        self.baseline(approved=False)
        self.project.start_date, self.project.end_date = date(2040, 1, 1), date(2040, 12, 31)
        self.project.save(update_fields=['start_date', 'end_date'])
        self.version.calculated_finish = date(2040, 12, 31)
        self.version.save(update_fields=['calculated_finish'])
        row = self.row(self.get_list())
        self.assertEqual(row['portfolio']['baseline']['id'], original.pk)
        self.assertTrue(row['portfolio']['baseline']['approved'])
        self.assertEqual(row['portfolio']['baseline']['start_date'], '2035-01-06')
        self.assertEqual(row['portfolio']['baseline']['finish_date'], '2035-09-04')
        self.assertEqual(row['portfolio']['health']['key'], 'unknown')
        latest = self.baseline(snapshot={})
        latest_row = self.row(self.get_list())['portfolio']['baseline']
        self.assertEqual(latest_row['id'], latest.pk)
        self.assertIsNone(latest_row['start_date'])
        self.assertIsNone(latest_row['finish_date'])

    def test_legacy_snapshot_dates_are_complete_frozen_activity_spans(self):
        baseline = self.baseline(snapshot={'activities': [
            {'planned_start': '2035-02-03', 'planned_finish': '2035-02-06'},
            {'planned_start': '2035-01-09', 'planned_finish': '2035-03-08'},
        ]})
        row = self.row(self.get_list())['portfolio']['baseline']
        self.assertEqual((row['start_date'], row['finish_date']), ('2035-01-09', '2035-03-08'))
        # An incomplete legacy snapshot does not acquire dates from today's plan.
        ScheduleBaseline.objects.filter(pk=baseline.pk).update(snapshot={'activities': [
            {'planned_start': '2035-02-03', 'planned_finish': '2035-02-06'},
            {'planned_start': None, 'planned_finish': 'not-a-date'},
        ]})
        row = self.row(self.get_list())['portfolio']['baseline']
        self.assertIsNone(row['start_date'])
        self.assertIsNone(row['finish_date'])

    def test_access_scope_excludes_other_deleted_and_inactive_memberships(self):
        active, _, active_schedule, active_version = self.project_fixture('ACTIVE-MEMBER', owner=self.other)
        inactive, _, _, _ = self.project_fixture('INACTIVE-MEMBER', owner=self.other)
        foreign, _, foreign_schedule, foreign_version = self.project_fixture('OTHER', owner=self.other)
        deleted, _, _, _ = self.project_fixture('DELETED', is_deleted=True)
        ProjectMember.objects.create(project=active, user=self.owner, is_active=True)
        ProjectMember.objects.create(project=active, user=self.other, is_active=True)
        ProjectMember.objects.create(project=inactive, user=self.owner, is_active=False)
        self.baseline(schedule=active_schedule, version=active_version)
        foreign_baseline = self.baseline(schedule=foreign_schedule, version=foreign_version)
        response = self.get_list()
        self.assertEqual({row['id'] for row in response.data['results']}, {self.project.pk, active.pk})
        self.assertEqual(self.row(response, active)['team_size'], 2)
        self.assertNotIn(foreign_baseline.pk, [row['portfolio']['baseline']['id']
            for row in response.data['results'] if row['portfolio']['baseline']])
        # Existing commercial readers retain their portfolio-wide read scope.
        with patch('apps.project_control.access.has_commercial_module_access', return_value=True):
            commercial = self.get_list(self.other)
        self.assertEqual({row['id'] for row in commercial.data['results']},
                         {self.project.pk, active.pk, inactive.pk, foreign.pk})
        self.assertNotIn(deleted.pk, {row['id'] for row in commercial.data['results']})

    def test_deleted_baseline_ancestors_and_cross_schedule_sources_are_excluded(self):
        baseline = self.baseline()
        for instance in (baseline, self.version, self.schedule, self.workspace):
            type(instance).objects.filter(pk=instance.pk).update(is_deleted=True)
            self.assertIsNone(self.row(self.get_list())['portfolio']['baseline'])
            type(instance).objects.filter(pk=instance.pk).update(is_deleted=False)
        _, _, _, other_version = self.project_fixture('OTHER-SOURCE', owner=self.other)
        ScheduleBaseline.objects.filter(pk=baseline.pk).update(source_version=other_version)
        self.assertIsNone(self.row(self.get_list())['portfolio']['baseline'])

    def test_health_does_not_mark_closed_or_unconfirmed_projects_overdue(self):
        self.project.end_date = timezone.localdate() - timedelta(days=1)
        self.project.save(update_fields=['end_date'])
        self.assertEqual(self.row(self.get_list())['portfolio']['health']['key'], 'at_risk')
        for status in ('completed', 'cancelled'):
            Project.objects.filter(pk=self.project.pk).update(status=status)
            row = self.row(self.get_list())
            self.assertEqual(row['portfolio']['health']['key'], 'unknown')
            self.assertFalse(row['is_overdue'])
        Project.objects.filter(pk=self.project.pk).update(
            status='active', custom_fields={'control_setup': {'operational_status_confirmed': False}},
        )
        self.assertEqual(self.row(self.get_list())['portfolio']['health']['key'], 'needs_setup')
        Project.objects.filter(pk=self.project.pk).update(owner=None, end_date=date(2035, 12, 31), custom_fields={})
        row = self.row(self.get_list(self.admin))
        self.assertTrue(row['portfolio']['missing_owner'])
        self.assertIsNone(row['owner_id'])
        self.assertEqual(row['portfolio']['health']['key'], 'needs_setup')

    def test_number_of_queries_is_constant_as_portfolio_grows(self):
        self.baseline()
        with CaptureQueriesContext(connection) as first:
            self.get_list(self.admin)
        for index in range(8):
            _, _, schedule, version = self.project_fixture(f'BULK-{index}')
            self.baseline(schedule=schedule, version=version)
        with CaptureQueriesContext(connection) as larger:
            response = self.get_list(self.admin)
        self.assertEqual(response.data['count'], 9)
        self.assertEqual(len(larger), len(first))
        self.assertLessEqual(len(larger), 5)
