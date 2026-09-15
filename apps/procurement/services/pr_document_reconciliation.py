"""Compare signed PR evidence and reconcile explicit PR / PO references.

Attaching a document must not replace spreadsheet business values. Matching a
purchase order uses document references only, never amount or supplier guesses.
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
import unicodedata

from django.db import transaction
from django.utils import timezone

from ..models import PurchaseOrder, PurchaseRequisition


def _text(value):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or ""))).strip()


def normalize_document_number(value, *, kind=None):
    text = _text(value)
    text = re.sub(r"[‐‑‒–—−]", "-", text)
    match = re.fullmatch(
        r"RAD\s*-\s*(GEN|PRJ)\s*-\s*(PR|PUR)\s*-\s*(\d{4,})[\s_-]+"
        r"((?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)?\s*\d{4})",
        text, re.IGNORECASE,
    )
    if not match or (kind and match.group(2).upper() != kind):
        return ""
    scope, document_kind, sequence, suffix = match.groups()
    suffix = re.sub(r"\s+", "", suffix).upper()
    if document_kind.upper() == "PR" and not suffix.isdigit():
        return ""
    return f"RAD-{scope.upper()}-{document_kind.upper()}-{sequence}_{suffix}"


def _money(value):
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        amount = Decimal(re.sub(r"[,\s]", "", str(value)))
        return amount.quantize(Decimal("0.01")) if amount.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _date(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    for pattern in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(_text(value), pattern).date().isoformat()
        except (ValueError, TypeError):
            continue
    return ""


def _project(value):
    text = _text(value)
    codes = set(re.findall(r"\b590\d{4}\b", text))
    return tuple(sorted(codes)) if codes else text.casefold()


def _display(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return "" if value is None else str(value)


def _issuer_name(pr):
    user = getattr(pr, "issued_by", None)
    if user is None:
        return ""
    get_name = getattr(user, "get_full_name", None)
    return get_name() if callable(get_name) else _text(f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}")


def _source_identity_verified(fields):
    confidence = (fields.get("field_confidence") or {}).get("pr_number", "")
    provenance = (fields.get("field_provenance") or {}).get("pr_number") or {}
    return confidence not in {"manual", "missing", "conflict"} and provenance.get("source") not in {
        "filename", "manual", "manual_review", "reviewer_correction", "manual_override",
    }


def compare_existing_pr(pr, extracted_fields):
    """Compare untouched PDF extraction against current stored values.

    Callers must supply the original extraction, before applying form edits.
    Filename-only and manually edited PR numbers cannot prove PDF identity.
    """
    source = extracted_fields or {}
    net_total = getattr(pr, "net_total_excl_vat", None)
    if net_total is None:
        net_total = getattr(pr, "total_price", None)
    current_project = getattr(pr, "project", "") or getattr(pr, "project_department", "")
    text_normalizer = lambda value: _text(value).casefold()
    definitions = (
        ("pr_number", "PR number", getattr(pr, "pr_number", ""), source.get("pr_number"), lambda value: normalize_document_number(value, kind="PR")),
        ("product_service", "Product / service", getattr(pr, "product_service", "") or getattr(pr, "title", ""), source.get("product_service"), text_normalizer),
        ("issued_by_name", "Issued by", _issuer_name(pr), source.get("issued_by_name"), text_normalizer),
        ("issued_date", "Issued date", getattr(pr, "issued_date", None), source.get("issued_date"), _date),
        ("supplier_name", "Supplier", getattr(pr, "supplier_name", "") or getattr(pr, "preferred_supplier_if_any", ""), source.get("supplier_name") or source.get("preferred_supplier"), text_normalizer),
        ("project", "Project", current_project, source.get("project_number") or source.get("project_department"), _project),
        ("currency", "Currency", getattr(pr, "currency", ""), source.get("currency"), text_normalizer),
        ("net_total", "Net total excluding VAT", net_total, source.get("net_total"), _money),
        ("po_reference", "PO reference", getattr(pr, "po_number_reference", ""), source.get("po_reference"), lambda value: normalize_document_number(value, kind="PUR")),
    )
    compared = []
    for key, label, current, pdf, normalizer in definitions:
        missing_current = current is None or current == ""
        missing_pdf = pdf is None or pdf == ""
        normalized_current, normalized_pdf = normalizer(current), normalizer(pdf)
        verified_identity = key != "pr_number" or _source_identity_verified(source)
        if missing_current or missing_pdf or not verified_identity:
            status = "missing"
        elif normalized_current == normalized_pdf and normalized_current not in (None, ""):
            status = "matched"
        else:
            status = "mismatch"
        compared.append({
            "field": key, "label": label, "status": status,
            "current_value": _display(current), "pdf_value": _display(pdf),
            "missing_in": "both" if missing_current and missing_pdf else "current" if missing_current else "pdf" if missing_pdf else "source_identity" if not verified_identity else "",
        })
    identity = compared[0]
    return {
        "fields": compared,
        "has_mismatches": any(field["status"] == "mismatch" for field in compared),
        "identity_matched": identity["status"] == "matched",
        "identity_status": identity["status"],
        "matched_count": sum(field["status"] == "matched" for field in compared),
        "mismatch_count": sum(field["status"] == "mismatch" for field in compared),
        "missing_count": sum(field["status"] == "missing" for field in compared),
    }


def _order_references(po):
    po_numbers = {normalize_document_number(getattr(po, "po_number", ""), kind="PUR")}
    pr_numbers = set()
    contacts = getattr(po, "contact_persons", None)
    if isinstance(contacts, dict):
        for key in ("requisition_number", "pr_number"):
            pr_numbers.add(normalize_document_number(contacts.get(key), kind="PR"))
    for attachment in getattr(po, "attachments", []) or []:
        if not isinstance(attachment, dict) or attachment.get("type") not in {"po_excel_import_source", "signed_purchase_order_pdf"}:
            continue
        for key in ("source_po_number", "canonical_po_number"):
            po_numbers.add(normalize_document_number(attachment.get(key), kind="PUR"))
        register = attachment.get("procurement_register")
        if isinstance(register, dict):
            for key, value in register.items():
                label = re.sub(r"[^a-z]", "", str(key).lower())
                if label in {"prnumber", "prno", "requisitionnumber", "requisitionno"}:
                    pr_numbers.add(normalize_document_number(value, kind="PR"))
                elif label in {"ponumber", "pono"}:
                    po_numbers.add(normalize_document_number(value, kind="PUR"))
    return po_numbers - {""}, pr_numbers - {""}


def _result(status, message, *, po=None, candidates=(), manual=True):
    return {
        "status": status, "po_id": str(po.pk) if po else None,
        "po_number": po.po_number if po else "",
        "message": message, "manual_link_required": manual,
        "candidates": [{"po_id": str(item.pk), "po_number": item.po_number,
                        "linked_pr_id": str(item.pr_reference_id) if item.pr_reference_id else None}
                       for item in candidates],
    }


def reconcile_pr_po_link(pr, *, extracted_fields=None):
    """Link one uniquely referenced PO under row locks, without stealing links."""
    with transaction.atomic():
        locked_pr = PurchaseRequisition.objects.select_for_update().get(pk=pr.pk)
        pr_number = normalize_document_number(locked_pr.pr_number, kind="PR")
        if not pr_number:
            return _result("invalid_reference", "Enter a valid PR number before linking a purchase order.")
        if extracted_fields is not None and not compare_existing_pr(locked_pr, extracted_fields)["identity_matched"]:
            return _result("identity_conflict", "The PDF PR number does not match this recommendation. Check the document before linking.")
        raw_references = [_text(getattr(locked_pr, "po_number_reference", ""))]
        if extracted_fields is not None:
            raw_references.append(_text(extracted_fields.get("po_reference")))
        references = {normalize_document_number(value, kind="PUR") for value in raw_references if value}
        if "" in references:
            return _result("invalid_reference", "Check the PO reference format, then select the purchase order to link.")

        # Read only reference metadata while finding candidates. Lock just those
        # rows before mutation, not every order in the company register.
        possible = []
        orders = PurchaseOrder.objects.only("pk", "po_number", "pr_reference_id", "attachments", "contact_persons").iterator()
        for po in orders:
            po_numbers, pr_numbers = _order_references(po)
            if str(po.pr_reference_id or "") == str(locked_pr.pk) or references.intersection(po_numbers) or pr_number in pr_numbers:
                possible.append(po.pk)
        if not possible:
            return _result("not_found", "No matching purchase order exists in RADAI. Create or import it, then link it to this recommendation.")
        candidates = list(PurchaseOrder.objects.select_for_update().filter(pk__in=possible).order_by("pk"))
        if len(candidates) != 1:
            return _result("ambiguous", "More than one purchase order matches these references. Select the correct order to link.", candidates=candidates)
        po = candidates[0]
        po_numbers, source_pr_numbers = _order_references(po)
        if po.pr_reference_id and str(po.pr_reference_id) != str(locked_pr.pk):
            return _result("conflict", "This purchase order is already linked to another recommendation. Its existing link was kept.", candidates=candidates)
        if references and not references.issubset(po_numbers):
            return _result("conflict", "The stored and PDF PO references do not identify the same order. Review them before linking.", candidates=candidates)
        if source_pr_numbers and source_pr_numbers != {pr_number}:
            return _result("conflict", "The purchase order source names a different PR. Review the source references before linking.", candidates=candidates)
        already_linked = str(po.pr_reference_id or "") == str(locked_pr.pk)
        # Recheck a locked candidate in case its references changed since discovery.
        if not already_linked and not references.intersection(po_numbers) and pr_number not in source_pr_numbers:
            return _result("conflict", "The purchase order references changed. Refresh and select the intended order.", candidates=candidates)
        if not already_linked:
            po.pr_reference = locked_pr
            po.save(update_fields=["pr_reference", "updated_at"])
        changed = []
        if not getattr(locked_pr, "po_applicable", False):
            locked_pr.po_applicable = True
            changed.append("po_applicable")
        if not getattr(locked_pr, "po_number_reference", ""):
            locked_pr.po_number_reference = po.po_number
            changed.append("po_number_reference")
        if getattr(locked_pr, "status", "") == "approved":
            locked_pr.status = "converted"
            changed.append("status")
        if changed:
            locked_pr.save(update_fields=[*changed, "updated_at"])
        # Keep the caller's instance consistent without touching its other fields.
        pr.po_applicable = locked_pr.po_applicable
        pr.po_number_reference = locked_pr.po_number_reference
        pr.status = locked_pr.status
        return _result("already_linked" if already_linked else "linked", f"Linked to purchase order {po.po_number}.", po=po, manual=False)


def link_selected_purchase_order(pr, purchase_order_id, *, actor=None):
    """Apply an explicit selection without taking an order from another PR."""
    from .purchase_order_numbering import PurchaseOrderNumberService

    with transaction.atomic():
        locked_pr = PurchaseRequisition.objects.select_for_update().get(pk=pr.pk)
        po = PurchaseOrder.objects.select_for_update().filter(pk=purchase_order_id).first()
        if po is None:
            return _result("not_found", "The selected purchase order no longer exists. Refresh the order list.")
        if po.pr_reference_id and str(po.pr_reference_id) != str(locked_pr.pk):
            return _result("conflict", "This purchase order is already linked to another recommendation. Its existing link was kept.", candidates=[po])
        existing = PurchaseOrder.objects.filter(pr_reference_id=locked_pr.pk).exclude(pk=po.pk)
        if existing.exists():
            return _result("conflict", "This recommendation is already linked to a different purchase order. Review that link before selecting another order.")
        pr_number = normalize_document_number(locked_pr.pr_number, kind="PR")
        po_number = normalize_document_number(po.po_number, kind="PUR")
        if not pr_number:
            return _result("conflict", "Enter a valid PR number before linking a purchase order.")
        valid, message = PurchaseOrderNumberService.verify(po_number, pr_number)
        if not valid:
            return _result("conflict", message or "Check the PR and PO reference numbers before linking.")
        _po_numbers, source_pr_numbers = _order_references(po)
        if source_pr_numbers and source_pr_numbers != {pr_number}:
            return _result("conflict", "The purchase order source names a different PR. Review the source document before linking.", candidates=[po])
        already_linked = str(po.pr_reference_id or "") == str(locked_pr.pk)
        if not already_linked:
            po.pr_reference = locked_pr
            po.save(update_fields=["pr_reference", "updated_at"])
        changed = []
        for field, value in (("po_applicable", True), ("po_number_reference", po.po_number)):
            if getattr(locked_pr, field, None) != value:
                setattr(locked_pr, field, value)
                changed.append(field)
        # Association does not approve a draft or a recommendation in review.
        if getattr(locked_pr, "status", "") == "approved":
            locked_pr.status = "converted"
            changed.append("status")
        po_link = _result("already_linked" if already_linked else "linked", f"Linked to purchase order {po.po_number}.", po=po, manual=False)
        get_actor_name = getattr(actor, "get_full_name", None)
        po_link.update({
            "method": "manual", "linked_at": timezone.now().isoformat(),
            "linked_by_id": str(actor.pk) if actor is not None else None,
            "linked_by_name": get_actor_name() if callable(get_actor_name) else str(getattr(actor, "username", "")),
        })
        metadata = dict(getattr(locked_pr, "price_remarks_data", None) or {})
        metadata["po_link"] = po_link
        locked_pr.price_remarks_data = metadata
        changed.append("price_remarks_data")
        if changed:
            locked_pr.save(update_fields=[*changed, "updated_at"])
        for field in ("po_applicable", "po_number_reference", "status", "price_remarks_data"):
            setattr(pr, field, getattr(locked_pr, field))
        return po_link
