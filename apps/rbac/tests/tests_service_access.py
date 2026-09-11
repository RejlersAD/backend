from importlib import import_module
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from apps.core.config.enquiry_access_config import user_has_enquiry_access
from apps.core.models import Enquiry
from apps.finance.views import InvoiceViewSet, dashboard_stats
from apps.rbac.models import Module, Organization, Role, RoleModule, UserProfile, UserRole, _sync_module_catalogue
from apps.rbac.rbac_config import ALL_MODULES_CATALOGUE, DEFAULT_ROLE_MODULES
from apps.rbac.service_catalogue import SERVICE_MODULES, VIEW_SERVICE_MODULES


class ServiceAccessTests(TestCase):
    def setUp(self):
        cache.clear()
        _sync_module_catalogue()
        self.user = get_user_model().objects.create_user('service-reader', email='reader@example.test', password='test-only')
        org, _ = Organization.objects.get_or_create(code='SERVICE-TEST', defaults={'name': 'Service Test'})
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'organization': org})
        UserRole.objects.filter(user_profile=self.profile).delete()
        self.role = Role.objects.create(name='Service reader', code='service_reader_test', level=4)
        UserRole.objects.create(user_profile=self.profile, role=self.role)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.factory = APIRequestFactory()

    def grant(self, code):
        return RoleModule.objects.get_or_create(role=self.role, module=Module.objects.get(code=code))[0]

    def finance(self, action='list', method='get'):
        request = getattr(self.factory, method)('/api/v1/finance/invoices/')
        force_authenticate(request, self.user)
        return InvoiceViewSet.as_view({method: action})(request)

    def test_catalogue_is_complete_unique_and_idempotent(self):
        count = Module.objects.count()
        _sync_module_catalogue()
        self.assertEqual(count, Module.objects.count())
        codes = [item['code'] for item in ALL_MODULES_CATALOGUE]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertIn('ai_champion', codes)
        self.assertTrue(set(VIEW_SERVICE_MODULES.values()).issubset(codes))
        self.assertNotIn('enquiry_management', DEFAULT_ROLE_MODULES)
        self.assertTrue(all(Module.objects.filter(code=code, is_active=True).exists() for code, *_ in SERVICE_MODULES))

    def test_child_grant_does_not_grant_parent_or_sibling(self):
        self.grant('finance_outgoing')
        self.assertTrue(self.profile.has_module_access('finance_outgoing'))
        self.assertFalse(self.profile.has_module_access('finance'))
        self.assertFalse(self.profile.has_module_access('finance_incoming'))
        self.assertEqual(self.client.get('/api/v1/invoice-tracker/invoices/').status_code, 200)
        self.assertEqual(self.finance().status_code, 403)
        self.assertEqual(self.finance('create', 'post').status_code, 403)

    def test_broad_grants_are_expanded_then_retired(self):
        from django.db import connection
        Module.objects.get_or_create(code='finance', defaults={'name': 'Finance'})
        self.grant('finance')
        self.assertEqual(self.finance().status_code, 403)
        migration = import_module('apps.rbac.migrations.0052_replace_broad_business_grants')
        migration.replace_broad_grants(apps, SimpleNamespace(connection=connection))
        self.assertEqual(self.finance().status_code, 200)
        self.assertEqual(self.client.get('/api/v1/invoice-tracker/invoices/').status_code, 200)
        self.assertTrue(self.profile.has_module_access('finance_overview'))
        self.assertTrue(self.profile.has_module_access('finance_salary'))
        self.assertFalse(RoleModule.objects.filter(module__code='finance').exists())
        self.assertFalse(Module.objects.get(code='finance').is_active)
        self.grant('finance_incoming').delete()
        migration.replace_broad_grants(apps, SimpleNamespace(connection=connection))
        self.assertEqual(self.finance().status_code, 403)

    def test_finance_overview_cannot_open_or_mutate_invoice_register(self):
        self.grant('finance_overview')
        self.assertEqual(self.finance('combined_summary').status_code, 200)
        self.assertEqual(self.finance().status_code, 403)
        self.assertEqual(self.finance('create', 'post').status_code, 403)

    def test_salary_slips_require_their_own_selection(self):
        from apps.finance.salary_views import SalarySlipViewSet
        from apps.rbac.permissions import HasModuleAccess
        view = SalarySlipViewSet()
        request = SimpleNamespace(user=self.user)
        self.grant('finance_overview')
        self.assertFalse(HasModuleAccess().has_permission(request, view))
        self.grant('finance_salary')
        self.assertTrue(HasModuleAccess().has_permission(request, view))
        self.assertEqual(self.client.get('/api/v1/invoice-tracker/invoices/').status_code, 403)

    def test_finance_stats_denies_unassigned_user(self):
        request = self.factory.get('/api/v1/finance/dashboard/stats/')
        force_authenticate(request, self.user)
        self.assertEqual(dashboard_stats(request).status_code, 403)

    def test_invoice_preview_does_not_bypass_service_grant(self):
        request = self.factory.get('/api/v1/finance/invoices/preview/')
        force_authenticate(request, self.user)
        response = InvoiceViewSet.as_view({'get': 'preview'})(request, pk='00000000-0000-0000-0000-000000000001')
        self.assertEqual(response.status_code, 403)

    def test_sales_service_grant_is_enforced_on_get_and_post(self):
        self.grant('sales_clients')
        self.assertEqual(self.client.get('/api/v1/sales/clients/').status_code, 200)
        self.assertEqual(self.client.get('/api/v1/sales/deals/').status_code, 403)
        self.assertEqual(self.client.post('/api/v1/sales/deals/', {}, format='json').status_code, 403)
        self.assertEqual(self.client.get('/api/v1/sales/email-intakes/').status_code, 403)

    def test_disabled_roles_modules_and_revocation_fail_closed(self):
        grant = self.grant('sales_clients')
        self.profile.get_all_modules()  # Warm the permission cache.
        grant.delete()
        self.assertEqual(self.client.get('/api/v1/sales/clients/').status_code, 403)
        self.grant('sales_clients')
        self.role.is_active = False
        self.role.save()
        self.assertEqual(self.client.get('/api/v1/sales/clients/').status_code, 403)
        self.role.is_active = True
        self.role.save()
        Module.objects.filter(code='sales_clients').update(is_active=False)
        self.assertEqual(self.client.get('/api/v1/sales/clients/').status_code, 403)

    def test_staff_is_not_a_service_permission(self):
        self.user.is_staff = True
        self.user.save()
        self.assertEqual(self.finance().status_code, 403)
        self.assertEqual(self.client.get('/api/v1/sales/clients/').status_code, 403)
        self.assertFalse(user_has_enquiry_access(self.user))

    def test_enquiry_admin_endpoints_deny_without_role_even_if_assigned(self):
        enquiry = Enquiry.objects.create(name='Requester', email='requester@example.test', subject='Private', message='Private enquiry', assigned_to=self.user)
        for path in ['', 'stats/', 'representatives/', f'{enquiry.pk}/']:
            with self.subTest(path=path):
                self.assertEqual(self.client.get('/api/v1/enquiries/' + path).status_code, 403)
        for suffix in ['respond/', 'escalate/', 'resolve/']:
            self.assertEqual(self.client.post(f'/api/v1/enquiries/{enquiry.pk}/{suffix}', {}, format='json').status_code, 403)
        self.assertEqual(self.client.patch(f'/api/v1/enquiries/{enquiry.pk}/', {}, format='json').status_code, 403)
        self.assertEqual(self.client.delete(f'/api/v1/enquiries/{enquiry.pk}/').status_code, 403)

    def test_explicit_enquiry_role_allows_and_revocation_denies(self):
        grant = self.grant('enquiry_management')
        self.assertEqual(self.client.get('/api/v1/enquiries/').status_code, 200)
        self.assertTrue(user_has_enquiry_access(self.user))
        grant.delete()
        self.assertEqual(self.client.get('/api/v1/enquiries/').status_code, 403)

    def test_requester_self_service_remains_scoped(self):
        own = Enquiry.objects.create(name='Self', email=self.user.email, subject='Own', message='Own enquiry', requester=self.user)
        other = Enquiry.objects.create(name='Other', email='other@example.test', subject='Other', message='Other enquiry')
        self.assertEqual(self.client.get('/api/v1/enquiries/mine/').status_code, 200)
        self.assertEqual(self.client.get(f'/api/v1/enquiries/mine/{own.pk}/').status_code, 200)
        self.assertEqual(self.client.get(f'/api/v1/enquiries/mine/{other.pk}/').status_code, 404)

    def test_email_identity_no_longer_bypasses_roles(self):
        self.user.email = 'radai@rejlers.ae'
        self.user.save()
        self.assertFalse(user_has_enquiry_access(self.user))

    def test_migration_only_revokes_historical_default_grant(self):
        default, _ = Role.objects.get_or_create(code='default', defaults={'name': 'Default', 'level': 6})
        module = Module.objects.get(code='enquiry_management')
        RoleModule.objects.get_or_create(role=default, module=module)
        self.grant('enquiry_management')
        migrate = import_module('apps.rbac.migrations.0051_business_services_and_enquiry_rbac').refresh_service_access
        migrate(apps, SimpleNamespace(connection=SimpleNamespace(alias='default')))
        migrate(apps, SimpleNamespace(connection=SimpleNamespace(alias='default')))
        self.assertFalse(RoleModule.objects.filter(role=default, module=module).exists())
        self.assertTrue(RoleModule.objects.filter(role=self.role, module=module).exists())
