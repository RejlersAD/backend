"""Keep commercial terms bound to the purchase order that was approved."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json

from rest_framework.exceptions import ValidationError


COMMERCIAL_LOCK_REASON = (
    'Approved commercial details are locked. Create a revised purchase order for commercial changes.'
)
CONTENT_REVIEW_REASON = (
    'The purchase order commercial details differ from the recorded approval. Approval review is required.'
)
MONEY_FIELDS = frozenset({
    'net_amount', 'total_amount', 'tax_amount', 'vat_percentage', 'discount_amount',
})
# These are saved document terms, not receipt/invoice progress or internal notes.
# Relationship reconciliation may change canonical project/PR links without
# changing the approved printed project number or commercial agreement.
COMMERCIAL_FIELDS = (
    'po_number', 'po_date', 'vendor_id', 'title', 'description', 'form_note',
    'seller_reference', 'quote_ref', 'seller_license_no', 'seller_address',
    'invoicing_attn', 'invoicing_emails', 'company_fax',
    'buyer_reference_pm', 'buyer_reference_email', 'buyer_reference_pe',
    'net_amount', 'total_amount', 'currency', 'tax_amount', 'vat_percentage',
    'discount_amount', 'vat_basis', 'payment_terms', 'payment_mode', 'delivery_terms',
    'marking', 'payment_milestones', 'workshop_rates', 'items', 'items_table_headers',
    'start_date', 'end_date', 'expected_delivery', 'project_number', 'rad_project_no',
    'end_client', 'contractor', 'subcontractor', 'company_agreement_no',
    'scope_of_services', 'safety_requirements', 'variations_clause', 'time_schedule',
    'reporting_meetings', 'performance_requirements', 'terms_and_conditions',
    'material_specifications', 'required_certifications', 'inspection_requirements',
    'witness_inspection', 'heat_numbers_required', 'ndt_requirements',
    'applicable_standards', 'material_grade', 'pressure_rating', 'temperature_rating',
    'contact_persons',
)


def _canonical(value):
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, Decimal):
        return format(value.normalize(), 'f')
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if value is not None and not isinstance(value, (str, bool, int, float)):
        return str(value)
    return value


def _field_value(field, value):
    if field in MONEY_FIELDS and value is not None:
        try:
            return format(Decimal(str(value)).normalize(), 'f')
        except (InvalidOperation, ValueError):
            pass
    if field == 'contact_persons' and isinstance(value, dict):
        # Storage retention bookkeeping is not part of the approved document.
        value = {key: item for key, item in value.items() if not str(key).startswith('_retained_')}
    return _canonical(value)


def is_requisition_approval_history(row):
    """Recognize server-owned PR history, which never approves PO terms.

    Assignment normalization does not accept these source markers from a
    client. A reviewed PR signer may retain a user ID in historical evidence,
    so the external flag and exact source distinguish it from a PO assignment.
    """
    return (
        isinstance(row, dict) and row.get('external') is True
        and row.get('source') in ('purchase_requisition', 'signed_purchase_requisition_pdf')
    )


def commercial_edit_locked(order):
    if any(getattr(order, field, None) for field in ('approved_at', 'approved_date', 'approval_signature')):
        return True
    return any(
        isinstance(row, dict) and str(row.get('status') or '').strip().lower() == 'approved'
        and not is_requisition_approval_history(row)
        for row in (getattr(order, 'approval_log', None) or [])
    )


def protect_purchase_order_content(order, changes):
    """Reject material edits after any approval, including older signed orders.

    The caller must repeat this check on the locked row immediately before
    saving; validation performed before a concurrent approval is insufficient.
    """
    if not commercial_edit_locked(order):
        return
    changes = dict(changes)
    if 'vendor' in changes:
        changes['vendor_id'] = getattr(changes['vendor'], 'pk', changes['vendor'])
    changed = [
        field for field in COMMERCIAL_FIELDS if field in changes
        and _field_value(field, changes[field]) != _field_value(field, getattr(order, field, None))
    ]
    if changed:
        raise ValidationError({('vendor' if field == 'vendor_id' else field): COMMERCIAL_LOCK_REASON for field in changed})


def purchase_order_content_fingerprint(order):
    payload = {field: _field_value(field, getattr(order, field, None)) for field in COMMERCIAL_FIELDS}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return 'po-v1:' + hashlib.sha256(encoded).hexdigest()


def purchase_order_content_issue(order):
    fingerprints = [
        row['content_fingerprint'] for row in (getattr(order, 'approval_log', None) or [])
        if isinstance(row, dict) and str(row.get('status') or '').strip().lower() == 'approved'
        and not is_requisition_approval_history(row)
        and row.get('content_fingerprint')
    ]
    if fingerprints and any(value != purchase_order_content_fingerprint(order) for value in fingerprints):
        return CONTENT_REVIEW_REASON
    return ''
