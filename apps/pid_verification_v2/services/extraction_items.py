"""
Extraction Items Persistence — P&ID Verification V2 (engine prompt §4)
======================================================================

Maps one page's pipeline output into PIDVExtractionItem rows.

Reader priority (29 Oct 2026 rework):
  1. VECTOR text layer — pages with an embedded text layer are read with
     PyMuPDF get_text('dict') line-by-line WITH bbox, and Linde-format
     sheets are fully structured by services/linde_layout (line labels +
     design cases + notes + connectors, all with real bboxes,
     source='vector'). OCR damage is impossible on this path.
  2. OCR fallback — only pages/regions without a text layer
     (source='ocr').

The drawing's `readability` ('vector' when the text layer covers the
drawing, else 'ocr') is persisted into drawing.metadata so the list
serializer shows it.

Classification order (engine prompt §5): legend equipment codes →
instrument letters → line numbers → connectors → notes. A tag claimed by
an earlier class is never double-counted (e.g. '13-VT-310' is Equipment
(VT = tower), never instrument 'VT-310').

Reprocess idempotency: rows for (document, page) are replaced each run.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Pages with at least this many text-layer lines are considered
# vector-covered (soft-coded).
_TEXT_LAYER_MIN_LINES = 50

_INSTRUMENT_TAG_RE = re.compile(r'^([A-Z]{1,5})-?(\d[\dA-Z./-]*)$')
_EQUIPMENT_CODE_RE = re.compile(r'^[A-Z]{1,4}')


def _bbox_from_pct(x_pct, y_pct) -> Optional[list]:
    """Single anchor point in % -> a small bbox around it (the extraction
    only produces anchor coordinates, not full bounding boxes)."""
    if not isinstance(x_pct, (int, float)) or not isinstance(y_pct, (int, float)):
        return None
    w, h = 6.0, 1.5  # soft-coded: typical single-line text extent in %
    return [round(max(x_pct - w / 2, 0), 2), round(max(y_pct - h / 2, 0), 2),
            round(min(x_pct + w / 2, 100), 2), round(min(y_pct + h / 2, 100), 2)]


def _bbox_lookup(text: str, text_lines: List[dict]) -> Optional[list]:
    """bbox of the first text-layer line containing this exact tag."""
    for l in text_lines:
        if text in l['text']:
            return [l['x'], l['y'], l['x1'], l['y1']]
    return None


def _category_table(codes: Dict[str, Dict[str, str]], hint: str) -> Dict[str, str]:
    """First Codes-sheet table whose category name contains `hint`
    (dash-insensitive — the register uses en dashes)."""
    norm = hint.lower().replace('–', '-').replace('—', '-')
    for category, table in codes.items():
        if norm in category.lower().replace('–', '-').replace('—', '-'):
            return table
    return {}


def _lookup(table: Dict[str, str], code: str) -> Optional[str]:
    """Case-sensitive first, then upper-cased (register codes are upper)."""
    if code in table:
        return table[code]
    return table.get(code.upper())


def _decode_piping_class(value: str, codes: Dict[str, Dict[str, str]]) -> Optional[dict]:
    """Split the LD-04 line-class key into its five positions — rating /
    corrosion allowance / material / service letter / service sequential
    number — and look up each character in ITS OWN table only (30 Oct 2026:
    decoding was keyed by code alone, so e.g. size '12' matched 'Pipe class
    – service sequential number: CATALYST…' and insulation 'KB' matched
    'Equipment designation code: BLOWER, FAN')."""
    m = re.match(r'^([A-Za-z])(\d)([A-Za-z])([A-Za-z])(\d{2})$', value)
    if not m:
        return None
    rating, ca, material, letter, serial = m.groups()
    slots = [
        ('pressure rating', rating.upper(), 'Pipe class - pressure rating', ''),
        ('corrosion allowance', ca, 'Pipe class - corrosion allowance', ' CA'),
        ('material code', material.upper(), 'Pipe class - material code', ''),
        ('service letter', letter.upper(), 'Pipe class - service letter', ''),
        ('service sequential number', serial, 'Pipe class - service sequential number', ''),
    ]
    parts = []
    for label, code, hint, suffix in slots:
        table = _category_table(codes, hint)
        desc = _lookup(table, code)
        if desc is None:
            return None  # partial decodes mislead more than they help
        parts.append({'position': label, 'code': code,
                      'description': f'{desc}{suffix}', 'category': hint})
    return {
        'code': value,
        'description': ' · '.join(p['description'] for p in parts),
        'category': 'Pipe class (LD-04)',
        'parts': parts,
    }


def _decode_insulation(value: str, codes: Dict[str, Dict[str, str]]) -> Optional[dict]:
    """Insulation type, with the trailing letter of a K* / L* / D* / FD*
    code read as the porous-layer class (LD-05): KB → 'HOT / ACOUSTIC
    INSULATION, porous layer B'."""
    table = _category_table(codes, 'Insulation type')
    if not table:
        return None
    desc = _lookup(table, value)
    if desc is not None:
        return {'code': value, 'description': desc, 'category': 'Insulation type'}
    v = value.upper()
    star_keys = sorted((k for k in table if k.endswith('*')), key=len, reverse=True)
    for key in star_keys:
        prefix = key[:-1]
        if v.startswith(prefix) and len(v) > len(prefix):
            layer = v[len(prefix):]
            return {
                'code': value,
                'description': f'{table[key]}, porous layer {layer}',
                'category': 'Insulation type',
            }
    return None


def _decode_with_register(parsed: dict, codes: Dict[str, Dict[str, str]]) -> dict:
    """Decode line fields against the legend register's Codes sheet:
    returns {field: {'code', 'description', 'category'}} for decoded
    fields only. Each field is looked up ONLY in its own category (30 Oct
    2026): size and serial are never decoded; system → 'System number';
    fluid_code → 'Line designation code'; piping_class → LD-04 split;
    insulation → 'Insulation type' (LD-05 porous-layer letter)."""
    decoded: Dict[str, dict] = {}
    if not codes:
        return decoded

    def _simple(field_key, hint):
        value = parsed.get(field_key)
        if not value:
            return
        v = str(value).strip().rstrip('"')
        desc = _lookup(_category_table(codes, hint), v)
        if desc is not None:
            decoded[field_key] = {'code': v, 'description': desc, 'category': hint}

    _simple('system', 'System number')
    _simple('fluid_code', 'Line designation code')

    pclass = str(parsed.get('piping_class') or '').strip()
    if pclass:
        hit = _decode_piping_class(pclass, codes)
        if hit:
            decoded['piping_class'] = hit

    insulation = str(parsed.get('insulation') or '').strip()
    if insulation:
        hit = _decode_insulation(insulation, codes)
        if hit:
            decoded['insulation'] = hit

    return decoded


def persist_extraction_items(doc, drawing, extraction: Dict[str, Any],
                             continuation_refs: Optional[List[dict]] = None,
                             legend_fields: Optional[List[dict]] = None,
                             file_path: Optional[str] = None,
                             layout_out: Optional[list] = None) -> int:
    """Replace (doc, page) extraction items with this run's data. Returns
    the number of rows created. Non-fatal by design at the call site."""
    from apps.pid_verification_v2.models import PIDVExtractionItem

    page = drawing.page_index

    # ── Readability: vector text layer vs OCR ────────────────────────────
    text_lines: List[dict] = []
    if file_path:
        from apps.pid_verification_v2.services.extraction import read_text_layer_lines
        text_lines = read_text_layer_lines(file_path, page)
    readability = 'vector' if len(text_lines) >= _TEXT_LAYER_MIN_LINES else 'ocr'
    md = drawing.metadata or {}
    if md.get('readability') != readability:
        md['readability'] = readability
        drawing.metadata = md
        drawing.save(update_fields=['metadata'])

    PIDVExtractionItem.objects.filter(document=doc, page=page).delete()
    items: List[PIDVExtractionItem] = []
    claimed_tags: set = set()  # classification-order claim registry

    # ── Legend register (for decoding + in_register) ─────────────────────
    register_codes: Dict[str, Dict[str, str]] = {}
    drawing_in_register = None
    project = doc.project if doc.project_id else None
    if project is not None:
        try:
            from apps.pid_verification_v2.services.legend_register import (
                get_register_codes, drawing_in_register as _dir,
            )
            register_codes = get_register_codes(project)
            drawing_in_register = _dir
        except Exception:
            logger.debug('[ExtractionItems] register read failed', exc_info=True)

    # ── Linde layout (vector sheets) ─────────────────────────────────────
    layout = None
    if readability == 'vector':
        try:
            from apps.pid_verification_v2.services.linde_layout import read_linde_layout
            layout = read_linde_layout(text_lines)
            if not (layout['lines'] or layout['notes'] or layout['connectors_ce']):
                layout = None
        except Exception:
            logger.warning('[ExtractionItems] Linde layout parse failed (non-fatal)', exc_info=True)
            layout = None

    if layout_out is not None:
        # Expose the parsed layout to the caller (project rules reuse it).
        layout_out.append(layout)

    def _mk(kind, text, parsed, page_, bbox, labels=1, flag=None, source=None):
        items.append(PIDVExtractionItem(
            document=doc, kind=kind, text=text, parsed=parsed, page=page_,
            bbox=bbox, labels=labels, flag=flag,
            source=source or (
                PIDVExtractionItem.Source.VECTOR if readability == 'vector'
                else PIDVExtractionItem.Source.OCR),
        ))

    if layout is not None:
        # ── Lines (with design cases + legend decode) ────────────────────
        norm_counts = Counter(
            re.sub(r'\s+', '', l['text']).rstrip('.') for l in layout['lines']
        )
        for l in layout['lines']:
            parsed = dict(l['parsed'])
            parsed['design_cases'] = [
                {k: c[k] for k in ('no', 'p_barg', 't_c', 'stc')}
                for c in l['design_cases']
            ]
            parsed['legend'] = _decode_with_register(parsed, register_codes)
            if l['parsed'].get('trailing_ref'):
                parsed['note_ref'] = l['parsed']['trailing_ref']
            norm = re.sub(r'\s+', '', l['text']).rstrip('.')
            _mk(PIDVExtractionItem.Kind.LINES, l['text'], parsed, page,
                l['bbox'], labels=norm_counts.get(norm, 1))
            claimed_tags.add(norm)

        # ── Connectors: C&E rows + drawing references (split) ────────────
        for c in layout['connectors_ce']:
            parsed = {
                'connector_type': 'cause_effect',
                'row_type': c['row_type'],
                'drawing_no': c['drawing_no'],
                'connector_no': c['connector_no'],
                'direction': c['direction'],
                'service': c['service'],
                'in_register': (
                    drawing_in_register(project, c['drawing_no'])
                    if c['drawing_no'] and drawing_in_register else None
                ),
            }
            _mk(PIDVExtractionItem.Kind.CONNECTORS, c['text'], parsed, page, c['bbox'])
        for r in layout['drawing_references']:
            parsed = {
                'connector_type': 'drawing_reference',
                'drawing_no': r['drawing_no'],
                'connector_no': None,
                'direction': r['direction'],
                'service': None,
                'in_register': (
                    drawing_in_register(project, r['drawing_no'])
                    if drawing_in_register else None
                ),
            }
            _mk(PIDVExtractionItem.Kind.CONNECTORS, r['text'], parsed, page, r['bbox'])

        # ── Notes (sheet + general + the legend-note remark) ─────────────
        for n in layout['notes'] + layout['general_notes']:
            _mk(PIDVExtractionItem.Kind.NOTES, n['text'], {
                'number': n['number'],
                'text': n['text'],
                'referenced_at': n['referenced_at'],
                'deleted': n['deleted'],
            }, page, n.get('bbox'),
                flag='deleted' if n['deleted'] else None)
        if layout.get('note_remark'):
            rm = layout['note_remark']
            _mk(PIDVExtractionItem.Kind.NOTES, rm['text'], {
                'number': None,
                'text': rm['text'],
                'referenced_at': rm.get('referenced_at', {}),
                'deleted': False,
                'remark_for_range': [rm['from'], rm['to']],
                'legend_drawing': rm['drawing'],
            }, page, rm.get('bbox'), flag='legend_note_remark')

    # ── Equipment (classification order: BEFORE instruments) ─────────────
    # Linde mode: equipment tags come from the text layer itself (layout
    # reader) — no OCR damage possible. Fallback: the merged-text regex.
    if layout is not None:
        eq_tag_list = layout.get('_equipment_tags', [])
        eq_counts = Counter()
        raw_text_all = '\n'.join(l['text'] for l in text_lines)
        for t in eq_tag_list:
            eq_counts[t] = raw_text_all.count(t)
        for tag in eq_tag_list:
            m = _EQUIPMENT_CODE_RE.match(tag.split('-')[1] if '-' in tag else tag)
            parsed = {
                'tag': tag,
                'code': m.group(0) if m else None,
                'description': None,   # description/data blocks are not part of
                'data_block': None,    # the current reader output
            }
            _mk(PIDVExtractionItem.Kind.EQUIPMENT, tag, parsed, page,
                _bbox_lookup(tag, text_lines), labels=eq_counts.get(tag, 1))
            claimed_tags.add(tag)
    else:
        equipment = extraction.get('equipment', []) or []
        eq_counts = Counter(str(e.get('tag', '')).strip() for e in equipment)
        for eq in equipment:
            tag = str(eq.get('tag', '')).strip()
            if not tag:
                continue
            m = _EQUIPMENT_CODE_RE.match(tag.split('-')[1] if '-' in tag else tag)
            parsed = {
                'tag': tag,
                'code': m.group(0) if m else None,
                'description': None,   # description/data blocks are not part of
                'data_block': None,    # the current reader output
            }
            bbox = _bbox_from_pct(eq.get('x_pct'), eq.get('y_pct')) or _bbox_lookup(tag, text_lines)
            _mk(PIDVExtractionItem.Kind.EQUIPMENT, tag, parsed, page, bbox,
                labels=eq_counts.get(tag, 1))
            claimed_tags.add(tag)

    # ── Instruments (never tags already claimed as equipment) ────────────
    instruments = extraction.get('instruments', []) or []
    instr_counts = Counter(str(i.get('tag', '')).strip() for i in instruments)
    for ins in instruments:
        tag = str(ins.get('tag', '')).strip()
        if not tag:
            continue
        # Classification order: skip tags that are the tail of an equipment
        # tag already claimed (e.g. 'VT-310' ⊂ '13-VT-310' — VT = tower).
        if any(tag != eq_tag and eq_tag.endswith('-' + tag) for eq_tag in claimed_tags):
            continue
        m = _INSTRUMENT_TAG_RE.match(tag)
        parsed = {
            'tag': tag,
            'function_letters': m.group(1) if m else None,
            'loop': m.group(2) if m else None,
            'hookup_type': None,  # not extracted by the reader
        }
        bbox = _bbox_from_pct(ins.get('x_pct'), ins.get('y_pct')) or _bbox_lookup(tag, text_lines)
        _mk(PIDVExtractionItem.Kind.INSTRUMENTS, tag, parsed, page, bbox,
            labels=instr_counts.get(tag, 1))

    # ── Generic fallback when the Linde layout did not apply ─────────────
    if layout is None:
        line_tags = extraction.get('line_tags', []) or []
        text_counts = Counter(str(lt.get('text', '')).strip() for lt in line_tags)
        for lt in line_tags:
            text = str(lt.get('text', '')).strip()
            if not text:
                continue
            parsed = {
                'size': lt.get('size') or None,
                'system': lt.get('area_code') or None,
                'fluid_code': lt.get('fluid_code') or None,
                'serial': lt.get('sequence_no') or None,
                'piping_class': lt.get('pipe_class') or None,
                'insulation': lt.get('insulation') or None,
                'design_cases': [],  # no design-case reader for this layout
                'legend': {},
            }
            parsed['legend'] = _decode_with_register(parsed, register_codes)
            _mk(PIDVExtractionItem.Kind.LINES, text, parsed, page,
                _bbox_from_pct(lt.get('x_pct'), lt.get('y_pct')) or _bbox_lookup(text, text_lines),
                labels=text_counts.get(text, 1))

        for ref in continuation_refs or []:
            text = str(ref.get('text', '')).strip()
            if not text:
                continue
            _mk(PIDVExtractionItem.Kind.CONNECTORS, text, {
                'connector_type': 'drawing_reference',
                'drawing_no': ref.get('drawing_no'),
                'connector_no': ref.get('connector_no'),
                'direction': ref.get('direction'),
                'service': ref.get('service'),
                'in_register': (
                    drawing_in_register(project, ref.get('drawing_no'))
                    if ref.get('drawing_no') and drawing_in_register else None
                ),
            }, page, None)

        notes = list(extraction.get('notes', []) or [])
        red_note_texts = [
            str(a.get('text', '')).strip()
            for a in (extraction.get('red_annotations', []) or [])
            if re.match(r'^\s*(?:NOTE\s*)?(\d{1,3})[\).\s:]', str(a.get('text', '')).strip(), re.I)
        ]
        all_notes = list(dict.fromkeys([str(n).strip() for n in notes] + red_note_texts))
        red_set = set(red_note_texts)
        note_counts = Counter(all_notes)
        for text in all_notes:
            if not text:
                continue
            m = re.match(r'^\s*(?:NOTE\s*)?(\d{1,3})[\).\s:]', text, re.I)
            _mk(PIDVExtractionItem.Kind.NOTES, text, {
                'number': int(m.group(1)) if m else None,
                'text': text,
                'referenced_at': [],
                'deleted': False,
            }, page, None, labels=note_counts.get(text, 1),
                flag='red_annotation' if text in red_set else None)

    if items:
        PIDVExtractionItem.objects.bulk_create(items, batch_size=500)
    logger.info(
        '[ExtractionItems] doc=%s page=%s: %d items (%d lines, %d instruments, '
        '%d equipment, %d connectors, %d notes) readability=%s',
        doc.document_id, page, len(items),
        sum(1 for i in items if i.kind == 'lines'),
        sum(1 for i in items if i.kind == 'instruments'),
        sum(1 for i in items if i.kind == 'equipment'),
        sum(1 for i in items if i.kind == 'connectors'),
        sum(1 for i in items if i.kind == 'notes'),
        readability,
    )
    return len(items)
