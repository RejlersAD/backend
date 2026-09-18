"""Draft planning inputs persist without modifying enterprise or baseline records."""
from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
from rest_framework.test import APIClient

from ..models import PlanningAuditEvent, PlanningProject, Schedule, ScheduleBaseline, ScheduleVersion
from .test_phase0_foundation import Phase0Fixture
from .test_scheduling_engine import grant_planning_test_actions


class PlanningScopeInputTests(Phase0Fixture):
    def setUp(self):
        super().setUp()
        grant_planning_test_actions((self.owner,), ('read', 'create', 'update'))
        # Keep the denial test at the project-write gate, after module access.
        grant_planning_test_actions((self.viewer,), ('read', 'update'))
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.url = f'/api/v1/planning-intelligence/projects/{self.workspace.pk}/'

    def baseline(self):
        self.workspace.effective_date = date(2026, 10, 5)
        self.workspace.planned_end_date = date(2026, 12, 18)
        self.workspace.save()
        schedule = Schedule.objects.create(
            project=self.workspace, code='SCOPE-TEST', name='Scope test',
            planned_start=self.workspace.effective_date, created_by=self.owner,
        )
        version = ScheduleVersion.objects.create(
            schedule=schedule, version=1, status='baselined', created_by=self.owner,
        )
        return ScheduleBaseline.objects.create(
            schedule=schedule, source_version=version, name='Approved baseline',
            approved_by=self.owner,
        )

    def test_draft_scope_round_trip_retains_dates_and_enterprise_project(self):
        self.enterprise_project.refresh_from_db()
        enterprise_before = dict(self.enterprise_project.__dict__)
        response = self.client.patch(self.url, {
            'scope_summary': 'Electrical studies and civil foundations.\nCoordinated submissions.',
            'exclusions': 'Construction execution and equipment procurement.',
            'budgeted_effort_hours': '200.50',
            'effective_date': '2026-10-05',
            'planned_end_date': '2026-12-18',
        }, format='json')

        self.assertEqual(response.status_code, 200, response.data)
        saved = self.client.get(self.url)
        self.assertEqual(saved.data['scope_summary'], response.data['scope_summary'])
        self.assertEqual(saved.data['exclusions'], response.data['exclusions'])
        self.assertEqual(saved.data['budgeted_effort_hours'], '200.50')
        self.assertEqual(saved.data['duration_days'], 74)
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.budgeted_effort_hours, Decimal('200.50'))
        self.enterprise_project.refresh_from_db()
        self.assertEqual(
            {key: value for key, value in self.enterprise_project.__dict__.items() if key != '_state'},
            {key: value for key, value in enterprise_before.items() if key != '_state'},
        )
        self.assertFalse(ScheduleBaseline.objects.exists())
        self.assertTrue(PlanningAuditEvent.objects.filter(
            project=self.workspace, action='project.updated', after__scope_summary=saved.data['scope_summary'],
        ).exists())

    def test_new_workspace_accepts_draft_inputs_without_requiring_dates(self):
        response = self.client.post('/api/v1/planning-intelligence/projects/', {
            'name': 'New draft planning workspace', 'scope_summary': 'Assess grid integration.',
            'exclusions': '', 'budgeted_effort_hours': '0',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        project = PlanningProject.objects.get(pk=response.data['id'])
        self.assertEqual(project.scope_summary, 'Assess grid integration.')
        self.assertEqual(project.budgeted_effort_hours, Decimal('0'))
        self.assertIsNone(project.effective_date)

    def test_blank_inputs_and_unknown_effort_can_be_saved(self):
        response = self.client.patch(self.url, {
            'scope_summary': '', 'exclusions': '', 'budgeted_effort_hours': None,
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data['budgeted_effort_hours'])

    def test_invalid_effort_is_rejected_without_saving_other_fields(self):
        for value in ('-0.01', 'NaN', 'Infinity', '1.234'):
            with self.subTest(value=value):
                response = self.client.patch(self.url, {
                    'budgeted_effort_hours': value, 'scope_summary': 'Must not be saved',
                }, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn('budgeted_effort_hours', response.data)
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.scope_summary, '')
        self.assertIsNone(self.workspace.budgeted_effort_hours)

    def test_negative_effort_cannot_bypass_api_validation(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            PlanningProject.objects.filter(pk=self.workspace.pk).update(budgeted_effort_hours=-1)

    def test_published_baseline_rejects_changed_or_cleared_dates(self):
        baseline = self.baseline()
        for field, value in (
            ('effective_date', '2026-10-06'), ('planned_end_date', '2026-12-20'),
            ('effective_date', None), ('planned_end_date', None),
        ):
            with self.subTest(field=field, value=value):
                response = self.client.patch(self.url, {field: value}, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn(field, response.data)
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.effective_date, date(2026, 10, 5))
        self.assertEqual(self.workspace.planned_end_date, date(2026, 12, 18))
        self.assertTrue(ScheduleBaseline.objects.filter(pk=baseline.pk).exists())

    def test_baseline_allows_draft_notes_and_unchanged_dates(self):
        self.baseline()
        response = self.client.patch(self.url, {
            'scope_summary': 'Clarified draft scope.', 'budgeted_effort_hours': '120',
            'effective_date': '2026-10-05', 'planned_end_date': '2026-12-18',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['scope_summary'], 'Clarified draft scope.')

    def test_deleted_baseline_does_not_lock_project_dates(self):
        baseline = self.baseline()
        baseline.is_deleted = True
        baseline.save(update_fields=['is_deleted'])
        response = self.client.patch(self.url, {'planned_end_date': '2026-12-20'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)

    def test_viewer_cannot_save_draft_scope(self):
        self.client.force_authenticate(self.viewer)
        response = self.client.patch(self.url, {'scope_summary': 'Unauthorized'}, format='json')
        self.assertEqual(response.status_code, 403)
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.scope_summary, '')
