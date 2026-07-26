"""
Spec Customization — Soft-Coded AI Prompt Templates
====================================================

All prompts live here. Tweak in this file only.

`build_extraction_prompt()` dynamically assembles the "valid component
catalog" section from `exporters/smartplant_config.py` (CAT_SHEET_DEFAULTS /
_CAT_ROUTING_RULES / GENERIC_FITTING_FAMILY_SHEETS) — the single source of
truth for what SP3D can bulkload. Adding a new CAT sheet or routing rule
there automatically updates what the AI is told to extract; no manual edits
needed here. Mirrors the pattern used by
`electrical_checklist/handwriting_extractor.py::_build_vision_prompt()`
(field dictionary generated from TEMPLATE_V2_SECTIONS).
"""
from __future__ import annotations
from .config import COMPONENT_TYPE_DETECTION_CONFIG

SYSTEM_PROMPT = """You are an expert piping engineer extracting structured data
from legacy Piping Material Specification (PMS) PDF pages used in Oil & Gas
plants (ADNOC, ARAMCO, Shell, BP style).

For every PIPING SPEC class you find, return a strict JSON object — no prose,
no markdown fences, no commentary.

CRITICAL REQUIREMENTS:
1. Extract COMPLETE component tables - if a table has 100 rows, extract ALL 100 rows
2. Do NOT skip rows or sample - extract EVERY component from EVERY table
3. A typical spec has 50-200+ components per class across multiple tables
4. Extract ALL component types: pipes, fittings, branch connections (weldolets, sockolets, elbolets), 
   flanges (including general flanges), valves (including vent & drain valves), gaskets, bolts
5. Be conservative with metadata: if a value is not clearly visible, return empty string
6. Be COMPREHENSIVE with components: extract every single row from component tables
7. NEVER skip valves, gaskets, sockolets, or general flanges - these are critical components
"""


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic "valid component catalog" section — built from smartplant_config.py
# ─────────────────────────────────────────────────────────────────────────────
_BUCKET_TITLES = [
    ('pipe',    'PIPES'),
    ('flange',  'FLANGES'),
    ('fitting', 'FITTINGS (be specific — use the exact shape name below, not just "fitting")'),
    ('valve',   'VALVES (be specific — use the exact type name below, not just "valve")'),
    ('gasket',  'GASKETS'),
    ('bolt',    'BOLTS/STUDS'),
]


def _build_component_catalog_text() -> str:
    """List every CAT sheet target grouped by component_type bucket, derived
    live from `smartplant_config._CAT_ROUTING_RULES` — so the AI is always
    told the exact SP3D shape/type vocabulary the routing engine understands."""
    from .exporters import smartplant_config as cat_cfg

    by_bucket: dict[str, list[str]] = {}
    for bucket, _pattern, sheet in cat_cfg._CAT_ROUTING_RULES:
        sheets = by_bucket.setdefault(bucket, [])
        if sheet not in sheets:
            sheets.append(sheet)

    lines = []
    for bucket, title in _BUCKET_TITLES:
        sheets = by_bucket.get(bucket, [])
        if not sheets:
            continue
        lines.append(f"  • {title}: {', '.join(sheets)}")
    return "\n".join(lines)


def _build_priority_component_checklist() -> str:
    """Build a comprehensive checklist of priority component sub-types from
    COMPONENT_TYPE_DETECTION_CONFIG. This guides the AI to extract ALL critical
    component types that users commonly report as missing."""
    if not COMPONENT_TYPE_DETECTION_CONFIG.get("enable_enhanced_detection", True):
        return ""
    
    priority_subtypes = COMPONENT_TYPE_DETECTION_CONFIG.get("priority_subtypes", {})
    
    if not priority_subtypes:
        return ""
    
    lines = ["**PRIORITY COMPONENTS TO EXTRACT (do NOT skip these)**:"]
    
    for comp_type, subtypes in priority_subtypes.items():
        type_display = comp_type.upper()
        subtype_list = ", ".join(subtypes)
        lines.append(f"  • {type_display}: {subtype_list}")
    
    lines.append("")
    lines.append("If you find ANY of these components in the table, extract them. "
                 "Missing these components is a critical extraction error.")
    
    return "\n".join(lines)


def _build_generic_fitting_family_note() -> str:
    """Explain the generic-fitting-family escape hatch, listing the exact
    weld-type keys the fan-out builder understands (kept in sync with
    `smartplant_config.GENERIC_FITTING_FAMILY_SHEETS`)."""
    from .exporters import smartplant_config as cat_cfg

    weld_types = ", ".join(f'"{k}"' for k in cat_cfg.GENERIC_FITTING_FAMILY_SHEETS)
    return f"""Some specs describe an ENTIRE weld-type family with ONE umbrella spec line
instead of naming a shape — e.g. a row literally labelled "B.W. FITTINGS" or
"S.W. FITTINGS" or "SCRD FITTINGS" covering ALL elbows/tees/reducers/caps of
that weld type at once, with no specific shape given. For THESE rows only:
  - Do NOT guess a shape (do not invent "elbow" or "tee").
  - Set "is_generic_fitting_family": true and "weld_type" to one of: {weld_types}.
  - Leave "sub_type" as the umbrella label as printed (e.g. "B.W. Fittings").
For every OTHER fitting row where a specific shape IS given (the normal case),
leave "is_generic_fitting_family": false and use the specific shape name."""


def build_extraction_prompt() -> str:
    """Assemble the full piping-class extraction prompt. The "valid component
    catalog" and "generic fitting family" sections are generated dynamically
    from smartplant_config.py (see module docstring); everything else is a
    static template. Call this instead of importing a static prompt string
    so routing-rule/CAT-sheet changes are automatically reflected."""
    component_catalog = _build_component_catalog_text()
    generic_family_note = _build_generic_fitting_family_note()
    priority_checklist = _build_priority_component_checklist()
    
    return f"""Analyse the page(s) below and identify every
PIPING SPEC class (also known as: Piping Material Specification, PMS, Line Class,
Pipe Class, Material Class, Spec Code, or similar). The header may appear in any
of these forms — treat them all as piping class headers:
  • "PIPING SPEC: A"      • "PIPING SPECIFICATION: A1"
  • "PIPING CLASS A"      • "PIPE CLASS A"
  • "LINE CLASS: A1B"     • "MATERIAL CLASS A"
  • "SPEC CODE: A1A"      • "P.M.S. A"  / "PMS: A"
  • "CLASS 150-A"         • "PIPING MATERIAL SPECIFICATION A"

For each class, extract the complete component table. Most piping specs contain 
hundreds of components organized by type. You MUST extract ALL components from 
EVERY table on the page, not just samples.

**CRITICAL — VALID COMPONENT CATALOG (SP3D-aligned, derived from the live
CAT routing configuration; use these exact category buckets and, wherever
possible, name the SPECIFIC shape/type from the list so the row routes
correctly)**:
{component_catalog}

{priority_checklist}

**GENERIC FITTING-FAMILY ROWS (special case)**:
{generic_family_note}

**TABLE EXTRACTION RULES**:
1. Extract EVERY row from component tables - do NOT skip rows
2. If a table has 50 rows, extract all 50 - do NOT sample
3. Each row becomes one component object in the components array
4. Preserve size ranges exactly as shown (e.g. "1/2 to 2", "DN15-DN50", "1½" & BELOW")
5. Include ALL material standards, schedules, and specifications
6. Extract W.T. (Wall Thickness) / Schedule values EXACTLY as printed - do NOT normalize
   (e.g. "SCH. 80", "SCH.40", "3/8" THK", "NOTE 1" - keep spaces, periods, and formatting)

For each class produce one object in this exact schema:

{{
  "class_code":           "A",                       # single letter or short code
  "class_full_code":      "PIPING SPEC: A",          # full header line
  "material_grade":       "CARBON STEEL",            # principal material
  "pressure_rating":      "CLASS 150",               # ANSI / ASME class
  "flange_facing":        "RF",                      # RF / FF / RTJ
  "corrosion_allowance":  "1.5 mm",
  "service_list": [
    "General Process", "Sweet Fuel Gas", "L.P. Steam"
  ],
  "pt_rating_table": [
    {{"pressure_bar_g": 19.7, "temperature_c": 38}},
    {{"pressure_bar_g": 17.7, "temperature_c": 100}}
  ],
  "components": [
    {{
      "component_type":     "pipe",                  # pipe|valve|fitting|flange|gasket|bolt
      "sub_type":           "",                      # CRITICAL: e.g. "90 Deg LR Elbow", "Weldolet", "Gate", "Weld Neck"
      "size_from":          "1/2\\"",
      "size_to":            "2\\"",                   # Extract size ranges completely
      "description":        "SMLS, BE, PE, A106 GR.B",
      "schedule_or_rating": "SCH 80",
      "material_standard":  "ASTM A106 GR. B",
      "end_connection":     "BW",
      "notes":              "",
      "is_generic_fitting_family": false,             # true only for umbrella "B.W./S.W./SCRD FITTINGS" rows
      "weld_type":          ""                        # "BW"|"SW"|"SCRD" — only set when is_generic_fitting_family is true
    }},
    {{
      "component_type":     "fitting",
      "sub_type":           "Weldolet",              # Extract branch connections!
      "size_from":          "1/2\\"",
      "size_to":            "4\\"",
      "description":        "Socket Weld Branch Outlet",
      "schedule_or_rating": "3000#",
      "material_standard":  "ASTM A105",
      "end_connection":     "SW",
      "notes":              "Bonney Forge or equal",
      "is_generic_fitting_family": false,
      "weld_type":          ""
    }},
    {{
      "component_type":     "fitting",
      "sub_type":           "90 Deg LR Elbow",       # Be specific!
      "size_from":          "1/2\\"",
      "size_to":            "12\\"",
      "description":        "Long Radius, Butt Weld",
      "schedule_or_rating": "SCH 40",
      "material_standard":  "ASTM A234 WPB",
      "end_connection":     "BW",
      "notes":              "",
      "is_generic_fitting_family": false,
      "weld_type":          ""
    }},
    {{
      "component_type":     "fitting",
      "sub_type":           "B.W. Fittings",         # Umbrella family row — no shape named in source!
      "size_from":          "2\\"",
      "size_to":            "6\\"",
      "description":        "DIMS BS1640 PART 3",
      "schedule_or_rating": "SCH 40",
      "material_standard":  "ASTM A234 GR.WPB",
      "end_connection":     "BW",
      "notes":              "",
      "is_generic_fitting_family": true,
      "weld_type":          "BW"
    }},
    {{
      "component_type":     "valve",
      "sub_type":           "Gate",                  # Valve type
      "size_from":          "1/2\\"",
      "size_to":            "24\\"",
      "description":        "OS&Y, Bolted Bonnet, Rising Stem",
      "schedule_or_rating": "CLASS 150",
      "material_standard":  "ASTM A216 WCB",
      "end_connection":     "RF",
      "notes":              "API 600",
      "is_generic_fitting_family": false,
      "weld_type":          ""
    }},
    {{
      "component_type":     "flange",
      "sub_type":           "Weld Neck",
      "size_from":          "1/2\\"",
      "size_to":            "24\\"",
      "description":        "WN Flange, Raised Face",
      "schedule_or_rating": "CLASS 150",
      "material_standard":  "ASTM A105",
      "end_connection":     "BW",
      "notes":              "ASME B16.5",
      "is_generic_fitting_family": false,
      "weld_type":          ""
    }},
    {{
      "component_type":     "gasket",
      "sub_type":           "Spiral Wound",
      "size_from":          "1/2\\"",
      "size_to":            "24\\"",
      "description":        "Spiral Wound with Inner Ring",
      "schedule_or_rating": "CLASS 150",
      "material_standard":  "316SS + Graphite",
      "end_connection":     "RF",
      "notes":              "ASME B16.20",
      "is_generic_fitting_family": false,
      "weld_type":          ""
    }},
    {{
      "component_type":     "bolt",
      "sub_type":           "Hex Head Stud",
      "size_from":          "1/2\\"",
      "size_to":            "24\\"",
      "description":        "Heavy Hex Nut, 2H",
      "schedule_or_rating": "",
      "material_standard":  "ASTM A193 B7 / A194 2H",
      "end_connection":     "",
      "notes":              "",
      "is_generic_fitting_family": false,
      "weld_type":          ""
    }}
    # ... EXTRACT ALL REMAINING COMPONENTS FROM ALL TABLES ...
  ],
  "raw_notes":            "",                        # any general notes block
  "confidence":           0.88                       # 0.0–1.0 self-assessed
}}

**EXTRACTION CHECKLIST** (verify before submitting):
✓ Did you extract ALL pipes (multiple schedules/sizes)?
✓ Did you extract ALL fittings (elbows, tees, reducers, caps)?
✓ Did you extract ALL branch connections (weldolets, SOCKOLETS, elbolets, threadolets)?
✓ Did you extract ALL flanges (WN, blind, slip-on, FLANGES (GEN.), FLANGES GENERAL)?
✓ Did you extract ALL valves (gate, globe, CHECK VALVES, plug, ball, butterfly, VENT & DRAIN VALVES)?
✓ Did you extract ALL gaskets (GASKETS, spiral wound, ring joint, flat gaskets)?
✓ Did you extract ALL bolts and studs?
✓ Did you flag genuine umbrella "B.W./S.W./SCRD FITTINGS" rows with is_generic_fitting_family?
✓ Did you read COMPLETE tables, not just samples?
✓ Did you extract size ranges accurately (e.g., "1/2\" thru 1-1/2\"", "1.5 & Below")?

Return a single JSON object: {{"piping_classes": [ ... ]}}

IMPORTANT:
- Return ONLY the JSON object. No markdown. No explanation.
- If the page contains no PIPING SPEC class, return {{"piping_classes": []}}.
- Extract COMPLETE component tables - if a table has 100 rows, extract all 100
- Group components by component_type as listed in the page tables.
- Preserve units exactly as printed (inches, bar-g, °C, etc.).
- A typical spec has 50-200+ components per class - extract them ALL
"""




# Concise prompt used when AI must re-process a chunk with smaller context.
COMPACT_EXTRACTION_PROMPT = """Extract piping classes from this page in JSON.
CRITICAL: Extract ALL components from ALL tables - do NOT skip rows.
Include pipes, fittings, branch connections (weldolets, sockolets, elbolets), 
flanges (including FLANGES (GEN.)), valves (including CHECK VALVES and VENT & DRAIN VALVES), 
gaskets (GASKETS), bolts.

Return ONLY the JSON object: 
{"piping_classes":[{"class_code":"","class_full_code":"","material_grade":"",
"pressure_rating":"","flange_facing":"","corrosion_allowance":"",
"service_list":[],"pt_rating_table":[],"components":[...ALL COMPONENTS...],"raw_notes":"",
"confidence":0.0}]}
"""
