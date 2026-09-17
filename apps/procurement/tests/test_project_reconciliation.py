"""Evidence-backed reconciliation, persistent exceptions and guarded mutations."""

from decimal import Decimal
from importlib import import_module
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import connection, models
from django.db.migrations.state import ModelState, ProjectState
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.core.project_models import Project as EnterpriseProject
from apps.finance.models import Invoice, InvoicePurchaseOrderAllocation
from apps.procurement.models import ProjectRelationshipResolution, PurchaseOrder, PurchaseRequisition, Vendor
from apps.procurement.services.project_relationships import (
    build_project_reconciliation_payload, resolve_project_relationship, save_project_relationship_exception,
)
from apps.procurement.views import ProjectViewSet
from apps.rbac.models import Module, Organization, Permission, Role, RoleModule, RolePermission, UserProfile, UserRole
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints


router = DefaultRouter()
router.register('projects', ProjectViewSet, basename='reconciliation-project')
urlpatterns = [path('api/v1/procurement/', include(router.urls))]
secure_module_endpoints(urlpatterns)
BASE = '/api/v1/procurement/projects/'


class ReconciliationPayloadTests(TestCase):
    def setUp(self):
        self.alpha = EnterpriseProject.objects.create(code='ALPHA-100', name='Alpha gas compression', currency='AED')
        self.beta = EnterpriseProject.objects.create(code='BETA-200', name='Beta cooling system', currency='USD')
        self.vendor = Vendor.objects.create(vendor_code='RECON-V', name='Reconciliation supplier')

    def requisition(self, **changes):
        values = {'pr_number': 'RECON-PR', 'total_price': Decimal('100.10'), 'currency': 'AED'}
        values.update(changes)
        return PurchaseRequisition.objects.create(**values)

    def invoice(self, **changes):
        values = {'invoice_number': 'RECON-INV', 'amount': Decimal('25.25'), 'currency': 'USD',
                  'original_filename': 'invoice.pdf', 'file_path': 'tests/invoice.pdf'}
        values.update(changes)
        return Invoice.objects.create(**values)

    def row(self, record):
        return next(row for row in build_project_reconciliation_payload()['unresolved'] if row['id'] == str(record.pk))

    def test_multiple_explicit_projects_provide_explained_suggestions_without_linking(self):
        requisition = self.requisition(project_details=[
            {'project_number': self.alpha.code}, {'project_number': self.beta.code},
        ])
        payload = build_project_reconciliation_payload()
        row = payload['unresolved'][0]
        self.assertTrue(payload['generated_at'])
        self.assertEqual(row['created_at'], requisition.created_at.isoformat())
        self.assertIsNone(row['current_project'])
        self.assertEqual({item['id'] for item in row['suggested_projects']}, {str(self.alpha.pk), str(self.beta.pk)})
        for suggestion in row['suggested_projects']:
            self.assertEqual(suggestion['match_strength'], 'high')
            self.assertIn('Exact project code reference', suggestion['reasons'][0])
            self.assertNotIn('confidence', suggestion)
        requisition.refresh_from_db()
        self.assertIsNone(requisition.enterprise_project_id)

    def test_suggestions_are_limited_and_absent_when_there_is_no_evidence(self):
        projects = [self.alpha, self.beta] + [EnterpriseProject.objects.create(code=f'X-{index}', name=f'Other {index}') for index in range(2)]
        referenced = self.requisition(project_details=[{'project_number': project.code} for project in projects])
        blank = self.requisition(pr_number='RECON-BLANK')
        self.assertEqual(len(self.row(referenced)['suggested_projects']), 3)
        self.assertEqual(self.row(blank)['suggested_projects'], [])

    def test_exact_project_name_is_medium_evidence_and_shared_words_are_low(self):
        exact = self.requisition(project_details=[{'project_name': self.alpha.name}])
        shared = self.requisition(pr_number='RECON-SHARED', title='Alpha gas study')
        self.assertEqual(self.row(exact)['suggested_projects'][0]['match_strength'], 'medium')
        self.assertEqual(self.row(shared)['suggested_projects'][0]['match_strength'], 'low')

    def test_totals_keep_currencies_separate_and_sample_coverage_is_explicit(self):
        self.requisition()
        self.requisition(pr_number='RECON-OTHER', total_price=Decimal('50.20'))
        self.invoice()
        payload = build_project_reconciliation_payload()
        summary = payload['summary']
        self.assertEqual(summary['unresolved_amounts_by_currency'], [
            {'currency': 'AED', 'amount': 150.30, 'record_count': 2},
            {'currency': 'USD', 'amount': 25.25, 'record_count': 1},
        ])
        self.assertTrue(summary['sample_complete'])
        sampled = build_project_reconciliation_payload(sample_limit=1)['summary']
        self.assertEqual(sampled['unresolved_total'], 3)
        self.assertEqual(sampled['sample_count'], 2)
        self.assertFalse(sampled['sample_complete'])
        self.assertEqual(sampled['amount_totals_scope'], 'returned_records')
        self.assertEqual(sampled['suggestion_scope'], 'returned_records')

    def test_exception_persists_and_does_not_change_link_or_commercial_amount(self):
        requisition = self.requisition()
        save_project_relationship_exception(record_type='purchase_requisition', record_id=requisition.pk,
            reason='Awaiting the signed project allocation schedule.', user=None)
        requisition.refresh_from_db()
        self.assertIsNone(requisition.enterprise_project_id)
        self.assertEqual(requisition.total_price, Decimal('100.10'))
        self.assertIn('signed project', self.row(requisition)['exception']['reason'])
        payload = build_project_reconciliation_payload()
        self.assertEqual(payload['recent_resolutions'][0]['record_identifier'], requisition.pr_number)
        self.assertEqual(payload['recent_resolutions'][0]['resolution'], 'exception')

    def test_invoice_exception_uses_numeric_identity_without_creating_po_allocations(self):
        invoice = self.invoice()
        save_project_relationship_exception(record_type='invoice', record_id=invoice.pk,
            reason='Supplier has not provided the PO reference.', user=None)
        audit = ProjectRelationshipResolution.objects.get()
        self.assertEqual(audit.record_id, str(invoice.pk))
        self.assertFalse(InvoicePurchaseOrderAllocation.objects.filter(invoice=invoice).exists())
        invoice.refresh_from_db()
        self.assertEqual(invoice.amount, Decimal('25.25'))
        self.assertTrue(self.row(invoice)['exception'])

    def test_invoice_suggestion_uses_real_po_reference_and_leaves_invoice_unmatched(self):
        order = PurchaseOrder.objects.create(po_number='PO-RECON-EVIDENCE', title='Evidence PO',
            vendor=self.vendor, total_amount=100, enterprise_project=self.alpha)
        invoice = self.invoice(po_reference_text=order.po_number)
        suggestion = self.row(invoice)['suggested_projects'][0]
        self.assertEqual(suggestion['id'], str(self.alpha.pk))
        self.assertIn(order.po_number, suggestion['reasons'][0])
        self.assertFalse(invoice.po_allocations.exists())

    def test_resolution_history_reports_actual_before_and_after_projects(self):
        requisition = self.requisition(enterprise_project=self.alpha)
        resolve_project_relationship(record_type='purchase_requisition', record_id=requisition.pk,
            enterprise_project_id=self.beta.pk, expected_project_id=self.alpha.pk, user=None, reason='Confirmed transfer')
        history = build_project_reconciliation_payload()['recent_resolutions'][0]
        self.assertEqual(history['record_identifier'], requisition.pr_number)
        self.assertEqual(history['previous_enterprise_project_code'], self.alpha.code)
        self.assertEqual(history['enterprise_project_code'], self.beta.code)

    def test_stale_expected_project_cannot_overwrite_a_concurrent_different_link(self):
        requisition = self.requisition(enterprise_project=self.beta)
        with self.assertRaisesMessage(ValidationError, 'different project'):
            resolve_project_relationship(record_type='purchase_requisition', record_id=requisition.pk,
                enterprise_project_id=self.alpha.pk, expected_project_id=None, user=None)
        requisition.refresh_from_db()
        self.assertEqual(requisition.enterprise_project_id, self.beta.pk)
        self.assertFalse(ProjectRelationshipResolution.objects.exists())


@override_settings(ROOT_URLCONF=__name__)
class ReconciliationAuthorizationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.module, _ = Module.objects.get_or_create(code='procurement', defaults={'name': 'Procurement'})
        ensure_module_actions(Module, Permission, module_ids=[self.module.pk])
        organization = Organization.objects.create(code='recon-auth', name='Reconciliation authorization')
        self.reader = self.user('reader', organization, ['read'])
        self.editor = self.user('editor', organization, ['read', 'update'])
        self.requisition = PurchaseRequisition.objects.create(pr_number='RECON-AUTH', total_price=123)
        self.project = EnterpriseProject.objects.create(code='RECON-AUTH', name='Auth project')
        self.client = APIClient()

    def user(self, name, organization, actions):
        user = get_user_model().objects.create_user(f'recon-{name}', email=f'recon-{name}@example.test')
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': organization})
        role = Role.objects.create(code=f'recon-{name}', name=f'Reconciliation {name}', level=3)
        UserRole.objects.create(user_profile=profile, role=role)
        RoleModule.objects.create(role=role, module=self.module)
        for permission in self.module.permissions.filter(action__in=actions, is_active=True):
            RolePermission.objects.create(role=role, permission=permission)
        return user

    def data(self, **changes):
        data = {'record_type': 'purchase_requisition', 'record_id': str(self.requisition.pk), 'reason': 'Needs source evidence'}
        data.update(changes)
        return data

    def test_read_only_operator_can_view_but_cannot_save_exception_or_resolve(self):
        self.client.force_authenticate(self.reader)
        self.assertEqual(self.client.get(BASE + 'relationship-report/').status_code, 200)
        for action, data in (
            ('relationship-exception/', self.data()),
            ('resolve-relationship/', self.data(enterprise_project_id=str(self.project.pk), expected_project_id=None)),
        ):
            with self.subTest(action=action):
                self.assertEqual(self.client.post(BASE + action, data, format='json').status_code, 403)
        self.assertFalse(ProjectRelationshipResolution.objects.exists())

    def test_editor_can_save_exception_through_registered_action_guard(self):
        self.client.force_authenticate(self.editor)
        response = self.client.post(BASE + 'relationship-exception/', self.data(), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(ProjectRelationshipResolution.objects.get().resolved_by, self.editor)
        self.requisition.refresh_from_db()
        self.assertIsNone(self.requisition.enterprise_project_id)
        self.assertEqual(self.requisition.total_price, 123)

    def test_anonymous_and_invalid_reason_cannot_create_exception(self):
        response = self.client.post(BASE + 'relationship-exception/', self.data(), format='json')
        self.assertIn(response.status_code, (401, 403))
        self.client.force_authenticate(self.editor)
        for reason in ('', 'x' * 501):
            response = self.client.post(BASE + 'relationship-exception/', self.data(reason=reason), format='json')
            self.assertEqual(response.status_code, 400, response.data)
            self.assertIn('reason', response.data)
        self.assertFalse(ProjectRelationshipResolution.objects.exists())

    def test_editor_resolution_honors_expected_link_and_reason_length(self):
        self.client.force_authenticate(self.editor)
        data = self.data(enterprise_project_id=str(self.project.pk), expected_project_id=None, reason='x' * 501)
        self.assertEqual(self.client.post(BASE + 'resolve-relationship/', data, format='json').status_code, 400)
        data['reason'] = 'Verified against project register'
        response = self.client.post(BASE + 'resolve-relationship/', data, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['changed'])


class RelationshipAuditMigrationTests(TransactionTestCase):
    def test_uuid_audit_identity_is_preserved_when_invoice_ids_are_enabled(self):
        state = ProjectState()
        state.add_model(ModelState('procurement', 'ProjectRelationshipResolution', [
            ('id', models.UUIDField(primary_key=True, default=uuid4)),
            ('record_id', models.UUIDField(db_index=True)),
            ('record_type', models.CharField(max_length=30)),
            ('resolution', models.CharField(max_length=20, default='manual')),
        ], options={'db_table': 'test_reconciliation_legacy_audit'}))
        legacy_model = state.apps.get_model('procurement', 'ProjectRelationshipResolution')
        with connection.schema_editor() as editor:
            editor.create_model(legacy_model)
        migrated_model = legacy_model
        try:
            record_id = uuid4()
            legacy = legacy_model.objects.create(record_id=record_id, record_type='purchase_requisition')
            migration = import_module('apps.procurement.migrations.0043_project_relationship_exceptions').Migration('0043', 'procurement')
            with connection.schema_editor() as editor:
                final_state = migration.apply(state, editor)
            migrated_model = final_state.apps.get_model('procurement', 'ProjectRelationshipResolution')
            self.assertEqual(migrated_model.objects.get(pk=legacy.pk).record_id, str(record_id))
            migrated_model.objects.create(record_id='123', record_type='invoice', resolution='exception')
            self.assertTrue(migrated_model.objects.filter(record_id='123', record_type='invoice').exists())
        finally:
            with connection.schema_editor() as editor:
                editor.delete_model(migrated_model)
