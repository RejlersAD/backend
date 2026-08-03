"""
AI-Powered P&ID Check Configuration
Soft-coded settings for vision API extraction and automated checking.
All thresholds, patterns, and rules are externalized here for easy tuning.
"""

# ═══════════════════════════════════════════════════════════════════════════
# VISION API CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

VISION_API_CONFIG = {
    'providers': {
        'openai': {
            'model': 'gpt-4o',
            'temperature': 0.1,  # Low for deterministic extraction
            'max_tokens': 16000,
            'timeout': 60,  # seconds
        },
        'claude': {
            'model': 'claude-3-5-sonnet-20241022',
            'temperature': 0.1,
            'max_tokens': 16000,
            'timeout': 60,
        },
    },
    'extraction_strategy': {
        'default_provider': 'claude',  # Claude better for dense OCR
        'symbol_recognition_provider': 'openai',  # OpenAI better for symbols
        'hybrid_mode_enabled': True,
        'consensus_checks': ['line_list', 'equipment_list'],
    },
    'confidence_thresholds': {
        'high': 0.95,
        'medium': 0.85,
        'low': 0.70,
        'reject_below': 0.60,
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# EXTRACTION PATTERNS (REGEX)
# ═══════════════════════════════════════════════════════════════════════════

EXTRACTION_PATTERNS = {
    'line_number': [
        r'(\d+(?:\.\d+)?)"[-_]([A-Z]{2,4})[-_](\d{4,6})[-_]([A-Z0-9]{3,6})[-_]([A-Z]{1,2})',
        r'(\d+)"[-_]([A-Z]{2})[-_](\d{4,6})[-_]([A-Z0-9]{3,6})',
        r'(\d+(?:\.\d+)?)"[-_]([A-Z]{2,4})[-_](\d{4,6})',
    ],
    'equipment_tag': [
        r'([A-Z])-([0-9]{3,4})([A-Z]?)',  # V-001, P-102A
        r'([A-Z]{2})-([0-9]{3,4})([A-Z]?)',  # HE-301A
    ],
    'instrument_tag': [
        r'([A-Z]{1,4})[- ]?(\d{2,6}(?:[A-Z]?(?:[-]\d{1,4})?))',  # PI-3610-16
    ],
    'psv_set_pressure': [
        r'(\d+(?:\.\d+)?)\s*(barg|psig|kPa)',
    ],
}

# ═══════════════════════════════════════════════════════════════════════════
# VISION API PROMPTS (SOFT-CODED FOR EASY TUNING)
# ═══════════════════════════════════════════════════════════════════════════

VISION_PROMPTS = {
    'equipment_extraction': """
Extract all equipment tags from this P&ID sheet.
For each equipment item, provide in JSON array format:

[
  {
    "tag": "V-001",
    "type": "vessel",
    "service": "Feed Drum",
    "sheet": "P&ID-001",
    "confidence": "high"
  }
]

Equipment types: vessel, pump, compressor, exchanger, tank, reactor, column, filter, separator

Mark any unreadable tags with "confidence": "low" and provide best-effort reading.
Return ONLY the JSON array, no additional text.
""",
    
    'line_extraction': """
Extract all piping line numbers from this P&ID sheet.
For each line, parse the complete line number format: SIZE"-CODE-XXXXX-SPEC-INS
Example: 8"-BD-4860-033842-X-N

Provide in JSON array format:

[
  {
    "line_number": "8\\"-BD-4860-033842-X-N",
    "size": "8",
    "fluid_code": "BD",
    "spec": "033842",
    "insulation": "N",
    "from": "V-001",
    "to": "P-102A",
    "sheet": "P&ID-001",
    "confidence": "high"
  }
]

Mark incomplete or ambiguous line numbers with "confidence": "low".
Return ONLY the JSON array, no additional text.
""",
    
    'instrument_extraction': """
Extract all instrument tags following ISA 5.1 standard from this P&ID sheet.
For each instrument, provide in JSON array format:

[
  {
    "tag": "PI-3610-16",
    "type": "pressure_indicator",
    "location": "field",
    "associated_equipment": "V-001",
    "sheet": "P&ID-001",
    "confidence": "high"
  }
]

Instrument types: transmitter, indicator, controller, switch, analyzer, recorder
Location: field, panel, dcs

Flag any non-standard tags or ambiguous symbols with "confidence": "low".
Return ONLY the JSON array, no additional text.
""",

    'quick_overview': """
Provide a quick overview of this P&ID sheet in JSON format:

{
  "sheet_number": "P&ID-001",
  "equipment_count": 12,
  "line_count": 45,
  "instrument_count": 28,
  "has_legend": true,
  "has_notes": true,
  "quality": "high"
}

Quality assessment: "high" (clear, readable), "medium" (some unclear areas), "low" (poor scan, handwritten)
Return ONLY the JSON object, no additional text.
"""
}

# ═══════════════════════════════════════════════════════════════════════════
# RECONCILIATION RULES (AUTO CHECKS)
# ═══════════════════════════════════════════════════════════════════════════

RECONCILIATION_CONFIG = {
    'line_list': {
        'check_id': 'AUTO_001',
        'name': 'Line List Two-Way Reconciliation',
        'key_field': 'line_number',
        'compare_fields': ['size', 'spec', 'insulation'],
        'tolerance': {'size': 0.01},  # inch tolerance
        'orphan_threshold': 5,  # Max allowed orphans before fail
        'severity': 'critical',
        'ai_feasibility': 'AUTO',
    },
    'equipment_list': {
        'check_id': 'AUTO_002',
        'name': 'Equipment List Two-Way Reconciliation',
        'key_field': 'equipment_tag',
        'compare_fields': ['service', 'type'],
        'fuzzy_match_threshold': 0.85,  # for service description
        'orphan_threshold': 3,
        'severity': 'critical',
        'ai_feasibility': 'AUTO',
    },
    'instrument_index': {
        'check_id': 'AUTO_003',
        'name': 'Instrument Index Two-Way Reconciliation',
        'key_field': 'instrument_tag',
        'compare_fields': ['type', 'location'],
        'allow_sub_tags': True,  # PI-001 matches PI-001-16
        'orphan_threshold': 5,
        'severity': 'major',
        'ai_feasibility': 'AUTO',
    },
    'legend': {
        'check_id': 'AUTO_004',
        'name': 'Legend Symbol Verification',
        'description': 'Verify all P&ID symbols match project legend definitions',
        'key_field': 'symbol_type',
        'compare_fields': ['prefix', 'description'],
        'orphan_threshold': 0,  # Any unknown symbol is an issue
        'severity': 'critical',
        'ai_feasibility': 'AUTO',
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# PATTERN RULES (DESIGN CONVENTIONS)
# ═══════════════════════════════════════════════════════════════════════════

PATTERN_RULES = [
    {
        'id': 'PATTERN_001',
        'name': 'PSV Car Seal Closed Marking',
        'description': 'All PSV/PRV must have CSC (Car Sealed Closed) marking',
        'target_symbols': ['psv', 'prv'],
        'validation_function': 'check_csc_marker_proximity',
        'parameters': {
            'marker_text': 'CSC',
            'proximity_radius_px': 50,
            'allow_alternatives': ['CS', 'CAR SEAL'],
        },
        'severity': 'critical',
        'ai_feasibility': 'AUTO',
        'confidence_threshold': 0.95,
    },
    {
        'id': 'PATTERN_002',
        'name': 'Check Valve Flow Direction',
        'description': 'Check valves must have arrow pointing in correct flow direction',
        'target_symbols': ['check_valve'],
        'validation_function': 'check_arrow_alignment',
        'parameters': {
            'arrow_search_radius_px': 30,
            'angle_tolerance_degrees': 15,
        },
        'severity': 'major',
        'ai_feasibility': 'AUTO',
        'confidence_threshold': 0.90,
    },
]

# ═══════════════════════════════════════════════════════════════════════════
# PROCESSING CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

PROCESSING_CONFIG = {
    'parallel_sheets': 3,  # Max concurrent sheets (conservative for API limits)
    'batch_size': 10,  # Sheets per batch
    'retry_attempts': 3,
    'timeout_per_sheet': 180,  # seconds
    'save_intermediate_results': True,
    'cache_extractions': True,  # Cache vision API results to avoid re-processing
}

# ═══════════════════════════════════════════════════════════════════════════
# CHECK CATEGORIES (BASED ON AI FEASIBILITY ANALYSIS)
# ═══════════════════════════════════════════════════════════════════════════

CHECK_CATEGORIES = {
    'AUTO': {
        'description': 'Fully automated checks (no engineer input required)',
        'confidence_requirement': 'high',
        'auto_execute': True,
        'color': '#10B981',  # green
    },
    'ASSIST': {
        'description': 'AI-assisted checks (engineer confirms findings)',
        'confidence_requirement': 'medium',
        'auto_execute': False,
        'color': '#F59E0B',  # amber
    },
    'HUMAN': {
        'description': 'Engineering judgment required (AI provides context only)',
        'confidence_requirement': None,
        'auto_execute': False,
        'color': '#EF4444',  # red
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# API USAGE LIMITS & COST CONTROLS
# ═══════════════════════════════════════════════════════════════════════════

API_LIMITS = {
    'max_sheets_per_run': 100,  # Safety limit
    'max_api_cost_per_run': 50.0,  # USD, safety threshold
    'rate_limit_pause': 2.0,  # seconds between API calls
    'estimated_cost_per_sheet': {
        'openai': 0.25,
        'claude': 0.12,
        'hybrid': 0.35,
    },
}
