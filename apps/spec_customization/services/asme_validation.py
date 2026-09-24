"""
Spec Customization — ASME Validation Service
=============================================

Cross-checks an extracted PipingClass against the valve_standards reference
database (ASME B16.34 pressure-temperature ratings):

  1. Resolve the class's material (material_grade / component
     material_standard strings) to a B16.34 MaterialGroup via soft-coded
     spec/grade matching.
  2. Parse the class's pressure_rating (e.g. 'CLASS 150') to a class number.
  3. For each extracted PT-table point, compare the spec pressure against the
     ASME rating table value at that temperature (exact match or 2-point
     linear interpolation per ASME para 2.1(f)).

Every knob lives in ASME_VALIDATION_CONFIG — tolerance, interpolation
enablement, group-resolution maps. No literal magic values below.
"""
from __future__ import annotations

import logging
import math
import re
from typing import Any, Dict, List, Optional, Tuple

from .config import ASME_VALIDATION_CONFIG as AVC

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Spec-string normalisation helpers
# ─────────────────────────────────────────────────────────────────────────────
def _norm_spec_text(text: str) -> str:
    """Normalise an ASTM spec token for comparison: uppercase, strip
    punctuation, collapse spaces. 'ASTM A-216 Gr. WCB' → 'A216 WCB'."""
    s = (text or '').upper()
    s = re.sub(r'\bASTM\b', ' ', s)
    s = re.sub(r'\bASME\b', ' ', s)
    s = re.sub(r'\bGR(?:ADE)?\b\.?', ' ', s)
    s = re.sub(r'[^A-Z0-9 ]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def _extract_spec_grade(text: str) -> Tuple[str, str]:
    """Split a material-standard string into (spec_no, grade) tokens.
    'ASTM A216 WCB' → ('A216', 'WCB'); 'A 105' → ('A105', '').
    The spec number must be followed by whitespace/end (not digits) so
    'BS 1504-161' doesn't yield a bogus '1504161' token."""
    norm = _norm_spec_text(text)
    m = re.search(r'\b(A|B)\s?(\d{3,4})(?!\d)\s*([A-Z]{1,6}\d{0,3}[A-Z]{0,2})?(?=$|\s|\b)', norm)
    if not m:
        return norm, ''
    spec = m.group(1) + m.group(2)
    grade = (m.group(3) or '').strip()
    return spec, grade


def _pressure_class_number(rating: str) -> Optional[int]:
    """'CLASS 150' / '150#' / 'CL300' → 150/300. None when unparseable."""
    if not rating:
        return None
    m = re.search(r'(\d{3,4})', str(rating))
    return int(m.group(1)) if m else None


# ─────────────────────────────────────────────────────────────────────────────
# Material-group resolution (soft-coded)
# ─────────────────────────────────────────────────────────────────────────────
def _candidate_material_strings(cls) -> List[Tuple[str, str]]:
    """Collect (text, product_form_hint) candidates from a PipingClass:
    the class material_grade plus the most common component
    material_standard strings, prioritised by frequency."""
    from collections import Counter
    candidates: List[Tuple[str, str]] = []
    if cls.material_grade:
        candidates.append((cls.material_grade, ''))
    counter: Counter = Counter()
    forms: Dict[str, str] = {}
    for comp in cls.components.all():
        ms = (comp.material_standard or '').strip()
        if not ms:
            continue
        counter[ms] += 1
        forms.setdefault(ms, _infer_product_form(comp))
    for text, _n in counter.most_common(AVC.get('max_material_candidates', 3)):
        candidates.append((text, forms.get(text, '')))
    return candidates


def _infer_product_form(comp) -> str:
    """Heuristic product-form hint from component type (soft-coded map)."""
    return AVC.get('component_product_form_map', {}).get(
        (comp.component_type or '').lower(), '')


def resolve_material_group(candidates: List[Tuple[str, str]]) -> Optional[Dict[str, Any]]:
    """Match candidate strings against MaterialGroupSpec rows. Returns
    {group_no, matched_spec, matched_grade, score} or None."""
    from apps.valve_standards.models import MaterialGroupSpec

    best: Optional[Dict[str, Any]] = None
    for text, form_hint in candidates:
        spec_tok, grade_tok = _extract_spec_grade(text)
        if not spec_tok:
            continue
        qs = MaterialGroupSpec.objects.select_related('material_group')
        if form_hint:
            qs = qs.filter(product_form=form_hint)
        for row in qs:
            row_spec = re.sub(r'\s+', '', (row.spec_no or '').upper())
            if row_spec != spec_tok:
                continue
            score = 1
            if grade_tok and row.grade and \
                    grade_tok == re.sub(r'\s+', '', row.grade.upper()):
                score = 2
            elif grade_tok and row.grade:
                continue  # grade conflict — different grade of same spec
            if best is None or score > best['score']:
                best = {
                    'group_no':      row.material_group.group_no,
                    'matched_spec':  row.spec_no,
                    'matched_grade': row.grade,
                    'product_form':  row.product_form,
                    'score':         score,
                }
        if best and best['score'] >= 2:
            break
    return best


# ─────────────────────────────────────────────────────────────────────────────
# Rating-table lookup + interpolation
# ─────────────────────────────────────────────────────────────────────────────
_TEMP_RANGE_RE = re.compile(r'(-?\d+(?:\.\d+)?)\s*(?:to|-)\s*(-?\d+(?:\.\d+)?)', re.IGNORECASE)
_TEMP_SINGLE_RE = re.compile(r'(-?\d+(?:\.\d+)?)')


def _temp_label_value(label: str) -> Optional[float]:
    """'-29 to 38' → 38.0 (upper bound); '500' → 500.0 (mirrors
    valve_standards.views.validate_rating behaviour)."""
    if not label:
        return None
    m = _TEMP_RANGE_RE.search(label)
    if m:
        try:
            return float(m.group(2))
        except (TypeError, ValueError):
            return None
    m = _TEMP_SINGLE_RE.search(label)
    if m:
        try:
            return float(m.group(1))
        except (TypeError, ValueError):
            return None
    return None


def _interpolate(group_no: str, class_number: int, temp_c: float) -> Optional[Dict[str, Any]]:
    """ASME pressure (bar) for (group, class) at temp_c. Exact row when a
    rating-table temperature equals temp_c (within tolerance); otherwise
    2-point linear interpolation between bracketing temperatures."""
    from apps.valve_standards.models import PressureTemperatureRating

    if not math.isfinite(temp_c):
        return None
    std_code   = AVC.get('standard_code', 'ASME_B16_34')
    section    = AVC.get('class_section', 'A')
    temp_tol   = float(AVC.get('temp_exact_tolerance_c', 0.51))

    rows = list(
        PressureTemperatureRating.objects
        .filter(material_group__group_no=group_no,
                material_group__standard__code=std_code,
                class_number=class_number,
                class_section=section,
                temp_unit='C', pressure_unit='bar')
        .exclude(pressure__isnull=True)
    )
    points: List[Tuple[float, float]] = []
    ranges: List[Tuple[float, float, float]] = []
    for r in rows:
        t = _temp_label_value(r.temp_label)
        if t is None or r.pressure is None:
            continue
        points.append((t, float(r.pressure)))
        interval = _TEMP_RANGE_RE.search(r.temp_label)
        if interval:
            lower, upper = float(interval.group(1)), float(interval.group(2))
            if lower <= upper:
                ranges.append((lower, upper, float(r.pressure)))
    if not points:
        return None
    points.sort(key=lambda p: p[0])

    # An explicit reference interval covers its printed bounds. A nearby row
    # or the final table value cannot establish a rating beyond those bounds.
    for lower, upper, pressure in ranges:
        if lower <= temp_c <= upper:
            return {'allowed_bar': pressure, 'method': 'exact', 'bracket': [lower, upper]}
    if temp_c < points[0][0] or temp_c > points[-1][0]:
        return None

    for t, p in points:
        if abs(t - temp_c) <= temp_tol:
            return {'allowed_bar': p, 'method': 'exact', 'bracket': [t, t]}

    if not AVC.get('interpolation_enabled', True):
        return None
    lower = [p for p in points if p[0] < temp_c]
    upper = [p for p in points if p[0] > temp_c]
    if not lower or not upper:
        return None
    t0, p0 = lower[-1]
    t1, p1 = upper[0]
    frac = (temp_c - t0) / (t1 - t0)
    return {'allowed_bar': round(p0 + frac * (p1 - p0), 3),
            'method': 'interpolated', 'bracket': [t0, t1]}


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────
def validate_piping_class(cls) -> Dict[str, Any]:
    """Validate a PipingClass's PT table against ASME ratings.

    Always returns a dict with a `status`; never raises — validation is an
    advisory overlay and must not break the class-detail endpoint.
    """
    cfg_enabled = AVC.get('enabled', True)
    labels = AVC.get('status_labels', {})
    if not cfg_enabled:
        return {'status': 'disabled',
                'label': labels.get('disabled', 'ASME validation disabled')}

    class_number = _pressure_class_number(cls.pressure_rating)
    if class_number is None:
        return {'status': 'skipped',
                'label': labels.get('skipped_no_class', 'No ASME class parsed'),
                'reason': 'pressure_rating_unparseable',
                'pressure_rating_raw': cls.pressure_rating or ''}

    match = resolve_material_group(_candidate_material_strings(cls))
    if match is None:
        return {'status': 'skipped',
                'label': labels.get('skipped_no_material', 'Material group not resolved'),
                'reason': 'material_group_unresolved',
                'pressure_class': class_number}

    pt_rows = cls.pt_rating_table or []
    tolerance_pct = float(AVC.get('tolerance_pct', 0.0))
    results: List[Dict[str, Any]] = []
    any_fail = False
    for row in pt_rows:
        try:
            temp_c = float(row.get('temperature_c'))
            spec_bar = float(row.get('pressure_bar_g'))
        except (TypeError, ValueError):
            continue
        hit = _interpolate(match['group_no'], class_number, temp_c)
        if hit is None:
            results.append({'temperature_c': temp_c, 'spec_bar_g': spec_bar,
                            'allowed_bar_g': None, 'method': 'no_data', 'ok': None})
            continue
        allowed = hit['allowed_bar']
        ok = spec_bar <= allowed * (1.0 + tolerance_pct / 100.0)
        any_fail = any_fail or not ok
        results.append({
            'temperature_c': temp_c,
            'spec_bar_g':    spec_bar,
            'allowed_bar_g': allowed,
            'delta_bar_g':   round(allowed - spec_bar, 3),
            'method':        hit['method'],
            'bracket_c':     hit['bracket'],
            'ok':            ok,
        })

    checked = [r for r in results if r.get('ok') is not None]
    if not checked:
        status = 'skipped'
        label = labels.get('skipped_no_pt', 'No PT data to validate')
        reason = 'pt_table_empty_or_no_rating_data'
    elif not any_fail and len(checked) != len(pt_rows):
        status = 'skipped'
        label = labels.get('skipped_incomplete_pt', 'Incomplete PT reference data')
        reason = 'pt_table_incomplete_rating_data'
    else:
        status = 'fail' if any_fail else 'pass'
        label = labels.get('fail' if any_fail else 'pass', '')
        reason = ''

    return {
        'status':          status,
        'label':           label,
        'reason':          reason,
        'standard':        AVC.get('standard_code', 'ASME_B16_34'),
        'material_group':  match['group_no'],
        'matched_spec':    match['matched_spec'],
        'matched_grade':   match['matched_grade'],
        'product_form':    match['product_form'],
        'pressure_class':  class_number,
        'points':          results,
        'points_checked':  len(checked),
        'points_failed':   sum(1 for r in checked if r['ok'] is False),
    }
