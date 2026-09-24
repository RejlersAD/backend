"""Saved PR Word exports preserve values, evidence and existing access rules."""

import base64
from copy import deepcopy
from datetime import date
from io import BytesIO
from unittest.mock import patch
from uuid import uuid4
from zipfile import ZipFile

from docx import Document
from PIL import Image
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.notifications.models import Notification
from apps.procurement.models import PurchaseRequisition
from apps.procurement.services.requisition_word_export import (
    DOCX_MIME_TYPE,
    build_purchase_requisition_docx,
)
from apps.procurement.views import PurchaseRequisitionViewSet
from apps.rbac.models import (
    Module, Organization, Permission, Role, RoleModule, RolePermission,
    UserPermissionOverride, UserProfile, UserRole,
)
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints


router = DefaultRouter()
router.register('requisitions', PurchaseRequisitionViewSet, basename='requisition')
urlpatterns = [path('api/v1/procurement/', include(router.urls))]
secure_module_endpoints(urlpatterns)
BASE = '/api/v1/procurement/requisitions/'


def logical_cells(row):
    """Merged company-form cells occupy several underlying grid columns."""
    seen = set()
    for cell in row.cells:
        if cell._tc not in seen:
            seen.add(cell._tc)
            yield cell


def table_rows(container):
    for table in container.tables:
        for row in table.rows:
            cells = list(logical_cells(row))
            yield cells
            for cell in cells:
                yield from table_rows(cell)


def container_text(container):
    return '\n'.join([
        *(paragraph.text for paragraph in container.paragraphs),
        *(cell.text for cells in table_rows(container) for cell in cells),
    ])


def docx_text(content):
    document = Document(BytesIO(content))
    return '\n'.join([
        container_text(document),
        *(container_text(part) for section in document.sections
          for part in (section.header, section.footer)),
    ])


def row_text(document, first_cell):
    return next([cell.text for cell in cells] for cells in table_rows(document)
                if cells[0].text == first_cell)


def signature_image():
    stream = BytesIO()
    Image.new('RGB', (31, 13), '#75314f').save(stream, format='PNG')
    content = stream.getvalue()
    return content, 'data:image/png;base64,' + base64.b64encode(content).decode('ascii')


class RequisitionWordDocumentTests(SimpleTestCase):
    def record(self, **changes):
        return {
            'pr_number': 'RAD-PRJ-PR-0910_2026', 'status': 'draft', 'status_display': 'Draft',
            'issued_by_name': 'Synthetic Requester', 'issued_date': '2026-09-24',
            'product_service': 'Engineering scope', 'description_reason': 'Replace a failed instrument.',
            'currency': 'EUR', 'vat_basis': 'unconfirmed', 'form_reference': 'RAD-TEST-PR',
            **changes,
        }

    def test_editable_docx_preserves_decimal_amounts_and_prints_saved_status(self):
        record = self.record(status='submitted', status_display='Submitted',
                             total_price='1000000000000.01', net_total_excl_vat='1000000000000.00',
                             items=[{'description': 'Precision quotation', 'quantity': '2.5',
                                     'unit_price': '2.675', 'total': '6.695'}])
        original = deepcopy(record)
        content = build_purchase_requisition_docx(record)
        with ZipFile(BytesIO(content)) as package:
            self.assertIn('word/document.xml', package.namelist())
            self.assertIn('[Content_Types].xml', package.namelist())
        text = docx_text(content)
        for value in ('Purchase Requisition', record['pr_number'], 'Status: Submitted',
                      'Synthetic Requester', 'Precision quotation',
                      'EUR 6.70', 'EUR 1,000,000,000,000.01'):
            self.assertIn(value, text)
        self.assertEqual(record, original)

    def test_company_form_keeps_price_and_approval_column_order_with_status_in_footer(self):
        document = Document(BytesIO(build_purchase_requisition_docx(self.record(
            supplier_name='Recorded Supplier', project_department='Synthetic Project',
            items=[{'description': 'Quoted scope', 'total': '10.25', 'remarks': 'Confirmed quotation'}],
            approval_workflow_config=[{'role': 'Engineering review', 'user_name': 'Recorded Reviewer',
                                       'status': 'pending', 'approved_at': '2026-09-24T05:30:00Z'}],
        ))))
        rows = list(table_rows(document))
        self.assertEqual(len(rows[0]), 2)
        self.assertEqual(rows[0][0].text, 'Purchase Requisition')
        self.assertEqual(len(rows[1]), 3)
        for cell, label in zip(rows[1], ('Issued by:', 'PR No.', 'Date:')):
            self.assertTrue(cell.text.startswith(label), cell.text)
        self.assertIn('24.09.2026', rows[1][-1].text)
        self.assertEqual(row_text(document, '3. Price'), ['3. Price', 'Price', 'Remarks'])
        self.assertEqual(row_text(document, 'Quoted scope'), ['Quoted scope', 'EUR 10.25', 'Confirmed quotation'])
        approval_header = next([cell.text for cell in cells] for cells in rows
                               if len(cells) == 5 and cells[1].text == 'Name')
        self.assertEqual(approval_header[1:], ['Name', 'Signature', 'Status', 'Approval Timestamp'])
        approval = next([cell.text for cell in cells] for cells in rows
                        if len(cells) == 5 and cells[1].text == 'Recorded Reviewer')
        self.assertEqual(approval[2:], ['Pending', 'pending', '\u2014'])
        self.assertEqual(row_text(document, 'Final Approval Timestamp'), ['Final Approval Timestamp', '\u2014'])
        self.assertNotIn('Status: Draft', container_text(document))
        self.assertIn('Status: Draft', container_text(document.sections[0].footer))
        self.assertTrue(document.tables[0]._tbl.tblPr.xpath('./w:tblBorders/w:top'))

    def test_partial_prices_and_missing_totals_are_not_fabricated_as_zero(self):
        document = Document(BytesIO(build_purchase_requisition_docx(self.record(
            items=[{'description': 'Quotation pending', 'quantity': '', 'unit_price': None,
                    'total': '', 'remarks': 'Awaiting supplier price'}],
            price_remarks_data={'net_total_aed': '123.45'},
        ))))
        self.assertEqual(row_text(document, 'Quotation pending'),
                         ['Quotation pending', '\u2014', 'Awaiting supplier price'])
        self.assertEqual(row_text(document, 'Total'), ['Total', '\u2014', '\u2014'])
        self.assertNotIn('0.00', container_text(document))

    def test_zero_and_nonfinite_prices_remain_distinct(self):
        document = Document(BytesIO(build_purchase_requisition_docx(self.record(items=[
            {'description': 'Complimentary', 'quantity': '1', 'unit_price': '0', 'total': '0.00'},
            {'description': 'Unknown', 'quantity': '', 'unit_price': 'NaN', 'total': 'Infinity'},
        ]))))
        self.assertEqual(row_text(document, 'Complimentary')[1], 'EUR 0.00')
        self.assertEqual(row_text(document, 'Unknown')[1], '\u2014')

    def test_budget_retains_its_recorded_currency_without_implying_conversion(self):
        cases = [
            ({'estimated_budget': '123.45'}, 'EUR 123.45'),
            ({'estimated_budget': '123.45', 'price_remarks_data': {'budget_in_aed': '500.00'}}, 'AED 500.00'),
            ({'estimated_budget': '123.45', 'price_remarks_data': {'budget_in_aed': '0'}}, 'AED 0.00'),
        ]
        for values, expected in cases:
            with self.subTest(values=values):
                text = docx_text(build_purchase_requisition_docx(self.record(**values)))
                self.assertIn('Budget', text)
                self.assertIn(expected, text)

    def test_control_characters_are_removed_without_losing_readable_unicode(self):
        text = docx_text(build_purchase_requisition_docx(self.record(
            product_service='Valve\x00 scope\x0b \u2014 \u03bc',
            description_reason='First\nSecond\tcolumn\x1f',
            items=[{'description': 'Line\x08 item'}],
        )))
        self.assertIn('Valve scope \u2014 \u03bc', text)
        self.assertIn('First\nSecond\tcolumn', text)
        self.assertIn('Line item', text)
        for character in ('\x00', '\x0b', '\x1f', '\x08'):
            self.assertNotIn(character, text)

    def test_only_verified_matching_approved_internal_signature_is_embedded(self):
        image, data_url = signature_image()
        baseline = {
            'role': 'Engineering review', 'user_id': '41', 'user_email': 'reviewer@example.test',
            'user_name': 'Recorded Reviewer', 'status': 'approved', 'approved_by_id': '41',
            'approved_by_email': 'reviewer@example.test', 'signature': data_url,
            'approved_at': '2026-09-24T05:30:00Z',
        }
        cases = [
            ({}, True, None),
            ({'approved_by_email': 'different@example.test'}, False, 'Signature needs review'),
            ({'signature_user_id': '42'}, False, 'Signature needs review'),
            ({'signature_review_required': True}, False, 'Signature needs review'),
            ({'approved_by_id': '', 'approved_by_email': ''}, False, 'Signer not verified'),
            ({'status': 'pending'}, False, 'Pending'),
        ]
        for changes, embedded, evidence in cases:
            with self.subTest(changes=changes):
                content = build_purchase_requisition_docx(self.record(
                    approval_workflow_config=[{**baseline, **changes}],
                ))
                with ZipFile(BytesIO(content)) as package:
                    media = [package.read(name) for name in package.namelist() if name.startswith('word/media/')]
                self.assertEqual(image in media, embedded)
                text = docx_text(content)
                if evidence:
                    self.assertIn(evidence, text)
                if changes.get('status') == 'pending':
                    self.assertNotIn('24.09.2026 09:30:00 GST', text)
                else:
                    self.assertIn('24.09.2026 09:30:00 GST', text)

    def test_source_evidence_is_not_invented_or_fetched(self):
        image, data_url = signature_image()
        for verified, signature, embedded in [(False, data_url, False), (True, data_url, True),
                                               (True, 'https://source.example.test/signed.pdf', False)]:
            with self.subTest(verified=verified, inline=signature == data_url):
                with patch('requests.sessions.Session.request', side_effect=AssertionError('No HTTP fetch')):
                    content = build_purchase_requisition_docx(self.record(approval_workflow_config=[{
                        'role': 'Original approval', 'user_name': 'Historical Reviewer', 'external': True,
                        'evidence_document_id': 'synthetic-source', 'status': 'approved',
                        'signature_verified': verified, 'signature': signature,
                    }]))
                with ZipFile(BytesIO(content)) as package:
                    media = [package.read(name) for name in package.namelist() if name.startswith('word/media/')]
                self.assertEqual(image in media, embedded)
                if not embedded:
                    self.assertIn('Source evidence recorded' if verified else 'See original source evidence', docx_text(content))


@override_settings(ROOT_URLCONF=__name__, TEAMS_APPROVAL_WEBHOOK_URL='', WEB_PUSH_VAPID_PRIVATE_KEY='')
class RequisitionWordExportAPITests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = get_user_model().objects.create_user('pr-word-exporter', email='exporter@example.test')
        self.issuer = get_user_model().objects.create_user('pr-word-issuer', email='issuer@example.test')
        organization, _ = Organization.objects.get_or_create(code='pr-word-tests', defaults={'name': 'Word tests'})
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'organization': organization})
        self.profile.roles.clear()
        self.role = Role.objects.create(code='pr-word-exporter', name='PR Word exporter', level=3)
        UserRole.objects.create(user_profile=self.profile, role=self.role)
        self.module, _ = Module.objects.get_or_create(code='procurement_requisitions', defaults={'name': 'Recommendations'})
        ensure_module_actions(Module, Permission, module_ids=[self.module.pk])
        RoleModule.objects.create(role=self.role, module=self.module)
        for permission in self.module.permissions.filter(action__in=['read', 'export'], is_active=True):
            RolePermission.objects.create(role=self.role, permission=permission)
        self.pr = PurchaseRequisition.objects.create(
            pr_number='RAD-PRJ-PR-0911_2026', issued_by=self.issuer, issued_date=date(2026, 9, 24),
            product_service='Saved canonical scope', status='draft', currency='EUR',
            total_price='112.37', net_total_excl_vat='100.00', vat_basis='exclusive',
            items=[{'description': 'Partial saved price', 'quantity': '', 'unit_price': '', 'total': ''}],
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def url(self, identifier=None):
        return f'{BASE}{identifier or self.pr.pk}/export-word/'

    def download(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200, getattr(response, 'data', ''))
        self.assertEqual(response['Content-Type'], DOCX_MIME_TYPE)
        self.assertIn('attachment; filename=', response['Content-Disposition'])
        self.assertTrue(response['Content-Disposition'].endswith('.docx"'))
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        return response

    def test_every_saved_status_exports_truthfully_without_mutating_the_record_or_notifying(self):
        for saved_status, display in PurchaseRequisition.STATUS_CHOICES:
            with self.subTest(status=saved_status):
                PurchaseRequisition.objects.filter(pk=self.pr.pk).update(status=saved_status)
                before = PurchaseRequisition.objects.values().get(pk=self.pr.pk)
                notices = list(Notification.objects.order_by('pk').values())
                with self.captureOnCommitCallbacks(execute=True) as callbacks, \
                        patch('requests.sessions.Session.request', side_effect=AssertionError('No HTTP fetch')), \
                        patch('django.core.files.storage.default_storage.open', side_effect=AssertionError('No original read')), \
                        patch('django.core.files.storage.default_storage.save', side_effect=AssertionError('No storage write')):
                    for _ in range(2):
                        text = docx_text(self.download().content)
                        canonical_display = 'Converted' if saved_status == 'converted' else display
                        self.assertIn(f'Status: {canonical_display}', text)
                        self.assertIn('Saved canonical scope', text)
                        self.assertIn('EUR 112.37', text)
                        self.assertIn('Partial saved price', text)
                self.assertEqual(callbacks, [])
                self.assertEqual(PurchaseRequisition.objects.values().get(pk=self.pr.pk), before)
                self.assertEqual(list(Notification.objects.order_by('pk').values()), notices)

    def test_internal_mismatch_is_reported_and_original_approval_evidence_is_preserved(self):
        image, data_url = signature_image()
        self.pr.approval_workflow_config = [{
            'level': 1, 'role': 'Engineering review', 'status': 'approved',
            'user_name': 'Recorded Reviewer', 'user_email': 'reviewer@example.test',
            'approved_by_email': 'different@example.test', 'signature': data_url,
        }]
        self.pr.save(update_fields=['approval_workflow_config'])
        before = deepcopy(self.pr.approval_workflow_config)
        response = self.download()
        self.assertIn('Signature needs review', docx_text(response.content))
        with ZipFile(BytesIO(response.content)) as package:
            self.assertNotIn(image, [package.read(name) for name in package.namelist() if name.startswith('word/media/')])
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.approval_workflow_config, before)

    def test_converted_source_record_does_not_show_unrecorded_internal_stage_as_pending(self):
        self.pr.status = 'converted'
        self.pr.approval_workflow_config = [{'level': 1, 'role': 'Engineering review',
                                           'user_name': 'Historical Reviewer', 'status': 'pending'}]
        source_key = (f'procurement/signed_requisitions/{self.pr.pk}/2026/'
                      f'{self.pr.pr_number}_Purchase_Requisition_2026-09-24.pdf')
        self.pr.attachments = [{'type': 'signed_purchase_requisition_pdf', 'filename': 'Original.pdf',
                                'storage_key': source_key}]
        self.pr.save(update_fields=['status', 'approval_workflow_config', 'attachments'])
        before = PurchaseRequisition.objects.values().get(pk=self.pr.pk)
        with patch('requests.sessions.Session.request', side_effect=AssertionError('No HTTP fetch')), \
                patch('django.core.files.storage.default_storage.open', side_effect=AssertionError('No original read')), \
                patch('django.core.files.storage.default_storage.url', return_value='/media/synthetic-original.pdf') as source_url:
            text = docx_text(self.download().content)
        self.assertIn('Not recorded', text)
        source_url.assert_any_call(source_key)
        self.assertEqual(PurchaseRequisition.objects.values().get(pk=self.pr.pk), before)

    def test_converted_legacy_fields_do_not_invent_pending_approval_requests(self):
        PurchaseRequisition.objects.filter(pk=self.pr.pk).update(status='converted', approval_workflow_config=[])
        text = docx_text(self.download().content)
        self.assertNotIn('Pending', text)
        self.assertIn('Not recorded', text)

    def test_imported_number_produces_safe_attachment_filename(self):
        PurchaseRequisition.objects.filter(pk=self.pr.pk).update(pr_number='../Imported/PR\r\n\"0911')
        response = self.download()
        disposition = response['Content-Disposition']
        self.assertNotIn('\r', disposition)
        self.assertNotIn('\n', disposition)
        self.assertNotIn('/', disposition)
        self.assertNotIn('..', disposition)
        self.assertEqual(disposition.count('"'), 2)

    def test_read_access_without_export_grant_cannot_download(self):
        RolePermission.objects.filter(role=self.role, permission__action='export').delete()
        cache.clear()
        self.assertEqual(self.client.get(f'{BASE}{self.pr.pk}/').status_code, 200)
        self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_explicit_export_denial_wins_over_role_grant(self):
        permission = self.module.permissions.filter(action='export', is_active=True).first()
        UserPermissionOverride.objects.create(user_profile=self.profile, permission=permission, allowed=False)
        cache.clear()
        self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_assigned_record_read_access_alone_does_not_confer_export_access(self):
        self.profile.roles.clear()
        self.pr.approval_workflow_config = [{'level': 1, 'role': 'Level 1 Approver',
                                           'user_id': str(self.user.pk), 'status': 'pending'}]
        self.pr.save(update_fields=['approval_workflow_config'])
        cache.clear()
        self.assertEqual(self.client.get(f'{BASE}{self.pr.pk}/').status_code, 200)
        self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_missing_record_and_anonymous_access_do_not_return_document_content(self):
        response = self.client.get(self.url(uuid4()))
        self.assertEqual(response.status_code, 404)
        self.assertNotEqual(response.get('Content-Type'), DOCX_MIME_TYPE)
        self.client.force_authenticate(user=None)
        response = self.client.get(self.url())
        self.assertIn(response.status_code, (401, 403))
        self.assertNotEqual(response.get('Content-Type'), DOCX_MIME_TYPE)
