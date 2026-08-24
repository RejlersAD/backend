import datetime
from decimal import Decimal

from django.test import TestCase

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
