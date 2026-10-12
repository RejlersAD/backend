"""Legend Workbook Exporter — per-section legends + symbol pictures → one
formatted multi-sheet .xlsx.

Companion to legend_workbook_importer.py: the importer turns a multi-sheet
legend workbook into per-section legend definitions + LegendSymbolImage
rows; this exporter turns them back into a readable workbook:

  • One sheet per legend — title row, separator, a styled fields table
    (bold header, borders, frozen header, sensible column widths, wrapped
    text) and, for every field that carries a {code: description} lookup,
    an aligned two-column CODE / DESCRIPTION table instead of the flattened
    "A=… | B=…" string the client-side export produces.
  • One "… Symbols" sheet per section that has symbol pictures — each
    picture embedded in the SYMBOL column (openpyxl anchored image) next to
    its name, rows sized to the picture so nothing overlaps.
  • A Metadata sheet summarising the export.

Symbols come from the same four tiers the UI uses (project uploads →
cross-project fallback → DB default library; see views.SymbolImagesListView).
SVG pictures can't be embedded by openpyxl and are skipped with a note in
the NAME column.

Everything presentational (colours, widths, image size) is soft-coded at
module level.
"""
from __future__ import annotations

import io
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import openpyxl
from openpyxl.drawing.image import Image as XlImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from ..legend_defaults import SECTION_LABELS
from ..models import PidCheckerV2LegendSheet, LegendSymbolImage

logger = logging.getLogger(__name__)

# ─── Soft-coded styling ─────────────────────────────────────────────────
HEADER_FILL = PatternFill('solid', fgColor='1F4E79')
HEADER_FONT = Font(bold=True, color='FFFFFF', size=10)
TITLE_FONT = Font(bold=True, size=14, color='1F4E79')
SUBTITLE_FONT = Font(italic=True, size=10, color='595959')
SECTION_FONT = Font(bold=True, size=11, color='1F4E79')
THIN = Side(style='thin', color='BFBFBF')
CELL_BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP_TOP = Alignment(wrap_text=True, vertical='top')
CENTER = Alignment(horizontal='center', vertical='center', wrap_text=True)

FIELD_HEADERS = ['Order', 'Key', 'Label', 'Regex', 'Suffix', 'Optional', 'Separator', 'Notes']
FIELD_WIDTHS = [7, 16, 26, 30, 9, 10, 11, 60]
LOOKUP_WIDTHS = [18, 70]
SYMBOL_IMG_PX = 56                      # embedded symbol pictures are scaled to this
SYMBOL_COL_WIDTH = 12                   # ~ SYMBOL_IMG_PX
SYMBOL_ROW_PT = SYMBOL_IMG_PX * 0.78    # px → points, plus padding
SHEET_NAME_MAX = 31
CONTENT_TYPE_XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


# ─── Small helpers ──────────────────────────────────────────────────────
def _sheet_title(base: str, used: set) -> str:
    """Excel-safe unique sheet name (<=31 chars, no []:*?/\\)."""
    name = re.sub(r'[\[\]:*?/\\]', '_', str(base or 'Sheet'))[:SHEET_NAME_MAX] or 'Sheet'
    out, n = name, 2
    while out in used:
        suffix = f' ({n})'
        out = name[:SHEET_NAME_MAX - len(suffix)] + suffix
        n += 1
    used.add(out)
    return out


def _style_header_row(ws, row: int, ncols: int) -> None:
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = CENTER
        cell.border = CELL_BORDER


def _set_widths(ws, widths: List[float]) -> None:
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _lookup_items(lookup: Any) -> List[Tuple[str, str]]:
    if not isinstance(lookup, dict):
        return []
    return sorted(((str(k), str(v)) for k, v in lookup.items()), key=lambda kv: kv[0])


# ─── Legend sheet writer ────────────────────────────────────────────────
def _write_legend_sheet(wb, legend, used_names: set, sheet_base: Optional[str] = None) -> str:
    """One worksheet per legend: title, fields table, per-field lookup
    tables and notes — all styled/aligned. ``sheet_base`` overrides the
    sheet-tab label (the full legend name still heads the sheet itself)."""
    definition = legend.definition or {}
    fields = definition.get('fields') or []
    ws = wb.create_sheet(_sheet_title(sheet_base or legend.name, used_names))
    _set_widths(ws, FIELD_WIDTHS)

    row = 1
    ws.cell(row=row, column=1, value=legend.name).font = TITLE_FONT
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=len(FIELD_HEADERS))
    row += 1
    subtitle_parts = [
        f'Section: {SECTION_LABELS.get(legend.section, legend.section)}',
        f'Separator: {definition.get("separator", "-")!r}',
    ]
    if legend.description:
        subtitle_parts.append(str(legend.description))
    cell = ws.cell(row=row, column=1, value='  ·  '.join(subtitle_parts))
    cell.font = SUBTITLE_FONT
    cell.alignment = WRAP_TOP
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=len(FIELD_HEADERS))
    row += 2

    # ── Fields table ────────────────────────────────────────────────────
    for i, h in enumerate(FIELD_HEADERS, start=1):
        ws.cell(row=row, column=i, value=h)
    _style_header_row(ws, row, len(FIELD_HEADERS))
    ws.freeze_panes = f'A{row + 1}'
    row += 1
    for order, field in enumerate(fields, start=1):
        values = [
            field.get('order', order),
            field.get('key', ''),
            field.get('label', ''),
            field.get('regex', ''),
            field.get('suffix') or '',
            'yes' if field.get('optional') else 'no',
            field.get('separator') or '',
            field.get('notes') or '',
        ]
        for c, v in enumerate(values, start=1):
            cell = ws.cell(row=row, column=c, value=v)
            cell.alignment = CENTER if c in (1, 5, 6, 7) else WRAP_TOP
            cell.border = CELL_BORDER
        row += 1

    # ── Lookup tables (aligned code/description columns) ────────────────
    for field in fields:
        items = _lookup_items(field.get('lookup'))
        if not items:
            continue
        row += 1
        title = f'CODE TABLE — {field.get("label") or field.get("key") or "Lookup"}'
        ws.cell(row=row, column=1, value=title).font = SECTION_FONT
        row += 1
        ws.cell(row=row, column=1, value='Code')
        ws.cell(row=row, column=2, value='Description')
        _style_header_row(ws, row, 2)
        ws.column_dimensions['B'].width = max(ws.column_dimensions['B'].width or 0, 26)
        row += 1
        for code, desc in items:
            c1 = ws.cell(row=row, column=1, value=code)
            c1.alignment = Alignment(horizontal='center', vertical='top')
            c1.border = CELL_BORDER
            c2 = ws.cell(row=row, column=2, value=desc)
            c2.alignment = WRAP_TOP
            c2.border = CELL_BORDER
            ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=len(FIELD_HEADERS))
            row += 1

    # ── Notes block ─────────────────────────────────────────────────────
    notes = str(definition.get('notes') or '').strip()
    if notes:
        row += 1
        ws.cell(row=row, column=1, value='NOTES').font = SECTION_FONT
        row += 1
        cell = ws.cell(row=row, column=1, value=notes)
        cell.alignment = WRAP_TOP
        cell.border = CELL_BORDER
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=len(FIELD_HEADERS))
        # Estimate a height that keeps the wrapped block readable.
        lines = sum(max(1, (len(l) // 110) + 1) for l in notes.splitlines())
        ws.row_dimensions[row].height = min(400, max(30, lines * 13))
    return ws.title


# ─── Symbols sheet writer ───────────────────────────────────────────────
def _collect_symbol_rows(project, sections: List[str]) -> List[LegendSymbolImage]:
    """Same tier logic as views.SymbolImagesListView, restricted to the
    exported sections and to rows with a real image file."""
    from ..views import _get_symbol_images_with_fallback, _normalise_symbol_name

    wanted = set(sections)
    rows: List[LegendSymbolImage] = []
    covered = set()
    # With a project: its own uploads + cross-project fallback. Without one
    # (global library): the same fallback tiers keyed on a nil UUID still
    # surface every uploaded/seeded picture (project=None rows included).
    fallback_key = str(project.project_id) if project is not None else '00000000-0000-0000-0000-000000000000'
    for r in _get_symbol_images_with_fallback(fallback_key):
        if r.section in wanted:
            rows.append(r)
            covered.add((r.section, _normalise_symbol_name(r.symbol_name)))
    for r in LegendSymbolImage.objects.filter(is_default=True, section__in=wanted).exclude(image_file=''):
        key = (r.section, _normalise_symbol_name(r.symbol_name))
        if key not in covered:
            covered.add(key)
            rows.append(r)
    return rows


def _read_image_bytes(row: LegendSymbolImage) -> Optional[bytes]:
    try:
        with row.image_file.open('rb') as fh:
            return fh.read()
    except Exception as exc:
        logger.warning('[LegendWorkbookExport] cannot read %s: %s', row.image_file.name, exc)
        return None


def _write_symbols_sheet(wb, section: str, rows: List[LegendSymbolImage], used_names: set) -> Optional[str]:
    label = SECTION_LABELS.get(section, section)
    base = label if label.lower().endswith('symbols') else f'{label} Symbols'
    ws = wb.create_sheet(_sheet_title(base, used_names))
    _set_widths(ws, [SYMBOL_COL_WIDTH, 48, 30])
    ws.cell(row=1, column=1, value='Symbol')
    ws.cell(row=1, column=2, value='Name')
    ws.cell(row=1, column=3, value='Section')
    _style_header_row(ws, 1, 3)
    ws.freeze_panes = 'A2'

    row_idx = 2
    embedded = 0
    for r in sorted(rows, key=lambda x: (x.section, x.symbol_name)):
        name_cell = ws.cell(row=row_idx, column=2, value=r.symbol_name)
        name_cell.alignment = Alignment(vertical='center', wrap_text=True)
        name_cell.border = CELL_BORDER
        sec_cell = ws.cell(row=row_idx, column=3, value=SECTION_LABELS.get(r.section, r.section))
        sec_cell.alignment = Alignment(vertical='center', wrap_text=True)
        sec_cell.border = CELL_BORDER
        sym_cell = ws.cell(row=row_idx, column=1)
        sym_cell.border = CELL_BORDER

        if (r.content_type or '').lower() == 'image/svg+xml' or str(r.image_file.name).lower().endswith('.svg'):
            sym_cell.value = '(SVG — see app)'
            sym_cell.alignment = CENTER
        else:
            data = _read_image_bytes(r)
            if data:
                try:
                    img = XlImage(io.BytesIO(data))
                    img.width = SYMBOL_IMG_PX
                    img.height = SYMBOL_IMG_PX
                    ws.add_image(img, f'A{row_idx}')
                    embedded += 1
                except Exception as exc:
                    logger.warning('[LegendWorkbookExport] embed failed for %s: %s', r.symbol_name, exc)
                    sym_cell.value = '(image unreadable)'
                    sym_cell.alignment = CENTER
            else:
                sym_cell.value = '(missing)'
                sym_cell.alignment = CENTER
        ws.row_dimensions[row_idx].height = SYMBOL_ROW_PT
        row_idx += 1

    if row_idx == 2:
        # No rows at all — drop the empty sheet.
        wb.remove(ws)
        return None
    return ws.title


# ─── Public API ─────────────────────────────────────────────────────────
def build_legend_workbook(*, user, project=None, section: Optional[str] = None,
                          legend_id: Optional[str] = None) -> Tuple[bytes, str]:
    """Build the workbook bytes + download filename.

    Scope: ``legend_id`` wins; otherwise all in-scope legends (optionally
    filtered to one ``section``). In-scope = the user's own legends plus the
    project pack legends when a project is given (same rule as the list
    endpoint).
    """
    from django.db.models import Q

    qs = PidCheckerV2LegendSheet.objects.filter(created_by=user)
    if project is not None:
        qs = PidCheckerV2LegendSheet.objects.filter(Q(created_by=user) | Q(project=project))
    if legend_id:
        qs = qs.filter(legend_id=legend_id)
    elif section:
        qs = qs.filter(section=section)
    legends = list(qs.order_by('section', 'name'))
    if not legends:
        raise ValueError('No legends found for the requested scope.')

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    used_names: set = set()

    # Sheet tabs read better without a shared pack prefix
    # ("ADNOC Offshore - Piping — Valve" → "Valve").
    names = [str(l.name or '') for l in legends]
    prefix = ''
    if len(names) > 1:
        common = None
        for sep in ('—', '-'):
            parts = [n.split(sep)[0] + sep for n in names if sep in n]
            if len(parts) == len(names) and len({p.strip() for p in parts}) == 1:
                common = parts[0]
                break
        if common:
            prefix = common

    symbol_sections: List[str] = []
    for legend in legends:
        sheet_base = str(legend.name or '')
        if prefix and sheet_base.startswith(prefix):
            sheet_base = sheet_base[len(prefix):].strip(' —-') or sheet_base
        _write_legend_sheet(wb, legend, used_names, sheet_base=sheet_base)
        if legend.section not in symbol_sections:
            symbol_sections.append(legend.section)

    symbols_embedded = 0
    if symbol_sections:
        by_section: Dict[str, List[LegendSymbolImage]] = {}
        for r in _collect_symbol_rows(project, symbol_sections):
            by_section.setdefault(r.section, []).append(r)
        for sec in symbol_sections:
            rows = by_section.get(sec) or []
            if not rows:
                continue
            title = _write_symbols_sheet(wb, sec, rows, used_names)
            if title:
                symbols_embedded += len(rows)

    # ── Metadata ────────────────────────────────────────────────────────
    meta = wb.create_sheet(_sheet_title('Metadata', used_names))
    _set_widths(meta, [26, 90])
    meta.cell(row=1, column=1, value='Property')
    meta.cell(row=1, column=2, value='Value')
    _style_header_row(meta, 1, 2)
    from django.utils import timezone
    meta_rows = [
        ('Project', getattr(project, 'name', None) or 'Global library'),
        ('Sections', ', '.join(SECTION_LABELS.get(s, s) for s in symbol_sections)),
        ('Legends', len(legends)),
        ('Symbol pictures', symbols_embedded),
        ('Exported at', timezone.now().isoformat()),
    ] + [(f'Legend #{i + 1}', l.name) for i, l in enumerate(legends)]
    for i, (k, v) in enumerate(meta_rows, start=2):
        meta.cell(row=i, column=1, value=k).border = CELL_BORDER
        c = meta.cell(row=i, column=2, value=str(v))
        c.alignment = WRAP_TOP
        c.border = CELL_BORDER

    buf = io.BytesIO()
    wb.save(buf)

    if legend_id and len(legends) == 1:
        stem = legends[0].name
    elif section:
        stem = f'{section}_all_legends'
    else:
        stem = f'{getattr(project, "name", None) or "global"}_legend_workbook'
    filename = re.sub(r'[\[\]:*?/\\"\s]+', '_', stem).strip('_') + '.xlsx'
    return buf.getvalue(), filename
