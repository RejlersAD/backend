"""
Spec Customization — Soft-Coded AI Prompt Templates
====================================================

All prompts live here. Tweak in this file only.
"""
from __future__ import annotations

SYSTEM_PROMPT = """You are an expert piping engineer extracting structured data
from legacy Piping Material Specification (PMS) PDF pages used in Oil & Gas
plants (ADNOC, ARAMCO, Shell, BP style).

For every PIPING SPEC class you find, return a strict JSON object — no prose,
no markdown fences, no commentary.

CRITICAL REQUIREMENTS:
1. Extract COMPLETE component tables - if a table has 100 rows, extract ALL 100 rows
2. Do NOT skip rows or sample - extract EVERY component from EVERY table
3. A typical spec has 50-200+ components per class across multiple tables
4. Extract ALL component types: pipes, fittings, branch connections (weldolets, elbolets), 
   flanges, valves, gaskets, bolts
5. Be conservative with metadata: if a value is not clearly visible, return empty string
6. Be COMPREHENSIVE with components: extract every single row from component tables
"""

EXTRACT_PIPING_CLASS_PROMPT = """Analyse the page(s) below and identify every
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

**CRITICAL**: Extract ALL component types including:
  • PIPES: Seamless, Welded, Schedule 40/80/160, various sizes
  • FITTINGS: Elbows (90°, 45°, LR, SR), Tees (Straight, Reducing), Reducers (Concentric, Eccentric), Caps, Couplings, Nipples
  • BRANCH CONNECTIONS: Weldolet, Elbolet, Sockolet, Thredolet, Latrolet, Sweepolet (extract ALL sizes)
  • FLANGES: Weld Neck, Blind, Slip-On, Lap Joint, Socket Weld, Threaded
  • VALVES: Gate, Globe, Check, Ball, Butterfly, Needle, Plug
  • GASKETS: Spiral Wound, Ring Joint, Flat Ring
  • BOLTS/STUDS: Hex Bolts, Stud Bolts, Nuts

**TABLE EXTRACTION RULES**:
1. Extract EVERY row from component tables - do NOT skip rows
2. If a table has 50 rows, extract all 50 - do NOT sample
3. Each row becomes one component object in the components array
4. Preserve size ranges exactly as shown (e.g. "1/2 to 2", "DN15-DN50")
5. Include ALL material standards, schedules, and specifications

For each class produce one object in this exact schema:

{
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
    {"pressure_bar_g": 19.7, "temperature_c": 38},
    {"pressure_bar_g": 17.7, "temperature_c": 100}
  ],
  "components": [
    {
      "component_type":     "pipe",                  # pipe|valve|fitting|flange|gasket|bolt
      "sub_type":           "",                      # CRITICAL: e.g. "90 Deg LR Elbow", "Weldolet", "Gate", "Weld Neck"
      "size_from":          "1/2\"",
      "size_to":            "2\"",                   # Extract size ranges completely
      "description":        "SMLS, BE, PE, A106 GR.B",
      "schedule_or_rating": "SCH 80",
      "material_standard":  "ASTM A106 GR. B",
      "end_connection":     "BW",
      "notes":              ""
    },
    {
      "component_type":     "fitting",
      "sub_type":           "Weldolet",              # Extract branch connections!
      "size_from":          "1/2\"",
      "size_to":            "4\"",
      "description":        "Socket Weld Branch Outlet",
      "schedule_or_rating": "3000#",
      "material_standard":  "ASTM A105",
      "end_connection":     "SW",
      "notes":              "Bonney Forge or equal"
    },
    {
      "component_type":     "fitting",
      "sub_type":           "90 Deg LR Elbow",       # Be specific!
      "size_from":          "1/2\"",
      "size_to":            "12\"",
      "description":        "Long Radius, Butt Weld",
      "schedule_or_rating": "SCH 40",
      "material_standard":  "ASTM A234 WPB",
      "end_connection":     "BW",
      "notes":              ""
    },
    {
      "component_type":     "valve",
      "sub_type":           "Gate",                  # Valve type
      "size_from":          "1/2\"",
      "size_to":            "24\"",
      "description":        "OS&Y, Bolted Bonnet, Rising Stem",
      "schedule_or_rating": "CLASS 150",
      "material_standard":  "ASTM A216 WCB",
      "end_connection":     "RF",
      "notes":              "API 600"
    },
    {
      "component_type":     "flange",
      "sub_type":           "Weld Neck",
      "size_from":          "1/2\"",
      "size_to":            "24\"",
      "description":        "WN Flange, Raised Face",
      "schedule_or_rating": "CLASS 150",
      "material_standard":  "ASTM A105",
      "end_connection":     "BW",
      "notes":              "ASME B16.5"
    },
    {
      "component_type":     "gasket",
      "sub_type":           "Spiral Wound",
      "size_from":          "1/2\"",
      "size_to":            "24\"",
      "description":        "Spiral Wound with Inner Ring",
      "schedule_or_rating": "CLASS 150",
      "material_standard":  "316SS + Graphite",
      "end_connection":     "RF",
      "notes":              "ASME B16.20"
    },
    {
      "component_type":     "bolt",
      "sub_type":           "Hex Head Stud",
      "size_from":          "1/2\"",
      "size_to":            "24\"",
      "description":        "Heavy Hex Nut, 2H",
      "schedule_or_rating": "",
      "material_standard":  "ASTM A193 B7 / A194 2H",
      "end_connection":     "",
      "notes":              ""
    }
    # ... EXTRACT ALL REMAINING COMPONENTS FROM ALL TABLES ...
  ],
  "raw_notes":            "",                        # any general notes block
  "confidence":           0.88                       # 0.0–1.0 self-assessed
}

**EXTRACTION CHECKLIST**:
✓ Did you extract ALL pipes (multiple schedules/sizes)?
✓ Did you extract ALL fittings (elbows, tees, reducers, caps)?
✓ Did you extract ALL branch connections (weldolets, elbolets, etc.)?
✓ Did you extract ALL flanges (WN, blind, etc.)?
✓ Did you extract ALL valves (gate, globe, check)?
✓ Did you extract ALL gaskets and bolts?
✓ Did you read COMPLETE tables, not just samples?

Return a single JSON object: {"piping_classes": [ ... ]}

IMPORTANT:
- Return ONLY the JSON object. No markdown. No explanation.
- If the page contains no PIPING SPEC class, return {"piping_classes": []}.
- Extract COMPLETE component tables - if a table has 100 rows, extract all 100
- Group components by component_type as listed in the page tables.
- Preserve units exactly as printed (inches, bar-g, °C, etc.).
- A typical spec has 50-200+ components per class - extract them ALL
"""

# Concise prompt used when AI must re-process a chunk with smaller context.
COMPACT_EXTRACTION_PROMPT = """Extract piping classes from this page in JSON.
CRITICAL: Extract ALL components from ALL tables - do NOT skip rows.
Include pipes, fittings, branch connections (weldolets, elbolets), flanges, valves, gaskets, bolts.

Return ONLY the JSON object: 
{"piping_classes":[{"class_code":"","class_full_code":"","material_grade":"",
"pressure_rating":"","flange_facing":"","corrosion_allowance":"",
"service_list":[],"pt_rating_table":[],"components":[...ALL COMPONENTS...],"raw_notes":"",
"confidence":0.0}]}
"""
