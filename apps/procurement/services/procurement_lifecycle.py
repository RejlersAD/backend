"""Keep procurement relationships and owned source files consistent on deletion."""

import logging
import json
from pathlib import PurePosixPath

from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import ProtectedError, Q
from rest_framework.exceptions import APIException

from ..models import PODocument, PurchaseOrder, PurchaseRequisition, Receipt
from .requisition_source_documents import requisition_source_key
from .requisition_status import canonicalize_pr_status


logger = logging.getLogger(__name__)
PREVIOUS_STATUS = 'po_link_previous_status'
RETAINED_SOURCES = '_retained_requisition_sources'
RETAINED_ATTACHMENTS = '_retained_attachment_sources'


class ProcurementDeleteConflict(APIException):
    status_code = 409
    default_code = 'linked_records'

    def __init__(self, message):
        super().__init__({'error': message})


def mark_requisition_converted(pr, po_number):
    metadata = dict(pr.price_remarks_data or {})
    if canonicalize_pr_status(pr.status) != 'converted':
        metadata[PREVIOUS_STATUS] = canonicalize_pr_status(pr.status)
    pr.price_remarks_data = metadata
    pr.status = 'converted'
    pr.po_number_reference = po_number
    pr.save(update_fields=['status', 'po_number_reference', 'price_remarks_data', 'updated_at'])


def reconcile_requisition_orders(pr, removed_po_number):
    """Removing a PO must not grant an approval the recommendation never had."""
    remaining = pr.purchase_orders.order_by('-created_at', '-pk').first()
    if remaining:
        mark_requisition_converted(pr, remaining.po_number)
        return
    metadata = dict(pr.price_remarks_data or {})
    previous = metadata.pop(PREVIOUS_STATUS, None)
    if canonicalize_pr_status(pr.status) == 'converted':
        workflow = [row for row in (pr.approval_workflow_config or []) if isinstance(row, dict)]
        decisions = [str(row.get('status', '')).lower() for row in workflow]
        if decisions and all(value in {'approved', 'complete', 'completed'} for value in decisions):
            pr.status = 'approved'
        elif 'rejected' in decisions:
            pr.status = 'rejected'
        elif previous in {'draft', 'submitted', 'in_review', 'approved', 'rejected', 'cancelled'}:
            pr.status = previous
        elif pr.approved_at or (metadata.get('signed_document_verification') or {}).get('signed_off'):
            pr.status = 'approved'
        else:
            pr.status = 'draft'
    if pr.po_number_reference == removed_po_number:
        pr.po_number_reference = ''
    metadata.pop('po_link', None)
    pr.price_remarks_data = metadata
    pr.save(update_fields=['status', 'po_number_reference', 'price_remarks_data', 'updated_at'])


def _safe_key(value):
    if not isinstance(value, str) or not value or value != value.strip():
        return ''
    if value.startswith('/') or '\\' in value or '%' in value:
        return ''
    return value if all(part not in {'', '.', '..'} for part in value.split('/')) and str(PurePosixPath(value)) == value else ''


def _attachment_keys(record, prefix):
    keys = set()
    metadata = record.contact_persons if isinstance(record, PurchaseOrder) else record.price_remarks_data
    keys.update(_safe_key(key) for key in (metadata or {}).get(RETAINED_ATTACHMENTS, []))
    if isinstance(record, PurchaseOrder):
        keys.update(_safe_key(key) for key in (record.contact_persons or {}).get(RETAINED_SOURCES, []))
    for attachment in [*(record.attachments or []), *(getattr(record, 'management_approval_evidence', None) or [])]:
        if not isinstance(attachment, dict):
            continue
        key = _safe_key(attachment.get('s3_key') or attachment.get('storage_key'))
        if key.startswith(prefix):
            keys.add(key)
        if isinstance(record, PurchaseRequisition):
            original = requisition_source_key(record, attachment)
            if original:
                keys.add(original)
    return keys - {''}


def attachment_cleanup_keys(record):
    """Capture ownership while the current number still authorizes its paths."""
    if isinstance(record, PurchaseOrder):
        prefix = f'procurement/orders/{record.po_number}/'
    else:
        prefix = f'procurement/requisitions/{record.pr_number}/'
    return _attachment_keys(record, prefix)


def _has_reference(key):
    # Converted POs deliberately retain the source PR's attachment metadata.
    # Do not remove shared bytes while any saved record still refers to them.
    if PODocument.objects.filter(s3_key=key).exists():
        return True
    for model, fields in (
        (PurchaseRequisition, ('attachments', 'management_approval_evidence')),
        (PurchaseOrder, ('attachments',)),
        (Receipt, ('attachments',)),
    ):
        filters = Q()
        for field in fields:
            filters |= Q(**{f'{field}__icontains': key})
        if model.objects.filter(filters).exists():
            return True
    return False


def schedule_source_cleanup(keys):
    """Storage deletion follows a successful commit, never a rolled-back delete."""
    keys = {_safe_key(key) for key in keys} - {''}

    def cleanup():
        for key in keys:
            if not _has_reference(key):
                try:
                    default_storage.delete(key)
                except Exception:
                    logger.exception('Procurement source cleanup failed after record deletion')

    transaction.on_commit(cleanup, robust=True)


def _check_cost_dependencies(source_type, source_id):
    from apps.project_control.models import CostAllocation, CostLedgerEntry

    lookup = {'source_type': source_type, 'source_id': str(source_id)}
    allocations = CostAllocation.objects.filter(**lookup)
    if allocations.exclude(status='draft').exists() or CostLedgerEntry.objects.filter(**lookup).exists():
        raise ProcurementDeleteConflict('This record has Project Control cost allocations or ledger entries. Resolve those links before deleting it.')
    return allocations


@transaction.atomic
def delete_requisition(pr_id):
    pr = PurchaseRequisition.objects.select_for_update().get(pk=pr_id)
    allocations = _check_cost_dependencies('purchase_requisition', pr.pk)
    keys = attachment_cleanup_keys(pr)
    # A converted PO can outlive its PR and retains that PR's evidence. Carry
    # trusted cleanup ownership forward before removing the source record.
    # Ordinary PO inputs cannot create or replace this server-owned metadata.
    related_sources = Q()
    for key in keys:
        related_sources |= Q(attachments__icontains=key)
    if keys:
        for order in PurchaseOrder.objects.select_for_update().filter(related_sources):
            content = json.dumps(order.attachments or [])
            inherited = {key for key in keys if key in content}
            contacts = dict(order.contact_persons or {})
            contacts[RETAINED_SOURCES] = sorted(set(contacts.get(RETAINED_SOURCES, [])) | inherited)
            order.contact_persons = contacts
            order.save(update_fields=['contact_persons', 'updated_at'])
    try:
        pr.delete()  # Independent POs remain, with their nullable PR link cleared.
    except ProtectedError as error:
        raise ProcurementDeleteConflict('This recommendation is linked to protected Project Control records. Resolve those links before deleting it.') from error
    allocations.delete()
    schedule_source_cleanup(keys)


@transaction.atomic
def delete_order(po_id):
    # Match conversion's lock order: lock the PR before locking its order.
    relationship = PurchaseOrder.objects.only('pr_reference_id').get(pk=po_id)
    pr = PurchaseRequisition.objects.select_for_update().filter(pk=relationship.pr_reference_id).first()
    order = PurchaseOrder.objects.select_for_update().get(pk=po_id)
    if order.pr_reference_id != relationship.pr_reference_id:
        raise ProcurementDeleteConflict('The linked recommendation changed. Refresh the order and retry deletion.')
    if order.receipts.exists():
        raise ProcurementDeleteConflict('This order has goods receipts. Resolve those receipt records before deleting the order.')
    allocations = _check_cost_dependencies('purchase_order', order.pk)
    keys = attachment_cleanup_keys(order)
    documents = list(PODocument.objects.select_for_update().filter(confirmed_po=order))
    keys.update(document.s3_key for document in documents)
    number = order.po_number
    try:
        PODocument.objects.filter(pk__in=[document.pk for document in documents]).delete()
        order.delete()
    except ProtectedError as error:
        raise ProcurementDeleteConflict('This order is linked to invoices or protected Project Control records. Resolve those links before deleting it.') from error
    allocations.delete()
    if pr:
        reconcile_requisition_orders(pr, number)
    schedule_source_cleanup(keys)
