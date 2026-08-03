"""
Spec Customization — Data Quality & Validation Service
=======================================================

Post-processing layer that runs AFTER AI extraction but BEFORE persistence.
Fixes common quality issues:

  • Component-level deduplication (exact matches + fuzzy near-duplicates)
  • Empty field validation & smart filling
  • Standards compliance checks (ASME, ANSI, API)
  • Project-context enrichment
  • Confidence scoring based on data completeness

All knobs soft-coded in DATA_QUALITY_CONFIG.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# Import size expansion and component detection configs
from .config import SIZE_EXPANSION_CONFIG, COMPONENT_TYPE_DETECTION_CONFIG, COMPONENT_TYPE_DETECTION_CONFIG


# ─────────────────────────────────────────────────────────────────────────────
# Soft-coded configuration
# ─────────────────────────────────────────────────────────────────────────────
DATA_QUALITY_CONFIG = {
    # ── Component Deduplication ─────────────────────────────────────────
    "dedupe_components": True,
    # Similarity threshold for fuzzy matching (0.0–1.0); 0.95 = near-exact match
    "fuzzy_match_threshold": 0.90,
    
    # ── Empty Field Handling ────────────────────────────────────────────
    "validate_empty_fields": True,
    # Minimum % of key fields that must be non-empty for a class to be kept
    "min_field_completeness": 0.30,  # 30%
    # Key fields that contribute to completeness score
    "critical_fields": [
        "class_code", "material_grade", "pressure_rating",
        "flange_facing", "corrosion_allowance"
    ],
    
    # ── Standards Validation ────────────────────────────────────────────
    "validate_standards": True,
    # Known ASME/ANSI pressure ratings
    "valid_pressure_ratings": [
        "CLASS 150", "CLASS 300", "CLASS 600", "CLASS 900",
        "CLASS 1500", "CLASS 2500", "CL 150", "CL 300",
        "CL 600", "CL 900", "CL 1500", "CL 2500",
        "PN 10", "PN 16", "PN 25", "PN 40", "PN 63", "PN 100",
        "#150", "#300", "#600", "#900", "#1500", "#2500",
    ],
    # Known flange facings
    "valid_flange_facings": [
        "RF", "FF", "RTJ", "RAISED FACE", "FLAT FACE",
        "RING TYPE JOINT", "RING JOINT", "MALE & FEMALE",
        "TONGUE & GROOVE", "M&F", "T&G", "LAP JOINT",
    ],
    # Known material standards (ASTM, API, ASME)
    "material_standard_patterns": [
        r"ASTM\s+[A-Z]\d+",        # ASTM A106, A234, etc.
        r"API\s+\d+[A-Z]*",        # API 5L, API 6D
        r"ASME\s+B\d+\.?\d*",      # ASME B16.9, B16.5
        r"MSS\s+SP-?\d+",          # MSS SP-75
        r"ISO\s+\d+",              # ISO 9001
        r"DIN\s+\d+",              # DIN 2527
        r"EN\s+\d+",               # EN 10025
        r"BS\s+\d+",               # BS 3600
    ],
    
    # ── Component Type Normalization ────────────────────────────────────
    "component_type_aliases": {
        "piping": "pipe",
        "pipework": "pipe",
        "tube": "pipe",
        "tubing": "pipe",
        "pipe stock": "pipe",
        "gate valve": "valve",
        "globe valve": "valve",
        "ball valve": "valve",
        "check valve": "valve",
        "butterfly valve": "valve",
        "needle valve": "valve",
        "elbow": "fitting",
        "elbows": "fitting",
        "tee": "fitting",
        "tees": "fitting",
        "reducer": "fitting",
        "coupling": "fitting",
        "cap": "fitting",
        "nipple": "fitting",
        "swage": "fitting",
        "weld neck flange": "flange",
        "slip-on flange": "flange",
        "blind flange": "flange",
        "lap joint flange": "flange",
        "socket weld flange": "flange",
        "threaded flange": "flange",
        "spiral wound gasket": "gasket",
        "ring gasket": "gasket",
        "rtj gasket": "gasket",
        "flat ring gasket": "gasket",
        "stud bolt": "bolt",
        "hex bolt": "bolt",
        "machine bolt": "bolt",
        "hex nut": "bolt",
    },
    
    # ── Branch Connection Keywords (for sub_type enrichment) ────────────
    # These are critical for SmartPlant 3D routing - many specs have 100+
    # branch connection entries that must be properly identified
    "branch_connection_keywords": {
        "weldolet": "Weldolet",
        "wol": "Weldolet",
        "elbolet": "Elbolet",
        "latrolet": "Latrolet",
        "sockolet": "Sockolet",
        "thredolet": "Thredolet",
        "threadolet": "Thredolet",
        "sweepolet": "Sweepolet",
        "nipolet": "Nipolet",
        "insert weld": "Weldolet",
        "branch connection": "Weldolet",
        "branch outlet": "Weldolet",
    },
    
    # ── Project Context Enrichment ──────────────────────────────────────
    "enrich_with_project_context": True,
    "enrich_component_sub_types": True,  # NEW: Enhance sub_type classification
}


# ─────────────────────────────────────────────────────────────────────────────
# Helper: calculate similarity between two strings (Levenshtein-based)
# ─────────────────────────────────────────────────────────────────────────────
def _similarity(a: str, b: str) -> float:
    """Return similarity score 0.0–1.0 (1.0 = identical)."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    
    # Normalize: lowercase, strip whitespace
    a = a.lower().strip()
    b = b.lower().strip()
    
    if a == b:
        return 1.0
    
    # Simple Levenshtein distance
    m, n = len(a), len(b)
    if m > n:
        a, b, m, n = b, a, n, m
    
    current = list(range(n + 1))
    for i in range(1, m + 1):
        previous, current = current, [i] + [0] * n
        for j in range(1, n + 1):
            add, delete, change = previous[j] + 1, current[j - 1] + 1, previous[j - 1]
            if a[i - 1] != b[j - 1]:
                change += 1
            current[j] = min(add, delete, change)
    
    distance = current[n]
    max_len = max(m, n)
    return 1.0 - (distance / max_len)


# ─────────────────────────────────────────────────────────────────────────────
# Component Deduplication
# ─────────────────────────────────────────────────────────────────────────────
def _component_signature(comp: Dict[str, Any]) -> str:
    """Generate a stable signature for exact-match deduplication."""
    sig_fields = [
        comp.get("component_type", "").lower().strip(),
        comp.get("sub_type", "").lower().strip(),
        comp.get("size_from", "").lower().strip(),
        comp.get("size_to", "").lower().strip(),
        comp.get("description", "").lower().strip(),
        comp.get("schedule_or_rating", "").lower().strip(),
        comp.get("material_standard", "").lower().strip(),
    ]
    return "|".join(sig_fields)


def _are_components_similar(c1: Dict[str, Any], c2: Dict[str, Any], threshold: float) -> bool:
    """Return True if c1 and c2 are fuzzy-similar above threshold."""
    sig1 = _component_signature(c1)
    sig2 = _component_signature(c2)
    return _similarity(sig1, sig2) >= threshold


def deduplicate_components(components: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Remove exact + fuzzy duplicates from component list."""
    if not DATA_QUALITY_CONFIG["dedupe_components"]:
        return components
    
    if not components:
        return []
    
    # Step 1: exact match deduplication
    seen_sigs: Set[str] = set()
    exact_deduped: List[Dict[str, Any]] = []
    for comp in components:
        sig = _component_signature(comp)
        if sig not in seen_sigs:
            seen_sigs.add(sig)
            exact_deduped.append(comp)
    
    # Step 2: fuzzy match deduplication
    threshold = DATA_QUALITY_CONFIG["fuzzy_match_threshold"]
    fuzzy_deduped: List[Dict[str, Any]] = []
    for comp in exact_deduped:
        is_duplicate = False
        for kept in fuzzy_deduped:
            if _are_components_similar(comp, kept, threshold):
                is_duplicate = True
                # Merge: prefer comp with more non-empty fields
                comp_fields = sum(1 for v in comp.values() if v and str(v).strip())
                kept_fields = sum(1 for v in kept.values() if v and str(v).strip())
                if comp_fields > kept_fields:
                    fuzzy_deduped[fuzzy_deduped.index(kept)] = comp
                break
        if not is_duplicate:
            fuzzy_deduped.append(comp)
    
    logger.info(
        "[DataQuality] Component dedup: %d → %d (exact) → %d (fuzzy)",
        len(components), len(exact_deduped), len(fuzzy_deduped)
    )
    return fuzzy_deduped


# ─────────────────────────────────────────────────────────────────────────────
# Empty Field Validation
# ─────────────────────────────────────────────────────────────────────────────
def calculate_field_completeness(cls: Dict[str, Any]) -> float:
    """Return field completeness score 0.0–1.0."""
    critical = DATA_QUALITY_CONFIG["critical_fields"]
    if not critical:
        return 1.0
    
    filled = 0
    for field in critical:
        val = cls.get(field, "")
        if val and str(val).strip():
            filled += 1
    
    return filled / len(critical)


def validate_empty_fields(cls: Dict[str, Any]) -> bool:
    """Return True if class passes minimum completeness threshold."""
    if not DATA_QUALITY_CONFIG["validate_empty_fields"]:
        return True
    
    completeness = calculate_field_completeness(cls)
    min_required = DATA_QUALITY_CONFIG["min_field_completeness"]
    
    if completeness < min_required:
        logger.warning(
            "[DataQuality] Class %s failed completeness check: %.1f%% < %.1f%%",
            cls.get("class_code", "?"),
            completeness * 100,
            min_required * 100
        )
        return False
    
    return True


def fill_missing_fields_with_defaults(cls: Dict[str, Any]) -> Dict[str, Any]:
    """Smart-fill empty fields with project/industry defaults."""
    # If flange_facing is empty but pressure_rating exists, infer common default
    if not cls.get("flange_facing") and cls.get("pressure_rating"):
        rating = cls.get("pressure_rating", "").upper()
        if "150" in rating or "300" in rating:
            cls["flange_facing"] = "RF"  # Raised Face is most common for Class 150/300
        elif "600" in rating or "900" in rating:
            cls["flange_facing"] = "RTJ"  # Ring Type Joint common for higher ratings
    
    # If corrosion_allowance is empty but material_grade contains "CARBON STEEL", assume 3mm
    if not cls.get("corrosion_allowance"):
        material = (cls.get("material_grade", "") or "").upper()
        if "CARBON" in material or "C.S." in material:
            cls["corrosion_allowance"] = "3.0 mm"
        elif "STAINLESS" in material or "S.S." in material:
            cls["corrosion_allowance"] = "0 mm"  # Stainless typically no CA
    
    return cls


# ─────────────────────────────────────────────────────────────────────────────
# Standards Validation
# ─────────────────────────────────────────────────────────────────────────────
def normalize_pressure_rating(rating: str) -> str:
    """Normalize pressure rating to standard format."""
    if not rating:
        return ""
    
    r = rating.upper().strip()
    
    # Normalize #150 → CLASS 150, CL 150 → CLASS 150
    r = re.sub(r'#\s*(\d+)', r'CLASS \1', r)
    r = re.sub(r'CL\.?\s*(\d+)', r'CLASS \1', r)
    
    # Standardize spacing
    r = re.sub(r'\s+', ' ', r)
    
    return r


def normalize_flange_facing(facing: str) -> str:
    """Normalize flange facing to standard abbreviation."""
    if not facing:
        return ""
    
    f = facing.upper().strip()
    
    # Normalize to abbreviations
    if "RAISED" in f or "RAISED FACE" in f:
        return "RF"
    elif "FLAT" in f or "FLAT FACE" in f:
        return "FF"
    elif "RING" in f or "RTJ" in f or "RING TYPE" in f:
        return "RTJ"
    elif "MALE" in f and "FEMALE" in f or "M&F" in f:
        return "M&F"
    elif "TONGUE" in f and "GROOVE" in f or "T&G" in f:
        return "T&G"
    elif "LAP" in f and "JOINT" in f:
        return "LAP JOINT"
    
    return f


def validate_material_standard(standard: str) -> bool:
    """Return True if standard matches known patterns."""
    if not standard or not DATA_QUALITY_CONFIG["validate_standards"]:
        return True
    
    patterns = DATA_QUALITY_CONFIG["material_standard_patterns"]
    for pattern in patterns:
        if re.search(pattern, standard.upper()):
            return True
    
    # Warn but don't reject (AI might have extracted valid non-standard notation)
    logger.warning("[DataQuality] Unrecognized material standard: %s", standard)
    return True


def normalize_component_type(comp_type: str) -> str:
    """Normalize component type using alias mapping."""
    if not comp_type:
        return "other"
    
    ct = comp_type.lower().strip()
    aliases = DATA_QUALITY_CONFIG["component_type_aliases"]
    
    return aliases.get(ct, ct)


def enrich_component_sub_type(comp: Dict[str, Any]) -> Dict[str, Any]:
    """Enrich component sub_type based on description keywords.
    
    Critical for SmartPlant 3D routing - ensures branch connections,
    specific fitting types, and valve types are properly classified.
    """
    if not DATA_QUALITY_CONFIG.get("enrich_component_sub_types", True):
        return comp
    
    # Combine all text fields for keyword matching
    text_blob = " ".join([
        comp.get("sub_type", "") or "",
        comp.get("description", "") or "",
        comp.get("component_type", "") or "",
        comp.get("notes", "") or "",
    ]).lower()
    
    comp_type = (comp.get("component_type") or "").lower().strip()
    
    # Branch connections (highest priority - these are often miscategorized)
    branch_keywords = DATA_QUALITY_CONFIG.get("branch_connection_keywords", {})
    for keyword, standard_name in branch_keywords.items():
        if keyword in text_blob:
            comp["component_type"] = "fitting"
            comp["sub_type"] = standard_name
            return comp
    
    # Fitting sub-types
    if comp_type in ["fitting", "fittings"]:
        if "90" in text_blob and ("lr" in text_blob or "long radius" in text_blob):
            comp["sub_type"] = "90 Deg LR Elbow"
        elif "90" in text_blob:
            comp["sub_type"] = "90 Deg Elbow"
        elif "45" in text_blob and ("lr" in text_blob or "long radius" in text_blob):
            comp["sub_type"] = "45 Deg LR Elbow"
        elif "45" in text_blob:
            comp["sub_type"] = "45 Deg Elbow"
        elif "reducing tee" in text_blob or "red tee" in text_blob or "red. tee" in text_blob:
            comp["sub_type"] = "Reducing Tee"
        elif "tee" in text_blob or "t-piece" in text_blob:
            comp["sub_type"] = "Tee"
        elif "concentric reducer" in text_blob or "conc red" in text_blob or "conc. red" in text_blob:
            comp["sub_type"] = "Concentric Reducer"
        elif "eccentric reducer" in text_blob or "ecc red" in text_blob or "ecc. red" in text_blob:
            comp["sub_type"] = "Eccentric Reducer"
        elif "swage" in text_blob:
            comp["sub_type"] = "Concentric Swage"
        elif "cap" in text_blob and "end cap" in text_blob:
            comp["sub_type"] = "Cap"
        elif "coupling" in text_blob:
            comp["sub_type"] = "Coupling"
        elif "nipple" in text_blob:
            comp["sub_type"] = "Nipple"
        elif "paddle" in text_blob or "spec blind" in text_blob or "spectacle" in text_blob:
            comp["sub_type"] = "Paddle"
    
    # Valve sub-types
    elif comp_type in ["valve", "valves"]:
        if "gate" in text_blob:
            comp["sub_type"] = "Gate"
        elif "globe" in text_blob or "needle" in text_blob:
            comp["sub_type"] = "Globe"
        elif "check" in text_blob or "nrv" in text_blob or "non-return" in text_blob:
            comp["sub_type"] = "Check"
        elif "ball" in text_blob:
            comp["sub_type"] = "Ball"
        elif "butterfly" in text_blob:
            comp["sub_type"] = "Butterfly"
    
    # Flange sub-types
    elif comp_type in ["flange", "flanges"]:
        if "blind" in text_blob or "bld" in text_blob or "blnd" in text_blob:
            comp["sub_type"] = "Blind"
        elif "weld neck" in text_blob or "wn" in text_blob or "weldneck" in text_blob:
            comp["sub_type"] = "Weld Neck"
        elif "slip" in text_blob and "on" in text_blob:
            comp["sub_type"] = "Slip-On"
        elif "lap joint" in text_blob:
            comp["sub_type"] = "Lap Joint"
        elif "socket" in text_blob and "weld" in text_blob:
            comp["sub_type"] = "Socket Weld"
        elif "threaded" in text_blob or "screwed" in text_blob:
            comp["sub_type"] = "Threaded"
    
    # Gasket sub-types
    elif comp_type in ["gasket", "gaskets"]:
        if "spiral" in text_blob and "wound" in text_blob:
            comp["sub_type"] = "Spiral Wound"
        elif "ring" in text_blob and ("joint" in text_blob or "rtj" in text_blob):
            comp["sub_type"] = "Ring Joint"
        elif "flat" in text_blob:
            comp["sub_type"] = "Flat Ring"
    
    # Bolt sub-types
    elif comp_type in ["bolt", "bolts", "stud"]:
        if "stud" in text_blob:
            comp["sub_type"] = "Hex Head Stud"
        elif "hex" in text_blob:
            comp["sub_type"] = "Hex Bolt"
    
    return comp


def validate_and_normalize_standards(cls: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and normalize all standards in a piping class."""
    if not DATA_QUALITY_CONFIG["validate_standards"]:
        return cls
    
    # Normalize pressure rating
    if cls.get("pressure_rating"):
        cls["pressure_rating"] = normalize_pressure_rating(cls["pressure_rating"])
    
    # Normalize flange facing
    if cls.get("flange_facing"):
        cls["flange_facing"] = normalize_flange_facing(cls["flange_facing"])
    
    # Validate material standards in components
    for comp in cls.get("components", []):
        if comp.get("material_standard"):
            validate_material_standard(comp["material_standard"])
        
        # Normalize component type
        if comp.get("component_type"):
            comp["component_type"] = normalize_component_type(comp["component_type"])
        
        # Enrich sub_type based on description keywords (NEW)
        # Critical for SmartPlant 3D routing - ensures branch connections
        # and specific fitting types are properly classified
        enrich_component_sub_type(comp)
    
    return cls


# ─────────────────────────────────────────────────────────────────────────────
# Project Context Enrichment
# ─────────────────────────────────────────────────────────────────────────────
def enrich_with_project_context(
    cls: Dict[str, Any],
    project_id: Optional[str] = None,
    project_title: Optional[str] = None,
    document_number: Optional[str] = None
) -> Dict[str, Any]:
    """Add project context metadata to class for traceability."""
    if not DATA_QUALITY_CONFIG["enrich_with_project_context"]:
        return cls
    
    # Add project metadata (used for reporting, exports, audit trails)
    cls["_project_id"] = project_id or ""
    cls["_project_title"] = project_title or ""
    cls["_document_number"] = document_number or ""
    
    return cls


# ─────────────────────────────────────────────────────────────────────────────
# Size Expansion — Smart Detection of "1.5 & Below" Patterns
# ─────────────────────────────────────────────────────────────────────────────
def _parse_size_value(size_str: str) -> Optional[float]:
    """Parse a size string to float value (handles fractions like 1½, 1-1/2, 1/2).
    
    Returns None if unparseable. Reuses logic from smartplant_config._to_float_npd.
    """
    if not size_str:
        return None
    
    s = str(size_str)
    
    # Remove inch markers
    for tok in ('"', '"', '"', '″', '′', "''", "'", 'IN', 'in', 'In', 'inch', 'INCH', 'Inch'):
        s = s.replace(tok, '')
    s = s.replace('-', ' ').strip()
    
    if not s:
        return None
    
    # Common fraction shortcuts
    fraction_map = {
        '1/8': 0.125, '1/4': 0.25, '3/8': 0.375,
        '1/2': 0.5,   '3/4': 0.75,
        '1 1/4': 1.25, '1-1/4': 1.25,
        '1 1/2': 1.5,  '1-1/2': 1.5, '1½': 1.5,
        '2 1/2': 2.5,  '2-1/2': 2.5,
    }
    if s in fraction_map:
        return fraction_map[s]
    
    # Mixed fraction: '1 1/2', '2 1/4'
    if ' ' in s and '/' in s:
        try:
            whole, frac = s.split(' ', 1)
            num, den = frac.split('/', 1)
            return float(whole) + float(num) / float(den)
        except (ValueError, ZeroDivisionError):
            pass
    
    # Pure fraction: '1/2', '3/4'
    if '/' in s:
        try:
            num, den = s.split('/', 1)
            return float(num) / float(den)
        except (ValueError, ZeroDivisionError):
            return None
    
    try:
        return float(s)
    except ValueError:
        return None


def _detect_below_pattern(size_str: str) -> bool:
    """Return True if size_str contains '& Below' or 'and below' pattern."""
    if not size_str:
        return False
    pattern = SIZE_EXPANSION_CONFIG.get("below_pattern_regex", r'(?:&|and)\s*below')
    return bool(re.search(pattern, str(size_str), re.IGNORECASE))


def _detect_range_pattern(size_str: str) -> bool:
    """Detect if size string contains ANY range pattern (below, thru, to, etc.).
    Matches: '1.5 & Below', '1/2" thru 1-1/2"', '≤ 1.5', 'up to 2"', etc.
    """
    if not size_str:
        return False
    
    patterns = SIZE_EXPANSION_CONFIG.get("range_pattern_regexes", [
        r'(?:&|and)\s*below',
        r'\bthru\b',
        r'\bthrough\b',
        r'\bto\b',
        r'up\s+to',
        r'≤|<=',
        r'\band\s+smaller\b',
        r'\band\s+less\b',
    ])
    
    for pattern in patterns:
        if re.search(pattern, str(size_str), re.IGNORECASE):
            return True
    return False


def _detect_size_range(size_from: str, size_to: str) -> bool:
    """Detect if component has a size RANGE (different from/to values).
    Returns True if from != to and both are valid numbers.
    """
    if not size_from or not size_to:
        return False
    
    from_val = _parse_size_value(size_from)
    to_val = _parse_size_value(size_to)
    
    if from_val is None or to_val is None:
        return False
    
    # Range detected if values are different
    return abs(from_val - to_val) > 0.01  # Allow tiny floating point diff


def _format_size_display(size_float: float) -> str:
    """Format a float size back to clean display format.
    
    Examples: 0.5 → "1/2", 0.75 → "3/4", 1.0 → "1", 1.5 → "1-1/2", 2.0 → "2"
    """
    # Common fractions
    if size_float == 0.125:
        return "1/8"
    elif size_float == 0.25:
        return "1/4"
    elif size_float == 0.375:
        return "3/8"
    elif size_float == 0.5:
        return "1/2"
    elif size_float == 0.75:
        return "3/4"
    elif size_float == 1.25:
        return "1-1/4"
    elif size_float == 1.5:
        return "1-1/2"
    elif size_float == 2.5:
        return "2-1/2"
    elif float(size_float).is_integer():
        return str(int(size_float))
    else:
        return str(size_float)


def expand_small_size_components(components: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Expand components with size ranges to individual size rows.
    
    Intelligently detects and expands:
    1. "Below" patterns: "1.5 & Below" → [0.5, 0.75, 1.0, 1.25, 1.5]
    2. Range patterns: "1/2\" thru 1-1/2\"" → [0.5, 0.75, 1.0, 1.25, 1.5]
    3. Explicit ranges: size_from="1/2", size_to="1-1/2" → individual rows
    
    Returns: expanded list of components (original + individual size rows)
    """
    if not SIZE_EXPANSION_CONFIG.get("enable_size_expansion", True):
        return components
    
    small_threshold = SIZE_EXPANSION_CONFIG.get("small_size_threshold", 1.5)
    medium_threshold = SIZE_EXPANSION_CONFIG.get("medium_size_threshold", 6.0)
    small_ladder = SIZE_EXPANSION_CONFIG.get("small_size_ladder", [0.5, 0.75, 1.0, 1.25, 1.5])
    medium_ladder = SIZE_EXPANSION_CONFIG.get("medium_size_ladder", [2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0])
    expand_all_ranges = SIZE_EXPANSION_CONFIG.get("expand_all_ranges", True)
    duplicate_rows = SIZE_EXPANSION_CONFIG.get("duplicate_expanded_rows", True)
    log_expansions = SIZE_EXPANSION_CONFIG.get("log_expansions", True)
    
    expanded: List[Dict[str, Any]] = []
    expansion_count = 0
    
    for comp in components:
        size_from_str = (comp.get("size_from") or "").strip()
        size_to_str = (comp.get("size_to") or "").strip()
        
        # Parse size values
        from_val = _parse_size_value(size_from_str)
        to_val = _parse_size_value(size_to_str)
        
        # Check for range patterns (below, thru, to, etc.)
        from_has_pattern = _detect_range_pattern(size_from_str)
        to_has_pattern = _detect_range_pattern(size_to_str)
        has_range_pattern = from_has_pattern or to_has_pattern
        
        # Check for explicit range (different from/to values)
        has_explicit_range = _detect_size_range(size_from_str, size_to_str)
        
        # Determine if expansion should happen
        should_expand = False
        max_size = None
        min_size = None
        
        if has_range_pattern:
            # Pattern detected (e.g., "1.5 & Below", "thru 1-1/2")
            should_expand = True
            if from_has_pattern and from_val is not None:
                max_size = from_val
            elif to_has_pattern and to_val is not None:
                max_size = to_val
            min_size = 0.5  # Default minimum for range patterns
            
        elif expand_all_ranges and has_explicit_range:
            # Explicit range detected (e.g., from="1/2", to="1-1/2")
            should_expand = True
            if from_val is not None and to_val is not None:
                min_size = min(from_val, to_val)
                max_size = max(from_val, to_val)
        
        # If no expansion needed, keep original
        if not should_expand or max_size is None:
            expanded.append(comp)
            continue
        
        # Determine which size ladder to use
        if max_size <= small_threshold:
            size_ladder = small_ladder
        elif max_size <= medium_threshold:
            size_ladder = medium_ladder
        else:
            # Size too large for expansion - keep original
            expanded.append(comp)
            continue
        
        # Expansion triggered - generate individual rows
        if log_expansions:
            logger.info(
                "[SizeExpansion] Expanding component %s | size_from=%s, size_to=%s → ladder %s (max=%.2f)",
                comp.get("sub_type") or comp.get("component_type"),
                size_from_str,
                size_to_str,
                size_ladder,
                max_size
            )
        
        # Keep original row if configured
        if duplicate_rows:
            expanded.append(comp)
        
        # Generate individual rows for each size in the applicable ladder
        for individual_size in size_ladder:
            # Only include sizes within the detected range
            if min_size is not None and individual_size < min_size:
                continue
            if individual_size > max_size:
                continue
            
            expanded_comp = dict(comp)  # Copy all fields
            size_display = _format_size_display(individual_size)
            
            # Set both size_from and size_to to the same value (single size)
            expanded_comp["size_from"] = size_display
            expanded_comp["size_to"] = size_display
            
            # Add audit note
            original_note = expanded_comp.get("notes", "") or ""
            expansion_note = f"[Auto-expanded from range: '{size_from_str}' to '{size_to_str}']"
            expanded_comp["notes"] = f"{original_note} {expansion_note}".strip()
            
            expanded.append(expanded_comp)
            expansion_count += 1
    
    if log_expansions and expansion_count > 0:
        logger.info(
            "[SizeExpansion] Generated %d individual size rows from %d range patterns",
            expansion_count,
            sum(1 for c in components if _detect_range_pattern(c.get("size_from", "")) or 
                                          _detect_range_pattern(c.get("size_to", "")) or
                                          (expand_all_ranges and _detect_size_range(c.get("size_from", ""), c.get("size_to", ""))))
        )
    
    return expanded


# ─────────────────────────────────────────────────────────────────────────────
# Main Quality Processing Pipeline
# ─────────────────────────────────────────────────────────────────────────────
def process_extracted_classes(
    classes: List[Dict[str, Any]],
    project_id: Optional[str] = None,
    project_title: Optional[str] = None,
    document_number: Optional[str] = None
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Run full data quality pipeline on extracted classes.
    
    Returns:
        (cleaned_classes, quality_report)
    """
    quality_report = {
        "input_classes": len(classes),
        "output_classes": 0,
        "removed_low_quality": 0,
        "total_components_before": 0,
        "total_components_after": 0,
        "duplicates_removed": 0,
        "fields_normalized": 0,
        "fields_filled": 0,
        "size_expansions": 0,  # NEW: Track small size expansions
    }
    
    cleaned: List[Dict[str, Any]] = []
    
    for cls in classes:
        # Count original components
        orig_comp_count = len(cls.get("components", []))
        quality_report["total_components_before"] += orig_comp_count
        
        # Step 1: Validate empty fields
        if not validate_empty_fields(cls):
            quality_report["removed_low_quality"] += 1
            continue
        
        # Step 2: Fill missing fields with intelligent defaults
        original_cls = dict(cls)
        cls = fill_missing_fields_with_defaults(cls)
        for field in DATA_QUALITY_CONFIG["critical_fields"]:
            if not original_cls.get(field) and cls.get(field):
                quality_report["fields_filled"] += 1
        
        # Step 3: Validate and normalize standards
        cls = validate_and_normalize_standards(cls)
        if cls.get("pressure_rating") != original_cls.get("pressure_rating"):
            quality_report["fields_normalized"] += 1
        if cls.get("flange_facing") != original_cls.get("flange_facing"):
            quality_report["fields_normalized"] += 1
        
        # Step 4: Deduplicate components
        original_comps = cls.get("components", [])
        cls["components"] = deduplicate_components(original_comps)
        dupes = len(original_comps) - len(cls["components"])
        quality_report["duplicates_removed"] += dupes
        
        # Step 4a: Expand "1.5 & Below" size patterns (NEW - Phase 2)
        # This generates individual component rows for each small size (0.5, 0.75, 1.0, 1.25, 1.5)
        # when the spec uses umbrella notation like "1½" & BELOW"
        components_before_expansion = len(cls["components"])
        cls["components"] = expand_small_size_components(cls["components"])
        if len(cls["components"]) > components_before_expansion:
            expansions = len(cls["components"]) - components_before_expansion
            quality_report["size_expansions"] += expansions
            logger.info(
                "[DataQuality] Class %s: expanded %d → %d components (+%d from small size auto-expansion)",
                cls.get("class_code", "?"),
                components_before_expansion,
                len(cls["components"]),
                expansions
            )
        
        quality_report["total_components_after"] += len(cls["components"])
        
        # Step 5: Enrich with project context
        cls = enrich_with_project_context(cls, project_id, project_title, document_number)
        
        # Step 6: Update confidence score based on data quality
        completeness = calculate_field_completeness(cls)
        has_components = len(cls.get("components", [])) > 0
        original_confidence = float(cls.get("confidence", 0.0) or 0.0)
        
        # Boost confidence if data is complete and has components
        quality_boost = 0.0
        if completeness > 0.8:
            quality_boost += 0.1
        if has_components and len(cls["components"]) >= 5:
            quality_boost += 0.1
        
        cls["confidence"] = min(1.0, original_confidence + quality_boost)
        cls["_data_completeness"] = completeness
        
        cleaned.append(cls)
    
    quality_report["output_classes"] = len(cleaned)
    
    logger.info(
        "[DataQuality] Pipeline complete: %d → %d classes, "
        "%d components deduped, %d size expansions, %d fields normalized, %d fields filled",
        quality_report["input_classes"],
        quality_report["output_classes"],
        quality_report["duplicates_removed"],
        quality_report["size_expansions"],
        quality_report["fields_normalized"],
        quality_report["fields_filled"],
    )
    
    return cleaned, quality_report
