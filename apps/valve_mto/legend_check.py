"""
Valve MTO — legend ↔ drawing match check (§4 Stage 4).

check_legend_against_drawing(legend_definition, drawing) → {
    line_format: {matched, total, ratio, passed},
    classes:     {in_legend, total, ratio, passed},
    fluids:      {matched, total, matches},          # informational
    symbol_groups: [{group_id, count, suggested_type, evidence, covered}],
    passed: bool,
}

Legend definition JSON keys consumed (all optional — missing dims score 0):
    line_pattern     regex string for line numbers
    class_list       ['31170', ...]
    fluid_codes      ['BD', 'P', ...]
    symbol_entries   [{type, variant, image?, ...}]
    symbol_group_map {groupId: {type, variant, ...}}   (user mappings)

Thresholds per spec: line format ≥50%, classes ≥50%, every symbol group
with ≥5 members covered (mapped or marked leave-unidentified).
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

LINE_FORMAT_THRESHOLD = 0.5
CLASS_THRESHOLD = 0.5
MIN_GROUP_SIZE = 5


def _ratio(part, whole):
    return (part / whole) if whole else 0.0


def check_legend_against_drawing(definition: dict, drawing, symbol_groups: list | None = None) -> dict:
    parsed = drawing.parsed or {}
    labels = [l['text'] for l in parsed.get('line_labels', [])]
    drawing_classes = parsed.get('classes') or []
    if not drawing_classes:
        # classes come from the pattern pass; fall back to label scan.
        drawing_classes = sorted({
            seg for text in labels
            for seg in text.split('-') if seg.isalnum() and 3 <= len(seg) <= 7
        })

    result = {'passed': True}

    # ── Line format ───────────────────────────────────────────────────
    pattern = (definition or {}).get('line_pattern', '')
    matched = 0
    if pattern and labels:
        try:
            rx = re.compile(pattern)
            matched = sum(1 for t in labels if rx.search(t))
        except re.error:
            logger.warning('legend line_pattern invalid: %r', pattern)
    ratio = _ratio(matched, len(labels))
    line_ok = ratio >= LINE_FORMAT_THRESHOLD and bool(labels)
    result['line_format'] = {
        'matched': matched, 'total': len(labels),
        'ratio': round(ratio, 3), 'passed': line_ok,
    }

    # ── Classes ───────────────────────────────────────────────────────
    legend_classes = set((definition or {}).get('class_list', []))
    in_legend = [c for c in drawing_classes if c in legend_classes]
    cratio = _ratio(len(in_legend), len(drawing_classes))
    class_ok = cratio >= CLASS_THRESHOLD and bool(drawing_classes)
    result['classes'] = {
        'in_legend': len(in_legend), 'total': len(drawing_classes),
        'ratio': round(cratio, 3), 'passed': class_ok,
        'covered_classes': in_legend,
    }

    # ── Fluid codes (informational) ───────────────────────────────────
    legend_fluids = set((definition or {}).get('fluid_codes', []))
    drawing_text = '\n'.join(labels)
    fluid_matches = sorted(f for f in legend_fluids if re.search(rf'\b{re.escape(f)}\b', drawing_text))
    result['fluids'] = {
        'matched': len(fluid_matches), 'total': len(legend_fluids),
        'matches': fluid_matches,
    }

    # ── Symbol-group coverage ─────────────────────────────────────────
    group_map = (definition or {}).get('symbol_group_map', {})
    groups_out = []
    groups_ok = True
    for g in (symbol_groups or []):
        entry = group_map.get(g['group_id'])
        covered = bool(entry) and (
            entry.get('type') or entry.get('leave_unidentified')
        )
        big = g['count'] >= MIN_GROUP_SIZE
        if big and not covered:
            groups_ok = False
        groups_out.append({
            'group_id': g['group_id'],
            'count': g['count'],
            'suggested_type': (entry or {}).get('type') or g.get('suggested_type', ''),
            'evidence': g.get('evidence', []),
            'covered': covered,
            'requires_mapping': big and not covered,
        })
    result['symbol_groups'] = groups_out
    result['symbol_groups_passed'] = groups_ok

    result['passed'] = line_ok and class_ok and groups_ok
    return result
