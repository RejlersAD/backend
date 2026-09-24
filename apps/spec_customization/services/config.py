"""
Spec Customization — Soft-Coded Configuration
==============================================

EVERY knob lives here. Adjust values in this module to retune chunking,
AI engine priorities, regex patterns, cost guard rails, etc — no other file
should ever hold a literal magic number for this feature.
"""
from __future__ import annotations

import os


def _env_int(name: str, default: int, *, lo: int = 1, hi: int | None = None) -> int:
    """Read an integer env-var with safe bounds; falls back to `default` on
    missing / un-parseable values. Allows ops to retune chunking via
    environment without code changes (e.g. `SPEC_CHUNK_SIZE_PAGES=5`)."""
    raw = os.environ.get(name)
    if raw in (None, ''):
        return default
    try:
        v = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    if v < lo:
        return lo
    if hi is not None and v > hi:
        return hi
    return v


# ─────────────────────────────────────────────────────────────────────────────
# Master config dict — exported as `SPEC_EXTRACTION_CONFIG`
# ─────────────────────────────────────────────────────────────────────────────
SPEC_EXTRACTION_CONFIG = {
    # ── Chunking ────────────────────────────────────────────────────────
    # Chunk size MUST be large enough to capture complete component tables.
    # Most piping spec component tables span 3-10 pages. Increased from 5 to 10
    # to reduce table fragmentation and ensure comprehensive extraction.
    # Override via env var `SPEC_CHUNK_SIZE_PAGES` (1..50) without redeploying.
    "chunk_size_pages":      _env_int("SPEC_CHUNK_SIZE_PAGES", 10, lo=1, hi=50),
    "max_chunks_parallel":   _env_int("SPEC_MAX_CHUNKS_PARALLEL", 4, lo=1, hi=32),
    # Page overlap ensures component tables split across chunk boundaries are
    # captured in both chunks. Increased from 0 to 2 for table continuity.
    "page_overlap":          _env_int("SPEC_PAGE_OVERLAP", 2, lo=0, hi=10),

    # ── AI engine waterfall (first non-failed engine wins per chunk) ────
    # Supported: 'pymupdf_text', 'gemini_vision', 'openai_vision', 'tesseract'
    "ai_engines": [
        "pymupdf_text",        # free, instant; if rich text → may skip AI entirely
        "gemini_vision",       # primary AI
        "openai_vision",       # fallback AI
        "tesseract",           # last resort for fully scanned pages
    ],

    # ── Model identifiers ───────────────────────────────────────────────
    "gemini_model":            "gemini-2.0-flash",
    "openai_model":            "gpt-4o",
    # Increased from 8000 to 16000 to support comprehensive component extraction.
    # A typical piping spec has 50-200+ components; the AI needs sufficient tokens
    # to output complete JSON arrays without truncation.
    "openai_max_tokens":        16000,
    "openai_temperature":       0.1,
    "gemini_temperature":       0.1,

    # ── Claude models (BYOK support) ─────────────────────────────────────
    "claude_model":            "claude-3-5-sonnet-20241022",  # Default Claude model
    "claude_max_tokens":        16000,
    "claude_temperature":       0.1,

    # ── Cost guard rails ────────────────────────────────────────────────
    # If a page already has ≥ this many chars from PyMuPDF text-layer,
    # the pipeline MAY skip Vision AI -- but only after the text parser has
    # produced structured component rows. A header-only result is not an
    # extraction. Native PDFs such as ADNOC PMS files are text-rich while their
    # merged component tables still require layout-aware / Vision extraction.
    "skip_ai_if_text_chars_gte": 3000,
    "text_layer_min_components_for_direct_accept": 1,

    # Hard ceiling on how many pages may be sent to a Vision AI per job.
    # Once exceeded, remaining pages are processed by PyMuPDF/Tesseract only.
    "max_ai_pages_per_job":      500,

    # ── PDF rendering ───────────────────────────────────────────────────
    "render_dpi":            150,
    "max_image_size":       3072,
    "jpeg_quality":           85,

    # ── Retry + timeout ─────────────────────────────────────────────────
    "retry_max":             3,
    "retry_backoff_base":    2,        # exponential: 2s, 4s, 8s…
    "chunk_timeout_s":       300,      # per chunk Celery task soft-limit
    "job_total_timeout_s":   60 * 60,  # 60 min absolute job cap

    # ── Extraction quality knobs ────────────────────────────────────────
    "confidence_threshold":  0.35,   # below this, class flagged low-confidence
    "min_components_to_keep":  0,    # keep header-only detections (0 = no filter)

    # ── AI escalation thresholds (OpenAI = last-level AI tier) ──────────
    # Mirrors electrical_checklist's HANDWRITING_CONFIG escalation pattern:
    # Gemini (primary, cheaper) result is accepted immediately ONLY if it
    # clears both thresholds below; otherwise OpenAI is tried as the final
    # escalation and the better of the two results is kept (see
    # extraction_service.extract_chunk()'s WATERFALL MODE branch).
    # Override via env vars without redeploying.
    "escalate_if_components_below":       _env_int("SPEC_ESCALATE_IF_COMPONENTS_BELOW", 3, lo=0, hi=1000),
    # Average self-reported `confidence` (0.0-1.0) across all classes found
    # in the chunk; below this, escalate. Stored as float via env override.
    "escalate_if_avg_confidence_below":   float(os.environ.get("SPEC_ESCALATE_IF_AVG_CONF_BELOW", "0.5") or 0.5),

    # ── Regex patterns (soft-coded) ─────────────────────────────────────
    # Broad regex covers common Oil & Gas PMS header formats:
    #   PIPING SPEC: A  |  PIPING SPECIFICATION: A  |  CLASS 150-A
    #   PIPING MATERIAL SPECIFICATION A  |  P.M.S. A  |  PMS: A
    #   PIPING CLASS: A1  |  SPEC CODE: A1A  |  MATERIAL CLASS A
    #   LINE CLASS: A  |  PIPE CLASS A
    "piping_class_header_regex": (
        r'(?:'
        r'PIPING\s+(?:MATERIAL\s+)?SPEC(?:IFICATION)?'  # PIPING [MATERIAL] SPEC[IFICATION]
        r'|P\.?M\.?S\.?'                                 # PMS or P.M.S.
        r'|PIPE\s+CLASS'                                  # PIPE CLASS
        r'|PIPING\s+CLASS'                                # PIPING CLASS
        r'|LINE\s+CLASS'                                  # LINE CLASS
        r'|MATERIAL\s+CLASS'                              # MATERIAL CLASS
        r'|SPEC(?:IFICATION)?\s+CODE'                     # SPEC CODE
        r')'
        r'\s*[:\-]?\s*'
        r'(?P<code>[A-Z][A-Z0-9]{0,10})\b'
    ),
    # Recognises P/T rating tables (column-1 = pressure, col-2 = temperature)
    "pt_table_header_regex": (
        r'(?:PRESSURE.{0,5}TEMPERATURE|P\s*/\s*T\s+RATING|SERVICE\s+LIMITS)'
    ),
    # Service-block keywords
    "service_keywords": [
        "general process", "sweet fuel gas", "sour gas", "lp steam", "mp steam",
        "hp steam", "utility air", "instrument air", "nitrogen", "propane",
        "butane", "light distillate", "condensate", "produced water", "cooling water",
        "boiler feed water", "diesel", "lube oil",
    ],

    # ── Dedupe ──────────────────────────────────────────────────────────
    # TEMPORARILY DISABLED to force fresh extraction with new AI code (2026-07-15)
    # TODO: Re-enable after testing new extraction pipeline
    "dedupe_by_sha256":      False,

    # ── Progress band reserved for chunk loop (0-100) ───────────────────
    # 0-10  → upload + page count
    # 10-90 → chunk processing
    # 90-100 → merge + persist
    "chunk_progress_start":  10,
    "chunk_progress_end":    90,
}


# ─────────────────────────────────────────────────────────────────────────────
# Running header/footer stripping — soft-coded page-furniture removal
# ─────────────────────────────────────────────────────────────────────────────
# Legacy PMS PDFs (e.g. ADNOC LNG) repeat a title block on EVERY page
# ("ADNOC LNG PIPING SPECIFICATIONS", "Page 5 of 181", doc number, owner).
# Sent to the AI unfiltered, this furniture invites false class detections and
# pollutes the token budget. A line is treated as furniture when its
# whitespace-normalised form repeats on at least two distinct pages and
# `min_repeat_ratio` of the pages in
# a chunk, within the first `max_top_lines` / last `max_bottom_lines`
# non-empty lines of each page. Tune here only — no code changes elsewhere.
HEADER_FOOTER_STRIP_CONFIG = {
    "enabled":            True,
    "max_top_lines":      _env_int("SPEC_STRIP_TOP_LINES", 12, lo=1, hi=40),
    "max_bottom_lines":   _env_int("SPEC_STRIP_BOTTOM_LINES", 6, lo=1, hi=20),
    # Fraction of chunk pages a line must appear on to be considered furniture.
    "min_repeat_ratio":   float(os.environ.get("SPEC_STRIP_MIN_REPEAT_RATIO", "0.6") or 0.6),
    # Ignore very short lines (page numbers alone are caught by patterns below).
    "min_line_length":    _env_int("SPEC_STRIP_MIN_LINE_LEN", 8, lo=1, hi=200),
    # Always-strip patterns (case-insensitive), regardless of repeat ratio.
    "extra_line_patterns": [
        r'^page\s+\d+\s+(of\s+\d+)?$',
        r'unauthorized use prohibited',
        r'classification\s*:\s*\w+',
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# Cross-chunk component merge — soft-coded union strategy
# ─────────────────────────────────────────────────────────────────────────────
# A spec class averaging ~12 pages spans 10-page chunk boundaries. The legacy
# merge kept ONLY the chunk extraction with the most components, silently
# losing rows found by the other chunk. With `union_components_across_chunks`
# the merge instead unions components from ALL chunk extractions of the same
# class_code, deduplicated by a soft-coded signature.
COMPONENT_MERGE_CONFIG = {
    "union_components_across_chunks": True,
    # Fields forming the dedupe signature (order matters, keep stable).
    "signature_fields": [
        "component_type", "sub_type", "size_from", "size_to",
        "schedule_or_rating", "material_standard", "description",
    ],
    # Also union these class-level list fields across chunks.
    "union_list_fields": ["service_list"],
    # PT table rows dedupe by (pressure, temperature) rounded to 3 decimals.
    "union_pt_table": True,
}


# ─────────────────────────────────────────────────────────────────────────────
# ASME validation — soft-coded cross-check against valve_standards DB
# ─────────────────────────────────────────────────────────────────────────────
# Validates an extracted PipingClass PT table against ASME pressure-temperature
# ratings (valve_standards app). Advisory overlay: never blocks, never mutates
# extraction data. Tune here only.
ASME_VALIDATION_CONFIG = {
    "enabled": True,
    # Reference standard + rating table section to validate against.
    "standard_code":  "ASME_B16_34",
    "class_section":  "A",          # 'A' = Standard class, 'B' = Special class
    # Allowed overage: spec pressure may exceed the ASME table value by this
    # many percent and still pass. Default 1.5% absorbs edition drift — many
    # legacy specs carry ASME B16.5-2003 (or earlier) flange rating numbers
    # (19.7 bar @ 38 °C) while the reference DB holds the current-edition
    # 19.6 bar. Set to 0 for strict current-edition comparison.
    "tolerance_pct":  float(os.environ.get("SPEC_ASME_TOLERANCE_PCT", "1.5") or 1.5),
    # A PT row counts as an exact table hit when |t_row - t_spec| <= this (°C).
    "temp_exact_tolerance_c": 0.51,
    # 2-point linear interpolation between bracketing table temperatures
    # (ASME B16.34 para 2.1(f)).
    "interpolation_enabled": True,
    # Max distinct component material_standard strings tried when resolving
    # the class's MaterialGroup (most-frequent first).
    "max_material_candidates": 3,
    # Product-form hint per component_type (narrows MaterialGroupSpec search).
    "component_product_form_map": {
        "valve":   "casting",
        "flange":  "forging",
        "fitting": "",
        "pipe":    "tubular",
        "gasket":  "",
        "bolt":    "bar",
    },
    # Status labels surfaced in the UI badge (soft-coded wording).
    "status_labels": {
        "pass":    "Within ASME B16.34 rating",
        "fail":    "Exceeds ASME B16.34 rating",
        "skipped_no_class":    "No ASME class parsed",
        "skipped_no_material": "Material group not resolved",
        "skipped_no_pt":       "No PT data to validate",
        "disabled":            "ASME validation disabled",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# NPD Format Configuration — Display Format for Size Columns
# ─────────────────────────────────────────────────────────────────────────────
NPD_FORMAT_CONFIG = {
    # NPD display format for FirstSizeFrom/FirstSizeTo columns in Excel export
    # Options:
    #   'decimal'  → 0.5, 0.75, 1.0, 1.25, 1.5, 2.0 (preferred for data processing)
    #   'fraction' → 1/2, 3/4, 1, 1-1/4, 1-1/2, 2 (industry-standard piping notation)
    # 
    # User requirement: "convert format '1/2, 3/4, 1, 1-1/4, 1-1/2' to decimal value
    # example '0.5, 0.75, 1.0, 1.5'" — using decimal format for better Excel visibility
    "npd_display_format": "decimal",  # 'decimal' or 'fraction'
    
    # When decimal format is selected, control decimal precision
    # (0 = whole numbers only, 2 = two decimal places for all sizes)
    "decimal_precision": None,  # None = smart (whole numbers as int, fractional as-is)
    
    # Force decimal places for whole numbers (e.g., 1 → 1.0, 2 → 2.0)
    "force_decimal_notation": False,
}


# ─────────────────────────────────────────────────────────────────────────────
# Size Expansion Configuration — Intelligent Range Detection & Expansion
# ─────────────────────────────────────────────────────────────────────────────
SIZE_EXPANSION_CONFIG = {
    # Enable automatic expansion of size range patterns to individual rows
    "enable_size_expansion": True,
    
    # Enable expansion for ALL size ranges (not just "below" patterns)
    # When True: "1/2" to 1-1/2" → expands to [0.5, 0.75, 1.0, 1.25, 1.5]
    # When False: only "1.5 & Below" patterns trigger expansion
    "expand_all_ranges": True,
    
    # Threshold: if size_from or size_to contains a size ≤ this value,
    # expand to include all standard small sizes
    "small_size_threshold": 1.5,
    
    # Standard small sizes to include when expansion is triggered
    # (calibrated against ADNOC LNG / ARAMCO specs — common for small-bore piping)
    "small_size_ladder": [0.5, 0.75, 1.0, 1.25, 1.5],
    
    # Medium sizes (1.5" to 6") — expanded with 0.25" increments
    "medium_size_threshold": 6.0,
    "medium_size_ladder": [2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0],
    
    # Regex patterns that trigger size expansion (case-insensitive)
    # Matches: "1.5 & Below", "1½ & Below", "1-1/2 & Below", "1.5 and below",
    # "thru", "to", "up to", "≤", etc.
    "range_pattern_regexes": [
        r'(?:&|and)\s*below',           # "1.5 & Below"
        r'\bthru\b',                     # "1/2" thru 1-1/2""
        r'\bthrough\b',                  # "1/2" through 1-1/2""
        r'\bto\b',                       # "1/2" to 1-1/2""
        r'up\s+to',                      # "up to 1-1/2""
        r'≤|<=',                         # "≤ 1.5""
        r'\band\s+smaller\b',            # "1.5 and smaller"
        r'\band\s+less\b',               # "1.5 and less"
    ],
    
    # When a range pattern is detected, keep the original row AND generate
    # individual rows for each sub-size (so SmartPlant 3D gets explicit size entries)
    "duplicate_expanded_rows": True,
    
    # Log expansion actions for audit trail
    "log_expansions": True,
}


# ─────────────────────────────────────────────────────────────────────────────
# Custom Size Range Configuration — User-Defined Range Entries
# ─────────────────────────────────────────────────────────────────────────────
# For PipingCommodityFilter sheet: generate additional entries with custom
# from-to size ranges. These are user-defined bins for better size filtering
# granularity in SmartPlant 3D material selection.
# 
# User requirement: "whenever you find 1.5" (11/2), introduce records ranging from
# 0.25 to 0.5, 0.5 to 0.75, 0.75 to 1.0, 1.0 to 1.25, 1.25 to 1.50"
# ─────────────────────────────────────────────────────────────────────────────
CUSTOM_SIZE_RANGE_CONFIG = {
    # Enable generation of custom size range entries
    "enable_custom_ranges": True,
    
    # Trigger NPD: When this size is encountered, generate the custom ranges below
    # (11/2" = 1.5 inches nominal pipe diameter)
    "trigger_size": 1.5,
    
    # Custom size ranges to generate (from, to) pairs in inches
    # These create granular filtering entries for SmartPlant commodity selection
    "custom_ranges": [
        {"from": 0.25, "to": 0.5,  "label": "1/4\" to 1/2\""},
        {"from": 0.5,  "to": 0.75, "label": "1/2\" to 3/4\""},
        {"from": 0.75, "to": 1.0,  "label": "3/4\" to 1\""},
        {"from": 1.0,  "to": 1.25, "label": "1\" to 1-1/4\""},
        {"from": 1.25, "to": 1.5,  "label": "1-1/4\" to 1-1/2\""},
    ],
    
    # Include full-range entry (0.25 to trigger_size) in addition to sub-ranges
    "include_full_range": True,
    
    # Log when custom ranges are generated
    "log_custom_ranges": True,
}


# ─────────────────────────────────────────────────────────────────────────────
# Component Type Detection — Enhanced AI Extraction Guidance
# ─────────────────────────────────────────────────────────────────────────────
COMPONENT_TYPE_DETECTION_CONFIG = {
    # Enable comprehensive component type extraction
    "enable_enhanced_detection": True,
    
    # Critical component types that MUST be extracted (used to validate extraction quality)
    "required_component_types": [
        "pipe",
        "fitting",
        "flange",
        "valve",
        "gasket",
        "bolt",
    ],
    
    # Specific component sub-types to explicitly request from AI
    # (added to prompt to improve extraction recall)
    "priority_subtypes": {
        "valve": [
            "GATE VALVE",
            "GLOBE VALVE",
            "CHECK VALVE",
            "BALL VALVE",
            "PLUG VALVE",
            "BUTTERFLY VALVE",
            "NEEDLE VALVE",
            "VENT & DRAIN VALVE",
            "VENT AND DRAIN VALVE",
            "DRAIN VALVE",
            "BLOWDOWN VALVE",
        ],
        "fitting": [
            "90° ELBOW",
            "45° ELBOW",
            "TEE",
            "REDUCER",
            "CAP",
            "WELDOLET",
            "SOCKOLET",
            "THREADOLET",
            "ELBOLET",
            "COUPLING",
            "NIPPLE",
            "UNION",
            "SWAGE",
        ],
        "flange": [
            "WELD NECK FLANGE",
            "BLIND FLANGE",
            "SLIP-ON FLANGE",
            "THREADED FLANGE",
            "LAP JOINT FLANGE",
            "SOCKET WELD FLANGE",
            "FLANGES (GEN.)",
            "FLANGES GENERAL",
        ],
        "gasket": [
            "SPIRAL WOUND GASKET",
            "RING JOINT GASKET",
            "FLAT GASKET",
            "GASKETS",
        ],
        "bolt": [
            "STUD BOLT",
            "MACHINE BOLT",
            "BOLTS",
        ],
    },
    
    # Minimum components per class to consider extraction successful
    "min_components_warning_threshold": 10,
}


# ─────────────────────────────────────────────────────────────────────────────
# AI model pricing (USD per 1M tokens) — for cost estimation & billing transparency
# ─────────────────────────────────────────────────────────────────────────────
# NOTE: These are approximate rates as of 2026-07; verify against current vendor pricing.
# Override in production via environment variables if needed.
GEMINI_PRICING_PER_1M_TOKENS = {
    "input":  float(os.environ.get("GEMINI_INPUT_PRICE_PER_1M", "0.15") or 0.15),    # flash model ~$0.15/1M input
    "output": float(os.environ.get("GEMINI_OUTPUT_PRICE_PER_1M", "0.60") or 0.60),   # flash model ~$0.60/1M output
}

OPENAI_PRICING_PER_1M_TOKENS = {
    "input":  float(os.environ.get("OPENAI_INPUT_PRICE_PER_1M", "2.50") or 2.50),    # gpt-4o ~$2.50/1M input
    "output": float(os.environ.get("OPENAI_OUTPUT_PRICE_PER_1M", "10.00") or 10.00), # gpt-4o ~$10.00/1M output
}

CLAUDE_PRICING_PER_1M_TOKENS = {
    "input":  float(os.environ.get("CLAUDE_INPUT_PRICE_PER_1M", "3.00") or 3.00),    # claude-3-5-sonnet ~$3.00/1M input
    "output": float(os.environ.get("CLAUDE_OUTPUT_PRICE_PER_1M", "15.00") or 15.00), # claude-3-5-sonnet ~$15.00/1M output
}


# ─────────────────────────────────────────────────────────────────────────────
# BYOK (Bring Your Own Key) Configuration — Soft-coded provider & model options
# ─────────────────────────────────────────────────────────────────────────────
BYOK_CONFIG = {
    "enabled": True,  # Master switch for BYOK feature
    
    # Supported AI providers for BYOK
    "supported_providers": ["openai", "claude"],
    
    # OpenAI models available for BYOK (soft-coded list)
    "openai_models": [
        {"id": "gpt-4o", "label": "GPT-4o (Latest)", "description": "Most capable, best for complex specs", "recommended": True},
        {"id": "gpt-4o-mini", "label": "GPT-4o Mini", "description": "Faster, more cost-effective"},
        {"id": "gpt-4-turbo", "label": "GPT-4 Turbo", "description": "Previous generation, still powerful"},
    ],
    
    # Claude models available for BYOK (soft-coded list)
    "claude_models": [
        {"id": "claude-3-5-sonnet-20241022", "label": "Claude 3.5 Sonnet (Latest)", "description": "Best balance of speed and accuracy", "recommended": True},
        {"id": "claude-3-5-haiku-20241022", "label": "Claude 3.5 Haiku", "description": "Fastest, most cost-effective"},
        {"id": "claude-3-opus-20240229", "label": "Claude 3 Opus", "description": "Most capable, highest quality"},
        {"id": "claude-3-sonnet-20240229", "label": "Claude 3 Sonnet", "description": "Previous generation balanced model"},
    ],
    
    # Default models per provider
    "default_models": {
        "openai": "gpt-4o",
        "claude": "claude-3-5-sonnet-20241022",
    },
    
    # API key validation patterns (soft-coded regex)
    "api_key_patterns": {
        "openai": r"^sk-[A-Za-z0-9_\-]{18,}$",  # OpenAI: sk-...
        "claude": r"^sk-ant-[A-Za-z0-9_\-]{20,}$",  # Claude: sk-ant-...
    },
    
    # Security: maximum API key storage duration (seconds)
    # Keys are stored temporarily during extraction and wiped after completion
    "api_key_retention_seconds": 3600,  # 1 hour max
    
    # Display labels for UI
    "provider_labels": {
        "openai": "Your OpenAI API Key",
        "claude": "Your Claude API Key (Anthropic)",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Cache key templates (Redis) — keep aligned with Celery tasks
# ─────────────────────────────────────────────────────────────────────────────
PROGRESS_CACHE_KEY_TPL  = "paper_spec_progress_{job_id}"
PARTIAL_CACHE_KEY_TPL   = "paper_spec_partial_{job_id}"
PROGRESS_CACHE_TIMEOUT  = 3600
