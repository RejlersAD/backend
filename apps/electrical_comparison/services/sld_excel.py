"""SLD Checker — workbook readers for the staged analysis flow (§3.2 / §3.3).

Two readers, both returning UI-ready read summaries plus normalized rows:

  read_equipment_list(path) -> {document, rev, sheets: [{name, status, rows, row_count}],
                                rewrites: [str], rows: [EqRow...]}
  read_load_list(path)      -> {document, rev, board, section, columns,
                                loads: [LoadRow...], notes: [str]}

Design rules baked in (from the alignment spec):
- Sheet status auto-detect: sheet name contains 'demolition', a title cell
  contains 'DEMOLITION', or a Remarks cell says 'Demolition'. Default 'new'.
- Tag rewrites:
    '285-U-558A/B'      -> two tags 558A + 558B (keeps qty)
    '285-SB-001A/B/C'   -> one tag per letter
    '285-U-505A MCC-D'  -> tag 285-U-505A, section MCC-D
    '285-U-503A – Extension' -> tag 285-U-503A, section Extension
    '285-JB-001-007'    -> ONE row, quantity 7 (never split)
- Rows without a tag are KEPT (never dropped) with tag ''.
- Load list: boards/sections come from 'PANEL TAG :' header rows; an empty
  tag cell falls back to a leading tag in the description (tagSource=
  'description') and is surfaced as a note.
"""
from __future__ import annotations

import re

import openpyxl

# ── shared tag helpers ────────────────────────────────────────────────────

_DASHES = dict.fromkeys(map(ord, '\u2013\u2014\u2010'), '-')


def norm_tag(value: str) -> str:
    """Uppercase, unify dashes, strip spaces — compare form only."""
    return re.sub(r'\s+', '', (value or '').translate(_DASHES).upper())


TAG_RE = re.compile(r'^\d{3}-[A-Z]{1,3}-[0-9A-Z]+$|^[LP]-?[0-9][0-9A-Z-]*$')
# A tag leading a description cell, e.g. 'P-3ED08 (SPARE)'.
DESC_TAG_RE = re.compile(r'^\s*([LP]-?\d[0-9A-Z-]*|\d{3}-[A-Z]{1,3}-[0-9A-Z]+)\b')

DOC_NO_RE = re.compile(r'Company Drg\.?\s*No\.?:\s*([A-Z0-9-]+)', re.I)
REV_RE = re.compile(r'\bRev(?:ision)?\.?\s*:?\s*([A-Z0-9]{1,4})\b', re.I)

# '285-U-558A/B' or '285-SB-001A/B/C' -> base + letters
MULTI_TAG_RE = re.compile(r'^(\d{3}-[A-Z]{1,3}-[0-9A-Z]*?)([A-Z](?:/[A-Z])+)$')
# '285-U-505A MCC-D' -> (tag, section)
TAG_SECTION_RE = re.compile(r'^(\d{3}-[A-Z]{1,3}-[0-9A-Z]+)\s+([A-Z]{2,3}(?:-[A-Z])?)$')
# '285-U-503A – Extension'
TAG_SUFFIX_WORD_RE = re.compile(r'^(\d{3}-[A-Z]{1,3}-[0-9A-Z]+)\s*[–—-]\s*([A-Za-z][A-Za-z ]*)$')


def _doc_meta(rows):
    doc_no, rev = '', ''
    for r in rows[:6]:
        for c in r:
            s = str(c or '')
            m = DOC_NO_RE.search(s)
            if m and not doc_no:
                doc_no = m.group(1)
            m = REV_RE.search(s)
            if m and not rev:
                rev = m.group(1)
    return doc_no, rev


def _find_header(rows, aliases=('tag number', 'tag no', 'tag')):
    """Locate the header row and column indexes for tag/description/qty/rating/remarks."""
    for i, r in enumerate(rows):
        cells = [str(c or '').strip().lower() for c in r]
        if any(c in aliases for c in cells):
            def col(*keys):
                for j, c in enumerate(cells):
                    if any(k in c for k in keys):
                        return j
                return None
            return i, {
                'tag': col('tag'),
                'description': col('description'),
                'qty': col('quantity', 'qty'),
                'rating': col('rating'),
                'remarks': col('remark'),
            }
    return None, None


def _sheet_status(name, rows):
    if 'demolition' in name.lower():
        return 'demolition'
    for r in rows[:6]:
        for c in r:
            if 'demolition' in str(c or '').lower():
                return 'demolition'
    return 'new'


def _rewrite_tag(raw_tag):
    """Return (norm_tags, base_tag, section, note). One entry per spec rule."""
    raw = (raw_tag or '').strip()
    t = norm_tag(raw)
    if not t or t in {'-', '—'}:
        return [], '', '', None
    # Section suffixes must be read from the RAW tag (spaces still present).
    m = TAG_SECTION_RE.match(raw.upper())
    if m:
        base = norm_tag(m.group(1))
        return [base], base, m.group(2), f'{raw} → {base} · section {m.group(2)}'
    m = TAG_SUFFIX_WORD_RE.match(raw)
    if m:
        base = norm_tag(m.group(1))
        return [base], base, m.group(2).strip(), f'{raw} → {base} · section {m.group(2).strip()}'
    m = MULTI_TAG_RE.match(t)
    if m:
        base, letters = m.group(1), m.group(2).split('/')
        tags = [f'{base}{letter}' for letter in letters]
        short = ', '.join(t.rsplit('-', 1)[-1] for t in tags)
        return tags, base, None, f'{raw} → {short if len(tags) <= 2 else str(len(tags)) + " tags"}'
    return [t], t, None, None


def read_equipment_list(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheets_out, all_rows, rewrites = [], [], []
    for name in wb.sheetnames:
        ws = wb[name]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        doc_no, rev = _doc_meta(rows)
        status = _sheet_status(name, rows)
        hrow, cols = _find_header(rows)
        sheet_rows = []
        source_rows = 0
        if cols:
            for r in rows[hrow + 1:]:
                raw_tag = str(r[cols['tag']] or '').strip() if cols['tag'] is not None else ''
                desc = str(r[cols['description']] or '').strip() if cols['description'] is not None else ''
                if not raw_tag and not desc:
                    continue
                if desc.lower().startswith('total'):
                    continue
                source_rows += 1
                qty_raw = r[cols['qty']] if cols['qty'] is not None else None
                try:
                    qty = int(float(qty_raw)) if qty_raw not in (None, '') else 1
                except (TypeError, ValueError):
                    qty = 1
                rating = str(r[cols['rating']] or '').strip() if cols['rating'] is not None else ''
                remarks = str(r[cols['remarks']] or '').strip() if cols['remarks'] is not None else ''
                row_status = status
                if status == 'new' and 'demolition' in remarks.lower():
                    row_status = 'demolition'
                norm_tags, base, section, note = _rewrite_tag(raw_tag)
                if note:
                    rewrites.append(note)
                if not norm_tags and (raw_tag or desc):
                    # kept row without a usable tag
                    sheet_rows.append({
                        'tag': raw_tag, 'norm': [], 'sheet': name, 'listStatus': row_status,
                        'description': desc, 'qty': qty, 'rating': rating, 'section': section or '',
                    })
                    continue
                for n in norm_tags:
                    sheet_rows.append({
                        'tag': n, 'norm': [n], 'sheet': name, 'listStatus': row_status,
                        'description': desc, 'qty': qty, 'rating': rating, 'section': section or '',
                        **({'fromTag': raw_tag.strip()} if len(norm_tags) > 1 or (section and raw_tag.strip() != n) else {}),
                    })
        no_tag = sum(1 for r in sheet_rows if not r['norm'])
        sheets_out.append({
            'name': name, 'status': status,
            'row_count': source_rows,           # rows as drawn in the workbook
            'tag_count': len(sheet_rows) - no_tag,  # tags after splitting rewrites
            'no_tag_rows': no_tag,
        })
        all_rows.extend(sheet_rows)
    if any(s['no_tag_rows'] for s in sheets_out):
        total_no_tag = sum(s['no_tag_rows'] for s in sheets_out)
        rewrites.append(f'{total_no_tag} Demolition rows have no tag' if any(s['status'] == 'demolition' and s['no_tag_rows'] for s in sheets_out) else f'{total_no_tag} rows have no tag')
    # de-duplicate rewrite notes (same rewrite can appear on both sheets)
    seen, unique_rewrites = set(), []
    for note in rewrites:
        if note not in seen:
            seen.add(note)
            unique_rewrites.append(note)
    return {
        'document': doc_no, 'rev': rev,
        'sheets': sheets_out, 'rewrites': unique_rewrites, 'rows': all_rows,
    }


PANEL_HEADER_RE = re.compile(r'PANEL\s*TAG\s*:?\s*([0-9A-Z-]+)', re.I)
PANEL_SECTION_RE = re.compile(r'\(([A-Z]{2,3}(?:-[A-Z])?)\)')
RATING_KEYS = ('equipment rating', 'rating (b)', 'rated kw')


def read_load_list(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    doc_no, rev = _doc_meta(rows)
    hrow, cols = None, None
    for i, r in enumerate(rows):
        cells = [str(c or '').strip().lower() for c in r]
        if 'tag number' in cells or 'tag no' in cells or 'tag' in cells:
            def col(*keys):
                for j, c in enumerate(cells):
                    if any(k in c for k in keys):
                        return j
                return None
            cols = {
                'tag': col('tag'),
                'description': col('description'),
                'rated_kw': None,
                'duty': col('load duty'),
            }
            for j, c in enumerate(cells):
                if any(k in c for k in RATING_KEYS):
                    cols['rated_kw'] = j
            hrow = i
            break
    loads, notes, boards = [], [], []
    board, section = '', ''
    if cols:
        for r in rows[hrow + 1:]:
            first = ' '.join(str(c or '') for c in r[:3])
            pm = PANEL_HEADER_RE.search(first)
            if pm:
                board = norm_tag(pm.group(1))
                sm = PANEL_SECTION_RE.search(first)
                if sm:
                    section = sm.group(1)
                boards.append(board)
                continue
            raw_tag = str(r[cols['tag']] or '').strip() if cols['tag'] is not None else ''
            desc = str(r[cols['description']] or '').strip() if cols['description'] is not None else ''
            if not raw_tag and not desc:
                continue
            if desc.lower().startswith(('total', 'note:')) or 'plant load summary' in desc.lower():
                continue
            tag_source = 'tag-cell'
            tag = norm_tag(raw_tag)
            if not tag:
                m = DESC_TAG_RE.match(desc)
                if m:
                    tag = norm_tag(m.group(1))
                    tag_source = 'description'
                    notes.append(f'1 tag taken from the description cell: {desc.split("(")[0].strip()} (SPARE)' if '(SPARE)' in desc.upper() else f'1 tag taken from the description cell: {m.group(1)}')
            kw_raw = r[cols['rated_kw']] if cols['rated_kw'] is not None else None
            try:
                rated_kw = float(kw_raw) if kw_raw not in (None, '') else 0.0
            except (TypeError, ValueError):
                rated_kw = 0.0
            loads.append({
                'tag': tag, 'tagSource': tag_source, 'board': board, 'section': section,
                'description': desc, 'ratedKw': rated_kw,
                'duty': str(r[cols['duty']] or '').strip() if cols['duty'] is not None else '',
            })
    return {
        'document': doc_no, 'rev': rev,
        'board': boards[0] if boards else '', 'boards': boards,
        'section': section,
        'columns': ['Tag', 'Description', 'Rated kW (B)', 'Load duty'],
        'loads': loads, 'notes': notes,
    }
