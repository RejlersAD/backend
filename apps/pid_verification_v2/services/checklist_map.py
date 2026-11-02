"""
Rule → checklist item map (AI-PID-02, spec §2.4)
================================================

Every rule id the engine can raise maps to at least one checklist item.
tests/test_checklist_map.py fails when a rule id has no item or an item
number does not exist in the template.

Cross-drawing codes (XCON/XLINE/XTAG/XREV) map to the same items as their
per-sheet equivalents — the project-scope view reports them there.
"""
from __future__ import annotations

RULE_TO_ITEMS = {
    # ── tags ────────────────────────────────────────────────────────────
    'TAG-001': ['0.5', '2.1'],
    'TAG-002': ['0.5'],
    'TAG-003': ['0.5'],
    'TAG-004': ['0.5'],
    # ── connectivity ────────────────────────────────────────────────────
    'CON-003': ['1.12'],
    # ── valves & equipment ──────────────────────────────────────────────
    'VLV-001': ['9.15'],
    'VLV-002': ['9.16'],
    'VLV-003': ['9.16'],
    'EQP-001': ['2.1'],
    'EQP-002': ['0.6', '1.1'],
    # ── line sizes / designations ───────────────────────────────────────
    'LSZ-001': ['9.1'],
    'LSZ-002': ['9.1'],
    'LSZ-003': ['9.16'],
    'LSZ-004': ['9.1'],
    'LSZ-005': ['9.6'],
    'LSZ-006': ['9.1'],
    'LSZ-007': ['9.1'],
    'LSZ-008': ['9.1'],
    'LSZ-009': ['9.1'],
    'LSZ-010': ['9.1'],
    'LSZ-011': ['9.8'],
    'LSZ-012': ['2.4'],
    'LN-001': ['9.1'],
    'LN-002': ['9.9'],
    # ── notes / holds / revision marks ──────────────────────────────────
    'NTS-001': ['1.5', '1.6'],
    'NTS-002': ['1.5', '1.11'],
    'RED-001': ['1.2'],
    # ── compressor rules ────────────────────────────────────────────────
    'CMP-001': ['3.1'],
    'CMP-002': ['3.4'],
    'CMP-003': ['3.5'],
    'CMP-004': ['3.6'],
    'CMP-005': ['3.7'],
    'CMP-006': ['3.8'],
    'CMP-007': ['3.2'],
    'CMP-008': ['3.3'],
    # ── Borouge project rules ───────────────────────────────────────────
    'BRG-001': ['1.3', '6.3'],
    'BRG-002': ['9.6', '9.9'],
    'BRG-003': ['0.6', '1.1'],
    'BRG-004': ['0.6', '1.1'],
    'BRG-005': ['10.12', '11.11'],
    'BRG-006': ['9.1'],
    'BRG-007': ['10.12', '11.11'],
    # ── cross-reference / cross-drawing ─────────────────────────────────
    'XREF-001': ['0.5'],            # removed from findings; map kept for old data
    'XREF-002': ['1.12'],
    'XREF-003': ['1.12'],
    'XCON-001': ['1.12'],
    'XLINE-001': ['9.9'],
    'XTAG-001': ['0.5', '2.1'],
    'XREV-001': ['1.2'],
}

# Soft-coded: template item numbers (AI-PID-02 has 125 items 0.1…13.4).
VALID_ITEM_NOS = frozenset(
    f'{s}.{i}'
    for s, n in ((0, 6), (1, 13), (2, 7), (3, 8), (4, 9), (5, 10), (6, 6),
                 (7, 2), (8, 2), (9, 18), (10, 14), (11, 14), (12, 12), (13, 4))
    for i in range(1, n + 1)
)
