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

# Attribute-comparison AI judge
AI_ATTR_MAX_TAGS_PER_BATCH = 12
AI_ATTR_MAX_TOKENS = 3000

AI_SYSTEM_PROMPT = (
    "You are a piping engineering QA reviewer. You are given two lists of "
    "equipment tags: (A) tags read from a P&ID drawing, and (B) tags from "
    "the master Equipment List (Excel).  Some tags in list A may "
    "correspond to tags in list B despite typos, OCR errors, or minor "
    "formatting differences.  For each MISSING (list-B tag not seen on the "
    "drawing) and each EXTRA (list-A tag not in the Equipment List), "
    "suggest the most likely correlated counterpart if any, plus a short "
    "reason and a confidence low|medium|high.  Respond with ONLY a JSON "
    "array of objects with keys: kind ('missing_on_pid'|'extra_on_pid'), "
    "tag, suggested_match, reason, confidence."
)

AI_ATTR_SYSTEM_PROMPT = (
    "You are a senior process/mechanical engineering QA reviewer. For each "
    "equipment tag you receive a list of attribute triples "
    "(attribute_key, pid_value, excel_value).  Decide, per attribute, "
    "whether the two values are engineering-equivalent, accounting for: "
    "unit conversions (bar vs psi vs kPa, °C vs °F, m vs mm vs inches, "
    "m³ vs litres), synonyms and abbreviations for materials (SS316 = "
    "Stainless Steel 316 = A312 TP316), rounding, and range notation "
    "('150-200' vs '150 to 200').  Values that are blank, 'n/a', '-' or "
    "'--' count as MISSING on that side.  Return ONLY a JSON array with "
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

    ai_used = False
    ai_attr_used = False
    if use_ai and ai_provider and ai_api_key:
        try:
            _enrich_with_ai(findings, ai_provider, ai_api_key)
            ai_used = True
        except Exception:
            logger.exception('AI equipment cross-check enrichment failed — falling back to deterministic result')

    # Attribute-level cross-check on matched pairs
    if pid_attr_by_tag:
        matched = [f for f in findings if f['kind'] == FINDING_MATCH]
        _compare_attributes_deterministic(matched, pid_attr_by_tag, el_by_tag)
        if ai_provider and ai_api_key:
            try:
                _refine_attributes_with_ai(matched, ai_provider, ai_api_key)
                ai_attr_used = True
            except Exception:
                logger.exception('AI attribute-comparison judge failed — keeping deterministic result')

    summary = _summarise(findings, len(pid_by_tag), len(el_by_tag))
    return {
        'summary': summary,
        'findings': findings,
        'ai_used': ai_used,
        'ai_attributes_used': ai_attr_used,
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

def _enrich_with_ai(findings: list[dict], provider: str, api_key: str) -> None:
    missing = [f for f in findings if f['kind'] == FINDING_MISSING_ON_PID][:AI_MAX_TAGS_PER_SIDE]
    extra   = [f for f in findings if f['kind'] == FINDING_EXTRA_ON_PID][:AI_MAX_TAGS_PER_SIDE]
    if not missing and not extra:
        return

    user_prompt = (
        "P&ID EXTRACTED TAGS (EXTRA — on drawing, not in Equipment List):\n"
        + '\n'.join(f'  • {f["tag"]}' for f in extra) + '\n\n'
        "EQUIPMENT LIST TAGS (MISSING — in Equipment List, not found on drawing):\n"
        + '\n'.join(f'  • {f["tag"]}   [desc: {f.get("description","")}, pid: {f.get("pid_no","")}]'
                    for f in missing) + '\n\n'
        "Correlate them where possible. Respond with the JSON array as specified."
    )
    raw = _call_ai(provider, api_key, user_prompt)
    parsed = _extract_json_array(raw)

    by_kind_tag: dict[tuple, dict] = {}
    for f in findings:
        by_kind_tag[(f['kind'], _norm(f['tag']))] = f

    for row in parsed:
        if not isinstance(row, dict):
            continue
        kind = str(row.get('kind') or '').strip()
        tag  = _norm(row.get('tag'))
        target = by_kind_tag.get((kind, tag))
        if not target:
            continue
        target['ai_suggested_match'] = str(row.get('suggested_match') or '')
        target['ai_reason']          = str(row.get('reason') or '')
        target['ai_confidence']      = str(row.get('confidence') or '')


def _call_ai(provider: str, api_key: str, user_prompt: str) -> str:
    from .vision_extractor import VISION_MODELS
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
        return resp.choices[0].message.content or ''
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
        return ''.join(parts)
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
            rows.append({
                'key': key,
                'label': EQUIPMENT_ATTRIBUTE_LABELS.get(key, key),
                'pid_value': str(pid_val),
                'excel_value': str(el_val),
                'status': _cell_status(pid_val, el_val),
                'note': '',
            })
        f['attributes'] = rows
        f['severity'] = _overall_severity_from_rows(rows)


def _cell_status(pid_val, el_val) -> str:
    p_empty = _is_empty_attr(pid_val)
    e_empty = _is_empty_attr(el_val)
    if p_empty and e_empty:
        return ATTR_STATUS_BOTH_EMPTY
    if p_empty:
        return ATTR_STATUS_MISSING_PID
    if e_empty:
        return ATTR_STATUS_MISSING_XLS
    return ATTR_STATUS_MATCH if _attr_norm(pid_val) == _attr_norm(el_val) else ATTR_STATUS_MISMATCH


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


def _refine_attributes_with_ai(match_findings: list[dict], provider: str, api_key: str) -> None:
    """Ask BYOK model to re-judge attribute equivalence (units, synonyms, ranges).

    Overwrites ``status`` / ``note`` on each attribute row and recomputes
    overall ``severity``.  Only tags with at least one non-empty attribute
    on either side are sent.
    """
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
            "Compare the P&ID vs Equipment List values for each attribute of each "
            "tag below.  Respond with ONLY the JSON array per the schema in the "
            "system prompt.\n\n"
            + json.dumps(batch, ensure_ascii=False, indent=2)
        )
        raw = _call_ai_attr(provider, api_key, user_prompt)
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
            # Prefer AI overall_severity when provided
            ai_sev = str(row.get('overall_severity') or '').strip()
            if ai_sev in (SEVERITY_OK, SEVERITY_MINOR, SEVERITY_CRITICAL):
                target['severity'] = ai_sev
            else:
                target['severity'] = _overall_severity_from_rows(target.get('attributes') or [])


def _call_ai_attr(provider: str, api_key: str, user_prompt: str) -> str:
    from .vision_extractor import VISION_MODELS
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
        return resp.choices[0].message.content or ''
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
        return ''.join(parts)
    raise ValueError(f'unknown AI provider {provider!r}')
