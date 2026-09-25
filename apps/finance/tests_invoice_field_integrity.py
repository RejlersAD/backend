"""F06: generic incoming-invoice writes cannot manufacture protected evidence.

Production views, serializers, services and secure_module_endpoints are used.
All actors/records/files are synthetic; external HTTP and email are isolated.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from django.urls import include, path, resolve
from django.utils import timezone
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.rbac.models import Module, Organization, Permission, Role, RoleModule, RolePermission, UserProfile, UserRole
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import ModuleActionGuardMixin, secure_module_endpoints
from .models import Approval, AuditLog, Invoice, InvoiceLineItem, InvoicePurchaseOrderAllocation, PayablePayment
from .serializers import InvoiceDetailSerializer
from .views import InvoiceViewSet, submit_approval_decision


router = DefaultRouter()
router.register('invoices', InvoiceViewSet, basename='invoice')
urlpatterns = [
    path('api/v1/finance/', include(router.urls)),
    path('api/v1/finance/approval/<uuid:token>/submit/', submit_approval_decision, name='approval-submit'),
]
secure_module_endpoints(urlpatterns)
BASE = '/api/v1/finance/invoices/'
PDF_BYTES = b'%PDF-1.4\nF06 synthetic source evidence\n%%EOF'


@override_settings(ROOT_URLCONF=__name__)
class InvoiceFieldIntegrityAPITests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        for target in ('requests.sessions.Session.request', 'httpx.Client.send'):
            blocker = patch(target, side_effect=AssertionError('No live financial/provider HTTP'))
            mocked = blocker.start()
            self.addCleanup(blocker.stop)
            self.addCleanup(mocked.assert_not_called)

        self.organization = Organization.objects.create(code='F06-SYNTHETIC', name='F06 synthetic organization')
        self.module, _ = Module.objects.get_or_create(code='finance_incoming', defaults={'name': 'Incoming invoices'})
        ensure_module_actions(Module, Permission, module_ids=[self.module.pk])
        self.editor = self.actor('editor', ('update',))
        self.creator = self.actor('creator', ('create',))
        self.reader = self.actor('reader', ('read',))
        self.approver = self.actor('approver', ('read', 'approve'))
        self.outsider = self.actor('outsider', ())
        self.admin = self.actor('admin', ('read', 'create', 'update', 'approve'))
        self.admin.is_superuser = True
        self.admin.is_staff = True
        self.admin.save(update_fields=['is_superuser', 'is_staff'])
        self.client = APIClient()
        self.client.force_authenticate(self.editor)
        self.source_path = default_storage.save('f06-synthetic/invoice.pdf', ContentFile(PDF_BYTES))
        self.addCleanup(default_storage.delete, self.source_path)
        now = timezone.now()
        self.invoice = Invoice.objects.create(
            invoice_number='F06-ORIGINAL', vendor_name='Synthetic vendor',
            amount=Decimal('100.00'), tax_amount=Decimal('5.00'), total_amount=Decimal('105.00'),
            currency='AED', original_filename='invoice.pdf', file_path=self.source_path,
            submitted_by=self.creator, status='approved', procurement_status='approved_for_payment',
            match_status='manual_matched', manual_review_required=False,
            procurement_reviewed_by=self.approver, procurement_reviewed_at=now,
            finance_reviewed_by=self.approver, finance_reviewed_at=now,
            payment_status='partial', paid_amount=Decimal('25.00'),
            scheduled_payment_date=date(2026, 10, 1), payment_date=date(2026, 9, 23),
            payment_reference='SYNTHETIC-PAST-SETTLEMENT', processed_at=now,
            extracted_text='Synthetic historical extraction', classification_confidence=0.8,
            classification_reasoning='Synthetic stored classification evidence',
        )
        Approval.objects.create(
            invoice=self.invoice, approver_name='Synthetic approver', approver_email=self.approver.email,
            approval_level=1, level_name='Synthetic assigned review', status='approved',
            decision='approve', decision_date=now, comments='Preserve historical decision',
        )
        AuditLog.objects.create(invoice=self.invoice, user=self.approver, action='historical_review',
                                description='Synthetic existing evidence', metadata={'preserve': True})
        PayablePayment.objects.create(invoice=self.invoice, operation='payment', amount=Decimal('25.00'),
                                      currency='AED', effective_date=date(2026, 9, 23),
                                      reference='SYNTHETIC-PAST-SETTLEMENT', created_by=self.approver)
        InvoiceLineItem.objects.create(invoice=self.invoice, line_number=1, description='Historical line',
                                       total_amount=Decimal('105.00'), manually_verified=True)
        self.before = self.snapshot()

    def actor(self, name, actions):
        user = get_user_model().objects.create_user(f'f06-{name}', email=f'{name}@f06.example.test')
        profile, _ = UserProfile.objects.get_or_create(
            user=user, defaults={'organization': self.organization, 'status': 'active'},
        )
        if actions:
            role = Role.objects.create(code=f'f06_{name}', name=f'F06 {name}', level=5)
            RoleModule.objects.create(role=role, module=self.module)
            for permission in self.module.permissions.filter(action__in=actions):
                RolePermission.objects.create(role=role, permission=permission)
            UserRole.objects.create(user_profile=profile, role=role)
        return user

    def snapshot(self):
        return {
            model.__name__: deepcopy(list(model.objects.order_by('pk').values()))
            for model in (Invoice, Approval, AuditLog, PayablePayment, InvoiceLineItem, InvoicePurchaseOrderAllocation)
        }

    def assert_unchanged(self, expected=None):
        self.assertEqual(self.snapshot(), self.before if expected is None else expected)
        with default_storage.open(self.source_path, 'rb') as stored:
            self.assertEqual(stored.read(), PDF_BYTES)

    def url(self, invoice=None, action=''):
        return f'{BASE}{(invoice or self.invoice).pk}/{action}'

    def metadata(self, number='F06-METADATA'):
        return {
            'invoice_number': number, 'vendor_name': 'Corrected synthetic vendor',
            'original_filename': 'invoice.pdf', 'file_path': self.source_path,
            'invoice_date': '2026-09-20', 'received_date': '2026-09-21', 'due_date': '2026-10-20',
            'payment_terms': 'Recorded supplier terms', 'po_reference_text': 'SYN-PO-001',
            'amount': '100.00', 'tax_amount': '5.00', 'total_amount': '105.00', 'currency': 'AED',
            'vat_percentage': '5.00', 'vat_registration_number': 'SYNTHETIC-TRN', 'invoice_type': 'finance',
        }

    def protected_payloads(self):
        # Explicit contract examples, deliberately independent of the production
        # tuple so an accidentally removed protection remains a regression.
        return {
            'status': 'pending_extraction', 'procurement_status': 'approved_for_payment',
            'match_status': 'auto_matched', 'payment_status': 'scheduled',
            'manual_review_required': False,
            'procurement_reviewed_by': self.editor.pk, 'procurement_reviewed_at': '2026-09-01T00:00:00Z',
            'finance_reviewed_by': self.editor.pk, 'finance_reviewed_at': '2026-09-01T00:00:00Z',
            'scheduled_payment_date': '2026-10-12', 'payment_date': '2026-09-12',
            'payment_reference': 'SYNTHETIC-FORGED', 'paid_amount': '105.00',
            'processed_at': '2026-09-01T00:00:00Z', 'submitted_by': self.editor.pk,
            'approvals': [], 'audit_logs': [], 'payment_operations': [],
            'po_allocations': [], 'structured_line_items': [],
            'extracted_text': 'Replacement extraction', 'classification_confidence': 1,
            'classification_reasoning': 'Replacement classification',
            'confirmed_po_references': [{'po_number': 'SYNTHETIC-FORGED-PO'}],
            'procurement_reviewed_by_id': self.editor.pk, 'finance_reviewed_by_id': self.editor.pk,
            'submitted_by_id': self.editor.pk,
        }

    def test_routes_use_the_actual_central_guard(self):
        for url in (BASE, self.url(), self.url(action='reconcile-status/'), self.url(action='payment-operations/')):
            self.assertTrue(issubclass(resolve(url).func.cls, ModuleActionGuardMixin))

    def test_read_contract_retains_states_reviews_and_payment_evidence(self):
        self.client.force_authenticate(self.reader)
        detail = self.client.get(self.url())
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data['payment_status'], 'partial')
        self.assertEqual(detail.data['paid_amount'], '25.00')
        self.assertEqual(detail.data['procurement_status'], 'approved_for_payment')
        self.assertEqual(detail.data['approvals'][0]['comments'], 'Preserve historical decision')
        self.assertEqual(detail.data['payment_operations'][0]['reference'], 'SYNTHETIC-PAST-SETTLEMENT')
        listing = self.client.get(BASE)
        self.assertEqual(listing.status_code, 200, listing.data)
        rows = listing.data['results'] if isinstance(listing.data, dict) else listing.data
        self.assertEqual(rows[0]['payment_status'], 'partial')
        self.assert_unchanged()

    def test_update_only_metadata_patch_preserves_all_other_state_and_evidence(self):
        response = self.client.patch(self.url(), {'vendor_name': 'Corrected vendor', 'due_date': '2026-10-15'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        expected = deepcopy(self.before)
        expected['Invoice'][0].update(vendor_name='Corrected vendor', due_date=date(2026, 10, 15))
        expected['Invoice'][0]['updated_at'] = Invoice.objects.get(pk=self.invoice.pk).updated_at
        self.assert_unchanged(expected)

    def test_update_only_full_put_keeps_protected_fields_when_omitted(self):
        response = self.client.put(self.url(), self.metadata(), format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.invoice_number, 'F06-METADATA')
        self.assertEqual(self.invoice.due_date, date(2026, 10, 20))
        after = self.snapshot()
        for key in self.before:
            if key != 'Invoice':
                self.assertEqual(after[key], self.before[key])
        for name in self.protected_payloads():
            field = name if name in self.before['Invoice'][0] else f'{name}_id'
            if field in self.before['Invoice'][0]:
                self.assertEqual(after['Invoice'][0][field], self.before['Invoice'][0][field], name)

    def test_create_stamps_authenticated_actor_and_server_defaults(self):
        self.client.force_authenticate(self.creator)
        response = self.client.post(BASE, self.metadata('F06-CREATED'), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        created = Invoice.objects.get(pk=response.data['id'])
        self.assertEqual(created.submitted_by_id, self.creator.pk)
        self.assertEqual((created.status, created.procurement_status, created.match_status, created.payment_status),
                         ('pending_extraction', 'ocr_review', 'unmatched', 'not_scheduled'))
        self.assertTrue(created.manual_review_required)
        self.assertEqual(created.paid_amount, Decimal('0'))
        for name in ('procurement_reviewed_by_id', 'procurement_reviewed_at', 'finance_reviewed_by_id',
                     'finance_reviewed_at', 'scheduled_payment_date', 'payment_date', 'processed_at'):
            self.assertIsNone(getattr(created, name), name)
        self.assertEqual(created.payment_reference, '')
        self.assertFalse(created.approvals.exists())
        self.assertFalse(created.payment_operations.exists())

    def test_each_protected_field_is_rejected_by_patch_without_partial_metadata_save(self):
        for field, value in self.protected_payloads().items():
            with self.subTest(field=field):
                response = self.client.patch(self.url(), {'vendor_name': 'Must not save', field: value}, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn(field, response.data)
                self.assert_unchanged()

    def test_each_protected_field_is_rejected_by_put_without_partial_metadata_save(self):
        for field, value in self.protected_payloads().items():
            with self.subTest(field=field):
                response = self.client.put(self.url(), {**self.metadata(), field: value}, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn(field, response.data)
                self.assert_unchanged()

    def test_each_protected_field_is_rejected_by_create_without_partial_record(self):
        self.client.force_authenticate(self.creator)
        for field, value in self.protected_payloads().items():
            with self.subTest(field=field):
                response = self.client.post(BASE, {**self.metadata('F06-REJECTED'), field: value}, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn(field, response.data)
                self.assert_unchanged()

    def test_protected_null_default_and_unchanged_values_are_not_silent_success(self):
        cases = [
            {'paid_amount': '25.00'}, {'manual_review_required': False}, {'payment_status': 'partial'},
            {'procurement_status': 'approved_for_payment'}, {'payment_reference': 'SYNTHETIC-PAST-SETTLEMENT'},
            {'procurement_reviewed_by': None}, {'finance_reviewed_at': None}, {'payment_date': None},
            {'scheduled_payment_date': None}, {'processed_at': None}, {'approvals': []},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                response = self.client.patch(self.url(), payload, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assert_unchanged()
        self.client.force_authenticate(self.creator)
        for payload in ({'paid_amount': '0.00'}, {'payment_status': 'not_scheduled'}, {'manual_review_required': True},
                        {'status': 'pending_extraction'}, {'procurement_status': 'ocr_review'}, {'payment_date': None}):
            response = self.client.post(BASE, {**self.metadata('F06-DEFAULT-REJECTED'), **payload}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
            self.assert_unchanged()

    def test_terminal_payloads_still_require_central_approval_permission_before_validation(self):
        for field, value in (('status', 'approved'), ('payment_status', 'paid'), ('procurement_status', 'rejected')):
            response = self.client.patch(self.url(), {'vendor_name': 'Must not save', field: value}, format='json')
            self.assertEqual(response.status_code, 403, response.data)
            self.assertIn('approve permission', str(response.data))
            self.assert_unchanged()

    def test_superuser_cannot_bypass_generic_field_contract_with_approval_access(self):
        self.client.force_authenticate(self.admin)
        for field, value in {**self.protected_payloads(), 'status': 'approved', 'payment_status': 'paid'}.items():
            with self.subTest(field=field):
                response = self.client.patch(self.url(), {'vendor_name': 'Must not save', field: value}, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn(field, response.data)
                self.assert_unchanged()

    def test_multiple_protected_fields_report_errors_together_without_any_save(self):
        payload = {'vendor_name': 'Must not save', 'paid_amount': '55.00', 'payment_date': '2026-09-24',
                   'procurement_status': 'approved_for_payment', 'manual_review_required': False}
        response = self.client.patch(self.url(), payload, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        for field in ('paid_amount', 'payment_date', 'procurement_status', 'manual_review_required'):
            self.assertIn(field, response.data)
        self.assert_unchanged()

    def test_multipart_patch_and_put_cannot_bypass_field_checks(self):
        for method in ('patch', 'put'):
            payload = {**self.metadata(), 'paid_amount': '105.00', 'finance_reviewed_by_id': str(self.editor.pk)}
            response = getattr(self.client, method)(self.url(), payload, format='multipart')
            self.assertEqual(response.status_code, 400, response.data)
            self.assertIn('paid_amount', response.data)
            self.assertIn('finance_reviewed_by_id', response.data)
            self.assert_unchanged()

    def test_allowed_field_validation_failure_does_not_save_other_metadata(self):
        response = self.client.patch(self.url(), {'vendor_name': 'Must not save', 'due_date': 'invalid-date'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('due_date', response.data)
        self.assert_unchanged()

    def test_action_grants_remain_separate(self):
        self.assertEqual(self.client.post(BASE, self.metadata('F06-DENIED'), format='json').status_code, 403)
        for actor in (self.reader, self.creator):
            self.client.force_authenticate(actor)
            response = self.client.patch(self.url(), {'vendor_name': 'Must not save'}, format='json')
            self.assertEqual(response.status_code, 403, response.data)
        self.assert_unchanged()

    def test_missing_grants_and_anonymous_requests_cannot_read_or_edit(self):
        for actor in (self.outsider, None):
            self.client.force_authenticate(actor)
            self.assertIn(self.client.get(self.url()).status_code, (401, 403))
            self.assertIn(self.client.patch(self.url(), {'paid_amount': '105'}, format='json').status_code, (401, 403))
        self.assert_unchanged()

    def test_serializer_contract_exposes_protected_values_as_read_only(self):
        serializer = InvoiceDetailSerializer()
        for name in self.protected_payloads():
            if name in serializer.fields:
                self.assertTrue(serializer.fields[name].read_only, name)
        for name in ('invoice_number', 'vendor_name', 'due_date', 'payment_terms', 'total_amount', 'currency'):
            self.assertFalse(serializer.fields[name].read_only, name)

    def test_metadata_save_does_not_revert_protected_state_changed_after_object_load(self):
        original_update = InvoiceDetailSerializer.update
        now = timezone.now()

        def interleaved_update(serializer, instance, validated_data):
            # Deterministic interleaving: another domain operation persisted
            # after get_object but before generic metadata persistence.
            Invoice.objects.filter(pk=instance.pk).update(
                paid_amount=Decimal('65.00'), payment_reference='SYNTHETIC-CONCURRENT',
                payment_date=date(2026, 9, 24), finance_reviewed_at=now,
            )
            return original_update(serializer, instance, validated_data)

        with patch.object(InvoiceDetailSerializer, 'update', interleaved_update):
            response = self.client.patch(self.url(), {'vendor_name': 'Allowed late metadata'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.vendor_name, 'Allowed late metadata')
        self.assertEqual(self.invoice.paid_amount, Decimal('65.00'))
        self.assertEqual(self.invoice.payment_reference, 'SYNTHETIC-CONCURRENT')
        self.assertEqual(self.invoice.finance_reviewed_at, now)
        self.assertEqual(response.data['paid_amount'], '65.00')
        self.assertEqual(response.data['payment_reference'], 'SYNTHETIC-CONCURRENT')

    def test_existing_reconciliation_command_still_updates_derived_state_with_audit(self):
        Invoice.objects.filter(pk=self.invoice.pk).update(payment_status='not_scheduled')
        response = self.client.post(self.url(action='reconcile-status/'), {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.payment_status, 'partial')
        self.assertEqual(self.invoice.paid_amount, Decimal('25.00'))
        self.assertTrue(self.invoice.audit_logs.filter(action='ap_status_reconciled').exists())
        self.assertEqual(self.invoice.approvals.get().comments, 'Preserve historical decision')
        self.assertEqual(self.invoice.payment_operations.count(), 1)

    def test_f07_payment_command_remains_denied_without_business_registration(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(self.url(action='payment-operations/'), {
            'operation': 'payment', 'amount': '80.00', 'effective_date': '2026-09-24',
            'reference': 'SYNTHETIC-NOT-EXECUTED',
        }, format='json')
        self.assertEqual(response.status_code, 403, response.data)
        self.assertIn('No verified business approval route', str(response.data))
        self.assert_unchanged()

    def test_assigned_approval_domain_command_remains_authorized_and_records_decision(self):
        # Existing legacy approval URL additionally uses the finance_salary
        # route prefix. Preserve that current guard; do not change its policy.
        module, _ = Module.objects.get_or_create(code='finance_salary', defaults={'name': 'Salary service'})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        role = self.approver.rbac_profile.roles.get(code='f06_approver')
        RoleModule.objects.create(role=role, module=module)
        for permission in module.permissions.filter(action='approve'):
            RolePermission.objects.create(role=role, permission=permission)
        awaiting = Invoice.objects.create(
            invoice_number='F06-AWAITING-REVIEW', original_filename='invoice.pdf', file_path=self.source_path,
            status='pending_approval', submitted_by=self.creator, total_amount=Decimal('105.00'),
        )
        approval = Approval.objects.create(
            invoice=awaiting, approver_name='Synthetic assigned reviewer', approver_email=self.approver.email,
            approval_level=1, level_name='Synthetic review', status='pending',
        )
        url = f'/api/v1/finance/approval/{approval.approval_token}/submit/'
        self.assertTrue(issubclass(resolve(url).func.cls, ModuleActionGuardMixin))
        self.client.force_authenticate(self.approver)
        with patch('apps.finance.services.workflow_service.PDFExtractor'), \
                patch('apps.finance.services.workflow_service.InvoiceClassifier'), \
                patch('apps.finance.services.workflow_service.EmailService'):
            response = self.client.post(url, {'decision': 'approve', 'comments': 'Synthetic authorized decision'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        approval.refresh_from_db()
        awaiting.refresh_from_db()
        self.assertEqual((approval.status, approval.decision, awaiting.status), ('approved', 'approve', 'approved'))
        self.assertEqual(approval.comments, 'Synthetic authorized decision')
        self.assertIsNotNone(approval.decision_date)
        self.assertTrue(awaiting.audit_logs.filter(action='approval_approve').exists())
        self.assertTrue(awaiting.audit_logs.filter(action='fully_approved').exists())
        self.assertEqual(awaiting.paid_amount, Decimal('0'))
        self.assertFalse(awaiting.payment_operations.exists())
