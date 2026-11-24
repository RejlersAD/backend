"""Employee separation (resignation / termination) aligned with offboarding."""
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.hr_core.models import EmployeeMaster
from apps.onboarding.models import OffboardingRecord
from apps.onboarding.separation import close_separated_account, separate_employee
from apps.onboarding.views import OffboardingRecordViewSet
from apps.rbac.models import AuditLog, Organization, Role, UserProfile, UserRole


class SeparationTests(TestCase):
    def setUp(self):
        users = get_user_model().objects
        org = Organization.objects.create(name='Rejlers', code='REJ-SEP')
        self.hr = users.create_user(username='hr-sep', email='hr-sep@example.test')
        self.employee = users.create_user(username='leaver', email='leaver@example.test', first_name='Lee', last_name='Ver')
        self.colleague = users.create_user(username='colleague-sep', email='colleague-sep@example.test')
        role, _ = Role.objects.update_or_create(code='hr_manager', defaults={'name': 'HR Manager', 'level': 3, 'is_active': True})
        UserRole.objects.create(user_profile=UserProfile.objects.create(user=self.hr, organization=org, status='active'), role=role)
        UserProfile.objects.create(user=self.employee, organization=org, status='active')
        UserProfile.objects.create(user=self.colleague, organization=org, status='active')
        self.record = EmployeeMaster.objects.create(
            user=self.employee, first_name='Lee', last_name='Ver', email='leaver@example.test', employee_number='SEP1',
            employee_code='SEP1', emp_code='SEP1', designation='Piping Engineer', department='Piping', join_date=date(2021, 1, 1))
        self.last_day = timezone.localdate() + timedelta(days=30)
        self.notify = patch('apps.onboarding.separation.NotificationService.create_notification').start()
        self.addCleanup(patch.stopall)

    def separate(self, **kwargs):
        values = {'actor': self.hr, 'user': self.employee, 'separation_type': 'resignation',
                  'last_working_day': self.last_day, 'reason': '', **kwargs}
        with self.captureOnCommitCallbacks(execute=True):
            return separate_employee(**values)

    def test_termination_deactivates_now_and_opens_offboarding(self):
        record, created, deactivated = self.separate(separation_type='termination', reason='Gross misconduct')
        self.employee.refresh_from_db()
        self.record.refresh_from_db()
        self.assertTrue(created and deactivated)
        self.assertFalse(self.employee.is_active)
        self.assertEqual(self.employee.rbac_profile.status, 'inactive')
        self.assertEqual(self.record.employment_status, 'exited')  # not 'suspended'
        self.assertEqual(self.record.exit_date, self.last_day)
        self.assertEqual(record.exit_reason, 'termination')
        self.assertEqual(record.position, 'Piping Engineer')
        self.assertTrue(AuditLog.objects.filter(resource_type='EmployeeSeparation', metadata__event='separation_recorded').exists())
        self.assertEqual([c.kwargs['recipient'] for c in self.notify.call_args_list], [self.hr])
        self.assertIn('Termination: Lee Ver', self.notify.call_args.kwargs['title'])

    def test_resignation_keeps_access_until_offboarding_completes(self):
        record, created, deactivated = self.separate()
        self.employee.refresh_from_db()
        self.record.refresh_from_db()
        self.assertTrue(created)
        self.assertFalse(deactivated)
        self.assertTrue(self.employee.is_active)
        self.assertEqual(self.record.employment_status, 'notice_period')
        with self.captureOnCommitCallbacks(execute=True):
            self.assertTrue(close_separated_account(record, actor=self.hr))
        self.employee.refresh_from_db()
        self.record.refresh_from_db()
        self.assertFalse(self.employee.is_active)
        self.assertEqual(self.record.employment_status, 'exited')

    def test_existing_open_offboarding_case_is_reused(self):
        first, _, _ = self.separate()
        second, created, _ = self.separate(separation_type='termination', reason='Policy breach')
        self.assertFalse(created)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(OffboardingRecord.objects.filter(user=self.employee).count(), 1)
        second.refresh_from_db()
        self.assertEqual(second.exit_reason, 'termination')

    def test_override_resignation_can_deactivate_now(self):
        _, _, deactivated = self.separate(deactivate_now=True)
        self.assertTrue(deactivated)

    def test_validation(self):
        for kwargs in ({'separation_type': 'retired'}, {'separation_type': 'termination', 'reason': ''},
                       {'user': self.hr}, {'last_working_day': None},
                       {'last_working_day': timezone.localdate() - timedelta(days=400)}):
            with self.assertRaises(ValidationError):
                self.separate(**kwargs)
        self.assertFalse(OffboardingRecord.objects.exists())
        self.employee.refresh_from_db()
        self.assertTrue(self.employee.is_active)

    def call_api(self, actor, payload):
        request = APIRequestFactory().post('/offboarding/separate/', payload, format='json')
        force_authenticate(request, actor)
        with self.captureOnCommitCallbacks(execute=True):
            return OffboardingRecordViewSet.as_view({'post': 'separate'})(request)

    def test_api_requires_hr_and_validates_date(self):
        payload = {'user_id': self.employee.pk, 'separation_type': 'termination',
                   'last_working_day': str(self.last_day), 'reason': 'Contract breach'}
        self.assertEqual(self.call_api(self.colleague, payload).status_code, 403)
        self.assertEqual(self.call_api(self.hr, {**payload, 'last_working_day': '24/11/2026'}).status_code, 400)
        response = self.call_api(self.hr, payload)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertFalse(response.data['account_active'])
        self.assertTrue(response.data['offboarding_created'])
