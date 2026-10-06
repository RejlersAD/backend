"""Legend workbook extraction — text grid + anchored in-cell images per sheet.

The legend workbook stores symbols two ways (both handled):
  1. Drawing-anchored images (xl/drawings/*.xml anchors → xl/media/*.png)
  2. In-cell image functions whose cached value is the '#VALUE!' error

Output is a list of SheetGrid records — one per worksheet — carrying the
text grid and every image with its (row, col) anchor so downstream models
can pair a symbol with the description beside it.
"""
from __future__ import annotations

import io
import logging
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

import openpyxl

from . import config

logger = logging.getLogger('legend_models.workbook')

_NS = {
    'xdr': 'http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'rel': 'http://schemas.openxmlformats.org/package/2006/relationships',
}


@dataclass
class SheetImage:
    """One embedded image with its 0-based (row, col) anchor cell."""
    row: int
    col: int
    data: bytes
    ext: str


@dataclass
class SheetGrid:
    """One worksheet: title, soft-coded key, text grid, anchored images."""
    title: str
    key: str
    rows: list[list[str]] = field(default_factory=list)
    images: list[SheetImage] = field(default_factory=list)

    @property
    def n_cols(self) -> int:
        return max((len(r) for r in self.rows), default=0)

    def cell(self, row: int, col: int) -> str:
        if 0 <= row < len(self.rows) and 0 <= col < len(self.rows[row]):
            return self.rows[row][col]
        return ''

    def image_at(self, row: int, col: int) -> SheetImage | None:
        for img in self.images:
            if img.row == row and img.col == col:
                return img
        return None


def _rels_for(zf: zipfile.ZipFile, part: str) -> dict[str, str]:
    """Return {rId: target} for a part's .rels file (targets resolved to xl/...)."""
    if not part:
        return {}
    parent, name = posixpath.split(part)
    rels_name = posixpath.join(parent, '_rels', name + '.rels')
    out: dict[str, str] = {}
    if rels_name not in zf.namelist():
        return out
    root = ET.fromstring(zf.read(rels_name))
    for rel in root.findall('rel:Relationship', _NS):
        target = rel.attrib.get('Target', '')
        resolved = (target.lstrip('/') if target.startswith('/')
                    else posixpath.normpath(posixpath.join(parent, target)))
        out[rel.attrib['Id']] = resolved
    return out


def _sheet_part_map(zf: zipfile.ZipFile) -> dict[str, str]:
    """Map sheet title → worksheet xml part (xl/worksheets/sheetN.xml)."""
    wb_root = ET.fromstring(zf.read('xl/workbook.xml'))
    wb_rels = _rels_for(zf, 'xl/workbook.xml')
    ns_main = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    out: dict[str, str] = {}
    for sheet in wb_root.findall('.//m:sheet', ns_main):
        rid = sheet.attrib.get(f'{{{_NS["r"]}}}id')
        if rid in wb_rels:
            out[sheet.attrib['name']] = wb_rels[rid]
    return out


def _anchored_images(zf: zipfile.ZipFile, sheet_part: str) -> list[SheetImage]:
    """Parse a sheet's drawing part and return images with anchor cells."""
    sheet_rels = _rels_for(zf, sheet_part)
    images: list[SheetImage] = []
    drawing_parts = [t for t in sheet_rels.values() if '/drawings/' in t and t.endswith('.xml')]
    for drawing_part in drawing_parts:
        if drawing_part not in zf.namelist():
            continue
        drawing_rels = _rels_for(zf, drawing_part)
        root = ET.fromstring(zf.read(drawing_part))
        for anchor_tag in ('twoCellAnchor', 'oneCellAnchor'):
            for anchor in root.findall(f'xdr:{anchor_tag}', _NS):
                frm = anchor.find('xdr:from', _NS)
                blip = anchor.find('.//a:blip', _NS)
                if frm is None or blip is None:
                    continue
                embed = blip.attrib.get(f'{{{_NS["r"]}}}embed')
                media = drawing_rels.get(embed or '')
                if not media or media not in zf.namelist():
                    continue
                row_el, col_el = frm.find('xdr:row', _NS), frm.find('xdr:col', _NS)
                if row_el is None or col_el is None:
                    continue
                ext = Path(media).suffix.lstrip('.') or 'png'
                images.append(SheetImage(
                    row=int(row_el.text or 0), col=int(col_el.text or 0),
                    data=zf.read(media), ext=ext,
                ))
    return images


# ── Modern in-cell images (xl/richData) ─────────────────────────────────────
# Chain: sheet cell <c r="A2" vm="N">  →  metadata.xml futureMetadata bk[N-1]
#   →  <xlrd:rvb i="I">  →  rdrichvalue.xml rv[I] first <v> = R
#   →  richValueRel.xml rel[R] r:id  →  rels →  xl/media/imageK.png
_NS_RV = 'http://schemas.microsoft.com/office/spreadsheetml/2017/richdata'
_NS_RVREL = 'http://schemas.microsoft.com/office/spreadsheetml/2022/richvaluerel'
_NS_MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
_CELL_REF_RE = re.compile(r'^([A-Z]+)(\d+)$')


def _richdata_chain(zf: zipfile.ZipFile) -> list[str] | None:
    """Return the workbook-level list: vm index (1-based) → media part name.

    None when the workbook has no richData in-cell images.
    """
    names = set(zf.namelist())
    required = {'xl/metadata.xml', 'xl/richData/rdrichvalue.xml',
                'xl/richData/richValueRel.xml'}
    if not required.issubset(names):
        return None

    # rel index (0-based order in richValueRel.xml) → media part
    rel_root = ET.fromstring(zf.read('xl/richData/richValueRel.xml'))
    rel_ids = [rel.attrib.get(f'{{{_NS["r"]}}}id') for rel in rel_root]
    rel_targets = _rels_for(zf, 'xl/richData/richValueRel.xml')
    rel_media = [rel_targets.get(rid, '') for rid in rel_ids]

    # rv index → rel index (first <v> of each <rv>)
    rv_root = ET.fromstring(zf.read('xl/richData/rdrichvalue.xml'))
    rv_to_rel: list[int] = []
    for rv in rv_root.findall(f'{{{_NS_RV}}}rv'):
        first_v = rv.find(f'{{{_NS_RV}}}v')
        rv_to_rel.append(int(first_v.text or 0) if first_v is not None else -1)

    # vm index (1-based) → rv index, from futureMetadata bk order
    meta_root = ET.fromstring(zf.read('xl/metadata.xml'))
    vm_to_media: list[str] = []
    for fm in meta_root.findall(f'{{{_NS_MAIN}}}futureMetadata'):
        if fm.attrib.get('name') != 'XLRICHVALUE':
            continue
        for bk in fm.findall(f'{{{_NS_MAIN}}}bk'):
            rvb = bk.find(f'.//{{{_NS_RV}}}rvb')
            rv_idx = int(rvb.attrib.get('i', -1)) if rvb is not None else -1
            rel_idx = rv_to_rel[rv_idx] if 0 <= rv_idx < len(rv_to_rel) else -1
            vm_to_media.append(rel_media[rel_idx] if 0 <= rel_idx < len(rel_media) else '')
    return vm_to_media


def _cellref_to_rowcol(ref: str) -> tuple[int, int] | None:
    m = _CELL_REF_RE.match(ref)
    if not m:
        return None
    col = 0
    for ch in m.group(1):
        col = col * 26 + (ord(ch) - ord('A') + 1)
    return int(m.group(2)) - 1, col - 1  # 0-based (row, col)


def _incell_images(zf: zipfile.ZipFile, sheet_part: str,
                   vm_to_media: list[str] | None) -> list[SheetImage]:
    """Extract richData in-cell images for one sheet via vm cell attributes."""
    if not vm_to_media or not sheet_part or sheet_part not in zf.namelist():
        return []
    root = ET.fromstring(zf.read(sheet_part))
    images: list[SheetImage] = []
    for cell in root.iter(f'{{{_NS_MAIN}}}c'):
        vm = cell.attrib.get('vm')
        if not vm:
            continue
        idx = int(vm) - 1
        if not (0 <= idx < len(vm_to_media)):
            continue
        media = vm_to_media[idx]
        rc = _cellref_to_rowcol(cell.attrib.get('r', ''))
        if not rc or not media or media not in zf.namelist():
            continue
        images.append(SheetImage(row=rc[0], col=rc[1], data=zf.read(media),
                                 ext=Path(media).suffix.lstrip('.') or 'png'))
    return images


def extract_workbook(path: str | Path) -> list[SheetGrid]:
    """Extract every worksheet as a SheetGrid (text grid + anchored images)."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f'Legend workbook not found: {path}')

    wb = openpyxl.load_workbook(path, data_only=True)
    grids: list[SheetGrid] = []

    with zipfile.ZipFile(path) as zf:
        part_map = _sheet_part_map(zf)
        vm_to_media = _richdata_chain(zf)
        for ws in wb.worksheets:
            title = ws.title
            if not config.sheet_enabled(title):
                logger.info('Skipping disabled sheet %r', title)
                continue
            rows = [
                ['' if v is None else str(v).strip() for v in row]
                for row in ws.iter_rows(values_only=True)
            ]
            # Trim fully-empty trailing rows/cols raggedness is fine downstream
            while rows and not any(rows[-1]):
                rows.pop()
            sheet_part = part_map.get(title, '')
            images = (_anchored_images(zf, sheet_part)
                      + _incell_images(zf, sheet_part, vm_to_media))
            grids.append(SheetGrid(title=title, key=config.sheet_key(title), rows=rows, images=images))
            logger.info('Extracted %-32r rows=%-4d cols=%-3d images=%d',
                        title, len(rows), max((len(r) for r in rows), default=0), len(images))
    return grids


def save_extracted(grids: list[SheetGrid], out_dir: str | Path) -> None:
    """Persist extraction (images → per-sheet folder, grid → JSON) for inspection."""
    import json

    out = Path(out_dir)
    for grid in grids:
        sheet_dir = out / grid.key
        img_dir = sheet_dir / 'images'
        img_dir.mkdir(parents=True, exist_ok=True)
        manifest = []
        for i, img in enumerate(grid.images):
            fname = f'img_{i:03d}_r{img.row}_c{img.col}.{img.ext}'
            (img_dir / fname).write_bytes(img.data)
            manifest.append({'file': f'images/{fname}', 'row': img.row, 'col': img.col})
        (sheet_dir / 'grid.json').write_text(
            json.dumps({'title': grid.title, 'key': grid.key,
                        'rows': grid.rows, 'images': manifest}, indent=2),
            encoding='utf-8',
        )
