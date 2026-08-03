"""
Non-TEFF Completeness Analyzer.

A purely additive layer that pushes bulk extraction toward 100% accuracy
WITHOUT modifying any core regex / vision logic. It does three things:

  1. **Coverage report** — per-batch and per-item % of important fields that
     are filled, plus per-column fill-rate so the user can see exactly which
     columns the pipeline struggles with.
  2. **Cross-row reconciliation** — for "constant-across-batch" columns
     (project_title, originator, plant, agreement_no …) the modal/most-common
     value across the batch is back-filled into rows that are still NA.
     Per-document unique columns (document_number, tag, …) are never touched.
  3. **Suggestion list** — actionable next steps to lift coverage further
     (e.g. "5 rows missing revision — re-run vision pass").

Every threshold, weight, and column class lives in COMPLETENESS_CONFIG
below. To tune behaviour, edit that dict — no code changes required.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any, Dict, Iterable, List, Tuple

from .master_index_service import get_columns, get_na_value

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SOFT-CODED configuration
# ---------------------------------------------------------------------------

COMPLETENESS_CONFIG: Dict[str, Any] = {
    'enabled': True,

    # ── Importance weights for coverage scoring. Columns absent from this
    # map default to weight=1. Columns explicitly set to 0 are ignored
    # (e.g. derived/auto fields that are always populated).
    'column_weights': {
        # Core identity (must-have)
        'document_number':  5,
        'document_title':   5,
        'document_type':    4,
        'revision':         4,
        'issue_date':       3,
        'document_subtype': 2,
        'discipline':       2,
        'tag':              2,
        # Project context
        'project_title':    2,
        'adnoc_project_no': 2,
        'originator':       1,
        'unit':             1,
        'area':             1,
        # Vendor / commercial
        'vendor_name':      1,
        'po_no':            1,
        'contractor_ref':   1,
        'vendor_ref':       1,
        # Always populated — exclude from score
        'sr_no':            0,
        'file_name':        0,
        'full_path':        0,
        'file_format':      0,
        'no_of_sheets':     0,
        'paper_size':       0,
        'source_folder':    0,
        'vendor_doc_flag':  0,
    },

    # ── Reconciliation: columns whose value is typically constant across an
    # entire batch (same project / same vendor package). When at least
    # ``min_modal_share`` of populated rows agree on a value, the modal
    # value is back-filled into NA rows. Columns NOT listed here are never
    # back-filled (e.g. document_number, tag).
    'reconcilable_columns': [
        'project_title', 'adnoc_project_no', 'project_location',
        'agreement_no', 'agreement_desc', 'plant', 'category',
        'originator', 'to', 'class_review', 'source_folder',
    ],

    # Modal value must appear in at least this fraction of populated rows
    # to be considered "the batch's value". Below this, do not backfill.
    'min_modal_share': 0.60,
    # And there must be at least this many populated rows to even compute it.
    'min_populated_rows': 2,

    # ── Coverage thresholds for the UI badge ("good"/"fair"/"poor").
    'coverage_thresholds': {
        'good': 0.85,   # ≥85 % weighted coverage = green
        'fair': 0.65,   # ≥65 % weighted coverage = amber
    },

    # Suggestion templates — soft-coded so we can change wording centrally.
    'suggestions': {
        'low_coverage':
            "Overall coverage is {pct:.0%}. Run a vision-AI re-pass on the "
            "{n} weakest items to fill missing fields.",
        'column_gap':
            "Column '{label}' is filled in only {pct:.0%} of items "
            "({filled}/{total}).",
        'reconcile_available':
            "{n} field(s) across {rows} row(s) can be auto-filled from the "
            "batch's most-common value. Click \u201cReconcile\u201d to apply.",
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_na(value: Any, na: str) -> bool:
    if value is None:
        return True
    s = str(value).strip()
    if not s:
        return True
    return s.upper() == na.upper()


def _scoring_columns() -> List[Dict[str, Any]]:
    """Return the schema columns that count toward the coverage score."""
    weights = COMPLETENESS_CONFIG['column_weights']
    out: List[Dict[str, Any]] = []
    for col in get_columns():
        w = weights.get(col['key'], 1)
        if w <= 0:
            continue
        out.append({**col, '_weight': w})
    return out


def _row_score(row: Dict[str, Any], na: str,
               cols: List[Dict[str, Any]]) -> Tuple[float, int, int]:
    """Return (weighted_pct, filled_count, total_count) for a single row."""
    total_w = sum(c['_weight'] for c in cols)
    if total_w <= 0:
        return 1.0, 0, 0
    got_w = 0
    filled = 0
    for c in cols:
        v = row.get(c['key'])
        if not _is_na(v, na):
            got_w  += c['_weight']
            filled += 1
    return (got_w / total_w), filled, len(cols)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def coverage_report(items: Iterable[Any]) -> Dict[str, Any]:
    """
    Compute a soft-coded completeness report for a batch.

    ``items`` is an iterable of NonTeffBatchItem (or anything with .item_id,
    .file_name, .fields). Pure read-only — never mutates.

    Returns:
        {
          "overall_pct":     0.78,
          "rating":          "fair",
          "items_total":     42,
          "items_full":      11,    # rows ≥ good threshold
          "items_weak":      9,     # rows < fair threshold
          "per_column":      [{key,label,filled,total,pct}, ...],
          "weakest_items":   [{item_id,file_name,pct,missing:[...]}, ...],
          "reconcile_plan":  [{column,value,share,fillable_rows}, ...],
          "suggestions":     ["...", "..."],
        }
    """
    na = get_na_value()
    cols = _scoring_columns()
    col_index = {c['key']: c for c in cols}

    items = list(items)
    n = len(items)
    if n == 0 or not COMPLETENESS_CONFIG.get('enabled'):
        return {
            'overall_pct': 0.0, 'rating': 'poor',
            'items_total': 0, 'items_full': 0, 'items_weak': 0,
            'per_column': [], 'weakest_items': [],
            'reconcile_plan': [], 'suggestions': [],
        }

    weights = COMPLETENESS_CONFIG['column_weights']
    th       = COMPLETENESS_CONFIG['coverage_thresholds']

    # ── per-row scoring ────────────────────────────────────────────────
    item_scores: List[Tuple[Any, float, List[str]]] = []
    overall_w_got, overall_w_total = 0.0, 0.0
    full_count = weak_count = 0
    column_filled = Counter()
    for it in items:
        fields = getattr(it, 'fields', None) or {}
        pct, filled, total = _row_score(fields, na, cols)
        missing = [c['key'] for c in cols if _is_na(fields.get(c['key']), na)]
        item_scores.append((it, pct, missing))
        overall_w_got   += pct * sum(c['_weight'] for c in cols)
        overall_w_total += sum(c['_weight'] for c in cols)
        if pct >= th['good']:
            full_count += 1
        elif pct < th['fair']:
            weak_count += 1
        for c in cols:
            if not _is_na(fields.get(c['key']), na):
                column_filled[c['key']] += 1

    overall_pct = (overall_w_got / overall_w_total) if overall_w_total else 0.0
    if overall_pct >= th['good']:
        rating = 'good'
    elif overall_pct >= th['fair']:
        rating = 'fair'
    else:
        rating = 'poor'

    # ── per-column report ──────────────────────────────────────────────
    per_column = []
    for c in cols:
        filled = column_filled.get(c['key'], 0)
        per_column.append({
            'key':    c['key'],
            'label':  c.get('label', c['key']),
            'weight': c['_weight'],
            'filled': filled,
            'total':  n,
            'pct':    (filled / n) if n else 0.0,
        })
    # Sort weakest columns first (most impactful gaps).
    per_column.sort(key=lambda r: (r['pct'], -r['weight']))

    # ── weakest items (bottom 10 by score) ─────────────────────────────
    item_scores.sort(key=lambda x: x[1])
    weakest_items = []
    for it, pct, missing in item_scores[:10]:
        weakest_items.append({
            'item_id':   str(getattr(it, 'item_id', '')),
            'file_name': getattr(it, 'file_name', ''),
            'pct':       round(pct, 4),
            'missing':   [
                {'key': k, 'label': col_index.get(k, {}).get('label', k)}
                for k in missing[:8]
            ],
        })

    # ── reconciliation plan (read-only preview) ────────────────────────
    plan = _build_reconcile_plan(items, na)

    # ── suggestions ────────────────────────────────────────────────────
    suggestions: List[str] = []
    sug = COMPLETENESS_CONFIG['suggestions']
    if overall_pct < th['fair']:
        weak_n = sum(1 for _, p, _ in item_scores if p < th['fair'])
        suggestions.append(sug['low_coverage'].format(pct=overall_pct, n=weak_n))
    for col in per_column[:3]:
        if col['pct'] < 0.50 and col['weight'] >= 2:
            suggestions.append(sug['column_gap'].format(
                label=col['label'], pct=col['pct'],
                filled=col['filled'], total=col['total']))
    if plan:
        total_cells = sum(p['fillable_rows'] for p in plan)
        suggestions.append(sug['reconcile_available'].format(
            n=len(plan), rows=total_cells))

    return {
        'overall_pct':    round(overall_pct, 4),
        'rating':         rating,
        'items_total':    n,
        'items_full':     full_count,
        'items_weak':     weak_count,
        'per_column':     per_column,
        'weakest_items':  weakest_items,
        'reconcile_plan': plan,
        'suggestions':    suggestions,
        'thresholds':     th,
    }


def _build_reconcile_plan(items: List[Any], na: str) -> List[Dict[str, Any]]:
    """Detect columns whose modal value can safely back-fill NA cells."""
    plan: List[Dict[str, Any]] = []
    columns = COMPLETENESS_CONFIG['reconcilable_columns']
    min_share    = float(COMPLETENESS_CONFIG['min_modal_share'])
    min_populate = int(COMPLETENESS_CONFIG['min_populated_rows'])

    for col_key in columns:
        values: List[str] = []
        empty_rows = 0
        for it in items:
            v = (getattr(it, 'fields', {}) or {}).get(col_key, '')
            if _is_na(v, na):
                empty_rows += 1
            else:
                values.append(str(v).strip())
        if not values or empty_rows == 0:
            continue
        if len(values) < min_populate:
            continue
        # Case-insensitive modal match, but preserve the canonical casing.
        norm = Counter(v.lower() for v in values)
        modal_lower, modal_count = norm.most_common(1)[0]
        share = modal_count / len(values)
        if share < min_share:
            continue
        # Pick the canonical (most-frequent original casing) version.
        canonical_counts = Counter(v for v in values if v.lower() == modal_lower)
        canonical_value = canonical_counts.most_common(1)[0][0]
        plan.append({
            'column':         col_key,
            'value':          canonical_value,
            'share':          round(share, 3),
            'populated_rows': len(values),
            'fillable_rows':  empty_rows,
        })
    return plan


def apply_reconciliation(items: List[Any]) -> Dict[str, Any]:
    """
    Apply the reconciliation plan to the supplied items in-place. Caller is
    responsible for persisting the items after this returns.

    Only NA cells are filled — never overwrites a populated value.
    Returns a summary {plan, applied_cells, touched_items}.
    """
    if not COMPLETENESS_CONFIG.get('enabled'):
        return {'plan': [], 'applied_cells': 0, 'touched_items': 0}

    na = get_na_value()
    plan = _build_reconcile_plan(items, na)
    if not plan:
        return {'plan': [], 'applied_cells': 0, 'touched_items': 0}

    touched_ids: set = set()
    applied = 0
    for entry in plan:
        col, val = entry['column'], entry['value']
        for it in items:
            fields = getattr(it, 'fields', None) or {}
            cur = fields.get(col, '')
            if _is_na(cur, na):
                fields[col] = val
                # If the model object stores fields separately (Django JSONField),
                # we need to re-assign so the ORM marks it dirty.
                try:
                    setattr(it, 'fields', fields)
                except Exception:
                    pass
                applied   += 1
                touched_ids.add(getattr(it, 'item_id', id(it)))
    return {
        'plan': plan,
        'applied_cells': applied,
        'touched_items': len(touched_ids),
        'touched_item_ids': touched_ids,
    }
