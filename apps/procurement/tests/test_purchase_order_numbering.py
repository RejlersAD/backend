from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.procurement.services.purchase_order_numbering import (
    PurchaseOrderNumberService,
    legacy_po_number,
    source_po_number,
)


class SourcePurchaseOrderNumberTests(SimpleTestCase):
    def test_preserves_printed_month_year_and_long_sequences(self):
        for value in (
            'RAD-PRJ-PUR-0085_JUL2026',
            'RAD-PRJ-PUR-0085_2026',
            'RAD-GEN-PUR-10000_SEP2026',
        ):
            with self.subTest(value=value):
                self.assertEqual(source_po_number(value), value)

    def test_repairs_only_case_and_layout_whitespace(self):
        self.assertEqual(
            source_po_number(' rad - prj - pur - 0085 _\n jul 2026 '),
            'RAD-PRJ-PUR-0085_JUL2026',
        )

    def test_search_reads_complete_document_and_filename_identifiers(self):
        for text in (
            'Purchase Order: RAD-PRJ-PUR-0085_JUL2026\nSeller: Example',
            'RAD-PRJ-PUR-0085_JUL2026.pdf',
            'Archive RAD-PRJ-PUR-10000_2026 copy.pdf',
        ):
            with self.subTest(text=text):
                expected = ('RAD-PRJ-PUR-10000_2026' if '10000' in text
                            else 'RAD-PRJ-PUR-0085_JUL2026')
                self.assertEqual(source_po_number(text, search=True), expected)
                self.assertIsNone(source_po_number(text))

    def test_rejects_invalid_or_truncated_identifier_tokens(self):
        for value in (
            '', None, 'RAD-PRJ-PUR-85_JUL2026',
            'RAD-PRJ-PUR-0085_JULL2026', 'RAD-PRJ-PUR-0085_ABC2026',
            'RAD-PRJ-PUR-0085_JUL20260', 'RAD-PRJ-PUR-0085_JUL2026A',
            'RAD-PRJ-PUR-0085_JUL2026_REV', 'RAD-PRJ-PUR-0085_JUL2026-REV',
            'XRAD-PRJ-PUR-0085_JUL2026',
            'RAD-PRJ-PUR-' + '0' * 40 + '_JUL2026',
        ):
            with self.subTest(value=value):
                self.assertIsNone(source_po_number(value))
                self.assertIsNone(source_po_number(value, search=True))

    def test_legacy_lookup_does_not_change_full_source_identity(self):
        july = 'RAD-PRJ-PUR-0085_JUL2026'
        january = 'RAD-PRJ-PUR-0085_JAN2026'
        self.assertEqual(legacy_po_number(july), 'RAD-PRJ-PUR-0085_2026')
        self.assertEqual(legacy_po_number(january), legacy_po_number(july))
        self.assertNotEqual(source_po_number(july), source_po_number(january))
        self.assertIsNone(legacy_po_number('invalid'))

    def test_review_serializer_preserves_valid_full_identity(self):
        from apps.procurement.serializers import PODocumentReviewSerializer

        serializer = PODocumentReviewSerializer(data={'po_number': 'RAD-PRJ-PUR-0085_JUL2026'})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['po_number'], 'RAD-PRJ-PUR-0085_JUL2026')

    def test_review_serializer_rejects_embedded_or_overlong_number(self):
        from apps.procurement.serializers import PODocumentReviewSerializer

        for number in ('Prefix RAD-PRJ-PUR-0085_JUL2026', 'RAD-PRJ-PUR-' + '0' * 40 + '_JUL2026'):
            with self.subTest(number=number):
                serializer = PODocumentReviewSerializer(data={'po_number': number})
                self.assertFalse(serializer.is_valid())
                self.assertIn('po_number', serializer.errors)


class PurchaseOrderNumberServiceTests(SimpleTestCase):
    @patch('apps.procurement.services.purchase_order_numbering.PurchaseOrder.objects')
    @patch('apps.procurement.services.purchase_order_numbering.ProcurementNumberSequence.objects')
    def test_allocation_uses_locked_company_sequence(self, sequences, orders):
        locked = MagicMock()
        sequences.select_for_update.return_value = locked
        sequence = SimpleNamespace(last_value=4, save=MagicMock())
        locked.get_or_create.return_value = (sequence, False)
        orders.filter.return_value.values_list.return_value = [
            'RAD-PRJ-PUR-0003_2026',
            'RAD-PRJ-PUR-0007_SEP2026',
        ]

        number = PurchaseOrderNumberService.next_number.__wrapped__(
            PurchaseOrderNumberService,
            'project',
            2026,
        )

        self.assertEqual(number, 'RAD-PRJ-PUR-0008_2026')
        sequences.select_for_update.assert_called_once_with()
        locked.get_or_create.assert_called_once_with(
            document_type='PO',
            prefix='PRJ',
            year=2026,
            defaults={'last_value': 0},
        )
        sequence.save.assert_called_once_with(update_fields=['last_value', 'updated_at'])

    def test_conversion_preserves_pr_scope_sequence_and_year(self):
        number = PurchaseOrderNumberService.from_requisition('RAD-GEN-PR-0042_2026')

        self.assertEqual(number, 'RAD-GEN-PUR-0042_2026')

    def test_conversion_rejects_nonstandard_pr_identifier(self):
        with self.assertRaisesMessage(ValueError, 'company numbering standard'):
            PurchaseOrderNumberService.from_requisition('PR-42')

    @patch.object(PurchaseOrderNumberService, 'next_number')
    def test_reservation_uses_pr_scope_and_year(self, next_number):
        next_number.return_value = 'RAD-GEN-PUR-0101_2025'

        number = PurchaseOrderNumberService.next_for_requisition('RAD-GEN-PR-0042_2025')

        self.assertEqual(number, 'RAD-GEN-PUR-0101_2025')
        next_number.assert_called_once_with('general', year=2025)

    def test_verification_rejects_short_manual_sequence(self):
        verified, message = PurchaseOrderNumberService.verify(
            'RAD-PRJ-PUR-42_2026',
            'RAD-PRJ-PR-0042_2026',
        )

        self.assertFalse(verified)
        self.assertIn('RAD-{GEN|PRJ}-PUR-####_YYYY', message)

    def test_verification_accepts_month_and_year_suffix(self):
        verified, _ = PurchaseOrderNumberService.verify(
            'RAD-PRJ-PUR-0461_SEP2026',
            'RAD-PRJ-PR-0042_2026',
        )

        self.assertTrue(verified)

    def test_verification_rejects_invalid_month_suffix(self):
        verified, message = PurchaseOrderNumberService.verify(
            'RAD-PRJ-PUR-0461_ABC2026',
            'RAD-PRJ-PR-0042_2026',
        )

        self.assertFalse(verified)
        self.assertIn('MMMYYYY', message)

    def test_verification_checks_pr_scope_and_year_but_allows_independent_sequence(self):
        verified, _ = PurchaseOrderNumberService.verify(
            'RAD-PRJ-PUR-0042_2026',
            'RAD-PRJ-PR-0042_2026',
        )
        independent_sequence, _ = PurchaseOrderNumberService.verify(
            'RAD-PRJ-PUR-0043_2026',
            'RAD-PRJ-PR-0042_2026',
        )

        mismatched, message = PurchaseOrderNumberService.verify(
            'RAD-GEN-PUR-0043_2026',
            'RAD-PRJ-PR-0042_2026',
        )

        self.assertTrue(verified)
        self.assertTrue(independent_sequence)
        self.assertFalse(mismatched)
        self.assertIn('same GEN/PRJ scope and year', message)
