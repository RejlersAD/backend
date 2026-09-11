from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.hr_core.models import EmployeeMaster
from apps.hr_core.services import EmployeeService
from apps.rbac.models import Organization, UserProfile, UserRole


class ReportingManagerProfileTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Employee lookup', code='LOOKUP')
        self.profile = self.make_profile('employee', 'Engineering')
        self.manager = self.make_profile('sales.person', 'sales')
        for profile in (self.profile, self.manager):
            EmployeeService.create_employee(
                user=profile.user, employee_number=f'EMP-{profile.user.username}',
                first_name=profile.user.first_name, email=profile.user.email,
                department=profile.department,
            )
        self.client = APIClient()
        self.client.force_authenticate(self.profile.user)

    def make_profile(self, name, department):
        user = get_user_model().objects.create_user(username=name, email=f'{name}@example.test', first_name=name)
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': self.organization})
        profile.department = department
        profile.save()
        UserRole.objects.filter(user_profile=profile).delete()
        return profile

    def test_lookup_includes_non_engineers_without_module_grants_or_pagination(self):
        for index in range(55):
            self.make_profile(f'finance{index}', 'finance')
        inactive = self.make_profile('inactive', 'sales')
        inactive.user.is_active = False
        inactive.user.save()
        response = self.client.get('/api/v1/rbac/users/reporting-managers/')
        self.assertEqual(response.status_code, 200, response.data)
        rows = response.data['results']
        self.assertEqual(len(rows), 56)
        self.assertIn(str(self.manager.pk), {row['id'] for row in rows})
        self.assertNotIn(str(self.profile.pk), {row['id'] for row in rows})
        self.assertEqual(set(rows[0]), {'id', 'name', 'email', 'employee_id', 'department', 'job_title'})

    def test_lookup_requires_authentication(self):
        self.client.force_authenticate(user=None)
        self.assertIn(self.client.get('/api/v1/rbac/users/reporting-managers/').status_code, (401, 403))

    def test_sales_and_reporting_manager_save_and_reload(self):
        response = self.client.patch('/api/v1/rbac/users/me/?view=profile', {
            'department': 'sales', 'manager_id': str(self.manager.pk),
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.department, 'sales')
        self.assertEqual(self.profile.manager_id, self.manager.pk)
        employee = EmployeeMaster.objects.get(user=self.profile.user)
        self.assertEqual(employee.department, 'sales')
        self.assertEqual(employee.manager.user_id, self.manager.user_id)
        response = self.client.get('/api/v1/rbac/users/me/?view=profile')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['department'], 'sales')
        self.assertEqual(response.data['manager_detail']['id'], str(self.manager.pk))
        response = self.client.patch('/api/v1/rbac/users/me/', {'manager_id': ''}, format='multipart')
        self.assertEqual(response.status_code, 200, response.data)
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.manager_id)

    def test_invalid_manager_does_not_partially_save_profile(self):
        for manager_id in ('not-a-uuid', str(self.profile.pk)):
            with self.subTest(manager_id=manager_id):
                response = self.client.patch('/api/v1/rbac/users/me/', {
                    'first_name': 'Do not save', 'department': 'sales', 'manager_id': manager_id,
                }, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.profile.refresh_from_db()
                self.profile.user.refresh_from_db()
                self.assertEqual(self.profile.department, 'Engineering')
                self.assertEqual(self.profile.user.first_name, 'employee')
