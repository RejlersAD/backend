"""
P&ID valve extractor using Google Gemini Vision.

Strategy (soft-coded via ai_provider.py):
  Primary  → Gemini 2.0 Flash (cheap, 1M token context, fast)
  Fallback → OpenAI gpt-4o Vision (high-accuracy, costly)
  Last     → OCR-only regex pass (no LLM cost)

Call extract_valves_from_pdf() — the provider chain is fully automatic.
"""

import logging
import os
import base64
import json
import re
import io
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

import fitz  # PyMuPDF
from PIL import Image
import pytesseract

from apps.process_datasheet.ai_provider import (
    get_fallback_chain,
    build_gemini_client,
    build_openai_client,
    GEMINI_VISION_MODEL,
    OPENAI_VISION_MODEL,
)
from apps.process_datasheet.tag_validator import (
    VALID_TAG_PREFIXES,
    has_valid_engineering_prefix,
)

logger = logging.getLogger(__name__)

# ─── Drawing number extraction — soft-coded patterns ─────────────────────────
# Patterns are tried in order; first match wins.
# Add or reorder entries here without touching any other code.
_DWG_NUMBER_PATTERNS = [
    # Long compound codes like PJ6-EXD-MRI-BQDA-0022
    re.compile(r'\b([A-Z0-9]{2,8}-[A-Z0-9]{2,8}-[A-Z0-9]{2,8}-[A-Z0-9]{2,8}-\d{3,5})\b'),
    # Three-part codes like PJ6-P&ID-0022 or ABC-XYZ-12345
    re.compile(r'\b([A-Z0-9]{2,8}-[A-Z0-9]{2,8}-\d{3,5})\b'),
    # DWG / Drawing No. labelled values
    re.compile(r'(?:DWG\.?\s*No\.?|Drawing\s*No\.?|DOC\.?\s*No\.?)\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-_]{4,30})', re.IGNORECASE),
    # P&ID No. labelled values
    re.compile(r'(?:P&ID\s*No\.?|PID\s*No\.?)\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-_]{4,30})', re.IGNORECASE),
]


def _extract_drawing_number(ocr_text: str, fallback_filename: str = None) -> str:
    """
    Extract the actual engineering drawing number from OCR text.

    Strategy (soft-coded via _DWG_NUMBER_PATTERNS above):
      1. Try each pattern against the OCR text in order.
      2. Reject hits that look like tag numbers (start with a known valve prefix).
      3. If nothing found, fall back to the filename stem.

    To add a new pattern format, append to _DWG_NUMBER_PATTERNS — no other code changes needed.
    """
    if ocr_text:
        for pattern in _DWG_NUMBER_PATTERNS:
            for match in pattern.finditer(ocr_text):
                candidate = match.group(1).strip()
                # Skip candidates that are just valve/instrument tags
                if has_valid_engineering_prefix(candidate):
                    continue
                # Skip very short or obviously bad hits
                if len(candidate) < 6:
                    continue
                logger.info(f"[DWGExtract] Found drawing number: {candidate} (pattern: {pattern.pattern[:40]})")
                return candidate

    # Fallback: use filename stem (strips extension)
    if fallback_filename:
        stem = Path(fallback_filename).stem
        logger.info(f"[DWGExtract] No drawing number in OCR; using filename stem: {stem}")
        return stem

    return 'UNKNOWN'


_VALVE_PROMPT_TEMPLATE = """You are an expert P&ID (Piping & Instrumentation Diagram) analyst.
Analyse page {page} of this P&ID drawing and extract ALL valve / instrument tags.

{ocr_context}
{valve_filter}

IMPORTANT: Tag numbers in P&ID drawings are typically written INSIDE CIRCLES or BUBBLES.
Look carefully at every circle/bubble symbol in the drawing.
Known prefixes (extract any tag that starts with one of these):
{known_prefixes}

Tag formats: MOV-8001, SDV-100A, XV-5001, PG-200, PIT-301, FV-400 etc.
Extract EXACTLY as written in the drawing.

For EACH tag you find, return a JSON object:
{{
  "tag_no": "<exact tag from drawing>",
  "tag": "<exact tag from drawing>",
  "type": "<2-4 letter prefix e.g. MOV>",
  "line_no": "<piping line number, e.g. 6\\"-GA-100-1501-A2B>",
  "service": "<service or description>",
  "location": "<area on drawing>",
  "piping_class": "<pipe spec if visible>",
  "notes": "<other visible info>"
}}

Return ONLY a JSON array (no text before or after). Example:
[ {{"tag_no": "MOV-8001", ... }} ]
If no tags visible: []
"""

_KNOWN_PREFIXES_STR = ', '.join(VALID_TAG_PREFIXES)


def _make_prompt(page_num: int, ocr_text: str, valve_type: Optional[str]) -> str:
    ocr_ctx = ""
    if ocr_text:
        ocr_ctx = (
            "\n=== OCR TEXT (All Pages) ===\n"
            + ocr_text[:3000]
            + "\n=== END OCR ===\n"
            "Use this OCR text to confirm tag numbers detected in the image.\n"
        )
    vf = (
        f"Focus specifically on {valve_type} type valves/instruments."
        if valve_type else
        "Extract ALL valve and instrument tags."
    )
    return _VALVE_PROMPT_TEMPLATE.format(
        page=page_num,
        ocr_context=ocr_ctx,
        valve_filter=vf,
        known_prefixes=_KNOWN_PREFIXES_STR,
    )


def _parse_json_array(text: str) -> list:
    """Robustly extract JSON array from LLM response."""
    text = re.sub(r'```json\s*', '', text)
    text = re.sub(r'```\s*', '', text)
    text = text.strip()
    if not text.startswith('['):
        bracket = text.find('[')
        if bracket != -1:
            text = text[bracket:]
    return json.loads(text)


# ─────────────────────────────────────────────────────────────────────────────
# Provider implementations
# ─────────────────────────────────────────────────────────────────────────────

def _extract_page_with_gemini(client, img_bytes: bytes, page_num: int, ocr_text: str, valve_type: Optional[str]) -> list:
    """Use Gemini Vision (cheapest) to extract valve tags from one page."""
    from google.genai import types

    prompt = _make_prompt(page_num, ocr_text, valve_type)

    response = client.models.generate_content(
        model=GEMINI_VISION_MODEL,
        contents=[
            types.Part.from_bytes(data=img_bytes, mime_type='image/png'),
            prompt,
        ],
    )
    raw = response.text or ''
    logger.info(f"[GeminiPIDExtractor] Page {page_num} response preview: {raw[:300]}")
    return _parse_json_array(raw)


def _extract_page_with_openai(client, img_b64: str, page_num: int, ocr_text: str, valve_type: Optional[str]) -> list:
    """Fallback: OpenAI gpt-4o Vision."""
    prompt = _make_prompt(page_num, ocr_text, valve_type)

    response = client.chat.completions.create(
        model=OPENAI_VISION_MODEL,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/png;base64,{img_b64}",
                    "detail": "high"
                }},
            ],
        }],
        max_tokens=4000,
        temperature=0.1,
    )
    raw = response.choices[0].message.content.strip()
    logger.info(f"[OpenAIPIDExtractor] Page {page_num} response preview: {raw[:300]}")
    return _parse_json_array(raw)


def _extract_page_ocr_only(ocr_text: str, page_num: int) -> list:
    """
    Last-resort: regex scan of OCR text for known tag patterns.
    No LLM cost at all.
    """
    if not ocr_text:
        return []

    tag_pattern = re.compile(
        r'\b(' + '|'.join(re.escape(p) for p in VALID_TAG_PREFIXES) + r')[-_]\d{1,5}(?:[-_]\d{1,5})?[A-Z]?\b',
        re.IGNORECASE,
    )
    found = tag_pattern.findall(ocr_text)
    valves = []
    seen = set()
    for match_prefix in found:
        # Reconstruct full tag from the regex match position
        pass

    # Better: find full tags
    full_pattern = re.compile(
        r'\b(?:' + '|'.join(re.escape(p) for p in VALID_TAG_PREFIXES) + r')[-_]\d{1,5}(?:[-_]\d{1,5})?[A-Z]?\b',
        re.IGNORECASE,
    )
    for tag in full_pattern.findall(ocr_text):
        tag_upper = tag.upper()
        if tag_upper not in seen:
            seen.add(tag_upper)
            valves.append({
                'tag_no': tag_upper,
                'tag': tag_upper,
                'type': tag_upper.split('-')[0].split('_')[0],
                'line_no': '',
                'service': '',
                'location': f'page {page_num}',
                'piping_class': '',
                'notes': 'extracted via OCR regex (no Vision AI)',
            })

    logger.info(f"[OCROnlyExtractor] Page {page_num}: {len(valves)} tags via regex")
    return valves


# ─────────────────────────────────────────────────────────────────────────────
# OCR helper (unchanged from original)
# ─────────────────────────────────────────────────────────────────────────────

def _run_ocr(pdf_path: str) -> str:
    """Run EasyOCR + Tesseract on all pages and return combined text."""
    try:
        doc = fitz.open(pdf_path)
        texts = []
        for page_num in range(len(doc)):
            page = doc[page_num]
            pix = page.get_pixmap(matrix=fitz.Matrix(3.0, 3.0))
            img_data = pix.tobytes("png")

            # EasyOCR
            try:
                import easyocr
                reader = easyocr.Reader(['en'], gpu=False, verbose=False)
                results = reader.readtext(img_data, detail=0, paragraph=False)
                if results:
                    texts.append(f"[EasyOCR p{page_num+1}] " + ' '.join(results))
            except Exception:
                pass

            # Tesseract
            try:
                img = Image.open(io.BytesIO(img_data))
                t = pytesseract.image_to_string(img, config='--psm 11')
                if t.strip():
                    texts.append(f"[Tesseract p{page_num+1}] {t}")
            except Exception:
                pass

        doc.close()
        return '\n'.join(texts)
    except Exception as e:
        logger.warning(f"[OCR] Failed: {e}")
        return ''


# ─────────────────────────────────────────────────────────────────────────────
# Main public extractor
# ─────────────────────────────────────────────────────────────────────────────

class GeminiPIDExtractor:
    """
    P&ID valve extractor with automatic provider fallback.
    Provider order is configured in ai_provider.py (soft-coded).
    """

    def extract_valves_from_pdf(
        self,
        pdf_path: str,
        original_filename: str = None,
        valve_type: str = None,
    ) -> dict:
        fallback_chain = get_fallback_chain('pid_extraction')
        logger.info(f"[GeminiPIDExtractor] Provider chain: {fallback_chain}")

        # OCR once, reused by all providers
        logger.info("[GeminiPIDExtractor] Running OCR...")
        ocr_text = _run_ocr(pdf_path)
        logger.info(f"[GeminiPIDExtractor] OCR extracted {len(ocr_text)} chars")

        # Render all pages once
        doc = fitz.open(pdf_path)
        num_pages = len(doc)
        pages_data = []  # list of (img_bytes, img_b64)
        for page_num in range(num_pages):
            page = doc[page_num]
            pix = page.get_pixmap(matrix=fitz.Matrix(3.0, 3.0))
            # Resize if too large (prevent token overload)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            max_dim = 3072
            if img.width > max_dim or img.height > max_dim:
                scale = max_dim / max(img.width, img.height)
                img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            img_bytes = buf.getvalue()
            pages_data.append((img_bytes, base64.b64encode(img_bytes).decode()))
        doc.close()

        all_valves = []
        seen_tags = set()

        # Lazy-init clients only when needed
        _gemini_client = None
        _openai_client = None

        for provider in fallback_chain:
            logger.info(f"[GeminiPIDExtractor] Trying provider: {provider}")
            try:
                for page_num, (img_bytes, img_b64) in enumerate(pages_data, 1):
                    try:
                        if provider == 'gemini':
                            if _gemini_client is None:
                                _gemini_client = build_gemini_client()
                                if _gemini_client is None:
                                    raise RuntimeError("Gemini API key not configured (GEMINI_API_KEY)")
                            page_valves = _extract_page_with_gemini(_gemini_client, img_bytes, page_num, ocr_text, valve_type)

                        elif provider == 'openai':
                            if _openai_client is None:
                                _openai_client = build_openai_client()
                                if _openai_client is None:
                                    raise RuntimeError("OpenAI API key not configured (OPENAI_API_KEY)")
                            page_valves = _extract_page_with_openai(_openai_client, img_b64, page_num, ocr_text, valve_type)

                        elif provider == 'ocr_only':
                            page_valves = _extract_page_ocr_only(ocr_text, page_num)

                        else:
                            logger.warning(f"[GeminiPIDExtractor] Unknown provider '{provider}', skipping")
                            continue

                        # Deduplicate across pages
                        for valve in page_valves:
                            tag = (valve.get('tag_no') or valve.get('tag') or '').strip().upper()
                            if tag and tag not in seen_tags:
                                seen_tags.add(tag)
                                all_valves.append(valve)
                            elif not tag:
                                all_valves.append(valve)

                        logger.info(f"[GeminiPIDExtractor] Page {page_num} ({provider}): {len(page_valves)} valves (total {len(all_valves)})")

                    except Exception as page_err:
                        logger.warning(f"[GeminiPIDExtractor] {provider} page {page_num} error: {page_err}")
                        # Continue to next page within same provider

                # If we got results, stop trying other providers
                if all_valves:
                    logger.info(f"[GeminiPIDExtractor] Success with provider '{provider}': {len(all_valves)} valves")
                    break
                else:
                    logger.warning(f"[GeminiPIDExtractor] Provider '{provider}' returned 0 valves, trying next...")

            except Exception as provider_err:
                logger.warning(f"[GeminiPIDExtractor] Provider '{provider}' failed entirely: {provider_err}, trying next...")

        # Soft filter: if valve_type specified, prefer matching but don't drop all
        if valve_type and all_valves:
            vt = valve_type.upper()
            filtered = [
                v for v in all_valves
                if v.get('type', '').upper() == vt
                or v.get('tag_no', '').upper().startswith(vt)
                or v.get('tag', '').upper().startswith(vt)
            ]
            if filtered:
                all_valves = filtered
                logger.info(f"[GeminiPIDExtractor] Filtered to {len(all_valves)} {valve_type} valves")
            else:
                found_types = list({(v.get('type') or v.get('tag_no','')[:3]).upper() for v in all_valves})
                logger.warning(
                    f"[GeminiPIDExtractor] {valve_type} filter yielded 0; "
                    f"found types: {found_types}. Returning all {len(all_valves)} valves."
                )

        pid_no = _extract_drawing_number(ocr_text, fallback_filename=original_filename or os.path.basename(pdf_path))

        return {
            'valves': all_valves,
            'drawing_info': {
                'pid_no': pid_no,
                'date': datetime.now().strftime('%d-%b-%Y'),
                'extraction_method': f'Gemini Flash + OCR ({num_pages} page(s))',
                'ocr_text_length': len(ocr_text),
                'source_file': original_filename or os.path.basename(pdf_path),
                'pages_processed': num_pages,
            },
        }
