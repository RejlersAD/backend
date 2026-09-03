from unittest.mock import patch

from django.test import SimpleTestCase
from rest_framework import serializers as drf_serializers

from apps.procurement.models import PurchaseOrder
from apps.procurement.serializers import PurchaseOrderSerializer


class PurchaseOrderCompletedLockTests(SimpleTestCase):
    def test_completed_order_cannot_be_updated(self):
        order = PurchaseOrder(status='completed')
        serializer = PurchaseOrderSerializer(
            order,
            data={'title': 'Changed title'},
            partial=True,
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn('read-only', str(serializer.errors).lower())

    def test_non_completed_order_remains_editable(self):
        order = PurchaseOrder(status='sent')
        serializer = PurchaseOrderSerializer(
            order,
            data={'title': 'Changed title'},
            partial=True,
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)


class PurchaseOrderOptionalDateTests(SimpleTestCase):
    def test_blank_optional_dates_are_normalized_to_none(self):
        serializer = PurchaseOrderSerializer()

        for field_name in (
            'start_date',
            'end_date',
            'expected_delivery',
            'actual_delivery',
            'approved_date',
            'confirmation_date',
        ):
            with self.subTest(field=field_name):
                self.assertIsNone(serializer.fields[field_name].run_validation(''))

    def test_optional_dates_still_validate_real_dates(self):
        serializer = PurchaseOrderSerializer()

        value = serializer.fields['start_date'].run_validation('2026-08-20')

        self.assertEqual(value.isoformat(), '2026-08-20')


class PurchaseOrderUnifiedProjectTests(SimpleTestCase):
    def test_core_picker_identity_populates_enterprise_project(self):
        serializer = PurchaseOrderSerializer()
        picker_data = {
            'project': 'core:44',
            'project_number': '5900913',
        }

        # Isolate the boundary normalization from relational validation; DRF's
        # normal field validation then resolves enterprise_project=44.
        with patch.object(
            drf_serializers.ModelSerializer,
            'to_internal_value',
            side_effect=lambda value: value,
        ):
            normalized = serializer.to_internal_value(picker_data)

        self.assertNotIn('project', normalized)
        self.assertEqual(normalized['enterprise_project'], '44')
        self.assertEqual(normalized['project_number'], '5900913')
