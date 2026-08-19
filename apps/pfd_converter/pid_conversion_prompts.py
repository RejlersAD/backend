"""
PFD → P&ID Conversion Prompts (soft-coded)
==========================================

Soft-coded prompt builder for the "Intelligent Diagram Conversion Engine"
that powers the PFD upload page (/pfd/upload).

Design rules:
- Every section, toggle and threshold lives in `PFD_PROMPT_CONFIG` and can
  be overridden per environment via `PFD_PROMPT_<UPPER_KEY>` env vars.
- The output JSON schema is ADDITIVE — it preserves the legacy keys
  (equipment, process_streams, instruments, control_loops, valves,
  text_annotations, utilities) that the downstream pipeline already
  parses, AND adds the new engineering-grade keys requested by the user
  (pipelines, control_logic, safety_systems, pigging_system, connections).
- Nothing here imports from the rest of the pfd_converter package, so it
  is safe to drop in or out without disturbing core conversion logic.

Public API
----------
build_user_prompt(engineering_context: dict | None = None) -> str
build_system_prompt() -> str
PFD_PROMPT_CONFIG: dict   # for introspection / health-checks
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional


# ─── SOFT-CODED CONFIG ────────────────────────────────────────────────────
PFD_PROMPT_CONFIG: Dict[str, Any] = {
    # Role / persona
    "role_title": "Senior Oil & Gas Process Engineer & Intelligent Diagram Conversion Engine",
    "role_standards": [
        "ADNOC DEP",
        "Shell DEP",
        "Saudi Aramco SAES",
        "ISA-5.1",
        "ISO 14617 / ISO 15926",
        "ASME B31.3",
        "API RP 520 / 521 / 14C",
    ],

    # Master toggles for each enrichment section the prompt asks the model to perform.
    # Disabling a section trims the prompt at runtime — useful if a project doesn't
    # need (e.g.) pigging logic.
    "sections_enabled": {
        "equipment_mapping":          True,
        "line_pipe_specification":    True,
        "valve_insertion":            True,
        "instrumentation_enrichment": True,
        "safety_and_control_logic":   True,
        "pigging_system_logic":       True,
        "connections":                True,
        "intelligent_regeneration":   True,
        "validation_rules":           True,
        "consistency_mode":           True,
    },

    # Document acceptance policy (mirrors instrument-datasheet pages)
    "accept_doc_types": [
        "PFD",
        "P&ID",
        "Plot Plan",
        "Equipment List",
        "Line List",
        "Instrument Index",
        "Cause & Effect / SAFE Chart",
        "Process Datasheet",
    ],

    # Output schema — legacy keys MUST be preserved for downstream parser.
    "legacy_schema_keys": [
        "equipment",
        "process_streams",
        "instruments",
        "control_loops",
        "valves",
        "text_annotations",
        "utilities",
    ],
    # Additive engineering-grade keys requested by the v2 conversion engine.
    "extended_schema_keys": [
        "pipelines",
        "control_logic",
        "safety_systems",
        "pigging_system",
        "connections",
    ],

    # Tag conventions to enforce
    "tag_conventions": {
        "equipment": [
            "V-XXX (vessels)",
            "T-XXX (tanks/towers)",
            "P-XXX A/B (pumps)",
            "K-XXX (compressors)",
            "E-XXX (heat exchangers)",
            "C-XXX (columns)",
            "PL-XXX / PR-XXX (pig launcher / receiver)",
            "KOD-XXX (knockout drum)",
        ],
        "valves": [
            "SDV-XXX (shutdown valve)",
            "ESDV-XXX (emergency shutdown)",
            "MOV-XXX (motor-operated valve)",
            "XV-XXX (on/off valve)",
            "BDV-XXX (blowdown valve)",
            "PCV/FCV/LCV/TCV-XXX (control valves)",
            "PSV-XXX (pressure safety valve)",
            "CV-XXX (check valve)",
        ],
        "instruments_isa_5_1": [
            "Pressure: PI, PT, PIT, PIC, PSH, PSL, PSHH, PSLL",
            "Temperature: TI, TT, TIC, TW, TSH, TSL",
            "Flow: FE, FT, FI, FIC, FIT, FQIT, FCV, FSH, FSL",
            "Level: LI, LT, LIC, LG, LSH, LSL, LSHH, LSLL, LCV",
            "Analytical: AIT, QIT",
            "Logic: PSD, LSD, DPSD",
        ],
    },

    # Pigging-system mandatory items (CRITICAL block of the user's spec)
    "pigging_required_items": [
        "Kicker line (with isolation valves)",
        "Bypass line around launcher/receiver",
        "Vent line to flare or atmosphere",
        "Drain line to closed/open drain",
        "Pig signalers (upstream + downstream)",
        "Key-lock interlock sequence (DBB, vent, drain → open)",
        "Pressure indicators on barrel + kicker",
        "PSV protection on closed barrel",
    ],

    # Safety-critical components that MUST exist after enrichment
    "safety_critical_components": [
        "PSV on every isolatable pressure-containing equipment",
        "BDV on every gas-filled equipment >20 barg",
        "ESD valves on plant battery limits",
        "Emergency depressurization route to flare",
        "High/low alarms on all critical control loops",
        "Independent shutdown trip per IEC 61511 SIL target",
    ],

    # External connections that must be preserved (no floating nodes)
    "mandatory_connections": [
        "Flare header (HP / LP / acid)",
        "Closed drain header",
        "Open drain / oily water",
        "Export pipeline / battery limit",
        "Instrument air supply",
        "Nitrogen utility",
        "Fuel gas (if applicable)",
        "Cooling water / steam (if applicable)",
    ],

    # Validation rules the model must self-check before returning
    "validation_rules": [
        "No floating or disconnected nodes",
        "No duplicate equipment / instrument tags",
        "Every PSV has a relieving destination",
        "Every control valve belongs to a complete control loop",
        "Every isolatable section has DBB and vent/drain",
        "Tag numbers conform to project numbering convention",
        "All safety-critical components from the policy list are present",
    ],

    # Output controls
    "output_format": "json",
    "json_strict": True,        # require pure JSON, no prose
    "include_assumptions_log": True,   # echo what was inferred vs. observed
}


# ─── ENV OVERRIDE HELPER ──────────────────────────────────────────────────
def _pcfg(key: str, default: Any = None) -> Any:
    """
    Read a value from PFD_PROMPT_CONFIG with optional environment override.

    Env var name convention: PFD_PROMPT_<UPPER_KEY>
    Comma-separated env values are returned as lists; "true"/"false" become bool.
    """
    env_key = f"PFD_PROMPT_{key.upper()}"
    raw = os.environ.get(env_key)
    if raw is not None:
        if raw.lower() in ("true", "false"):
            return raw.lower() == "true"
        if "," in raw:
            return [v.strip() for v in raw.split(",") if v.strip()]
        return raw
    return PFD_PROMPT_CONFIG.get(key, default)


# ─── PROMPT FRAGMENTS ─────────────────────────────────────────────────────
def _section_equipment_mapping() -> str:
    return (
        "1️⃣  EQUIPMENT MAPPING\n"
        "    • Identify EVERY major piece of equipment (Pig Launcher/Receiver, KOD,\n"
        "      vessels, pumps, exchangers, columns, pipelines, etc.).\n"
        "    • Preserve all tag numbers, sizes, design P/T, materials of construction.\n"
        f"    • Tag conventions to enforce: {', '.join(PFD_PROMPT_CONFIG['tag_conventions']['equipment'])}.\n"
    )


def _section_line_pipe() -> str:
    return (
        "2️⃣  LINE & PIPE SPECIFICATION\n"
        "    • Convert every process line into a fully-specified pipeline:\n"
        "      line size, piping class/spec, schedule, material, flow direction.\n"
        "    • Maintain continuity end-to-end — never produce a dangling pipe segment.\n"
    )


def _section_valves() -> str:
    return (
        "3️⃣  VALVE INSERTION\n"
        "    • Automatically introduce SDV / ESDV, MOV, manual block, check, and\n"
        "      control valves following standard upstream / downstream placement.\n"
        f"    • Tag conventions: {', '.join(PFD_PROMPT_CONFIG['tag_conventions']['valves'])}.\n"
        "    • Each valve MUST declare: tag, type, size, body class, fail position\n"
        "      (FC/FO/FL), actuator (manual/pneumatic/MOV/SOV), location reference.\n"
    )


def _section_instruments() -> str:
    return (
        "4️⃣  INSTRUMENTATION ENRICHMENT (ISA-5.1)\n"
        "    • Add and map every instrument under ISA-5.1 nomenclature:\n"
        f"      {chr(10).join('      - ' + line for line in PFD_PROMPT_CONFIG['tag_conventions']['instruments_isa_5_1'])}\n"
        "    • Build complete control loops (PCV, LCV, FCV, TCV …) with controller,\n"
        "      transmitter, final element and setpoint variable.\n"
        "    • Maintain proper tagging hierarchy: <area>-<type>-<loop>.\n"
    )


def _section_safety() -> str:
    items = "\n".join(f"      - {c}" for c in PFD_PROMPT_CONFIG["safety_critical_components"])
    return (
        "5️⃣  SAFETY & CONTROL LOGIC\n"
        "    • Include PSV, BDV, ESD logic, flare/vent connections, drain connections.\n"
        "    • Reflect interlocks (e.g. SDV closure on LSLL, ESD trip cascades).\n"
        "    Mandatory safety-critical components:\n"
        f"{items}\n"
    )


def _section_pigging() -> str:
    items = "\n".join(f"      - {p}" for p in PFD_PROMPT_CONFIG["pigging_required_items"])
    return (
        "6️⃣  PIGGING SYSTEM LOGIC (CRITICAL)\n"
        "    For every pig launcher / receiver detected, add:\n"
        f"{items}\n"
    )


def _section_connections() -> str:
    items = "\n".join(f"      - {c}" for c in PFD_PROMPT_CONFIG["mandatory_connections"])
    return (
        "7️⃣  EXTERNAL CONNECTIONS (no floating nodes)\n"
        f"{items}\n"
    )


def _section_regeneration() -> str:
    return (
        "8️⃣  INTELLIGENT REGENERATION\n"
        "    • If input is incomplete or ambiguous, INFER the missing elements using\n"
        "      standard oil & gas engineering practice.\n"
        "    • Cross-reference with any provided sample P&ID and reuse its style /\n"
        "      density / numbering scheme.\n"
        "    • Log every inferred item in `assumptions_log` so engineers can review.\n"
    )


def _section_validation() -> str:
    items = "\n".join(f"      - {v}" for v in PFD_PROMPT_CONFIG["validation_rules"])
    return (
        "🔟  VALIDATION RULES — self-check before responding\n"
        f"{items}\n"
    )


def _section_consistency() -> str:
    standards = ", ".join(PFD_PROMPT_CONFIG["role_standards"])
    return (
        "1️⃣1️⃣  CONSISTENCY MODE\n"
        f"    • Follow {standards} conventions where applicable.\n"
        "    • Match style and density of the reference P&ID if supplied.\n"
    )


# Section-key → builder lookup. Order is the order they appear in the prompt.
_SECTION_BUILDERS = [
    ("equipment_mapping",          _section_equipment_mapping),
    ("line_pipe_specification",    _section_line_pipe),
    ("valve_insertion",            _section_valves),
    ("instrumentation_enrichment", _section_instruments),
    ("safety_and_control_logic",   _section_safety),
    ("pigging_system_logic",       _section_pigging),
    ("connections",                _section_connections),
    ("intelligent_regeneration",   _section_regeneration),
    ("validation_rules",           _section_validation),
    ("consistency_mode",           _section_consistency),
]


# ─── OUTPUT-SCHEMA SKELETON ───────────────────────────────────────────────
def _output_schema_block() -> str:
    """Emit a JSON skeleton listing all required keys (legacy + extended)."""
    legacy = _pcfg("legacy_schema_keys", [])
    extended = _pcfg("extended_schema_keys", [])
    all_keys = list(legacy) + [k for k in extended if k not in legacy]

    sample_lines = []
    for k in all_keys:
        sample_lines.append(f'  "{k}": []')
    if _pcfg("include_assumptions_log", True):
        sample_lines.append('  "assumptions_log": []')
    sample = ",\n".join(sample_lines)

    return (
        "📤  OUTPUT FORMAT — return STRICT JSON, no prose, no markdown fences:\n"
        "{\n"
        f"{sample}\n"
        "}\n"
        "\n"
        "Each list contains objects whose minimal shape is:\n"
        '  equipment[i]:        {"tag","type","description","design_pressure","design_temperature","material","position":{"x","y"}}\n'
        '  pipelines[i]:        {"line_no","size","class","material","from_tag","to_tag","flow_direction","insulation"}\n'
        '  process_streams[i]:  {"stream_id","name","source","destination","flow_rate","pressure","temperature","phase"}\n'
        '  valves[i]:           {"tag","type","size","fail_position","actuator","upstream_tag","downstream_tag"}\n'
        '  instruments[i]:      {"tag","type","measured_variable","connected_to","range","signal"}\n'
        '  control_loops[i]:    {"controller","transmitter","final_element","controlled_variable","setpoint"}\n'
        '  control_logic[i]:    {"id","trigger","action","interlock_targets":[],"sil"}\n'
        '  safety_systems[i]:   {"tag","type":"PSV|BDV|ESD|HIPPS","setpoint","relieves_to","protects_tag"}\n'
        '  pigging_system[i]:   {"barrel_tag","kicker_line","bypass_line","vent_line","drain_line","signalers":[],"interlock":""}\n'
        '  connections[i]:      {"from_tag","to_tag","header":"flare|closed_drain|open_drain|export|IA|N2"}\n'
        '  text_annotations[i]: {"text","type","location"}\n'
        '  utilities[i]:        {"type","supply_header","return_header","connected_equipment":[]}\n'
        '  assumptions_log[i]:  {"item","rationale","source":"observed|inferred|reference_pid"}\n'
    )


# ─── ENGINEERING-CONTEXT BLOCK ────────────────────────────────────────────
def _engineering_context_block(ctx: Optional[Dict[str, str]]) -> str:
    if not ctx:
        return ""
    parts = [f"    • {k.replace('_', ' ').title()}: {v}" for k, v in ctx.items() if v]
    if not parts:
        return ""
    return "🧭  ENGINEERING CONTEXT (use as ground truth)\n" + "\n".join(parts) + "\n\n"


# ─── PUBLIC BUILDERS ──────────────────────────────────────────────────────
def build_system_prompt() -> str:
    role = _pcfg("role_title", "Senior Oil & Gas Process Engineer")
    standards = ", ".join(_pcfg("role_standards", []))
    return (
        f"You are a {role}.\n"
        f"You comply with {standards} when transforming PFDs into P&IDs.\n"
        "You return STRICT JSON only — never markdown, never prose explanation.\n"
        "You self-validate against the validation rules before responding.\n"
        "You preserve all existing tag numbers, naming conventions, and project standards.\n"
    )


def build_user_prompt(engineering_context: Optional[Dict[str, str]] = None) -> str:
    """
    Build the full PFD → P&ID conversion prompt.

    `engineering_context` (optional) is the dict of project-specific overrides
    captured on the upload form (fluid_service, operating_pressure, etc.).
    """
    enabled = _pcfg("sections_enabled", {}) or {}
    accept_docs = ", ".join(_pcfg("accept_doc_types", []))

    header = (
        "🎯  ROLE: You are an expert Oil & Gas Process Engineer and Intelligent\n"
        "    Diagram Conversion Engine. Convert the supplied Process Flow Diagram\n"
        "    (PFD) into a fully-detailed Piping & Instrumentation Diagram (P&ID)\n"
        "    with engineering-grade accuracy.\n"
        "\n"
        "🛡️  CONSTRAINTS\n"
        "    • Do NOT invent tag numbers that conflict with the input.\n"
        "    • Maintain strict consistency with existing tag numbering and naming.\n"
        f"    • Acceptable input documents: {accept_docs}.\n"
        "\n"
    )

    body_parts: List[str] = []
    for key, builder in _SECTION_BUILDERS:
        if enabled.get(key, True):
            body_parts.append(builder())

    schema_block = _output_schema_block()
    ctx_block = _engineering_context_block(engineering_context)

    closing = (
        "\nFINAL CHECK BEFORE RESPONDING:\n"
        "    1. Every equipment, valve, instrument has a unique tag.\n"
        "    2. Every node connects to something — no floating items.\n"
        "    3. The JSON is syntactically valid and contains every required key.\n"
        "    4. Inferred items are listed in `assumptions_log`.\n"
        "\nReturn the JSON now.\n"
    )

    return header + ctx_block + "\n".join(body_parts) + "\n" + schema_block + closing
