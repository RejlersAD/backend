"""
SOFT-CODED: 6-Column Checklist Template Configuration (v2)

Mirror of frontend/src/config/electricalChecklistTemplate.config.js
Structure:
  - 6 columns: field_name | site_value | remarks | need_list | query | company_reply
  - 15 sections, 71 fields
  - Field IDs are FLAT and MUST match the frontend TEMPLATE_SECTIONS field IDs 1:1

Any change to a field ID here must be mirrored in the frontend config, and vice versa.
The AI extraction prompt is generated dynamically from this config, so adding a new
section/field automatically enables extraction for it — no code changes required.
"""

# ─────────────────────────────────────────────────────────────────────────────
# 6-Column Template Structure
# ─────────────────────────────────────────────────────────────────────────────

TEMPLATE_V2_COLUMNS = [
    {"key": "field_name",    "label": "General Information", "editable": False},
    {"key": "site_value",    "label": "To be fill at Site",  "editable": True},
    {"key": "remarks",       "label": "Remarks",             "editable": True},
    {"key": "need_list",     "label": "Need List",           "editable": True},
    {"key": "query",         "label": "Query",               "editable": True},
    {"key": "company_reply", "label": "Company Reply",       "editable": True},
]

# Column keys the extractor is allowed to populate from a source document.
# `field_name` is fixed (label), `company_reply` is filled by the company later.
EXTRACTABLE_COLUMNS = ["site_value", "remarks", "need_list", "query"]

# ─────────────────────────────────────────────────────────────────────────────
# 15 Sections with fields (id + human label)
# ─────────────────────────────────────────────────────────────────────────────

TEMPLATE_V2_SECTIONS = [
    {
        "id": "section_1", "number": "1", "title": "GENERAL SITE INFORMATION",
        "fields": [
            {"id": "item",              "name": "Item"},
            {"id": "area_facility",     "name": "Area/Facility"},
            {"id": "substation",        "name": "Substation/IES/MCR"},
            {"id": "ups_room",          "name": "UPS Room / Battery Room"},
            {"id": "date_of_visit",     "name": "Date of Visit"},
            {"id": "attendees",         "name": "Attendees (Ops/Maint/FEED)"},
            {"id": "safety_induction",  "name": "Safety Induction Completed"},
        ],
    },
    {
        "id": "section_2", "number": "2", "title": "UPS IDENTIFICATION",
        "fields": [
            {"id": "ups_tag",         "name": "UPS Tag"},
            {"id": "ups_type",        "name": "UPS Type(AC/DC/Static)"},
            {"id": "application",     "name": "Application(DCS/F&G/ESD/C&P)"},
            {"id": "ups_make_model",  "name": "UPS Make & Model"},
            {"id": "rated_capacity",  "name": "Rated Capacity(kVA/A)"},
        ],
    },
    {
        "id": "section_3", "number": "3", "title": "LOADING DATA",
        "fields": [
            {"id": "operating_load",           "name": "Presenting Operating Load(%/KVA/A)"},
            {"id": "load_measurement_source",  "name": "Load Measurement Source(HMI/ECMS/Clamp/Ammeter)"},
            {"id": "highest_load",             "name": "Highest Historical Load Encountered"},
            {"id": "operating_condition",      "name": "Operating Condition Durring Peak Load"},
            {"id": "load_margin",              "name": "Load Margin Adequated(<70%)"},
            {"id": "autonomy_time",            "name": "Autonomy Time"},
        ],
    },
    {
        "id": "section_4", "number": "4", "title": "BATTERY SYSTEM DATA",
        "fields": [
            {"id": "battery_make",                "name": "Battery Make"},
            {"id": "battery_rating_voltage",      "name": "Rating & Voltage"},
            {"id": "battery_model",               "name": "Battery Model"},
            {"id": "year_manufacturing",          "name": "Year of Manufacturing"},
            {"id": "battery_ah_rating",           "name": "Battery Ah Rating"},
            {"id": "number_of_cells",             "name": "Number of Cells"},
            {"id": "battery_installation_year",   "name": "Battery Installation Year"},
            {"id": "battery_physical_condition",  "name": "Battery Physical Condition"},
            {"id": "additional_space",            "name": "Additional Space Avaialbility if needs to be add new cells"},
            {"id": "design_life",                 "name": "Design Life and Replacement Year"},
            {"id": "battery_condition_test",      "name": "Battery Condition based on the capacity test"},
        ],
    },
    {
        "id": "section_5", "number": "5", "title": "FEEDER RATING & PROTECTION",
        "fields": [
            {"id": "normal_input_feeder",     "name": "Normal Input Feeder Rating(A)"},
            {"id": "bypass_feeder",           "name": "Bypass Feeder rating(A)"},
            {"id": "ups_output_feeder",       "name": "UPS Output Feeder Rating(A)"},
            {"id": "downstream_db_feeders",   "name": "Downstream DB Feeder Ratings"},
            {"id": "feeder_coordination",     "name": "Feeder Cordination OK"},
        ],
    },
    {
        "id": "section_6", "number": "6", "title": "LOAD RATIONALIZATION",
        "fields": [
            {"id": "load_item",           "name": "Item"},
            {"id": "single_fed_load",     "name": "Single-fed Load Identified"},
            {"id": "non_critical_loads",  "name": "Non Crticial Loads on UPS"},
            {"id": "db_segregation",      "name": "DB A/B Segregation Adequate"},
        ],
    },
    {
        "id": "section_7", "number": "7", "title": "LV Switchgear TIE-IN",
        "fields": [
            {"id": "source_lv_panel",   "name": "Source LV Panel ID"},
            {"id": "switchgear_make",   "name": "Switchgear Make"},
        ],
    },
    {
        "id": "section_7_1", "number": "7.1", "title": "GENERAL INFORMATION",
        "fields": [
            {"id": "feeder_rating",         "name": "Feeder Rating"},
            {"id": "feeder_tag",            "name": "Feeder Tag"},
            {"id": "bus_type",              "name": "Bus-A/B/Emergency"},
            {"id": "spare_breaker",         "name": "Spare Breaker Available & Rating"},
            {"id": "vacant_space_rating",   "name": "Vacant Space Avaialble & Rating"},
            {"id": "vacant_space_height",   "name": "Vacant Space Avaialble & Height"},
            {"id": "shutdown_required",     "name": "Shoutdown Required for Tie-In"},
        ],
    },
    {
        "id": "section_8", "number": "8", "title": "STATIC/CONVERTER SWITCH",
        "fields": [
            {"id": "static_switch_present",   "name": "Static/Converter Switch Present"},
            {"id": "static_switch_rating",    "name": "Rating"},
            {"id": "critical_load_supplied",  "name": "Critical Load Supplied"},
            {"id": "space_available",         "name": "Space Available"},
        ],
    },
    {
        "id": "section_9", "number": "9", "title": "BMS System",
        "fields": [
            {"id": "bms_make",           "name": "Existing BMS Make"},
            {"id": "monitoring_level",   "name": "Monitoring Level (Cell/String)"},
            {"id": "bms_interface",      "name": "Interface with DCS /ECMS"},
        ],
    },
    {
        "id": "section_10", "number": "10", "title": "SHUTDOWN & TEMPORARY UPS",
        "fields": [
            {"id": "shutdown_window",   "name": "Shutdown Window Available"},
            {"id": "temporary_ups",     "name": "Temporary UPS Avaialbe"},
        ],
    },
    {
        "id": "section_11", "number": "11", "title": "DATA GAPS & FEED ASSUMPTIONS",
        "fields": [
            {"id": "missing_load_history",  "name": "Missing Load History"},
            {"id": "missing_feeder_data",   "name": "Missing Feeder Data"},
            {"id": "feed_assumptions",      "name": "Feed Assumptions Required"},
        ],
    },
    {
        "id": "section_12", "number": "12", "title": "SPACE Availability",
        "fields": [
            {"id": "space_existing_ups",   "name": "At Existing UPS"},
            {"id": "space_temporary_ups",  "name": "For Temporary UPS"},
        ],
    },
    {
        "id": "section_13", "number": "13", "title": "Cable Size",
        "fields": [
            {"id": "incomer_1",  "name": "Incomer - 1"},
            {"id": "incomer_2",  "name": "Incomer - 2"},
        ],
    },
    {
        "id": "section_14", "number": "14", "title": "Existing Signal Interface with DCS/ECMS/ENMS etc",
        "fields": [
            {"id": "signal_interface",  "name": "Interface Details"},
        ],
    },
    {
        "id": "section_15", "number": "15", "title": "Documents Required",
        "fields": [
            {"id": "load_list_format",         "name": "Load List Format to be followed"},
            {"id": "load_criticality_check",   "name": "Load Criticality to be checked"},
            {"id": "shutdown_allowed",         "name": "Shutdown is allowed"},
            {"id": "rationalization_required", "name": "Load Rationalization is Required"},
            {"id": "cable_change",             "name": "Cable change due to Load Rationalization"},
            {"id": "og_feeder_pa",             "name": "O/G Feeder specifically for PA based on inrush current"},
            {"id": "unit_number",              "name": "Unit Number required against each loads"},
        ],
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# OpenAI Vision API Pricing (soft-coded — update here when OpenAI changes prices)
# ─────────────────────────────────────────────────────────────────────────────
# All prices are USD per 1,000,000 tokens, matching OpenAI's published rate
# cards. Used to compute the exact $ cost of each extraction job's Vision API
# calls (input tokens = prompt + image, output tokens = generated JSON).
# Source: https://openai.com/api/pricing/ — review periodically and update.
OPENAI_VISION_PRICING_PER_1M_TOKENS = {
    "gpt-4o":               {"input": 2.50,  "output": 10.00},
    "gpt-4o-2024-08-06":    {"input": 2.50,  "output": 10.00},
    "gpt-4o-2024-05-13":    {"input": 5.00,  "output": 15.00},
    "gpt-4o-mini":          {"input": 0.15,  "output": 0.60},
    "gpt-4-vision-preview": {"input": 10.00, "output": 30.00},
    "gpt-4-turbo":          {"input": 10.00, "output": 30.00},
}
# Fallback pricing used when the configured vision_model isn't in the table
# above (keeps cost estimation from silently returning $0 for new models).
OPENAI_VISION_PRICING_DEFAULT = {"input": 2.50, "output": 10.00}


def get_vision_pricing(model_name: str) -> dict:
    """Return {'input': $/1M tokens, 'output': $/1M tokens} for a Vision model."""
    return OPENAI_VISION_PRICING_PER_1M_TOKENS.get(model_name, OPENAI_VISION_PRICING_DEFAULT)


# ─────────────────────────────────────────────────────────────────────────────
# Handwriting Extraction Configuration (all soft-coded)
# ─────────────────────────────────────────────────────────────────────────────

HANDWRITING_CONFIG = {
    # ── COST-OPTIMISED ORDER ────────────────────────────────────────────────
    # 1) Tesseract (FREE, local)      — always tried first (unless vision_only_mode)
    # 2) OpenAI Vision (PAID)         — only if OCR result is below thresholds
    # Set `enable_vision_escalation` to False to disable paid AI entirely.

    # ── PRIMARY: Tesseract OCR (FREE) ───────────────────────────────────────
    "enable_ocr_primary":    True,
    "tesseract_config":      "--oem 3 --psm 6",
    "tesseract_lang":        "eng",
    "fuzzy_match_threshold": 0.72,           # 0..1 — strictness of field-label fuzzy match
                                             # (raised from 0.62: cursive OCR produces noisy
                                             # matches that were filling fields with garbage)

    # ── ESCALATION: OpenAI Vision (PAID, used only when OCR is weak) ────────
    "enable_vision_escalation": True,        # master switch for paid fallback
    "vision_only_mode":         False,       # skip OCR entirely — best accuracy, higher cost
    "vision_model":             "gpt-4o",    # gpt-4o | gpt-4o-mini (cheaper) | gpt-4-vision-preview
    "vision_temperature":       0.0,         # deterministic
    "vision_max_tokens":        6000,        # increased for richer multi-column output
    "vision_timeout_sec":       90,          # multi-pass may take longer
    "image_detail":             "high",      # "high" for handwriting accuracy | "low" for cost
    "vision_use_layout_aware_prompt": True,  # explicit 6-column layout description in prompt

    # ── Multi-pass Vision consensus (accuracy > cost) ───────────────────────
    "enable_multipass_vision":   False,      # run Vision N times and merge by confidence
    "vision_passes":             1,          # number of Vision calls per page (1..3 recommended)
    "vision_pass_temperatures":  [0.0, 0.3, 0.5],  # temperature per pass (extra ignored)
    "vision_consensus_strategy": "highest_confidence",  # highest_confidence | majority_vote

    # Escalation triggers — Vision runs only if OCR result fails ANY of these:
    # Defaults tuned for handwriting: Tesseract almost never crosses 80% avg conf on cursive
    "escalate_if_fields_below":     40,      # OCR extracted fewer than N fields
    "escalate_if_avg_conf_below":   80,      # OCR average confidence below N (0..100)
    "escalate_if_ocr_unavailable":  True,    # pytesseract not installed / crashed

    # ── PDF rendering (higher DPI + PNG for handwriting) ────────────────────
    "pdf_dpi":          300,                 # higher = better OCR & Vision, slower / larger payloads
    "pdf_image_format": "png",               # png preserves pen strokes better than jpeg
    "pdf_jpeg_quality": 92,                  # used only if pdf_image_format == "jpeg"

    # ── Image preprocessing (PIL + numpy, no cv2 required) ──────────────────
    "enable_preprocessing":         True,
    "preprocess_remove_highlights": True,    # neutralise yellow highlight cells → white
    "preprocess_contrast_boost":    1.35,    # 1.0 = off; 1.2-1.6 makes pen strokes darker
    "preprocess_sharpen":           True,    # UnsharpMask to enhance thin pen strokes
    "preprocess_denoise":           False,   # median filter — can blur cursive; off by default
    "preprocess_max_side_px":       2400,    # downscale for Vision if image side exceeds this

    # ── Multi-file / multi-engineer merging ─────────────────────────────────
    "merge_strategy":            "highest_confidence",   # highest_confidence | first_wins | last_wins
    "per_field_min_confidence":  30,         # drop fields below this confidence

    # ── Signature detection (via existing OpenCV pipeline) ──────────────────
    "extract_signatures": True,
}


# ─────────────────────────────────────────────────────────────────────────────
# Extraction Mode Presets (soft-coded)
# ─────────────────────────────────────────────────────────────────────────────
# Each preset is a shallow override applied on top of HANDWRITING_CONFIG.
# The active mode is chosen by the user in the UI and forwarded to the extractor.
#
#   - fast         : Tesseract-only, no Vision. Cheapest, weakest on cursive.
#   - balanced     : Tesseract → Vision escalation (single pass). Recommended default.
#   - deep         : Tesseract → Vision escalation + multi-pass consensus. Best on
#                    difficult handwriting; ~2× Vision cost.
#   - vision_only  : Skip Tesseract entirely + multi-pass. Highest accuracy, highest cost.

EXTRACTION_MODE_PRESETS = {
    "fast": {
        "label":                    "Fast (free OCR only)",
        "description":              "Local Tesseract only — no AI cost. Best for clean printed forms.",
        "enable_ocr_primary":       True,
        "enable_vision_escalation": False,
        "vision_only_mode":         False,
        "enable_multipass_vision":  False,
        "pdf_dpi":                  250,
        "pdf_image_format":         "png",
    },
    "balanced": {
        "label":                    "Balanced (OCR + AI fallback)",
        "description":              "Free OCR first; AI Vision only when OCR is weak. Recommended.",
        "enable_ocr_primary":       True,
        "enable_vision_escalation": True,
        "vision_only_mode":         False,
        "enable_multipass_vision":  False,
        "vision_passes":            1,
        "pdf_dpi":                  300,
        "pdf_image_format":         "png",
    },
    "deep": {
        "label":                    "Deep Analysis (multi-pass AI)",
        "description":              "OCR + multi-pass GPT-4o Vision with consensus. Best for messy cursive.",
        "enable_ocr_primary":       True,
        "enable_vision_escalation": True,
        "vision_only_mode":         False,
        "enable_multipass_vision":  True,
        "vision_passes":            2,
        "vision_pass_temperatures": [0.0, 0.3],
        "vision_consensus_strategy": "highest_confidence",
        "pdf_dpi":                  300,
        "pdf_image_format":         "png",
        "image_detail":             "high",
    },
    "vision_only": {
        "label":                    "Vision-Only (max accuracy, BYOK)",
        "description":              "Skip OCR; go straight to multi-pass GPT-4o Vision. Requires OpenAI key.",
        "enable_ocr_primary":       False,
        "enable_vision_escalation": True,
        "vision_only_mode":         True,
        "enable_multipass_vision":  True,
        "vision_passes":            2,
        "vision_pass_temperatures": [0.0, 0.3],
        "vision_consensus_strategy": "highest_confidence",
        "pdf_dpi":                  300,
        "pdf_image_format":         "png",
        "image_detail":             "high",
    },
}

DEFAULT_EXTRACTION_MODE = "balanced"


def get_config_for_mode(mode: str) -> dict:
    """Return HANDWRITING_CONFIG merged with the chosen mode preset.
    Unknown modes fall back to DEFAULT_EXTRACTION_MODE.
    """
    preset = EXTRACTION_MODE_PRESETS.get(
        (mode or "").lower(),
        EXTRACTION_MODE_PRESETS[DEFAULT_EXTRACTION_MODE],
    )
    # Drop UI-only keys from the preset before merging into the runtime config.
    ui_keys = {"label", "description"}
    return {**HANDWRITING_CONFIG, **{k: v for k, v in preset.items() if k not in ui_keys}}

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_all_v2_fields():
    """Return a flat list of {id, name, section_id, section_title} for every field."""
    out = []
    for section in TEMPLATE_V2_SECTIONS:
        for field in section["fields"]:
            out.append({
                "id":            field["id"],
                "name":          field["name"],
                "section_id":    section["id"],
                "section_title": section["title"],
                "section_number": section["number"],
            })
    return out


def get_empty_template_v2_data():
    """Return an empty 6-column data structure keyed by field_id (mirror of frontend)."""
    data = {}
    for section in TEMPLATE_V2_SECTIONS:
        for field in section["fields"]:
            data[field["id"]] = {
                "field_name":    field["name"],
                "site_value":    "",
                "remarks":       "",
                "need_list":     "",
                "query":         "",
                "company_reply": "",
            }
    return data


def get_field_by_id(field_id):
    """Look up a field definition by ID. Returns None if unknown."""
    for section in TEMPLATE_V2_SECTIONS:
        for field in section["fields"]:
            if field["id"] == field_id:
                return {**field, "section_id": section["id"], "section_title": section["title"]}
    return None


# Metadata
TEMPLATE_V2_METADATA = {
    "version":        "2.0",
    "title":          "UPS/Battery System Inspection Checklist",
    "columns":        len(TEMPLATE_V2_COLUMNS),
    "total_sections": len(TEMPLATE_V2_SECTIONS),
    "total_fields":   sum(len(s["fields"]) for s in TEMPLATE_V2_SECTIONS),
    "source":         "Check_List_Template.xlsx",
}
