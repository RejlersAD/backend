"""Approval-table evidence from recognized cells, never positional guesses."""

import re

import numpy as np
import pymupdf
from scipy.ndimage import binary_opening

from .pr_pdf_semantics import approval_rows_from_layout
from .pr_pdf_handwriting import parse_source_date, read_approval_date


ROLES = ("pm", "moe", "mop", "vp")


def _signature_candidate(document, row, layout):
    """Measure interior ink only; a candidate is not signature verification."""
    bounds = row.get("signature_bbox", [])
    if len(bounds) != 4 or not row.get("page_width") or not row.get("page_height"):
        return False, 0.0, False
    page = document[max(0, int(row.get("page", 1)) - 1)]
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
    pixels = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)[:, :, :3]
    sx, sy = pixmap.width / row["page_width"], pixmap.height / row["page_height"]
    x0, y0, x1, y1 = (int(bounds[0] * sx) + 4, int(bounds[1] * sy) + 4, int(bounds[2] * sx) - 4, int(bounds[3] * sy) - 4)
    crop = pixels[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
    if not crop.size:
        return False, 0.0, False
    ink = crop.mean(axis=2) < 175
    # Remove remaining long rules even when the scan is slightly misaligned.
    horizontal = binary_opening(ink, structure=np.ones((1, max(12, int(ink.shape[1] * 0.45))), dtype=bool))
    vertical = binary_opening(ink, structure=np.ones((max(8, int(ink.shape[0] * 0.65)), 1), dtype=bool))
    ink &= ~(horizontal | vertical)
    for token in layout.get("tokens", []):
        tx0, ty0, tx1, ty1 = token.get("bbox", [0, 0, 0, 0])
        if not (bounds[0] <= (tx0 + tx1) / 2 <= bounds[2] and bounds[1] <= (ty0 + ty1) / 2 <= bounds[3]):
            continue
        printed = token.get("source") == "native" or (
            float(token.get("confidence") or 0) >= 70 and re.fullmatch(r"[A-Za-z0-9]{3,}", token.get("text", ""))
        )
        if printed:
            a, b = max(0, int(tx0 * sx) - x0 - 1), max(0, int(tx1 * sx) - x0 + 1)
            c, d = max(0, int(ty0 * sy) - y0 - 1), max(0, int(ty1 * sy) - y0 + 1)
            ink[c:d, a:b] = False
    density = float(ink.mean())
    candidate = bool(ink.sum() >= max(12, ink.size * 0.002))
    yy, xx = np.where(ink)
    spread_x = int(xx.max() - xx.min() + 1) if xx.size else 0
    spread_y = int(yy.max() - yy.min() + 1) if yy.size else 0
    detected = bool(candidate and density >= 0.01 and ink.sum() >= 30
                    and spread_x >= ink.shape[0] * 0.7 and spread_y >= ink.shape[0] * 0.3)
    return candidate, round(density, 4), detected


def evaluate_pr_approvals(pdf_bytes, source):
    layouts = source.get("page_layout", [])
    rows, date_cells = approval_rows_from_layout(layouts)
    page_lookup = {page.get("page"): page for page in layouts}
    result = {
        "template": "Detected purchase requisition approval table",
        "table_detected": bool(rows), "table_anchor_y": None,
        "approval_rows": rows,
        "signatures": {role: False for role in ROLES},
        "signature_candidates": {role: False for role in ROLES},
        "signature_density": {role: 0.0 for role in ROLES},
        "all_four_signatures": False,
        "approver_names": {role: "" for role in ROLES},
        "date_present": False, "date_density": 0.0,
        "approval_date": None, "date_ocr": [cell["text"] for cell in date_cells],
        "approval_evidence_issues": [],
    }
    if rows:
        result["table_anchor_y"] = round(rows[0]["row_bbox"][1] / rows[0]["page_height"], 4)
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as document:
        for row in rows:
            candidate, density, detected = _signature_candidate(document, row, page_lookup.get(row.get("page"), {}))
            row.update({
                "signature_candidate": candidate, "signature_detected": detected,
                "signature_density": density, "signature_requires_manual_verification": candidate and not detected,
            })
            role = row["role_key"]
            if result["approver_names"][role] and result["approver_names"][role] != row["name"]:
                result["approval_evidence_issues"].append(f"More than one explicitly labeled {role.upper()} row exists; confirm the intended approver.")
                continue
            result["approver_names"][role] = row["name"]
            result["signature_candidates"][role] = candidate
            result["signatures"][role] = detected
            result["signature_density"][role] = density
        for cell in date_cells:
            evidence = cell["evidence"]
            value = parse_source_date(cell["text"])
            native = evidence.get("text_method") == "native"
            if value and native:
                # A printed/native date remains direct source evidence. It
                # does not itself verify any approval signature or identity.
                date_result = {"date": value, "ink_present": True, "evidence": evidence}
            else:
                layout = page_lookup.get(evidence.get("page"), {})
                date_result = read_approval_date(document, cell, layout)
            result["date_present"] = result["date_present"] or date_result["ink_present"]
            result["approval_date_evidence"] = date_result["evidence"]
            result["date_ocr"].extend(candidate["text"] for candidate in date_result["evidence"].get("candidates", []) if candidate.get("text"))
            if date_result["date"]:
                result["approval_date"] = date_result["date"]
                break
    missing = [role.upper() for role, name in result["approver_names"].items() if not name]
    if missing:
        result["approval_evidence_issues"].append(f"No explicitly labeled source row was found for: {', '.join(missing)}.")
    result["all_four_signatures"] = all(result["signatures"].values())
    if any(row["signature_requires_manual_verification"] for row in rows):
        result["approval_evidence_issues"].append("Some signature cells contain uncertain ink; visually verify those signatures.")
    if date_cells and result["approval_date"] is None:
        result["approval_evidence_issues"].append("The approval date is not reliably readable; enter the date after checking the source PDF.")
    return result
