from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.procurement.models import Vendor
from apps.procurement.serializers import VendorSerializer


class VendorCodeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('vendor-code-test')
        self.context = {'request': SimpleNamespace(user=self.user)}

    def create_vendor(self, **data):
        serializer = VendorSerializer(data={'name': 'Test Vendor', **data}, context=self.context)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        return serializer.save()

    def test_blank_and_omitted_codes_generate_distinct_codes(self):
        vendors = [self.create_vendor(), self.create_vendor(vendor_code=''), self.create_vendor(vendor_code='   ')]
        self.assertEqual(len({vendor.vendor_code for vendor in vendors}), 3)
        self.assertTrue(all(vendor.vendor_code.startswith('VEN-') for vendor in vendors))

    def test_custom_codes_are_preserved_and_duplicates_rejected(self):
        self.create_vendor(vendor_code='CUSTOM-001')
        serializer = VendorSerializer(data={'name': 'Duplicate', 'vendor_code': 'CUSTOM-001'}, context=self.context)
        self.assertFalse(serializer.is_valid())
        self.assertIn('vendor_code', serializer.errors)
        self.assertEqual(Vendor.objects.filter(vendor_code='CUSTOM-001').count(), 1)

    def test_blank_update_keeps_existing_code(self):
        vendor = self.create_vendor(vendor_code='CUSTOM-002')
        serializer = VendorSerializer(vendor, data={'vendor_code': '', 'name': 'Updated'}, partial=True, context=self.context)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.save().vendor_code, 'CUSTOM-002')
