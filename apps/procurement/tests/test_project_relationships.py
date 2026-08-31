from io import StringIO
from decimal import Decimal

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from apps.core.project_models import Project as EnterpriseProject
from apps.finance.models import AuditLog, Invoice, InvoiceMatchStatus, InvoicePurchaseOrderAllocation
from apps.procurement.models import (
    Project,
    ProjectRelationshipResolution,
    PurchaseOrder,
    PurchaseRequisition,
    Vendor,
)
from apps.procurement.services.project_relationships import (
    build_project_relationship_report,
    extract_requisition_project_codes,
    normalize_project_code,
    resolve_invoice_purchase_order,
    resolve_project_relationship,
)


class ProjectRelationshipPureTests(SimpleTestCase):
    def test_normalization_is_exact_but_case_and_whitespace_insensitive(self):
        self.assertEqual(normalize_project_code('  RAD   100  '), 'rad 100')
        self.assertNotEqual(normalize_project_code('RAD-100'), normalize_project_code('RAD100'))

    def test_extracts_only_explicit_requisition_project_codes(self):
        codes = extract_requisition_project_codes(
            'LEGACY-1',
            [
                {'project_number': 'RAD-100', 'project_name': 'Alpha'},
                {'project_code': 'RAD-200'},
                {'value': 'Name containing RAD-300'},
            ],
        )
        self.assertEqual(codes, ['LEGACY-1', 'RAD-100', 'RAD-200'])


class ProjectRelationshipDatabaseTests(TestCase):
    def setUp(self):
        self.enterprise = EnterpriseProject.objects.create(code='RAD-100', name='Alpha')
        self.vendor = Vendor.objects.create(vendor_code='V-100', name='Vendor 100', status='active')

    def test_apply_links_master_requisition_and_order_by_exact_code(self):
        master = Project.objects.create(project_number='rad-100', project_name='Legacy Alpha')
        requisition = PurchaseRequisition.objects.create(
            pr_number='PR-100', project_details=[{'project_number': ' RAD-100 '}],
        )
        order = PurchaseOrder.objects.create(
            po_number='PO-100', vendor=self.vendor, title='Order', category='other',
            total_amount=100, project_number='RAD-100',
        )

        report = build_project_relationship_report(apply=True)

        master.refresh_from_db()
        requisition.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(master.enterprise_project, self.enterprise)
        self.assertEqual(requisition.enterprise_project, self.enterprise)
        self.assertEqual(order.enterprise_project, self.enterprise)
        self.assertEqual(report['changes_applied'], 3)

    def test_multi_project_requisition_is_reported_without_guessing(self):
        EnterpriseProject.objects.create(code='RAD-200', name='Beta')
        requisition = PurchaseRequisition.objects.create(
            pr_number='PR-MULTI',
            project_details=[
                {'project_number': 'RAD-100'},
                {'project_number': 'RAD-200'},
            ],
        )

        report = build_project_relationship_report(apply=True)

        requisition.refresh_from_db()
        self.assertIsNone(requisition.enterprise_project)
        unresolved = [
            item for item in report['unresolved']
            if item['record_type'] == 'purchase_requisition' and item['id'] == str(requisition.pk)
        ]
        self.assertEqual(unresolved[0]['reason'], 'multiple_projects')

    def test_management_command_is_read_only_by_default(self):
        master = Project.objects.create(project_number='RAD-100', project_name='Legacy Alpha')
        output = StringIO()

        call_command('report_project_relationships', stdout=output)

        master.refresh_from_db()
        self.assertIsNone(master.enterprise_project)
        self.assertIn('Mode: report', output.getvalue())

    def test_manual_master_resolution_is_audited_and_propagates_to_order(self):
        master = Project.objects.create(project_number='LEGACY-ALPHA', project_name='Legacy Alpha')
        order = PurchaseOrder.objects.create(
            po_number='PO-PROPAGATE', vendor=self.vendor, title='Linked order',
            category='other', total_amount=100, project=master,
        )

        result = resolve_project_relationship(
            record_type='procurement_project', record_id=master.pk,
            enterprise_project_id=self.enterprise.pk, user=None,
            reason='Verified against signed project register',
        )

        master.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(master.enterprise_project, self.enterprise)
        self.assertEqual(order.enterprise_project, self.enterprise)
        self.assertEqual(result['propagated'], 1)
        self.assertEqual(ProjectRelationshipResolution.objects.count(), 2)
        self.assertSetEqual(
            set(ProjectRelationshipResolution.objects.values_list('resolution', flat=True)),
            {'manual', 'propagated'},
        )

    def test_manual_invoice_po_match_is_audited_and_keeps_receipt_exception(self):
        order = PurchaseOrder.objects.create(
            po_number='PO-INVOICE-MATCH', vendor=self.vendor, title='Matched order',
            category='other', total_amount=Decimal('600'), currency='AED',
            status='sent', enterprise_project=self.enterprise,
        )
        invoice = Invoice.objects.create(
            invoice_number='INV-MANUAL-MATCH', vendor=self.vendor,
            vendor_name=self.vendor.name, amount=Decimal('250'),
            total_amount=Decimal('250'), currency='AED',
            original_filename='invoice.pdf', file_path='tests/invoice.pdf',
        )

        result = resolve_invoice_purchase_order(
            invoice_id=invoice.pk, purchase_order_id=order.pk,
            allocated_amount='250', user=None,
            reason='Verified against the supplier invoice copy',
        )

        allocation = InvoicePurchaseOrderAllocation.objects.get(invoice=invoice)
        invoice.refresh_from_db()
        self.assertEqual(result['match_status'], InvoiceMatchStatus.EXCEPTION)
        self.assertEqual(allocation.match_status, InvoiceMatchStatus.EXCEPTION)
        self.assertIn('missing_accepted_receipt', allocation.exception_codes)
        self.assertEqual(invoice.match_status, InvoiceMatchStatus.EXCEPTION)
        self.assertTrue(
            AuditLog.objects.filter(invoice=invoice, action='manual_po_reconciliation').exists()
        )
