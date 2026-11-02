"""
Linde/Borouge Layout Reader — P&ID Verification V2
==================================================

Reads the STRUCTURED text layer of Linde-format P&ID sheets (e.g. Borouge
13-PP-152-20070) line-by-line with bounding boxes — no OCR, no AI. OCR
damage like '1-£X-$300' is impossible because these values come from the
PDF's own text layer (source='vector').

Pure functions over a list of text-line dicts
    {'text', 'x', 'y', 'x1', 'y1', 'vertical'}   (coordinates in % of sheet)
produced by read_text_layer_lines() in extraction.py — host-testable
without Django or fitz.

Geometry constants are soft-coded below, derived from the real sheet
(29 Oct 2026 dump, 780 text lines):
  * Line labels:  12"-52- BW-140025- F2AU05- KB .   (spaces after hyphens,
    trailing dot, optional trailing note-ref number after insulation)
  * Design cases: numbered rows '1.' '2.' … in the block BELOW a
    horizontal label, or case COLUMNS below a vertical label; stop at the
    first repeated row number; 'STC' token near the case => STC flag.
    Row layout:  '1.'  20.0  65      (case no / p barg / t °C)
    Column layout: temp (int) and pressure (decimal) stacked above the
    case number in the same column, STC above that.
  * Notes column: x >= 80% — 'NN) TEXT' sheet notes (NN) DELETED when
    struck), 'N. TEXT' general notes, remark
    'FOR NOTES 1) TO 50) REFER TO E3-PP-152-01100'.
  * C&E connector rows: 'C : svc / hookup / code / drawing / conn-no',
    'E : …'.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Soft-coded patterns
# ---------------------------------------------------------------------------

# Line designation label (proven on the text layer 29 Oct 2026 — 31 labels
# / 29 unique on 13-PP-152-20070). Trailing dot belongs to the label.
# groups: size, system, fluid_code, serial, piping_class, insulation(+ref)
LINE_LABEL_RE = re.compile(
    r'^\s*(\d+(?:\s*mm)?"?)\s*-\s*(\d{2})\s*-\s*([A-Z0-9]{1,4})\s*-\s*'
    r'(\d{4,6})\s*-\s*([A-Z0-9]+)\s*-\s*(.*?)\s*\.?\s*$'
)
# Insulation group may carry a trailing reference number: 'I 50'.
_INSULATION_REF_RE = re.compile(r'^([A-Z0-9]+)(?:\s+(\d{1,3}))?$')

_CASE_NO_RE  = re.compile(r'^\d{1,2}\.$')           # '1.' … '4.'
_PRESSURE_RE = re.compile(r'^-?\d+\.\d+$')          # '224.0', '-0.5'
_TEMP_RE     = re.compile(r'^-?\d{2,3}$')           # '160', '65', '-10'
_STC         = 'STC'

_SHEET_NOTE_RE   = re.compile(r'^\s*(\d{2})\)\s*(.*)$')   # '51) TIE IN …'
_GENERAL_NOTE_RE = re.compile(r'^\s*(\d)\.\s+(\S.*)$')    # '1. CLASSIFICATION…'
_NOTE_REFER_RE   = re.compile(
    r'FOR\s+NOTES\s+(\d+)\)?\s+TO\s+(\d+)\)?\s+REFER\s+TO\s+(\S+)', re.IGNORECASE)

_CE_ROW_RE = re.compile(r'^\s*([CE])\s*:\s*(.+)$')
_BARE_INT_RE = re.compile(r'^\d{1,3}$')

# Drawing references are project/legend document numbers (13-PP-152-20070,
# 52-PP-152-148190-P2, E3-PP-152-01100) — NOT area-prefixed equipment tags
# (52-HE-1402), valve/nozzle numbers (52-02-01) or insulation spec codes
# (IP-B-B3-M3-L-3210AE), all of which also match the generic pattern.
_DRAWING_REF_RE = re.compile(
    r'\b((?:\d{2}|E\d)-[A-Z]{2}-\d{3}-\d{4,6}(?:-P\d)?)\b'
)
_VALVE_NO_RE = re.compile(r'^\d{2}-\d{2}-\d{2}$')
_INSUL_SPEC_RE = re.compile(r'^[A-Z]{2}-(?:[A-Z]\d?-)+\d{4}[A-Z]{0,2}$')

# Notes column / connector row bands (fractions of sheet, %).
NOTES_COLUMN_X_MIN = 80.0
# Design-case windows
_CASE_COL_X_TOL   = 0.9    # same-column tolerance (column layout)
_CASE_ROW_Y_TOL   = 0.45   # same-row tolerance (row layout)
_CASE_ROW_X_MAX   = 4.5    # how far right of the case number p/t may sit
_CASE_ABOVE_MAX   = 8.0    # how far above the case number p/t/STC may sit
_LABEL_BELOW_MAX  = 12.0   # case block max distance below its label
_LABEL_X_TOL      = 2.4    # label↔case horizontal alignment tolerance


def _pct_lines(raw_lines: List[dict]) -> List[dict]:
    """Normalize to {'text','x','y','x1','y1','vertical'} floats."""
    out = []
    for l in raw_lines:
        out.append({
            'text': l['text'], 'x': float(l['x']), 'y': float(l['y']),
            'x1': float(l.get('x1', l['x'])), 'y1': float(l.get('y1', l['y'])),
            'vertical': bool(l.get('vertical')),
        })
    return out


def parse_line_label(text: str) -> Optional[dict]:
    """Parse a Linde line-designation label into its positional fields."""
    m = LINE_LABEL_RE.match(text)
    if not m:
        return None
    size, system, fluid, serial, pclass, tail = m.groups()
    insulation, note_ref = None, None
    im = _INSULATION_REF_RE.match((tail or '').strip())
    if im:
        insulation = im.group(1) or None
        note_ref = int(im.group(2)) if im.group(2) else None
    return {
        'size': size.replace(' ', ''),
        'system': system,
        'fluid_code': fluid,
        'serial': serial,
        'piping_class': pclass,
        'insulation': insulation,
        'trailing_ref': note_ref,
    }


def _numeric(token: str) -> Optional[float]:
    try:
        return float(token)
    except (ValueError, TypeError):
        return None


def _extract_design_case(lines: List[dict], case: dict) -> dict:
    """Build one design case from its 'N.' anchor token and neighbours."""
    cx, cy = case['x'], case['y']
    no = int(case['text'].rstrip('.'))

    # Row layout: p/t to the right on the same row.
    row_tokens = [
        l for l in lines
        if l is not case and abs(l['y'] - cy) <= _CASE_ROW_Y_TOL
        and cx + 0.2 < l['x'] <= cx + _CASE_ROW_X_MAX
    ]
    # Column layout: p/t/STC stacked above in the same column.
    col_tokens = [
        l for l in lines
        if l is not case and abs(l['x'] - cx) <= _CASE_COL_X_TOL
        and cy - _CASE_ABOVE_MAX < l['y'] < cy - 0.1
    ]

    p_barg = t_c = None
    for pool in (row_tokens, col_tokens):
        decimals = sorted(
            (l for l in pool if _PRESSURE_RE.match(l['text'])),
            key=lambda l: abs(l['x'] - cx) + abs(l['y'] - cy),
        )
        ints = sorted(
            (l for l in pool if _TEMP_RE.match(l['text'])),
            key=lambda l: abs(l['x'] - cx) + abs(l['y'] - cy),
        )
        if decimals and ints:
            p_barg = _numeric(decimals[0]['text'])
            t_c = _numeric(ints[0]['text'])
            break
        if decimals and not p_barg:
            p_barg = _numeric(decimals[0]['text'])
        if ints and t_c is None:
            t_c = _numeric(ints[0]['text'])

    stc = any(
        l['text'] == _STC
        for l in col_tokens
    ) or any(l['text'] == _STC for l in row_tokens)

    return {
        'no': no, 'p_barg': p_barg, 't_c': t_c, 'stc': stc,
        'bbox': [case['x'], case['y'], case['x1'], case['y1']],
    }


def read_linde_layout(raw_lines: List[dict]) -> dict:
    """
    Parse one page's text-layer lines into Linde-layout structures.

    Returns {
      'lines': [{text, parsed, bbox, vertical, design_cases, note_refs}],
      'notes': [{number, text, deleted, referenced_at}],
      'general_notes': [...],
      'note_remark': {'from': 1, 'to': 50, 'drawing': 'E3-PP-152-01100'} | None,
      'connectors_ce': [...], 'drawing_references': [...],
      'unassigned_case_count': int,
    }
    """
    lines = _pct_lines(raw_lines)

    # ── 1. Line labels ───────────────────────────────────────────────────
    labels: List[dict] = []
    for l in lines:
        parsed = parse_line_label(l['text'])
        if parsed:
            labels.append({
                'text': l['text'], 'parsed': parsed,
                'bbox': [l['x'], l['y'], l['x1'], l['y1']],
                'vertical': l['vertical'], '_y': l['y'], '_x': l['x'],
            })

    # ── 2. Design cases ──────────────────────────────────────────────────
    case_tokens = [
        l for l in lines
        if _CASE_NO_RE.match(l['text']) and l['x'] < NOTES_COLUMN_X_MIN
    ]
    cases_per_label: Dict[int, List[dict]] = {i: [] for i in range(len(labels))}
    unassigned = 0
    for ct in case_tokens:
        # Owning label: nearest label ABOVE within window, column-aligned.
        best_i, best_d = None, None
        for i, lab in enumerate(labels):
            dy = ct['y'] - lab['_y']
            dx = abs(ct['x'] - lab['_x'])
            if 0.2 < dy <= _LABEL_BELOW_MAX and dx <= _LABEL_X_TOL:
                d = dy + dx
                if best_d is None or d < best_d:
                    best_i, best_d = i, d
        if best_i is None:
            unassigned += 1
            continue
        cases_per_label[best_i].append(_extract_design_case(lines, ct))

    for i, lab in enumerate(labels):
        cases = cases_per_label[i]
        # Row layout → order by y then x; column layout → order by x.
        cases.sort(key=lambda c: (c['bbox'][1], c['bbox'][0]))
        # Stop at first repeated row number (next label's block restart).
        seen, kept = set(), []
        for c in cases:
            if c['no'] in seen:
                break
            seen.add(c['no'])
            kept.append(c)
        lab['design_cases'] = kept

    # ── 3. Notes (notes column, x >= 80%) ────────────────────────────────
    col = sorted(
        (l for l in lines if l['x'] >= NOTES_COLUMN_X_MIN),
        key=lambda l: (l['y'], l['x']),
    )
    notes: List[dict] = []
    general_notes: List[dict] = []
    note_remark = None
    current = None
    for l in col:
        t = l['text']
        rm = _NOTE_REFER_RE.search(t)
        if rm:
            note_remark = {
                'from': int(rm.group(1)), 'to': int(rm.group(2)),
                'drawing': rm.group(3), 'text': t,
                'bbox': [l['x'], l['y'], l['x1'], l['y1']],
            }
            continue
        sm = _SHEET_NOTE_RE.match(t)
        if sm:
            body = sm.group(2).strip()
            current = {
                'number': int(sm.group(1)),
                'text': body,
                'deleted': body.upper() == 'DELETED',
                'referenced_at': [],
                'bbox': [l['x'], l['y'], l['x1'], l['y1']],
            }
            notes.append(current)
            continue
        gm = _GENERAL_NOTE_RE.match(t)
        if gm and l['y'] < 15:  # general notes live top of the notes column
            current = {
                'number': int(gm.group(1)),
                'text': gm.group(2).strip(),
                'deleted': False,
                'referenced_at': [],
                'bbox': [l['x'], l['y'], l['x1'], l['y1']],
            }
            general_notes.append(current)
            continue
        # Continuation line of the current note.
        if current is not None and t and not t.isupper() is False:
            current['text'] = (current['text'] + ' ' + t).strip()

    # ── 4. Note references on the drawing body ───────────────────────────
    # Known note numbers = sheet notes + general notes + the remark range
    # ("FOR NOTES 1) TO 50) REFER TO E3-PP-152-01100" — references like the
    # bare markers 26 / 29 / 50 point to notes living in the legend).
    known_note_numbers = {n['number'] for n in notes} | {n['number'] for n in general_notes}
    if note_remark:
        known_note_numbers |= set(range(note_remark['from'], note_remark['to'] + 1))
    note_ref_lines = [
        l for l in lines
        if _BARE_INT_RE.match(l['text']) and l['x'] < NOTES_COLUMN_X_MIN
        and int(l['text']) in known_note_numbers
    ]
    by_number: Dict[int, List[dict]] = {}
    for l in note_ref_lines:
        by_number.setdefault(int(l['text']), []).append(
            {'x': l['x'], 'y': l['y']}
        )
    for n in notes + general_notes:
        n['referenced_at'] = by_number.get(n['number'], [])
    # Markers referencing LEGEND notes (in the remark range but not sheet
    # notes — e.g. 26 / 29 / 50 → E3-PP-152-01100) attach to the remark.
    if note_remark:
        sheet_numbers = {n['number'] for n in notes} | {n['number'] for n in general_notes}
        note_remark['referenced_at'] = {
            str(num): pos
            for num, pos in sorted(by_number.items())
            if num not in sheet_numbers and num >= 10  # 1-digit bare tokens are grid labels
        }

    # ── 5. Connectors: C&E rows + drawing references ─────────────────────
    from .segmentation import _DRAWING_NO_RE  # local import avoids cycle
    connectors_ce: List[dict] = []
    drawing_refs: List[dict] = []
    for l in lines:
        if l['x'] >= NOTES_COLUMN_X_MIN:
            continue
        t = l['text']
        m_ce = _CE_ROW_RE.match(t)
        dm = _DRAWING_NO_RE.search(t)
        if m_ce:
            parts = [p.strip() for p in m_ce.group(2).split('/') if p.strip()]
            dno = next((p for p in parts if _DRAWING_NO_RE.fullmatch(p)), None)
            conn = None
            if dno:
                tail = parts[parts.index(dno) + 1:] if dno in parts else []
                conn = tail[0] if tail and tail[0].isdigit() else None
            connectors_ce.append({
                'text': t,
                'row_type': m_ce.group(1).upper(),  # C (cause) / E (effect)
                'service': ' / '.join(
                    p for p in parts
                    if p != dno and not p.isdigit()
                ) or None,
                'drawing_no': dno,
                'connector_no': conn,
                'direction': 'incoming' if m_ce.group(1).upper() == 'C' else 'outgoing',
                'bbox': [l['x'], l['y'], l['x1'], l['y1']],
            })
        elif l['y'] < 80:
            # Drawing reference: project/legend document numbers only —
            # equipment tags, valve numbers and insulation spec codes that
            # also match the generic pattern are excluded.
            for ref_m in _DRAWING_REF_RE.finditer(t):
                drawing_refs.append({
                    'text': t,
                    'drawing_no': ref_m.group(1),
                    'direction': (
                        'outgoing' if t.upper().startswith('TO ')
                        else 'incoming' if t.upper().startswith('FROM ')
                        else None
                    ),
                    'bbox': [l['x'], l['y'], l['x1'], l['y1']],
                })

    # Dedupe identical rows
    def _uniq(rows):
        seen, out = set(), []
        for r in rows:
            if r['text'] not in seen:
                seen.add(r['text'])
                out.append(r)
        return out

    result = {
        'lines': labels,
        'notes': notes,
        'general_notes': general_notes,
        'note_remark': note_remark,
        'connectors_ce': _uniq(connectors_ce),
        'drawing_references': _uniq(drawing_refs),
        'unassigned_case_count': unassigned,
    }

    # Equipment tags read straight from the text layer (area-prefixed
    # Borouge style, e.g. 52-HE-1402 / 52-PC-1401A/B/C) — used by the
    # classification-order step and the legend-code rule.
    from .extraction import _EQUIPMENT_TYPES_AREA, _AREA_EQUIPMENT_CLASSES
    eq_tags: List[str] = []
    for l in lines:
        for m in _EQUIPMENT_TYPES_AREA.finditer(l['text']):
            if m.group(1) in _AREA_EQUIPMENT_CLASSES:
                eq_tags.append(m.group(0))
    result['_equipment_tags'] = sorted(set(eq_tags))
    result['_text_lines'] = lines
    logger.info(
        '[LindeLayout] lines=%d (%d unique) cases=%d notes=%d general=%d '
        'ce=%d drawrefs=%d unassigned_cases=%d',
        len(labels),
        len({re.sub(r'\s+', '', l['text']).rstrip('.') for l in labels}),
        sum(len(l['design_cases']) for l in labels),
        len(notes), len(general_notes),
        len(result['connectors_ce']), len(result['drawing_references']),
        unassigned,
    )
    return result
