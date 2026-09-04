"""Cross-check a list of P&ID equipment tags against a master Equipment
List (uploaded Excel).

Produces three buckets of findings per tag:

  * MISSING_ON_PID     — item is in the Equipment List but no matching
                          tag was seen on the drawing.
  * EXTRA_ON_PID       — tag was extracted from the drawing but does not
                          appear in the Equipment List.
  * MATCH              — same tag present on both sides.

An optional AI pass batches the MISSING and EXTRA sets together and asks
Claude/OpenAI to correlate them (typo? partial match? OCR miss?) and
suggest what to do.
"""
from __future__ import annotations

import json
import logging
import re
from difflib import SequenceMatcher
from typing import Optional

from .equipment_vision_extractor import EQUIPMENT_ATTRIBUTE_KEYS, EQUIPMENT_ATTRIBUTE_LABELS

logger = logging.getLogger(__name__)


# ─── Soft-coded config ────────────────────────────────────────────────
SEVERITY_INFO = 'info'
SEVERITY_WARNING = 'warning'
SEVERITY_ERROR = 'error'

# Attribute-level severity (overall per matched tag)
SEVERITY_OK = 'ok'
SEVERITY_MINOR = 'minor'
SEVERITY_CRITICAL = 'critical'

# Attributes that are safety-critical → any real mismatch is CRITICAL
CRITICAL_ATTRIBUTE_KEYS = frozenset({
    'op_pressure',
    'design_pressure_min',
    'design_pressure_max',
    'op_temp_min',
    'op_temp_max',
    'design_temp_min',
    'design_temp_max',
    'material_shell',
    'material_internal',
    'trim',
})

FINDING_MISSING_ON_PID = 'missing_on_pid'
FINDING_EXTRA_ON_PID   = 'extra_on_pid'
FINDING_MATCH          = 'match'

# Attribute-comparison per-cell statuses
ATTR_STATUS_MATCH        = 'match'
ATTR_STATUS_MISMATCH     = 'mismatch'
ATTR_STATUS_MISSING_PID  = 'missing_pid'
ATTR_STATUS_MISSING_XLS  = 'missing_excel'
ATTR_STATUS_BOTH_EMPTY   = 'both_empty'

# Excel-side field name lookup per attribute key (Equipment List column keys)
EL_ATTRIBUTE_FIELD_MAP = {
    'nominal_capacity':     'nominal_capacity',
    'length_tt':            'length_tt',
    'diameter_id':          'diameter_id',
    'op_pressure':          'op_pressure',
    'design_pressure_min':  'design_p_min',
    'design_pressure_max':  'design_p_max',
    'op_temp_min':          'op_temp',
    'op_temp_max':          'op_temp',
    'design_temp_min':      'design_t_min',
    'design_temp_max':      'design_t_max',
    'material_shell':       'material_shell',
    'material_internal':    'material_internal',
    'trim':                 'trim',
}

AI_MAX_TAGS_PER_SIDE = 40
AI_MAX_TOKENS = 2048
AI_TEMPERATURE = 0.0

# Fuzzy (deterministic) pairing of MISSING ↔ EXTRA before AI. Catches OCR /
# hyphen / single-character misreads like "V-803-TF" vs "V-803-TE" without
# spending any BYOK tokens — this is what makes a near-miss reading resolve
# to an actual Match instead of being permanently stuck as "Missing on P&ID"
# with only an annotation nobody notices.
FUZZY_MATCH_ENABLED         = True
FUZZY_MATCH_THRESHOLD       = 0.85     # SequenceMatcher ratio
FUZZY_ALPHA_MUST_MATCH      = True     # item-symbol prefix letters must be identical
FUZZY_NUMERIC_MUST_MATCH    = True     # sequence-number digits must be identical

# AI-suggested pair promotion — when the AI returns should_match=true with
# confidence high|medium, the pair is promoted to a real MATCH row (marked
# ai_probable_match=True) instead of staying Missing/Extra forever with
# just an annotation.
AI_PROMOTE_CONFIDENCES = ('high', 'medium')

# Attribute-comparison AI judge
AI_ATTR_MAX_TAGS_PER_BATCH = 12
AI_ATTR_MAX_TOKENS = 3000

AI_SYSTEM_PROMPT = (
    "You are a piping engineering QA reviewer. You are given two lists of "
    "equipment tags: (A) tags read from a P&ID drawing, and (B) tags from "
    "the master Equipment List (Excel). Some tags in list A may correspond "
    "to tags in list B despite typos, OCR errors, hyphen/spacing "
    "differences, or a single misread character (e.g. 'V-803-TF' vs "
    "'V-803-TE', 'P-801A' vs 'P-8O1A'). Your job is to PAIR them wherever "
    "engineering intent is the same equipment item. For each MISSING "
    "(list-B tag not seen on the drawing) and each EXTRA (list-A tag not "
    "in the Equipment List) return ONE JSON object with keys: "
    "  kind ('missing_on_pid'|'extra_on_pid'), "
    "  tag (the input tag verbatim), "
    "  suggested_match (the counterpart tag from the OTHER list, or ''), "
    "  should_match (true when you are confident the two refer to the "
    "                 same equipment item and should be treated as MATCH), "
    "  reason (max 25 words), "
    "  confidence ('low'|'medium'|'high'). "
    "Respond with ONLY a JSON array of these objects. "
    "Rules: (1) never invent tags — suggested_match must come from the "
    "supplied lists; (2) same item-symbol prefix + same sequence number is "
    "a strong pair even if a suffix/site-symbol letter differs; (3) return "
    "should_match=false when confidence is low or when equipment types "
    "clearly differ (e.g. 'V' vessel vs 'P' pump)."
)

AI_ATTR_SYSTEM_PROMPT = (
    "You are a senior process/mechanical engineering QA reviewer. For each "
    "equipment tag you receive a list of attribute triples "
    "(attribute_key, pid_value, excel_value).  Decide, per attribute, "
    "whether the two values are engineering-equivalent, accounting for: "
    "unit conversions (bar vs psi vs kPa, °C vs °F, m vs mm vs inches, "
    "m³ vs litres), synonyms and abbreviations for materials (SS316 = "
    "Stainless Steel 316 = A312 TP316), rounding, and range notation "
    "('150-200' vs '150 to 200').  When one side is a single Min or Max "
    "field (e.g. 'op_temp_min') and the other is a combined range cell "
    "('Min: 60 / Max: 105'), match it against the CORRESPONDING endpoint of "
    "that range, not just any value inside it — an op_temp_min of 60 "
    "matches a range's Min of 60, and an op_temp_max of 105 matches that "
    "same range's Max of 105.  Apply general engineering judgment and "
    "common sense the way an experienced engineer would, for every "
    "attribute, not only the examples listed here.  Values that are blank, "
    "'n/a', '-' or '--' count as MISSING on that side.  Return ONLY a JSON array with "
    "one object per input tag: "
    "{tag, overall_severity, attributes:[{key, status, note}]}. "
    "status is one of 'match'|'mismatch'|'missing_pid'|'missing_excel'. "
    "overall_severity is 'ok' when every attribute is match or both-missing, "
    "'minor' when only non-safety attributes (e.g. nominal_capacity, "
    "length, diameter) mismatch, and 'critical' when any pressure, "
    "temperature or material-of-construction attribute mismatches. "
    "Keep 'note' under 20 words."
)


def cross_check(
    pid_equipment_tags: list[str],
    equipment_list_rows: list[dict],
    *,
    use_ai: bool = False,
    ai_provider: Optional[str] = None,
    ai_api_key: Optional[str] = None,
    pid_attributes: Optional[dict] = None,
) -> dict:
    """Return summary + list of findings comparing P&ID equipment tags to Equipment List.

    Parameters
    ----------
    pid_attributes : dict[str, dict[str,str]] | None
        Optional mapping tag → {attribute_key: pid_value}.  When present
        AND ai_provider/ai_api_key are given, matched tags will also
        undergo an attribute-level equivalence check using an AI judge;
        each MATCH finding gains an ``attributes`` list and a per-tag
        ``severity`` (ok|minor|critical).
    """
    pid_by_tag: dict[str, str] = {}
    for t in pid_equipment_tags:
        tag = _norm(t)
        if not tag:
            continue
        pid_by_tag.setdefault(tag, tag)

    el_by_tag: dict[str, dict] = {}
    for r in equipment_list_rows:
        tag = _norm(r.get('tag'))
        if not tag:
            continue
        el_by_tag.setdefault(tag, r)

    # Normalise pid_attributes keys → tag-normalised
    pid_attr_by_tag: dict[str, dict] = {}
    if pid_attributes:
        for t, attrs in pid_attributes.items():
            if not isinstance(attrs, dict):
                continue
            key = _norm(t)
            if not key:
                continue
            pid_attr_by_tag[key] = attrs

    findings: list[dict] = []

    # MATCH + MISSING
    for tag, el_row in el_by_tag.items():
        if tag in pid_by_tag:
            findings.append(_finding_match(tag, el_row))
        else:
            findings.append(_finding_missing(tag, el_row))

    # EXTRA_ON_PID
    for tag in pid_by_tag:
        if tag not in el_by_tag:
            findings.append(_finding_extra(tag))

    # Deterministic fuzzy pass: promote near-identical MISSING↔EXTRA pairs
    # to real Matches so an obvious OCR/single-character misread (e.g.
    # "V-803-TF" read as "V-803-TE") doesn't sit stuck as "Missing" forever
    # — no BYOK tokens needed for this pass.
    fuzzy_pairs = 0
    if FUZZY_MATCH_ENABLED:
        fuzzy_pairs = _pair_fuzzy_matches(findings, el_by_tag)

    ai_used = False
    ai_attr_used = False
    ai_promoted = 0
    from .token_accounting import UsageMeter
    meter = UsageMeter(feature='equipment_cross_check')
    if use_ai and ai_provider and ai_api_key:
        try:
            ai_promoted = _enrich_with_ai(findings, el_by_tag, ai_provider, ai_api_key, meter=meter)
            ai_used = True
        except Exception:
            logger.exception('AI equipment cross-check enrichment failed — falling back to deterministic result')

    # Attribute-level cross-check on matched pairs
    if pid_attr_by_tag:
        matched = [f for f in findings if f['kind'] == FINDING_MATCH]
        _compare_attributes_deterministic(matched, pid_attr_by_tag, el_by_tag)
        if ai_provider and ai_api_key:
            try:
                _refine_attributes_with_ai(matched, ai_provider, ai_api_key, meter=meter)
                ai_attr_used = True
            except Exception:
                logger.exception('AI attribute-comparison judge failed — keeping deterministic result')

    summary = _summarise(findings, len(pid_by_tag), len(el_by_tag))
    summary['fuzzy_pairs'] = fuzzy_pairs
    summary['ai_promoted_pairs'] = ai_promoted
    return {
        'summary': summary,
        'findings': findings,
        'ai_used': ai_used,
        'ai_attributes_used': ai_attr_used,
        'token_usage': meter.summary(),
    }


# ═════════════════════════════════════════════════════════════════════
# Finding builders
# ═════════════════════════════════════════════════════════════════════

def _finding_match(tag: str, el_row: dict) -> dict:
    return {
        'kind': FINDING_MATCH,
        'severity': SEVERITY_INFO,
        'tag': tag,
        'pid_tag': tag,
        'equipment_list_tag': tag,
        'description': el_row.get('description') or '',
        'pid_no': el_row.get('pid_no') or '',
        'message': 'Present on both P&ID and Equipment List',
        'equipment_list_row': el_row.get('excel_row'),
    }


def _finding_missing(tag: str, el_row: dict) -> dict:
    return {
        'kind': FINDING_MISSING_ON_PID,
        'severity': SEVERITY_ERROR,
        'tag': tag,
        'pid_tag': None,
        'equipment_list_tag': tag,
        'description': el_row.get('description') or '',
        'pid_no': el_row.get('pid_no') or '',
        'moc': el_row.get('moc') or '',
        'phase': el_row.get('phase') or '',
        'message': 'Equipment is in the master Equipment List but no matching tag was found on the P&ID',
        'equipment_list_row': el_row.get('excel_row'),
    }


def _finding_extra(tag: str) -> dict:
    return {
        'kind': FINDING_EXTRA_ON_PID,
        'severity': SEVERITY_WARNING,
        'tag': tag,
        'pid_tag': tag,
        'equipment_list_tag': None,
        'message': 'Tag was extracted from the P&ID but is not present in the master Equipment List',
    }


# ═════════════════════════════════════════════════════════════════════
# AI enrichment
# ═════════════════════════════════════════════════════════════════════

def _enrich_with_ai(findings: list[dict], el_by_tag: dict[str, dict], provider: str, api_key: str, *, meter=None) -> int:
    missing = [f for f in findings if f['kind'] == FINDING_MISSING_ON_PID][:AI_MAX_TAGS_PER_SIDE]
    extra   = [f for f in findings if f['kind'] == FINDING_EXTRA_ON_PID][:AI_MAX_TAGS_PER_SIDE]
    if not missing and not extra:
        return 0

    user_prompt = (
        "P&ID EXTRACTED TAGS (EXTRA — on drawing, not in Equipment List):\n"
        + '\n'.join(f'  • {f["tag"]}' for f in extra) + '\n\n'
        "EQUIPMENT LIST TAGS (MISSING — in Equipment List, not found on drawing):\n"
        + '\n'.join(f'  • {f["tag"]}   [desc: {f.get("description","")}, pid: {f.get("pid_no","")}]'
                    for f in missing) + '\n\n'
        "Correlate them where possible. Respond with the JSON array as specified."
    )
    raw, in_t, out_t = _call_ai(provider, api_key, user_prompt)
    if meter is not None:
        from .vision_extractor import VISION_MODELS
        meter.add(provider, VISION_MODELS[provider], in_t, out_t)
    parsed = _extract_json_array(raw)

    by_kind_tag: dict[tuple, dict] = {}
    for f in findings:
        by_kind_tag[(f['kind'], _norm(f['tag']))] = f

    # Two-pass: annotate first, then promote confident pairs to MATCH.
    promote_pairs: list[tuple[dict, dict, str, str]] = []
    for row in parsed:
        if not isinstance(row, dict):
            continue
        kind = str(row.get('kind') or '').strip()
        tag  = _norm(row.get('tag'))
        target = by_kind_tag.get((kind, tag))
        if not target:
            continue
        suggested = str(row.get('suggested_match') or '')
        reason    = str(row.get('reason') or '')
        confidence = str(row.get('confidence') or '').lower()
        target['ai_suggested_match'] = suggested
        target['ai_reason']          = reason
        target['ai_confidence']      = confidence

        if (
            bool(row.get('should_match'))
            and suggested
            and confidence in AI_PROMOTE_CONFIDENCES
            and target.get('kind') == FINDING_MISSING_ON_PID
        ):
            extra_target = by_kind_tag.get((FINDING_EXTRA_ON_PID, _norm(suggested)))
            if extra_target is not None:
                promote_pairs.append((target, extra_target, reason, confidence))

    promoted = 0
    for missing_f, extra_f, reason, confidence in promote_pairs:
        _promote_pair_to_match(findings, missing_f, extra_f, el_by_tag, reason, confidence)
        promoted += 1
    return promoted


def _promote_pair_to_match(
    findings: list[dict],
    missing_f: dict,
    extra_f: dict,
    el_by_tag: dict[str, dict],
    reason: str,
    confidence: str,
) -> None:
    equipment_list_tag = missing_f.get('equipment_list_tag') or missing_f.get('tag')
    pid_tag = extra_f.get('pid_tag') or extra_f.get('tag')
    el_row = el_by_tag.get(_norm(equipment_list_tag), {})
    promoted = _finding_match(_norm(equipment_list_tag), el_row)
    promoted.update({
        'severity': SEVERITY_WARNING,
        'ai_probable_match': True,
        'ai_reason': reason,
        'ai_confidence': confidence,
        'pid_tag': pid_tag,
        'message': f"AI-matched: P&ID '{pid_tag}' ↔ Equipment List '{equipment_list_tag}' ({reason})",
    })
    findings.remove(missing_f)
    findings.remove(extra_f)
    findings.append(promoted)


# ═════════════════════════════════════════════════════════════════════
# Fuzzy pairing (deterministic, no AI cost)
# ═════════════════════════════════════════════════════════════════════

_ALPHA_RE   = re.compile(r'[A-Z]+')
_NUMERIC_RE = re.compile(r'\d+')


def _tag_alpha(tag: str) -> str:
    """Return the leading item-symbol letters (e.g. 'V' from 'V-803-TF')."""
    m = _ALPHA_RE.match(_norm(tag))
    return m.group(0) if m else ''


def _tag_numeric(tag: str) -> str:
    """Return the first numeric run (e.g. '803' from 'V-803-TF')."""
    m = _NUMERIC_RE.search(_norm(tag))
    return m.group(0) if m else ''


def _tag_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _pair_fuzzy_matches(findings: list[dict], el_by_tag: dict[str, dict]) -> int:
    missing = [f for f in findings if f['kind'] == FINDING_MISSING_ON_PID]
    extra   = [f for f in findings if f['kind'] == FINDING_EXTRA_ON_PID]
    if not missing or not extra:
        return 0

    used_extra: set[int] = set()
    promoted = 0

    for miss in list(missing):
        miss_tag = miss.get('tag') or ''
        miss_alpha = _tag_alpha(miss_tag)
        miss_num   = _tag_numeric(miss_tag)

        best_idx = -1
        best_score = 0.0
        for idx, ex in enumerate(extra):
            if idx in used_extra:
                continue
            ex_tag = ex.get('tag') or ''
            if FUZZY_ALPHA_MUST_MATCH and _tag_alpha(ex_tag) != miss_alpha:
                continue
            if FUZZY_NUMERIC_MUST_MATCH and _tag_numeric(ex_tag) != miss_num:
                continue
            score = _tag_similarity(miss_tag, ex_tag)
            if score > best_score:
                best_score = score
                best_idx = idx

        if best_idx < 0 or best_score < FUZZY_MATCH_THRESHOLD:
            continue
        ex = extra[best_idx]
        used_extra.add(best_idx)
        _promote_pair_to_match(
            findings, miss, ex, el_by_tag,
            reason=f"deterministic fuzzy pair (similarity {best_score:.2f})",
            confidence='high',
        )
        findings[-1]['fuzzy_match'] = True
        findings[-1].pop('ai_probable_match', None)
        findings[-1]['message'] = (
            f"Fuzzy-matched: P&ID '{ex.get('pid_tag') or ex.get('tag')}' ↔ "
            f"Equipment List '{miss.get('equipment_list_tag') or miss.get('tag')}' "
            f"(similarity {best_score:.2f})"
        )
        promoted += 1

    return promoted


def _call_ai(provider: str, api_key: str, user_prompt: str):
    from .vision_extractor import VISION_MODELS
    from .token_accounting import read_openai_usage, read_claude_usage
    p = (provider or '').lower()
    if p == 'openai':
        import openai
        client = openai.OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=VISION_MODELS['openai'],
            max_tokens=AI_MAX_TOKENS,
            temperature=AI_TEMPERATURE,
            messages=[
                {'role': 'system', 'content': AI_SYSTEM_PROMPT},
                {'role': 'user', 'content': user_prompt},
            ],
        )
        inp, out = read_openai_usage(resp)
        return (resp.choices[0].message.content or ''), inp, out
    if p == 'claude':
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=VISION_MODELS['claude'],
            max_tokens=AI_MAX_TOKENS,
            temperature=AI_TEMPERATURE,
            system=AI_SYSTEM_PROMPT,
            messages=[{'role': 'user', 'content': user_prompt}],
        )
        parts = [b.text for b in resp.content if getattr(b, 'type', None) == 'text']
        inp, out = read_claude_usage(resp)
        return ''.join(parts), inp, out
    raise ValueError(f'unknown AI provider {provider!r}')


def _extract_json_array(text: str) -> list:
    if not text:
        return []
    stripped = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip(), flags=re.MULTILINE)
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass
    m = re.search(r'\[[\s\S]*\]', stripped)
    if not m:
        return []
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return []


# ═════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════

def _norm(v) -> str:
    """Tag-matching key. Delegates to the shared, more forgiving
    normalize_tag() (whitespace/underscore -> hyphen, repeated-hyphen
    collapse, junk-char strip) instead of a bare whitespace-strip — a Vision
    extraction that reads a tag with slightly different spacing/hyphenation
    than the Equipment List Excel (e.g. "V-803 -TF" vs "V-803-TF") would
    otherwise fail to match and show up as a false Missing+Extra pair
    instead of a Match. Falls back to the old whitespace-only behavior if
    the shared helper can't be imported for any reason."""
    if v is None:
        return ''
    try:
        from apps.pid_verification_v2.services.comparison_engine import normalize_tag
        return normalize_tag(v)
    except Exception:
        return re.sub(r'\s+', '', str(v).strip().upper())


def _summarise(findings: list[dict], n_pid: int, n_el: int) -> dict:
    matches  = sum(1 for f in findings if f['kind'] == FINDING_MATCH)
    missing  = sum(1 for f in findings if f['kind'] == FINDING_MISSING_ON_PID)
    extras   = sum(1 for f in findings if f['kind'] == FINDING_EXTRA_ON_PID)
    attr_minor    = sum(1 for f in findings
                        if f['kind'] == FINDING_MATCH and f.get('severity') == SEVERITY_MINOR)
    attr_critical = sum(1 for f in findings
                        if f['kind'] == FINDING_MATCH and f.get('severity') == SEVERITY_CRITICAL)
    attr_mismatches = sum(
        1
        for f in findings
        if f['kind'] == FINDING_MATCH
        for a in (f.get('attributes') or [])
        if a.get('status') in (ATTR_STATUS_MISMATCH, ATTR_STATUS_MISSING_PID, ATTR_STATUS_MISSING_XLS)
    )
    return {
        'pid_total': n_pid,
        'equipment_list_total': n_el,
        'match': matches,
        'missing_on_pid': missing,
        'extra_on_pid': extras,
        'attribute_mismatches': attr_mismatches,
        'attribute_minor': attr_minor,
        'attribute_critical': attr_critical,
        'coverage_pct': round(100.0 * matches / n_el, 1) if n_el else 0.0,
    }


# ═════════════════════════════════════════════════════════════════════
# Attribute-level comparison
# ═════════════════════════════════════════════════════════════════════

_ATTR_NORM_STRIP = re.compile(r'[\s,]+')
_ATTR_EMPTY_TOKENS = frozenset({'', 'N/A', 'NA', '-', '--', 'NIL', 'NONE', 'TBD'})


def _attr_norm(v) -> str:
    if v is None:
        return ''
    s = str(v).strip().upper()
    s = _ATTR_NORM_STRIP.sub('', s)
    return '' if s in _ATTR_EMPTY_TOKENS else s


def _is_empty_attr(v) -> bool:
    return _attr_norm(v) == ''


def _compare_attributes_deterministic(
    match_findings: list[dict],
    pid_attr_by_tag: dict[str, dict],
    el_by_tag: dict[str, dict],
) -> None:
    """Attach a raw ``attributes`` list + overall ``severity`` to each match finding.

    Deterministic pass: normalized-string equality only.  AI judge (called
    afterwards when BYOK is provided) will re-classify mismatches that are
    engineering-equivalent (unit conversions, synonyms, etc.).
    """
    for f in match_findings:
        tag = f['tag']
        pid_attrs = pid_attr_by_tag.get(tag) or {}
        el_row = el_by_tag.get(tag) or {}
        rows = []
        for key in EQUIPMENT_ATTRIBUTE_KEYS:
            pid_val = pid_attrs.get(key, '') or ''
            el_field = EL_ATTRIBUTE_FIELD_MAP.get(key, key)
            el_val = el_row.get(el_field, '') or ''
            status, note = _cell_status(pid_val, el_val)
            rows.append({
                'key': key,
                'label': EQUIPMENT_ATTRIBUTE_LABELS.get(key, key),
                'pid_value': str(pid_val),
                'excel_value': str(el_val),
                'status': status,
                'note': note or '',
            })
        f['attributes'] = rows
        f['severity'] = _overall_severity_from_rows(rows)


def _cell_status(pid_val, el_val) -> tuple[str, str]:
    """Returns (status, note). `note` is a short explanation shown inline
    in the UI for any match that wasn't a plain literal string match — e.g.
    a unit conversion ("15 M = 15000 mm") or a range/endpoint check —
    so the reviewer isn't left wondering why two different-looking values
    were called equivalent."""
    p_empty = _is_empty_attr(pid_val)
    e_empty = _is_empty_attr(el_val)
    if p_empty and e_empty:
        return ATTR_STATUS_BOTH_EMPTY, ''
    if p_empty:
        return ATTR_STATUS_MISSING_PID, ''
    if e_empty:
        return ATTR_STATUS_MISSING_XLS, ''
    if _attr_norm(pid_val) == _attr_norm(el_val):
        return ATTR_STATUS_MATCH, ''
    # Free, no-AI resolution for unit/format differences a plain
    # normalized-string check can't see (e.g. "150 psig" vs "150", "-13.2
    # °F" vs "-13.2", "60 °F" vs "Min: 60 / Max: 105") — same deterministic
    # matcher used by the V2 comparison engine. Only when this can't
    # confidently call it either does the pair stay MISMATCH, deferred to
    # the AI attribute judge (_refine_attributes_with_ai) when a BYOK key
    # is available.
    try:
        from apps.pid_verification_v2.services.comparison_engine import _try_deterministic_value_match_ex
        status, note = _try_deterministic_value_match_ex(pid_val, el_val)
        if status == 'MATCH':
            return ATTR_STATUS_MATCH, note or ''
    except Exception:
        logger.warning('[EquipmentCrossCheck] Deterministic value match helper unavailable', exc_info=True)
    return ATTR_STATUS_MISMATCH, ''


def _overall_severity_from_rows(rows: list[dict]) -> str:
    worst = SEVERITY_OK
    for r in rows:
        status = r.get('status')
        if status in (ATTR_STATUS_MATCH, ATTR_STATUS_BOTH_EMPTY):
            continue
        if r.get('key') in CRITICAL_ATTRIBUTE_KEYS:
            return SEVERITY_CRITICAL
        worst = SEVERITY_MINOR
    return worst


def _refine_attributes_with_ai(match_findings: list[dict], provider: str, api_key: str, *, meter=None) -> None:
    """Ask BYOK model to re-judge attribute equivalence (units, synonyms, ranges).

    Overwrites ``status`` / ``note`` on each attribute row and recomputes
    overall ``severity``.  Only tags with at least one non-empty attribute
    on either side are sent.
    """
    payload_tags = []
    for f in match_findings:
        rows = f.get('attributes') or []
        # Only send attributes the deterministic pass couldn't already
        # confidently resolve. A row already marked MATCH (plain equality
        # or _try_deterministic_value_match's unit/range handling) is
        # confident by construction — sending it to the AI anyway risks a
        # single misjudged call silently flipping a correct Match back to
        # Mismatch, with no way for the deterministic result to win back.
        nonempty = [
            {'key': r['key'], 'pid_value': r['pid_value'], 'excel_value': r['excel_value']}
            for r in rows
            if r.get('status') not in (ATTR_STATUS_BOTH_EMPTY, ATTR_STATUS_MATCH)
        ]
        if not nonempty:
            continue
        payload_tags.append({'tag': f['tag'], 'attributes': nonempty})

    if not payload_tags:
        return

    by_tag = {f['tag']: f for f in match_findings}

    for i in range(0, len(payload_tags), AI_ATTR_MAX_TAGS_PER_BATCH):
        batch = payload_tags[i:i + AI_ATTR_MAX_TAGS_PER_BATCH]
        user_prompt = (
            "Compare the P&ID vs Equipment List values for each attribute of each "
            "tag below.  Respond with ONLY the JSON array per the schema in the "
            "system prompt.\n\n"
            + json.dumps(batch, ensure_ascii=False, indent=2)
        )
        raw, in_t, out_t = _call_ai_attr(provider, api_key, user_prompt)
        if meter is not None:
            from .vision_extractor import VISION_MODELS
            meter.add(provider, VISION_MODELS[provider], in_t, out_t)
        parsed = _extract_json_array(raw)
        for row in parsed:
            if not isinstance(row, dict):
                continue
            tag = _norm(row.get('tag'))
            target = by_tag.get(tag)
            if not target:
                continue
            ai_attrs = row.get('attributes') or []
            if not isinstance(ai_attrs, list):
                continue
            existing_by_key = {a['key']: a for a in (target.get('attributes') or [])}
            for a in ai_attrs:
                if not isinstance(a, dict):
                    continue
                key = a.get('key')
                cell = existing_by_key.get(key)
                if not cell:
                    continue
                if cell.get('status') == ATTR_STATUS_MATCH:
                    # Belt-and-suspenders: a deterministic MATCH is never
                    # sent to the AI (see the payload_tags filter above),
                    # but guard here too in case of a stray/duplicate key
                    # in the AI's response — never let it downgrade a
                    # value the deterministic pass already confirmed.
                    continue
                status = str(a.get('status') or '').strip()
                if status in (
                    ATTR_STATUS_MATCH, ATTR_STATUS_MISMATCH,
                    ATTR_STATUS_MISSING_PID, ATTR_STATUS_MISSING_XLS,
                ):
                    cell['status'] = status
                note = a.get('note')
                if note:
                    cell['note'] = str(note)[:200]
            # Prefer AI overall_severity when provided
            ai_sev = str(row.get('overall_severity') or '').strip()
            if ai_sev in (SEVERITY_OK, SEVERITY_MINOR, SEVERITY_CRITICAL):
                target['severity'] = ai_sev
            else:
                target['severity'] = _overall_severity_from_rows(target.get('attributes') or [])


def _call_ai_attr(provider: str, api_key: str, user_prompt: str):
    from .vision_extractor import VISION_MODELS
    from .token_accounting import read_openai_usage, read_claude_usage
    p = (provider or '').lower()
    if p == 'openai':
        import openai
        client = openai.OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=VISION_MODELS['openai'],
            max_tokens=AI_ATTR_MAX_TOKENS,
            temperature=AI_TEMPERATURE,
            messages=[
                {'role': 'system', 'content': AI_ATTR_SYSTEM_PROMPT},
                {'role': 'user', 'content': user_prompt},
            ],
        )
        inp, out = read_openai_usage(resp)
        return (resp.choices[0].message.content or ''), inp, out
    if p == 'claude':
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=VISION_MODELS['claude'],
            max_tokens=AI_ATTR_MAX_TOKENS,
            temperature=AI_TEMPERATURE,
            system=AI_ATTR_SYSTEM_PROMPT,
            messages=[{'role': 'user', 'content': user_prompt}],
        )
        parts = [b.text for b in resp.content if getattr(b, 'type', None) == 'text']
        inp, out = read_claude_usage(resp)
        return ''.join(parts), inp, out
    raise ValueError(f'unknown AI provider {provider!r}')
