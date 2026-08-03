"""
P&ID Analysis Service - Multi-Pass Comprehensive Analysis
Architecture: OCR + Vision + Cross-Validation + Chain-of-Thought + Reference Verification
"""
import os
import base64
import io
import json
import re
import uuid
from typing import Dict, List, Any, Optional, Set, Tuple
from django.conf import settings
from openai import OpenAI
import fitz  # PyMuPDF
from PIL import Image
from .reference_processor import ReferenceDocumentProcessor
from .element_classifier import get_classifier, ElementType


def ocr_inventory_contains(inventory: set, token: str) -> bool:
    """Return True if any item in inventory starts with or contains the given token.
    Used by _cag_post_filter for partial-match lookups (e.g. token '4"-BD-4860'
    matches inventory item '4"-BD-4860-033842-X-N')."""
    t = token.upper().strip()
    if not t or len(t) < 4:
        return False
    return any(item.startswith(t) or t in item for item in inventory)


class PIDAnalysisService:
    """AI-Powered P&ID Analysis Service with Multi-Pass Validation"""

    # ── Model cascade (vision-capable only — gpt-3.5 does NOT support images) ──
    # Primary model is used for all passes.  If it refuses to process the image
    # (e.g. due to context overload on long prompts), FALLBACK_MODEL is tried
    # automatically.  Both support vision; mini is lighter and more tolerant.
    _PRIMARY_MODEL   = "gpt-4o"
    _FALLBACK_MODEL  = "gpt-4o-mini"

    # Phrases that indicate the model refused / couldn't see the image.
    # Checked case-insensitively.  Extend here; no other code changes needed.
    _REFUSAL_PHRASES = (
        "i'm unable to process",
        "i'm sorry, i can't",
        "i cannot process",
        "i can't process",
        "cannot view",
        "unable to view",
        "no image",
        "don't see any image",
        "no image was provided",
        "i don't have the ability to view",
        "i'm not able to view",
    )

    @classmethod
    def _model_refused_image(cls, text: str) -> bool:
        """Return True if the model response is a vision-refusal (not a real result)."""
        lower = text.lower()
        return any(phrase in lower for phrase in cls._REFUSAL_PHRASES)

    def _call_with_vision_fallback(
        self,
        messages: list,
        pass_label: str,
        primary_tokens: int,
        fallback_tokens: int,
        primary_timeout: int,
        fallback_timeout: int,
        temperature: float = 0.0,
    ) -> str:
        """
        Call _PRIMARY_MODEL; if it refuses the image, transparently retry with
        _FALLBACK_MODEL.  Returns the raw response text (empty string on total failure).
        Both models are vision-capable — GPT-3.5 / GPT-4-base are NOT used.
        """
        # ── Primary attempt ────────────────────────────────────────────────────
        print(f"[INFO] {pass_label}: calling {self._PRIMARY_MODEL}...")
        try:
            resp = self.client.chat.completions.create(
                model=self._PRIMARY_MODEL,
                messages=messages,
                max_tokens=primary_tokens,
                temperature=temperature,
                seed=42,
                timeout=primary_timeout,
            )
            text = (resp.choices[0].message.content or "").strip() if resp and resp.choices else ""
        except Exception as ex:
            print(f"[WARNING] {pass_label}: {self._PRIMARY_MODEL} call failed ({ex}) — trying fallback.")
            text = ""

        if text and not self._model_refused_image(text):
            print(f"[DEBUG {pass_label}] {self._PRIMARY_MODEL} OK | len={len(text)} | preview={text[:120]}")
            return text

        # ── Fallback to lighter vision model ───────────────────────────────────
        reason = "refused image" if text else "empty response"
        print(f"[WARNING] {pass_label}: {self._PRIMARY_MODEL} {reason} — retrying with {self._FALLBACK_MODEL}...")
        try:
            fb_resp = self.client.chat.completions.create(
                model=self._FALLBACK_MODEL,
                messages=messages,
                max_tokens=fallback_tokens,
                temperature=temperature,
                seed=42,
                timeout=fallback_timeout,
            )
            fb_text = (fb_resp.choices[0].message.content or "").strip() if fb_resp and fb_resp.choices else ""
        except Exception as fb_ex:
            print(f"[WARNING] {pass_label}: {self._FALLBACK_MODEL} also failed ({fb_ex}).")
            return ""

        if fb_text and not self._model_refused_image(fb_text):
            print(f"[DEBUG {pass_label}] {self._FALLBACK_MODEL} OK | len={len(fb_text)} | preview={fb_text[:120]}")
            print(f"[INFO] {pass_label}: {self._FALLBACK_MODEL} succeeded as fallback.")
            return fb_text

        print(f"[WARNING] {pass_label}: both models refused / returned empty — returning empty.")
        return ""

    def __init__(self):
        """Initialize Multi-Model AI client (OpenAI + Gemini)"""
        from .multi_model_service import MultiModelAIService
        
        # Initialize multi-model AI service (supports both OpenAI and Gemini)
        try:
            self.ai_service = MultiModelAIService()
            print('[INFO] ✅ Multi-Model AI Service initialized (OpenAI + Gemini)')
        except Exception as e:
            print(f'[WARNING] Multi-model service failed, falling back to OpenAI only: {e}')
            # Fallback to OpenAI only
            api_key = (
                os.getenv('OPENAI_API_KEY') or
                getattr(settings, 'OPENAI_API_KEY', None)
            )
            if not api_key:
                raise ValueError("OPENAI_API_KEY not configured")
            self.client = OpenAI(api_key=api_key, timeout=180.0, max_retries=2)
            self.ai_service = None
        
        # Keep legacy OpenAI client for backward compatibility
        api_key = os.getenv('OPENAI_API_KEY') or getattr(settings, 'OPENAI_API_KEY', None)
        if api_key:
            self.client = OpenAI(
                api_key=api_key,
                timeout=180.0,
                max_retries=2
            )
        
        self.reference_processor = ReferenceDocumentProcessor()
        self.extracted_text = ""
        self.instrument_tags = set()
        self.equipment_tags = set()
        self.line_numbers = set()
        self.notes_references = set()
        self.drawing_number_candidates = set()
        
        # CONTEXT ISOLATION: Generate unique session ID for this analysis
        self.session_id = str(uuid.uuid4())
        print(f'[CONTEXT ISOLATION] Session ID: {self.session_id}')
        
        # Get deterministic classifier instance
        self.classifier = get_classifier()
        
        # Load soft-coded analysis configuration
        self._load_analysis_config()
        # Initialize optional Google Gemini client
        self._init_gemini_client()
        print('[INFO] Multi-Pass PID Analysis Service initialized with 180s timeout')

    # =========================================================================
    # SOFT-CODED CONFIGURATION LOADER
    # =========================================================================

    def _load_analysis_config(self):
        """
        Load soft-coded analysis configuration from pid_analysis_config.json.
        Populates instance variables used throughout all analysis passes.
        Gracefully falls back to hard-coded defaults if the file is missing/malformed.
        No code changes needed to tune parameters — edit the JSON file directly.
        """
        import json as _json
        config_path = os.path.join(os.path.dirname(__file__), 'config', 'pid_analysis_config.json')
        try:
            with open(config_path, 'r', encoding='utf-8') as _f:
                cfg = _json.load(_f).get('pid_analysis', {})

            self.pdf_dpi                  = int(cfg.get('pdf_dpi', 300))
            self.ocr_sparse_threshold     = int(cfg.get('ocr_sparse_threshold', 10))
            self.ocr_tesseract_fallback   = bool(cfg.get('ocr_tesseract_fallback_enabled', True))
            self.min_issues_target        = int(cfg.get('min_issues_target', 20))
            self.min_issues_rescan        = int(cfg.get('min_issues_rescan_threshold', 15))
            self.near_dup_threshold       = int(cfg.get('near_dup_threshold', 2))
            self.supplement_notes_below   = int(cfg.get('supplement_notes_below', 20))
            self.confidence_high_thresh   = int(cfg.get('confidence_high_threshold', 15))

            _p3 = cfg.get('pass_3', {})
            self.pass3_max_tokens    = int(_p3.get('max_tokens', 16384))
            self.pass3_temperature   = float(_p3.get('temperature', 0.3))
            self.pass3_timeout       = int(_p3.get('timeout_seconds', 600))
            # Load Pass 3 system prompt from external txt file (soft-coded)
            _p3_prompt_file = _p3.get('system_prompt_file', 'pass3_system_prompt.txt')
            _p3_prompt_path = os.path.join(os.path.dirname(__file__), 'config', _p3_prompt_file)
            try:
                with open(_p3_prompt_path, 'r', encoding='utf-8') as _pf:
                    self.pass3_system_prompt = _pf.read().strip()
                print(f'[INFO] Pass 3 prompt loaded from {_p3_prompt_file} ({len(self.pass3_system_prompt)} chars)')
            except Exception as _pe:
                self.pass3_system_prompt = None
                print(f'[WARNING] Could not load {_p3_prompt_file} ({_pe}) — using built-in Pass 3 prompt')

            _p5 = cfg.get('pass_5', {})
            self.pass5_max_tokens    = int(_p5.get('max_tokens', 12000))
            self.pass5_temperature   = float(_p5.get('temperature', 0.4))
            self.pass5_timeout       = int(_p5.get('timeout_seconds', 300))

            _p6 = cfg.get('pass_6', {})
            self.pass6_enabled       = bool(_p6.get('enabled', True))
            self.pass6_max_tokens    = int(_p6.get('max_tokens', 12000))
            self.pass6_temperature   = float(_p6.get('temperature', 0.0))
            self.pass6_timeout       = int(_p6.get('timeout_seconds', 300))

            _p7 = cfg.get('pass_7', {})
            self.pass7_max_tokens    = int(_p7.get('max_tokens', 8000))
            self.pass7_temperature   = float(_p7.get('temperature', 0.2))
            self.pass7_timeout       = int(_p7.get('timeout_seconds', 240))

            _p8 = cfg.get('pass_8', {})
            self.pass8_max_tokens    = int(_p8.get('max_tokens', 16000))
            self.pass8_temperature   = float(_p8.get('temperature', 0.0))
            self.pass8_timeout       = int(_p8.get('timeout_seconds', 360))

            _ld = cfg.get('layout_detection', {})
            self.layout_detection_enabled = bool(_ld.get('enabled', True))
            self.layout_default_zones     = _ld.get('default_zones', [
                "Top-Left", "Top-Center", "Top-Right",
                "Middle-Left", "Middle-Center", "Middle-Right",
                "Bottom-Left", "Bottom-Center", "Bottom-Right",
            ])

            # Gemini provider config (soft-coded)
            _gem = cfg.get('gemini', {})
            self.gemini_enabled         = bool(_gem.get('enabled', False))
            self._GEMINI_PRIMARY_MODEL  = _gem.get('primary_model', 'gemini-2.0-flash')
            self._GEMINI_FALLBACK_MODEL = _gem.get('fallback_model', 'gemini-2.0-flash-lite')
            self.gemini_api_key_env     = _gem.get('api_key_env', 'GEMINI_API_KEY')
            self.gemini_max_tokens      = int(_gem.get('max_output_tokens', 32768))

            # Per-pass provider routing (soft-coded: 'gemini' or 'openai')
            _rt = cfg.get('provider_routing', {})
            self.pass3_provider = _rt.get('pass_3', 'openai')
            self.pass5_provider = _rt.get('pass_5', 'openai')
            self.pass6_provider = _rt.get('pass_6', 'openai')
            self.pass7_provider = _rt.get('pass_7', 'openai')

            # S3 analysis result cache (soft-coded: change enabled/prefix without code deploy)
            _ca = cfg.get('cache', {})
            self.cache_enabled       = bool(_ca.get('enabled', False))
            self.cache_backend       = _ca.get('backend', 's3')
            self.cache_s3_bucket_env = _ca.get('s3_bucket_env', 'AWS_STORAGE_BUCKET_NAME')
            self.cache_s3_prefix     = _ca.get('s3_prefix', 'pid_analysis_cache/')
            self.cache_ttl_days      = int(_ca.get('ttl_days', 0))

            # Suppressed categories (soft-coded: add/remove without code deploy)
            self.suppressed_categories = [
                c.lower().strip() for c in cfg.get('suppressed_categories', [])
            ]
            if self.suppressed_categories:
                print(f'[INFO] Suppressed categories: {self.suppressed_categories}')

            # Evidence writing guidance (SOFT-CODED: controls how the AI justifies every finding)
            # Injected as a block into every pass system prompt so all passes enforce the same standard.
            _ev = cfg.get('evidence_guidance', {})
            self.evidence_guidance_block = self._build_evidence_block(_ev)
            if self.evidence_guidance_block:
                print(f'[INFO] Evidence guidance block loaded ({len(self.evidence_guidance_block)} chars)')

            print('[INFO] pid_analysis_config.json loaded successfully')

        except Exception as _ex:
            print(f'[WARNING] Could not load pid_analysis_config.json ({_ex}) — using built-in defaults')
            self.pdf_dpi                = 300
            self.ocr_sparse_threshold   = 10
            self.ocr_tesseract_fallback = True
            self.min_issues_target      = 20
            self.min_issues_rescan      = 15
            self.near_dup_threshold     = 2
            self.supplement_notes_below = 20
            self.confidence_high_thresh = 15
            self.pass3_max_tokens       = 16384
            self.pass3_temperature      = 0.0
            self.pass3_timeout          = 600
            self.pass3_system_prompt    = None  # built-in prompt used as fallback
            self.pass5_max_tokens       = 12000
            self.pass5_temperature      = 0.0
            self.pass5_timeout          = 300
            self.pass6_max_tokens       = 12000
            self.pass6_temperature      = 0.0
            self.pass6_timeout          = 300
            self.pass7_max_tokens       = 8000
            self.pass7_temperature      = 0.0
            self.pass7_timeout          = 240
            self.pass8_max_tokens       = 16000
            self.pass8_temperature      = 0.0
            self.pass8_timeout          = 360
            self.pass6_enabled          = True   # defaults to on if config missing
            self.layout_detection_enabled = True
            self.layout_default_zones   = [
                "Top-Left", "Top-Center", "Top-Right",
                "Middle-Left", "Middle-Center", "Middle-Right",
                "Bottom-Left", "Bottom-Center", "Bottom-Right",
            ]
            self.gemini_enabled         = False
            self._GEMINI_PRIMARY_MODEL  = 'gemini-2.0-flash'
            self._GEMINI_FALLBACK_MODEL = 'gemini-2.0-flash-lite'
            self.gemini_api_key_env     = 'GEMINI_API_KEY'
            self.gemini_max_tokens      = 32768
            self.pass3_provider         = 'openai'
            self.pass5_provider         = 'openai'
            self.pass6_provider         = 'openai'
            self.pass7_provider         = 'openai'
            # Cache defaults (off by default when config is unavailable)
            self.cache_enabled          = False
            self.cache_backend          = 's3'
            self.cache_s3_bucket_env    = 'AWS_STORAGE_BUCKET_NAME'
            self.cache_s3_prefix        = 'pid_analysis_cache/'
            self.cache_ttl_days         = 0
            self.suppressed_categories  = []
            self.evidence_guidance_block = ''  # no evidence guidance when config fails

    # =========================================================================
    # EVIDENCE GUIDANCE FORMATTER  (soft-coded via pid_analysis_config.json)
    # =========================================================================

    def _build_evidence_block(self, ev_cfg: dict) -> str:
        """
        Format the soft-coded evidence_guidance config into a prompt injection block.
        Called by _load_analysis_config(). The returned string is appended to every
        pass system prompt so all AI passes enforce the same evidence quality standard.
        SOFT-CODED: edit pid_analysis_config.json → evidence_guidance to change behaviour.
        """
        if not ev_cfg:
            return ''
        sep = '=' * 59
        lines = [
            sep,
            'EVIDENCE FIELD — MANDATORY WRITING STANDARD',
            sep,
            '',
            'Format EVERY "evidence" value using this exact template:',
            f'  {ev_cfg.get("format_template", "")}',
            '',
            'QUALITY RULES — violations produce findings that senior engineers will reject:',
        ]
        for rule in ev_cfg.get('quality_rules', []):
            lines.append(f'  \u2192 {rule}')
        lines.append('')
        lines.append('DRAWING-INTERNAL CHECK BASIS — use only what is observable on this drawing:')
        for cat, standards in ev_cfg.get('standards_library', {}).items():
            lines.append(f'  [{cat.upper()}]')
            for s in standards:
                lines.append(f'    \u2022 {s}')
        lines.append('')
        lines.append('EVIDENCED EXAMPLES — every evidence field must match this depth and precision:')
        for i, ex in enumerate(ev_cfg.get('examples', []), 1):
            lines.append(f'  Example {i} (category: {ex.get("category", "")}, ref: {ex.get("pid_reference", "")})')
            lines.append(f'  "{ex.get("good_evidence", "")}"')
            lines.append('')
        return '\n'.join(lines)

    # =========================================================================
    # GEMINI PROVIDER — init, vision call, unified routing wrapper (soft-coded)
    # =========================================================================

    def _init_gemini_client(self):
        """
        Initialize Google Gemini AI client if enabled in pid_analysis_config.json.
        Stores the configured genai module as self._gemini_module (None if disabled/failed).
        API key is read from the GEMINI_API_KEY environment variable — never hardcoded.
        SOFT-CODED: enabled/disabled via pid_analysis_config.json → gemini.enabled
        """
        self._gemini_client = None
        if not getattr(self, 'gemini_enabled', False):
            print('[INFO] Gemini provider disabled in config (gemini.enabled=false)')
            return
        try:
            from google import genai
            api_key = (
                os.getenv(getattr(self, 'gemini_api_key_env', 'GEMINI_API_KEY'))
                or os.getenv('GEMINI_API_KEY')
                or getattr(settings, 'GEMINI_API_KEY', None)
            )
            if not api_key:
                print('[WARNING] Gemini enabled but GEMINI_API_KEY env var not set — Gemini disabled')
                return
            self._gemini_client = genai.Client(api_key=api_key)
            print(f'[INFO] Google Gemini client initialized (primary={self._GEMINI_PRIMARY_MODEL})')
        except ImportError:
            print('[WARNING] google-genai not installed — Gemini disabled. '
                  'Run: pip install google-genai>=0.8.0')
        except Exception as _ex:
            print(f'[WARNING] Gemini initialization failed: {_ex} — falling back to OpenAI only')

    def _call_gemini_vision(
        self,
        messages: list,
        pass_label: str,
        max_tokens: int = 32768,
        temperature: float = 0.3,
        timeout: int = 600,
    ) -> str:
        """
        Call Google Gemini vision API using OpenAI-format messages.
        Converts the messages list to Gemini native parts format:
          - role='system' content  → GenerativeModel system_instruction
          - role='user' text parts → text strings
          - role='user' image_url  → {mime_type, data: bytes}
        Returns response text string or '' on any failure (caller falls back to OpenAI).
        SOFT-CODED: model controlled by pid_analysis_config.json → gemini.primary_model
        """
        if not getattr(self, '_gemini_client', None):
            return ''
        import base64 as _b64
        from google.genai import types as _gtypes
        client = self._gemini_client
        try:
            system_prompt = next(
                (m.get('content', '') for m in messages if m.get('role') == 'system'), ''
            )
            # Build content parts list from OpenAI-format messages
            parts = []
            for msg in messages:
                if msg.get('role') != 'user':
                    continue
                content = msg.get('content', '')
                if isinstance(content, str):
                    parts.append(content)
                elif isinstance(content, list):
                    for part in content:
                        ptype = part.get('type', '')
                        if ptype == 'text':
                            parts.append(part.get('text', ''))
                        elif ptype == 'image_url':
                            img_url = part.get('image_url', {}).get('url', '')
                            if img_url.startswith('data:image/'):
                                header, b64data = img_url.split(',', 1)
                                mime = header.split(';')[0].replace('data:', '')
                                parts.append(_gtypes.Part.from_bytes(
                                    data=_b64.b64decode(b64data),
                                    mime_type=mime,
                                ))
            if not parts:
                print(f'[WARNING] {pass_label}: no content parts for Gemini — skipping')
                return ''
            model_name = getattr(self, '_GEMINI_PRIMARY_MODEL', 'gemini-2.0-flash')
            print(
                f'[INFO] {pass_label}: calling Gemini {model_name}'
                f' ({len(parts)} parts, max_tokens={max_tokens})...'
            )
            config = _gtypes.GenerateContentConfig(
                system_instruction=system_prompt or None,
                max_output_tokens=min(max_tokens, 65536),
                temperature=temperature,
                seed=42,
            )
            response = client.models.generate_content(
                model=model_name,
                contents=parts,
                config=config,
            )
            result_text = (response.text or '').strip()
            _meta = getattr(response, 'usage_metadata', None)
            tokens_in  = getattr(_meta, 'prompt_token_count', 0)
            tokens_out = getattr(_meta, 'candidates_token_count', 0)
            print(f'[INFO] {pass_label}: Gemini ✓ {len(result_text)} chars | in={tokens_in} out={tokens_out}')
            return result_text
        except Exception as _gex:
            print(f'[WARNING] {pass_label}: Gemini call failed ({_gex}) — cascading to OpenAI')
            return ''

    def _call_ai_vision(
        self,
        messages: list,
        pass_label: str,
        provider: str = 'openai',
        max_tokens: int = 16384,
        temperature: float = 0.3,
        timeout: int = 600,
    ) -> str:
        """
        Unified AI vision routing wrapper — Multi-Model (Gemini + OpenAI).
        Uses MultiModelAIService for automatic provider routing and fallback.
          provider='gemini'  → try Gemini first; cascade to OpenAI if empty/refused
          provider='openai'  → try OpenAI first; cascade to Gemini if fails
          provider='both'    → intelligent routing based on AI_MODEL_PROVIDER env var
        """
        # Use multi-model service if available (supports both OpenAI and Gemini with auto-fallback)
        if hasattr(self, 'ai_service') and self.ai_service:
            try:
                print(f"[INFO] {pass_label}: Using multi-model service (provider: {provider})...")
                
                # Extract images from messages for vision analysis
                images_base64 = []
                text_prompt = ""
                for msg in messages:
                    if msg.get('role') == 'user':
                        content = msg.get('content', [])
                        if isinstance(content, list):
                            for item in content:
                                if item.get('type') == 'text':
                                    text_prompt = item.get('text', '')
                                elif item.get('type') == 'image_url':
                                    # Extract base64 from data URL
                                    img_url = item.get('image_url', {}).get('url', '')
                                    if 'base64,' in img_url:
                                        base64_data = img_url.split('base64,')[1]
                                        images_base64.append(base64_data)
                
                # Use multi-model vision analysis
                response_text = self.ai_service.vision_analysis(
                    images_base64=images_base64,
                    prompt=text_prompt,
                    model="auto" if provider == 'both' else provider,
                    max_tokens=max_tokens,
                    temperature=temperature
                )
                
                if response_text:
                    print(f"[INFO] {pass_label}: Multi-model vision analysis successful")
                    return response_text
                    
            except Exception as e:
                print(f"[WARNING] {pass_label}: Multi-model service failed: {e}")
                print(f"[INFO] {pass_label}: Falling back to legacy OpenAI chain...")
        
        # Legacy fallback: use original provider-based routing
        if provider == 'gemini' and getattr(self, '_gemini_client', None):
            gemini_text = self._call_gemini_vision(
                messages=messages,
                pass_label=pass_label,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
            )
            if gemini_text and not self._model_refused_image(gemini_text):
                return gemini_text
            print(f'[INFO] {pass_label}: Gemini empty/refused — cascading to OpenAI')
        # OpenAI chain: primary gpt-4o → fallback gpt-4o-mini
        return self._call_with_vision_fallback(
            messages=messages,
            pass_label=pass_label,
            primary_tokens=max_tokens,
            fallback_tokens=min(max_tokens, 12000),
            primary_timeout=timeout,
            fallback_timeout=max(timeout // 2, 120),
            temperature=temperature,
        )

    def _detect_pid_layout(self, images_base64: List[str]) -> Dict[str, Any]:
        """
        Pre-analysis layout detection: a fast, lightweight gpt-4o-mini call that
        identifies the drawn P&ID's structural layout BEFORE the main 8-pass analysis.

        Returns a dict containing:
          - zones          : list of zone name strings for this specific drawing
          - layout         : raw parsed JSON from the model
          - layout_context_str : a formatted string injected into Pass 3, 5, and 6
                              prompts so the AI knows exactly where title block,
                              notes section, legend, and process zones are located.
          - detected       : bool — True if detection succeeded

        Why this matters:
          Every P&ID has its own layout design. ADNOC drawings differ from Petrofac,
          Shell, or bespoke EPC formats. Without layout context the AI assumes a
          generic 9-zone 3×3 grid and may place findings in wrong zones or miss
          the notes/legend entirely. This pass makes analysis dynamically adaptive.
        """
        if not self.layout_detection_enabled or not images_base64:
            return {
                'zones': self.layout_default_zones,
                'layout_context_str': '',
                'detected': False,
            }

        try:
            # Use only the first image — sufficient for structural layout detection
            first_img = images_base64[0]

            system_msg = (
                "You are a P&ID drawing layout analyst.\n"
                "Your ONLY task: describe the STRUCTURAL LAYOUT of this P&ID drawing.\n"
                "Do NOT scan for compliance issues. Do NOT produce findings.\n"
                "ONLY describe the physical structure of the drawing.\n\n"
                "Return ONLY valid JSON — no markdown fences:\n"
                "{\n"
                '  "company_standard": "ADNOC|Shell|BP|Petrofac|FEED|EPC|Custom|Unknown",\n'
                '  "drawing_title": "Brief title from title block, or empty string",\n'
                '  "drawing_number": "Drawing number from title block, or empty string",\n'
                '  "revision": "Revision letter/number, or empty string",\n'
                '  "zones": ["zone1", "zone2", ...],\n'
                '  "zone_count": 9,\n'
                '  "title_block_position": "Bottom-Right|Bottom-Left|Right|Bottom|Top",\n'
                '  "notes_section_position": "Bottom-Left|Bottom-Right|Bottom|Left|Right|None",\n'
                '  "legend_section_position": "Top-Right|Bottom-Left|Left|Right|None",\n'
                '  "has_revision_cloud": true,\n'
                '  "drawing_orientation": "Landscape|Portrait",\n'
                '  "drawing_type": "Process_PID|Utility_PID|HVAC|Instrument_Loop|General_Arrangement|Unknown",\n'
                '  "layout_notes": "One-sentence summary for analysis engine"\n'
                "}\n\n"
                "For zones: use a logical grid matching the drawing content.\n"
                "Standard P&IDs: 9 zones (3×3). Dense complex drawings: 12 zones (4×3).\n"
                "Name zones as: Top-Left, Top-Center, Top-Right, Middle-Left, etc.\n"
                "OR use area names visible on the drawing (e.g. 'Separator Area', 'Utility Header')."
            )

            user_text = (
                "Analyze the layout of this P&ID drawing.\n"
                "Look at: title block position, notes section, legend location, "
                "major equipment groupings, zone markers or area labels.\n"
                "Return ONLY valid JSON describing the drawing layout."
            )

            messages = [
                {"role": "system", "content": system_msg},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{first_img}",
                                "detail": "low",  # Low detail — overview only
                            },
                        },
                    ],
                },
            ]

            response = self.client.chat.completions.create(
                model=self._FALLBACK_MODEL,  # Mini model — fast + cheap
                messages=messages,
                max_tokens=800,
                temperature=0.0,
                seed=42,
                timeout=60,
            )

            resp_text = (
                (response.choices[0].message.content or "").strip()
                if response and response.choices else ""
            )
            if not resp_text:
                raise ValueError("Empty response from layout detection model")

            json_start = resp_text.find('{')
            json_end   = resp_text.rfind('}') + 1
            layout = json.loads(resp_text[json_start:json_end])

            zones = layout.get('zones', self.layout_default_zones)
            if not zones or len(zones) < 3:
                zones = self.layout_default_zones

            title_blk  = layout.get('title_block_position', 'Bottom-Right')
            notes_pos  = layout.get('notes_section_position', 'Bottom-Right')
            legend_pos = layout.get('legend_section_position', 'None')
            standard   = layout.get('company_standard', 'Unknown')
            drw_type   = layout.get('drawing_type', 'Process_PID')
            drw_title  = layout.get('drawing_title', '')
            drw_number = layout.get('drawing_number', '')
            revision   = layout.get('revision', '')
            lay_notes  = layout.get('layout_notes', '')
            zone_list  = ', '.join(zones)

            ctx_lines = [
                "╔══════════════════════════════════════════════════════════════",
                "║  P&ID LAYOUT CONTEXT  (auto-detected — use for zone reporting)",
                "╚══════════════════════════════════════════════════════════════",
                "  ⚠️  CONSISTENCY-ONLY MODE: Base ALL findings on drawing-internal",
                "      inconsistencies ONLY. Do NOT apply any external company or",
                "      industry standards (ADNOC, ARAMCO, SHELL, ISA, API, ASME,",
                "      NACE, IEC etc.) unless explicitly referenced in this drawing's",
                "      own notes or title block.",
                f"  Drawing Type      : {drw_type}",
            ]
            if drw_title:
                ctx_lines.append(f"  Drawing Title     : {drw_title}")
            if drw_number:
                ctx_lines.append(f"  Drawing Number    : {drw_number}")
            if revision:
                ctx_lines.append(f"  Revision          : {revision}")
            ctx_lines += [
                f"  Zone Grid         : {zone_list}",
                f"  Title Block       : {title_blk}",
                f"  Notes Section     : {notes_pos}  ← read ALL notes/holds carefully here",
                f"  Legend / Symbols  : {legend_pos}  ← verify all abbreviations here",
            ]
            if lay_notes:
                ctx_lines.append(f"  Layout Notes      : {lay_notes}")
            ctx_lines += [
                "",
                "  SCANNING RULES (based on layout):",
                f"  • Use ONLY these zone names when filling location_on_drawing.zone: {zone_list}",
                f"  • Notes and HOLDS are in the {notes_pos} section — read each one verbatim",
                f"  • Legend/symbols are in the {legend_pos} area — use to resolve abbreviations",
                "  • If a zone name you would write is NOT in the list above, use the nearest listed zone instead",
                "══════════════════════════════════════════════════════════════",
            ]

            layout_context_str = '\n'.join(ctx_lines)

            print(
                f"[INFO] Layout detection: {standard} | {drw_type} | {len(zones)} zones | "
                f"notes@{notes_pos} | legend@{legend_pos}"
            )

            return {
                'zones': zones,
                'layout': layout,
                'layout_context_str': layout_context_str,
                'detected': True,
            }

        except Exception as _ex:
            print(f"[WARNING] Layout detection failed ({_ex}) — using default 9-zone grid")
            return {
                'zones': self.layout_default_zones,
                'layout_context_str': '',
                'detected': False,
            }

    # =========================================================================
    # S3 ANALYSIS RESULT CACHE — content-addressable, soft-coded via config
    # =========================================================================
    # Key  : SHA-256(raw PDF bytes) + analysis_mode  →  unique per file content
    # Store: gzip-compressed JSON in S3 under pid_analysis_cache/{hash}_{mode}.json.gz
    # Hit  : full 8-pass analysis is skipped; cached result returned in <1 second
    # Miss : analysis runs normally; result stored to S3 before returning
    # =========================================================================

    def _read_pdf_bytes(self, pdf_file) -> bytes:
        """Read raw bytes from either a file-path string or a Django FieldFile.
        After reading the file pointer is reset to 0 so later passes can re-read."""
        if isinstance(pdf_file, str):
            with open(pdf_file, 'rb') as _fh:
                return _fh.read()
        pdf_file.seek(0)
        data = pdf_file.read()
        pdf_file.seek(0)   # reset so Pass 1 / _pdf_to_base64_images can read it again
        return data

    def _compute_cache_key(self, pdf_bytes: bytes, analysis_mode: str) -> str:
        """Return the S3 object key for this (content, mode) pair.
        Format: {prefix}{sha256}_{mode}.json.gz
        The key is purely content-based — renaming or re-uploading the same P&ID
        always maps to the exact same cache slot."""
        import hashlib as _hl
        digest = _hl.sha256(pdf_bytes).hexdigest()
        prefix = getattr(self, 'cache_s3_prefix', 'pid_analysis_cache/')
        return f"{prefix}{digest}_{analysis_mode}.json.gz"

    def _cache_get(self, s3_key: str):
        """Fetch a cached analysis result from S3.
        Returns the parsed dict on cache HIT, or None on miss / error / disabled."""
        if not getattr(self, 'cache_enabled', False):
            return None
        try:
            import boto3 as _boto3, gzip as _gz, json as _json
            from decouple import config as _dcfg
            bucket = _dcfg(self.cache_s3_bucket_env, default='')
            if not bucket:
                print('[CACHE] AWS_STORAGE_BUCKET_NAME not set — cache disabled')
                return None
            s3 = _boto3.client(
                's3',
                aws_access_key_id=_dcfg('AWS_ACCESS_KEY_ID', default=''),
                aws_secret_access_key=_dcfg('AWS_SECRET_ACCESS_KEY', default=''),
                region_name=_dcfg('AWS_S3_REGION_NAME', default='us-east-1'),
            )
            obj = s3.get_object(Bucket=bucket, Key=s3_key)
            compressed = obj['Body'].read()
            result = _json.loads(_gz.decompress(compressed).decode('utf-8'))
            # Re-apply suppression filter in case config changed after this result was cached
            _sup = [s.lower() for s in getattr(self, 'suppressed_categories', [])]
            if _sup and isinstance(result.get('issues'), list):
                before = len(result['issues'])
                result['issues'] = [
                    i for i in result['issues']
                    if not any(tok in (i.get('category') or '').lower() for tok in _sup)
                ]
                # Renumber after filter
                for idx, i in enumerate(result['issues'], 1):
                    i['serial_number'] = idx
                if len(result['issues']) < before:
                    print(f'[CACHE] Suppressed {before - len(result["issues"])} cached finding(s)')
            print(f'[CACHE HIT] {s3_key} — returning stored report ({len(compressed):,} bytes compressed)')
            # Guard: if the cached result has issues but none have an evidence field,
            # this is a stale pre-v2 cache entry — invalidate and force fresh analysis.
            issues = result.get('issues') or []
            if issues and not any(i.get('evidence') for i in issues):
                print('[CACHE] Stale cache entry detected (no evidence fields) — invalidating, running fresh analysis')
                return None
            return result
        except Exception as _e:
            code = getattr(getattr(_e, 'response', None) or {}, 'get', lambda *a: '')('Error', {}).get('Code', '')
            if code not in ('NoSuchKey', '404'):
                print(f'[CACHE] S3 get error ({_e}) — proceeding with fresh analysis')
            return None

    def _cache_put(self, s3_key: str, result: dict) -> None:
        """Store an analysis result in S3 as gzip-compressed JSON (non-fatal).
        Any upload failure is logged and swallowed — the caller always gets its result."""
        if not getattr(self, 'cache_enabled', False):
            return
        try:
            import boto3 as _boto3, gzip as _gz, json as _json, datetime as _dt
            from decouple import config as _dcfg
            bucket = _dcfg(self.cache_s3_bucket_env, default='')
            if not bucket:
                return
            s3 = _boto3.client(
                's3',
                aws_access_key_id=_dcfg('AWS_ACCESS_KEY_ID', default=''),
                aws_secret_access_key=_dcfg('AWS_SECRET_ACCESS_KEY', default=''),
                region_name=_dcfg('AWS_S3_REGION_NAME', default='us-east-1'),
            )
            payload    = _json.dumps(result, default=str).encode('utf-8')
            compressed = _gz.compress(payload, compresslevel=6)
            put_kwargs = dict(
                Bucket=bucket,
                Key=s3_key,
                Body=compressed,
                ContentType='application/gzip',
                ContentEncoding='gzip',
                Metadata={'cached_at': _dt.datetime.utcnow().isoformat()},
            )
            # Optional: set object expiry via S3 lifecycle or explicit Expires header
            ttl = getattr(self, 'cache_ttl_days', 0)
            if ttl > 0:
                import datetime as _dt2
                put_kwargs['Expires'] = _dt2.datetime.utcnow() + _dt2.timedelta(days=ttl)
            s3.put_object(**put_kwargs)
            print(f'[CACHE STORED] {s3_key} ({len(compressed):,} bytes compressed / {len(payload):,} raw)')
        except Exception as _e:
            print(f'[CACHE] S3 put error ({_e}) — result still returned to caller')

    def analyze_pid_drawing(self, pdf_file, drawing_number: Optional[str] = None, reference_documents: Dict[str, Any] = None, analysis_mode: str = 'standard') -> Dict[str, Any]:
        """
        Multi-Pass P&ID Analysis with OCR, Vision, Cross-Validation, and Reference Document Verification
        
        PASS 1: OCR Text Extraction - Extract all text, tags, notes, line numbers
        PASS 2: Reference Document Processing - Extract equipment specs, legends, standards
        PASS 3: Vision Analysis - Comprehensive visual inspection with chain-of-thought
        PASS 4: Cross-Validation - Verify consistency between text, visual, and reference data
        PASS 5: Second Review - Re-analyze to catch missed issues
        
        Args:
            pdf_file: Django FieldFile or file path
            drawing_number: Optional drawing number for reference
            reference_documents: Dict of reference document data extracted from uploaded files
                {
                    'equipment_datasheets': [...],  # Equipment dimensions, ratings, specs
                    'instrument_datasheets': [...], # Instrument specs, ranges, fail-safe positions
                    'legends_symbols': [...],       # Standard symbols and abbreviations
                    'pid_standards': [...],         # P&ID standards and guidelines
                    'process_description': [...],   # Process flow and operating conditions
                    'safety_requirements': [...]    # SIL, HAZOP, PSV requirements
                }
            
        Returns:
            Dictionary with comprehensive analysis results including reference compliance
        """
        try:
            print(f"[INFO] ========== MULTI-PASS ANALYSIS WITH REFERENCE VERIFICATION ==========")
            print(f"[INFO] Drawing: {drawing_number or 'Unknown'}")
            # Store for use across passes (e.g. Pass 7 drawing-number exclusion)
            self._supplied_drawing_number = drawing_number or ''
            print(f"[INFO] Analysis Mode: {analysis_mode.upper()}")
            if reference_documents:
                print(f"[INFO] Reference documents provided: {list(reference_documents.keys())}")

            # ── S3 cache check ───────────────────────────────────────────────────────
            # Compute a content-based key from the raw PDF bytes.  If the SAME file
            # was analysed before, return the stored result immediately (< 1 second)
            # and skip all 8 AI passes entirely.  Cache miss → run full analysis.
            _pdf_raw   = self._read_pdf_bytes(pdf_file)
            _cache_key = self._compute_cache_key(_pdf_raw, analysis_mode)
            _cached    = self._cache_get(_cache_key)
            if _cached is not None:
                _cached.setdefault('analysis_metadata', {})['cache_hit'] = True
                return _cached
            print('[CACHE MISS] No cached result found — running full 8-pass analysis')

            # PASS 1: OCR Text Extraction
            print(f"[INFO] PASS 1: OCR Text Extraction")
            images_base64 = self._pdf_to_base64_images(pdf_file)
            self._extract_text_from_pdf(pdf_file)
            self._parse_extracted_data()
            
            print(f"[INFO] Extracted {len(self.instrument_tags)} instrument tags")
            print(f"[INFO] Extracted {len(self.equipment_tags)} equipment tags")
            print(f"[INFO] Extracted {len(self.line_numbers)} line numbers")
            print(f"[INFO] Extracted {len(self.notes_references)} note references")
            
            # PASS 2: Reference Document Processing (SOFT-CODED: AI-Powered Intelligence)
            reference_data = {}
            if reference_documents:
                print(f"[INFO] PASS 2: Reference Document Intelligence Extraction")
                # SOFT-CODED: Separate JSON-typed references (e.g. DesignIQ line list) from file paths
                json_references = {}
                file_references = {}
                for k, v in reference_documents.items():
                    if isinstance(v, (dict, list)):
                        json_references[k] = v
                    else:
                        file_references[k] = v
                try:
                    if file_references:
                        reference_data = self._process_reference_documents(file_references)
                    print(f"[INFO] Reference data extracted: {len(reference_data)} categories")
                except Exception as e:
                    print(f"[WARNING] Reference document processing failed (non-critical): {e}")
                    reference_data = {}
                # Merge JSON references directly (already structured — no file processing needed)
                reference_data.update(json_references)
                if json_references:
                    print(f"[INFO] Merged {len(json_references)} JSON reference(s) from DesignIQ: {list(json_references.keys())}")
            else:
                print(f"[INFO] PASS 2: Skipped (No reference documents provided)")

            # PRE-PASS (Layout Detection): lightweight gpt-4o-mini call that identifies the
            # drawing's zone grid, company standard, and section positions.  The resulting
            # layout_context_str is injected into Passes 3, 5, and 6 so the AI adapts to
            # the ACTUAL layout of this specific P&ID rather than assuming a fixed 9-zone grid.
            print(f"[INFO] PRE-PASS: Layout Detection (adaptive zone grid for this drawing)")
            layout_info = {'layout_context_str': '', 'zones': self.layout_default_zones, 'detected': False}
            if self.layout_detection_enabled:
                try:
                    layout_info = self._detect_pid_layout(images_base64)
                except Exception as _le:
                    print(f"[WARNING] Layout detection failed (non-critical): {_le}")
            else:
                print(f"[INFO] Layout detection disabled in config — using default 9-zone grid")

            # PASS 3: Vision Analysis with Chain-of-Thought & Reference Cross-Verification
            print(f"[INFO] PASS 3: Vision Analysis (Chain-of-Thought + Reference Verification)")
            try:
                vision_result = self._vision_analysis_with_references(
                    images_base64, reference_data, layout_info.get('layout_context_str', '')
                )
            except Exception as e:
                print(f"[ERROR] PASS 3 failed: {str(e)}")
                vision_result = {'issues': [], 'total_issues': 0, 'confidence': 'Low'}
            
            # PASS 4: Cross-Validation
            print(f"[INFO] PASS 4: Cross-Validation & Consistency Checks")
            try:
                consistency_issues = self._cross_validation_pass(vision_result)
            except Exception as e:
                print(f"[ERROR] PASS 3 failed: {str(e)}")
                consistency_issues = []
            
            # PASS 5: Second Review Pass — ALWAYS run to catch missed elements.
            # Runs unconditionally so every drawing gets two independent AI scans merged.
            # The second pass is given the first-pass findings and told not to duplicate them.
            second_pass_issues = []
            issues_found = vision_result.get('total_issues', 0)
            print(f"[INFO] PASS 5: Second Review Pass ({issues_found} issues from Pass 3 — scanning for missed elements)")
            try:
                second_pass_issues = self._second_review_pass(
                    images_base64, vision_result, consistency_issues,
                    layout_info.get('layout_context_str', '')
                )
            except Exception as e:
                print(f"[WARNING] PASS 5 failed (non-critical): {str(e)}")
                second_pass_issues = []

            # PASS 6: Engineering Compliance Deep-Scan — dedicated AI call for standards compliance.
            # Focuses ONLY on advanced engineering domains (valve standards, NACE, tie-ins, PSV,
            # spec breaks, free-drain, LTCS, corrosion allowance) that Pass 3 often glosses over.
            # Disabled by default via config (pass_6.enabled=false) — this pass applies external
            # industry standards which are NOT project-specific and produce false positives.
            engineering_issues = []
            if getattr(self, 'pass6_enabled', False):
                print(f"[INFO] PASS 6: Engineering Compliance Deep-Scan (API/ASME/NACE/Tie-in specialist)")
                try:
                    engineering_issues = self._engineering_compliance_pass(
                        images_base64, vision_result, second_pass_issues
                    )
                except Exception as e:
                    print(f"[WARNING] PASS 6 failed (non-critical): {str(e)}")
                    engineering_issues = []
            else:
                print(f"[INFO] PASS 6: Skipped (pass_6.enabled=false in config — consistency-only mode)")

            # PASS 7: Line Size Validation & AI Recommendation Engine
            # Detects line size anomalies (nozzle mismatches, unjustified size jumps,
            # velocity-based outliers) and produces structured recommendations.
            print(f"[INFO] PASS 7: Line Size Validation & AI Recommendations")
            line_size_result = {"issues": [], "line_size_recommendations": []}
            try:
                # Pass the confirmed drawing number so Pass 7 never mistakes it for a line size
                _confirmed_drw_num = (
                    layout_info.get('layout', {}).get('drawing_number', '')
                    or getattr(self, '_supplied_drawing_number', '')
                    or next(iter(getattr(self, 'drawing_number_candidates', set())), '')
                )
                line_size_result = self._line_size_validation_pass(
                    images_base64, reference_data, confirmed_drawing_number=_confirmed_drw_num
                )
            except Exception as e:
                print(f"[WARNING] PASS 7 failed (non-critical): {str(e)}")

            # PASS 8: Smart QC Enhancement — four targeted specialist checks:
            #   1. Duplicate / near-duplicate line number detection (programmatic + AI confirm)
            #   2. Dynamic valve size consistency (any project standard, fully AI-driven)
            #   3. Deep NOTES & HOLDS cross-verification (full-stack process engineer view)
            #   4. Equipment TYPE designation validation (e.g. TYPE 01A vs TYPE 09A)
            # Purely additive — does NOT modify any previous pass logic.
            print(f"[INFO] PASS 8: Smart QC Enhancement (Dup Lines + Valve Size + Notes/Holds + TYPE Designations)")
            smart_qc_result = {"issues": [], "total_issues": 0}
            try:
                _merged_for_pass8 = (
                    vision_result.get('issues', []) +
                    consistency_issues +
                    second_pass_issues +
                    engineering_issues +
                    line_size_result.get('issues', [])
                )
                smart_qc_result = self._smart_qc_enhancement_pass(
                    images_base64, reference_data, _merged_for_pass8
                )
            except Exception as e:
                print(f"[WARNING] PASS 8 failed (non-critical): {str(e)}")

            # Merge all findings from all passes (including line size and smart QC issues)
            all_issues = self._merge_and_deduplicate(
                vision_result.get('issues', []),
                consistency_issues,
                second_pass_issues,
                engineering_issues + line_size_result.get('issues', []) + smart_qc_result.get('issues', [])
            )

            # CAG post-filter: remove or demote issues that reference elements not confirmed
            # by OCR and not grounded in visual evidence (anti-hallucination pass).
            try:
                before_cag = len(all_issues)
                all_issues = self._cag_post_filter(all_issues)
                if len(all_issues) < before_cag:
                    print(f"[CAG] Post-filter: removed/demoted {before_cag - len(all_issues)} hallucinated finding(s)")
            except Exception as _cag_ex:
                print(f"[WARNING] CAG post-filter failed (non-critical): {_cag_ex}")

            # If NO issues found at all, log a warning but do NOT fabricate a placeholder finding.
            # A clean drawing with genuinely zero findings should return an empty list.
            if len(all_issues) == 0:
                print("[WARNING] No issues found in any pass — returning empty findings list (no placeholder injected)")

            # Supplement with individual note/hold compliance items when below threshold
            # Each extracted note/hold reference is a legitimate QC check item
            _supp_limit = getattr(self, 'supplement_notes_below', 10)
            if len(all_issues) < _supp_limit and self.notes_references:
                already_refs = {iss.get('pid_reference', '').upper() for iss in all_issues}
                for note_ref in sorted(self.notes_references):
                    if len(all_issues) >= _supp_limit:
                        break
                    nr_upper = note_ref.upper().replace(' ', '-')
                    if not any(nr_upper in ref or note_ref.upper() in ref for ref in already_refs):
                        is_hold = 'HOLD' in note_ref.upper()
                        all_issues.append({
                            'pid_reference': note_ref.upper().replace(' ', '-'),
                            'issue_observed': f"{'Open hold' if is_hold else 'Note'} reference found on drawing — individual compliance not verified by AI scan.",
                            'action_required': f"{'Verify this HOLD is resolved or obtain written approval for each outstanding requirement.' if is_hold else 'Verify that the requirement stated in this NOTE is implemented on the drawing.'}",
                            'evidence': f"Programmatically detected: {'HOLD' if is_hold else 'NOTE'} reference '{note_ref}' was extracted from the drawing text. Individual compliance against drawing content was not verified by AI visual scan.",
                            'severity': 'major' if is_hold else 'minor',
                            'category': 'holds_compliance' if is_hold else 'notes_compliance',
                            'location_on_drawing': {
                                'zone': 'Bottom-Right',
                                'drawing_section': 'Notes',
                                'proximity_description': 'Notes/holds section of drawing',
                                'visual_cues': f'See {note_ref} in drawing notes'
                            }
                        })
                        already_refs.add(nr_upper)
                if len(all_issues) > 0:
                    print(f"[INFO] Supplemented with note/hold compliance items. Total issues: {len(all_issues)}")

            # Re-number serial numbers sequentially after any additions
            for idx, iss in enumerate(all_issues, start=1):
                iss['serial_number'] = idx

            
            # Categorize by severity
            categorized = self._categorize_by_severity(all_issues)

            final_confidence = 'High' if len(all_issues) >= getattr(self, 'confidence_high_thresh', 10) else 'Medium'

            final_result = {
                'issues': all_issues,
                'critical_issues': categorized['critical'],
                'major_observations': categorized['major'],
                'minor_observations': categorized['minor'],
                'total_issues': len(all_issues),
                'critical_count': len(categorized['critical']),
                'major_count': len(categorized['major']),
                'minor_count': len(categorized['minor']),
                'confidence': final_confidence,
                # SOFT-CODED: Structured sections passed through from AI response (present for new analyses)
                'specification_breaks': vision_result.get('specification_breaks', []),
                'pfd_guidelines_compliance': vision_result.get('pfd_guidelines_compliance', {}),
                # PASS 7: Line size recommendations (structured AI output)
                'line_size_recommendations': line_size_result.get('line_size_recommendations', []),
                'analysis_metadata': {
                    # AI model info (used by frontend AI Insights panel)
                    'ai_model': 'gpt-4o',
                    'confidence_score': final_confidence,
                    'analysis_type': 'comprehensive',
                    'analysis_duration': 'Multi-pass (8 passes)',
                    'rag_context_used': bool(reference_documents),
                    'rag_context_length': sum(len(str(v)) for v in reference_data.values()) if reference_data else 0,
                    # OCR extraction statistics
                    'extracted_text_length': len(self.extracted_text),
                    'instrument_tags_found': len(self.instrument_tags),
                    'equipment_tags_found': len(self.equipment_tags),
                    'line_numbers_found': len(self.line_numbers),
                    'line_size_anomalies_found': len(line_size_result.get('line_size_recommendations', [])),
                    # Pass 8: Smart QC Enhancement statistics
                    'smart_qc_issues_found': len(smart_qc_result.get('issues', [])),
                    'line_duplicates_found': len([i for i in smart_qc_result.get('issues', []) if i.get('category') == 'line_duplicate']),
                    'valve_size_issues_found': len([i for i in smart_qc_result.get('issues', []) if i.get('category') == 'valve_size']),
                    'type_designation_issues_found': len([i for i in smart_qc_result.get('issues', []) if i.get('category') == 'type_designation']),
                    # New Pass 8 extended checks (E-I)
                    'revision_changes_found': len([i for i in smart_qc_result.get('issues', []) if i.get('category') == 'revision_change']),
                    'line_number_anomalies_found': len([i for i in smart_qc_result.get('issues', []) if i.get('category') in ('line_number_anomaly', 'spec_break')]),
                    'missing_fittings_found': len([i for i in smart_qc_result.get('issues', []) if i.get('category') == 'missing_fitting']),
                    'instrument_downgrades_found': len([i for i in smart_qc_result.get('issues', []) if i.get('category') == 'instrument_downgrade']),
                    'line_continuity_issues_found': len([i for i in smart_qc_result.get('issues', []) if i.get('category') == 'line_continuity']),
                    'analysis_passes': 8,
                    'multi_pass_enabled': True,
                    'analysis_mode': analysis_mode,  # 'standard' or 'premium'
                    'reference_documents_used': bool(reference_documents),
                    'reference_categories': list(reference_data.keys()) if reference_data else []
                }
            }
            
            print(f"[INFO] ========== ANALYSIS COMPLETE ==========")
            print(f"[INFO] Total Issues: {len(all_issues)}")
            print(f"[INFO] Critical: {len(categorized['critical'])}, Major: {len(categorized['major'])}, Minor: {len(categorized['minor'])}")
            print(f"[INFO] Line Size Anomalies: {len(line_size_result.get('line_size_recommendations', []))}")
            print(f"[INFO] Smart QC Enhancement (Pass 8): {len(smart_qc_result.get('issues', []))} issues")

            # ── Store result in S3 cache ─────────────────────────────────────────────
            self._cache_put(_cache_key, final_result)

            return final_result
            
        except Exception as e:
            print(f"[ERROR] Analysis failed: {str(e)}")
            import traceback
            traceback.print_exc()
            raise

    def _extract_text_from_pdf(self, pdf_file):
        """Extract all text from PDF using OCR"""
        try:
            # Soft-coded approach: Handle both file paths and file objects (S3/Django FileField)
            if isinstance(pdf_file, str):
                # Local file path
                doc = fitz.open(pdf_file)
            else:
                # File object (from S3 or Django FileField) - read content into memory
                pdf_file.seek(0)  # Ensure we're at the start
                pdf_bytes = pdf_file.read()
                doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            
            text_parts = []
            
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                text = page.get_text()
                text_parts.append(text)
            
            doc.close()
            self.extracted_text = "\n".join(text_parts)

            # Tesseract fallback: if embedded text is very sparse (likely a scanned drawing),
            # attempt pytesseract OCR so that downstream tag extraction is more complete.
            # SOFT-CODED: controlled by ocr_tesseract_fallback_enabled in pid_analysis_config.json
            _tess_enabled = getattr(self, 'ocr_tesseract_fallback', True)
            if _tess_enabled and len(self.extracted_text.strip()) < 200:
                print(f'[INFO] Embedded text sparse ({len(self.extracted_text.strip())} chars) — attempting Tesseract OCR fallback')
                try:
                    import pytesseract
                    from PIL import Image as _PILImage
                    _tess_texts = []
                    if isinstance(pdf_file, str):
                        _doc2 = fitz.open(pdf_file)
                    else:
                        pdf_file.seek(0)
                        _doc2 = fitz.open(stream=pdf_file.read(), filetype="pdf")
                    for _pnum in range(len(_doc2)):
                        _pg = _doc2.load_page(_pnum)
                        _mat = fitz.Matrix(2.0, 2.0)  # 144 DPI — fast yet readable for Tesseract
                        _pix = _pg.get_pixmap(matrix=_mat)
                        _img = _PILImage.frombytes("RGB", [_pix.width, _pix.height], _pix.samples)
                        _tess_texts.append(pytesseract.image_to_string(_img, config='--psm 11 -l eng'))
                    _doc2.close()
                    _tess_combined = "\n".join(_tess_texts)
                    if len(_tess_combined.strip()) > len(self.extracted_text.strip()):
                        self.extracted_text = _tess_combined
                        print(f'[INFO] Tesseract OCR extracted {len(_tess_combined)} chars')
                except Exception as _tess_ex:
                    print(f'[WARNING] Tesseract OCR fallback failed: {_tess_ex}')

        except Exception as e:
            print(f"[WARNING] OCR extraction failed: {str(e)}")
            self.extracted_text = ""
    
    def _parse_extracted_data(self):
        """
        Parse extracted text to identify tags, line numbers, notes with STRICT filtering
        
        🔒 DETERMINISTIC CLASSIFICATION:
        - Uses element_classifier.py to prevent misclassifications
        - Line numbers (2"-D-6155-...) will NEVER be classified as equipment
        - DELETED notes are completely filtered out
        - Drawing numbers are never confused with line numbers
        - Context isolation enforced (session-specific)
        """
        if not self.extracted_text:
            return
            
        print(f"[PARSE] Session {self.session_id[:8]}: Starting deterministic classification")
        
        # Valid instrument prefixes (ISA-5.1 standard)
        valid_instrument_prefixes = {
            'FI', 'FIC', 'FIT', 'FE', 'FT', 'FV', 'FY', 'FCV', 'FICV',
            'LI', 'LIC', 'LIT', 'LE', 'LT', 'LV', 'LY', 'LCV', 'LSH', 'LSL', 'LSHH', 'LSLL', 'LAH', 'LAL',
            'PI', 'PIC', 'PIT', 'PE', 'PT', 'PV', 'PY', 'PCV', 'PSH', 'PSL', 'PSHH', 'PSLL', 'PAH', 'PAL', 'PDI', 'PDIC', 'PDIT',
            'TI', 'TIC', 'TIT', 'TE', 'TT', 'TV', 'TY', 'TCV', 'TSH', 'TSL', 'TSHH', 'TSLL', 'TAH', 'TAL',
            'AI', 'AIC', 'AIT', 'AE', 'AT', 'AV', 'AY', 'ACV', 'ASH', 'ASL',
            'PSV', 'PRV', 'PDSV', 'TSV', 'ESV', 'XV', 'SDV', 'BDV', 'MOV', 'SOV',
            'ZS', 'ZSH', 'ZSL', 'ZI', 'ZIC', 'ZIT',
            'HS', 'HV', 'HY', 'HL', 'HIC',
            'SI', 'SC', 'SE', 'SS', 'SV',
            'WI', 'WIC', 'WIT', 'WE', 'WT',
            'VI', 'VIC', 'VIT', 'VT',
            'EI', 'EIC', 'EY',
            'UI', 'UIC', 'UY'
        }
        
        # Line number prefixes to exclude from instrument tags (common piping service codes)
        line_number_prefixes = {
            'HD', 'HU', 'CD', 'CU', 'LF', 'FL', 'SY', 'NG', 'FG', 'FW', 'BW', 
            'CW', 'SW', 'DW', 'PW', 'HP', 'MP', 'LP', 'IA', 'NA', 'PA',
            'HC', 'HO', 'CO', 'ST', 'CS', 'DS', 'SS', 'RW', 'WW', 'GN',
            'N2', 'O2', 'CO2', 'H2', 'AR', 'HE'
        }
        
        # Extract potential instrument tags
        instrument_pattern = r'\b([A-Z]{2,4}[ICSVT]?[-_][\d]{1,4}(?:[-_][\d]{1,2}[A-Z]?)?)\b'
        potential_instruments = set(re.findall(instrument_pattern, self.extracted_text))
        
        # Filter instrument tags: keep only valid ISA prefixes and exclude line number prefixes
        self.instrument_tags = set()
        for tag in potential_instruments:
            prefix = tag.split('-')[0].upper()
            
            # ═══ USE CLASSIFIER to prevent misclassification ═══
            classified = self.classifier.classify_element(tag, self.extracted_text)
            
            if classified.element_type == ElementType.INSTRUMENT_TAG:
                self.instrument_tags.add(tag.upper())
            elif classified.element_type == ElementType.LINE_NUMBER:
                # This is part of a line number, not an instrument
                continue
            else:
                # Additional validation for non-classified
                is_valid_instrument = any(
                    prefix == valid_prefix or prefix.startswith(valid_prefix) 
                    for valid_prefix in valid_instrument_prefixes
                )
                is_line_number = prefix in line_number_prefixes
                
                if is_valid_instrument and not is_line_number:
                    self.instrument_tags.add(tag.upper())
        
        print(f"[PARSE] Session {self.session_id[:8]}: Found {len(self.instrument_tags)} instrument tags")
        
        # ═══════════════════════════════════════════════════════════════════
        # LINE NUMBERS (using deterministic classifier)
        # ═══════════════════════════════════════════════════════════════════
        # Use classifier to extract ONLY line numbers (never misclassify equipment)
        self.line_numbers = self.classifier.extract_line_numbers_only(self.extracted_text)
        
        # Filter out P&ID connector numbers: NN-PP-NNN-NNNNN format
        pid_connector_pattern = re.compile(r'^\d+[-]PP[-]\d+[-]\d+', re.IGNORECASE)
        self.line_numbers = {ln for ln in self.line_numbers if not pid_connector_pattern.match(ln)}
        
        print(f"[PARSE] Session {self.session_id[:8]}: Found {len(self.line_numbers)} line numbers")
        
        # ═══════════════════════════════════════════════════════════════════
        # EQUIPMENT TAGS (using deterministic classifier)
        # ═══════════════════════════════════════════════════════════════════
        # Use classifier to extract ONLY equipment (never include line fragments)
        self.equipment_tags = self.classifier.extract_equipment_tags_only(self.extracted_text)
        
        print(f"[PARSE] Session {self.session_id[:8]}: Found {len(self.equipment_tags)} equipment tags")

        # ── Drawing-number extraction (soft-coded) ────────────────────────────
        # Drawing numbers use DOT separators: NN.NN.NN.NNNN or NN.NN.NNNN etc.
        # Common in ADNOC/Gulf-region P&IDs (e.g. 16.01.08.1678).
        # Stored in self.drawing_number_candidates to be explicitly excluded from
        # line-size validation and instrument tag checks.
        drw_num_pattern = re.compile(
            r'\b(\d{1,4}\.\d{1,4}\.\d{1,4}\.\d{2,6})\b'
        )
        self.drawing_number_candidates = set(re.findall(drw_num_pattern, self.extracted_text))
        
        print(f"[PARSE] Session {self.session_id[:8]}: Found {len(self.drawing_number_candidates)} drawing numbers")
        
        # ═══════════════════════════════════════════════════════════════════
        # NOTE REFERENCES (with DELETED filtering)
        # ═══════════════════════════════════════════════════════════════════
        # Extract all note/hold references
        note_pattern = r'\b((?:NOTE|HOLD|REF)[\s]*[\d]+(?:[:\-].*?(?:\n|$))?)\b'
        all_notes = list(set(re.findall(note_pattern, self.extracted_text, re.IGNORECASE | re.MULTILINE)))
        
        # ═══ CRITICAL: Filter out DELETED notes ═══
        active_notes = self.classifier.filter_deleted_notes(all_notes)
        self.notes_references = set(active_notes)
        
        deleted_count = len(all_notes) - len(active_notes)
        if deleted_count > 0:
            print(f"[PARSE] Session {self.session_id[:8]}: Filtered out {deleted_count} DELETED notes")
        
        print(f"[PARSE] Session {self.session_id[:8]}: Found {len(self.notes_references)} active notes/holds")
        
        # ═══════════════════════════════════════════════════════════════════
        # VALIDATION: Ensure no cross-contamination
        # ═══════════════════════════════════════════════════════════════════
        # Verify line numbers don't overlap with equipment tags
        overlap = self.line_numbers.intersection(self.equipment_tags)
        if overlap:
            print(f"[WARNING] Session {self.session_id[:8]}: Found {len(overlap)} overlapping tags - reclassifying")
            for item in overlap:
                # Line numbers take priority (they contain size prefix)
                self.equipment_tags.discard(item)
        
        print(f"[PARSE] Session {self.session_id[:8]}: Classification complete - context isolated")

    def _build_cag_context(self) -> str:
        """
        Context Augmented Generation (CAG) block.

        Builds a hard-boundary inventory injected into every AI pass so the model
        can ONLY generate findings for elements that are either:
          (a) present in the OCR-confirmed inventory below, OR
          (b) directly and unambiguously visible on the attached drawing image.

        This prevents the AI from:
          • Hallucinating line numbers from memory or training data
          • Fabricating notes/holds compliance issues when no notes exist
          • Referencing elements from other drawings or design packages

        SOFT-CODED: adjust suppression thresholds in pid_analysis_config.json.
        """
        # Load strict validation rules
        rules_path = os.path.join(os.path.dirname(__file__), 'config', 'strict_validation_rules.txt')
        strict_rules = ""
        try:
            with open(rules_path, 'r', encoding='utf-8') as f:
                strict_rules = f.read().strip()
        except Exception as e:
            print(f"[WARNING] Could not load strict validation rules: {e}")
        
        sep = '═' * 68
        lines = [
            sep,
            f'CAG CONTEXT — SESSION {self.session_id[:8]} — CONTEXT ISOLATION ENFORCED',
            sep,
            '',
            '🔒 CONTEXT ISOLATION ACTIVE:',
            f'- Session ID: {self.session_id}',
            '- Analyze ONLY the attached image(s) from THIS upload',
            '- DO NOT reference previous analyses, other users\' documents, or training data',
            '- Each finding MUST be grounded in THIS drawing',
            '',
            '── OCR-CONFIRMED LINE NUMBERS ON THIS DRAWING ──',
        ]

        if self.line_numbers:
            for ln in sorted(self.line_numbers)[:60]:
                lines.append(f'  {ln}')
            lines.append(
                f'  (Total: {len(self.line_numbers)} line numbers extracted by OCR)'
            )
            lines.append('')
            lines.append(
                '⚠️ CRITICAL: These line numbers (e.g., 2"-D-6155-033842) are COMPLETE IDENTIFIERS.'
            )
            lines.append(
                '   DO NOT extract fragments (e.g., D-6155) and treat as separate equipment tags.'
            )
            lines.append(
                '   ANY reference starting with SIZE + QUOTE + DASH is a LINE NUMBER, NOT EQUIPMENT.'
            )
            lines.append('')
            lines.append(
                'RULE ➜ Every "pid_reference" for a PIPING finding MUST match one of the '
                'line numbers above OR be a line number you can READ directly on the image.'
            )
            lines.append(
                'Do NOT invent or paraphrase line numbers — use the exact tag as written on the drawing.'
            )
        else:
            lines.append(
                '  (No line numbers extracted by OCR — rely entirely on visual scan of image.)'
            )

        lines += [
            '',
            '── OCR-CONFIRMED INSTRUMENT TAGS ON THIS DRAWING ──',
        ]
        if self.instrument_tags:
            for tag in sorted(self.instrument_tags)[:60]:
                lines.append(f'  {tag}')
            lines.append(f'  (Total: {len(self.instrument_tags)} tags extracted by OCR)')
            lines.append('')
            lines.append(
                'RULE ➜ Every "pid_reference" for an INSTRUMENT finding MUST match one of the '
                'tags above OR be a tag you can READ on the image.'
            )
        else:
            lines.append('  (No instrument tags extracted by OCR — rely on visual scan.)')

        lines += [
            '',
            '── OCR-CONFIRMED EQUIPMENT TAGS ON THIS DRAWING ──',
        ]
        if self.equipment_tags:
            for tag in sorted(self.equipment_tags)[:30]:
                lines.append(f'  {tag}')
        else:
            lines.append('  (No equipment tags extracted by OCR — rely on visual scan.)')

        lines += ['', '── NOTES & HOLDS INVENTORY (ACTIVE ONLY) ──']
        if self.notes_references:
            lines.append(
                f'OCR detected {len(self.notes_references)} ACTIVE note/hold reference(s) on this drawing:'
            )
            lines.append('  (DELETED notes have been automatically removed from this list)')
            for n in sorted(self.notes_references):
                lines.append(f'  {n}')
            lines.append('')
            lines.append(
                '🚫 DELETED NOTE RULE: If a note contains "DELETED" keyword, it has been removed from above list.'
            )
            lines.append(
                '   DO NOT generate findings for deleted notes. Only check compliance for ACTIVE notes shown above.'
            )
            lines.append('')
            lines.append(
                'RULE ➜ Only generate notes_compliance / holds_compliance findings for the '
                'note/hold numbers listed above. Read their EXACT text from the drawing image.'
            )
        else:
            lines += [
                '  OCR detected ZERO note or hold references on this drawing.',
                '',
                '⚠ CRITICAL RULE: Because NO notes or holds were found by OCR, you MUST NOT',
                '  generate any "notes_compliance" or "holds_compliance" issue.',
                '  Do NOT invent a Notes section that does not exist on this drawing.',
                '  If you cannot visually confirm a NOTES section in the image, skip those checks.',
            ]

        lines += [
            '',
            '── DRAWING NUMBER (DO NOT USE AS A LINE NUMBER OR TAG) ──',
        ]
        if self.drawing_number_candidates:
            for dn in sorted(self.drawing_number_candidates):
                lines.append(f'  {dn}  ← drawing/document number, NOT a pipe specification')
        else:
            lines.append('  (none detected)')

        lines += [
            '',
            sep,
            'CAG SUMMARY RULES (apply to EVERY finding in EVERY pass):',
            sep,
            '  1. pid_reference  → must be in the inventory above OR visually readable on drawing.',
            '  2. issue_observed → must reference only elements confirmed in the inventory or image.',
            '  3. notes/holds    → skip entirely if ZERO notes/holds detected (see above). Ignore DELETED notes.',
            '  4. Never fabricate tags, line numbers, or document references from memory.',
            '  5. If uncertain whether an element exists — it is NOT a finding.',
            '  6. Line number fragments (e.g., D-6155 from 2"-D-6155-033842) are NOT separate equipment.',
            '  7. Arrows indicating direction/destination are NOT missing pipelines.',
            '  8. VISUAL + OCR confirmation required — mark low confidence if only one method.',
            sep,
        ]
        
        # Append strict validation rules if loaded
        if strict_rules:
            lines.append('')
            lines.append(strict_rules)
        
        return '\n'.join(lines)

    def _cag_post_filter(self, issues: List[Dict]) -> List[Dict]:
        """
        Post-processing CAG filter: removes findings whose pid_reference cannot be
        matched to any OCR-confirmed element AND whose issue_observed contains only
        references to elements not found in the OCR inventory.

        Conservative approach — only removes issues that are clearly hallucinated
        (ref and issue_observed both reference non-existent elements AND there is
        zero visual evidence sentence in the evidence field).

        SOFT-CODED: controlled via pid_analysis_config.json → cag_post_filter_enabled.
        """
        import os as _os, json as _js
        try:
            cfg_path = _os.path.join(_os.path.dirname(__file__), 'config', 'pid_analysis_config.json')
            with open(cfg_path, 'r', encoding='utf-8') as _f:
                _cfg = _js.load(_f)
            enabled = bool(_cfg.get('pid_analysis', {}).get('cag_post_filter_enabled', True))
        except Exception:
            enabled = True

        if not enabled:
            return issues

        # Build OCR inventory as a set of uppercase tokens for fast lookup
        ocr_inventory: Set[str] = set()
        for tag in (self.instrument_tags or []):
            ocr_inventory.add(tag.upper().strip())
        for ln in (self.line_numbers or []):
            ocr_inventory.add(ln.upper().strip())
        for eq in (self.equipment_tags or []):
            ocr_inventory.add(eq.upper().strip())
        # Also add individual fragments (e.g. "4\"-BD-4860" matches "4\"-BD-4860-033842-X-N")
        fragments: Set[str] = set()
        for item in ocr_inventory:
            parts = item.split('-')
            if len(parts) >= 2:
                fragments.add('-'.join(parts[:2]).upper())
                fragments.add('-'.join(parts[:3]).upper())
        ocr_inventory.update(fragments)

        # Note/hold keywords — if OCR found none, these categories are hallucinated
        no_notes = not self.notes_references
        hallucinated_cats = {'notes_compliance', 'holds_compliance'} if no_notes else set()

        kept, removed = [], []
        for issue in issues:
            cat = (issue.get('category') or '').lower().strip()
            pid_ref = (issue.get('pid_reference') or '').upper().strip()
            evidence = (issue.get('evidence') or '').lower()

            # Rule 1: notes/holds when OCR found none → remove
            if cat in hallucinated_cats:
                removed.append(pid_ref)
                continue

            # Rule 2: if the pid_reference matches anything in OCR or contains a
            # drawing-number-like DOT-separated pattern → keep (drawing numbers are valid refs)
            import re as _re_cag
            if _re_cag.search(r'\d+\.\d+\.\d+\.\d+', pid_ref):
                # Drawing number reference — let other filters handle it
                kept.append(issue)
                continue

            # Rule 3: check if ANY part of pid_reference matches OCR inventory
            ref_matched = any(
                tok in ocr_inventory or ocr_inventory_contains(ocr_inventory, tok)
                for tok in pid_ref.replace('/', ' ').replace(',', ' ').split()
                if len(tok) > 2
            )
            if ref_matched:
                kept.append(issue)
                continue

            # Rule 4: if the evidence has "visually confirmed" / "visible" phrasing, keep
            visual_phrases = ('visually confirm', 'visible on', 'can see', 'visible in',
                              'drawing shows', 'shown on', 'present on')
            if any(ph in evidence for ph in visual_phrases):
                kept.append(issue)
                continue

            # Rule 5: observation-severity issues with unmatched refs are kept as-is
            # (they may be valid visual-only items with no OCR counterpart)
            if (issue.get('severity') or '').lower() == 'observation':
                kept.append(issue)
                continue

            # Default: if ref not in inventory AND no visual evidence phrase → flag as suspect
            # We demote to 'observation' rather than silently dropping, so engineers can still see it
            issue = dict(issue)
            issue['severity'] = 'observation'
            issue['evidence'] = (
                (issue.get('evidence') or '') +
                ' [CAG: pid_reference could not be confirmed in OCR inventory — finding demoted to observation]'
            ).strip()
            kept.append(issue)

        if removed:
            print(f'[CAG] Post-filter removed {len(removed)} hallucinated note/hold issues: {removed[:5]}')
        return kept

    def _build_per_instrument_instructions(self) -> str:
        """
        Build an explicit per-loop, per-tag checkbox checklist.
        Groups OCR instruments by ISA-5.1 loop number, augments with related
        tags from OCR line_numbers, then generates mandatory per-tag checks.
        Rule: Each unchecked box = one JSON finding.
        """
        import re

        # ISA-5.1 function-code sets (prefix-based match)
        CTRL      = ('HIC', 'FIC', 'LIC', 'TIC', 'PIC', 'ZIC', 'AIC', 'WIC', 'HC', 'FC', 'LC', 'TC', 'PC')
        VALVE     = ('XV', 'SDV', 'BDV', 'FCV', 'HV', 'MOV', 'SOV', 'LCV', 'TCV', 'PCV', 'EV', 'PV', 'CV')
        XMIT      = ('FT', 'PT', 'TT', 'LT', 'AT', 'FE', 'TE', 'PE', 'LE', 'FIT', 'PIT', 'TIT', 'LIT', 'AIT')
        SOLENOID  = ('XY', 'HY', 'TY', 'PY', 'FY', 'LY')
        # SIS-level trips (CRITICAL — these must have SIS/interlock connection)
        SIS_SW    = ('TSHH', 'TSLL', 'PSHH', 'PSLL', 'LSHH', 'LSLL', 'FSHH', 'FSLL')
        # Process-level switches (MINOR — discrete alarm contact, no SIS required)
        PROC_SW   = ('TSH', 'TSL', 'PSH', 'PSL', 'LSH', 'LSL', 'FSH', 'FSL',
                     'ZSH', 'ZSL', 'XZSH', 'XZSL', 'XZLH', 'XZLL', 'XZPH', 'XZPL')
        SWITCH    = SIS_SW + PROC_SW
        INDIC     = ('FI', 'PI', 'TI', 'LI', 'AI', 'PG', 'LG', 'PDI', 'VI', 'TG', 'WI')
        SAFETY_V  = ('PSV', 'PRV', 'PDSV', 'TSV')
        ALL_INST  = CTRL + VALVE + XMIT + SOLENOID + SWITCH + INDIC + SAFETY_V

        def func_code(tag: str) -> str:
            for part in tag.split('-'):
                if not part.isdigit():
                    return part.upper()
            return tag.split('-')[0].upper()

        def loop_num(tag: str) -> str:
            nums = re.findall(r'\d+', tag)
            return nums[-1] if nums else '0'

        # --- Build loops dict from OCR instrument tags ---
        loops: dict = {}
        for tag in sorted(self.instrument_tags or []):
            ln = loop_num(tag)
            loops.setdefault(ln, set()).add(tag)

        # --- Augment loops with instrument-type tags from OCR line_numbers ---
        # OCR line_numbers often contain area-prefixed instrument references like 13-XY-4513
        # Explicitly exclude alarm-element prefixes (TA, TDA, TSA etc.) — they are instruments but
        # not categories that need loop-based QC checks here
        _NOT_LOOP_INST = {'TA', 'TDA', 'TSA', 'FAL', 'LAL', 'PAL', 'LSA', 'FSA', 'ASA', 'KX'}
        for raw in (self.line_numbers or []):
            parts = raw.split('-')
            if len(parts) == 3 and parts[0].isdigit() and not parts[1].isdigit():
                fc = parts[1].upper()
                ln = parts[2]
                short = f"{fc}-{ln}"
                # Add only genuine instrument function codes (not equipment, not alarm elements)
                if fc not in _NOT_LOOP_INST and any(fc.startswith(p) for p in ALL_INST):
                    loops.setdefault(ln, set()).add(short)

        if not loops:
            return ("No OCR-confirmed instrument tags — visually scan the entire drawing "
                    "for any instruments and report missing documentation as findings.")

        lines = [
            "=== MANDATORY INSTRUMENT LOOP VERIFICATION ===",
            "QC RULE: For every □ item below, look at the drawing image.",
            "If you CANNOT VISUALLY CONFIRM the element IS present on the drawing → it IS a finding.",
            "Add one JSON issue per unchecked □ item. Do NOT group multiple checkboxes into one finding.",
            "Expected: 15-35 findings for a drawing with 10+ instruments at IFC stage.",
            "",
        ]

        for lnum in sorted(loops.keys()):
            tags = sorted(loops[lnum])
            ctrl_t   = [t for t in tags if any(func_code(t).startswith(p) for p in CTRL)]
            valve_t  = [t for t in tags if any(func_code(t).startswith(p) for p in VALVE)]
            xmit_t   = [t for t in tags if any(func_code(t).startswith(p) for p in XMIT)]
            sol_t    = [t for t in tags if any(func_code(t).startswith(p) for p in SOLENOID)]
            sis_sw_t = [t for t in tags if any(func_code(t).startswith(p) for p in SIS_SW)]
            proc_sw_t= [t for t in tags if any(func_code(t).startswith(p) for p in PROC_SW)
                        and not any(func_code(t).startswith(p) for p in SIS_SW)]
            ind_t    = [t for t in tags if any(func_code(t).startswith(p) for p in INDIC)]
            sfv_t    = [t for t in tags if any(func_code(t).startswith(p) for p in SAFETY_V)]

            lines.append(f"── LOOP {lnum}  ({', '.join(tags)})")

            paired_cv = set()

            # Controller → paired valve checks (controller has highest priority)
            for ctag in ctrl_t:
                lines.append(f"  □ [{ctag}] Controller symbol visible and tag labeled on drawing?  → NO = MAJOR")
                if valve_t:
                    vtag = valve_t[0]
                    paired_cv.add(vtag)
                    lines.append(f"  □ [{ctag}] Is control valve {vtag} body symbol physically drawn near {ctag}?  → NO = CRITICAL")
                    lines.append(f"  □ [{vtag}] Is fail-safe position FC, FO, or FL annotated ON {vtag} symbol?  → NO = MAJOR")
                    lines.append(f"  □ [{ctag}→{vtag}] Is control signal dashed line drawn from {ctag} to {vtag}?  → NO = MAJOR")
                else:
                    lines.append(f"  □ [{ctag}] Is there any final control element (valve with actuator) in this control loop?  → NO = CRITICAL")

            # Actuated valves not already covered by controller pairing
            for vtag in valve_t:
                if vtag in paired_cv:
                    continue
                lines.append(f"  □ [{vtag}] Valve body symbol (triangle/gate/globe) visible for {vtag}?  → NO = MAJOR")
                lines.append(f"  □ [{vtag}] Actuator symbol attached to {vtag}?  → NO = MAJOR")
                lines.append(f"  □ [{vtag}] Fail-safe FC, FO, or FL labeled on {vtag} symbol?  → NO = MINOR")
                lines.append(f"  □ [{vtag}] DCS or controller signal connection shown to {vtag}?  → NO = MINOR")

            # Solenoids / I-P converters
            for stag in sol_t:
                lines.append(f"  □ [{stag}] Solenoid/I-P {stag} symbol visible and connected to valve?  → NO = MINOR")
                lines.append(f"  □ [{stag}] DCS signal line to {stag} shown?  → NO = MINOR")

            # Field transmitters / elements
            for xtag in xmit_t:
                lines.append(f"  □ [{xtag}] Transmitter/element {xtag} symbol visible on drawing?  → NO = MAJOR")
                lines.append(f"  □ [{xtag}] Signal type (4-20 mA or dashed line) shown from {xtag}?  → NO = MINOR")
                if ctrl_t:
                    lines.append(f"  □ [{xtag}→{ctrl_t[0]}] Signal connection from {xtag} to {ctrl_t[0]} or DCS visible?  → NO = MAJOR")

            # Safety switches / process switches
            for stag in sis_sw_t:
                lines.append(f"  □ [{stag}] SIS switch symbol visible and process tap connected?  → NO = MAJOR")
                lines.append(f"  □ [{stag}] SIS / interlock connection shown for {stag}?  → NO = CRITICAL (SIS requirement)")
                # NOTE: trip setpoints belong in SIS cause-and-effect / datasheet, NOT on P&ID — do not flag

            for stag in proc_sw_t:
                lines.append(f"  □ [{stag}] Process switch {stag} symbol visible?  → NO = MINOR")
                lines.append(f"  □ [{stag}] DCS alarm connection shown for {stag}?  → NO = MINOR")
                # NOTE: Process switches (XZ-class, single-letter) do NOT require SIS connection

            # Indicators / gauges
            for itag in ind_t:
                lines.append(f"  □ [{itag}] Indicator {itag} visible and tag label clear?  → NO = MINOR")
                lines.append(f"  □ [{itag}] Process tap / connection shown for {itag}?  → NO = MINOR")

            # Safety relief valves
            for svtag in sfv_t:
                lines.append(f"  □ [{svtag}] PSV/PRV symbol visible and set pressure annotated?  → NO = CRITICAL")
                lines.append(f"  □ [{svtag}] Discharge line shown with destination (flare / vent)?  → NO = CRITICAL")

            lines.append("")

        return '\n'.join(lines)
    
    def _vision_analysis_pass(self, images_base64: List[str], reference_context: str = "", layout_context_str: str = "") -> Dict[str, Any]:
        """PASS 3: Systematic vision-based P&ID quality analysis"""
        try:
            # Use soft-coded prompt from pass3_system_prompt.txt if available, else use built-in
            system_prompt = getattr(self, 'pass3_system_prompt', None) or """You are a senior P&ID QA/QC engineer performing a formal quality control review.
Analyze ONLY the provided drawing — base all findings on what is VISUALLY PRESENT, not assumptions.

CORE RULES (follow strictly — violations produce INVALID findings):
1. VISUAL CONFIRMATION MANDATORY — Only report an element if you can see it RIGHT NOW on this drawing.
   NEVER invent, hallucinate, or assume a tag/PSV/valve exists unless its symbol AND tag are visible.

2. P&ID CONNECTOR NUMBERS → NOT piping lines:
   Format NN-PP-NNN-NNNNN (e.g. 13-PP-152-45060, 13-PP-152-143070) = sheet-to-sheet connector arrows.
   These are DRAWING REFERENCE NUMBERS — do NOT flag for missing pipe spec, size, or routing.

3. INSTRUMENT TAG CLASSIFICATION (ISA-5.1 / AGES-GL-08-005):
   INDICATORS (no alarm, no control loop required):
     FI, PI, TI, LI, AI, GI, DI, PG, LG = indicators / gauges only.
     PG (pressure gauge) and LG (level gauge) are LOCAL MECHANICAL INSTRUMENTS — do NOT flag for missing alarm setpoints.
     Measurement ranges / calibration data are in datasheets, NOT required on P&ID.
   CONTROLLERS (require a paired final control element):
     FIC, PIC, TIC, LIC, AIC, HIC, SIC = controllers — verify control valve exists in loop.
   SAFETY SWITCHES (SIS / interlock connection required):
     PSHH, PSLL, LSHH, LSLL, TSHH, TSLL, FSHH, FSLL = safety shutdown switches → verify SIS/interlock.
   PROCESS SWITCHES (discrete alarm contacts, NOT full control loops):
     PSH, PSL, LSH, LSL, TSH, TSL, FSH, FSL = process switches → do NOT flag for missing controller.
     XZ-prefix instruments (XZSH, XZSL, XZLH, XZLL, XZPH, XZPL, XZFH, XZFL) = position/limit switches
       → These are discrete contact outputs. They do NOT need a controller. Do NOT categorize as control_loop.
   SOLENOIDS & CONVERTERS (not equipment):
     XY-prefix (e.g. XY-4513, XY-101) = solenoid valve or I/P converter → category: valve or instrument. NEVER equipment.
   ALARM ELEMENTS (not piping):
     TA, TDA, TSA, FAL, PAL, LAL, FSA, ASA, LSA = alarm elements → category: instrument. NEVER piping.

4. SOFT TAGS / DCS LOGIC BLOCKS (not physical hardware):
   XV, HV, SDV, BDV, FCV, PCV tags appearing ONLY inside DCS logic bubbles or interlock diagrams
   WITHOUT a corresponding physical valve body symbol on the process line = SOFT TAGS.
   Do NOT flag soft tags for missing fail-safe, actuator type, or physical installation issues.
   ONLY flag valves with a physical body symbol visually present on a process line.

5. FAIL-SAFE ANNOTATION:
   FC, FO, FL already shown on the valve symbol = fail-safe IS specified → do NOT re-flag.
   HV and XV use the same notation — if FC/FO/FL or OPEN/CLOSE is on the handle/stem, it is specified.

6. NOTE INTEGRITY — READ, DO NOT INVENT:
   Read the exact text of each note from the drawing. ONLY flag a note issue if the OCR/visual confirms
   the actual note text is non-compliant with what you can read on the drawing.
   Do NOT fabricate note content. If you cannot read a note clearly, report "Note text not legible" (minor).
   NOTE 1, NOTE 2 etc. have specific text — never assume what they say.

7. PIPING RULES:
   Line numbers on the P&ID follow format: SIZE-FLUIDCODE-SEQ-SPEC (e.g. 4"-HC-1001-CS150).
   SERVICE-CODE DRAIN LINES: D-XXXX where XXXX ≥ 1000 (e.g. D-6159, D-5690, D-6156) = Drain service line → PIPING.
   PUMP TAGS: P = PUMP. P-3610, P-3610-02, P-101A are ALL pump (equipment) tags — NEVER a line number.
     A pump tag with a suffix (e.g. P-3610-02) denotes pump train/unit 02 — still equipment.
   TRUE EQUIPMENT TAGS: V-XXXX (vessel/drum), E-XXX (exchanger), K-XXX (compressor), H-XXX (heater), P-XXX (pump).
   DRAWING NUMBER (NOT a line number or pipe size):
     Numbers in format NN.NN.NN.NNNN using DOT separators (e.g. 16.01.08.1678) are DRAWING NUMBERS
     found in the title block. NEVER flag a drawing number as a line size, missing pipe spec, or missing annotation.
     Confirm the drawing number from the title block FIRST — use it as an anchor to avoid confusion.
   Tags like KX-402, TA-4580, TDA-4580 are instrument tags, NOT line numbers — do NOT categorize as piping.
   A component tag with letters (e.g. 13-KX-402) where letters suggest an instrument → check ISA-5.1 first.

8. MINIMUM TARGET: For an IFC-stage multi-loop P&ID, expect 15-30 genuine findings.
   If you find fewer than 10, re-scan zone by zone — you likely missed instruments.

Return ONLY valid JSON in this exact format:
{
    "reasoning": "Summary of what you examined category by category",
    "issues": [
        {
            "serial_number": 1,
            "pid_reference": "Exact tag/line/equipment visible on drawing",
            "issue_observed": "Specific issue with exact values",
            "action_required": "Clear corrective action",
            "evidence": "State exactly what you visually confirmed (or could NOT find) on the drawing. If the finding is inferred from an engineering standard, name the standard. Example: 'Tag X symbol is visible at Bottom-Right but no FO annotation is present next to the actuator.'",
            "severity": "critical/major/minor/observation",
            "category": "instrument/equipment/piping/valve/safety/control_loop/documentation/legend/pipe_class/psv_compliance/holds_compliance/notes_compliance/trim_class/dissimilar_material/ltcs_compliance/free_drain_slope/spool_requirement/critical_stress/valve_standard/tie_in_reference/corrosion_allowance",
            "location_on_drawing": {
                "zone": "Top-Left/Top-Center/Top-Right/Middle-Left/Middle-Center/Middle-Right/Bottom-Left/Bottom-Center/Bottom-Right",
                "drawing_section": "Process area/utility/legend/notes",
                "proximity_description": "Near which equipment or line",
                "visual_cues": "Describe exact position"
            }
        }
    ],
    "specification_breaks": [
        {
            "spec_break_id": "SB-001",
            "location": "Description of where the spec break occurs on the drawing",
            "break_properly_marked": "Yes/No",
            "reason_for_break": "Why the specification changes here",
            "cost_impact": "High/Medium/Low",
            "upstream_spec": {"line_number": "", "material_spec": "", "pressure_class": "", "special_requirements": "None"},
            "downstream_spec": {"line_number": "", "material_spec": "", "pressure_class": "", "special_requirements": "None"},
            "issues_found": ["Description of any issues at this spec break"],
            "transition_piece_required": "Yes/No"
        }
    ],
    "pfd_guidelines_compliance": {
        "holds_and_notes_compliance": {
            "holds_list": [
                {
                    "hold_number": "HOLD-1",
                    "hold_description": "Text of the hold requirement from drawing notes",
                    "compliance_status": "Compliant/Non-Compliant/Under Review",
                    "verification_notes": "How you verified this on the drawing",
                    "related_issues": []
                }
            ],
            "general_notes_list": [
                {
                    "note_number": "NOTE-1",
                    "note_text": "Text of the note from drawing notes section",
                    "note_category": "Design/Safety/Construction/Process",
                    "compliance_status": "Compliant/Non-Compliant/Under Review",
                    "verification_notes": "How you verified this"
                }
            ],
            "critical_violations": []
        }
    },
    "total_issues": 0,
    "confidence": "High/Medium/Low"
}"""

            if reference_context:
                system_prompt += "\n\nREFERENCE DOCUMENTS:\n" + reference_context

            # CAG context block — injected into system prompt so the model knows
            # exactly what OCR confirmed ON THIS DRAWING before it starts generating.
            cag_context = self._build_cag_context()
            system_prompt = system_prompt + '\n\n' + cag_context

            # Pre-compute sparse-OCR conditional blocks (avoids nested triple-quotes inside f-string)
            _few_ocr = len(self.instrument_tags) < 10
            _eq70 = '=' * 70
            if _few_ocr:
                _sparse_banner = (
                    f"{_eq70}\n"
                    f"WARNING: SPARSE OCR - FULL VISUAL SCAN MANDATORY\n"
                    f"{_eq70}\n"
                    f"OCR TEXT EXTRACTION FOUND VERY FEW INSTRUMENT TAGS ({len(self.instrument_tags)} total).\n"
                    f"This drawing likely uses scanned images, embedded fonts, or non-standard encoding.\n\n"
                    f"YOU MUST PERFORM A COMPLETE VISUAL SCAN - do NOT limit analysis to OCR tags only.\n\n"
                    f"STEP 1 - VISUAL INVENTORY (do this first, before any checks):\n"
                    f"  Scan the entire drawing image systematically zone by zone (Top, Middle, Bottom x Left, Center, Right).\n"
                    f"  List EVERY instrument/valve/equipment tag you can read directly on the drawing image.\n"
                    f"  Include: FT, PT, TT, LT, AT, FI, PI, TI, LI, FIC, PIC, TIC, LIC, PCV, FCV, SDV, XV,\n"
                    f"           BDV, PSV, PRV, ZSH, ZSL, TSH, PSH, LSH, XY, HY, TY,\n"
                    f"           pumps (P-xxx), vessels (V-xxx, T-xxx), exchangers (E-xxx), etc.\n\n"
                    f"STEP 2 - For EACH tag you found visually, perform the ISA-5.1 checks below.\n"
                    f"  Do NOT skip any tag you can read. Minimum expected: 15+ findings for an IFC drawing.\n\n"
                    f"CRITICAL: Even if a tag appears only in a DCS bubble or small label, check it visually.\n"
                    f"{_eq70}"
                )
                _sparse_extra = (
                    "--- ADDITIONAL VISUAL SCAN INSTRUCTIONS (OCR was sparse) ---\n"
                    "Since OCR found very few tags, YOU MUST ALSO:\n"
                    "1. Read every bubble/circle on the drawing -- each bubble likely contains an instrument tag\n"
                    "2. Read every valve symbol label -- check for SDV, XV, FCV, PCV, MOV, BDV annotations\n"
                    "3. Read every process line label -- check for pipe class, size, fluid code\n"
                    "4. Read every equipment box/shape -- pump tags, vessel tags, exchanger tags\n"
                    "5. Check the title block and notes section for referenced instrument or equipment tags\n"
                    "6. For each instrument you find visually:\n"
                    "   - Check: is the instrument symbol complete (circle with function letter)?\n"
                    "   - Check: is it connected to process with a signal line?\n"
                    "   - Check: does controller have a paired control valve?\n"
                    "   - Check: does actuated valve show fail-safe position (FC/FO/FL)?\n"
                    "   - Check: do safety instruments (PSH, LSH, TSH) show SIS/interlock connection?\n"
                )
                _ocr_label = "use as a cross-check (NOT the complete list - OCR was sparse)"
                _none_msg = "  None detected - YOU MUST find instruments visually"
            else:
                _sparse_banner = ""
                _sparse_extra = ""
                _ocr_label = "use them as a systematic check checklist"
                _none_msg = "  None detected"

            # Inject soft-coded evidence guidance block (SOFT-CODED: pid_analysis_config.json → evidence_guidance)
            _ev_block = getattr(self, 'evidence_guidance_block', '')
            if _ev_block:
                system_prompt = system_prompt + '\n\n' + _ev_block

            messages = [
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"""Please perform a complete, systematic P&ID Quality Control review on this drawing.

This review should follow the standard for an IFC-stage QC check at an EPC oil and gas company.

{layout_context_str + chr(10) if layout_context_str else ''}{reference_context}

{_sparse_banner}

--- OCR-CONFIRMED ELEMENTS ON THIS DRAWING ---
These tags were extracted by OCR — use as a cross-check (NOT the complete list when OCR is sparse):

INSTRUMENT TAGS ({len(self.instrument_tags)} total):
{chr(10).join('  - ' + t for t in sorted(self.instrument_tags)[:30]) if self.instrument_tags else _none_msg}

LINE NUMBERS ({len(self.line_numbers)} total, first 25):
{chr(10).join('  - ' + ln for ln in sorted(self.line_numbers)[:25]) if self.line_numbers else '  None detected'}

NOTE: Tags in format AREA-FUNCCODE-NUMBER (e.g. 13-FE-4580) are INSTRUMENT TAGS.
Line numbers in format NN-PP-NNN-NNNNN are P&ID sheet connectors - exclude from piping line checks.

--- PER-INSTRUMENT SYSTEMATIC CHECK (OCR confirmed + visually found) ---
{self._build_per_instrument_instructions()}

{_sparse_extra}
--- PIPING LINE CHECK ---
For each line number above (excluding PP-prefix connectors):
  1) Is the full line number visible? (format: SIZE-FLUIDCODE-SEQ-SPEC, e.g. 4"-HC-1001-CS150)
  2) Is the pipe spec class labeled on the line?
  3) Are isolation valves present at equipment nozzle connections?
  4) Are spec breaks indicated where pipe class changes?
  5) Is the source and destination clear (equipment tag or OPC arrow)?

--- OVERALL DRAWING CHECKS ---
Equipment: For each visible vessel/pump/compressor/exchanger - tag format, design conditions (P/T), nozzle connections complete
Safety (PSV_COMPLIANCE): For each PSV/PRV/TSV visible on this drawing:
  - Tag label readable on drawing? (MISSING = MAJOR)
  - Inlet isolation valve present, car-sealed open (CSO)? (MISSING = CRITICAL)
  - Discharge line drawn with labeled destination (flare header, vent stack, or closed system)? (MISSING = CRITICAL)
  NOT ON P&ID — DO NOT FLAG: set pressure value, relieving capacity, back pressure, sizing basis.
    These are DATASHEET values. They do NOT appear on P&ID drawings.
Notes/Holds: Read each active note/hold text - flag non-compliance as separate critical/major issues
Documentation: Legend completeness, title block revision, legibility, symbol consistency

--- ADVANCED ENGINEERING COMPLIANCE CHECKS ---
PIPE_CLASS_vs_TRIM_CLASS (flag pipe-to-valve rated pressure mismatches):
  For each actuated valve (XV, SDV, MOV, BDV, FCV, PCV) connected to equipment nozzles:
  - Identify pipe spec class from connecting line label (e.g., CS150, A1A, B2S, 300#, 600#)
  - Identify valve trim pressure class from annotation near valve symbol
  - RULE: trim class MUST match or EXCEED pipe class rating
  - Trim class LOWER than pipe class = CRITICAL finding (category: trim_class)
  - Trim class annotation MISSING on actuated valve at equipment nozzle = MAJOR finding (category: trim_class)

DISSIMILAR_MATERIAL_CONNECTIONS (galvanic isolation check):
  For every visible spec break or material transition at flanged connections:
  - CS to SS (stainless steel): insulating/dielectric gasket REQUIRED to prevent galvanic corrosion
  - CS to Alloy (duplex, Inconel, Monel, titanium): insulating gasket REQUIRED
  - Spec break symbol (filled triangle/diamond/hexagon) must be present at transition point
  - 'INS. GASKET', 'INSUL. GASKET', or 'DIELECTRIC UNION' note must appear at dissimilar material joint
  - Spec break at dissimilar material boundary WITHOUT insulating gasket annotation = MAJOR finding (category: dissimilar_material)
  - Spec break symbol absent at material transition = MAJOR finding (category: dissimilar_material)

LTCS_SERVICE_COMPLIANCE (cryogenic and low-temperature line check):
  For any line or service that may operate below -29°C (-20°F):
  - LNG, LPG, propane, ethylene, methane C3/C4 services: pipe class must indicate LTCS
  - LTCS designators: suffix L, CS3L, BNW, BNS, or explicit 'LT' in class code
  - If standard CS pipe class shown on a known cryogenic/LPG/LNG service = CRITICAL finding (category: ltcs_compliance)
  - CS class where LTCS is required per line service/temperature = MAJOR finding (category: ltcs_compliance)

FREE_DRAIN_and_SLOPE (gravity-drain lines require slope annotation):
  For horizontal lines in gravity-drain, condensate, or self-draining service:
  - Steam condensate return lines: slope arrow ≥ 1:100 REQUIRED on drawing
  - Flare/blowdown drain headers: free-drain slope to KO drum annotation REQUIRED
  - OWS (oily water sewer) branches: grade/slope annotation REQUIRED
  - Low-point drain valve (½" or ¾" drain connection) required at all horizontal low points
  - High-point vent required at all high points of horizontal liquid-filled lines
  - Horizontal gravity/condensate line without slope annotation = MAJOR finding (category: free_drain_slope)
  - Missing low-point drain at horizontal run low point = MINOR finding (category: free_drain_slope)

MINIMUM_SPOOL_DOWNSTREAM_RO (restriction orifice straight-run requirement):
  For each RO (Restriction Orifice) or VO (Variable Orifice) visible on drawing:
  - Minimum straight pipe spool DOWNSTREAM of RO: 10× pipe diameter (10D) before any fitting
  - Minimum straight pipe spool UPSTREAM of RO: 5× pipe diameter (5D) from previous fitting
  - RO immediately followed by elbow, tee, or reducer without visible spool = MAJOR finding (category: spool_requirement)
  - RO sizing note (bore diameter, calculated Cd) MISSING near RO tag = MINOR finding (category: spool_requirement)

CRITICAL_STRESS_LINE_REQUIREMENTS (stress analysis and piping flexibility):
  NOTE: Anchor points (△), pipe guides, supports, and hangers are shown on PIPING ISOMETRICS and
  SUPPORT DRAWINGS — NOT on P&IDs. DO NOT flag missing anchor points or pipe supports on a P&ID.
  ONLY report critical_stress findings if the P&ID itself carries a CSS or STRESS CRITICAL annotation:
  - If "STRESS CRITICAL" or "CSS" annotation is visible on a line: check that expansion loops or
    bellows (∫ symbol) are drawn for that line.  MISSING = MAJOR  (category: critical_stress)
  - CSS-designated line without any flexibility provision shown = CRITICAL finding (category: critical_stress)
  DO NOT generate findings for anchor points, guides, or pipe supports — they are not P&ID items.

VALVE_STANDARDS_COMPLIANCE (API 6D, ASME B16.34, API 600, ISO 15848):
  For each valve visible on the drawing — check type, annotation, and service compliance:
  BALL VALVES (API 6D):
    - NPS 6 and above on full-bore isolating duty: annotation must indicate trunnion-mounted (not floating ball)
    - Pig-able lines (piggable header): full-bore ball valve REQUIRED; reduced-bore or gate valve on piggable line = MAJOR finding (category: valve_standard)
    - ESD/HIPPS/safety isolation valves: cavity relief port annotation (CR or CAVITY RELIEF) REQUIRED; missing = MAJOR finding (category: valve_standard)
  GATE VALVES (API 600 / ASME B16.34):
    - OS&Y (outside screw and yoke) visible on gate valve symbol for NPS 2" and above? Missing OS&Y annotation = MAJOR finding (category: valve_standard)
    - Fire-safe design annotation required for gate valves in HC service (any flammable fluid); missing = MAJOR finding (category: valve_standard)
  GLOBE AND CONTROL VALVES (BS EN 13709 / IEC 60534):
    - Flow direction arrow REQUIRED on globe valve or control valve body symbol; missing = MINOR finding (category: valve_standard)
    - Manual globe valve NPS 2" and larger requires handwheel symbol shown; missing handwheel = MINOR finding (category: valve_standard)
  CHECK VALVES (API 6D / API 594):
    - Spring-loaded NRV (non-return valve) required at pump discharge where backflow is hazardous; swing check without spring annotation = MAJOR finding (category: valve_standard)
    - Check valve at compressor discharge without "SLOW CLOSING" or "CONTROLLED CLOSURE" annotation = MAJOR finding (category: valve_standard)
  FUGITIVE EMISSIONS (ISO 15848):
    - Actuated valves on BTEX / benzene / H2S / toxic service: "FE CLASS A" or "FE CLASS B" annotation REQUIRED; missing = MAJOR finding (category: valve_standard)
    - Double-block-and-bleed (DBB) required for all safety isolation on HC service above 150 barg or 6" bore; single block shown = MAJOR finding (category: valve_standard)
  PRESSURE-TEMPERATURE CHECK (ASME B16.34):
    - Valve body pressure class must MATCH or EXCEED connecting flange pressure class (e.g., Class 300 flange → requires minimum Class 300 valve body)
    - Valve body class LOWER than connecting pipe flange class = CRITICAL finding (category: valve_standard)
    - Valve adjacent to high-temperature service (>200°C): WC6/WC9/P91 body material REQUIRED; WCB shown = MAJOR finding (category: valve_standard)

TIE_IN_POINT_VERIFICATION (tie-in package and battery limit compliance):
  For each tie-in point or battery limit (BL) connection visible on the drawing:
  - Must have a readable sequential tie-in tag visible on the drawing (e.g. TI-0001); untagged or unreadable tie-in tag = MAJOR finding (category: tie_in_reference)
  - Existing pipe spec clearly shown at the tie-in point (upstream/existing spec vs new spec); missing existing spec = MAJOR finding (category: tie_in_reference)
  - Block valve (isolation valve) shown at tie-in to allow hot tap or cold cut isolation; missing isolation valve at TI = CRITICAL finding (category: tie_in_reference)
  - Connection type annotated: "HOT TAP", "COLD TIE-IN", "FLANGED TIE-IN", or "WELDED TIE-IN"; missing type = MAJOR finding (category: tie_in_reference)
  - Vent and drain connections shown at new spool piece adjacent to tie-in for pressure testing/purging; missing = MINOR finding (category: tie_in_reference)
  - Battery limit boxes at plot limits must show: design pressure, design temperature, fluid service, pipe class on BOTH sides of limit line; missing any = MAJOR finding (category: tie_in_reference)

CORROSION_ALLOWANCE_AND_NACE (material, CA annotation, sour/H2S compliance):
  For pressure vessels, columns, and process equipment visible on drawing:
  - Corrosion allowance (CA) annotation REQUIRED in operating condition box (e.g., CA = 3 mm); missing = MAJOR finding (category: corrosion_allowance)
  - Lines and equipment in sour service (H2S present > trace): "SOUR SERVICE", "NACE", or "MR0175" annotation REQUIRED; missing = CRITICAL finding (category: corrosion_allowance)
  - Amine service vessels/piping: "PWHT REQUIRED" annotation REQUIRED; missing = MAJOR finding (category: corrosion_allowance)
  - Seawater service: "DNV-GL", "SEAWATER GRADE", or alloy annotation REQUIRED (duplex SS, 6Mo, Cu-Ni); carbon steel in SW service without annotation = CRITICAL finding (category: corrosion_allowance)
  - Acid (HCl, H2SO4) service: lining annotation REQUIRED (glass-lined, rubber-lined, PVDF, Hastelloy); bare CS in acid service = CRITICAL finding (category: corrosion_allowance)
  - Heat tracing annotation: for lines in which freezing or solidification is a risk (wax, hydrate, viscous fluid), "HT" or "HEAT TRACED" or "EHT" annotation MUST appear on line label; missing = MAJOR finding (category: corrosion_allowance)
  - Insulation annotation: process lines above 60°C or below 0°C require insulation class (HOT, COLD, PERSONNEL PROTECTION, PP) annotation; missing = MINOR finding (category: corrosion_allowance)

--- RULES (always apply, violation = false positive) ---
- VISUAL ONLY: Report ONLY elements visually confirmed on this drawing. Never invent a tag or instrument.
- PP-PREFIX: Lines like 13-PP-152-45060 are P&ID sheet connectors, NOT piping lines — skip all piping checks.
- INDICATORS: FI/PI/TI/LI/PG/LG = indicators/gauges — NO alarm setpoints, NO control loop required.
- MEASUREMENT RANGE: Do NOT flag missing measurement range on P&ID — that belongs in the instrument datasheet.
- SWITCHES: PSH/PSL/LSH/LSL/TSH/TSL/FSH/FSL = process switches (discrete). Do NOT flag for missing controller.
- XZ-SWITCHES: XZSH/XZSL/XZLH/XZLL/XZPH/XZPL = position/limit switches. NOT control_loop. NOT safety unless SIS-linked.
- SOLENOIDS: XY-prefix tags = solenoid/converter. Category = valve or instrument. NEVER equipment.
- ALARM ELEMENTS: TA/TDA/TSA/LSA/FSA tags = instrument alarm elements. NEVER piping.
- SOFT TAGS: XV/HV/SDV/BDV appearing in DCS logic blocks without a physical valve body on a process line = soft tags. Do NOT flag.
- FAIL-SAFE: FC/FO/FL or OPEN/CLOSED already on any valve symbol (XV, HV, SDV, BDV, FCV) = specified. Do NOT re-flag.
- NOTES: Only report a note issue if you can read the note text and it is genuinely non-compliant. Never fabricate note content.
- HALLUCINATION CHECK: Before writing any issue, confirm: "I can see this tag's symbol on the drawing right now."

--- CRITICAL REPORTING REQUIREMENT ---
EVERY instrument, valve, and loop element you can read on the drawing that has a missing or
non-compliant annotation MUST become a separate JSON issue entry.
"Cannot confirm" = element is absent, unclear, or not annotated. One element = one issue.
Do NOT merge multiple issues into one finding.
MINIMUM TARGET: {self.min_issues_target} genuine findings for an IFC-stage drawing. If you find fewer than {max(10, self.min_issues_target - 5)}, re-scan
zone by zone. But do NOT add fabricated findings to reach the target — only real issues.

Return ONLY valid JSON:
{{
    "reasoning": "What you examined: list instruments by category, describe lines, equipment, safety, notes checked",
    "issues": [
        {{
            "serial_number": 1,
            "pid_reference": "Exact tag/line/equipment visible on drawing",
            "issue_observed": "Specific issue with exact values",
            "action_required": "Clear corrective action",
            "evidence": "State exactly what you visually confirmed (or could NOT find) on the drawing. If the finding is inferred from an engineering standard, name the standard.",
            "severity": "critical/major/minor/observation",
            "category": "instrument/equipment/piping/valve/safety/control_loop/documentation/legend/pipe_class/psv_compliance/holds_compliance/notes_compliance/trim_class/dissimilar_material/ltcs_compliance/free_drain_slope/spool_requirement/critical_stress/valve_standard/tie_in_reference/corrosion_allowance",
            "location_on_drawing": {{
                "zone": "Top-Left/Top-Center/Top-Right/Middle-Left/Middle-Center/Middle-Right/Bottom-Left/Bottom-Center/Bottom-Right",
                "drawing_section": "Process area/utility/legend/notes",
                "proximity_description": "Near which equipment or line",
                "visual_cues": "Describe exact position on the page"
            }}
        }}
    ],
    "specification_breaks": [
        {{
            "spec_break_id": "SB-001",
            "location": "Where the spec break occurs",
            "break_properly_marked": "Yes/No",
            "reason_for_break": "Why the spec changes",
            "cost_impact": "High/Medium/Low",
            "upstream_spec": {{"line_number": "", "material_spec": "", "pressure_class": "", "special_requirements": "None"}},
            "downstream_spec": {{"line_number": "", "material_spec": "", "pressure_class": "", "special_requirements": "None"}},
            "issues_found": [],
            "transition_piece_required": "Yes/No"
        }}
    ],
    "pfd_guidelines_compliance": {{
        "holds_and_notes_compliance": {{
            "holds_list": [
                {{
                    "hold_number": "HOLD-1",
                    "hold_description": "Text of hold from drawing",
                    "compliance_status": "Compliant/Non-Compliant/Under Review",
                    "verification_notes": "How verified",
                    "related_issues": []
                }}
            ],
            "general_notes_list": [
                {{
                    "note_number": "NOTE-1",
                    "note_text": "Text of note from drawing",
                    "note_category": "Design/Safety/Construction/Process",
                    "compliance_status": "Compliant/Non-Compliant/Under Review",
                    "verification_notes": "How verified"
                }}
            ],
            "critical_violations": []
        }}
    }},
    "total_issues": 0,
    "confidence": "High/Medium/Low"
}}

Return ONLY valid JSON. No markdown, no text outside the JSON."""
                        }
                    ] + [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{img}",
                                "detail": "high"
                            }
                        }
                        for img in images_base64
                    ]
                }
            ]
            
            print("[INFO] Calling AI Vision API (Pass 3: Chain-of-Thought)...")
            _p3_provider = getattr(self, 'pass3_provider', 'openai')
            response_text = self._call_ai_vision(
                messages=messages,
                pass_label="PASS 3",
                provider=_p3_provider,
                max_tokens=self.pass3_max_tokens,
                temperature=self.pass3_temperature,
                timeout=self.pass3_timeout,
            )

            if not response_text:
                print("[ERROR] AI provider returned empty response for Pass 3")
                return {'issues': [], 'total_issues': 0, 'confidence': 'Low'}

            response_text = response_text.strip()
            print(f"[INFO] Pass 3 complete. Response length: {len(response_text)} chars")

            return self._parse_analysis_response(response_text, 0)
            
        except Exception as e:
            print(f"[ERROR] Vision analysis failed: {str(e)}")
            return {'issues': [], 'total_issues': 0, 'confidence': 'Low'}
    
    def _cross_validation_pass(self, vision_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """PASS 3: Cross-validate OCR data with vision findings - Smart filtering to reduce false positives"""
        consistency_issues = []
        serial_offset = vision_result.get('total_issues', 0)
        
        # Check 1: Instruments mentioned in text but not found in vision analysis
        # Smart filtering: Only report if significant number AND likely real instruments
        vision_tags = set()
        for issue in vision_result.get('issues', []):
            ref = issue.get('pid_reference', '')
            vision_tags.add(ref)
        
        missing_in_vision = self.instrument_tags - vision_tags
        
        # RULE 18 ENFORCEMENT: Never generate hallucinated critical-instrument issues from OCR alone.
        # OCR may extract tag strings from drawing text, notes, or title-block that are NOT actual
        # physical instruments on this sheet. We only raise an observation-level note here — never
        # a "critical" or "major" finding — because we cannot visually confirm the instrument exists.
        # Previously "PSV" was in critical_prefixes causing false PSV findings (Issue #3 from expert).
        # Cross-validation issues are now observation-only and clearly flagged as "text reference only".
        
        if len(missing_in_vision) > 10:  # Only report if many tags missing (possible OCR limitation)
            consistency_issues.append({
                'serial_number': serial_offset + len(consistency_issues) + 1,
                'pid_reference': f"OCR Text Reference: {', '.join(list(missing_in_vision)[:5])}... ({len(missing_in_vision)} total)",
                'issue_observed': f'Found {len(missing_in_vision)} instrument tag strings in extracted text that were not visually confirmed on drawing. These may be: (1) references to instruments on connected OPC/drawings, (2) OCR artifacts, (3) tags in notes/title block, or (4) instruments with symbol recognition limitations. Visual confirmation required.',
                'action_required': 'Review if these tags are cross-references to connected drawings. If they should be on this drawing, verify instrument symbols are physically present.',
                'evidence': 'Programmatically generated: these tag strings were found by OCR text extraction but were NOT visually confirmed as physical instrument symbols on the drawing. Source may be text in notes, title block, or connected drawing references.',
                'severity': 'observation',
                'category': 'instrument',
                'location_on_drawing': {
                    'zone': 'Multiple',
                    'drawing_section': 'Text references or connected systems',
                    'proximity_description': 'Tags found in text extraction only — NOT visually confirmed',
                    'visual_cues': 'Check notes section and connected OPC/drawing references'
                }
            })
        
        # Check 2: Equipment tags consistency - Smart filtering
        missing_equipment = self.equipment_tags - vision_tags
        
        # Only report if significant (> 5) and not likely P&ID references
        if len(missing_equipment) > 5:
            consistency_issues.append({
                'serial_number': serial_offset + len(consistency_issues) + 1,
                'pid_reference': f"Equipment: {', '.join(list(missing_equipment)[:5])}... ({len(missing_equipment)} total)",
                'issue_observed': f'Found {len(missing_equipment)} equipment tags in text not verified visually. These may be: (1) Equipment in connected systems/drawings, (2) Legend references, or (3) OCR artifacts.',
                'action_required': 'Review if these are intentional cross-references. Verify critical equipment is properly shown with symbols and datasheets.',
                'evidence': 'Programmatically generated: equipment tags found by OCR text extraction but NOT visually confirmed as symbols on the drawing. Possible sources: legend, notes section, connected drawing references, or OCR artifacts.',
                'severity': 'observation',
                'category': 'equipment',
                'location_on_drawing': {
                    'zone': 'Multiple',
                    'drawing_section': 'Check legend and connected drawings',
                    'proximity_description': 'Tags found in text',
                    'visual_cues': 'May be in notes, legend, or P&ID reference section'
                }
            })
        
        # Check 3: Notes and Holds validation - Keep as observation only
        if len(self.notes_references) > 0:
            consistency_issues.append({
                'serial_number': serial_offset + len(consistency_issues) + 1,
                'pid_reference': f"NOTES: {', '.join(list(self.notes_references)[:5])}",
                'issue_observed': f'Found {len(self.notes_references)} note/hold references. Verify all notes are applicable and properly implemented in the design.',
                'action_required': 'Cross-check each note/hold requirement is addressed in equipment specs, line specs, and instrumentation.',
                'evidence': 'Programmatically generated: note/hold reference text was extracted by OCR. Individual note compliance was not verified by AI visual scan; this is a reminder to cross-check each note against the design.',
                'severity': 'observation',
                'category': 'documentation',
                'location_on_drawing': {
                    'zone': 'Bottom-Right',
                    'drawing_section': 'Notes Section',
                    'proximity_description': 'Drawing notes area',
                    'visual_cues': 'Check notes section for all references'
                }
            })
        
        print(f"[INFO] Cross-validation found {len(consistency_issues)} consistency observations (smart filtered)")
        return consistency_issues
    
    def _second_review_pass(self, images_base64: List[str], first_pass: Dict, consistency: List, layout_context_str: str = '') -> List[Dict[str, Any]]:
        """PASS 5: Second review — broader visual scan when first pass found too few issues"""
        try:
            first_pass_issues = first_pass.get('issues', [])
            first_refs = {i.get('pid_reference', '').upper() for i in first_pass_issues}
            few_ocr_tags = len(self.instrument_tags) < 10

            # Find OCR tags not mentioned in first-pass findings
            all_ocr = sorted(list(self.instrument_tags or []) + list({
                f"{p.split('-')[1]}-{p.split('-')[2]}"
                for p in (self.line_numbers or [])
                if len(p.split('-')) == 3 and p.split('-')[0].isdigit() and not p.split('-')[1].isdigit()
            }))
            unchecked = [t for t in all_ocr if not any(t.upper() in ref or ref in t.upper() for ref in first_refs)]

            first_summary = '\n'.join(
                f"  - {i.get('pid_reference')}: {i.get('issue_observed','')[:60]}"
                for i in first_pass_issues[:15]
            )

            unchecked_str = ', '.join(unchecked[:20]) if unchecked else 'All OCR tags were addressed'

            # Build an aggressive visual-scan instruction when OCR was sparse or few issues found
            sparse_scan_instruction = ""
            if few_ocr_tags or len(first_pass_issues) < 15:
                sparse_scan_instruction = f"""
⚠️  IMPORTANT: The first pass found only {len(first_pass_issues)} issues with {len(self.instrument_tags)} OCR tags.
This strongly suggests the drawing has MORE instruments that OCR could not read.

YOU MUST NOW DO A COMPLETE VISUAL SWEEP zone by zone:

Zone scan order: Top-Left → Top-Center → Top-Right → Middle-Left → Middle-Center → Middle-Right → Bottom-Left → Bottom-Center → Bottom-Right

For EACH zone, look for and report issues on:
1. INSTRUMENT BUBBLES — any circle with letters (FT, PT, TT, LT, AT, FI, PI, TI, LI, FIC, PIC, etc.)
   - Missing signal connection (dashed line)?
   - Missing fail-safe annotation (FC/FO/FL) on any actuated valve?
   - Controller without paired control valve in this zone?
2. ACTUATED VALVES — any valve symbol with actuator stem (SDV, XV, FCV, PCV, BDV, MOV)
   - Is fail-safe position labeled?
   - Is DCS/SIS signal shown?
3. SAFETY INSTRUMENTS — PSH, LSH, TSH, PSHH, LSHH, TSHH
   - Is SIS/interlock connection shown?
4. PIPING LINES — any line with visible number label
   - Full line number format correct? (size-fluidcode-seq-spec)
   - Spec break where pipe class changes?
5. EQUIPMENT — any vessel, pump, compressor, exchanger symbols
   - Design conditions shown?
   - Nozzle connections complete?

If you find instruments/valves not addressed in the first pass, REPORT THEM NOW.
Target: report every genuine drawing-internal inconsistency you can observe.
Do NOT invent findings. Do NOT apply external company or industry standards."""

            # Build the layout context block to inject into the zone-sweep instruction
            _layout_block = (
                f"\nDRAWING LAYOUT (auto-detected):\n{layout_context_str}\n"
                if layout_context_str else ""
            )

            messages = [
                {
                    "role": "system",
                    "content": """Perform a focused SECOND REVIEW on a P&ID drawing.

STRICT RULES:
- ONLY report issues visually confirmed on the drawing - never fabricate
- CONSISTENCY-ONLY: compare each element ONLY to similar elements on this same drawing
- Do NOT apply external standards (ISA, API, ASME, ADNOC, ARAMCO, SHELL) unless
  they are explicitly referenced in this drawing's own notes or title block
- Indicators (FI/PI/TI/LI/PG/LG): flag a missing signal line ONLY if other same-type
  instruments on this drawing DO show signal lines — flag the inconsistency, not the absence
- FC/FO/FL already annotated on valve = fail-safe specified - do NOT re-flag
- P&ID connector numbers (NN-PP-NNN-NNNNN) are NOT process piping lines

WHAT TO LOOK FOR:
- Any instruments / equipment visible but not addressed in first pass
- Control loops where signal connections are absent
- Missing fail-safe annotations on actuated valves
- Safety switches without interlock wiring shown
- Piping lines with incomplete annotation
- Equipment without design conditions

MANDATORY JSON FORMAT - each issue MUST have ALL these exact keys:
{
  "issues": [
    {
      "serial_number": 1,
      "pid_reference": "exact tag or line number visible on drawing (e.g. FT-3601-03)",
      "issue_observed": "specific description of what is missing or non-compliant",
      "action_required": "clear corrective action",
      "evidence": "VISUAL: [what is drawn]. GAP: [what is missing/inconsistent]. DRAWING BASIS: [reference to other similar elements on THIS drawing that establish the requirement — never cite external standards].",
      "severity": "critical|major|minor|observation",
      "category": "instrument|equipment|piping|valve|safety|control_loop|documentation",
      "location_on_drawing": {
        "zone": "Top-Left|Top-Center|Top-Right|Middle-Left|Middle-Center|Middle-Right|Bottom-Left|Bottom-Center|Bottom-Right",
        "drawing_section": "Process area/utility/legend/notes",
        "proximity_description": "near which equipment or line",
        "visual_cues": "describe exact position"
      }
    }
  ],
  "total_issues": 0
}

Do NOT use any other key names. The keys pid_reference and issue_observed are REQUIRED in every issue."""
                + ('\n\n' + getattr(self, 'evidence_guidance_block', '') if getattr(self, 'evidence_guidance_block', '') else '')
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"""SECOND REVIEW PASS — comprehensive scan for missed elements.
{_layout_block}
{self._build_cag_context()}

FIRST PASS ({len(first_pass_issues)} issues found):
{first_summary}

OCR TAGS NOT YET COVERED: {unchecked_str}
{sparse_scan_instruction}

For each uncovered tag or visually found element, check:
- Is the instrument symbol visible and properly labeled?
- Is its signal connection / wiring clearly shown?
- Is required annotation (fail-safe, setpoint reference, etc.) present?
Report any missing or unclear elements as separate issues.

--- NOTES/HOLDS COMPLIANCE CHECK ---
OCR found these {len(self.notes_references)} note/hold references on the drawing:
{chr(10).join('  - ' + n for n in sorted(self.notes_references)) if self.notes_references else '  None found — DO NOT generate notes/holds issues'}

{'For each HOLD visible on the drawing, read the exact text and check if the requirement is resolved. OPEN holds = CRITICAL. For each NOTE, check if its requirement is implemented. Non-compliant notes = MAJOR.' if self.notes_references else 'There are NO notes or holds on this drawing. Skip all notes/holds checks.'}

Return ONLY valid JSON: {{"issues": [...], "total_issues": N}}"""
                        }
                    ] + [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{img}",
                                "detail": "high"
                            }
                        }
                        for img in images_base64
                    ]
                }
            ]

            _p5_provider = getattr(self, 'pass5_provider', 'openai')
            print(f"[INFO] Calling AI Vision API (Pass 5) — provider={_p5_provider}...")
            response_text = self._call_ai_vision(
                messages=messages,
                pass_label="PASS 5",
                provider=_p5_provider,
                max_tokens=self.pass5_max_tokens,
                temperature=self.pass5_temperature,
                timeout=self.pass5_timeout,
            )

            if not response_text:
                print("[WARNING] Pass 5: AI provider returned empty response")
                return []

            response_text = response_text.strip()
            print(f"[DEBUG RAW SECOND PASS] len={len(response_text)} | preview={response_text[:120]}")
            result = self._parse_analysis_response(response_text, 0)

            print(f"[INFO] Second pass found {len(result.get('issues', []))} additional issues")
            return result.get('issues', [])

        except Exception as e:
            print(f"[WARNING] Second review pass failed: {str(e)}")
            return []
    
    def _engineering_compliance_pass(
        self,
        images_base64: List[str],
        first_pass: Dict,
        second_pass_issues: List,
    ) -> List[Dict[str, Any]]:
        """
        PASS 6: Engineering Compliance Deep-Scan.

        A dedicated AI call with a specialist prompt focused EXCLUSIVELY on the nine
        advanced engineering compliance domains that a conventional P&ID instrument-check
        pass tends to miss.  The results are later merged with Passes 3/4/5.

        Domains covered (all soft-coded — extend by adding to the prompt):
          1. PSV / PRV / TSV compliance (set pressure, discharge line, sizing note)
          2. Valve standards (API 6D, ASME B16.34, API 600, ISO 15848)
          3. Tie-in points & battery limits
          4. Corrosion allowance (CA), NACE MR0175, PWHT annotations
          5. Dissimilar-material spec breaks (insulating gaskets)
          6. LTCS / cryogenic service pipe-class compliance
          7. Free-drain / slope annotations on gravity and condensate lines
          8. Restriction orifice (RO) straight-run spool requirements
          9. Critical stress / CSS / high-temp line annotations
        """
        try:
            # ── Build "already reported" context so the engineer pass doesn't duplicate ──
            already = []
            for iss in [*first_pass.get('issues', []), *second_pass_issues]:
                ref = iss.get('pid_reference', '')
                obs = iss.get('issue_observed', '')[:60]
                already.append(f"  - [{ref}] {obs}")
            already_str = '\n'.join(already[:30]) if already else '  None yet'

            # ── Sparse-OCR guard ──
            ocr_note = (
                "NOTE: OCR yielded very few tags — perform a complete visual sweep of the "
                "drawing before attempting each check below.\n"
                if len(self.instrument_tags) < 10 else ""
            )

            system_msg = """You are a senior process/piping/safety engineer performing a
SPECIALIST engineering compliance review of a P&ID drawing at IFC stage.

Your focus is EXCLUSIVELY on the nine advanced engineering domains listed below.
You must NOT re-report any issue already captured in the instrument/loop check pass.
Every finding YOU produce must come from a VISUALLY CONFIRMED element on the drawing.

GOLDEN RULES (same as always):
- VISUAL CONFIRMATION MANDATORY — never invent a tag, equipment item, or annotation.
- pid_reference MUST be the EXACT tag you read from the drawing. NEVER echo placeholder text from these instructions (e.g. never write "PSV-XXXX", "TI-XXXX", or any other template — always write the actual tag you can see, or describe as "PSV [tag unreadable]" if you cannot read it).
- PP-prefix connector numbers (NN-PP-NNN-NNNNN) are sheet connectors, NOT piping lines.
- PG/LG are local gauges — they do NOT need alarm setpoints or measurement ranges on P&ID.
- FC/FO/FL already on a valve symbol = fail-safe specified — do NOT flag again.
- Indicators (FI/PI/TI/LI/AI/PG/LG) do NOT need a control loop.
- Soft DCS tags inside logic bubbles without a physical valve body = NOT physical hardware.

MANDATORY JSON FORMAT — return ONLY valid JSON, no markdown:
{
  "issues": [
    {
      "serial_number": 1,
      "pid_reference": "exact tag/line/equipment visible on drawing",
      "issue_observed": "specific non-compliance with exact values",
      "action_required": "clear corrective action referencing the applicable standard",
      "evidence": "VISUAL: [what is drawn]. GAP: [what is missing or inconsistent with other elements on this drawing]. DRAWING BASIS: [what on this drawing establishes the requirement — compare to other similar elements on the same drawing].",
      "severity": "critical|major|minor|observation",
      "category": "psv_compliance|valve_standard|tie_in_reference|corrosion_allowance|dissimilar_material|ltcs_compliance|free_drain_slope|spool_requirement|critical_stress",
      "location_on_drawing": {
        "zone": "Top-Left|Top-Center|Top-Right|Middle-Left|Middle-Center|Middle-Right|Bottom-Left|Bottom-Center|Bottom-Right",
        "drawing_section": "Process area/utility/legend/notes",
        "proximity_description": "near which equipment or line",
        "visual_cues": "describe exact position"
      }
    }
  ],
  "total_issues": 0
}"""

            user_text = f"""You are examining the P&ID engineering drawing image(s) attached to this message.
Analyze the VISUAL CONTENT of the attached drawing(s) to check engineering compliance.

{ocr_note}ENGINEERING COMPLIANCE DEEP-SCAN — 9 specialist domains.

ISSUES ALREADY REPORTED (do NOT repeat these):
{already_str}

OCR-Confirmed line numbers on this drawing (first 25):
{chr(10).join('  ' + ln for ln in sorted(self.line_numbers)[:25]) if self.line_numbers else '  None'}

For EVERY visually-confirmed element below, check compliance and flag each deficiency as a
separate JSON issue.  One element × one deficiency = one issue entry. Do NOT group findings.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DOMAIN 1 — PSV / PRV / TSV COMPLIANCE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
For each PSV, PRV, PDSV, or TSV symbol visible on the drawing:
  □ Does the PSV have a readable, complete tag label (e.g. PSV-1015)?  → NO READABLE TAG = MAJOR
  □ Is a car-sealed open (CSO) block valve shown on the PSV inlet line?  → MISSING = CRITICAL
  □ Is the discharge line drawn to a flare header, vent stack, or labeled closed system?  → MISSING = CRITICAL
  □ Is the inlet line size equal to or larger than the PSV inlet nozzle (no reducers on PSV inlet)?  → REDUCER ON INLET = MAJOR

NOT REQUIRED ON P&ID (do NOT flag these — they belong in the PSV datasheet, not the drawing):
  ✕ Set pressure value (e.g. "10 barg") — this is a DATASHEET item
  ✕ Relieving capacity (e.g. "1200 kg/h") — this is a DATASHEET item
  ✕ Back pressure, superimposed back pressure — this is a DATASHEET item
  ✕ Sizing basis note / reference tag — this is a DATASHEET item
  ✕ "SEE PSV DATA SHEET" annotation — not required on P&ID

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DOMAIN 2 — VALVE STANDARDS (API 6D / ASME B16.34 / API 600 / ISO 15848)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
For each actuated or specialty valve visible:
  □ Ball valve NPS ≥ 6" on isolating/ESD duty: is "TRUNNION" annotation present?  → MISSING = MAJOR  (category: valve_standard)
  □ Ball valve on piggable header: is it full-bore?  → REDUCED BORE = MAJOR  (category: valve_standard)
  □ ESD / BDV / isolation valve: is "CAVITY RELIEF" or "CR" annotation present?  → MISSING = MAJOR  (category: valve_standard)
  □ Gate valve NPS ≥ 2": is OS&Y annotation shown?  → MISSING = MAJOR  (category: valve_standard)
  □ Gate valve in HC service: is "FIRE SAFE" or "FS" annotation shown?  → MISSING = MAJOR  (category: valve_standard)
  □ Globe / control valve: is flow direction arrow shown on body symbol?  → MISSING = MINOR  (category: valve_standard)
  □ Check valve at pump discharge in hazardous service: is "SPRING LOADED" annotated?  → MISSING = MAJOR  (category: valve_standard)
  □ Actuated valve in BTEX/H2S/toxic service: is "FE CLASS A/B" (ISO 15848) annotated?  → MISSING = MAJOR  (category: valve_standard)
  □ Double-block-and-bleed (DBB) required for HC isolation >150 barg or >6": is it shown?  → SINGLE BLOCK = MAJOR  (category: valve_standard)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DOMAIN 3 — TIE-IN POINTS & BATTERY LIMITS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
For each tie-in point or BL (battery limit) connection visible:
  □ Does the tie-in point have a readable sequential tag visible on the drawing (e.g. TI-0001)?  → NO READABLE TAG = MAJOR  (category: tie_in_reference)
  □ Is the existing pipe spec labeled at the tie-in arrow?  → MISSING = MAJOR  (category: tie_in_reference)
  □ Is an isolation valve shown at the tie-in to allow hot-tap or cold-cut?  → MISSING = CRITICAL  (category: tie_in_reference)
  □ Is the connection type annotated (HOT TAP / COLD TIE-IN / FLANGED / WELDED)?  → MISSING = MAJOR  (category: tie_in_reference)
  □ Are vent and drain connections shown on the new spool at the tie-in?  → MISSING = MINOR  (category: tie_in_reference)
  □ Battery limit box: does it show design P, design T, fluid service, AND pipe class on BOTH sides?  → MISSING ANY = MAJOR  (category: tie_in_reference)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DOMAIN 4 — CORROSION ALLOWANCE, NACE, PWHT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
For each pressure vessel, column, or major piece of equipment visible:
  □ Is CA (corrosion allowance, e.g. CA = 3 mm) shown in the operating conditions box?  → MISSING = MAJOR  (category: corrosion_allowance)
  □ Is the equipment in sour (H2S) service? → "SOUR SERVICE" / "NACE MR0175" REQUIRED  → MISSING = CRITICAL  (category: corrosion_allowance)
  □ Is the equipment in amine service? → "PWHT REQUIRED" annotation REQUIRED  → MISSING = MAJOR  (category: corrosion_allowance)
  □ Seawater service lines: alloy (duplex, 6Mo, Cu-Ni) or "SEAWATER GRADE" annotation?  → CS WITHOUT NOTE = CRITICAL  (category: corrosion_allowance)
  □ Acid (HCl/H2SO4) service: lining annotation (glass-lined, rubber-lined, PVDF)?  → BARE CS = CRITICAL  (category: corrosion_allowance)
  □ Lines at risk of freezing/solidification: "HT" or "EHT" (electric heat tracing) annotation?  → MISSING = MAJOR  (category: corrosion_allowance)
  □ Lines >60°C or <0°C: insulation class (HOT / COLD / PP) on line label?  → MISSING = MINOR  (category: corrosion_allowance)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DOMAIN 5 — DISSIMILAR MATERIAL SPEC BREAKS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
For every visible spec break symbol (triangle/diamond/hexagon on a line):
  □ Is an insulating/dielectric gasket annotation present at CS→SS or CS→Alloy transitions?  → MISSING = MAJOR  (category: dissimilar_material)
  □ Is the spec break symbol clearly shown at the material transition boundary?  → MISSING SYMBOL = MAJOR  (category: dissimilar_material)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DOMAIN 6 — LTCS / CRYOGENIC SERVICE PIPE CLASS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
For any line in LNG / LPG / propane / ethylene / cryogenic service:
  □ Does the pipe class code indicate LTCS (suffix L, CS3L, BNW, BNS, or "LT" in class code)?  → STANDARD CS = CRITICAL  (category: ltcs_compliance)
  □ Is a low-temperature design temperature annotation on the line or vessel?  → MISSING = MAJOR  (category: ltcs_compliance)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DOMAIN 7 — FREE-DRAIN / SLOPE ANNOTATIONS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
For horizontal lines in gravity-drain, condensate, or self-draining service:
  □ Steam condensate return: slope arrow ≥ 1:100 annotated?  → MISSING = MAJOR  (category: free_drain_slope)
  □ Flare/blowdown drain header: free-drain slope to KO drum annotated?  → MISSING = MAJOR  (category: free_drain_slope)
  □ OWS (oily water sewer) branch: grade/slope annotation present?  → MISSING = MINOR  (category: free_drain_slope)
  □ Low-point drain valve (½" or ¾") present at all horizontal low points?  → MISSING = MINOR  (category: free_drain_slope)
  □ High-point vent at all high points of liquid-filled horizontal lines?  → MISSING = MINOR  (category: free_drain_slope)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DOMAIN 8 — RESTRICTION ORIFICE (RO) STRAIGHT-RUN SPOOLS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
For each RO / VO visible on the drawing:
  □ Minimum 10D straight spool DOWNSTREAM before any fitting shown?  → MISSING = MAJOR  (category: spool_requirement)
  □ Minimum 5D straight spool UPSTREAM of RO shown?  → MISSING = MAJOR  (category: spool_requirement)
  □ Bore diameter and Cd (discharge coefficient) annotation near RO tag?  → MISSING = MINOR  (category: spool_requirement)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DOMAIN 9 — CRITICAL STRESS / CSS LINES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NOTE: Anchor points, guides, hangers, and stress calculations are shown on PIPING
ISOTOMETRICS and STRESS ANALYSIS drawings — NOT on the P&ID. Do NOT flag their absence
on a P&ID; it is not a P&ID deficiency.

For any line that is explicitly annotated CSS, STRESS CRITICAL, or HIGH TEMP on THIS P&ID:
  □ If a CSS annotation is visible: check that expansion loops, bellows (∫ symbol), or
    cold-pull offsets are drawn on the line.  MISSING = MAJOR  (category: critical_stress)
  □ Any line in H2 / steam / cryogenic service explicitly noted as CSS on this drawing:
    verify flexibility provisions are shown.  MISSING = CRITICAL  (category: critical_stress)

IF no CSS or STRESS CRITICAL annotation is visible anywhere on the drawing — skip this domain.
DO NOT generate critical_stress findings based on assumptions about the service alone.

Return ONLY valid JSON with ALL findings from ALL nine domains above."""

            # Inject soft-coded evidence guidance (SOFT-CODED: pid_analysis_config.json → evidence_guidance)
            _ev_block6 = getattr(self, 'evidence_guidance_block', '')
            if _ev_block6:
                system_msg += '\n\n' + _ev_block6

            messages = [
                {"role": "system", "content": system_msg},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                    ] + [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{img}",
                                "detail": "high"
                            }
                        }
                        for img in images_base64
                    ]
                }
            ]

            response_text = self._call_with_vision_fallback(
                messages,
                pass_label="PASS 6",
                primary_tokens=12000,
                fallback_tokens=10000,
                primary_timeout=300,
                fallback_timeout=270,
                temperature=self.pass6_temperature,
            )
            if not response_text:
                print("[INFO] Pass 6 (engineering compliance) found 0 additional issues")
                return []
            result = self._parse_analysis_response(response_text, 0)
            issues = result.get('issues', [])
            print(f"[INFO] Pass 6 (engineering compliance) found {len(issues)} additional issues")
            return issues

        except Exception as e:
            print(f"[WARNING] Engineering compliance pass failed: {str(e)}")
            return []

    def _line_size_validation_pass(
        self,
        images_base64: List[str],
        reference_data: Dict = None,
        confirmed_drawing_number: str = '',
    ) -> Dict[str, Any]:
        """
        PASS 7: AI-Powered Line Size Validation & Recommendation Engine.

        Analyses every visible piping line on the drawing and checks whether its
        annotated nominal size is consistent with:
          1. Adjacent equipment nozzle sizes (pump, vessel, exchanger connections)
          2. Size continuity along a flow path (unexpected jumps without reducers)
          3. Process engineering velocity/flow expectations for the fluid type
          4. Reference line list (DesignIQ) if supplied
          5. Common P&ID sizing errors (e.g., over-sized utility drops, under-sized
             relief discharge headers)

        Returns a dict:
        {
          "issues": [...],                       # standard issue dicts (category: line_size)
          "line_size_recommendations": [...]     # richer recommendation objects
        }
        """
        try:
            import re as _re

            # ── 1. Parse sizes from OCR line numbers ──────────────────────────────
            # Format: SIZE"-FLUIDCODE-SEQ-SPEC   (e.g. 4"-HC-1001-CS150)
            parsed_lines: List[Dict] = []
            size_pattern = _re.compile(
                r'^([\d½¼¾]+(?:\.\d+)?)"?[-–]([\w]{1,6})[-–]([\d]{3,5})([-–][\w\d]+)?$',
                _re.IGNORECASE
            )
            for raw_ln in sorted(self.line_numbers or []):
                m = size_pattern.match(raw_ln.strip())
                if m:
                    parsed_lines.append({
                        'line_number': raw_ln,
                        'size_inch': m.group(1),
                        'fluid_code': m.group(2).upper(),
                        'sequence': m.group(3),
                        'pipe_class': (m.group(4) or '').lstrip('-–'),
                    })

            # ── 2. Build reference line list context (if DesignIQ data available) ─
            ref_linelist_ctx = ""
            if reference_data:
                for key, val in reference_data.items():
                    if 'line' in key.lower() and isinstance(val, (list, dict)):
                        import json as _json
                        ref_linelist_ctx = (
                            "\n\nREFERENCE LINE LIST (from DesignIQ / engineering data):\n"
                            + _json.dumps(val, indent=2)[:4000]
                        )
                        break

            # ── 3. Build prompt ───────────────────────────────────────────────────
            ocr_lines_str = '\n'.join(
                f"  {p['line_number']}  → size={p['size_inch']}\", fluid={p['fluid_code']}, class={p['pipe_class']}"
                for p in parsed_lines[:40]
            ) or "  None parsed from OCR (use visual scan)"

            system_msg = """You are a senior process engineer specialising in piping line sizing for oil & gas P&IDs.

Your ONLY task is to identify LINE SIZE ERRORS and produce AI-powered sizing recommendations.

═══════════════════════════════════════════════════════════════════
HOW TO SPOT A LINE SIZE ERROR (check each one visually):
═══════════════════════════════════════════════════════════════════
1. EQUIPMENT NOZZLE MISMATCH
   • Pipe labelled 8" connecting directly to equipment with a 4" nozzle without a reducer symbol indicates a sizing error.
   • Rule: process pipe ≤ equipment nozzle OR a concentric/eccentric reducer must be shown.

2. SIZE CONTINUITY / UNJUSTIFIED JUMP
   • A 3" branch suddenly widening to a 10" header with no flow-design reason.
   • Downstream of a control valve (which reduces ΔP) the line should NOT shrink unexpectedly – flag if it does.

3. VELOCITY-BASED CHECK (engineering estimate)
   Rough rules of thumb (use ONLY when a visible nozzle or line label gives direct evidence):
   • Gas / vapour lines:  typical velocity 15–30 m/s  → flag ONLY if a very small line feeds a large-bore nozzle
   • Liquid lines:        typical velocity  1–3  m/s  → flag ONLY if a very large line connects to a small nozzle
   • PSV discharge pipes: ONLY flag if the discharge pipe bore is VISIBLY smaller than the PSV outlet nozzle symbol
     shown on the drawing. Do NOT infer or calculate a 'recommended' size.

   IMPORTANT: Do NOT apply the following rules — they are engineering assumptions, not P&ID observable facts:
   ✕ DO NOT flag: pump suction line vs discharge line sizing comparison
   ✕ DO NOT recommend a specific pipe size based on flow calculations (no flow data on P&ID)
   ✕ DO NOT flag: utility line oversizing without visible evidence of a mismatch

═══════════════════════════════════════════════════════════════════
CRITICAL EXCLUSIONS — never flag these as line size issues:
═══════════════════════════════════════════════════════════════════
DRAWING NUMBERS (DOT-NOTATION) — NOT pipe sizes or line numbers:
  Numbers formatted with DOT separators such as  16.01.08.1678  or  22.03.12.4521
  are DRAWING REFERENCE NUMBERS printed in the title block, revision panel, or
  document index. They follow the pattern  NN.NN.NN.NNNN  (area.system.sheet.sequence).
  RULE: If a number you see on the drawing contains 3 or more dot-separated segments,
        it is a DRAWING NUMBER — do NOT report it as a line size anomaly.
  PROCESS: Before scanning for line sizes, locate the title block and READ OFF the
           drawing number. Record it mentally as ⛔ EXCLUDED and skip any reference
           to that number in your line-size findings.

═══════════════════════════════════════════════════════════════════
OUTPUT FORMAT — return ONLY valid JSON, no markdown fences:
═══════════════════════════════════════════════════════════════════
{
  "reasoning": "Brief explanation of your scan approach",
  "line_size_recommendations": [
    {
      "line_number": "exact line number as shown on drawing",
      "current_size_inch": "annotated size from drawing (number only, e.g. '8')",
      "recommended_size_inch": "AI-suggested correct size (e.g. '4') or 'Verify' if uncertain",
      "confidence": "High | Medium | Low",
      "check_type": "nozzle_mismatch | size_jump | velocity_estimate | ref_list_mismatch | flare_header",
      "engineering_basis": "brief engineering rule or standard applied",
      "reasoning": "specific explanation referencing visible elements on the drawing",
      "severity": "critical | major | minor | observation",
      "location_on_drawing": {
        "zone": "Top-Left|Top-Center|Top-Right|Middle-Left|Middle-Center|Middle-Right|Bottom-Left|Bottom-Center|Bottom-Right",
        "drawing_section": "process area description",
        "proximity_description": "near which equipment or junction",
        "visual_cues": "describe the exact position"
      }
    }
  ],
  "total_anomalies": 0
}

IMPORTANT:
- Only flag a line if you are VISUALLY CONFIDENT the size annotation is visible on the drawing.
- Do NOT invent line numbers. Use exactly what is printed on the drawing.
- If no anomaly is found, return an empty list and total_anomalies: 0.
- Expected to find 0-8 anomalies per typical P&ID sheet."""

            # Build the confirmed drawing number exclusion note for the user prompt
            _drw_excl_note = (
                f"\n⛔ CONFIRMED DRAWING NUMBER (EXCLUDE from all line-size checks): {confirmed_drawing_number}\n"
                f"   This is the document reference number from the title block — NOT a pipe size or line number.\n"
                f"   Do NOT flag it, mention it in findings, or reference it as a line number.\n"
                if confirmed_drawing_number else
                "\n⛔ DRAWING NUMBER NOTE: Locate the title block first. Any number with dot-separators\n"
                "   (e.g. 16.01.08.1678) is a DRAWING NUMBER — exclude it from all line-size findings.\n"
            )

            user_text = f"""Perform a LINE SIZE VALIDATION scan on this P&ID drawing.
{_drw_excl_note}
OCR-extracted line numbers and parsed sizes (first 40):
{ocr_lines_str}
{ref_linelist_ctx}

Steps:
1. READ the title block — identify and record the drawing number (dot-notation). EXCLUDE it.
2. Visually confirm each OCR line number on the drawing
3. For every piping line visible, check its annotated size against the five checks above
4. Flag every anomaly as a separate JSON entry in line_size_recommendations
5. Return ONLY valid JSON in the format specified

Focus especially on:
- Pump suction vs discharge sizing
- PSV inlet/outlet line sizing
- Lines that abruptly change size without a reducer symbol
- Any line where the size looks disproportionate to adjacent equipment nozzles
"""

            # Inject soft-coded evidence guidance (SOFT-CODED: pid_analysis_config.json → evidence_guidance)
            _ev_block7 = getattr(self, 'evidence_guidance_block', '')
            if _ev_block7:
                system_msg += '\n\n' + _ev_block7

            messages = [
                {"role": "system", "content": system_msg},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                    ] + [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{img}",
                                "detail": "high"
                            }
                        }
                        for img in images_base64
                    ]
                }
            ]

            print("[INFO] Calling OpenAI for line size validation (Pass 7)...")
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                max_tokens=8000,
                temperature=self.pass7_temperature,
                seed=42,
                timeout=240
            )

            if not response or not response.choices:
                print("[WARNING] Pass 7: OpenAI returned empty response")
                return {"issues": [], "line_size_recommendations": []}

            response_text = (response.choices[0].message.content or "").strip()
            print(f"[DEBUG PASS 7] len={len(response_text)} | preview={response_text[:120]}")

            # Parse the JSON response
            result = self._parse_analysis_response(response_text, 0)
            recommendations = result.get('line_size_recommendations', [])

            # Convert each recommendation to a standard issue entry for the main table
            issues = []
            for rec in recommendations:
                line_no = rec.get('line_number', 'UNKNOWN LINE')
                curr_sz = rec.get('current_size_inch', '?')
                rec_sz  = rec.get('recommended_size_inch', 'Verify')
                basis   = rec.get('engineering_basis', '')
                reason  = rec.get('reasoning', '')
                loc     = rec.get('location_on_drawing', {
                    'zone': 'Middle-Center',
                    'drawing_section': 'Piping',
                    'proximity_description': 'See line annotation',
                    'visual_cues': line_no
                })

                issues.append({
                    'pid_reference': line_no,
                    'issue_observed': (
                        f"Line size anomaly detected: annotated as {curr_sz}\", "
                        f"AI recommends {rec_sz}\". "
                        f"Check type: {rec.get('check_type', 'general')}. {reason[:200]}"
                    ),
                    'action_required': (
                        f"Verify line size against process datasheet and hydraulic calculations. "
                        f"Engineering basis: {basis}. "
                        f"If {curr_sz}\" is incorrect, update line number and piping isometric."
                    ),
                    'severity': rec.get('severity', 'major'),
                    'category': 'line_size',
                    'location_on_drawing': loc,
                })

            print(f"[INFO] Pass 7 (line size validation) found {len(recommendations)} anomalies")
            return {"issues": issues, "line_size_recommendations": recommendations}

        except Exception as e:
            print(f"[WARNING] Line size validation pass failed: {str(e)}")
            return {"issues": [], "line_size_recommendations": []}

    # =========================================================================
    # PASS 8 — SMART QC ENHANCEMENT (pure additive layer, no existing logic changed)
    # =========================================================================

    def _detect_near_duplicate_lines(self) -> List[Dict[str, Any]]:
        """
        Programmatically detect exact-duplicate and near-duplicate line numbers from OCR data.

        Near-duplicate definition: same size + fluid code + pipe specification suffix,
        with sequence numbers differing by ≤ 2.  These are high-risk for:
          - Construction-package confusion (wrong isometric pulled)
          - Material take-off (MTO) errors
          - Valve / instrument tagging mistakes in the field

        Returns a list of standard issue dicts, one per detected pair.
        Soft-coded: the near-duplicate threshold (default: 2) can be adjusted by changing
        NEAR_DUP_THRESHOLD below without touching any other logic.
        """
        import re as _re

        NEAR_DUP_THRESHOLD = getattr(self, 'near_dup_threshold', 2)  # soft-coded from pid_analysis_config.json

        # Extended line-number parser: covers both common formats
        # Format A: SIZE"-FLUID-SEQ-SPEC          e.g. 4"-HC-1001-CS150
        # Format B: SIZE"-FLUID-SEQ-SPEC-X-Y       e.g. 2"-D-6150-033842-X-N
        line_num_pattern = _re.compile(
            r'^([\d½¼¾]+(?:\.\d+)?)"?[-–]([A-Z]{1,6})[-–](\d{3,6})((?:[-–][\w\d]+)*)$',
            _re.IGNORECASE,
        )

        # Soft-coded: separator used to split pipe-class from trailing insulation/tracing code.
        # e.g. "013842-X-N" → pipe_class="013842", insulation="X-N"
        # e.g. "CS150"      → pipe_class="CS150",   insulation=""
        # This allows duplicate comparison on pipe_class only, so a line whose insulation
        # suffix is partially obscured by a revision cloud is still matched correctly.
        _SPEC_SEP = '-'

        parsed: List[Dict] = []
        for raw in sorted(self.line_numbers or []):
            m = line_num_pattern.match(raw.strip())
            if m:
                _sfx = m.group(4).upper().lstrip('-–')
                _sfx_parts = _sfx.split(_SPEC_SEP) if _sfx else []
                parsed.append({
                    'raw': raw,
                    'size': m.group(1),
                    'fluid': m.group(2).upper(),
                    'sequence': m.group(3),
                    'suffix': _sfx,                                                    # full spec+insulation
                    'pipe_class': _sfx_parts[0] if _sfx_parts else '',                # e.g. "013842" — used for duplicate comparison
                    'insulation': _SPEC_SEP.join(_sfx_parts[1:]) if len(_sfx_parts) > 1 else '',  # e.g. "X-N"
                })

        issues: List[Dict] = []
        seen_pairs: set = set()

        for i, a in enumerate(parsed):
            for b in parsed[i + 1:]:
                pair_key = tuple(sorted([a['raw'], b['raw']]))
                if pair_key in seen_pairs:
                    continue

                # Soft-coded: compare on size + fluid + PIPE CLASS only.
                # Insulation/tracing suffix (e.g. -X-N, -N, -H, -HH) is intentionally
                # excluded from the comparison because revision clouds on the drawing can
                # obscure the trailing suffix, causing OCR to read a truncated label.
                # pipe_class guard ensures we skip entries where the spec was not parsed.
                if (a['size'] == b['size'] and
                        a['fluid'] == b['fluid'] and
                        a['pipe_class'] == b['pipe_class'] and
                        a['pipe_class']):
                    try:
                        seq_a = int(a['sequence'])
                        seq_b = int(b['sequence'])
                        diff = abs(seq_a - seq_b)

                        if diff == 0:
                            severity = 'critical'
                            # Soft-coded: note when insulation suffix differs (possible cloud truncation)
                            _insul_note = ''
                            if a['insulation'] != b['insulation']:
                                _insul_note = (
                                    f" Insulation/tracing suffix differs "
                                    f"('{a['insulation'] or 'none'}' vs '{b['insulation'] or 'none'}') "
                                    f"— one label may be partially obscured by a revision cloud; "
                                    f"visually confirm the complete label on the drawing."
                                )
                            obs = (
                                f"Exact duplicate line number detected: '{a['raw']}' and '{b['raw']}' share "
                                f"identical size, fluid code, sequence AND pipe specification.{_insul_note} "
                                f"Duplicate line numbers violate piping data-integrity requirements and will "
                                f"cause errors in isometric numbering, MTO, and valve tagging."
                            )
                            action = (
                                "Remove one occurrence or reassign a unique sequential number. "
                                "Each process piping line must have a globally unique identifier per "
                                "the project line-numbering procedure."
                            )
                        elif diff <= NEAR_DUP_THRESHOLD:
                            severity = 'major'
                            # Soft-coded: note when insulation suffix differs (possible cloud truncation)
                            _insul_note = ''
                            if a['insulation'] != b['insulation']:
                                _insul_note = (
                                    f" Insulation/tracing suffix differs "
                                    f"('{a['insulation'] or 'none'}' vs '{b['insulation'] or 'none'}') "
                                    f"— one label may be partially obscured by a revision cloud."
                                )
                            obs = (
                                f"Near-duplicate line numbers detected: '{a['raw']}' and '{b['raw']}' share "
                                f"the same fluid code ({a['fluid']}), pipe class ({a['pipe_class']}), and nominal size ({a['size']}\"), "
                                f"with only a {diff}-digit sequence difference.{_insul_note} "
                                f"These are easily confused in construction documents, isometrics, MTO, and valve/instrument tagging."
                            )
                            action = (
                                "Verify both lines are genuine separate process streams (cross-check PFD/FEED data). "
                                "If the same line, consolidate and correct. "
                                "If separate, confirm against process flow data and ensure their distinct routing "
                                "is unambiguous on the drawing (unique from/to, equipment, and valve tags)."
                            )
                        else:
                            continue

                        seen_pairs.add(pair_key)
                        issues.append({
                            'pid_reference': f"{a['raw']} / {b['raw']}",
                            'issue_observed': obs,
                            'action_required': action,
                            'severity': severity,
                            'category': 'line_duplicate',
                            'location_on_drawing': {
                                'zone': 'Multiple',
                                'drawing_section': 'Piping / Line Routing',
                                'proximity_description': (
                                    f"Two lines in same fluid service ({a['fluid']}) with near-identical numbers"
                                ),
                                'visual_cues': (
                                    f"Search for line labels '{a['raw']}' and '{b['raw']}' on this drawing"
                                ),
                            },
                        })

                    except ValueError:
                        continue

        # ── Enhancement: same-line-identity with different SIZE or SPEC ──────────
        # Group parsed lines by (fluid_code, sequence_number).
        # The combination (fluid, sequence) is the unique identity of a piping line.
        # If the SAME identity appears with multiple sizes OR specs on this drawing,
        # it indicates a size/spec change that was not consistently applied.
        from collections import defaultdict as _dd
        by_identity: dict = _dd(list)
        for p in parsed:
            by_identity[(p['fluid'], p['sequence'])].append(p)

        for (_fluid, _seq), _entries in by_identity.items():
            if len(_entries) < 2:
                continue
            _sizes = [e['size'] for e in _entries]
            _specs = [e['suffix'] for e in _entries]
            _unique_sizes = sorted(set(_sizes))
            _unique_specs = sorted(set(_specs))

            # SIZE CONFLICT on same line identity
            if len(_unique_sizes) > 1:
                _raws = ' / '.join(e['raw'] for e in _entries)
                _pair_key = tuple(sorted(e['raw'] for e in _entries))
                if _pair_key not in seen_pairs:
                    seen_pairs.add(_pair_key)
                    issues.append({
                        'pid_reference': _raws,
                        'issue_observed': (
                            f"Line identity {_fluid}-{_seq} appears on this drawing with CONFLICTING NOMINAL SIZES: "
                            f"{', '.join(_unique_sizes)}\". "
                            f"Same fluid code and sequence number must carry a single consistent nominal size. "
                            f"This is a strong indicator that the pipe size was changed in a recent revision "
                            f"but not all line-number labels were updated."
                        ),
                        'action_required': (
                            f"Verify the correct nominal size for line {_fluid}-{_seq} against the PFD, "
                            f"process datasheet, and hydraulic calculation. "
                            f"Correct all inconsistent line-number labels on the drawing. "
                            f"If the change is intentional, ensure a reducer/expander fitting is shown at the transition."
                        ),
                        'severity': 'major',
                        'category': 'line_number_anomaly',
                        'location_on_drawing': {
                            'zone': 'Multiple',
                            'drawing_section': 'Piping / Line Number Labels',
                            'proximity_description': (
                                f"Search for all line-number annotations containing {_fluid}-{_seq} on this drawing"
                            ),
                            'visual_cues': f"Conflicting labels: {_raws}",
                        },
                    })

            # SPEC CONFLICT on same line identity (only if size is consistent to avoid double-report)
            if len(_unique_specs) > 1 and len(_unique_sizes) < 2:
                _raws = ' / '.join(e['raw'] for e in _entries)
                _pair_key = tuple(sorted(e['raw'] for e in _entries))
                if _pair_key not in seen_pairs:
                    seen_pairs.add(_pair_key)
                    # Soft-coded: detect cloud-truncation pattern —
                    # when the only difference between specs is that one is a prefix of the other
                    # (e.g. "013842" vs "013842-X-N"), it is very likely a revision cloud is obscuring
                    # the insulation suffix on one occurrence rather than a genuine spec change.
                    _pipe_classes = sorted(set(e['pipe_class'] for e in _entries))
                    _insulations  = sorted(set(e['insulation'] for e in _entries))
                    _is_cloud_truncation = (
                        len(_pipe_classes) == 1 and          # same pipe class
                        len(_insulations)  > 1 and           # but different insulation suffixes
                        any(ins == '' for ins in _insulations)  # at least one has no insulation (truncated)
                    )
                    if _is_cloud_truncation:
                        _full_label  = next((e['raw'] for e in _entries if e['insulation']), _raws)
                        _trunc_label = next((e['raw'] for e in _entries if not e['insulation']), _raws)
                        _obs = (
                            f"Possible cloud-truncated line number: '{_trunc_label}' appears to be a "
                            f"partially obscured version of '{_full_label}'. "
                            f"The line identity {_fluid}-{_seq} with pipe class {_pipe_classes[0]} occurs "
                            f"twice — once with the insulation/tracing suffix "
                            f"({', '.join(i for i in _insulations if i)}) and once without. "
                            f"A revision cloud on the drawing is likely covering the trailing suffix on one label, "
                            f"creating an apparent duplicate entry."
                        )
                        _action = (
                            f"Visually inspect all occurrences of line {_fluid}-{_seq} on the drawing. "
                            f"Confirm whether the truncated label '{_trunc_label}' is the same physical line as "
                            f"'{_full_label}' with its suffix obscured by a revision cloud, or a genuinely "
                            f"separate line. If it is the same line, update the label to show the complete "
                            f"designation including the insulation/tracing suffix."
                        )
                        _severity = 'critical'
                        _category = 'line_duplicate'
                    else:
                        _obs = (
                            f"Line identity {_fluid}-{_seq} appears on this drawing with CONFLICTING PIPE SPECIFICATIONS: "
                            f"{', '.join(_unique_specs)}. "
                            f"Same fluid code and sequence number must carry a single consistent pipe class. "
                            f"This indicates a spec/class change in a recent revision that was not consistently "
                            f"applied to all line-number labels, or an unmarked spec break."
                        )
                        _action = (
                            f"Verify the correct pipe class for line {_fluid}-{_seq} against the line list and "
                            f"piping class definition document. "
                            f"Correct all inconsistent spec annotations on the drawing. "
                            f"If a spec-break is intentional, add the required spec-break flange symbol and annotation."
                        )
                        _severity = 'major'
                        _category = 'spec_break'
                    issues.append({
                        'pid_reference': _raws,
                        'issue_observed': _obs,
                        'action_required': _action,
                        'severity': _severity,
                        'category': _category,
                        'location_on_drawing': {
                            'zone': 'Multiple',
                            'drawing_section': 'Piping / Line Number Labels',
                            'proximity_description': (
                                f"Search for all line-number annotations containing {_fluid}-{_seq} on this drawing"
                            ),
                            'visual_cues': f"Conflicting labels: {_raws}",
                        },
                    })

        return issues

    def _smart_qc_enhancement_pass(
        self,
        images_base64: List[str],
        reference_data: Dict,
        all_previous_issues: List[Dict],
    ) -> Dict[str, Any]:
        """
        PASS 8: Smart QC Enhancement — nine targeted specialist checks.

        This pass is PURELY ADDITIVE: it does not touch or replace any logic from
        Passes 1-7.  Each check is a self-contained block in the AI prompt, so new
        checks can be appended without changing the base prompt or any other pass.

          Check A — Duplicate / near-duplicate line number verification
                    (programmatic pre-scan + AI visual confirmation)
          Check B — Dynamic valve-size consistency
                    (AI extracts annotations, compares to nominal pipe bore,
                     applies project-agnostic oil & gas engineering rules)
          Check C — Deep NOTES & HOLDS cross-verification
                    (full-stack process-engineer perspective: reads every note/hold
                     verbatim and audits whether its requirement is implemented)
          Check D — Equipment TYPE designation validation
                    (flags TYPE 09A vs TYPE 01A style transposition errors,
                     conflicts with adjacent service/pipe-class context)
          Check E — Revision cloud & change indicator identification
                    (finds ALL revision clouds/delta markers, evaluates engineering
                     impact of each change: size/spec/type/fitting/line removal)
          Check F — Line number component integrity analysis
                    (SIZE-FLUID-SEQ-SPEC breakdown: size vs nozzle, spec vs adjacent
                     lines, seq anomalies — catches size/spec/seq-number changes)
          Check G — Missing reducer / expander at size transitions
                    (every pipe-size change point must have fitting symbol or explicit
                     equipment-nozzle exception — catches "expander removed" class bugs)
          Check H — Instrument function regression detection
                    (PI/FI/TI/LI where PIC/FIC/TIC/LIC expected; CV without controller;
                     catches downgrade from controller to indicator)
          Check I — Process line continuity & missing connection detection
                    (dead-end lines, orphaned nozzles, lines referenced in notes/tables
                     but absent from drawing — catches "line removed" class bugs)
        """
        try:
            # ── Enhancement A: programmatic near-duplicate line detection ─────────
            dup_issues = self._detect_near_duplicate_lines()
            if dup_issues:
                print(f"[INFO] Pass 8 — programmatic near-dup scan: {len(dup_issues)} pair(s) flagged")

            # ── Context: issues already reported (dedup guard for AI) ─────────────
            already_str = '\n'.join(
                f"  - [{i.get('pid_reference', '')}] {i.get('issue_observed', '')[:55]}"
                for i in all_previous_issues[:30]
            ) or '  None yet'

            # ── Context: near-dup pairs for AI visual confirmation ────────────────
            if dup_issues:
                dup_pairs_str = '\n'.join(f"  ⚠ {d['pid_reference']}" for d in dup_issues)
                dup_context = (
                    "PROGRAMMATICALLY DETECTED NEAR-DUPLICATE LINE NUMBERS:\n"
                    f"{dup_pairs_str}\n"
                    "→ For each pair: confirm BOTH labels appear on the drawing AND have distinct routing.\n"
                    "  If you spot additional near-duplicate pairs, report those too.\n"
                )
            else:
                dup_context = (
                    "Programmatic near-duplicate scan: no pairs found by pattern matching.\n"
                    "Visually scan the drawing for any line number labels that look nearly identical.\n"
                )

            # ── Context: OCR line numbers for valve-size comparison ───────────────
            ocr_lines_str = (
                '\n'.join(f"  {ln}" for ln in sorted(self.line_numbers)[:40])
                if self.line_numbers else "  None from OCR — rely on visual scan"
            )

            # ── Context: parsed line-number components (for Checks E, F) ─────────
            import re as _re_ctx
            _line_pat = _re_ctx.compile(
                r'^([\d½¼¾]+(?:\.\d+)?)["\u2019]?[-\u2013]([A-Z]{1,6})[-\u2013](\d{3,6})((?:[-\u2013][\w\d]+)*)$',
                _re_ctx.IGNORECASE,
            )
            _parsed_ctx = []
            for _ln in sorted(self.line_numbers)[:60]:
                _m = _line_pat.match(_ln.strip())
                if _m:
                    _parsed_ctx.append(
                        f"  {_ln:35s}  size={_m.group(1)}\", fluid={_m.group(2).upper()}, "
                        f"seq={_m.group(3)}, spec={_m.group(4).lstrip('-') or '(none)'}"
                    )
                else:
                    _parsed_ctx.append(f"  {_ln:35s}  (non-standard format)")
            line_parsed_str = (
                '\n'.join(_parsed_ctx) if _parsed_ctx else "  None parsed from OCR — rely on visual scan"
            )

            # ── Context: OCR notes/holds refs ─────────────────────────────────────
            if self.notes_references:
                notes_ctx = (
                    f"OCR-detected note/hold references ({len(self.notes_references)} total):\n" +
                    '\n'.join(f"  {n}" for n in sorted(self.notes_references))
                )
            else:
                notes_ctx = (
                    "OCR note/hold detection: none found by pattern matching — scan visually."
                )

            # ── System prompt ─────────────────────────────────────────────────────
            system_msg = """You are a highly experienced FULL-STACK PROCESS ENGINEER and P&ID REVISION AUDITOR
performing a targeted QC scan.  You specialize in NINE domains:
  A. Line-number uniqueness (piping data integrity)
  B. Valve-size vs pipe-size consistency (dynamic, any project standard)
  C. Notes and holds cross-compliance (every requirement checked against the drawing)
  D. Equipment type designation accuracy (TYPE codes, class designations)
  E. Revision cloud identification & change impact assessment
  F. Line number component integrity (size / spec / sequence consistency)
  G. Missing fitting detection at pipe size transitions
  H. Instrument function regression (indicator replacing controller / transmitter)
  I. Process line continuity & dead-end / missing-connection detection

CORE RULES (identical to all other passes — violations produce false positives):
- VISUAL CONFIRMATION MANDATORY — report ONLY what is visually confirmed on this drawing.
- PP-prefix connector numbers (NN-PP-NNN-NNNNN) are sheet connectors, NOT piping lines.
- FC/FO/FL already annotated on any valve symbol = fail-safe specified — do NOT re-flag.
- Indicators (FI/PI/TI/LI/PG/LG) do NOT need a control loop UNLESS a control valve is nearby.
- Local gauges (PG, LG) intentionally indicator-only — do NOT flag.
- Do NOT re-report issues from previous passes already listed in ISSUES ALREADY REPORTED.

MANDATORY JSON RESPONSE — return ONLY valid JSON, no markdown fences:
{
  "issues": [
    {
      "serial_number": 1,
      "pid_reference": "exact reference visible on drawing (tag / line number / note number)",
      "issue_observed": "specific description with exact values extracted from the drawing",
      "action_required": "clear corrective action",
      "evidence": "VISUAL: [what is drawn — symbol, tag, zone, connections]. GAP: [what is missing/inconsistent with other elements on this drawing]. DRAWING BASIS: [reference to other similar elements on THIS drawing — never cite external standards unless explicitly written on this drawing].",
      "severity": "critical|major|minor|observation",
      "category": "line_duplicate|valve_size|notes_compliance|holds_compliance|type_designation|revision_change|line_number_anomaly|spec_break|missing_fitting|instrument_downgrade|line_continuity|piping|valve|documentation",
      "location_on_drawing": {
        "zone": "Top-Left|Top-Center|Top-Right|Middle-Left|Middle-Center|Middle-Right|Bottom-Left|Bottom-Center|Bottom-Right",
        "drawing_section": "Process area / notes section / title block / equipment schedule",
        "proximity_description": "near which equipment, line, or symbol",
        "visual_cues": "exact position description"
      }
    }
  ],
  "total_issues": 0
}"""

            # ── User prompt — nine check blocks (softcoded, each independent) ─────
            user_text = f"""You are examining the P&ID engineering drawing image(s) attached to this message.
Analyze the VISUAL CONTENT of the attached drawing(s) to complete the nine checks below.

PASS 8 — SMART QC ENHANCEMENT SCAN (9 targeted checks)

{self._build_cag_context()}

ISSUES ALREADY REPORTED IN PREVIOUS PASSES (do NOT repeat these):
{already_str}

OCR-CONFIRMED LINE NUMBERS ON THIS DRAWING (first 40):
{ocr_lines_str}

PARSED LINE NUMBER COMPONENTS (size / fluid / sequence / spec) for Check E & F:
{line_parsed_str}

{notes_ctx}

{dup_context}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHECK A — DUPLICATE & NEAR-DUPLICATE LINE NUMBER DETECTION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Examine every visible line-number label on the drawing.
Rule: each process line MUST have a completely unique line number.

□ Are there any two lines with IDENTICAL line numbers on this drawing?
  → IDENTICAL = CRITICAL  (category: line_duplicate)
□ Near-duplicate line numbers (same fluid code, same pipe-class spec, sequence differs by 1–2)?
  → NEAR-DUP = MAJOR  (category: line_duplicate)
□ For any near-duplicate pair in the programmatic list above: visually confirm both labels
  exist on the drawing AND each has a distinct routing/from-to.
  → UNCONFIRMED DISTINCT ROUTING = MAJOR  (category: line_duplicate)

IMPORTANT — REVISION CLOUD TRUNCATION OF LINE NUMBER SUFFIX:
  Line number labels that sit inside or near a REVISION CLOUD may have their trailing
  insulation/tracing suffix (e.g. -X-N, -N, -H, -HH, -TW) partially or fully hidden by
  the cloud boundary or by clouded-out hatching.
  When comparing line numbers, treat the BASE IDENTITY (size-fluid-sequence-pipeclass) as
  the unique key — even if the suffix is NOT visible on one occurrence:
  □ Two labels with the SAME size-fluid-sequence-pipeclass but DIFFERENT or ABSENT trailing
    suffix → may be the SAME physical line with the cloud obscuring the suffix.
    → CLOUD-TRUNCATED DUPLICATE = CRITICAL  (category: line_duplicate)
    Report the full label and the truncated label.  Visually describe where the cloud sits
    relative to the line number text.

Industry context: Near-duplicate line numbers (e.g. 2"-D-6150-033842-X-N vs 2"-D-6152-033842-X-N)
cause downstream errors in isometric numbering, MTO, valve tagging, and construction packages.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHECK B — DYNAMIC VALVE SIZE CONSISTENCY (project-agnostic oil & gas standard)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Apply dynamic engineering judgement — works for ANY project standard.
Do NOT hardcode rules. Extract and compare what IS annotated on the drawing.

For EACH visible process line:
  Step 1 – Read the nominal pipe size from the line number label.
           (e.g. 4"-HC-1001-CS150  →  nominal size = 4")
  Step 2 – Read the annotated size (inch) of EVERY valve on that line.
           Valve sizes appear as: standalone "4"", bore note, or embedded in valve tag (e.g. 8"-MOV-XXX).
  Step 3 – Compare each valve bore annotation to the pipe nominal size.

RULE (dynamic — applies to all oil & gas P&IDs by default):
  • Inline valve bore MUST equal pipe nominal bore.
  • Exception: intentional reduced-bore globe/control valve WITH a concentric/eccentric reducer shown.
  • Exception: a pressure-reducing station with explicit design annotation.
  • A valve annotated at a DIFFERENT bore than adjacent pipe WITHOUT a reducer symbol = DISCREPANCY.

□ Any inline valve annotated at a different size than the pipe it sits on?
  → MISMATCH WITHOUT REDUCER = MAJOR  (category: valve_size)
□ Multiple different valve sizes found on the SAME process line without clear engineering justification?
  → UNEXPLAINED SIZE MIX = MAJOR  (category: valve_size)

REPORTING FORMAT for valve-size findings:
  pid_reference : "<LINE-NUMBER> / <VALVE-TAG if visible>"
  issue_observed: "Line annotated as X\" but inline valve shown/tagged as Y\" without reducer symbol.
                   All visible valve size annotations on this line: [list them all]."
  action_required: "Verify against valve datasheet and hydraulic calculation.
                    If Y\" is incorrect, re-annotate. Add reducer symbol if bore change is intentional."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHECK C — DEEP NOTES & HOLDS CROSS-VERIFICATION (full-stack process-engineer review)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Act as an experienced full-stack process engineer reviewing this drawing at IFC stage.
Read EVERY NOTE and HOLD on the drawing VERBATIM, then cross-check each one against:
  1. Symbols and annotations on the main drawing body
  2. Instrument tags and their configurations
  3. Piping line specifications and routing
  4. Equipment shown and its design conditions
  5. Surrounding process context (upstream/downstream equipment, fluid service)

For EACH NOTE visible on the drawing:
  □ Read the EXACT text of the note.
  □ Identify WHAT requirement the note imposes (material, construction, inspection, process, safety).
  □ Check if that requirement IS visually implemented on this drawing.
  □ NOT implemented → MAJOR finding with note number and specific gap  (category: notes_compliance)
  □ Partially implemented or ambiguous → MINOR finding  (category: notes_compliance)
  □ Future-scope item (FOR FUTURE USE, TBC, TO BE CONFIRMED) → MAJOR  (category: notes_compliance)
  □ Note number in title block / list NOT referenced at its location on the drawing body → MINOR  (category: notes_compliance)

For EACH HOLD visible on the drawing:
  □ Read the EXACT text of the hold.
  □ Check if the hold has been signed off / resolved (closure signature or annotation near it).
  □ OPEN (no resolution) → CRITICAL finding  (category: holds_compliance)
  □ Hold references a requirement not visible or legible → MAJOR  (category: holds_compliance)

Engineering checks (process-engineer perspective):
  □ Does any note reference a pipe class, material, or spec that conflicts with what is shown
    elsewhere on the drawing?  → INCONSISTENCY = MAJOR
  □ Do notes collectively describe a consistent process design, or do they contradict each other?
    → CONTRADICTION = MAJOR

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHECK D — EQUIPMENT TYPE DESIGNATION VALIDATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Equipment type designations (TYPE 01A, TYPE 09A, TYPE III, TRIM CLASS I, etc.) define
materials, valve trim, flange class, and construction methods.  Transposition errors
(e.g. TYPE 09A where TYPE 01A is expected) are common and have procurement consequences.

Scan for ALL type designation annotations on this drawing:
  - Equipment schedules or tables listing types / classes
  - Inline annotations like "TYPE 01A", "TYPE 09A", "CLASS II"
  - Valve trim type codes (e.g. TRIM CLASS I, TRIM TYPE A3)
  - Instrument or equipment type references in notes or title block

□ Are there TYPE annotations visible? List each one with its associated equipment/line.
□ Does any TYPE designation appear inconsistent with adjacent equipment of the same service
  (e.g. one item showing TYPE 09A while all similar nearby items show TYPE 01A)?
  → INCONSISTENT TYPE = MAJOR  (category: type_designation)
□ Does any TYPE designation appear to be a transposition error based on engineering context
  (service conditions, pressure class, fluid, temperature visible on the drawing)?
  → POSSIBLE TRANSPOSITION = MAJOR  (category: type_designation)
□ Is any TYPE designation cross-referenced to a legend, schedule, or standard visible on or
  referenced by the drawing?  → MISSING REFERENCE = MINOR  (category: type_designation)
□ Does any TYPE designation conflict with the pipe class, pressure rating, or service
  conditions annotated on the same line or equipment?
  → CONFLICT WITH SERVICE = MAJOR  (category: type_designation)

REPORTING FORMAT for TYPE designation findings:
  pid_reference : "Equipment tag / line number where TYPE annotation appears"
  issue_observed: "TYPE DESIGNATION reads 'TYPE 09A'. Adjacent equipment of the same
                   [service/fluid/pressure class] shows 'TYPE 01A'. Engineering context
                   [describe what you can see] suggests this may be a transposition error."
  action_required: "Cross-check against TYPE designation schedule / equipment spec. Correct if
                    transposition confirmed.  Ensure all equipment of same service uses consistent type."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHECK E — REVISION CLOUD & CHANGE INDICATOR IDENTIFICATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
P&ID revisions are visually marked with REVISION CLOUDS (irregular outline around changed
area), REVISION TRIANGLES (delta △ with revision letter/number), STRIKETHROUGH text showing
what was removed, or "REVISED REV X" labels. EVERY revision marker represents a deliberate
change that MUST be audited for engineering impact.

Step 1 — IDENTIFY all revision markers on this drawing:
  Look for: irregular cloud/bubble outlines surrounding an area; △ delta symbols with revision
  letters next to them; strikethrough text showing old values; circled revision numbers; any
  annotation containing "REV", "REVISED", "REVISION", or a revision letter in a triangle.

Step 2 — For EACH revision cloud / marker found, evaluate engineering impact:
  □ LINE SIZE CHANGED (line number says 4" but was previously 6", or vice versa):
      Is the new size consistent with connected equipment nozzles?
      Is there a missing reducer/expander now needed at the transition?
      → SIZE CHANGE IMPACT = MAJOR  (category: revision_change)
  □ PIPE SPEC / CLASS CHANGED (e.g., CS150 → CS300 or vice versa):
      Is the new spec consistent with the process design conditions?
      Are adjacent directly-connected lines updated to match?
      Is a spec-break marker added if spec changes mid-line?
      → SPEC CHANGE IMPACT = MAJOR  (category: revision_change)
  □ INSTRUMENT DOWNGRADED (PIC/FIC/TIC → PI/FI/TI, losing control function):
      Is the control function now missing from the loop?
      Is there still a control valve with no controller?
      → INSTRUMENT DOWNGRADE IMPACT = MAJOR  (category: instrument_downgrade)
  □ FITTING REMOVED (expander, reducer, strainer, check valve missing):
      Does removal leave a size mismatch or missing safety element?
      → MISSING FITTING IMPACT = MAJOR  (category: missing_fitting)
  □ LINE REMOVED (process pipe completely removed):
      Does this leave dead-end nozzles or disconnected equipment?
      Does any note or instrument still reference the removed line?
      → LINE REMOVAL IMPACT = MAJOR  (category: line_continuity)
  □ SEQUENCE NUMBER CHANGED (e.g., 1001 → 1012 in a line number):
      Does the old sequence still appear elsewhere creating a duplicate or gap?
      → SEQ CHANGE IMPACT = MAJOR  (category: line_number_anomaly)
  □ SAFETY ELEMENT CHANGED (PSV, ESD, SIS element modified):
      → SAFETY CHANGE = CRITICAL  (category: revision_change)

REPORTING FORMAT for revision-change findings:
  pid_reference : "Line/Tag/Equipment enclosed by revision cloud (Rev X if visible)"
  issue_observed: "REVISION CLOUD detected enclosing [exact description of element].
                   Apparent change: [describe what changed and the engineering implication]."
  action_required: "Verify revision against the Approved-for-Issue markup or change register.
                    Confirm the modified element complies with current process design intent,
                    line list, and all affected downstream documents."

IMPORTANT: If NO revision clouds are visible on this drawing, state "No revision clouds
found" as an observation entry (severity: observation). Do NOT invent revision markers.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHECK F — LINE NUMBER COMPONENT INTEGRITY ANALYSIS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Line numbers encode key engineering data: SIZE"-FLUID-SEQUENCE-SPEC (e.g. 4"-HC-1001-CS150).
Each component must be INTERNALLY CONSISTENT with what is drawn.

USE THE PARSED LINE NUMBER COMPONENTS TABLE ABOVE as context.

For EACH line number visible on the drawing:

□ SIZE COMPONENT CHECK:
  At the line's connection to equipment: visible nozzle ID should match the line size.
  At a direct weld/flange connection to another line (no reducer shown), BOTH line numbers
  should show the SAME nominal size.
  If the line number says 4" but the pipe visually connects to a 6" pipe without a
  reducer → SIZE VS NOZZLE MISMATCH = MAJOR  (category: line_number_anomaly)

□ SPEC COMPONENT CHECK:
  Lines of the same fluid service on the same continuous flow path SHOULD have the
  same pipe class, UNLESS a spec-break marker (double slash /) is shown at the transition.
  If two directly connected lines show DIFFERENT spec classes without a spec-break marker
  between them → UNMARKED SPEC CHANGE = MAJOR  (category: spec_break)
  If a line's spec class appears inconsistent with its fluid service and pressure context
  (e.g., CS150 annotated on a high-pressure stream) → SPEC vs SERVICE FLAG = MAJOR  (category: spec_break)

□ SEQUENCE NUMBER ANOMALY:
  Within the same fluid code, sequence numbers should be broadly sequential.
  Large unexplained GAPS in sequence (e.g., 1001, 1002, then jumps to 1050)
  may indicate lines were deleted without updating the drawing.
  → SEQUENCE GAP = MINOR  (category: line_number_anomaly)
  Any sequence number > 9600 → OUT OF ALLOTTED RANGE = MAJOR  (category: line_number_anomaly)

REPORTING FORMAT:
  pid_reference : "Full line number as annotated on drawing (e.g., 6\"-HC-1001-CS150)"
  issue_observed: "LINE NUMBER COMPONENT ANOMALY: [specific component] reads [value A] but
                   [drawing context] shows [value B]. [Exact engineering description]."
  action_required: "Verify correct [size/spec/sequence] against line list and PFD.
                    Correct the line number label or the drawing accordingly."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHECK G — MISSING REDUCER / EXPANDER AT SIZE TRANSITIONS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Every point where pipeline NOMINAL SIZE CHANGES must show the appropriate fitting symbol.
  • Concentric Reducer  (larger→smaller): standard inline size reduction
  • Eccentric Reducer   (larger→smaller): pump suction, sloping lines to prevent pooling
  • Expander/Increaser  (smaller→larger): velocity reduction after compressor, flow meter

A size transition WITHOUT a fitting symbol is a documentation gap (fitting will still be
included in construction but MTO and isometrics will miss it).

For EACH point on the drawing where two different nominal pipe sizes connect:
  □ Is a reducer or expander SYMBOL shown at the transition?
  □ If NO symbol: is the change occurring AT an equipment nozzle (natural transition point)?
  □ If neither a fitting symbol NOR an obvious equipment nozzle: → MISSING FITTING = MAJOR
            (category: missing_fitting)

ACCEPTABLE EXCEPTIONS (do NOT flag):
  ✓ Connection directly at vessel/exchanger/pump nozzle (nozzle absorbs size transition)
  ✓ Fitting label "RDR", "ECC RDR", or "EXP" visible near the transition point
  ✓ Control valve body (inherently reduced bore — annotated or standard practice)
  ✓ Restriction orifice (designed bore reduction)

REPORTING FORMAT:
  pid_reference : "LINE-A / LINE-B (or location description near equipment)"
  issue_observed: "SIZE TRANSITION from X\" (line [A]) to Y\" (line [B] or nozzle) at
                   [location] shows NO reducer or expander fitting symbol. The two lines
                   connect directly without an intermediate fitting."
  action_required: "Add the appropriate concentric or eccentric reducer / expander symbol
                    at the transition. Confirm fitting type against the 3D model or
                    isometric. Add nozzle size annotation if transition is at equipment."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHECK H — INSTRUMENT FUNCTION REGRESSION DETECTION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
A FUNCTION REGRESSION occurs when a CONTROL / TRANSMITTER instrument is replaced by a
simple INDICATOR, removing the feedback control capability from the loop.

ISA-5.1 FUNCTION HIERARCHY:
  Indicator     :  PI / FI / TI / LI / AI  — displays value, NO control action
  Transmitter   :  PT / FT / TT / LT / AT  — signal output (feeds controller/DCS)
  Controller    :  PIC / FIC / TIC / LIC   — closed-loop automatic control
  Final element :  PCV / FCV / TCV / LCV   — actuated valve responding to controller

A COMPLETE control loop requires: [Transmitter] → [Controller] → [Actuated Valve]

DETECTION RULES (apply with engineering judgement — DO NOT over-flag):
  □ Actuated CONTROL VALVE (FCV/PCV/TCV/LCV) visible on a line, but ONLY a plain
    INDICATOR (FI/PI/TI/LI) shown as the measurement device (no FT/PT/TT, no FIC/PIC/TIC
    controller bubble visible anywhere on the loop)
    → CONTROL LOOP MISSING TRANSMITTER/CONTROLLER = MAJOR  (category: instrument_downgrade)
  □ A FLOW measurement tag visible as FI (indicator) at a point where the process context
    strongly suggests active flow control (e.g., immediately upstream of an FCV)
    → FI WHERE FIC EXPECTED = MAJOR  (category: instrument_downgrade)
  □ A PRESSURE indicator (PI) on a header that clearly has a pressure-control valve (PCV)
    downstream, with no PIC or PT visible
    → PI WHERE PIC/PT EXPECTED = MAJOR  (category: instrument_downgrade)
  □ An instrument TAG NUMBER that matches a controller convention (e.g., FIC-301 on a
    bubble drawn as FI rather than FIC) — tag number says controller, symbol says indicator
    → TAG vs SYMBOL CONFLICT = MAJOR  (category: instrument_downgrade)

DO NOT FLAG:
  ✓ Local gauges (PG, LG) — always indicator-only by design
  ✓ PSV / PRV — self-acting, not a control loop instrument
  ✓ Indicators supplemental to existing FT/FIC loop (dual indication is acceptable)
  ✓ Indicators in utility lines where automatic control is designed to be absent
  ✓ XZ-prefix position switches (XZSH, XZSL) — not control loop instruments

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHECK I — PROCESS LINE CONTINUITY & MISSING CONNECTION DETECTION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Process lines must be continuous: start from a SOURCE (equipment nozzle, battery limit,
or sheet connector) and end at a DESTINATION (equipment nozzle, battery limit, sheet
connector, or intentional termination). DEAD-ENDS indicate removed connections.

DETECTION RULES:
  □ A line bearing a LINE NUMBER that originates from an equipment nozzle but ENDS
    without connecting to another equipment, battery-limit marker, or sheet connector
    → PROCESS LINE DEAD-END = MAJOR  (category: line_continuity)
  □ An equipment NOZZLE STUB visible (short pipe protruding from vessel/exchanger)
    with no line number, no connecting line shown, and no "FUTURE" annotation
    → ORPHAN NOZZLE = MAJOR  (category: line_continuity)
  □ A line ending at a BLIND FLANGE or cap without a "FUTURE" / "SPARE" / "TBC" label
    and without being inside an obvious maintenance isolation or test connection
    → UNRESOLVED BLIND TERMINATION = MINOR  (category: line_continuity)
  □ Two pieces of equipment that the process CLEARLY requires to be connected
    (e.g., pump discharge with no outlet pipe, compressor suction unconnected) but
    NO process line is shown between them
    → MISSING PROCESS CONNECTION = MAJOR  (category: line_continuity)

ACCEPTABLE TERMINATIONS (do NOT flag):
  ✓ Line ending at a TIE-IN point labelled with a tie-in tag number (e.g. TI-0001) or "TIE-IN"
  ✓ Line ending at battery limit (BL) or area limit (AL) marker
  ✓ Deliberately stubbed "FUTURE" or "FPSO" connections with labels
  ✓ Lines continuing on another sheet via PP sheet connector
  ✓ Utility stubs labelled "UB" or "UTILITY CONNECTION AS REQUIRED"

REPORTING FORMAT:
  pid_reference : "LINE NUMBER or EQUIPMENT TAG + NOZZLE (e.g., V-101 N3)"
  issue_observed: "DEAD-END / MISSING CONNECTION: Line [X] or nozzle at [location]
                   terminates at [description] without a connection destination or
                   proper termination annotation."
  action_required: "Verify process flow sheet for correct from/to. Add the missing
                    connection, or annotate with TIE-IN / FUTURE / BL marker."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FINAL INSTRUCTION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Report EACH deficiency as a SEPARATE JSON issue entry.
Do NOT group multiple problems into one finding.
Do NOT invent issues — every finding must be visually confirmed on the drawing.
Return ONLY valid JSON: {{"issues": [...], "total_issues": N}}"""

            # Inject soft-coded evidence guidance (SOFT-CODED: pid_analysis_config.json → evidence_guidance)
            _ev_block8 = getattr(self, 'evidence_guidance_block', '')
            if _ev_block8:
                system_msg += '\n\n' + _ev_block8

            messages = [
                {"role": "system", "content": system_msg},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                    ] + [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{img}",
                                "detail": "high",
                            },
                        }
                        for img in images_base64
                    ],
                },
            ]

            response_text = self._call_with_vision_fallback(
                messages,
                pass_label="PASS 8",
                primary_tokens=16000,
                fallback_tokens=12000,
                primary_timeout=360,
                fallback_timeout=300,
                temperature=self.pass8_temperature,
            )
            if not response_text:
                return {"issues": dup_issues, "total_issues": len(dup_issues)}
            result = self._parse_analysis_response(response_text, 0)
            ai_issues = result.get("issues", [])

            # Merge: programmatic near-dup findings (authoritative) + AI findings
            all_enhancement_issues = dup_issues + ai_issues
            print(
                f"[INFO] Pass 8 found {len(all_enhancement_issues)} issues "
                f"({len(dup_issues)} programmatic + {len(ai_issues)} AI)"
            )
            return {"issues": all_enhancement_issues, "total_issues": len(all_enhancement_issues)}

        except Exception as e:
            print(f"[WARNING] Pass 8 (Smart QC Enhancement) failed (non-critical): {str(e)}")
            # Graceful fallback: return programmatic near-dup findings even if AI call fails
            try:
                fallback_dups = self._detect_near_duplicate_lines()
                return {"issues": fallback_dups, "total_issues": len(fallback_dups)}
            except Exception:
                return {"issues": [], "total_issues": 0}

    def _merge_and_deduplicate(self, pass1: List, pass2: List, pass3: List, pass4: List = None) -> List[Dict[str, Any]]:
        """
        Merge findings from all passes and remove true duplicates using smart key normalization.

        Deduplication strategy (soft-coded, can extend without schema change):
          • Strip area prefix from tag  (13-FT-4580 → FT-4580)
          • Normalize category + first 5 words of issue_observed
          • Two issues are duplicates only when BOTH keys match, so different problems
            on the same tag (e.g. missing signal AND missing fail-safe) are KEPT distinct.
        """
        import re as _re

        def _norm_ref(ref: str) -> str:
            """Strip leading area prefix like '13-' from a tag string."""
            return _re.sub(r'^\d+[-]', '', (ref or '').strip().upper())

        def _norm_obs(obs: str) -> str:
            """First 6 significant words from issue_observed — enough to distinguish."""
            words = [w for w in _re.split(r'\W+', (obs or '').upper()) if len(w) > 2]
            return ' '.join(words[:6])

        all_passes = [p for p in (pass1, pass2, pass3, pass4) if p is not None]
        all_issues = []
        seen_keys: set = set()

        for issue_list in all_passes:
            for issue in (issue_list or []):
                ref  = _norm_ref(issue.get('pid_reference', ''))
                obs  = _norm_obs(issue.get('issue_observed', ''))
                cat  = (issue.get('category', '') or '').upper()[:12]
                key  = f"{ref}|{cat}|{obs}"

                if key not in seen_keys:
                    seen_keys.add(key)
                    all_issues.append(issue)

        # Second-pass dedup: same tag + same issue concept reported in different categories
        # (e.g. "PT-4501 — not connected to loop" appearing as both 'instrument' and 'control_loop')
        # Key: just ref + first 5 words of issue — ignore category differences
        seen_cross_cat: set = set()
        deduped: list = []
        for issue in all_issues:
            ref  = _norm_ref(issue.get('pid_reference', ''))
            obs  = _norm_obs(issue.get('issue_observed', ''))
            # Shorter key: ref + 5 words (no category) — catches cross-category duplicates
            cross_key = f"{ref}|{' '.join(obs.split()[:5])}"
            if cross_key not in seen_cross_cat:
                seen_cross_cat.add(cross_key)
                deduped.append(issue)

        all_issues = deduped

        # Third-pass filter: drop findings where pid_reference contains '[tag unreadable]'
        # (if we cannot read the tag, we cannot reliably assert there is a P&ID issue)
        all_issues = [
            iss for iss in all_issues
            if 'unreadable' not in (iss.get('pid_reference') or '').lower()
        ]

        # Fifth-pass filter: drop findings where pid_reference IS a drawing number.
        # Drawing numbers use dot-notation: NN.NN.NN.NNNN (e.g. 16.01.08.1678, 16.39.08.1603).
        # Any finding whose entire pid_reference matches this pattern is a false positive —
        # the AI confused the title-block drawing number for a line number or tag.
        import re as _re_drw
        _drw_num_re = _re_drw.compile(r'^\s*\d{2,3}\.\d{2,3}\.\d{2,3}\.\d{2,6}\s*$')
        before_drw = len(all_issues)
        all_issues = [
            iss for iss in all_issues
            if not _drw_num_re.match(iss.get('pid_reference', '') or '')
        ]
        if len(all_issues) < before_drw:
            print(f'[INFO] Dropped {before_drw - len(all_issues)} finding(s) with drawing numbers as pid_reference')

        # Fourth-pass filter: suppressed categories (soft-coded via pid_analysis_config.json)
        # Any category whose name contains a suppressed token is dropped entirely.
        _suppressed = [s.lower() for s in getattr(self, 'suppressed_categories', [])]
        if _suppressed:
            before = len(all_issues)
            all_issues = [
                iss for iss in all_issues
                if not any(token in (iss.get('category') or '').lower() for token in _suppressed)
            ]
            dropped = before - len(all_issues)
            if dropped:
                print(f'[INFO] Suppressed {dropped} finding(s) from categories: {_suppressed}')

        # Renumber serially
        for idx, issue in enumerate(all_issues, 1):
            issue['serial_number'] = idx

        print(f"[INFO] Merged {len(all_issues)} unique issues from all passes")
        return all_issues
    
    def _categorize_by_severity(self, issues: List[Dict]) -> Dict[str, List]:
        """Categorize issues by severity"""
        categorized = {
            'critical': [],
            'major': [],
            'minor': [],
            'observation': []
        }
        
        for issue in issues:
            severity = issue.get('severity', 'observation').lower()
            if severity in categorized:
                categorized[severity].append(issue)
        
        return categorized

    def _parse_analysis_response(self, response_text: str, tokens_used: int) -> Dict[str, Any]:
        """Parse OpenAI response and extract JSON — with aggressive JSON recovery"""
        try:
            # Strategy 1: Direct JSON parse (response IS a JSON object)
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            
            if json_start >= 0 and json_end > json_start:
                json_str = response_text[json_start:json_end]
                # Strategy 1a: Try direct parse
                try:
                    result = json.loads(json_str)
                    result['tokens_used'] = tokens_used
                    result['raw_response'] = response_text
                    return result
                except json.JSONDecodeError:
                    pass
                # Strategy 1b: Fix common AI JSON issues (trailing commas, truncated arrays)
                import re as _re
                cleaned = _re.sub(r',\s*([\]}])', r'\1', json_str)  # remove trailing commas
                # Truncation recovery: if json ends mid-array, close it
                try:
                    result = json.loads(cleaned)
                    result['tokens_used'] = tokens_used
                    result['raw_response'] = response_text
                    return result
                except json.JSONDecodeError:
                    pass
                # Strategy 1c: Extract just the issues array if full parse failed
                issues_match = _re.search(r'"issues"\s*:\s*(\[.*?\])', json_str, _re.DOTALL)
                if issues_match:
                    try:
                        issues_list = json.loads(issues_match.group(1))
                        return {
                            'issues': issues_list,
                            'total_issues': len(issues_list),
                            'confidence': 'Medium',
                            'tokens_used': tokens_used,
                            'raw_response': response_text,
                            'reasoning': 'Partial JSON recovered — issues array extracted successfully'
                        }
                    except json.JSONDecodeError:
                        pass

            # Strategy 2: Look for JSON inside markdown code block
            md_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
            if md_match:
                try:
                    result = json.loads(md_match.group(1))
                    result['tokens_used'] = tokens_used
                    result['raw_response'] = response_text
                    return result
                except json.JSONDecodeError:
                    pass

            # Strategy 3: Response is plain-text analysis — extract key findings as issues
            # This handles cases where AI returned prose instead of JSON
            print(f"[WARNING] Could not parse JSON from AI response (len={len(response_text)}), extracting from text")
            extracted_issues = self._extract_issues_from_text(response_text)
            return {
                'issues': extracted_issues,
                'total_issues': len(extracted_issues),
                'confidence': 'Low',
                'tokens_used': tokens_used,
                'raw_response': response_text,
                'reasoning': 'AI returned prose — key findings extracted heuristically'
            }
                
        except Exception as e:
            print(f"[ERROR] Response parsing failed: {str(e)}")
            return {
                'issues': [],
                'total_issues': 0,
                'confidence': 'Low',
                'tokens_used': tokens_used,
                'raw_response': response_text,
                'parsing_error': True
            }

    def _extract_issues_from_text(self, text: str) -> List[Dict[str, Any]]:
        """
        Fallback: extract numbered issues from AI prose response when JSON parsing fails.
        Looks for patterns like '1. ...', 'Issue 1:', 'CRITICAL:', 'MAJOR:', etc.
        """
        issues = []
        lines = text.split('\n')
        current_issue = None
        serial = 1

        # Severity keywords
        sev_map = {'critical': 'critical', 'major': 'major', 'minor': 'minor', 'observation': 'observation'}

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Detect numbered items: "1. ", "1) ", "Issue 1:", etc.
            num_match = re.match(r'^(\d+)[.)]\s+(.+)', stripped)
            sev_match = re.match(r'^(CRITICAL|MAJOR|MINOR|OBSERVATION)[:\s]+(.+)', stripped, re.IGNORECASE)

            if num_match or sev_match:
                if current_issue:
                    issues.append(current_issue)
                if num_match:
                    desc = num_match.group(2)
                elif sev_match:
                    desc = sev_match.group(2)
                    # Determine severity
                else:
                    desc = stripped

                sev = 'observation'
                for kw, sv in sev_map.items():
                    if kw in stripped.lower():
                        sev = sv
                        break

                current_issue = {
                    'serial_number': serial,
                    'pid_reference': 'DRAWING',
                    'issue_observed': desc[:300],
                    'action_required': 'Review and verify on drawing',
                    'severity': sev,
                    'category': 'documentation',
                    'location_on_drawing': {
                        'zone': 'Middle-Center',
                        'drawing_section': 'Drawing',
                        'proximity_description': 'See description',
                        'visual_cues': desc[:100]
                    }
                }
                serial += 1
            elif current_issue and len(stripped) > 10:
                # Continuation of current issue description
                current_issue['issue_observed'] = (current_issue['issue_observed'] + ' ' + stripped)[:400]

        if current_issue:
            issues.append(current_issue)

        return issues

    def _pdf_to_base64_images(self, pdf_file, dpi: int = None) -> List[str]:
        """
        Convert PDF pages to base64-encoded PNG images
        
        Args:
            pdf_file: Django FieldFile or file path
            dpi: Resolution for rendering (default: from pid_analysis_config.json → 300)
            
        Returns:
            List of base64-encoded image strings
        """
        if dpi is None:
            dpi = getattr(self, 'pdf_dpi', 300)
        images_base64 = []
        
        try:
            # Soft-coded approach: Handle both file paths and file objects (S3/Django FileField)
            if isinstance(pdf_file, str):
                # Local file path
                doc = fitz.open(pdf_file)
            else:
                # File object (from S3 or Django FileField) - read content into memory
                pdf_file.seek(0)  # Ensure we're at the start
                pdf_bytes = pdf_file.read()
                doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            
            # Convert each page
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                
                # Render to image
                mat = fitz.Matrix(dpi/72, dpi/72)
                pix = page.get_pixmap(matrix=mat)
                
                # Convert to PIL Image
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                
                # Convert to base64
                buffer = io.BytesIO()
                img.save(buffer, format="PNG", optimize=True)
                img_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
                images_base64.append(img_base64)
            
            doc.close()
            return images_base64
            
        except Exception as e:
            print(f"[ERROR] PDF conversion failed: {str(e)}")
            raise

    def generate_report_summary(self, issues: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Generate summary statistics from analysis issues
        
        Args:
            issues: List of identified issues
            
        Returns:
            Dictionary with summary statistics
        """
        if not issues:
            return {
                'total_issues': 0,
                'critical_count': 0,
                'major_count': 0,
                'minor_count': 0,
                'observation_count': 0,
                'approved_count': 0,
                'ignored_count': 0,
                'pending_count': 0,
                'categories': {}
            }
        
        # Count by severity
        severity_counts = {
            'critical': 0,
            'major': 0,
            'minor': 0,
            'observation': 0
        }
        
        # Count by status
        status_counts = {
            'approved': 0,
            'ignored': 0,
            'pending': 0
        }
        
        # Count by category
        categories = {}
        
        for issue in issues:
            severity = issue.get('severity', 'observation').lower()
            if severity in severity_counts:
                severity_counts[severity] += 1
            
            status = issue.get('status', 'pending').lower()
            if status in status_counts:
                status_counts[status] += 1
            
            category = issue.get('category', 'general')
            categories[category] = categories.get(category, 0) + 1
        
        return {
            'total_issues': len(issues),
            'critical_count': severity_counts['critical'],
            'major_count': severity_counts['major'],
            'minor_count': severity_counts['minor'],
            'observation_count': severity_counts['observation'],
            'approved_count': status_counts['approved'],
            'ignored_count': status_counts['ignored'],
            'pending_count': status_counts['pending'],
            'categories': categories
        }
    
    def _process_reference_documents(self, documents: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process reference documents and extract structured data
        SOFT-CODED: AI-powered intelligence extraction
        """
        return self.reference_processor.process_reference_documents(documents)
    
    def _vision_analysis_with_references(self, images_base64: List[str], reference_data: Dict[str, Any], layout_context_str: str = '') -> Dict[str, Any]:
        """
        Enhanced vision analysis with reference document cross-verification
        SOFT-CODED: Comprehensive compliance checking against uploaded references
        """
        # Build reference context for AI
        reference_context = self._build_reference_context(reference_data)
        
        # Use the existing vision analysis but with enhanced prompt and layout context
        return self._vision_analysis_pass(images_base64, reference_context, layout_context_str)
    
    def _build_reference_context(self, reference_data: Dict[str, Any]) -> str:
        """
        Build AI-readable context from reference documents
        Updated: Equipment List, Line List, Alarm & Trip Schedule, Legend/Symbol Sheet
        """
        if not reference_data:
            return ""
        
        context_parts = ["\n\n🔍 REFERENCE DOCUMENTS FOR CROSS-VERIFICATION:\n"]
        context_parts.append("🚨 CRITICAL INSTRUCTION:\n")
        context_parts.append("   ✓ PRIMARY DOCUMENT: Analyze the P&ID drawing - report ONLY issues visible on the P&ID\n")
        context_parts.append("   ✓ REFERENCE DOCUMENTS: Use these to VERIFY and CROSS-CHECK the P&ID for discrepancies\n")
        context_parts.append("   ✓ MANDATORY: Flag any mismatches between P&ID and reference documents\n")
        context_parts.append("   ✓ Both are EQUALLY IMPORTANT - P&ID is analyzed, references validate correctness\n\n")
        
        # 1. Equipment List - Structured equipment data
        if 'equipment_list' in reference_data:
            eq_list = reference_data['equipment_list']
            context_parts.append("\n📦 EQUIPMENT LIST PROVIDED:")
            context_parts.append("   VERIFY: Equipment tags on P&ID match Equipment List exactly")
            context_parts.append("   VERIFY: Equipment tagging parameters consistent with AGES-GL-08-005, Rev B4")
            context_parts.append("   VERIFY: Design pressures and temperatures match")
            context_parts.append("   VERIFY: Nozzles, manways, internal components shown as per datasheets")
            
            if 'equipment' in eq_list and eq_list['equipment']:
                context_parts.append(f"   - Equipment List contains {len(eq_list['equipment'])} equipment items:")
                psv_crosscheck_rows = []
                for eq in eq_list['equipment'][:15]:
                    design_p = eq.get('design_pressure', 'N/A')
                    design_t = eq.get('design_temp', 'N/A')
                    tag = eq.get('tag', 'N/A')
                    context_parts.append(f"     - {tag}: {eq.get('type', 'Unknown')} "
                                       f"(Design P: {design_p} / Design T: {design_t})")
                    # Build PSV cross-check: any PSV/PRV protecting this equipment must have set P ≤ design P
                    if design_p and design_p != 'N/A':
                        psv_crosscheck_rows.append(f"       • Equipment {tag}: design pressure = {design_p} → "
                                                   f"any PSV protecting {tag} MUST have set pressure ≤ {design_p}")
                context_parts.append("   -- CRITICAL: Each equipment MUST appear on P&ID with matching specifications")
                if psv_crosscheck_rows:
                    context_parts.append("\n   🔴 PSV vs EQUIPMENT DESIGN PRESSURE CROSS-CHECK TABLE:")
                    context_parts.append("   RULE: PSV set pressure MUST BE ≤ equipment design pressure (API 520/521)")
                    context_parts.append("   ACTION: For each PSV/PRV visible on drawing, locate protecting equipment and verify:")
                    for row in psv_crosscheck_rows:
                        context_parts.append(row)
                    context_parts.append("   → CRITICAL finding if PSV set pressure EXCEEDS any equipment design pressure above")
        
        # 2. Line List - Structured piping data (from uploaded PDF reference)
        if 'line_list' in reference_data:
            line_list = reference_data['line_list']
            context_parts.append("\n📋 LINE LIST PROVIDED (from uploaded reference):")
            self._append_line_list_checks(context_parts, line_list.get('lines', []))

        # 2b. Line List from DesignIQ (imported as JSON — already structured)
        if 'line_list_json' in reference_data:
            line_list_json = reference_data['line_list_json']
            context_parts.append("\n📋 LINE LIST PROVIDED (from DesignIQ line-list extractor):")
            # DesignIQ format: list of {item_tag, data: {service, size, rating, material, fluid, ...}}
            lines_raw = line_list_json if isinstance(line_list_json, list) else line_list_json.get('lines', [])
            # Normalise to the shared format [{line_number, size, spec, material, service, fluid}]
            normalised = []
            for item in lines_raw:
                if isinstance(item, dict):
                    data = item.get('data', item)  # DesignIQ wraps fields inside 'data'
                    tag = item.get('item_tag') or data.get('line_number') or data.get('tag', 'N/A')
                    normalised.append({
                        'line_number': tag,
                        'size': data.get('size', 'N/A'),
                        'spec': data.get('rating') or data.get('spec', 'N/A'),
                        'material': data.get('material', 'N/A'),
                        'service': data.get('service', 'N/A'),
                        'fluid': data.get('fluid', 'N/A'),
                        'from': data.get('from', ''),
                        'to': data.get('to', ''),
                        'design_temp': data.get('design_temp') or data.get('temperature', ''),
                        'insulation_class': data.get('insulation_class') or data.get('insulation', ''),
                        'ltcs_required': data.get('ltcs_required') or data.get('ltcs', ''),
                    })
            self._append_line_list_checks(context_parts, normalised)
        
        # 2c. Critical Stress Line List from DesignIQ (imported as JSON)
        if 'critical_stress_json' in reference_data:
            css_json = reference_data['critical_stress_json']
            context_parts.append("\n⚠️ CRITICAL STRESS LINE LIST PROVIDED (from DesignIQ):")
            context_parts.append("   VERIFY: Every line tagged as stress-critical on P&ID must appear in this list")
            context_parts.append("   VERIFY: Stress-critical lines on P&ID MUST show anchor points, guides, or expansion provisions")
            context_parts.append("   VERIFY: Lines in this list must have CSS/STRESS CRITICAL annotation visible on P&ID")
            lines_raw = css_json if isinstance(css_json, list) else css_json.get('lines', [])
            if lines_raw:
                context_parts.append(f"   - Critical Stress List contains {len(lines_raw)} lines:")
                for item in lines_raw[:20]:
                    data = item.get('data', item) if isinstance(item, dict) else {}
                    tag = item.get('item_tag') or data.get('line_number') or data.get('tag', 'N/A')
                    size = data.get('size', 'N/A')
                    service = data.get('service', '')
                    rating = data.get('rating', '')
                    material = data.get('material', '')
                    context_parts.append(f"     - {tag}: {size} {rating} {material} | service={service}")
                context_parts.append("   → CRITICAL finding if a CSS-listed line is visible on P&ID without stress annotations")
                context_parts.append("   → MAJOR finding if any CSS-listed line tag is missing from the P&ID entirely")

        # 2d. Critical Stress Line List from uploaded PDF reference
        if 'critical_stress_list' in reference_data:
            css_data = reference_data['critical_stress_list']
            context_parts.append("\n⚠️ CRITICAL STRESS LINE LIST PROVIDED (from uploaded reference):")
            context_parts.append("   VERIFY: All CSS-designated lines on P&ID must appear in this list")
            context_parts.append("   VERIFY: CSS lines must show anchor, guide, and expansion annotations on P&ID")
            lines_css = css_data.get('lines', [])
            if lines_css:
                context_parts.append(f"   - Critical Stress List contains {len(lines_css)} lines:")
                for ln in lines_css[:20]:
                    tag = ln.get('line_number', 'N/A')
                    size = ln.get('size', 'N/A')
                    service = ln.get('service', '')
                    context_parts.append(f"     - {tag}: {size} | service={service}")

        # 3. Alarm & Trip Schedule - Setpoints reference
        if 'alarm_trip_schedule' in reference_data:
            ats = reference_data['alarm_trip_schedule']
            context_parts.append("\n? ALARM & TRIP SCHEDULE PROVIDED:")
            context_parts.append("   VERIFY: Alarm setpoints on P&ID match Alarm & Trip Schedule")
            context_parts.append("   VERIFY: Trip setpoints match schedule")
            context_parts.append("   FORMAT: H=High Alarm, L=Low Alarm, HH=High-High Alarm/Trip, LL=Low-Low Alarm/Trip")
            context_parts.append("   NOTE: Engineering unit box for setpoint NOT required on P&ID")
            context_parts.append("   NOTE: Verification against Alarm & Trip Summary is NOT detailed on P&ID itself")
            
            if 'alarms_trips' in ats and ats['alarms_trips']:
                context_parts.append(f"   - Alarm & Trip Schedule contains {len(ats['alarms_trips'])} instruments:")
                for at in ats['alarms_trips'][:8]:  # Show first 8
                    alarms = []
                    if 'alarm_ll' in at: alarms.append(f"LL={at['alarm_ll']}")
                    if 'alarm_l' in at: alarms.append(f"L={at['alarm_l']}")
                    if 'alarm_h' in at: alarms.append(f"H={at['alarm_h']}")
                    if 'alarm_hh' in at: alarms.append(f"HH={at['alarm_hh']}")
                    alarm_str = ", ".join(alarms) if alarms else "No alarms"
                    context_parts.append(f"     - {at.get('tag', 'N/A')}: {alarm_str} {at.get('units', '')}")
                context_parts.append("   -- MAJOR: Verify setpoints shown on P&ID match schedule")
        
        # 4. Legend / Symbol Sheet - Symbol and spec interpretation
        if 'legend_symbols' in reference_data:
            legend = reference_data['legend_symbols']
            context_parts.append("\n? LEGEND / SYMBOL SHEET PROVIDED:")
            context_parts.append("   VERIFY: All symbols on P&ID are defined in legend")
            context_parts.append("   VERIFY: Symbol usage consistent with legend definitions")
            context_parts.append("   VERIFY: Abbreviations match legend")
            context_parts.append("   VERIFY: Pipe specifications follow legend coding")
            context_parts.append("   VERIFY: Line numbering format follows legend system")
            
            if 'abbreviations' in legend and legend['abbreviations']:
                context_parts.append("   - Symbol/Abbreviation Definitions:")
                for abbr, meaning in list(legend['abbreviations'].items())[:10]:
                    context_parts.append(f"     - {abbr} = {meaning}")
            
            if 'line_numbering' in legend:
                ln = legend['line_numbering']
                if 'format' in ln:
                    context_parts.append(f"   - Line Number Format: {ln['format']}")
                if 'example' in ln:
                    context_parts.append(f"   - Example: {ln['example']}")
                if 'serial_range' in ln:
                    context_parts.append(f"   - Serial Number Range: {ln['serial_range']}")
            
            if 'standards_references' in legend:
                context_parts.append("   - Standards Referenced:")
                for std in legend['standards_references'][:5]:
                    context_parts.append(f"     - {std}")
        
        # Add comprehensive verification checklist based on user requirements
        context_parts.append("\n\n-- MANDATORY P&ID QUALITY CHECKS (Fixed Checklist):\n")
        context_parts.append("---------------------------------------------------------------")
        
        context_parts.append("\n1-- DRAWING INFORMATION:")
        context_parts.append("   - Verify drawing number, revision number, project name, client name are correct")
        context_parts.append("   - Match against EDDR (Project Reference Document if provided)")
        
        context_parts.append("\n2-- CONNECTION VERIFICATION:")
        context_parts.append("   - Ensure all connections flagged as going to/from other P&IDs are correctly noted")
        context_parts.append("   - Match corresponding P&ID references")
        context_parts.append("   - Do NOT report issues about explicit receiving line numbers for connectors")
        context_parts.append("   - Do NOT report issues about node/nozzle ID for connectors")
        
        context_parts.append("\n3-- EQUIPMENT TAGGING:")
        context_parts.append("   - Verify equipment tagging details consistent with AGES-GL-08-005, Rev B4")
        context_parts.append("   - Confirm each equipment tagging parameter matches Equipment List")
        context_parts.append("   - Ensure nozzles, manways, internal components shown as per datasheets")
        context_parts.append("   - Do NOT report issues for equipment NOT part of provided P&ID")
        
        context_parts.append("\n4-- CONTROL VALVE MANIFOLD:")
        context_parts.append("   - Verify isolation and bypass valve sizes per AGES-GL-08-005, Rev B4, Table 7-2")
        context_parts.append("   - Reference: Table 7-2 Selection of block and bypass valve sizes in control valve manifold")
        context_parts.append("   - Do NOT report hook-up class selection issues")
        
        context_parts.append("\n5-- ACTUATED VALVES:")
        context_parts.append("   - Trace ALL actuated valves (control valves, shutdown valves, blowdown valves)")
        context_parts.append("   - Verify 'failsafe' position indicated (FC/FO/FL)")
        
        context_parts.append("\n6-- SPECTACLE BLINDS:")
        context_parts.append("   - Check position of all spectacle blinds")
        context_parts.append("   - Check function of line (always open or always closed in normal operation)")
        context_parts.append("   - Verify other valves are in same status as spectacle blind")
        context_parts.append("   - Avoid generic issues if specific PSV tag not identified on drawing")
        
        context_parts.append("\n7-- THERMOWELL CONNECTIONS:")
        context_parts.append("   - Check size of thermowell connections against AGES-PH-04-001, Rev-1, Table 14.1")
        context_parts.append("   - Format remark: 'TIT {tag} connection sizes indicated as X'' which are higher/lower than minimum specified size of Y'' as per AGES-PH-04-001, Rev-1, Table 14.1'")
        context_parts.append("   - Do NOT report connection size requirement between TIT and TI")
        
        context_parts.append("\n8-- LINE NUMBERS:")
        context_parts.append("   - Verify line serial numbers are correct")
        context_parts.append("   - Serial numbers beyond 9600 are INCORRECT: 'Line number {XXXXX} is beyond allotted range (up to 9600)'")
        context_parts.append("   - Identify discrepancies when compared to Line List")
        context_parts.append("   - Line size format: X'' (correct) NOT X\\'' (incorrect)")
        context_parts.append("   - Do NOT report issues for line numbers NOT part of provided P&ID")
        
        context_parts.append("\n9-- CHECK VALVES:")
        context_parts.append("   - Check direction of ALL check valves or non-return valves")
        context_parts.append("   - Check function of line and flow direction FIRST before assessing check valve direction")
        context_parts.append("   - Check valve direction should ALWAYS be in direction of flow")
        context_parts.append("   - Check valve symbol alone is enough - orientation arrows NOT required")
        context_parts.append("   - Do NOT report absence of check-valve orientation arrow as issue")
        
        context_parts.append("\n-- NOTES VERIFICATION:")
        context_parts.append("   - Check all notes on drawing")
        context_parts.append("   - If equipment/control valve/instrument/analyzer mentioned in note, verify note number placed near that tag")
        context_parts.append("   - Format: 'Note-X should be placed near equipment tag {TAG}'")
        
        context_parts.append("\n1--1-- ALARM & TRIP SETPOINTS:")
        context_parts.append("   - Check alarm settings against Alarm and Trip Schedule document")
        context_parts.append("   - Verify setpoints shown on P&ID match schedule")
        context_parts.append("   - High alarm (H), Low alarm (L), High-High trip (HH), Low-Low trip (LL)")
        context_parts.append("   - NOTE: Detailed verification against Alarm & Trip Summary NOT typically shown on P&ID itself")
        
        context_parts.append("\n1--2-- ORIFICE/RO SIZING:")
        context_parts.append("   - Check minimum straight-pipe spool downstream of every RO (minimum 10× pipe diameter)")
        context_parts.append("   - Check minimum straight-pipe spool upstream of every RO (minimum 5× pipe diameter)")
        context_parts.append("   - RO immediately followed by elbow/tee without spool = MAJOR finding")
        context_parts.append("   - Sizing note (bore diameter, Cd value) missing at RO tag = MINOR finding")
        
        context_parts.append("\n1--3-- STRAINERS:")
        context_parts.append("   - Verify strainers provided where required (e.g., pump suction)")
        
        context_parts.append("\n---------------------------------------------------------------")
        context_parts.append("\n⚠️ CRITICAL INSTRUCTIONS:")
        context_parts.append("   - Do NOT report legibility/readability issues")
        context_parts.append("   - Do NOT report call-out issues")
        context_parts.append("   - Do NOT report generic issues without specific location")
        context_parts.append("   - Do NOT report issues for equipment/lines NOT on provided P&ID")
        context_parts.append("   - Provide serial numbers for ALL issues")
        context_parts.append("   - Reference specific AGES clause/page/section/table number when citing standards")
        context_parts.append("   - Generate SPECIFIC mismatches/outputs, not generic observations")
        context_parts.append("   - Verify ALL information from P&ID image - do NOT return empty P&ID column")
        context_parts.append("\n✅ FOCUS: Find REAL engineering mistakes based on P&ID drawing!")
        context_parts.append("AVOID: Generic issues, legibility complaints, equipment not on drawing, false positives")
        
        return "\n".join(context_parts)

    def _append_line_list_checks(self, context_parts: List[str], lines: List[Dict]) -> None:
        """
        SOFT-CODED helper: Append specific per-line engineering compliance checks to context_parts.
        Works with both PDF-extracted line list data and DesignIQ JSON line list data.
        Checks: line number format, LTCS compliance, insulation annotation, pipe-class consistency.
        """
        context_parts.append("   VERIFY: All line numbers on P&ID exist in Line List")
        context_parts.append("   VERIFY: Line sizes match between P&ID and Line List")
        context_parts.append("   VERIFY: Pipe specification classes are consistent")
        context_parts.append("   VERIFY: From/To equipment tags match P&ID routing")
        context_parts.append("   VERIFY: Line serial numbers ≤ 9600 (beyond 9600 = INCORRECT)")

        if not lines:
            return

        context_parts.append(f"   - Line List contains {len(lines)} piping lines (first 20 shown):")
        ltcs_lines = []
        insulated_lines = []
        low_temp_lines = []

        for line in lines[:20]:
            ln = line.get('line_number', 'N/A')
            size = line.get('size', 'N/A')
            spec = line.get('spec') or line.get('rating', 'N/A')
            frm = line.get('from', '')
            to = line.get('to', '')
            service = line.get('service', '')
            fluid = line.get('fluid', '')
            material = line.get('material', '')
            design_temp = str(line.get('design_temp', ''))
            ins_class = str(line.get('insulation_class', ''))
            ltcs_req = str(line.get('ltcs_required', ''))
            routing = f"({frm} → {to})" if (frm or to) else ''
            context_parts.append(f"     - {ln}: {size} {spec} {material} {routing} | service={service} fluid={fluid}")

            # Flag lines needing LTCS validation
            is_ltcs_service = any(kw in (service + fluid + spec + material).upper()
                                  for kw in ('LNG', 'LPG', 'PROPANE', 'ETHYLENE', 'CRYO', 'METHANE', 'C3', 'C4',
                                             'LTCS', 'LT ', '-L ', 'CS3L', 'BNW', 'BNS'))
            try:
                temp_val = float(''.join(c for c in design_temp if c in '-0123456789.'))
                if temp_val < -29:
                    low_temp_lines.append((ln, design_temp))
            except (ValueError, TypeError):
                pass

            if ltcs_req.upper() in ('YES', 'TRUE', '1', 'REQUIRED', 'Y') or is_ltcs_service:
                ltcs_lines.append(ln)

            if ins_class and ins_class.upper() not in ('NO', 'NONE', 'N/A', '', 'FALSE', '0'):
                insulated_lines.append((ln, ins_class))

        context_parts.append("   -- MAJOR: Flag any P&ID line number NOT found in Line List above")
        context_parts.append("   -- MAJOR: Flag any line where P&ID pipe spec class DIFFERS from Line List spec")

        if ltcs_lines:
            context_parts.append(f"\n   🧊 LTCS COMPLIANCE CHECK ({len(ltcs_lines)} lines require LTCS or low-temp material):")
            for ln in ltcs_lines[:10]:
                context_parts.append(f"     → Line {ln}: verify LTCS pipe class on drawing (suffix L, CS3L, BNW, or SS)")
            context_parts.append("   → If standard CS class shown for any LTCS-required line = CRITICAL finding")

        if low_temp_lines:
            context_parts.append(f"\n   🌡️ LOW TEMPERATURE LINES (design temp < -29°C — LTCS mandatory):")
            for ln, t in low_temp_lines[:10]:
                context_parts.append(f"     → Line {ln}: design temp {t} — verify LTCS or SS pipe class on drawing")

        if insulated_lines:
            context_parts.append(f"\n   🔥 INSULATED LINES ({len(insulated_lines)} lines require insulation annotation):")
            for ln, ins in insulated_lines[:10]:
                context_parts.append(f"     → Line {ln}: insulation class '{ins}' — verify HOT/COLD/TRACE HEATED annotation on P&ID line")
            context_parts.append("   → Missing insulation annotation on above lines = MAJOR finding")




