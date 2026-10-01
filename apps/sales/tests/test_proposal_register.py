"""Real register projections, stable pages and guarded explicit CSV selections."""

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
from django.utils import timezone
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.rbac.action_policy import module_action_allowed
from apps.rbac.models import Permission, RolePermission, UserPermissionOverride
from apps.rbac.route_guard import secure_module_endpoints
from apps.sales.models import Client, Deal, Quote
from apps.sales.views import QuoteViewSet
from .access_fixtures import grant_sales_actions


router = DefaultRouter()
router.register('quotes', QuoteViewSet, basename='proposal-register-quotes')
urlpatterns = [path('api/v1/sales/', include(router.urls))]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class ProposalRegisterTests(TestCase):
    url = '/api/v1/sales/quotes/'

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.actor = get_user_model().objects.create_user(
            username='proposal-exporter', email='proposal-exporter@example.test', first_name='Proposal', last_name='Preparer',
        )
        self.other = get_user_model().objects.create_user(username='another-opportunity-owner', email='another-owner@example.test')
        grant_sales_actions(self.actor, 'sales_proposals', 'sales_opportunities')
        self.customer = Client.objects.create(client_code='PROPOSAL-EXPORT-CLIENT', company_name='Synthetic Buyer', industry_type='other')
        self.deal = Deal.objects.create(
            deal_code='Q-103001', deal_name='Engineering, review\nphase two',
            client=self.customer, owner=self.actor,
            service_categories=['engineering_design', 'project_management'], submission_due_date=date(2026, 10, 2),
        )
        self.quote = self.make_quote('PROP-SYNTHETIC-001', version=2)
        self.api = APIClient()
        self.api.force_authenticate(self.actor)

    def make_quote(self, number, **kwargs):
        return Quote.objects.create(
            quote_number=number, deal=self.deal, client=self.customer, prepared_by=self.actor,
            subtotal=Decimal('123456.78'), total_amount=Decimal('123456.78'), estimated_cost=Decimal('100000.01'), currency='AED',
            issue_date=date(2026, 9, 1), valid_until=date(2026, 11, 1), notes='NOT_AN_EXPORT_COLUMN', **kwargs,
        )

    def export(self, *ids):
        return self.api.post(self.url + 'export/', {'ids': ','.join(str(value) for value in (ids or (self.quote.pk,)))}, format='json')

    @staticmethod
    def csv_rows(response):
        return list(csv.DictReader(StringIO(response.content.decode('utf-8-sig'))))

    def test_list_sources_canonical_identity_deadline_categories_and_preparer(self):
        response = self.api.get(self.url)
        self.assertEqual(response.status_code, 200)
        row = response.data['results'][0]
        self.assertEqual(row['deal_code'], 'Q-103001')
        self.assertEqual(row['submission_due_date'], '2026-10-02')
        self.assertEqual(row['valid_until'], '2026-11-01')
        self.assertEqual(row['service_categories'], ['engineering_design', 'project_management'])
        self.assertEqual(row['prepared_by_name'], 'Proposal Preparer')
        self.assertEqual(row['version'], 2)
        grant_sales_actions(self.other, 'sales_opportunities')
        Deal.objects.filter(pk=self.deal.pk).update(owner=self.other, submission_due_date=None, service_categories=[])
        row = self.api.get(self.url).data['results'][0]
        self.assertIsNone(row['submission_due_date'])
        self.assertEqual(row['service_categories'], [])
        self.assertEqual(row['prepared_by_name'], 'Proposal Preparer')

    def test_list_requires_proposal_read_grant(self):
        RolePermission.objects.filter(permission__module__code='sales_proposals', permission__action='read').delete()
        self.assertEqual(self.api.get(self.url).status_code, 403)

    @patch('rest_framework.pagination.PageNumberPagination.page_size', 2)
    def test_tied_creation_times_have_stable_nonoverlapping_pages(self):
        second = self.make_quote('PROP-SYNTHETIC-002')
        third = self.make_quote('PROP-SYNTHETIC-003')
        Quote.objects.all().update(created_at=timezone.now())
        pages = [self.api.get(self.url, {'page': number}) for number in (1, 2)]
        for response in pages:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['count'], 3)
        self.assertEqual([row['id'] for response in pages for row in response.data['results']],
                         sorted([str(self.quote.pk), str(second.pk), str(third.pk)], reverse=True))
        self.assertIsNotNone(pages[0].data['next'])
        self.assertIsNone(pages[1].data['next'])

    def test_existing_search_and_status_filters_remain_available(self):
        self.make_quote('PROP-SYNTHETIC-002', status='submitted')
        response = self.api.get(self.url, {'search': 'SYNTHETIC-002', 'status': 'submitted'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['quote_number'] for row in response.data['results']], ['PROP-SYNTHETIC-002'])

    def test_export_exact_selection_order_decimals_distinct_dates_and_headers(self):
        second = self.make_quote('PROP-SYNTHETIC-002')
        response = self.export(second.pk, self.quote.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        self.assertRegex(response['Content-Disposition'], r'^attachment; filename="proposals-\d{4}-\d{2}-\d{2}\.csv"$')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        rows = self.csv_rows(response)
        self.assertEqual([row['Proposal'] for row in rows], ['PROP-SYNTHETIC-002', 'PROP-SYNTHETIC-001'])
        self.assertEqual(rows[1], {
            'Proposal': 'PROP-SYNTHETIC-001', 'Version': '2', 'VF code': 'Q-103001',
            'Title': 'Engineering, review\nphase two', 'Client': 'Synthetic Buyer', 'Status': 'Draft',
            'Owner': 'Proposal Preparer', 'Service line': 'Engineering & Design; Project Management',
            'Submission deadline': '2026-10-02', 'Valid until': '2026-11-01', 'Issue date': '2026-09-01',
            'Proposed price': '123456.78', 'Estimated cost': '100000.01', 'Currency': 'AED',
        })
        self.assertNotIn('NOT_AN_EXPORT_COLUMN', response.content.decode('utf-8-sig'))
        self.assertEqual(len(self.csv_rows(self.export())), 1)

    def test_unknowns_and_zero_preserved_without_inferred_owners(self):
        Deal.objects.filter(pk=self.deal.pk).update(submission_due_date=None, service_categories=[])
        Quote.objects.filter(pk=self.quote.pk).update(prepared_by=None, total_amount=Decimal('0.00'), currency='')
        row = self.csv_rows(self.export())[0]
        for field in ('Owner', 'Service line', 'Submission deadline', 'Currency'):
            self.assertEqual(row[field], '')
        self.assertEqual(row['Proposed price'], '0.00')

    def test_proposal_export_grant_required_independently_of_read(self):
        RolePermission.objects.filter(permission__module__code='sales_proposals', permission__action='export').delete()
        self.assertTrue(module_action_allowed(self.actor, 'sales_proposals', 'read'))
        response = self.export()
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('Content-Disposition', response)

    def test_proposal_read_required_independently_of_export(self):
        RolePermission.objects.filter(permission__module__code='sales_proposals', permission__action='read').delete()
        self.assertTrue(module_action_allowed(self.actor, 'sales_proposals', 'export'))
        self.assertEqual(self.export().status_code, 403)

    def test_opportunity_read_required_for_related_source_data(self):
        RolePermission.objects.filter(permission__module__code='sales_opportunities', permission__action='read').delete()
        self.assertEqual(self.export().status_code, 403)

    def test_post_export_requires_neither_create_nor_opportunity_export(self):
        RolePermission.objects.filter(permission__action='create').delete()
        RolePermission.objects.filter(permission__module__code='sales_opportunities', permission__action='export').delete()
        self.assertEqual(self.export().status_code, 200)

    def test_explicit_export_denial_overrides_role_grant(self):
        permission = Permission.objects.get(module__code='sales_proposals', action='export', is_active=True)
        UserPermissionOverride.objects.create(user_profile=self.actor.rbac_profile, permission=permission, allowed=False)
        self.assertEqual(self.export().status_code, 403)

    def test_missing_or_foreign_selection_fails_wholly_without_identifying_rows(self):
        hidden_deal = Deal.objects.create(deal_code='HIDDEN-VF', deal_name='Private title', client=self.customer, owner=self.other)
        hidden = self.make_quote('HIDDEN-PROPOSAL')
        Quote.objects.filter(pk=hidden.pk).update(deal=hidden_deal)
        denied = self.export(self.quote.pk, hidden.pk)
        missing = self.export(self.quote.pk, uuid4())
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(missing.status_code, 403)
        self.assertEqual(denied.data, missing.data)
        self.assertNotIn('Content-Disposition', denied)
        for value in ('Q-103001', 'HIDDEN-VF', 'HIDDEN-PROPOSAL', 'Private title', str(hidden.pk)):
            self.assertNotIn(value, denied.content.decode())

    def test_mismatched_canonical_client_is_not_exported(self):
        another = Client.objects.create(client_code='MISMATCH', company_name='Wrong client', industry_type='other')
        Quote.objects.filter(pk=self.quote.pk).update(client=another)
        self.assertEqual(self.export().status_code, 403)

    def test_formula_protection_covers_all_untrusted_text(self):
        self.customer.company_name = '+CLIENT'
        self.customer.save(update_fields=['company_name'])
        self.actor.first_name = '@PREPARER'
        self.actor.save(update_fields=['first_name'])
        Deal.objects.filter(pk=self.deal.pk).update(deal_code='=VF', service_categories=['-SERVICE'])
        Quote.objects.filter(pk=self.quote.pk).update(quote_number='=PROPOSAL', currency='+AED', status='=STATUS')
        for value in ('=SUM(1,2)', '+1+2', '-1+2', '@SUM(A1)', '\t=1+2', ' \r=1+2', '\ufeff=1+2', '\x00=1+2'):
            with self.subTest(value=repr(value)):
                Deal.objects.filter(pk=self.deal.pk).update(deal_name=value)
                row = self.csv_rows(self.export())[0]
                self.assertEqual(row['Title'], "'" + value)
                for field in ('Proposal', 'VF code', 'Client', 'Status', 'Owner', 'Service line', 'Currency'):
                    self.assertTrue(row[field].startswith("'"), field)

    def test_invalid_payloads_duplicates_limits_and_query_parameters_rejected(self):
        identifier = str(self.quote.pk)
        for payload in ({}, {'ids': ''}, {'ids': []}, {'ids': 1}, {'ids': 'bad-uuid'},
                        {'ids': identifier + ','}, {'ids': identifier + ',' + identifier},
                        {'ids': identifier, 'all': True}, {'ids': ','.join([identifier] * 10001)}):
            with self.subTest(payload_type=type(payload.get('ids')).__name__):
                response = self.api.post(self.url + 'export/', payload, format='json')
                self.assertEqual(response.status_code, 400)
                self.assertNotIn('Content-Disposition', response)
        self.assertEqual(self.api.post(self.url + 'export/?page=1', {'ids': identifier}, format='json').status_code, 400)

    def test_anonymous_denied_and_get_cannot_export(self):
        self.assertEqual(self.api.get(self.url + 'export/').status_code, 405)
        self.api.force_authenticate(None)
        self.assertEqual(self.export().status_code, 401)
