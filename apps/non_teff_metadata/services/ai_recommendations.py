"""
Non-TEFF Smart Recommendations service.

A lightweight AI advisor that runs **on-demand** (per item, on hover) and
returns concise, actionable suggestions about a single document:

  • Inferred document type / discipline (when blank)
  • Likely missing fields the reviewer should verify
  • Quality / consistency issues spotted in the extracted row
  • Cross-reference hints (related tags, suggested next actions)

Design goals (matches the user's brief):

  • **Smart & easy** — single endpoint, single round-trip, results are
    rendered as a small hover card. No new tables, no schema change.
  • **Soft-coded** — every threshold, prompt and provider lives in
    ``RECO_CONFIG`` below.
  • **Cost-aware** — Gemini-first (free tier), OpenAI fallback. Results
    are cached in-memory by item_id + content hash so a hover that
    re-fires never repeats the call.
  • **Additive only** — never mutates the row; the caller decides what
    to do with the suggestions.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SOFT-CODED configuration
# ---------------------------------------------------------------------------

RECO_CONFIG: Dict[str, Any] = {
    # Master switch
    'enabled': True,

    # Cost-first provider chain — same shape as vision_extractor.VISION_CONFIG.
    'providers': [
        {'provider': 'gemini', 'model': 'gemini-2.0-flash',  'enabled': True},
        {'provider': 'gemini', 'model': 'gemini-1.5-flash',  'enabled': True},
        {'provider': 'openai', 'model': 'gpt-4o-mini',       'enabled': True},
    ],
    'temperature': 0.2,
    'max_tokens':  600,
    'timeout_s':   30,

    # Cache cap — recommendations are tiny dicts so a generous cap is fine.
    'cache_enabled': True,
    'cache_max_entries': 512,

    # Which row fields we send to the model. Keep this short — fewer tokens,
    # less chance of leaking irrelevant data into the prompt.
    'context_fields': [
        'file_name', 'document_number', 'document_title', 'document_type',
        'discipline', 'tag', 'equipment_no', 'unit', 'area',
        'project_title', 'revision', 'revision_status', 'issue_date',
        'originator', 'vendor_name', 'po_no', 'contractor_ref', 'vendor_ref',
        'status',
    ],

    # Minimum non-empty context fields required before we even try the AI.
    # If the row is essentially empty there is nothing to reason about.
    'min_context_fields': 2,

    # Document-text excerpt sent to the model so two files with similar
    # metadata still produce distinct recommendations. This is the single
    # biggest accuracy lever — keep big enough to be unique, small enough
    # to be cheap.
    'text_excerpt_chars':       2400,   # max chars of body text
    'text_excerpt_head_chars':  1400,   # bias toward the title block area
    'text_excerpt_tail_chars':  1000,   # plus the tail (often has rev/notes)
    'text_min_unique_chars':     120,   # below this, skip AI (heuristic only)

    # Document-type lexicon — soft-coded keyword → (type, discipline). Order
    # matters: more specific patterns appear first.
    'type_lexicon': [
        # (regex, inferred_type, discipline)
        (r'\bp\s*&\s*id\b|piping\s+and\s+instrument',  'P&ID',                 'process'),
        (r'\bpfd\b|process\s+flow\s+diagram',          'PFD',                  'process'),
        (r'\bsld\b|single[\s-]?line\s+diagram',        'Single Line Diagram',  'electrical'),
        (r'\bgad?\b|general\s+arrangement',            'General Arrangement',  'mechanical'),
        (r'\biso(metric)?\b|piping\s+iso',             'Isometric',            'piping'),
        (r'\bmto\b|material\s+take[\s-]?off',          'Material Take-Off',    'piping'),
        (r'\bmr\b|material\s+requisition',             'Material Requisition', 'piping'),
        (r'\bdatasheet\b|data\s*sheet',                'Datasheet',            ''),
        (r'instrument\s+index|loop\s+index',           'Instrument Index',     'instrument'),
        (r'cable\s+schedule|cable\s+list',              'Cable Schedule',       'electrical'),
        (r'load\s+(list|schedule)',                     'Load List',            'electrical'),
        (r'line\s+list',                                'Line List',            'piping'),
        (r'equipment\s+list',                           'Equipment List',       'process'),
        (r'specification|\bspec\b',                     'Specification',        ''),
        (r'philosoph(y|ies)',                           'Philosophy Document',  'process'),
        (r'cause\s*&?\s*effect',                        'Cause & Effect Chart', 'instrument'),
        (r'sld|elementary\s+wiring',                    'Wiring Diagram',       'electrical'),
        (r'\bhazop\b',                                  'HAZOP Report',         'process'),
        (r'\brfq\b|request\s+for\s+quotation',         'RFQ',                  ''),
        (r'\bpo\b\s*\d|purchase\s+order',              'Purchase Order',       ''),
    ],

    # Discipline keyword booster — used when type_lexicon didn't decide.
    'discipline_lexicon': {
        'electrical':  [r'transformer', r'switchgear', r'\bmcc\b', r'\bvfd\b',
                        r'\bups\b', r'busbar', r'cable\s*tray'],
        'instrument': [r'\bdcs\b', r'\bplc\b', r'\besd\b', r'transmitter',
                       r'control\s+valve', r'\borifice\b'],
        'piping':     [r'\bspool\b', r'\bflange\b', r'\bgasket\b', r'\bweld\b',
                       r'\bnozzle\b', r'piping\s+class'],
        'process':    [r'separator', r'reactor', r'distillation', r'feed\s+stream',
                       r'\bfluid\b', r'mass\s+balance'],
        'mechanical': [r'pump', r'compressor', r'heat\s+exchanger', r'vessel',
                       r'\btank\b', r'\brotor\b'],
        'civil':      [r'foundation', r'concrete', r'rebar', r'soil',
                       r'reinforcement'],
    },

    # Total recommendations capped — the hover card stays digestible.
    'max_recommendations': 5,
    'max_summary_chars':   180,

    # Hardened prompt — refuses to invent values.
    'system_prompt': (
        "You are a senior document-control engineer reviewing a single "
        "engineering document's extracted metadata. Your job is to provide "
        "concise, actionable recommendations to the reviewer. NEVER invent "
        "values. Base every observation on the supplied JSON only. Keep "
        "language professional and brief. Output strictly JSON matching the "
        "requested schema."
    ),

    # Response schema — sent verbatim so models stick to it.
    'response_schema_hint': (
        '{\n'
        '  "summary":          "<one short sentence describing the document>",\n'
        '  "inferred_type":    "<best-guess document type, blank if unsure>",\n'
        '  "discipline":       "<process|piping|electrical|instrument|mechanical|civil|other|>",\n'
        '  "confidence":       "<low|medium|high>",\n'
        '  "missing_fields":   ["<column_key_1>", "<column_key_2>", ...],\n'
        '  "quality_flags":    ["<short observation 1>", "<short observation 2>"],\n'
        '  "next_actions":     ["<imperative suggestion 1>", "<imperative suggestion 2>"]\n'
        '}'
    ),
}


# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------

_RECO_CACHE: Dict[str, Dict[str, Any]] = {}


def _cache_get(key: str) -> Optional[Dict[str, Any]]:
    if not RECO_CONFIG.get('cache_enabled'):
        return None
    return _RECO_CACHE.get(key)


def _cache_put(key: str, value: Dict[str, Any]) -> None:
    if not RECO_CONFIG.get('cache_enabled'):
        return
    cap = int(RECO_CONFIG.get('cache_max_entries', 512))
    if len(_RECO_CACHE) >= cap:
        try:
            _RECO_CACHE.pop(next(iter(_RECO_CACHE)))
        except StopIteration:
            pass
    _RECO_CACHE[key] = value


def _hash_context(item_id: str, ctx: Dict[str, Any]) -> str:
    blob = json.dumps(ctx, sort_keys=True, ensure_ascii=False, default=str)
    return f"{item_id}:{hashlib.sha256(blob.encode('utf-8')).hexdigest()[:16]}"


# ---------------------------------------------------------------------------
# Provider keys
# ---------------------------------------------------------------------------

def _gemini_api_key() -> Optional[str]:
    return (
        os.getenv('GEMINI_API_KEY')
        or os.getenv('GOOGLE_GENERATIVEAI_API_KEY')
        or getattr(settings, 'GEMINI_API_KEY', None)
    )


def _openai_api_key() -> Optional[str]:
    return os.getenv('OPENAI_API_KEY') or getattr(settings, 'OPENAI_API_KEY', None)


# ---------------------------------------------------------------------------
# Prompt + parsing
# ---------------------------------------------------------------------------

def _build_user_prompt(context: Dict[str, Any], text_excerpt: str = '') -> str:
    ctx_json = json.dumps(context, indent=2, ensure_ascii=False, default=str)
    excerpt_block = ''
    if text_excerpt:
        # Trim again defensively — never let the prompt balloon.
        cap = int(RECO_CONFIG['text_excerpt_chars'])
        snippet = text_excerpt[:cap]
        excerpt_block = (
            "\nRaw text excerpt extracted from the document (use this as the "
            "primary source of truth — the metadata above may be sparse):\n"
            f"```text\n{snippet}\n```\n"
        )
    return (
        "Here is the extracted metadata for ONE engineering document.\n\n"
        f"```json\n{ctx_json}\n```\n"
        f"{excerpt_block}\n"
        "Produce recommendations as a JSON object matching this exact schema:\n\n"
        f"{RECO_CONFIG['response_schema_hint']}\n\n"
        "Rules:\n"
        f"- summary: at most {RECO_CONFIG['max_summary_chars']} characters; "
        "reference at least one concrete fact (tag, equipment, drawing no.) "
        "taken from the metadata or excerpt.\n"
        f"- missing_fields, quality_flags, next_actions: at most "
        f"{RECO_CONFIG['max_recommendations']} items each, each item \u2264 100 chars.\n"
        "- Only flag fields that are clearly missing or inconsistent given the "
        "rest of the data. Do NOT invent values.\n"
        "- If the excerpt clearly indicates a document type (e.g. P&ID, "
        "Datasheet, SLD), set inferred_type accordingly.\n"
        "- Output ONLY the JSON object — no prose, no markdown."
    )


def _parse_json_response(content: str) -> Dict[str, Any]:
    if not content:
        return {}
    s = content.strip()
    if s.startswith('```'):
        s = re.sub(r'^```(?:json)?\s*', '', s)
        s = re.sub(r'\s*```$', '', s)
    m = re.search(r'\{.*\}', s, re.DOTALL)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _normalise(reco: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce + cap fields; drops anything outside the agreed schema."""
    cap = int(RECO_CONFIG['max_recommendations'])
    summary_cap = int(RECO_CONFIG['max_summary_chars'])

    def _str(v: Any) -> str:
        return '' if v is None else str(v).strip()

    def _str_list(v: Any) -> List[str]:
        if not isinstance(v, list):
            return []
        return [str(x).strip() for x in v if str(x).strip()][:cap]

    out = {
        'summary':        _str(reco.get('summary'))[:summary_cap],
        'inferred_type':  _str(reco.get('inferred_type')),
        'discipline':     _str(reco.get('discipline')).lower(),
        'confidence':     _str(reco.get('confidence')).lower() or 'medium',
        'missing_fields': _str_list(reco.get('missing_fields')),
        'quality_flags':  _str_list(reco.get('quality_flags')),
        'next_actions':   _str_list(reco.get('next_actions')),
    }
    # Discipline whitelist — anything else collapses to 'other'.
    allowed = {'process', 'piping', 'electrical', 'instrument',
               'mechanical', 'civil', 'other', ''}
    if out['discipline'] not in allowed:
        out['discipline'] = 'other'
    if out['confidence'] not in {'low', 'medium', 'high'}:
        out['confidence'] = 'medium'
    return out


# ---------------------------------------------------------------------------
# Provider calls
# ---------------------------------------------------------------------------

def _call_gemini(model: str, user_prompt: str) -> Dict[str, Any]:
    api_key = _gemini_api_key()
    if not api_key:
        return {}
    try:
        from google import genai
        from google.genai import types as _gtypes
    except ImportError:
        return {}
    try:
        client = genai.Client(api_key=api_key)
        cfg = _gtypes.GenerateContentConfig(
            system_instruction=RECO_CONFIG['system_prompt'],
            max_output_tokens=int(RECO_CONFIG['max_tokens']),
            temperature=float(RECO_CONFIG['temperature']),
            response_mime_type='application/json',
            seed=42,
        )
        resp = client.models.generate_content(
            model=model, contents=[user_prompt], config=cfg,
        )
        return _parse_json_response(getattr(resp, 'text', '') or '')
    except Exception as exc:
        logger.warning('Reco Gemini call failed (%s): %s', model, exc)
        return {}


def _call_openai(model: str, user_prompt: str) -> Dict[str, Any]:
    api_key = _openai_api_key()
    if not api_key:
        return {}
    try:
        from openai import OpenAI
    except ImportError:
        return {}
    try:
        client = OpenAI(
            api_key=api_key,
            timeout=float(RECO_CONFIG['timeout_s']),
            max_retries=1,
        )
        resp = client.chat.completions.create(
            model=model,
            temperature=float(RECO_CONFIG['temperature']),
            max_tokens=int(RECO_CONFIG['max_tokens']),
            response_format={'type': 'json_object'},
            messages=[
                {'role': 'system', 'content': RECO_CONFIG['system_prompt']},
                {'role': 'user',   'content': user_prompt},
            ],
        )
        return _parse_json_response(resp.choices[0].message.content or '')
    except Exception as exc:
        logger.warning('Reco OpenAI call failed (%s): %s', model, exc)
        return {}


def _dispatch(user_prompt: str) -> Tuple[str, Dict[str, Any]]:
    for entry in RECO_CONFIG.get('providers', []):
        if not entry.get('enabled'):
            continue
        provider = entry.get('provider')
        model = entry.get('model')
        if not provider or not model:
            continue
        if provider == 'gemini':
            data = _call_gemini(model, user_prompt)
        elif provider == 'openai':
            data = _call_openai(model, user_prompt)
        else:
            continue
        if data:
            return f'{provider}:{model}', data
    return '', {}


# ---------------------------------------------------------------------------
# Heuristic fallback (zero-cost) — always populated so the UI is never empty.
# ---------------------------------------------------------------------------

def _classify_via_lexicon(haystack: str) -> Tuple[str, str]:
    """Return (inferred_type, discipline) using soft-coded lexicons."""
    if not haystack:
        return '', ''
    h = haystack.lower()
    inferred, discipline = '', ''
    for pat, t, d in RECO_CONFIG.get('type_lexicon', []):
        try:
            if re.search(pat, h, re.IGNORECASE):
                inferred = t
                if d:
                    discipline = d
                break
        except re.error:
            continue
    if not discipline:
        scores: Dict[str, int] = {}
        for disc, pats in RECO_CONFIG.get('discipline_lexicon', {}).items():
            for pat in pats:
                try:
                    if re.search(pat, h, re.IGNORECASE):
                        scores[disc] = scores.get(disc, 0) + 1
                except re.error:
                    continue
        if scores:
            discipline = max(scores, key=scores.get)
    return inferred, discipline


def _heuristic_recommendations(context: Dict[str, Any],
                               text_excerpt: str = '') -> Dict[str, Any]:
    """
    Local pure-Python fallback. Used when no AI provider is reachable, or as
    a baseline that the AI response is merged on top of.

    Now uses the document-text excerpt + lexicons so the result actually
    differs between documents even when their metadata rows are blank.
    """
    missing = [k for k, v in context.items() if not str(v or '').strip()]
    flags: List[str] = []
    actions: List[str] = []
    fname = (context.get('file_name') or '')

    # Combine filename + first portion of the excerpt for classification.
    haystack = f"{fname}\n{text_excerpt[:1500] if text_excerpt else ''}"
    inferred, discipline = _classify_via_lexicon(haystack)

    if not context.get('document_number'):
        actions.append('Confirm and fill the document number.')
    if not context.get('revision'):
        actions.append('Set the latest revision (e.g. A, B, 0, 1).')
    if not context.get('document_title'):
        actions.append('Add a descriptive document title.')

    if context.get('revision') and not context.get('issue_date'):
        flags.append('Revision present but issue date missing.')
    if context.get('vendor_name') and not context.get('po_no'):
        flags.append('Vendor identified but no PO reference recorded.')

    # Build a uniqueness-aware summary so the heuristic card varies per doc.
    bits: List[str] = []
    if inferred:
        bits.append(inferred)
    if discipline:
        bits.append(f'({discipline})')
    if context.get('document_number'):
        bits.append(f"Doc {context['document_number']}")
    elif context.get('tag'):
        bits.append(f"Tag {context['tag']}")
    elif fname:
        bits.append(f"file '{fname}'")
    summary = ' \u00b7 '.join(bits) if bits else f"Document '{fname or 'unknown'}' \u2014 metadata sparse, review needed."

    return {
        'summary':        summary,
        'inferred_type':  inferred,
        'discipline':     discipline,
        'confidence':     'medium' if (inferred or discipline) else 'low',
        'missing_fields': missing[:int(RECO_CONFIG['max_recommendations'])],
        'quality_flags':  flags,
        'next_actions':   actions,
    }


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def _build_text_excerpt(text: str) -> str:
    """Pull a head + tail slice so we feed the model unique-per-file content."""
    if not text:
        return ''
    cap_total = int(RECO_CONFIG['text_excerpt_chars'])
    head_n    = int(RECO_CONFIG['text_excerpt_head_chars'])
    tail_n    = int(RECO_CONFIG['text_excerpt_tail_chars'])
    s = re.sub(r'[ \t]+', ' ', text).strip()
    if len(s) <= cap_total:
        return s
    head = s[:head_n]
    tail = s[-tail_n:] if tail_n > 0 else ''
    if tail:
        return f"{head}\n...\n{tail}"
    return head


def recommend_for_item(*, item_id: str, file_name: str,
                       fields: Dict[str, Any],
                       text_excerpt: str = '',
                       sha256: str = '') -> Dict[str, Any]:
    """
    Returns a compact recommendations dict for a single item. The shape is:

        {
          "provider":       "gemini:gemini-2.0-flash" | "heuristic" | ...,
          "summary":        "...",
          "inferred_type":  "...",
          "discipline":     "...",
          "confidence":     "low" | "medium" | "high",
          "missing_fields": [...],
          "quality_flags":  [...],
          "next_actions":   [...]
        }

    ``text_excerpt`` (optional) is the document body text. When supplied it
    becomes the primary source of truth, dramatically improving accuracy on
    sparse/old PDFs whose metadata row is mostly blank. ``sha256`` (optional)
    is mixed into the cache key so two different files never share a cached
    answer even when their visible metadata happens to match.

    The function never raises \u2014 on any error it returns the heuristic
    fallback so the UI always has something to show.
    """
    if not RECO_CONFIG.get('enabled'):
        return {}

    # Build the trimmed context \u2014 only the columns we whitelist.
    raw_context = {'file_name': file_name}
    for key in RECO_CONFIG['context_fields']:
        if key in raw_context:
            continue
        v = (fields or {}).get(key)
        if v is None:
            continue
        s = str(v).strip()
        if s and s.upper() != 'NA':
            raw_context[key] = s

    excerpt = _build_text_excerpt(text_excerpt or '')

    # Cache key: item_id + content hash + sha256 of the file (if known) +
    # short hash of the excerpt so different docs never collide.
    excerpt_sig = ''
    if excerpt:
        excerpt_sig = hashlib.sha256(excerpt.encode('utf-8')).hexdigest()[:12]
    cache_key = _hash_context(item_id, raw_context) + f":{(sha256 or '')[:12]}:{excerpt_sig}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    heur = _heuristic_recommendations(raw_context, excerpt)

    # Decide whether to hit the AI. We need either enough metadata OR enough
    # unique text excerpt \u2014 otherwise the heuristic alone is fine.
    non_empty = sum(1 for v in raw_context.values() if v)
    has_enough_text = len(excerpt) >= int(RECO_CONFIG['text_min_unique_chars'])
    if non_empty < int(RECO_CONFIG['min_context_fields']) and not has_enough_text:
        result = {**heur, 'provider': 'heuristic'}
        _cache_put(cache_key, result)
        return result

    # AI dispatch (cheap, free-tier first).
    provider_label, ai = _dispatch(_build_user_prompt(raw_context, excerpt))
    if ai:
        merged = {**heur, **_normalise(ai)}
        # Keep heuristic's lexicon classification when AI returns blank fields.
        if not merged.get('inferred_type') and heur.get('inferred_type'):
            merged['inferred_type'] = heur['inferred_type']
        if not merged.get('discipline') and heur.get('discipline'):
            merged['discipline'] = heur['discipline']
        merged['provider'] = provider_label
    else:
        merged = {**heur, 'provider': 'heuristic'}

    _cache_put(cache_key, merged)
    return merged
