from django.test import SimpleTestCase

from apps.procurement.models import PurchaseRequisition
from apps.procurement.services.requisition_status import (
    CANONICAL_PR_STATUSES,
    canonicalize_pr_status,
    stored_values_for,
)


class RequisitionStatusTests(SimpleTestCase):
    def test_model_exposes_only_canonical_statuses(self):
        model_statuses = tuple(value for value, _ in PurchaseRequisition.STATUS_CHOICES)
        self.assertEqual(model_statuses, CANONICAL_PR_STATUSES)

    def test_legacy_review_statuses_are_normalized(self):
        self.assertEqual(canonicalize_pr_status('pending_level_2'), 'in_review')
        self.assertEqual(canonicalize_pr_status('pm_approved'), 'in_review')

    def test_legacy_approval_statuses_are_normalized(self):
        self.assertEqual(canonicalize_pr_status('vp_approved'), 'approved')
        self.assertEqual(canonicalize_pr_status('fully_approved'), 'approved')

    def test_database_values_include_transitional_aliases(self):
        self.assertEqual(
            stored_values_for('approved'),
            {'approved', 'vp_approved', 'fully_approved'},
        )
