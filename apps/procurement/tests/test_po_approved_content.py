"""Commercial writes and approval evidence stay bound to the reviewed PO."""

from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.exceptions import ValidationError
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.procurement.models import PurchaseOrder, PurchaseRequisition, Vendor
from apps.procurement.serializers import PurchaseOrderSerializer
from apps.procurement.views import PurchaseOrderViewSet
from apps.procurement.services.approval_integrity import purchase_order_signature_issue
from apps.procurement.services.po_excel_import import import_po_workbook
from apps.procurement.services.purchase_order_approvals import record_decision
from apps.procurement.tests.approval_fixtures import grant_approval, set_position
from apps.procurement.tests.tests_po_excel_import import workbook_upload
from apps.rbac.models import Module, Permission, Role, RoleModule, RolePermission, UserRole, UserProfile
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints


router = DefaultRouter()
router.register('orders', PurchaseOrderViewSet, basename='content-order')
urlpatterns = [path('api/v1/procurement/', include(router.urls))]
secure_module_endpoints(urlpatterns)
BASE = '/api/v1/procurement/orders/'
SIGNATURE = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j8uoAAAAASUVORK5CYII='


@override_settings(ROOT_URLCONF=__name__, TEAMS_APPROVAL_WEBHOOK_URL='', WEB_PUSH_VAPID_PRIVATE_KEY='')
class PurchaseOrderApprovedContentTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        for target in ('apps.notifications.services.send_notification_email.delay',
                       'apps.notifications.teams.send_teams_approval_assignment.delay',
                       'apps.notifications.services.send_web_push_notification.delay'):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        users = get_user_model()
        self.editor = users.objects.create_user('content-editor', email='editor@content.example.test')
        self.signer = users.objects.create_user('content-signer', email='signer@content.example.test')
        self.next_signer = users.objects.create_user('content-next', email='next@content.example.test')
        for actor in (self.signer, self.next_signer):
            grant_approval(actor, 'procurement_orders')
            set_position(actor, 'Engineer')
            profile = actor.rbac_profile
            profile.signature_image = SIGNATURE
            profile.save(update_fields=['signature_image'])
        module = Module.objects.get(code='procurement_orders')
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        role = Role.objects.create(code='content-editor', name='Content editor', level=3)
        RoleModule.objects.create(role=role, module=module)
        for permission in module.permissions.filter(action__in=['read', 'create', 'update']):
            RolePermission.objects.create(role=role, permission=permission)
        editor_profile, _ = UserProfile.objects.get_or_create(
            user=self.editor, defaults={'organization': self.signer.rbac_profile.organization},
        )
        UserRole.objects.create(user_profile=editor_profile, role=role)
        self.vendor = Vendor.objects.create(vendor_code='CONTENT', name='Content supplier')
        self.pr = PurchaseRequisition.objects.create(pr_number='RAD-GEN-PR-0091_2026', status='approved')
        self.order = PurchaseOrder.objects.create(
            po_number='RAD-GEN-PUR-0091_2026', vendor=self.vendor, pr_reference=self.pr,
            title='Engineering study', category='other', created_by=self.editor,
            total_amount=100, net_amount=100, tax_amount=0, vat_percentage=0,
            vat_basis='none', currency='AED', approval_log=[self.stage(self.signer)],
        )
        self.url = f'{BASE}{self.order.pk}/'
        self.client = APIClient()
        self.client.force_authenticate(self.editor)

    @staticmethod
    def stage(actor, level=0):
        return {'stage': f'Technical Approval {level}', 'level': level, 'user_id': str(actor.pk),
                'approver_email': actor.email, 'status': 'Pending', 'business_position': 'engineer'}

    def approve(self, actor=None):
        self.order, _ = record_decision(self.order, actor or self.signer, 'approve', require_signature=True)
        return self.order

    def test_amount_change_after_approval_is_rejected_without_altering_signature(self):
        self.approve()
        evidence = deepcopy(self.order.approval_log)
        approved_at = self.order.approved_at
        response = self.client.patch(self.url, {'entered_amount': '100000', 'vat_basis': 'none'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.total_amount, Decimal('100'))
        self.assertEqual(self.order.approval_log, evidence)
        self.assertEqual(self.order.approval_signature, SIGNATURE)
        self.assertEqual(self.order.approved_at, approved_at)
        self.assertIn('revised purchase order', str(response.data))

    def test_partial_approval_already_locks_commercial_terms(self):
        self.order.approval_log.append(self.stage(self.next_signer, 1))
        self.order.save(update_fields=['approval_log'])
        self.approve()
        self.assertIsNone(self.order.approved_at)
        response = self.client.patch(self.url, {'payment_terms': 'New payment terms'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_terms, '')
        self.assertEqual(self.order.approval_log[1]['status'], 'Pending')

    def test_draft_commercial_edits_without_decisions_remain_allowed(self):
        response = self.client.patch(self.url, {'entered_amount': '125', 'vat_basis': 'none'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.total_amount, Decimal('125'))
        self.assertFalse(response.data['commercial_edit_locked'])

    def test_identical_money_and_internal_notes_remain_editable(self):
        self.approve()
        response = self.client.patch(self.url, {
            'entered_amount': '100.00', 'vat_basis': 'none', 'notes': 'Delivery follow-up',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['commercial_edit_locked'])
        self.assertEqual(response.data['notes'], 'Delivery follow-up')
        self.assertTrue(response.data['can_send_to_vendor'])
        self.assertTrue(response.data['can_complete'])

    def test_stale_validated_edit_cannot_overwrite_concurrent_approval(self):
        serializer = PurchaseOrderSerializer(self.order, data={'entered_amount': '150', 'vat_basis': 'none'},
                                             partial=True, context={'request': SimpleNamespace(user=self.editor)})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.approve()
        with self.assertRaises(ValidationError):
            serializer.save()
        self.order.refresh_from_db()
        self.assertEqual(self.order.total_amount, Decimal('100'))
        self.assertEqual(self.order.approval_log[0]['status'], 'Approved')

    def test_legacy_approvals_without_fingerprint_still_protect_terms(self):
        self.order.approval_log[0]['status'] = 'Approved'
        self.order.save(update_fields=['approval_log'])
        for payload in ({'vendor': str(Vendor.objects.create(vendor_code='OTHER', name='Other').pk)},
                        {'scope_of_services': 'Different scope'}, {'items': [{'description': 'Changed goods'}]},
                        {'currency': 'USD', 'entered_amount': '100', 'vat_basis': 'none'}):
            with self.subTest(payload=payload):
                response = self.client.patch(self.url, payload, format='json')
                self.assertEqual(response.status_code, 400, response.data)

    def test_verified_source_approval_also_locks_money(self):
        self.order.approval_log = [{'stage': 'Signed PO document approval', 'status': 'Approved',
                                   'evidence_document_id': 'historical-document', 'signature_verified': True}]
        self.order.save(update_fields=['approval_log'])
        response = self.client.patch(self.url, {'entered_amount': '150', 'vat_basis': 'none'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)

    def test_content_change_outside_editor_suppresses_signature_and_progression(self):
        self.approve()
        self.assertTrue(self.order.approval_log[0]['content_fingerprint'].startswith('po-v1:'))
        PurchaseOrder.objects.filter(pk=self.order.pk).update(total_amount=Decimal('100000'))
        self.order.refresh_from_db()
        self.assertIn('commercial details differ', purchase_order_signature_issue(self.order))
        detail = self.client.get(self.url)
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data['approval_signature'], '')
        self.assertEqual(detail.data['approval_log'][0]['signature'], '')
        self.assertTrue(detail.data['signature_review_required'])
        self.assertFalse(detail.data['can_complete'])
        response = self.client.patch(self.url, {'status': 'completed'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)

    def test_later_approval_cannot_certify_changed_earlier_approved_terms(self):
        self.order.approval_log.append(self.stage(self.next_signer, 1))
        self.order.save(update_fields=['approval_log'])
        self.approve()
        PurchaseOrder.objects.filter(pk=self.order.pk).update(total_amount=Decimal('100000'))
        with self.assertRaisesMessage(ValidationError, 'commercial details differ'):
            self.approve(self.next_signer)
        self.order.refresh_from_db()
        self.assertEqual(self.order.approval_log[1]['status'], 'Pending')

    def test_assignment_normalization_retains_server_fingerprint(self):
        self.approve()
        fingerprint = self.order.approval_log[0]['content_fingerprint']
        echoed = deepcopy(self.order.approval_log)
        echoed[0]['content_fingerprint'] = 'client-replacement'
        serializer = PurchaseOrderSerializer(self.order, data={'approval_log': echoed}, partial=True,
                                             context={'request': SimpleNamespace(user=self.signer)})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        self.order.refresh_from_db()
        self.assertEqual(self.order.approval_log[0]['content_fingerprint'], fingerprint)

    @staticmethod
    def editable_route(rows):
        return [{key: row[key] for key in ('stage', 'level', 'user_id', 'business_position') if key in row}
                for row in rows]

    def test_new_duplicate_assignments_are_rejected_on_canonical_identity(self):
        for label in ('Technical Approval 0', ' technical APPROVAL 0 '):
            with self.subTest(label=label):
                route = self.editable_route(self.order.approval_log)
                route.append({**route[0], 'stage': label})
                response = self.client.patch(self.url, {'approval_log': route}, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn('duplicate', str(response.data).lower())
                self.order.refresh_from_db()
                self.assertEqual(len(self.order.approval_log), 1)

    def test_existing_duplicate_route_cannot_erase_partial_approval_and_unlock_money(self):
        self.order.approval_log.append(deepcopy(self.order.approval_log[0]))
        self.order.save(update_fields=['approval_log'])
        self.approve()
        evidence = deepcopy(self.order.approval_log)
        self.assertEqual([row['status'] for row in evidence], ['Approved', 'Pending'])
        response = self.client.patch(self.url, {
            'approval_log': self.editable_route(evidence),
        }, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('duplicate', str(response.data).lower())
        self.order.refresh_from_db()
        self.assertEqual(self.order.approval_log, evidence)
        response = self.client.patch(self.url, {'entered_amount': '100000', 'vat_basis': 'none'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.total_amount, Decimal('100'))

    def test_stage_capitalization_cannot_erase_approval_fingerprint_or_unlock_money(self):
        self.order.approval_log.append(self.stage(self.next_signer, 1))
        self.order.save(update_fields=['approval_log'])
        self.approve()
        evidence = deepcopy(self.order.approval_log[0])
        route = self.editable_route(self.order.approval_log)
        route[0]['stage'] = f" {route[0]['stage'].upper()} "
        response = self.client.patch(self.url, {'approval_log': route}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.order.refresh_from_db()
        for field in ('status', 'signature', 'approved_at', 'approved_by_id', 'content_fingerprint'):
            self.assertEqual(self.order.approval_log[0][field], evidence[field], field)
        response = self.client.patch(self.url, {'entered_amount': '100000', 'vat_basis': 'none'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.total_amount, Decimal('100'))

    def test_workbook_cannot_overwrite_signed_amount_or_approval_route(self):
        self.approve()
        row = [self.order.po_number, self.pr.pr_number, '13.05.2026', self.vendor.name,
               'Engineering study', '', '14.05.2026', None, '', '30 days net',
               100000, 'AED', 100000, 100000, 'UAE', 'Import']
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run):
                result = import_po_workbook(workbook_upload([row]), user=self.editor, dry_run=dry_run)
                self.assertEqual(result['ready_rows'], 0)
                self.assertEqual(result['rows'][0]['status'], 'error')
        self.order.refresh_from_db()
        self.assertEqual(self.order.total_amount, Decimal('100'))
        self.assertEqual(self.order.approval_log[0]['status'], 'Approved')
