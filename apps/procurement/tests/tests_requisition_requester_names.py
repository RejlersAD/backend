from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.procurement.serializers import PurchaseRequisitionSerializer


class PurchaseRequisitionRequesterNameTests(SimpleTestCase):
    @patch('apps.procurement.serializers.employee_display_name', return_value='Firaol Akawak')
    def test_requester_aliases_fall_back_to_issued_by(self, display_name_mock):
        issuer = SimpleNamespace(id='issuer-1')
        requisition = SimpleNamespace(requested_by=None, issued_by=issuer)
        serializer = PurchaseRequisitionSerializer()

        self.assertEqual(serializer.get_requester_name(requisition), 'Firaol Akawak')
        self.assertEqual(serializer.get_requested_by_name(requisition), 'Firaol Akawak')
        self.assertEqual(display_name_mock.call_count, 2)
