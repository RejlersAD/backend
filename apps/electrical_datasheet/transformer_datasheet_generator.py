"""
Power/Distribution Transformer Datasheet Generator

Generates a transformer datasheet aligned 1:1 with the ADNOC / Borouge
template `DS-13-574-EP-00001.xlsm` (sheets `1.25MVA` and `25MVA`).

The full structural template (sections A–U, units, default specified values
and Rev codes) is **soft-coded** in
`transformer_datasheet_schema.py` — this module orchestrates extraction and
Excel rendering only.

Columns: Sl. No. | DESCRIPTION | UNIT | SPECIFIED DESIGN DATA | VENDOR DATA | Rev
"""
from __future__ import annotations

import json
import logging
from io import BytesIO
from typing import Dict, List, Optional

import PyPDF2
from django.conf import settings
from openai import OpenAI

from .transformer_datasheet_schema import (
    DOC_HEADER,
    TABLE_HEADERS,
    TABLE_COL_WIDTHS,
    VARIANT_POWER,
    VARIANT_DISTRIBUTION,
    VARIANT_DEFAULTS,
    SHEET_TITLES,
    DEFAULT_PAGINATION,
    REVISION_HISTORY,
    REVISION_FOOTER_NOTES,
    HOLD_ENTRIES,
    INDEX_ENTRIES,
    ABBREVIATIONS,
    GENERAL_NOTES,
    build_schema,
    detect_variant_from_text,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Soft-coded constants
# ─────────────────────────────────────────────────────────────────────────────
AI_MODEL              = "gpt-4o"
AI_TEMPERATURE        = 0.1
AI_MAX_TOKENS         = 8000
AI_DOC_TEXT_LIMIT     = 8000      # chars of document content sent to model
MIN_DOC_TEXT_LEN      = 20         # minimum extracted PDF text length

# Excel formatting constants
EXCEL_TITLE_FILL      = "1F4E79"
EXCEL_TITLE_FONT      = "FFFFFF"
EXCEL_SECTION_FILL    = "D6E4F0"
EXCEL_HEADER_FILL     = "1F4E79"


class TransformerDatasheetGenerator:
    """Generate Power/Distribution Transformer datasheets from sizing calculations."""

    def __init__(self):
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)

    # ──────────────────────────────────────────────────────────────────────
    # Document Extraction (multi-format — PDF / Excel / Word / image / …)
    # ──────────────────────────────────────────────────────────────────────
    def extract_text_from_pdf(self, pdf_file) -> str:
        """Extract text from any supported document type (kept name for back-compat)."""
        from .document_extractor import extract_text
        text = extract_text(pdf_file)
        logger.info(f"[TransformerDatasheet] Extracted {len(text)} chars from {getattr(pdf_file, 'name', '?')}")
        return text

    # ──────────────────────────────────────────────────────────────────────
    # Main entry point
    # ──────────────────────────────────────────────────────────────────────
    def generate_datasheet_from_sizing_calc(
        self, pdf_file, project_info: Optional[Dict] = None
    ) -> Dict:
        """Generate datasheet rows from a transformer sizing calculation PDF."""
        try:
            # ── Step 1: Always start with the full template ──────────────────────
            datasheet_rows = self._get_default_datasheet_template()

            # ── Step 2: Extract text from PDF ────────────────────────────────────
            logger.info("[TransformerDatasheet] Extracting text from sizing calculation PDF…")
            doc_text = self.extract_text_from_pdf(pdf_file)

<<<<<<< HEAD
            if not doc_text or len(doc_text) < MIN_DOC_TEXT_LEN:
                logger.error(
                    f"[TransformerDatasheet] Insufficient text: "
                    f"{len(doc_text) if doc_text else 0} chars"
                )
                return {
                    "success": False,
                    "error": (
                        "Could not extract text from the PDF. The file may be image-based "
                        "or empty. Please provide a text-based transformer sizing "
                        "calculation document."
                    ),
                }

            # Detect variant (power vs distribution) from document content
            variant = detect_variant_from_text(doc_text)
            logger.info(f"[TransformerDatasheet] Detected variant: {variant}")

            # Build the structural template for the detected variant
            template_rows = build_schema(variant)

            # Use AI to fill VENDOR DATA values from the document content
            logger.info("[TransformerDatasheet] Populating vendor data via AI…")
            populated_rows = self._populate_vendor_data_with_ai(
                template_rows, doc_text, variant, project_info
            )
=======
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
>>>>>>> c2ffe7a2aedceca15c10a57859e766d539217d13

            summary = {
                "variant":          variant,
                "total_rows":       len(populated_rows),
                "section_rows":     sum(1 for r in populated_rows if r.get("is_section")),
                "data_rows":        sum(1 for r in populated_rows if not r.get("is_section")),
                "completed_fields": sum(
                    1 for r in populated_rows
                    if not r.get("is_section") and (r.get("vendor_data") or "").strip()
                ),
                "missing_fields":   sum(
                    1 for r in populated_rows
                    if not r.get("is_section") and not (r.get("vendor_data") or "").strip()
                ),
            }

<<<<<<< HEAD
            logger.info(
                f"[TransformerDatasheet] ✅ Generated {summary['total_rows']} rows "
                f"({summary['completed_fields']} vendor fields populated)"
            )

=======
            logger.info(f"[TransformerDatasheet] ✅ {summary['total_rows']} rows | {summary['completed_fields']} vendor values filled")
>>>>>>> c2ffe7a2aedceca15c10a57859e766d539217d13
            return {
                "success": True,
                "datasheet_rows": populated_rows,
                "summary": summary,
                "extraction_metadata": {
                    "document_length": len(doc_text),
                    "variant":         variant,
                    "project_info":    project_info or {},
                },
            }

        except Exception as e:
            logger.error(f"[TransformerDatasheet] Error: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

<<<<<<< HEAD
    # ──────────────────────────────────────────────────────────────────────
    # AI vendor-data population
    # ──────────────────────────────────────────────────────────────────────
    def _populate_vendor_data_with_ai(
        self,
        template_rows: List[Dict],
        doc_text: str,
        variant: str,
        project_info: Optional[Dict] = None,
    ) -> List[Dict]:
        """
        Send the structured template to the AI and ask it to populate ONLY the
        ``vendor_data`` field for non-section rows, by extracting matching
        values from the supplied sizing-calculation document.
=======
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
>>>>>>> c2ffe7a2aedceca15c10a57859e766d539217d13

        Returns the rows in the same order with vendor_data filled where found.
        """
        # Build a compact list the AI must populate (sr_no + description + unit)
        ai_targets = [
            {"sr_no": r["sr_no"], "description": r["description"], "unit": r["unit"]}
            for r in template_rows
            if not r.get("is_section")
        ]

        variant_label = (
            "POWER TRANSFORMER (e.g. 25 MVA, 33/11.5 kV)"
            if variant == VARIANT_POWER
            else "DISTRIBUTION TRANSFORMER (e.g. 1250 kVA, 11/0.433 kV)"
        )

        prompt = f"""You are a senior electrical engineer specialising in power and distribution
transformers per IEC 60076 and ADNOC / Borouge specifications.

You will receive:
  1. PROJECT INFORMATION
  2. TRANSFORMER VARIANT detected from the document
  3. The TEXT CONTENT of a Transformer Sizing Calculation document
  4. A LIST OF DATASHEET LINE-ITEMS (sr_no + description + unit)

TASK
Return a JSON ARRAY where each element corresponds — IN THE SAME ORDER — to
each item in the LIST OF DATASHEET LINE-ITEMS, with one key:
  • "vendor_data" : the value extracted FROM THE DOCUMENT for that parameter,
                    or "" (empty string) if not explicitly present.

RULES
- Do NOT invent values. If the document does not state a value, return "".
- Numeric values: include the value only (no unit, since unit is a separate column).
- For YES/NO style entries, return "YES" / "NO" / "NA" / "***" as appropriate.
- For Tag No., extract the actual transformer tag(s) found in the document.
- Preserve original casing from the document where reasonable.
- Output ONLY the JSON array — no markdown fences, no commentary.
- The array length MUST equal the number of LINE-ITEMS supplied.

PROJECT INFORMATION:
{json.dumps(project_info or {}, indent=2)}

TRANSFORMER VARIANT:
{variant_label}

DOCUMENT CONTENT (truncated):
{doc_text[:AI_DOC_TEXT_LIMIT]}

DATASHEET LINE-ITEMS (extract vendor_data for each, in order):
{json.dumps(ai_targets, ensure_ascii=False)}
"""

        try:
            response = self.client.chat.completions.create(
                model=AI_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You extract structured engineering data from technical "
                            "documents. Return only a valid JSON array."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=AI_TEMPERATURE,
                max_tokens=AI_MAX_TOKENS,
            )

            ai_response = (response.choices[0].message.content or "").strip()

            # Strip markdown fences if present
            if "```json" in ai_response:
                ai_response = ai_response.split("```json", 1)[1].split("```", 1)[0]
            elif "```" in ai_response:
                ai_response = ai_response.split("```", 1)[1].split("```", 1)[0]

            extracted = json.loads(ai_response.strip())
            if not isinstance(extracted, list):
                logger.warning("[TransformerDatasheet] AI response is not a list — keeping defaults")
                return template_rows

            # Merge vendor_data back into the template (preserve order)
            data_iter = iter(extracted)
            out: List[Dict] = []
            for row in template_rows:
                if row.get("is_section"):
                    out.append(dict(row))
                    continue
                ai_row = next(data_iter, {}) or {}
                vendor = (ai_row.get("vendor_data") or "").strip() if isinstance(ai_row, dict) else ""
                merged = dict(row)
                if vendor:
                    merged["vendor_data"] = vendor
                out.append(merged)

            return out

        except json.JSONDecodeError as e:
            logger.error(f"[TransformerDatasheet] JSON decode error: {e}")
            return template_rows
        except Exception as e:
            logger.error(f"[TransformerDatasheet] AI extraction error: {e}", exc_info=True)
            return template_rows

<<<<<<< HEAD
    # ──────────────────────────────────────────────────────────────────────
    # Fallback default template
    # ──────────────────────────────────────────────────────────────────────
    def _get_default_datasheet_template(self) -> List[Dict]:
        """Return the full structured template for the power-transformer variant."""
        return build_schema(VARIANT_POWER)

    # ──────────────────────────────────────────────────────────────────────
    # Excel Export — full multi-sheet ADNOC document (Cover / Revision /
    # Hold / Index / Datasheet / Notes) — soft-coded from the schema.
    # ──────────────────────────────────────────────────────────────────────
    def export_to_excel(
        self, datasheet_rows: List[Dict], project_info: Optional[Dict] = None
    ) -> BytesIO:
        """Render the full ADNOC datasheet workbook (6 sheets) to BytesIO."""
=======
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
>>>>>>> c2ffe7a2aedceca15c10a57859e766d539217d13
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

        info = project_info or {}
        pagination = {**DEFAULT_PAGINATION, **(info.get("pagination") or {})}
        rev_letter = info.get("revision", "P")

        # Shared styles ----------------------------------------------------
        thin   = Side(style="thin")
        medium = Side(style="medium")
        border = Border(left=thin, right=thin, top=thin, bottom=thin)
        thick_border = Border(left=medium, right=medium, top=medium, bottom=medium)

        title_fill   = PatternFill(start_color=EXCEL_TITLE_FILL,   end_color=EXCEL_TITLE_FILL,   fill_type="solid")
        header_fill  = PatternFill(start_color=EXCEL_HEADER_FILL,  end_color=EXCEL_HEADER_FILL,  fill_type="solid")
        section_fill = PatternFill(start_color=EXCEL_SECTION_FILL, end_color=EXCEL_SECTION_FILL, fill_type="solid")
        title_font   = Font(color=EXCEL_TITLE_FONT, bold=True, size=12)
        header_font  = Font(color="FFFFFF", bold=True, size=10)
        section_font = Font(bold=True, size=10)
        bold9        = Font(bold=True, size=9)
        bold10       = Font(bold=True, size=10)
        bold11       = Font(bold=True, size=11)

        company_doc = info.get("company_doc_number")        or DOC_HEADER["company_doc_default"]
        contractor  = info.get("contractor_drawing_number") or DOC_HEADER["contractor_default"]
        rejlers     = info.get("rejlers_drawing_number")    or DOC_HEADER["rejlers_default"]

        # ── Helper: render the 7-row ADNOC document header block (A1:F7)
        def _render_header(ws, sheet_pageno: str):
            for col_idx, width in enumerate(TABLE_COL_WIDTHS, start=1):
                ws.column_dimensions[chr(64 + col_idx)].width = width

            ws.merge_cells("A1:C2")
            ws["A1"] = DOC_HEADER["company_name"]
            ws["A1"].font = bold11
            ws["A1"].alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

            ws.merge_cells("D1:E1")
            ws["D1"] = DOC_HEADER["company_doc_label"]
            ws["D1"].font = bold9
            ws["D1"].alignment = Alignment(horizontal="center", vertical="center")
            ws.merge_cells("D2:E2")
            ws["D2"] = company_doc
            ws["D2"].alignment = Alignment(horizontal="center", vertical="center")

            ws["F1"] = "Rev"
            ws["F1"].font = bold9
            ws["F1"].alignment = Alignment(horizontal="center", vertical="center")
            ws["F2"] = rev_letter
            ws["F2"].alignment = Alignment(horizontal="center", vertical="center")

            ws.merge_cells("A3:C3")
            ws["A3"] = f"LOCATION:\n{DOC_HEADER['location']}"
            ws["A3"].alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

            ws.merge_cells("D3:E3")
            ws["D3"] = DOC_HEADER["project_title"]
            ws["D3"].alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            ws["F3"] = f"Sheet\n{sheet_pageno}"
            ws["F3"].alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

            ws.merge_cells("A4:C4")
            ws["A4"] = DOC_HEADER["document_title"]
            ws["A4"].font = bold10
            ws["A4"].alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

            ws.merge_cells("D4:F4")
            ws["D4"] = DOC_HEADER["contractor_label"]
            ws["D4"].font = bold9
            ws["D4"].alignment = Alignment(horizontal="center", vertical="center")
            ws.merge_cells("D5:F5")
            ws["D5"] = contractor
            ws["D5"].alignment = Alignment(horizontal="center", vertical="center")

            ws.merge_cells("D6:F6")
            ws["D6"] = DOC_HEADER["rejlers_label"]
            ws["D6"].font = bold9
            ws["D6"].alignment = Alignment(horizontal="center", vertical="center")
            ws.merge_cells("D7:F7")
            ws["D7"] = rejlers
            ws["D7"].alignment = Alignment(horizontal="center", vertical="center")

            for r in range(1, 8):
                for c in range(1, 7):
                    ws.cell(row=r, column=c).border = border

        def _banner(ws, row, text):
            """Section banner spanning A:F."""
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
            cell = ws.cell(row=row, column=1, value=text)
            cell.fill = title_fill
            cell.font = title_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
            for c in range(1, 7):
                ws.cell(row=row, column=c).border = border

        # ── Workbook scaffold ─────────────────────────────────────────────
        wb = Workbook()
        wb.remove(wb.active)

        # ╔════════════════════════════════════════════════════════════════╗
        # ║ 1) COVERSHEET                                                  ║
        # ╚════════════════════════════════════════════════════════════════╝
        ws = wb.create_sheet(SHEET_TITLES["cover"])
        _render_header(ws, pagination["cover"])
        ws.merge_cells("A9:F12")
        cover_title = info.get("variant_title") or VARIANT_DEFAULTS[VARIANT_POWER]["title_line"]
        ws["A9"] = f"TECHNICAL DATASHEET FOR TRANSFORMER (POWER AND DISTRIBUTION)\n\n{cover_title}"
        ws["A9"].fill = title_fill
        ws["A9"].font = Font(color=EXCEL_TITLE_FONT, bold=True, size=14)
        ws["A9"].alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for r in range(9, 13):
            for c in range(1, 7):
                ws.cell(row=r, column=c).border = thick_border

        # ╔════════════════════════════════════════════════════════════════╗
        # ║ 2) REVISION                                                    ║
        # ╚════════════════════════════════════════════════════════════════╝
        ws = wb.create_sheet(SHEET_TITLES["revision"])
        _render_header(ws, pagination["revision"])
        _banner(ws, 9, "REVISION HISTORY")
        rev_headers = ["Rev. No.", "Date", "Section or Page Revised", "Revision Description"]
        ws.cell(row=10, column=1, value=rev_headers[0]).font = header_font
        ws.cell(row=10, column=1).fill = header_fill
        ws.cell(row=10, column=2, value=rev_headers[1]).font = header_font
        ws.cell(row=10, column=2).fill = header_fill
        ws.cell(row=10, column=3, value=rev_headers[2]).font = header_font
        ws.cell(row=10, column=3).fill = header_fill
        ws.merge_cells("D10:F10")
        ws.cell(row=10, column=4, value=rev_headers[3]).font = header_font
        ws.cell(row=10, column=4).fill = header_fill
        for c in range(1, 7):
            ws.cell(row=10, column=c).border = border
            ws.cell(row=10, column=c).alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

        r = 11
        for rev, date, section, desc in REVISION_HISTORY:
            ws.cell(row=r, column=1, value=rev).alignment = Alignment(horizontal="center")
            ws.cell(row=r, column=2, value=date).alignment = Alignment(horizontal="center")
            ws.cell(row=r, column=3, value=section).alignment = Alignment(horizontal="center")
            ws.merge_cells(start_row=r, start_column=4, end_row=r, end_column=6)
            ws.cell(row=r, column=4, value=desc).alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
            for c in range(1, 7):
                ws.cell(row=r, column=c).border = border
            r += 1

        r += 1
        ws.cell(row=r, column=1, value="NOTES:").font = bold10
        r += 1
        for note in REVISION_FOOTER_NOTES:
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
            cell = ws.cell(row=r, column=1, value=note)
            cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
            r += 1

        # ╔════════════════════════════════════════════════════════════════╗
        # ║ 3) HOLD                                                        ║
        # ╚════════════════════════════════════════════════════════════════╝
        ws = wb.create_sheet(SHEET_TITLES["hold"])
        _render_header(ws, pagination["hold"])
        _banner(ws, 9, "HOLDS")
        ws.cell(row=10, column=1, value="Rev. No.").font = header_font
        ws.cell(row=10, column=1).fill = header_fill
        ws.merge_cells("B10:E10")
        ws.cell(row=10, column=2, value="Hold Description").font = header_font
        ws.cell(row=10, column=2).fill = header_fill
        ws.cell(row=10, column=6, value="Section").font = header_font
        ws.cell(row=10, column=6).fill = header_fill
        for c in range(1, 7):
            ws.cell(row=10, column=c).border = border
            ws.cell(row=10, column=c).alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

        r = 11
        for rev, desc, section in HOLD_ENTRIES:
            ws.cell(row=r, column=1, value=rev).alignment = Alignment(horizontal="center")
            ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=5)
            ws.cell(row=r, column=2, value=desc).alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
            ws.cell(row=r, column=6, value=section).alignment = Alignment(horizontal="center")
            for c in range(1, 7):
                ws.cell(row=r, column=c).border = border
            r += 1

        # ╔════════════════════════════════════════════════════════════════╗
        # ║ 4) INDEX                                                       ║
        # ╚════════════════════════════════════════════════════════════════╝
        ws = wb.create_sheet(SHEET_TITLES["index"])
        _render_header(ws, pagination["index"])
        _banner(ws, 9, "TABLE OF CONTENTS")
        ws.cell(row=10, column=1, value="Sr. No.").font = header_font
        ws.cell(row=10, column=1).fill = header_fill
        ws.merge_cells("B10:E10")
        ws.cell(row=10, column=2, value="DESCRIPTION").font = header_font
        ws.cell(row=10, column=2).fill = header_fill
        ws.cell(row=10, column=6, value="SHEET").font = header_font
        ws.cell(row=10, column=6).fill = header_fill
        for c in range(1, 7):
            ws.cell(row=10, column=c).border = border
            ws.cell(row=10, column=c).alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

        r = 11
        for sr, desc, sheet in INDEX_ENTRIES:
            ws.cell(row=r, column=1, value=sr).alignment = Alignment(horizontal="center")
            ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=5)
            ws.cell(row=r, column=2, value=desc).alignment = Alignment(horizontal="left", vertical="center")
            ws.cell(row=r, column=6, value=sheet).alignment = Alignment(horizontal="center")
            for c in range(1, 7):
                ws.cell(row=r, column=c).border = border
            r += 1

        r += 1
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
        ws.cell(row=r, column=1, value="ABBREVIATIONS:").font = bold10
        r += 1
        for abbr, meaning in ABBREVIATIONS:
            ws.cell(row=r, column=1, value=abbr).font = bold9
            ws.cell(row=r, column=1).alignment = Alignment(horizontal="center")
            ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=6)
            ws.cell(row=r, column=2, value=meaning).alignment = Alignment(horizontal="left", vertical="center")
            for c in range(1, 7):
                ws.cell(row=r, column=c).border = border
            r += 1

        # ╔════════════════════════════════════════════════════════════════╗
        # ║ 5) DATASHEET (variant body)                                    ║
        # ╚════════════════════════════════════════════════════════════════╝
        ws_title = info.get("data_sheet_name") or info.get("variant_title") or SHEET_TITLES["data"]
        # Excel sheet-name rules: max 31 chars, no  : \ / ? * [ ]
        import re
        ws_title = re.sub(r"[:\\/\?\*\[\]]", "-", ws_title)[:31] or SHEET_TITLES["data"]
        ws = wb.create_sheet(ws_title)
        _render_header(ws, pagination["data"])

        # Variant title (row 9)
        variant_title = info.get("variant_title", "")
        if not variant_title:
            for row in datasheet_rows:
                if (row.get("description") or "").strip().upper() == "RATING":
                    rating = (row.get("vendor_data") or row.get("required_data") or "").strip()
                    unit   = (row.get("unit") or "").strip()
                    if rating:
                        variant_title = f"{rating} {unit}".strip() + " TRANSFORMER"
                    break
            if not variant_title:
                variant_title = VARIANT_DEFAULTS[VARIANT_POWER]["title_line"]
        _banner(ws, 9, variant_title)

        # Body header (row 10)
        body_header_row = 10
        for col_idx, label in enumerate(TABLE_HEADERS, start=1):
            cell = ws.cell(row=body_header_row, column=col_idx, value=label)
            cell.fill = header_fill
            cell.font = header_font
            cell.border = border
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

        # Body data rows
        row_idx = body_header_row + 1
        aligns  = ["center", "left", "center", "left", "left", "center"]

        for row in datasheet_rows:
            sr_no       = row.get("sr_no", "")
            description = row.get("description", "")
            unit        = row.get("unit", "")
            req_data    = row.get("required_data", "")
            vendor_data = row.get("vendor_data", "")
            rev         = row.get("rev", "")

            if "is_section" in row:
                is_section = bool(row["is_section"])
            else:
                is_section = bool(description and not unit and not req_data and not vendor_data)

            cells = [sr_no, description, unit, req_data, vendor_data, rev]
            for col_idx, (val, align) in enumerate(zip(cells, aligns), start=1):
                cell = ws.cell(row=row_idx, column=col_idx, value=val)
                cell.border = border
                cell.alignment = Alignment(horizontal=align, vertical="top", wrap_text=True)
                if is_section:
                    cell.fill = section_fill
                    cell.font = section_font

            row_idx += 1

        ws.freeze_panes = "A11"

        # ╔════════════════════════════════════════════════════════════════╗
        # ║ 6) NOTES                                                       ║
        # ╚════════════════════════════════════════════════════════════════╝
        ws = wb.create_sheet(SHEET_TITLES["notes"])
        _render_header(ws, pagination["notes"])
        _banner(ws, 9, "GENERAL NOTES")
        ws.cell(row=10, column=1, value="SI. NO").font = header_font
        ws.cell(row=10, column=1).fill = header_fill
        ws.merge_cells("B10:F10")
        ws.cell(row=10, column=2, value="Description").font = header_font
        ws.cell(row=10, column=2).fill = header_fill
        for c in range(1, 7):
            ws.cell(row=10, column=c).border = border
            ws.cell(row=10, column=c).alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

        r = 11
        for label, text in GENERAL_NOTES:
            ws.cell(row=r, column=1, value=label).alignment = Alignment(horizontal="center", vertical="top")
            ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=6)
            ws.cell(row=r, column=2, value=text).alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
            for c in range(1, 7):
                ws.cell(row=r, column=c).border = border
            r += 1

        # ── Save ─────────────────────────────────────────────────────────
        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf
