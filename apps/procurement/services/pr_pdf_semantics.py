"""Assign PR values using document sections and physical table columns.

The text reader provides cells; this module determines what those cells mean.
Money in Remarks cannot become a purchase amount, and approval roles never
come from a row's ordinal position.
"""

from __future__ import annotations

import re


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip(" |\t\r\n")


def normalized(value):
    return re.sub(r"[^a-z0-9]", "", clean(value).lower())


def rows_from_layout(layout):
    for page in layout or []:
        for row in sorted(page.get("table_rows", []), key=lambda item: item.get("bbox", [0, 0])[1]):
            cells = sorted(row.get("cells", []), key=lambda cell: cell.get("bbox", [0])[0])
            if cells:
                yield page, row, cells


def column_value(cells, column):
    """Find the cell overlapping a recognized header column, including wraps."""
    left, _, right, _ = column["bbox"]
    matches = []
    for cell in cells:
        x0, _, x1, _ = cell["bbox"]
        overlap = min(right, x1) - max(left, x0)
        if overlap > 0 and overlap >= min(right - left, x1 - x0) * 0.65:
            matches.append(cell)
    return max(matches, key=lambda cell: min(right, cell["bbox"][2]) - max(left, cell["bbox"][0]), default=None)


def _evidence(page, cell, section):
    return {
        "source": "layout_cell", "section": section,
        "page": page.get("page"), "bbox": cell.get("bbox", []),
        "coordinate_space": page.get("coordinate_space"),
        "evidence": clean(cell.get("text"))[:2000],
        "text_method": cell.get("source", "unknown"),
        "recognition_confidence": cell.get("confidence"),
    }


def apply_pr_layout_semantics(fields, source, parser, filename):
    """Reconcile fields with labeled sections and the Price table's geometry."""
    values, evidence, price_rows = {}, {}, []
    price_columns = None
    in_price = False
    for page, row, cells in rows_from_layout(source.get("page_layout", [])):
        row_text = " | ".join(clean(cell.get("text")) for cell in cells)
        norm = normalized(row_text)
        if norm == "approvals":
            break

        for cell in cells:
            text = clean(cell.get("text"))
            patterns = (
                ("issued_by_name", r"^Issued\s+by\s*:\s*(.+)$"),
                ("issued_date", r"^(?:Issued\s+)?Date\s*:\s*(.+)$"),
                ("product_service", r"^Product\s*/\s*Service\s*:\s*(.+)$"),
                ("supplier_name", r"^Supplier\s*:\s*(.+)$"),
                ("project_department", r"^Project\s*/\s*Department\s*:\s*(.+)$"),
                ("description_reason", r"^\d*\s*[.)]?\s*Description\s+and\s+Reason\s+for\s+Purchase\s*:?\s*(.+)$"),
                ("preferred_supplier", r"^\d*\s*[.)]?\s*Preferred\s+Supplier\s*(?:\(\s*if\s*any\s*\))?\s*:?\s*(.+)$"),
                ("po_reference", r"^P\.?\s*O\.?\s*Reference\s*:?\s*(.+)$"),
                ("special_notes", r"^\d*\s*[.)]?\s*(?:Special\s+Notes|Purchase\s+Recommendation)\s*:?\s*(?:\(\s*if\s*any\s*\)\s*:?\s*)?(.*)$"),
            )
            for key, pattern in patterns:
                match = re.match(pattern, text, re.IGNORECASE)
                if match and clean(match.group(1)):
                    values[key] = clean(match.group(1))
                    evidence[key] = _evidence(page, cell, key)
                    if key == "special_notes":
                        raw_lines = [line.strip() for line in str(cell.get("text", "")).splitlines() if line.strip()]
                        first_line = re.match(pattern, raw_lines[0], re.IGNORECASE) if raw_lines else None
                        if len(raw_lines) > 1 and first_line and clean(first_line.group(1)):
                            values["special_notes_annotation"] = clean(first_line.group(1))
                            values[key] = clean(" ".join(raw_lines[1:]))

        # Header meanings establish the amount and Remarks columns. Column
        # numbers and page-relative crop fractions are deliberately irrelevant.
        amount_header = next((cell for cell in cells if normalized(cell.get("text")) in {"totalprice", "amount", "totalamount", "priceamount"}), None)
        remarks_header = next((cell for cell in cells if normalized(cell.get("text")).startswith("remarks")), None)
        if amount_header and remarks_header:
            description_header = next((cell for cell in cells if cell["bbox"][2] <= amount_header["bbox"][0] + 2), None)
            if description_header:
                price_columns = {"description": description_header, "amount": amount_header, "remarks": remarks_header}
                in_price = True
            continue

        if not price_columns:
            continue
        if "nettotal" in norm and ("vat" in norm or "excl" in norm):
            amount_cell = column_value(cells, price_columns["amount"])
            if amount_cell and clean(amount_cell.get("text")):
                values["net_total_cell"] = clean(amount_cell["text"])
                evidence["price"] = _evidence(page, amount_cell, "net_total")
            in_price = False
            continue
        if "poreference" in norm or "specialnotes" in norm or "purchaserecommendation" in norm:
            in_price = False
        if in_price:
            row_cells = {key: column_value(cells, header) for key, header in price_columns.items()}
            amount_cell = row_cells["amount"]
            if amount_cell and re.search(r"\d", amount_cell.get("text", "")):
                description = clean((row_cells["description"] or {}).get("text"))
                amount = clean(amount_cell["text"])
                remarks = clean((row_cells["remarks"] or {}).get("text"))
                price_rows.append(" | ".join((description, amount, remarks)))
                evidence.setdefault("price_lines", []).append({
                    "description": _evidence(page, row_cells["description"], "price_description") if row_cells["description"] else None,
                    "amount": _evidence(page, amount_cell, "price_amount"),
                    "remarks": _evidence(page, row_cells["remarks"], "price_remarks") if row_cells["remarks"] else None,
                })

    if not values and not price_rows:
        return fields
    merged = {**fields, **values}
    canonical = [
        f"PR No. {fields.get('pr_number', '')}",
        f"Issued by: {merged.get('issued_by_name', '')}",
        f"Date: {merged.get('issued_date') or ''}",
        f"Product/Service: {merged.get('product_service', '')}",
        f"Supplier: {merged.get('supplier_name', '')}",
        f"Project/Department: {merged.get('project_department', '')}",
        f"1. Description and Reason for Purchase: {merged.get('description_reason', '')}",
        f"2. Preferred Supplier (if any): {merged.get('preferred_supplier', '')}",
        "3. Price | Total Price | Remarks",
        *price_rows,
        f"Net Total, excl VAT | {values.get('net_total_cell', '')}",
        f"PO Reference: {merged.get('po_reference', '')}",
        f"4. Special Notes (if any): {merged.get('special_notes', '')}",
        "APPROVALS",
    ]
    parsed = parser("\n".join(canonical), filename, allow_missing_pr_number=True)
    keys = set(values) - {"net_total_cell", "special_notes_annotation"}
    if "project_department" in values:
        keys.update(("project_number", "project_numbers", "project_reference_numbers"))
    if price_rows or "net_total_cell" in values:
        keys.update(("price_lines", "price_remarks", "net_total", "currency", "net_total_aed", "budget_in_aed"))
    aliases = {"issued_by_name": "issued_by", "supplier_name": "supplier", "description_reason": "description", "net_total": "price"}
    result = {**fields, "field_provenance": dict(fields.get("field_provenance", {})), "field_confidence": dict(fields.get("field_confidence", {}))}
    for key in keys:
        if parsed.get(key) in (None, "", []):
            continue
        result[key] = parsed[key]
        confidence_key = aliases.get(key, key)
        confidence = parsed.get("field_confidence", {}).get(confidence_key, "medium")
        result["field_confidence"][confidence_key] = "medium" if confidence == "high" and source.get("method") != "native" else confidence
        cell_evidence = evidence.get(key) or evidence.get(confidence_key)
        if key == "currency":
            cell_evidence = evidence.get("price")
        if key in {"price_lines", "price_remarks"}:
            cell_evidence = {"source": "layout_table_columns", "rows": evidence.get("price_lines", [])}
        result["field_provenance"][confidence_key] = cell_evidence or {"source": "layout_section", "evidence": str(parsed[key])[:1500]}
    still_missing = {key for key, confidence in result["field_confidence"].items() if confidence == "missing"}
    result["extraction_issues"] = [
        issue for issue in fields.get("extraction_issues", [])
        if not issue.startswith("OCR could not confidently extract the labeled field:")
        or any(f": {key.replace('_', ' ')}." in issue for key in still_missing)
    ]
    result["extraction_issues"].extend(issue for issue in parsed.get("extraction_issues", []) if "differs" in issue or "currencies" in issue or "Different labeled" in issue)
    result["semantic_sections"] = sorted(values)
    if values.get("special_notes_annotation"):
        result["special_notes_annotation"] = values["special_notes_annotation"]
        result["field_confidence"]["special_notes_annotation"] = "low"
        result["field_provenance"]["special_notes_annotation"] = evidence.get("special_notes", {})
        result["extraction_issues"].append("Check the handwritten special note against the PDF.")
    return result


def approval_role(value):
    role = normalized(value)
    if role in {"pm", "pd", "dm", "projectmanager", "projectdirector", "deliverymanager"}:
        return "pm"
    if role in {"moe", "managerofengineering", "engineeringmanager"}:
        return "moe"
    if role in {"mop", "managerofprojects", "projectsmanager"}:
        return "mop"
    if role in {"vp", "vpop", "vpo", "vicepresident", "vicepresidentoperations"}:
        return "vp"
    return ""


def approval_rows_from_layout(layout):
    """Read explicitly labeled roles beneath Name / Signature table headings."""
    result, columns, dates = [], None, []
    for page, row, cells in rows_from_layout(layout):
        names = next((cell for cell in cells if normalized(cell.get("text")) == "name"), None)
        signature = next((cell for cell in cells if normalized(cell.get("text")) in {"signature", "signatures"}), None)
        if names and signature:
            role = next((cell for cell in cells if cell["bbox"][2] <= names["bbox"][0] + 2), None)
            remarks = next((cell for cell in cells if normalized(cell.get("text")).startswith("remarks")), None)
            columns = {"role": role, "name": names, "signature": signature, "remarks": remarks}
            continue
        if not columns or not columns["role"]:
            continue
        role_cell = column_value(cells, columns["role"])
        source_role = clean((role_cell or {}).get("text"))
        name_cell = column_value(cells, columns["name"])
        if normalized(source_role) == "date":
            if name_cell:
                dates.append({"text": clean(name_cell.get("text")), "evidence": _evidence(page, name_cell, "approval_date")})
            continue
        role_key = approval_role(source_role)
        if not role_key:
            continue
        remarks_cell = column_value(cells, columns["remarks"]) if columns["remarks"] else None
        result.append({
            "source_role": source_role, "role_key": role_key,
            "raw_name": clean((name_cell or {}).get("text")),
            "name": clean((name_cell or {}).get("text")),
            "remarks": clean((remarks_cell or {}).get("text")),
            "page": page.get("page"), "coordinate_space": page.get("coordinate_space"),
            "page_width": page.get("width"), "page_height": page.get("height"),
            "row_bbox": row.get("bbox", []),
            "signature_bbox": [columns["signature"]["bbox"][0], row["bbox"][1], columns["signature"]["bbox"][2], row["bbox"][3]],
            "name_evidence": _evidence(page, name_cell, "approval_name") if name_cell else {},
        })
    return result, dates
