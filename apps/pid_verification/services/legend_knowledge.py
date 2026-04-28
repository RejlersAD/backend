"""
Legend Knowledge Service
========================
Extracts and persists reusable legend knowledge from legend sheets.
This enables future PID verification runs to reuse project legend prefixes.
"""
import json
import logging
import re
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[3]
LEGEND_KNOWLEDGE_PATH = BASE_DIR / "domain_knowledge" / "pid_verification" / "legend_knowledge.json"

# Conservative defaults to avoid over-learning noisy OCR tokens.
DEFAULT_INSTRUMENT_PREFIXES = {
    "FI", "FIC", "PI", "PIC", "TI", "TIC", "LI", "LIC",
    "AI", "AT", "FY", "PY", "LY", "FT", "PT", "LT", "TT",
}
DEFAULT_VALVE_PREFIXES = {
    "HV", "FV", "XV", "PV", "SDV", "BDV", "PSV", "PRV", "CV", "LV", "TV",
}


def extract_text_from_pdf(file_path: str) -> str:
    """Extract plain text from a legend PDF."""
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(file_path)
        chunks = []
        for page in doc:
            chunks.append(page.get_text("text"))
        doc.close()
        return "\n".join(chunks)
    except Exception as exc:
        logger.warning("[LegendKnowledge] PDF text extraction failed for %s: %s", file_path, exc)
        return ""


def _normalize_prefix(token: str) -> str | None:
    token = token.strip().upper()
    if not token:
        return None
    if len(token) < 1 or len(token) > 5:
        return None
    if not re.fullmatch(r"[A-Z]+", token):
        return None
    return token


def parse_legend_knowledge(raw_text: str) -> dict:
    """
    Parse legend text into reusable structured data.
    Extracts instrument/valve prefixes, service codes, insulation codes,
    piping spec descriptions, and department deviation codes.
    """
    instrument_prefixes = set(DEFAULT_INSTRUMENT_PREFIXES)
    valve_prefixes = set(DEFAULT_VALVE_PREFIXES)
    note_keywords = set()
    hold_keywords = set()

    # New: project-specific code lookups extracted from legend sheets
    service_codes: dict[str, str] = {}      # {'D': 'Drain', 'HC': 'Hydrocarbon Condensate', ...}
    insulation_codes: dict[str, str] = {}   # {'N': 'No Insulation', 'H': 'Hot Insulation', ...}
    piping_specs: dict[str, str] = {}       # {'033842': 'CS ANSI 150#', ...}
    dept_deviations: dict[str, str] = {}    # {'X': 'Deviation from standard', ...}

    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]

    # Patterns for code : description rows in legend sheets
    # Matches: "D - Drain", "HC: Hydrocarbon Condensate", "N – No Insulation"
    _code_desc_re = re.compile(
        r'^([A-Z0-9]{1,8})\s*[-:–]\s*([A-Za-z][^|]{2,80})$',
        re.IGNORECASE,
    )
    # Piping spec rows: "033842 - Carbon Steel ANSI 150#", "013842 - SS 150#"
    _piping_spec_re = re.compile(
        r'^(\d{4,8})\s*[-:–]\s*(.{4,80})$',
    )
    # Keywords that indicate a line is a fluid/service code table
    _FLUID_KEYWORDS = {
        'DRAIN', 'CONDENSATE', 'FLARE', 'HYDROCARBON', 'CRUDE', 'GAS',
        'WATER', 'STEAM', 'CHEMICAL', 'OIL', 'ACID', 'CAUSTIC', 'SOLVENT',
        'SLOP', 'BLOWDOWN', 'RELIEF', 'VENT', 'INSTRUMENT', 'UTILITY',
        'COOLING', 'PROCESS', 'SEWAGE', 'NITROGEN', 'FUEL', 'FIRE',
    }
    # Keywords that indicate a line is an insulation code table
    _INSUL_KEYWORDS = {
        'INSULATION', 'INSULATE', 'PERSONNEL PROTECTION', 'HEAT TRACING',
        'COLD', 'HOT', 'TRACE', 'ACOUSTIC', 'FIREPROOFING',
    }

    for line in lines:
        upper = line.upper()

        # ── Instrument / valve prefix detection (existing logic) ──────────
        m = re.match(r"^([A-Z]{1,5})\s*[-:]\s*([A-Za-z].+)$", line)
        if m:
            prefix = _normalize_prefix(m.group(1))
            desc = m.group(2).upper()
            if prefix:
                if "VALVE" in desc:
                    valve_prefixes.add(prefix)
                if any(word in desc for word in ["INDICAT", "CONTROLL", "TRANSMIT", "SWITCH", "INSTRUMENT", "ANALYZ"]):
                    instrument_prefixes.add(prefix)

        # ── Note/Hold hints ───────────────────────────────────────────────
        if "NOTE" in upper:
            note_keywords.add("NOTE")
        if "HOLD" in upper:
            hold_keywords.add("HOLD")

        # ── Piping spec rows (numeric codes) ─────────────────────────────
        pm = _piping_spec_re.match(line)
        if pm:
            code = pm.group(1).strip()
            desc = pm.group(2).strip()
            piping_specs[code] = desc
            continue

        # ── Generic code : description rows ──────────────────────────────
        cm = _code_desc_re.match(line)
        if not cm:
            continue

        code_raw = cm.group(1).strip().upper()
        desc_raw = cm.group(2).strip()
        desc_up = desc_raw.upper()

        # Classify: insulation code (short 1-2 letter codes + insulation keywords)
        if len(code_raw) <= 2 and any(kw in desc_up for kw in _INSUL_KEYWORDS):
            insulation_codes[code_raw] = desc_raw
            continue

        # Classify: fluid / service code (1-4 letters, fluid keywords OR in typical range)
        if re.fullmatch(r'[A-Z]{1,4}', code_raw):
            if any(kw in desc_up for kw in _FLUID_KEYWORDS):
                service_codes[code_raw] = desc_raw
            elif len(code_raw) <= 2:
                # Short codes with any description — likely service codes
                service_codes[code_raw] = desc_raw

        # Classify: dept deviation (single letter outside fluid/insulation)
        if len(code_raw) == 1 and code_raw not in service_codes and code_raw not in insulation_codes:
            dept_deviations[code_raw] = desc_raw

    return {
        "instrument_prefixes": sorted(instrument_prefixes),
        "valve_prefixes": sorted(valve_prefixes),
        "note_keywords": sorted(note_keywords),
        "hold_keywords": sorted(hold_keywords),
        "service_codes": service_codes,
        "insulation_codes": insulation_codes,
        "piping_specs": piping_specs,
        "dept_deviations": dept_deviations,
        "raw_line_count": len(lines),
    }


def build_legend_knowledge(file_paths: Iterable[str]) -> dict:
    """Build merged legend knowledge from one or more legend files."""
    merged_text = []
    sources = []
    for fp in file_paths:
        text = extract_text_from_pdf(fp)
        if text.strip():
            merged_text.append(text)
            sources.append(fp)

    parsed = parse_legend_knowledge("\n".join(merged_text))
    parsed["sources"] = sources
    return parsed


def save_legend_knowledge(knowledge: dict, output_path: Path | None = None) -> Path:
    """Persist legend knowledge JSON for future recognition."""
    target = output_path or LEGEND_KNOWLEDGE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(knowledge, indent=2), encoding="utf-8")
    return target


def load_legend_knowledge(path: Path | None = None) -> dict:
    """Load persisted legend knowledge, or return defaults if missing."""
    target = path or LEGEND_KNOWLEDGE_PATH
    if not target.exists():
        return {
            "instrument_prefixes": sorted(DEFAULT_INSTRUMENT_PREFIXES),
            "valve_prefixes": sorted(DEFAULT_VALVE_PREFIXES),
            "note_keywords": ["NOTE"],
            "hold_keywords": ["HOLD"],
            "service_codes": {},
            "insulation_codes": {},
            "piping_specs": {},
            "dept_deviations": {},
            "sources": [],
        }

    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        # Back-fill new keys for older JSON files
        data.setdefault("service_codes", {})
        data.setdefault("insulation_codes", {})
        data.setdefault("piping_specs", {})
        data.setdefault("dept_deviations", {})
        return data
    except Exception as exc:
        logger.warning("[LegendKnowledge] Failed to load %s: %s", target, exc)
        return {
            "instrument_prefixes": sorted(DEFAULT_INSTRUMENT_PREFIXES),
            "valve_prefixes": sorted(DEFAULT_VALVE_PREFIXES),
            "note_keywords": ["NOTE"],
            "hold_keywords": ["HOLD"],
            "service_codes": {},
            "insulation_codes": {},
            "piping_specs": {},
            "dept_deviations": {},
            "sources": [],
        }
