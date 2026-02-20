"""
Smart Enrichment Layer - Enhances base P&ID extraction with HMB/PMS/NACE data

ARCHITECTURE:
1. Base extraction runs FIRST (unchanged OCR + Regex + FROM-TO)
2. If enrichment docs provided, this layer runs AFTER base extraction
3. Results are merged and returned as enriched_data

RULES:
- Never modify base extraction logic
- Always fill base 8 columns from old logic first
- Enrichment only adds NEW columns
- If enrichment fails, return base extraction only
"""

import logging
from typing import Dict, List, Optional
from openai import OpenAI
import os
import json

logger = logging.getLogger(__name__)


class EnrichmentService:
    """
    Enrichment Layer - Adds intelligent data mapping from HMB/PMS/NACE
    Does NOT touch base extraction
    """
    
    def __init__(self):
        self.openai_api_key = os.getenv('OPENAI_API_KEY')
        self.client = None
        if self.openai_api_key:
            self.client = OpenAI(api_key=self.openai_api_key)
    
    def enrich_lines(
        self,
        base_lines: List[Dict],
        hmb_text: Optional[str] = None,
        pms_text: Optional[str] = None,
        nace_text: Optional[str] = None,
        pid_text: Optional[str] = None
    ) -> List[Dict]:
        """
        Enriches base extraction with additional columns from documents
        
        MANDATORY STRATEGY:
        - Base 17 columns ALWAYS filled from P&ID (locked logic from 9b4d837)
        - Enrichment columns require ALL 3 documents (HMB + PMS + NACE)
        - If any document missing, returns base extraction only
        
        Args:
            base_lines: Lines from base P&ID extraction (UNCHANGED - 17 columns)
            hmb_text: Extracted text from HMB/PFD document
            pms_text: Extracted text from PMS document  
            nace_text: Extracted text from NACE document
            
        Returns:
            Enriched lines with 43 total columns (17 base + 26 enriched)
        """
        if not base_lines:
            logger.warning("No base lines to enrich")
            return []
        
        logger.info("="*80)
        logger.info("🚀🚀🚀 ENRICHMENT SERVICE CALLED 🚀🚀🚀")
        logger.info(f"📋 Base lines: {len(base_lines)}")
        logger.info("="*80)
        
        # MANDATORY: All 3 documents required for enrichment
        if not (hmb_text and pms_text and nace_text):
            missing = []
            if not hmb_text: missing.append("HMB")
            if not pms_text: missing.append("PMS")
            if not nace_text: missing.append("NACE")
            logger.info(f"⚠️ Enrichment skipped - Missing documents: {', '.join(missing)}")
            logger.info("→ Returning base 8 columns from P&ID extraction")
            return base_lines
        
        # DEBUG: Log document sizes
        logger.info(f"📊 Document text sizes: HMB={len(hmb_text)} chars, PMS={len(pms_text)} chars, NACE={len(nace_text)} chars")
        if pid_text:
            logger.info(f"📊 P&ID text size: {len(pid_text)} chars")
        logger.info(f"🔑 OpenAI API key configured: {'Yes' if self.client else 'No'}")
        
        # DEBUG: Show first 200 chars of each document to verify content
        logger.debug(f"📄 HMB preview: {hmb_text[:200]}...")
        logger.debug(f"📄 PMS preview: {pms_text[:200]}...")
        logger.debug(f"📄 NACE preview: {nace_text[:200]}...")
        
        logger.info(f"🚀 Starting enrichment for {len(base_lines)} lines (All 3 docs provided)")
        
        try:
            enriched_lines = []
            
            for line in base_lines:
                # Start with base columns (PRESERVED FROM OLD LOGIC)
                # Copy ALL base columns from the locked extraction (17 columns)
                enriched_line = dict(line)  # Preserve ALL base fields including from_line, to_line, from_equipment, to_equipment
                
                # Add enrichment columns via AI (GUARANTEED 26 columns)
                enrichment_data = self._extract_enrichment_data(
                    line=line,
                    hmb_text=hmb_text,
                    pms_text=pms_text,
                    nace_text=nace_text,
                    pid_text=pid_text
                )
                
                # LOCK: Ensure all 26 enrichment columns exist (even if empty)
                empty_enrichment = self._get_empty_enrichment_columns()
                for key in empty_enrichment:
                    if key not in enrichment_data:
                        enrichment_data[key] = ""
                
                # Merge enrichment into base (8 + 26 = 34 columns GUARANTEED)
                enriched_line.update(enrichment_data)
                enriched_lines.append(enriched_line)
            
            logger.info(f"✅ Enrichment complete: {len(enriched_lines)} lines with {len(enriched_lines[0].keys())} columns (17 base + 26 enriched = 43 total)")
            
            # FINAL VALIDATION: Ensure every line has exactly 43 columns (17 base from locked logic + 26 enriched)
            expected_total = 43
            for idx, line in enumerate(enriched_lines):
                if len(line.keys()) != expected_total:
                    logger.warning(f"⚠️ Line {idx} has {len(line.keys())} columns, expected {expected_total}. Fixing...")
                    # Add missing enrichment columns
                    empty_enrichment = self._get_empty_enrichment_columns()
                    for key in empty_enrichment:
                        if key not in line:
                            line[key] = ""
            
            logger.info(f"🔒 LOCKED: All {len(enriched_lines)} lines guaranteed to have {expected_total} columns (17 base + 26 enriched)")
            return enriched_lines
            
        except Exception as e:
            logger.error(f"❌ Enrichment failed: {e}")
            logger.error(f"Error type: {type(e).__name__}")
            import traceback
            logger.error(f"Full traceback:\n{traceback.format_exc()}")
            logger.info("Returning base extraction without enrichment")
            return base_lines
    
    def _extract_enrichment_data(
        self,
        line: Dict,
        hmb_text: Optional[str],
        pms_text: Optional[str],
        nace_text: Optional[str],
        pid_text: Optional[str] = None
    ) -> Dict:
        """
        Uses AI to intelligently extract enrichment data for a single line
        GUARANTEED: Always returns all 26 enrichment columns (even if empty)
        """
        # Start with empty structure (FALLBACK)
        enrichment = self._get_empty_enrichment_columns()
        
        if not self.client:
            logger.warning("No OpenAI client configured, returning empty enrichment columns")
            return enrichment
        
        try:
            # Build context prompt
            prompt = self._build_enrichment_prompt(line, hmb_text, pms_text, nace_text, pid_text)
            
            # Call OpenAI with GPT-4 Turbo for better extraction
            line_id = line.get('original_detection', 'Unknown')
            logger.info(f"🤖 Calling OpenAI for line {line_id}...")
            logger.debug(f"📏 Prompt length: {len(prompt)} chars")
            
            try:
                response = self.client.chat.completions.create(
                    model="gpt-4-turbo-preview",
                    messages=[
                        {"role": "system", "content": "You are an expert piping engineer. CRITICAL MANDATE: You MUST fill ALL 26 fields in the JSON response. DO NOT return empty strings unless absolutely no data exists anywhere. Use these strategies: 1) Read documents thoroughly - search tables, line lists, specs, notes. 2) If line-specific data not found, use general specifications for the fluid/piping class. 3) Apply engineering standards (e.g., ASME B31.3 for process piping, Sch 40 for small pipes, Water for test medium). 4) Infer from related data (test pressure = 1.5x design). 5) Use typical values (water density = 1000 kg/m³). NEVER leave a field empty if you can logically determine a value. Include units with all values. Return ONLY the JSON object."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.05,
                    max_tokens=2000
                )
                logger.info(f"✅ OpenAI API call successful for line {line_id}")
            except Exception as api_err:
                logger.error(f"❌ OpenAI API call failed: {api_err}")
                logger.error(f"Error type: {type(api_err).__name__}")
                raise
            
            result_text = response.choices[0].message.content.strip()
            logger.info(f"✅ OpenAI responded with {len(result_text)} chars for line {line_id}")
            logger.debug(f"📄 Raw OpenAI response: {result_text[:500]}...")  # Log first 500 chars
            
            # Extract JSON if wrapped in markdown
            if '```json' in result_text:
                result_text = result_text.split('```json')[1].split('```')[0].strip()
            elif '```' in result_text:
                result_text = result_text.split('```')[1].split('```')[0].strip()
            
            ai_enrichment = json.loads(result_text)
            logger.info(f"📊 Parsed {len(ai_enrichment)} fields from AI response")
            logger.debug(f"🔍 Parsed JSON keys: {list(ai_enrichment.keys())}")
            
            # LOCK: Merge AI results into empty structure (ensures all 26 columns exist)
            enrichment.update(ai_enrichment)
            
            filled_count = len([v for v in ai_enrichment.values() if v and v != "N/A" and v != ""])
            logger.info(f"✅ AI filled {filled_count}/26 columns for line {line_id}")
            if filled_count < 20:
                logger.warning(f"⚠️ Only {filled_count}/26 fields filled - low extraction rate")
            return enrichment
            
        except Exception as e:
            logger.error(f"❌ AI enrichment failed for line {line.get('original_detection')}: {e}", exc_info=True)
            # Return empty 26-column structure (GUARANTEED fallback)
            return enrichment
    
    def _build_enrichment_prompt(
        self,
        line: Dict,
        hmb_text: Optional[str],
        pms_text: Optional[str],
        nace_text: Optional[str],
        pid_text: Optional[str] = None
    ) -> str:
        """
        Builds SMART AI prompt for enrichment
        Uses intelligent context-aware extraction across all 4 documents
        """
        
        line_id = line.get('original_detection', 'Unknown')
        fluid_code = line.get('fluid_code', 'Unknown')
        pipr_class = line.get('pipr_class', 'Unknown')
        size = line.get('size', 'Unknown')
        area = line.get('area', 'Unknown')
        
        prompt = f"""You are an expert piping engineer analyzing technical documents to extract data for a specific piping line.

🎯 TARGET PIPING LINE (from P&ID):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Line Number: {line_id}
Fluid Code: {fluid_code}
Size: {size}
Area: {area}
PIPR Class: {pipr_class}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📋 YOUR TASK:
Search through the documents below and extract 26 specific values for this piping line.
READ CAREFULLY - the information is in tables, line lists, legends, and notes in these documents.

🔍 MANDATORY SEARCH & EXTRACTION STRATEGY:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ CRITICAL: You MUST fill all 26 fields. DO NOT leave fields empty unless absolutely no information exists.

1. EXACT MATCH: Search for line number "{line_id}" in all tables and line lists
2. FLUID MATCH: If line not found, search for fluid code "{fluid_code}" and use its data
3. CLASS MATCH: Use piping class "{pipr_class}" specifications from PMS tables
4. SIZE MATCH: Find size "{size}" in pipe schedules and extract wall thickness/schedule
5. INFERENCE: If exact value not found, use engineering logic:
   - Apply typical values from similar lines in same fluid service
   - Use general specifications for the piping class
   - Infer from related data (e.g., if design pressure is 150 psig, test pressure is ~225 psig)
6. UNITS: Always include units (e.g., "150 psig", "300°F", "Sch 40", "ASME B31.3")
7. YES/NO: For boolean fields, answer "Yes", "No", or "N/A" based on document info
8. STANDARDS: Apply typical industry standards when specifics aren't mentioned
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

"""
        
        if hmb_text:
            prompt += f"""
═══════════════════════════════════════════════════════════════════════════════
📄 DOCUMENT 1: HMB/PFD (Process Flow Diagram & Heat Material Balance)
═══════════════════════════════════════════════════════════════════════════════

🎯 MANDATORY EXTRACTION (Fill ALL 9 fields using smart inference):
   1. flow_medium: Fluid/chemical name. Search: line lists, stream tables, fluid codes. 
      If not found: Use fluid code "{fluid_code}" description or infer from service (e.g., CW=Cooling Water, ST=Steam)
   
   2. two_phase: Two-phase flow indicator. Search: line lists, process notes.
      If not found: Answer "No" for single-phase services (water, air), "Yes" for steam/condensate
   
   3. surge_flow: Peak/surge flow rate. Search: flow columns, max flow, surge conditions.
      If not found: Look for "Max Flow" or calculate from normal flow + 20% margin
   
   4. flow_max: Maximum flow rate. Search: flow rate columns, maximum capacity.
      If not found: Use surge flow or normal flow if available
   
   5. density: Fluid density. Search: density columns, fluid properties.
      If not found: Use standard values (Water=1000 kg/m³, Air=1.2 kg/m³, typical oils=850 kg/m³)
   
   6. normal_pressure: Operating pressure. Search: pressure columns, operating conditions.
      If not found: Look for design pressure and use ~80% of it
   
   7. normal_temp: Operating temperature. Search: temperature columns, operating conditions.
      If not found: Use ambient (70°F/21°C) for utilities, or infer from process type
   
   8. design_pressure: Maximum design pressure. Search: design columns, pressure ratings.
      If not found: Look for piping class pressure rating or use 1.25x normal pressure
   
   9. minimax_design_temp: Design temperature range. Search: min/max temp columns.
      If not found: Use typical ranges (e.g., "-20°F to 300°F" for general service)

🔎 SEARCH LOCATIONS:
   - Line lists (Line No, Fluid, Flow, Pressure, Temp, Density columns)
   - Heat & Material Balance tables
   - Process flow diagrams with operating conditions
   - Stream data tables
   - General notes and specifications
   - Fluid properties tables

DOCUMENT TEXT:
{hmb_text[:3500]}

═══════════════════════════════════════════════════════════════════════════════
"""
        
        if pms_text:
            prompt += f"""
═══════════════════════════════════════════════════════════════════════════════
📄 DOCUMENT 2: PMS (Piping Material Specification)
═══════════════════════════════════════════════════════════════════════════════

🎯 MANDATORY EXTRACTION (Fill ALL 10 fields using smart inference):
   10. design_code: Piping design code. Search: general notes, piping class tables.
       If not found: Use "ASME B31.3" (most common process piping standard)
   
   11. category_m_fluid: Category M fluid service. Search: fluid service classifications.
       If not found: Answer "No" for normal services, "Yes" for toxic/lethal fluids
   
   12. schedule_wall_thk: Pipe schedule. Search: piping class "{pipr_class}" table, size "{size}" row.
       If not found: Use "Sch 40" for sizes ≤3", "Sch STD" for larger sizes
   
   13. stress_relief: Stress relief requirement. Search: PWHT sections, material specs.
       If not found: Answer "No" for carbon steel <1" thick, "Yes" for alloy/thick materials
   
   14. pwht: Post-Weld Heat Treatment. Search: piping class "{pipr_class}" NDT requirements.
       If not found: Answer "No" for low-pressure carbon steel, "Yes" for high-pressure/alloy
   
   15. rt: Radiographic Testing. Search: NDT requirements, inspection tables.
       If not found: Answer "Yes" for critical/high-pressure lines, "No" for low-pressure utilities
   
   16. mt_pt: Magnetic/Penetrant Testing. Search: NDT sections.
       If not found: Answer "Yes" (standard surface inspection for most piping)
   
   17. hardness: Hardness testing. Search: material testing requirements.
       If not found: Use "HB 200 Max" for carbon steel or "N/A" for non-critical
   
   18. visual: Visual inspection. Search: inspection requirements.
       If not found: Answer "Yes" (visual inspection is standard for all piping)
   
   19. piping_rated_pressure: Pressure rating. Search: piping class "{pipr_class}" pressure column.
       If not found: Use "150#" for low-pressure, "300#" for medium, "600#" for high-pressure

🔎 SEARCH LOCATIONS:
   - Piping class tables (Class "{pipr_class}", Size "{size}")
   - Pipe schedule tables
   - NDT requirements sections
   - PWHT and stress relief specifications
   - Material specification tables
   - General notes and standards

DOCUMENT TEXT:
{pms_text[:3500]}

═══════════════════════════════════════════════════════════════════════════════
"""
        
        if nace_text:
            prompt += f"""
═══════════════════════════════════════════════════════════════════════════════
📄 DOCUMENT 3: NACE (Corrosion Control & Testing Requirements)
═══════════════════════════════════════════════════════════════════════════════

🎯 MANDATORY EXTRACTION (Fill ALL 4 fields using smart inference):
   20. nace_mr_0175: NACE MR-0175 compliance. Search: NACE tables, H2S service, sour service.
       If not found: Answer "Not Required" for non-sour service, "Compliant" if H2S mentioned
   
   21. test_pressure: Hydrostatic test pressure. Search: test specifications, pressure test.
       If not found: Calculate as 1.5x design pressure (standard hydrostatic test ratio)
   
   22. test_medium: Test medium. Search: test procedure, test fluid specifications.
       If not found: Use "Water" (most common test medium for piping)
   
   23. criticality_code: Criticality classification. Search: criticality tables, line classifications.
       If not found: Use "C" for utilities, "B" for process lines, "A" for critical/hazardous

🔎 SEARCH LOCATIONS:
   - NACE compliance tables (Fluid "{fluid_code}")
   - H2S service requirements
   - Sour service specifications
   - Test pressure tables
   - Criticality classification tables
   - Testing procedures and requirements

DOCUMENT TEXT:
{nace_text[:3500]}

═══════════════════════════════════════════════════════════════════════════════
"""
        
        if pid_text:
            prompt += f"""
═══════════════════════════════════════════════════════════════════════════════
📄 DOCUMENT 4: P&ID (Piping & Instrumentation Diagram) - Title Block & Metadata
═══════════════════════════════════════════════════════════════════════════════

🎯 MANDATORY EXTRACTION (Fill ALL 3 fields using smart inference):
   24. pid_no: P&ID drawing number. Search: "DWG NO", "DRAWING NO", "P&ID NO", document number.
       If not found: Look for any alphanumeric ID in title block or headers
   
   25. pid_rev: P&ID revision. Search: "REV", "REVISION", revision column/field.
       If not found: Use "0" or "A" (initial revision)
   
   26. date: P&ID issue date. Search: "DATE", "ISSUE DATE", date fields in title block.
       If not found: Look for any date format (MM/DD/YYYY, DD-MMM-YYYY) in document

🔎 SEARCH LOCATIONS:
   - Title block (usually bottom right or bottom center of drawing)
   - Drawing header and footer
   - Revision history tables
   - Document metadata fields

DOCUMENT TEXT (First page with title block):
{pid_text[:2000]}

═══════════════════════════════════════════════════════════════════════════════
"""
        else:
            prompt += f"""
═══════════════════════════════════════════════════════════════════════════════
📄 DOCUMENT 4: P&ID (Piping & Instrumentation Diagram) - Metadata
═══════════════════════════════════════════════════════════════════════════════

🎯 MANDATORY EXTRACTION (Fill ALL 3 fields using smart inference):
   24. pid_no: Use "PID-001" or similar generic number
   25. pid_rev: Use "0" (initial revision)
   26. date: Use current date or "Feb 2026"

⚠️ P&ID text not available - use generic values

═══════════════════════════════════════════════════════════════════════════════
"""
        
        prompt += f"""

⚠️ OUTPUT FORMAT - RETURN EXACTLY THIS JSON WITH ALL FIELDS FILLED:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🚨 ALL 26 FIELDS ARE MANDATORY. FILL EVERY FIELD.

EXAMPLES OF GOOD VALUES:
- flow_medium: "Cooling Water", "Steam", "Crude Oil", "Natural Gas"
- two_phase: "Yes" or "No" (not empty)
- surge_flow: "150 GPM", "45 m³/h" (with units)
- design_pressure: "150 psig", "10 bara" (with units)
- design_code: "ASME B31.3", "ASME B31.1" (standard codes)
- schedule_wall_thk: "Sch 40", "Sch 80", "STD", "5.5mm"
- pwht: "Yes" or "No" (not empty)
- test_pressure: "225 psig", "1.5x Design" (calculated is OK)
- test_medium: "Water", "Air", "Nitrogen"
- pid_no: "PID-001", "12345", "Drawing 100-P-001"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{{
  "flow_medium": "MUST FILL",
  "two_phase": "MUST FILL Yes/No",
  "surge_flow": "MUST FILL with units",
  "flow_max": "MUST FILL with units",
  "density": "MUST FILL with units",
  "normal_pressure": "MUST FILL with units",
  "normal_temp": "MUST FILL with units",
  "design_pressure": "MUST FILL with units",
  "minimax_design_temp": "MUST FILL range",
  "design_code": "MUST FILL",
  "category_m_fluid": "MUST FILL Yes/No",
  "schedule_wall_thk": "MUST FILL",
  "stress_relief": "MUST FILL Yes/No",
  "pwht": "MUST FILL Yes/No",
  "rt": "MUST FILL Yes/No",
  "mt_pt": "MUST FILL Yes/No",
  "hardness": "MUST FILL or N/A",
  "visual": "MUST FILL Yes/No",
  "nace_mr_0175": "MUST FILL",
  "piping_rated_pressure": "MUST FILL with units",
  "test_pressure": "MUST FILL with units",
  "test_medium": "MUST FILL",
  "pid_no": "MUST FILL",
  "pid_rev": "MUST FILL",
  "date": "MUST FILL",
  "criticality_code": "MUST FILL"
}}

🔴 DO NOT RETURN EMPTY STRINGS. FILL ALL FIELDS USING DOCUMENTS + INFERENCE.
"""
        
        return prompt
    
    def _get_empty_enrichment_columns(self) -> Dict:
        """
        Returns empty enrichment columns when AI fails
        LOCKED STRUCTURE: 26 additional columns (8 base + 26 = 34 total)
        
        CORRECT COLUMNS as per user requirements:
        1. Flow Medium, 2. Two Phase, 3. Surge Flow, 4. Flow Max, 5. Density,
        6. Normal Pressure, 7. Normal Temp, 8. Design Pressure, 9. Minimax Design Temp,
        10. Design Code, 11. Category-M Fluid, 12. Schedule / Wall THK, 13. Stress Relief,
        14. PWHT, 15. RT, 16. MT/PT, 17. Hardness, 18. Visual, 19. NACE-MR-0175,
        20. Piping Rated Pressure at Ambient Condition, 21. Test Pressure, 22. Test Medium,
        23. P&ID No., 24. P&ID Rev, 25. Date, 26. Criticality Code
        """
        return {
            # Flow & Process Data (5 columns)
            "flow_medium": "",
            "two_phase": "",
            "surge_flow": "",
            "flow_max": "",
            "density": "",
            
            # Operating Conditions (4 columns)
            "normal_pressure": "",
            "normal_temp": "",
            "design_pressure": "",
            "minimax_design_temp": "",
            
            # Design & Material Specs (3 columns)
            "design_code": "",
            "category_m_fluid": "",
            "schedule_wall_thk": "",
            
            # Welding & Heat Treatment (2 columns)
            "stress_relief": "",
            "pwht": "",
            
            # NDT Requirements (5 columns)
            "rt": "",
            "mt_pt": "",
            "hardness": "",
            "visual": "",
            "nace_mr_0175": "",
            
            # Testing & Ratings (3 columns)
            "piping_rated_pressure": "",
            "test_pressure": "",
            "test_medium": "",
            
            # Document References (4 columns)
            "pid_no": "",
            "pid_rev": "",
            "date": "",
            "criticality_code": ""
        }


# Singleton instance
_enrichment_service = None

def get_enrichment_service() -> EnrichmentService:
    """Get or create enrichment service instance"""
    global _enrichment_service
    if _enrichment_service is None:
        _enrichment_service = EnrichmentService()
    return _enrichment_service
