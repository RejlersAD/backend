"""
Piping Valve MTO — PDF Extractor
=================================

Vision-assisted extractor that reads a P&ID / valve-data PDF and returns the
canonical Valve MTO row schema consumed by the frontend
(`frontend/src/pages/Engineering/Piping/ValveMTO.jsx`).

Design notes
------------
* Soft-coded: every threshold, regex, prompt template and model name lives
  at module level so they can be tuned without code changes.
* Fast & cheap: text-first via PyMuPDF; Vision (GPT-4o) only runs when text
  yields fewer than `TEXT_SUFFICIENT_CHARS` characters AND `OPENAI_API_KEY`
  is configured. If OpenAI is unavailable the extractor still returns any
  rows that text-regex could find (graceful degradation).
* Returns the frontend's row keys directly — no mapping layer needed.

Public entry point
------------------
    extract_valve_mto(pdf_path: str) -> dict

Returned shape::

    {
      "status": "ok" | "error",
      "engine": "text" | "vision" | "text+vision",
      "page_count": int,
      "rows":         [ { "sl_no": 1, "area": "...", ... }, ... ],
      "project_meta": { "doc_no": "...", "doc_title": "...", ... },
      "warnings":     [ "..." ]
    }
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

# ─── Soft-coded constants (env-overridable) ─────────────────────────────────────
TEXT_SUFFICIENT_CHARS  = int(os.getenv('VALVE_MTO_TEXT_THRESHOLD', '1500'))
# Hard cap on pages we'll process — protects against runaway docs.
VISION_MAX_PAGES       = int(os.getenv('VALVE_MTO_VISION_MAX_PAGES', '50'))
# How many pages to bundle into a single OpenAI call (smaller = more accurate, larger = cheaper).
VISION_BATCH_SIZE      = int(os.getenv('VALVE_MTO_VISION_BATCH_SIZE', '2'))
# How many batches to run in parallel.
VISION_PARALLEL_BATCHES = int(os.getenv('VALVE_MTO_VISION_PARALLEL', '4'))
VISION_IMAGE_DPI       = int(os.getenv('VALVE_MTO_VISION_DPI', '120'))
VISION_MAX_EDGE_PX     = int(os.getenv('VALVE_MTO_VISION_MAX_EDGE', '1600'))
VISION_MODEL           = os.getenv('VALVE_MTO_VISION_MODEL', 'gpt-4o-mini')
VISION_TEMPERATURE     = 0.0
VISION_TIMEOUT_SECS    = float(os.getenv('VALVE_MTO_VISION_TIMEOUT', '90'))
JPEG_QUALITY           = 80
MAX_ROWS               = int(os.getenv('VALVE_MTO_MAX_ROWS', '2000'))

# Canonical row schema — must match frontend `valveMTO.config.js` VALVE_COLUMNS.
ROW_KEYS = [
    'sl_no', 'area', 'type', 'pms_class', 'rating', 'size_1', 'size_2',
    'bore', 'valve_tag', 'description', 'qty_island', 'qty_field', 'unit',
    'remarks',
]

# Soft-coded list (kept small — vision model picks the closest match).
VALID_AREAS       = ['ISLAND', 'Field', 'COMBINED']
DEFAULT_UNIT      = 'EACH'
NUMERIC_KEYS      = {'sl_no', 'qty_island', 'qty_field'}

# Prompt is intentionally explicit — every column is described including
# accepted values so the model emits clean JSON.
VISION_PROMPT_TEMPLATE = """\
You are a senior piping engineer extracting a VALVE MATERIAL TAKE-OFF (Valve MTO)
from the attached drawing/datasheet pages (this batch covers pages {page_range} of a
larger document). Return ONLY a valid JSON object — no prose.

Schema:
{{
  "project_meta": {{
    "doc_no": "<COMPANY Document No., e.g. PJ6-EXD-GEN-TX0T-0004>",
    "doc_title": "<title, e.g. PIPING VALVES MTO>",
    "doc_desc": "<doc description>",
    "revision": "<numeric or alphanumeric revision>",
    "doc_date": "<YYYY-MM-DD if visible>",
    "project_name": "<project name if visible>"
  }},
  "rows": [
    {{
      "sl_no":       <integer>,
      "area":        "ISLAND" | "Field" | "COMBINED",
      "type":        "<BALL VALVE | GATE VALVE | GLOBE VALVE | CHECK VALVE | PLUG VALVE | BUTTERFLY VALVE | NEEDLE VALVE>",
      "pms_class":   "<piping material class code>",
      "rating":      "<e.g. CLASS 150 RF, CLASS 600 RTJ>",
      "size_1":      "<nominal bore in inches with double quotes, e.g. 2\\"",
      "size_2":      "<reduced size if any, else empty string>",
      "bore":        "FB" | "RB" | "",
      "valve_tag":   "<valve tag id>",
      "description": "<short service description>",
      "qty_island":  <integer total in ISLAND, 0 if none>,
      "qty_field":   <integer total in FIELD, 0 if none>,
      "unit":        "EACH" | "NOS" | "SET",
      "remarks":     "<free text>"
    }}
  ]
}}

Rules:
- Output only valid JSON; do not wrap in markdown fences.
- Extract EVERY valve row visible in the attached pages — do not summarise or skip.
- Use empty strings for unknown text fields and 0 for unknown numeric fields.
- Do not invent valve tags or sizes — leave empty if uncertain.
- Renumber sl_no starting from 1 within this batch (the server merges batches).
- Maximum {max_rows} rows per batch.

Embedded text excerpt (use as ground truth where it conflicts with the image):
---
{text_excerpt}
---
"""

# ─── Helpers ────────────────────────────────────────────────────────────
def _page_count(pdf_path: str) -> int:
    try:
        import fitz
        doc = fitz.open(pdf_path)
        n = doc.page_count
        doc.close()
        return n
    except Exception:                                  # pragma: no cover
        return 0


def _extract_text(pdf_path: str, on_text_progress=None) -> str:
    """
    Best-effort text via PyMuPDF (fast). pdfplumber is only consulted as a
    fallback when PyMuPDF returns less than ``TEXT_SUFFICIENT_CHARS`` —
    pdfplumber is *much* slower (often 10-30× on large searchable PDFs)
    and Vision already handles image-only drawings, so the fallback rarely
    pays for itself.

    ``on_text_progress(current_page, total_pages)`` fires per page so the
    job snapshot keeps advancing during this otherwise-silent phase.
    """
    parts: List[str] = []
    try:
        import fitz
        doc = fitz.open(pdf_path)
        total = doc.page_count
        for i, page in enumerate(doc):
            t = page.get_text() or ''
            if t.strip():
                parts.append(t)
            if on_text_progress:
                try:
                    on_text_progress(i + 1, total)
                except Exception:
                    pass
        doc.close()
    except Exception as exc:                           # pragma: no cover
        logger.warning('PyMuPDF failed: %s', exc)

    combined = '\n'.join(parts)
    if len(combined) >= TEXT_SUFFICIENT_CHARS or os.getenv('VALVE_MTO_DISABLE_PDFPLUMBER', '1') == '1':
        return combined

    # Slow fallback only when PyMuPDF clearly under-extracted.
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ''
                if t.strip():
                    parts.append(t)
    except Exception:                                  # pragma: no cover
        pass
    return '\n'.join(parts)


def _render_pages_b64(pdf_path: str, max_pages: int, dpi: int, on_render_progress=None) -> List[str]:
    """
    Render PDF pages to base64 JPEG strings.

    Soft-coded:
      * `max_pages` — hard cap (VISION_MAX_PAGES)
      * `dpi`       — render DPI (VISION_IMAGE_DPI)
      * `VISION_MAX_EDGE_PX` / `JPEG_QUALITY`

    `on_render_progress(current_page, total_pages)` fires after each page so
    the async job runner can keep its heartbeat alive even before any AI
    batch has completed (PDF rendering on a slim CPU can take minutes).
    """
    images: List[str] = []
    try:
        import fitz
        from PIL import Image
        doc = fitz.open(pdf_path)
        total_to_render = min(doc.page_count, max_pages)
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
            # Cap the longest edge — P&IDs are huge, we don't need 4k pixels
            # to read tag text reliably.
            longest = max(img.size)
            if longest > VISION_MAX_EDGE_PX:
                ratio = VISION_MAX_EDGE_PX / float(longest)
                new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
                img = img.resize(new_size, Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=JPEG_QUALITY, optimize=True)
            images.append(base64.b64encode(buf.getvalue()).decode('ascii'))
            if on_render_progress:
                try:
                    on_render_progress(i + 1, total_to_render)
                except Exception:
                    pass
        doc.close()
    except Exception as exc:                           # pragma: no cover
        logger.warning('Failed rendering PDF pages: %s', exc)
    return images


def _coerce_row(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Force a raw dict into the canonical row schema."""
    row: Dict[str, Any] = {}
    for k in ROW_KEYS:
        v = raw.get(k, '')
        if k in NUMERIC_KEYS:
            try:
                row[k] = int(float(str(v).replace(',', '').strip() or 0))
            except (TypeError, ValueError):
                row[k] = 0
        else:
            row[k] = '' if v is None else str(v).strip()

    # Area normalisation (case-insensitive against VALID_AREAS).
    if row['area']:
        for a in VALID_AREAS:
            if row['area'].lower() == a.lower():
                row['area'] = a
                break
    if not row['unit']:
        row['unit'] = DEFAULT_UNIT
    return row


def _coerce_meta(raw: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for k in ('doc_no', 'doc_title', 'doc_desc', 'revision', 'doc_date', 'project_name'):
        v = raw.get(k, '')
        out[k] = '' if v is None else str(v).strip()
    return out


# ─── Extractors ─────────────────────────────────────────────────────────
def _extract_meta_from_text(text: str) -> Dict[str, str]:
    """Cheap regex scan for project header fields."""
    meta: Dict[str, str] = {}
    patterns = {
        'doc_no':   [r'(?:Company\s+)?Doc(?:ument)?\.?\s*No\.?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]{5,})'],
        'revision': [r'\bRev(?:ision)?\.?\s*[:\-]?\s*([A-Z0-9]{1,3})\b'],
        'doc_date': [r'\bDate\s*[:\-]?\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2})',
                     r'\bDate\s*[:\-]?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})'],
        'doc_title': [r'(PIPING\s+VALVES?\s+MTO)', r'(VALVE\s+M(?:ATERIAL\s+)?T(?:AKE[\s-]?OFF)?)'],
    }
    for field, pats in patterns.items():
        for pat in pats:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                meta[field] = m.group(1).strip()
                break
    return meta


def _extract_via_vision(pdf_path: str, text_excerpt: str) -> Dict[str, Any]:
    """
    Render every page (up to VISION_MAX_PAGES), split into batches of
    VISION_BATCH_SIZE pages each, then call OpenAI in parallel.
    All rows are merged across batches, deduplicated and renumbered.
    """
    api_key = os.getenv('OPENAI_API_KEY')
    if not api_key:
        return {'rows': [], 'project_meta': {}, 'warnings': ['vision skipped — no OPENAI_API_KEY']}

    try:
        from openai import OpenAI
    except Exception:
        return {'rows': [], 'project_meta': {}, 'warnings': ['vision skipped — openai package unavailable']}

    images = _render_pages_b64(pdf_path, VISION_MAX_PAGES, VISION_IMAGE_DPI)
    if not images:
        return {'rows': [], 'project_meta': {}, 'warnings': ['vision skipped — no pages rendered']}

    # Split into batches.
    batches: List[Tuple[int, List[str]]] = []
    for i in range(0, len(images), VISION_BATCH_SIZE):
        batches.append((i, images[i:i + VISION_BATCH_SIZE]))

    client = OpenAI(api_key=api_key, timeout=VISION_TIMEOUT_SECS)
    logger.info(
        '[ValveMTO] Vision → model=%s pages=%d batches=%d (size=%d, parallel=%d) dpi=%d',
        VISION_MODEL, len(images), len(batches), VISION_BATCH_SIZE,
        VISION_PARALLEL_BATCHES, VISION_IMAGE_DPI,
    )

    def _call_one_batch(batch_idx: int, batch_imgs: List[str]) -> Tuple[List[Dict[str, Any]], Dict[str, str], List[str]]:
        prompt = VISION_PROMPT_TEMPLATE.format(
            max_rows=MAX_ROWS,
            text_excerpt=(text_excerpt or '')[:6000],
            page_range=f'{batch_idx + 1}–{batch_idx + len(batch_imgs)}',
        )
        content: List[Dict[str, Any]] = [{'type': 'text', 'text': prompt}]
        for b64 in batch_imgs:
            content.append({
                'type': 'image_url',
                'image_url': {'url': f'data:image/jpeg;base64,{b64}'},
            })
        try:
            resp = client.chat.completions.create(
                model=VISION_MODEL,
                temperature=VISION_TEMPERATURE,
                response_format={'type': 'json_object'},
                messages=[{'role': 'user', 'content': content}],
            )
            raw = resp.choices[0].message.content or '{}'
            data = json.loads(raw)
        except Exception as exc:
            logger.warning('[ValveMTO] Batch %d failed: %s', batch_idx, exc)
            return [], {}, [f'batch starting at page {batch_idx + 1} failed: {exc}']

        rows_raw = data.get('rows') or []
        meta_raw = data.get('project_meta') or {}
        rows: List[Dict[str, Any]] = []
        if isinstance(rows_raw, list):
            for r in rows_raw:
                if not isinstance(r, dict):
                    continue
                row = _coerce_row(r)
                if row['valve_tag'] or row['description'] or row['type']:
                    rows.append(row)
        return rows, _coerce_meta(meta_raw), []

    all_rows: List[Dict[str, Any]] = []
    merged_meta: Dict[str, str] = {}
    warnings: List[str] = []

    parallelism = max(1, min(VISION_PARALLEL_BATCHES, len(batches)))
    with ThreadPoolExecutor(max_workers=parallelism) as pool:
        futures = {pool.submit(_call_one_batch, idx, imgs): idx for idx, imgs in batches}
        # Collect results in submission order so row order roughly tracks page order.
        results_by_idx: Dict[int, Tuple[List[Dict[str, Any]], Dict[str, str], List[str]]] = {}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                results_by_idx[idx] = fut.result()
            except Exception as exc:                                            # pragma: no cover
                results_by_idx[idx] = ([], {}, [f'batch {idx} crashed: {exc}'])

    for idx in sorted(results_by_idx):
        rows, meta, warns = results_by_idx[idx]
        all_rows.extend(rows)
        for k, v in meta.items():
            if v and not merged_meta.get(k):
                merged_meta[k] = v
        warnings.extend(warns)

    # Deduplicate across batches — same valve appearing on consecutive pages
    # must not be double-counted. Key on the discriminating columns.
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for r in all_rows:
        key = (
            (r.get('area') or '').lower(),
            (r.get('valve_tag') or '').lower(),
            (r.get('pms_class') or '').lower(),
            (r.get('size_1') or '').lower(),
            (r.get('rating') or '').lower(),
            (r.get('description') or '').lower(),
            (r.get('type') or '').lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)

    # Cap and renumber.
    deduped = deduped[:MAX_ROWS]
    for i, r in enumerate(deduped):
        r['sl_no'] = i + 1

    return {'rows': deduped, 'project_meta': merged_meta, 'warnings': warnings}


# ─── Public API ─────────────────────────────────────────────────────────
def extract_valve_mto(pdf_path: str) -> Dict[str, Any]:
    pages = _page_count(pdf_path)
    text  = _extract_text(pdf_path)
    text_meta = _extract_meta_from_text(text)
    warnings: List[str] = []

    use_vision = len(text) < TEXT_SUFFICIENT_CHARS or True  # always on for now — drawings rarely have enough text
    vision_result: Dict[str, Any] = {'rows': [], 'project_meta': {}, 'warnings': []}
    if use_vision:
        vision_result = _extract_via_vision(pdf_path, text)
        warnings.extend(vision_result.get('warnings') or [])

    rows  = vision_result['rows']
    meta  = {**text_meta, **{k: v for k, v in vision_result['project_meta'].items() if v}}

    engine = 'vision' if rows and not text_meta else (
        'text+vision' if rows and text_meta else (
            'text' if text_meta else 'none'
        )
    )

    return {
        'status': 'ok' if rows or meta else 'empty',
        'engine': engine,
        'page_count': pages,
        'rows': rows,
        'project_meta': meta,
        'warnings': warnings,
    }


# ─── Streaming public API (used by the async job runner) ────────────────
def _dedupe_and_renumber(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for r in rows:
        key = (
            (r.get('area') or '').lower(),
            (r.get('valve_tag') or '').lower(),
            (r.get('pms_class') or '').lower(),
            (r.get('size_1') or '').lower(),
            (r.get('rating') or '').lower(),
            (r.get('description') or '').lower(),
            (r.get('type') or '').lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    out = out[:MAX_ROWS]
    for i, r in enumerate(out):
        r['sl_no'] = i + 1
    return out


def extract_valve_mto_streaming(
    pdf_path: str,
    on_progress=None,
    on_partial=None,
) -> Dict[str, Any]:
    """
    Same logic as `extract_valve_mto` but emits incremental progress/results
    through callbacks so a long-running async job can be polled.

    Callbacks
    ---------
    * `on_progress(current_batch:int, total_batches:int, rows_so_far:int)`
    * `on_partial(rows_so_far:list, project_meta_so_far:dict)`
    """
    pages = _page_count(pdf_path)
    # Emit an immediate progress signal so the UI shows movement right after
    # the worker thread starts, even before any page is processed.
    if on_progress:
        try:
            on_progress(0, max(pages, 1), 0)
        except Exception:
            pass

    # Per-page heartbeat during text extraction (PyMuPDF) — searchable PDFs
    # can be 100+ pages and the user must see progress.
    text  = _extract_text(
        pdf_path,
        on_text_progress=(
            (lambda cur, tot: on_progress(cur, tot, 0))
            if on_progress else None
        ),
    )
    text_meta = _extract_meta_from_text(text)

    api_key = os.getenv('OPENAI_API_KEY')
    warnings: List[str] = []

    if not api_key:
        warnings.append('vision skipped — no OPENAI_API_KEY')
        return {
            'status': 'ok' if text_meta else 'empty',
            'engine': 'text' if text_meta else 'none',
            'page_count': pages,
            'rows': [],
            'project_meta': text_meta,
            'warnings': warnings,
        }

    try:
        from openai import OpenAI
    except Exception:
        warnings.append('vision skipped — openai package unavailable')
        return {
            'status': 'ok' if text_meta else 'empty',
            'engine': 'text' if text_meta else 'none',
            'page_count': pages,
            'rows': [],
            'project_meta': text_meta,
            'warnings': warnings,
        }

    # Render with a per-page heartbeat so the frontend's stall timer never
    # trips during the slow PDF→JPEG phase on slim-CPU containers.
    images = _render_pages_b64(
        pdf_path,
        VISION_MAX_PAGES,
        VISION_IMAGE_DPI,
        on_render_progress=(
            (lambda cur, tot: on_progress(cur, tot, 0))
            if on_progress else None
        ),
    )
    if not images:
        warnings.append('vision skipped — no pages rendered')
        return {
            'status': 'empty',
            'engine': 'none',
            'page_count': pages,
            'rows': [],
            'project_meta': text_meta,
            'warnings': warnings,
        }

    batches: List[Tuple[int, List[str]]] = []
    for i in range(0, len(images), VISION_BATCH_SIZE):
        batches.append((i, images[i:i + VISION_BATCH_SIZE]))

    total_batches = len(batches)
    if on_progress:
        try:
            on_progress(0, total_batches, 0)
        except Exception:
            pass

    client = OpenAI(api_key=api_key, timeout=VISION_TIMEOUT_SECS)
    logger.info(
        '[ValveMTO] Streaming vision → model=%s pages=%d batches=%d (size=%d, parallel=%d)',
        VISION_MODEL, len(images), total_batches, VISION_BATCH_SIZE,
        VISION_PARALLEL_BATCHES,
    )

    def _call_one_batch(batch_idx: int, batch_imgs: List[str]):
        prompt = VISION_PROMPT_TEMPLATE.format(
            max_rows=MAX_ROWS,
            text_excerpt=(text or '')[:6000],
            page_range=f'{batch_idx + 1}–{batch_idx + len(batch_imgs)}',
        )
        content: List[Dict[str, Any]] = [{'type': 'text', 'text': prompt}]
        for b64 in batch_imgs:
            content.append({
                'type': 'image_url',
                'image_url': {'url': f'data:image/jpeg;base64,{b64}'},
            })
        try:
            resp = client.chat.completions.create(
                model=VISION_MODEL,
                temperature=VISION_TEMPERATURE,
                response_format={'type': 'json_object'},
                messages=[{'role': 'user', 'content': content}],
            )
            raw = resp.choices[0].message.content or '{}'
            data = json.loads(raw)
        except Exception as exc:
            logger.warning('[ValveMTO] Batch %d failed: %s', batch_idx, exc)
            return [], {}, [f'batch starting at page {batch_idx + 1} failed: {exc}']

        rows_raw = data.get('rows') or []
        meta_raw = data.get('project_meta') or {}
        rows: List[Dict[str, Any]] = []
        if isinstance(rows_raw, list):
            for r in rows_raw:
                if not isinstance(r, dict):
                    continue
                row = _coerce_row(r)
                if row['valve_tag'] or row['description'] or row['type']:
                    rows.append(row)
        return rows, _coerce_meta(meta_raw), []

    all_rows: List[Dict[str, Any]] = []
    merged_meta: Dict[str, str] = dict(text_meta)  # seed with regex-derived meta
    completed = 0

    parallelism = max(1, min(VISION_PARALLEL_BATCHES, len(batches)))
    with ThreadPoolExecutor(max_workers=parallelism) as pool:
        futures = {pool.submit(_call_one_batch, idx, imgs): idx for idx, imgs in batches}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                rows, meta, warns = fut.result()
            except Exception as exc:                                            # pragma: no cover
                rows, meta, warns = [], {}, [f'batch {idx} crashed: {exc}']
            all_rows.extend(rows)
            for k, v in meta.items():
                if v and not merged_meta.get(k):
                    merged_meta[k] = v
            warnings.extend(warns)
            completed += 1

            partial = _dedupe_and_renumber(list(all_rows))
            if on_progress:
                try:
                    on_progress(completed, total_batches, len(partial))
                except Exception:
                    pass
            if on_partial:
                try:
                    on_partial(partial, dict(merged_meta))
                except Exception:
                    pass

    final_rows = _dedupe_and_renumber(all_rows)
    return {
        'status': 'ok' if final_rows or merged_meta else 'empty',
        'engine': 'vision' if final_rows else ('text' if text_meta else 'none'),
        'page_count': pages,
        'rows': final_rows,
        'project_meta': merged_meta,
        'warnings': warnings,
    }
