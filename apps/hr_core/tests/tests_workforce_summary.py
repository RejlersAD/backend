from datetime import date

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.hr_core.models import EmployeeMaster
from apps.rbac.models import Module, Organization, Role, RoleModule, UserProfile, UserRole


User = get_user_model()
TEST_MIDDLEWARE = [
    middleware for middleware in settings.MIDDLEWARE
    if middleware not in {
        'apps.activity.tracker.ActivityMiddleware',
        'apps.usage_tracking.middleware.UsageTrackingMiddleware',
        'apps.core.middleware.ApiUsageLoggingMiddleware',
    }
]


@override_settings(MIDDLEWARE=TEST_MIDDLEWARE)
class WorkforceSummaryAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.organization = Organization.objects.create(name='RAD Test', code='RAD-TEST')
        self.employee_user = User.objects.create_user(
            username='employee.summary', email='employee.summary@example.com'
        )
        self.employee = EmployeeMaster.objects.create(
            user=self.employee_user,
            employee_number='EMP-SUMMARY',
            employee_code='SUMMARY',
            emp_code='SUMMARY',
            email=self.employee_user.email,
            first_name='Employee',
            last_name='Summary',
            department='Engineering',
            designation='Engineer',
            join_date=date(2026, 1, 1),
        )

    def test_hr_management_module_can_read_lightweight_summary(self):
        hr_user = User.objects.create_user(username='hr.viewer', email='hr.viewer@example.com')
        profile = UserProfile.objects.create(user=hr_user, organization=self.organization)
        module, _ = Module.objects.get_or_create(
            code='hr_management',
            defaults={'name': 'Human Resources Test'},
        )
        role, _ = Role.objects.get_or_create(
            code='hr_viewer_test',
            defaults={'name': 'HR Viewer Test'},
        )
        RoleModule.objects.create(role=role, module=module, granted_by=hr_user)
        UserRole.objects.create(user_profile=profile, role=role, assigned_by=hr_user)
        self.client.force_authenticate(hr_user)

        response = self.client.get('/api/v1/hr/employees/workforce-summary/')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['count'], 1)
        row = response.data['results'][0]
        self.assertEqual(row['employee_number'], 'EMP-SUMMARY')
        self.assertEqual(row['department'], 'Engineering')
        self.assertNotIn('bank_account_number', row)
        self.assertNotIn('current_base_salary', row)

    def test_user_without_hr_management_is_denied(self):
        outsider = User.objects.create_user(username='outsider.summary', email='outsider@example.com')
        self.client.force_authenticate(outsider)

        response = self.client.get('/api/v1/hr/employees/workforce-summary/')

        self.assertEqual(response.status_code, 403)
