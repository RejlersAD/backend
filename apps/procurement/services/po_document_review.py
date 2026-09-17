"""Validate reviewed fields without saving a partial PO workflow."""

from copy import deepcopy

from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .po_excel_import import canonical_po_number
from .procurement_lifecycle import ProcurementDeleteConflict
from .procurement_vat import CONFIRMED_BASES, confirmed_totals, decimal_amount
from .signed_po_pdf_import import _serializable_fields


def reviewed_document_fields(saved_fields, validated_data, *, user):
    fields = deepcopy(saved_fields or {})
    fields.setdefault('source_extracted_data', deepcopy(fields))
    values = dict(validated_data)
    basis = values.pop('vat_basis', None)
    entered = values.pop('entered_amount', None)
    monetary_fields = {'total_amount', 'tax_amount', 'gross_amount'}
    changed_money = any(key in values and decimal_amount(values[key]) != decimal_amount(fields.get(key))
                        for key in monetary_fields)
    changed_money = changed_money or ('currency' in values and values['currency'] != fields.get('currency'))
    if basis in CONFIRMED_BASES:
        if entered is None:
            raise ValidationError({'entered_amount': 'Enter the price to confirm its VAT treatment.'})
        try:
            totals = confirmed_totals(entered, basis)
        except ValueError as error:
            raise ValidationError({'vat_basis': str(error)}) from error
        fields['canonical_financials'] = _serializable_fields({**totals, 'entered_amount': entered, 'vat_basis': basis})
        for key in monetary_fields:
            values.pop(key, None)
    elif entered is not None or changed_money:
        raise ValidationError({'vat_basis': 'Confirm whether VAT applies before changing amounts.'})
    if 'pr_id' in values:
        pr = values.pop('pr_id')
        if fields.get('originating_pr_id') and str(pr.pk if pr else '') != str(fields['originating_pr_id']):
            raise ProcurementDeleteConflict('Keep the originating purchase recommendation selected for this uploaded PDF.')
        fields['pr_id'] = str(pr.pk) if pr else None
        fields['pr_number'] = pr.pr_number if pr else ''
    if 'po_number' in values:
        supplied = values.pop('po_number')
        fields.update(po_number=canonical_po_number(supplied), source_po_number=supplied)
    if 'vendor_name' in values and values['vendor_name'] != fields.get('vendor_name'):
        fields['vendor_id'] = None
        fields['vendor_name_source'] = 'manual'
    fields.update(_serializable_fields(values))
    fields['reconciliation_required'] = True
    fields['reconciliation_issues'] = [
        *([] if fields.get('pr_id') else ['Link the matching purchase recommendation.']),
        'Supplier and order details await reconciliation.',
    ]
    fields['reviewed_by'] = str(user.pk)
    fields['reviewed_at'] = timezone.now().isoformat()
    fields['manually_reviewed_fields'] = sorted(set(fields.get('manually_reviewed_fields', [])) | set(validated_data))
    return fields
