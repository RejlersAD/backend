"""Import a reviewed PR and its PO as one linked, indivisible save."""

import hashlib
import json

from rest_framework.exceptions import PermissionDenied, ValidationError

from ..models import PODocument, PurchaseOrder, PurchaseRequisition
from .atomic_source_import import atomic_source_import
from .po_excel_import import canonical_po_number
from .procurement_lifecycle import ProcurementDeleteConflict
from .signed_po_pdf_import import (
    SignedPOImportError, _date, ensure_retained_po_source, import_signed_po_pdf, preview_signed_po_pdf,
)
from .signed_pr_pdf_import import import_signed_pr_pdf, preview_signed_pr_pdf


def preview_signed_pair(pr_bytes, po_bytes, *, pr_filename, po_filename, expected_pr_number=''):
    result = preview_signed_pr_pdf(pr_bytes, filename=pr_filename, expected_pr_number=expected_pr_number)
    preview = preview_signed_po_pdf(po_bytes, filename=po_filename)
    result['po_preview'] = {
        key: preview[key] for key in (
            'extracted_data', 'approval_evidence', 'mapping_issues', 'reconciliation_issues', 'page_count',
        )
    }
    return result


def _existing_pair_result(pr, po, source):
    metadata = pr.price_remarks_data or {}
    verified = metadata.get('signed_document_verification') or {}
    extracted = source.extracted_data or {}
    order_result = {
        'success': True, 'operation': 'attached' if (metadata.get('paired_signed_import') or {}).get('po_operation') == 'attached' else 'already_imported',
        'purchase_order_id': str(po.pk), 'po_number': po.po_number,
        'pr_id': str(pr.pk), 'pr_number': pr.pr_number, 'database_verified': True,
        'document_id': str(source.pk), 'source_document_url': source.s3_url,
        'extracted_data': extracted,
        'signature_verified': bool(extracted.get('signature_verified')),
        'stamp_verified': bool(extracted.get('stamp_verified')),
        'reconciliation_required': bool(extracted.get('reconciliation_required')),
        **{key: extracted.get(key, []) for key in ('reconciliation_issues', 'mapping_issues', 'workflow_issues')},
    }
    link = {'status': 'already_linked', 'po_id': str(po.pk), 'po_number': po.po_number,
            'manual_link_required': False, 'message': f'Linked to purchase order {po.po_number}.'}
    order_result['po_link'] = link
    return {
        'success': True, 'created': False, 'operation': 'already_imported',
        'pr_id': str(pr.pk), 'requisition_id': str(pr.pk), 'pr_number': pr.pr_number,
        'status': pr.status, 'database_verified': True,
        'signature_verified': bool(verified.get('signed_off')), 'document_signed_off': bool(verified.get('signed_off')),
        'extracted_data': verified.get('approved_fields') or {},
        'approval_detection': metadata.get('signed_approval_evidence') or {},
        'po_link': link, 'purchase_order_id': str(po.pk),
        'purchase_order': order_result,
    }


def import_signed_pair(pr_bytes, po_bytes, *, pr_filename, po_filename, request, pr_options,
                       po_reviewed_fields=None, po_evidence=None):
    from apps.rbac.action_policy import request_action_allowed

    if not request_action_allowed(request, 'procurement_orders', 'create'):
        raise PermissionDenied('Purchase order create permission is required to save both PDFs.')
    if not pr_options.get('create_new') and not request_action_allowed(request, 'procurement_requisitions', 'update'):
        raise PermissionDenied('Purchase requisition update permission is required to attach to or update this PR.')
    po_evidence = dict(po_evidence or {})
    if po_evidence.get('signature_verified') and (
        not str(po_evidence.get('approved_by_name') or '').strip()
        or not _date(str(po_evidence.get('approved_date') or ''))
    ):
        raise ValidationError({'po_approval': 'Confirm the PO approver name and a valid PO approval date from its own signed PDF.'})
    pr_digest, po_digest = (hashlib.sha256(content).hexdigest() for content in (pr_bytes, po_bytes))
    fingerprint = hashlib.sha256(json.dumps({
        'pr_sha256': pr_digest, 'po_sha256': po_digest,
        'pr_options': pr_options, 'po_reviewed_fields': po_reviewed_fields,
        'po_evidence': po_evidence,
    }, sort_keys=True, default=str).encode()).hexdigest()
    number = pr_options.get('expected_pr_number') or (pr_options.get('manual_overrides') or {}).get('pr_number')
    with atomic_source_import():
        existing = PurchaseRequisition.objects.select_for_update().filter(pr_number__iexact=number).first() if number else None
        if existing:
            saved_pair = (existing.price_remarks_data or {}).get('paired_signed_import') or {}
            if saved_pair.get('fingerprint') == fingerprint:
                po = PurchaseOrder.objects.select_for_update().filter(pk=saved_pair.get('purchase_order_id'), pr_reference=existing).first()
                source = PODocument.objects.filter(confirmed_po=po, document_type='purchase_order').order_by('-created_at', '-id').first() if po else None
                current_pr_digest = ((existing.price_remarks_data or {}).get('signed_document_verification') or {}).get('document_sha256')
                if po and source and current_pr_digest == pr_digest and (source.extracted_data or {}).get('source_sha256') == po_digest:
                    ensure_retained_po_source(source, po_bytes, {**(source.extracted_data or {}), 'po_number': po.po_number},
                                              request.user, allow_restore=request_action_allowed(request, 'procurement_orders', 'update'))
                    return _existing_pair_result(existing, po, source)
                raise ProcurementDeleteConflict('The saved PR or PO source changed after this paired import. Refresh and review the current documents before saving again.')
        result = import_signed_pr_pdf(
            pr_bytes, filename=pr_filename, uploaded_by=request.user, **pr_options,
        )
        order_result = import_signed_po_pdf(
            po_bytes, filename=po_filename, user=request.user, pr_id=result['pr_id'],
            reviewed_fields=po_reviewed_fields, require_complete=True, **po_evidence,
            register_missing_vendor=True,
            allow_vendor_create=request_action_allowed(request, 'procurement_vendors', 'create'),
            allow_existing_update=request_action_allowed(request, 'procurement_orders', 'update'),
        )
        if not order_result.get('purchase_order_id') or (order_result.get('po_link') or {}).get('manual_link_required', True):
            issues = order_result.get('reconciliation_issues') or ['Review the PO fields before saving both documents.']
            raise SignedPOImportError('Both documents were not saved. ' + ' '.join(issues))
        pr = PurchaseRequisition.objects.select_for_update().get(pk=result['pr_id'])
        po = PurchaseOrder.objects.get(pk=order_result['purchase_order_id'], pr_reference=pr)
        metadata = dict(pr.price_remarks_data or {})
        source_pr = (metadata.get('signed_document_verification') or {}).get('source_fields') or {}
        if source_pr.get('po_reference') and canonical_po_number(source_pr['po_reference']) != canonical_po_number(po.po_number):
            raise ProcurementDeleteConflict('The PR PDF references a different purchase order. Review both source documents before saving them together.')
        metadata['paired_signed_import'] = {
            'fingerprint': fingerprint, 'pr_sha256': pr_digest, 'po_sha256': po_digest,
            'purchase_order_id': str(po.pk),
            'po_operation': order_result.get('operation'),
        }
        pr.price_remarks_data = metadata
        pr.save(update_fields=['price_remarks_data', 'updated_at'])
        result.update(status=pr.status, po_link=order_result['po_link'],
                      purchase_order_id=str(po.pk), purchase_order=order_result)
        return result
