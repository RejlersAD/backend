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


def cross_check(
    pid_equipment_tags: list[str],
    equipment_list_rows: list[dict],
    *,
    use_ai: bool = False,
    ai_provider: Optional[str] = None,
    ai_api_key: Optional[str] = None,
) -> dict:
    """Return summary + list of findings comparing P&ID equipment tags to Equipment List."""
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
    if use_ai and ai_provider and ai_api_key:
        try:
            _enrich_with_ai(findings, ai_provider, ai_api_key)
            ai_used = True
        except Exception:
            logger.exception('AI equipment cross-check enrichment failed — falling back to deterministic result')

    summary = _summarise(findings, len(pid_by_tag), len(el_by_tag))
    return {
        'summary': summary,
        'findings': findings,
        'ai_used': ai_used,
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
    return {
        'pid_total': n_pid,
        'equipment_list_total': n_el,
        'match': matches,
        'missing_on_pid': missing,
        'extra_on_pid': extras,
        'coverage_pct': round(100.0 * matches / n_el, 1) if n_el else 0.0,
    }
