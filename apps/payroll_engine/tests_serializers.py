from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from .models import PayrollEmployee
from .serializers import PayrollEmployeeSerializer
from .views import PayrollEmployeeViewSet


class PayrollEmployeeSerializerTests(SimpleTestCase):
    def test_legacy_null_salary_components_do_not_break_detail_serialization(self):
        employee = PayrollEmployee(
            employee_no='LEGACY-1',
            full_name='Legacy Employee',
            basic=None,
            housing=None,
            transport=None,
            home_leave=None,
        )

        data = PayrollEmployeeSerializer(employee).data

        self.assertEqual(employee.default_gross, Decimal('0.00'))
        self.assertEqual(data['default_gross'], '0.00')
        self.assertIsNone(data['profile_photo'])


class PayrollEmployeeViewSetTests(SimpleTestCase):
    @patch('apps.payroll_engine.views.Payslip.objects.filter')
    def test_draft_sync_uses_saved_values_and_does_not_break_patch(self, mock_filter):
        mock_filter.return_value.update.side_effect = RuntimeError('legacy snapshot failure')
        employee = SimpleNamespace(
            pk=1,
            department='Canonical Department',
            designation='Canonical Title',
            joining_date=None,
        )

        PayrollEmployeeViewSet()._sync_to_draft_payslips(
            employee,
            {
                'department': 'Untrusted Request Department',
                'designation': 'Untrusted Request Title',
                'joining_date': '2026-01-14',
            },
        )

        mock_filter.return_value.update.assert_called_once_with(
            snapshot_department='Canonical Department',
            snapshot_designation='Canonical Title',
            snapshot_joining_date=None,
        )

    def test_perform_update_saves_and_synchronizes_only_once(self):
        employee = SimpleNamespace(pk=1)
        serializer = MagicMock()
        serializer.save.return_value = employee
        serializer.validated_data = {'housing': Decimal('2500.00')}
        view = PayrollEmployeeViewSet()
        view._sync_to_draft_payslips = MagicMock()

        view.perform_update(serializer)

        serializer.save.assert_called_once_with()
        view._sync_to_draft_payslips.assert_called_once_with(
            employee,
            serializer.validated_data,
        )
