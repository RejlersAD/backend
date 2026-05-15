"""
Workbook Preview Builder
========================

Generates the SPEC/CAT workbook contents as **JSON** (instead of writing to
Excel), so the frontend can render an editable "cross-check" canvas that
matches the template layout exactly.

The same soft-coded builders used by `smartplant_exporter` are reused — so
the canvas and the downloaded xlsx always agree.

Outputs the shape:

    {
        "workbook":   "spec" | "cat",
        "job_id":     "<uuid>",
        "sheets": [
            {
                "name":    "PipingMaterialsClassData",
                "headers": ["Class", "MaterialGrade", ...],
                "rows": [
                    {
                        "row_key":   "cls:<uuid>:b0:0",
                        "cells":     {"Class": "A1", "MaterialGrade": "...", ...},
                        "overridden": ["Class"],     # cells edited by user
                        "source": {                  # provenance for UI hints
                            "class_id":     "<uuid>",
                            "class_code":   "A",
                            "component_id": "<uuid>" | null
                        }
                    },
                    ...
                ]
            },
            ...
        ]
    }

The `row_key` is deterministic and stable across rebuilds; it is the same
key persisted on `WorkbookCellOverride.row_key`.
"""
from __future__ import annotations

import logging
from typing import Iterable

from openpyxl import load_workbook

from . import smartplant_config as cfg

logger = logging.getLogger(__name__)


# ─── Soft-coded knobs ────────────────────────────────────────────────────────
WORKBOOK_SPEC = 'spec'
WORKBOOK_CAT  = 'cat'

# Limit how deep we scan the template for the Head row.
_HEADER_SCAN_DEPTH = 60


def _template_headers(template_path: str, sheet_name: str) -> list[str]:
    """Read the column headers (left-to-right) from a template sheet.

    The header row is the LAST row in the first ~60 rows whose column-A value
    is in `cfg.HEADER_MARKERS`.  Returns headers in column order, dropping
    blank columns at the end.
    """
    try:
        wb = load_workbook(template_path, read_only=True, data_only=True)
    except Exception:
        logger.exception("[WorkbookPreview] could not open template %s", template_path)
        return []
    if sheet_name not in wb.sheetnames:
        return []
    ws = wb[sheet_name]
    header_row = None
    for r in range(1, min(ws.max_row, _HEADER_SCAN_DEPTH) + 1):
        v = ws.cell(r, 1).value
        if v is not None and str(v).strip() in cfg.HEADER_MARKERS:
            header_row = r
    if header_row is None:
        return []
    headers: list[str] = []
    for c in range(1, ws.max_column + 1):
        v = ws.cell(header_row, c).value
        headers.append(str(v).strip() if v is not None else '')
    # Trim trailing blanks.
    while headers and not headers[-1]:
        headers.pop()
    return headers


# Module-level cache: template_path → {sheet_name → [headers]}
_HEADERS_CACHE: dict[str, dict[str, list[str]]] = {}


def _get_headers(template_path: str, sheet_name: str) -> list[str]:
    by_sheet = _HEADERS_CACHE.setdefault(template_path, {})
    if sheet_name not in by_sheet:
        by_sheet[sheet_name] = _template_headers(template_path, sheet_name)
    return by_sheet[sheet_name]


# ─── Row enumeration (reuses exporter builders) ──────────────────────────────
def _enumerate_spec_rows(job):
    """Yield (sheet_name, row_key, source_dict, cells_dict) tuples for SPEC."""
    classes = list(job.piping_classes.prefetch_related('components').order_by('class_code'))
    for sheet_name, builders in cfg.SPEC_SHEET_BUILDERS.items():
        for cls in classes:
            for b_idx, builder in enumerate(builders):
                seq = 0
                for row in builder(cls):
                    if not row:
                        continue
                    row_key = f'cls:{cls.id}:b{b_idx}:{seq}'
                    source = {
                        'class_id':     str(cls.id),
                        'class_code':   cls.class_code,
                        'component_id': None,
                    }
                    yield sheet_name, row_key, source, dict(row)
                    seq += 1


def _enumerate_cat_rows(job):
    """Yield (sheet_name, row_key, source_dict, cells_dict) tuples for CAT."""
    classes = list(job.piping_classes.prefetch_related('components').order_by('class_code'))
    # Route components → sheets exactly like the exporter does.
    by_sheet: dict[str, list] = {}
    for cls in classes:
        for comp in cls.components.all().order_by('display_order'):
            sheet = cfg.route_component_to_cat_sheet(comp)
            if sheet is None:
                continue
            by_sheet.setdefault(sheet, []).append((cls, comp))

    for sheet_name, items in by_sheet.items():
        builder = cfg.CAT_SHEET_BUILDERS.get(sheet_name)
        if builder is None:
            continue
        for cls, comp in items:
            result = builder(cls, comp)
            if not result:
                continue
            rows = result if isinstance(result, list) else [result]
            for seq, row in enumerate(rows):
                if not row:
                    continue
                row_key = f'comp:{comp.id}:{sheet_name}:{seq}'
                source = {
                    'class_id':     str(cls.id),
                    'class_code':   cls.class_code,
                    'component_id': str(comp.id),
                }
                yield sheet_name, row_key, source, dict(row)


# ─── Override application ────────────────────────────────────────────────────
def _load_overrides(job, workbook: str) -> dict[tuple[str, str], dict[str, str]]:
    """Return overrides keyed by (sheet_name, row_key) → {column_name: value}."""
    from ...models import WorkbookCellOverride  # local import to dodge cycles
    out: dict[tuple[str, str], dict[str, str]] = {}
    qs = WorkbookCellOverride.objects.filter(job=job, workbook=workbook)
    for ov in qs:
        out.setdefault((ov.sheet_name, ov.row_key), {})[ov.column_name] = ov.value
    return out


# ─── Public API ──────────────────────────────────────────────────────────────
def build_preview(job, workbook: str) -> dict:
    """Build the JSON preview for the given workbook ('spec' or 'cat').

    Applies any saved `WorkbookCellOverride` entries on top of the
    builder-generated cells.
    """
    if workbook not in (WORKBOOK_SPEC, WORKBOOK_CAT):
        raise ValueError(f"unknown workbook: {workbook!r}")

    template_path = cfg.SPEC_TEMPLATE_PATH if workbook == WORKBOOK_SPEC else cfg.CAT_TEMPLATE_PATH
    enumerate_fn  = _enumerate_spec_rows if workbook == WORKBOOK_SPEC else _enumerate_cat_rows
    overrides     = _load_overrides(job, workbook)

    # Bucket rows by sheet, preserving order.
    sheets_order: list[str] = []
    rows_by_sheet: dict[str, list[dict]] = {}

    for sheet_name, row_key, source, cells in enumerate_fn(job):
        if sheet_name not in rows_by_sheet:
            sheets_order.append(sheet_name)
            rows_by_sheet[sheet_name] = []
        sheet_overrides = overrides.get((sheet_name, row_key), {})
        # Apply overrides (string-typed; UI sends strings).
        merged = {k: ('' if v is None else v) for k, v in cells.items()}
        for k, v in sheet_overrides.items():
            merged[k] = v
        rows_by_sheet[sheet_name].append({
            'row_key':    row_key,
            'cells':      merged,
            'overridden': sorted(sheet_overrides.keys()),
            'source':     source,
        })

    # Build the final shape with template-driven column order.
    sheets_out = []
    for sheet_name in sheets_order:
        headers = _get_headers(template_path, sheet_name)
        # If the template scan failed, derive headers from the first row.
        if not headers and rows_by_sheet[sheet_name]:
            headers = list(rows_by_sheet[sheet_name][0]['cells'].keys())
        sheets_out.append({
            'name':       sheet_name,
            'headers':    headers,
            'row_count':  len(rows_by_sheet[sheet_name]),
            'rows':       rows_by_sheet[sheet_name],
        })

    return {
        'workbook':  workbook,
        'job_id':    str(job.id),
        'sheets':    sheets_out,
    }


def apply_overrides_to_row(
    sheet_name: str,
    row_key: str,
    cells: dict,
    overrides: dict[tuple[str, str], dict[str, str]],
) -> dict:
    """Merge an override slice into a generated cells dict.

    Helper for the xlsx exporter so the same overrides also flow into the
    downloadable workbook.
    """
    ov = overrides.get((sheet_name, row_key))
    if not ov:
        return cells
    merged = dict(cells)
    for k, v in ov.items():
        merged[k] = v
    return merged
