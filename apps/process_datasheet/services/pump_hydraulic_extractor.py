"""
Pump Hydraulic Extractor
========================

Soft-coded extractor that pre-fills the Pump Hydraulic Calculation Datasheet
from a PFD / pump-data PDF.

Design notes
------------
* All regex patterns and value bounds live in `config/pump_hydraulic_extraction.json`.
* The extractor never modifies the calculation logic on the frontend; it only
  returns a flat `{field_name: value}` dictionary plus per-field provenance
  ("text" | "vision") so the UI can show confidence indicators.
* OpenAI Vision is used **only** as a fallback when text extraction yields
  too few characters (e.g. scanned drawings) and an `OPENAI_API_KEY` is
  available.  This keeps cost predictable.

Public entry point
------------------
    extract_pump_hydraulic(pdf_path: str) -> dict

Returned shape::

    {
      "status": "ok" | "error",
      "fields": { "<form_field>": "<string value>", ... },
      "provenance": { "<form_field>": "text" | "vision", ... },
      "warnings": [ ... ],
      "engine": "text" | "text+vision",
      "page_count": int
    }
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Soft-coded constants
# ---------------------------------------------------------------------------
CONFIG_PATH = Path(__file__).resolve().parent.parent / 'config' / 'pump_hydraulic_extraction.json'
_CONFIG_CACHE: Dict[str, Any] | None = None


def _load_config() -> Dict[str, Any]:
    """Load and cache the extraction config from JSON."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None:
        with CONFIG_PATH.open('r', encoding='utf-8') as fh:
            _CONFIG_CACHE = json.load(fh)
    return _CONFIG_CACHE


def reload_config() -> None:
    """Force the next call to re-read the config file."""
    global _CONFIG_CACHE
    _CONFIG_CACHE = None


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------
def _extract_text(pdf_path: str) -> str:
    """Pull text from every page using pdfplumber, falling back to PyMuPDF."""
    text_parts: List[str] = []

    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ''
                if t.strip():
                    text_parts.append(t)
    except Exception as exc:                          # pragma: no cover
        logger.warning('pdfplumber failed for %s: %s', pdf_path, exc)

    # Always also run PyMuPDF — sometimes pdfplumber misses rotated text
    try:
        import fitz
        doc = fitz.open(pdf_path)
        for page in doc:
            t = page.get_text() or ''
            if t.strip():
                text_parts.append(t)
        doc.close()
    except Exception as exc:                          # pragma: no cover
        logger.warning('PyMuPDF failed for %s: %s', pdf_path, exc)

    return '\n'.join(text_parts)


def _page_count(pdf_path: str) -> int:
    try:
        import fitz
        doc = fitz.open(pdf_path)
        n = doc.page_count
        doc.close()
        return n
    except Exception:                                 # pragma: no cover
        return 0


# ---------------------------------------------------------------------------
# Pattern matching
# ---------------------------------------------------------------------------
def _normalise_text(text: str) -> str:
    """Collapse whitespace so regex with `\\s*` works across line breaks."""
    return re.sub(r'[ \t]+', ' ', text)


def _within_bounds(field: str, value: str, validators: Dict[str, Any]) -> bool:
    rule = validators.get(field)
    if not rule:
        return True
    try:
        v = float(value)
    except (TypeError, ValueError):
        return True   # non-numeric, validator does not apply
    return rule.get('min', float('-inf')) <= v <= rule.get('max', float('inf'))


def _match_patterns(text: str, patterns: List[str]) -> str | None:
    for pat in patterns:
        try:
            m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
            if m:
                return (m.group(1) or '').strip().rstrip('.,;:')
        except re.error as exc:
            logger.warning('Bad regex %r: %s', pat, exc)
    return None


def _extract_via_text(text: str, cfg: Dict[str, Any]) -> Tuple[Dict[str, str], List[str]]:
    """Return (fields, warnings) using the JSON pattern table."""
    text = _normalise_text(text)
    field_patterns: Dict[str, List[str]] = cfg.get('field_patterns', {})
    validators: Dict[str, Any] = cfg.get('value_validators', {})

    out: Dict[str, str] = {}
    warnings: List[str] = []
    for field, patterns in field_patterns.items():
        value = _match_patterns(text, patterns)
        if value is None:
            continue
        if not _within_bounds(field, value, validators):
            warnings.append(f'{field}={value!r} dropped (out of bounds)')
            continue
        out[field] = value
    return out, warnings


# ---------------------------------------------------------------------------
# Vision fallback (optional — best effort)
# ---------------------------------------------------------------------------
def _render_pages_to_b64_jpegs(pdf_path: str, max_pages: int, dpi: int) -> List[str]:
    """Render the first N pages to base-64-encoded JPEGs for the vision API."""
    images: List[str] = []
    try:
        import fitz
        from PIL import Image
        doc = fitz.open(pdf_path)
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=85)
            images.append(base64.b64encode(buf.getvalue()).decode('ascii'))
        doc.close()
    except Exception as exc:                          # pragma: no cover
        logger.warning('Failed to render PDF pages: %s', exc)
    return images


def _extract_via_vision(
    pdf_path: str,
    cfg: Dict[str, Any],
    text_already: str,
) -> Tuple[Dict[str, str], List[str]]:
    """
    Send rendered pages + field hints to GPT-4o.  Returns (fields, warnings).
    Silent no-op if the OpenAI client is unavailable.
    """
    vis_cfg = cfg.get('engines', {}).get('vision_fallback', {})
    if not vis_cfg.get('enabled', False):
        return {}, ['vision disabled in config']

    if len(text_already) >= int(vis_cfg.get('skip_if_text_chars_gte', 800)):
        return {}, ['vision skipped — text extraction was sufficient']

    api_key = os.getenv('OPENAI_API_KEY')
    if not api_key:
        return {}, ['vision skipped — no OPENAI_API_KEY']

    try:
        from openai import OpenAI
    except Exception:
        return {}, ['vision skipped — openai package unavailable']

    images = _render_pages_to_b64_jpegs(
        pdf_path,
        max_pages=int(vis_cfg.get('max_pages', 4)),
        dpi=int(vis_cfg.get('image_dpi', 200)),
    )
    if not images:
        return {}, ['vision skipped — no pages rendered']

    hints = cfg.get('vision_field_hints', {}) or {}
    hint_lines = '\n'.join(f'- {k}: {v}' for k, v in hints.items() if not k.startswith('_'))

    prompt = (
        'You are extracting structured data from a process pump PFD / data sheet. '
        'Return ONLY a JSON object whose keys match exactly the field list below. '
        'For numeric fields return the number with no units. If a field is not '
        'visible, omit the key. Do not invent values.\n\n'
        f'Fields:\n{hint_lines}'
    )

    content = [{'type': 'text', 'text': prompt}]
    for b64 in images:
        content.append({
            'type': 'image_url',
            'image_url': {'url': f'data:image/jpeg;base64,{b64}'},
        })

    try:
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=vis_cfg.get('model', 'gpt-4o'),
            temperature=float(vis_cfg.get('temperature', 0.0)),
            response_format={'type': 'json_object'},
            messages=[{'role': 'user', 'content': content}],
        )
        raw = resp.choices[0].message.content or '{}'
        data = json.loads(raw)
    except Exception as exc:                          # pragma: no cover
        return {}, [f'vision call failed: {exc}']

    validators = cfg.get('value_validators', {}) or {}
    out: Dict[str, str] = {}
    warnings: List[str] = []
    for k, v in data.items():
        if v in (None, '', 'N/A', 'n/a'):
            continue
        sv = str(v).strip()
        if not _within_bounds(k, sv, validators):
            warnings.append(f'vision {k}={sv!r} dropped (out of bounds)')
            continue
        out[k] = sv
    return out, warnings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def extract_pump_hydraulic(pdf_path: str) -> Dict[str, Any]:
    """
    Run text + (optional) vision extraction and merge results.

    Text wins where both engines return a value — text is more deterministic.
    """
    cfg = _load_config()
    pages = _page_count(pdf_path)

    raw_text = _extract_text(pdf_path)
    text_fields, text_warnings = _extract_via_text(raw_text, cfg)

    vision_fields, vision_warnings = _extract_via_vision(pdf_path, cfg, raw_text)

    merged: Dict[str, str] = dict(vision_fields)
    merged.update(text_fields)         # text overrides vision

    provenance = {k: ('text' if k in text_fields else 'vision') for k in merged}
    engine = 'text+vision' if vision_fields else 'text'

    return {
        'status': 'ok',
        'engine': engine,
        'page_count': pages,
        'fields': merged,
        'provenance': provenance,
        'warnings': text_warnings + vision_warnings,
    }
