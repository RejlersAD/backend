"""Import a signed Purchase Order PDF and reconcile it with RADAI master data."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import hashlib
import re
from typing import Any

from botocore.exceptions import ClientError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Case, IntegerField, Q, Value, When
from django.utils import timezone
from rest_framework.exceptions import APIException, PermissionDenied

from ..models import PODocument, PurchaseOrder, PurchaseRequisition, Vendor
from ..models_master import Project
from .po_excel_import import canonical_po_number
from .po_tesseract_extractor import PDFTextUnreadableError, extract_text_from_pdf_tesseract
from .document_filenames import build_procurement_pdf_filename
from .pr_excel_import import _match_vendor
from .purchase_order_numbering import PurchaseOrderNumberService
from .po_supplier_extraction import complete_wrapped_seller_name, extract_seller_cover_details, needs_seller_layout
from .po_supplier_contacts import parse_vendor_contact_block


# Cover, scope, payment terms and price summary; retain all PDF pages as evidence.
SIGNED_PO_TEXT_PAGE_LIMIT = 4
SIGNED_PO_CONTACT_PAGE_LIMIT = 8


class SignedPOImportError(ValueError):
    pass


class SignedPOSourceStorageUnavailable(APIException):
    status_code = 503
    default_detail = 'The retained PO source could not be checked or restored. Please retry.'
    default_code = 'signed_po_source_storage_unavailable'


def _match(pattern: str, text: str, default: str = "") -> str:
    result = re.search(pattern, text, re.IGNORECASE | re.MULTILINE | re.DOTALL)
    return re.sub(r"\s+", " ", result.group(1)).strip() if result else default


_TOTAL_LABEL = re.compile(
    r"\b(?:(?P<net>Total\s+(?:(?:Purchase|Estimated)\s+Price\s*:?|Price\s*:))"
    r"|(?P<tax>VAT(?:\s*\(\s*\d+(?:\.\d+)?\s*%\s*\))?\s*:)"
    r"|(?P<gross>Total\s+Sum\s*:))", re.IGNORECASE,
)
_AMOUNT_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?"
_AMOUNT_CURRENCY = r"(?:USD|AED|EUR|GBP)"
_AMOUNT_VALUE = re.compile(
    rf"[\s|]*(?:(?P<prefix>{_AMOUNT_CURRENCY})[\s|]*(?P<prefix_value>{_AMOUNT_NUMBER})(?![\w.,])"
    rf"|(?P<suffix_value>{_AMOUNT_NUMBER})[\s|]*(?P<suffix>{_AMOUNT_CURRENCY})(?!\w))",
    re.IGNORECASE,
)


def _purchase_totals(text: str) -> tuple[Decimal | None, Decimal | None, Decimal | None, str, bool]:
    """Read adjacent, labelled PO totals without crossing another field.

    The official cover uses amount/currency while the detail table uses
    currency/amount. OCR can also read the entire label column before the
    value column; only a complete, ordered group can establish that mapping.
    """
    cover = re.split(r'---\s*Page\s+2\s*---', text, maxsplit=1, flags=re.IGNORECASE)[0]
    if re.search(r'\bTotal\s+(?:Purchase|Estimated)\s+Price\b', cover, re.IGNORECASE):
        # An explicit cover total is authoritative. Later pages can contain
        # supplier quotes or contract totals, including different currencies.
        text = cover
    labels = list(_TOTAL_LABEL.finditer(text))
    candidates = {'net': set(), 'tax': set(), 'gross': set()}
    position = 0
    while position < len(labels):
        group = [labels[position]]
        position += 1
        while position < len(labels) and re.fullmatch(r'[\s|]*', text[group[-1].end():labels[position].start()]):
            group.append(labels[position])
            position += 1
        kinds = [label.lastgroup for label in group]
        if len(group) > 1 and kinds != [kind for kind in candidates if kind in kinds]:
            continue
        offset = group[-1].end()
        values = []
        for label in group:
            value = _AMOUNT_VALUE.match(text, offset)
            if not value:
                break
            amount = Decimal((value['prefix_value'] or value['suffix_value']).replace(',', ''))
            currency = (value['prefix'] or value['suffix']).upper()
            values.append((label.lastgroup, amount, currency))
            offset = value.end()
        # Incomplete columns must not make the final label consume the net
        # value, and surplus values make a column assignment ambiguous.
        if len(values) != len(group) or (len(group) > 1 and _AMOUNT_VALUE.match(text, offset)):
            continue
        for kind, amount, currency in values:
            candidates[kind].add((amount, currency))

    currencies = {currency for values in candidates.values() for _, currency in values}
    if any(len(values) > 1 for values in candidates.values()) or len(currencies) > 1:
        # Repeated cover/detail totals must agree; never combine currencies
        # or silently choose one of conflicting source amounts.
        return None, None, None, '', False
    values = [next(iter(candidates[kind]))[0] if candidates[kind] else None for kind in candidates]
    net, tax, gross = values
    if net is not None and gross is not None and gross != net + (tax or Decimal('0')):
        # An unreadable VAT value must not become zero beside a higher gross.
        return None, None, None, next(iter(currencies), ''), False
    # A second layout pass can recover a displaced value column, but must
    # never override conflicting or partially read source totals.
    retry_layout = not any(candidates.values()) and bool(labels) and bool(_AMOUNT_VALUE.search(text))
    return *values, next(iter(currencies), ''), retry_layout


def _date(value: str):
    value = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _approval_evidence(*, signature_verified=False, stamp_verified=False, approved_by_name='',
                       approved_by_title='', approved_date='', previous=None):
    """A visible signature is an observation, not a complete approval record."""
    from rest_framework.exceptions import ValidationError

    values = {}
    for key, value in (('approved_by_name', approved_by_name), ('approved_by_title', approved_by_title),
                       ('approved_date', approved_date)):
        if value is None:
            value = ''
        if not isinstance(value, str) or (key != 'approved_date' and len(value.strip()) > 300):
            raise ValidationError({key: 'Enter a valid PO approval detail or leave it blank for review.'})
        values[key] = value.strip()
    if values['approved_date'] and not _date(values['approved_date']):
        raise ValidationError({'approved_date': 'Enter a valid PO approval date or leave it blank for review.'})
    previous = previous if isinstance(previous, dict) else {}
    prior_complete = bool(previous.get('signature_verified') and
                          str(previous.get('approved_by_name') or '').strip() and _date(previous.get('approved_date')))
    for key in values:
        prior = previous.get(key)
        if isinstance(prior, str) and prior.strip() and (prior_complete or not values[key]):
            values[key] = prior.strip()
    visible = bool(signature_verified or previous.get('signature_visible') or previous.get('signature_verified'))
    complete = bool(visible and values['approved_by_name'] and _date(values['approved_date']))
    issues = []
    if visible and not complete:
        issues.append('PO signature is visible, but the approver name or a valid approval date is missing. '
                      'The documents were saved; PO approval evidence requires review.')
    elif not visible:
        issues.append('PO approval signature requires visual verification.')
    return {
        **values, 'signature_visible': visible, 'signature_verified': complete,
        'stamp_verified': bool(stamp_verified or previous.get('stamp_verified')),
        'approval_evidence_complete': complete, 'approval_evidence_issues': issues,
    }


_SELLER_FIELD_END = (
    r"\bSeller\s*(?:Address|Country|Reference|Contact|E[- ]?mail|Phone|Telephone|Fax|Signature)\b"
    # Two-column OCR may put the address value between 'Seller' and 'Address:'.
    r"|\bSeller\s*(?=\s+(?:Unit|Suite|Office|Building|P\.?\s*O\.?\s*Box)\b)"
    r"|\b(?:Address|Invoicing(?:\s+Address)?|Invoice\s+Address|Buyer(?:\s+(?:Reference|Address))?)\s*:"
    r"|\bInvoicing\b"
    r"|\b(?:Quote\s*Ref(?:erence)?\.?|License\s*No\.?|Payment\s+(?:Terms?|Mode)"
    r"|Delivery\s+(?:Terms?|Date)|Project|Purchase\s+Summary|Total\s+(?:Purchase|Estimated)\s+Price)\s*:"
)


def _seller_name(text: str) -> str:
    """Read the seller value without absorbing neighboring PO header fields."""
    start = re.search(r"\bSeller\s*:\s*", text or "", re.IGNORECASE)
    if not start:
        return ""
    value = text[start.end():]
    end = re.search(_SELLER_FIELD_END, value, re.IGNORECASE)
    if end:
        value = value[:end.start()]
    else:
        # With no recognizable following label, don't consume the rest of an
        # arbitrary page. Wrapped company names remain supported before labels.
        value = value.split("\n\n", 1)[0]
    return re.sub(r"\s+", " ", value).strip(" |\r\n\t")


def _native_seller_name(pdf_bytes: bytes) -> str:
    try:
        import pymupdf

        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as document:
            for page_number in range(min(len(document), SIGNED_PO_TEXT_PAGE_LIMIT)):
                page = document[page_number]
                value = _seller_name(page.get_text("text", sort=True))
                if value:
                    return value
    except Exception:
        pass
    return ""


def _seller_details(text):
    return extract_seller_cover_details(text)


_PO_SUMMARY_END = (
    r"\b(?:Total\s+(?:(?:Purchase|Estimated)\s+)?Price|Total\s+Sum|Net\s+Total|VAT\s*(?:\([^)]*\))?)\s*:"
    r"|\b(?:Approved\s+by|Approvals?|Order\s+Confirmation|Seller\s+(?:Name|Signature|Ref(?:erence)?\.?))\s*:"
    r"|\b(?:Scope|Prices|Summary\s+of\s+Prices|Payment|Terms\s*&\s*Conditions"
    r"|Delivery\s*(?:&\s*Installation|Place)|Software\s+Maintenance\s+Support)\s*:"
    r"|\bWe\s*,\s*|\bThe\s+total\s+purchase\s+price\b"
    r"|---\s*Page\s+\d+\s*---|\bRAD-(?:GEN|PRJ)-PUR-\d{4}_"
)


def _bounded_po_summary(value: str) -> str:
    # Keep wrapped title lines, but never absorb the next paragraph or field.
    value = re.split(r"\n\s*\n", value.strip(), maxsplit=1)[0]
    value = re.split(_PO_SUMMARY_END, value, maxsplit=1, flags=re.IGNORECASE)[0]
    value = re.sub(r"\s+", " ", value).strip(" |\t\r\n")
    # A missing boundary should produce a missing title, not a page of text.
    return value if len(value) <= 300 and re.search(r"[A-Za-z]{2}", value) else ""


def normalize_po_summary(text: str) -> str:
    """Extract a bounded PO title from source OCR or an old flattened summary.

    Prefer the explicit scope-page title over a two-column cover-page summary.
    Already clean titles are returned unchanged; no document-specific values
    are inferred when a title cannot be separated from neighboring fields.
    """
    text = str(text or "").replace("\r\n", "\n").strip()
    for heading in re.finditer(r"\bPURCHASE\s+ORDER\s*:\s*", text, re.IGNORECASE):
        title = _bounded_po_summary(text[heading.end():])
        if title:
            return title

    heading = re.search(r"\bPurchase\s+Summary\s*:\s*", text, re.IGNORECASE)
    value = text[heading.end():] if heading else text
    # Cover-page OCR can interleave the right-hand price column immediately
    # after the summary label, before the actual title on the following line.
    value = re.sub(
        r"^Total\s+(?:Purchase|Estimated)\s+Price\s*:?\s*"
        r"(?:(?:USD|AED|EUR|GBP)\s*[\d,]+\.\d{2}|[\d,]+\.\d{2}\s*(?:USD|AED|EUR|GBP))\s*",
        "", value, count=1, flags=re.IGNORECASE,
    )
    if not heading and re.match(r"(?:---\s*Page|PURCHASE\s+ORDER\b|Seller\s*:|RAD-(?:GEN|PRJ)-PUR-)", value, re.IGNORECASE):
        return ""
    return _bounded_po_summary(value)


def extract_signed_po_fields(pdf_bytes: bytes, filename: str) -> dict[str, Any]:
    try:
        text = extract_text_from_pdf_tesseract(pdf_bytes, max_pages=SIGNED_PO_TEXT_PAGE_LIMIT)
    except PDFTextUnreadableError as exc:
        raise SignedPOImportError(
            'The PDF text could not be read. Upload an unlocked PDF with clear, readable '
            'purchase order pages and try again.'
        ) from exc
    source_page_count = None
    try:
        import pymupdf

        with pymupdf.open(stream=pdf_bytes, filetype='pdf') as document:
            source_page_count = len(document)
    except (ImportError, RuntimeError, ValueError):
        pass
    source_number = _match(r"(RAD-(?:GEN|PRJ)-PUR-\d{4}_\s*[A-Z]{3}\d{4})", text)
    if not source_number:
        source_number = re.sub(r"\.pdf$", "", filename, flags=re.IGNORECASE)
    po_number = canonical_po_number(source_number)
    if not po_number:
        raise SignedPOImportError("The signed PDF does not contain a valid RAD PO number.")

    po_date_text = _match(r"\b(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})\b", text)
    delivery_text = _match(r"Delivery\s+date\s*:\s*(\d{1,2}\.\d{1,2}\.\d{4})", text)
    net, vat, gross, currency, retry_layout = _purchase_totals(text)
    seller_details = _seller_details(text)
    amount_text = ''
    if retry_layout or needs_seller_layout(text, seller_details):
        try:
            # Some scanned covers are read column-first, separating amount
            # labels from their values by addresses. Re-read only the cover
            # as rows; do not search contract pages for a plausible amount.
            amount_text = extract_text_from_pdf_tesseract(
                pdf_bytes, max_pages=1, ocr_config='--psm 6', max_image_dimension=2400,
            )
        except (PDFTextUnreadableError, RuntimeError):
            pass  # The original missing-amount review remains actionable.
        else:
            if retry_layout:
                net, vat, gross, currency, _ = _purchase_totals(amount_text)
            seller_details.update({key: value for key, value in _seller_details(amount_text).items() if value})
    currency = currency or "USD"

    raw_vendor_name = _match(r"Seller\s*:\s*(.+?)(?:\s+Seller\s+Reference|\Z)", text)
    native_vendor_name = _native_seller_name(pdf_bytes)
    vendor_name = native_vendor_name or complete_wrapped_seller_name(amount_text or text, _seller_name(text))

    def merge_contacts(contact_text):
        contacts = parse_vendor_contact_block(contact_text, vendor_name)
        for key, value in contacts.items():
            if value and (key in {'seller_contact_person', 'seller_email', 'seller_phone'} or not seller_details.get(key)):
                seller_details[key] = value
        return bool(contacts.get('seller_phone'))

    contact_found = merge_contacts(text) if vendor_name else False
    contact_keys = ('seller_contact_person', 'seller_email', 'seller_phone', 'seller_address', 'seller_country')
    if vendor_name and not contact_found and not all(seller_details.get(key) for key in contact_keys):
        # Contact sections can follow the commercial pages. Inspect only the
        # next few pages, stopping at a supplier-owned phone block. Reuse the
        # existing cover OCR, and never read a full contract attachment again.
        for page_number in range(SIGNED_PO_TEXT_PAGE_LIMIT + 1, min(source_page_count or 0, SIGNED_PO_CONTACT_PAGE_LIMIT) + 1):
            try:
                contact_text = extract_text_from_pdf_tesseract(
                    pdf_bytes, first_page=page_number, max_pages=1,
                    ocr_config='--psm 6', max_image_dimension=2400,
                )
            except (PDFTextUnreadableError, RuntimeError):
                continue
            if merge_contacts(contact_text):
                break
    project_number = _match(r"Project\s*:\s*(\d{5,12})", text)
    summary = normalize_po_summary(text)

    return {
        "ocr_text_length": len(text),
        "source_page_count": source_page_count,
        "extracted_page_count": min(source_page_count, SIGNED_PO_TEXT_PAGE_LIMIT) if source_page_count is not None else None,
        "extraction_truncated": source_page_count is not None and source_page_count > SIGNED_PO_TEXT_PAGE_LIMIT,
        "source_po_number": source_number,
        "source_pr_numbers": sorted(set(re.findall(
            r"\bRAD-(?:GEN|PRJ)-PR-\d{4,}_\d{4}\b", text, re.IGNORECASE,
        ))),
        "po_number": po_number,
        "po_date": _date(po_date_text),
        "vendor_name": vendor_name,
        "ocr_vendor_name_raw": raw_vendor_name,
        "vendor_name_source": "native" if native_vendor_name else "ocr",
        **seller_details,
        "seller_reference": _match(r"Seller\s+Reference\s*:\s*(Mr\.\s+[A-Za-z ]+)", text),
        "quote_ref": _match(r"Quote\s+Ref\.\s*:\s*([^\n]+)", text),
        "project_number": project_number,
        "summary": summary,
        "payment_terms": _match(r"Payment\s+Terms?\s*:\s*(.+?)(?:Delivery\s+terms|Payment\s+Mode)", text),
        "payment_mode": _match(r"Payment\s+Mode\s*:\s*([^\n]+)", text),
        "delivery_terms": _match(r"Delivery\s+terms\s*:\s*(.+?)(?:Payment|Delivery\s+date)", text),
        "expected_delivery": _date(delivery_text),
        "total_amount": net or Decimal("0.00"),
        "tax_amount": vat or Decimal("0.00"),
        "gross_amount": gross or ((net or Decimal("0.00")) + (vat or Decimal("0.00"))),
        "currency": currency,
        "items": [],
    }


def preview_signed_po_pdf(pdf_bytes, *, filename, pr_id=None):
    """Extract reviewable PO/supplier facts without staging a document or source."""
    from .po_pdf_approval import preview_signed_po_approval

    result = preview_signed_po_approval(pdf_bytes)
    fields = extract_signed_po_fields(pdf_bytes, filename)
    issues = _extraction_review_issues(fields)
    result.update(success=True, preview_only=True, extracted_data=_serializable_fields(fields),
                  mapping_issues=issues, reconciliation_issues=issues)
    if pr_id:
        pr = _originating_requisition(pr_id, lock=False)
        result.update(pr_id=str(pr.pk), pr_number=pr.pr_number, bound_pr_number=pr.pr_number)
    return result


def ensure_retained_po_source(document, pdf_bytes, fields, user, *, allow_restore=True):
    """Restore absent bytes from the same verified original, retaining its document."""
    from uuid import uuid4
    from .atomic_source_import import save_import_source
    from .procurement_lifecycle import ProcurementDeleteConflict
    from .purchase_order_sources import is_safe_source_storage_key

    digest = hashlib.sha256(pdf_bytes).hexdigest()
    if (document.extracted_data or {}).get('source_sha256') != digest:
        raise ProcurementDeleteConflict('The uploaded PDF differs from this retained source. Its original evidence was kept.')
    missing = not is_safe_source_storage_key(document.s3_key)
    if not missing:
        try:
            with default_storage.open(document.s3_key, 'rb') as stored:
                retained = stored.read(15 * 1024 * 1024 + 1)
        except FileNotFoundError:
            missing = True
        except ClientError as error:
            if str(error.response.get('Error', {}).get('Code')) in {'NoSuchKey', 'NotFound', '404'}:
                missing = True
            else:
                raise SignedPOSourceStorageUnavailable() from error
        except Exception as error:
            raise SignedPOSourceStorageUnavailable() from error
        else:
            if hashlib.sha256(retained).hexdigest() != digest:
                raise ProcurementDeleteConflict('The stored PO source differs from its saved evidence. It was not overwritten; review the source with an administrator.')
    if not missing:
        return document
    if not allow_restore:
        raise PermissionDenied('Purchase order update permission is required to restore its original PDF.')
    source_date = _date(fields.get('po_date')) or timezone.localdate()
    safe_name = build_procurement_pdf_filename(fields['po_number'], 'po', source_date)
    # A new key avoids overwriting a concurrent restore or unrelated content.
    key = save_import_source(default_storage,
        f'procurement/signed_documents/{source_date.year}/{uuid4().hex}_{safe_name}', ContentFile(pdf_bytes),
    )
    metadata = dict(document.extracted_data or {})
    metadata['source_storage_history'] = [*(metadata.get('source_storage_history') or []), {
        'previous_storage_key': document.s3_key, 'restored_at': timezone.now().isoformat(),
        'restored_by': str(user.pk), 'sha256': digest,
    }]
    document.s3_key, document.s3_url = key, default_storage.url(key)
    document.file_size_bytes = len(pdf_bytes)
    document.extracted_data = metadata
    document.save(update_fields=['s3_key', 's3_url', 'file_size_bytes', 'extracted_data', 'updated_at'])
    return document


def _store_source_pdf(pdf_bytes, fields, filename, user):
    from .atomic_source_import import save_import_source
    digest = hashlib.sha256(pdf_bytes).hexdigest()
    document = PODocument.objects.filter(extracted_data__source_sha256=digest).filter(
        Q(uploaded_by=user) | Q(confirmed_po__isnull=False),
    ).first()
    if document:
        return ensure_retained_po_source(document, pdf_bytes, fields, user), digest
    source_date = fields["po_date"] or timezone.localdate()
    safe_name = build_procurement_pdf_filename(fields["po_number"], "po", source_date)
    key = save_import_source(default_storage,
        f"procurement/signed_documents/{source_date.year}/{safe_name}", ContentFile(pdf_bytes),
    )
    return PODocument.objects.create(
        original_filename=filename, s3_key=key, s3_url=default_storage.url(key),
        file_size_bytes=len(pdf_bytes), uploaded_by=user,
    ), digest


def _serializable_fields(fields):
    return {key: value.isoformat() if hasattr(value, "isoformat") else str(value) if isinstance(value, Decimal) else value
            for key, value in fields.items()}


def _extraction_review_issues(fields):
    issues = []
    if not fields.get('extraction_reviewed') and (
        fields.get('extraction_truncated')
        or ('source_page_count' in fields and fields['source_page_count'] is None)
    ):
        issues.append(
            'Automatic extraction is limited to the first four purchase order pages. Review the amount, VAT and '
            'commercial terms against the complete saved PDF before completing reconciliation.'
        )
    if Decimal(str(fields.get('total_amount') or '0')) <= 0:
        issues.append('The purchase amount could not be confirmed. Review and save the amount from the original PDF.')
    return issues


def _originating_requisition(pr_id, *, lock=True):
    try:
        identity = PurchaseRequisition._meta.pk.to_python(pr_id)
    except (DjangoValidationError, ValueError, TypeError):
        raise SignedPOImportError('Select a valid originating purchase recommendation.')
    queryset = PurchaseRequisition.objects.select_for_update() if lock else PurchaseRequisition.objects
    pr = queryset.filter(pk=identity).first()
    if pr is None:
        raise SignedPOImportError('The originating purchase recommendation no longer exists. Refresh the PR list.')
    return pr


def validate_originating_requisition(pr, fields, *, po=None):
    """An upload launched from a PR cannot silently move to another PR."""
    from .pr_document_reconciliation import _order_references, normalize_document_number
    from .procurement_lifecycle import ProcurementDeleteConflict

    origin = fields.get('originating_pr_id')
    if origin and str(origin) != str(pr.pk):
        raise ProcurementDeleteConflict('This PDF was uploaded for another purchase recommendation. Its original PR link was kept.')
    number = canonical_po_number(fields.get('po_number') or fields.get('source_po_number'))
    valid, message = PurchaseOrderNumberService.verify(number, pr.pr_number)
    if not valid:
        raise ProcurementDeleteConflict(message)
    if po and po.pr_reference_id and po.pr_reference_id != pr.pk:
        raise ProcurementDeleteConflict('This purchase order is already linked to another recommendation. Its existing link was kept.')
    other_orders = PurchaseOrder.objects.filter(pr_reference_id=pr.pk)
    if po:
        other_orders = other_orders.exclude(pk=po.pk)
    if other_orders.exists():
        raise ProcurementDeleteConflict('This recommendation is already linked to a different purchase order. Review that link before uploading another order.')
    references = set()
    for source in (fields, fields.get('source_extracted_data') or {}):
        references.update(normalize_document_number(value, kind='PR')
                          for value in source.get('source_pr_numbers', []))
    if po:
        references.update(_order_references(po)[1])
    references.discard('')
    if references and references != {normalize_document_number(pr.pr_number, kind='PR')}:
        raise ProcurementDeleteConflict('The purchase order source names a different PR. Review the source document before linking.')


def _verified_origin_link(pr, po_id, user):
    from .pr_document_reconciliation import verify_originating_po_link

    return verify_originating_po_link(pr, po_id, user)


def _attach_existing_order(po, fields, pdf_bytes, filename, user, *, signature_verified,
                           stamp_verified, approved_by_name, approved_by_title, approved_date,
                           retained_document=None):
    """An upload is evidence, not permission to replace an existing order."""
    document, digest = (
        (retained_document, hashlib.sha256(pdf_bytes).hexdigest()) if retained_document is not None
        else _store_source_pdf(pdf_bytes, fields, filename, user)
    )
    if document.confirmed_po_id and document.confirmed_po_id != po.pk:
        raise SignedPOImportError('This original PDF is already linked to another purchase order.')
    previous = document.extracted_data or {}
    if previous.get('source_sha256') == digest and (
        previous.get('extraction_reviewed') or previous.get('reconciled_at')
    ):
        # Identical bytes must retain the reviewer's corrections; fresh OCR
        # cannot replace confirmed amounts or reopen a completed source review.
        fields = {**fields, **previous}
    issues = []
    saved_net = po.net_amount if po.net_amount is not None else po.total_amount
    if Decimal(str(fields['total_amount'])) != saved_net:
        issues.append('The PDF amount differs from the saved order. Review the order amount before reconciling.')
    if fields['currency'] != po.currency:
        issues.append('The PDF currency differs from the saved order.')
    matched = _match_vendor(fields['vendor_name'], [po.vendor])
    if not matched.get('matched'):
        issues.append('The PDF supplier differs from the saved order or could not be read. Review the supplier.')
    if not po.pr_reference_id:
        issues.append('PR link pending. Link the correct purchase recommendation during reconciliation.')
    issues[:0] = _extraction_review_issues(fields)
    # Re-uploading the same original without checking boxes must not erase a
    # prior reviewer-confirmed signature, stamp, name or source date.
    evidence = _approval_evidence(
        signature_verified=signature_verified, stamp_verified=stamp_verified, approved_by_name=approved_by_name,
        approved_by_title=approved_by_title, approved_date=approved_date, previous=previous,
    )
    signature_verified, stamp_verified = evidence['signature_verified'], evidence['stamp_verified']
    approved_by_name, approved_by_title, approved_date = (evidence[key] for key in ('approved_by_name', 'approved_by_title', 'approved_date'))
    workflow_issues = [*evidence['approval_evidence_issues'], *([] if stamp_verified else ['Company stamp requires visual verification.'])]
    source = {
        **previous, **_serializable_fields(fields), 'source_sha256': digest,
        'pr_id': str(po.pr_reference_id) if po.pr_reference_id else None,
        'pr_number': po.pr_reference.pr_number if po.pr_reference_id else '',
        'reconciliation_required': bool(issues), 'reconciliation_issues': issues,
        'signature_verified': signature_verified, 'stamp_verified': stamp_verified,
        'approved_by_name': approved_by_name, 'approved_by_title': approved_by_title,
        'approved_date': approved_date,
        **evidence, 'workflow_issues': workflow_issues,
    }
    document.document_type = 'purchase_order'
    document.extraction_status = 'completed'
    document.extraction_error = ''
    document.extracted_data = source
    document.confirmed_po = po
    document.save()
    attachment = {
        'type': 'signed_purchase_order_pdf', 'document_id': str(document.pk),
        'filename': filename, 'url': document.s3_url, 'sha256': digest,
        'source_po_number': fields['source_po_number'], 'canonical_po_number': po.po_number,
        'reconciliation_required': bool(issues), 'reconciliation_issues': issues,
        'signature_verified': signature_verified, 'stamp_verified': stamp_verified,
        'signature_visible': evidence['signature_visible'], 'approval_evidence_complete': signature_verified,
    }
    po.attachments = [row for row in (po.attachments or [])
                      if not (isinstance(row, dict) and row.get('type') == 'signed_purchase_order_pdf'
                              and row.get('sha256') == digest)] + [attachment]
    changed = ['attachments', 'updated_at']
    if signature_verified:
        for field, value in (
            ('approved_by_name', approved_by_name), ('approved_by_title', approved_by_title),
            ('approved_date', _date(approved_date)), ('approved_at', timezone.now()),
            ('approval_signature', f'{document.s3_url}#page=1'),
        ):
            if not getattr(po, field) and value:
                setattr(po, field, value)
                changed.append(field)
    if stamp_verified and not po.approval_stamp:
        po.approval_stamp = f'{document.s3_url}#page=1'
        changed.append('approval_stamp')
    # Retain RADAI decisions and append the separate external source evidence.
    evidence_log = list(po.approval_log or [])
    existing = next((row for row in evidence_log if isinstance(row, dict)
                     and str(row.get('evidence_document_id', '')) == str(document.pk)), None)
    updated = dict(existing or {})
    if existing is None or existing.get('status') != ('Approved' if signature_verified else 'Evidence review required'):
        updated.update({
            'stage': 'Signed PO document approval', 'approver': approved_by_name,
            'status': 'Approved' if signature_verified else 'Evidence review required',
            'date': approved_date if signature_verified else '',
            'evidence_document_id': str(document.pk),
        })
    if not signature_verified:
        updated['approver'] = approved_by_name
    updated.update(signature_verified=signature_verified, stamp_verified=stamp_verified,
                   signature_visible=evidence['signature_visible'], approval_evidence_complete=signature_verified)
    if updated != existing:
        if existing is not None:
            evidence_log[evidence_log.index(existing)] = updated
        else:
            evidence_log.append(updated)
        po.approval_log = evidence_log
        changed.append('approval_log')
    po.save(update_fields=changed)
    return {
        'success': True, 'operation': 'attached', 'document_id': str(document.pk),
        'purchase_order_id': str(po.pk), 'po_number': po.po_number,
        'pr_id': str(po.pr_reference_id) if po.pr_reference_id else None,
        'pr_number': source['pr_number'], 'vendor_id': str(po.vendor_id), 'vendor_name': po.vendor.name,
        'database_verified': True, 'source_document_url': document.s3_url,
        'reconciliation_required': bool(issues), 'reconciliation_issues': issues,
        'signature_verified': signature_verified, 'stamp_verified': stamp_verified,
        **evidence, 'extracted_data': source, 'workflow_issues': workflow_issues, 'mapping_issues': [],
    }


def _import_signed_po_pdf(
    pdf_bytes: bytes,
    *,
    filename: str,
    user,
    signature_verified: bool = False,
    stamp_verified: bool = False,
    approved_by_name: str = "",
    approved_by_title: str = "",
    approved_date: str = "",
    allow_existing_update: bool = True,
    originating_pr=None,
    extracted_fields=None,
    register_missing_vendor: bool = False,
    allow_vendor_create: bool = False,
    previous_evidence=None,
) -> dict[str, Any]:
    if not pdf_bytes.startswith(b"%PDF"):
        raise SignedPOImportError("The uploaded file is not a valid PDF.")
    fields = extracted_fields if extracted_fields is not None else extract_signed_po_fields(pdf_bytes, filename)
    source_number, po_number = fields["source_po_number"], fields["po_number"]
    verified, message = PurchaseOrderNumberService.verify(po_number)
    if not verified:
        raise SignedPOImportError(message)

    po = PurchaseOrder.objects.select_for_update().filter(
        Q(po_number=po_number) | Q(po_number=source_number)
    ).annotate(
        canonical_first=Case(When(po_number=po_number, then=Value(0)), default=Value(1), output_field=IntegerField()),
    ).order_by("canonical_first").first()
    retained_document = None
    if po:
        if not allow_existing_update:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('Purchase order update permission is required to attach a PDF to an existing order.')
        review_issues = _extraction_review_issues(fields)
        if review_issues:
            retained_document, _ = _store_source_pdf(pdf_bytes, fields, filename, user)
            previous = retained_document.extracted_data or {}
            if previous.get('extraction_reviewed'):
                fields['extraction_reviewed'] = True
                review_issues = _extraction_review_issues(fields)
        if not review_issues or (retained_document and retained_document.confirmed_po_id):
            if originating_pr:
                _verified_origin_link(originating_pr, po.pk, user)
                po.refresh_from_db()
            return _attach_existing_order(
                po, fields, pdf_bytes, filename, user,
                signature_verified=signature_verified, stamp_verified=stamp_verified,
                approved_by_name=approved_by_name, approved_by_title=approved_by_title,
                approved_date=approved_date, retained_document=retained_document,
            )
        # A linked document is read-only. Keep a partially extracted original
        # pending until its owner explicitly reviews and attaches it to this PO.
    evidence = _approval_evidence(
        signature_verified=signature_verified, stamp_verified=stamp_verified, approved_by_name=approved_by_name,
        approved_by_title=approved_by_title, approved_date=approved_date, previous=previous_evidence,
    )
    signature_verified, stamp_verified = evidence['signature_verified'], evidence['stamp_verified']
    approved_by_name, approved_by_title, approved_date = (evidence[key] for key in ('approved_by_name', 'approved_by_title', 'approved_date'))
    candidates = list(PurchaseRequisition.objects.filter(
        Q(po_number_reference__iexact=source_number) | Q(po_number_reference__iexact=po_number)
    )[:2])
    pr = originating_pr or (po.pr_reference if po and po.pr_reference_id else candidates[0] if len(candidates) == 1 else None)
    extraction_review_issues = _extraction_review_issues(fields)
    reconciliation_issues = list(extraction_review_issues)
    if pr:
        verified, message = PurchaseOrderNumberService.verify(po_number, pr.pr_number)
        if not verified and not (po and po.pr_reference_id):
            pr = None
            reconciliation_issues.append(message)
    if not pr:
        reconciliation_issues.append("PR link pending. Upload saved; link the correct purchase recommendation during reconciliation.")

    mapping_issues = []
    fields["ocr_vendor_name"] = fields["vendor_name"]
    fields["ocr_summary"] = fields["summary"]
    if pr and pr.product_service and not originating_pr and not fields.get('extraction_reviewed'):
        fields["summary"] = pr.product_service
        if len(fields["ocr_summary"]) > 500:
            mapping_issues.append(
                "The two-column scan caused OCR summary spillover; purchase summary was mapped from the uniquely linked authoritative PR."
            )

    vendors = list(Vendor.objects.all().only("id", "vendor_code", "name"))
    if po:
        vendor_match = {
            'matched': True, 'source': fields['ocr_vendor_name'], 'id': str(po.vendor_id),
            'vendor_code': po.vendor.vendor_code, 'vendor_name': po.vendor.name,
            'method': 'existing purchase order vendor master', 'confidence': 1.0,
        }
        fields['vendor_name'] = po.vendor.name
    elif register_missing_vendor:
        from .document_vendor import resolve_document_vendor
        vendor, registered = resolve_document_vendor(
            fields, user=user, allow_create=allow_vendor_create,
            create_missing=not extraction_review_issues and bool(str(fields.get('vendor_name') or '').strip()),
        )
        vendor_match = ({
            'matched': True, 'source': fields['ocr_vendor_name'], 'id': str(vendor.pk),
            'vendor_code': vendor.vendor_code, 'vendor_name': vendor.name,
            'method': 'registered from PO source' if registered else 'supplier name or trade license',
            'registered': registered,
        } if vendor else {'matched': False, 'source': fields['ocr_vendor_name']})
    elif pr and pr.vendor_id and not originating_pr and not fields.get('extraction_reviewed'):
        vendor_match = {
            "matched": True,
            "source": fields["ocr_vendor_name"],
            "id": str(pr.vendor_id),
            "vendor_code": pr.vendor.vendor_code,
            "vendor_name": pr.vendor.name,
            "method": "linked PR vendor master",
            "confidence": 1.0,
        }
        fields["vendor_name"] = pr.vendor.name
        if fields["ocr_vendor_name"] != pr.vendor.name:
            mapping_issues.append(
                "OCR captured a truncated seller name; vendor was mapped from the uniquely linked PR vendor master."
            )
    else:
        vendor_match = _match_vendor((pr.supplier_name if pr and not originating_pr and not fields.get('extraction_reviewed') else "") or fields["vendor_name"], vendors)
    if not vendor_match.get("matched"):
        reconciliation_issues.append("Supplier match pending. Select the correct supplier during reconciliation.")
    if not vendor_match.get("matched") or extraction_review_issues:
        # Keep the complete source for explicit review rather than creating an
        # order from incomplete amounts or a partially extracted long document.
        vendor_id = vendor_match.get("id") if vendor_match.get("matched") else None
        document, digest = (
            (retained_document, hashlib.sha256(pdf_bytes).hexdigest()) if retained_document is not None
            else _store_source_pdf(pdf_bytes, fields, filename, user)
        )
        document.document_type = "purchase_order"
        document.extraction_status = "completed"
        document.extraction_error = ""
        document.extracted_data = {
            **_serializable_fields(fields), "source_sha256": digest,
            "reconciliation_required": True, "reconciliation_issues": reconciliation_issues,
            "signature_verified": signature_verified, "stamp_verified": stamp_verified,
            "approved_by_name": approved_by_name, "approved_by_title": approved_by_title,
            "approved_date": approved_date, "vendor_match": vendor_match,
            "pr_id": str(pr.pk) if pr else None, "pr_number": pr.pr_number if pr else "",
            "vendor_id": vendor_id,
            **evidence, 'workflow_issues': evidence['approval_evidence_issues'],
        }
        document.save()
        return {
            "success": True, "operation": "uploaded", "document_id": str(document.pk),
            "purchase_order_id": str(document.confirmed_po_id) if document.confirmed_po_id else None,
            "po_number": po_number, "source_document_url": document.s3_url,
            "pr_id": str(pr.pk) if pr else None, "pr_number": pr.pr_number if pr else "",
            "vendor_id": vendor_id, "vendor_name": fields["vendor_name"],
            "database_verified": True, "reconciliation_required": True,
            "reconciliation_issues": reconciliation_issues, "extracted_data": document.extracted_data,
            "mapping_issues": mapping_issues, "workflow_issues": evidence['approval_evidence_issues'],
            "signature_verified": signature_verified, "stamp_verified": stamp_verified,
            **evidence,
        }
    created = po is None
    if created:
        po = PurchaseOrder(po_number=po_number, created_by=user)
    else:
        po.po_number = po_number

    project = Project.objects.filter(project_number=fields["project_number"]).first()
    po.pr_reference = pr
    po.vendor_id = vendor_match["id"]
    po.project = project
    po.project_number = fields["project_number"]
    po.rad_project_no = fields["project_number"]
    po.title = (fields["summary"] or f"Purchase Order {po_number}")[:300]
    po.description = fields["summary"]
    po.category = (pr.category if pr else "") or po.category or "other"
    po.status = "sent"
    po.total_amount = fields["total_amount"]
    po.tax_amount = fields["tax_amount"]
    po.vat_percentage = (
        (fields['tax_amount'] * Decimal('100') / fields['total_amount']).quantize(Decimal('0.01'))
        if fields['total_amount'] else Decimal('0.00')
    )
    po.currency = fields["currency"]
    if fields.get('canonical_financials'):
        financials = fields['canonical_financials']
        po.net_amount = Decimal(financials['net_amount'])
        po.total_amount = Decimal(financials['total_amount'])
        po.vat_basis = financials['vat_basis']
    po.payment_terms = fields["payment_terms"]
    po.payment_mode = fields["payment_mode"] or "Bank Transfer"
    po.delivery_terms = fields["delivery_terms"]
    po.marking = re.sub(r"_\d{4}$", "", po_number)
    po.expected_delivery = fields["expected_delivery"]
    po.items = fields["items"] or po.items or []
    po.seller_reference = fields["seller_reference"]
    po.quote_ref = fields["quote_ref"]
    po.seller_license_no = fields["vendor_license_no"]
    approval_recorded_at = timezone.now() if signature_verified else None
    po.approved_by_name = approved_by_name if signature_verified else ""
    po.approved_by_title = approved_by_title if signature_verified else ""
    po.approved_date = _date(approved_date) if signature_verified else None
    po.approved_at = approval_recorded_at
    po.save()
    if fields["po_date"]:
        PurchaseOrder.objects.filter(pk=po.pk).update(po_date=fields["po_date"])
        po.po_date = fields["po_date"]

    document, digest = _store_source_pdf(pdf_bytes, fields, filename, user)
    evidence_url = f"{document.s3_url}#page=1"
    po.approval_signature = evidence_url if signature_verified else ""
    po.approval_stamp = evidence_url if stamp_verified else ""
    po.approval_log = [{
        "stage": "Signed PO document approval",
        "approver": approved_by_name,
        "status": "Approved" if signature_verified else "Evidence review required",
        "date": approval_recorded_at.isoformat() if approval_recorded_at else "",
        "approved_at": approval_recorded_at.isoformat() if approval_recorded_at else "",
        "evidence_document_id": str(document.id),
        "signature_verified": signature_verified,
        "stamp_verified": stamp_verified,
        'signature_visible': evidence['signature_visible'], 'approval_evidence_complete': signature_verified,
    }]
    attachment = {
        "type": "signed_purchase_order_pdf",
        "document_id": str(document.id),
        "filename": filename,
        "url": document.s3_url,
        "sha256": digest,
        "source_po_number": source_number,
        "canonical_po_number": po_number,
        "reconciliation_required": bool(reconciliation_issues),
        "reconciliation_issues": reconciliation_issues,
        "signature_verified": signature_verified,
        "stamp_verified": stamp_verified,
        'signature_visible': evidence['signature_visible'], 'approval_evidence_complete': signature_verified,
        "procurement_register": {
            "PO Number": po_number,
            "PR Number": pr.pr_number if pr else "",
            "PR Accepted Date": pr.issued_date.isoformat() if pr and pr.issued_date else "",
            "Suppl.Name": vendor_match["vendor_name"],
            "Summary of Purchase": fields["summary"],
            "Project short name/ Code": fields["project_number"],
            "Ord.Date": fields["po_date"].isoformat() if fields["po_date"] else "",
            "OA date": "",
            "Delivery Date": fields["expected_delivery"].isoformat() if fields["expected_delivery"] else "",
            "Payment terms": fields["payment_terms"],
            "Amount Curr.": str(fields["total_amount"]),
            "Curr.": fields["currency"],
            "Amount including VAT": str(fields["gross_amount"]),
            "Amount Inc VAT in AED": str(fields["gross_amount"]) if fields["currency"] == "AED" else "",
            "Country": getattr(pr.vendor, "country", "") if pr and pr.vendor_id else "",
            "Remarks": "Signed PO PDF uploaded." + (" Reconciliation pending." if reconciliation_issues else ""),
        },
    }
    po.attachments = [
        item for item in (po.attachments or [])
        if not (isinstance(item, dict) and item.get("type") == "signed_purchase_order_pdf" and item.get("sha256") == digest)
    ] + [attachment]
    po.save(update_fields=["approval_signature", "approval_stamp", "approval_log", "attachments", "updated_at"])

    if pr and not originating_pr:
        from .procurement_lifecycle import mark_requisition_converted
        pr.po_applicable = True
        pr.save(update_fields=['po_applicable'])
        mark_requisition_converted(pr, po_number)

    workflow_issues = []
    pending_pr_approvals = [
        field for field in (
            "pm_approval_status", "eng_manager_approval_status",
            "manager_projects_approval_status", "vp_op_approval_status",
        ) if getattr(pr, field, "pending") != "approved"
    ]
    if pr and pending_pr_approvals:
        workflow_issues.append(
            "The historical PR is linked and converted, but RADAI does not contain its individual internal approval/signature evidence."
        )
    workflow_issues.extend(evidence['approval_evidence_issues'])
    if not stamp_verified:
        workflow_issues.append("Company stamp requires visual verification.")

    extracted_data = {
        **fields,
        "total_amount": str(fields["total_amount"]),
        "tax_amount": str(fields["tax_amount"]),
        "gross_amount": str(fields["gross_amount"]),
        "po_date": fields["po_date"].isoformat() if fields["po_date"] else None,
        "expected_delivery": fields["expected_delivery"].isoformat() if fields["expected_delivery"] else None,
        "source_sha256": digest,
        "pr_number": pr.pr_number if pr else "",
        "pr_id": str(pr.id) if pr else None,
        "reconciliation_required": bool(reconciliation_issues),
        "reconciliation_issues": reconciliation_issues,
        "vendor_id": vendor_match["id"],
        "vendor_match": vendor_match,
        "signature_verified": signature_verified,
        "stamp_verified": stamp_verified,
        "approved_by_name": approved_by_name,
        "approved_by_title": approved_by_title,
        "approved_date": approved_date,
        "workflow_issues": workflow_issues,
        "mapping_issues": mapping_issues,
        **evidence,
    }
    document.document_type = "purchase_order"
    document.extraction_status = "completed"
    document.extraction_error = ""
    document.extracted_data = extracted_data
    document.confirmed_po = po
    document.save()

    persisted = PurchaseOrder.objects.select_related("pr_reference", "vendor").get(pk=po.pk)
    return {
        "success": True,
        "operation": "created" if created else "overwritten",
        "document_id": str(document.id),
        "purchase_order_id": str(persisted.id),
        "po_number": persisted.po_number,
        "pr_id": str(persisted.pr_reference_id) if persisted.pr_reference_id else None,
        "pr_number": persisted.pr_reference.pr_number if persisted.pr_reference_id else "",
        "vendor_id": str(persisted.vendor_id),
        "vendor_name": persisted.vendor.name,
        "vendor_registered": vendor_match.get('registered', False),
        "database_verified": True,
        "source_document_url": document.s3_url,
        "reconciliation_required": bool(reconciliation_issues),
        "reconciliation_issues": reconciliation_issues,
        "signature_verified": signature_verified,
        "stamp_verified": stamp_verified,
        "extracted_data": extracted_data,
        "workflow_issues": workflow_issues,
        "mapping_issues": mapping_issues,
        **evidence,
    }


@transaction.atomic
def import_signed_po_pdf(pdf_bytes: bytes, *, filename: str, user, pr_id=None, reviewed_fields=None,
                         require_complete=False, **options) -> dict[str, Any]:
    """Keep an explicitly selected PR and its resulting PO in one transaction."""
    from .procurement_lifecycle import ProcurementDeleteConflict

    _approval_evidence(**{key: options[key] for key in (
        'signature_verified', 'stamp_verified', 'approved_by_name', 'approved_by_title', 'approved_date',
    ) if key in options})

    if not pdf_bytes.startswith(b'%PDF'):
        raise SignedPOImportError('The uploaded file is not a valid PDF.')
    digest = hashlib.sha256(pdf_bytes).hexdigest()
    retained = PODocument.objects.filter(extracted_data__source_sha256=digest).filter(
        Q(uploaded_by=user) | Q(confirmed_po__isnull=False),
    ).first()
    previous = (retained.extracted_data or {}) if retained else {}
    reviewed_pr = (reviewed_fields or {}).get('pr_id')
    if reviewed_pr and pr_id and str(reviewed_pr.pk) != str(pr_id):
        raise ProcurementDeleteConflict('Select the same purchase recommendation in the reviewed PO details.')
    origin_id = pr_id or (reviewed_pr.pk if reviewed_pr else None) or previous.get('originating_pr_id')
    # Keep the established PR -> PO lock order used by manual link actions.
    pr = _originating_requisition(origin_id) if origin_id else None
    fields = extract_signed_po_fields(pdf_bytes, filename)
    if reviewed_fields is not None:
        from .po_document_review import reviewed_document_fields
        fields = reviewed_document_fields(_serializable_fields(fields), reviewed_fields, user=user)
        fields['extraction_reviewed'] = True
        for key in ('po_date', 'expected_delivery'):
            if isinstance(fields.get(key), str):
                fields[key] = _date(fields[key])
        if fields.get('canonical_financials'):
            financials = fields['canonical_financials']
            fields.update(total_amount=Decimal(financials['net_amount']), tax_amount=Decimal(financials['tax_amount']),
                          gross_amount=Decimal(financials['total_amount']))
        for key in ('total_amount', 'tax_amount', 'gross_amount'):
            fields[key] = Decimal(str(fields.get(key) or '0'))
    if require_complete:
        from rest_framework.exceptions import ValidationError
        issues = _extraction_review_issues(fields)
        if issues:
            raise ValidationError({'po_reviewed_fields': issues})
        if not fields.get('po_date'):
            raise ValidationError({'po_date': 'Enter the purchase order date from the PDF.'})
        if not str(fields.get('summary') or '').strip():
            raise ValidationError({'summary': 'Enter the purchase description from the PDF.'})
        currency = str(fields.get('currency') or '').strip().upper()
        if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
            raise ValidationError({'currency': 'Enter a three-letter purchase order currency.'})
    if pr:
        candidates = list(PurchaseOrder.objects.select_for_update().filter(
            po_number__in={fields['po_number'], fields['source_po_number']},
        )[:2])
        if len(candidates) > 1:
            raise ProcurementDeleteConflict('More than one saved order matches this PDF number. Resolve the duplicate references before uploading.')
        existing = candidates[0] if candidates else None
        if previous:
            validate_originating_requisition(pr, previous, po=existing)
        validate_originating_requisition(pr, fields, po=existing)
        if retained and retained.confirmed_po_id and (existing is None or retained.confirmed_po_id != existing.pk):
            raise ProcurementDeleteConflict('This original PDF is already linked to another purchase order.')
        fields['originating_pr_id'] = str(pr.pk)
        if retained and not retained.confirmed_po_id and previous.get('originating_pr_id') and reviewed_fields is None:
            # Identical bytes already have a retained review. Keep corrections,
            # VAT confirmation and signature evidence; complete via reconcile.
            return {
                'success': True, 'operation': 'uploaded', 'document_id': str(retained.pk),
                'purchase_order_id': None, 'po_number': previous.get('po_number'),
                'pr_id': str(pr.pk), 'pr_number': pr.pr_number,
                'vendor_id': previous.get('vendor_id'), 'vendor_name': previous.get('vendor_name', ''),
                'database_verified': True, 'source_document_url': retained.s3_url,
                'reconciliation_required': True, 'reconciliation_issues': previous.get('reconciliation_issues', []),
                'extracted_data': previous, 'signature_verified': bool(previous.get('signature_verified')),
                'stamp_verified': bool(previous.get('stamp_verified')),
                **_approval_evidence(previous=previous),
                'mapping_issues': previous.get('mapping_issues', []), 'workflow_issues': previous.get('workflow_issues', []),
            }
    result = _import_signed_po_pdf(
        pdf_bytes, filename=filename, user=user, originating_pr=pr, extracted_fields=fields, previous_evidence=previous, **options,
    )
    if require_complete and not result.get('purchase_order_id'):
        raise SignedPOImportError('The PO was not saved. ' + ' '.join(result.get('reconciliation_issues') or ['Review the supplier and PO details.']))
    if pr and result.get('purchase_order_id'):
        result['po_link'] = _verified_origin_link(pr, result['purchase_order_id'], user)
        result.update(pr_id=str(pr.pk), pr_number=pr.pr_number)
    return result
