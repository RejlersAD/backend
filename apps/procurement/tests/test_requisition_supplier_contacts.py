"""PR contact presentation follows supplier identity and PO read authorization."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import include, path
from rest_framework.request import Request
from rest_framework.test import APIClient, APIRequestFactory

from apps.procurement.models import PODocument, PurchaseOrder, PurchaseRequisition, Vendor
from apps.procurement.serializers import PurchaseRequisitionSerializer
from apps.procurement.views import PurchaseRequisitionViewSet
from apps.rbac.models import Module, Organization, Permission, Role, RoleModule, RolePermission, UserPermissionOverride, UserProfile, UserRole
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints


urlpatterns = [path('api/v1/procurement/', include('apps.procurement.urls'))]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class RequisitionSupplierContactTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user('supplier-contact-reader', email='reader@example.test')
        self.other = get_user_model().objects.create_user('supplier-contact-other', email='other@example.test')
        organization, _ = Organization.objects.get_or_create(code='supplier-contact-tests', defaults={'name': 'Supplier contacts'})
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'organization': organization})
        self.profile.roles.clear()
        self.role = Role.objects.create(code='supplier-contact-reader', name='Contact reader', level=3)
        UserRole.objects.create(user_profile=self.profile, role=self.role)
        for code in ('procurement_requisitions', 'procurement_orders'):
            module, _ = Module.objects.get_or_create(code=code, defaults={'name': code})
            ensure_module_actions(Module, Permission, module_ids=[module.pk])
            RoleModule.objects.get_or_create(role=self.role, module=module)
            for permission in module.permissions.filter(action='read', is_active=True):
                RolePermission.objects.get_or_create(role=self.role, permission=permission)
        self.vendor = Vendor.objects.create(vendor_code='CONTACT-SUPPLIER', name='Example Supplier LLC')
        self.foreign_vendor = Vendor.objects.create(vendor_code='CONTACT-OTHER', name='Different Supplier LLC')
        self.pr = PurchaseRequisition.objects.create(pr_number='RAD-PRJ-PR-0003_2026', supplier_name=self.vendor.name, issued_by=self.user)
        self.order = PurchaseOrder.objects.create(po_number='RAD-PRJ-PUR-0003_2026', pr_reference=self.pr,
                                                  vendor=self.vendor, title='Supplier order', total_amount=100, created_by=self.other)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def read(self):
        response = self.client.get(f'/api/v1/procurement/requisitions/{self.pr.pk}/')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data['supplier_contact_details']

    def contact_source(self, *, order=None, **overrides):
        return PODocument.objects.create(
            original_filename='Signed PO.pdf', s3_key='retained/source.pdf', uploaded_by=self.other,
            document_type='purchase_order', confirmed_po=order or self.order,
            extracted_data={'vendor_id': str(self.vendor.pk), 'vendor_name': self.vendor.name,
                            'seller_contact_person': 'Source Contact', 'seller_email': 'source@example.test', **overrides},
        )

    def deny_po(self):
        permission = Permission.objects.filter(module__code='procurement_orders', action='read', is_active=True).first()
        UserPermissionOverride.objects.create(user_profile=self.profile, permission=permission, allowed=False)

    def test_unlinked_pr_vendor_uses_actual_linked_po_master_without_mutating_records(self):
        self.vendor.contact_person, self.vendor.email = 'Supplier Contact', 'contact@example.test'
        self.vendor.save(update_fields=['contact_person', 'email'])
        self.pr.supplier_name += ' (M/s. Another Subcontractor LLC)'
        self.pr.save(update_fields=['supplier_name'])
        snapshots = {model: list(model.objects.values()) for model in (PurchaseRequisition, PurchaseOrder, Vendor, PODocument)}
        with patch('apps.procurement.services.po_tesseract_extractor.extract_text_from_pdf_tesseract', side_effect=AssertionError('OCR on read')):
            details = self.read()
        self.assertEqual(details['vendor_id'], str(self.vendor.pk))
        self.assertEqual(details['vendor_name'], self.vendor.name)
        self.assertEqual(details['contact_person'], 'Supplier Contact')
        self.assertEqual(details['email'], 'contact@example.test')
        self.assertEqual(details['sources']['email'], 'linked_po_vendor')
        for model, before in snapshots.items():
            self.assertEqual(list(model.objects.values()), before)

    def test_saved_order_specific_contact_fills_missing_pr_master_contact(self):
        self.pr.vendor = self.vendor
        self.pr.save(update_fields=['vendor'])
        self.order.seller_contact_person, self.order.seller_email = 'Order Contact', 'order@example.test'
        self.order.save(update_fields=['seller_contact_person', 'seller_email'])
        self.assertEqual(self.read()['sources'], {'contact_person': 'linked_po', 'email': 'linked_po'})

    def test_explicit_pr_vendor_master_remains_authoritative(self):
        self.vendor.contact_person, self.vendor.email = 'Master Contact', 'master@example.test'
        self.vendor.save(update_fields=['contact_person', 'email'])
        self.pr.vendor = self.vendor
        self.pr.save(update_fields=['vendor'])
        self.order.seller_contact_person, self.order.seller_email = 'Other Contact', 'other@example.test'
        self.order.save(update_fields=['seller_contact_person', 'seller_email'])
        self.assertEqual(self.read()['email'], 'master@example.test')

    def test_only_matching_shortlist_vendor_can_fill_missing_contact(self):
        self.pr.vendor = self.vendor
        self.pr.selected_vendors = [
            {'vendor_id': str(self.foreign_vendor.pk), 'contact_person': 'Wrong Bidder', 'email': 'wrong@example.test'},
            {'vendor_id': str(self.vendor.pk), 'contact_person': 'Selected Contact', 'email': 'selected@example.test'},
        ]
        self.pr.save(update_fields=['vendor', 'selected_vendors'])
        details = self.read()
        self.assertEqual(details['email'], 'selected@example.test')
        self.assertEqual(details['sources']['contact_person'], 'selected_vendor')

    def test_unlinked_pr_matches_unique_named_shortlist_with_an_id(self):
        self.pr.selected_vendors = [
            {'vendor_id': str(self.foreign_vendor.pk), 'vendor_name': self.foreign_vendor.name, 'email': 'wrong@example.test'},
            {'vendor_id': str(self.vendor.pk), 'vendor_name': self.vendor.name, 'email': 'selected@example.test'},
        ]
        self.pr.save(update_fields=['selected_vendors'])
        self.assertEqual(self.read()['email'], 'selected@example.test')

    def test_unlinked_pr_does_not_choose_between_duplicate_names_with_different_ids(self):
        self.order.pr_reference = None
        self.order.save(update_fields=['pr_reference'])
        self.pr.selected_vendors = [
            {'vendor_id': '12', 'vendor_name': self.vendor.name, 'email': 'first@example.test'},
            {'vendor_id': '34', 'vendor_name': self.vendor.name, 'email': 'second@example.test'},
        ]
        self.pr.save(update_fields=['selected_vendors'])
        self.assertEqual(self.read()['email'], '')

    def test_numeric_source_id_cannot_be_treated_as_missing(self):
        self.contact_source(vendor_id=12345)
        self.assertEqual(self.read()['email'], '')

    def test_numeric_shortlist_identity_is_retained_for_unique_named_supplier(self):
        self.pr.selected_vendors = [{'vendor_id': 123, 'vendor_name': self.vendor.name,
                                    'contact_person': 'Saved Contact', 'email': 'selected@example.test'}]
        self.pr.save(update_fields=['selected_vendors'])
        details = self.read()
        self.assertEqual(details['vendor_id'], '123')
        self.assertEqual(details['email'], 'selected@example.test')

    def test_ambiguous_named_shortlist_without_ids_does_not_pick_first_contact(self):
        self.order.pr_reference = None
        self.order.save(update_fields=['pr_reference'])
        self.pr.selected_vendors = [
            {'name': self.vendor.name, 'email': 'first@example.test'},
            {'name': self.vendor.name, 'email': 'second@example.test'},
        ]
        self.pr.save(update_fields=['selected_vendors'])
        self.assertEqual(self.read()['email'], '')

    def test_partial_name_only_shortlist_contact_survives_linked_order_email_fallback(self):
        self.pr.selected_vendors = [{'name': self.vendor.name, 'contact_person': 'Saved Contact'}]
        self.pr.save(update_fields=['selected_vendors'])
        self.vendor.email = 'master@example.test'
        self.vendor.save(update_fields=['email'])
        details = self.read()
        self.assertEqual(details['contact_person'], 'Saved Contact')
        self.assertEqual(details['email'], 'master@example.test')
        self.assertEqual(details['sources'], {'contact_person': 'selected_vendor', 'email': 'linked_po_vendor'})
        self.vendor.email = ''
        self.vendor.save(update_fields=['email'])
        self.contact_source()
        details = self.read()
        self.assertEqual(details['contact_person'], 'Saved Contact')
        self.assertEqual(details['email'], 'source@example.test')
        self.assertEqual(details['sources']['email'], 'linked_po_source')

    def test_selected_supplier_identity_prevents_borrowing_from_other_vendor_order(self):
        self.pr.selected_vendors = [{'vendor_id': 123, 'vendor_name': self.vendor.name, 'contact_person': 'Selected Contact'}]
        self.pr.save(update_fields=['selected_vendors'])
        self.contact_source()
        self.assertEqual(self.read()['email'], '')

    def test_distinct_non_latin_supplier_names_are_not_collapsed(self):
        self.vendor.name = 'شركة المورد الأول'
        self.vendor.save(update_fields=['name'])
        self.pr.supplier_name = 'شركة المورد الثاني'
        self.pr.save(update_fields=['supplier_name'])
        self.contact_source()
        self.assertEqual(self.read()['email'], '')

    def test_current_confirmed_same_vendor_document_supplies_saved_contact(self):
        self.contact_source()
        self.assertEqual(self.read()['email'], 'source@example.test')
        self.assertEqual(self.read()['sources']['email'], 'linked_po_source')

    def test_current_signed_source_wins_over_later_history(self):
        current = self.contact_source(source_sha256='a' * 64)
        self.contact_source(seller_email='historical@example.test')
        self.order.attachments = [{'type': 'signed_purchase_order_pdf', 'document_id': str(current.pk), 'sha256': 'a' * 64}]
        self.order.save(update_fields=['attachments'])
        self.assertEqual(self.read()['email'], 'source@example.test')

    def test_document_supplier_conflict_does_not_use_name_as_override(self):
        self.contact_source(vendor_id=str(self.foreign_vendor.pk))
        self.assertEqual(self.read()['email'], '')

    def test_legacy_document_without_vendor_id_requires_exact_vendor_name(self):
        document = self.contact_source(vendor_id='', vendor_name=self.vendor.name)
        self.assertEqual(self.read()['email'], 'source@example.test')
        document.extracted_data['vendor_name'] = self.foreign_vendor.name
        document.save(update_fields=['extracted_data'])
        self.assertEqual(self.read()['email'], '')

    def test_foreign_order_source_or_stale_po_metadata_cannot_supply_contacts(self):
        self.order.pr_reference = None
        self.order.save(update_fields=['pr_reference'])
        self.contact_source()
        self.pr.price_remarks_data = {'po_link': {'po_id': str(self.order.pk)}}
        self.pr.po_number_reference = self.order.po_number
        self.pr.save(update_fields=['price_remarks_data', 'po_number_reference'])
        self.assertEqual(self.read()['email'], '')

    def test_different_explicit_pr_supplier_cannot_borrow_po_contact(self):
        self.pr.vendor = self.foreign_vendor
        self.pr.save(update_fields=['vendor'])
        self.contact_source()
        details = self.read()
        self.assertEqual(details['vendor_id'], str(self.foreign_vendor.pk))
        self.assertEqual(details['email'], '')

    def test_different_free_text_or_interior_parenthetical_identity_is_not_matched(self):
        self.contact_source()
        for name in (self.foreign_vendor.name, 'Example (Dubai) Supplier LLC'):
            with self.subTest(name=name):
                self.pr.supplier_name = name
                self.pr.save(update_fields=['supplier_name'])
                self.assertEqual(self.read()['email'], '')

    def test_explicit_order_read_deny_hides_po_contact_even_for_order_owner(self):
        self.order.created_by = self.user
        self.order.save(update_fields=['created_by'])
        self.contact_source()
        self.deny_po()
        self.assertEqual(self.read()['email'], '')

    def test_pr_access_without_po_access_does_not_expose_po_contact(self):
        RolePermission.objects.filter(role=self.role, permission__module__code='procurement_orders').delete()
        self.contact_source()
        self.assertEqual(self.read()['email'], '')

    def test_order_assignee_may_read_contact_without_module_wide_order_access(self):
        RolePermission.objects.filter(role=self.role, permission__module__code='procurement_orders').delete()
        self.order.approval_log = [{'user_email': self.user.email, 'status': 'pending'}]
        self.order.save(update_fields=['approval_log'])
        self.contact_source()
        self.assertEqual(self.read()['email'], 'source@example.test')

    def test_contact_field_is_read_only(self):
        serializer = PurchaseRequisitionSerializer(self.pr, data={'supplier_contact_details': {'email': 'injected@example.test'}}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertNotIn('supplier_contact_details', serializer.validated_data)

    def test_source_and_supplier_reads_stay_constant_for_one_or_ten_requisitions(self):
        self.contact_source()
        for number in range(4, 13):
            pr = PurchaseRequisition.objects.create(pr_number=f'RAD-PRJ-PR-{number:04d}_2026', supplier_name=self.vendor.name, issued_by=self.user)
            order = PurchaseOrder.objects.create(po_number=f'RAD-PRJ-PUR-{number:04d}_2026', pr_reference=pr,
                                                 vendor=self.vendor, title='Contact order', total_amount=100, created_by=self.other)
            self.contact_source(order=order)
        totals = []
        for count in (1, 10):
            request = Request(APIRequestFactory().get('/'))
            request.user = get_user_model().objects.get(pk=self.user.pk)
            with CaptureQueriesContext(connection) as queries:
                data = PurchaseRequisitionSerializer(PurchaseRequisitionViewSet.queryset.all()[:count], many=True,
                                                     context={'request': request}).data
            self.assertTrue(all(row['supplier_contact_details']['email'] == 'source@example.test' for row in data))
            self.assertEqual(sum('FROM "procurement_po_documents"' in query['sql'] for query in queries), 1)
            totals.append(len(queries))
        self.assertEqual(totals[0], totals[1], totals)
