"""Approval records combine original PRs with uploaded or RADAI-generated POs."""

import hashlib
import re
from datetime import timezone as datetime_timezone

from botocore.exceptions import ClientError
from django.core.files.storage import default_storage
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import APIException, NotFound, PermissionDenied

from apps.rbac.action_policy import record_workflow_not_denied, request_action_allowed

from ..models import PurchaseOrder
from .purchase_order_approvals import _entry_matches_user
from .purchase_order_sources import is_safe_source_storage_key, uploaded_purchase_order_sources
from .requisition_source_documents import SIGNED_PR_TYPE, requisition_source_key
from .requisition_workflow import RequisitionWorkflowService


MAX_SOURCE_BYTES = 15 * 1024 * 1024


class ApprovalRecordInvalid(APIException):
    status_code = 409
    default_code = 'approval_record_invalid'


class ApprovalRecordStorageUnavailable(APIException):
    status_code = 503
    default_code = 'approval_record_storage_unavailable'


class ApprovalRecordSourceMissing(NotFound):
    default_code = 'approval_record_source_missing'

    def __init__(self, label, *, reason='not_attached'):
        source = 'po' if label == 'linked PO' else 'pr'
        super().__init__({
            'error': f'The original {label} PDF is unavailable. Upload its original PDF to include it in the approval record.',
            'code': self.default_code, 'source': source, 'reason': reason,
            'recovery': f'upload_original_{source}',
        })


def _check_read(request, record, *, order=False):
    user = request.user
    module = 'procurement_orders' if order else 'procurement_requisitions'
    label = 'Purchase Order' if order else 'Purchase Requisition'
    if not record_workflow_not_denied(user, module, 'read'):
        raise PermissionDenied(f'You do not have read access to this {label}.')
    if request_action_allowed(request, module, 'read'):
        return
    if order:
        owner = record.created_by_id == user.pk
        assigned = any(_entry_matches_user(row, user) for row in (record.approval_log or []) if isinstance(row, dict))
    else:
        owner = user.pk in (record.issued_by_id, record.requested_by_id)
        assigned = any(RequisitionWorkflowService._stage_matches_user(row, user)
                       for row in (record.approval_workflow_config or []) if isinstance(row, dict))
    if not (owner or assigned):
        raise PermissionDenied(f'You do not have read access to this {label}.')


def _uploaded_at(attachment):
    try:
        value = parse_datetime(str(attachment.get('uploaded_at') or ''))
        if value:
            return value.replace(tzinfo=value.tzinfo or datetime_timezone.utc).timestamp()
    except (ValueError, TypeError, OverflowError):
        pass
    return 0


def _pr_source(pr):
    verification = (pr.price_remarks_data or {}).get('signed_document_verification') or {}
    current_sha = verification.get('document_sha256')
    sources = [row for row in (pr.attachments or []) if isinstance(row, dict)
               and SIGNED_PR_TYPE in (row.get('type'), row.get('document_type'))]
    if not sources:
        raise ApprovalRecordSourceMissing('PR')
    selected = max(sources, key=lambda row: (bool(current_sha and row.get('sha256') == current_sha), _uploaded_at(row)))
    return {'storage_key': requisition_source_key(pr, selected), 'sha256': selected.get('sha256')}


def _po_source(order):
    documents = list(order.source_documents.filter(document_type__in=('purchase_order', 'unknown'))
                     .only('id', 's3_key', 'extracted_data', 'created_at').order_by('-created_at', '-id'))
    by_id = {str(document.pk): document for document in documents}
    # A signed attachment's digest can identify a current re-upload of an older
    # document, but its ID must belong to this order's confirmed relation.
    selected = None
    for attachment in reversed(order.attachments or []):
        if not isinstance(attachment, dict) or attachment.get('type') != 'signed_purchase_order_pdf':
            continue
        document = by_id.get(str(attachment.get('document_id') or ''))
        if document and attachment.get('sha256') and attachment['sha256'] == (document.extracted_data or {}).get('source_sha256'):
            selected = document
            break
    selected = selected or (documents[0] if documents else None)
    if selected:
        return {'storage_key': selected.s3_key, 'sha256': (selected.extracted_data or {}).get('source_sha256')}
    legacy = uploaded_purchase_order_sources(order)
    if not legacy or not legacy[-1].get('storage_key'):
        raise ApprovalRecordSourceMissing('linked PO')
    return legacy[-1]


def _original_bytes(source, label):
    key = source.get('storage_key')
    if not is_safe_source_storage_key(key):
        raise ApprovalRecordSourceMissing(label, reason='invalid_reference')
    try:
        with default_storage.open(key, 'rb') as stored:
            content = stored.read(MAX_SOURCE_BYTES + 1)
    except FileNotFoundError as error:
        raise ApprovalRecordSourceMissing(label, reason='file_missing') from error
    except ClientError as error:
        if str(error.response.get('Error', {}).get('Code')) in {'NoSuchKey', 'NotFound', '404'}:
            raise ApprovalRecordSourceMissing(label, reason='file_missing') from error
        raise ApprovalRecordStorageUnavailable({'error': f'The original {label} PDF could not be loaded. Please retry.'}) from error
    except Exception as error:
        raise ApprovalRecordStorageUnavailable({'error': f'The original {label} PDF could not be loaded. Please retry.'}) from error
    digest = source.get('sha256')
    if len(content) > MAX_SOURCE_BYTES or not content.startswith(b'%PDF'):
        raise ApprovalRecordInvalid({'error': f'The original {label} PDF cannot be read for this preview.'})
    if isinstance(digest, str) and re.fullmatch(r'[a-fA-F0-9]{64}', digest) and hashlib.sha256(content).hexdigest() != digest.lower():
        raise ApprovalRecordInvalid({'error': f'The original {label} PDF differs from its saved evidence. Review the original upload.'})
    return content


def _order_without_uploaded_pdf(order):
    """App/Excel data may be rendered; evidence of an uploaded PDF wins."""
    if order.source_documents.filter(document_type__in=('purchase_order', 'unknown')).exists():
        return False
    if any(isinstance(row, dict) and row.get('type') == 'signed_purchase_order_pdf'
           for row in (order.attachments or [])):
        return False
    return not any(isinstance(row, dict) and (
        row.get('evidence_document_id') or str(row.get('stage') or '').casefold() == 'signed po document approval'
    ) for row in (order.approval_log or []))


def build_requisition_approval_record_pdf(pr, request, *, include_metadata=False):
    """Return the PDF and filename, optionally followed by source metadata.

    Original PR pages precede the linked PO. Native POs use the same official
    renderer as their export; retained original uploads always take precedence.
    """
    import pymupdf

    _check_read(request, pr)
    # Query the actual relation afresh; cached PR metadata and editable number
    # references cannot authorize another order's original document.
    order = PurchaseOrder.objects.filter(pr_reference_id=pr.pk).order_by('-created_at', '-pk').first()
    if order:
        _check_read(request, order, order=True)
    metadata = {'po_source': 'none', 'attachment_warning_count': 0}
    native = bool(order and _order_without_uploaded_pdf(order))
    try:
        sources = [('PR', _pr_source(pr))]
        if order and not native:
            sources.append(('linked PO', _po_source(order)))
            metadata['po_source'] = 'uploaded_original'
        originals = [(label, _original_bytes(source, label)) for label, source in sources]
    except ApprovalRecordSourceMissing as error:
        error.detail['requisition_id'] = str(pr.pk)
        if order:
            error.detail['purchase_order_id'] = str(order.pk)
        raise
    if native:
        from .purchase_order_exports import build_purchase_order_pdf
        generated, warnings = build_purchase_order_pdf(order)
        originals.append(('RADAI-generated PO', generated))
        metadata.update(po_source='radai_generated', attachment_warning_count=len(warnings))
    try:
        with pymupdf.open() as combined:
            for label, content in originals:
                with pymupdf.open(stream=content, filetype='pdf') as original:
                    if original.needs_pass or not original.is_pdf or original.page_count < 1:
                        raise ApprovalRecordInvalid({'error': f'The original {label} PDF is unreadable or password protected.'})
                    combined.insert_pdf(original)
            content = combined.tobytes(garbage=3, deflate=True) if order else originals[0][1]
    except ApprovalRecordInvalid:
        raise
    except Exception as error:
        raise ApprovalRecordInvalid({'error': 'An original PDF could not be read. Review the uploaded PR and PO originals.'}) from error
    number = re.sub(r'[^A-Za-z0-9._-]+', '_', str(pr.pr_number)).strip('._-') or 'PR'
    result = (content, f'{number}_Approval_Record.pdf')
    return (*result, metadata) if include_metadata else result
