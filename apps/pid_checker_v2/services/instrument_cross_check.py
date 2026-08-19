"""Cross-check a list of P&ID instrument tags against a master Instrument
Index (uploaded Excel).

Produces three buckets of findings per tag:

  * MISSING_ON_PID     — instrument is in the Instrument Index but no
                          matching tag was seen on the drawing.
  * EXTRA_ON_PID       — tag was extracted from the drawing but does not
                          appear in the Instrument Index.
  * MATCH              — same tag present on both sides.

An optional AI pass batches the MISSING and EXTRA sets together and asks
Claude/OpenAI to correlate them (typo? OCR miss? space-vs-no-space?)
and suggest what to do.
"""
from __future__ import annotations

import json
import logging
import re
from difflib import SequenceMatcher
from typing import Optional

from .instrument_vision_extractor import (
    INSTRUMENT_ATTRIBUTE_KEYS,
    INSTRUMENT_ATTRIBUTE_LABELS,
)

logger = logging.getLogger(__name__)


# ─── Soft-coded config ────────────────────────────────────────────────
SEVERITY_INFO = 'info'
SEVERITY_WARNING = 'warning'
SEVERITY_ERROR = 'error'

# Attribute-level severity (overall per matched tag)
SEVERITY_OK = 'ok'
SEVERITY_MINOR = 'minor'
SEVERITY_CRITICAL = 'critical'

# Safety-critical instrument attributes → any real mismatch is CRITICAL.
# Range / calibration drive trip & alarm logic; type mismatch means the
# wrong physical device would be installed.
CRITICAL_ATTRIBUTE_KEYS = frozenset({
    'instrument_type',
    'range_min',
    'range_max',
    'range_unit',
    'cal_min',
    'cal_max',
    'cal_unit',
    'ex_class',
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

# Instrument-attribute key → column name on PidCheckerV2InstrumentIndexRow.
# Every value in this map must be a column name selected in the view.
IX_ATTRIBUTE_FIELD_MAP = {
    'instrument_type':     'instrument_type',
    'service_description': 'service_description',
    'range_min':           'range_min',
    'range_max':           'range_max',
    'range_unit':          'range_unit',
    'cal_min':             'cal_min',
    'cal_max':             'cal_max',
    'cal_unit':            'cal_unit',
    'ex_class':            'ex_class',
    'power_supply':        'power_supply',
    'manufacturer':        'manufacturer',
    'model':               'model',
}

AI_MAX_TAGS_PER_SIDE = 40
AI_MAX_TOKENS = 2048
AI_TEMPERATURE = 0.0

# Fuzzy (deterministic) pairing of MISSING ↔ EXTRA before AI.
# Catches OCR / hyphen / suffix drift like "PT-8003A" vs "PT8003 A" or
# "LT-8019TF" vs "LT-8019 TF" without spending BYOK tokens.
FUZZY_MATCH_ENABLED         = True
FUZZY_MATCH_THRESHOLD       = 0.85     # SequenceMatcher ratio
FUZZY_ALPHA_MUST_MATCH      = True     # function-code letters must be identical
FUZZY_NUMERIC_MUST_MATCH    = True     # numeric core must be identical

# AI-suggested pair promotion — when the AI returns
# should_match=true with confidence high|medium the pair is promoted
# to a MATCH row (marked as `ai_probable_match=True`).
AI_PROMOTE_CONFIDENCES = ('high', 'medium')

# Attribute-comparison AI judge
AI_ATTR_MAX_TAGS_PER_BATCH = 12
AI_ATTR_MAX_TOKENS = 3000

AI_SYSTEM_PROMPT = (
    "You are an instrumentation engineering QA reviewer. You are given two "
    "lists of instrument tags: (A) tags read from a P&ID drawing, and (B) "
    "tags from the master Instrument Index (Excel). Some tags in list A "
    "may correspond to tags in list B despite typos, OCR errors, hyphen / "
    "space differences, or suffix drift (e.g. 'LT-8019 TF' vs 'LT8019TF', "
    "'PT-8003A' vs 'PT8003ATF', 'FIC 8002' vs 'FIC-8002'). Your job is to "
    "PAIR them wherever engineering intent is the same instrument. For "
    "each MISSING (index tag not seen on drawing) and each EXTRA (drawing "
    "tag not in index) return ONE JSON object with keys: "
    "  kind ('missing_on_pid'|'extra_on_pid'), "
    "  tag (the input tag verbatim), "
    "  suggested_match (the counterpart tag from the OTHER list, or ''), "
    "  should_match (true when you are confident the two refer to the "
    "                 same instrument and should be treated as MATCH), "
    "  reason (max 25 words), "
    "  confidence ('low'|'medium'|'high'). "
    "Respond with ONLY a JSON array of these objects. "
    "Rules: (1) never invent tags — suggested_match must come from the "
    "supplied lists; (2) same function-code letters + same numeric core "
    "is a strong pair even if suffixes differ by one letter; (3) return "
    "should_match=false when confidence is low or when instrument "
    "families clearly differ (e.g. 'PT' vs 'LT')."
)

AI_ATTR_SYSTEM_PROMPT = (
    "You are a senior instrumentation & control QA reviewer. For each "
    "instrument tag you receive a list of attribute triples "
    "(attribute_key, pid_value, excel_value).  Decide, per attribute, "
    "whether the two values are engineering-equivalent, accounting for: "
    "unit conversions (bar vs psi vs kPa, °C vs °F, mmH2O vs kPa vs "
    "inH2O), synonyms and abbreviations for instrument type "
    "('LT' = 'Level Transmitter', 'Ex ia' = 'Intrinsically Safe', "
    "'24VDC' = '24 V DC' = '24 volts DC'), rounding, and range notation "
    "('0-100' vs '0 to 100').  Values that are blank, 'n/a', '-' or '--' "
    "count as MISSING on that side.  Return ONLY a JSON array with one "
    "object per input tag: "
    "{tag, overall_severity, attributes:[{key, status, note}]}. "
    "status is one of 'match'|'mismatch'|'missing_pid'|'missing_excel'. "
    "overall_severity is 'ok' when every attribute is match or "
    "both-missing, 'minor' when only non-safety attributes (manufacturer, "
    "model, power_supply, service_description) mismatch, and 'critical' "
    "when instrument type, calibration, range, or ex-classification "
    "mismatches. Keep 'note' under 20 words."
)


def cross_check(
    pid_instrument_tags: list[str],
    instrument_index_rows: list[dict],
    *,
    use_ai: bool = False,
    ai_provider: Optional[str] = None,
    ai_api_key: Optional[str] = None,
    pid_attributes: Optional[dict] = None,
) -> dict:
    """Return summary + list of findings comparing P&ID instrument tags to Instrument Index.

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
    for t in pid_instrument_tags:
        tag = _norm(t)
        if not tag:
            continue
        pid_by_tag.setdefault(tag, tag)

    ii_by_tag: dict[str, dict] = {}
    for r in instrument_index_rows:
        tag = _norm(r.get('tag'))
        if not tag:
            continue
        ii_by_tag.setdefault(tag, r)

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
    for tag, ii_row in ii_by_tag.items():
        if tag in pid_by_tag:
            findings.append(_finding_match(tag, ii_row))
        else:
            findings.append(_finding_missing(tag, ii_row))

    # EXTRA_ON_PID
    for tag in pid_by_tag:
        if tag not in ii_by_tag:
            findings.append(_finding_extra(tag))

    # Deterministic fuzzy pass: promote near-identical MISSING↔EXTRA pairs
    # to fuzzy matches so obvious OCR / hyphen / suffix drift doesn't
    # burn BYOK tokens or bloat the discrepancy list.
    fuzzy_pairs = 0
    if FUZZY_MATCH_ENABLED:
        fuzzy_pairs = _pair_fuzzy_matches(findings, ii_by_tag)

    ai_used = False
    ai_attr_used = False
    ai_promoted = 0
    from .token_accounting import UsageMeter
    meter = UsageMeter(feature='instrument_cross_check')
    if use_ai and ai_provider and ai_api_key:
        try:
            ai_promoted = _enrich_with_ai(findings, ii_by_tag, ai_provider, ai_api_key, meter=meter)
            ai_used = True
        except Exception:
            logger.exception(
                'AI instrument cross-check enrichment failed — falling back to deterministic result'
            )

    if pid_attr_by_tag:
        matched = [f for f in findings if f['kind'] == FINDING_MATCH]
        _compare_attributes_deterministic(matched, pid_attr_by_tag, ii_by_tag)
        if ai_provider and ai_api_key:
            try:
                _refine_attributes_with_ai(matched, ai_provider, ai_api_key, meter=meter)
                ai_attr_used = True
            except Exception:
                logger.exception(
                    'AI instrument attribute-comparison judge failed — keeping deterministic result'
                )

    summary = _summarise(findings, len(pid_by_tag), len(ii_by_tag))
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

def _finding_match(tag: str, ii_row: dict) -> dict:
    return {
        'kind': FINDING_MATCH,
        'severity': SEVERITY_INFO,
        'tag': tag,
        'pid_tag': tag,
        'instrument_index_tag': tag,
        'instrument_type': ii_row.get('instrument_type') or '',
        'service_description': ii_row.get('service_description') or '',
        'pid_no': ii_row.get('pid_no') or '',
        'eqpt_no': ii_row.get('eqpt_no') or '',
        'message': 'Present on both P&ID and Instrument Index',
        'instrument_index_row': ii_row.get('excel_row'),
    }


def _finding_missing(tag: str, ii_row: dict) -> dict:
    return {
        'kind': FINDING_MISSING_ON_PID,
        'severity': SEVERITY_ERROR,
        'tag': tag,
        'pid_tag': None,
        'instrument_index_tag': tag,
        'instrument_type': ii_row.get('instrument_type') or '',
        'service_description': ii_row.get('service_description') or '',
        'pid_no': ii_row.get('pid_no') or '',
        'eqpt_no': ii_row.get('eqpt_no') or '',
        'line_no': ii_row.get('line_no') or '',
        'message': 'Instrument is in the master Instrument Index but no matching tag was found on the P&ID',
        'instrument_index_row': ii_row.get('excel_row'),
    }


def _finding_extra(tag: str) -> dict:
    return {
        'kind': FINDING_EXTRA_ON_PID,
        'severity': SEVERITY_WARNING,
        'tag': tag,
        'pid_tag': tag,
        'instrument_index_tag': None,
        'message': 'Tag was extracted from the P&ID but is not present in the master Instrument Index',
    }


# ═════════════════════════════════════════════════════════════════════
# AI enrichment
# ═════════════════════════════════════════════════════════════════════

def _enrich_with_ai(findings: list[dict], ii_by_tag: dict[str, dict], provider: str, api_key: str, *, meter=None) -> int:
    missing = [f for f in findings if f['kind'] == FINDING_MISSING_ON_PID][:AI_MAX_TAGS_PER_SIDE]
    extra   = [f for f in findings if f['kind'] == FINDING_EXTRA_ON_PID][:AI_MAX_TAGS_PER_SIDE]
    if not missing and not extra:
        return 0

    user_prompt = (
        "P&ID EXTRACTED TAGS (EXTRA — on drawing, not in Instrument Index):\n"
        + '\n'.join(f'  • {f["tag"]}' for f in extra) + '\n\n'
        "INSTRUMENT INDEX TAGS (MISSING — in index, not found on drawing):\n"
        + '\n'.join(
            f'  • {f["tag"]}   [type: {f.get("instrument_type","")}, '
            f'eqpt: {f.get("eqpt_no","")}, pid: {f.get("pid_no","")}]'
            for f in missing
        ) + '\n\n'
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
    promote_pairs: list[tuple[dict, str, str, str]] = []
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
            # For MISSING rows we can look up the paired EXTRA finding
            # and merge both into a single AI-promoted MATCH.
            extra_target = by_kind_tag.get((FINDING_EXTRA_ON_PID, _norm(suggested)))
            if extra_target is not None:
                promote_pairs.append((target, extra_target, reason, confidence))

    promoted = 0
    for missing_f, extra_f, reason, confidence in promote_pairs:
        _promote_pair_to_match(findings, missing_f, extra_f, ii_by_tag, reason, confidence)
        promoted += 1
    return promoted


def _promote_pair_to_match(
    findings: list[dict],
    missing_f: dict,
    extra_f: dict,
    ii_by_tag: dict[str, dict],
    reason: str,
    confidence: str,
) -> None:
    index_tag = missing_f.get('instrument_index_tag') or missing_f.get('tag')
    pid_tag   = extra_f.get('pid_tag') or extra_f.get('tag')
    ii_row    = ii_by_tag.get(_norm(index_tag), {})
    promoted = _finding_match(index_tag, ii_row)
    promoted.update({
        'severity': SEVERITY_WARNING,
        'ai_probable_match': True,
        'ai_reason': reason,
        'ai_confidence': confidence,
        'pid_tag': pid_tag,
        'message': f"AI-matched: P&ID '{pid_tag}' ↔ Index '{index_tag}' ({reason})",
    })
    # Drop the original MISSING + EXTRA rows and append the promoted MATCH.
    findings.remove(missing_f)
    findings.remove(extra_f)
    findings.append(promoted)


# ═════════════════════════════════════════════════════════════════════
# Fuzzy pairing (deterministic, no AI cost)
# ═════════════════════════════════════════════════════════════════════

_ALPHA_RE   = re.compile(r'[A-Z]+')
_NUMERIC_RE = re.compile(r'\d+')


def _tag_alpha(tag: str) -> str:
    """Return the leading function-code letters (e.g. 'PT' from 'PT-8003A')."""
    m = _ALPHA_RE.match(_norm(tag))
    return m.group(0) if m else ''


def _tag_numeric(tag: str) -> str:
    """Return the first numeric run (e.g. '8003' from 'PT-8003A')."""
    m = _NUMERIC_RE.search(_norm(tag))
    return m.group(0) if m else ''


def _tag_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _pair_fuzzy_matches(findings: list[dict], ii_by_tag: dict[str, dict]) -> int:
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
            findings, miss, ex, ii_by_tag,
            reason=f"deterministic fuzzy pair (similarity {best_score:.2f})",
            confidence='high',
        )
        # Mark as deterministic, not AI-promoted, for downstream reporting.
        # Last appended item is the promoted match.
        findings[-1]['fuzzy_match'] = True
        findings[-1].pop('ai_probable_match', None)
        findings[-1]['message'] = (
            f"Fuzzy-matched: P&ID '{ex.get('pid_tag') or ex.get('tag')}' ↔ "
            f"Index '{miss.get('instrument_index_tag') or miss.get('tag')}' "
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
    if v is None:
        return ''
    # Strip whitespace AND hyphens so "SDV8005" (P&ID) matches "SDV-8005"
    # (Instrument Index Excel).
    return re.sub(r'[\s\-]+', '', str(v).strip().upper())


def _summarise(findings: list[dict], n_pid: int, n_ii: int) -> dict:
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
        'instrument_index_total': n_ii,
        'match': matches,
        'missing_on_pid': missing,
        'extra_on_pid': extras,
        'attribute_mismatches': attr_mismatches,
        'attribute_minor': attr_minor,
        'attribute_critical': attr_critical,
        'coverage_pct': round(100.0 * matches / n_ii, 1) if n_ii else 0.0,
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
    ii_by_tag: dict[str, dict],
) -> None:
    for f in match_findings:
        tag = f['tag']
        pid_attrs = pid_attr_by_tag.get(tag) or {}
        ii_row = ii_by_tag.get(tag) or {}
        rows = []
        for key in INSTRUMENT_ATTRIBUTE_KEYS:
            pid_val = pid_attrs.get(key, '') or ''
            ii_field = IX_ATTRIBUTE_FIELD_MAP.get(key, key)
            ii_val = ii_row.get(ii_field, '') or ''
            rows.append({
                'key': key,
                'label': INSTRUMENT_ATTRIBUTE_LABELS.get(key, key),
                'pid_value': str(pid_val),
                'excel_value': str(ii_val),
                'status': _cell_status(pid_val, ii_val),
                'note': '',
            })
        f['attributes'] = rows
        f['severity'] = _overall_severity_from_rows(rows)


def _cell_status(pid_val, ii_val) -> str:
    p_empty = _is_empty_attr(pid_val)
    e_empty = _is_empty_attr(ii_val)
    if p_empty and e_empty:
        return ATTR_STATUS_BOTH_EMPTY
    if p_empty:
        return ATTR_STATUS_MISSING_PID
    if e_empty:
        return ATTR_STATUS_MISSING_XLS
    return ATTR_STATUS_MATCH if _attr_norm(pid_val) == _attr_norm(ii_val) else ATTR_STATUS_MISMATCH


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
    payload_tags = []
    for f in match_findings:
        rows = f.get('attributes') or []
        nonempty = [
            {'key': r['key'], 'pid_value': r['pid_value'], 'excel_value': r['excel_value']}
            for r in rows
            if r.get('status') != ATTR_STATUS_BOTH_EMPTY
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
            "Compare the P&ID vs Instrument Index values for each attribute of each "
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
                status = str(a.get('status') or '').strip()
                if status in (
                    ATTR_STATUS_MATCH, ATTR_STATUS_MISMATCH,
                    ATTR_STATUS_MISSING_PID, ATTR_STATUS_MISSING_XLS,
                ):
                    cell['status'] = status
                note = a.get('note')
                if note:
                    cell['note'] = str(note)[:200]
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
