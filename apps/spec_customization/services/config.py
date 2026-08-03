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

    # ── Cost guard rails ────────────────────────────────────────────────
    # If a page already has ≥ this many chars from PyMuPDF text-layer,
    # do NOT send it to Vision AI — saves $$ on bulk PDFs.
    # IMPORTANT: only skip AI if the text-layer regex already found classes.
    # A page may be text-rich yet use a non-standard header — AI must still run.
    # Raised from 800 to 3000 so only clearly data-dense text pages skip AI.
    "skip_ai_if_text_chars_gte": 3000,

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


# ─────────────────────────────────────────────────────────────────────────────
# Cache key templates (Redis) — keep aligned with Celery tasks
# ─────────────────────────────────────────────────────────────────────────────
PROGRESS_CACHE_KEY_TPL  = "paper_spec_progress_{job_id}"
PARTIAL_CACHE_KEY_TPL   = "paper_spec_partial_{job_id}"
PROGRESS_CACHE_TIMEOUT  = 3600
