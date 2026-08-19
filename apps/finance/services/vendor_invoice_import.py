"""Review-first OCR import and PO matching for procurement vendor invoices."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from typing import Any

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.finance.models import (
    AllocationMatchMethod,
    AuditLog,
    Invoice,
    InvoiceLineItem,
    InvoiceMatchStatus,
    InvoicePurchaseOrderAllocation,
    InvoiceStatus,
    ProcurementInvoiceStatus,
)
from apps.procurement.config import THREE_WAY_MATCHING_CONFIG
from apps.procurement.models import PurchaseOrder, Vendor
from apps.procurement.services.po_tesseract_extractor import extract_text_from_pdf_tesseract


MAX_PDF_BYTES = 20 * 1024 * 1024
SUPPORTED_CURRENCIES = {'AED', 'USD', 'EUR', 'GBP', 'SAR', 'QAR', 'OMR', 'KWD', 'BHD'}
PO_PATTERN = re.compile(
    r'\bRAD[-\s](?:GEN|PRJ)[-\s]PUR[-\s]\d{3,5}(?:[-_\s](?:[A-Z]{3})?\d{4})?\b',
    re.IGNORECASE,
)


def _clean(value: Any) -> str:
    return re.sub(r'\s+', ' ', str(value or '')).strip(' \t:|-')


def _normal(value: Any) -> str:
    return re.sub(r'[^a-z0-9]', '', _clean(value).lower())


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _decimal(value: Any, field: str, *, required: bool = False) -> Decimal | None:
    if value in (None, ''):
        if required:
            raise ValidationError({field: 'This field is required.'})
        return None
    cleaned = re.sub(r'[^0-9.,-]', '', str(value))
    if ',' in cleaned and '.' in cleaned:
        if cleaned.rfind(',') > cleaned.rfind('.'):
            cleaned = cleaned.replace('.', '').replace(',', '.')
        else:
            cleaned = cleaned.replace(',', '')
    elif ',' in cleaned:
        tail = cleaned.rsplit(',', 1)[-1]
        cleaned = cleaned.replace(',', '.') if len(tail) == 2 else cleaned.replace(',', '')
    try:
        result = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        raise ValidationError({field: 'Enter a valid amount.'})
    if result < 0:
        raise ValidationError({field: 'Amount cannot be negative.'})
    return result


def _date(value: Any, field: str, *, required: bool = False):
    if value in (None, ''):
        if required:
            raise ValidationError({field: 'This field is required.'})
        return None
    if hasattr(value, 'year') and not isinstance(value, str):
        return value
    raw = _clean(value)
    for fmt in (
        '%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y',
        '%m/%d/%Y', '%d %b %Y', '%d %B %Y', '%b %d, %Y', '%B %d, %Y',
    ):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValidationError({field: 'Use YYYY-MM-DD or a recognizable invoice date.'})


def _first_label_value(text: str, labels: list[str], max_length: int = 300) -> str:
    label_group = '|'.join(labels)
    match = re.search(
        rf'(?im)^\s*(?:{label_group})\s*(?:number|no\.?|#)?\s*[:\-]?\s*([^\n\r]{{1,{max_length}}})$',
        text,
    )
    return _clean(match.group(1)) if match else ''


def _extract_labeled_amount(text: str, labels: list[str]) -> Decimal | None:
    label_group = '|'.join(labels)
    pattern = re.compile(
        rf'(?im)^\s*(?:{label_group})\s*[:\-]?\s*(?:AED|USD|EUR|GBP|SAR|QAR|OMR|KWD|BHD|DHS?\.?|[$€£])?\s*'
        r'([-]?[0-9][0-9,.]*(?:\s?[0-9]{2})?)\b',
    )
    matches = list(pattern.finditer(text))
    if not matches:
        return None
    try:
        return _decimal(matches[-1].group(1), 'amount')
    except ValidationError:
        return None


def _extract_date_after_label(text: str, labels: list[str]):
    label_group = '|'.join(labels)
    match = re.search(
        rf'(?i)(?:{label_group})\s*[:\-]?\s*'
        r'(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|'
        r'\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})',
        text,
    )
    if not match:
        return None
    try:
        return _date(match.group(1), 'date')
    except ValidationError:
        return None


class VendorInvoiceImportService:
    """Extract, suggest, validate, and persist procurement invoices safely."""

    def validate_pdf(self, pdf_bytes: bytes, filename: str) -> None:
        if not filename.lower().endswith('.pdf'):
            raise ValidationError({'file': 'Only PDF files are supported.'})
        if not pdf_bytes.startswith(b'%PDF'):
            raise ValidationError({'file': 'The uploaded file is not a valid PDF.'})
        if len(pdf_bytes) > MAX_PDF_BYTES:
            raise ValidationError({'file': 'PDF exceeds the 20 MB limit.'})

    def preview(self, pdf_bytes: bytes, filename: str) -> dict[str, Any]:
        self.validate_pdf(pdf_bytes, filename)
        source_hash = hashlib.sha256(pdf_bytes).hexdigest()
        duplicate = Invoice.objects.filter(source_file_sha256=source_hash).first()
        if duplicate:
            raise ValidationError({
                'file': 'This exact PDF has already been recorded.',
                'duplicate_invoice_id': duplicate.pk,
                'duplicate_invoice_number': duplicate.invoice_number,
            })

        text = extract_text_from_pdf_tesseract(pdf_bytes)
        if len(_clean(text)) < 30:
            raise ValidationError({'file': 'No readable invoice text was detected. Manual entry is required.'})

        extracted, confidence, warnings = self._extract_fields(text)
        vendor_suggestions = self._suggest_vendors(extracted)
        po_suggestions = self._suggest_purchase_orders(extracted, vendor_suggestions)
        required_fields = ('invoice_number', 'vendor_name', 'invoice_date', 'total_amount', 'currency')
        missing = [field for field in required_fields if not extracted.get(field)]
        warnings.extend(f'{field.replace("_", " ").title()} was not confidently detected.' for field in missing)
        overall = round(sum(confidence.values()) / max(len(confidence), 1), 2)

        return {
            'filename': filename,
            'source_file_sha256': source_hash,
            'extracted': extracted,
            'field_confidence': confidence,
            'ocr_confidence': overall,
            'manual_review_required': bool(missing or overall < 80),
            'warnings': list(dict.fromkeys(warnings)),
            'vendor_suggestions': vendor_suggestions,
            'purchase_order_suggestions': po_suggestions,
            # Complete active master-data options let the reviewer recover when
            # OCR similarity is low; suggestions remain visually prioritised.
            'vendor_options': [
                {'id': str(vendor.id), 'vendor_code': vendor.vendor_code, 'name': vendor.name}
                for vendor in Vendor.objects.filter(status='active').only('id', 'vendor_code', 'name').order_by('name')
            ],
            'purchase_order_options': [
                {
                    'id': str(po.id), 'po_number': po.po_number,
                    'vendor_id': str(po.vendor_id), 'vendor_name': po.vendor.name,
                    'total_amount': str(po.total_amount), 'currency': po.currency,
                }
                for po in PurchaseOrder.objects.exclude(status='cancelled').select_related('vendor').order_by('-created_at')[:500]
            ],
            'extracted_text': text[:100000],
            'review_contract': {
                'required_fields': list(required_fields) + ['vendor_id'],
                'po_is_never_linked_automatically': True,
                'confirmed_po_requires_confirm_po_match': True,
            },
        }

    def _extract_fields(self, text: str) -> tuple[dict[str, Any], dict[str, int], list[str]]:
        text = text.replace('\x00', ' ')
        warnings: list[str] = []

        invoice_number = _first_label_value(
            text, [r'tax\s+invoice', r'invoice\s+(?:number|no\.?)', r'invoice\s*#', r'invoice'], 100,
        )
        if invoice_number:
            invoice_number = re.split(r'\s{2,}|\b(?:date|po\s*(?:no|number))\b', invoice_number, flags=re.I)[0]
            invoice_number = _clean(invoice_number)
            if PO_PATTERN.fullmatch(invoice_number):
                invoice_number = ''

        vendor_name = _first_label_value(text, [r'vendor', r'supplier', r'from', r'seller'], 300)
        if not vendor_name:
            for line in text.splitlines()[:25]:
                candidate = _clean(line)
                if re.search(r'\b(?:LLC|L\.L\.C|LTD|LIMITED|FZE|FZC|PJSC|COMPANY|CO\.)\b', candidate, re.I):
                    if not re.search(r'\b(?:bill to|ship to|invoice to)\b', candidate, re.I):
                        vendor_name = candidate[:300]
                        break

        po_match = PO_PATTERN.search(text)
        po_reference = re.sub(r'\s+', '-', po_match.group(0).upper()) if po_match else ''
        po_reference = re.sub(r'-(?=\d{4}$)', '_', po_reference) if po_reference else ''

        invoice_date = _extract_date_after_label(text, [r'invoice\s+date', r'date\s+of\s+invoice'])
        due_date = _extract_date_after_label(text, [r'due\s+date', r'payment\s+due'])
        payment_terms = _first_label_value(text, [r'payment\s+terms?', r'terms\s+of\s+payment'], 300)

        total = _extract_labeled_amount(
            text,
            [r'grand\s+total', r'total\s+including\s+vat', r'total\s+incl\.?\s+vat',
             r'invoice\s+total', r'total\s+amount\s+due', r'amount\s+due', r'net\s+payable'],
        )
        subtotal = _extract_labeled_amount(
            text, [r'sub\s*total', r'total\s+excluding\s+vat', r'amount\s+excl\.?\s+vat', r'net\s+amount'],
        )
        tax = _extract_labeled_amount(text, [r'vat\s+amount', r'tax\s+amount', r'total\s+vat'])
        if total is None and subtotal is not None:
            total = subtotal + (tax or Decimal('0'))
            warnings.append('Total was calculated from subtotal and tax; please verify it.')
        if subtotal is None and total is not None and tax is not None:
            subtotal = total - tax

        currency_match = re.search(r'\b(AED|USD|EUR|GBP|SAR|QAR|OMR|KWD|BHD)\b', text, re.I)
        currency = currency_match.group(1).upper() if currency_match else ('AED' if re.search(r'\bDHS?\.?\b|د\.?إ', text, re.I) else '')
        vat_number = _first_label_value(
            text, [r'(?:supplier|vendor)?\s*(?:vat|tax)\s+registration(?:\s+number)?', r'\bTRN'], 100,
        )
        vat_rate_match = re.search(r'(?i)\bVAT\s*(?:@|rate)?\s*[:\-]?\s*(\d{1,2}(?:\.\d{1,2})?)\s*%', text)
        vat_percentage = vat_rate_match.group(1) if vat_rate_match else ''
        lines = self._extract_line_items(text, currency)

        extracted = {
            'invoice_number': invoice_number,
            'vendor_name': vendor_name,
            'invoice_date': invoice_date.isoformat() if invoice_date else '',
            'due_date': due_date.isoformat() if due_date else '',
            'payment_terms': payment_terms,
            'po_reference_text': po_reference,
            'amount': str(subtotal) if subtotal is not None else '',
            'tax_amount': str(tax) if tax is not None else '',
            'total_amount': str(total) if total is not None else '',
            'currency': currency,
            'vat_percentage': vat_percentage,
            'vat_registration_number': vat_number,
            'line_items': lines,
        }
        confidence = {
            'invoice_number': 92 if invoice_number else 0,
            'vendor_name': 85 if vendor_name else 0,
            'invoice_date': 90 if invoice_date else 0,
            'po_reference_text': 98 if po_reference else 0,
            'amount': 88 if subtotal is not None else 0,
            'tax_amount': 88 if tax is not None else 0,
            'total_amount': 92 if total is not None else 0,
            'currency': 90 if currency else 0,
            'payment_terms': 80 if payment_terms else 0,
            'line_items': 65 if lines else 0,
        }
        return extracted, confidence, warnings

    def _extract_line_items(self, text: str, currency: str) -> list[dict[str, Any]]:
        """Conservative table fallback; uncertain rows remain editable in preview."""
        rows: list[dict[str, Any]] = []
        ignored = re.compile(r'(?i)\b(subtotal|grand total|amount due|vat|tax|invoice total|balance)\b')
        row_pattern = re.compile(
            r'^\s*(\d{1,3})[.)\s]+(.{3,180}?)\s+(\d+(?:[.,]\d{1,4})?)\s+'
            r'(?:[A-Z]{3}\s*)?([0-9][0-9,.]*)\s+(?:[A-Z]{3}\s*)?([0-9][0-9,.]*)\s*$',
        )
        for raw in text.splitlines():
            if ignored.search(raw):
                continue
            match = row_pattern.match(raw)
            if not match:
                continue
            try:
                quantity = _decimal(match.group(3), 'quantity')
                unit_price = _decimal(match.group(4), 'unit_price')
                total = _decimal(match.group(5), 'total_amount')
            except ValidationError:
                continue
            rows.append({
                'line_number': len(rows) + 1,
                'description': _clean(match.group(2)),
                'quantity': str(quantity),
                'unit_price': str(unit_price),
                'net_amount': str(total),
                'tax_rate': '',
                'tax_amount': '',
                'total_amount': str(total),
                'currency': currency,
                'po_item_reference': '',
                'ocr_confidence': 65,
            })
        return rows[:200]

    def _suggest_vendors(self, extracted: dict[str, Any]) -> list[dict[str, Any]]:
        name = _normal(extracted.get('vendor_name'))
        vat = _normal(extracted.get('vat_registration_number'))
        candidates = []
        for vendor in Vendor.objects.filter(status='active').only('id', 'vendor_code', 'name', 'vat_number'):
            name_score = SequenceMatcher(None, name, _normal(vendor.name)).ratio() if name else 0
            vat_match = bool(vat and vat == _normal(vendor.vat_number))
            score = 1.0 if vat_match else name_score
            if score >= 0.45:
                candidates.append({
                    'id': str(vendor.id), 'vendor_code': vendor.vendor_code, 'name': vendor.name,
                    'confidence': round(score * 100, 2),
                    'reason': 'VAT/TRN exact match' if vat_match else 'Vendor name similarity',
                })
        return sorted(candidates, key=lambda item: item['confidence'], reverse=True)[:5]

    def _suggest_purchase_orders(
        self, extracted: dict[str, Any], vendor_suggestions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        po_ref = _normal(extracted.get('po_reference_text'))
        currency = _clean(extracted.get('currency')).upper()
        total = _decimal(extracted.get('total_amount'), 'total_amount')
        top_vendor_id = vendor_suggestions[0]['id'] if vendor_suggestions and vendor_suggestions[0]['confidence'] >= 70 else None
        results = []
        queryset = PurchaseOrder.objects.exclude(status='cancelled').select_related('vendor')
        for po in queryset.order_by('-created_at')[:500]:
            reasons, score = [], 0
            if po_ref and po_ref == _normal(po.po_number):
                score += 65
                reasons.append('Exact PO reference')
            if top_vendor_id and str(po.vendor_id) == top_vendor_id:
                score += 20
                reasons.append('Vendor match')
            if currency and currency == po.currency.upper():
                score += 5
                reasons.append('Currency match')
            remaining = max(Decimal('0'), po.total_amount - po.total_invoiced_amount)
            if total is not None and total <= remaining * Decimal('1.05'):
                score += 10
                reasons.append('Amount fits remaining PO value')
            if score < 20:
                continue
            results.append({
                'id': str(po.id), 'po_number': po.po_number, 'vendor_id': str(po.vendor_id),
                'vendor_name': po.vendor.name, 'total_amount': str(po.total_amount),
                'remaining_amount': str(remaining), 'currency': po.currency,
                'confidence': min(score, 100), 'reasons': reasons,
                'requires_user_confirmation': True,
            })
        return sorted(results, key=lambda item: item['confidence'], reverse=True)[:10]

    def save_reviewed(
        self, *, pdf_bytes: bytes, filename: str, reviewed_data: dict[str, Any], user,
        expected_sha256: str = '',
    ) -> Invoice:
        self.validate_pdf(pdf_bytes, filename)
        source_hash = hashlib.sha256(pdf_bytes).hexdigest()
        if expected_sha256 and source_hash != expected_sha256:
            raise ValidationError({'file': 'The reviewed PDF does not match the previewed PDF.'})
        duplicate = Invoice.objects.filter(source_file_sha256=source_hash).first()
        if duplicate:
            raise ValidationError({'file': f'This PDF is already recorded as {duplicate.invoice_number}.'})

        data = self._validate_review(reviewed_data)
        confirmed_po = None
        if data['confirmed_po_id']:
            if not data['confirm_po_match']:
                raise ValidationError({'confirm_po_match': 'Explicit confirmation is required before linking a PO.'})
            try:
                confirmed_po = PurchaseOrder.objects.select_related('vendor').get(pk=data['confirmed_po_id'])
            except (PurchaseOrder.DoesNotExist, ValueError):
                raise ValidationError({'confirmed_po_id': 'The selected PO does not exist.'})
            if confirmed_po.status == 'cancelled':
                raise ValidationError({'confirmed_po_id': 'A cancelled PO cannot be linked.'})

        try:
            vendor = Vendor.objects.get(pk=data['vendor_id'], status='active')
        except (Vendor.DoesNotExist, ValueError):
            raise ValidationError({'vendor_id': 'Select an active vendor from the company database.'})
        if Invoice.objects.filter(
            vendor=vendor, invoice_number__iexact=data['invoice_number'],
        ).exists():
            raise ValidationError({
                'invoice_number': 'This vendor already has an invoice with this number.',
            })

        stored_path = ''
        try:
            safe_ext = os.path.splitext(filename)[1].lower() or '.pdf'
            stored_path = default_storage.save(
                f'invoices/{uuid.uuid4()}{safe_ext}', ContentFile(pdf_bytes),
            )
            with transaction.atomic():
                invoice = Invoice.objects.create(
                    invoice_number=data['invoice_number'], vendor=vendor,
                    vendor_name=data['vendor_name'], invoice_date=data['invoice_date'],
                    received_date=data['received_date'] or timezone.localdate(), due_date=data['due_date'],
                    payment_terms=data['payment_terms'], amount=data['amount'], tax_amount=data['tax_amount'],
                    total_amount=data['total_amount'], currency=data['currency'],
                    vat_percentage=data['vat_percentage'],
                    vat_registration_number=data['vat_registration_number'],
                    po_reference_text=data['po_reference_text'], invoice_type='finance',
                    extracted_text=str(reviewed_data.get('extracted_text', ''))[:100000],
                    line_items=_json_safe(data['line_items']),
                    ocr_metadata={'source': 'reviewed_pdf_import', 'field_confidence': reviewed_data.get('field_confidence', {})},
                    ocr_confidence=data['ocr_confidence'], manual_review_required=False,
                    source_file_sha256=source_hash, original_filename=filename[:500], file_path=stored_path[:1000],
                    status=InvoiceStatus.PENDING_APPROVAL,
                    procurement_status=(ProcurementInvoiceStatus.PROCUREMENT_REVIEW if confirmed_po else ProcurementInvoiceStatus.READY_FOR_MATCHING),
                    match_status=InvoiceMatchStatus.UNMATCHED, submitted_by=user,
                    procurement_reviewed_by=user, procurement_reviewed_at=timezone.now(),
                )
                for position, item in enumerate(data['line_items'], 1):
                    InvoiceLineItem.objects.create(
                        invoice=invoice, line_number=item.get('line_number') or position,
                        description=item.get('description', ''), quantity=item.get('quantity'),
                        unit_price=item.get('unit_price'), net_amount=item.get('net_amount'),
                        tax_rate=item.get('tax_rate'), tax_amount=item.get('tax_amount'),
                        total_amount=item.get('total_amount'), currency=item.get('currency') or data['currency'],
                        po_item_reference=item.get('po_item_reference', ''), source_data=_json_safe(item),
                        ocr_confidence=item.get('ocr_confidence'), manually_verified=True,
                    )
                if confirmed_po:
                    self._create_confirmed_allocation(invoice, confirmed_po, vendor, data, user)
                AuditLog.objects.create(
                    invoice=invoice, user=user, action='procurement_invoice_imported',
                    description='Reviewed supplier invoice PDF recorded.',
                    metadata={
                        'source_file_sha256': source_hash,
                        'confirmed_po': confirmed_po.po_number if confirmed_po else None,
                        'manual_review_completed': True,
                    },
                )
            return invoice
        except Exception:
            if stored_path and default_storage.exists(stored_path):
                default_storage.delete(stored_path)
            raise

    def _validate_review(self, payload: dict[str, Any]) -> dict[str, Any]:
        invoice_number = _clean(payload.get('invoice_number'))
        vendor_name = _clean(payload.get('vendor_name'))
        if not invoice_number:
            raise ValidationError({'invoice_number': 'This field is required.'})
        if len(invoice_number) > 100:
            raise ValidationError({'invoice_number': 'Maximum length is 100 characters.'})
        if not vendor_name:
            raise ValidationError({'vendor_name': 'This field is required.'})
        currency = _clean(payload.get('currency')).upper()
        if currency not in SUPPORTED_CURRENCIES:
            raise ValidationError({'currency': f'Select one of: {", ".join(sorted(SUPPORTED_CURRENCIES))}.'})
        total = _decimal(payload.get('total_amount'), 'total_amount', required=True)
        amount = _decimal(payload.get('amount'), 'amount')
        tax = _decimal(payload.get('tax_amount'), 'tax_amount')
        if amount is None and tax is not None:
            amount = total - tax
        if tax is None and amount is not None:
            tax = total - amount
        if amount is not None and tax is not None and abs((amount + tax) - total) > Decimal('0.02'):
            raise ValidationError({'total_amount': 'Total must equal net amount plus tax (tolerance AED/currency 0.02).'})
        if (amount is not None and amount < 0) or (tax is not None and tax < 0):
            raise ValidationError({'total_amount': 'Net and tax values cannot produce negative amounts.'})

        line_items = payload.get('line_items') or []
        if not isinstance(line_items, list):
            raise ValidationError({'line_items': 'Expected a list of invoice lines.'})
        cleaned_lines = []
        seen_numbers = set()
        for position, item in enumerate(line_items, 1):
            if not isinstance(item, dict):
                raise ValidationError({'line_items': f'Line {position} must be an object.'})
            line_number = int(item.get('line_number') or position)
            if line_number < 1 or line_number in seen_numbers:
                raise ValidationError({'line_items': 'Line numbers must be unique positive integers.'})
            seen_numbers.add(line_number)
            cleaned_lines.append({
                'line_number': line_number, 'description': _clean(item.get('description')),
                'quantity': _decimal(item.get('quantity'), 'quantity'),
                'unit_price': _decimal(item.get('unit_price'), 'unit_price'),
                'net_amount': _decimal(item.get('net_amount'), 'net_amount'),
                'tax_rate': _decimal(item.get('tax_rate'), 'tax_rate'),
                'tax_amount': _decimal(item.get('tax_amount'), 'tax_amount'),
                'total_amount': _decimal(item.get('total_amount'), 'line_total_amount'),
                'currency': _clean(item.get('currency')).upper() or currency,
                'po_item_reference': _clean(item.get('po_item_reference')),
                'ocr_confidence': _decimal(item.get('ocr_confidence'), 'ocr_confidence'),
            })
        confidence = _decimal(payload.get('ocr_confidence'), 'ocr_confidence')
        if confidence is not None and confidence > 100:
            raise ValidationError({'ocr_confidence': 'Confidence cannot exceed 100.'})
        return {
            'invoice_number': invoice_number, 'vendor_name': vendor_name,
            'vendor_id': payload.get('vendor_id'), 'invoice_date': _date(payload.get('invoice_date'), 'invoice_date', required=True),
            'received_date': _date(payload.get('received_date'), 'received_date'),
            'due_date': _date(payload.get('due_date'), 'due_date'),
            'payment_terms': _clean(payload.get('payment_terms'))[:300],
            'amount': amount, 'tax_amount': tax, 'total_amount': total, 'currency': currency,
            'vat_percentage': _decimal(payload.get('vat_percentage'), 'vat_percentage'),
            'vat_registration_number': _clean(payload.get('vat_registration_number'))[:100],
            'po_reference_text': _clean(payload.get('po_reference_text'))[:100],
            'line_items': cleaned_lines, 'ocr_confidence': confidence,
            'confirmed_po_id': payload.get('confirmed_po_id'),
            'confirm_po_match': str(payload.get('confirm_po_match', '')).lower() in ('true', '1', 'yes'),
        }

    def _create_confirmed_allocation(self, invoice, po, selected_vendor, data, user):
        tolerance = Decimal(str(THREE_WAY_MATCHING_CONFIG.get('tolerance_percentage', 5)))
        receipt_required = bool(THREE_WAY_MATCHING_CONFIG.get('require_receipt', True))
        accepted_receipts = list(po.receipts.filter(status__in=('accepted', 'partial')))
        prior_allocated = po.invoice_allocations.exclude(invoice=invoice).aggregate(
            total=Sum('allocated_amount')
        )['total'] or Decimal('0')
        remaining = max(Decimal('0'), po.total_amount - prior_allocated)
        amount_variance = data['total_amount'] - remaining
        within_tolerance = data['total_amount'] <= remaining * (Decimal('1') + tolerance / Decimal('100'))
        vendor_matched = po.vendor_id == selected_vendor.id
        currency_matched = po.currency.upper() == data['currency']
        exceptions = []
        if not vendor_matched:
            exceptions.append('vendor_mismatch')
        if not currency_matched:
            exceptions.append('currency_mismatch')
        if not within_tolerance:
            exceptions.append('amount_exceeds_po_tolerance')
        if receipt_required and not accepted_receipts:
            exceptions.append('missing_accepted_receipt')
        match_status = InvoiceMatchStatus.EXCEPTION if exceptions else InvoiceMatchStatus.MANUAL_MATCHED
        allocation = InvoicePurchaseOrderAllocation.objects.create(
            invoice=invoice, purchase_order=po, allocated_amount=data['total_amount'], currency=data['currency'],
            match_method=AllocationMatchMethod.MANUAL, match_status=match_status,
            match_confidence=Decimal('100'), po_amount_at_match=po.total_amount,
            invoice_amount_at_match=data['total_amount'], amount_variance=amount_variance,
            tolerance_percentage=tolerance, amount_within_tolerance=within_tolerance,
            vendor_matched=vendor_matched, currency_matched=currency_matched,
            receipt_required=receipt_required, exception_codes=exceptions,
            matched_by=user, matched_at=timezone.now(),
        )
        allocation.receipts.add(*accepted_receipts)
        from .payables import evaluate_three_way_match
        evaluate_three_way_match(allocation, user=user)
        # The legacy PO aggregate has no currency conversion. Keep it synchronized
        # only when its arithmetic is financially valid; the allocation still records
        # a confirmed cross-currency link as an exception for review.
        if currency_matched:
            po.related_invoices.add(invoice)
            po.update_invoice_status()


def parse_reviewed_data(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or '{}')
    except (TypeError, json.JSONDecodeError):
        raise ValidationError({'reviewed_data': 'Provide valid JSON.'})
    if not isinstance(value, dict):
        raise ValidationError({'reviewed_data': 'Expected a JSON object.'})
    return value
