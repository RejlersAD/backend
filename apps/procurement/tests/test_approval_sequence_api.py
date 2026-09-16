"""Exercise the actual API boundary for approval ordering and signer identity."""

from copy import deepcopy
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.test import APIClient
from rest_framework.exceptions import ValidationError

from apps.procurement.models import PurchaseOrder, PurchaseRequisition, Vendor
from apps.procurement.serializers import PurchaseOrderSerializer, PurchaseRequisitionSerializer
from apps.procurement.services.requisition_workflow import RequisitionWorkflowService
from apps.rbac.models import Module, Organization, Permission, Role, RoleModule, UserPermissionOverride, UserProfile, UserRole
from apps.rbac.module_actions import ensure_module_actions
from .approval_fixtures import grant_approval, set_position


urlpatterns = [path('api/v1/procurement/', include('apps.procurement.urls'))]


@override_settings(
    ROOT_URLCONF=__name__, TEAMS_APPROVAL_WEBHOOK_URL='',
    WEB_PUSH_VAPID_PRIVATE_KEY='', WEB_PUSH_VAPID_PUBLIC_KEY='',
)
class ApprovalSequenceAPITests(TestCase):
    def setUp(self):
        cache.clear()
        users = get_user_model()
        self.procurement = users.objects.create_user('procurement-assignee', email='procurement@example.test')
        self.ceo = users.objects.create_superuser('ceo-assignee', email='ceo@example.test', password='test-only')
        organization, _ = Organization.objects.get_or_create(code='sequence-api', defaults={'name': 'Sequence API'})
        module, _ = Module.objects.get_or_create(code='procurement_requisitions', defaults={'name': 'Purchase requisitions'})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        role = Role.objects.create(code='sequence-api-approver', name='Approval API test role', level=3)
        RoleModule.objects.create(role=role, module=module)
        for user in (self.procurement, self.ceo):
            profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': organization})
            profile.signature_image = f'data:image/png;base64,signature-for-{user.pk}'
            profile.status = 'active'
            profile.is_deleted = False
            profile.save()
            UserRole.objects.create(user_profile=profile, role=role)
            grant_approval(user)
            set_position(user, 'CEO' if user == self.ceo else 'Procurement Manager')
            for permission in module.permissions.filter(action__in=['read', 'update', 'approve', 'reject'], is_active=True):
                UserPermissionOverride.objects.create(user_profile=profile, permission=permission, allowed=True)
        self.workflow = [
            {'role': 'Procurement Manager', 'level': 0, 'user_id': str(self.procurement.pk),
             'user_email': self.procurement.email, 'user_name': 'Assigned Procurement', 'status': 'pending'},
            {'role': 'CEO', 'level': 5, 'user_id': str(self.ceo.pk),
             'user_email': self.ceo.email, 'user_name': 'Assigned CEO', 'status': 'pending'},
        ]
        self.pr = PurchaseRequisition.objects.create(
            pr_number='SEQ-API-PR-2026', title='Sequence API test', issued_by=self.ceo,
            status='submitted', po_applicable=False, approval_workflow_config=deepcopy(self.workflow),
        )
        self.client = APIClient()
        self.url = f'/api/v1/procurement/requisitions/{self.pr.pk}/'

    def serializer(self, actor):
        return PurchaseRequisitionSerializer(self.pr, context={'request': SimpleNamespace(user=actor)})

    def test_ceo_superuser_has_no_action_or_queue_item_before_level_zero(self):
        self.client.force_authenticate(self.ceo)
        detail = self.client.get(self.url)
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertFalse(detail.data['can_approve'])
        self.assertEqual(detail.data['current_approval']['level'], 0)
        queue = self.client.get('/api/v1/procurement/requisitions/pending-for-me/')
        self.assertEqual(queue.status_code, 200, queue.data)
        self.assertEqual(queue.data['count'], 0)

    def test_every_pr_approval_endpoint_refuses_ceo_before_turn_without_changing_row(self):
        self.client.force_authenticate(self.ceo)
        for action in ('approve', 'process_dynamic_approval', 'pm_approve',
                       'eng_manager_approve', 'manager_projects_approve', 'vp_approve'):
            with self.subTest(action=action):
                response = self.client.post(self.url + action + '/', {'signature': 'forged'}, format='json')
                self.assertIn(response.status_code, (400, 403), response.data)
                self.pr.refresh_from_db()
                self.assertEqual(self.pr.approval_workflow_config, self.workflow)
                self.assertEqual(self.pr.status, 'submitted')
                self.assertFalse(self.pr.vp_op_signature)

    def test_every_pr_rejection_endpoint_refuses_ceo_before_turn(self):
        self.client.force_authenticate(self.ceo)
        for action in ('reject', 'process_dynamic_rejection', 'pm_reject',
                       'eng_manager_reject', 'manager_projects_reject', 'vp_reject'):
            with self.subTest(action=action):
                response = self.client.post(self.url + action + '/', {'reason': 'Out of turn'}, format='json')
                self.assertIn(response.status_code, (400, 403), response.data)
                self.pr.refresh_from_db()
                self.assertEqual(self.pr.approval_workflow_config, self.workflow)

    def test_correct_profile_signature_is_saved_and_ceo_unlocks_only_after_lower_level(self):
        self.client.force_authenticate(self.procurement)
        first = self.client.post(self.url + 'process_dynamic_approval/', {'signature': 'ceo-forged-signature'}, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        self.pr.refresh_from_db()
        row = self.pr.approval_workflow_config[0]
        self.assertEqual(row['approved_by_id'], str(self.procurement.pk))
        self.assertEqual(row['signature_user_id'], str(self.procurement.pk))
        self.assertEqual(row['signature'], self.procurement.rbac_profile.signature_image)
        self.assertFalse(self.pr.vp_op_signature)
        self.assertTrue(self.serializer(self.ceo).data['can_approve'])
        self.client.force_authenticate(self.ceo)
        last = self.client.post(self.url + 'process_dynamic_approval/', {}, format='json')
        self.assertEqual(last.status_code, 200, last.data)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'approved')
        self.assertEqual(self.pr.approval_workflow_config[0], row)
        self.assertEqual(self.pr.approval_workflow_config[1]['signature'], self.ceo.rbac_profile.signature_image)

    def test_known_wrong_signer_is_flagged_and_does_not_unlock_ceo(self):
        self.pr.approval_workflow_config[0].update(
            status='approved', signature='wrong-person-signature',
            approved_by_id=str(self.ceo.pk), approved_by_name='Wrong signer',
        )
        self.pr.save(update_fields=['approval_workflow_config'])
        data = self.serializer(self.ceo).data
        self.assertFalse(data['can_approve'])
        row = data['approval_workflow_config'][0]
        self.assertTrue(row['signature_review_required'])
        self.assertEqual(row['signature'], '')
        self.assertEqual(row['approved_by_id'], str(self.ceo.pk))
        self.client.force_authenticate(self.ceo)
        response = self.client.post(self.url + 'process_dynamic_approval/', {}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.approval_workflow_config[0]['signature'], 'wrong-person-signature')

    def test_display_identity_uses_same_email_authority_as_routing(self):
        self.pr.approval_workflow_config[0]['user_id'] = str(self.ceo.pk)
        data = self.serializer(self.procurement).data
        self.assertTrue(data['can_approve'])
        self.assertEqual(data['approval_workflow_config'][0]['user_id'], str(self.procurement.pk))

    def test_stale_edit_preserves_decision_and_signature_committed_after_validation(self):
        serializer = PurchaseRequisitionSerializer(
            self.pr, data={'approval_workflow_config': deepcopy(self.workflow)}, partial=True,
            context={'request': SimpleNamespace(user=self.ceo)},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        updated = RequisitionWorkflowService.approve(self.pr.pk, self.procurement, require_signature=True)
        approved_row = deepcopy(updated.approval_workflow_config[0])
        serializer.save()
        self.pr.refresh_from_db()
        for field in ('status', 'approved_at', 'approved_by_id', 'approved_by_email',
                      'signature', 'signature_user_id', 'signature_user_email'):
            self.assertEqual(self.pr.approval_workflow_config[0][field], approved_row[field])

    def test_ordinary_po_edit_cannot_forge_final_approval_fields(self):
        vendor = Vendor.objects.create(vendor_code='SEQ-API', name='Sequence supplier')
        order = PurchaseOrder.objects.create(po_number='SEQ-API-PO', title='Sequence PO', vendor=vendor, total_amount=100)
        serializer = PurchaseOrderSerializer(order, data={
            'approved_by': self.ceo.pk, 'approved_by_name': 'Forged CEO',
            'approved_by_title': 'CEO', 'approved_date': '2026-09-16',
            'approval_signature': 'forged-signature', 'approval_stamp': 'forged-stamp',
        }, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        order.refresh_from_db()
        self.assertIsNone(order.approved_by_id)
        self.assertIsNone(order.approved_date)
        self.assertFalse(order.approval_signature)
        self.assertFalse(order.approval_stamp)
        self.assertFalse(order.approved_by_name)

    def test_submitted_route_cannot_remove_predecessors_or_move_ceo_forward(self):
        self.client.force_authenticate(self.ceo)
        moved = deepcopy(self.workflow)
        moved[1]['level'] = 0
        for payload in (
            {'approval_workflow_config': [self.workflow[1]]},
            {'approval_workflow_config': moved},
            {'po_applicable': True},
        ):
            with self.subTest(payload=payload):
                response = self.client.patch(self.url, payload, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.pr.refresh_from_db()
                self.assertEqual(self.pr.approval_workflow_config, self.workflow)
                self.assertFalse(self.pr.po_applicable)

    def test_stale_reassignment_cannot_remove_approval_recorded_during_edit(self):
        substitute = get_user_model().objects.create_user('substitute', email='substitute@example.test')
        grant_approval(substitute)
        set_position(substitute, 'Procurement Manager')
        changed = deepcopy(self.workflow)
        changed[0]['user_id'] = str(substitute.pk)
        serializer = PurchaseRequisitionSerializer(
            self.pr, data={'approval_workflow_config': changed}, partial=True,
            context={'request': SimpleNamespace(user=self.ceo)},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        RequisitionWorkflowService.approve(self.pr.pk, self.procurement, require_signature=True)
        with self.assertRaisesMessage(ValidationError, 'recorded decision'):
            serializer.save()
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.approval_workflow_config[0]['approved_by_id'], str(self.procurement.pk))

    def test_sent_po_cannot_remove_lower_approval_level(self):
        vendor = Vendor.objects.create(vendor_code='SEQ-ROUTE', name='Sequence supplier')
        rows = [
            {'stage': 'Technical Approval', 'level': 0, 'user_id': str(self.procurement.pk),
             'approver_email': self.procurement.email, 'status': 'Pending'},
            {'stage': 'Final Management Sign-off', 'level': 5, 'user_id': str(self.ceo.pk),
             'approver_email': self.ceo.email, 'status': 'Pending'},
        ]
        order = PurchaseOrder.objects.create(
            po_number='SEQ-ROUTE-PO', title='Sequence PO', vendor=vendor, total_amount=100,
            status='sent', approval_log=rows,
        )
        serializer = PurchaseOrderSerializer(order, data={'approval_log': [rows[1]]}, partial=True)
        self.assertFalse(serializer.is_valid())
        self.assertIn('cannot be removed', str(serializer.errors))
        reset = PurchaseOrderSerializer(order, data={'status': 'draft'}, partial=True)
        self.assertFalse(reset.is_valid())
        self.assertIn('cannot be reset', str(reset.errors))

    def test_migrated_approved_identity_is_not_falsely_flagged(self):
        self.pr.approval_workflow_config[0].update(
            user_id='999999', approved_by_id='999999', status='approved', signature='historical',
        )
        data = self.serializer(self.ceo).data
        self.assertEqual(data['approval_workflow_config'][0]['signature'], 'historical')
        self.assertNotIn('signature_review_required', data['approval_workflow_config'][0])

    def test_returning_assignee_gets_new_notification_identity(self):
        substitute = get_user_model().objects.create_user('push-substitute', email='push-substitute@example.test')
        grant_approval(substitute)
        set_position(substitute, 'Procurement Manager')
        self.pr.approval_workflow_config[0]['assignment_id'] = 'original-assignment'
        self.pr.save(update_fields=['approval_workflow_config'])

        def reassign(approver):
            self.pr.refresh_from_db()
            workflow = deepcopy(self.pr.approval_workflow_config)
            workflow[0]['user_id'] = str(approver.pk)
            serializer = PurchaseRequisitionSerializer(
                self.pr, data={'approval_workflow_config': workflow}, partial=True,
                context={'request': SimpleNamespace(user=self.ceo)},
            )
            self.assertTrue(serializer.is_valid(), serializer.errors)
            serializer.save()
            self.pr.refresh_from_db()
            return self.pr.approval_workflow_config[0]['assignment_id']

        original = reassign(self.procurement)
        second = reassign(substitute)
        returning = reassign(self.procurement)
        self.assertEqual(original, 'original-assignment')
        self.assertTrue(second)
        self.assertTrue(returning)
        self.assertEqual(len({original, second, returning}), 3)
        self.assertEqual(returning, reassign(self.procurement))
