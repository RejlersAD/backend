"""Read-only supplier contacts for the supplier actually selected by a PR."""

import re

from django.db.models import prefetch_related_objects

from apps.rbac.action_policy import record_workflow_not_denied, request_action_allowed

from ..models import PODocument
from .purchase_order_approvals import _entry_matches_user


def _text(value):
    return value.strip() if isinstance(value, str) else ''


def _name(value):
    return ''.join(character for character in _text(value).casefold() if character.isalnum())


def _identity(value):
    return str(value).strip() if isinstance(value, (str, int)) and not isinstance(value, bool) else ''


def _same_supplier(pr, order):
    if pr.vendor_id:
        return pr.vendor_id == order.vendor_id
    # Imported PRs can append a subcontractor note to their supplier name.
    primary_name = re.sub(r'\s+\(\s*M/s\.?[^()]*\)\s*$', '', _text(pr.supplier_name), flags=re.IGNORECASE)
    return not primary_name or _name(primary_name) == _name(order.vendor.name)


def _details(pr, order=None, document=None, previous=None):
    vendor = pr.vendor if pr.vendor_id else (order.vendor if order else None)
    result = {
        'vendor_id': str(vendor.pk) if vendor else None,
        'vendor_name': vendor.name if vendor else _text(pr.supplier_name),
        'contact_person': '', 'email': '',
        'sources': {'contact_person': '', 'email': ''},
    }

    def fill(values, source):
        for key in ('contact_person', 'email'):
            value = _text(values.get(key))
            if not result[key] and value:
                result[key], result['sources'][key] = value, source

    if previous:
        for key in ('contact_person', 'email'):
            if previous[key]:
                result[key], result['sources'][key] = previous[key], previous['sources'][key]

    if pr.vendor_id:
        fill({'contact_person': pr.vendor.contact_person, 'email': pr.vendor.email}, 'vendor_master')
    shortlist = [row for row in pr.selected_vendors if isinstance(row, dict)] if isinstance(pr.selected_vendors, list) else []
    if result['vendor_id']:
        matches = [row for row in shortlist if _identity(row.get('vendor_id') or row.get('id')) == result['vendor_id']]
    else:
        matches = [row for row in shortlist if _name(result['vendor_name']) and
                   _name(row.get('vendor_name') or row.get('name')) == _name(result['vendor_name'])]
        if len(matches) != 1:
            matches = []
        else:
            result['vendor_id'] = _identity(matches[0].get('vendor_id') or matches[0].get('id')) or None
    for row in matches:
        fill(row, 'selected_vendor')
    if order:
        fill({'contact_person': order.seller_contact_person, 'email': order.seller_email}, 'linked_po')
        fill({'contact_person': order.vendor.contact_person, 'email': order.vendor.email}, 'linked_po_vendor')
    if document:
        fields = document.extracted_data if isinstance(document.extracted_data, dict) else {}
        source_vendor_id = fields.get('vendor_id')
        same_vendor = (_identity(source_vendor_id) == str(order.vendor_id)) if source_vendor_id not in (None, '') else (
            bool(_name(fields.get('vendor_name'))) and _name(fields.get('vendor_name')) == _name(order.vendor.name)
        )
        if same_vendor:
            fill({'contact_person': fields.get('seller_contact_person'), 'email': fields.get('seller_email')}, 'linked_po_source')
    return result


def requisition_supplier_contacts(requisitions, request):
    """Batch contact presentation, honoring PO read access and actual FK links."""
    details = {pr.pk: _details(pr) for pr in requisitions}
    if not request or not any(not row['contact_person'] or not row['email'] for row in details.values()):
        return details
    if not record_workflow_not_denied(request.user, 'procurement_orders', 'read'):
        return details
    module_read = request_action_allowed(request, 'procurement_orders', 'read')
    # The view normally supplies this cache. Direct serializer calls also
    # load the page in batches, never one supplier query per requisition.
    prefetch_related_objects(requisitions, 'purchase_orders__vendor')
    candidates = {}
    for pr in requisitions:
        if details[pr.pk]['contact_person'] and details[pr.pk]['email']:
            continue
        order = max(pr.purchase_orders.all(), key=lambda row: row.created_at, default=None)
        if not order or not _same_supplier(pr, order):
            continue
        if details[pr.pk]['vendor_id'] and details[pr.pk]['vendor_id'] != str(order.vendor_id):
            continue
        assigned = any(_entry_matches_user(row, request.user) for row in (order.approval_log or []) if isinstance(row, dict))
        if not (module_read or order.created_by_id == request.user.pk or assigned):
            continue
        details[pr.pk] = _details(pr, order, previous=details[pr.pk])
        if not details[pr.pk]['contact_person'] or not details[pr.pk]['email']:
            candidates[pr.pk] = (pr, order)
    if not candidates:
        return details
    documents = {}
    for document in PODocument.objects.filter(
        confirmed_po_id__in=[order.pk for _, order in candidates.values()],
        document_type__in=('purchase_order', 'unknown'),
    ).only('id', 'confirmed_po_id', 'extracted_data', 'created_at').order_by('-created_at', '-pk'):
        documents.setdefault(document.confirmed_po_id, []).append(document)
    for pr, order in candidates.values():
        sources = documents.get(order.pk, [])
        selected = sources[0] if sources else None
        by_id = {str(document.pk): document for document in sources}
        for attachment in reversed(order.attachments or []):
            if not isinstance(attachment, dict) or attachment.get('type') != 'signed_purchase_order_pdf':
                continue
            source = by_id.get(str(attachment.get('document_id') or ''))
            evidence = source.extracted_data if source and isinstance(source.extracted_data, dict) else {}
            if source and attachment.get('sha256') and attachment['sha256'] == evidence.get('source_sha256'):
                selected = source
                break
        details[pr.pk] = _details(pr, order, selected, previous=details[pr.pk])
    return details
