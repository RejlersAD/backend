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
from .signed_po_pdf_import import _attach_existing_order, _date


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


@transaction.atomic
def reconcile_saved_po_document(document_id, request, mapping):
    from apps.rbac.action_policy import request_action_allowed
    from ..serializers import PurchaseOrderSerializer

    observed = get_object_or_404(PODocument, pk=document_id, uploaded_by=request.user)
    if observed.document_type != 'purchase_order':
        raise ValidationError({'error': 'Only purchase order PDFs can be reconciled here.'})
    if observed.confirmed_po_id:
        return {'success': True, 'operation': 'already_reconciled', 'document_id': str(observed.pk),
                'purchase_order_id': str(observed.confirmed_po_id), 'confirmed_po': str(observed.confirmed_po_id),
                'po_number': observed.confirmed_po.po_number}
    snapshot = observed.extracted_data or {}
    selected_pr = mapping.get('pr_id')
    pr_id = selected_pr.pk if selected_pr else snapshot.get('pr_id')
    if not pr_id:
        raise ValidationError({'pr_id': 'Select the matching purchase recommendation before reconciliation.'})
    pr = get_object_or_404(PurchaseRequisition.objects.select_for_update(), pk=pr_id)
    vendor = mapping['vendor_id']
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
    document = get_object_or_404(PODocument.objects.select_for_update(), pk=document_id, uploaded_by=request.user)
    if document.confirmed_po_id:
        return {'success': True, 'operation': 'already_reconciled', 'document_id': str(document.pk),
                'purchase_order_id': str(document.confirmed_po_id), 'confirmed_po': str(document.confirmed_po_id),
                'po_number': document.confirmed_po.po_number}
    if document.updated_at != observed.updated_at:
        raise ProcurementDeleteConflict('The saved PDF details changed during reconciliation. Refresh and review the latest values.')
    fields = deepcopy(document.extracted_data or {})
    fields.setdefault('source_extracted_data', deepcopy(fields))
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
    signature = fields.get('signature_verified') is True
    if signature and (not str(fields.get('approved_by_name') or '').strip() or not _date(str(fields.get('approved_date') or ''))):
        raise ValidationError({'error': 'The saved approval evidence is incomplete. Review the approver and approval date on the original upload.'})
    fields.update(po_number=number, vendor_id=str(vendor.pk), vendor_name=vendor.name,
                  pr_id=str(pr.pk), pr_number=pr.pr_number, total_amount=total, tax_amount=tax,
                  currency=currency, po_date=issued)
    if existing:
        if not request_action_allowed(request, 'procurement_orders', 'update'):
            raise PermissionDenied('Purchase order update permission is required to attach this PDF to an existing order.')
        mismatches = []
        if existing.vendor_id != vendor.pk:
            mismatches.append('supplier')
        if existing.pr_reference_id and existing.pr_reference_id != pr.pk:
            mismatches.append('purchase recommendation')
        if existing.total_amount != total or existing.tax_amount != tax:
            mismatches.append('amount or tax')
        if existing.currency != currency:
            mismatches.append('currency')
        if mismatches:
            raise ProcurementDeleteConflict('This PO number already exists with different ' + ', '.join(mismatches) + '. Review the existing order; it has not been overwritten.')
        order = existing
        if not order.pr_reference_id:
            order.pr_reference = pr
            order.save(update_fields=['pr_reference', 'updated_at'])
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
        serializer = PurchaseOrderSerializer(data=data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                order = serializer.save()
        except IntegrityError as error:
            raise ProcurementDeleteConflict('An order with this number was created while reconciling. Refresh and retry to review the existing order.') from error
        PurchaseOrder.objects.filter(pk=order.pk).update(po_date=issued)
        order.po_date = issued
    result = _attach_existing_order(
        order, fields, content, document.original_filename, request.user,
        signature_verified=signature, stamp_verified=fields.get('stamp_verified') is True,
        approved_by_name=fields.get('approved_by_name') or '',
        approved_by_title=fields.get('approved_by_title') or '',
        approved_date=fields.get('approved_date') or '', retained_document=document,
    )
    metadata = dict(document.extracted_data or {})
    metadata.update(reconciled_by=str(request.user.pk), reconciled_at=timezone.now().isoformat())
    document.extracted_data = metadata
    document.save(update_fields=['extracted_data', 'updated_at'])
    result.update(operation='attached' if existing else 'created', confirmed_po=str(order.pk))
    return result
