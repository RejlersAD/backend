"""Compare a list of extracted line tags against the active Legend
Sheet, produce structured findings, and optionally enrich them with an
AI diagnosis + suggested correction.

Findings are two-layer:
  1) Deterministic  – regex + lookup checks (fast, offline, always run)
  2) AI (optional)  – Claude/OpenAI is asked to explain each failure
                       in plain English and propose a fix

The deterministic layer never calls the network. The AI layer is
short-circuited when there are no failing tags, keeping cost near-zero
on clean drawings.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from .legend_engine import (
    CompiledLegend,
    build_prompt_block,
    FIELD_KEY_ATTR,
    FIELD_LABEL_ATTR,
    FIELD_LOOKUP_ATTR,
    FIELD_OPTIONAL_ATTR,
    FIELD_SUFFIX_ATTR,
    FIELD_REGEX_ATTR,
)

logger = logging.getLogger(__name__)

# ─── Soft-coded config ────────────────────────────────────────────────
SEVERITY_OK = 'ok'
SEVERITY_WARNING = 'warning'
SEVERITY_ERROR = 'error'

# The AI review is capped to protect user cost / latency
AI_MAX_FAILING_TAGS = 40
AI_MAX_TOKENS = 2048
AI_TEMPERATURE = 0.0

AI_SYSTEM_PROMPT = (
    "You are a piping/instrumentation engineering QA reviewer. You are "
    "given a Line-List legend (naming rules + allowed codes) and a list "
    "of extracted line tags from a P&ID drawing that FAILED to match the "
    "legend. For each failing tag: (a) explain the specific rule violation "
    "in one short sentence, (b) suggest the most likely corrected tag, "
    "(c) rate confidence low|medium|high. Respond with ONLY a JSON array "
    "of objects with the exact keys: tag, diagnosis, suggestion, confidence."
)


def validate_tags(
    tags: list[dict],
    compiled: CompiledLegend,
    *,
    use_ai: bool = False,
    ai_provider: Optional[str] = None,
    ai_api_key: Optional[str] = None,
) -> dict:
    """Main entry — returns {summary, findings, ai_used}."""
    if not isinstance(tags, list):
        raise ValueError('tags must be a list')

    findings: list[dict] = []
    for t in tags:
        findings.append(_diagnose_one(t, compiled))

    # Optional AI diagnosis for failing tags only
    ai_used = False
    if use_ai and ai_provider and ai_api_key:
        failing = [f for f in findings if f['severity'] != SEVERITY_OK]
        if failing:
            try:
                ai_map = _ai_diagnose(failing[:AI_MAX_FAILING_TAGS], compiled, ai_provider, ai_api_key)
                for f in findings:
                    ai = ai_map.get(f['tag'])
                    if ai:
                        f['ai_diagnosis'] = ai.get('diagnosis') or ''
                        f['ai_suggestion'] = ai.get('suggestion') or ''
                        f['ai_confidence'] = ai.get('confidence') or ''
                ai_used = True
            except Exception:
                logger.exception('AI validation failed — returning deterministic findings only')

    summary = _summarise(findings)
    return {
        'summary': summary,
        'findings': findings,
        'ai_used': ai_used,
    }


# ═════════════════════════════════════════════════════════════════════
# Deterministic diagnosis
# ═════════════════════════════════════════════════════════════════════

def _diagnose_one(tag_dict: dict, compiled: CompiledLegend) -> dict:
    tag_text = (tag_dict.get('tag') or '').strip()

    if not tag_text:
        return {
            'tag': '',
            'severity': SEVERITY_ERROR,
            'message': 'Empty tag',
            'field_findings': [],
        }

    m = compiled.pattern.match(tag_text)
    if m:
        return _finding_ok(tag_text, compiled, m)

    # Whole-regex failed — try per-field split so we can point at exactly
    # which field(s) violate the rule.
    return _diagnose_split(tag_text, compiled)


def _finding_ok(tag_text: str, compiled: CompiledLegend, match: re.Match) -> dict:
    parts = match.groups()
    field_findings = []
    unresolved_codes: list[str] = []
    for key, value, field in zip(compiled.field_keys, parts, compiled.fields):
        label = field.get(FIELD_LABEL_ATTR) or key
        entry = {
            'key': key,
            'label': label,
            'value': value or '',
            'ok': True,
        }
        lookup = compiled.lookups.get(key)
        if lookup and value:
            code = value.upper()
            if code in lookup:
                entry['resolved_label'] = lookup[code]
            else:
                entry['ok'] = False
                entry['problem'] = f'Code {code!r} is not in the allowed list for {label}'
                unresolved_codes.append(f'{label}: {code}')
        field_findings.append(entry)

    if unresolved_codes:
        return {
            'tag': tag_text,
            'severity': SEVERITY_WARNING,
            'message': 'Structure matches legend but some codes are unknown: ' + '; '.join(unresolved_codes),
            'field_findings': field_findings,
        }
    return {
        'tag': tag_text,
        'severity': SEVERITY_OK,
        'message': 'Matches legend',
        'field_findings': field_findings,
    }


def _diagnose_split(tag_text: str, compiled: CompiledLegend) -> dict:
    """Slice the tag by separator and diagnose each field individually."""
    sep = compiled.separator
    tokens = tag_text.split(sep)
    n_expected_min = sum(
        1 for f in compiled.fields if not f.get(FIELD_OPTIONAL_ATTR)
    )
    n_expected_max = len(compiled.fields)

    field_findings: list[dict] = []
    problems: list[str] = []

    # Try to align tokens 1:1 with fields. If we have too few, mark trailing
    # required fields as MISSING. If too many, mark extras as EXTRA.
    for i, field in enumerate(compiled.fields):
        key = field.get(FIELD_KEY_ATTR) or f'field_{i}'
        label = field.get(FIELD_LABEL_ATTR) or key
        regex = field.get(FIELD_REGEX_ATTR) or ''
        suffix = field.get(FIELD_SUFFIX_ATTR) or ''
        optional = bool(field.get(FIELD_OPTIONAL_ATTR))
        lookup = compiled.lookups.get(key) or {}

        value = tokens[i] if i < len(tokens) else ''
        entry = {'key': key, 'label': label, 'value': value, 'ok': True}

        if not value:
            if optional:
                entry['ok'] = True
                entry['problem'] = ''
            else:
                entry['ok'] = False
                entry['problem'] = f'Missing required {label}'
                problems.append(entry['problem'])
            field_findings.append(entry)
            continue

        # Peel suffix
        core = value
        if suffix:
            if core.endswith(suffix):
                core = core[: -len(suffix)]
            else:
                entry['ok'] = False
                entry['problem'] = f'{label} should end with {suffix!r}'
                problems.append(entry['problem'])

        # Regex check on the core
        try:
            if regex and not re.fullmatch(regex, core):
                entry['ok'] = False
                p = f'{label} value {core!r} does not match required pattern /{regex}/'
                entry['problem'] = p
                problems.append(p)
        except re.error:
            pass

        # Lookup check
        if lookup:
            code = core.upper()
            if code and code not in lookup:
                entry['ok'] = False
                p = f'{label} code {code!r} is not in the allowed list'
                entry['problem'] = p
                problems.append(p)
            elif code in lookup:
                entry['resolved_label'] = lookup[code]

        field_findings.append(entry)

    # Extra tokens beyond declared fields
    if len(tokens) > n_expected_max:
        extras = tokens[n_expected_max:]
        p = f'Extra segment(s) after last field: {sep.join(extras)}'
        problems.append(p)
        field_findings.append({
            'key': '_extra',
            'label': 'Extra segment',
            'value': sep.join(extras),
            'ok': False,
            'problem': p,
        })

    severity = SEVERITY_ERROR if problems else SEVERITY_WARNING
    message = '; '.join(problems) or 'Does not match legend structure'
    if len(tokens) < n_expected_min:
        message = f'Only {len(tokens)} segment(s) — legend requires at least {n_expected_min}. ' + message
    return {
        'tag': tag_text,
        'severity': severity,
        'message': message,
        'field_findings': field_findings,
    }


def _summarise(findings: list[dict]) -> dict:
    total = len(findings)
    ok = sum(1 for f in findings if f['severity'] == SEVERITY_OK)
    warn = sum(1 for f in findings if f['severity'] == SEVERITY_WARNING)
    err = sum(1 for f in findings if f['severity'] == SEVERITY_ERROR)
    return {
        'total': total,
        'ok': ok,
        'warnings': warn,
        'errors': err,
        'valid_pct': round(100.0 * ok / total, 1) if total else 0.0,
    }


# ═════════════════════════════════════════════════════════════════════
# AI diagnosis
# ═════════════════════════════════════════════════════════════════════

def _ai_diagnose(
    failing: list[dict],
    compiled: CompiledLegend,
    provider: str,
    api_key: str,
) -> dict[str, dict]:
    rules_block = build_prompt_block(compiled)
    failing_lines = []
    for f in failing:
        failing_lines.append(f'  • {f["tag"]}  — issue: {f["message"]}')
    user_prompt = (
        "LEGEND RULES:\n"
        f"{rules_block}\n\n"
        "FAILING TAGS EXTRACTED FROM THE P&ID:\n"
        + '\n'.join(failing_lines)
        + "\n\nRespond with the JSON array as specified in the system prompt."
    )

    raw = _call_ai_text(provider, api_key, user_prompt)
    parsed = _extract_json_array(raw)
    out: dict[str, dict] = {}
    for row in parsed:
        if not isinstance(row, dict):
            continue
        tag = str(row.get('tag') or '').strip()
        if tag:
            out[tag] = {
                'diagnosis': str(row.get('diagnosis') or ''),
                'suggestion': str(row.get('suggestion') or ''),
                'confidence': str(row.get('confidence') or ''),
            }
    return out


def _call_ai_text(provider: str, api_key: str, user_prompt: str) -> str:
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


def _extract_json_array(text: str) -> list[Any]:
    """Robustly pull the first JSON array out of a model response."""
    if not text:
        return []
    # Strip common markdown code fences
    stripped = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip(), flags=re.MULTILINE)
    # Try direct parse
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict) and isinstance(parsed.get('findings'), list):
            return parsed['findings']
    except json.JSONDecodeError:
        pass
    # Fallback: find the first '[' ... ']'
    m = re.search(r'\[[\s\S]*\]', stripped)
    if not m:
        return []
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
