"""
Instrument Symbol Extractor — AI-Powered
=========================================
Extracts instrumentation symbols from P&ID legend sheets using OpenAI GPT-4o Vision.
Focuses exclusively on the six instrumentation/valve categories:

  1. CONTROL VALVES         — actuated / automated valves (e.g. HV, FV, LV)
  2. MANUAL VALVES          — hand-operated valves (globe, gate, ball, butterfly…)
  3. INSTRUMENTS            — measurement instruments (FT, TT, PT, LT, AT…)
  4. INSTRUMENT TAGGING     — tag-code conventions (function letter tables, ISA 5.1)
  5. EQUIPMENT NUMBERING    — equipment number / tag format breakdown
  6. IN-LINE EQUIPMENT      — in-line items that are not valves/instruments (strainers, sight-glasses…)

Pipeline:
  1. PDF text extraction via PyMuPDF (fast, free, deterministic)
  2. If text too sparse or AI requested → GPT-4o Vision

All thresholds, prompts, and schema keys are soft-coded via module-level constants.
"""
import base64
import io
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Soft-coded: extraction config ─────────────────────────────────────────────
MIN_TEXT_CHARS_FOR_TEXT_ONLY = 300     # chars; below this we fall back to AI Vision
AI_MODEL                     = 'gpt-4o'
GEMINI_MODEL                 = os.getenv('GEMINI_VISION_MODEL', 'gemini-2.0-flash')
AI_IMAGE_MAX_PX              = 4096   # max dimension per page sent to AI (px) — increased from 2048
AI_MAX_TOKENS                = 16384  # max tokens per OpenAI batch call — increased from 4096
# DPI scale factor for rendering PDF pages.  3× base ≈ 216–300 DPI — captures small-font
# symbol tables that are missed at 2× (≈150 DPI).  Was hardcoded 2.0 previously.
AI_IMAGE_DPI_SCALE           = 3.0
GEMINI_BATCH_SIZE            = 1      # legend pages per Gemini call
OPENAI_BATCH_SIZE            = 1      # 1 page per OpenAI call

# Set True to skip the Gemini pass entirely (Gemini always fails when API
# key is invalid/quota exceeded — skipping removes ~25 s of wasted calls).
SKIP_GEMINI_PASS             = True

# Max concurrent OpenAI requests during parallel extraction.
# 4 workers reduces 16 sequential calls from ~90 s to ~25 s.
MAX_PARALLEL_OPENAI_CALLS    = 4

# ── Soft-coded: section heading patterns ──────────────────────────────────────
# Keys = canonical category names used throughout the system
SECTION_HEADING_RE = {
    'control_valves':        r'CONTROL\s+VALVE',
    'manual_valves':         r'MANUAL\s+VALVE|HAND\s+OPERATED|HAND\s+VALVE',
    'instruments':           r'\bINSTRUMENT(?:S|ATION)?\b(?!\s+TAG|\s+NUMB)',
    'instrument_tagging':    r'INSTRUMENT\s+TAG|TAGGING\s+CONVENTION|FUNCTION\s+LETTER|ISA',
    'equipment_numbering':   r'EQUIPMENT\s+NUMB|EQUIP(?:MENT)?\s+TAG|TAG\s+FORMAT',
    'inline_equipment':      r'IN[\s\-]?LINE\s+EQUIP|INLINE\s+EQUIP',
    'electrical_components': r'MOTOR\s+FEEDER|TYPICAL\s+\d|MCC|VSD|VARIABLE\s+SPEED|ELECTRICAL\s+TYPICAL|MOTOR\s+CIRCUIT',
}

# ── Soft-coded: AI system prompt ──────────────────────────────────────────────
_AI_SYSTEM_PROMPT = (
    "You are an expert engineering drawing interpreter for oil & gas projects, "
    "specialising in P&ID legend sheets, instrument symbol tables, and electrical "
    "typical wiring diagrams (MCC, VSD, motor feeders, DCS interface circuits). "
    "Extract every symbol, tag code, component, and abbreviation visible. "
    "Always respond with valid JSON only — no markdown, no explanations."
)

# ── Soft-coded: AI user prompt ─────────────────────────────────────────────────
_AI_USER_PROMPT = r"""
Analyse this engineering drawing page and extract ALL symbols, components, codes, and
technical items into the JSON structure below.

This page may be a P&ID legend table, an electrical wiring typical (e.g. 415V motor
feeder, MCC/VSD circuit), or a mixed-content drawing. Extract content from ALL types:

  1. CONTROL VALVES — actuated / automated valves
  2. MANUAL VALVES  — hand-operated valves
  3. INSTRUMENTS    — measurement devices (transmitters, indicators, controllers, gauges,
                      switches) AND instruments shown in electrical diagrams (speed
                      indicator, current meter, run-hours counter, start/stop signals)
  4. INSTRUMENT TAGGING — tag conventions, ISA function letter definitions
  5. EQUIPMENT NUMBERING — equipment tag format / numbering conventions
  6. IN-LINE EQUIPMENT  — strainers, sight-glasses, vent silencers, non-valve in-line
  7. ELECTRICAL COMPONENTS — every electrical / control item found in motor feeder
                      typicals, MCC schematics, DCS interface drawings, or VSD circuits
                      (e.g. MCC, VSD, BCU, SCMS, motor, contactor, relay, isolator,
                      circuit breaker, DCS I/O card, speed transmitter, etc.)

Be EXHAUSTIVE — process every row, column, symbol, bubble, and label visible.
Do NOT stop early. Every item on the page must be captured in one of the seven categories.

Return ONLY this JSON structure:
{
  "control_valves": [
    {
      "symbol_code":  "HV",
      "description":  "Hand Control Valve",
      "symbol_type":  "control_valve",
      "drawing_standard": "ISA 5.1",
      "attributes": {
        "actuator_type": "hand | pneumatic | electric | hydraulic | solenoid | other",
        "fail_position":  "open | close | last | n/a",
        "body_type":      "globe | gate | ball | butterfly | plug | other"
      }
    }
  ],
  "manual_valves": [
    {
      "symbol_code":  "BV",
      "description":  "Ball Valve",
      "symbol_type":  "manual_valve",
      "drawing_standard": "ISA 5.1",
      "attributes": {
        "body_type":    "ball | gate | globe | butterfly | needle | plug | check | other",
        "end_connection": "flanged | screwed | butt-weld | socket-weld | other"
      }
    }
  ],
  "instruments": [
    {
      "symbol_code":  "FT",
      "description":  "Flow Transmitter",
      "symbol_type":  "transmitter",
      "drawing_standard": "ISA 5.1",
      "attributes": {
        "measurement_variable": "flow | pressure | temperature | level | analysis | other",
        "instrument_function":  "transmitter | indicator | controller | recorder | gauge | switch | other",
        "mounting":             "field | panel | local | DCS"
      }
    }
  ],
  "instrument_tagging": [
    {
      "symbol_code":  "F",
      "description":  "Flow (first letter)",
      "symbol_type":  "function_letter",
      "drawing_standard": "ISA 5.1",
      "attributes": {
        "letter_type": "measured_variable | modifier | readout | output | function",
        "isa_table":   "Table 1 | Table 2 | n/a"
      }
    }
  ],
  "equipment_numbering": [
    {
      "symbol_code":  "E-",
      "description":  "Heat Exchanger prefix",
      "symbol_type":  "equipment_prefix",
      "drawing_standard": "project",
      "attributes": {
        "equipment_class":  "vessel | exchanger | pump | compressor | column | drum | furnace | other",
        "numbering_format": "e.g. E-XXX where XXX is 3-digit sequence"
      }
    }
  ],
  "inline_equipment": [
    {
      "symbol_code":  "STR",
      "description":  "Strainer",
      "symbol_type":  "inline_equipment",
      "drawing_standard": "ISA 5.1",
      "attributes": {
        "item_type": "strainer | sight_glass | vent_silencer | rupture_disc | mixer | other"
      }
    }
  ],
  "electrical_components": [
    {
      "symbol_code":  "MCC",
      "description":  "Motor Control Centre",
      "symbol_type":  "electrical_panel",
      "drawing_standard": "IEC 60617",
      "attributes": {
        "component_class": "panel | drive | motor | contactor | relay | breaker | sensor | controller | isolator | other",
        "connection_to_dcs": "yes | no | n/a",
        "signal_type": "digital_in | digital_out | analog_in | analog_out | hardwired | bus | n/a",
        "voltage_level": "e.g. 415V | 11kV | 24VDC | n/a"
      }
    }
  ],
  "extraction_method": "ai_vision",
  "drawing_standard_overall": "ISA 5.1"
}

Rules:
 - Process the ENTIRE page top-to-bottom — do not skip any section or table.
 - For electrical typical drawings: extract every labelled component in the circuit.
 - Use the exact symbol/tag codes as printed (e.g. HV, MCC, VSD-01, BCU).
 - For drawing_standard use "ISA 5.1" for instruments/valves, "IEC 60617" for electrical.
 - Fill attributes with best available context; leave empty string "" for unknown.
 - Return an empty list [] for any category absent from this page.
""".strip()

# ── Soft-coded: text-extraction section separators ────────────────────────────
# After splitting legend text into lines, these regexes mark the start of each section.
_TEXT_SECTION_STARTERS = {k: re.compile(v, re.IGNORECASE) for k, v in SECTION_HEADING_RE.items()}


# =============================================================================
# Public entry point
# =============================================================================

def extract_instrument_symbols(file_path: str, use_ai: bool = True, pages_b64: list = None) -> dict:
    """
    Extract all instrument / valve / equipment symbols from a legend sheet.

    Args:
        file_path:  Absolute path to the PDF or image file.
        use_ai:     If True and text extraction is sparse, fall back to GPT-4o Vision.
        pages_b64:  Optional pre-rendered page images (base64 strings).  When
                    provided the PDF render step is skipped — callers can render
                    once and share images across multiple extractors.

    Returns:
        A dict with keys:
          control_valves, manual_valves, instruments, instrument_tagging,
          equipment_numbering, inline_equipment, extraction_method, drawing_standard_overall
    """
    path = Path(file_path)
    if not path.exists():
        logger.warning('[InstrExtract] File not found: %s', file_path)
        return _empty_result('file_not_found')

    suffix = path.suffix.lower()

    # ── Stage 1: Text extraction ───────────────────────────────────────────
    raw_text = ''
    if suffix == '.pdf':
        raw_text = _extract_pdf_text(file_path)

    logger.debug('[InstrExtract] Text chars extracted: %d', len(raw_text))

    if len(raw_text) >= MIN_TEXT_CHARS_FOR_TEXT_ONLY and not use_ai:
        result = _parse_text(raw_text)
        result['extraction_method'] = 'text_only'
        return result

    # ── Stage 2: AI Vision ─────────────────────────────────────────────────
    if use_ai:
        try:
            # Use pre-rendered pages if provided; otherwise render now
            if pages_b64 is not None:
                images = pages_b64
            elif suffix == '.pdf':
                images = _pdf_to_images(file_path)
            else:
                images = [_load_image(file_path)]
            ai_result = _ai_vision_extract(images)
            if ai_result:
                logger.info('[InstrExtract] AI extraction succeeded, found %d categories',
                            sum(1 for k in ai_result if isinstance(ai_result.get(k), list) and ai_result[k]))
                return ai_result
        except Exception as exc:
            logger.warning('[InstrExtract] AI Vision failed: %s', exc)

    # ── Fallback: text parse ───────────────────────────────────────────────
    if raw_text:
        result = _parse_text(raw_text)
        result['extraction_method'] = 'text_fallback'
        return result

    return _empty_result('no_content')


# =============================================================================
# PDF / Image helpers
# =============================================================================

def _extract_pdf_text(file_path: str) -> str:
    """Extract all text from every page of the PDF using PyMuPDF."""
    try:
        import fitz  # PyMuPDF
        doc   = fitz.open(file_path)
        pages = [page.get_text('text') for page in doc]
        doc.close()
        return '\n'.join(pages)
    except ImportError:
        logger.warning('[InstrExtract] PyMuPDF not installed; skipping text extraction')
        return ''
    except Exception as exc:
        logger.warning('[InstrExtract] PDF text extraction error: %s', exc)
        return ''


def _pdf_to_images(file_path: str) -> list:
    """
    Render each PDF page to a base-64 encoded JPEG (capped at AI_IMAGE_MAX_PX).
    Returns list of base-64 strings.
    """
    images = []
    try:
        import fitz
        from PIL import Image
        doc = fitz.open(file_path)
        for page in doc:
            mat  = fitz.Matrix(AI_IMAGE_DPI_SCALE, AI_IMAGE_DPI_SCALE)   # soft-coded DPI scale
            pix  = page.get_pixmap(matrix=mat, alpha=False)
            img  = Image.frombytes('RGB', [pix.width, pix.height], pix.samples)
            img  = _resize_image(img, AI_IMAGE_MAX_PX)
            buf  = io.BytesIO()
            img.save(buf, format='JPEG', quality=85)
            images.append(base64.b64encode(buf.getvalue()).decode('utf-8'))
        doc.close()
    except ImportError:
        logger.warning('[InstrExtract] PyMuPDF or Pillow not available for image render')
    except Exception as exc:
        logger.warning('[InstrExtract] PDF→image conversion failed: %s', exc)
    return images


def _load_image(file_path: str) -> str:
    """Load an image file and return as base-64 JPEG string."""
    try:
        from PIL import Image
        img = Image.open(file_path).convert('RGB')
        img = _resize_image(img, AI_IMAGE_MAX_PX)
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=85)
        return base64.b64encode(buf.getvalue()).decode('utf-8')
    except Exception as exc:
        logger.warning('[InstrExtract] Image load failed: %s', exc)
        return ''


def _resize_image(img, max_px: int):
    """Resize image so its largest dimension does not exceed max_px."""
    try:
        from PIL import Image
        w, h = img.size
        if max(w, h) > max_px:
            scale = max_px / max(w, h)
            img   = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    except Exception:
        pass
    return img


# =============================================================================
# AI Vision extraction
# =============================================================================

# ── Soft-coded: category keys used for merge/dedup ──────────────────────────
_MERGEABLE_KEYS = [
    'control_valves', 'manual_valves', 'instruments',
    'instrument_tagging', 'equipment_numbering', 'inline_equipment',
    'electrical_components',
]


def _has_content(result: dict) -> bool:
    """Return True if at least one category list is non-empty."""
    return any(isinstance(result.get(k), list) and result[k] for k in _MERGEABLE_KEYS)


def _merge_extractions(results: list) -> dict:
    """
    Merge multiple per-page extraction dicts into one, deduplicating by symbol_code.
    When the same code appears on multiple pages, the richer description is kept.
    """
    merged: dict = {k: [] for k in _MERGEABLE_KEYS}
    merged['extraction_method']         = 'ai_vision_paginated'
    merged['drawing_standard_overall']  = 'ISA 5.1'

    for cat in _MERGEABLE_KEYS:
        seen: dict = {}  # symbol_code (upper) → index in merged[cat]
        for result in results:
            for entry in (result.get(cat) or []):
                code = str(entry.get('symbol_code') or '').strip().upper()
                if not code:
                    continue
                if code not in seen:
                    seen[code] = len(merged[cat])
                    merged[cat].append(entry)
                else:
                    # Keep the entry with the longer description
                    existing = merged[cat][seen[code]]
                    if len(str(entry.get('description') or '')) > len(str(existing.get('description') or '')):
                        merged[cat][seen[code]] = entry
    return merged


def _extract_batch_gemini(images: list) -> Optional[dict]:
    """
    Call Gemini to extract symbol data from a small batch of legend page images.
    Returns parsed dict or None on failure.
    """
    try:
        from google import genai
        from google.genai import types

        api_key = os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY')
        if not api_key:
            return None

        client = genai.Client(api_key=api_key)
        parts  = [types.Part.from_text(text=_AI_USER_PROMPT)]
        for img_b64 in images:
            parts.append(types.Part.from_bytes(
                data=base64.b64decode(img_b64),
                mime_type='image/jpeg',
            ))

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[types.Content(role='user', parts=parts)],
            config=types.GenerateContentConfig(
                system_instruction=_AI_SYSTEM_PROMPT,
                max_output_tokens=8192,  # Gemini 2.0 Flash ceiling
                temperature=0.0,
            ),
        )
        raw = response.text or ''
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
        raw = re.sub(r'```\s*$',          '', raw, flags=re.MULTILINE)
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.debug('[InstrExtract] Gemini batch JSON parse error: %s', exc)
        return None
    except Exception as exc:
        logger.debug('[InstrExtract] Gemini batch call failed: %s', exc)
        return None


def _extract_batch_openai(images: list) -> Optional[dict]:
    """
    Call OpenAI GPT-4o to extract symbol data from a small batch of legend page images.
    Returns parsed dict or None on failure.
    """
    try:
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            try:
                from django.conf import settings
                api_key = settings.OPENAI_API_KEY
            except Exception:
                pass
        if not api_key:
            return None

        from openai import OpenAI
        client  = OpenAI(api_key=api_key)
        content = [{'type': 'text', 'text': _AI_USER_PROMPT}]
        for b64 in images:
            content.append({
                'type':      'image_url',
                'image_url': {'url': f'data:image/jpeg;base64,{b64}', 'detail': 'high'},
            })

        resp = client.chat.completions.create(
            model=AI_MODEL,
            messages=[
                {'role': 'system', 'content': _AI_SYSTEM_PROMPT},
                {'role': 'user',   'content': content},
            ],
            max_tokens=AI_MAX_TOKENS,
            temperature=0.0,
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
        raw = re.sub(r'```\s*$',          '', raw, flags=re.MULTILINE)
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.debug('[InstrExtract] OpenAI batch JSON parse error: %s', exc)
        return None
    except Exception as exc:
        logger.debug('[InstrExtract] OpenAI batch call failed: %s', exc)
        return None


def _ai_vision_extract(images: list) -> Optional[dict]:
    """
    Extract symbols from ALL legend pages using parallel AI calls.

    Pass 1: Gemini (skipped when SKIP_GEMINI_PASS=True — saves ~25 s of
            guaranteed-fail API calls when no valid key is configured).
    Pass 2: OpenAI — all pages processed concurrently via ThreadPoolExecutor
            with MAX_PARALLEL_OPENAI_CALLS workers (reduces 16 sequential
            calls from ~90 s to ~25 s).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if not images:
        return None

    n = len(images)
    page_results: list = [None] * n

    # ── Pass 1: Gemini (optional) ────────────────────────────────────────
    if not SKIP_GEMINI_PASS:
        for i in range(n):
            result = _extract_batch_gemini([images[i]])
            if result and _has_content(result):
                count = sum(len(result.get(k) or []) for k in _MERGEABLE_KEYS)
                logger.info('[InstrExtract] Gemini page %d/%d: %d items', i + 1, n, count)
                page_results[i] = result
            else:
                logger.info('[InstrExtract] Gemini page %d/%d: empty — queued for OpenAI', i + 1, n)
    else:
        logger.info('[InstrExtract] Gemini pass skipped (SKIP_GEMINI_PASS=True)')

    # ── Pass 2: OpenAI — parallel for all pages still empty ─────────────
    empty_indices = [i for i, r in enumerate(page_results) if r is None]
    if empty_indices:
        logger.info('[InstrExtract] OpenAI processing %d pages in parallel (workers=%d)',
                    len(empty_indices), MAX_PARALLEL_OPENAI_CALLS)
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL_OPENAI_CALLS) as pool:
            future_map = {
                pool.submit(_extract_batch_openai, [images[i]]): i
                for i in empty_indices
            }
            for future in as_completed(future_map):
                i = future_map[future]
                try:
                    result = future.result()
                    if result and _has_content(result):
                        count = sum(len(result.get(k) or []) for k in _MERGEABLE_KEYS)
                        logger.info('[InstrExtract] OpenAI retry pages [%d]: %d items extracted', i + 1, count)
                        page_results[i] = result
                    else:
                        logger.info('[InstrExtract] OpenAI page %d/%d: empty', i + 1, n)
                except Exception as exc:
                    logger.warning('[InstrExtract] OpenAI page %d/%d failed: %s', i + 1, n, exc)

    all_results = [r for r in page_results if r]
    if not all_results:
        return None

    merged = _merge_extractions(all_results)
    total  = sum(len(merged.get(k) or []) for k in _MERGEABLE_KEYS)
    logger.info('[InstrExtract] Paginated extraction complete: %d total items across %d pages',
                total, n)
    return merged


# =============================================================================
# Text-based fallback parser
# =============================================================================

def _parse_text(text: str) -> dict:
    """
    Lightweight text parser for simple legend sheets.
    Splits the text into sections by heading matches; within each section
    extracts rows as (code, description) pairs.
    """
    lines   = [ln.strip() for ln in text.splitlines() if ln.strip()]
    result  = {k: [] for k in SECTION_HEADING_RE}
    current = None

    for line in lines:
        # Check if this line is a section heading
        matched = False
        for cat, pattern in _TEXT_SECTION_STARTERS.items():
            if pattern.search(line):
                current = cat
                matched = True
                break
        if matched:
            continue

        if current and line:
            # Try to parse "CODE  Description text"
            parts = re.split(r'\s{2,}|\t', line, maxsplit=1)
            if len(parts) == 2:
                symbol_code, description = parts[0].strip(), parts[1].strip()
                if symbol_code and description:
                    result[current].append({
                        'symbol_code':      symbol_code,
                        'description':      description,
                        'symbol_type':      '',
                        'drawing_standard': 'ISA 5.1',
                        'attributes':       {},
                    })

    return result


# =============================================================================
# Empty result helper
# =============================================================================

def _empty_result(reason: str) -> dict:
    return {
        'control_valves':        [],
        'manual_valves':         [],
        'instruments':           [],
        'instrument_tagging':    [],
        'equipment_numbering':   [],
        'inline_equipment':      [],
        'electrical_components': [],
        'extraction_method':     reason,
        'drawing_standard_overall': 'ISA 5.1',
    }
