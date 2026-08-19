"""
DCS / Instrument Symbol Compliance Analyzer
============================================
Two-stage AI pipeline that:

  Stage 1 — EXTRACT (Gemini → OpenAI fallback)
    • Reads the uploaded legend/instrument-sheet PDF and extracts ALL 48+
      instrument symbol definitions from the "INSTRUMENTS" section.
    • Returns a structured registry: { symbol_code, description, dcs_connection,
      bubble_type, signal_line, body_symbol }.

  Stage 2 — ANALYZE (OpenAI GPT-4o Vision → Gemini fallback)
    • Sends the P&ID drawing image(s) + the extracted symbol registry to AI.
    • AI identifies every instrument circle/bubble on the drawing and checks:
        1. Bubble type correct (circle / circle+square / split-circle / etc.)?
        2. DCS/PCS connection shown correctly (dashed line to top/bottom/side)?
        3. Signal line type correct (electrical / pneumatic / data-link)?
        4. Function letters valid per ISA 5.1?
        5. Instrument loop consistency (transmitter → controller → valve linkage)?
    • Returns a list of PIDVFinding-compatible finding dicts.

All thresholds, prompts, and model names are soft-coded constants.
No magic numbers inline.

Usage (called from the view):
    from apps.pid_verification.services.dcs_symbol_analyzer import run_dcs_analysis
    findings = run_dcs_analysis(drawing_obj, legend_file_path=None)
"""

import base64
import io
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Soft-coded: model names (override via env vars) ───────────────────────────
GEMINI_MODEL        = os.getenv('GEMINI_VISION_MODEL',  'gemini-2.0-flash')
OPENAI_VISION_MODEL = os.getenv('OPENAI_VISION_MODEL',  'gpt-4o')

# Soft-coded: max tokens per AI call
EXTRACT_MAX_TOKENS  = 16384  # OpenAI per-batch legend extraction — increased from 8192
ANALYZE_MAX_TOKENS  = 8192   # P&ID analysis — complex multi-instrument drawing

# Soft-coded: legend extraction batching
GEMINI_EXTRACT_BATCH  = 2    # legend pages per Gemini call (2 pages ≈ 4 k output tokens, fits 8 k limit)
OPENAI_EXTRACT_BATCH  = 3    # legend pages per OpenAI call (3 pages ≈ 6 k output tokens, fits 16 k limit)

# Soft-coded: image render resolution for P&ID pages (DPI scale factor for PyMuPDF 2×=~150dpi)
PDF_RENDER_SCALE    = 2.5    # higher gives better instrument bubble visibility
IMAGE_MAX_PX        = 3000   # cap to avoid API payload limits

# Soft-coded: minimum legend symbols needed before we trust extraction result
MIN_INSTRUMENT_SYMBOLS = 5

# Soft-coded: single-letter ISA first-function-letter set (for quick DCS check)
DCS_FUNCTION_LETTERS = {'F', 'T', 'P', 'L', 'A', 'Q', 'D', 'W', 'Z', 'S', 'H', 'J'}

# ── Severity mapping from AI response ────────────────────────────────────────
_SEV_MAP = {
    'critical': 'critical',
    'major': 'major',
    'minor': 'minor',
    'info': 'info',
    'low': 'minor',
    'medium': 'major',
    'high': 'critical',
}

# =============================================================================
# STAGE 1 — EXTRACT INSTRUMENT SYMBOLS FROM LEGEND
# =============================================================================

_LEGEND_EXTRACT_SYSTEM = (
    "You are an expert P&ID legend reader for oil & gas engineering projects. "
    "You extract instrument symbol definitions with 100% accuracy. "
    "Your output MUST be valid JSON only — no markdown, no commentary."
)

_LEGEND_EXTRACT_USER = """
You are given one or more pages from a P&ID Legend / Instrument Legend sheet.

Your task: extract EVERY instrument symbol defined under the section titled
"INSTRUMENTS" (or similar: "Instrument Symbols", "Instrumentation Symbols",
"Measurement Instruments", etc.).

For each symbol extract:
  - symbol_code       : exact code as printed (e.g. FT, PT, LT, TT, AT, FIC, PIC…)
  - description       : full description (e.g. "Flow Transmitter", "Pressure Indicator Controller")
  - bubble_type       : circle | circle_with_line | square_in_circle | diamond | hexagon | other
  - dcs_connection    : "connected to DCS/PCS" | "local" | "field" | "SIS/ESD" | "not specified"
  - signal_line       : "electric_4_20mA" | "pneumatic" | "data_link" | "not_specified"
  - isa_first_letter  : single letter (F, P, T, L, A, S, Z, W, H, J, Q, D, or other)
  - isa_subsequent    : subsequent letter(s) (I, T, C, R, CV, V, S, etc.)

Also extract general instrument connection conventions:
  - dcs_bubble_convention : description of how DCS-connected instruments are shown
  - sis_bubble_convention : description of how SIS/ESD-connected instruments are shown
  - local_bubble_convention : for locally-mounted instruments

Return ONLY this JSON structure:
{
  "instruments": [
    {
      "symbol_code": "FT",
      "description": "Flow Transmitter",
      "bubble_type": "circle",
      "dcs_connection": "connected to DCS/PCS",
      "signal_line": "electric_4_20mA",
      "isa_first_letter": "F",
      "isa_subsequent": "T"
    }
  ],
  "dcs_bubble_convention": "Circle with horizontal line dividing upper/lower half — upper half shows tag, lower half shows loop number",
  "sis_bubble_convention": "Circle with square around it or double circle",
  "local_bubble_convention": "Circle without connecting line above",
  "total_symbols_found": 48,
  "extraction_confidence": "high | medium | low"
}

Important:
- Include ALL symbols, including indicators (PI, FI, TI, LI…), controllers (PIC, FIC, TIC…),
  transmitters (PT, FT, TT, LT…), switches (PS, FS, LS, TS…), recorders, gauges, analysers.
- Do NOT skip any symbol even if description seems obvious.
- If a symbol appears multiple times with different bubble styles, list each separately.
- Be exhaustive — extract EVERY row visible on this page. Do not stop early.
""".strip()

# =============================================================================
# STAGE 2 — ANALYZE P&ID DRAWING FOR FINDINGS
# =============================================================================

_ANALYZE_SYSTEM = (
    "You are a senior instrument engineer with 20+ years of P&ID verification experience "
    "for oil & gas projects. You review P&IDs against the project instrument symbol legend "
    "and identify findings related to the Distributed Control System (DCS/PCS), "
    "instrument bubble symbols, signal lines, and ISA 5.1 compliance. "
    "Output MUST be valid JSON only."
)

def _build_analyze_prompt(legend_symbols: list, conventions: dict) -> str:
    legend_text = json.dumps(legend_symbols, indent=2)
    conv_text   = json.dumps(conventions, indent=2)
    return f"""
You are reviewing a P&ID drawing for instrument symbol compliance and DCS connections.

=== PROJECT LEGEND: INSTRUMENT SYMBOLS ===
{legend_text}

=== CONNECTION CONVENTIONS ===
{conv_text}

=== YOUR TASK ===
Carefully examine the P&ID drawing image and identify ALL issues in the following categories:

1. DCS/CONTROL SYSTEM FINDINGS
   - Instruments that should be DCS-connected but are shown as field/local
   - Missing dashed connection lines to DCS/PCS bubble
   - Wrong signal line type (pneumatic shown where electric expected, or vice versa)
   - Instruments missing from DCS but of a type that should be in DCS

2. INSTRUMENT BUBBLE SYMBOL FINDINGS
   - Wrong bubble shape used (circle vs square vs diamond vs hexagon)
   - Missing or incorrect horizontal line inside bubble (DCS upper/lower split)
   - SIS bubbles without square surround
   - Symbol doesn't match the legend definition for that instrument type

3. ISA 5.1 FUNCTION LETTER FINDINGS
   - First letter doesn't match the measured variable (e.g. "PT" used for temperature)
   - Invalid subsequent letters (e.g. "FTC" — no such function in ISA 5.1)
   - Repeated/conflicting function letters in same loop

4. INSTRUMENT LOOP CONSISTENCY
   - Transmitter present but no corresponding indicator or controller
   - Control valve (FCV, PCV, TCV, LCV) without a controller
   - High/High-High alarm (PSHH, LSHH) defined without Low-Low alarm when spec requires both
   - Shutdown valve (SDV, XV) without associated ESD/SIS interlock symbol

5. MISSING INSTRUMENT FINDINGS
   - Required safety instruments not shown (e.g. PSV on pressure vessel but no PSV tag on P&ID)
   - Flow measurement missing on key lines
   - Temperature not measured on heat exchanger outlet

For EACH finding return:
{{
  "category": "dcs" | "bubble_symbol" | "isa_function_letter" | "loop_consistency" | "missing_instrument",
  "severity": "critical" | "major" | "minor" | "info",
  "tag": "TAG-NUMBER or N/A",
  "issue_observed": "Concise description of the finding (max 200 chars)",
  "action_required": "What the engineer must do to resolve this",
  "evidence": "Exact text / location / description from drawing (OCR text or visual location)",
  "direction": "Horizontal | Vertical | N/A",
  "rule_id": "DCS-001 | DCS-002 | BUBBLE-001 | ISA-001 | LOOP-001 | MISSING-001 (etc.)"
}}

Return ONLY this JSON:
{{
  "findings": [ ... ],
  "summary": {{
    "total_instruments_checked": <number>,
    "dcs_connected": <number>,
    "local_mounted": <number>,
    "sis_connected": <number>,
    "issues_found": <number>,
    "analysis_confidence": "high | medium | low",
    "notes": "any caveats"
  }}
}}

Be thorough. A typical well-drawn P&ID has 15–40 instrument findings when reviewed rigorously.
Focus on REAL issues, not formatting preferences.
""".strip()


# =============================================================================
# INTERNAL HELPERS — image rendering
# =============================================================================

def _drawing_to_images(drawing_obj) -> list[str]:
    """
    Convert the drawing's source page to base64-JPEG image(s).
    Returns list of base64-encoded strings.
    """
    images = []
    try:
        import fitz
        from PIL import Image

        # Resolve the django file field path
        doc_file = drawing_obj.document.original_file
        if not doc_file:
            logger.warning('[DCSAnalyzer] No original_file on document')
            return images

        file_path = doc_file.path
        if not Path(file_path).exists():
            logger.warning('[DCSAnalyzer] File not found: %s', file_path)
            return images

        pdf = fitz.open(file_path)
        page_idx = drawing_obj.page_index or 0
        if page_idx >= len(pdf):
            page_idx = 0

        page = pdf[page_idx]
        mat  = fitz.Matrix(PDF_RENDER_SCALE, PDF_RENDER_SCALE)
        pix  = page.get_pixmap(matrix=mat, alpha=False)
        img  = Image.frombytes('RGB', [pix.width, pix.height], pix.samples)

        # Cap dimensions
        w, h = img.size
        if max(w, h) > IMAGE_MAX_PX:
            scale = IMAGE_MAX_PX / max(w, h)
            img   = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=88)
        images.append(base64.b64encode(buf.getvalue()).decode('utf-8'))
        pdf.close()
    except ImportError:
        logger.warning('[DCSAnalyzer] PyMuPDF / Pillow not available')
    except Exception as exc:
        logger.warning('[DCSAnalyzer] Drawing render failed: %s', exc)
    return images


def _legend_pdf_to_images(legend_file_path: str) -> list[str]:
    """Render all pages of the legend PDF to base64-JPEG."""
    images = []
    try:
        import fitz
        from PIL import Image

        pdf = fitz.open(legend_file_path)
        for page in pdf:
            mat = fitz.Matrix(2.0, 2.0)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = Image.frombytes('RGB', [pix.width, pix.height], pix.samples)
            w, h = img.size
            if max(w, h) > IMAGE_MAX_PX:
                scale = IMAGE_MAX_PX / max(w, h)
                img   = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=85)
            images.append(base64.b64encode(buf.getvalue()).decode('utf-8'))
        pdf.close()
    except Exception as exc:
        logger.warning('[DCSAnalyzer] Legend PDF render failed: %s', exc)
    return images


def _safe_json(text: str) -> Optional[dict]:
    """
    Robustly parse a JSON string that may be wrapped in markdown code fences.
    Returns parsed dict or None.
    """
    if not text:
        return None
    # Strip markdown fences
    cleaned = re.sub(r'^```(?:json)?\s*', '', text.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r'\s*```$', '', cleaned.strip(), flags=re.MULTILINE)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Try extracting first {...} block
        match = re.search(r'\{.*\}', cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except Exception:
                pass
    return None


def _merge_dcs_legend_results(results: list) -> Optional[dict]:
    """
    Merge multiple per-batch DCS legend extraction dicts into one.
    Deduplicates by symbol_code (upper-case). Keeps the richer description.
    Preserves convention fields from the first non-empty result.
    """
    if not results:
        return None

    merged_instruments: list = []
    seen: dict = {}  # symbol_code (upper) → index in merged_instruments

    # Collect convention fields from first result that has them
    convention_fields = {
        'dcs_bubble_convention':   '',
        'sis_bubble_convention':   '',
        'local_bubble_convention': '',
        'extraction_confidence':   'high',
    }
    for r in results:
        if r and r.get('dcs_bubble_convention'):
            convention_fields.update({
                'dcs_bubble_convention':   r.get('dcs_bubble_convention', ''),
                'sis_bubble_convention':   r.get('sis_bubble_convention', ''),
                'local_bubble_convention': r.get('local_bubble_convention', ''),
                'extraction_confidence':   r.get('extraction_confidence', 'high'),
            })
            break

    for result in results:
        if not result:
            continue
        for entry in (result.get('instruments') or []):
            code = str(entry.get('symbol_code') or '').strip().upper()
            if not code:
                continue
            if code not in seen:
                seen[code] = len(merged_instruments)
                merged_instruments.append(entry)
            else:
                # Keep richer description
                existing = merged_instruments[seen[code]]
                if len(str(entry.get('description') or '')) > len(str(existing.get('description') or '')):
                    merged_instruments[seen[code]] = entry

    merged = {
        'instruments':            merged_instruments,
        'total_symbols_found':    len(merged_instruments),
    }
    merged.update(convention_fields)
    return merged


# =============================================================================
# STAGE 1 — EXTRACT WITH GEMINI (primary)
# =============================================================================

def _extract_legend_gemini(images: list[str]) -> Optional[dict]:
    """
    Extract instrument symbols from legend pages using Gemini.
    Processes GEMINI_EXTRACT_BATCH pages per call; merges all batch results.
    """
    try:
        from google import genai
        from google.genai import types

        api_key = os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY')
        if not api_key:
            return None

        client       = genai.Client(api_key=api_key)
        batch_results: list = []

        for i in range(0, len(images), GEMINI_EXTRACT_BATCH):
            batch = images[i: i + GEMINI_EXTRACT_BATCH]
            parts = [types.Part.from_text(text=_LEGEND_EXTRACT_USER)]
            for img_b64 in batch:
                raw_bytes = base64.b64decode(img_b64)
                parts.append(types.Part.from_bytes(data=raw_bytes, mime_type='image/jpeg'))

            try:
                response = client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=[types.Content(role='user', parts=parts)],
                    config=types.GenerateContentConfig(
                        system_instruction=_LEGEND_EXTRACT_SYSTEM,
                        max_output_tokens=8192,  # Gemini 2.0 Flash ceiling
                        temperature=0.1,
                    ),
                )
                raw    = response.text or ''
                parsed = _safe_json(raw)
                if parsed and parsed.get('instruments'):
                    count = len(parsed['instruments'])
                    logger.info('[DCSAnalyzer] Gemini legend pages %d–%d: %d symbols',
                                i + 1, min(i + GEMINI_EXTRACT_BATCH, len(images)), count)
                    batch_results.append(parsed)
                else:
                    logger.debug('[DCSAnalyzer] Gemini legend pages %d–%d: empty response',
                                 i + 1, min(i + GEMINI_EXTRACT_BATCH, len(images)))
            except Exception as exc:
                logger.warning('[DCSAnalyzer] Gemini batch %d failed: %s', i // GEMINI_EXTRACT_BATCH + 1, exc)

        if not batch_results:
            return None
        merged = _merge_dcs_legend_results(batch_results)
        logger.info('[DCSAnalyzer] Gemini legend extraction complete: %d total symbols',
                    len((merged or {}).get('instruments', [])))
        return merged
    except Exception as exc:
        logger.warning('[DCSAnalyzer] Gemini legend extract failed: %s', exc)
        return None


# =============================================================================
# STAGE 1 — EXTRACT WITH OPENAI (fallback)
# =============================================================================

def _extract_legend_openai(images: list[str]) -> Optional[dict]:
    """
    Extract instrument symbols from legend pages using OpenAI GPT-4o (fallback).
    Processes OPENAI_EXTRACT_BATCH pages per call; merges all batch results.
    """
    try:
        from openai import OpenAI
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            return None

        client        = OpenAI(api_key=api_key)
        batch_results : list = []

        for i in range(0, len(images), OPENAI_EXTRACT_BATCH):
            batch   = images[i: i + OPENAI_EXTRACT_BATCH]
            content = [{"type": "text", "text": _LEGEND_EXTRACT_USER}]
            for img_b64 in batch:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{img_b64}", "detail": "high"},
                })

            try:
                resp = client.chat.completions.create(
                    model=OPENAI_VISION_MODEL,
                    messages=[
                        {"role": "system",  "content": _LEGEND_EXTRACT_SYSTEM},
                        {"role": "user",    "content": content},
                    ],
                    max_tokens=EXTRACT_MAX_TOKENS,
                    temperature=0.1,
                )
                raw    = resp.choices[0].message.content or ''
                parsed = _safe_json(raw)
                if parsed and parsed.get('instruments'):
                    count = len(parsed['instruments'])
                    logger.info('[DCSAnalyzer] OpenAI legend pages %d–%d: %d symbols',
                                i + 1, min(i + OPENAI_EXTRACT_BATCH, len(images)), count)
                    batch_results.append(parsed)
                else:
                    logger.debug('[DCSAnalyzer] OpenAI legend pages %d–%d: empty response',
                                 i + 1, min(i + OPENAI_EXTRACT_BATCH, len(images)))
            except Exception as exc:
                logger.warning('[DCSAnalyzer] OpenAI batch %d failed: %s', i // OPENAI_EXTRACT_BATCH + 1, exc)

        if not batch_results:
            return None
        merged = _merge_dcs_legend_results(batch_results)
        logger.info('[DCSAnalyzer] OpenAI legend extraction complete: %d total symbols',
                    len((merged or {}).get('instruments', [])))
        return merged
    except Exception as exc:
        logger.warning('[DCSAnalyzer] OpenAI legend extract failed: %s', exc)
        return None


# =============================================================================
# STAGE 2 — ANALYZE WITH OPENAI (primary — better spatial reasoning for P&IDs)
# =============================================================================

def _analyze_drawing_openai(images: list[str], legend_symbols: list, conventions: dict) -> Optional[dict]:
    """Use GPT-4o Vision to analyze the P&ID drawing against extracted legend."""
    try:
        from openai import OpenAI
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            return None

        client   = OpenAI(api_key=api_key)
        prompt   = _build_analyze_prompt(legend_symbols, conventions)

        content = [{"type": "text", "text": prompt}]
        for img_b64 in images:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{img_b64}", "detail": "high"},
            })

        resp = client.chat.completions.create(
            model=OPENAI_VISION_MODEL,
            messages=[
                {"role": "system", "content": _ANALYZE_SYSTEM},
                {"role": "user",   "content": content},
            ],
            max_tokens=ANALYZE_MAX_TOKENS,
            temperature=0.2,
        )
        raw = resp.choices[0].message.content or ''
        return _safe_json(raw)
    except Exception as exc:
        logger.warning('[DCSAnalyzer] OpenAI analysis failed: %s', exc)
        return None


# =============================================================================
# STAGE 2 — ANALYZE WITH GEMINI (fallback)
# =============================================================================

def _analyze_drawing_gemini(images: list[str], legend_symbols: list, conventions: dict) -> Optional[dict]:
    """Use Gemini Vision to analyze the P&ID drawing (fallback)."""
    try:
        from google import genai
        from google.genai import types

        api_key = os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY')
        if not api_key:
            return None

        client = genai.Client(api_key=api_key)
        prompt = _build_analyze_prompt(legend_symbols, conventions)

        parts = [types.Part.from_text(text=prompt)]
        for img_b64 in images:
            raw_bytes = base64.b64decode(img_b64)
            parts.append(types.Part.from_bytes(data=raw_bytes, mime_type='image/jpeg'))

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[types.Content(role='user', parts=parts)],
            config=types.GenerateContentConfig(
                system_instruction=_ANALYZE_SYSTEM,
                max_output_tokens=ANALYZE_MAX_TOKENS,
                temperature=0.2,
            ),
        )
        raw = response.text or ''
        return _safe_json(raw)
    except Exception as exc:
        logger.warning('[DCSAnalyzer] Gemini analysis failed: %s', exc)
        return None


# =============================================================================
# STAGE 2 — DETERMINISTIC FALLBACK (no AI available)
# =============================================================================

def _analyze_drawing_deterministic(drawing_obj, legend_symbols: list) -> list:
    """
    Rule-based fallback when both AI providers fail.
    Uses the tag_positions and findings already extracted to infer DCS issues.
    """
    findings = []
    metadata = drawing_obj.metadata or {}
    tag_positions = metadata.get('tag_positions', {})

    # Build a set of all controller tags present on drawing
    tag_set = set()
    for tag in tag_positions.keys():
        upper = tag.upper()
        if re.match(r'^[A-Z]{2,6}-\d', upper):
            tag_set.add(upper)

    # Soft-coded: ISA function letters that SHOULD be DCS-connected on oil & gas P&IDs
    DCS_MANDATORY = {'FT', 'PT', 'TT', 'LT', 'AT', 'FIC', 'PIC', 'TIC', 'LIC',
                     'FCV', 'PCV', 'TCV', 'LCV', 'SDV', 'XV', 'ZT', 'ZI'}

    # Rule: No DCS-mandatory instrument but their counterpart found
    for tag in tag_set:
        prefix = re.match(r'^([A-Z]{2,6})', tag)
        if prefix and prefix.group(1) in DCS_MANDATORY:
            loop_id_match = re.search(r'(\d{3,5})', tag)
            if loop_id_match:
                loop_id = loop_id_match.group(1)
                # Check if transmitter exists without controller
                if tag.startswith('FT') and not any(f'FIC-{loop_id}' in t or f'FC-{loop_id}' in t for t in tag_set):
                    findings.append({
                        'category': 'loop_consistency',
                        'severity': 'major',
                        'tag': tag,
                        'issue_observed': f'Flow transmitter {tag} has no corresponding flow controller (FIC/FC) in loop {loop_id}',
                        'action_required': 'Add FIC/FC tag to complete the control loop or confirm open-loop design',
                        'evidence': f'Tag {tag} in tag_positions; no FIC-{loop_id} or FC-{loop_id} found',
                        'direction': 'N/A',
                        'rule_id': 'LOOP-001',
                    })

    return findings


# =============================================================================
# MAIN PUBLIC ENTRY POINT
# =============================================================================

def run_dcs_analysis(drawing_obj, legend_file_path: Optional[str] = None) -> list:
    """
    Full two-stage DCS/instrument symbol compliance analysis.

    Args:
        drawing_obj:       PIDVDrawing instance with document + metadata attached.
        legend_file_path:  Optional path to the instrument legend PDF.
                           If None, extraction is skipped and a built-in ISA 5.1
                           symbol set is used as the reference.

    Returns:
        List of finding dicts (PIDVFinding-compatible):
          category, severity, tag, issue_observed, action_required,
          evidence, direction, rule_id
    """
    logger.info('[DCSAnalyzer] Starting DCS analysis for drawing=%s', drawing_obj.drawing_id)

    # ── Stage 1: Extract legend symbols ───────────────────────────────────────
    legend_symbols  = []
    conventions     = {}

    if legend_file_path and Path(legend_file_path).exists():
        logger.info('[DCSAnalyzer] Extracting symbols from legend: %s', legend_file_path)
        leg_images = _legend_pdf_to_images(legend_file_path)

        # Gemini first (cheaper, 1M context = handles multi-page legend well)
        leg_result = _extract_legend_gemini(leg_images)
        if not leg_result or len(leg_result.get('instruments', [])) < MIN_INSTRUMENT_SYMBOLS:
            logger.info('[DCSAnalyzer] Gemini legend extract insufficient, falling back to OpenAI')
            leg_result = _extract_legend_openai(leg_images)

        if leg_result:
            legend_symbols = leg_result.get('instruments', [])
            conventions = {
                'dcs_bubble':   leg_result.get('dcs_bubble_convention', ''),
                'sis_bubble':   leg_result.get('sis_bubble_convention', ''),
                'local_bubble': leg_result.get('local_bubble_convention', ''),
            }
            logger.info('[DCSAnalyzer] Legend extracted: %d symbols, confidence=%s',
                        len(legend_symbols), leg_result.get('extraction_confidence', 'unknown'))
        else:
            logger.warning('[DCSAnalyzer] Both AI providers failed for legend extraction; using ISA defaults')

    # ── Stage 1b: ISA 5.1 default symbol set (when no legend available) ────────
    # Soft-coded: standard ISA 5.1 instrument symbol registry used as fallback.
    if not legend_symbols:
        legend_symbols = _ISA_DEFAULT_SYMBOLS

    # ── Stage 2: Render drawing to images ─────────────────────────────────────
    pid_images = _drawing_to_images(drawing_obj)

    if not pid_images:
        logger.warning('[DCSAnalyzer] Could not render P&ID page — using deterministic fallback')
        raw_findings = _analyze_drawing_deterministic(drawing_obj, legend_symbols)
        return _finalize_findings(raw_findings, drawing_obj)

    # ── Stage 2a: OpenAI Vision analysis (primary) ────────────────────────────
    logger.info('[DCSAnalyzer] Running OpenAI Vision analysis')
    analysis_result = _analyze_drawing_openai(pid_images, legend_symbols, conventions)

    # ── Stage 2b: Gemini Vision analysis (fallback) ───────────────────────────
    if not analysis_result or not analysis_result.get('findings'):
        logger.info('[DCSAnalyzer] OpenAI analysis insufficient, falling back to Gemini')
        analysis_result = _analyze_drawing_gemini(pid_images, legend_symbols, conventions)

    # ── Stage 2c: Deterministic fallback ──────────────────────────────────────
    if not analysis_result or not analysis_result.get('findings'):
        logger.warning('[DCSAnalyzer] Both AI providers failed for analysis; using deterministic fallback')
        raw_findings = _analyze_drawing_deterministic(drawing_obj, legend_symbols)
        return _finalize_findings(raw_findings, drawing_obj)

    raw_findings = analysis_result.get('findings', [])
    summary      = analysis_result.get('summary', {})
    logger.info('[DCSAnalyzer] AI analysis complete: %d findings, confidence=%s',
                len(raw_findings), summary.get('analysis_confidence', 'unknown'))

    return _finalize_findings(raw_findings, drawing_obj)


# =============================================================================
# FINDINGS POST-PROCESSOR
# =============================================================================

# Soft-coded: maps AI category names to PIDVFinding.Category values
_CAT_MAP = {
    'dcs':                  'tag',
    'bubble_symbol':        'tag',
    'isa_function_letter':  'tag',
    'loop_consistency':     'connectivity',
    'missing_instrument':   'tag',
    # pass-through for already-correct values
    'tag':                  'tag',
    'connectivity':         'connectivity',
    'valve':                'valve',
    'line_size':            'line_size',
    'notes':                'notes',
}


def _finalize_findings(raw: list, drawing_obj) -> list:
    """
    Normalize AI raw output into PIDVFinding-compatible dicts.
    Validates required fields, maps categories + severities, enforces uniqueness.
    """
    seen     = set()
    results  = []

    for item in raw:
        if not isinstance(item, dict):
            continue

        issue = (item.get('issue_observed') or '').strip()
        if not issue:
            continue

        # Deduplicate on (tag, issue) — AI sometimes returns duplicates
        key = (item.get('tag', ''), issue[:80])
        if key in seen:
            continue
        seen.add(key)

        sev_raw  = (item.get('severity') or 'major').lower()
        cat_raw  = (item.get('category') or 'tag').lower()
        sev      = _SEV_MAP.get(sev_raw, 'major')
        cat      = _CAT_MAP.get(cat_raw, 'tag')

        results.append({
            'category':        cat,
            'severity':        sev,
            'tag':             (item.get('tag') or 'N/A').strip(),
            'issue_observed':  issue[:500],
            'action_required': (item.get('action_required') or 'Review and update drawing').strip()[:500],
            'evidence':        (item.get('evidence') or '').strip()[:500],
            'direction':       (item.get('direction') or 'N/A').strip()[:100],
            'rule_id':         (item.get('rule_id') or 'DCS-000').strip()[:50],
            'source':          'dcs_analysis',   # marks these as AI-generated DCS findings
        })

    return results


# =============================================================================
# BUILT-IN ISA 5.1 DEFAULT SYMBOL SET (used when no legend PDF supplied)
# =============================================================================
# Soft-coded: standard ISA 5.1 instrument categories.
# These cover the 48 common instrument symbols found in typical ADNOC / IOC P&ID legends.

_ISA_DEFAULT_SYMBOLS = [
    # ── Temperature ──
    {"symbol_code": "TE",   "description": "Temperature Element",         "bubble_type": "circle", "dcs_connection": "field",              "isa_first_letter": "T", "isa_subsequent": "E"},
    {"symbol_code": "TT",   "description": "Temperature Transmitter",     "bubble_type": "circle", "dcs_connection": "connected to DCS/PCS","isa_first_letter": "T", "isa_subsequent": "T"},
    {"symbol_code": "TI",   "description": "Temperature Indicator",       "bubble_type": "circle", "dcs_connection": "local",              "isa_first_letter": "T", "isa_subsequent": "I"},
    {"symbol_code": "TIC",  "description": "Temp. Indicator Controller",  "bubble_type": "circle", "dcs_connection": "connected to DCS/PCS","isa_first_letter": "T", "isa_subsequent": "IC"},
    {"symbol_code": "TIT",  "description": "Temp. Indicator Transmitter", "bubble_type": "circle", "dcs_connection": "connected to DCS/PCS","isa_first_letter": "T", "isa_subsequent": "IT"},
    # ── Pressure ──
    {"symbol_code": "PE",   "description": "Pressure Element",            "bubble_type": "circle", "dcs_connection": "field",              "isa_first_letter": "P", "isa_subsequent": "E"},
    {"symbol_code": "PT",   "description": "Pressure Transmitter",        "bubble_type": "circle", "dcs_connection": "connected to DCS/PCS","isa_first_letter": "P", "isa_subsequent": "T"},
    {"symbol_code": "PI",   "description": "Pressure Indicator",          "bubble_type": "circle", "dcs_connection": "local",              "isa_first_letter": "P", "isa_subsequent": "I"},
    {"symbol_code": "PIC",  "description": "Press. Indicator Controller", "bubble_type": "circle", "dcs_connection": "connected to DCS/PCS","isa_first_letter": "P", "isa_subsequent": "IC"},
    {"symbol_code": "PIT",  "description": "Press. Indicator Transmitter","bubble_type": "circle", "dcs_connection": "connected to DCS/PCS","isa_first_letter": "P", "isa_subsequent": "IT"},
    {"symbol_code": "PSV",  "description": "Pressure Safety Valve",       "bubble_type": "diamond","dcs_connection": "not specified",       "isa_first_letter": "P", "isa_subsequent": "SV"},
    {"symbol_code": "PSH",  "description": "Pressure Switch High",        "bubble_type": "circle", "dcs_connection": "SIS/ESD",           "isa_first_letter": "P", "isa_subsequent": "SH"},
    {"symbol_code": "PSL",  "description": "Pressure Switch Low",         "bubble_type": "circle", "dcs_connection": "SIS/ESD",           "isa_first_letter": "P", "isa_subsequent": "SL"},
    {"symbol_code": "PSHH", "description": "Pressure Switch High-High",   "bubble_type": "circle_with_line", "dcs_connection": "SIS/ESD","isa_first_letter": "P", "isa_subsequent": "SHH"},
    {"symbol_code": "PSLL", "description": "Pressure Switch Low-Low",     "bubble_type": "circle_with_line", "dcs_connection": "SIS/ESD","isa_first_letter": "P", "isa_subsequent": "SLL"},
    {"symbol_code": "PSAL", "description": "Pressure Switch Alarm Low",   "bubble_type": "circle", "dcs_connection": "connected to DCS/PCS","isa_first_letter": "P", "isa_subsequent": "SAL"},
    {"symbol_code": "PSDL", "description": "Pressure Switch Diff Low",    "bubble_type": "circle", "dcs_connection": "connected to DCS/PCS","isa_first_letter": "P", "isa_subsequent": "SDL"},
    # ── Differential Pressure ──
    {"symbol_code": "DPIT", "description": "Diff. Press. Indicator Trans.","bubble_type": "circle","dcs_connection": "connected to DCS/PCS","isa_first_letter": "D", "isa_subsequent": "PIT"},
    {"symbol_code": "DPI",  "description": "Diff. Press. Indicator",      "bubble_type": "circle", "dcs_connection": "local",              "isa_first_letter": "D", "isa_subsequent": "PI"},
    # ── Flow ──
    {"symbol_code": "FE",   "description": "Flow Element",                "bubble_type": "circle", "dcs_connection": "field",              "isa_first_letter": "F", "isa_subsequent": "E"},
    {"symbol_code": "FT",   "description": "Flow Transmitter",            "bubble_type": "circle", "dcs_connection": "connected to DCS/PCS","isa_first_letter": "F", "isa_subsequent": "T"},
    {"symbol_code": "FI",   "description": "Flow Indicator",              "bubble_type": "circle", "dcs_connection": "local",              "isa_first_letter": "F", "isa_subsequent": "I"},
    {"symbol_code": "FIC",  "description": "Flow Indicator Controller",   "bubble_type": "circle", "dcs_connection": "connected to DCS/PCS","isa_first_letter": "F", "isa_subsequent": "IC"},
    {"symbol_code": "FIT",  "description": "Flow Indicator Transmitter",  "bubble_type": "circle", "dcs_connection": "connected to DCS/PCS","isa_first_letter": "F", "isa_subsequent": "IT"},
    {"symbol_code": "FCV",  "description": "Flow Control Valve",          "bubble_type": "diamond","dcs_connection": "connected to DCS/PCS","isa_first_letter": "F", "isa_subsequent": "CV"},
    # ── Level ──
    {"symbol_code": "LE",   "description": "Level Element",               "bubble_type": "circle", "dcs_connection": "field",              "isa_first_letter": "L", "isa_subsequent": "E"},
    {"symbol_code": "LT",   "description": "Level Transmitter",           "bubble_type": "circle", "dcs_connection": "connected to DCS/PCS","isa_first_letter": "L", "isa_subsequent": "T"},
    {"symbol_code": "LI",   "description": "Level Indicator",             "bubble_type": "circle", "dcs_connection": "local",              "isa_first_letter": "L", "isa_subsequent": "I"},
    {"symbol_code": "LIC",  "description": "Level Indicator Controller",  "bubble_type": "circle", "dcs_connection": "connected to DCS/PCS","isa_first_letter": "L", "isa_subsequent": "IC"},
    {"symbol_code": "LIT",  "description": "Level Indicator Transmitter", "bubble_type": "circle", "dcs_connection": "connected to DCS/PCS","isa_first_letter": "L", "isa_subsequent": "IT"},
    {"symbol_code": "LCV",  "description": "Level Control Valve",         "bubble_type": "diamond","dcs_connection": "connected to DCS/PCS","isa_first_letter": "L", "isa_subsequent": "CV"},
    {"symbol_code": "LHS",  "description": "Level High Switch",           "bubble_type": "circle", "dcs_connection": "SIS/ESD",           "isa_first_letter": "L", "isa_subsequent": "HS"},
    # ── Shutdown / Safety Valves ──
    {"symbol_code": "SDV",  "description": "Shutdown Valve",              "bubble_type": "diamond","dcs_connection": "SIS/ESD",           "isa_first_letter": "S", "isa_subsequent": "DV"},
    {"symbol_code": "XV",   "description": "Actuated Valve (Generic)",    "bubble_type": "diamond","dcs_connection": "connected to DCS/PCS","isa_first_letter": "X", "isa_subsequent": "V"},
    {"symbol_code": "HV",   "description": "Hand Control Valve",          "bubble_type": "diamond","dcs_connection": "field",              "isa_first_letter": "H", "isa_subsequent": "V"},
    # ── Miscellaneous ──
    {"symbol_code": "ST",   "description": "Speed Transmitter",           "bubble_type": "circle", "dcs_connection": "connected to DCS/PCS","isa_first_letter": "S", "isa_subsequent": "T"},
    {"symbol_code": "ZT",   "description": "Position Transmitter",        "bubble_type": "circle", "dcs_connection": "connected to DCS/PCS","isa_first_letter": "Z", "isa_subsequent": "T"},
    {"symbol_code": "ZI",   "description": "Position Indicator",          "bubble_type": "circle", "dcs_connection": "local",              "isa_first_letter": "Z", "isa_subsequent": "I"},
    {"symbol_code": "AI",   "description": "Analysis Indicator",          "bubble_type": "circle", "dcs_connection": "connected to DCS/PCS","isa_first_letter": "A", "isa_subsequent": "I"},
    {"symbol_code": "AT",   "description": "Analysis Transmitter",        "bubble_type": "circle", "dcs_connection": "connected to DCS/PCS","isa_first_letter": "A", "isa_subsequent": "T"},
    {"symbol_code": "AIC",  "description": "Analysis Indicator Controller","bubble_type": "circle","dcs_connection": "connected to DCS/PCS","isa_first_letter": "A", "isa_subsequent": "IC"},
    {"symbol_code": "WI",   "description": "Weight / Flow Indicator",     "bubble_type": "circle", "dcs_connection": "local",              "isa_first_letter": "W", "isa_subsequent": "I"},
    {"symbol_code": "JI",   "description": "Power Indicator",             "bubble_type": "circle", "dcs_connection": "local",              "isa_first_letter": "J", "isa_subsequent": "I"},
    {"symbol_code": "HS",   "description": "Hand Switch",                 "bubble_type": "circle", "dcs_connection": "field",              "isa_first_letter": "H", "isa_subsequent": "S"},
    {"symbol_code": "HIC",  "description": "Hand Indicator Controller",   "bubble_type": "circle", "dcs_connection": "field",              "isa_first_letter": "H", "isa_subsequent": "IC"},
]
