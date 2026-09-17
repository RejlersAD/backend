"""Read-only approval previews assembled from the authorized original PDFs."""

import hashlib
import re
from datetime import timezone as datetime_timezone
from pathlib import PurePosixPath

from botocore.exceptions import ClientError
from django.core.files.storage import default_storage
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import APIException, NotFound, PermissionDenied

from apps.rbac.action_policy import record_workflow_not_denied, request_action_allowed

from ..models import PurchaseOrder
from .purchase_order_approvals import _entry_matches_user
from .purchase_order_sources import uploaded_purchase_order_sources
from .requisition_source_documents import SIGNED_PR_TYPE, requisition_source_key
from .requisition_workflow import RequisitionWorkflowService


MAX_SOURCE_BYTES = 15 * 1024 * 1024


class ApprovalRecordInvalid(APIException):
    status_code = 409
    default_code = 'approval_record_invalid'


class ApprovalRecordStorageUnavailable(APIException):
    status_code = 503
    default_code = 'approval_record_storage_unavailable'


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
        raise NotFound({'error': 'The original PR PDF is unavailable.'})
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
        raise NotFound({'error': 'The original linked PO PDF is unavailable.'})
    return legacy[-1]


def _original_bytes(source, label):
    key = source.get('storage_key')
    if (not isinstance(key, str) or not key or key != key.strip()
            or any(value in key for value in ('\\', ':', '%')) or key.startswith('/')
            or any(part in ('', '.', '..') for part in key.split('/'))
            or str(PurePosixPath(key)) != key):
        raise NotFound({'error': f'The original {label} PDF is unavailable.'})
    try:
        with default_storage.open(key, 'rb') as stored:
            content = stored.read(MAX_SOURCE_BYTES + 1)
    except FileNotFoundError as error:
        raise NotFound({'error': f'The original {label} PDF is unavailable.'}) from error
    except ClientError as error:
        if str(error.response.get('Error', {}).get('Code')) in {'NoSuchKey', 'NotFound', '404'}:
            raise NotFound({'error': f'The original {label} PDF is unavailable.'}) from error
        raise ApprovalRecordStorageUnavailable({'error': f'The original {label} PDF could not be loaded. Please retry.'}) from error
    except Exception as error:
        raise ApprovalRecordStorageUnavailable({'error': f'The original {label} PDF could not be loaded. Please retry.'}) from error
    digest = source.get('sha256')
    if len(content) > MAX_SOURCE_BYTES or not content.startswith(b'%PDF'):
        raise ApprovalRecordInvalid({'error': f'The original {label} PDF cannot be read for this preview.'})
    if isinstance(digest, str) and re.fullmatch(r'[a-fA-F0-9]{64}', digest) and hashlib.sha256(content).hexdigest() != digest.lower():
        raise ApprovalRecordInvalid({'error': f'The original {label} PDF differs from its saved evidence. Review the original upload.'})
    return content


def build_requisition_approval_record_pdf(pr, request):
    """Return (PDF bytes, filename), preserving originals and current relations.

    Each current source appears once, with every PR page preceding every PO
    page. With no linked PO the exact original PR bytes are returned.
    """
    import pymupdf

    _check_read(request, pr)
    # Query the actual relation afresh; cached PR metadata and editable number
    # references cannot authorize another order's original document.
    order = PurchaseOrder.objects.filter(pr_reference_id=pr.pk).order_by('-created_at', '-pk').first()
    if order:
        _check_read(request, order, order=True)
    sources = [('PR', _pr_source(pr))]
    if order:
        sources.append(('linked PO', _po_source(order)))
    originals = [(label, _original_bytes(source, label)) for label, source in sources]
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
    return content, f'{number}_Approval_Record.pdf'
