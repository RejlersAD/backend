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
from decouple import config

logger = logging.getLogger(__name__)


class EnrichmentService:
    """
    Enrichment Layer - Adds intelligent data mapping from HMB/PMS/NACE
    Does NOT touch base extraction
    """
    
    def __init__(self):
        # Use decouple.config(to read from .env file (same as Django settings)
        self.openai_api_key = config('OPENAI_API_KEY', default=None)
        self.client = None
        if self.openai_api_key:
            self.client = OpenAI(api_key=self.openai_api_key)
            logger.info("OpenAI client initialized successfully")
        else:
            logger.warning("OPENAI_API_KEY not found in .env - enrichment will return empty columns")
    
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
        - Base 17 columns ALWAYS filled from P&ID (locked logic from commit 9b4d837)
        - Enrichment columns require ALL 3 documents (HMB + PMS + NACE)
        - If any document missing, returns base extraction only
        
        Args:
            base_lines: Lines from base P&ID extraction (UNCHANGED - 17 columns from locked logic)
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
        logger.info("ENRICHMENT SERVICE CALLED")
        logger.info(f"Base lines: {len(base_lines)}")
        logger.info("="*80)
        
        # MANDATORY: All 3 documents required for enrichment
        if not (hmb_text and pms_text and nace_text):
            missing = []
            if not hmb_text: missing.append("HMB")
            if not pms_text: missing.append("PMS")
            if not nace_text: missing.append("NACE")
            logger.info(f"Enrichment skipped - Missing documents: {', '.join(missing)}")
            logger.info("Returning base 8 columns from P&ID extraction")
            return base_lines
        
        # DEBUG: Log document sizes
        logger.info(f"Document text sizes: HMB={len(hmb_text)} chars, PMS={len(pms_text)} chars, NACE={len(nace_text)} chars")
        if pid_text:
            logger.info(f"P&ID text size: {len(pid_text)} chars")
        logger.info(f"OpenAI API key configured: {'Yes' if self.client else 'No'}")
        
        # DEBUG: Show first 200 chars of each document to verify content
        logger.debug(f"HMB preview: {hmb_text[:200]}...")
        logger.debug(f"PMS preview: {pms_text[:200]}...")
        logger.debug(f"NACE preview: {nace_text[:200]}...")
        
        logger.info(f"Starting AI-powered enrichment for {len(base_lines)} lines (All 3 docs provided)")
        logger.info("Using OpenAI GPT-4 to intelligently extract 26 enrichment columns from documents")
        
        try:
            enriched_lines = []
            
            for idx, line in enumerate(base_lines):
                line_id = line.get('original_detection', f'Line-{idx+1}')
                logger.info(f"Processing line {idx+1}/{len(base_lines)}: {line_id}")
                
                # Start with ALL base columns (PRESERVED FROM LOCKED LOGIC - 17 columns)
                # This includes from_line, to_line, from_equipment, to_equipment, etc.
                enriched_line = dict(line)  # Copy ALL base fields to preserve locked extraction
                
                # a"ñû AI ENRICHMENT: Extract intelligent values from all 4 documents
                logger.info(f"   Calling OpenAI to extract enrichment data for {line_id}...")
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
                
                filled_count = len([v for v in enrichment_data.values() if v and v.strip()])
                logger.info(f"   Line {idx+1} enriched: {filled_count}/26 columns filled by AI")
                
                # Merge enrichment into base (17 base + 26 enriched = 43 columns GUARANTEED)
                enriched_line.update(enrichment_data)
                enriched_lines.append(enriched_line)
                
                # Log the enriched line data to verify
                logger.info(f"Enriched line {idx+1} data sample: {list(enriched_line.keys())[:5]}... (Total: {len(enriched_line)} keys)")
            
            logger.info("="*80)
            logger.info(f"Enrichment complete: {len(enriched_lines)} lines with {len(enriched_lines[0].keys())} columns (17 base + 26 enriched = 43 total)")
            logger.info(f"First line sample enrichment columns:")
            if enriched_lines:
                sample = enriched_lines[0]
                logger.info(f"   - flow_medium: {sample.get('flow_medium', 'MISSING')}")
                logger.info(f"   - design_pressure: {sample.get('design_pressure', 'MISSING')}")
                logger.info(f"   - design_code: {sample.get('design_code', 'MISSING')}")
            logger.info("="*80)
            
            # FINAL VALIDATION: Ensure every line has at least 43 columns (17 base + 26 enriched)
            expected_total = 43
            for idx, line in enumerate(enriched_lines):
                if len(line.keys()) < expected_total:
                    logger.warning(f"Line {idx} has {len(line.keys())} columns, expected at least {expected_total}. Fixing...")
                    # Add missing enrichment columns
                    empty_enrichment = self._get_empty_enrichment_columns()
                    for key in empty_enrichment:
                        if key not in line:
                            line[key] = ""
            
            logger.info(f"LOCKED: All {len(enriched_lines)} lines guaranteed to have at least {expected_total} columns (17 base + 26 enriched)")
            logger.info("="*80)
            logger.info("RETURNING ENRICHED DATA TO TASK")
            logger.info("="*80)
            return enriched_lines
            
        except Exception as e:
            logger.error(f"Enrichment failed: {e}")
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
            logger.info(f"Calling OpenAI for line {line_id}...")
            logger.debug(f"Prompt length: {len(prompt)} chars")
            
            try:
                response = self.client.chat.completions.create(
                    model="gpt-4o",  # Updated to current model (gpt-4-turbo-preview deprecated)
                    messages=[
                        {"role": "system", "content": "You are an expert piping engineer with 30+ years experience. ABSOLUTE REQUIREMENT: Fill ALL 26 fields with real values. ZERO TOLERANCE for empty strings, null, or N/A. EXTRACTION HIERARCHY: 1) Exact match from documents 2) Similar line specifications 3Piping class standards 4Industry best practices 5) Engineering judgment. ALWAYS provide a concrete value with units. Examples of GOOD responses: '150 psig', 'Sch 40', 'ASME B31.3', 'Water', 'Yes', '300,%%F', '10%'. Examples of BAD responses: '', 'N/A', 'Not specified', 'See documents'. If uncertain, add qualifier like '(typical for this service)' or '(per piping class)' but ALWAYS include the actual value. Return pure JSON with all 26 fields filled. NO markdown, NO explanations, ONLY JSON."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.2,  # Increased for more creative inference when data missing
                    max_tokens=2500  # Increased to allow more detailed responses
                )
                logger.info(f"OpenAI API call successful for line {line_id}")
            except Exception as api_err:
                logger.error(f"OpenAI API call failed: {api_err}")
                logger.error(f"Error type: {type(api_err).__name__}")
                raise
            
            result_text = response.choices[0].message.content.strip()
            logger.info(f"OpenAI responded with {len(result_text)} chars for line {line_id}")
            logger.debug(f"Raw OpenAI response: {result_text[:500]}...")  # Log first 500 chars
            
            # Extract JSON if wrapped in markdown
            if '```json' in result_text:
                result_text = result_text.split('```json')[1].split('```')[0].strip()
            elif '```' in result_text:
                result_text = result_text.split('```')[1].split('```')[0].strip()
            
            ai_enrichment = json.loads(result_text)
            logger.info(f"Parsed {len(ai_enrichment)} fields from AI response")
            logger.debug(f"Parsed JSON keys: {list(ai_enrichment.keys())}")
            
            # LOCK: Merge AI results into empty structure (ensures all 26 columns exist)
            enrichment.update(ai_enrichment)
            
            # AGGRESSIVE FALLBACK: Fill empty fields with intelligent defaults
            filled_count = len([v for v in ai_enrichment.values() if v and v != "N/A" and v != ""])
            logger.info(f"AI filled {filled_count}/26 columns initially")
            
            if filled_count < 26:
                logger.info(f"Applying intelligent defaults for {26 - filled_count} empty fields...")
                enrichment = self._apply_intelligent_defaults(enrichment, line)
                new_filled = len([v for v in enrichment.values() if v and v != "N/A" and v != ""])
                logger.info(f"After defaults: {new_filled}/26 columns filled")
            
            return enrichment
            
        except Exception as e:
            logger.error(f"AI enrichment failed for line {line.get('original_detection')}: {e}", exc_info=True)
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

a"Ä» TARGET PIPING LINE (from P&ID):
öüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöü
Line Number: {line_id}
Fluid Code: {fluid_code}
Size: {size}
Area: {area}
PIPR Class: {pipr_class}
öüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöü

a"ôï YOUR TASK:
Search through the documents below and extract 26 specific values for this piping line.
READ CAREFULLY - the information is in tables, line lists, legends, and notes in these documents.

a"öì MANDATORY SEARCH & EXTRACTION STRATEGY:
öüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöü
Üá)"U%Å CRITICAL: You MUST fill all 26 fields. DO NOT leave fields empty unless absolutely no information exists.

1. EXACT MATCH: Search for line number "{line_id}" in all tables and line lists
2. FLUID MATCH: If line not found, search for fluid code "{fluid_code}" and use its data
3. CLASS MATCH: Use piping class "{pipr_class}" specifications from PMS tables
4. SIZE MATCH: Find size "{size}" in pipe schedules and extract wall thickness/schedule
5. INFERENCE: If exact value not found, use engineering logic:
   - Apply typical values from similar lines in same fluid service
   - Use general specifications for the piping class
   - Infer from related data (e.g., if design pressure is 150 psig, test pressure is ~225 psig)
6. UNITS: Always include units (e.g., "150 psig", "300,%%F", "Sch 40", "ASME B31.3")
7. YES/NO: For boolean fields, answer "Yes", "No", or "N/A" based on document info
8. STANDARDS: Apply typical industry standards when specifics aren't mentioned
öüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöü

"""
        
        if hmb_text:
            prompt += f"""
òÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉ
a"ôä DOCUMENT 1: HMB/PFD (Process Flow Diagram & Heat Material Balance)
òÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉ

a"Ä» MANDATORY EXTRACTION (Fill ALL 9 fields using smart inference):
   1. flow_medium: Fluid/chemical name. Search: line lists, stream tables, fluid codes. 
      If not found: Use fluid code "{fluid_code}" description or infer from service (e.g., CW=Cooling Water, ST=Steam)
   
   2. two_phase: Two-phase flow indicator. Search: line lists, process notes.
      If not found: Answer "No" for single-phase services (water, air), "Yes" for steam/condensate
   
   3. surge_flow: Peak/surge flow rate. Search: flow columns, max flow, surge conditions.
      If not found: Look for "Max Flow" or calculate from normal flow + 20% margin
   
   4. flow_max: Maximum flow rate. Search: flow rate columns, maximum capacity.
      If not found: Use surge flow or normal flow if available
   
   5. density: Fluid density. Search: density columns, fluid properties.
      If not found: Use standard values (Water=1000 kg/m,%%, Air=1.2 kg/m,%%, typical oils=850 kg/m,%%)
   
   6. normal_pressure: Operating pressure. Search: pressure columns, operating conditions.
      If not found: Look for design pressure and use ~80% of it
   
   7. normal_temp: Operating temperature. Search: temperature columns, operating conditions.
      If not found: Use ambient (70,%%F/21,%%C) for utilities, or infer from process type
   
   8. design_pressure: Maximum design pressure. Search: design columns, pressure ratings.
      If not found: Look for piping class pressure rating or use 1.25x normal pressure
   
   9. minimax_design_temp: Design temperature range. Search: min/max temp columns.
      If not found: Use typical ranges (e.g., "-20,%%F to 300,%%F" for general service)

a"öÄ SEARCH LOCATIONS:
   - Line lists (Line No, Fluid, Flow, Pressure, Temp, Density columns)
   - Heat & Material Balance tables
   - Process flow diagrams with operating conditions
   - Stream data tables
   - General notes and specifications
   - Fluid properties tables

DOCUMENT TEXT:
{hmb_text[:3500]}

òÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉ
"""
        
        if pms_text:
            prompt += f"""
òÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉ
a"ôä DOCUMENT 2: PMS (Piping Material Specification)
òÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉ

a"Ä» MANDATORY EXTRACTION (Fill ALL 10 fields using smart inference):
   10. design_code: Piping design code. Search: general notes, piping class tables.
       If not found: Use "ASME B31.3" (most common process piping standard)
   
   11. category_m_fluid: Category M fluid service. Search: fluid service classifications.
       If not found: Answer "No" for normal services, "Yes" for toxic/lethal fluids
   
   12. schedule_wall_thk: Pipe schedule. Search: piping class "{pipr_class}" table, size "{size}" row.
       If not found: Use "Sch 40" for sizes ëñ3", "Sch STD" for larger sizes
   
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

a"öÄ SEARCH LOCATIONS:
   - Piping class tables (Class "{pipr_class}", Size "{size}")
   - Pipe schedule tables
   - NDT requirements sections
   - PWHT and stress relief specifications
   - Material specification tables
   - General notes and standards

DOCUMENT TEXT:
{pms_text[:3500]}

òÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉ
"""
        
        if nace_text:
            prompt += f"""
òÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉ
a"ôä DOCUMENT 3: NACE (Corrosion Control & Testing Requirements)
òÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉ

a"Ä» MANDATORY EXTRACTION (Fill ALL 4 fields using smart inference):
   20. nace_mr_0175: NACE MR-0175 compliance. Search: NACE tables, H2S service, sour service.
       If not found: Answer "Not Required" for non-sour service, "Compliant" if H2S mentioned
   
   21. test_pressure: Hydrostatic test pressure. Search: test specifications, pressure test.
       If not found: Calculate as 1.5x design pressure (standard hydrostatic test ratio)
   
   22. test_medium: Test medium. Search: test procedure, test fluid specifications.
       If not found: Use "Water" (most common test medium for piping)
   
   23. criticality_code: Criticality classification. Search: criticality tables, line classifications.
       If not found: Use "C" for utilities, "B" for process lines, "A" for critical/hazardous

a"öÄ SEARCH LOCATIONS:
   - NACE compliance tables (Fluid "{fluid_code}")
   - H2S service requirements
   - Sour service specifications
   - Test pressure tables
   - Criticality classification tables
   - Testing procedures and requirements

DOCUMENT TEXT:
{nace_text[:3500]}

òÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉ
"""
        
        if pid_text:
            prompt += f"""
òÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉ
a"ôä DOCUMENT 4: P&ID (Piping & Instrumentation Diagram) - Title Block & Metadata
òÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉ

a"Ä» MANDATORY EXTRACTION (Fill ALL 3 fields using smart inference):
   24. pid_no: P&ID drawing number. Search: "DWG NO", "DRAWING NO", "P&ID NO", document number.
       If not found: Look for any alphanumeric ID in title block or headers
   
   25. pid_rev: P&ID revision. Search: "REV", "REVISION", revision column/field.
       If not found: Use "0" or "A" (initial revision)
   
   26. date: P&ID issue date. Search: "DATE", "ISSUE DATE", date fields in title block.
       If not found: Look for any date format (MM/DD/YYYY, DD-MMM-YYYYin document

a"öÄ SEARCH LOCATIONS:
   - Title block (usually bottom right or bottom center of drawing)
   - Drawing header and footer
   - Revision history tables
   - Document metadata fields

DOCUMENT TEXT (First page with title block):
{pid_text[:2000]}

òÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉ
"""
        else:
            prompt += f"""
òÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉ
a"ôä DOCUMENT 4: P&ID (Piping & Instrumentation Diagram) - Metadata
òÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉ

a"Ä» MANDATORY EXTRACTION (Fill ALL 3 fields using smart inference):
   24. pid_no: Use "PID-001" or similar generic number
   25. pid_rev: Use "0" (initial revision)
   26. date: Use current date or "Feb 2026"

Üá)"U%Å P&ID text not available - use generic values

òÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉòÉ
"""
        
        prompt += f"""

Üá)"U%Å OUTPUT FORMAT - RETURN EXACTLY THIS JSON WITH ALL FIELDS FILLED:
öüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöü
a"Ü¿ ALL 26 FIELDS ARE MANDATORY. FILL EVERY FIELD.

EXAMPLES OF GOOD VALUES:
- flow_medium: "Cooling Water", "Steam", "Crude Oil", "Natural Gas"
- two_phase: "Yes" or "No" (not empty)
- surge_flow: "150 GPM", "45 m,%%/h" (with units)
- design_pressure: "150 psig", "10 bara" (with units)
- design_code: "ASME B31.3", "ASME B31.1" (standard codes)
- schedule_wall_thk: "Sch 40", "Sch 80", "STD", "5.5mm"
- pwht: "Yes" or "No" (not empty)
- test_pressure: "225 psig", "1.5x Design" (calculated is OK)
- test_medium: "Water", "Air", "Nitrogen"
- pid_no: "PID-001", "12345", "Drawing 100-P-001"

öüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöüöü

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

a"ö$% DO NOT RETURN EMPTY STRINGS. FILL ALL FIELDS USING DOCUMENTS + INFERENCE.
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
    
    def _apply_intelligent_defaults(self, enrichment: Dict, line: Dict) -> Dict:
        """
        Apply intelligent defaults for empty enrichment fields
        Uses engineering standards and typical values to ensure ALL fields have data
        """
        fluid_code = line.get('fluid_code', '').upper()
        size = line.get('size', '')
        pipr_class = line.get('pipr_class', '')
        
        # Flow & Process Data
        if not enrichment.get('flow_medium'):
            enrichment['flow_medium'] = self._infer_flow_medium(fluid_code)
        if not enrichment.get('two_phase'):
            enrichment['two_phase'] = "Yes" if any(x in fluid_code for x in ['ST', 'STEAM', 'COND']) else "No"
        if not enrichment.get('surge_flow'):
            enrichment['surge_flow'] = "N/A"
        if not enrichment.get('flow_max'):
            enrichment['flow_max'] = "N/A"
        if not enrichment.get('density'):
            enrichment['density'] = self._infer_density(fluid_code)
        
        # Operating Conditions
        if not enrichment.get('normal_pressure'):
            enrichment['normal_pressure'] = "150 psig" if 'LP' in pipr_class else "300 psig"
        if not enrichment.get('normal_temp'):
            enrichment['normal_temp'] = "70,%%F" if any(x in fluid_code for x in ['CW', 'WATER', 'AIR']) else "300,%%F"
        if not enrichment.get('design_pressure'):
            enrichment['design_pressure'] = "225 psig" if 'LP' in pipr_class else "450 psig"
        if not enrichment.get('minimax_design_temp'):
            enrichment['minimax_design_temp'] = "-20,%%F to 300,%%F"
        
        # Design & Material Specs
        if not enrichment.get('design_code'):
            enrichment['design_code'] = "ASME B31.3"
        if not enrichment.get('category_m_fluid'):
            enrichment['category_m_fluid'] = "No"
        if not enrichment.get('schedule_wall_thk'):
            enrichment['schedule_wall_thk'] = self._infer_schedule(size)
        
        # Welding & Heat Treatment
        if not enrichment.get('stress_relief'):
            enrichment['stress_relief'] = "No"
        if not enrichment.get('pwht'):
            enrichment['pwht'] = "No"
        
        # NDT Requirements
        if not enrichment.get('rt'):
            enrichment['rt'] = "10%" if 'critical' not in pipr_class.lower() else "100%"
        if not enrichment.get('mt_pt'):
            enrichment['mt_pt'] = "Yes"
        if not enrichment.get('hardness'):
            enrichment['hardness'] = "HB 200 Max"
        if not enrichment.get('visual'):
            enrichment['visual'] = "Yes"
        if not enrichment.get('nace_mr_0175'):
            enrichment['nace_mr_0175'] = "Not Required"
        
        # Testing & Ratings
        if not enrichment.get('piping_rated_pressure'):
            enrichment['piping_rated_pressure'] = "150# ANSI" if 'LP' in pipr_class else "300# ANSI"
        if not enrichment.get('test_pressure'):
            enrichment['test_pressure'] = "340 psig" if 'LP' in pipr_class else "675 psig"
        if not enrichment.get('test_medium'):
            enrichment['test_medium'] = "Water"
        
        # Document References
        if not enrichment.get('pid_no'):
            enrichment['pid_no'] = "See P&ID"
        if not enrichment.get('pid_rev'):
            enrichment['pid_rev'] = "0"
        if not enrichment.get('date'):
            enrichment['date'] = "N/A"
        if not enrichment.get('criticality_code'):
            enrichment['criticality_code'] = "C"
        
        return enrichment
    
    def _infer_flow_medium(self, fluid_code: str) -> str:
        """Infer flow medium from fluid code"""
        mappings = {
            'CW': 'Cooling Water',
            'PW': 'Potable Water',
            'FW': 'Fire Water',
            'SW': 'Sea Water',
            'ST': 'Steam',
            'COND': 'Condensate',
            'AIR': 'Compressed Air',
            'IA': 'Instrument Air',
            'N2': 'Nitrogen',
            'FG': 'Fuel Gas',
            'NG': 'Natural Gas'
        }
        for code, medium in mappings.items():
            if code in fluid_code:
                return medium
        return "Process Fluid"
    
    def _infer_density(self, fluid_code: str) -> str:
        """Infer density from fluid code"""
        if any(x in fluid_code for x in ['WATER', 'CW', 'PW', 'FW', 'SW']):
            return "1000 kg/m,%%"
        elif any(x in fluid_code for x in ['AIR', 'IA', 'N2']):
            return "1.2 kg/m,%%"
        elif any(x in fluid_code for x in ['OIL', 'DIESEL', 'FUEL']):
            return "850 kg/m,%%"
        elif any(x in fluid_code for x in ['GAS', 'NG', 'FG']):
            return "0.8 kg/m,%%"
        return "N/A"
    
    def _infer_schedule(self, size: str) -> str:
        """Infer pipe schedule from size"""
        try:
            # Extract numeric size
            import re
            size_match = re.search(r'(\d+)', size)
            if size_match:
                size_num = int(size_match.group(1))
                if size_num <= 3:
                    return "Sch 40"
                elif size_num <= 8:
                    return "Sch STD"
                else:
                    return "Sch 20"
        except:
            pass
        return "Sch 40"


# Singleton instance
_enrichment_service = None

def get_enrichment_service() -> EnrichmentService:
    """Get or create enrichment service instance"""
    global _enrichment_service
    if _enrichment_service is None:
        _enrichment_service = EnrichmentService()
    return _enrichment_service
