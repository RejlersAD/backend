"""Reviewed registration, source defaults and actor-bound retries."""

from uuid import uuid4
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.rbac.route_guard import secure_module_endpoints
from apps.sales.models import Client, Deal, OpportunityAuditEvent
from apps.sales.views import DealViewSet
from .access_fixtures import grant_sales_actions


router = DefaultRouter()
router.register('deals', DealViewSet, basename='vf-deals')
urlpatterns = [path('api/v1/sales/', include(router.urls))]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class VFRegistrationAPITests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.actor = get_user_model().objects.create_user('vf-operator', email='vf-operator@example.test')
        grant_sales_actions(self.actor, 'sales_opportunities', 'sales_clients')
        self.customer = Client.objects.create(
            client_code='VF-CUSTOMER', company_name='Synthetic Buyer',
            industry_type='other', account_manager=self.actor,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.actor)
        self.payload = {
            'registration_request_id': str(uuid4()), 'deal_name': 'Engineering enquiry',
            'client': str(self.customer.pk), 'opportunity_type': 'rfq',
            'open_date': '2026-09-30', 'submission_due_date': '2026-10-02',
        }

    def save(self, **changes):
        return self.client.post('/api/v1/sales/deals/', {**self.payload, **changes}, format='json')

    def test_minimal_registration_and_retry_have_one_number_and_audit(self):
        first = self.save()
        self.assertEqual(first.status_code, 201, first.data)
        second = self.save()
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(first.data['id'], second.data['id'])
        deal = Deal.objects.get()
        self.assertEqual(deal.deal_code, 'Q-102101')
        self.assertEqual(deal.created_by, self.actor)
        self.assertEqual(deal.owner, self.actor)
        self.assertEqual(deal.stage, 'lead')
        self.assertIsNone(deal.estimated_value)
        self.assertIsNone(deal.weighted_value)
        self.assertIsNone(deal.expected_close_date)
        self.assertEqual(deal.currency, '')
        self.assertEqual(OpportunityAuditEvent.objects.count(), 1)

    def test_changed_payload_with_same_request_id_conflicts(self):
        self.assertEqual(self.save().status_code, 201)
        response = self.save(deal_name='Different opportunity')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(Deal.objects.count(), 1)
        self.assertEqual(OpportunityAuditEvent.objects.count(), 1)

    def test_audit_failure_rolls_back_number_and_record(self):
        with patch('apps.sales.views._audit', side_effect=RuntimeError('synthetic audit failure')):
            with self.assertRaises(RuntimeError):
                self.save()
        self.assertEqual(Deal.objects.count(), 0)
        self.assertEqual(OpportunityAuditEvent.objects.count(), 0)
        response = self.save()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['deal_code'], 'Q-102101')

    def test_missing_commercial_details_warn_but_allow_qualification(self):
        created = self.save()
        response = self.client.post(f"/api/v1/sales/deals/{created.data['id']}/submit-qualification/", {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Deal.objects.get().stage, 'qualified')
        warnings = {warning['field'] for warning in response.data['warnings']}
        self.assertIn('scope_type', warnings)
        self.assertIn('required_attachment', warnings)

    def test_unknown_value_is_not_reported_as_zero_pipeline(self):
        self.assertEqual(self.save().status_code, 201)
        response = self.client.get('/api/v1/sales/deals/pipeline_summary/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data['pipeline_summary']['total_value'])
        self.assertIsNone(response.data['pipeline_summary']['weighted_value'])

    def test_options_exclude_inactive_users_and_are_permission_guarded(self):
        inactive = get_user_model().objects.create_user('vf-inactive', email='vf-inactive@example.test', is_active=False)
        response = self.client.get('/api/v1/sales/deals/registration-options/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['default_owner'], self.actor.pk)
        self.assertIn(self.actor.pk, [row['id'] for row in response.data['owners']])
        self.assertNotIn(inactive.pk, [row['id'] for row in response.data['owners']])
        self.assertNotIn('next_code', response.data)
        denied = get_user_model().objects.create_user('vf-denied', email='vf-denied@example.test')
        self.client.force_authenticate(denied)
        self.assertEqual(self.client.get('/api/v1/sales/deals/registration-options/').status_code, 403)

    def test_invalid_request_uuid_does_not_allocate_number(self):
        self.assertEqual(self.save(registration_request_id='invalid').status_code, 400)
        self.assertFalse(Deal.objects.exists())

    def test_completing_commercial_details_retains_registration_names_and_code(self):
        created = self.save()
        response = self.client.patch(f"/api/v1/sales/deals/{created.data['id']}/", {
            'estimated_value': '125000.50', 'currency': 'AED',
            'expected_close_date': '2026-11-15', 'scope_type': 'feed',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['deal_code'], 'Q-102101')
        self.assertEqual(response.data['client_name'], 'Synthetic Buyer')
        self.assertEqual(response.data['owner_name'], 'vf-operator')
        self.assertEqual(response.data['created_by_name'], 'vf-operator')
        self.assertEqual(response.data['created_at'], created.data['created_at'])
        self.assertEqual(response.data['estimated_value'], '125000.50')
