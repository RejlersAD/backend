"""
Instrument Index Service — ALL-INSTRUMENT extraction from P&ID drawings
-----------------------------------------------------------------------
Scans P&ID pages via OpenAI Vision (gpt-4o) and returns every instrument
tag found — flow, pressure, temperature, level, analysis, control valves,
shutdown valves, safety valves, etc.

Output per instrument:
  tag_number, instrument_type, category, pid_no, service_description,
  line_number, equipment_number, loop_number, fail_safe, signal_type,
  set_point, drawing_number, revision, notes

Excel export: multi-sheet workbook  (Instrument Index  +  Summary)

SOFT-CODED: all instrument categories live in INSTRUMENT_CATEGORIES dict
below — add/remove types without touching logic.
"""

import io
import os
import re
import json
import base64
import logging
import traceback
from datetime import datetime

from pdf2image import convert_from_bytes
from PIL import Image
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────────
# SOFT-CODED CONFIGURATION
# All instrument categories / type descriptions live here.
# ────────────────────────────────────────────────────────────────────────────
INSTRUMENT_CATEGORIES = {
    # ── FLOW ──────────────────────────────────────────────────────────────
    "FT":   {"name": "Flow Transmitter",                  "category": "Flow"},
    "FI":   {"name": "Flow Indicator",                    "category": "Flow"},
    "FIC":  {"name": "Flow Indicating Controller",        "category": "Flow"},
    "FIT":  {"name": "Flow Indicating Transmitter",       "category": "Flow"},
    "FCV":  {"name": "Flow Control Valve",                "category": "Flow"},
    "FE":   {"name": "Flow Element / Orifice",            "category": "Flow"},
    "FQI":  {"name": "Flow Quantity Indicator",           "category": "Flow"},
    "FG":   {"name": "Flow Glass / Sight Glass",          "category": "Flow"},
    "FO":   {"name": "Flow Orifice",                      "category": "Flow"},
    "FY":   {"name": "Flow Relay / Computer",             "category": "Flow"},
    # ── PRESSURE ──────────────────────────────────────────────────────────
    "PT":   {"name": "Pressure Transmitter",              "category": "Pressure"},
    "PI":   {"name": "Pressure Indicator",                "category": "Pressure"},
    "PIC":  {"name": "Pressure Indicating Controller",    "category": "Pressure"},
    "PIT":  {"name": "Pressure Indicating Transmitter",   "category": "Pressure"},
    "PS":   {"name": "Pressure Switch",                   "category": "Pressure"},
    "PSH":  {"name": "Pressure Switch High",              "category": "Pressure"},
    "PSL":  {"name": "Pressure Switch Low",               "category": "Pressure"},
    "PSHH": {"name": "Pressure Switch High-High",         "category": "Pressure"},
    "PSLL": {"name": "Pressure Switch Low-Low",           "category": "Pressure"},
    "PSAL": {"name": "Pressure Switch Alarm Low",         "category": "Pressure"},
    "PSAH": {"name": "Pressure Switch Alarm High",        "category": "Pressure"},
    "PSDL": {"name": "Pressure Switch Differential Low",  "category": "Pressure"},
    "PSDH": {"name": "Pressure Switch Differential High", "category": "Pressure"},
    "PCV":  {"name": "Pressure Control Valve",            "category": "Pressure"},
    "PG":   {"name": "Pressure Gauge",                    "category": "Pressure"},
    "PSV":  {"name": "Pressure Safety Valve",             "category": "Safety"},
    "PRV":  {"name": "Pressure Relief Valve",             "category": "Safety"},
    # ── TEMPERATURE ───────────────────────────────────────────────────────
    "TT":   {"name": "Temperature Transmitter",           "category": "Temperature"},
    "TI":   {"name": "Temperature Indicator",             "category": "Temperature"},
    "TIC":  {"name": "Temperature Indicating Controller", "category": "Temperature"},
    "TIT":  {"name": "Temperature Indicating Transmitter","category": "Temperature"},
    "TS":   {"name": "Temperature Switch",                "category": "Temperature"},
    "TSH":  {"name": "Temperature Switch High",           "category": "Temperature"},
    "TSL":  {"name": "Temperature Switch Low",            "category": "Temperature"},
    "TSHH": {"name": "Temperature Switch High-High",      "category": "Temperature"},
    "TSLL": {"name": "Temperature Switch Low-Low",        "category": "Temperature"},
    "TCV":  {"name": "Temperature Control Valve",         "category": "Temperature"},
    "TW":   {"name": "Thermowell",                        "category": "Temperature"},
    "TE":   {"name": "Temperature Element (Thermocouple)","category": "Temperature"},
    # ── LEVEL ─────────────────────────────────────────────────────────────
    "LT":   {"name": "Level Transmitter",                 "category": "Level"},
    "LI":   {"name": "Level Indicator",                   "category": "Level"},
    "LIC":  {"name": "Level Indicating Controller",       "category": "Level"},
    "LIT":  {"name": "Level Indicating Transmitter",      "category": "Level"},
    "LS":   {"name": "Level Switch",                      "category": "Level"},
    "LSH":  {"name": "Level Switch High",                 "category": "Level"},
    "LSL":  {"name": "Level Switch Low",                  "category": "Level"},
    "LSHH": {"name": "Level Switch High-High",            "category": "Level"},
    "LSLL": {"name": "Level Switch Low-Low",              "category": "Level"},
    "LSAL": {"name": "Level Switch Alarm Low",            "category": "Level"},
    "LSAH": {"name": "Level Switch Alarm High",           "category": "Level"},
    "LSDL": {"name": "Level Switch Differential Low",     "category": "Level"},
    "LSDH": {"name": "Level Switch Differential High",    "category": "Level"},
    "LG":   {"name": "Level Gauge",                       "category": "Level"},
    "LCV":  {"name": "Level Control Valve",               "category": "Level"},
    "LY":   {"name": "Level Relay / Computer",            "category": "Level"},
    # ── DIFFERENTIAL PRESSURE ─────────────────────────────────────────────
    "DPI":  {"name": "Differential Pressure Indicator",   "category": "Differential Pressure"},
    "DPIT": {"name": "DP Indicating Transmitter",         "category": "Differential Pressure"},
    "DPT":  {"name": "Differential Pressure Transmitter", "category": "Differential Pressure"},
    "DPAH": {"name": "DP Alarm High",                     "category": "Differential Pressure"},
    "DPAL": {"name": "DP Alarm Low",                      "category": "Differential Pressure"},
    "DPZY": {"name": "DP Position Transmitter",           "category": "Differential Pressure"},
    # ── ANALYSIS ──────────────────────────────────────────────────────────
    "AT":   {"name": "Analyzer Transmitter",              "category": "Analysis"},
    "AI":   {"name": "Analyzer Indicator",                "category": "Analysis"},
    "AIC":  {"name": "Analyzer Indicating Controller",    "category": "Analysis"},
    "AIT":  {"name": "Analyzer Indicating Transmitter",   "category": "Analysis"},
    # ── SHUTDOWN / ESD / CONTROL VALVES ───────────────────────────────────
    "SDV":  {"name": "Shutdown Valve",                    "category": "Shutdown & ESD"},
    "BDV":  {"name": "Blowdown Valve",                    "category": "Shutdown & ESD"},
    "XV":   {"name": "On/Off Valve (ESD)",                "category": "Shutdown & ESD"},
    "EV":   {"name": "Emergency Valve",                   "category": "Shutdown & ESD"},
    "HCV":  {"name": "Hand Control Valve",                "category": "Control Valves"},
    # ── MOTOR / SOLENOID OPERATED ─────────────────────────────────────────
    "MOV":  {"name": "Motor Operated Valve",              "category": "Motor & Solenoid"},
    "SOV":  {"name": "Solenoid Operated Valve",           "category": "Motor & Solenoid"},
    "AOV":  {"name": "Air Operated Valve",                "category": "Motor & Solenoid"},
    # ── POSITION / VALVE POSITION ─────────────────────────────────────────
    "ZI":   {"name": "Position Indicator",                "category": "Position"},
    "ZT":   {"name": "Position Transmitter",              "category": "Position"},
    "ZS":   {"name": "Position Switch",                   "category": "Position"},
    "ZSH":  {"name": "Position Switch High (Open)",       "category": "Position"},
    "ZSL":  {"name": "Position Switch Low (Closed)",      "category": "Position"},
    "ZCV":  {"name": "Position Control Valve",            "category": "Position"},
    "SVZY": {"name": "Solenoid Valve + Position TX",      "category": "Position"},
    "BVZY": {"name": "Ball Valve + Position TX",          "category": "Position"},
    # ── RESTRICTION / SPECIAL ─────────────────────────────────────────────
    "RO":   {"name": "Restriction Orifice",               "category": "Restriction"},
    "XPD":  {"name": "Special / Explosion Proof Device",  "category": "Special"},
    "XY":   {"name": "Relay / Computer (Special)",        "category": "Special"},
    "WI":   {"name": "Weight Indicator",                  "category": "Weight"},
    "WIT":  {"name": "Weight Indicating Transmitter",     "category": "Weight"},
    "SI":   {"name": "Speed Indicator",                   "category": "Speed"},
    "SIT":  {"name": "Speed Indicating Transmitter",      "category": "Speed"},
    "VI":   {"name": "Vibration Indicator",               "category": "Vibration"},
    "VIT":  {"name": "Vibration Indicating Transmitter",  "category": "Vibration"},
    "HI":   {"name": "Hand Indicator",                    "category": "Hand/Manual"},
    "HS":   {"name": "Hand Switch",                       "category": "Hand/Manual"},
}

# Column definitions for Excel output
EXCEL_COLUMNS = [
    {"key": "index_no",              "label": "Index No.",          "width": 10},
    {"key": "tag_number",            "label": "Tag Number",         "width": 18},
    {"key": "instrument_type",       "label": "Instrument Type",    "width": 35},
    {"key": "category",              "label": "Category",           "width": 22},
    {"key": "pid_no",                "label": "P&ID No.",           "width": 22},
    {"key": "service_description",   "label": "Service Description","width": 40},
    {"key": "line_number",           "label": "Line Number",        "width": 20},
    {"key": "equipment_number",      "label": "Equipment No.",      "width": 18},
    {"key": "loop_number",           "label": "Loop No.",           "width": 14},
    {"key": "fail_safe",             "label": "Fail Safe",          "width": 12},
    {"key": "signal_type",           "label": "Signal Type",        "width": 16},
    {"key": "set_point",             "label": "Set Point",          "width": 14},
    {"key": "drawing_number",        "label": "Drawing No.",        "width": 22},
    {"key": "revision",              "label": "Rev.",               "width": 8},
    {"key": "notes",                 "label": "Notes",              "width": 40},
]

# Category colour coding for Excel rows
CATEGORY_COLOURS = {
    "Flow":               "DDEEFF",
    "Pressure":           "FFE4CC",
    "Temperature":        "FFE4E4",
    "Level":              "E4F4E4",
    "Differential Pressure": "FFF9CC",
    "Analysis":           "E8E4FF",
    "Safety":             "FFCCCC",
    "Shutdown & ESD":     "FFD9D9",
    "Control Valves":     "CCFFEE",
    "Motor & Solenoid":   "E0E0FF",
    "Position":           "FFFACC",
    "Restriction":        "DDEEDD",
    "Special":            "F0F0F0",
    "Weight":             "E8F4FF",
    "Speed":              "F4E8FF",
    "Vibration":          "FFE8F4",
    "Hand/Manual":        "F0FFE8",
}


class InstrumentIndexService:
    """
    Extract ALL instrument tags from a P&ID drawing using OpenAI Vision (gpt-4o).
    Supports single-page and multi-page PDFs.
    """

    def __init__(self):
        self.openai_client = self._init_openai()

    # ────────────────────────────────────────────────────────────────────
    # Initialisation helpers
    # ────────────────────────────────────────────────────────────────────

    def _init_openai(self):
        try:
            import openai
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                logger.error("[InstrumentIndex] OPENAI_API_KEY not set")
                return None
            client = openai.OpenAI(api_key=api_key)
            logger.info("[InstrumentIndex] ✅ OpenAI client initialised")
            return client
        except Exception as e:
            logger.error(f"[InstrumentIndex] OpenAI init error: {e}")
            return None

    # ────────────────────────────────────────────────────────────────────
    # Public entry point
    # ────────────────────────────────────────────────────────────────────

    def extract_instruments(self, pid_bytes, drawing_info):
        """
        Process the uploaded file (PDF or image) and return a list of
        instrument records.

        Args:
            pid_bytes  : raw file bytes
            drawing_info: dict with drawing_number, drawing_title, revision, project_name

        Returns:
            list[dict]: instrument records
        """
        if not self.openai_client:
            logger.error("[InstrumentIndex] Cannot extract — no OpenAI client")
            return []

        try:
            pages = self._to_jpeg_pages(pid_bytes)
            logger.info(f"[InstrumentIndex] Processing {len(pages)} page(s)")

            all_instruments = []
            seen_tags = set()

            for page_no, jpeg_bytes in enumerate(pages, start=1):
                logger.info(f"[InstrumentIndex] Analysing page {page_no}/{len(pages)}")
                page_instruments = self._analyse_page(jpeg_bytes, drawing_info, page_no)

                # Deduplicate by tag_number across pages
                for inst in page_instruments:
                    tag = (inst.get("tag_number") or "").strip().upper()
                    if tag and tag not in seen_tags:
                        seen_tags.add(tag)
                        all_instruments.append(inst)
                    elif not tag:
                        all_instruments.append(inst)

            # Sequential index numbers
            for i, inst in enumerate(all_instruments, start=1):
                inst["index_no"] = i

            # Ensure drawing number is populated
            if drawing_info.get("drawing_number"):
                for inst in all_instruments:
                    if not inst.get("pid_no"):
                        inst["pid_no"] = drawing_info["drawing_number"]
                    if not inst.get("drawing_number"):
                        inst["drawing_number"] = drawing_info["drawing_number"]

            logger.info(f"[InstrumentIndex] ✅ Total unique instruments: {len(all_instruments)}")
            return all_instruments

        except Exception as e:
            logger.error(f"[InstrumentIndex] extract_instruments error: {e}", exc_info=True)
            return []

    # ────────────────────────────────────────────────────────────────────
    # PDF → JPEG conversion
    # ────────────────────────────────────────────────────────────────────

    def _to_jpeg_pages(self, pid_bytes):
        """Convert PDF (or image) to list of JPEG bytes, one entry per page."""
        is_pdf = pid_bytes[:4] == b"%PDF"

        if is_pdf:
            logger.info("[InstrumentIndex] Converting PDF to images…")
            try:
                pil_images = convert_from_bytes(pid_bytes, dpi=200)
            except Exception as e:
                logger.error(f"[InstrumentIndex] pdf2image failed: {e}")
                # Fallback: send raw bytes as a single "page"
                return [pid_bytes]
        else:
            # Already an image
            pil_images = [Image.open(io.BytesIO(pid_bytes))]

        jpeg_pages = []
        for img in pil_images:
            jpeg_pages.append(self._pil_to_jpeg(img))
        return jpeg_pages

    def _pil_to_jpeg(self, img, max_size=2000):
        """Resize + convert PIL image to JPEG bytes."""
        # Resize if too large
        if img.width > max_size or img.height > max_size:
            ratio = min(max_size / img.width, max_size / img.height)
            img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.Resampling.LANCZOS)
        # Flatten transparency
        if img.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            bg.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        return buf.getvalue()

    # ────────────────────────────────────────────────────────────────────
    # Per-page AI analysis
    # ────────────────────────────────────────────────────────────────────

    def _analyse_page(self, jpeg_bytes, drawing_info, page_no):
        """Send one JPEG page to OpenAI Vision and return instrument list."""
        b64 = base64.b64encode(jpeg_bytes).decode("utf-8")
        if len(b64) > 20 * 1024 * 1024:
            logger.error("[InstrumentIndex] Base64 image exceeds 20 MB limit — skipping page")
            return []

        prompt = self._build_prompt(drawing_info, page_no)

        instruments = self._call_vision(b64, prompt, "primary")
        if not instruments:
            logger.warning(f"[InstrumentIndex] Primary extraction empty on page {page_no}, trying fallback…")
            instruments = self._call_vision(b64, self._build_fallback_prompt(drawing_info, page_no), "fallback")

        # Enrich with category from tag prefix
        for inst in instruments:
            inst = self._enrich_category(inst)
        return instruments

    def _call_vision(self, b64_image, prompt, mode_label):
        """Call gpt-4o with vision content; return parsed list or []."""
        try:
            logger.info(f"[InstrumentIndex] Calling OpenAI Vision ({mode_label})…")
            resp = self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an expert P&ID analyst and process instrumentation engineer "
                            "with 20+ years of experience reading engineering drawings. "
                            "You can identify ALL types of instrument symbols — not just pressure instruments — "
                            "including flow, temperature, level, analysis, control valves, shutdown valves, "
                            "safety valves, position indicators, and restriction orifices. "
                            "Extract EVERY instrument tag you see. Return ONLY a valid JSON array."
                        ),
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{b64_image}",
                                    "detail": "high",
                                },
                            },
                        ],
                    },
                ],
                max_tokens=12000,
                temperature=0.1,
            )
            raw = resp.choices[0].message.content
            logger.info(f"[InstrumentIndex] Vision response {len(raw)} chars")
            return self._parse_response(raw)
        except Exception as e:
            logger.error(f"[InstrumentIndex] Vision API error ({mode_label}): {e}", exc_info=True)
            return []

    # ────────────────────────────────────────────────────────────────────
    # Prompt templates
    # ────────────────────────────────────────────────────────────────────

    def _build_prompt(self, drawing_info, page_no):
        type_list = ", ".join(
            f"{k} ({v['name']})" for k, v in INSTRUMENT_CATEGORIES.items()
        )
        return f"""
🎯 MISSION: Extract the COMPLETE Instrument Index from this P&ID drawing.
Page {page_no} — Drawing: {drawing_info.get('drawing_number', 'N/A')} — {drawing_info.get('drawing_title', 'N/A')}
Project: {drawing_info.get('project_name', 'N/A')}   Revision: {drawing_info.get('revision', '0')}

─────────────────────────────────────────────
WHAT TO EXTRACT
─────────────────────────────────────────────
Extract EVERY instrument tag visible on this drawing.  A P&ID typically has
15–60+ instruments.  Do NOT skip any.

Target instrument tag prefixes (non-exhaustive):
{type_list}

Tag format examples from ADNOC / oil & gas:
  FIT-3901-08A   TI-3901-01   PIT-3901-03   LIT-3601-01   SDV-3901-01
  SVZY-3901-03   MOV-3901-01  PSV-3901-01   RO-3901-01    XPD-3901-01

─────────────────────────────────────────────
WHERE TO LOOK
─────────────────────────────────────────────
1. Instrument circles / bubbles on process lines
2. Circles connected to equipment nozzles
3. Circles inside control loops (dashed boxes)
4. Instrument index tables in title block or margins
5. Legend / symbol tables
6. Any small circle with text inside or attached — regardless of size

─────────────────────────────────────────────
FIELDS TO EXTRACT PER INSTRUMENT
─────────────────────────────────────────────
For EACH instrument found, return:

- tag_number          : Full tag e.g. "PIT-3901-01" — REQUIRED
- instrument_type     : Full description e.g. "Pressure Indicating Transmitter"
- category            : e.g. Flow / Pressure / Temperature / Level / Safety / Shutdown & ESD / etc.
- pid_no              : P&ID drawing number (default: "{drawing_info.get('drawing_number','N/A')}")
- service_description : What the instrument measures (e.g. "Pig Receiver Inlet Pressure")
- line_number         : Process line tag where instrument is installed
- equipment_number    : Associated equipment tag (vessel, pump, compressor, etc.)
- loop_number         : Control/safety loop number if shown
- fail_safe           : Fail-safe position — "FC" (fail closed), "FO" (fail open), "FL" (fail last), "N/A"
- signal_type         : "4-20mA", "Discrete (0/1)", "HART", "Fieldbus", "Pneumatic", "N/A"
- set_point           : Alarm / trip set point if shown on drawing or in instrument list
- drawing_number      : "{drawing_info.get('drawing_number','N/A')}"
- revision            : "{drawing_info.get('revision','0')}"
- notes               : Any relevant remark, special service (H2S, NACE, SIL), or uncertainty

─────────────────────────────────────────────
OUTPUT
─────────────────────────────────────────────
Return ONLY a JSON array — no markdown fences, no explanation text.
Example single record:
[
  {{
    "tag_number":         "PIT-3901-01",
    "instrument_type":    "Pressure Indicating Transmitter",
    "category":           "Pressure",
    "pid_no":             "{drawing_info.get('drawing_number','N/A')}",
    "service_description":"Pig Receiver Inlet Pressure",
    "line_number":        "10\"-G-3901-A2A",
    "equipment_number":   "LP-3901",
    "loop_number":        "3901",
    "fail_safe":          "N/A",
    "signal_type":        "4-20mA",
    "set_point":          "75 barg (PSHH)",
    "drawing_number":     "{drawing_info.get('drawing_number','N/A')}",
    "revision":           "{drawing_info.get('revision','0')}",
    "notes":              "SIL-rated loop"
  }}
]

⚠️ CRITICAL: Extract ALL instruments.  A response of [] or < 5 items for a
process P&ID almost certainly means you missed instruments.  Scan carefully.
Start response with [ and end with ].
"""

    def _build_fallback_prompt(self, drawing_info, page_no):
        """Simpler, more aggressive fallback prompt."""
        return f"""
EMERGENCY FALLBACK — Extract ALL instrument tags from this P&ID.
Page {page_no}  |  Drawing: {drawing_info.get('drawing_number', 'N/A')}

Instructions:
1. Find EVERY circle or bubble containing a text tag on this drawing.
2. Read the tag — it will look like: FIT-1234, TI-56, SDV-3901-01, MOV-3901-02, LIT-101A, etc.
3. For each tag extract as much data as you can see.

Return JSON array only, format:
[
  {{
    "tag_number": "TAG-NO",
    "instrument_type": "Description",
    "category": "Category",
    "pid_no": "{drawing_info.get('drawing_number','N/A')}",
    "service_description": "what it measures",
    "line_number": "line tag or N/A",
    "equipment_number": "equipment tag or N/A",
    "loop_number": "N/A",
    "fail_safe": "N/A",
    "signal_type": "N/A",
    "set_point": "N/A",
    "drawing_number": "{drawing_info.get('drawing_number','N/A')}",
    "revision": "{drawing_info.get('revision','0')}",
    "notes": ""
  }}
]

Return ONLY the JSON array.
"""

    # ────────────────────────────────────────────────────────────────────
    # Response parsing helpers
    # ────────────────────────────────────────────────────────────────────

    def _parse_response(self, raw):
        """Extract JSON array from raw AI response text."""
        try:
            text = raw.strip()
            # Strip markdown fences
            for fence in ("```json", "```"):
                if fence in text:
                    start = text.find(fence) + len(fence)
                    end = text.find("```", start)
                    if end > start:
                        text = text[start:end].strip()
                        break

            # Find JSON array boundaries
            s = text.find("[")
            e = text.rfind("]") + 1
            if s >= 0 and e > s:
                data = json.loads(text[s:e])
                if isinstance(data, list):
                    return data

            # Try entire payload
            data = json.loads(text)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "instruments" in data:
                return data["instruments"]

        except json.JSONDecodeError as exc:
            logger.error(f"[InstrumentIndex] JSON decode error: {exc}")
        except Exception as exc:
            logger.error(f"[InstrumentIndex] Parse error: {exc}", exc_info=True)
        return []

    def _enrich_category(self, inst):
        """Fill in instrument_type + category from INSTRUMENT_CATEGORIES if missing."""
        tag = (inst.get("tag_number") or "").strip().upper()
        if not tag:
            return inst

        # Extract function code (letters before first digit or dash-digit)
        match = re.match(r"^([A-Z]+)", tag)
        if not match:
            return inst
        code = match.group(1)

        cfg = INSTRUMENT_CATEGORIES.get(code)
        if cfg:
            if not inst.get("instrument_type"):
                inst["instrument_type"] = cfg["name"]
            if not inst.get("category"):
                inst["category"] = cfg["category"]
        return inst

    # ────────────────────────────────────────────────────────────────────
    # Excel export
    # ────────────────────────────────────────────────────────────────────

    def generate_excel(self, instruments, drawing_info):
        """
        Build an openpyxl workbook with two sheets:
          1. Instrument Index — row per instrument, category-coloured
          2. Summary          — count per category

        Returns bytes of the .xlsx file.
        """
        wb = openpyxl.Workbook()

        # ── Sheet 1: Instrument Index ────────────────────────────────────
        ws = wb.active
        ws.title = "Instrument Index"
        ws.sheet_view.showGridLines = True

        # Header style
        hdr_font   = Font(name="Calibri", bold=True, color="FFFFFF", size=10)
        hdr_fill   = PatternFill("solid", fgColor="1F4E79")
        hdr_align  = Alignment(horizontal="center", vertical="center", wrap_text=True)
        thin       = Side(style="thin", color="CCCCCC")
        std_border = Border(left=thin, right=thin, top=thin, bottom=thin)

        # Title row
        ws.row_dimensions[1].height = 22
        title_cell = ws.cell(row=1, column=1, value="INSTRUMENT INDEX")
        title_cell.font = Font(name="Calibri", bold=True, size=14, color="1F4E79")
        title_cell.alignment = Alignment(horizontal="left", vertical="center")

        # Metadata row
        ws.row_dimensions[2].height = 16
        ws.cell(row=2, column=1, value=f"Drawing: {drawing_info.get('drawing_number','N/A')}")
        ws.cell(row=2, column=5, value=f"Title: {drawing_info.get('drawing_title','N/A')}")
        ws.cell(row=2, column=9, value=f"Rev: {drawing_info.get('revision','0')}")
        ws.cell(row=2, column=11, value=f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

        # Header row (row 4)
        ws.row_dimensions[4].height = 30
        for col_idx, col_def in enumerate(EXCEL_COLUMNS, start=1):
            cell = ws.cell(row=4, column=col_idx, value=col_def["label"])
            cell.font = hdr_font
            cell.fill = hdr_fill
            cell.alignment = hdr_align
            cell.border = std_border
            ws.column_dimensions[cell.column_letter].width = col_def["width"]

        # Data rows
        DATA_START = 5
        for row_offset, inst in enumerate(instruments):
            row_no = DATA_START + row_offset
            ws.row_dimensions[row_no].height = 15

            category = inst.get("category") or "Special"
            fill_hex  = CATEGORY_COLOURS.get(category, "F5F5F5")
            row_fill  = PatternFill("solid", fgColor=fill_hex)
            std_font  = Font(name="Calibri", size=9)
            std_align = Alignment(vertical="center", wrap_text=False)

            for col_idx, col_def in enumerate(EXCEL_COLUMNS, start=1):
                val = inst.get(col_def["key"], "")
                cell = ws.cell(row=row_no, column=col_idx, value=val if val != "N/A" else "")
                cell.font = std_font
                cell.fill = row_fill
                cell.alignment = std_align
                cell.border = std_border

        # Freeze header
        ws.freeze_panes = "A5"

        # Auto-filter on header row
        ws.auto_filter.ref = (
            f"A4:{ws.cell(row=4, column=len(EXCEL_COLUMNS)).column_letter}4"
        )

        # ── Sheet 2: Summary ─────────────────────────────────────────────
        ws2 = wb.create_sheet("Summary")
        ws2.column_dimensions["A"].width = 30
        ws2.column_dimensions["B"].width = 14

        ws2.row_dimensions[1].height = 22
        ws2.cell(row=1, column=1, value="INSTRUMENT INDEX — SUMMARY").font = Font(
            bold=True, size=13, color="1F4E79"
        )
        ws2.cell(row=2, column=1, value=f"Drawing: {drawing_info.get('drawing_number','N/A')}")
        ws2.cell(row=2, column=2, value=f"Total: {len(instruments)}")

        ws2.row_dimensions[4].height = 22
        for col, hdr in [(1, "Category"), (2, "Count")]:
            c = ws2.cell(row=4, column=col, value=hdr)
            c.font = hdr_font
            c.fill = hdr_fill
            c.alignment = hdr_align
            c.border = std_border

        # Build category counts
        counts: dict[str, int] = {}
        for inst in instruments:
            cat = inst.get("category") or "Unknown"
            counts[cat] = counts.get(cat, 0) + 1

        row_n = 5
        for cat, cnt in sorted(counts.items()):
            fill_hex = CATEGORY_COLOURS.get(cat, "F5F5F5")
            for col, val in [(1, cat), (2, cnt)]:
                c = ws2.cell(row=row_n, column=col, value=val)
                c.font = Font(name="Calibri", size=10)
                c.fill = PatternFill("solid", fgColor=fill_hex)
                c.alignment = Alignment(vertical="center")
                c.border = std_border
            row_n += 1

        # Total row
        total_cell = ws2.cell(row=row_n, column=1, value="TOTAL")
        total_cell.font = Font(bold=True, size=10, color="1F4E79")
        ws2.cell(row=row_n, column=2, value=len(instruments)).font = Font(bold=True)

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf.getvalue()
