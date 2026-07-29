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
from typing import Optional

logger = logging.getLogger(__name__)


# ─── Soft-coded config ────────────────────────────────────────────────
SEVERITY_INFO = 'info'
SEVERITY_WARNING = 'warning'
SEVERITY_ERROR = 'error'

FINDING_MISSING_ON_PID = 'missing_on_pid'
FINDING_EXTRA_ON_PID   = 'extra_on_pid'
FINDING_MATCH          = 'match'

AI_MAX_TAGS_PER_SIDE = 40
AI_MAX_TOKENS = 2048
AI_TEMPERATURE = 0.0

AI_SYSTEM_PROMPT = (
    "You are an instrumentation engineering QA reviewer. You are given two "
    "lists of instrument tags: (A) tags read from a P&ID drawing, and (B) "
    "tags from the master Instrument Index (Excel).  Some tags in list A "
    "may correspond to tags in list B despite typos, OCR errors, or minor "
    "formatting differences (e.g. 'LT-8019 TF' vs 'LT-8019TF', or "
    "confusion between 'PT-8003A' and 'PT-8003ATF'). For each MISSING "
    "(list-B tag not seen on the drawing) and each EXTRA (list-A tag not "
    "in the Instrument Index), suggest the most likely correlated "
    "counterpart if any, plus a short reason and a confidence "
    "low|medium|high.  Respond with ONLY a JSON array of objects with "
    "keys: kind ('missing_on_pid'|'extra_on_pid'), tag, suggested_match, "
    "reason, confidence."
)


def cross_check(
    pid_instrument_tags: list[str],
    instrument_index_rows: list[dict],
    *,
    use_ai: bool = False,
    ai_provider: Optional[str] = None,
    ai_api_key: Optional[str] = None,
) -> dict:
    """Return summary + list of findings comparing P&ID instrument tags to Instrument Index."""
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

    ai_used = False
    if use_ai and ai_provider and ai_api_key:
        try:
            _enrich_with_ai(findings, ai_provider, ai_api_key)
            ai_used = True
        except Exception:
            logger.exception(
                'AI instrument cross-check enrichment failed — falling back to deterministic result'
            )

    summary = _summarise(findings, len(pid_by_tag), len(ii_by_tag))
    return {
        'summary': summary,
        'findings': findings,
        'ai_used': ai_used,
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

def _enrich_with_ai(findings: list[dict], provider: str, api_key: str) -> None:
    missing = [f for f in findings if f['kind'] == FINDING_MISSING_ON_PID][:AI_MAX_TAGS_PER_SIDE]
    extra   = [f for f in findings if f['kind'] == FINDING_EXTRA_ON_PID][:AI_MAX_TAGS_PER_SIDE]
    if not missing and not extra:
        return

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


def _summarise(findings: list[dict], n_pid: int, n_ii: int) -> dict:
    matches  = sum(1 for f in findings if f['kind'] == FINDING_MATCH)
    missing  = sum(1 for f in findings if f['kind'] == FINDING_MISSING_ON_PID)
    extras   = sum(1 for f in findings if f['kind'] == FINDING_EXTRA_ON_PID)
    return {
        'pid_total': n_pid,
        'instrument_index_total': n_ii,
        'match': matches,
        'missing_on_pid': missing,
        'extra_on_pid': extras,
        'coverage_pct': round(100.0 * matches / n_ii, 1) if n_ii else 0.0,
    }
