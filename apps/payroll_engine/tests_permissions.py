from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.payroll_engine.views import _user_has_any_role, _user_role_codes
from apps.rbac.models import Organization, Role, UserProfile, UserRole


User = get_user_model()


class PayrollRolePermissionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='payroll-admin',
            email='payroll-admin@example.com',
            is_staff=False,
            is_superuser=False,
        )
        organization = Organization.objects.create(name='Rejlers', code='REJLERS')
        self.profile = UserProfile.objects.create(
            user=self.user,
            organization=organization,
        )

    def test_reads_role_codes_from_rbac_profile(self):
        role, _ = Role.objects.update_or_create(
            code='super_admin',
            defaults={
                'name': 'Super Administrator',
                'level': 1,
                'is_active': True,
            },
        )
        UserRole.objects.create(user_profile=self.profile, role=role)

        self.assertIn('super_admin', _user_role_codes(self.user))
        self.assertTrue(_user_has_any_role(self.user, {'super_admin'}))

    def test_inactive_rbac_role_does_not_grant_payroll_write(self):
        role, _ = Role.objects.update_or_create(
            code='admin',
            defaults={
                'name': 'Inactive Payroll Administrator',
                'level': 2,
                'is_active': False,
            },
        )
        UserRole.objects.create(user_profile=self.profile, role=role)

        self.assertNotIn('admin', _user_role_codes(self.user))
        self.assertFalse(_user_has_any_role(self.user, {'admin'}))
