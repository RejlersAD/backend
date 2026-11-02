"""
Spec Customization — Paper Spec Extraction Service
===================================================

Engine waterfall (soft-coded via SPEC_EXTRACTION_CONFIG['ai_engines']):

  1. pymupdf_text  — free, instant; if text-layer is rich we skip AI entirely.
  2. gemini_vision — primary AI (1M context, cheap).
  3. openai_vision — fallback AI (GPT-4o).
    4. claude_vision — optional BYOK/provider-selected Anthropic path.
  4. tesseract     — last resort for scanned pages.

Mirrors the pattern used by `InstrumentIndexService` but specialised to
piping-class structured extraction.
"""
from __future__ import annotations
from apps.core.ai_consumer_clients import lazy_provider_client, provider_api_key
from apps.core.ai_credentials import get_provider_configuration

import base64
import io
import json
import logging
import math
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from .config import SPEC_EXTRACTION_CONFIG
from .prompts import SYSTEM_PROMPT, build_extraction_prompt
from .local_extraction import local_capabilities, run_local_engine
from .local_tables import LocalTableParser
from .advanced_validation import (
    merge_ensemble_results,
    validate_extracted_classes,
    ADVANCED_VALIDATION_CONFIG,
)
logger = logging.getLogger(__name__)



class ExtractionProviderConfigurationError(RuntimeError):
    """Safe diagnostics for settings that retries cannot repair."""


# ─────────────────────────────────────────────────────────────────────────────
# Helper: parse model JSON output that may be wrapped in ```json fences
# ─────────────────────────────────────────────────────────────────────────────
def _extract_json_blob(raw: str) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    # Strip code fences.
    s = raw.strip()
    fence_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', s, re.DOTALL | re.IGNORECASE)
    if fence_match:
        s = fence_match.group(1)
    # Find the outermost {...}.
    first = s.find('{')
    last = s.rfind('}')
    if first < 0 or last < 0 or last <= first:
        return None
    try:
        return json.loads(s[first:last + 1])
    except Exception as e:
        logger.warning("[SpecExtraction] JSON parse failed: %s", e)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Main service
# ─────────────────────────────────────────────────────────────────────────────
class PaperSpecExtractionService:
    """Stateless service — instantiate once per Celery task or per chunk."""

    def __init__(self, config: Optional[Dict[str, Any]] = None, user_api_key: str = "", user_provider: str = "", user_model: str = ""):
        self.config = {**SPEC_EXTRACTION_CONFIG, **(config or {})}
        self._module_models = {name: self.config[f'{name}_model'] for name in ('gemini', 'openai', 'claude')}
        self._gemini_client = None
        self._openai_client = None
        self._claude_client = None
        self._gemini_quota_exceeded = False
        self._openai_quota_exceeded = False
        self._claude_quota_exceeded = False
        self._ai_pages_used = 0
        self.provider_configuration_errors = {}
        self._chunk_deadline = None
        self._job_deadline = time.monotonic() + self.config['job_total_timeout_s']
        self._cloud_attempted = False
        self._local_candidate = None
        self.chunk_attempts = []
        self.local_engine_errors = []
        self._ocr_pages = None
        
        # BYOK (Bring Your Own Key) support
        self._user_api_key = (user_api_key or "").strip()
        self._user_provider = (user_provider or "").lower()  # "openai", "claude", or ""
        self._user_model = (user_model or "").strip()
        
        # Token usage tracking (for cost calculation)
        self._gemini_prompt_tokens = 0
        self._gemini_completion_tokens = 0
        self._openai_prompt_tokens = 0
        self._openai_completion_tokens = 0
        self._claude_prompt_tokens = 0
        self._claude_completion_tokens = 0

    # ── Lazy client init ────────────────────────────────────────────────
    def _configured_engines(self):
        """Prefer local OCR and parsing so extraction still works offline.

        AI providers remain optional and are only used when a valid configured
        credential is available. Missing credentials should not block extraction
        for locally processable documents.
        """
        engines = ['pymupdf_text']
        for local_name in ('rapidocr', 'paddleocr', 'tesseract'):
            if local_name not in engines:
                engines.append(local_name)

        if self.config.get('local_only', True):
            return engines
        configurations = [get_provider_configuration(provider) for provider in ('gemini', 'openai', 'anthropic')]
        for item in configurations:
            if not item['ready']:
                continue
            name = 'claude' if item['provider'] == 'anthropic' else item['provider']
            engines.append(f'{name}_vision')
            self.config[f'{name}_model'] = item['model'] or self._module_models[name]

        return engines

    def remaining_seconds(self):
        deadline = min(self._job_deadline, self._chunk_deadline or self._job_deadline)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Extraction processing budget exhausted.')
        return remaining

    def _extract_local(self, pdf_path, start, end, engine):
        if not next(item['available'] for item in local_capabilities(self.config)['engines'] if item['id'] == engine):
            self.local_engine_errors.append({'engine': engine, 'reason': 'not_available'})
            return None
        if engine != 'pymupdf_text' and self._ocr_pages == []:
            return None
        remaining = self.remaining_seconds()
        # Leave time for another engine and final result/report handling.
        timeout = min(self.config['local_engine_timeout_s'], remaining * .75)
        try:
            result = run_local_engine(pdf_path, start, end, engine, self.config, timeout,
                                      pages=None if engine == 'pymupdf_text' else self._ocr_pages)
            if result.get('timed_out'):
                self.local_engine_errors.append({'engine': engine, 'reason': 'timeout'})
            return result
        except TimeoutError:
            self.local_engine_errors.append({'engine': engine, 'reason': 'timeout'})
            logger.warning('[SpecExtraction] Local engine %s timed out on pages %d-%d; trying fallback', engine, start + 1, end + 1)
            return None
        except OSError:
            self.local_engine_errors.append({'engine': engine, 'reason': 'execution_failed'})
            return None

    def _combine_local_results(self, base, result):
        """Union source rows; OCR only resolves explicit image-readability gaps."""
        if not base:
            return result
        resolved = set(result.get('pages_with_components', [])) - {
            issue.get('page') for issue in result.get('local_report', {}).get('issues', [])}
        issues = [issue for issue in base.get('local_report', {}).get('issues', [])
                  if not (issue.get('reason') in {'image_page_requires_ocr', 'ocr_no_component_rows', 'local_engine_timeout'} and issue.get('page') in resolved)]
        issues.extend(issue for issue in result.get('local_report', {}).get('issues', []) if issue not in issues)
        classes = self.merge_classes([base.get('piping_classes', []), result.get('piping_classes', [])])
        return {**result, 'piping_classes': classes,
                'local_report': {**base.get('local_report', {}), **result.get('local_report', {}),
                                 'components': sum(len(item.get('components', [])) for item in classes),
                                 'issues': issues}}

    def _call_vision(self, engine, images, page_range):
        pages = page_range[1] - page_range[0] + 1
        self.remaining_seconds()
        if self._ai_pages_used + pages > int(self.config['max_ai_pages_per_job']):
            return None
        # Reserve BEFORE calling, including failed requests and escalation.
        self._ai_pages_used += pages
        self._cloud_attempted = True
        return getattr(self, f'extract_via_{engine}')(images, page_range)

    def _bounded_client(self, client):
        if client is not None and hasattr(client, 'with_options'):
            return client.with_options(timeout=self.remaining_seconds(), max_retries=0)
        return client

    def _get_gemini(self):
        if self._gemini_quota_exceeded:
            return None
        if self._gemini_client is not None:
            return self._gemini_client
        try:
            from google import genai  # type: ignore
            api_key = provider_api_key('gemini', fallback=(lambda: (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))))
            if not api_key:
                self.provider_configuration_errors['Gemini'] = 'credential_missing'
                logger.info("[SpecExtraction] GEMINI_API_KEY not set — Gemini disabled")
                return None
            self._gemini_client = lazy_provider_client('gemini', genai.Client, api_key=lambda: (api_key))
            logger.info("[SpecExtraction] ✅ Gemini ready")
            return self._gemini_client
        except Exception as e:
            logger.info("[SpecExtraction] Gemini unavailable: %s", e)
            return None

    def _get_openai(self):
        if self._openai_quota_exceeded:
            return None
        if self._openai_client is not None:
            return self._openai_client
        try:
            import openai  # type: ignore
            # BYOK: Use user-supplied API key if provider is OpenAI
            if self._user_provider == "openai" and self._user_api_key:
                api_key = self._user_api_key
                logger.info("[SpecExtraction] Using user-provided OpenAI API key (BYOK)")
            else:
                api_key = provider_api_key('openai', fallback=(lambda: (os.environ.get("OPENAI_API_KEY"))))
                if not api_key:
                    self.provider_configuration_errors['OpenAI'] = 'credential_missing'
                    logger.info("[SpecExtraction] OPENAI_API_KEY not set — OpenAI disabled")
                    return None
            self._openai_client = lazy_provider_client('openai', openai.OpenAI, api_key=lambda: (api_key))
            logger.info("[SpecExtraction] ✅ OpenAI ready")
            return self._openai_client
        except Exception as e:
            logger.info("[SpecExtraction] OpenAI unavailable: %s", e)
            return None

    def _get_claude(self):
        if self._claude_quota_exceeded:
            return None
        if self._claude_client is not None:
            return self._claude_client
        try:
            from anthropic import Anthropic  # type: ignore
            # BYOK: Use user-supplied API key if provider is Claude
            if self._user_provider == "claude" and self._user_api_key:
                api_key = self._user_api_key
                logger.info("[SpecExtraction] Using user-provided Claude API key (BYOK)")
            else:
                api_key = provider_api_key('anthropic', fallback=(lambda: (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY"))))
                if not api_key:
                    self.provider_configuration_errors['Claude'] = 'credential_missing'
                    logger.info("[SpecExtraction] ANTHROPIC_API_KEY not set — Claude disabled")
                    return None
            self._claude_client = lazy_provider_client('anthropic', Anthropic, api_key=lambda: (api_key))
            logger.info("[SpecExtraction] ✅ Claude ready")
            return self._claude_client
        except Exception as e:
            logger.info("[SpecExtraction] Claude unavailable: %s", e)
            return None

    # ── PDF helpers ─────────────────────────────────────────────────────
    def get_page_count(self, pdf_path: str) -> int:
        try:
            import pymupdf
            with pymupdf.open(pdf_path) as doc:
                return doc.page_count
        except Exception as e:
            logger.warning("[SpecExtraction] page count failed: %s", e)
            try:
                import PyPDF2
                with open(pdf_path, 'rb') as f:
                    return len(PyPDF2.PdfReader(f).pages)
            except Exception:
                return 0

    def chunk_ranges(self, total_pages: int) -> List[Tuple[int, int]]:
        size = max(1, int(self.config["chunk_size_pages"]))
        out: List[Tuple[int, int]] = []
        for start in range(0, total_pages, size):
            end = min(start + size - 1, total_pages - 1)
            out.append((start, end))
        return out

    def extract_appendix_a_summary(self, pdf_path: str) -> Dict[str, Any]:
        """Locate Appendix-A ahead of the chunk waterfall and retain source-page evidence."""
        import pymupdf

        heading = re.compile(self.config["appendix_a_summary_regex"], re.IGNORECASE)
        table_header = re.compile(self.config["appendix_table_header_regex"], re.IGNORECASE)
        code_pattern = re.compile(self.config["appendix_class_code_regex"], re.IGNORECASE)
        with pymupdf.open(pdf_path) as doc:
            # Printed page 29 may differ from the PDF index by cover pages.
            nearby = list(range(max(0, 28 - 6), min(doc.page_count, 28 + 7)))
            nearby_set = set(nearby)
            candidates = nearby + [i for i in range(doc.page_count) if i not in nearby_set]
            scanned = []
            summary_start = None
            for i in candidates:
                scanned.append(i + 1)
                if heading.search(doc[i].get_text("text") or ""):
                    summary_start = i
                    break
            if summary_start is None:
                return {"piping_classes": [], "pages_scanned": scanned, "appendix_pages": []}

            pages: List[Tuple[int, str]] = []
            for i in range(summary_start, min(doc.page_count, summary_start + self.config["appendix_summary_max_pages"])):
                text = doc[i].get_text("text") or ""
                if i > summary_start and re.search(r'APPENDIX\s*[-–]?\s*[B-Z]\b', text, re.IGNORECASE):
                    break
                pages.append((i + 1, text))

        classes: Dict[str, Dict[str, Any]] = {}
        header_found = False
        for page_number, text in pages:
            header = table_header.search(text)
            if header:
                header_found = True
                text = text[header.end():]
            if not header_found:
                continue
            for line in text.splitlines():
                match = code_pattern.match(line.strip())
                if not match:
                    continue
                code = re.sub(r'\s*[-–]\s*', '-', match.group("code")).upper()
                classes.setdefault(code, self._appendix_class(code, page_number, "appendix_table"))

        if not classes:
            for page_number, text in pages:
                for match in code_pattern.finditer(text):
                    code = re.sub(r'\s*[-–]\s*', '-', match.group("code")).upper()
                    classes.setdefault(code, self._appendix_class(code, page_number, "appendix_regex"))

        logger.info("[SpecExtraction] Appendix-A pages %s: %d class codes", [n for n, _ in pages], len(classes))
        return {
            "piping_classes": list(classes.values()),
            "pages_scanned": scanned,
            "appendix_pages": [n for n, _ in pages],
        }

    @staticmethod
    def _appendix_class(code: str, page_number: int, engine: str) -> Dict[str, Any]:
        return {
            "class_code": code,
            "class_full_code": code,
            "material_grade": "",
            "pressure_rating": "",
            "flange_facing": "",
            "corrosion_allowance": "",
            "service_list": [],
            "pt_rating_table": [],
            "components": [],
            "raw_notes": "Appendix-A class summary; component details require review.",
            "confidence": 0.30 if engine == "appendix_table" else 0.20,
            "_engine": engine,
            "_appendix_summary": True,
            "_source_pages": [page_number, page_number],
        }

    def log_zero_class_diagnostics(self, pdf_path: str, pages: List[int]) -> None:
        """Log scanned page numbers; raw excerpts require explicit DEBUG logging."""
        logger.warning("[SpecExtraction] Zero piping classes; scanned PDF pages: %s", pages)
        if not logger.isEnabledFor(logging.DEBUG):
            return
        import pymupdf
        limit = self.config["zero_class_log_chars_per_page"]
        with pymupdf.open(pdf_path) as doc:
            for page_number in pages:
                raw = doc[page_number - 1].get_text("text") or ""
                logger.debug(
                    "[SpecExtraction] Page %d raw text chunk (%d chars%s): %r",
                    page_number, len(raw), " truncated" if len(raw) > limit else "", raw[:limit],
                )

    def extract_text_for_pages(self, pdf_path: str, start: int, end: int) -> List[str]:
        """Return per-page plain text from PyMuPDF, with running
        header/footer furniture stripped (soft-coded, HEADER_FOOTER_STRIP_CONFIG)."""
        texts: List[str] = []
        try:
            import pymupdf
            with pymupdf.open(pdf_path) as doc:
                for i in range(start, end + 1):
                    self.remaining_seconds()
                    if i >= doc.page_count:
                        break
                    page = doc[i]
                    texts.append(page.get_text("text") or "")
        except TimeoutError:
            raise
        except Exception as e:
            logger.warning("[SpecExtraction] text extract failed: %s", e)
        return self.strip_running_furniture(texts)

    @staticmethod
    def strip_running_furniture(texts: List[str]) -> List[str]:
        """Remove repeating running headers/footers from per-page text.

        A line is furniture when its whitespace-normalised form repeats on at
        least two distinct pages and `min_repeat_ratio` of the chunk's pages
        within the first/last N non-empty lines,
        or when it matches an always-strip pattern. All knobs live in
        config.HEADER_FOOTER_STRIP_CONFIG.
        """
        from .config import HEADER_FOOTER_STRIP_CONFIG as HF
        if not HF.get("enabled", True) or not texts:
            return texts

        top_n   = int(HF.get("max_top_lines", 12))
        bot_n   = int(HF.get("max_bottom_lines", 6))
        ratio   = float(HF.get("min_repeat_ratio", 0.6))
        min_len = int(HF.get("min_line_length", 8))
        always  = [re.compile(p, re.IGNORECASE) for p in HF.get("extra_line_patterns", [])]

        def _norm(line: str) -> str:
            """Keep source numbers distinct; page counters have explicit patterns."""
            s = re.sub(r"\s+", " ", line).strip()
            return s.lower()

        counts: Dict[str, int] = {}
        n_pages = len(texts)
        for text in texts:
            lines = [ln for ln in text.splitlines() if ln.strip()]
            seen_on_page = set()
            for ln in lines[:top_n] + lines[-bot_n:]:
                key = _norm(ln)
                if len(key) < min_len or key in seen_on_page:
                    continue
                seen_on_page.add(key)
                counts[key] = counts.get(key, 0) + 1

        threshold = max(2, math.ceil(n_pages * ratio))
        furniture = {k for k, v in counts.items() if v >= threshold}
        if not furniture and not always:
            return texts

        cleaned: List[str] = []
        for text in texts:
            lines = text.splitlines()
            nonempty_positions = [i for i, ln in enumerate(lines) if ln.strip()]
            boundary_positions = set(nonempty_positions[:top_n] + nonempty_positions[-bot_n:])
            kept = []
            for position, ln in enumerate(lines):
                if ln.strip():
                    key = _norm(ln)
                    if (position in boundary_positions and key in furniture) or any(p.search(ln.strip()) for p in always):
                        continue
                kept.append(ln)
            cleaned.append("\n".join(kept))
        return cleaned

    def render_pages_to_jpeg_b64(self, pdf_path: str, start: int, end: int) -> List[str]:
        """Render the requested pages as JPEG base64 strings."""
        out: List[str] = []
        try:
            import pymupdf
            with pymupdf.open(pdf_path) as doc:
                dpi = self.config["render_dpi"]
                for i in range(start, end + 1):
                    self.remaining_seconds()
                    if i >= doc.page_count:
                        break
                    page = doc[i]
                    scale = min(dpi / 72, self.config['max_image_size'] / max(page.rect.width, page.rect.height))
                    mat = pymupdf.Matrix(scale, scale)
                    pix = page.get_pixmap(matrix=mat)
                    img_bytes = pix.tobytes("jpeg", jpg_quality=self.config["jpeg_quality"])
                    out.append(base64.b64encode(img_bytes).decode("ascii"))
        except TimeoutError:
            raise
        except Exception as e:
            logger.warning("[SpecExtraction] render failed: %s", e)
        return out

    # ── Engine: PyMuPDF text-layer only (no AI) ─────────────────────────
    def extract_via_text_layer(self, pdf_path: str, start: int, end: int) -> Dict[str, Any]:
        """Extract explicit component tables in an isolated local process."""
        return self._extract_local(pdf_path, start, end, 'pymupdf_text') or {
            'piping_classes': [], 'page_text_chars': 0, 'engine_used': 'pymupdf_text'}

    def _text_layer_result_is_complete(self, classes: List[Dict[str, Any]]) -> bool:
        """Whether the deterministic parser produced a usable extraction.

        Detecting a class title in a text-rich PDF is useful routing evidence,
        but it is not sufficient to produce a SPEC/CAT workbook.  Keep Vision
        enabled until the text path can actually supply component rows.
        """
        minimum = max(1, int(self.config.get("text_layer_min_components_for_direct_accept", 1)))
        component_count = sum(len(item.get("components") or []) for item in classes)
        return component_count >= minimum

    # ── Engine: Gemini Vision ───────────────────────────────────────────
    def extract_via_gemini(self, images_b64: List[str], page_range: Tuple[int, int]) -> Optional[Dict[str, Any]]:
        client = self._get_gemini()
        if not client or not images_b64:
            return None
        try:
            from google.genai import types  # type: ignore
            parts: List[Any] = [SYSTEM_PROMPT + "\n\n" + build_extraction_prompt()]
            for b64 in images_b64:
                parts.append(types.Part.from_bytes(
                    data=base64.b64decode(b64),
                    mime_type="image/jpeg",
                ))
            resp = client.models.generate_content(
                model=self.config["gemini_model"],
                contents=parts,
                config=types.GenerateContentConfig(
                    http_options=types.HttpOptions(
                        timeout=max(1, int(self.remaining_seconds() * 1000)),
                        retry_options=types.HttpRetryOptions(attempts=1),
                    ),
                    temperature=self.config["gemini_temperature"],
                    response_mime_type="application/json",
                ),
            )
            
            # Track token usage for cost calculation
            if hasattr(resp, 'usage_metadata') and resp.usage_metadata:
                self._gemini_prompt_tokens += getattr(resp.usage_metadata, 'prompt_token_count', 0)
                self._gemini_completion_tokens += getattr(resp.usage_metadata, 'candidates_token_count', 0)
            
            blob = _extract_json_blob(resp.text or "")
            if not blob:
                return None
            for c in blob.get("piping_classes", []):
                c.setdefault("_engine", "gemini_vision")
                c.setdefault("_source_pages", [page_range[0] + 1, page_range[1] + 1])
            return blob
        except Exception as e:
            if getattr(e, 'status_code', None) in {401, 403, 404}:
                self.provider_configuration_errors['Gemini'] = e.status_code
                self._gemini_quota_exceeded = True
            msg = str(e).lower()
            if "quota" in msg or "rate" in msg or "429" in msg:
                self._gemini_quota_exceeded = True
                logger.warning("[SpecExtraction] Gemini quota — disabling for remainder of job")
            else:
                logger.warning("[SpecExtraction] Gemini failed: %s", e)
            return None

    # ── Engine: OpenAI Vision ───────────────────────────────────────────
    def extract_via_openai(self, images_b64: List[str], page_range: Tuple[int, int]) -> Optional[Dict[str, Any]]:
        client = self._bounded_client(self._get_openai())
        if not client or not images_b64:
            return None
        try:
            content: List[Dict[str, Any]] = [
                {"type": "text", "text": build_extraction_prompt()},
            ]
            for b64 in images_b64:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                })
            
            # Use user-specified model if BYOK, else default
            model = self._user_model if (self._user_provider == "openai" and self._user_model) else self.config["openai_model"]
            
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": content},
                ],
                max_tokens=self.config["openai_max_tokens"],
                temperature=self.config["openai_temperature"],
                response_format={"type": "json_object"},
            )
            
            # Track token usage for cost calculation
            if hasattr(resp, 'usage') and resp.usage:
                self._openai_prompt_tokens += getattr(resp.usage, 'prompt_tokens', 0)
                self._openai_completion_tokens += getattr(resp.usage, 'completion_tokens', 0)
            
            raw = resp.choices[0].message.content or ""
            blob = _extract_json_blob(raw)
            if not blob:
                return None
            for c in blob.get("piping_classes", []):
                c.setdefault("_engine", "openai_vision")
                c.setdefault("_source_pages", [page_range[0] + 1, page_range[1] + 1])
            return blob
        except Exception as e:
            if getattr(e, 'status_code', None) in {401, 403, 404}:
                self.provider_configuration_errors['OpenAI'] = e.status_code
                self._openai_quota_exceeded = True
            msg = str(e).lower()
            if "quota" in msg or "rate" in msg or "429" in msg or "insufficient" in msg:
                self._openai_quota_exceeded = True
                logger.warning("[SpecExtraction] OpenAI quota — disabling for remainder of job")
            else:
                logger.warning("[SpecExtraction] OpenAI failed: %s", e)
            return None

    # ── Engine: Claude Vision ───────────────────────────────────────────
    def extract_via_claude(self, images_b64: List[str], page_range: Tuple[int, int]) -> Optional[Dict[str, Any]]:
        """Extract piping specs using Claude (Anthropic) vision API."""
        client = self._bounded_client(self._get_claude())
        if not client or not images_b64:
            return None
        try:
            # Build content array with text prompt + images
            content: List[Dict[str, Any]] = [
                {"type": "text", "text": build_extraction_prompt()},
            ]
            for b64 in images_b64:
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": b64,
                    },
                })
            
            # Use user-specified model if BYOK, else default
            model = self._user_model if (self._user_provider == "claude" and self._user_model) else self.config["claude_model"]
            
            message = client.messages.create(
                model=model,
                max_tokens=self.config["claude_max_tokens"],
                # Anthropic Python SDK 1.x removed sampling parameters from
                # Messages methods. Omit them for both new and legacy SDKs.
                system=SYSTEM_PROMPT,
                messages=[
                    {"role": "user", "content": content},
                ],
            )
            
            # Track token usage for cost calculation
            if hasattr(message, 'usage') and message.usage:
                self._claude_prompt_tokens += getattr(message.usage, 'input_tokens', 0)
                self._claude_completion_tokens += getattr(message.usage, 'output_tokens', 0)
            
            # Extract text from response
            raw = ""
            if hasattr(message, 'content') and message.content:
                # Claude returns a list of content blocks
                for block in message.content:
                    if hasattr(block, 'text'):
                        raw += block.text
            
            blob = _extract_json_blob(raw)
            if not blob:
                return None
            for c in blob.get("piping_classes", []):
                c.setdefault("_engine", "claude_vision")
                c.setdefault("_source_pages", [page_range[0] + 1, page_range[1] + 1])
            return blob
        except Exception as e:
            msg = str(e).lower()
            if getattr(e, 'status_code', None) in {401, 403, 404}:
                self.provider_configuration_errors['Claude'] = e.status_code
                self._claude_quota_exceeded = True
            if "quota" in msg or "rate" in msg or "429" in msg or "overloaded" in msg:
                self._claude_quota_exceeded = True
                logger.warning("[SpecExtraction] Claude quota — disabling for remainder of job")
            else:
                logger.warning("[SpecExtraction] Claude failed: %s", e)
            return None

    def _collect_pages_as_images(self, pdf_path: str, start: int, end: int) -> List[Any]:
        import pymupdf
        from PIL import Image

        images: List[Any] = []
        with pymupdf.open(pdf_path) as doc:
            for i in range(start, end + 1):
                if i >= doc.page_count:
                    break
                page = doc[i]
                pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2))
                image = Image.open(io.BytesIO(pix.tobytes("png")))
                images.append(image)
        return images

    @staticmethod
    def _ocr_lines_from_result(result: Any) -> List[str]:
        lines: List[str] = []
        if not result:
            return lines
        if isinstance(result, list):
            for item in result:
                if isinstance(item, (list, tuple)):
                    if len(item) >= 2:
                        value = item[1]
                        if isinstance(value, str) and value.strip():
                            lines.append(value.strip())
                        elif isinstance(value, (list, tuple)) and value and isinstance(value[0], str):
                            lines.append(str(value[0]).strip())
                    elif item and isinstance(item[0], str):
                        lines.append(str(item[0]).strip())
                elif isinstance(item, str):
                    if item.strip():
                        lines.append(item.strip())
        elif isinstance(result, dict):
            for key in ("text", "ocr_text", "content"):
                value = result.get(key)
                if isinstance(value, str):
                    lines.append(value.strip())
                    break
        return [line for line in lines if line]

    def _classes_from_ocr_text(self, ocr_text: str, engine: str, start: int, end: int) -> List[Dict[str, Any]]:
        parser = LocalTableParser(self.config['piping_class_header_regex'], engine)
        parser.read_text(ocr_text, start + 1)
        return parser.result()['piping_classes']

    def extract_via_rapidocr(self, pdf_path: str, start: int, end: int) -> Optional[Dict[str, Any]]:
        return self._extract_local(pdf_path, start, end, 'rapidocr')

    def extract_via_paddleocr(self, pdf_path: str, start: int, end: int) -> Optional[Dict[str, Any]]:
        return self._extract_local(pdf_path, start, end, 'paddleocr')

    def extract_via_tesseract(self, pdf_path: str, start: int, end: int) -> Optional[Dict[str, Any]]:
        return self._extract_local(pdf_path, start, end, 'tesseract')

    @staticmethod
    def _classes_quality(classes: List[Dict[str, Any]]) -> Tuple[int, float]:
        """Return (total_component_count, avg_confidence) for a list of
        piping classes, used to decide whether to escalate to the next AI tier."""
        if not classes:
            return 0, 0.0
        total_components = sum(len(c.get("components", []) or []) for c in classes)
        confidences = [float(c.get("confidence", 0) or 0) for c in classes]
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
        return total_components, avg_conf

    def _meets_escalation_thresholds(self, classes: List[Dict[str, Any]]) -> bool:
        """True if `classes` is good enough to accept WITHOUT escalating to
        the next, costlier AI tier. Soft-coded via
        `escalate_if_components_below` / `escalate_if_avg_confidence_below`
        in SPEC_EXTRACTION_CONFIG."""
        if not classes:
            return False
        total_components, avg_conf = self._classes_quality(classes)
        min_components = int(self.config["escalate_if_components_below"])
        min_conf = float(self.config["escalate_if_avg_confidence_below"])
        return total_components >= min_components and avg_conf >= min_conf

    # ── Orchestrator: process one chunk via the engine waterfall ────────
    def extract_chunk(self, pdf_path, start, end, enable_ensemble=None, retry_attempt=0):
        """Share a chunk deadline; retry transient local failures, never paid calls."""
        self._chunk_deadline = min(self._job_deadline, time.monotonic() + self.config['chunk_timeout_s'])
        self._cloud_attempted = False
        self.local_engine_errors = []
        self._ocr_pages = None
        attempts = 1 + int(self.config['retry_max'])
        for attempt in range(attempts):
            self.remaining_seconds()
            self._local_candidate = None
            try:
                result = self._extract_chunk_once(pdf_path, start, end, enable_ensemble, retry_attempt + attempt)
                if self._local_candidate and ('vision' in result.get('engine_used', '') or 'ensemble' in result.get('engine_used', '')):
                    # A provider response does not prove previously rejected
                    # local rows were covered. Keep source candidates and gaps.
                    result['piping_classes'] = self.merge_classes([
                        result.get('piping_classes', []), self._local_candidate['piping_classes']])
                    result['local_report'] = self._local_candidate.get('local_report', {})
                self.remaining_seconds()
                self.chunk_attempts.append({'page_from': start + 1, 'page_to': end + 1,
                                            'attempts': attempt + 1, 'state': 'processed',
                                            'engine_errors': list(self.local_engine_errors)})
                return result
            except (OSError, TimeoutError) as exc:
                self.chunk_attempts.append({'page_from': start + 1, 'page_to': end + 1,
                                            'attempts': attempt + 1, 'state': 'timeout' if isinstance(exc, TimeoutError) else 'local_error'})
                if self._local_candidate:
                    candidate = self._local_candidate
                    candidate.setdefault('local_report', {}).setdefault('issues', []).append(
                        {'page': start + 1, 'page_to': end + 1, 'reason': 'chunk_processing_timeout' if isinstance(exc, TimeoutError) else 'local_execution_failed'})
                    return candidate
                # Retrying an expired chunk cannot replenish its budget.
                if isinstance(exc, TimeoutError) or self._cloud_attempted or attempt + 1 == attempts:
                    raise
                delay = self.config['retry_backoff_base'] ** (attempt + 1)
                if self.remaining_seconds() <= delay:
                    raise TimeoutError('Insufficient chunk budget for a local retry.') from exc
                time.sleep(delay)
        raise RuntimeError('No extraction attempt executed.')

    def _extract_chunk_once(
        self,
        pdf_path: str,
        start: int,
        end: int,
        enable_ensemble: Optional[bool] = None,
        retry_attempt: int = 0
    ) -> Dict[str, Any]:
        """Return {"piping_classes": [...], "engine_used": str, "validation_report": {...}}.
        
        Args:
            pdf_path: Path to PDF file
            start: Start page index (0-based)
            end: End page index (0-based, inclusive)
            enable_ensemble: If True, run Gemini + OpenAI in parallel (default: from config)
            retry_attempt: Retry attempt number (0 = first attempt)
        """
        if enable_ensemble is None:
            enable_ensemble = ADVANCED_VALIDATION_CONFIG.get("enable_ensemble_extraction", False)
        
        local_only = self.config.get('local_only', True)
        engines = (list(self.config['ai_engines']) if not local_only and self._user_provider in {'openai', 'claude'}
                   else self._configured_engines())
        if local_only:
            engines = [engine for engine in engines if engine in {'pymupdf_text', 'rapidocr', 'paddleocr', 'tesseract'}]
            enable_ensemble = False
        max_ai_pages = int(self.config["max_ai_pages_per_job"])
        page_count_in_chunk = (end - start + 1)

        # BYOK provider routing: when a user/project key is present, run the
        # explicitly selected provider first. For predictable billing/behavior,
        # default to a strict provider-only AI path plus OCR fallback.
        byok_provider = self._user_provider if not local_only and self._user_provider in {"openai", "claude"} else ""
        if byok_provider:
            byok_engine = f"{byok_provider}_vision"
            if byok_engine not in engines:
                engines.insert(1, byok_engine)
            # Keep deterministic text-layer first and OCR fallback last.
            engines = ["pymupdf_text", byok_engine, "rapidocr", "paddleocr", "tesseract"]
            # Ensemble currently supports Gemini+OpenAI only.
            enable_ensemble = False
        
        # Adjust extraction strategy based on retry attempt
        if retry_attempt > 0:
            logger.info(
                "[SpecExtraction] Retry attempt %d for pages %d-%d",
                retry_attempt, start + 1, end + 1
            )

        # 1) Always try text-layer first — cheap, accurate on vector PDFs.
        text_result = self.extract_via_text_layer(pdf_path, start, end)
        # Native page routing is explicit. Unsupported native table semantics
        # are review gaps; rasterising those same readable pages cannot fix them.
        self._ocr_pages = text_result.get('ocr_pages')
        text_classes = text_result.get("piping_classes", [])
        local_candidate = text_result if self._text_layer_result_is_complete(text_classes) else None
        self._local_candidate = local_candidate

        # A text-rich document is not enough by itself.  Only bypass Vision
        # when a deterministic parser has returned actual component rows;
        # otherwise merged table cells yield a deceptively successful,
        # header-only job with an empty catalog.
        if (
            local_candidate and not text_result.get('local_report', {}).get('issues')
        ):
            return {**text_result, "engine_used": "pymupdf_text"}

        # Local engines are independent of the paid-page budget. A header-only
        # OCR result must never prevent later structured extraction.
        if not byok_provider:
            for engine in engines:
                if engine in {'rapidocr', 'paddleocr', 'tesseract'}:
                    blob = getattr(self, f'extract_via_{engine}')(pdf_path, start, end)
                    if blob:
                        blob = self._combine_local_results(local_candidate or text_result, {**blob, 'engine_used': engine})
                        if self._text_layer_result_is_complete(blob.get('piping_classes', [])):
                            local_candidate = blob
                            self._local_candidate = blob
                            if not blob.get('local_report', {}).get('issues'):
                                return blob
                        else:
                            text_result = blob

        # Within AI budget?
        ai_budget_ok = not local_only and (self._ai_pages_used + page_count_in_chunk) <= max_ai_pages

        if ai_budget_ok:
            images: Optional[List[str]] = None
            
            # ── ENSEMBLE MODE: Run Gemini + OpenAI in parallel ──
            if enable_ensemble and "gemini_vision" in engines and "openai_vision" in engines:
                logger.info(
                    "[SpecExtraction] Ensemble mode: running Gemini + OpenAI in parallel for pages %d-%d",
                    start + 1, end + 1
                )
                images = self.render_pages_to_jpeg_b64(pdf_path, start, end)
                
                gemini_blob = self._call_vision("gemini", images, (start, end))
                openai_blob = self._call_vision("openai", images, (start, end))
                
                gemini_classes = gemini_blob.get("piping_classes", []) if gemini_blob else []
                openai_classes = openai_blob.get("piping_classes", []) if openai_blob else []
                
                if self._text_layer_result_is_complete(gemini_classes + openai_classes):
                    merged_classes, ensemble_metrics = merge_ensemble_results(
                        gemini_classes, openai_classes
                    )
                    
                    return {
                        "piping_classes": merged_classes,
                        "engine_used": "ensemble_gemini+openai",
                        "ensemble_metrics": ensemble_metrics,
                    }
            
            # ── WATERFALL MODE: Try engines sequentially, escalating to the
            # next (costlier) AI tier ONLY if the current result is below the
            # soft-coded quality thresholds (escalate_if_components_below /
            # escalate_if_avg_confidence_below in config.py). OpenAI is the
            # final AI escalation tier — mirrors electrical_checklist's
            # OCR-first / Vision-escalation pattern (HANDWRITING_CONFIG).
            gemini_candidate: Optional[Dict[str, Any]] = None
            for engine in engines:
                if engine == "pymupdf_text":
                    continue  # already done
                if engine == "gemini_vision":
                    images = images or self.render_pages_to_jpeg_b64(pdf_path, start, end)
                    blob = self._call_vision("gemini", images, (start, end))
                    classes = blob.get("piping_classes") if blob else None
                    if classes and self._text_layer_result_is_complete(classes):
                        if self._meets_escalation_thresholds(classes):
                            return {"piping_classes": classes, "engine_used": "gemini_vision"}
                        # Below threshold — keep as a fallback candidate and
                        # escalate to OpenAI (the last AI level) instead of
                        # accepting immediately.
                        gemini_candidate = {"piping_classes": classes, "engine_used": "gemini_vision"}
                        logger.info(
                            "[SpecExtraction] Gemini result below escalation thresholds "
                            "(pages %d-%d) — escalating to OpenAI", start + 1, end + 1
                        )
                elif engine == "openai_vision":
                    images = images or self.render_pages_to_jpeg_b64(pdf_path, start, end)
                    blob = self._call_vision("openai", images, (start, end))
                    classes = blob.get("piping_classes") if blob else None
                    if classes and self._text_layer_result_is_complete(classes):
                        if not gemini_candidate:
                            return {"piping_classes": classes, "engine_used": "openai_vision"}
                        # Both tiers produced something — pick whichever is
                        # objectively better (more components, else higher
                        # confidence) instead of blindly preferring whichever
                        # ran last.
                        g_count, g_conf = self._classes_quality(gemini_candidate["piping_classes"])
                        o_count, o_conf = self._classes_quality(classes)
                        if (o_count, o_conf) > (g_count, g_conf):
                            return {"piping_classes": classes, "engine_used": "openai_vision_escalated"}
                        return {"piping_classes": gemini_candidate["piping_classes"],
                                "engine_used": "gemini_vision_kept_over_openai"}
                    if gemini_candidate:
                        # OpenAI failed/empty — Gemini's below-threshold result
                        # is still better than nothing.
                        return gemini_candidate
                elif engine == "claude_vision":
                    images = images or self.render_pages_to_jpeg_b64(pdf_path, start, end)
                    blob = self._call_vision("claude", images, (start, end))
                    classes = blob.get("piping_classes") if blob else None
                    if classes and self._text_layer_result_is_complete(classes):
                        if not gemini_candidate:
                            return {"piping_classes": classes, "engine_used": "claude_vision"}
                        # Compare against prior candidate using objective quality.
                        g_count, g_conf = self._classes_quality(gemini_candidate["piping_classes"])
                        c_count, c_conf = self._classes_quality(classes)
                        if (c_count, c_conf) > (g_count, g_conf):
                            return {"piping_classes": classes, "engine_used": "claude_vision_escalated"}
                        return {"piping_classes": gemini_candidate["piping_classes"],
                                "engine_used": "gemini_vision_kept_over_claude"}
                    if gemini_candidate:
                        # Claude failed/empty — keep prior candidate.
                        return gemini_candidate
                elif engine in {'rapidocr', 'paddleocr', 'tesseract'}:
                    if not byok_provider:
                        continue  # already attempted, regardless of AI budget
                    if gemini_candidate:
                        return gemini_candidate
                    blob = getattr(self, f'extract_via_{engine}')(pdf_path, start, end)
                    if blob and self._text_layer_result_is_complete(blob.get('piping_classes', [])):
                        if not blob.get('local_report', {}).get('issues'):
                            return {**blob, 'engine_used': engine}
                        if not local_candidate or self._classes_quality(blob['piping_classes'])[0] > self._classes_quality(local_candidate['piping_classes'])[0]:
                            local_candidate = {**blob, 'engine_used': engine}
                            self._local_candidate = local_candidate

            if gemini_candidate:
                return gemini_candidate
        elif not local_only:
            logger.info("[SpecExtraction] AI budget exhausted (%d/%d) — using text/tesseract only",
                        self._ai_pages_used, max_ai_pages)

        # Failed/header-only fallback must not discard usable partial evidence.
        if local_candidate:
            return local_candidate
        # Fallback: text-layer classes (may be empty) + tesseract last attempt
        if text_result.get('piping_classes'):
            timed_out = any(item['reason'] == 'timeout' for item in self.local_engine_errors)
            if timed_out:
                text_result.setdefault('local_report', {}).setdefault('issues', []).append(
                    {'page': start + 1, 'page_to': end + 1, 'reason': 'local_engine_timeout'})
            return {**text_result, 'timed_out': timed_out, "engine_used": "pymupdf_text"}
        if byok_provider:
            blob = self.extract_via_tesseract(pdf_path, start, end)
            if blob and blob.get("piping_classes"):
                return {**blob, "engine_used": "tesseract"}
        if any(item['reason'] == 'execution_failed' for item in self.local_engine_errors):
            raise OSError('Available local engines failed to execute.')
        if any(item['reason'] == 'timeout' for item in self.local_engine_errors):
            raise TimeoutError('Available local extraction engines timed out for this chunk.')
        # Preserve image/unsupported-layout coverage even when no rows were found.
        return {**text_result, "engine_used": "none"}

    # ── Merging chunk results ───────────────────────────────────────────
    @staticmethod
    def _component_signature(comp: Dict[str, Any], fields: List[str]) -> tuple:
        """Normalised dedupe signature for a component dict (soft-coded fields)."""
        parts = []
        for f in fields:
            v = comp.get(f, "") or ""
            parts.append(re.sub(r"\s+", " ", str(v)).strip().lower())
        source = comp.get('_source_reference') or {}
        if source.get('item_code'):
            parts.extend([source['item_code'], source.get('cell_code', '')])
        if source.get('layout') == 'dgs':
            parts.extend(['dgs', source.get('page'), source.get('row')])
        if source.get('layout') == 'lng':
            parts.extend(['lng', source.get('page'), source.get('table'), source.get('row')])
        return tuple(parts)

    @staticmethod
    def merge_classes(all_class_lists: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        """Deduplicate by class_code.

        Metadata carrier: prefer the extraction with most components, else
        highest confidence (legacy behaviour). Components, service lists and
        PT tables are UNIONED across all chunk extractions of the same class
        (soft-coded via COMPONENT_MERGE_CONFIG) so classes spanning chunk
        boundaries no longer lose rows.
        """
        from .config import COMPONENT_MERGE_CONFIG as CM
        union_components = CM.get("union_components_across_chunks", True)
        sig_fields       = CM.get("signature_fields", ["component_type", "sub_type",
                                                       "size_from", "size_to",
                                                       "schedule_or_rating",
                                                       "material_standard", "description"])
        union_lists      = CM.get("union_list_fields", ["service_list"])
        union_pt         = CM.get("union_pt_table", True)

        bucket: Dict[str, Dict[str, Any]] = {}
        for lst in all_class_lists:
            for cls in lst:
                code = (cls.get("class_code") or "").strip().upper()
                if not code:
                    continue
                existing = bucket.get(code)
                if existing is None:
                    bucket[code] = cls
                    continue
                # Metadata carrier: more components wins, else higher confidence.
                if len(cls.get("components", [])) > len(existing.get("components", [])):
                    primary, secondary = cls, existing
                elif (cls.get("confidence", 0) or 0) > (existing.get("confidence", 0) or 0):
                    primary, secondary = cls, existing
                else:
                    primary, secondary = existing, cls

                if union_components:
                    seen_sigs = set()
                    merged_comps: List[Dict[str, Any]] = []
                    for src in (primary, secondary):
                        for comp in src.get("components", []) or []:
                            sig = PaperSpecExtractionService._component_signature(comp, sig_fields)
                            if any(sig) and sig in seen_sigs:
                                continue
                            seen_sigs.add(sig)
                            merged_comps.append(comp)
                    primary["components"] = merged_comps

                for field in union_lists:
                    combined = list(dict.fromkeys(
                        [str(x).strip() for x in (primary.get(field) or []) if str(x).strip()]
                        + [str(x).strip() for x in (secondary.get(field) or []) if str(x).strip()]
                    ))
                    primary[field] = combined

                if union_pt:
                    pt_seen = set()
                    pt_merged = []
                    for src in (primary, secondary):
                        for row in src.get("pt_rating_table", []) or []:
                            try:
                                key = (round(float(row.get("pressure_bar_g", 0)), 3),
                                       round(float(row.get("temperature_c", 0)), 3))
                            except (TypeError, ValueError):
                                key = (str(row), "")
                            if key in pt_seen:
                                # Same P/T point seen twice — keep any extracted
                                # allowable_stress (System 3 MaterialsData reads
                                # it verbatim) instead of dropping the richer row.
                                for kept in pt_merged:
                                    try:
                                        kept_key = (round(float(kept.get("pressure_bar_g", 0)), 3),
                                                    round(float(kept.get("temperature_c", 0)), 3))
                                    except (TypeError, ValueError):
                                        kept_key = (str(kept), "")
                                    if kept_key == key:
                                        if not kept.get("allowable_stress") and row.get("allowable_stress"):
                                            kept["allowable_stress"] = row.get("allowable_stress")
                                        break
                                continue
                            pt_seen.add(key)
                            pt_merged.append(row)
                    primary["pt_rating_table"] = pt_merged

                # Merge page ranges.
                merged_pages = sorted(set(
                    (existing.get("_source_pages") or []) + (cls.get("_source_pages") or [])
                ))
                if merged_pages:
                    primary["_source_pages"] = [merged_pages[0], merged_pages[-1]]
                if existing.get("_appendix_summary") or cls.get("_appendix_summary"):
                    primary["_appendix_summary"] = True
                if existing.get('_source_only') or cls.get('_source_only'):
                    primary['_source_only'] = True
                bucket[code] = primary
        return sorted(bucket.values(), key=lambda c: c.get("class_code", ""))


    # -- Token usage & cost calculation ----------------------------------
    def get_usage_and_cost(self) -> Dict[str, Any]:
        """Return token usage totals and computed cost in USD.
        
        Mirrors the pattern used by electrical_checklist.handwriting_extractor:
        compute cost once based on accumulated tokens, no async post-processing.
        """
        from .config import GEMINI_PRICING_PER_1M_TOKENS, OPENAI_PRICING_PER_1M_TOKENS, CLAUDE_PRICING_PER_1M_TOKENS
        from decimal import Decimal
        
        gemini_cost = (
            (self._gemini_prompt_tokens / 1_000_000) * GEMINI_PRICING_PER_1M_TOKENS["input"]
            + (self._gemini_completion_tokens / 1_000_000) * GEMINI_PRICING_PER_1M_TOKENS["output"]
        )
        openai_cost = (
            (self._openai_prompt_tokens / 1_000_000) * OPENAI_PRICING_PER_1M_TOKENS["input"]
            + (self._openai_completion_tokens / 1_000_000) * OPENAI_PRICING_PER_1M_TOKENS["output"]
        )
        claude_cost = (
            (self._claude_prompt_tokens / 1_000_000) * CLAUDE_PRICING_PER_1M_TOKENS["input"]
            + (self._claude_completion_tokens / 1_000_000) * CLAUDE_PRICING_PER_1M_TOKENS["output"]
        )
        total_cost_usd = Decimal(str(round(gemini_cost + openai_cost + claude_cost, 6)))
        
        return {
            "gemini_prompt_tokens": self._gemini_prompt_tokens,
            "gemini_completion_tokens": self._gemini_completion_tokens,
            "openai_prompt_tokens": self._openai_prompt_tokens,
            "openai_completion_tokens": self._openai_completion_tokens,
            "claude_prompt_tokens": self._claude_prompt_tokens,
            "claude_completion_tokens": self._claude_completion_tokens,
            "cost_usd": total_cost_usd,
            "byok_provider": self._user_provider if self._user_provider in {"openai", "claude"} else None,
            "byok_model": self._user_model if self._user_provider in {"openai", "claude"} else None,
        }
