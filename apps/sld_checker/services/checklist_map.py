"""
Rule → checklist item map (AI-SLD-01)
=====================================

Every rule id the checker can raise maps to at least one checklist item
number of the AI-SLD-01 template. Soft-coded: extend here when rules or
the template change. Item numbers follow the AI-SLD-01 section scheme:

  0.x  document control / inputs        (0.1–0.6)
  1.x  drafting & symbology             (1.1–1.8)
  2.x  short-circuit withstand          (2.1–2.6)
  3.x  equipment ratings                (3.1–3.6)
  4.x  loading & demand                 (4.1–4.5)
  5.x  generation & essential supply    (5.1–5.5)
  6.x  motors, feeders & spares         (6.1–6.8)
  7.x  protection                       (7.1–7.5)
  8.x  load-list / equipment-list cross (8.1–8.5)
"""
from __future__ import annotations

RULE_TO_ITEMS = {
    # ── short-circuit ───────────────────────────────────────────────────
    'ELE-SC-01': ['2.1'],
    'ELE-SC-02': ['2.2'],
    'ELE-SC-03': ['2.3'],
    'ELE-SC-04': ['2.4'],
    # ── ratings ─────────────────────────────────────────────────────────
    'ELE-RAT-01': ['3.1', '3.2'],
    'ELE-TF-01': ['3.3'],
    # ── loading ─────────────────────────────────────────────────────────
    'ELE-LD-01': ['4.1', '4.2'],
    # ── generation / essential ──────────────────────────────────────────
    'ELE-GEN-01': ['5.1', '5.2'],
    'ELE-FP-01': ['5.4'],
    # ── motors / feeders / spares ───────────────────────────────────────
    'ELE-MOT-01': ['6.1'],
    'ELE-MOT-02': ['6.2'],
    'ELE-SPR-01': ['6.6'],
    'ELE-FDR-01': ['6.7', '1.4'],
    # ── protection ──────────────────────────────────────────────────────
    'ELE-PROT-01': ['7.1', '7.2'],
    # ── drafting ────────────────────────────────────────────────────────
    'ELE-V-01': ['1.5'],
    # ── core → finding mapping (load-list / drafting / equipment) ───────
    'CMP-LL-BRD': ['8.1'],
    'CMP-LL-KW': ['8.2'],
    'CMP-LL-TAG': ['8.3'],
    'CMP-EL-SLD': ['8.5'],
    'DRF-WRONG-AREA-CODE': ['1.1'],
    'DRF-DUPLICATE-TAG': ['1.2'],
    'DRF-REPEATED-NUMBERING': ['1.3'],
    'DRF-PLACEHOLDER-TAGS': ['1.6'],
}

# Soft-coded: item numbers that exist in AI-SLD-01 (54 items).
VALID_ITEM_NOS = frozenset(
    f'{s}.{i}'
    for s, n in ((0, 6), (1, 8), (2, 6), (3, 6), (4, 5), (5, 5), (6, 8),
                 (7, 5), (8, 5))
    for i in range(1, n + 1)
)
assert len(VALID_ITEM_NOS) == 54, len(VALID_ITEM_NOS)
