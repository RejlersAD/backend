from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from rest_framework import serializers

from apps.procurement.serializers import PurchaseRequisitionSerializer


class _ApproverManager:
    def __init__(self, approver):
        self.approver = approver

    def get(self, **kwargs):
        if str(kwargs.get('pk')) != str(self.approver.pk):
            raise _Approver.DoesNotExist
        return self.approver


class _Approver:
    class DoesNotExist(Exception):
        pass

    def __init__(self, pk='approver-1'):
        self.pk = pk
        self.email = 'approver@example.com'

    def get_full_name(self):
        return 'Assigned Approver'


class PurchaseRequisitionSerializerGuardTests(SimpleTestCase):
    def setUp(self):
        eligibility = patch('apps.procurement.services.approval_eligibility.eligible_stage_assignee', return_value=True)
        eligibility.start()
        self.addCleanup(eligibility.stop)
        self.issuer = SimpleNamespace(id='issuer-1', is_superuser=False)
        self.other_user = SimpleNamespace(id='other-1', is_superuser=False)
        self.approver = _Approver()
        _Approver.objects = _ApproverManager(self.approver)

    def _context(self, user=None):
        return {'request': SimpleNamespace(user=user or self.issuer)}

    def _draft_instance(self, **overrides):
        values = {'status': 'draft', 'issued_by_id': self.issuer.id}
        values.update(overrides)
        return SimpleNamespace(**values)

    @patch('apps.procurement.serializers.PurchaseRequisition.objects.filter')
    def test_rejects_server_controlled_fields_on_create(self, filter_mock):
        filter_mock.return_value.exists.return_value = False
        protected_values = {
            'pr_number': 'RAD-PRJ-PR-0042_2026',
            'status': 'approved',
            'pm_approval_status': 'approved',
            'pm_signature': 'forged-signature',
            'approved_by': 'some-user',
            'rejection_reason': 'client supplied',
        }

        serializer = PurchaseRequisitionSerializer(
            data=protected_values,
            context=self._context(),
        )

        self.assertFalse(serializer.is_valid())
        for field in protected_values.keys() - {'pr_number'}:
            self.assertIn(field, serializer.errors)

    def test_rejects_server_controlled_fields_on_patch(self):
        serializer = PurchaseRequisitionSerializer(
            self._draft_instance(),
            data={'current_approval_step': 10, 'vp_op_approval_status': 'approved'},
            partial=True,
            context=self._context(),
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn('current_approval_step', serializer.errors)
        self.assertIn('vp_op_approval_status', serializer.errors)

    @patch('apps.procurement.serializers.get_user_model', return_value=_Approver)
    def test_draft_issuer_can_assign_workflow_and_client_state_is_reset(self, _get_user_model):
        serializer = PurchaseRequisitionSerializer(
            self._draft_instance(),
            data={
                'approval_workflow_config': [{
                    'step': 99,
                    'role': 'Project Manager',
                    'user_id': self.approver.pk,
                    'user_name': 'Forged Name',
                    'status': 'approved',
                    'approved_at': '2026-01-01T00:00:00Z',
                }]
            },
            partial=True,
            context=self._context(),
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        stage = serializer.validated_data['approval_workflow_config'][0]
        self.assertEqual(stage['step'], 1)
        self.assertEqual(stage['user_name'], 'Assigned Approver')
        self.assertEqual(stage['status'], 'pending')
        self.assertIsNone(stage['approved_at'])

    @patch('apps.procurement.serializers.get_user_model', return_value=_Approver)
    def test_level_zero_procurement_stage_accepts_active_employee_and_records_email(self, _get_user_model):
        serializer = PurchaseRequisitionSerializer(
            self._draft_instance(),
            data={'approval_workflow_config': [{
                'level': 0,
                'role': 'Procurement Department',
                'user_id': self.approver.pk,
            }]},
            partial=True,
            context=self._context(),
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        stage = serializer.validated_data['approval_workflow_config'][0]
        self.assertEqual(stage['level'], 0)
        self.assertEqual(stage['user_email'], 'approver@example.com')

    @patch('apps.procurement.serializers.get_user_model', return_value=_Approver)
    def test_non_draft_edit_updates_workflow_and_preserves_completed_decision(self, _get_user_model):
        serializer = PurchaseRequisitionSerializer(
            self._draft_instance(
                status='submitted',
                approval_workflow_config=[{
                    'level': 1,
                    'role': 'Project Manager',
                    'user_id': self.approver.pk,
                    'status': 'approved',
                    'approved_at': '2026-09-03T10:00:00Z',
                }],
            ),
            data={
                'approval_workflow_config': [{
                    'role': 'Project Manager',
                    'user_id': self.approver.pk,
                }]
            },
            partial=True,
            context=self._context(),
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        stage = serializer.validated_data['approval_workflow_config'][0]
        self.assertEqual(stage['status'], 'approved')
        self.assertEqual(stage['approved_at'], '2026-09-03T10:00:00Z')

    @patch('apps.procurement.serializers.get_user_model', return_value=_Approver)
    def test_non_issuer_cannot_change_draft_workflow(self, _get_user_model):
        serializer = PurchaseRequisitionSerializer(
            self._draft_instance(),
            data={
                'approval_workflow_config': [{
                    'role': 'Project Manager',
                    'user_id': self.approver.pk,
                }]
            },
            partial=True,
            context=self._context(self.other_user),
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn('approval_workflow_config', serializer.errors)

    def test_read_alias_cannot_be_used_to_replace_workflow(self):
        serializer = PurchaseRequisitionSerializer(
            self._draft_instance(),
            data={'approval_hierarchy': []},
            partial=True,
            context=self._context(),
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn('approval_hierarchy', serializer.errors)

    def test_incomplete_po_reference_can_be_saved_while_still_a_draft(self):
        serializer = PurchaseRequisitionSerializer(
            self._draft_instance(po_applicable=False, po_number_reference=''),
            data={
                'po_applicable': True,
                'po_number_reference': 'PO-PENDING-VALIDATION',
            },
            partial=True,
            context=self._context(),
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(
            serializer.validated_data['po_number_reference'],
            'PO-PENDING-VALIDATION',
        )

    def test_pricing_description_can_differ_from_purchase_description(self):
        serializer = PurchaseRequisitionSerializer(
            self._draft_instance(description_reason='Old description'),
            data={
                'description_reason': 'Unified purchase description',
                'price_description': 'Conflicting pricing description',
            },
            partial=True,
            context=self._context(),
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(
            serializer.validated_data['price_description'],
            'Conflicting pricing description',
        )

    @patch('apps.procurement.serializers.PurchaseRequisition.objects.filter')
    def test_manual_pr_number_is_normalized_when_available(self, filter_mock):
        filter_mock.return_value.exists.return_value = False
        serializer = PurchaseRequisitionSerializer(context=self._context())

        self.assertEqual(
            serializer.validate_pr_number('  rad-prj-pr-0042_2026  '),
            'RAD-PRJ-PR-0042_2026',
        )
        filter_mock.assert_called_once_with(pr_number__iexact='RAD-PRJ-PR-0042_2026')

    @patch('apps.procurement.serializers.PurchaseRequisition.objects.filter')
    def test_duplicate_manual_pr_number_is_rejected_case_insensitively(self, filter_mock):
        filter_mock.return_value.exists.return_value = True
        serializer = PurchaseRequisitionSerializer(context=self._context())

        with self.assertRaisesMessage(serializers.ValidationError, 'This PR number already exists.'):
            serializer.validate_pr_number('rad-prj-pr-0042_2026')
