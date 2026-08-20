from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.hr_core.services import EmployeeService
from apps.rbac.models import Organization, UserProfile


User = get_user_model()


class EmployeeProfileSyncTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Sync Test', code='SYNC')
        self.user = User.objects.create_user(
            username='sync.user',
            email='sync.user@example.com',
            first_name='Old',
            last_name='Name',
        )
        self.profile = UserProfile.objects.create(
            user=self.user,
            organization=self.organization,
            employee_id='1234',
            department='Engineering',
            job_title='Engineer',
            phone='+971500000000',
            location='Abu Dhabi',
        )
        self.employee = EmployeeService.create_employee(
            user=self.user,
            employee_number='EMP-SYNC-1',
            employee_code='1234',
            first_name='Old',
            last_name='Name',
            department='Old Department',
            designation='Old Title',
        )

    def test_rbac_changes_sync_to_employee_master(self):
        self.user.first_name = 'Updated'
        self.user.save(update_fields=['first_name'])
        self.profile.department = 'Process'
        self.profile.job_title = 'Senior Engineer'
        self.profile.phone = '+971511111111'
        self.profile.location = 'Dubai'
        self.profile.save()

        EmployeeService.sync_from_rbac_profile(
            self.profile,
            {'first_name', 'department', 'job_title', 'phone', 'location'},
        )

        self.employee.refresh_from_db()
        self.assertEqual(self.employee.first_name, 'Updated')
        self.assertEqual(self.employee.department, 'Process')
        self.assertEqual(self.employee.designation, 'Senior Engineer')
        self.assertEqual(self.employee.job_title_uae, 'Senior Engineer')
        self.assertEqual(self.employee.phone_number, '+971511111111')
        self.assertEqual(self.employee.office, 'Dubai')

    def test_employee_master_changes_sync_to_rbac_profile(self):
        self.employee.department = 'Projects'
        self.employee.designation = 'Project Manager'
        self.employee.phone_number = '+971522222222'
        self.employee.office = 'Chennai'
        self.employee.save()

        EmployeeService.sync_to_rbac_profile(
            self.employee,
            {'department', 'designation', 'phone_number', 'office'},
        )

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.department, 'Projects')
        self.assertEqual(self.profile.job_title, 'Project Manager')
        self.assertEqual(self.profile.phone, '+971522222222')
        self.assertEqual(self.profile.location, 'Chennai')

    def test_unrelated_update_does_not_overwrite_hr_fields(self):
        self.employee.department = 'Authoritative HR Department'
        self.employee.save(update_fields=['department'])
        self.profile.department = ''

        EmployeeService.sync_from_rbac_profile(self.profile, {'bio'})

        self.employee.refresh_from_db()
        self.assertEqual(self.employee.department, 'Authoritative HR Department')

    def test_deactivation_syncs_employment_status(self):
        self.user.is_active = False
        self.profile.status = 'inactive'
        EmployeeService.sync_from_rbac_profile(
            self.profile, {'status', 'is_active'}
        )

        self.employee.refresh_from_db()
        self.assertEqual(self.employee.employment_status, 'suspended')
