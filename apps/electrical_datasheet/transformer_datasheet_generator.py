"""
Power/Distribution Transformer Datasheet Generator
Extracts equipment data from Transformer Sizing Calculation documents
and generates comprehensive datasheets matching the standard ADNOC form.

Columns: SI No. | DESCRIPTION | UNIT | SPECIFIED DESIGN DATA | VENDOR DATA | Rev
"""
import logging
import json
from typing import Dict, List, Optional
from django.conf import settings
from openai import OpenAI
import PyPDF2

logger = logging.getLogger(__name__)


class TransformerDatasheetGenerator:
    """Generate Power/Distribution Transformer datasheets from sizing calculation documents."""

    def __init__(self):
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)

    # ──────────────────────────────────────────────────────────────────────────
    # PDF Extraction
    # ──────────────────────────────────────────────────────────────────────────
    def extract_text_from_pdf(self, pdf_file) -> str:
        """Extract all text from an uploaded PDF file."""
        try:
            pdf_file.seek(0)
            reader = PyPDF2.PdfReader(pdf_file)
            text = ""
            logger.info(f"[TransformerDatasheet] PDF has {len(reader.pages)} pages")
            for i, page in enumerate(reader.pages, 1):
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
                    logger.info(f"[TransformerDatasheet] Page {i}: {len(page_text)} chars")
                else:
                    logger.warning(f"[TransformerDatasheet] Page {i}: no text (image-based?)")
            logger.info(f"[TransformerDatasheet] Total: {len(text)} chars")
            return text
        except Exception as e:
            logger.error(f"[TransformerDatasheet] PDF extraction error: {e}", exc_info=True)
            return ""

    # ──────────────────────────────────────────────────────────────────────────
    # Main entry point
    # ──────────────────────────────────────────────────────────────────────────
    def generate_datasheet_from_sizing_calc(self, pdf_file, project_info: Dict = None) -> Dict:
        """
        Generate a transformer datasheet from a sizing calculation PDF.

        Returns:
            {
                'success': bool,
                'datasheet_rows': List[Dict],   # sr_no, description, unit, required_data, vendor_data, rev
                'summary': Dict,
                'extraction_metadata': Dict
            }
        """
        try:
            # ── Step 1: Always start with the full template ──────────────────────
            datasheet_rows = self._get_default_datasheet_template()

            # ── Step 2: Extract text from PDF ────────────────────────────────────
            logger.info("[TransformerDatasheet] Extracting text from sizing calculation PDF…")
            doc_text = self.extract_text_from_pdf(pdf_file)

            # ── Step 3: AI vendor data extraction + merge ─────────────────────────
            if doc_text and len(doc_text) >= 20:
                logger.info(f"[TransformerDatasheet] {len(doc_text)} chars extracted — running AI vendor extraction…")
                vendor_map = self._extract_vendor_data_with_ai(doc_text, project_info)
                if vendor_map:
                    merged = self._merge_vendor_data(datasheet_rows, vendor_map)
                    logger.info(f"[TransformerDatasheet] Merged {merged} vendor values into template")
            else:
                logger.warning("[TransformerDatasheet] No/insufficient text from PDF — showing template with empty vendor data")
                doc_text = ""

            summary = {
                'total_rows': len(datasheet_rows),
                'equipment_count': sum(1 for r in datasheet_rows if r.get('description', '').strip()),
                'completed_fields': sum(1 for r in datasheet_rows if r.get('vendor_data', '').strip()),
                'missing_fields': sum(1 for r in datasheet_rows if not r.get('vendor_data', '').strip()),
            }

            logger.info(f"[TransformerDatasheet] ✅ {summary['total_rows']} rows | {summary['completed_fields']} vendor values filled")
            return {
                'success': True,
                'datasheet_rows': datasheet_rows,
                'summary': summary,
                'extraction_metadata': {
                    'document_length': len(doc_text),
                    'project_info': project_info or {},
                }
            }

        except Exception as e:
            logger.error(f"[TransformerDatasheet] Error: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    # ──────────────────────────────────────────────────────────────────────────
    # AI vendor data extraction + merge helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _extract_vendor_data_with_ai(self, doc_text: str, project_info: Dict = None) -> Dict:
        """
        Use GPT-4o to extract parameter values from the uploaded document.
        Returns a dict mapping NORMALIZED_DESCRIPTION_UPPER → extracted_value.
        """
        prompt = f"""You are a senior electrical engineer specialising in power transformers. Read the following technical document and extract every parameter value you can find.

DOCUMENT:
{doc_text[:8000]}

TASK:
Return a JSON object where:
- Keys   = parameter/field names in UPPERCASE (e.g. "TAG NO.", "RATED POWER", "VECTOR GROUP")
- Values = the extracted value as a string

Focus on: equipment tags, kVA/MVA ratings, voltages, currents, impedances, vector groups, frequencies, cooling types, manufacturers, weights, dimensions, temperatures, efficiency values.

Rules:
- Include ONLY fields actually present in the document.
- Do NOT invent or assume values.
- Return ONLY a valid JSON object — no explanation, no markdown.

Example:
{{"TAG NO.": "13-BF-0113M", "RATED POWER": "1250", "VECTOR GROUP": "Dy11", "RATED PRIMARY VOLTAGE": "11", "MANUFACTURER": "ABB"}}"""

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are an expert electrical engineer. Extract parameter values from transformer documents. Return only valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=2000,
            )
            ai_response = response.choices[0].message.content.strip()
            if "```json" in ai_response:
                ai_response = ai_response.split("```json")[1].split("```")[0]
            elif "```" in ai_response:
                ai_response = ai_response.split("```")[1].split("```")[0]
            vendor_map = json.loads(ai_response.strip())
            if isinstance(vendor_map, dict):
                normalized = {k.strip().upper(): str(v).strip() for k, v in vendor_map.items() if v and str(v).strip()}
                logger.info(f"[TransformerDatasheet] AI extracted {len(normalized)} vendor key-value pairs")
                return normalized
            return {}
        except json.JSONDecodeError as e:
            logger.error(f"[TransformerDatasheet] Vendor JSON decode error: {e}")
            return {}
        except Exception as e:
            logger.error(f"[TransformerDatasheet] Vendor AI extraction error: {e}")
            return {}

    def _merge_vendor_data(self, template_rows: List[Dict], vendor_map: Dict) -> int:
        """
        Merge vendor_map values into template_rows by description matching.
        Returns count of rows filled.
        """
        merged = 0
        stop_words = {'AND', 'OR', 'OF', 'THE', 'WITH', 'FOR', 'AT', 'IN', 'TO', 'A', 'AN', 'BY', 'ON'}
        for row in template_rows:
            if row.get('vendor_data'):
                continue
            desc = row.get('description', '').strip().upper()
            if not desc:
                continue
            # 1. Exact match
            if desc in vendor_map:
                row['vendor_data'] = vendor_map[desc]
                merged += 1
                continue
            # 2. Containment match
            matched = False
            for ai_key, ai_val in vendor_map.items():
                if not ai_key or not ai_val:
                    continue
                if ai_key in desc or desc in ai_key:
                    row['vendor_data'] = ai_val
                    merged += 1
                    matched = True
                    break
            if matched:
                continue
            # 3. Word-overlap match (≥2 meaningful common words)
            desc_words = set(desc.split()) - stop_words
            for ai_key, ai_val in vendor_map.items():
                if not ai_key or not ai_val:
                    continue
                ai_words = set(ai_key.split()) - stop_words
                if len(desc_words & ai_words) >= 2:
                    row['vendor_data'] = ai_val
                    merged += 1
                    break
        return merged

    def _LEGACY_extract_datasheet_with_ai(self, doc_text: str, project_info: Dict = None) -> List[Dict]:
        """LEGACY — kept for reference only. Use _extract_vendor_data_with_ai instead."""
        # (original AI prompt method — not called)

        prompt = f"""You are a senior electrical engineer specialising in power and distribution transformers.
Analyse the provided Transformer Sizing Calculation document and extract comprehensive datasheet information.

PROJECT INFORMATION:
{json.dumps(project_info or {}, indent=2)}

DOCUMENT CONTENT:
{doc_text[:7000]}

TASK:
Populate a standard transformer datasheet with EXACTLY 6 fields per row, matching the ADNOC/IEC datasheet form:
- SI_NO      : Sequential item number (e.g. 1, 2, A, A.1, A.2 …). Blank for section-header rows.
- DESCRIPTION: Parameter name or section heading.
- UNIT       : Engineering unit (MVA, kV, A, Hz, ℃, %, mm, kg, dB, etc.). Blank if not applicable.
- REQUIRED_DATA : Specified design data – the required/design value (engineer-filled column).
- VENDOR_DATA   : Value extracted from the uploaded sizing-calculation document; empty string "" if not found.
- REV        : Revision marker – empty string "" unless explicitly noted in the document.

Cover ALL sections listed below in order:

A – GENERAL PARTICULARS
  Tag No., Title, Manufacturer / Country of Origin, Year of Manufacture, Quantity,
  Rating, Project Specification, Standards (IEC 60076 series), Design Life, Criticality Rating,
  Inspection Class, Material Certification

B – ENVIRONMENTAL CONDITIONS
  Type of Installation, Altitude, Max Ambient Temperature, Min Ambient Temperature,
  Max Relative Humidity (at 45 °C / at 54 °C), Degree of Protection (IP), Special Conditions

C – GENERAL CHARACTERISTICS
  Rated Power, Rated Secondary Voltage at No Load,
  Rated Voltage at Rated Power and P.F. 0.8, Rated Frequency, Vector Group,
  Connection Symbol and Vector Group, Maximum Flux Density, Number of Windings,
  With Separate Windings, Type of Cooling, Type of Tap Changer / Tapping / NER

D – INSULATION SYSTEMS
  Isolation Quality, Uniform Insulation,
  Power Frequency Withstand Voltage – Primary, Power Frequency Withstand Voltage – Secondary,
  Unearthed Transformer, Zero Sequence Impedance, Positive Sequence Voltage, Zero End Ratio

E – MODE OF OPERATION

F – PRIMARY WINDING
  Voltage, Material (Copper), Max Current Density in Winding, Rated Primary Current

G – SECONDARY WINDING
  High Voltage, Material (Copper), Additional Neutral in Separate Box,
  Earthing System, Max Current Density in Winding, Rated Primary Current

H – ELECTRICAL AND MECHANICAL CHARACTERISTICS
  No-Load Current (Primary), Magnetising Inrush Current & Duration,
  Short Circuit Impedance at Principal Tap, Short Circuit Impedance at Maximum Tap,
  Tolerance on Short Circuit Impedance, Zero Sequence Impedance, Positive Sequence Ratio,
  Primary System Apparent Short Circuit Rating, Max Short Circuit Duration,
  Top Oil Temperature Rise, Average Winding Temperature Rise, Hot Spot Temperature,
  Iron Losses (No Load), Copper Losses (Full Load), Total Losses,
  Efficiency at 0.9 PF – 50% Load / 75% Load / 100% Load,
  Voltage Regulation at 0.9 PF, Max Efficiency at % Load

I – TAP CHANGERS
  Series Parallel, On-Load, No. of Steps, Tapping Step, Tapping Range,
  Voltage Regulator & Parallel Control System

TANK
  Main Material, Thickness of Tank – Sides / Bottom / Radiators,
  Type of Tank (Sealed / Conservator), Radiator Mounting

J – TANK COVER TYPE
  Bolted, Welded, Bell Type, Thickness, Dimensions (L × W × H)

M – WEIGHTS
  Core & Winding, Oil, Tank & Fittings, Volume of Oil, Make of Oil

N – NOISE LEVEL
  Without Cooling, With Cooling

O – CONNECTIONS
  Primary Voltage Side – Cable Connection, Cable Size, Qty of Bushings,
    Plug-in CT, Pull & Test Facility, Cable Box with Oil, Air Cooled,
    Thermal Image Window, Pressure Relief Dampeners
  Secondary Voltage Side – Cable Connection, Cable Size
  Neutral End – Cable Terminal, Cable Size, Thermal Image Window, Pressure Relief Dampeners
  Cooling System – Thermal, Fans & Associated Contactors, Rated Power, Rated Frequency

P – CONTROL AND PROTECTION DEVICES
  Buchholz Relay (no trip contact form C), Buchholz Relay (2 alarm + 2 trip contacts),
  Oil Temperature Indicator, Thermal Image Winding Temperature (2 alarm + 2 trip),
  Oil Temp Indicator with Contacts (2 alarm + 2 trip),
  Winding Temp Indicator with Contacts (2 alarm + 2 trip),
  Thermometer Pockets, Thermowell, Thermistors,
  Liquid Level Gauge (2 contacts alarm/trip), Pressure Relief Valve (2-stage contacts),
  Magnetic Oil Level Indicator, Pressure Vacuum Gauge (4 contacts),
  Primary Phase CT for Transformer & Line Differential Protection,
  Current Transformer for Restricted EF (BREF)

R – ACCESSORIES
  Surge Arrester, Surge Suppression at Primary Side, Air Dryer,
  Filling Eyes & Jacking Lugs, Pulling Eyes, Tank Access Ladder,
  Safety Valve on Tank & Radiators, Filling Valve, Sampling / Drain Valve,
  Pre-filter Isolating Valve, Earth Connection, Marshalling Box

S – INSPECTION & TESTING
  Inspection, Routine Tests, Type Tests & Acoustic Sound Tests, Special Tests

PAINT / COLOUR SPECIFICATION
  Painting, Colour, Painting Thickness – Tank / Radiator, Oil Saturation Thickness

Return ONLY a JSON array. Each element must have exactly these keys:
  "sr_no", "description", "unit", "required_data", "vendor_data", "rev"

Rules:
- Section header rows: sr_no = "", unit = "", required_data = "", vendor_data = "", rev = ""
- Extract ACTUAL values from the document for vendor_data; use "" when not found
- required_data = standard/typical requirement value for a power transformer per IEC 60076 / ADNOC specs
- rev = "" always (unless document specifies a revision letter)
- Include every parameter listed above even if vendor_data is empty
- Return ONLY the JSON array – no markdown, no explanation"""

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an expert electrical engineer specialising in "
                            "power transformer datasheets per IEC 60076 and ADNOC standards. "
                            "Return only valid JSON arrays."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=6000,
            )

            ai_response = response.choices[0].message.content.strip()

            # Strip markdown fences if present
            if "```json" in ai_response:
                ai_response = ai_response.split("```json")[1].split("```")[0]
            elif "```" in ai_response:
                ai_response = ai_response.split("```")[1].split("```")[0]

            datasheet_rows = json.loads(ai_response.strip())

            if isinstance(datasheet_rows, list) and len(datasheet_rows) > 0:
                for i, row in enumerate(datasheet_rows):
                    row.setdefault("sr_no", "")
                    row.setdefault("description", "")
                    row.setdefault("unit", "")
                    row.setdefault("required_data", "")
                    row.setdefault("vendor_data", "")
                    row.setdefault("rev", "")
                    row.pop("remarks", None)  # remove legacy key if present
                logger.info(f"[TransformerDatasheet] AI returned {len(datasheet_rows)} rows")
                return datasheet_rows
            else:
                logger.error("[TransformerDatasheet] Invalid AI response structure")
                return self._get_default_datasheet_template()

        except json.JSONDecodeError as e:
            logger.error(f"[TransformerDatasheet] JSON decode error: {e}")
            return self._get_default_datasheet_template()
        except Exception as e:
            logger.error(f"[TransformerDatasheet] AI extraction error: {e}")
            return self._get_default_datasheet_template()

    # ──────────────────────────────────────────────────────────────────────────
    # Default template  (ADNOC distribution transformer form – all sections)
    # ──────────────────────────────────────────────────────────────────────────
    def _get_default_datasheet_template(self) -> List[Dict]:
        """Return the full ADNOC distribution transformer datasheet template (6-column)."""
        R = lambda sr, desc, unit="", req="": {
            "sr_no": sr, "description": desc, "unit": unit,
            "required_data": req, "vendor_data": "", "rev": ""
        }
        H = lambda desc: {
            "sr_no": "", "description": desc, "unit": "",
            "required_data": "", "vendor_data": "", "rev": ""
        }
        return [
            # ── A. GENERAL DATA ─────────────────────────────────────────────
            H("A   GENERAL DATA"),
            R("1",  "TAG NO."),
            R("2",  "TITLE",                                          "",    "11/0.433 KV DISTRIBUTION TRANSFORMER"),
            R("3",  "MANUFACTURER / COUNTRY OF ORIGIN",               "",    "*** (AS PER COMPANY APPROVED LIST)"),
            R("4",  "YEAR OF MANUFACTURE"),
            R("5",  "QUANTITY",                                       "No."),
            R("6",  "RATING",                                         "kVA"),
            R("7",  "PROJECT SPECIFICATION",                          "",    "BGS-EE-003"),
            R("8",  "STANDARD",                                       "",    "IEC 60076"),
            R("9",  "DESIGN LIFE",                                    "",    "25 YEARS"),
            R("10", "CRITICALITY RATING"),
            R("11", "INSPECTION CLASS",                               "",    "2"),
            R("12", "MATERIAL CERTIFICATION",                         "",    "3"),

            # ── B. ENVIRONMENTAL CONDITIONS ─────────────────────────────────
            H("B   ENVIRONMENTAL CONDITIONS"),
            R("1",  "TYPE OF INSTALLATION",                           "",    "OUTDOOR IN SHADED AREA"),
            R("2",  "ATMOSPHERE",                                     "",    "SALTY, SULPHUROUS AND DUSTY WITH HIGH CONCENTRATION OF WINDBORNE SAND"),
            R("3",  "ALTITUDE",                                       "M",   "LESS THAN 1000m AMSL"),
            R("4",  "MAX AMBIENT TEMPERATURE",                        "°C",  "56"),
            R("5",  "MINIMUM AMBIENT TEMPERATURE",                    "°C",  "-5"),
            R("6",  "MAXIMUM RELATIVE HUMIDITY",                      "at 43°C", "95%"),
            R("7",  "DEGREE OF PROTECTION (IP)",                      "at 54°C", "IP65"),
            R("8",  "SPECIAL CONDITIONS",                             "",    "TROPICALIZED"),

            # ── C. GENERAL TECHNICAL CHARACTERISTICS ────────────────────────
            H("C   GENERAL TECHNICAL CHARACTERISTICS"),
            R("1",  "RATED PRIMARY VOLTAGE",                          "kV",  "11"),
            R("2",  "RATED SECONDARY VOLTAGE AT NO LOAD",             "kV",  "0.433"),
            R("3",  "SECONDARY VOLTAGE AT RATED POWER AND P.F 0.8",   "kV",  "0.415"),
            R("4",  "RATED FREQUENCY",                                "Hz",  "50"),
            R("5",  "NO. OF PHASES",                                  "",    "3"),
            R("6",  "CONNECTION SYMBOL AND VECTOR GROUP",             "",    "Dy11"),
            R("7",  "MAXIMUM FLUX DENSITY",                           "T"),
            R("8",  "WITH SEPARATE WINDINGS"),

            # ── INSULATION SYSTEMS ───────────────────────────────────────────
            H("INSULATION SYSTEMS"),
            H("CORE TYPE"),
            R("1",  "UNIFORM INSULATION",                             "",    "YES"),
            H("2   POWER FREQUENCY WITHSTAND VOLTAGE"),
            R("3.1","- PRIMARY",                                      "kV",  "28"),
            R("3.2","- SECONDARY",                                    "kV",  "3"),
            H("4   IMPULSE SE WITHSTAND VOLTAGE"),
            R("4.1","- PRIMARY",                                      "kV",  "75"),
            R("4.2","- SECONDARY",                                    "kV",  "12"),
            R("5",  "RATED TRANSFORMER"),
            R("6",  "OIL IMMERSED TRANSFORMER",                       "",    "YES"),
            R("7",  "OIL TYPE",                                       "",    "AS PER BGS-EE-003"),
            R("8",  "TRANSFORMER TANK CONSTRUCTION",                  "",    "LIQUID (IMMERSED) HERMETICALLY SEALED"),
            R("9",  "COMPLETELY FILLED",                              "",    "YES"),
            R("10", "WITH GAS CUSHION"),
            R("11", "TYPE OF COOLING (ONAN/ONAF)",                    "",    "ONAN"),
            H("MODE OF OPERATION"),
            R("",   "INDIVIDUAL / PARALLEL",                          "",    "PARALLEL (REFER NOTE 10)"),

            # ── F. PRIMARY WINDING ───────────────────────────────────────────
            H("F   PRIMARY WINDING"),
            R("1",  "HIGH VOLTAGE (UM)",                              "kV",  "12"),
            R("2",  "COPPER",                                         "",    "YES"),
            R("3",  "MAXIMUM CURRENT DENSITY IN THE WINDING"),
            R("4",  "RATED PRIMARY CURRENT",                          "A",   "***"),

            # ── G. SECONDARY WINDING ─────────────────────────────────────────
            H("G   SECONDARY WINDING"),
            R("1",  "HIGH VOLTAGE (UM)",                              "kV",  "0.8"),
            R("2",  "COPPER",                                         "",    "YES"),
            R("3",  "ADDITIONAL NEUTRAL BROUGHT IN A SEPARATED BOX",  "",    "BUS"),
            R("4",  "EARTHING SYSTEM",                                "",    "SOLID"),
            R("5",  "MAXIMUM CURRENT DENSITY IN THE WINDING"),
            R("6",  "RATED PRIMARY CURRENT"),

            # ── H. ELECTRICAL AND MECHANICAL CHARACTERISTICS ─────────────────
            H("H   ELECTRICAL AND MECHANICAL CHARACTERISTICS"),
            R("1",  "NO-LOAD CURRENT (PRIMARY)"),
            R("2",  "MAGNETIZING INRUSH CURRENT AND DURATION"),
            R("3",  "SHORT CIRCUIT IMPEDANCE AT 75°C",                "%"),
            R("4",  "TRANSFORMER IMPEDANCE AT PRINCIPLE TAP"),
            R("5",  "TRANSFORMER IMPEDANCE AT MAXIMUM TAP"),
            R("6",  "TRANSFORMER IMPEDANCE AT MINIMUM TAP",           "%"),
            R("7",  "TOLERANCE ON SHORT CIRCUIT IMPEDANCE",           "%",   "+/- 10 %"),
            R("8",  "ZERO SEQUENCE IMPEDANCE"),
            R("9",  "POSITIVE SEQUENCE X/R RATIO"),
            R("9a", "ZERO SEQUENCE X/R RATIO"),
            R("10", "PRIMARY SIDE VOLTAGE LEVEL AND VARIATION",       "kV",  "11kV ± 10%"),
            R("11", "FREQUENCY AND VARIATION",                        "Hz",  "50 Hz ± 2%"),
            R("12", "PRIMARY 11KV SYSTEM APPARENT SHORT CIRCUIT RATING", "kA"),
            R("13", "MAX SHORT CIRCUIT DURATION",                     "Sec", "1"),
            R("14", "SECONDARY SIDE APPARENT SHORT CIRCUIT RATINGS",  "kA"),
            R("15", "MAX SHORT CIRCUIT DURATION",                     "Sec", "1"),
            R("16", "TOP OIL TEMPERATURE RISE (AS PER IEC - 60076-2, TABLE (J & II))", "°C", "45"),
            R("17", "AVERAGE WINDING TEMPERATURE RISE (AS PER IEC - 60076-2, TABLE (J & II))", "°C", "60"),
            R("",   "HOT SPOT TEMPERATURE",                           "°C"),
            R("18", "IRON LOSSES (NO LOAD)"),
            R("19", "COPPER LOSSES (FULL LOAD)"),
            R("20", "TOTAL LOSSES"),
            H("20  EFFICIENCY AT 0.8 POWER FACTOR"),
            R("20.1","50% LOAD"),
            R("20.2","75% LOAD"),
            R("20.3","100% LOAD"),
            H("20.4 EFFICIENCY AT POWER FACTOR 1"),
            R("20.5","50% LOAD"),
            R("20.6","75% LOAD"),
            R("20.7","100% LOAD"),
            R("21", "VOLTAGE REGULATION"),
            R("",   "AT UNITY POWER FACTOR"),
            R("",   "AT 0.8 POWER FACTOR"),
            R("",   "SATURATION VOLTAGE"),

            # ── I. TAP CHANGERS ──────────────────────────────────────────────
            H("I   TAP CHANGERS"),
            R("1",  "OFF-CIRCUIT (Y/N)",                              "",    "YES"),
            R("2",  "NO. OF TAPPINGS",                                "No."),
            R("3",  "TAPPING STEP"),
            R("4",  "TAPPING RANGE",                                  "%",   "± 5% (IN STEP OF 2.5%)"),
            R("5",  "VOLTAGE REGULATOR & PARALLEL CONTROL SYSTEM",    "",    "NA"),

            # ── J. TANK ──────────────────────────────────────────────────────
            H("J   TANK"),
            H("TANK MATERIAL"),
            R("1",  "FABRICATED UNDER BASE",                          "MM",  "YES (THICKNESS MIN. 10 MM)"),
            H("THICKNESS OF TANK"),
            R("3.1","- BOTTOM",                                       "MM"),
            R("3.2","- SIDES",                                        "MM"),
            R("3.3","- TOP",                                          "MM"),
            R("4",  "TYPE OF TANK (SEALED / CONSERVATOR)",            "",    "HERMETICALLY SEALED"),
            H("RADIATOR"),
            R("5",  "NUMBER OF RADIATORS",                            "",    "DETACHABLE"),
            R("6",  "TRANSFORMER MOUNTING",                           "",    "BI-DIRECTIONAL ROLLERS"),

            # ── K. TANK COVER TYPE ───────────────────────────────────────────
            H("K   TANK COVER TYPE"),
            R("1",  "BOLTED",                                         "",    "YES"),
            R("2",  "WELDED",                                         "",    "NA"),
            R("3",  "BELL TYPE",                                      "",    "NA"),
            R("",   "THICKNESS",                                      "MM"),
            H("DIMENSIONS"),
            R("",   "OVERALL WITH ACCESSORIES (LENGTH / WIDTH / HEIGHT)", "MM", "AS PER BGS-EE-003"),
            R("",   "BETWEEN ROLLER AXIS",                            "MM"),

            # ── L. WEIGHTS ───────────────────────────────────────────────────
            H("L   WEIGHTS"),
            R("1",  "TOTAL",                                          "KG"),
            R("2",  "OIL",                                            "LITER"),
            R("3",  "CORE AND WINDING",                               "KG"),
            R("4",  "TANK AND FITTING",                               "KG"),
            R("5",  "VOLUME OF OIL",                                  "LITER"),
            R("6",  "MAKE OF OIL"),

            # ── N. NOISE LEVEL ───────────────────────────────────────────────
            H("N   NOISE LEVEL"),
            R("",   "WITHOUT COOLING",                                "dB",  "*** (AS PER IEC 60076-10)"),
            R("",   "WITH COOLING",                                   "",    "NA"),

            # ── O. CONNECTIONS ───────────────────────────────────────────────
            H("O   CONNECTIONS"),
            H("PRIMARY VOLTAGE SIDE"),
            R("1",    "CABLE CONNECTION",                             "",    "YES"),
            R("1.2",  "CABLE TYPE AND SIZE"),
            R("1.3",  "TYPE OF BUSHING AND RATING",                   "",    "AS PER SPEC. BGS-EE-003"),
            R("1.3.1","QUANTITY OF BUSHINGS"),
            R("1.4",  "SPACE FOR CURRENT TRANSFORMER",                "kA"),
            R("1.5",  "PLUG IN TERMINAL"),
            R("1.6",  "CABLE BOX WITH OIL"),
            R("1.7",  "SF6 CONNECTION",                               "",    "NO"),
            R("1.8",  "PROTECTIVE ENCLOSURE",                         "",    "AIR INSULATED CABLE BOX"),
            R("1.9",  "THERMAL IMAGE WINDOW FOR CABLE INVESTIGATION", "",    "REQUIRED"),
            R("1.10", "PRESSURE RELIEF DIAPHRAGM",                    "",    "REQUIRED"),
            R("1.11", "DISCONNECTING CHAMBERS / LINKS",               "",    "REQUIRED"),
            H("SECONDARY VOLTAGE SIDE"),
            R("2.1",  "CABLE CONNECTION",                             "",    "NO"),
            R("2.2",  "CABLE SIZE",                                   "",    "NO"),
            R("2.3",  "BUS DUCT",                                     "",    "YES"),
            R("2.4",  "BUS DUCT TYPE",                                "",    "PHASE REGULATED AS PER BGS-EE-006"),
            R("2.8",  "BUS DUCT TERMINATIONS",                        "",    "AS PER BGS-EE-003"),
            H("NEUTRAL SIDE"),
            R("3.1",  "NEUTRAL TERMINAL IN A SEPARATE NEUTRAL TERMINAL BOX", "", "YES"),
            R("3.2",  "THERMAL IMAGE WINDOW FOR CABLE INVESTIGATION", "",    "YES"),
            R("3.3",  "PRESSURE RELIEF DIAPHRAGM",                    "",    "YES"),
            H("CONTROL AND PROTECTION DEVICES"),
            R("4",  "PRESSURE RELIEF DEVICE WITH TWO TRIP CONTACT FORM 'C'",       "", "YES"),
            R("5",  "BUCHHOLZ RELAY WITH TWO ALARM AND TWO TRIP CONTACTS",          "", "NA"),
            R("6",  "THERMAL IMAGE TYPE WINDING TEMPERATURE WITH CONTACTS (TWO ALARM / TWO TRIP)", "", "YES"),
            R("7",  "OIL TEMP. INDICATOR WITH CONTACTS (TWO ALARM & TWO TRIP)",     "", "YES"),
            R("8",  "WINDING TEMP. INDICATOR WITH CONTACTS (TWO ALARM & TWO TRIP)", "", "YES"),
            R("9",  "TEMPERATURE METER POCKETS / THERMOWELLS",                      "", "YES"),
            R("10", "THERMOSTAT",                                                    "", "YES"),
            R("11", "LIQUID LEVEL GAUGE WITH 2 CONTACTS (ALARM / TRIP)",            "", "YES"),
            R("12", "MAGNETIC OIL LEVEL GAUGE WITH TWO CONTACTS (ALARM)",           "", "YES"),
            R("13", "PRESSURE VACUUM GAUGE WITH OPERATING 4 CONTACTS (ALARM / TRIP)", "", "YES"),
            R("14", "NEUTRAL CT IN SEPARATE NEUTRAL TERMINAL BOX (Y/N)",            "", "YES (***)"),
            R("15", "CURRENT TRANSFORMER FOR STANDBY E/F PROTECTION",               "", "2000/1A, SP23, 15VA"),
            H("ACCESSORIES"),
            R("1",  "PRIMARY SURGE ARRESTOR",                         "",    "NA"),
            R("2",  "LIFTING EYES & JACKING LUGS",                    "",    "YES"),
            R("3",  "PULLING EYES FOR MOVING TRANSFORMER IN ALL DIRECTIONS", "", "YES"),
            R("4",  "SAFETY VALVE ON TANK AND RADIATORS",             "",    "YES AND BOTTOM"),
            R("5",  "FILLING VALVE ON TANK AND RADIATORS",            "",    "YES AND BOTTOM"),
            R("6",  "SAFETY VALVE ON TANK AND RADIATORS",             "",    "YES AND BOTTOM"),
            R("7",  "FILLING VALVE (DRAIN VALVE) ON TANK AND RADIATORS", "", "YES AND BOTTOM"),
            R("8",  "FILLER PLUG / FILTER VALVES",                    "",    "YES"),
            R("9",  "CASTERS (FIXED / ORIENTABLE)",                   "",    "ORIENTABLE"),
            R("10", "EARTH TERMINAL",                                  "",    "YES: 2"),
            R("11", "MARSHALLING BOX",                                "",    "YES"),
            R("12", "JACKING PADS",                                   "",    "YES"),
            R("13", "THERMOMETERS",                                   "",    "YES"),
            R("14", "PADLOCKS",                                       "",    "YES"),
            R("15", "TERMINAL BOX FOR AUXILIARIES",                   "",    "YES"),

            # ── R. INSPECTION & TESTING ──────────────────────────────────────
            H("R   INSPECTION & TESTING"),
            R("1",  "INSPECTIONS & TESTS",                            "",    "AS PER SPEC. BGS-EE-003"),
            R("2",  "ROUTINE TESTS",                                  "",    "AS PER APPENDIX-3 OF BGS-EE-003"),
            R("3",  "TYPE TESTS & ACOUSTIC SOUND TESTS",              "",    "AS PER APPENDIX-3 OF BGS-EE-003"),
            R("4",  "SPECIAL TESTS",                                  "",    "AS PER APPENDIX-3 OF BGS-EE-003"),

            # ── PAINTING ─────────────────────────────────────────────────────
            H("PAINTING"),
            R("",   "COLOUR"),
            R("",   "PAINTING THICKNESS TANK",                        "",    "RAL 6511 AS PER BGS-MX-001"),
            R("",   "PAINTING THICKNESS RADIATOR"),
            R("",   "GALVANISATION THICKNESS TANK"),
            R("",   "GALVANISATION THICKNESS RADIATOR"),

            # ── LOSS EVALUATION ──────────────────────────────────────────────
            H("LOSS EVALUATION"),
            R("",   "ENERGY COST + LATERS / kWH"),
            R("",   "INTEREST RATE"),
            R("",   "DISCOUNT FACTOR",                                "",    "50%"),

            # ── Q. EXTERNAL POWER SUPPLY REQUIREMENT ─────────────────────────
            H("Q   EXTERNAL POWER SUPPLY REQUIREMENT"),
            R("1",  "EXTERNAL POWER SUPPLY FOR AUXILIARY POWER",      "",    "230V AC, 50 Hz, 1-Ph FOR SPACE HEATER SUPPLY"),
            R("2",  "AUXILIARY LOAD DETAILS",                         "W"),
        ]

    # ──────────────────────────────────────────────────────────────────────────
    # Excel Export
    # ──────────────────────────────────────────────────────────────────────────
    def export_to_excel(self, datasheet_rows: List[Dict], project_info: Dict = None):
        """Export transformer datasheet to formatted Excel workbook."""
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from io import BytesIO

        wb = Workbook()
        ws = wb.active
        ws.title = "Transformer Datasheet"

        header_fill   = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
        header_font   = Font(color="FFFFFF", bold=True, size=10)
        section_fill  = PatternFill(start_color="D6E4F0", end_color="D6E4F0", fill_type="solid")
        section_font  = Font(bold=True, size=10)
        thin = Side(style="thin")
        border = Border(left=thin, right=thin, top=thin, bottom=thin)

        row_idx = 1

        # Title row
        ws.merge_cells("A1:F1")
        ws["A1"] = "POWER / DISTRIBUTION TRANSFORMER – DATASHEET"
        ws["A1"].font = Font(bold=True, size=13, color="1F4E79")
        ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
        row_idx = 3

        # Project info
        if project_info:
            for key, val in project_info.items():
                ws.cell(row=row_idx, column=1, value=key.replace("_", " ").title())
                ws.cell(row=row_idx, column=2, value=val)
                row_idx += 1
            row_idx += 1

        # Column headers – 6 columns
        col_headers = ["SI NO.", "DESCRIPTION", "UNIT", "SPECIFIED DESIGN DATA", "VENDOR DATA", "Rev"]
        col_widths  = [9, 52, 10, 35, 30, 8]
        for col_idx, (header, width) in enumerate(zip(col_headers, col_widths), 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.border = border
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            ws.column_dimensions[chr(64 + col_idx)].width = width

        row_idx += 1

        # Data rows
        for row_data in datasheet_rows:
            sr_no        = row_data.get("sr_no", "")
            description  = row_data.get("description", "")
            unit         = row_data.get("unit", "")
            req_data     = row_data.get("required_data", "")
            vendor_data  = row_data.get("vendor_data", "")
            rev          = row_data.get("rev", row_data.get("remarks", ""))

            is_section = (not sr_no) and description and not req_data and not vendor_data

            cells = [sr_no, description, unit, req_data, vendor_data, rev]
            aligns = ["center", "left", "center", "left", "left", "center"]

            for col_idx, (val, align) in enumerate(zip(cells, aligns), 1):
                cell = ws.cell(row=row_idx, column=col_idx, value=val)
                cell.border = border
                cell.alignment = Alignment(horizontal=align, vertical="top", wrap_text=True)
                if is_section:
                    cell.fill = section_fill
                    cell.font = section_font

            row_idx += 1

        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf
