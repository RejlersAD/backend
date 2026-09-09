"""
Workbook Validation Service
===========================

Validates already-extracted SPEC/CAT workbook rows before/after chatbot edits.
This is intentionally deterministic and rule-based (no LLM dependency).
"""
from __future__ import annotations

import re
from typing import Any

from .exporters.workbook_preview import WORKBOOK_CAT, WORKBOOK_SPEC, build_preview


_VALID_WORKBOOKS = {WORKBOOK_SPEC, WORKBOOK_CAT}
_MAX_ISSUES = 250

_PRESSURE_RE = re.compile(r"^(?:CLASS|CL)\s*\d{2,4}$|^#\s*\d{2,4}$|^PN\s*\d{1,3}$", re.IGNORECASE)
_ALLOWED_FACING = {"RF", "FF", "RTJ", "RAISED FACE", "FLAT FACE", "RING TYPE JOINT"}
_ALLOWED_END_CONN = {"BW", "SW", "THR", "NPT", "RF", "FF", "RTJ"}
_MATERIAL_HINT_RE = re.compile(r"ASTM|ASME|SA\s*-?\s*\d+|AISI|SS\s*3(?:04|16)|CS|LTCS|DUPLEX", re.IGNORECASE)


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _first_column(headers: list[str], *candidates: str) -> str | None:
    if not headers:
        return None
    lowered = {h.lower(): h for h in headers}
    for c in candidates:
        if c.lower() in lowered:
            return lowered[c.lower()]
    for h in headers:
        lh = h.lower()
        for c in candidates:
            if c.lower() in lh:
                return h
    return None


def _add_issue(issues: list[dict[str, Any]], *, severity: str, workbook: str, sheet_name: str, row_key: str, column_name: str, message: str, value: Any, suggestion: str) -> None:
    if len(issues) >= _MAX_ISSUES:
        return
    issues.append({
        "severity": severity,
        "workbook": workbook,
        "sheet_name": sheet_name,
        "row_key": row_key,
        "column_name": column_name,
        "message": message,
        "value": "" if value is None else str(value),
        "suggestion": suggestion,
    })


def _numeric_size_token(v: str) -> float | None:
    m = re.search(r"\d+(?:\.\d+)?", v)
    if not m:
        return None
    try:
        return float(m.group(0))
    except Exception:
        return None


def _validate_sheet(workbook: str, sheet: dict[str, Any], issues: list[dict[str, Any]]) -> int:
    headers = [str(h) for h in (sheet.get("headers") or [])]
    rows = sheet.get("rows") or []
    checked = 0

    col_desc = _first_column(headers, "Description")
    col_material = _first_column(headers, "MaterialGrade", "Material")
    col_commodity = _first_column(headers, "CommodityCode", "Commodity")
    col_pressure = _first_column(headers, "PressureRating", "Rating")
    col_facing = _first_column(headers, "FlangeFacing", "Facing")
    col_end = _first_column(headers, "EndConnection", "End Conn")
    col_size_from = _first_column(headers, "SizeFrom", "Size From")
    col_size_to = _first_column(headers, "SizeTo", "Size To")

    for row in rows:
        checked += 1
        cells = row.get("cells") or {}
        row_key = str(row.get("row_key") or "")
        sheet_name = str(sheet.get("name") or "")

        if col_desc and not _norm(cells.get(col_desc)):
            _add_issue(
                issues,
                severity="medium",
                workbook=workbook,
                sheet_name=sheet_name,
                row_key=row_key,
                column_name=col_desc,
                message="Description is empty.",
                value=cells.get(col_desc),
                suggestion="Fill description for reliable matching and export quality.",
            )

        if col_commodity and not _norm(cells.get(col_commodity)):
            _add_issue(
                issues,
                severity="high",
                workbook=workbook,
                sheet_name=sheet_name,
                row_key=row_key,
                column_name=col_commodity,
                message="Commodity code is empty.",
                value=cells.get(col_commodity),
                suggestion="Provide a commodity code for catalog routing.",
            )

        if col_material:
            mv = _norm(cells.get(col_material))
            if mv and not _MATERIAL_HINT_RE.search(mv):
                _add_issue(
                    issues,
                    severity="low",
                    workbook=workbook,
                    sheet_name=sheet_name,
                    row_key=row_key,
                    column_name=col_material,
                    message="Material grade looks non-standard.",
                    value=mv,
                    suggestion="Use a standard material format (e.g. ASTM A106 Gr.B, SS316).",
                )

        if col_pressure:
            pv = _norm(cells.get(col_pressure))
            if pv and not _PRESSURE_RE.match(pv):
                _add_issue(
                    issues,
                    severity="medium",
                    workbook=workbook,
                    sheet_name=sheet_name,
                    row_key=row_key,
                    column_name=col_pressure,
                    message="Pressure rating format is unusual.",
                    value=pv,
                    suggestion="Use CLASS/CL/PN format (e.g. CLASS 150, PN 16).",
                )

        if col_facing:
            fv = _norm(cells.get(col_facing)).upper()
            if fv and fv not in _ALLOWED_FACING:
                _add_issue(
                    issues,
                    severity="low",
                    workbook=workbook,
                    sheet_name=sheet_name,
                    row_key=row_key,
                    column_name=col_facing,
                    message="Flange facing is not in known set.",
                    value=fv,
                    suggestion="Use RF, FF, RTJ, Raised Face, or Flat Face.",
                )

        if col_end:
            ev = _norm(cells.get(col_end)).upper()
            if ev and ev not in _ALLOWED_END_CONN:
                _add_issue(
                    issues,
                    severity="low",
                    workbook=workbook,
                    sheet_name=sheet_name,
                    row_key=row_key,
                    column_name=col_end,
                    message="End connection is not in known set.",
                    value=ev,
                    suggestion="Use BW, SW, THR, NPT, RF, FF, or RTJ.",
                )

        if col_size_from and col_size_to:
            sfrom = _numeric_size_token(_norm(cells.get(col_size_from)))
            sto = _numeric_size_token(_norm(cells.get(col_size_to)))
            if sfrom is not None and sto is not None and sfrom > sto:
                _add_issue(
                    issues,
                    severity="high",
                    workbook=workbook,
                    sheet_name=sheet_name,
                    row_key=row_key,
                    column_name=f"{col_size_from}/{col_size_to}",
                    message="Size range appears inverted (from > to).",
                    value=f"{cells.get(col_size_from)} -> {cells.get(col_size_to)}",
                    suggestion="Swap or correct Size From and Size To.",
                )

        if len(issues) >= _MAX_ISSUES:
            break

    return checked


def validate_extracted_workbook(*, job, workbook_scope: str = "auto", active_workbook: str = WORKBOOK_SPEC) -> dict[str, Any]:
    scope = (workbook_scope or "auto").strip().lower()
    if scope in _VALID_WORKBOOKS:
        workbooks = [scope]
    elif scope == "both":
        workbooks = [WORKBOOK_SPEC, WORKBOOK_CAT]
    else:
        workbooks = [active_workbook if active_workbook in _VALID_WORKBOOKS else WORKBOOK_SPEC]

    issues: list[dict[str, Any]] = []
    checked_rows = 0
    checked_sheets = 0

    for wb in workbooks:
        preview = build_preview(job, wb)
        for sheet in preview.get("sheets", []):
            checked_sheets += 1
            checked_rows += _validate_sheet(wb, sheet, issues)
            if len(issues) >= _MAX_ISSUES:
                break
        if len(issues) >= _MAX_ISSUES:
            break

    by_severity = {"high": 0, "medium": 0, "low": 0}
    for it in issues:
        sev = str(it.get("severity") or "").lower()
        if sev in by_severity:
            by_severity[sev] += 1

    status = "pass" if not issues else ("warn" if by_severity["high"] == 0 else "fail")

    return {
        "ok": True,
        "status": status,
        "workbooks": workbooks,
        "checked_sheets": checked_sheets,
        "checked_rows": checked_rows,
        "issues_total": len(issues),
        "severity_counts": by_severity,
        "issues": issues,
    }
