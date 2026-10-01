"""Canonical identity reviews preserve source facts and existing access rules."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.core.project_models import Project
from apps.finance.receivables_source_models import (
    ReceivablesSourceIdentity, ReceivablesSourceRow, ReceivablesSourceSnapshot,
)
from apps.finance.shared_record_links import ADAPTERS, canonical_links
from apps.finance import tests_command_center as command_center_tests
from apps.invoice_tracker.models import CustomerInvoice
from apps.invoice_tracker.serializers import CustomerInvoiceSerializer
from apps.rbac.models import Permission, RolePermission
from apps.rbac.route_guard import secure_module_endpoints
from apps.sales.models import Client


urlpatterns = [path('api/v1/projects/', include('apps.core.project_urls'))]
secure_module_endpoints(urlpatterns)


class FinanceSharedRecordTests(TestCase):
    setUp = command_center_tests.FinanceCommandCenterTests.setUp
    grant = command_center_tests.FinanceCommandCenterTests.grant
    deny = command_center_tests.FinanceCommandCenterTests.deny

    def ready(self):
        self.grant('finance_outgoing', 'sales_clients', 'project_control')
        for code in ('finance_outgoing', 'project_control'):
            permission = Permission.objects.get(module__code=code, action='update')
            RolePermission.objects.get_or_create(role=self.role, permission=permission)
        self.customer = Client.objects.create(
            client_code='C-1', company_name='Reviewed customer', account_manager=self.user,
        )
        self.project = Project.objects.create(
            code='P-1', name='Reviewed project', owner=self.user,
            client=self.customer, client_name='Original project client label',
        )
        return {'project_id': str(self.project.pk), 'client_id': str(self.customer.pk)}

    def invoice(self):
        record = CustomerInvoice(
            invoice_number='INV-1', company='Original customer spelling',
            account='Original account', rad_project_no='External P1', project_id='EXT-1',
            project_name='Original project label', invoice_amount=Decimal('123.45'),
            actual_payment_received=Decimal('17.00'), balance_to_be_received=Decimal('70.00'),
        )
        record.save(_skip_recompute=True)
        return record

    def source(self, **changes):
        snapshot = ReceivablesSourceSnapshot.objects.create(
            sha256=changes.pop('sha256', 'a' * 64), file_name='source.xlsx',
            sheet_name='External Invoice', first_row=6, last_row=6,
            row_count=1, is_active=changes.pop('active', True),
        )
        return ReceivablesSourceRow.objects.create(
            snapshot=snapshot, row_number=6, invoice_number='INV-1',
            company='Original customer spelling', account='Original account',
            rad_project_no='External P1', project_name='Source project',
            invoice_amount=Decimal('123.45'), balance_to_be_received=Decimal('70.00'),
            actual_payment_received=Decimal('17.00'), **changes,
        )

    def test_operational_link_keeps_original_labels_and_financial_values(self):
        targets = self.ready()
        invoice = self.invoice()
        before = CustomerInvoice.objects.filter(pk=invoice.pk).values().get()
        with patch.object(CustomerInvoice, 'recompute_all') as recompute:
            ADAPTERS['customer_invoice'].apply(invoice, self.user, targets)
        recompute.assert_not_called()
        after = CustomerInvoice.objects.filter(pk=invoice.pk).values().get()
        for field, value in before.items():
            if field not in {'canonical_project_id', 'canonical_client_id', 'canonical_identity_basis', 'updated_at'}:
                self.assertEqual(after[field], value, field)
        self.assertEqual(after['canonical_project_id'], self.project.pk)
        self.assertEqual(after['canonical_client_id'], self.customer.pk)
        description = ADAPTERS['customer_invoice'].describe(invoice, self.user)
        self.assertEqual(description['state'], 'linked')
        self.assertEqual(description['links']['project']['id'], str(self.project.pk))

    def test_changed_import_identity_hides_old_links_and_returns_to_review(self):
        targets = self.ready()
        invoice = self.invoice()
        adapter = ADAPTERS['customer_invoice']
        adapter.apply(invoice, self.user, targets)
        previous = adapter.fingerprint(invoice)
        CustomerInvoice.objects.filter(pk=invoice.pk).update(company='Changed imported customer')
        invoice.refresh_from_db()
        self.assertNotEqual(previous, adapter.fingerprint(invoice))
        projected = canonical_links(invoice, self.user)
        self.assertEqual(projected['state'], 'needs_review')
        self.assertEqual(projected['links'], {'project': None, 'client': None})
        self.assertTrue(adapter.filter_queryset(adapter.queryset(self.user), 'unlinked', '').filter(pk=invoice.pk).exists())
        self.assertFalse(adapter.filter_queryset(adapter.queryset(self.user), 'linked', '').filter(pk=invoice.pk).exists())
        # Re-reviewing one target must not resurrect the stale counterpart.
        adapter.apply(invoice, self.user, {'project_id': str(self.project.pk)})
        invoice.refresh_from_db()
        self.assertIsNone(invoice.canonical_client_id)

    def test_serializer_rejects_bypass_and_withholds_stale_link_ids(self):
        targets = self.ready()
        invoice = self.invoice()
        ADAPTERS['customer_invoice'].apply(invoice, self.user, targets)
        context = {'request': SimpleNamespace(user=self.user)}
        for field, value in [('canonical_project', self.project.pk),
                             ('canonical_client', self.customer.pk),
                             ('canonical_identity_basis', {})]:
            serializer = CustomerInvoiceSerializer(invoice, data={field: value}, partial=True, context=context)
            self.assertFalse(serializer.is_valid(), field)
        invoice.company = 'Changed company'
        data = CustomerInvoiceSerializer(invoice, context=context).data
        self.assertIsNone(data['canonical_project'])
        self.assertIsNone(data['canonical_client'])
        self.assertNotIn('canonical_identity_basis', data)

    def test_source_mapping_is_sidecar_and_new_publication_does_not_inherit_it(self):
        targets = self.ready()
        source = self.source()
        original = ReceivablesSourceRow.objects.filter(pk=source.pk).values().get()
        ADAPTERS['receivables_source'].apply(source, self.user, targets)
        self.assertEqual(ReceivablesSourceRow.objects.filter(pk=source.pk).values().get(), original)
        self.assertEqual(ReceivablesSourceIdentity.objects.count(), 1)
        self.assertEqual(CustomerInvoice.objects.count(), 0)
        self.assertEqual(canonical_links(source, self.user)['state'], 'linked')
        source.snapshot.is_active = False
        source.snapshot.save(update_fields=['is_active'])
        next_source = self.source(sha256='b' * 64)
        self.assertEqual(canonical_links(next_source, self.user)['state'], 'unlinked')
        self.assertTrue(ReceivablesSourceIdentity.objects.filter(source_row=source).exists())
        self.assertFalse(ADAPTERS['receivables_source'].queryset(self.user).filter(pk=source.pk).exists())
        from apps.core.shared_records import LinkConflict
        with self.assertRaises(LinkConflict):
            ADAPTERS['receivables_source'].apply(source, self.user, targets)

    def test_read_and_write_denial_and_hidden_target_do_not_mutate(self):
        self.grant('finance_outgoing')
        invoice = self.invoice()
        with self.assertRaises(PermissionDenied):
            ADAPTERS['customer_invoice'].apply(invoice, self.user, {'project_id': '1'})
        self.ready()
        self.deny('sales_clients')
        with self.assertRaises(PermissionDenied):
            ADAPTERS['customer_invoice'].apply(invoice, self.user, {
                'project_id': str(self.project.pk), 'client_id': str(self.customer.pk),
            })
        invoice.refresh_from_db()
        self.assertIsNone(invoice.canonical_project_id)
        self.assertIsNone(invoice.canonical_client_id)
        self.assertEqual(ADAPTERS['customer_invoice'].candidates(invoice, self.user, 'client', '')['results'], [])

    def test_project_client_mismatch_rejected(self):
        self.ready()
        other = Client.objects.create(client_code='C-2', company_name='Other customer', account_manager=self.user)
        invoice = self.invoice()
        with self.assertRaises(ValidationError):
            ADAPTERS['customer_invoice'].apply(invoice, self.user, {
                'project_id': str(self.project.pk), 'client_id': str(other.pk),
            })
        invoice.refresh_from_db()
        self.assertIsNone(invoice.canonical_project_id)

    def test_same_name_candidates_remain_separate_without_automatic_link(self):
        self.ready()
        other = Client.objects.create(client_code='C-2', company_name='Reviewed customer', account_manager=self.user)
        invoice = self.invoice()
        candidates = ADAPTERS['customer_invoice'].candidates(invoice, self.user, 'client', 'Reviewed customer')
        self.assertEqual({row['id'] for row in candidates['results']}, {str(self.customer.pk), str(other.pk)})
        invoice.refresh_from_db()
        self.assertIsNone(invoice.canonical_client_id)

    def test_source_annotation_and_command_failure_roll_back_together(self):
        targets = self.ready()
        source = self.source()
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                ADAPTERS['receivables_source'].apply(source, self.user, targets)
                raise RuntimeError('Synthetic audit failure')
        self.assertFalse(ReceivablesSourceIdentity.objects.exists())
        self.assertEqual(ReceivablesSourceRow.objects.count(), 1)

    def test_target_access_revocation_hides_previous_links(self):
        targets = self.ready()
        invoice = self.invoice()
        ADAPTERS['customer_invoice'].apply(invoice, self.user, targets)
        self.deny('sales_clients')
        result = canonical_links(invoice, self.user)
        self.assertEqual(result['state'], 'needs_review')
        self.assertEqual(result['links'], {'project': None, 'client': None})

    def test_source_financial_values_do_not_change_the_identity_basis(self):
        targets = self.ready()
        invoice = self.invoice()
        ADAPTERS['customer_invoice'].apply(invoice, self.user, targets)
        CustomerInvoice.objects.filter(pk=invoice.pk).update(actual_payment_received=Decimal('27'))
        invoice.refresh_from_db()
        self.assertEqual(canonical_links(invoice, self.user)['state'], 'linked')

    def test_partial_link_stays_pending_until_other_recorded_reference_is_reviewed(self):
        targets = self.ready()
        invoice = self.invoice()
        adapter = ADAPTERS['customer_invoice']
        adapter.apply(invoice, self.user, {'project_id': targets['project_id']})
        self.assertEqual(canonical_links(invoice, self.user)['state'], 'needs_review')
        self.assertTrue(adapter.filter_queryset(adapter.queryset(self.user), 'unlinked', '').filter(pk=invoice.pk).exists())
        self.assertFalse(adapter.filter_queryset(adapter.queryset(self.user), 'linked', '').filter(pk=invoice.pk).exists())
        adapter.apply(invoice, self.user, {'client_id': targets['client_id']})
        self.assertEqual(canonical_links(invoice, self.user)['state'], 'linked')
        self.assertTrue(adapter.filter_queryset(adapter.queryset(self.user), 'linked', '').filter(pk=invoice.pk).exists())
        self.assertFalse(adapter.filter_queryset(adapter.queryset(self.user), 'unlinked', '').filter(pk=invoice.pk).exists())

    def test_unavailable_target_returns_link_to_pending_queue(self):
        targets = self.ready()
        invoice = self.invoice()
        adapter = ADAPTERS['customer_invoice']
        adapter.apply(invoice, self.user, targets)
        self.deny('sales_clients')
        self.assertTrue(adapter.filter_queryset(adapter.queryset(self.user), 'unlinked', '').filter(pk=invoice.pk).exists())
        self.assertFalse(adapter.filter_queryset(adapter.queryset(self.user), 'linked', '').filter(pk=invoice.pk).exists())

    def test_register_reads_reveal_only_authorized_current_link_annotations(self):
        from apps.finance.services.customer_invoice_register import build_customer_invoice_register
        targets = self.ready()
        source = self.source(currency='AED', balance_currency='AED', actual_payment_currency='AED')
        ADAPTERS['receivables_source'].apply(source, self.user, targets)
        result = build_customer_invoice_register(self.user)
        self.assertEqual(result['rows'][0]['canonical_links']['links']['client']['id'], str(self.customer.pk))
        self.assertEqual(result['rows'][0]['company'], 'Original customer spelling')
        self.assertIsNone(result['rows'][0]['invoice_route'])
        self.deny('sales_clients')
        result = build_customer_invoice_register(self.user)
        self.assertEqual(result['rows'][0]['canonical_links']['links'], {'client': None, 'project': None})

    @override_settings(ROOT_URLCONF=__name__)
    def test_guarded_api_links_and_replays_each_finance_source_once(self):
        from apps.core.shared_record_models import SharedRecordLinkCommand
        targets = self.ready()
        records = [('customer_invoice', self.invoice()), ('receivables_source', self.source())]
        with patch('apps.core.shared_records.adapters', return_value=ADAPTERS):
            for source_type, row in records:
                url = f'/api/v1/projects/shared-records/{source_type}/{row.pk}/'
                review = self.client.get(url)
                self.assertEqual(review.status_code, 200, review.data)
                payload = {'request_id': str(uuid4()), 'expected_token': review.data['expected_token'],
                           'reason': 'Reviewed source contract identity', 'targets': targets}
                result = self.client.post(url + 'link/', payload, format='json')
                self.assertEqual(result.status_code, 200, result.data)
                self.assertEqual(result.data['record']['state'], 'linked')
                replay = self.client.post(url + 'link/', payload, format='json')
                self.assertEqual(replay.status_code, 200, replay.data)
                self.assertTrue(replay.data['replayed'])
                payload['reason'] = 'Different review content'
                conflict = self.client.post(url + 'link/', payload, format='json')
                self.assertEqual(conflict.status_code, 409, conflict.data)
        self.assertEqual(SharedRecordLinkCommand.objects.count(), 2)
        self.assertEqual(ReceivablesSourceIdentity.objects.count(), 1)

    @override_settings(ROOT_URLCONF=__name__)
    def test_guarded_api_stale_invoice_and_revoked_write_do_not_link(self):
        from apps.core.shared_record_models import SharedRecordLinkCommand
        from apps.rbac.models import UserPermissionOverride
        targets = self.ready()
        invoice = self.invoice()
        url = f'/api/v1/projects/shared-records/customer_invoice/{invoice.pk}/'
        with patch('apps.core.shared_records.adapters', return_value=ADAPTERS):
            review = self.client.get(url)
            payload = {'request_id': str(uuid4()), 'expected_token': review.data['expected_token'],
                       'reason': 'Reviewed source identity', 'targets': targets}
            CustomerInvoice.objects.filter(pk=invoice.pk).update(rad_project_no='Changed project')
            stale = self.client.post(url + 'link/', payload, format='json')
            self.assertEqual(stale.status_code, 409, stale.data)
            payload['expected_token'] = self.client.get(url).data['expected_token']
            permission = Permission.objects.get(module__code='finance_outgoing', action='update')
            UserPermissionOverride.objects.create(user_profile=self.profile, permission=permission, allowed=False)
            denied = self.client.post(url + 'link/', payload, format='json')
            self.assertEqual(denied.status_code, 403, denied.data)
        invoice.refresh_from_db()
        self.assertIsNone(invoice.canonical_project_id)
        self.assertFalse(SharedRecordLinkCommand.objects.exists())

    @override_settings(ROOT_URLCONF=__name__)
    def test_guarded_source_command_rolls_back_mapping_when_audit_fails(self):
        from apps.core.shared_record_models import SharedRecordLinkCommand
        targets = self.ready()
        source = self.source()
        url = f'/api/v1/projects/shared-records/receivables_source/{source.pk}/'
        with patch('apps.core.shared_records.adapters', return_value=ADAPTERS):
            review = self.client.get(url)
            payload = {'request_id': str(uuid4()), 'expected_token': review.data['expected_token'],
                       'reason': 'Reviewed immutable source row', 'targets': targets}
            with patch('apps.core.shared_records.SharedRecordLinkCommand.objects.create', side_effect=RuntimeError('Synthetic audit failure')):
                with self.assertRaises(RuntimeError):
                    self.client.post(url + 'link/', payload, format='json')
        self.assertFalse(ReceivablesSourceIdentity.objects.exists())
        self.assertFalse(SharedRecordLinkCommand.objects.exists())

    def test_other_organization_targets_are_not_candidates_or_linkable(self):
        from apps.rbac.models import Organization, UserProfile
        from apps.users.models import User
        self.ready()
        outside = User.objects.create_user('other-finance-scope', email='other-scope@example.test')
        org = Organization.objects.create(code='other-finance', name='Other scope')
        profile, _ = UserProfile.objects.get_or_create(user=outside, defaults={'organization': org})
        profile.organization = org
        profile.save(update_fields=['organization'])
        customer = Client.objects.create(client_code='OUTSIDE', company_name='Outside customer', account_manager=outside)
        project = Project.objects.create(code='OUTSIDE', name='Outside project', owner=outside, client=customer)
        invoice = self.invoice()
        for kind, target in [('project', project), ('client', customer)]:
            with self.assertRaises(PermissionDenied):
                ADAPTERS['customer_invoice'].apply(invoice, self.user, {kind + '_id': str(target.pk)})
            self.assertEqual(ADAPTERS['customer_invoice'].candidates(invoice, self.user, kind, 'OUTSIDE')['results'], [])
        invoice.refresh_from_db()
        self.assertIsNone(invoice.canonical_project_id)
        self.assertIsNone(invoice.canonical_client_id)

    def test_source_queue_filters_keep_unmapped_and_partial_rows_pending(self):
        targets = self.ready()
        source = self.source()
        adapter = ADAPTERS['receivables_source']
        pending = lambda: adapter.filter_queryset(adapter.queryset(self.user), 'unlinked', '').filter(pk=source.pk).exists()
        self.assertTrue(pending())
        adapter.apply(source, self.user, {'project_id': targets['project_id']})
        self.assertTrue(pending())
        adapter.apply(source, self.user, {'client_id': targets['client_id']})
        self.assertFalse(pending())
        self.assertTrue(adapter.filter_queryset(adapter.queryset(self.user), 'linked', '').filter(pk=source.pk).exists())

    def test_stale_generic_invoice_save_cannot_erase_a_concurrent_link(self):
        targets = self.ready()
        invoice = self.invoice()
        stale = CustomerInvoice.objects.get(pk=invoice.pk)
        ADAPTERS['customer_invoice'].apply(invoice, self.user, targets)
        serializer = CustomerInvoiceSerializer(stale, data={'remarks': 'Independent ordinary edit'}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        invoice.refresh_from_db()
        self.assertEqual(invoice.remarks, 'Independent ordinary edit')
        self.assertEqual(invoice.canonical_project_id, self.project.pk)
        self.assertEqual(invoice.canonical_client_id, self.customer.pk)
        self.assertEqual(canonical_links(invoice, self.user)['state'], 'linked')

    @override_settings(ROOT_URLCONF=__name__)
    def test_explicit_project_control_read_deny_blocks_client_only_write(self):
        from apps.core.shared_record_models import SharedRecordLinkCommand
        targets = self.ready()
        invoice = self.invoice()
        url = f'/api/v1/projects/shared-records/customer_invoice/{invoice.pk}/'
        with patch('apps.core.shared_records.adapters', return_value=ADAPTERS):
            review = self.client.get(url)
            payload = {'request_id': str(uuid4()), 'expected_token': review.data['expected_token'],
                       'reason': 'Reviewed client only', 'targets': {'client_id': targets['client_id']}}
            self.deny('project_control')
            denied = self.client.post(url + 'link/', payload, format='json')
            self.assertEqual(denied.status_code, 403, denied.data)
        invoice.refresh_from_db()
        self.assertIsNone(invoice.canonical_client_id)
        self.assertFalse(SharedRecordLinkCommand.objects.exists())

    @override_settings(ROOT_URLCONF=__name__)
    def test_exact_retry_rechecks_client_read_after_permission_revocation(self):
        from apps.core.shared_record_models import SharedRecordLinkCommand
        targets = self.ready()
        invoice = self.invoice()
        url = f'/api/v1/projects/shared-records/customer_invoice/{invoice.pk}/'
        with patch('apps.core.shared_records.adapters', return_value=ADAPTERS):
            review = self.client.get(url)
            payload = {'request_id': str(uuid4()), 'expected_token': review.data['expected_token'],
                       'reason': 'Reviewed client and project', 'targets': targets}
            result = self.client.post(url + 'link/', payload, format='json')
            self.assertEqual(result.status_code, 200, result.data)
            self.deny('sales_clients')
            denied = self.client.post(url + 'link/', payload, format='json')
            self.assertEqual(denied.status_code, 403, denied.data)
        self.assertEqual(SharedRecordLinkCommand.objects.count(), 1)

    def test_source_queue_count_and_rows_keep_one_publication(self):
        self.ready()
        source = self.source()
        query = ADAPTERS['receivables_source'].queryset(self.user)
        self.assertEqual(query.count(), 1)
        source.snapshot.is_active = False
        source.snapshot.save(update_fields=['is_active'])
        replacement = self.source(sha256='b' * 64)
        self.assertEqual(list(query.values_list('pk', flat=True)), [source.pk])
        self.assertEqual(list(ADAPTERS['receivables_source'].queryset(self.user).values_list('pk', flat=True)), [replacement.pk])
