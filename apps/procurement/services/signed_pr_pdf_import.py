"""Review signed Purchase Requisition PDFs and capture their source evidence."""

from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal
from difflib import SequenceMatcher
import hashlib
import re

from botocore.exceptions import BotoCoreError, ClientError
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.db.models.functions import Concat
from django.db.models import CharField, Value
from django.utils import timezone

from ..models import PurchaseRequisition, Vendor
from .po_tesseract_extractor import extract_text_from_pdf_tesseract
from .pr_pdf_text import extract_pr_pdf_text
from .pr_document_reconciliation import compare_existing_pr, reconcile_pr_po_link
from .pr_pdf_semantics import apply_pr_layout_semantics, approval_role
from .document_filenames import build_procurement_pdf_filename
from .pr_excel_import import _match_vendor
from .requisition_source_documents import SIGNED_PR_TYPE, requisition_source_key


class SignedPRImportError(ValueError):
    pass


class SignedPRStorageError(SignedPRImportError):
    pass


APPROVAL_ROLES = ("pm", "moe", "mop", "vp")


def _apply_manual_signature_overrides(detected: dict, overrides: dict | None) -> dict:
    """Combine detector results with signatures visually verified by the reviewer."""
    if overrides is not None and not isinstance(overrides, dict):
        raise SignedPRImportError("Manual signature verification must be a JSON object.")

    unknown_roles = set(overrides or {}) - set(APPROVAL_ROLES)
    if unknown_roles:
        raise SignedPRImportError("Manual signature verification contains an unknown approval role.")
    if any(value is not True for value in (overrides or {}).values()):
        raise SignedPRImportError("Each manually verified signature must be confirmed with true.")

    automatic = {
        role: bool((detected.get("signatures") or {}).get(role))
        for role in APPROVAL_ROLES
    }
    manual = {
        role: bool((overrides or {}).get(role)) and not automatic[role]
        for role in APPROVAL_ROLES
    }
    effective = {role: automatic[role] or manual[role] for role in APPROVAL_ROLES}
    return {
        **detected,
        "automated_signatures": automatic,
        "manual_signature_overrides": manual,
        "signatures": effective,
        "signature_sources": {
            role: "automatic" if automatic[role] else "manual" if manual[role] else "missing"
            for role in APPROVAL_ROLES
        },
        "all_four_signatures": all(effective.values()),
    }


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip(" |\n\r\t")


def _capture(pattern: str, text: str, default: str = "") -> str:
    match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE | re.DOTALL)
    return _clean(match.group(1)) if match else default


def _date(value: str):
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


def _ocr_lines(text: str) -> list[str]:
    """Return meaningful OCR lines while preserving their document order."""
    return [
        _clean(line)
        for line in (text or "").replace("\r", "\n").split("\n")
        if _clean(line) and not re.fullmatch(r"---\s*Page\s+\d+\s*---", _clean(line), re.IGNORECASE)
    ]


_AMOUNT_PATTERN = r"\d+(?:(?:[, ]\d{3})+)?(?:\s*\.\s*\d{2})?"
_MONEY_PATTERN = re.compile(
    rf"(?:(?P<currency_before>USD|AED|EUR|GBP)\s*[:|]?\s*(?P<amount_after>{_AMOUNT_PATTERN})(?![\d.,])|"
    rf"(?P<amount_before>{_AMOUNT_PATTERN})\s*(?P<currency_after>USD|AED|EUR|GBP)(?!\s*[:|]?\s*\d))\b",
    re.IGNORECASE,
)
_NET_LABEL = r"Net\s+Total\s*[,:(|]*\s*(?:excl(?:uding)?\.?|excluding)\s*\.?\s*VAT\s*[):|]*\s*"
_SECTION_END = r"(?:PO\s+Reference\b|\d+\s*[.)]\s*(?:Special\s+Notes|Pu(?:r)?chase\s+Recommendation)|APPROVALS\b)"


def _money_match(value: str):
    return _MONEY_PATTERN.search(value or "")


def _money_value(match) -> tuple[str, Decimal]:
    currency = (match.group("currency_before") or match.group("currency_after")).upper()
    amount = match.group("amount_after") or match.group("amount_before")
    return currency, Decimal(re.sub(r"[,\s]", "", amount)).quantize(Decimal("0.01"))


def _reference(text: str, kind: str) -> str:
    """Normalize separators only; never repair unreadable digits or infer a number."""
    year = r"(?:[A-Z]{3}\s*)?\d{4}" if kind == "PUR" else r"\d{4}"
    match = re.search(
        rf"\bRAD\s*-\s*(GEN|PRJ)\s*-\s*{kind}\s*-\s*(\d{{4}})[\s_-]+({year})\b",
        text or "", re.IGNORECASE,
    )
    if not match:
        return ""
    suffix = re.sub(r"\s+", "", match.group(3)).upper()
    return f"RAD-{match.group(1).upper()}-{kind}-{match.group(2)}_{suffix}"


def _normalized_document_text(text: str) -> str:
    text = (text or "").replace("\r", "\n").replace("\u00a0", " ")
    for dash in ("\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2212"):
        text = text.replace(dash, "-")
    return re.sub(r"\bU\.?S\.?D\.?\b", "USD", text, flags=re.IGNORECASE)


def _section(text: str, start: str, end: str) -> str:
    match = re.search(start + r"(?P<body>[\s\S]*?)(?=" + end + r")", text, re.IGNORECASE)
    return _clean(match.group("body")) if match else ""


def extract_signed_pr_fields(pdf_bytes: bytes, filename: str, *, allow_missing_pr_number: bool = False, _source=None, _include_source=False) -> dict:
    if not pdf_bytes.startswith(b"%PDF"):
        raise SignedPRImportError("The uploaded file is not a valid PDF.")
    source = _source if _source is not None else extract_pr_pdf_text(pdf_bytes, fallback=extract_text_from_pdf_tesseract)
    fields = extract_signed_pr_fields_from_text(source["text"], filename, allow_missing_pr_number=allow_missing_pr_number)
    fields = apply_pr_layout_semantics(fields, source, extract_signed_pr_fields_from_text, filename)
    method = source.get("method", "unknown")
    fields["extraction_method"] = method
    fields["extraction_pages"] = source.get("pages", [])
    fields["extraction_issues"].extend(source.get("warnings", []))
    for field, provenance in fields["field_provenance"].items():
        provenance["text_method"] = method
        # OCR recognition is evidence requiring visual review, not a verified
        # business value. Keep conflict/derived/missing distinctions intact.
        if method != "native" and fields["field_confidence"].get(field) == "high":
            fields["field_confidence"][field] = "medium"
    if _include_source:
        fields["_layout_source"] = source
    return fields


def extract_signed_pr_fields_from_text(text: str, filename: str, *, allow_missing_pr_number: bool = False) -> dict:
    """Parse document labels and price rows without assuming missing business values."""
    text = _normalized_document_text(text)
    lines = _ocr_lines(text)
    pr_number = _reference(text, "PR")
    pr_number_source = "document_reference" if pr_number else "filename"
    if not pr_number:
        pr_number = _reference(re.sub(r"\.pdf$", "", filename, flags=re.IGNORECASE), "PR")
    if not re.fullmatch(r"RAD-(?:GEN|PRJ)-PR-\d{4}_\d{4}", pr_number) and not allow_missing_pr_number:
        raise SignedPRImportError("The PDF does not contain a valid RAD PR number.")
    if not re.fullmatch(r"RAD-(?:GEN|PRJ)-PR-\d{4}_\d{4}", pr_number):
        pr_number = ""

    issued_by_name = _capture(r"Issued\s+by\s*:\s*(.+?)(?=\s*[|]?\s*PR\s+(?:No|Number)\b|\n|$)", text)
    product = _capture(r"Product\s*/\s*Service\s*:\s*(.+?)(?=\s*[|]?\s*Supplier\s*:|\n|$)", text)
    supplier = _capture(r"(?<!Preferred )Supplier\s*:\s*(.+?)(?=\s*[|]?\s*(?:Project\s*/\s*Department|ICV)\s*:|\n|$)", text)
    supplier_business_match = re.search(
        r"(?:Supplier\s+)?Business\s+ID(?:\s+No\.)?\s*:\s*(CN\s*-?\s*\d{5,10})|\b(CN\s*-\s*\d{5,10})\b",
        text,
        re.IGNORECASE,
    )
    supplier_business_id = ""
    if supplier_business_match:
        supplier_business_id = re.sub(
            r"\s+", "", supplier_business_match.group(1) or supplier_business_match.group(2)
        ).upper().replace("CN", "CN-").replace("--", "-")
    if not supplier_business_id:
        interleaved_business_match = re.search(r"\bCN\s*-\s*[\s\S]{0,100}?(\d{7,10})\b", text, re.IGNORECASE)
        if interleaved_business_match:
            supplier_business_id = f"CN-{interleaved_business_match.group(1)}"

    project_department = _section(
        text,
        r"Project\s*/\s*Department\s*:\s*",
        r"\s*[|]?\s*\d+\s*[.)]\s*Description\s+and\s+Reason\s+for\s+Purchase",
    )
    project_department = re.sub(
        r"(?:Supplier\s+)?Business\s+ID(?:\s+No\.)?\s*:\s*", " ", project_department, flags=re.IGNORECASE
    )
    project_department = re.sub(r"\bCN\s*-?\s*\d{5,10}\b", " ", project_department, flags=re.IGNORECASE)
    project_department = re.sub(r"\bCN\s*-", " ", project_department, flags=re.IGNORECASE)
    if supplier_business_id:
        project_department = re.sub(
            rf"\b{re.escape(supplier_business_id.removeprefix('CN-'))}\b", " ", project_department
        )
    project_department = re.sub(r"\bICV\s*:\s*[\d.]+", " ", project_department, flags=re.IGNORECASE)
    project_department = _clean(project_department)
    icv = _capture(r"\bICV:\s*([\d.]+)", text)
    description = _capture(r"1\.\s*Description and Reason for Purchase:\s*(.+?)\s+2\.\s*Preferred Supplier", text)
    preferred = _capture(
        r"2\.\s*Preferred Supplier \(if any\):\s*(.+?)(?:\n\s*[—_-]+|\n\s*For M/s|\n\s*Total Price|\n\s*Supply of)",
        text,
    )
    description = _section(
        text,
        r"\d+\s*[.)]\s*Description\s+and\s+Reason\s+for\s+Purchase\s*:?\s*",
        r"\s*[|]?\s*\d+\s*[.)]\s*Preferred\s+Supplier",
    ) or description
    if issued_by_name:
        description = re.sub(
            rf"\bIssued\s+by\s*:\s*{re.escape(issued_by_name)}\b", " ", description, flags=re.IGNORECASE
        )
        description = _clean(description)
    for line_index, line in enumerate(lines):
        preferred_match = re.search(r"\d+\s*[.)]\s*Preferred\s+Supplier\s*(?:\(if any\))?\s*:?\s*(.*)$", line, re.IGNORECASE)
        if not preferred_match:
            continue
        preferred = _clean(preferred_match.group(1))
        if preferred and preferred.count("(") > preferred.count(")") and line_index + 1 < len(lines):
            preferred = _clean(f"{preferred} {lines[line_index + 1]}")
        break
    preferred_primary = _clean(re.split(r"\(\s*To\s+M/s", preferred, flags=re.IGNORECASE)[0])
    if preferred_primary:
        supplier = preferred_primary

    po_reference = _reference(text, "PUR")
    notes = _capture(r"4\.\s*Special Notes:\s*\(If any\)\s*(.+?)(?:Attachment\s+No\.|APPROVALS)", text).rstrip(" =➜")
    attachment_reference = _capture(r"(Attachment\s+No\.\s*\d+\s*:\s*.+?)(?:APPROVALS|\n\s*APPROVALS)", text)
    issued_date = _date(_capture(r"\b(?:Issued\s+)?Date\s*:\s*(\d{1,2}[./-]\d{1,2}[./-]\d{4}|\d{4}-\d{2}-\d{2})", text.split("APPROVALS")[0]))
    semantic_notes_match = re.search(
        r"\d+\s*[.)]\s*(?:(?:Special\s+Notes)|(?:Pu(?:r)?chase\s+Recommendation))"
        r"\s*:?\s*(?:\(\s*If\s+any\s*\)\s*:?\s*)?(?P<body>[\s\S]*?)(?=Attachment\s+No\.|APPROVALS|\Z)",
        text,
        re.IGNORECASE,
    )
    if semantic_notes_match:
        note_lines = _ocr_lines(semantic_notes_match.group("body"))
        while note_lines and len(re.sub(r"[^A-Za-z]", "", note_lines[0])) < 5:
            note_lines.pop(0)
        while note_lines and re.fullmatch(
            r"(?:(?:USD|AED|EUR|GBP)\s*)?[\d,]+\.\d{2}(?:\s*(?:USD|AED|EUR|GBP))?",
            note_lines[-1],
            re.IGNORECASE,
        ):
            note_lines.pop()
        semantic_notes = _clean(" ".join(note_lines)).rstrip(" =âžœ")
        if semantic_notes:
            notes = semantic_notes
    for line in lines:
        attachment_match = re.search(r"(Attachment\s+No\.\s*\d+\s*:\s*.+)$", line, re.IGNORECASE)
        if attachment_match:
            attachment_reference = _clean(attachment_match.group(1))
            break

    # Keep price columns separate from Remarks. A sales budget in the same row
    # is evidence, not another item or the purchase total.
    price_lines, price_evidence, money_issues = [], [], []
    price_start = re.search(r"(?:^|\n)\s*[|]?\s*3\s*[.)]\s*Price\b[^\n]*", text, re.IGNORECASE)
    if not price_start:
        price_start = re.search(r"\d+\s*[.)]\s*Preferred\s+Supplier[^\n]*", text, re.IGNORECASE)
    price_scope = text[price_start.end():] if price_start else ""
    price_end = re.search(_NET_LABEL + "|" + _SECTION_END, price_scope, re.IGNORECASE)
    price_section = price_scope[:price_end.start()] if price_end else price_scope
    pending_description = []
    for line in _ocr_lines(price_section):
        if re.fullmatch(r"[\s|:.-]*(?:(?:3\s*[.)]\s*)?Price\s*)?(?:Total\s+Price\s*)?(?:Remarks\s*)?[\s|:.-]*", line, re.IGNORECASE):
            continue
        row_match = _money_match(line)
        if re.match(r"(?:Sales\s+)?Budget\b|Unit\s+Price\b|Remarks\b", line, re.IGNORECASE):
            if price_lines:
                price_lines[-1]["remarks"] = _clean(f"{price_lines[-1].get('remarks', '')} {line}")
            continue
        if not row_match:
            if line.startswith("(") and price_lines:
                price_lines[-1]["description"] = _clean(f"{price_lines[-1]['description']} {line}")
            else:
                pending_description.append(line)
            continue
        row_currency, row_amount = _money_value(row_match)
        row_prefix = _clean(line[:row_match.start()]).strip(" |]:-")
        row_description = _clean(" ".join([*pending_description, row_prefix]))
        pending_description = []
        if not row_description:
            row_description = description or product
        # Currency-only cells without a description cannot establish item identity.
        if not row_description:
            continue
        price_item = {"description": row_description, "total": str(row_amount), "currency": row_currency}
        remarks = _clean(line[row_match.end():]).strip(" |]}")
        if remarks:
            price_item["remarks"] = remarks
        price_lines.append(price_item)
        price_evidence.append(line)

    net_label = re.search(_NET_LABEL, text, re.IGNORECASE)
    net_scope = text[net_label.end():] if net_label else ""
    net_end = re.search(_SECTION_END, net_scope, re.IGNORECASE)
    net_scope = net_scope[:net_end.start()] if net_end else net_scope[:500]
    net_match = _money_match(net_scope)
    currency, net_total = _money_value(net_match) if net_match else ("", None)
    money_source = "labeled_net_total" if net_match else "missing"
    money_evidence = _clean(net_scope[:net_match.end()]) if net_match else ""
    row_currencies = {row["currency"] for row in price_lines}
    if net_total is None and len(row_currencies) == 1:
        net_total = sum((Decimal(row["total"]) for row in price_lines), Decimal("0.00"))
        currency = next(iter(row_currencies))
        money_source = "sum_of_price_rows"
        money_evidence = " | ".join(price_evidence)
    if len(row_currencies) > 1:
        money_issues.append("Price rows use different currencies. Review each amount; no mixed-currency total was calculated.")
    if net_match and price_lines and row_currencies == {currency}:
        row_total = sum((Decimal(row["total"]) for row in price_lines), Decimal("0.00"))
        if row_total != net_total:
            money_issues.append(f"Labeled net total {currency} {net_total} differs from price-row total {currency} {row_total}.")
    if net_match and row_currencies and row_currencies != {currency}:
        money_issues.append("The labeled net total currency differs from the price-row currency. Review the PDF.")
    labeled_totals = set()
    for label_match in re.finditer(_NET_LABEL, text, re.IGNORECASE):
        following_text = text[label_match.end():]
        stop = re.search(_SECTION_END + "|" + _NET_LABEL, following_text, re.IGNORECASE)
        value_match = _money_match(following_text[:stop.start()] if stop else following_text[:500])
        if value_match:
            labeled_totals.add(_money_value(value_match))
    if len(labeled_totals) > 1:
        money_issues.append("Different labeled net totals were found in the document. Confirm the correct amount and currency.")

    # Some scans emit the amount column after later section text. When no row
    # survived the labeled Price section, accept only a repeated currency/amount
    # pair before APPROVALS; repetition is strong evidence for row total + net total.
    if net_total is None and not price_lines:
        preferred_offset = re.search(r"Preferred\s+Supplier", text, re.IGNORECASE)
        approval_offset = re.search(r"APPROVALS", text, re.IGNORECASE)
        amount_scope = text[
            preferred_offset.start() if preferred_offset else 0:
            approval_offset.start() if approval_offset else len(text)
        ]
        candidates = []
        for line in _ocr_lines(amount_scope):
            if re.search(r"\b(?:Budget|original|deduction)\b", line, re.IGNORECASE):
                continue
            money = _money_match(line)
            if money:
                found_currency, found_amount = _money_value(money)
                candidates.append((found_currency, str(found_amount)))
        repeated_candidates = set(item for item in candidates if candidates.count(item) >= 2)
        repeated = next(iter(repeated_candidates)) if len(repeated_candidates) == 1 else None
        if repeated:
            currency, repeated_amount = repeated
            net_total = Decimal(repeated_amount.replace(",", ""))
            money_source = "repeated_detached_amount"
            money_evidence = f"{currency} {repeated_amount} appears more than once outside budget lines."
            price_lines = [{
                "description": description or product,
                "total": str(net_total),
                "currency": currency,
            }]
    if not price_lines and net_total is not None:
        price_lines = [{"description": description or product, "total": str(net_total), "currency": currency}]
    product_tokens = re.sub(r"[^a-z0-9 ]", "", product.lower()).split()
    description_tokens = re.sub(r"[^a-z0-9 ]", "", description.lower()).split()
    for tokens in (product_tokens, description_tokens):
        while tokens and tokens[0] in {"supply", "of", "the", "a", "an"}:
            tokens.pop(0)
    product_prefix = " ".join(product_tokens[:3])
    normalized_description = re.sub(r"[^a-z0-9 ]", "", description.lower())
    semantic_prefix_match = bool(
        len(product_tokens) >= 2 and len(description_tokens) >= 2
        and all(
            left.startswith(right) or right.startswith(left)
            for left, right in zip(product_tokens[:2], description_tokens[:2])
        )
    )
    if description and (
        not product or semantic_prefix_match or (product_prefix and normalized_description.startswith(product_prefix))
    ):
        product = description
    for price_item in price_lines:
        item_description = price_item.get("description", "")
        if item_description.count("(") <= item_description.count(")"):
            continue
        open_fragment = _clean(item_description.rsplit("(", 1)[-1])
        completion = re.search(
            rf"\({re.escape(open_fragment)}\s+(?P<remainder>[^)]+)\)",
            f"{description} {product}",
            re.IGNORECASE,
        )
        if completion:
            price_item["description"] = _clean(
                f"{item_description} {completion.group('remainder')})"
            )
    supplier_looks_interleaved = bool(
        preferred and (
            len(supplier) > len(preferred) * 1.15
            or any(term.lower() in supplier.lower() for term in ("SmartPlant Electrical", "Licenses (CH"))
        )
    )
    if supplier_looks_interleaved:
        supplier = preferred

    # An AED equivalent requires document evidence. Never manufacture an
    # exchange rate or mistake the Sales Budget column for the net total.
    net_total_aed = ""
    net_aed_source = "missing"
    explicit_aed = re.search(
        rf"Net\s+Total\s+(?:in\s+AED|\(AED\))\s*[:|]?\s*({_AMOUNT_PATTERN})",
        text, re.IGNORECASE,
    )
    if explicit_aed:
        net_total_aed = str(Decimal(re.sub(r"[,\s]", "", explicit_aed.group(1))).quantize(Decimal("0.01")))
        net_aed_source = "labeled_aed_total"
    elif currency == "AED" and net_total is not None:
        net_total_aed = str(net_total.quantize(Decimal("0.01")))
        net_aed_source = "same_currency_total"
    budget_match = re.search(
        rf"(?:Sales\s+)?Budget\s*(?:in\s+)?(?:[:>\-→|]*\s*)AED\s*({_AMOUNT_PATTERN})",
        text, re.IGNORECASE,
    )
    budget_in_aed = str(Decimal(re.sub(r"[,\s]", "", budget_match.group(1))).quantize(Decimal("0.01"))) if budget_match else ""

    project_reference_numbers = list(dict.fromkeys(re.findall(r"\b\d{5,12}\b", project_department)))
    project_numbers = [number for number in project_reference_numbers if re.fullmatch(r"590\d{4}", number)]
    project_number_source = "project_department"
    if not project_numbers:
        project_numbers = list(dict.fromkeys(re.findall(r"\b590\d{4}\b", f"{product} {description}")))
        project_number_source = "service_project_reference"
    if not project_numbers:
        project_numbers = project_reference_numbers
        project_number_source = "unclassified_project_reference"
    project_number = project_numbers[0] if len(project_numbers) == 1 else ""
    price_remarks = _clean(" | ".join(dict.fromkeys(row.get("remarks", "") for row in price_lines if row.get("remarks"))))
    extracted_values = {
        "pr_number": pr_number,
        "issued_by": issued_by_name,
        "issued_date": issued_date,
        "product_service": product,
        "supplier": supplier,
        "supplier_business_id": supplier_business_id,
        "project_department": project_department,
        "project_number": project_number,
        "description": description,
        "preferred_supplier": preferred,
        "price": net_total,
        "currency": currency,
        "po_reference": po_reference,
        "special_notes": notes,
        "price_remarks": price_remarks,
        "budget_in_aed": budget_in_aed,
        "net_total_aed": net_total_aed,
        "attachment_reference": attachment_reference,
    }
    required_fields = ("issued_by", "issued_date", "product_service", "supplier", "description", "price", "currency")
    extraction_issues = [
        f"OCR could not confidently extract the labeled field: {field.replace('_', ' ')}."
        for field in required_fields
        if extracted_values[field] in (None, "")
    ]
    field_confidence = {
        field: ("high" if value not in (None, "") else "missing")
        for field, value in extracted_values.items()
    }

    field_provenance = {
        field: {"source": "labeled_document_text" if value not in (None, "") else "missing", "evidence": str(value)[:1000] if value is not None else ""}
        for field, value in extracted_values.items()
    }
    field_provenance["pr_number"]["source"] = pr_number_source if pr_number else "missing"
    if pr_number and pr_number_source == "filename":
        field_confidence["pr_number"] = "medium"
        extraction_issues.append("PR number was read from the filename; confirm it matches the PDF.")
    for key in ("price", "currency"):
        field_provenance[key] = {"source": money_source, "evidence": money_evidence[:1200]}
        if money_source == "sum_of_price_rows":
            field_confidence[key] = "derived" if key == "price" else "medium"
        elif money_source == "repeated_detached_amount":
            field_confidence[key] = "medium"
    if money_source in ("sum_of_price_rows", "repeated_detached_amount"):
        extraction_issues.append("Net total needs review: it was reconstructed from price evidence rather than read from the labeled net-total cell.")
    if money_issues:
        field_confidence["price"] = "conflict"
        if len(row_currencies) > 1 or (currency and row_currencies and row_currencies != {currency}) or len({item[0] for item in labeled_totals}) > 1:
            field_confidence["currency"] = "conflict"
        extraction_issues.extend(money_issues)
    field_provenance["project_number"] = {"source": project_number_source, "evidence": project_department if project_number_source != "service_project_reference" else _clean(f"{product} {description}")}
    if len(project_numbers) > 1:
        field_confidence["project_number"] = "conflict"
        extraction_issues.append("Multiple project numbers were found; select the correct project instead of assuming the first number.")
    elif project_number and project_number_source != "project_department":
        field_confidence["project_number"] = "medium"
    field_provenance["net_total_aed"] = {"source": net_aed_source, "evidence": explicit_aed.group(0) if explicit_aed else money_evidence if net_total_aed else ""}
    if net_aed_source == "same_currency_total":
        field_confidence["net_total_aed"] = "derived"
    field_provenance["budget_in_aed"] = {"source": "labeled_aed_budget" if budget_match else "missing", "evidence": budget_match.group(0) if budget_match else ""}
    field_provenance["price_remarks"] = {"source": "price_row_remarks" if price_remarks else "missing", "evidence": price_remarks}

    return {
        "ocr_text_length": len(text),
        "ocr_schema_version": "signed-pr-labels-v3",
        "field_confidence": field_confidence,
        "field_provenance": field_provenance,
        "extraction_issues": extraction_issues,
        "pr_number": pr_number,
        "issued_by_name": issued_by_name,
        "issued_date": issued_date,
        "product_service": product,
        "supplier_name": supplier,
        "supplier_business_id": supplier_business_id,
        "project_department": project_department,
        "project_number": project_number,
        "project_numbers": project_numbers,
        "project_reference_numbers": project_reference_numbers,
        "icv": icv,
        "description_reason": description or product,
        "preferred_supplier": preferred or supplier,
        "price_lines": price_lines,
        "price_remarks": price_remarks,
        "net_total": net_total,
        "currency": currency,
        "budget_in_aed": budget_in_aed,
        "net_total_aed": net_total_aed,
        "po_reference": po_reference,
        "special_notes": notes,
        "attachment_reference": attachment_reference,
    }


def _serialize_extracted_fields(fields: dict) -> dict:
    return {
        **fields,
        "issued_date": fields["issued_date"].isoformat() if fields.get("issued_date") else None,
        "net_total": str(fields["net_total"]) if fields.get("net_total") is not None else None,
    }


def _price_lines_as_items(price_lines: list[dict]) -> list[dict]:
    """Represent a source amount as one lump sum only when no rate exists."""
    items = []
    for source_line in price_lines:
        line = dict(source_line)
        if line.get("total") not in (None, "") and all(
            line.get(field) in (None, "") for field in ("quantity", "qty", "unit_price", "price")
        ):
            # Keep the extracted price rows unchanged as source evidence. This
            # explicit representation lets normal form validation retain their
            # amount without inventing a quantity/rate for partially read rows.
            line.update(quantity="1", unit="LS", unit_price=line["total"])
        items.append(line)
    return items


def _apply_manual_overrides(fields: dict, overrides: dict | None) -> dict:
    """Apply reviewed OCR corrections using a strict, field-level allow-list."""
    if not overrides:
        return fields

    corrected = dict(fields)
    text_fields = {
        "pr_number": 40,
        "issued_by_name": 200,
        "product_service": 2000,
        "supplier_name": 300,
        "supplier_business_id": 100,
        "project_department": 1000,
        "project_number": 100,
        "description_reason": 5000,
        "preferred_supplier": 300,
        "price_remarks": 5000,
        "po_reference": 100,
        "special_notes": 5000,
        "attachment_reference": 500,
        "budget_in_aed": 30,
        "net_total_aed": 30,
    }
    for field, max_length in text_fields.items():
        if field in overrides:
            corrected[field] = _clean(str(overrides.get(field) or ""))[:max_length]

    if "issued_date" in overrides:
        corrected["issued_date"] = _date(str(overrides.get("issued_date") or ""))
        if overrides.get("issued_date") and not corrected["issued_date"]:
            raise SignedPRImportError("Issued date must use YYYY-MM-DD format.")

    if "currency" in overrides:
        currency = _clean(str(overrides.get("currency") or "")).upper()
        if currency not in {"AED", "USD", "EUR", "GBP"}:
            raise SignedPRImportError("Currency must be AED, USD, EUR, or GBP.")
        corrected["currency"] = currency

    if "net_total" in overrides:
        try:
            corrected["net_total"] = Decimal(str(overrides.get("net_total") or "").replace(",", ""))
        except Exception as exc:
            raise SignedPRImportError("Total price must be a valid number.") from exc
        if not corrected["net_total"].is_finite():
            raise SignedPRImportError("Total price must be a finite number.")
        if corrected["net_total"] < 0:
            raise SignedPRImportError("Total price cannot be negative.")

    from .procurement_vat import CONFIRMED_BASES, confirmed_totals
    if overrides.get('vat_basis') in CONFIRMED_BASES:
        entered = overrides.get('entered_amount')
        if entered is None:
            raise SignedPRImportError('Enter the price to confirm its VAT treatment.')
        try:
            totals = confirmed_totals(entered, overrides['vat_basis'])
        except ValueError as error:
            raise SignedPRImportError(str(error)) from error
        corrected['net_total'] = Decimal(str(entered))
        corrected['canonical_financials'] = {**{key: str(value) for key, value in totals.items()},
                                            'vat_basis': overrides['vat_basis'], 'entered_amount': str(entered)}
    elif 'entered_amount' in overrides:
        raise SignedPRImportError('Confirm whether VAT applies before changing the price.')

    for money_field, label in (("budget_in_aed", "Budget in AED"), ("net_total_aed", "Net total in AED")):
        if money_field not in overrides or corrected.get(money_field) in (None, ""):
            continue
        try:
            amount = Decimal(str(corrected[money_field]).replace(",", ""))
        except Exception as exc:
            raise SignedPRImportError(f"{label} must be a valid number.") from exc
        if not amount.is_finite():
            raise SignedPRImportError(f"{label} must be a finite number.")
        if amount < 0:
            raise SignedPRImportError(f"{label} cannot be negative.")
        corrected[money_field] = str(amount)

    corrected["pr_number"] = re.sub(
        r"(RAD-(?:GEN|PRJ)-PR-\d{4})[\s_-]+(\d{4})", r"\1_\2",
        corrected.get("pr_number", ""), flags=re.IGNORECASE,
    ).upper()
    if not re.fullmatch(r"RAD-(?:GEN|PRJ)-PR-\d{4}_\d{4}", corrected["pr_number"]):
        raise SignedPRImportError("Enter a valid PR number using RAD-{GEN|PRJ}-PR-####_YYYY.")

    required = {
        "issued_by_name": "Issued by",
        "issued_date": "Issued date",
        "product_service": "Product / Service",
        "supplier_name": "Supplier",
        "description_reason": "Description and reason",
        "net_total": "Total price",
        "currency": "Currency",
    }
    missing = [label for field, label in required.items() if corrected.get(field) in (None, "")]
    if missing:
        raise SignedPRImportError(f"Complete the required reviewed fields: {', '.join(missing)}.")

    source_lines = fields.get("price_lines") or []
    reviewed_lines = overrides.get("price_lines", source_lines)
    if not isinstance(reviewed_lines, list) or len(reviewed_lines) > 500:
        raise SignedPRImportError("Price lines must be a list of at most 500 rows.")
    if not reviewed_lines:
        reviewed_lines = [{
            "description": corrected["description_reason"] or corrected["product_service"],
            "total": str(corrected["net_total"]), "currency": corrected["currency"],
            "remarks": corrected.get("price_remarks", ""),
        }]
    # A correction to a single-row total also corrects that row. Multi-row
    # documents must retain and reconcile their individual prices.
    elif len(reviewed_lines) == 1 and reviewed_lines == source_lines and (
        corrected["net_total"] != fields.get("net_total") or corrected["currency"] != fields.get("currency")
    ):
        reviewed_lines = [{**reviewed_lines[0], "total": str(corrected["net_total"]), "currency": corrected["currency"]}]
    normalized_lines = []
    for index, line in enumerate(reviewed_lines, start=1):
        if not isinstance(line, dict):
            raise SignedPRImportError(f"Price line {index} must contain a description, amount and currency.")
        try:
            amount = Decimal(str(line.get("total", "")).replace(",", ""))
        except Exception as exc:
            raise SignedPRImportError(f"Price line {index} amount must be a valid number.") from exc
        if not amount.is_finite() or amount < 0:
            raise SignedPRImportError(f"Price line {index} amount must be a finite, non-negative number.")
        if amount >= Decimal("10000000000000"):
            raise SignedPRImportError(f"Price line {index} amount must be less than 10 trillion.")
        if amount != amount.quantize(Decimal("0.01")):
            raise SignedPRImportError(f"Price line {index} amount must be a non-negative number with up to two decimal places.")
        line_currency = _clean(str(line.get("currency") or corrected["currency"])).upper()
        if line_currency != corrected["currency"]:
            raise SignedPRImportError(f"Price line {index} currency does not match the reviewed total. Correct the row against the PDF.")
        normalized_lines.append({
            **line,
            "description": _clean(str(line.get("description") or ""))[:5000],
            "total": str(amount), "currency": line_currency,
            "remarks": _clean(str(line.get("remarks") or ""))[:5000],
        })
    if sum(Decimal(line["total"]) for line in normalized_lines) != corrected["net_total"]:
        raise SignedPRImportError("The reviewed price lines do not add up to Total Price. Check each row and the total against the PDF.")
    if len(normalized_lines) == 1 and "price_remarks" in overrides:
        # Preserve an explicitly edited row remark when the summary was unchanged.
        if corrected.get("price_remarks") != fields.get("price_remarks", ""):
            normalized_lines[0]["remarks"] = corrected["price_remarks"]
        else:
            corrected["price_remarks"] = normalized_lines[0]["remarks"]
    corrected["price_lines"] = normalized_lines
    corrected.setdefault("source_price_lines", source_lines)
    confidence = dict(corrected.get("field_confidence") or {})
    override_confidence_keys = {
        "issued_by_name": "issued_by", "issued_date": "issued_date",
        "product_service": "product_service", "supplier_name": "supplier",
        "description_reason": "description", "net_total": "price", "currency": "currency",
    }
    for override_field, confidence_field in override_confidence_keys.items():
        if override_field in overrides:
            confidence[confidence_field] = "manual"
    provenance = dict(corrected.get("field_provenance") or {})
    for field in overrides.keys() & (text_fields.keys() | {"currency", "net_total", "issued_date", "price_lines"}):
        confidence[field] = "manual"
        previous = provenance.get(field) or provenance.get(override_confidence_keys.get(field)) or {}
        provenance[field] = {**previous, "reviewed": True, "changed": corrected.get(field) != fields.get(field)}
    corrected["field_provenance"] = provenance
    corrected["field_confidence"] = confidence
    corrected["extraction_issues"] = [
        issue for issue in corrected.get("extraction_issues", [])
        if not any(label.lower() in issue.lower() for label in required.values())
    ]
    corrected["manual_review_applied"] = True
    return corrected


def preview_signed_pr_pdf(pdf_bytes: bytes, *, filename: str, expected_pr_number: str = "") -> dict:
    """Extract an editable preview without modifying a requisition or storing the PDF."""
    fields = extract_signed_pr_fields(pdf_bytes, filename, allow_missing_pr_number=True, _include_source=True)
    source = fields.pop("_layout_source", None)
    detected = detect_approval_evidence(pdf_bytes, _source=source) if source is not None else detect_approval_evidence(pdf_bytes)
    mapping_issues = list(fields.get("extraction_issues") or [])
    if not fields.get("pr_number"):
        mapping_issues.insert(0, "OCR could not confidently read the PR number. Enter it manually.")
    elif expected_pr_number and fields["pr_number"].casefold() != expected_pr_number.strip().casefold():
        mapping_issues.insert(0, f"Detected PR {fields['pr_number']} does not match {expected_pr_number}.")

    existing_pr = None
    comparison = None
    database_match = False
    if expected_pr_number or fields.get("pr_number"):
        existing_pr = PurchaseRequisition.objects.filter(pr_number__iexact=expected_pr_number or fields["pr_number"]).first()
        database_match = existing_pr is not None
        if existing_pr:
            comparison = compare_existing_pr(existing_pr, fields)
        if not database_match and expected_pr_number:
            mapping_issues.append(
                f"PR {fields['pr_number']} is not in RADAI. "
                "This record-bound import cannot create a different recommendation."
            )

    return {
        "success": True,
        "preview_only": True,
        "requires_manual_review": bool(mapping_issues),
        "database_match": database_match,
        "can_create": not database_match and not bool(expected_pr_number),
        "pr_number": fields.get("pr_number", ""),
        "extracted_data": _serialize_extracted_fields(fields),
        "approval_detection": detected,
        "document_signed_off": document_is_signed_off(detected),
        "document_comparison": comparison,
        "requisition_id": str(existing_pr.pk) if existing_pr else None,
        "mapping_issues": mapping_issues,
        "workflow_issues": [],
    }


def _find_user(full_name: str):
    normalized = _clean(full_name).lower().replace("-", " ")
    if not normalized:
        return None
    User = get_user_model()
    for user in User.objects.annotate(
        full_name=Concat("first_name", Value(" "), "last_name", output_field=CharField())
    ).only("id", "first_name", "last_name"):
        candidate = _clean(f"{user.first_name} {user.last_name}").lower().replace("-", " ")
        if candidate == normalized:
            return user
    return None


def _find_unique_active_issuer(full_name: str):
    """New imports must identify the document issuer, without guessing a user."""
    normalized = _clean(full_name).casefold().replace("-", " ")
    matches = [
        user for user in get_user_model().objects.filter(is_active=True).only("id", "first_name", "last_name")
        if _clean(f"{user.first_name} {user.last_name}").casefold().replace("-", " ") == normalized
    ] if normalized else []
    return matches[0] if len(matches) == 1 else None


def _match_user(full_name: str):
    """Match OCR names to RADAI users while tolerating small scan errors."""
    exact = _find_user(full_name)
    if exact:
        return exact
    normalized = re.sub(r"[^a-z0-9 ]", "", _clean(full_name).lower().replace("-", " "))
    best_user, best_score = None, 0.0
    User = get_user_model()
    for user in User.objects.only("id", "first_name", "last_name"):
        candidate = re.sub(
            r"[^a-z0-9 ]", "", _clean(f"{user.first_name} {user.last_name}").lower().replace("-", " ")
        )
        score = SequenceMatcher(None, normalized, candidate).ratio()
        ocr_tokens = normalized.split()
        candidate_tokens = candidate.split()
        if (
            ocr_tokens and candidate_tokens
            and ocr_tokens[0] == candidate_tokens[0]
            and SequenceMatcher(None, ocr_tokens[-1], candidate_tokens[-1]).ratio() >= 0.72
        ):
            score = max(score, 0.88)
        if score > best_score:
            best_user, best_score = user, score
    return best_user if best_score >= 0.72 else None


def detect_approval_evidence(pdf_bytes: bytes, *, _source=None) -> dict:
    """Preserve explicitly labeled approval rows and reviewable ink evidence."""
    from .pr_pdf_approval import evaluate_pr_approvals

    source = _source if _source is not None else extract_pr_pdf_text(
        pdf_bytes, fallback=extract_text_from_pdf_tesseract,
    )
    return evaluate_pr_approvals(pdf_bytes, source)


def document_is_signed_off(detected: dict) -> bool:
    """Honor the signed document's own approval rows, without inventing stages."""
    rows = detected.get("approval_rows") or []
    if rows:
        return all(
            row.get("role_key") and row.get("name")
            and (row.get("signature_detected") or (detected.get("manual_signature_overrides") or {}).get(row["role_key"]))
            for row in rows
        )
    # Compatibility for older recorded evidence with canonical role summaries.
    return bool(detected.get("table_detected") and detected.get("all_four_signatures"))


@transaction.atomic
def import_signed_pr_pdf(
    pdf_bytes: bytes,
    *,
    filename: str,
    uploaded_by,
    approvals: dict[str, str] | None = None,
    signatures_verified: bool | None = None,
    approval_date: str = "",
    expected_pr_number: str = "",
    manual_overrides: dict | None = None,
    manual_signature_overrides: dict | None = None,
    create_new: bool = False,
    attach_only: bool = False,
) -> dict:
    if not isinstance(create_new, bool):
        raise SignedPRImportError("Create recommendation must be true or false.")
    if not isinstance(attach_only, bool):
        raise SignedPRImportError("Attach signed document must be true or false.")
    if attach_only and (create_new or not expected_pr_number):
        raise SignedPRImportError("Attach the signed PDF from an existing recommendation.")
    if manual_overrides is not None and not isinstance(manual_overrides, dict):
        raise SignedPRImportError("Manual corrections must be a JSON object.")
    if create_new and expected_pr_number:
        raise SignedPRImportError("An import opened from an existing recommendation cannot create a new record.")
    if create_new and not manual_overrides:
        raise SignedPRImportError("Review the extracted fields before creating a recommendation from PDF.")
    supplied_approval_date = _date(str(approval_date)) if approval_date else None
    if approval_date and supplied_approval_date is None:
        raise SignedPRImportError("Enter a valid approval date.")
    digest = hashlib.sha256(pdf_bytes).hexdigest()
    fields = extract_signed_pr_fields(
        pdf_bytes, filename,
        allow_missing_pr_number=bool((manual_overrides or {}).get("pr_number")),
        _include_source=True,
    )
    source = fields.pop("_layout_source", None)
    source_fields = dict(fields)
    if not attach_only:
        fields = _apply_manual_overrides(fields, manual_overrides)
    if expected_pr_number and fields["pr_number"].casefold() != expected_pr_number.strip().casefold():
        raise SignedPRImportError(
            f"Uploaded PDF is {fields['pr_number']}, but the edited record is {expected_pr_number}. Nothing was changed."
        )
    pr = PurchaseRequisition.objects.select_for_update().filter(pr_number__iexact=fields["pr_number"]).first()
    previous_metadata = (pr.price_remarks_data or {}) if pr is not None else {}
    previous_verification = previous_metadata.get("signed_document_verification") or {}
    same_document = previous_verification.get("document_sha256") == digest
    effective_signature_overrides = manual_signature_overrides
    if same_document and signatures_verified is not False and (
        manual_signature_overrides is None
        or isinstance(manual_signature_overrides, dict) and manual_signature_overrides
    ):
        recorded_overrides = (previous_metadata.get("signed_approval_evidence") or {}).get("manual_signature_overrides") or {}
        # Reviewing a remaining signature supplements the review of these exact
        # source bytes. A different PDF must never inherit those confirmations;
        # an explicit empty object still requests a fresh review.
        effective_signature_overrides = {
            **{role: True for role, verified in recorded_overrides.items() if verified is True},
            **(manual_signature_overrides or {}),
        } or None
    detected = _apply_manual_signature_overrides(
        detect_approval_evidence(pdf_bytes, _source=source) if source is not None else detect_approval_evidence(pdf_bytes),
        effective_signature_overrides,
    )
    recorded_names = {
        approval_role(row.get("role_key") or row.get("role", "")): row.get("user_name", "")
        for row in (previous_verification.get("source_approval_rows") or [])
        if same_document and isinstance(row, dict) and row.get("user_name")
    }
    approval_names = {**detected["approver_names"], **recorded_names, **(approvals or {})}
    if not detected.get("approval_rows") and all(
        detected["manual_signature_overrides"].get(role)
        and isinstance(approval_names.get(role), str) and approval_names[role].strip()
        for role in APPROVAL_ROLES
    ):
        # A reviewer can explicitly identify and confirm each of the four
        # source roles even when OCR misses the table. Keep this separate from
        # automated table/signature detection, and never infer it from a name,
        # filename, or a global "verified" flag alone.
        detected["approval_rows"] = [
            {"role_key": role, "source_role": role.upper(), "name": approval_names[role],
             "signature_detected": False, "signature_candidate": False,
             "evidence_source": "manual_review"}
            for role in APPROVAL_ROLES
        ]
        detected["manual_table_reviewed"] = True
    signatures_verified = document_is_signed_off(detected) if signatures_verified is None else (
        signatures_verified and document_is_signed_off(detected)
    )
    if pr is not None and create_new:
        raise SignedPRImportError(
            f"PR {fields['pr_number']} already exists. Nothing was changed. "
            "Check PR number again, then save to update that record."
        )
    if pr is None and not create_new:
        raise SignedPRImportError(
            f"PR {fields['pr_number']} is not in RADAI. Review the extracted fields and choose "
            "Create reviewed PR, or import its register record first. Nothing was changed."
        )

    comparison = compare_existing_pr(pr, source_fields) if pr is not None else None
    if attach_only and not comparison["identity_matched"]:
        raise SignedPRImportError("The PR number printed on this PDF does not match the selected recommendation. Nothing was attached.")
    if previous_verification.get("signed_off") and not signatures_verified:
        raise SignedPRImportError(
            "This PDF is not fully signed; the existing signed approval was kept. "
            "Verify the signatures before replacing its source document."
        )

    issuer = _find_unique_active_issuer(fields["issued_by_name"]) if create_new else _find_user(fields["issued_by_name"])
    if create_new:
        if issuer is None:
            raise SignedPRImportError(
                f"Issued-by name '{fields['issued_by_name']}' must match exactly one active RADAI user. "
                "Correct the reviewed issuer before creating this recommendation."
            )
        if fields["net_total"] <= 0 or fields["net_total"] >= Decimal("10000000000000"):
            raise SignedPRImportError("Total price must be greater than zero and less than 10 trillion.")
        for money_field in ("budget_in_aed", "net_total_aed"):
            value = fields.get(money_field)
            if value and (not Decimal(value).is_finite() or Decimal(value) >= Decimal("10000000000000")):
                raise SignedPRImportError("Reviewed AED amounts must be finite and less than 10 trillion.")
        try:
            # Reserve the unique document identity before storing any file. A
            # duplicate concurrent import must never update someone else's PR.
            with transaction.atomic():
                pr = PurchaseRequisition.objects.create(
                    pr_number=fields["pr_number"], issued_by=issuer, requested_by=issuer,
                    status="draft", requisition_type="general" if "-GEN-" in fields["pr_number"] else "project",
                )
        except IntegrityError as exc:
            raise SignedPRImportError(
                f"PR {fields['pr_number']} could not be created because its number was already registered. Nothing was changed."
            ) from exc

    mapping_issues = list(fields.get("extraction_issues") or [])
    if attach_only and comparison["has_mismatches"]:
        mapping_issues.append("The signed PDF differs from this recommendation. Review the differences before using it for a purchase order.")
    if issuer and not attach_only:
        pr.issued_by = issuer
        pr.requested_by = issuer
    elif not issuer and not attach_only:
        mapping_issues.append(f"Issued-by name '{fields['issued_by_name']}' was not matched to a RADAI user.")

    vendors = list(Vendor.objects.all().only("id", "vendor_code", "name"))
    vendor_match = _match_vendor(fields["supplier_name"], vendors)
    if vendor_match.get("matched") and not attach_only:
        pr.vendor_id = vendor_match["id"]
    elif not vendor_match.get("matched") and not attach_only:
        mapping_issues.append("The signed PR supplier was not matched unambiguously to the vendor master.")

    if not attach_only:
        pr.issued_date = fields["issued_date"] or pr.issued_date
        pr.product_service = fields["product_service"] or pr.product_service
        pr.title = pr.product_service[:300]
        pr.supplier_name = fields["supplier_name"] or pr.supplier_name
        pr.supplier_business_id = fields["supplier_business_id"] or pr.supplier_business_id
        pr.project_department = fields["project_department"] or pr.project_department
        pr.project = fields["project_number"] or pr.project
        pr.description_reason = fields["description_reason"] or pr.description_reason
        pr.preferred_supplier_if_any = fields["preferred_supplier"] or pr.preferred_supplier_if_any
        pr.price_description = pr.product_service
        financial_review_confirmed = create_new or bool(fields.get('canonical_financials'))
        if fields["price_lines"] and financial_review_confirmed:
            pr.items = _price_lines_as_items(fields["price_lines"])
            first_price_remarks = fields.get("price_remarks") or fields["price_lines"][0].get("remarks", "")
            if first_price_remarks:
                pr.price_remarks = first_price_remarks
        if fields["currency"] and financial_review_confirmed:
            pr.currency = fields["currency"]
        if fields["net_total"] is not None and financial_review_confirmed:
            pr.total_price = fields["net_total"]
            pr.net_total_excl_vat = fields["net_total"]
        if fields.get('canonical_financials'):
            financials = fields['canonical_financials']
            pr.vat_basis = financials['vat_basis']
            pr.total_price = Decimal(financials['total_amount'])
            pr.net_total_excl_vat = Decimal(financials['net_amount'])
        if fields["budget_in_aed"] and financial_review_confirmed:
            pr.estimated_budget = Decimal(fields["budget_in_aed"])
        pr.po_applicable = bool(fields["po_reference"])
        pr.po_number_reference = fields["po_reference"] or pr.po_number_reference
        pr.purchase_recommendation = fields["special_notes"] or pr.purchase_recommendation
        pr.priority = "urgent" if "urgent basis" in fields["special_notes"].lower() else pr.priority

    metadata = dict(pr.price_remarks_data or {})
    original_import_source = metadata.get("import_source")
    source_snapshot = _serialize_extracted_fields(source_fields)
    approved_snapshot = _serialize_extracted_fields(source_fields if attach_only else fields)
    if not attach_only and fields.get('canonical_financials'):
        approved_snapshot['net_total'] = fields['canonical_financials']['net_amount']
    if same_document and attach_only:
        # A signature-only review of the same bytes must retain the original
        # extraction and the commercial corrections already reviewed for them.
        source_snapshot = previous_verification.get("source_fields") or source_snapshot
        approved_snapshot = previous_verification.get("approved_fields") or approved_snapshot
    metadata.update({
        "import_source": "signed_pr_pdf",
        "import_operation": "create" if create_new else "update",
        "source_authority": "Signed Purchase Requisition",
        "signed_document_verification": {
            "signed_off": bool(signatures_verified),
            "document_sha256": digest,
            "attach_only": attach_only,
            "comparison": comparison,
            "source_fields": source_snapshot,
            "approved_fields": approved_snapshot,
            "reviewed_by_id": str(uploaded_by.pk),
            "reviewed_at": timezone.now().isoformat(),
        },
        "icv": fields["icv"],
        "budget_in_aed": fields["budget_in_aed"],
        "net_total_aed": fields["net_total_aed"],
        "price_lines": fields["price_lines"],
        "project_numbers": fields.get("project_numbers", []),
        "attachment_reference": fields["attachment_reference"],
        "ocr_schema_version": fields.get("ocr_schema_version"),
        "ocr_field_confidence": fields.get("field_confidence", {}),
        "ocr_field_provenance": fields.get("field_provenance", {}),
        "ocr_text_method": fields.get("extraction_method"),
        "ocr_text_pages": fields.get("extraction_pages", []),
        "ocr_source_price_lines": fields.get("source_price_lines", fields.get("price_lines", [])),
        "project_reference_numbers": fields.get("project_reference_numbers", []),
        "ocr_semantic_sections": fields.get("semantic_sections", []),
        "ocr_uncertain_annotations": {
            "special_notes": fields.get("special_notes_annotation", ""),
        },
        "ocr_extraction_issues": fields.get("extraction_issues", []),
        "mapping_issues": mapping_issues,
        "signed_approval_evidence": {
            "table_detected": detected.get("table_detected", False),
            "rows": detected.get("approval_rows", []),
            "signatures": detected.get("signatures", {}),
            "automated_signatures": detected.get("automated_signatures", {}),
            "manual_signature_overrides": detected.get("manual_signature_overrides", {}),
            "signature_sources": detected.get("signature_sources", {}),
            "signature_candidates": detected.get("signature_candidates", {}),
            "signature_density": detected.get("signature_density", {}),
            "approval_evidence_issues": detected.get("approval_evidence_issues", []),
            "manual_table_reviewed": detected.get("manual_table_reviewed", False),
            "reviewed_approver_names": {
                role: approval_names[role] for role in APPROVAL_ROLES
                if (approvals or {}).get(role) or recorded_names.get(role)
            },
            "approver_names": detected.get("approver_names", {}),
            "date_present": detected.get("date_present", False),
            "date_ocr": detected.get("date_ocr", []),
            "approval_date_evidence": detected.get("approval_date_evidence", {}),
        },
    })
    if attach_only and original_import_source:
        metadata["import_source"] = original_import_source
    if attach_only:
        metadata["signed_pdf_attached"] = True
        metadata["import_operation"] = "attach_signed_pdf"
        # Retain spreadsheet commercial metadata as well as model columns.
        for key in ("icv", "budget_in_aed", "net_total_aed", "price_lines", "project_numbers", "attachment_reference"):
            if key in (pr.price_remarks_data or {}):
                metadata[key] = pr.price_remarks_data[key]
            else:
                metadata.pop(key, None)
    if manual_overrides or any(detected.get("manual_signature_overrides", {}).values()):
        corrected_fields = set((manual_overrides or {}).keys())
        if same_document:
            corrected_fields.update((previous_metadata.get("manual_ocr_review") or {}).get("corrected_fields") or [])
        metadata["manual_ocr_review"] = {
            "applied": True,
            "reviewed_at": timezone.now().isoformat(),
            "reviewed_by_id": str(uploaded_by.id),
            "reviewed_by_name": uploaded_by.get_full_name() or uploaded_by.email,
            "corrected_fields": sorted(corrected_fields),
            "verified_signatures": [
                role for role, verified in detected.get("manual_signature_overrides", {}).items() if verified
            ],
        }
    pr.price_remarks_data = metadata

    existing_attachment = next((
        item for item in (pr.attachments or [])
        if isinstance(item, dict) and item.get("sha256") == digest
        and SIGNED_PR_TYPE in (item.get("type"), item.get("document_type"))
    ), None)
    existing_key = requisition_source_key(pr, existing_attachment)
    try:
        if existing_key and default_storage.exists(existing_key):
            # Reuse a healthy original and its upload timestamp, renewing only
            # the temporary URL returned to the reviewer.
            storage_url = default_storage.url(existing_key)
            existing_attachment["signature_verified"] = signatures_verified
        else:
            # Reattaching the same reviewed bytes must repair an unavailable
            # original, rather than treating a matching hash as a healthy file.
            # Never inspect, overwrite or delete an unvalidated historical key.
            effective_date = fields["issued_date"] or timezone.localdate()
            safe_name = build_procurement_pdf_filename(pr.pr_number, "pr", effective_date)
            key = default_storage.save(
                f"procurement/signed_requisitions/{pr.pk}/{effective_date.year}/{safe_name}",
                ContentFile(pdf_bytes),
            )
            storage_url = default_storage.url(key)
            attachment = {
                "type": SIGNED_PR_TYPE,
                "document_type": SIGNED_PR_TYPE,
                "filename": filename,
                "storage_key": key,
                "url": storage_url,
                "s3_url": storage_url,
                "sha256": digest,
                "uploaded_at": timezone.now().isoformat(),
                "signature_verified": signatures_verified,
            }
            if existing_attachment is not None:
                existing_attachment.update(attachment)
                existing_attachment.pop("s3_key", None)
            else:
                pr.attachments = list(pr.attachments or []) + [attachment]
    except (OSError, ValueError, NotImplementedError, BotoCoreError, ClientError) as exc:
        raise SignedPRStorageError(
            "The signed PR PDF could not be stored or verified. Please retry."
        ) from exc

    workflow_issues = []
    previous_date = _date(previous_verification.get("approval_date", "")) if same_document else None
    approved_on = supplied_approval_date or previous_date or detected.get("approval_date")
    approved_at = timezone.make_aware(datetime.combine(approved_on, time(12, 0))) if approved_on else None
    source_rows = detected.get("approval_rows") or [
        {"role_key": role, "source_role": role.upper(), "name": approval_names.get(role, ""),
         "signature_detected": detected["signatures"].get(role, False)}
        for role in APPROVAL_ROLES if approval_names.get(role)
    ]
    approval_fields = {
        "pm": ("pm_name", "pm_signature", "pm_approval_status", "pm_approved_at"),
        "moe": ("eng_manager_name", "eng_manager_signature", "eng_manager_approval_status", "eng_manager_approved_at"),
        "mop": ("manager_projects_name", "manager_projects_signature", "manager_projects_approval_status", "manager_projects_approved_at"),
        "vp": ("vp_op_name", "vp_op_signature", "vp_op_approval_status", "vp_op_approved_at"),
    }
    external_history = []
    last_signer = None
    for row in source_rows:
        role = row.get("role_key")
        if role not in approval_fields:
            continue
        user = _find_unique_active_issuer(approval_names.get(role, row.get("name", "")))
        present = bool(row.get("signature_detected") or detected["signatures"].get(role))
        user_field, signature_field, status_field, date_field = approval_fields[role]
        if signatures_verified:
            setattr(pr, user_field, user)
            setattr(pr, signature_field, f"{storage_url}#page={row.get('page', 1)}" if present else "")
            setattr(pr, status_field, "approved" if present else "pending")
            setattr(pr, date_field, approved_at if present else None)
            last_signer = user
        external_history.append({
            "step": len(external_history) + 1, "role": row.get("source_role") or role.upper(),
            "role_key": role,
            "user_id": str(user.pk) if user else None,
            "user_name": approval_names.get(role) or row.get("name", ""),
            "status": "approved" if present else "not_recorded",
            "approved_at": approved_at.isoformat() if present and approved_at else None,
            "signature_verified": present,
            "signature_source": detected.get("signature_sources", {}).get(role, "missing"),
            "signature_candidate": bool(row.get("signature_candidate") or detected.get("signature_candidates", {}).get(role)),
            "source": "signed_purchase_requisition_pdf", "external": True,
        })
    if signatures_verified:
        if pr.status != "converted":
            pr.status = "approved"
        pr.approved_by = last_signer
        pr.approved_at = approved_at
        pr.review_due_at = None
        # These are completed source-document decisions, not a new RADAI route.
        pr.approval_workflow_config = external_history
        pr.current_approval_step = len(external_history)
        if not approved_on:
            workflow_issues.append("The handwritten approval date could not be read. Enter it after checking the signed PDF.")
    else:
        workflow_issues.append("The document could not be confirmed as fully signed. Verify the signatures in the PDF.")
    metadata["signed_document_verification"].update({
        "approval_date": approved_on.isoformat() if approved_on else None,
        "approval_date_source": "manual" if supplied_approval_date else (
            previous_verification.get("approval_date_source", "source_pdf") if previous_date
            else "source_pdf" if detected.get("approval_date") else "unreadable"
        ),
        "source_approval_rows": external_history,
    })
    pr.price_remarks_data = metadata
    pr.save()
    po_link = reconcile_pr_po_link(pr, extracted_fields=source_fields)
    metadata = dict(pr.price_remarks_data or {})
    if po_link["status"] in {"linked", "already_linked"} and pr.status in {"approved", "converted"}:
        pr.status = "converted"
    metadata["po_link"] = po_link
    pr.price_remarks_data = metadata
    pr.save(update_fields=["price_remarks_data", "status", "updated_at"])

    persisted = PurchaseRequisition.objects.get(pk=pr.pk)
    return {
        "success": True,
        "created": create_new,
        "pr_id": str(persisted.id),
        "requisition_id": str(persisted.pk),
        "pr_number": persisted.pr_number,
        "status": persisted.status,
        "database_verified": True,
        "source_document_url": storage_url,
        "signature_verified": signatures_verified,
        "document_signed_off": bool(signatures_verified),
        "document_comparison": comparison,
        "attach_only": attach_only,
        "financial_values_preserved": not create_new and (attach_only or not fields.get('canonical_financials')),
        "saved_financials": {
            'vat_basis': persisted.vat_basis,
            'net_total_excl_vat': str(persisted.net_total_excl_vat) if persisted.net_total_excl_vat is not None else None,
            'total_price': str(persisted.total_price) if persisted.total_price is not None else None,
            'currency': persisted.currency,
        },
        "po_link": po_link,
        "approval_detection": {
            **detected,
            "approval_date": approved_on.isoformat() if approved_on else None,
        },
        "mapping_issues": mapping_issues,
        "workflow_issues": workflow_issues,
        "manual_review_applied": bool(manual_overrides) or any(detected.get("manual_signature_overrides", {}).values()),
        "extracted_data": _serialize_extracted_fields(fields),
    }
