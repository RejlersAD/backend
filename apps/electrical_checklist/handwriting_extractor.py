"""
Handwriting-aware extraction service for electrical inspection checklists.

COST-OPTIMISED STRATEGY (all soft-coded via HANDWRITING_CONFIG in template_v2_config.py):
  1. PRIMARY  : Local Tesseract OCR (FREE) — extracts text and fuzzy-matches against
                the 71 template field labels. Runs on every page.
  2. ESCALATION: OpenAI GPT-4o Vision (PAID) — only invoked if the OCR result fails
                the soft-coded escalation thresholds (too few fields OR low average
                confidence OR OCR unavailable). Skipped entirely when disabled.
  3. LAST RESORT: Return whatever was extracted (or an empty result) so downstream
                Excel export never fails.

This module is a companion to `extraction_service.py`. It does NOT modify or
replace the existing service — it is used by the new `extract_handwriting`
endpoint action and can be enabled/disabled from the frontend via a soft-coded flag.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional

from django.conf import settings
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from .template_v2_config import (
    HANDWRITING_CONFIG,
    TEMPLATE_V2_COLUMNS,
    TEMPLATE_V2_SECTIONS,
    get_all_v2_fields,
    get_config_for_mode,
    get_vision_pricing,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ExtractedField:
    """One extracted field value with provenance."""
    field_id:    str
    value:       str
    confidence:  int = 0        # 0..100
    page:        int = 0
    source_file: str = ""
    method:      str = ""        # "vision" | "ocr" | "fallback"


@dataclass
class ExtractionResult:
    """Aggregated result for one PDF (may contain N pages)."""
    fields:            List[ExtractedField] = field(default_factory=list)
    pages_processed:   int = 0
    method_used:       str = "none"
    raw_text_by_page:  Dict[int, str] = field(default_factory=dict)
    errors:            List[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Main extractor
# ─────────────────────────────────────────────────────────────────────────────

class HandwritingExtractor:
    """
    Extract handwritten inspection data from a PDF into the 71 template fields.

    Usage:
        extractor = HandwritingExtractor()
        result = extractor.extract(pdf_file, source_file_name="Part_01.pdf")
        # result.fields is a list of ExtractedField
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None,
                 user_openai_api_key: Optional[str] = None,
                 extraction_mode: Optional[str] = None):
        """
        Args:
            config: Optional overrides for HANDWRITING_CONFIG (applied last, wins over mode preset).
            user_openai_api_key: BYOK — user-supplied OpenAI API key. When provided,
                takes precedence over settings.OPENAI_API_KEY for this instance only.
                The key is held in memory for the lifetime of the extractor and
                MUST NOT be logged, persisted, or serialised.
            extraction_mode: Soft-coded mode name ("fast" | "balanced" | "deep" | "vision_only").
                When provided, its preset is merged on top of HANDWRITING_CONFIG BEFORE
                any explicit `config` overrides. Unknown modes fall back to the default.
        """
        # Order: base defaults → mode preset → caller overrides
        base = get_config_for_mode(extraction_mode) if extraction_mode else dict(HANDWRITING_CONFIG)
        self.config = {**base, **(config or {})}
        self.extraction_mode = (extraction_mode or "balanced").lower()

        # BYOK: validate the key SHAPE (never log the value). We do NOT contact
        # OpenAI here — just reject obviously-wrong values (passwords, empty
        # strings, sentences) so we don't waste a Vision call and don't leak
        # the raw string back through OpenAI's 401 error body.
        raw_key = (user_openai_api_key or "").strip()
        self._user_api_key: Optional[str] = raw_key if self._looks_like_openai_key(raw_key) else None
        self._user_key_rejected_reason: Optional[str] = None
        if raw_key and not self._user_api_key:
            self._user_key_rejected_reason = (
                "The value provided does not look like an OpenAI API key "
                "(must start with 'sk-' and be at least 20 characters). "
                "Falling back to the platform key."
            )
            logger.warning(
                "[Handwriting] User-supplied API key ignored (bad format, len=%d, prefix=%r)",
                len(raw_key), raw_key[:3] if raw_key else "",
            )
        # Runtime flag: if a valid-looking user key still gets rejected by OpenAI
        # at request time (e.g. revoked / spent quota), we flip this and fall
        # back to the platform key on the NEXT retry within the same run.
        self._user_key_runtime_disabled = False
        self._openai_client = None  # lazy init

        # Vision API cost tracking (soft-coded pricing — see get_vision_pricing()).
        # Accumulated across every chat.completions.create() call made by THIS
        # extractor instance (i.e. across all files/pages/passes in one job),
        # so `usage_cost_usd` reflects the exact total for the whole extraction.
        self._usage_prompt_tokens = 0
        self._usage_completion_tokens = 0
        self._usage_api_calls = 0

    @staticmethod
    def _looks_like_openai_key(value: str) -> bool:
        """Shape-only validation for OpenAI API keys.
        Real keys start with 'sk-' (or 'sk-proj-') and are >20 chars, no spaces.
        """
        if not value or len(value) < 20 or " " in value:
            return False
        return value.startswith("sk-")

    @staticmethod
    def _sanitize_openai_error(exc: BaseException, secrets: Iterable[str]) -> str:
        """Return a log-safe string for an OpenAI exception.
        OpenAI's 401 body echoes the invalid key back verbatim — strip any
        occurrence of the caller-provided secrets before logging.
        """
        msg = str(exc)
        for s in secrets:
            if s and len(s) >= 4 and s in msg:
                msg = msg.replace(s, "<redacted>")
        return msg[:500]

    # ── Public API ─────────────────────────────────────────────────────

    @property
    def key_source(self) -> str:
        """Returns which key source is active (safe to log — does NOT leak the key)."""
        if self._user_api_key and not self._user_key_runtime_disabled:
            return "user_supplied"
        if getattr(settings, "OPENAI_API_KEY", None):
            return "platform"
        return "none"

    @property
    def key_status(self) -> Dict[str, Any]:
        """Human-readable status of the Vision credential resolution.

        Safe to return to the frontend — never contains the key itself.
        Shape:
          {
            "source":         "user_supplied" | "platform" | "none",
            "user_rejected":  bool,          # True if user's key was ignored / disabled
            "reason":         "<message>" | None,
          }
        """
        reason = self._user_key_rejected_reason
        if not reason and self._user_key_runtime_disabled:
            reason = (
                "Your OpenAI key was rejected by OpenAI at request time "
                "(invalid, revoked, or out of quota). Falling back to the "
                "platform key for this extraction."
            )
        return {
            "source":        self.key_source,
            "user_rejected": bool(reason),
            "reason":        reason,
        }

    def _record_usage(self, response) -> None:
        """Accumulate prompt/completion token usage from one OpenAI response.
        Never raises — a missing/odd `usage` shape just means $0 is tracked
        for that call rather than breaking the extraction.
        """
        usage = getattr(response, "usage", None)
        if not usage:
            return
        try:
            self._usage_prompt_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
            self._usage_completion_tokens += int(getattr(usage, "completion_tokens", 0) or 0)
            self._usage_api_calls += 1
        except (TypeError, ValueError):
            pass

    @property
    def usage_tokens(self) -> Dict[str, int]:
        """Total Vision API token usage accumulated by this extractor instance."""
        return {
            "prompt_tokens":     self._usage_prompt_tokens,
            "completion_tokens": self._usage_completion_tokens,
            "total_tokens":      self._usage_prompt_tokens + self._usage_completion_tokens,
            "api_calls":         self._usage_api_calls,
        }

    @property
    def usage_cost_usd(self) -> float:
        """Exact $ cost of every Vision API call made by this extractor instance,
        computed from real token usage × the soft-coded per-model pricing table
        (template_v2_config.OPENAI_VISION_PRICING_PER_1M_TOKENS). Returns 0.0
        when no paid Vision calls were made (e.g. OCR-only "fast" mode).
        """
        if self._usage_prompt_tokens == 0 and self._usage_completion_tokens == 0:
            return 0.0
        model = self.config.get("vision_model", "gpt-4o")
        pricing = get_vision_pricing(model)
        cost = (
            (self._usage_prompt_tokens / 1_000_000) * pricing["input"]
            + (self._usage_completion_tokens / 1_000_000) * pricing["output"]
        )
        return round(cost, 6)

    def extract(self, pdf_file, source_file_name: str = "") -> ExtractionResult:
        """Run extraction on one PDF. Never raises — returns result with errors[]."""
        result = ExtractionResult()

        try:
            page_images = self._pdf_to_images(pdf_file)
        except Exception as exc:
            logger.error("[Handwriting] PDF->image conversion failed: %s", exc, exc_info=True)
            result.errors.append(f"PDF conversion failed: {exc}")
            return result

        result.pages_processed = len(page_images)
        if not page_images:
            result.errors.append("PDF produced 0 pages")
            return result

        # ── STEP 0: Preprocess pages (soft-coded) — helps both OCR and Vision ──
        if self.config.get("enable_preprocessing", True):
            page_images = [self._preprocess_image(img) for img in page_images]

        # ── VISION-ONLY MODE: skip OCR entirely (best accuracy, highest cost) ─
        vision_only = bool(self.config.get("vision_only_mode", False))
        vision_enabled = self.config.get("enable_vision_escalation", True)

        if vision_only and vision_enabled and self._vision_available():
            logger.info(
                "[Handwriting] Vision-only mode for %s (mode=%s, key=%s)",
                source_file_name, self.extraction_mode, self.key_source,
            )
            try:
                vision_fields = self._extract_with_vision(page_images, source_file_name)
                result.fields = vision_fields
                result.method_used = "vision_only" if vision_fields else "vision_empty"
                logger.info("[Handwriting] Vision-only extracted %d fields from %s",
                            len(vision_fields), source_file_name)
                return result
            except Exception as exc:
                safe = self._sanitize_openai_error(exc, secrets=[self._user_api_key or ""])
                logger.warning("[Handwriting] Vision-only failed, falling back to OCR: %s", safe)
                result.errors.append(f"Vision-only failed: {safe}")
                # Fall through to OCR path as a safety net.

        # ── STEP 1: Tesseract OCR (FREE) — always tried first for cost control ──
        ocr_ok = False
        ocr_available = True
        if self.config.get("enable_ocr_primary", True):
            try:
                ocr_fields, ocr_text, ocr_available = self._extract_with_ocr(
                    page_images, source_file_name,
                )
                result.fields.extend(ocr_fields)
                result.raw_text_by_page = ocr_text
                if ocr_fields:
                    result.method_used = "ocr"
                    ocr_ok = True
                logger.info("[Handwriting] OCR extracted %d fields from %s (available=%s)",
                            len(ocr_fields), source_file_name, ocr_available)
            except Exception as exc:
                logger.error("[Handwriting] OCR primary failed: %s", exc, exc_info=True)
                result.errors.append(f"OCR failed: {exc}")
                ocr_available = False

        # ── STEP 2: Decide whether to escalate to paid Vision ───────────────────
        should_escalate = self._should_escalate_to_vision(result.fields, ocr_available)
        vision_enabled = self.config.get("enable_vision_escalation", True)

        if should_escalate and vision_enabled and self._vision_available():
            logger.info("[Handwriting] Escalating to paid Vision for %s (OCR yielded %d fields, key=%s)",
                        source_file_name, len(result.fields), self.key_source)
            try:
                vision_fields = self._extract_with_vision(page_images, source_file_name)
                if vision_fields:
                    # Vision replaces OCR results (higher accuracy for handwriting).
                    result.fields = vision_fields
                    result.method_used = "vision_escalated" if ocr_ok else "vision"
                    logger.info("[Handwriting] Vision extracted %d fields from %s",
                                len(vision_fields), source_file_name)
            except Exception as exc:
                safe = self._sanitize_openai_error(exc, secrets=[self._user_api_key or ""])
                logger.warning("[Handwriting] Vision escalation failed: %s", safe)
                result.errors.append(f"Vision failed: {safe}")
        elif should_escalate and not vision_enabled:
            logger.info("[Handwriting] OCR result is weak but Vision escalation is disabled — keeping OCR output")

        if not result.method_used or result.method_used == "none":
            result.method_used = "ocr_empty" if ocr_available else "unavailable"

        return result

    # ── Escalation decision (soft-coded thresholds) ─────────────────────────

    def _should_escalate_to_vision(self, ocr_fields, ocr_available: bool) -> bool:
        """Return True if OCR result is too weak and paid Vision should be tried."""
        if not ocr_available and self.config.get("escalate_if_ocr_unavailable", True):
            return True

        min_fields = int(self.config.get("escalate_if_fields_below", 10))
        min_avg_conf = int(self.config.get("escalate_if_avg_conf_below", 65))

        if len(ocr_fields) < min_fields:
            return True

        avg_conf = (
            sum(f.confidence for f in ocr_fields) / len(ocr_fields)
            if ocr_fields else 0
        )
        if avg_conf < min_avg_conf:
            return True

        return False

    # ── PDF rendering ───────────────────────────────────────────────────────

    def _pdf_to_images(self, pdf_file) -> List[Image.Image]:
        """Convert PDF pages to PIL Images at configured DPI."""
        import fitz  # PyMuPDF

        if hasattr(pdf_file, "read"):
            pdf_bytes = pdf_file.read()
            pdf_file.seek(0)
        else:
            base_dir = os.path.abspath(str(settings.BASE_DIR))
            safe_filename = os.path.basename(pdf_file)
            safe_path = os.path.join(base_dir, safe_filename)
            with open(safe_path, "rb") as fp:
                pdf_bytes = fp.read()

        dpi = self.config.get("pdf_dpi", 200)
        zoom = dpi / 72.0
        images: List[Image.Image] = []

        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            for page in doc:
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                if img.mode != "RGB":
                    img = img.convert("RGB")
                images.append(img)

        return images

    # ── Image preprocessing (soft-coded, PIL + numpy only) ──────────────────

    def _preprocess_image(self, img: Image.Image) -> Image.Image:
        """Soft-coded preprocessing pipeline to improve OCR + Vision accuracy.

        Steps (each toggled by HANDWRITING_CONFIG):
          1. Neutralise yellow highlight cells → white (so OCR/Vision focuses on ink).
          2. Contrast boost → makes pen strokes darker vs paper.
          3. Optional sharpen (UnsharpMask) → enhances thin cursive strokes.
          4. Optional median denoise → removes salt/pepper (can blur cursive; off by default).
          5. Downscale if image is larger than `preprocess_max_side_px`.

        Never raises: on any error, returns the original image unchanged.
        """
        try:
            out = img
            cfg = self.config

            # 1) Remove yellow highlights — this is a HUGE accuracy win because
            #    highlighted cells become uniform noise for OCR and confuse Vision.
            if cfg.get("preprocess_remove_highlights", True):
                try:
                    import numpy as np
                    arr = np.array(out.convert("RGB"))
                    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
                    # Yellow ≈ high R, high G, low B. Empirical thresholds tuned for
                    # scanned checklists (bright yellow highlighter).
                    yellow_mask = (r > 200) & (g > 180) & (b < 170) & (r.astype(int) - b.astype(int) > 50)
                    arr[yellow_mask] = [255, 255, 255]
                    out = Image.fromarray(arr, mode="RGB")
                except Exception as exc:  # pragma: no cover
                    logger.debug("[Handwriting] Highlight removal skipped: %s", exc)

            # 2) Contrast boost
            boost = float(cfg.get("preprocess_contrast_boost", 1.0) or 1.0)
            if boost and abs(boost - 1.0) > 0.01:
                out = ImageEnhance.Contrast(out).enhance(boost)

            # 3) Sharpen (UnsharpMask) — makes thin pen strokes crisper
            if cfg.get("preprocess_sharpen", False):
                out = out.filter(ImageFilter.UnsharpMask(radius=1.2, percent=140, threshold=3))

            # 4) Median denoise (off by default — blurs cursive)
            if cfg.get("preprocess_denoise", False):
                out = out.filter(ImageFilter.MedianFilter(size=3))

            # 5) Cap the longest side to keep Vision payloads reasonable
            max_side = int(cfg.get("preprocess_max_side_px", 0) or 0)
            if max_side > 0:
                w, h = out.size
                longest = max(w, h)
                if longest > max_side:
                    scale = max_side / float(longest)
                    out = out.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

            return out
        except Exception as exc:  # never break the pipeline for preprocessing
            logger.warning("[Handwriting] Preprocessing skipped due to error: %s", exc)
            return img

    def _image_to_base64_data_url(self, img: Image.Image) -> str:
        """Encode PIL image as base64 data-URL in configured format/quality."""
        buf = io.BytesIO()
        fmt = self.config.get("pdf_image_format", "jpeg").lower()
        if fmt == "jpeg":
            img.save(buf, format="JPEG", quality=int(self.config.get("pdf_jpeg_quality", 85)))
            mime = "image/jpeg"
        else:
            img.save(buf, format="PNG")
            mime = "image/png"
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:{mime};base64,{b64}"

    # ── Vision path (OpenAI GPT-4o) ─────────────────────────────────────────

    def _vision_available(self) -> bool:
        # BYOK key wins if still trusted; otherwise fall back to platform key.
        if self._user_api_key and not self._user_key_runtime_disabled:
            return True
        return bool(getattr(settings, "OPENAI_API_KEY", None))

    def _resolve_api_key(self) -> Optional[str]:
        """Return the API key to use RIGHT NOW, respecting runtime disable."""
        if self._user_api_key and not self._user_key_runtime_disabled:
            return self._user_api_key
        return getattr(settings, "OPENAI_API_KEY", None)

    def _get_openai_client(self):
        if self._openai_client is None:
            from openai import OpenAI
            self._openai_client = OpenAI(
                api_key=self._resolve_api_key(),
                timeout=self.config.get("vision_timeout_sec", 60),
            )
        return self._openai_client

    def _reset_client_after_key_change(self) -> None:
        """Force lazy re-init of the OpenAI client with the currently-resolved key."""
        self._openai_client = None

    def _build_vision_prompt(self) -> str:
        """Soft-coded prompt built from TEMPLATE_V2_SECTIONS + TEMPLATE_V2_COLUMNS.

        Two modes controlled by `vision_use_layout_aware_prompt`:
          - True  : richer prompt describing the 6-column table layout, yellow
                    highlights, cursive tips, cross-outs, and section grouping.
          - False : legacy compact prompt (kept for cost-sensitive callers).
        """
        field_list_lines = []
        for section in TEMPLATE_V2_SECTIONS:
            field_list_lines.append(f"\n[{section['number']}] {section['title']}:")
            for f in section["fields"]:
                field_list_lines.append(f'  - "{f["id"]}"  ({f["name"]})')
        field_list_text = "\n".join(field_list_lines)

        if not self.config.get("vision_use_layout_aware_prompt", True):
            # ── Legacy compact prompt ─────────────────────────────────────────
            return f"""You are an expert electrical inspection engineer analyzing a handwritten UPS/Battery
inspection checklist. Extract every value written on this page and map it to the
correct field ID from the standard template below.

STANDARD TEMPLATE (field_id -> label):
{field_list_text}

Return STRICT JSON only:
{{
  "extractions": [
    {{"field_id":"...", "site_value":"...", "remarks":"...", "need_list":"...", "query":"...", "confidence":0-100}}
  ]
}}
Only use field_ids from the list above. Copy handwritten values verbatim.
"""

        # ── Layout-aware prompt (default) ─────────────────────────────────────
        column_lines = "\n".join(
            f"  Column {i+1}: {c['label']}  (JSON key: \"{c['key']}\")"
            for i, c in enumerate(TEMPLATE_V2_COLUMNS)
        )
        return f"""You are an expert electrical inspection engineer analyzing a scanned page from a
handwritten UPS / DC Charger / Battery inspection checklist. The page follows a
fixed 6-column table format described below. Your job is to read EVERY handwritten
entry (cursive, mixed-ink, cross-outs, arrows, sketches) and map each value to
the correct field_id AND the correct column.

═══════════════════ TABLE LAYOUT (6 columns, left→right) ═══════════════════
{column_lines}

Column 1 is a PRINTED label (do not extract — use it only to identify the field).
Columns 2-6 are handwritten. Some cells span multiple rows using a curly brace {{
or an arrow — apply the same value to every field the brace covers.

═══════════════════ FIELD DICTIONARY (id → label, grouped by section) ══════
{field_list_text}

═══════════════════ HANDWRITING RULES (critical for accuracy) ══════════════
- The document uses BLUE PEN for site values and RED PEN for engineer names /
  attention marks. YELLOW HIGHLIGHT marks priority items — always read those.
- Cross-outs mean "replaced" — use the NEW value written above/beside the crossed text.
- Ambiguous cursive: "AEB" often means "A&B", "A/B", or "Bus A & Bus B".
  Common terms in this domain: Charger, Chloride, SEC Energy Storage, Ammeter,
  Battery, Isolator, Feeder, ABB, MCB, Incomer, Bus, Redundant, Trip, Closing,
  Spare, Habshan, Lean Gas.
- Dates are usually dd/mm/yyyy (e.g. "22/06/2025" not "22 June 2025").
- Numeric ratings often include units: "100A", "450Ah", "8 Hrs", "24V", "1.8 VPC".
- "NA", "N/A", "-" all mean "Not Applicable".
- "Yes" / "Yl" / tick (✓) = Yes;  "No" / cross (✗) = No.
- Refer-to-sketch markers ("Refer Sketch", "As per drawing") should be captured
  as-is in the site_value field.

═══════════════════ OUTPUT (STRICT JSON, no markdown) ══════════════════════
{{
  "extractions": [
    {{
      "field_id":     "<one id from the field dictionary above>",
      "site_value":   "<Column 2 value, verbatim, or empty>",
      "remarks":      "<Column 3 value, or empty>",
      "need_list":    "<Column 4 value, or empty>",
      "query":        "<Column 5 value, or empty>",
      "confidence":   <integer 0..100 = how confident YOU are the value+field are right>
    }}
  ]
}}

RULES:
- Only include field_ids from the dictionary — NEVER invent new ones.
- Skip fields that have NO handwritten value visible on the page.
- Copy handwritten text VERBATIM — do not translate, expand abbreviations, or
  normalise units (write "100A" not "100 amps").
- If a value clearly belongs to more than one row (brace / arrow), emit one
  extraction per field with the same value.
- `confidence` should reflect uncertainty from illegible handwriting — use 40-60
  for hard-to-read cursive, 80-95 for clear entries.
- Ignore any Company Reply column (it will be filled later).
"""

    def _extract_with_vision(self, page_images: List[Image.Image],
                             source_file_name: str) -> List[ExtractedField]:
        """Run Vision extraction across all pages.

        Supports optional multi-pass consensus (soft-coded):
          - `enable_multipass_vision=True` runs N passes per page with different
            temperatures, then merges by `vision_consensus_strategy`.
          - Otherwise a single pass at `vision_temperature` is used.
        """
        client = self._get_openai_client()
        model = self.config.get("vision_model", "gpt-4o")
        detail = self.config.get("image_detail", "high")
        max_tokens = int(self.config.get("vision_max_tokens", 4000))
        base_temperature = float(self.config.get("vision_temperature", 0.0))
        min_conf = int(self.config.get("per_field_min_confidence", 40))

        multipass = bool(self.config.get("enable_multipass_vision", False))
        pass_count = max(1, int(self.config.get("vision_passes", 1) or 1))
        pass_temps = self.config.get("vision_pass_temperatures") or [base_temperature]
        if not multipass:
            pass_count = 1
        consensus_strategy = self.config.get("vision_consensus_strategy", "highest_confidence")

        prompt = self._build_vision_prompt()
        all_fields: List[ExtractedField] = []
        valid_field_ids = {f["id"] for f in get_all_v2_fields()}

        for page_idx, img in enumerate(page_images, start=1):
            try:
                data_url = self._image_to_base64_data_url(img)
                logger.info(
                    "[Handwriting] Vision page %d/%d for %s (mode=%s, passes=%d)",
                    page_idx, len(page_images), source_file_name,
                    self.extraction_mode, pass_count,
                )

                pass_results: List[List[ExtractedField]] = []
                for p in range(pass_count):
                    temperature = float(pass_temps[p % len(pass_temps)]) if pass_temps else base_temperature
                    try:
                        response = client.chat.completions.create(
                            model=model,
                            messages=[{
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": prompt},
                                    {"type": "image_url",
                                     "image_url": {"url": data_url, "detail": detail}},
                                ],
                            }],
                            temperature=temperature,
                            max_tokens=max_tokens,
                            response_format={"type": "json_object"},
                        )
                        self._record_usage(response)
                        raw = (response.choices[0].message.content or "").strip()
                        pass_fields = self._parse_vision_response(
                            raw, page_idx, source_file_name, valid_field_ids, min_conf,
                        )
                        pass_results.append(pass_fields)
                        logger.info(
                            "[Handwriting] Vision pass %d/%d on page %d yielded %d fields (temp=%.2f)",
                            p + 1, pass_count, page_idx, len(pass_fields), temperature,
                        )
                    except Exception as pass_exc:
                        # SANITIZE: never let the raw user key surface via OpenAI 401 body.
                        safe_msg = self._sanitize_openai_error(
                            pass_exc, secrets=[self._user_api_key or ""],
                        )
                        # 401 / invalid_api_key → disable user key at runtime and retry
                        # remaining passes with the platform key (if configured).
                        is_auth_error = (
                            "401" in safe_msg
                            or "invalid_api_key" in safe_msg
                            or "Incorrect API key" in safe_msg
                        )
                        if (
                            is_auth_error
                            and self._user_api_key
                            and not self._user_key_runtime_disabled
                            and getattr(settings, "OPENAI_API_KEY", None)
                        ):
                            logger.warning(
                                "[Handwriting] User BYOK key rejected by OpenAI — "
                                "disabling and retrying with platform key. Detail: %s",
                                safe_msg,
                            )
                            self._user_key_runtime_disabled = True
                            self._reset_client_after_key_change()
                            client = self._get_openai_client()
                            # Retry THIS pass once with the platform key.
                            try:
                                response = client.chat.completions.create(
                                    model=model,
                                    messages=[{
                                        "role": "user",
                                        "content": [
                                            {"type": "text", "text": prompt},
                                            {"type": "image_url",
                                             "image_url": {"url": data_url, "detail": detail}},
                                        ],
                                    }],
                                    temperature=temperature,
                                    max_tokens=max_tokens,
                                    response_format={"type": "json_object"},
                                )
                                self._record_usage(response)
                                raw = (response.choices[0].message.content or "").strip()
                                pass_fields = self._parse_vision_response(
                                    raw, page_idx, source_file_name, valid_field_ids, min_conf,
                                )
                                pass_results.append(pass_fields)
                                logger.info(
                                    "[Handwriting] Vision pass %d/%d on page %d (platform-key retry) yielded %d fields",
                                    p + 1, pass_count, page_idx, len(pass_fields),
                                )
                                continue
                            except Exception as retry_exc:
                                safe_retry = self._sanitize_openai_error(retry_exc, secrets=[])
                                logger.warning(
                                    "[Handwriting] Platform-key retry also failed: %s",
                                    safe_retry,
                                )
                                continue
                        logger.warning(
                            "[Handwriting] Vision pass %d/%d failed: %s",
                            p + 1, pass_count, safe_msg,
                        )
                        continue

                merged = self._merge_pass_results(pass_results, consensus_strategy)
                all_fields.extend(merged)

            except Exception as exc:
                safe = self._sanitize_openai_error(exc, secrets=[self._user_api_key or ""])
                logger.warning("[Handwriting] Vision page %d failed: %s", page_idx, safe)
                continue

        return all_fields

    @staticmethod
    def _merge_pass_results(pass_results: List[List[ExtractedField]],
                            strategy: str) -> List[ExtractedField]:
        """Merge multi-pass Vision results into a single field list.

        Strategies:
          - "highest_confidence" (default): keep the pass result with the highest
            confidence per field_id.
          - "majority_vote": for each field_id, pick the site_value that appears
            in the most passes (ties broken by highest confidence).
        """
        if not pass_results:
            return []
        if len(pass_results) == 1:
            return pass_results[0]

        # Flatten and group by field_id
        by_field: Dict[str, List[ExtractedField]] = {}
        for pf in pass_results:
            for ef in pf:
                by_field.setdefault(ef.field_id, []).append(ef)

        merged: List[ExtractedField] = []
        if strategy == "majority_vote":
            for fid, candidates in by_field.items():
                # Vote on the site_value component only (most-often-seen wins).
                votes: Dict[str, int] = {}
                for cand in candidates:
                    try:
                        sv = json.loads(cand.value).get("site_value", "").strip().lower()
                    except Exception:
                        sv = cand.value.strip().lower()
                    votes[sv] = votes.get(sv, 0) + 1
                # Best vote → pick the candidate with that value + max confidence
                best_val = max(votes.items(), key=lambda kv: kv[1])[0]
                winners = [c for c in candidates
                           if (json.loads(c.value).get("site_value", "").strip().lower()
                               if c.value.startswith("{") else c.value.strip().lower()) == best_val]
                merged.append(max(winners, key=lambda c: c.confidence))
        else:
            # highest_confidence
            for fid, candidates in by_field.items():
                merged.append(max(candidates, key=lambda c: c.confidence))

        return merged

    @staticmethod
    def _parse_vision_response(raw: str, page: int, source_file: str,
                               valid_ids: set, min_conf: int) -> List[ExtractedField]:
        """Robustly parse the AI JSON response (tolerates ```json fences)."""
        text = raw.strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("[Handwriting] Could not parse JSON from Vision response")
            return []

        items = payload.get("extractions") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            return []

        out: List[ExtractedField] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            fid = str(item.get("field_id", "")).strip()
            if fid not in valid_ids:
                continue
            confidence = int(item.get("confidence") or 0)
            if confidence < min_conf:
                continue

            # Build a composite value string that carries all four extractable columns.
            # The mapper is responsible for splitting them back into the 6-col row.
            out.append(ExtractedField(
                field_id=fid,
                value=json.dumps({
                    "site_value":  str(item.get("site_value") or "").strip(),
                    "remarks":     str(item.get("remarks") or "").strip(),
                    "need_list":   str(item.get("need_list") or "").strip(),
                    "query":       str(item.get("query") or "").strip(),
                }),
                confidence=max(0, min(100, confidence)),
                page=page,
                source_file=source_file,
                method="vision",
            ))
        return out

    # ── OCR fallback (Tesseract) ────────────────────────────────────────────

    def _extract_with_ocr(self, page_images: List[Image.Image],
                          source_file_name: str) -> tuple[List[ExtractedField], Dict[int, str], bool]:
        """Returns (fields, raw_text_by_page, ocr_available).

        `ocr_available` is False if pytesseract cannot be imported or the binary is missing,
        which is a signal to the caller to escalate to paid Vision.
        """
        try:
            import pytesseract
        except ImportError:
            logger.warning("[Handwriting] pytesseract not installed — will trigger escalation")
            return [], {}, False

        tess_cfg = self.config.get("tesseract_config", "--oem 3 --psm 6")
        tess_lang = self.config.get("tesseract_lang", "eng")
        threshold = float(self.config.get("fuzzy_match_threshold", 0.62))
        min_conf = int(self.config.get("per_field_min_confidence", 40))

        raw_text_by_page: Dict[int, str] = {}
        all_lines: List[tuple[int, str]] = []  # (page_idx, line)
        tesseract_ran = False

        for page_idx, img in enumerate(page_images, start=1):
            try:
                text = pytesseract.image_to_string(img, lang=tess_lang, config=tess_cfg)
                tesseract_ran = True
            except pytesseract.TesseractNotFoundError:
                logger.warning("[Handwriting] Tesseract binary not found — will trigger escalation")
                return [], raw_text_by_page, False
            except Exception as exc:
                logger.warning("[Handwriting] Tesseract failed on page %d: %s", page_idx, exc)
                continue
            raw_text_by_page[page_idx] = text
            for line in text.splitlines():
                line = line.strip()
                if line:
                    all_lines.append((page_idx, line))

        # Fuzzy match each field label against every OCR line — pick best line.
        all_fields: List[ExtractedField] = []
        for f in get_all_v2_fields():
            best_score = 0.0
            best_line = None
            best_page = 0
            for page_idx, line in all_lines:
                score = SequenceMatcher(None, f["name"].lower(), line.lower()).ratio()
                if score > best_score:
                    best_score = score
                    best_line = line
                    best_page = page_idx

            if best_line and best_score >= threshold:
                # After the matched label text, take the remainder as the value guess.
                label_lower = f["name"].lower()
                line_lower = best_line.lower()
                idx = line_lower.find(label_lower[:min(10, len(label_lower))])
                value = best_line[idx + len(f["name"]):].strip(" :\t-") if idx >= 0 else best_line
                confidence = int(best_score * 100)
                if confidence < min_conf:
                    continue
                all_fields.append(ExtractedField(
                    field_id=f["id"],
                    value=json.dumps({
                        "site_value": value,
                        "remarks":    "",
                        "need_list":  "",
                        "query":      "",
                    }),
                    confidence=confidence,
                    page=best_page,
                    source_file=source_file_name,
                    method="ocr",
                ))

        return all_fields, raw_text_by_page, tesseract_ran
