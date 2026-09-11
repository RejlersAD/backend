from datetime import date
from unittest.mock import patch
from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError
from .services.adjustment_period import require_current_or_future
from .serializers import PayrollAdjustmentSerializer


class AdjustmentPeriodTests(SimpleTestCase):
    @patch('apps.payroll_engine.services.adjustment_period.timezone.localdate', return_value=date(2026, 9, 10))
    def test_month_boundary(self, today):
        for year, month in [(2026, 8), (2025, 12)]:
            with self.assertRaises(ValidationError):
                require_current_or_future(year, month)
        for year, month in [(2026, 9), (2026, 12), (2027, 1)]:
            require_current_or_future(year, month)

    @patch('apps.payroll_engine.services.adjustment_period.timezone.localdate', return_value=date(2026, 9, 10))
    def test_partial_update_cannot_bypass_period(self, today):
        from types import SimpleNamespace
        serializer = PayrollAdjustmentSerializer(instance=SimpleNamespace(target_year=2026,target_month=8))
        with self.assertRaises(ValidationError):
            serializer.validate({'amount': 100})
        serializer.validate({'target_month': 9})
