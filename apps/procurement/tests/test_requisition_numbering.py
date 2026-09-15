from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.procurement.services.requisition_numbering import RequisitionNumberService


class RequisitionNumberServiceTests(SimpleTestCase):
    @patch('apps.procurement.services.requisition_numbering.PurchaseRequisition.objects')
    @patch('apps.procurement.services.requisition_numbering.ProcurementNumberSequence.objects')
    def test_allocation_locks_scope_and_advances_past_existing_numbers(self, sequences, requisitions):
        locked = MagicMock()
        sequences.select_for_update.return_value = locked
        sequence = SimpleNamespace(last_value=4, save=MagicMock())
        locked.get_or_create.return_value = (sequence, False)
        requisitions.filter.return_value.values_list.return_value = [
            'RAD-PRJ-PR-0003_2026',
            'RAD-PRJ-PR-0007_2026',
        ]

        number = RequisitionNumberService.next_number.__wrapped__(
            RequisitionNumberService,
            'project',
            2026,
        )

        self.assertEqual(number, 'RAD-PRJ-PR-0008_2026')
        sequences.select_for_update.assert_called_once_with()
        locked.get_or_create.assert_called_once_with(
            document_type='PR',
            prefix='PRJ',
            year=2026,
            defaults={'last_value': 0},
        )
        sequence.save.assert_called_once_with(update_fields=['last_value', 'updated_at'])

    @patch('apps.procurement.services.requisition_numbering.PurchaseRequisition.objects')
    @patch('apps.procurement.services.requisition_numbering.ProcurementNumberSequence.objects')
    def test_general_and_project_sequences_are_separate(self, sequences, requisitions):
        locked = MagicMock()
        sequences.select_for_update.return_value = locked
        sequence = SimpleNamespace(last_value=0, save=MagicMock())
        locked.get_or_create.return_value = (sequence, True)
        requisitions.filter.return_value.values_list.return_value = []

        number = RequisitionNumberService.next_number.__wrapped__(
            RequisitionNumberService,
            'general',
            2026,
        )

        self.assertEqual(number, 'RAD-GEN-PR-0001_2026')
