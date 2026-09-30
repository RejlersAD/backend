"""Real permission/row-scope checks and safe CSV export of explicit selections."""

import csv
from datetime import date
from decimal import Decimal
from io import StringIO
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.rbac.action_policy import module_action_allowed
from apps.rbac.models import Permission, RolePermission, UserPermissionOverride
from apps.rbac.route_guard import secure_module_endpoints
from apps.sales.models import Client, Deal
from apps.sales.views import DealViewSet
from .access_fixtures import grant_sales_actions


router = DefaultRouter()
router.register('deals', DealViewSet, basename='register-export-deals')
urlpatterns = [path('api/v1/sales/', include(router.urls))]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class OpportunityRegisterExportTests(TestCase):
    url = '/api/v1/sales/deals/export/'

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.actor = get_user_model().objects.create_user(
            username='register-exporter', email='register-exporter@example.test',
        )
        grant_sales_actions(self.actor, 'sales_opportunities')
        self.customer = Client.objects.create(
            client_code='EXPORT-CLIENT', company_name='Synthetic Buyer',
            industry_type='other', account_manager=self.actor,
        )
        self.deal = Deal.objects.create(
            deal_code='Q-102101', deal_name='Engineering, review\nphase two',
            client=self.customer, owner=self.actor, opportunity_type='rfq',
            service_categories=['engineering_design', 'project_management'],
            submission_due_date=date(2026, 10, 2), estimated_value=Decimal('125000.50'),
            currency='AED', next_action='Review scope',
            custom_fields={'private_source_payload': 'NOT_AN_EXPORT_COLUMN'},
        )
        self.client = APIClient()
        self.client.force_authenticate(self.actor)

    def export(self, *identifiers):
        return self.client.post(self.url, {
            'ids': ','.join(str(identifier) for identifier in (identifiers or (self.deal.pk,))),
        }, format='json')

    def csv_rows(self, response):
        return list(csv.DictReader(StringIO(response.content.decode('utf-8-sig'))))

    def test_exports_exact_selection_in_requested_order_with_attachment_headers(self):
        second = Deal.objects.create(
            deal_code='Q-102102', deal_name='Unknown commercial facts',
            client=self.customer, owner=self.actor,
        )
        response = self.export(second.pk, self.deal.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        self.assertRegex(response['Content-Disposition'], r'^attachment; filename="opportunities-\d{4}-\d{2}-\d{2}\.csv"$')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        rows = self.csv_rows(response)
        self.assertEqual([row['VF code'] for row in rows], ['Q-102102', 'Q-102101'])
        self.assertEqual(rows[1]['Title'], 'Engineering, review\nphase two')
        self.assertEqual(rows[1]['Client'], 'Synthetic Buyer')
        self.assertEqual(rows[1]['Type'], 'RFQ')
        self.assertEqual(rows[1]['Service line'], 'Engineering & Design; Project Management')
        self.assertEqual(rows[1]['Submission deadline'], '2026-10-02')
        self.assertEqual(rows[1]['Owner'], 'register-exporter')
        self.assertEqual(rows[1]['Estimated value'], '125000.50')
        self.assertEqual(rows[1]['Currency'], 'AED')
        self.assertEqual(rows[1]['Win probability (%)'], '10')
        self.assertEqual(rows[1]['Stage'], 'Open')
        self.assertEqual(rows[1]['Bid decision'], 'Pending')
        self.assertEqual(rows[1]['Next action'], 'Review scope')
        self.assertEqual(len(rows[1]), 13)
        self.assertNotIn('NOT_AN_EXPORT_COLUMN', response.content.decode('utf-8-sig'))
        selected = self.csv_rows(self.export(self.deal.pk))
        self.assertEqual([row['VF code'] for row in selected], ['Q-102101'])

    def test_preserves_unknowns_and_known_zero_without_currency_fabrication(self):
        Deal.objects.filter(pk=self.deal.pk).update(
            estimated_value=None, currency='', submission_due_date=None,
            service_categories=[], opportunity_type='',
        )
        row = self.csv_rows(self.export())[0]
        for field in ('Estimated value', 'Currency', 'Submission deadline', 'Service line', 'Type'):
            self.assertEqual(row[field], '')
        Deal.objects.filter(pk=self.deal.pk).update(estimated_value=Decimal('0.00'))
        self.assertEqual(self.csv_rows(self.export())[0]['Estimated value'], '0.00')

    def test_export_grant_is_required_independently_of_read(self):
        RolePermission.objects.filter(
            permission__module__code='sales_opportunities', permission__action='export',
        ).delete()
        self.assertTrue(module_action_allowed(self.actor, 'sales_opportunities', 'read'))
        response = self.export()
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('Content-Disposition', response)

    def test_read_grant_is_required_independently_of_export(self):
        RolePermission.objects.filter(
            permission__module__code='sales_opportunities', permission__action='read',
        ).delete()
        self.assertTrue(module_action_allowed(self.actor, 'sales_opportunities', 'export'))
        self.assertEqual(self.export().status_code, 403)

    def test_post_export_does_not_require_create_permission(self):
        RolePermission.objects.filter(
            permission__module__code='sales_opportunities', permission__action='create',
        ).delete()
        self.assertFalse(module_action_allowed(self.actor, 'sales_opportunities', 'create'))
        self.assertEqual(self.export().status_code, 200)

    def test_explicit_export_denial_overrides_role_grant(self):
        permission = Permission.objects.get(module__code='sales_opportunities', action='export', is_active=True)
        UserPermissionOverride.objects.create(
            user_profile=self.actor.rbac_profile, permission=permission, allowed=False,
        )
        self.assertEqual(self.export().status_code, 403)

    def test_inaccessible_or_missing_selection_denies_every_row_without_disclosure(self):
        outsider = get_user_model().objects.create_user(
            username='outside-export-scope', email='outside-export-scope@example.test',
        )
        hidden = Deal.objects.create(
            deal_code='HIDDEN-VF', deal_name='Private opportunity title',
            client=self.customer, owner=outsider,
        )
        hidden_response = self.export(self.deal.pk, hidden.pk)
        missing_response = self.export(self.deal.pk, uuid4())
        self.assertEqual(hidden_response.status_code, 403)
        self.assertEqual(missing_response.status_code, 403)
        self.assertEqual(hidden_response.data, missing_response.data)
        self.assertNotIn('Content-Disposition', hidden_response)
        text = hidden_response.content.decode()
        for private_value in ('Q-102101', 'HIDDEN-VF', 'Private opportunity title', str(hidden.pk)):
            self.assertNotIn(private_value, text)

    def test_formula_prefixes_are_literal_text_even_with_leading_controls(self):
        for value in ('=SUM(1,2)', '+1+2', '-1+2', '@SUM(A1)', '\t=1+2', ' \r=1+2', '\ufeff=1+2', '\x00=1+2'):
            with self.subTest(value=repr(value)):
                Deal.objects.filter(pk=self.deal.pk).update(deal_name=value)
                response = self.export()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.csv_rows(response)[0]['Title'], "'" + value)

    def test_formula_protection_applies_to_all_untrusted_text_columns(self):
        self.customer.company_name = '+CLIENT'
        self.customer.save(update_fields=['company_name'])
        self.actor.first_name = '@OWNER'
        self.actor.save(update_fields=['first_name'])
        Deal.objects.filter(pk=self.deal.pk).update(
            deal_code='=VF', opportunity_type='+TYPE', service_categories=['-SERVICE'],
            currency='=CURRENCY', stage='+STAGE', bid_decision='-DECISION', next_action=' \t=NEXT',
        )
        row = self.csv_rows(self.export())[0]
        for field in ('VF code', 'Client', 'Type', 'Service line', 'Owner', 'Currency', 'Stage', 'Bid decision', 'Next action'):
            self.assertTrue(row[field].startswith("'"), (field, row[field]))

    def test_invalid_empty_duplicate_and_excessive_ids_are_rejected(self):
        identifier = str(self.deal.pk)
        invalid = (
            {}, {'ids': ''}, {'ids': 'not-a-uuid'}, {'ids': [identifier]},
            {'ids': 10}, {'ids': identifier + ','}, {'ids': identifier + ',' + identifier},
            {'ids': identifier, 'all': True}, {'ids': ','.join([identifier] * 10001)},
        )
        for payload in invalid:
            with self.subTest(payload_type=type(payload.get('ids')).__name__):
                response = self.client.post(self.url, payload, format='json')
                self.assertEqual(response.status_code, 400)
                self.assertNotIn('Content-Disposition', response)
        self.assertEqual(self.client.post(self.url + '?ids=' + identifier, {}, format='json').status_code, 400)

    def test_anonymous_export_is_denied_and_get_does_not_download(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.client.force_authenticate(None)
        self.assertEqual(self.export().status_code, 401)

    def test_list_exposes_actual_service_categories(self):
        response = self.client.get('/api/v1/sales/deals/')
        self.assertEqual(response.status_code, 200)
        row = next(row for row in response.data['results'] if row['id'] == str(self.deal.pk))
        self.assertEqual(row['service_categories'], ['engineering_design', 'project_management'])

    @patch('rest_framework.pagination.PageNumberPagination.page_size', 2)
    def test_register_pages_follow_requested_unique_code_and_id_ordering(self):
        identifiers = [str(self.deal.pk)]
        for code in ('Q-102103', 'Q-102102'):
            deal = Deal.objects.create(
                deal_code=code, deal_name='Pagination fixture', client=self.customer, owner=self.actor,
            )
            identifiers.append(str(deal.pk))
        responses = [self.client.get('/api/v1/sales/deals/', {
            'page': page, 'ordering': 'deal_code,id',
        }) for page in (1, 2)]
        for response in responses:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['count'], 3)
        self.assertEqual([
            row['deal_code'] for response in responses for row in response.data['results']
        ], ['Q-102101', 'Q-102102', 'Q-102103'])
        self.assertIsNotNone(responses[0].data['next'])
        self.assertIsNone(responses[1].data['next'])
        by_id = self.client.get('/api/v1/sales/deals/', {'ordering': 'id'})
        self.assertEqual([row['id'] for row in by_id.data['results']], sorted(identifiers)[:2])
