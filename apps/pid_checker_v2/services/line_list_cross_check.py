"""Cross-check a P&ID line-tag extraction against a master Line List
(uploaded Excel).

Produces three buckets of findings per tag:

  * MISSING_ON_PID     — line is in the Line List but no matching tag was
                          extracted from the drawing.
  * EXTRA_ON_PID       — tag was extracted from the drawing but does not
                          appear in the Line List.
  * MISMATCH           — same serial appears on both sides but size or
                          spec differ (rename/renumber candidate).

An optional AI pass batches the MISSING and EXTRA sets together and asks
Claude/OpenAI to correlate them (typo? partial match? OCR miss?) and
suggest what to do.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


# ─── Soft-coded config ────────────────────────────────────────────────
SEVERITY_INFO = 'info'
SEVERITY_WARNING = 'warning'
SEVERITY_ERROR = 'error'

FINDING_MISSING_ON_PID = 'missing_on_pid'
FINDING_EXTRA_ON_PID   = 'extra_on_pid'
FINDING_MISMATCH       = 'mismatch'
FINDING_MATCH          = 'match'

AI_MAX_TAGS_PER_SIDE = 40
AI_MAX_TOKENS = 2048
AI_TEMPERATURE = 0.0

AI_SYSTEM_PROMPT = (
    "You are a piping engineering QA reviewer. You are given two lists of "
    "line tags: (A) tags extracted from a P&ID drawing, and (B) tags from "
    "the master Line List (Excel).  Some tags in list A may correspond to "
    "tags in list B despite typos, OCR errors, or minor formatting "
    "differences.  For each MISSING (list-B tag not seen on the drawing) "
    "and each EXTRA (list-A tag not in the Line List), suggest the most "
    "likely correlated counterpart if any, plus a short reason and a "
    "confidence low|medium|high.  Respond with ONLY a JSON array of "
    "objects with keys: kind ('missing_on_pid'|'extra_on_pid'), tag, "
    "suggested_match, reason, confidence."
)


def cross_check(
    pid_tags: list[dict],
    line_list_rows: list[dict],
    *,
    use_ai: bool = False,
    ai_provider: Optional[str] = None,
    ai_api_key: Optional[str] = None,
) -> dict:
    """Return summary + list of findings comparing P&ID tags to Line List rows."""
    pid_by_tag: dict[str, dict] = {}
    pid_by_serial: dict[str, dict] = {}
    for t in pid_tags:
        tag = _norm(t.get('tag'))
        if not tag:
            continue
        pid_by_tag[tag] = t
        s = _norm(t.get('serial'))
        if s:
            pid_by_serial.setdefault(s, t)

    ll_by_tag: dict[str, dict] = {}
    ll_by_serial: dict[str, dict] = {}
    for r in line_list_rows:
        tag = _norm(r.get('tag'))
        if not tag:
            continue
        ll_by_tag[tag] = r
        s = _norm(r.get('serial'))
        if s:
            ll_by_serial.setdefault(s, r)

    findings: list[dict] = []

    # MATCH  &  MISMATCH
    for tag, ll_row in ll_by_tag.items():
        pid_row = pid_by_tag.get(tag)
        if pid_row is not None:
            findings.append(_finding_match(tag, pid_row, ll_row))
            continue
        # Try to find the same serial with different size/spec on the P&ID
        serial = _norm(ll_row.get('serial'))
        candidate = pid_by_serial.get(serial) if serial else None
        if candidate and _norm(candidate.get('tag')) not in ll_by_tag:
            findings.append(_finding_mismatch(candidate, ll_row))
            continue
        # Truly missing
        findings.append(_finding_missing(tag, ll_row))

    # EXTRA_ON_PID  — anything on the drawing not accounted for above
    accounted_pid_tags = {f['pid_tag'] for f in findings if f.get('pid_tag')}
    for tag, pid_row in pid_by_tag.items():
        if tag in accounted_pid_tags:
            continue
        findings.append(_finding_extra(tag, pid_row))

    # Optional AI enrichment on missing + extra only
    ai_used = False
    from .token_accounting import UsageMeter
    meter = UsageMeter(feature='line_list_cross_check')
    if use_ai and ai_provider and ai_api_key:
        try:
            _enrich_with_ai(findings, ai_provider, ai_api_key, meter=meter)
            ai_used = True
        except Exception:
            logger.exception('AI cross-check enrichment failed — falling back to deterministic result')

    summary = _summarise(findings, len(pid_by_tag), len(ll_by_tag))
    return {
        'summary': summary,
        'findings': findings,
        'ai_used': ai_used,
        'token_usage': meter.summary(),
    }


# ═════════════════════════════════════════════════════════════════════
# Finding builders
# ═════════════════════════════════════════════════════════════════════

def _finding_match(tag: str, pid_row: dict, ll_row: dict) -> dict:
    return {
        'kind': FINDING_MATCH,
        'severity': SEVERITY_INFO,
        'tag': tag,
        'pid_tag': tag,
        'line_list_tag': tag,
        'message': 'Present on both P&ID and Line List',
        'line_list_row': ll_row.get('excel_row'),
    }


def _attrs_equivalent(p: str, l: str) -> bool:
    """Free, no-AI check for unit/format differences (e.g. "6 IN" vs "6\"",
    "150 psig" vs "150") before declaring a real attribute mismatch — same
    deterministic matcher used across the rest of the app's comparisons."""
    if p == l:
        return True
    try:
        from apps.pid_verification_v2.services.comparison_engine import _try_deterministic_value_match
        return _try_deterministic_value_match(p, l) == 'MATCH'
    except Exception:
        return False


def _finding_mismatch(pid_row: dict, ll_row: dict) -> dict:
    pid_tag = _norm(pid_row.get('tag'))
    ll_tag  = _norm(ll_row.get('tag'))
    diffs: list[str] = []
    for key, label in (('size', 'Size'), ('service_code', 'Service'), ('spec', 'Spec')):
        p = str(pid_row.get(key) if key != 'service_code' else pid_row.get('service') or '').strip()
        l = str(ll_row.get(key) or '').strip()
        if p and l and not _attrs_equivalent(p.upper(), l.upper()):
            diffs.append(f'{label}: P&ID={p!r} vs LineList={l!r}')
    return {
        'kind': FINDING_MISMATCH,
        'severity': SEVERITY_ERROR,
        'tag': ll_tag,
        'pid_tag': pid_tag,
        'line_list_tag': ll_tag,
        'message': 'Same serial, different attributes: ' + '; '.join(diffs) if diffs
                    else 'Same serial appears with a different composite tag',
        'line_list_row': ll_row.get('excel_row'),
    }


def _finding_missing(tag: str, ll_row: dict) -> dict:
    return {
        'kind': FINDING_MISSING_ON_PID,
        'severity': SEVERITY_ERROR,
        'tag': tag,
        'pid_tag': None,
        'line_list_tag': tag,
        'message': 'Line is in the master Line List but no matching tag was found on the P&ID',
        'line_list_row': ll_row.get('excel_row'),
        'from_ref': ll_row.get('from_ref') or '',
        'to_ref':   ll_row.get('to_ref') or '',
        'fluid_service': ll_row.get('fluid_service') or '',
    }


def _finding_extra(tag: str, pid_row: dict) -> dict:
    return {
        'kind': FINDING_EXTRA_ON_PID,
        'severity': SEVERITY_WARNING,
        'tag': tag,
        'pid_tag': tag,
        'line_list_tag': None,
        'message': 'Tag was extracted from the P&ID but is not present in the master Line List',
    }


# ═════════════════════════════════════════════════════════════════════
# AI enrichment
# ═════════════════════════════════════════════════════════════════════

def _enrich_with_ai(findings: list[dict], provider: str, api_key: str, *, meter=None) -> None:
    missing = [f for f in findings if f['kind'] == FINDING_MISSING_ON_PID][:AI_MAX_TAGS_PER_SIDE]
    extra   = [f for f in findings if f['kind'] == FINDING_EXTRA_ON_PID][:AI_MAX_TAGS_PER_SIDE]
    if not missing and not extra:
        return

    user_prompt = (
        "P&ID EXTRACTED TAGS (EXTRA — on drawing, not in Line List):\n"
        + '\n'.join(f'  • {f["tag"]}' for f in extra) + '\n\n'
        "LINE LIST TAGS (MISSING — in Line List, not found on drawing):\n"
        + '\n'.join(f'  • {f["tag"]}   [from: {f.get("from_ref","")} → to: {f.get("to_ref","")}]'
                    for f in missing) + '\n\n'
        "Correlate them where possible. Respond with the JSON array as specified."
    )
    raw, in_t, out_t = _call_ai(provider, api_key, user_prompt)
    if meter is not None:
        from .vision_extractor import VISION_MODELS
        meter.add(provider, VISION_MODELS[provider], in_t, out_t)
    parsed = _extract_json_array(raw)

    # Index findings by tag for fast update
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
    collapse, junk-char strip). A bare .strip().upper() (the previous
    implementation) doesn't even collapse INTERNAL whitespace — a P&ID
    extraction reading "6-FL AC3N-8183" or "V-803 -TF" against a Line
    List/Equipment List cell spelled without the stray space would fail to
    match at all, showing up as a false Missing+Extra pair instead of a
    Match. Falls back to the old behavior if the shared helper can't be
    imported for any reason."""
    if v is None:
        return ''
    try:
        from apps.pid_verification_v2.services.comparison_engine import normalize_tag
        return normalize_tag(v)
    except Exception:
        return str(v).strip().upper()


def _summarise(findings: list[dict], n_pid: int, n_ll: int) -> dict:
    matches   = sum(1 for f in findings if f['kind'] == FINDING_MATCH)
    missing   = sum(1 for f in findings if f['kind'] == FINDING_MISSING_ON_PID)
    extras    = sum(1 for f in findings if f['kind'] == FINDING_EXTRA_ON_PID)
    mismatches= sum(1 for f in findings if f['kind'] == FINDING_MISMATCH)
    return {
        'pid_total': n_pid,
        'line_list_total': n_ll,
        'match': matches,
        'missing_on_pid': missing,
        'extra_on_pid': extras,
        'mismatch': mismatches,
        'coverage_pct': round(100.0 * matches / n_ll, 1) if n_ll else 0.0,
    }
