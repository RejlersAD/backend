from decimal import Decimal

from django.test import SimpleTestCase

from .models import PayrollEmployee
from .serializers import PayrollEmployeeSerializer


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
