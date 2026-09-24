"""Render an editor snapshot in memory through the saved PO export pipeline."""

from copy import deepcopy
from decimal import Decimal, InvalidOperation
import json

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models
from rest_framework.exceptions import ValidationError

from ..models import PurchaseOrder, Vendor
from .requisition_validation import sanitize_attachment_name, validate_attachments


# Snapshot input may contain the entire form. Only document content is accepted;
# signatures, decisions, storage keys and audit fields always come from the DB.
DOCUMENT_FIELDS = set('''
po_number po_date pr_requester_name title description category form_note
seller_reference quote_ref seller_license_no invoicing_attn invoicing_emails company_fax
buyer_reference_pm buyer_reference_email buyer_reference_pe
total_amount net_amount currency tax_amount vat_percentage discount_amount vat_basis
payment_terms payment_mode delivery_terms marking payment_milestones workshop_rates
project_number project_manager end_client contractor subcontractor company_agreement_no rad_project_no
items items_table_headers start_date end_date expected_delivery confirmation_date
seller_contact_person seller_phone seller_fax seller_email seller_address
scope_of_services safety_requirements variations_clause time_schedule reporting_meetings
performance_requirements contact_persons terms_and_conditions notes
'''.split())
JSON_TYPES = {'invoicing_emails': list, 'payment_milestones': list, 'workshop_rates': dict,
              'items': list, 'items_table_headers': dict, 'contact_persons': dict}


def read_object(raw, label, expected=dict):
    if isinstance(raw, str):
        if len(raw) > 8 * 1024 * 1024:
            raise ValidationError({label: 'The document snapshot is too large.'})
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise ValidationError({label: 'Enter valid document data.'}) from exc
    if not isinstance(raw, expected):
        raise ValidationError({label: 'Enter valid document data.'})
    return raw


def _field_value(name, value):
    if name == 'summary':
        if not isinstance(value, str) or len(value) > 10000:
            raise ValidationError({name: 'Enter a valid purchase summary.'})
        return value
    field = PurchaseOrder._meta.get_field(name)
    if name in JSON_TYPES:
        if not isinstance(value, JSON_TYPES[name]):
            raise ValidationError({name: 'Invalid document content.'})
        if isinstance(value, list) and len(value) > 1000:
            raise ValidationError({name: 'Too many document rows.'})
        if name in {'items', 'payment_milestones'} and any(not isinstance(row, dict) for row in value):
            raise ValidationError({name: 'Each row must be an object.'})
        if name == 'items':
            for row in value:
                for key in ('quantity', 'qty', 'unit_price', 'price', 'total', 'line_total', 'discount'):
                    if row.get(key) in (None, ''):
                        continue
                    try:
                        number = Decimal(str(row[key]))
                        if not number.is_finite() or abs(number) > Decimal('9999999999999.99'):
                            raise ValueError('Invalid number.')
                    except (InvalidOperation, ValueError) as exc:
                        raise ValidationError({'items': 'Enter valid line amounts to preview the document.'}) from exc
        if name == 'contact_persons':
            from .purchase_order_introduction import validate_order_introduction
            validate_order_introduction(value)
            references = value.get('buyer_references', [])
            if not isinstance(references, list) or any(not isinstance(row, dict) for row in references):
                raise ValidationError({name: 'Invalid buyer contacts.'})
        return deepcopy(value)
    if value in (None, ''):
        if isinstance(field, models.DecimalField):
            return None if field.null else Decimal('0')
        if isinstance(field, models.DateField):
            return None
        return ''
    try:
        if isinstance(field, (models.CharField, models.TextField)) and not isinstance(value, str):
            raise ValueError('Expected text.')
        result = field.to_python(value)
        if isinstance(result, Decimal) and (not result.is_finite() or abs(result) > Decimal('9999999999999.99')):
            raise ValueError('Invalid amount.')
        if field.max_length and len(result) > field.max_length:
            raise ValueError('Text is too long.')
        return result
    except (DjangoValidationError, TypeError, ValueError) as exc:
        raise ValidationError({name: 'Enter a valid value to preview this document.'}) from exc


def _attachments(base, metadata, uploads):
    saved = list(base.attachments or []) if base else []
    if metadata is None:
        if uploads:
            raise ValidationError({'attachment_metadata': 'Provide the attachment order.'})
        return deepcopy(saved)
    metadata = read_object(metadata, 'attachment_metadata', list)
    if len(metadata) > 50:
        raise ValidationError({'attachment_metadata': 'Too many attachments.'})
    retained, result, used_saved, used_new = [], [], set(), set()
    validate_attachments(uploads)
    for row in metadata:
        if not isinstance(row, dict):
            raise ValidationError({'attachment_metadata': 'Invalid attachment row.'})
        existing, fresh = row.get('existing_attachment_index'), row.get('new_file_index')
        if (existing is None) == (fresh is None):
            raise ValidationError({'attachment_metadata': 'Select one saved or newly uploaded attachment per row.'})
        index, candidates, used = (existing, saved, used_saved) if existing is not None else (fresh, uploads, used_new)
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(candidates) or index in used:
            raise ValidationError({'attachment_metadata': 'The attachment selection has changed. Refresh the document.'})
        used.add(index)
        if existing is not None:
            item = deepcopy(saved[index])
            if not isinstance(item, dict):
                raise ValidationError({'attachment_metadata': 'This legacy attachment needs to be reattached.'})
            retained.append(item)
        else:
            upload = uploads[index]
            upload.seek(0)
            item = {'filename': sanitize_attachment_name(upload.name), 'content_type': upload.content_type,
                    'file_size': upload.size, '_preview_content': upload.read()}
        for name in ('title', 'description'):
            if name in row:
                if not isinstance(row[name], str) or len(row[name]) > 3000:
                    raise ValidationError({'attachment_metadata': 'Invalid attachment label.'})
                item[name] = row[name]
        result.append(item)
    if len(used_new) != len(uploads):
        raise ValidationError({'attachment_metadata': 'Every uploaded file must appear in the attachment order.'})
    validate_attachments(uploads, retained)
    return result


def document_preview_order(snapshot, *, base=None, attachment_metadata=None, uploads=()):
    """Return an unsaved copy; never reserve a number, upload a file or save a model."""
    snapshot = read_object(snapshot, 'snapshot')
    order = deepcopy(base) if base else PurchaseOrder(status='draft', total_amount=0)
    changed = base is None
    for name in DOCUMENT_FIELDS & snapshot.keys():
        value = _field_value(name, snapshot[name])
        changed = changed or value != getattr(order, name, '' if name == 'summary' else None)
        setattr(order, name, value)
    if 'summary' in snapshot:
        summary = _field_value('summary', snapshot['summary'])
        contacts = dict(order.contact_persons or {})
        if summary or 'purchase_summary' in contacts:
            changed = changed or summary != contacts.get('purchase_summary', '')
            contacts['purchase_summary'] = summary
            order.contact_persons = contacts
    if 'vendor' in snapshot:
        identifier = snapshot['vendor']
        if identifier in (None, ''):
            vendor = Vendor(name='Vendor not selected')
        else:
            try:
                vendor = Vendor.objects.get(pk=identifier)
            except (Vendor.DoesNotExist, DjangoValidationError, TypeError, ValueError) as exc:
                raise ValidationError({'vendor': 'Select an existing vendor to preview the order.'}) from exc
        changed = changed or vendor.pk != getattr(order, 'vendor_id', None)
        order.vendor = vendor
    elif not getattr(order, 'vendor_id', None):
        order.vendor = Vendor(name='Vendor not selected')
    order.attachments = _attachments(base, attachment_metadata, list(uploads))
    changed = changed or order.attachments != (base.attachments if base else [])
    if changed:
        # A saved signature cannot approve unsaved changes to the document.
        order.status = 'draft'
        order.approved_by = None
        order.approved_by_name = order.approved_by_title = ''
        order.approved_at = order.approved_date = None
        order.approval_signature = order.approval_stamp = ''
        order.approval_log = []
    return order
