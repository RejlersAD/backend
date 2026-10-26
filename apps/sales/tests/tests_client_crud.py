from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.test import APIClient
from apps.rbac.route_guard import secure_module_endpoints
from apps.sales.models import Client, Contact
from .access_fixtures import grant_sales_actions, deny_sales_action

urlpatterns = [path('api/v1/sales/', include('apps.sales.urls'))]
secure_module_endpoints(urlpatterns)
ROOT = '/api/v1/sales/clients/'


@override_settings(ROOT_URLCONF=__name__)
class ClientCrudTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(username='client-admin', email='admin@example.test', password='synthetic')
        self.owner = get_user_model().objects.create_user(username='account-owner', email='owner@example.test', first_name='Account', last_name='Owner')
        grant_sales_actions(self.actor, 'sales_clients')
        self.client = APIClient()
        self.client.force_authenticate(self.actor)
        self.payload = dict(company_name='Example Engineering', legal_name='Example Engineering LLC',
            industry_type='construction', city='Abu Dhabi', website='https://example.test',
            account_manager=self.owner.pk, country='United Arab Emirates', currency='usd')

    def test_create_read_update_delete_all_client_fields(self):
        created = self.client.post(ROOT, self.payload, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        pk = created.data['id']
        self.assertEqual(created.data['currency'], 'USD')
        detail = self.client.get(f'{ROOT}{pk}/')
        for field, value in self.payload.items():
            self.assertEqual(detail.data[field], value.upper() if field == 'currency' else value)
        listing = self.client.get(ROOT).data['results'][0]
        self.assertEqual(listing['currency'], 'USD')
        self.assertEqual(listing['city'], 'Abu Dhabi')
        update = {**self.payload, 'legal_name': 'Revised Legal LLC', 'industry_type': 'mining',
                  'city': 'Dubai', 'website': 'https://revised.example.test',
                  'account_manager': self.actor.pk, 'country': 'Oman', 'currency': 'aed'}
        result = self.client.patch(f'{ROOT}{pk}/', update, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['currency'], 'AED')
        self.assertEqual(result.data['industry_type'], 'mining')
        self.assertEqual(result.data['account_manager'], self.actor.pk)
        refreshed = self.client.get(f'{ROOT}{pk}/').data
        for field, value in update.items():
            self.assertEqual(refreshed[field], value.upper() if field == 'currency' else value)
        self.assertEqual(self.client.delete(f'{ROOT}{pk}/').status_code, 204)
        self.assertFalse(Client.objects.filter(pk=pk).exists())

    def test_form_options_match_backend_industry_choices_and_active_owners(self):
        self.owner.is_active = False
        self.owner.save(update_fields=['is_active'])
        response = self.client.get(f'{ROOT}form-options/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn('mining', [item['value'] for item in response.data['industries']])
        self.assertNotIn(self.owner.pk, [item['value'] for item in response.data['owners']])
        result = self.client.post(ROOT, self.payload, format='json')
        self.assertEqual(result.status_code, 400, result.data)
        self.assertFalse(Client.objects.exists())

    def test_invalid_currency_website_and_duplicate_identity_do_not_write(self):
        for fields in ({'currency': 'US12'}, {'currency': '123'}, {'website': 'invalid website'}):
            result = self.client.post(ROOT, {**self.payload, **fields}, format='json')
            self.assertEqual(result.status_code, 400, result.data)
        self.assertFalse(Client.objects.exists())
        self.assertEqual(self.client.post(ROOT, self.payload, format='json').status_code, 201)
        self.assertEqual(self.client.post(ROOT, self.payload, format='json').status_code, 400)

    def test_delete_linked_client_preserves_contacts_and_allows_inactivation(self):
        row = Client.objects.create(company_name='Linked', client_code='LINKED', industry_type='other', account_manager=self.actor)
        contact = Contact.objects.create(client=row, first_name='Existing', last_name='Contact', email='contact@example.test')
        response = self.client.delete(f'{ROOT}{row.pk}/')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertTrue(Contact.objects.filter(pk=contact.pk).exists())
        self.assertEqual(self.client.patch(f'{ROOT}{row.pk}/', {'status': 'inactive'}, format='json').status_code, 200)

    def test_crud_permissions_and_owner_scope_are_enforced(self):
        limited = get_user_model().objects.create_user(username='limited', email='limited@example.test')
        grant_sales_actions(limited, 'sales_clients')
        row = Client.objects.create(company_name='Retained', client_code='RETAINED', industry_type='other', account_manager=limited)
        self.client.force_authenticate(limited)
        with patch('apps.sales.client_forms.build_visibility_filter', return_value=Q(owner=limited)):
            options = self.client.get(f'{ROOT}form-options/')
            self.assertEqual([item['value'] for item in options.data['owners']], [limited.pk])
            self.assertEqual(self.client.patch(f'{ROOT}{row.pk}/', {'account_manager': self.owner.pk}, format='json').status_code, 400)
        for verb, method in [('create', 'post'), ('update', 'patch'), ('delete', 'delete')]:
            deny_sales_action(limited, 'sales_clients', verb)
            url = ROOT if method == 'post' else f'{ROOT}{row.pk}/'
            response = getattr(self.client, method)(url, self.payload if method == 'post' else {}, format='json')
            self.assertEqual(response.status_code, 403, response.data)
        self.assertTrue(Client.objects.filter(pk=row.pk).exists())
