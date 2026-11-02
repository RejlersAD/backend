"""
Borouge/Linde Project Rules — P&ID Verification V2
==================================================

Project-level semantic rules evaluated against the Linde-layout extraction
(services/linde_layout) plus the uploaded Excel legend register
(services/legend_register). Each rule returns findings of the form

    {'issue_observed', 'action_required', 'severity', 'evidence',
     'item_texts': [...]}   # item_texts link the finding to extraction
                            # items (entity_id + pills in the UI)

Expected findings on Borouge 13-PP-152-20070 Rev Q (acceptance 29 Oct
2026): six queries + one minor, no errors:
  BRG-001 note 58 vs missing vacuum design case on BFW tie-ins   (query)
  BRG-002 branch 6"-52-BW-142101 insulation KA vs header KB      (query)
  BRG-003 equipment code FB not in legend register               (query)
  BRG-004 vent code AP5ET not in legend register                 (query)
  BRG-005 GFSD1 SD-F1 connector 1054 out of sequence             (query)
  BRG-006 decoking-air class A0JU01 on service 63 (expected 71)  (query, low confidence)
  BRG-007 service strings 'GFHSSB' vs 'GFHSSB1'                  (minor)

All mappings below are soft-coded.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# BRG-001: note number whose text requires full-vacuum design cases, and
# the fluid code(s) of the tie-in lines it governs (BFW = boiler feed
# water 'BW' on this project).
_VACUUM_NOTE_NO = 58
_VACUUM_FLUID_CODES = {'BW'}

# BRG-006: piping class → expected service/system number (legend LD-08);
# a line using the class on a different system is flagged, low confidence.
_CLASS_EXPECTED_SYSTEM = {'A0JU01': '71'}

# Vent-code tokens on the drawing body (e.g. AP7E, AP5ET).
_VENT_CODE_RE = re.compile(r'\bAP\d[A-Z0-9]{0,3}\b')


def _mk(rule_id, issue, action, severity, evidence='', item_texts=None, confidence=None):
    return {
        'rule_id': rule_id,
        'issue_observed': issue,
        'action_required': action,
        'severity': severity,
        'evidence': evidence,
        'item_texts': item_texts or [],
        'confidence': confidence,
    }


def _register_code_set(register_codes: Dict[str, Dict[str, str]],
                       category_hint: Optional[str] = None) -> set:
    out = set()
    for cat, table in register_codes.items():
        if category_hint and category_hint.lower() not in cat.lower():
            continue
        out.update(c.upper() for c in table)
    return out


def run_borouge_rules(layout: dict,
                      register_codes: Dict[str, Dict[str, str]],
                      register_docs: Dict[str, dict],
                      project=None) -> List[dict]:
    """Evaluate the Borouge project rules. `layout` is the output of
    linde_layout.read_linde_layout(); register_* come from
    legend_register. Returns [] when layout is None."""
    if not layout:
        return []
    out: List[dict] = []
    lines = layout.get('lines', [])
    notes = layout.get('notes', [])
    ce_rows = layout.get('connectors_ce', [])

    # ── BRG-001  note 58 vs missing vacuum design case on BFW tie-ins ────
    note58 = next((n for n in notes if n['number'] == _VACUUM_NOTE_NO), None)
    if note58 and not note58.get('deleted'):
        no_vacuum = []
        for l in lines:
            if l['parsed'].get('fluid_code') not in _VACUUM_FLUID_CODES:
                continue
            cases = l['design_cases']
            has_vacuum = any(
                isinstance(c.get('p_barg'), (int, float)) and c['p_barg'] < 0
                for c in cases
            )
            if cases and not has_vacuum:
                no_vacuum.append(l['text'])
        if no_vacuum:
            uniq = sorted(set(no_vacuum))
            out.append(_mk(
                'BRG-001',
                f"Note {_VACUUM_NOTE_NO} requires full-vacuum design cases on the "
                f"BFW tie-ins, but {len(uniq)} BW line(s) have no vacuum "
                f"(negative barg) design case: {'; '.join(u.strip() for u in uniq[:5])}",
                'Add a full-vacuum design case to the listed BFW tie-in lines, or '
                'document why note 58 does not apply to them.',
                'query',
                evidence='; '.join(uniq),
                item_texts=uniq,
            ))

    # ── BRG-002  insulation class inconsistent within system+fluid+class ─
    groups: Dict[tuple, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))
    for l in lines:
        p = l['parsed']
        if p.get('insulation'):
            key = (p.get('system'), p.get('fluid_code'), p.get('piping_class'))
            groups[key][p['insulation']].append(l['text'])
    # Insulation codes known to the register are legitimate choices (e.g.
    # 'I', 'P'); only a deviation with a code UNKNOWN to the legend is
    # flagged (e.g. branch 'KA' vs header 'KB' on 13-PP-152-20070).
    known_codes = _register_code_set(register_codes)
    for (system, fluid, pclass), by_insul in groups.items():
        if len(by_insul) < 2:
            continue
        # Majority class = header; others are branch deviations.
        majority = max(by_insul, key=lambda k: len(by_insul[k]))
        for insul, texts in by_insul.items():
            if insul == majority:
                continue
            if known_codes and insul.upper() in known_codes:
                continue  # a known, deliberate insulation class — no finding
            uniq = sorted(set(texts))
            out.append(_mk(
                'BRG-002',
                f'Insulation class {insul} on {"; ".join(t.strip() for t in uniq[:3])} '
                f'deviates from the {system}-{fluid}-{pclass} header class {majority}.',
                f'Confirm insulation class {insul} on the branch line(s); '
                f'the header run uses {majority}.',
                'query',
                evidence='; '.join(uniq),
                item_texts=uniq,
            ))

    # ── BRG-003  equipment code not in the legend register ───────────────
    if register_codes:
        eq_codes = _register_code_set(register_codes, 'equipment')
        if eq_codes:
            # Equipment tags come from the layout's equipment-ish tokens —
            # area-prefixed tags collected from the text layer.
            from .extraction import _EQUIPMENT_TYPES_AREA, _AREA_EQUIPMENT_CLASSES
            seen: Dict[str, str] = {}
            # layout lines don't carry equipment; scan connector/services
            # and raw text is not available here — caller passes equipment
            # tags via layout['_equipment_tags'] when available.
            for tag in layout.get('_equipment_tags', []):
                m = _EQUIPMENT_TYPES_AREA.match(tag)
                if m and m.group(1).upper() not in eq_codes:
                    seen[tag] = m.group(1).upper()
            for tag, code in sorted(seen.items()):
                out.append(_mk(
                    'BRG-003',
                    f"Equipment code '{code}' (tag {tag}) is not in the legend "
                    'register equipment designation codes.',
                    f"Confirm '{code}' with the legend (LD-01) or correct the tag.",
                    'query',
                    evidence=tag,
                    item_texts=[tag],
                ))

    # ── BRG-004  vent code not in the legend register ────────────────────
    if register_codes:
        from .legend_register import code_known_anywhere
        vent_tokens: Dict[str, list] = {}
        for l in layout.get('_text_lines', []):
            for m in _VENT_CODE_RE.finditer(l.get('text', '')):
                vent_tokens.setdefault(m.group(0), l)
        for code in sorted(vent_tokens):
            # Codes table first, then the whole workbook (AP7E lives in the
            # legend detail sheets; AP5ET is genuinely absent).
            if code_known_anywhere(project, code) is not False:
                continue
            l = vent_tokens[code]
            out.append(_mk(
                'BRG-004',
                f"Vent code '{code}' is not in the legend register.",
                f"Verify '{code}' against the legend vent/utility code table "
                '(LD-04); add it or correct the drawing.',
                'query',
                evidence=code,
                item_texts=[l['text']],
            ))

    # ── BRG-005  connector serial out of sequence within a series ────────
    # Series key = service + hookup prefix of the second service part
    # (e.g. 'GFSD1' + 'SD-F'), so valve-reference rows in the same service
    # column don't pollute the connector run.
    def _series_key(c):
        parts = [p.strip() for p in (c.get('service') or '').split('/')]
        if len(parts) < 2:
            return None
        m = re.match(r'^([A-Za-z]+-?[A-Za-z]*)', parts[1])
        return (parts[0], m.group(1) if m else parts[1])

    by_series: Dict[tuple, List[dict]] = defaultdict(list)
    for c in ce_rows:
        key = _series_key(c)
        if key and c.get('connector_no'):
            by_series[key].append(c)
    for svc_key, rows in by_series.items():
        svc = ' / '.join(svc_key)
        nums = []
        for r in rows:
            try:
                nums.append((int(r['connector_no']), r))
            except (TypeError, ValueError):
                continue
        if len(nums) < 3:
            continue
        vals = sorted(n for n, _ in nums)
        # Sequential run = max-min == count-1; find the outlier otherwise.
        lo, hi = vals[0], vals[-1]
        if hi - lo != len(vals) - 1:
            # Majority run: the value farthest from the consecutive cluster
            expected = set(range(lo, hi + 1))
            outliers = [n for n in vals if vals.count(n) > 1] or [
                n for n in vals if n not in range(max(lo, hi - len(vals) + 1), hi + 1)
            ]
            for n, r in nums:
                if n in outliers:
                    out.append(_mk(
                        'BRG-005',
                        f"Connector {svc} … / {r.get('drawing_no')} / {r['connector_no']} "
                        f'is out of sequence ({svc} connectors run {lo}–{hi}).',
                        'Verify the connector number against the C&E schedule; '
                        'sequential numbering is expected within a service.',
                        'query',
                        evidence=r['text'],
                        item_texts=[r['text']],
                    ))

    # ── BRG-006  piping class on unexpected service/system (low confidence)
    # ONE consolidated finding per (class, wrong system) pair.
    class_hits: Dict[tuple, List[str]] = defaultdict(list)
    for l in lines:
        p = l['parsed']
        pclass = p.get('piping_class')
        if pclass in _CLASS_EXPECTED_SYSTEM and p.get('system') != _CLASS_EXPECTED_SYSTEM[pclass]:
            class_hits[(pclass, p.get('system'))].append(l['text'])
    for (pclass, system), texts in sorted(class_hits.items()):
        uniq = sorted(set(texts))
        out.append(_mk(
            'BRG-006',
            f'{len(uniq)} line(s) use piping class {pclass} on service system '
            f'{system} — class {pclass} is expected on service '
            f'{_CLASS_EXPECTED_SYSTEM[pclass]} (per legend LD-08): '
            + '; '.join(u.strip() for u in uniq[:3]),
            'Confirm the piping class against the line class specification; '
            'possible copy-over from the decoking-air system.',
            'query',
            evidence='; '.join(uniq),
            item_texts=uniq,
            confidence='low',
        ))

    # ── BRG-007  service strings differing only by a trailing digit ──────
    # Compare ALL service-part tokens across C&E rows (e.g. 'GFHSSB' inside
    # 'EBS 00 / GFHSSB / A-EX' vs 'GFHSSB1' in the C rows).
    parts_seen = set()
    for c in ce_rows:
        for part in (c.get('service') or '').split('/'):
            part = part.strip()
            if part and re.search(r'[A-Za-z]', part):
                parts_seen.add(part)
    svc_list = sorted(parts_seen)
    for i, a in enumerate(svc_list):
        for b in svc_list[i + 1:]:
            if b.startswith(a) and b[len(a):].isdigit() and len(b) == len(a) + 1:
                out.append(_mk(
                    'BRG-007',
                    f"Service strings '{a}' and '{b}' differ only by a trailing "
                    'digit — inconsistent service naming across C&E connectors.',
                    f"Confirm whether '{a}' and '{b}' are the same service; "
                    'align the spelling with the legend.',
                    'minor',
                    evidence=f'{a} | {b}',
                ))
    return out
