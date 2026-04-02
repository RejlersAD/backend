"""
pid_line_checks.py
==================
Soft-coded post-processing checks for the P&ID Verification pipeline.

This module is imported by the task/view that orchestrates a document run.
It adds two new check categories that complement the compiled rule engine:

  1. LSZ-DUP-001  Exact duplicate line numbers (critical)
  2. LSZ-DUP-002  Near-duplicate line numbers — sequence off by ≤ N (major)
  3. LSZ-DUP-003  String-level near-duplicate via Levenshtein (major)
  4. LSZ-ANG-001  Line number written at a vertical/angled orientation (minor)

All thresholds are soft-coded at the top of extraction_tagpos_new.py
(_NEAR_DUP_SEQ_THRESHOLD, _NEAR_DUP_LEVENSHTEIN_RATIO, _VERTICAL_THRESHOLD).

Usage
-----
Call `run_line_checks(extraction_result, file_path, page_index)` after the
existing `run_rules()` call, then merge the returned findings list into the
document's findings.

Example (inside your task / sync_process_fallback):

    from apps.pid_verification.services.pid_line_checks import run_line_checks

    extra_findings = run_line_checks(extraction, file_path, page_index)
    # extra_findings is a list of dicts compatible with PIDVFinding model fields
    # (same schema as RuleFinding namedtuple produced by rule_engine.run_rules)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Lazy import of helpers from the same package ─────────────────────────────
_TAGPOS_MOD = None


def _get_tagpos_module():
    global _TAGPOS_MOD
    if _TAGPOS_MOD is None:
        from apps.pid_verification.services import extraction_tagpos_new as m
        _TAGPOS_MOD = m
    return _TAGPOS_MOD


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def run_line_checks(
    extraction: Dict[str, Any],
    file_path: str,
    page_index: int,
) -> List[Dict[str, Any]]:
    """
    Run all supplementary line-number checks and return a flat list of findings.

    Parameters
    ----------
    extraction : dict
        The ExtractionResult dict returned by extraction.extract_drawing().
        Used for raw_text and any already-parsed line_sizes.
    file_path : str
        Absolute path to the PDF file on disk.
    page_index : int
        Zero-based page index to analyse.

    Returns
    -------
    list of dict — each dict is compatible with PIDVFinding fields:
        rule_id, severity, category, issue_observed,
        action_required, evidence, direction
    """
    findings: List[Dict[str, Any]] = []

    try:
        mod = _get_tagpos_module()

        # ── Step 1: Extract all line numbers (H + V + A) from the page ───────
        line_number_entries = mod.extract_line_numbers_from_page(
            file_path, page_index
        )

        raw_line_numbers = [e['raw'] for e in line_number_entries]
        logger.info(
            '[LineChecks] page %d: found %d line number candidates (%d unique)',
            page_index,
            len(raw_line_numbers),
            len(set(s.upper() for s in raw_line_numbers)),
        )

        # ── Step 2: Duplicate / near-duplicate detection ─────────────────────
        dup_issues = mod.detect_duplicate_line_numbers(raw_line_numbers)
        findings.extend(dup_issues)

        if dup_issues:
            logger.info(
                '[LineChecks] page %d: %d duplicate/near-dup issues raised',
                page_index, len(dup_issues),
            )

        # ── Step 3: Vertical / angled line number reporting ──────────────────
        for entry in line_number_entries:
            if entry.get('direction') in ('V', 'A'):
                findings.append({
                    'rule_id':        'LSZ-ANG-001',
                    'severity':       'minor',
                    'category':       'line_number_orientation',
                    'issue_observed': (
                        f"Line number '{entry['raw']}' is drawn at a "
                        f"{'vertical' if entry['direction'] == 'V' else 'angled'} "
                        f"orientation (detected at approx. "
                        f"{entry['x_pct']:.0f}%, {entry['y_pct']:.0f}% on drawing)."
                    ),
                    'action_required': (
                        "Line designations should be written horizontally per "
                        "ISO 10628-1 / AGES-GL-08-005.  Rotate text to 0° unless "
                        "an exception is documented on the legend sheet."
                    ),
                    'evidence':  [entry['raw']],
                    'direction': entry['direction'],
                })

        if findings:
            logger.info(
                '[LineChecks] page %d: total %d additional findings generated',
                page_index, len(findings),
            )

    except Exception as exc:
        logger.error('[LineChecks] run_line_checks failed: %s', exc, exc_info=True)

    return findings


def merge_into_findings(
    existing: List[Dict[str, Any]],
    extra: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Merge extra findings into existing, deduplicating on (rule_id, normalised evidence).
    Returns a new list — does not mutate either input.
    """
    seen = set()
    merged = list(existing)

    for f in existing:
        ev = tuple(sorted(str(e).upper() for e in f.get('evidence', [])))
        seen.add((f.get('rule_id', ''), ev))

    for f in extra:
        ev = tuple(sorted(str(e).upper() for e in f.get('evidence', [])))
        key = (f.get('rule_id', ''), ev)
        if key not in seen:
            seen.add(key)
            merged.append(f)

    return merged
