"""Materialize a reviewed, retained PO upload without extracting or storing it again."""

from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib

from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from ..models import PODocument, PurchaseOrder, PurchaseRequisition
from .po_excel_import import canonical_po_number
from .procurement_lifecycle import ProcurementDeleteConflict, mark_requisition_converted
from .purchase_order_numbering import PurchaseOrderNumberService
from .pr_document_reconciliation import verify_originating_po_link
from .signed_po_pdf_import import (
    _approval_evidence, _attach_existing_order, _date, validate_originating_requisition,
)


def _money(fields, key, *, positive=False):
    try:
        value = Decimal(str(fields.get(key) if fields.get(key) is not None else '0'))
        if not value.is_finite() or value < 0 or (positive and value <= 0):
            raise InvalidOperation
        return value
    except (InvalidOperation, ValueError):
        raise ValidationError({key: 'Save a valid positive amount before completing reconciliation.'})


def _source_bytes(document, fields):
    try:
        with default_storage.open(document.s3_key, 'rb') as source:
            content = source.read(15 * 1024 * 1024 + 1)
    except Exception:
        raise ValidationError({'error': 'The original PDF is unavailable. Restore or upload the original before reconciliation.'})
    if not content.startswith(b'%PDF') or len(content) > 15 * 1024 * 1024:
        raise ValidationError({'error': 'The retained source is not a supported PDF.'})
    digest = hashlib.sha256(content).hexdigest()
    if fields.get('source_sha256') and fields['source_sha256'] != digest:
        raise ProcurementDeleteConflict('The saved PDF differs from its recorded original. Re-upload and review the source before reconciliation.')
    return content


def _already_reconciled(document):
    order = document.confirmed_po
    fields = document.extracted_data or {}
    result = {'success': True, 'operation': 'already_reconciled', 'document_id': str(document.pk),
              'purchase_order_id': str(order.pk), 'confirmed_po': str(order.pk), 'po_number': order.po_number}
    evidence = _approval_evidence(previous=fields)
    result.update(evidence)
    result['workflow_issues'] = list(dict.fromkeys([*(fields.get('workflow_issues') or []), *evidence['approval_evidence_issues']]))
    origin_id = (document.extracted_data or {}).get('originating_pr_id')
    if origin_id:
        if str(order.pr_reference_id) != str(origin_id):
            raise ProcurementDeleteConflict('The saved purchase order PR link changed. Refresh and review the existing order.')
        result.update(pr_id=str(order.pr_reference_id), pr_number=order.pr_reference.pr_number,
                      po_link={'status': 'already_linked', 'po_id': str(order.pk),
                               'po_number': order.po_number, 'manual_link_required': False,
                               'message': f'Linked to purchase order {order.po_number}.'})
    return result


@transaction.atomic
def reconcile_saved_po_document(document_id, request, mapping):
    from apps.rbac.action_policy import request_action_allowed
    from ..serializers import PurchaseOrderSerializer

    observed = get_object_or_404(PODocument, pk=document_id, uploaded_by=request.user)
    if observed.document_type != 'purchase_order':
        raise ValidationError({'error': 'Only purchase order PDFs can be reconciled here.'})
    snapshot = observed.extracted_data or {}
    review = mapping.get('reviewed_fields')
    if review is not None and not observed.confirmed_po_id:
        if not request_action_allowed(request, 'procurement_orders', 'update'):
            raise PermissionDenied('Purchase order update permission is required to save reviewed PDF fields.')
        from .po_document_review import reviewed_document_fields
        snapshot = reviewed_document_fields(snapshot, review, user=request.user)
    origin_id = snapshot.get('originating_pr_id')
    selected_pr = mapping.get('pr_id')
    if selected_pr and review and review.get('pr_id') and selected_pr.pk != review['pr_id'].pk:
        raise ValidationError({'pr_id': 'Select the same purchase recommendation in the saved PO details.'})
    if origin_id and selected_pr and str(selected_pr.pk) != str(origin_id):
        raise ProcurementDeleteConflict('Keep the originating purchase recommendation selected for this uploaded PDF.')
    if observed.confirmed_po_id:
        return _already_reconciled(observed)
    pr_id = origin_id or (selected_pr.pk if selected_pr else snapshot.get('pr_id'))
    if not pr_id:
        raise ValidationError({'pr_id': 'Select the matching purchase recommendation before reconciliation.'})
    pr = get_object_or_404(PurchaseRequisition.objects.select_for_update(), pk=pr_id)
    vendor = mapping.get('vendor_id')
    number = canonical_po_number(snapshot.get('po_number') or snapshot.get('source_po_number'))
    verified, message = PurchaseOrderNumberService.verify(number, pr.pr_number)
    if not verified:
        raise ValidationError({'po_number': message})
    aliases = {number}
    for alias in (snapshot.get('source_po_number'), (snapshot.get('source_extracted_data') or {}).get('source_po_number')):
        if alias and canonical_po_number(alias) == number:
            aliases.add(alias)
    candidates = list(PurchaseOrder.objects.select_for_update().filter(po_number__in=aliases)[:2])
    if len(candidates) > 1:
        raise ProcurementDeleteConflict('More than one saved order matches this PDF number. Resolve the duplicate references before reconciliation.')
    existing = candidates[0] if candidates else None
    if origin_id:
        validate_originating_requisition(pr, snapshot, po=existing)
    document = get_object_or_404(PODocument.objects.select_for_update(), pk=document_id, uploaded_by=request.user)
    if document.confirmed_po_id:
        return _already_reconciled(document)
    if document.updated_at != observed.updated_at:
        raise ProcurementDeleteConflict('The saved PDF details changed during reconciliation. Refresh and review the latest values.')
    fields = deepcopy(snapshot)
    fields.setdefault('source_extracted_data', deepcopy(fields))
    registered_vendor = False
    if vendor is None:
        from .document_vendor import resolve_document_vendor
        vendor, registered_vendor = resolve_document_vendor(
            fields, user=request.user,
            allow_create=request_action_allowed(request, 'procurement_vendors', 'create'),
        )
    confirmed = fields.get('canonical_financials') or {}
    from .procurement_vat import CONFIRMED_BASES, confirmed_totals
    financial_values = {}
    if confirmed.get('vat_basis') in CONFIRMED_BASES:
        try:
            financial_values = confirmed_totals(confirmed.get('entered_amount'), confirmed['vat_basis'])
        except ValueError as error:
            raise ValidationError({'vat_basis': str(error)}) from error
        total, tax = financial_values['net_amount'], financial_values['tax_amount']
        if total <= 0:
            raise ValidationError({'entered_amount': 'Enter a positive price before completing reconciliation.'})
    else:
        total, tax = _money(fields, 'total_amount', positive=True), _money(fields, 'tax_amount')
        if fields.get('gross_amount') is not None and _money(fields, 'gross_amount') != total + tax:
            raise ValidationError({'gross_amount': 'Save a gross amount equal to the total amount plus tax before reconciliation.'})
    currency = str(fields.get('currency') or '').strip().upper()
    if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
        raise ValidationError({'currency': 'Save a three-letter currency code before reconciliation.'})
    issued = _date(str(fields.get('po_date') or ''))
    if not issued:
        raise ValidationError({'po_date': 'Save the purchase order date before reconciliation.'})
    if not str(fields.get('summary') or '').strip():
        raise ValidationError({'summary': 'Save the purchase description before reconciliation.'})
    content = _source_bytes(document, fields)
    signature_visible = fields.get('signature_visible', fields.get('signature_verified')) is True
    evidence = _approval_evidence(
        signature_verified=signature_visible, stamp_verified=fields.get('stamp_verified') is True,
        approved_by_name=fields.get('approved_by_name') or '', approved_by_title=fields.get('approved_by_title') or '',
        approved_date=fields.get('approved_date') or '',
    )
    signature = evidence['signature_verified']
    fields.update(po_number=number, vendor_id=str(vendor.pk), vendor_name=vendor.name,
                  pr_id=str(pr.pk), pr_number=pr.pr_number, total_amount=total, tax_amount=tax,
                  currency=currency, po_date=issued)
    if review is not None:
        fields['vendor_match'] = {
            'matched': True, 'id': str(vendor.pk), 'vendor_code': vendor.vendor_code,
            'vendor_name': vendor.name, 'registered': registered_vendor,
            'method': 'registered from PO source' if registered_vendor else 'selected or matched existing supplier',
        }
    if existing:
        if not request_action_allowed(request, 'procurement_orders', 'update'):
            raise PermissionDenied('Purchase order update permission is required to attach this PDF to an existing order.')
        mismatches = []
        if existing.vendor_id != vendor.pk:
            mismatches.append('supplier')
        if existing.pr_reference_id and existing.pr_reference_id != pr.pk:
            mismatches.append('purchase recommendation')
        existing_total = existing.net_amount if existing.net_amount is not None else existing.total_amount
        if existing_total != total or existing.tax_amount != tax:
            mismatches.append('amount or tax')
        if existing.currency != currency:
            mismatches.append('currency')
        if mismatches:
            raise ProcurementDeleteConflict('This PO number already exists with different ' + ', '.join(mismatches) + '. Review the existing order; it has not been overwritten.')
        order = existing
        if not order.pr_reference_id:
            order.pr_reference = pr
            order.save(update_fields=['pr_reference', 'updated_at'])
            if not origin_id:
                mark_requisition_converted(pr, order.po_number)
    else:
        data = {
            'po_number': number, 'pr_reference': str(pr.pk), 'vendor': str(vendor.pk),
            'title': str(fields['summary'])[:300], 'description': fields['summary'],
            'category': pr.category or 'other', 'status': 'sent' if signature else 'draft',
            'total_amount': total, 'tax_amount': tax, 'currency': currency,
            'vat_percentage': (tax * Decimal('100') / total).quantize(Decimal('0.01')),
            'project_number': fields.get('project_number') or pr.project or '',
            'payment_terms': fields.get('payment_terms') or '',
            'payment_mode': fields.get('payment_mode') or '',
            'delivery_terms': fields.get('delivery_terms') or '',
            'expected_delivery': fields.get('expected_delivery'),
            'items': fields.get('items') or [], 'quote_ref': fields.get('quote_ref') or '',
        }
        if financial_values:
            data.update(financial_values, vat_basis=confirmed['vat_basis'], entered_amount=confirmed['entered_amount'])
        serializer = PurchaseOrderSerializer(data=data, context={
            'request': request, 'defer_requisition_conversion': bool(origin_id),
        })
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                order = serializer.save()
        except IntegrityError as error:
            raise ProcurementDeleteConflict('An order with this number was created while reconciling. Refresh and retry to review the existing order.') from error
        PurchaseOrder.objects.filter(pk=order.pk).update(po_date=issued)
        order.po_date = issued
    # Completing reconciliation explicitly confirms the reviewed commercial
    # fields when automatic OCR was limited to the start of a long document.
    if fields.get('extraction_truncated') or ('source_page_count' in fields and fields['source_page_count'] is None):
        fields['extraction_reviewed'] = True
    result = _attach_existing_order(
        order, fields, content, document.original_filename, request.user,
        signature_verified=signature_visible, stamp_verified=fields.get('stamp_verified') is True,
        approved_by_name=fields.get('approved_by_name') or '',
        approved_by_title=fields.get('approved_by_title') or '',
        approved_date=fields.get('approved_date') or '', retained_document=document,
    )
    metadata = dict(document.extracted_data or {})
    metadata.update(reconciled_by=str(request.user.pk), reconciled_at=timezone.now().isoformat())
    document.extracted_data = metadata
    document.save(update_fields=['extracted_data', 'updated_at'])
    result.update(operation='attached' if existing else 'created', confirmed_po=str(order.pk))
    result['vendor_registered'] = registered_vendor
    if origin_id:
        result['po_link'] = verify_originating_po_link(pr, order.pk, request.user)
    return result
