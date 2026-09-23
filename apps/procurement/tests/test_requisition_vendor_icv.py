"""A recorded zero ICV survives supplier selection and requisition reloads."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.procurement.models import PurchaseRequisition, Vendor
from apps.rbac.models import Module, Organization, Permission, UserProfile
from apps.rbac.module_actions import ensure_module_actions


@override_settings(ROOT_URLCONF='config.urls_release_test')
class RequisitionVendorICVTests(TestCase):
    def setUp(self):
        cache.clear()
        self.actor = get_user_model().objects.create_superuser(
            'icv-procurement', email='icv-procurement@example.test', password='test',
        )
        organization, _ = Organization.objects.get_or_create(code='icv-tests', defaults={'name': 'ICV tests'})
        profile, _ = UserProfile.objects.get_or_create(user=self.actor, defaults={'organization': organization})
        profile.status, profile.is_deleted = 'active', False
        profile.save(update_fields=['status', 'is_deleted'])
        module, _ = Module.objects.get_or_create(
            code='procurement_requisitions', defaults={'name': 'Purchase Recommendations'},
        )
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        self.client = APIClient()
        self.client.force_authenticate(self.actor)
        self.vendor = Vendor.objects.create(
            vendor_code='ICV-ZERO', name='Zero ICV supplier', status='active',
            rating=5, adnoc_approved=True, icv_percentage=Decimal('0.00'),
        )
        self.missing = Vendor.objects.create(
            vendor_code='ICV-MISSING', name='Missing ICV supplier', status='active',
            rating=5, adnoc_approved=True, icv_percentage=None,
        )
        self.pr = PurchaseRequisition.objects.create(
            pr_number='ICV-PR-001', issued_by=self.actor, vendor=self.vendor,
            supplier_name=self.vendor.name, status='draft',
        )
        self.base = '/api/v1/procurement/requisitions/'

    def test_saved_zero_survives_vendor_options_and_requisition_reload(self):
        saved = self.client.patch(f'{self.base}vendor-icv/', {
            'vendor_id': self.vendor.pk, 'icv_percentage': 0,
        }, format='json')
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(saved.json()['icv_percentage'], '0.00')

        selected = self.client.get(f'{self.base}vendor-options/', {'id': self.vendor.pk})
        self.assertEqual(selected.status_code, 200, selected.data)
        self.assertEqual(selected.json()['count'], 1)
        self.assertEqual(selected.json()['suggestions'][0]['icv_percentage'], 0.0)

        options = self.client.get(f'{self.base}vendor-options/')
        self.assertEqual(options.status_code, 200, options.data)
        values = {row['vendor_code']: row['icv_percentage'] for row in options.json()['suggestions']}
        self.assertEqual(values['ICV-ZERO'], 0.0)
        self.assertIsNone(values['ICV-MISSING'])

        reloaded = self.client.get(f'{self.base}{self.pr.pk}/')
        self.assertEqual(reloaded.status_code, 200, reloaded.data)
        self.assertEqual(reloaded.json()['vendor_details']['icv_percentage'], '0.00')

    def test_vendor_recommendations_distinguish_recorded_zero_from_missing(self):
        response = self.client.post(f'{self.base}{self.pr.pk}/recommend_vendors/', {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        values = {row['vendor_code']: row['icv_percentage'] for row in response.json()['recommendations']}
        self.assertEqual(values['ICV-ZERO'], 0.0)
        self.assertIsNone(values['ICV-MISSING'])
        self.pr.refresh_from_db()
        saved = {row['vendor_code']: row['icv_percentage'] for row in self.pr.ai_vendor_recommendations}
        self.assertEqual(saved, values)
