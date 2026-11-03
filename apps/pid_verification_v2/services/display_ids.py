"""
Finding display IDs (spec alignment, 2 Nov 2026)
================================================

Every finding is labelled by its **checklist serial number** (the AI-PID-02
item it maps to, e.g. ``9.1``) instead of a bare sequential ``F1, F2…``.

Soft-coded rules:
  * Serial source: ``report_meta.checklist_items`` (first item), falling
    back to ``RULE_TO_ITEMS[rule_id]`` for rows written before report_meta
    existed, falling back to ``F<sl_no>`` when the rule has no mapping.
  * Several findings on the same checklist item get ``-2``, ``-3``…
    suffixes in sl_no order, per sheet (drawing), so each label is unique
    on its sheet while staying traceable to the checklist row.
  * Cross-drawing findings (no drawing / drawing None) share one counter
    keyed on the document so their labels stay unique too.
"""
from __future__ import annotations

from typing import Dict, Iterable, Optional

from .checklist_map import RULE_TO_ITEMS


def _serial_for(f) -> Optional[str]:
    rm = getattr(f, 'report_meta', None) or {}
    items = rm.get('checklist_items') or RULE_TO_ITEMS.get(getattr(f, 'rule_id', ''), [])
    return items[0] if items else None


def checklist_serials(findings: Iterable) -> Dict[int, Optional[str]]:
    """{finding_pk: checklist serial or None} — None means the finding's
    rule maps to no checklist item (the UI hides those from the Findings
    list and the drawing canvas — 2 Nov 2026 alignment)."""
    return {f.id: _serial_for(f) for f in findings}


def assign_display_ids(findings: Iterable) -> Dict[int, str]:
    """{finding_pk: label} for an iterable of PIDVFinding rows."""
    serials = checklist_serials(findings)
    labels: Dict[int, str] = {}
    # counter per (sheet key, serial) — sheet key keeps suffixes local to
    # one drawing so '9.1' on sheet 3 and '9.1' on sheet 7 both exist.
    counts: Dict[tuple, int] = {}
    # Sort by drawing_id (no FK fetch — avoids N+1), then sl_no, then PK.
    ordered = sorted(
        findings,
        key=lambda f: (f.drawing_id or 0, f.sl_no or 0, f.id),
    )
    for f in ordered:
        serial = serials.get(f.id)
        if serial is None:
            labels[f.id] = f'F{f.sl_no}'
            continue
        sheet_key = getattr(f, 'drawing_id', None)
        key = (sheet_key, serial)
        counts[key] = counts.get(key, 0) + 1
        labels[f.id] = serial if counts[key] == 1 else f'{serial}-{counts[key]}'
    return labels
