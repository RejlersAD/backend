from django.contrib.auth import get_user_model
from django.test import TestCase
from types import SimpleNamespace

from apps.hr_core.services import EmployeeService
from apps.payroll_engine.models import PayrollEmployee
from apps.payroll_engine.serializers import PayrollEmployeeSerializer
from apps.rbac.models import Organization, UserProfile
from apps.rbac.serializers import UserProfileListSerializer, UserProfileSerializer
from apps.rbac.views import UserProfileViewSet


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

    def test_employee_list_does_not_defer_serialized_canonical_fields(self):
        """Keep list serialization from reintroducing one query per employee."""
        view = UserProfileViewSet()
        view.action = 'list'
        view.request = SimpleNamespace(user=self.user, query_params={})

        profile = list(view.get_queryset().filter(pk=self.profile.pk))[0]
        master = profile.user.employee_master

        self.assertNotIn('canonical_employee_id', profile.get_deferred_fields())
        self.assertNotIn('probation_end_date', master.get_deferred_fields())
        self.assertNotIn('photo_file_path', master.get_deferred_fields())
        self.assertNotIn('photo_url', master.get_deferred_fields())

    def test_employee_list_photo_falls_back_to_user_employee_master(self):
        """Older profiles still show their canonical uploaded employee photo."""
        photo_url = 'https://cdn.example.com/employee-photo.png'
        self.employee.photo_file_path = ''
        self.employee.photo_url = photo_url
        self.employee.save(update_fields=['photo_file_path', 'photo_url'])
        UserProfile.objects.filter(pk=self.profile.pk).update(canonical_employee=None)
        self.profile.refresh_from_db()

        data = UserProfileListSerializer(self.profile).data

        self.assertEqual(data['profile_photo'], photo_url)

    def test_employee_id_becomes_the_canonical_employee_number(self):
        self.profile.employee_id = '23022'
        self.profile.save(update_fields=['employee_id', 'updated_at'])

        self.employee.refresh_from_db()
        self.assertEqual(self.employee.employee_number, '23022')
        self.assertEqual(self.employee.employee_code, '23022')
        self.assertEqual(self.employee.emp_code, '23022')

    def test_payroll_shared_fields_follow_employee_master(self):
        payroll = PayrollEmployee.objects.create(
            employee=self.employee,
            user=self.user,
            employee_no='OLD-23022',
            full_name='Different Payroll Name',
            department='Different Department',
            designation='Different Title',
        )
        PayrollEmployee.objects.filter(pk=payroll.pk).update(
            employee_no='OLD-23022',
            full_name='Different Payroll Name',
            department='Different Department',
            designation='Different Title',
        )
        payroll.refresh_from_db()

        data = PayrollEmployeeSerializer(payroll).data

        self.assertEqual(data['employee_no'], self.employee.employee_number)
        self.assertEqual(data['full_name'], self.employee.get_full_name())
        self.assertEqual(data['department'], self.employee.department)
        self.assertEqual(data['designation'], self.employee.designation)

        serializer = PayrollEmployeeSerializer(
            payroll,
            data={
                'full_name': 'Conflicting Payroll Name',
                'department': 'Conflicting Department',
                'designation': 'Conflicting Title',
            },
            partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        payroll.refresh_from_db()
        self.assertEqual(payroll.full_name, self.employee.get_full_name())
        self.assertEqual(payroll.department, self.employee.department)
        self.assertEqual(payroll.designation, self.employee.designation)

    def test_detail_serializer_labels_all_identity_keys(self):
        identity = UserProfileSerializer(self.profile).data['canonical_identity']

        self.assertEqual(identity['access_profile_uuid'], str(self.profile.pk))
        self.assertEqual(identity['login_account_id'], self.user.pk)
        self.assertEqual(identity['employee_uuid'], str(self.employee.pk))
        self.assertEqual(identity['employee_number'], self.employee.employee_number)
