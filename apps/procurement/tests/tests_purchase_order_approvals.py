from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import TemporaryUploadedFile
from django.http import QueryDict
from django.test import SimpleTestCase
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from apps.procurement.services.purchase_order_approvals import (
    FINANCIAL_STAGE,
    TECHNICAL_STAGE,
    _entry_matches_user,
    normalize_assignments,
    notify_purchase_order_created,
    record_decision,
)
from apps.procurement.serializers import PurchaseOrderSerializer, VendorICVSerializer


class EmptyRelations:
    def all(self):
        return []


class PurchaseOrderApprovalAssignmentTests(SimpleTestCase):
    def _profile(self, user_id, name, email, department):
        user = SimpleNamespace(
            id=user_id,
            email=email,
            get_full_name=lambda: name,
        )
        return SimpleNamespace(user_id=user_id, user=user, department=department, roles=EmptyRelations())

    def setUp(self):
        self.technical = self._profile('technical-id', 'Technical Approver', 'technical@example.com', 'Engineering')
        self.finance = self._profile('finance-id', 'Finance Approver', 'finance@example.com', 'Finance')
        self.non_finance = self._profile('other-id', 'Other Approver', 'other@example.com', 'Engineering')

    @patch('apps.procurement.services.purchase_order_approvals._active_profiles')
    def test_assignments_are_resolved_from_active_employees(self, active_profiles):
        active_profiles.return_value = {
            'technical-id': self.technical,
            'finance-id': self.finance,
        }
        result = normalize_assignments([
            {'stage': TECHNICAL_STAGE, 'user_id': 'technical-id'},
            {'stage': FINANCIAL_STAGE, 'user_id': 'finance-id'},
        ])

        self.assertEqual(result[0]['approver'], 'Technical Approver')
        self.assertEqual(result[0]['status'], 'Pending')
        self.assertEqual(result[1]['approver_email'], 'finance@example.com')

    @patch('apps.procurement.services.purchase_order_approvals._active_profiles')
    def test_financial_stage_rejects_non_finance_employee(self, active_profiles):
        active_profiles.return_value = {
            'technical-id': self.technical,
            'other-id': self.non_finance,
        }
        with self.assertRaisesMessage(ValidationError, 'active Finance employee'):
            normalize_assignments([
                {'stage': TECHNICAL_STAGE, 'user_id': 'technical-id'},
                {'stage': FINANCIAL_STAGE, 'user_id': 'other-id'},
            ])

    def test_draft_may_keep_approvals_unassigned(self):
        result = normalize_assignments([
            {'stage': TECHNICAL_STAGE, 'user_id': ''},
            {'stage': FINANCIAL_STAGE, 'user_id': ''},
        ], require_core=False)

        self.assertEqual(result, [])

    def test_migrated_assignment_uses_email_when_user_id_changed(self):
        actor = SimpleNamespace(id='production-id', email='firaol.akawak@rejlers.ae')
        entry = {
            'user_id': 'preproduction-id',
            'approver_email': 'FIRAOL.AKAWAK@rejlers.ae',
        }

        self.assertTrue(_entry_matches_user(entry, actor))

    @patch('apps.procurement.services.purchase_order_approvals._active_profiles')
    def test_linked_po_removes_jarmo_management_approval(self, active_profiles):
        jarmo = self._profile('jarmo-id', 'Jarmo Suominen', 'jarmo@example.com', 'Management')
        active_profiles.return_value = {'jarmo-id': jarmo}
        serializer = PurchaseOrderSerializer(instance=SimpleNamespace(
            status='sent',
            approval_log=[],
            pr_reference=SimpleNamespace(id='pr-id'),
        ))

        attrs = serializer.validate({'approval_log': [{
            'stage': 'Final Management Sign-off',
            'user_id': 'jarmo-id',
        }]})

        self.assertEqual(attrs['approval_log'], [])
        self.assertEqual(attrs['management_approver'], '')

    def test_core_project_multipart_normalization_keeps_open_attachments(self):
        upload = TemporaryUploadedFile(
            'support.pdf', 'application/pdf', 4, charset=None,
        )
        try:
            upload.write(b'%PDF')
            upload.seek(0)
            payload = QueryDict('', mutable=True)
            payload['project'] = 'core:3d2c3f86-2503-477d-892c-cae6d8920140'
            payload.setlist('attachments_files', [upload])

            with patch.object(
                serializers.ModelSerializer,
                'to_internal_value',
                return_value={'normalized': True},
            ) as parent:
                result = PurchaseOrderSerializer().to_internal_value(payload)

            normalized = parent.call_args.args[0]
            self.assertEqual(result, {'normalized': True})
            self.assertNotIn('project', normalized)
            self.assertEqual(
                normalized['enterprise_project'],
                '3d2c3f86-2503-477d-892c-cae6d8920140',
            )
            self.assertIs(normalized.getlist('attachments_files')[0], upload)
        finally:
            upload.close()

    @patch('apps.notifications.services.NotificationService.create_notification')
    @patch('apps.notifications.models.Notification.objects.filter')
    @patch('apps.procurement.services.purchase_order_approvals._jarmo_user')
    @patch('apps.procurement.services.purchase_order_approvals._resolve_entry_user')
    @patch('apps.procurement.services.purchase_order_approvals.employee_display_name')
    def test_po_created_notifies_buyer_references_and_ceo_once(
        self,
        display_name,
        resolve_entry_user,
        jarmo_user,
        notification_filter,
        create_notification,
    ):
        buyer = SimpleNamespace(pk='buyer-id', email='buyer@rejlers.ae')
        jarmo = SimpleNamespace(pk='ceo-id', email='jarmo@rejlers.ae')
        creator = SimpleNamespace(pk='creator-id', email='creator@rejlers.ae')
        resolve_entry_user.return_value = buyer
        jarmo_user.return_value = jarmo
        notification_filter.return_value.exists.return_value = False
        display_name.return_value = 'PO Creator'
        order = SimpleNamespace(
            id='po-id',
            po_number='RAD-PRJ-PUR-0117_2026',
            buyer_reference_email='buyer@rejlers.ae',
            buyer_reference_pm='Buyer Reference',
            contact_persons={'buyer_references': [{
                'user_id': 'buyer-id',
                'email': 'buyer@rejlers.ae',
                'name': 'Buyer Reference',
            }]},
            created_by=creator,
            expected_delivery=None,
            end_date=None,
        )

        notify_purchase_order_created(order)

        self.assertEqual(create_notification.call_count, 2)
        recipients = {
            call.kwargs['recipient'].email: call.kwargs
            for call in create_notification.call_args_list
        }
        self.assertSetEqual(set(recipients), {'buyer@rejlers.ae', 'jarmo@rejlers.ae'})
        for kwargs in recipients.values():
            self.assertTrue(kwargs['send_teams'])
            self.assertEqual(kwargs['teams_context']['event_type'], 'purchase_order_created')
            self.assertEqual(kwargs['action_url'], '/procurement/orders/po-id')

    def test_manual_vendor_icv_requires_valid_percentage_and_sets_certified(self):
        serializer = VendorICVSerializer(data={'icv_percentage': '64.25'})

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(str(serializer.validated_data['icv_percentage']), '64.25')
        self.assertTrue(serializer.validated_data['is_icv_certified'])

        invalid = VendorICVSerializer(data={'icv_percentage': '101'})
        self.assertFalse(invalid.is_valid())
        self.assertIn('icv_percentage', invalid.errors)

    @patch('apps.procurement.models.PurchaseOrder.objects.select_for_update')
    def test_approval_records_full_timestamp(self, select_for_update):
        actor = SimpleNamespace(id='jarmo-id', email='jarmo@example.com', get_full_name=lambda: 'Jarmo Suominen')
        locked = SimpleNamespace(
            id='po-id',
            approval_log=[{
                'stage': 'Final Management Sign-off',
                'user_id': 'jarmo-id',
                'status': 'Pending',
            }],
            approved_by=None,
            approved_by_name='',
            approved_date=None,
            approved_at=None,
            save=MagicMock(),
        )
        select_for_update.return_value.select_related.return_value.get.return_value = locked

        updated, entry = record_decision.__wrapped__(SimpleNamespace(pk='po-id'), actor, 'approve')

        self.assertEqual(entry['status'], 'Approved')
        self.assertIn('T', entry['approved_at'])
        self.assertIn('T', entry['decided_at'])
        self.assertIsNotNone(updated.approved_at)
