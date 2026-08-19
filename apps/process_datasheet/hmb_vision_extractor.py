"""
HMB Table Extractor â€” Gemini Flash (primary) with OpenAI Vision fallback.
Provider strategy is soft-coded in ai_provider.py.

STRICT ACCURACY MODE - No hallucination, only extract visible data.
"""
import logging
import base64
import json
import re
from typing import Dict, List
import os
import fitz  # PyMuPDF
import io

from apps.process_datasheet.ai_provider import (
    get_fallback_chain,
    build_gemini_client,
    build_openai_client,
    GEMINI_VISION_MODEL,
    OPENAI_VISION_MODEL,
)

logger = logging.getLogger(__name__)

# â”€â”€â”€ Shared HMB extraction prompt â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
_HMB_SYSTEM = """You are an expert engineering data extraction assistant for Heat and Material Balance (HMB) documents.
RULES:
- Extract only visible values â€” do NOT calculate or hallucinate
- Preserve units exactly as written (barg, bara, Â°C, Â°F, etc.)
- If multiple tables exist, extract from all of them
- Return null only if a field is truly absent
- Return ONLY valid JSON"""

_HMB_USER_TEMPLATE = """Extract ALL stream data from this HMB document (Page {page}).

Return JSON in this structure (even if arrays are empty):
{{
  "streams": [
    {{
      "stream_id": "Stream identifier",
      "line_no": "Line number",
      "fluid": "Fluid/chemical name",
      "phase": "Gas/Liquid/Two-Phase/Mixed",
      "state": "Normal/Supercritical/etc",
      "pressure_normal": "Operating pressure value",
      "pressure_design": "Design pressure value",
      "pressure_unit": "barg/bara/psig/etc",
      "temp_min": "Minimum operating temperature (null if not shown)",
      "temp_normal": "Normal/typical operating temperature (the main operating temperature row)",
      "temp_max": "Maximum operating temperature (null if not shown)",
      "temp_unit": "Â°C/Â°F/K",
      "design_temp_min": "Minimum design temperature",
      "design_temp_max": "Maximum design temperature",
      "design_temp_unit": "Â°C/Â°F/K",
      "shut_off_pressure": "Shut-off or relief pressure with unit"
    }}
  ],
  "process_conditions": {{
    "ambient_temp_min": null,
    "ambient_temp_max": null,
    "ambient_temp_unit": "Â°C"
  }}
}}

If this is a cover/title page return: {{"streams": [], "process_conditions": {{}}}}"""


def _parse_hmb_json(raw: str) -> dict:
    raw = re.sub(r'```json\s*', '', raw)
    raw = re.sub(r'```\s*', '', raw)
    raw = raw.strip()
    if not raw.startswith('{'):
        brace = raw.find('{')
        if brace != -1:
            raw = raw[brace:]
    return json.loads(raw)


class HMBVisionExtractor:
    """
    Extract HMB stream data using Vision AI.
    Primary: Gemini Flash (cheap).  Fallback: OpenAI gpt-4o Vision.
    Override via ai_provider.TASK_PROVIDERS['hmb_extraction'].
    """

    def __init__(self):
        # Clients are lazily initialised on first use
        self._gemini = None
        self._openai = None

    def extract_from_pdf(self, pdf_path: str) -> Dict:
        logger.info("[HMBVisionExtractor] Starting HMB extraction...")
        try:
            images = self._pdf_to_images(pdf_path)
            logger.info(f"[HMBVisionExtractor] {len(images)} pages to process")

            all_streams: list = []
            process_conditions: dict = {}

            for page_num, img_bytes in enumerate(images, 1):
                page_data = self._extract_page(img_bytes, page_num)
                all_streams.extend(page_data.get('streams', []))
                if page_data.get('process_conditions'):
                    process_conditions.update(page_data['process_conditions'])

            result = {
                'streams': all_streams,
                'process_conditions': process_conditions,
                'extraction_method': 'gemini_vision',
                'confidence': 'high' if all_streams else 'low',
            }
            logger.info(f"[HMBVisionExtractor] Extracted {len(all_streams)} streams")
            return result

        except Exception as e:
            logger.error(f"[HMBVisionExtractor] Error: {e}")
            return {'streams': [], 'process_conditions': {}, 'extraction_method': 'failed', 'error': str(e)}

    def _pdf_to_images(self, pdf_path: str, max_pages: int = 15) -> List[bytes]:
        """Return raw PNG bytes for each page (capped at max_pages)."""
        doc = fitz.open(pdf_path)
        pages_to_process = min(len(doc), max_pages)
        images = []
        for i in range(pages_to_process):
            pix = doc[i].get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72))
            images.append(pix.tobytes("png"))
        doc.close()
        return images

    def _extract_page(self, img_bytes: bytes, page_num: int) -> dict:
        """Try providers in order until one succeeds."""
        chain = get_fallback_chain('hmb_extraction')
        user_prompt = _HMB_USER_TEMPLATE.format(page=page_num)
        img_b64 = base64.b64encode(img_bytes).decode()

        for provider in chain:
            try:
                if provider == 'gemini':
                    raw = self._call_gemini(img_bytes, user_prompt)
                elif provider == 'openai':
                    raw = self._call_openai(img_b64, user_prompt)
                else:
                    continue

                if not raw:
                    continue

                logger.info(f"[HMBVisionExtractor] Page {page_num} ({provider}) preview: {raw[:200]}")
                result = _parse_hmb_json(raw)
                streams = result.get('streams', [])
                logger.info(f"[HMBVisionExtractor] Page {page_num} ({provider}): {len(streams)} streams")
                return result

            except Exception as e:
                logger.warning(f"[HMBVisionExtractor] Page {page_num} {provider} failed: {e}")

        return {'streams': [], 'process_conditions': {}}

    def _call_gemini(self, img_bytes: bytes, user_prompt: str) -> str:
        if self._gemini is None:
            self._gemini = build_gemini_client()
            if self._gemini is None:
                raise RuntimeError("GEMINI_API_KEY not set")
        from google.genai import types
        response = self._gemini.models.generate_content(
            model=GEMINI_VISION_MODEL,
            contents=[
                types.Part.from_bytes(data=img_bytes, mime_type='image/png'),
                _HMB_SYSTEM + "\n\n" + user_prompt,
            ],
        )
        return response.text or ''

    def _call_openai(self, img_b64: str, user_prompt: str) -> str:
        if self._openai is None:
            self._openai = build_openai_client()
            if self._openai is None:
                raise RuntimeError("OPENAI_API_KEY not set")
        response = self._openai.chat.completions.create(
            model=OPENAI_VISION_MODEL,
            messages=[
                {"role": "system", "content": _HMB_SYSTEM},
                {"role": "user", "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/png;base64,{img_b64}",
                        "detail": "high",
                    }},
                ]},
            ],
            temperature=0.1,
            max_tokens=4096,
        )
        return response.choices[0].message.content or ''
