"""Organization choices are metadata and never rewrite access grants."""

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.hr_core.models import EmployeeMaster
from apps.rbac.constants import DEPARTMENTS, get_department_choices, get_department_label
from apps.rbac.models import (
    Module, Organization, Permission, Role, RoleModule, RolePermission,
    UserProfile, UserRole,
)
from apps.rbac.organization_catalog import (
    get_organization_catalog, get_organizational_job_titles, get_organizational_role_choices,
)
from apps.rbac.route_guard import secure_module_endpoints
from apps.rbac.views import UserProfileViewSet


router = DefaultRouter()
router.register('users', UserProfileViewSet, basename='organization-test-user')
urlpatterns = [path('api/v1/rbac/', include(router.urls))]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class OrganizationCatalogApiTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Catalog tests', code='ORG-CATALOG')
        self.employee = self.make_user('catalog-employee')
        self.admin = self.make_user('catalog-admin', is_superuser=True)
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

        self.profile = UserProfile.objects.get(user=self.employee)
        self.profile.department = 'Historical department'
        self.profile.job_title = 'Historical position'
        self.profile.save(update_fields=['department', 'job_title'])

        self.role = Role.objects.create(name='Existing catalog-test access', code='catalog_existing')
        self.module = Module.objects.create(name='Existing catalog-test module', code='catalog_existing')
        self.permission, _ = Permission.objects.get_or_create(
            module=self.module, code='catalog_existing.read',
            defaults={'name': 'Read existing test module', 'action': 'read'},
        )
        management_module, _ = Module.objects.get_or_create(
            code='user_mgmt', defaults={'name': 'User Management'},
        )
        Permission.objects.get_or_create(
            module=management_module, code='user_mgmt.read',
            defaults={'name': 'Read user management', 'action': 'read'},
        )
        RoleModule.objects.create(role=self.role, module=self.module)
        RolePermission.objects.create(role=self.role, permission=self.permission)
        UserRole.objects.create(user_profile=self.profile, role=self.role, is_primary=True)

    def make_user(self, name, **kwargs):
        user = get_user_model().objects.create_user(username=name, email=f'{name}@example.test', **kwargs)
        profile, _ = UserProfile.objects.get_or_create(
            user=user, defaults={'organization': self.organization},
        )
        UserRole.objects.filter(user_profile=profile).delete()
        return user

    @staticmethod
    def snapshot():
        return {
            model._meta.label: list(model.objects.order_by('pk').values())
            for model in (
                Role, Module, Permission, RoleModule, RolePermission, UserRole,
                UserProfile, EmployeeMaster, get_user_model(),
            )
        }

    def test_catalog_is_available_without_user_management_grants(self):
        self.client.force_authenticate(self.employee)

        response = self.client.get('/api/v1/rbac/users/organization-catalog/')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data, get_organization_catalog())
        self.assertTrue(response.data['departments'])
        self.assertTrue(response.data['organizational_roles'])
        # Access to organizational metadata does not grant employee administration.
        self.assertEqual(self.client.get('/api/v1/rbac/users/').status_code, 403)
        self.assertEqual(self.client.get('/api/v1/rbac/users/departments/').status_code, 403)
        self.assertEqual(self.client.get('/api/v1/rbac/users/job-titles/').status_code, 403)

    def test_catalog_requires_authentication_and_rejects_writes(self):
        self.client.force_authenticate(user=None)
        response = self.client.get('/api/v1/rbac/users/organization-catalog/')
        self.assertIn(response.status_code, (401, 403))

        self.client.force_authenticate(self.employee)
        for method in ('post', 'patch', 'put', 'delete'):
            with self.subTest(method=method):
                response = getattr(self.client, method)(
                    '/api/v1/rbac/users/organization-catalog/', {}, format='json',
                )
                self.assertIn(response.status_code, (403, 405))

    def test_department_choices_preserve_the_existing_option_contract(self):
        response = self.client.get('/api/v1/rbac/users/department-choices/')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['departments'], get_department_choices())
        self.assertEqual(response.data['count'], len(DEPARTMENTS))
        self.assertEqual(response.data['source'], get_organization_catalog()['source'])
        self.assertEqual(response.data['organizational_roles'], get_organizational_role_choices())
        for value, label in DEPARTMENTS:
            self.assertEqual(get_department_label(value), label)
        self.assertEqual(get_department_label('Unlisted historical department'), 'Unlisted historical department')

    def test_filter_options_include_chart_labels_and_existing_values(self):
        departments = self.client.get('/api/v1/rbac/users/departments/')
        titles = self.client.get('/api/v1/rbac/users/job-titles/')

        for response, key in ((departments, 'departments'), (titles, 'job_titles')):
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(set(response.data), {key, 'count'})
            self.assertEqual(response.data['count'], len(response.data[key]))
            self.assertEqual(len(response.data[key]), len(set(response.data[key])))
            self.assertEqual(response.data[key], sorted(response.data[key], key=str.casefold))
        self.assertIn('Historical department', departments.data['departments'])
        self.assertIn('Historical position', titles.data['job_titles'])
        self.assertTrue(
            {item['label'] for item in get_department_choices()}
            <= set(departments.data['departments']),
        )
        self.assertTrue(
            set(get_organizational_job_titles()) <= set(titles.data['job_titles']),
        )

    def test_reading_all_options_preserves_accounts_employees_and_access_grants(self):
        before = self.snapshot()
        with CaptureQueriesContext(connection) as queries:
            for endpoint in ('organization-catalog', 'department-choices', 'departments', 'job-titles'):
                response = self.client.get(f'/api/v1/rbac/users/{endpoint}/')
                self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.snapshot(), before)
        for query in queries:
            self.assertNotRegex(query['sql'].lstrip().upper(), r'^(INSERT|UPDATE|DELETE|REPLACE|ALTER|CREATE|DROP)\b')
