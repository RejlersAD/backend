from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from rest_framework import serializers

from apps.procurement.serializers import PurchaseRequisitionSerializer


class ManualPRAndPricingSerializerTests(SimpleTestCase):
    def setUp(self):
        self.request = SimpleNamespace(user=SimpleNamespace(id='issuer-1', is_superuser=False))

    @patch('apps.procurement.serializers.PurchaseRequisition.objects.filter')
    def test_manual_pr_number_is_writable_normalized_and_available(self, filter_mock):
        filter_mock.return_value.exists.return_value = False
        serializer = PurchaseRequisitionSerializer(context={'request': self.request})

        self.assertFalse(serializer.fields['pr_number'].read_only)
        self.assertEqual(
            serializer.validate_pr_number('  rad-prj-pr-0042_2026  '),
            'RAD-PRJ-PR-0042_2026',
        )

    @patch('apps.procurement.serializers.PurchaseRequisition.objects.filter')
    def test_manual_pr_number_rejects_case_insensitive_duplicate(self, filter_mock):
        filter_mock.return_value.exists.return_value = True
        serializer = PurchaseRequisitionSerializer(context={'request': self.request})

        with self.assertRaisesMessage(serializers.ValidationError, 'This PR number already exists.'):
            serializer.validate_pr_number('rad-prj-pr-0042_2026')

    def test_pricing_description_is_independent_from_purchase_description(self):
        instance = SimpleNamespace(
            status='draft',
            issued_by_id='issuer-1',
            description_reason='Original purchase description',
        )
        serializer = PurchaseRequisitionSerializer(
            instance,
            data={
                'description_reason': 'Updated purchase description',
                'price_description': 'Procurement pricing description',
            },
            partial=True,
            context={'request': self.request},
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(
            serializer.validated_data['price_description'],
            'Procurement pricing description',
        )
