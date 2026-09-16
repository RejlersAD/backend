"""Procurement can search work identities without receiving private HR data."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.test import APIClient

from apps.hr_core.models import EmployeeMaster
from apps.rbac.models import (
    Module, Organization, Permission, Role, RoleModule, RolePermission,
    UserPermissionOverride, UserProfile, UserRole,
)
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints


urlpatterns = [path('api/v1/procurement/', include('apps.procurement.urls'))]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class ApprovalEmployeeDirectoryTests(TestCase):
    url = '/api/v1/procurement/po-documents/approval-employees/'

    def setUp(self):
        cache.clear()
        self.actor = get_user_model().objects.create_user('po-directory-user', email='po-reader@example.test')
        org, _ = Organization.objects.get_or_create(code='po-directory', defaults={'name': 'PO directory tests'})
        self.profile, _ = UserProfile.objects.get_or_create(user=self.actor, defaults={'organization': org})
        self.profile.roles.clear()
        self.role = Role.objects.create(code='po-directory-reader', name='PO reader', level=3)
        UserRole.objects.create(user_profile=self.profile, role=self.role)
        self.module, _ = Module.objects.get_or_create(code='procurement_orders', defaults={'name': 'Purchase orders'})
        ensure_module_actions(Module, Permission, module_ids=[self.module.pk])
        RoleModule.objects.create(role=self.role, module=self.module)
        for permission in self.module.permissions.filter(action='read', is_active=True):
            RolePermission.objects.create(role=self.role, permission=permission)
        self.client = APIClient()
        self.client.force_authenticate(self.actor)
        self.counter = 0

    def employee(self, **changes):
        self.counter += 1
        number = f'EMP-{self.counter:04d}'
        values = {
            'employee_number': number, 'employee_code': number, 'emp_code': number,
            'first_name': 'Alex', 'last_name': f'Employee {self.counter}',
            'join_date': date(2025, 1, 1), 'employment_status': 'active',
            'designation': 'Project Director', 'job_title_uae': 'UAE title',
            'job_title_finland': 'Finland title', 'department': 'Engineering',
        }
        values.update(changes)
        return EmployeeMaster.objects.create(**values)

    def search(self, **params):
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_order_reader_without_hr_access_receives_only_minimal_canonical_identity(self):
        employee = self.employee(
            first_name='Maria', last_name='Master', email='private-email@example.test',
            current_base_salary=Decimal('99999.00'), phone_number='private-phone',
            address='private-address', bank_account_number='private-bank',
            iban='private-iban', tax_id='private-tax-id', date_of_birth=date(1980, 2, 3),
        )
        before = EmployeeMaster.objects.filter(pk=employee.pk).values().get()
        data = self.search(search='Maria Master')
        self.assertEqual(data['results'], [{
            'id': str(employee.pk), 'name': 'Maria Master', 'position': 'Project Director',
            'employee_number': employee.employee_number, 'department': 'Engineering', 'linked_user_id': None,
        }])
        self.assertEqual(data['count'], 1)
        self.assertFalse(data['has_more'])
        self.assertEqual(list(self.role.modules.values_list('code', flat=True)), ['procurement_orders'])
        self.assertEqual(EmployeeMaster.objects.filter(pk=employee.pk).values().get(), before)
        self.assertNotIn('private-', str(data))
        self.assertNotIn('99999', str(data))

    def test_hr_only_active_probation_and_notice_period_employees_are_searchable(self):
        eligible = [self.employee(employment_status=status) for status in ('active', 'probation', 'notice_period')]
        self.employee(employment_status='exited')
        self.employee(employment_status='suspended')
        self.employee(protected_identity=True)
        self.employee(is_test_person=True)
        self.employee(first_name='', last_name='')
        results = self.search()['results']
        self.assertEqual({row['id'] for row in results}, {str(employee.pk) for employee in eligible})
        self.assertTrue(all(row['linked_user_id'] is None for row in results))

    def test_linked_employee_uses_hr_name_and_title_instead_of_stale_user_or_rbac_values(self):
        linked_user = get_user_model().objects.create_user('linked-signer', email='linked@example.test')
        linked_profile, _ = UserProfile.objects.get_or_create(
            user=linked_user, defaults={'organization': self.profile.organization},
        )
        employee = self.employee(
            user=linked_user, email=linked_user.email, first_name='Canonical', last_name='Signer',
            designation='Vice President, Project Delivery',
        )
        # Represent a stale compatibility record without invoking sync signals.
        get_user_model().objects.filter(pk=linked_user.pk).update(first_name='Stale', last_name='User', is_active=False)
        UserProfile.objects.filter(pk=linked_profile.pk).update(job_title='Unrelated RBAC title')
        row = self.search(search='Canonical Signer')['results'][0]
        self.assertEqual(row['id'], str(employee.pk))
        self.assertEqual(row['linked_user_id'], str(linked_user.pk))
        self.assertEqual(row['name'], 'Canonical Signer')
        self.assertEqual(row['position'], 'Vice President, Project Delivery')

    def test_full_name_preferred_name_and_employee_number_search_handle_multiple_terms(self):
        target = self.employee(first_name='Ossi Akseli', last_name='Valto', preferred_given_name='Ossi', employee_number='RAD-0042')
        self.employee(first_name='Ossi', last_name='Other')
        for term in ('ossi akseli valto', '  VALTO   Ossi ', 'RAD-0042', '0042'):
            with self.subTest(search=term):
                self.assertEqual([row['id'] for row in self.search(search=term)['results']], [str(target.pk)])
        preferred = self.employee(first_name='Alexander', last_name='Example', preferred_given_name='Sasha')
        self.assertEqual(self.search(search='Sasha Example')['results'][0]['id'], str(preferred.pk))
        self.assertEqual(self.search(search='Sasha Example')['results'][0]['name'], 'Alexander Example')
        self.assertEqual(self.search(search='No Matching Employee')['results'], [])

    def test_position_uses_hr_designation_then_uae_then_finland_and_can_remain_blank(self):
        cases = (
            ({}, 'Project Director'),
            ({'designation': '   '}, 'UAE title'),
            ({'designation': '', 'job_title_uae': ''}, 'Finland title'),
            ({'designation': '', 'job_title_uae': '', 'job_title_finland': ''}, ''),
        )
        for fields, expected in cases:
            employee = self.employee(**fields)
            with self.subTest(fields=fields):
                self.assertEqual(self.search(search=employee.employee_number)['results'][0]['position'], expected)

    def test_pagination_is_bounded_and_stably_ordered_without_duplicate_rows(self):
        EmployeeMaster.objects.bulk_create([
            EmployeeMaster(
                employee_number=f'PAGE-{index:03d}', employee_code=f'PAGE-{index:03d}', emp_code=f'PAGE-{index:03d}',
                first_name='Paged', last_name=f'Employee {index:03d}', join_date=date(2025, 1, 1),
            ) for index in range(55)
        ])
        default = self.search(search='Paged')
        self.assertEqual(len(default['results']), 20)
        self.assertEqual(default['count'], 55)
        first = self.search(search='Paged', page_size=50)
        second = self.search(search='Paged', page=2, page_size=50)
        self.assertEqual(len(first['results']), 50)
        self.assertTrue(first['has_more'])
        self.assertEqual(len(second['results']), 5)
        self.assertFalse(second['has_more'])
        self.assertFalse({row['id'] for row in first['results']} & {row['id'] for row in second['results']})
        self.assertEqual(first, self.search(search='Paged', page_size=50))
        self.assertEqual(self.search(search='Paged', page=9, page_size=50)['results'], [])

    def test_numbered_import_placeholders_fall_back_to_real_hr_titles_or_remain_blank(self):
        cases = (
            ({'designation': 'Designation-1', 'job_title_uae': 'Chief Executive Officer'}, 'Chief Executive Officer'),
            ({'designation': 'designation_12', 'job_title_uae': 'Designation 3', 'job_title_finland': 'President'}, 'President'),
            ({'designation': 'Designation-1', 'job_title_uae': 'Designation-1', 'job_title_finland': ''}, ''),
            ({'designation': 'Designation Engineer'}, 'Designation Engineer'),
        )
        for fields, expected in cases:
            employee = self.employee(**fields)
            with self.subTest(fields=fields):
                self.assertEqual(self.search(search=employee.employee_number)['results'][0]['position'], expected)

    def test_invalid_search_and_unbounded_pagination_are_rejected(self):
        for params in (
            {'search': 'x' * 201}, {'page': 0}, {'page': 'invalid'}, {'page': 10001},
            {'page_size': 0}, {'page_size': 51}, {'page_size': -1}, {'page_size': 'all'},
        ):
            with self.subTest(params=params):
                response = self.client.get(self.url, params)
                self.assertEqual(response.status_code, 400, response.data)

    def test_missing_read_grant_and_explicit_read_deny_block_directory(self):
        self.employee()
        RolePermission.objects.filter(role=self.role).delete()
        cache.clear()
        self.assertEqual(self.client.get(self.url).status_code, 403)
        for permission in self.module.permissions.filter(action='read', is_active=True):
            RolePermission.objects.create(role=self.role, permission=permission)
            UserPermissionOverride.objects.create(user_profile=self.profile, permission=permission, allowed=False)
        cache.clear()
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_anonymous_or_nonprocurement_users_cannot_browse_employees(self):
        self.employee()
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.url).status_code, (401, 403))
        self.client.force_authenticate(self.actor)
        RoleModule.objects.filter(role=self.role).delete()
        cache.clear()
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_directory_has_no_write_action(self):
        employee = self.employee()
        before = EmployeeMaster.objects.filter(pk=employee.pk).values().get()
        response = self.client.post(self.url, {'id': str(employee.pk), 'designation': 'Forged position'}, format='json')
        self.assertIn(response.status_code, (403, 405))
        self.assertEqual(EmployeeMaster.objects.filter(pk=employee.pk).values().get(), before)
