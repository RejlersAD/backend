"""Import a signed Purchase Order PDF and reconcile it with RADAI master data."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import hashlib
import re
from typing import Any

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Case, IntegerField, Q, Value, When
from django.utils import timezone

from ..models import PODocument, PurchaseOrder, PurchaseRequisition, Vendor
from ..models_master import Project
from .po_excel_import import canonical_po_number
from .po_tesseract_extractor import extract_text_from_pdf_tesseract
from .document_filenames import build_procurement_pdf_filename
from .pr_excel_import import _match_vendor
from .purchase_order_numbering import PurchaseOrderNumberService


class SignedPOImportError(ValueError):
    pass


def _match(pattern: str, text: str, default: str = "") -> str:
    result = re.search(pattern, text, re.IGNORECASE | re.MULTILINE | re.DOTALL)
    return re.sub(r"\s+", " ", result.group(1)).strip() if result else default


def _money(label: str, text: str) -> tuple[Decimal | None, str]:
    result = re.search(
        rf"{label}\s*:?\s*([\d,]+\.\d{{2}})\s*(USD|AED|EUR|GBP)",
        text,
        re.IGNORECASE,
    )
    if not result:
        return None, ""
    return Decimal(result.group(1).replace(",", "")), result.group(2).upper()


def _date(value: str):
    value = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


_SELLER_FIELD_END = (
    r"\bSeller\s*(?:Address|Reference|Contact|E[- ]?mail|Phone|Telephone|Fax|Signature)\b"
    # Two-column OCR may put the address value between 'Seller' and 'Address:'.
    r"|\bSeller\s*(?=\s+(?:Unit|Suite|Office|Building|P\.?\s*O\.?\s*Box)\b)"
    r"|\b(?:Address|Invoicing(?:\s+Address)?|Invoice\s+Address|Buyer\s+Reference)\s*:"
    r"|\bInvoicing\b"
    r"|\b(?:Quote\s*Ref(?:erence)?\.?|License\s*No\.?|Payment\s+(?:Terms?|Mode)"
    r"|Delivery\s+(?:Terms?|Date)|Project|Purchase\s+Summary|Total\s+Purchase\s+Price)\s*:"
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
            for page in document:
                value = _seller_name(page.get_text("text", sort=True))
                if value:
                    return value
    except Exception:
        pass
    return ""


_PO_SUMMARY_END = (
    r"\b(?:Total\s+(?:Purchase\s+)?Price|Total\s+Sum|Net\s+Total|VAT\s*(?:\([^)]*\))?)\s*:"
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
        r"^Total\s+Purchase\s+Price\s*:\s*"
        r"(?:(?:USD|AED|EUR|GBP)\s*[\d,]+\.\d{2}|[\d,]+\.\d{2}\s*(?:USD|AED|EUR|GBP))\s*",
        "", value, count=1, flags=re.IGNORECASE,
    )
    if not heading and re.match(r"(?:---\s*Page|PURCHASE\s+ORDER\b|Seller\s*:|RAD-(?:GEN|PRJ)-PUR-)", value, re.IGNORECASE):
        return ""
    return _bounded_po_summary(value)


def extract_signed_po_fields(pdf_bytes: bytes, filename: str) -> dict[str, Any]:
    text = extract_text_from_pdf_tesseract(pdf_bytes)
    source_number = _match(r"(RAD-(?:GEN|PRJ)-PUR-\d{4}_\s*[A-Z]{3}\d{4})", text)
    if not source_number:
        source_number = re.sub(r"\.pdf$", "", filename, flags=re.IGNORECASE)
    po_number = canonical_po_number(source_number)
    if not po_number:
        raise SignedPOImportError("The signed PDF does not contain a valid RAD PO number.")

    po_date_text = _match(r"\b(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})\b", text)
    delivery_text = _match(r"Delivery\s+date\s*:\s*(\d{1,2}\.\d{1,2}\.\d{4})", text)
    net, currency = _money(r"Total\s+Purchase\s+Price", text)
    if net is None:
        net, currency = _money(r"Total\s+Price", text)
    vat, vat_currency = _money(r"VAT\s*\(5%\)", text)
    gross, gross_currency = _money(r"Total\s+Sum", text)
    currency = currency or vat_currency or gross_currency or "USD"

    raw_vendor_name = _match(r"Seller\s*:\s*(.+?)(?:\s+Seller\s+Reference|\Z)", text)
    native_vendor_name = _native_seller_name(pdf_bytes)
    vendor_name = native_vendor_name or _seller_name(text)
    project_number = _match(r"Project\s*:\s*(\d{5,12})", text)
    summary = normalize_po_summary(text)

    return {
        "ocr_text_length": len(text),
        "source_po_number": source_number,
        "po_number": po_number,
        "po_date": _date(po_date_text),
        "vendor_name": vendor_name,
        "ocr_vendor_name_raw": raw_vendor_name,
        "vendor_name_source": "native" if native_vendor_name else "ocr",
        "vendor_license_no": _match(r"License\s+No\.\s*(CN-\d+)", text),
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


def _store_source_pdf(pdf_bytes, fields, filename, user):
    digest = hashlib.sha256(pdf_bytes).hexdigest()
    document = PODocument.objects.filter(extracted_data__source_sha256=digest).filter(
        Q(uploaded_by=user) | Q(confirmed_po__isnull=False),
    ).first()
    if document:
        return document, digest
    source_date = fields["po_date"] or timezone.localdate()
    safe_name = build_procurement_pdf_filename(fields["po_number"], "po", source_date)
    key = default_storage.save(
        f"procurement/signed_documents/{source_date.year}/{safe_name}", ContentFile(pdf_bytes),
    )
    return PODocument.objects.create(
        original_filename=filename, s3_key=key, s3_url=default_storage.url(key),
        file_size_bytes=len(pdf_bytes), uploaded_by=user,
    ), digest


def _serializable_fields(fields):
    return {key: value.isoformat() if hasattr(value, "isoformat") else str(value) if isinstance(value, Decimal) else value
            for key, value in fields.items()}


def _attach_existing_order(po, fields, pdf_bytes, filename, user, *, signature_verified,
                           stamp_verified, approved_by_name, approved_by_title, approved_date,
                           retained_document=None):
    """An upload is evidence, not permission to replace an existing order."""
    issues = []
    if Decimal(str(fields['total_amount'])) != po.total_amount:
        issues.append('The PDF amount differs from the saved order. Review the order amount before reconciling.')
    if fields['currency'] != po.currency:
        issues.append('The PDF currency differs from the saved order.')
    matched = _match_vendor(fields['vendor_name'], [po.vendor])
    if not matched.get('matched'):
        issues.append('The PDF supplier differs from the saved order or could not be read. Review the supplier.')
    if not po.pr_reference_id:
        issues.append('PR link pending. Link the correct purchase recommendation during reconciliation.')
    document, digest = (
        (retained_document, hashlib.sha256(pdf_bytes).hexdigest()) if retained_document is not None
        else _store_source_pdf(pdf_bytes, fields, filename, user)
    )
    if document.confirmed_po_id and document.confirmed_po_id != po.pk:
        raise SignedPOImportError('This original PDF is already linked to another purchase order.')
    previous = document.extracted_data or {}
    # Re-uploading the same original without checking boxes must not erase a
    # prior reviewer-confirmed signature, stamp, name or source date.
    signature_verified = signature_verified or bool(previous.get('signature_verified'))
    stamp_verified = stamp_verified or bool(previous.get('stamp_verified'))
    approved_by_name = previous.get('approved_by_name') or approved_by_name
    approved_by_title = previous.get('approved_by_title') or approved_by_title
    approved_date = previous.get('approved_date') or approved_date
    source = {
        **previous, **_serializable_fields(fields), 'source_sha256': digest,
        'pr_id': str(po.pr_reference_id) if po.pr_reference_id else None,
        'pr_number': po.pr_reference.pr_number if po.pr_reference_id else '',
        'reconciliation_required': bool(issues), 'reconciliation_issues': issues,
        'signature_verified': signature_verified, 'stamp_verified': stamp_verified,
        'approved_by_name': approved_by_name, 'approved_by_title': approved_by_title,
        'approved_date': approved_date,
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
    if existing is None or (signature_verified and not existing.get('signature_verified')):
        if existing is not None:
            evidence_log.remove(existing)
        evidence_log.append({
            'stage': 'Signed PO document approval', 'approver': approved_by_name,
            'status': 'Approved' if signature_verified else 'Evidence review required',
            'date': approved_date if signature_verified else '',
            'evidence_document_id': str(document.pk),
            'signature_verified': signature_verified, 'stamp_verified': stamp_verified,
        })
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
        'extracted_data': source, 'workflow_issues': [], 'mapping_issues': [],
    }


@transaction.atomic
def import_signed_po_pdf(
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
) -> dict[str, Any]:
    if not pdf_bytes.startswith(b"%PDF"):
        raise SignedPOImportError("The uploaded file is not a valid PDF.")
    fields = extract_signed_po_fields(pdf_bytes, filename)
    source_number, po_number = fields["source_po_number"], fields["po_number"]
    verified, message = PurchaseOrderNumberService.verify(po_number)
    if not verified:
        raise SignedPOImportError(message)

    po = PurchaseOrder.objects.select_for_update().filter(
        Q(po_number=po_number) | Q(po_number=source_number)
    ).annotate(
        canonical_first=Case(When(po_number=po_number, then=Value(0)), default=Value(1), output_field=IntegerField()),
    ).order_by("canonical_first").first()
    if po:
        if not allow_existing_update:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('Purchase order update permission is required to attach a PDF to an existing order.')
        return _attach_existing_order(
            po, fields, pdf_bytes, filename, user,
            signature_verified=signature_verified, stamp_verified=stamp_verified,
            approved_by_name=approved_by_name, approved_by_title=approved_by_title,
            approved_date=approved_date,
        )
    candidates = list(PurchaseRequisition.objects.filter(
        Q(po_number_reference__iexact=source_number) | Q(po_number_reference__iexact=po_number)
    )[:2])
    pr = po.pr_reference if po and po.pr_reference_id else candidates[0] if len(candidates) == 1 else None
    reconciliation_issues = []
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
    if pr and pr.product_service:
        fields["summary"] = pr.product_service
        if len(fields["ocr_summary"]) > 500:
            mapping_issues.append(
                "The two-column scan caused OCR summary spillover; purchase summary was mapped from the uniquely linked authoritative PR."
            )

    vendors = list(Vendor.objects.all().only("id", "vendor_code", "name"))
    if pr and pr.vendor_id:
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
        vendor_match = _match_vendor((pr.supplier_name if pr else "") or fields["vendor_name"], vendors)
    if not vendor_match.get("matched"):
        reconciliation_issues.append("Supplier match pending. Select the correct supplier during reconciliation.")
        document, digest = _store_source_pdf(pdf_bytes, fields, filename, user)
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
            "vendor_id": None,
        }
        document.save()
        return {
            "success": True, "operation": "uploaded", "document_id": str(document.pk),
            "purchase_order_id": str(document.confirmed_po_id) if document.confirmed_po_id else None,
            "po_number": po_number, "source_document_url": document.s3_url,
            "pr_id": str(pr.pk) if pr else None, "pr_number": pr.pr_number if pr else "",
            "vendor_id": None, "vendor_name": fields["vendor_name"],
            "database_verified": True, "reconciliation_required": True,
            "reconciliation_issues": reconciliation_issues, "extracted_data": document.extracted_data,
            "mapping_issues": mapping_issues, "workflow_issues": [],
            "signature_verified": signature_verified, "stamp_verified": stamp_verified,
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

    if pr:
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
    if not signature_verified:
        workflow_issues.append("PO approval signature requires visual verification.")
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
        "database_verified": True,
        "source_document_url": document.s3_url,
        "reconciliation_required": bool(reconciliation_issues),
        "reconciliation_issues": reconciliation_issues,
        "signature_verified": signature_verified,
        "stamp_verified": stamp_verified,
        "extracted_data": extracted_data,
        "workflow_issues": workflow_issues,
        "mapping_issues": mapping_issues,
    }
