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
    Focuses on instrument and valve prefixes plus common note/hold keywords.
    """
    instrument_prefixes = set(DEFAULT_INSTRUMENT_PREFIXES)
    valve_prefixes = set(DEFAULT_VALVE_PREFIXES)
    note_keywords = set()
    hold_keywords = set()

    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]

    for line in lines:
        upper = line.upper()

        # Common legend row styles:
        #   FIC - Flow Indicating Controller
        #   HV: Hand Valve
        m = re.match(r"^([A-Z]{1,5})\s*[-:]\s*([A-Za-z].+)$", line)
        if m:
            prefix = _normalize_prefix(m.group(1))
            desc = m.group(2).upper()
            if prefix:
                if "VALVE" in desc:
                    valve_prefixes.add(prefix)
                if any(word in desc for word in ["INDICAT", "CONTROLL", "TRANSMIT", "SWITCH", "INSTRUMENT", "ANALYZ"]):
                    instrument_prefixes.add(prefix)

        # Note/Hold hints from legends or title blocks.
        if "NOTE" in upper:
            note_keywords.add("NOTE")
        if "HOLD" in upper:
            hold_keywords.add("HOLD")

    return {
        "instrument_prefixes": sorted(instrument_prefixes),
        "valve_prefixes": sorted(valve_prefixes),
        "note_keywords": sorted(note_keywords),
        "hold_keywords": sorted(hold_keywords),
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
            "sources": [],
        }

    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("[LegendKnowledge] Failed to load %s: %s", target, exc)
        return {
            "instrument_prefixes": sorted(DEFAULT_INSTRUMENT_PREFIXES),
            "valve_prefixes": sorted(DEFAULT_VALVE_PREFIXES),
            "note_keywords": ["NOTE"],
            "hold_keywords": ["HOLD"],
            "sources": [],
        }
