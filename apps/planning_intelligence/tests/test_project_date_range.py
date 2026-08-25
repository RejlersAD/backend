import datetime
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.users.models import User

from ..models import PlanningGeneration, PlanningProject
from ..serializers import PlanningProjectSerializer


class PlanningProjectDateRangeTests(TestCase):
    def test_date_range_calculates_exact_calendar_months_and_days(self):
        serializer = PlanningProjectSerializer(data={
            'name': 'Date Range Project',
            'effective_date': '2026-01-15',
            'planned_end_date': '2027-01-15',
            'duration_months': '99.0000',
        })

        self.assertTrue(serializer.is_valid(), serializer.errors)
        project = serializer.save()

        self.assertEqual(project.duration_months, Decimal('12.0000'))
        self.assertEqual(project.planned_end_date, datetime.date(2027, 1, 15))
        self.assertEqual(serializer.data['duration_days'], 365)

    def test_partial_calendar_month_is_stored_as_decimal(self):
        serializer = PlanningProjectSerializer(data={
            'name': 'Partial Month Project',
            'effective_date': '2026-01-15',
            'planned_end_date': '2026-02-01',
        })

        self.assertTrue(serializer.is_valid(), serializer.errors)
        project = serializer.save()

        self.assertEqual(project.duration_months, Decimal('0.5484'))

    def test_end_date_must_be_after_start_date(self):
        serializer = PlanningProjectSerializer(data={
            'name': 'Invalid Range Project',
            'effective_date': '2026-02-01',
            'planned_end_date': '2026-02-01',
        })

        self.assertFalse(serializer.is_valid())
        self.assertIn('planned_end_date', serializer.errors)


class PlanningProjectListQueryTests(TestCase):
    def test_project_list_does_not_load_large_generation_payloads(self):
        user = User.objects.create_user(
            username='planning-list-owner', email='planning-list@example.com', password='test',
            is_staff=True,
        )
        project = PlanningProject.objects.create(name='Lean Project List', created_by=user)
        PlanningGeneration.objects.create(
            project=project, version=7,
            intelligence={'large': 'payload'},
            activities=[{'id': index, 'name': f'Activity {index}'} for index in range(20)],
            narrative='Large generated narrative',
            generated_by=user,
        )
        client = APIClient()
        client.force_authenticate(user)

        with CaptureQueriesContext(connection) as queries:
            response = client.get('/api/v1/planning-intelligence/projects/')

        self.assertEqual(response.status_code, 200)
        rows = response.data.get('results', []) if isinstance(response.data, dict) else response.data
        self.assertEqual(rows[0]['latest_generation_version'], 7)
        self.assertEqual(rows[0]['file_count'], 0)
        executed_sql = '\n'.join(query['sql'].lower() for query in queries.captured_queries)
        self.assertNotIn('planning_intelligence_planninggeneration"."activities', executed_sql)
        self.assertNotIn('planning_intelligence_planninggeneration"."intelligence', executed_sql)
