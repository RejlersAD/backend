"""
P&ID Analysis Service - Multi-Pass Comprehensive Analysis
Architecture: OCR + Vision + Cross-Validation + Chain-of-Thought
"""
import os
import base64
import io
import json
import re
from typing import Dict, List, Any, Optional, Set, Tuple
from django.conf import settings
from openai import OpenAI
import fitz  # PyMuPDF
from PIL import Image


class PIDAnalysisService:
    """AI-Powered P&ID Analysis Service with Multi-Pass Validation"""

    def __init__(self):
        """Initialize OpenAI client with timeout"""
        api_key = (
            os.getenv('OPENAI_API_KEY') or
            getattr(settings, 'OPENAI_API_KEY', None)
        )
        
        if not api_key:
            raise ValueError("OPENAI_API_KEY not configured")
        
        # Initialize OpenAI client with default timeout
        self.client = OpenAI(
            api_key=api_key,
            timeout=180.0,  # 3 minute default timeout for all API calls
            max_retries=2   # Retry failed requests twice
        )
        self.extracted_text = ""
        self.instrument_tags = set()
        self.equipment_tags = set()
        self.line_numbers = set()
        self.notes_references = set()
        print('[INFO] Multi-Pass PID Analysis Service initialized with 180s timeout')

    def analyze_pid_drawing(self, pdf_file, drawing_number: Optional[str] = None) -> Dict[str, Any]:
        """
        Multi-Pass P&ID Analysis with OCR, Vision, and Cross-Validation
        
        PASS 1: OCR Text Extraction - Extract all text, tags, notes, line numbers
        PASS 2: Vision Analysis - Comprehensive visual inspection with chain-of-thought
        PASS 3: Cross-Validation - Verify consistency between text and visual findings
        PASS 4: Second Review - Re-analyze to catch missed issues
        
        Args:
            pdf_file: Django FieldFile or file path
            drawing_number: Optional drawing number for reference
            
        Returns:
            Dictionary with comprehensive analysis results
        """
        try:
            print(f"[INFO] ========== MULTI-PASS ANALYSIS START ==========")
            print(f"[INFO] Drawing: {drawing_number or 'Unknown'}")
            
            # PASS 1: OCR Text Extraction
            print(f"[INFO] PASS 1: OCR Text Extraction")
            images_base64 = self._pdf_to_base64_images(pdf_file)
            self._extract_text_from_pdf(pdf_file)
            self._parse_extracted_data()
            
            print(f"[INFO] Extracted {len(self.instrument_tags)} instrument tags")
            print(f"[INFO] Extracted {len(self.equipment_tags)} equipment tags")
            print(f"[INFO] Extracted {len(self.line_numbers)} line numbers")
            print(f"[INFO] Extracted {len(self.notes_references)} note references")
            
            # PASS 2: Vision Analysis with Chain-of-Thought
            print(f"[INFO] PASS 2: Vision Analysis (Chain-of-Thought)")
            try:
                vision_result = self._vision_analysis_pass(images_base64)
            except Exception as e:
                print(f"[ERROR] PASS 2 failed: {str(e)}")
                vision_result = {'issues': [], 'total_issues': 0, 'confidence': 'Low'}
            
            # PASS 3: Cross-Validation
            print(f"[INFO] PASS 3: Cross-Validation & Consistency Checks")
            try:
                consistency_issues = self._cross_validation_pass(vision_result)
            except Exception as e:
                print(f"[ERROR] PASS 3 failed: {str(e)}")
                consistency_issues = []
            
            # PASS 4: Second Review Pass (if insufficient issues found)
            second_pass_issues = []
            issues_found = vision_result.get('total_issues', 0)
            if issues_found < 20:  # Target minimum 20 issues
                print(f"[INFO] PASS 4: Second Review Pass (Only {issues_found} issues found, need minimum 20)")
                try:
                    second_pass_issues = self._second_review_pass(images_base64, vision_result, consistency_issues)
                except Exception as e:
                    print(f"[WARNING] PASS 4 failed (non-critical): {str(e)}")
                    second_pass_issues = []
            else:
                print(f"[INFO] PASS 4: Skipped ({issues_found} issues already found)")

            
            # Merge all findings
            all_issues = self._merge_and_deduplicate(
                vision_result.get('issues', []),
                consistency_issues,
                second_pass_issues
            )
            
            # If NO issues found at all, create at least one from OCR data
            if len(all_issues) == 0:
                print("[WARNING] No issues found in any pass - creating summary observation")
                all_issues = [{
                    'serial_number': 1,
                    'pid_reference': 'DRAWING ANALYSIS',
                    'issue_observed': f'Automated analysis completed. Found {len(self.instrument_tags)} instruments, {len(self.equipment_tags)} equipment tags, {len(self.line_numbers)} line numbers. Manual review recommended.',
                    'action_required': 'Perform detailed manual review of the P&ID drawing for completeness and compliance',
                    'severity': 'observation',
                    'category': 'documentation',
                    'location_on_drawing': {
                        'zone': 'Middle-Center',
                        'drawing_section': 'Overall Drawing',
                        'proximity_description': 'Entire drawing scope',
                        'visual_cues': 'Complete drawing review'
                    }
                }]
            
            # Categorize by severity
            categorized = self._categorize_by_severity(all_issues)
            
            final_result = {
                'issues': all_issues,
                'critical_issues': categorized['critical'],
                'major_observations': categorized['major'],
                'minor_observations': categorized['minor'],
                'total_issues': len(all_issues),
                'critical_count': len(categorized['critical']),
                'major_count': len(categorized['major']),
                'minor_count': len(categorized['minor']),
                'confidence': 'High' if len(all_issues) >= 15 else 'Medium',
                'analysis_metadata': {
                    'extracted_text_length': len(self.extracted_text),
                    'instrument_tags_found': len(self.instrument_tags),
                    'equipment_tags_found': len(self.equipment_tags),
                    'line_numbers_found': len(self.line_numbers),
                    'analysis_passes': 4,
                    'multi_pass_enabled': True
                }
            }
            
            print(f"[INFO] ========== ANALYSIS COMPLETE ==========")
            print(f"[INFO] Total Issues: {len(all_issues)}")
            print(f"[INFO] Critical: {len(categorized['critical'])}, Major: {len(categorized['major'])}, Minor: {len(categorized['minor'])}")
            
            return final_result
            
        except Exception as e:
            print(f"[ERROR] Analysis failed: {str(e)}")
            import traceback
            traceback.print_exc()
            raise

    def _extract_text_from_pdf(self, pdf_file):
        """Extract all text from PDF using OCR"""
        try:
            # Soft-coded approach: Handle both file paths and file objects (S3/Django FileField)
            if isinstance(pdf_file, str):
                # Local file path
                doc = fitz.open(pdf_file)
            else:
                # File object (from S3 or Django FileField) - read content into memory
                pdf_file.seek(0)  # Ensure we're at the start
                pdf_bytes = pdf_file.read()
                doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            
            text_parts = []
            
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                text = page.get_text()
                text_parts.append(text)
            
            doc.close()
            self.extracted_text = "\n".join(text_parts)
            
        except Exception as e:
            print(f"[WARNING] OCR extraction failed: {str(e)}")
            self.extracted_text = ""
    
    def _parse_extracted_data(self):
        """Parse extracted text to identify tags, line numbers, notes with smart filtering"""
        if not self.extracted_text:
            return
        
        # Valid instrument prefixes (ISA-5.1 standard)
        valid_instrument_prefixes = {
            'FI', 'FIC', 'FIT', 'FE', 'FT', 'FV', 'FY', 'FCV', 'FICV',
            'LI', 'LIC', 'LIT', 'LE', 'LT', 'LV', 'LY', 'LCV', 'LSH', 'LSL', 'LSHH', 'LSLL', 'LAH', 'LAL',
            'PI', 'PIC', 'PIT', 'PE', 'PT', 'PV', 'PY', 'PCV', 'PSH', 'PSL', 'PSHH', 'PSLL', 'PAH', 'PAL', 'PDI', 'PDIC', 'PDIT',
            'TI', 'TIC', 'TIT', 'TE', 'TT', 'TV', 'TY', 'TCV', 'TSH', 'TSL', 'TSHH', 'TSLL', 'TAH', 'TAL',
            'AI', 'AIC', 'AIT', 'AE', 'AT', 'AV', 'AY', 'ACV', 'ASH', 'ASL',
            'PSV', 'PRV', 'PDSV', 'TSV', 'ESV', 'XV', 'SDV', 'BDV', 'MOV', 'SOV',
            'ZS', 'ZSH', 'ZSL', 'ZI', 'ZIC', 'ZIT',
            'HS', 'HV', 'HY', 'HL', 'HIC',
            'SI', 'SC', 'SE', 'SS', 'SV',
            'WI', 'WIC', 'WIT', 'WE', 'WT',
            'VI', 'VIC', 'VIT', 'VT',
            'EI', 'EIC', 'EY',
            'UI', 'UIC', 'UY'
        }
        
        # Line number prefixes to exclude from instrument tags (common piping service codes)
        line_number_prefixes = {
            'HD', 'HU', 'CD', 'CU', 'LF', 'FL', 'SY', 'NG', 'FG', 'FW', 'BW', 
            'CW', 'SW', 'DW', 'PW', 'HP', 'MP', 'LP', 'IA', 'NA', 'PA',
            'HC', 'HO', 'CO', 'ST', 'CS', 'DS', 'SS', 'RW', 'WW', 'GN',
            'N2', 'O2', 'CO2', 'H2', 'AR', 'HE'
        }
        
        # Extract potential instrument tags
        instrument_pattern = r'\b([A-Z]{2,4}[ICSVT]?[-_][\d]{1,4}(?:[-_][\d]{1,2}[A-Z]?)?)\b'
        potential_instruments = set(re.findall(instrument_pattern, self.extracted_text))
        
        # Filter instrument tags: keep only valid ISA prefixes and exclude line number prefixes
        self.instrument_tags = set()
        for tag in potential_instruments:
            prefix = tag.split('-')[0].upper()
            # Check if prefix matches valid instrument tag or starts with valid prefix
            is_valid_instrument = any(
                prefix == valid_prefix or prefix.startswith(valid_prefix) 
                for valid_prefix in valid_instrument_prefixes
            )
            # Exclude if it's a line number prefix
            is_line_number = prefix in line_number_prefixes
            
            if is_valid_instrument and not is_line_number:
                self.instrument_tags.add(tag)
        
        # Equipment tag patterns: V-3610-01, E-101, K-102, etc. (exclude single letter + small numbers that are likely P&ID refs)
        equipment_pattern = r'\b([VEKPCHMXDTRS][-_][\d]{3,4}(?:[-_][\d]{1,2}[A-Z]?)?)\b'
        potential_equipment = set(re.findall(equipment_pattern, self.extracted_text))
        
        # Filter equipment tags: exclude P&ID reference patterns (e.g., D-101, D-161 with numbers < 200 often P&ID numbers)
        self.equipment_tags = set()
        for tag in potential_equipment:
            parts = tag.split('-')
            if len(parts) >= 2:
                prefix = parts[0]
                number = parts[1]
                # Exclude D-XXX patterns where XXX < 200 (likely P&ID numbers, not equipment)
                if prefix == 'D' and number.isdigit() and int(number) < 200:
                    continue  # Skip likely P&ID reference
                # Exclude P-XXX patterns where XXX < 400 (likely line numbers or P&ID refs)
                if prefix == 'P' and number.isdigit() and int(number) < 400:
                    continue  # Skip likely line/P&ID reference
                self.equipment_tags.add(tag)
        
        # Line number patterns: 6"-N2-1001-C4N, 3"-HC-2003, etc.
        line_pattern = r'\b([\d]+"?[-][A-Z]{1,4}[-][\d]{3,4}(?:[-][A-Z\d]+)?)\b'
        self.line_numbers = set(re.findall(line_pattern, self.extracted_text))
        
        # Note references: NOTE 1, NOTE 2, HOLD 1, etc.
        note_pattern = r'\b((?:NOTE|HOLD|REF)[\s]*[\d]+)\b'
        self.notes_references = set(re.findall(note_pattern, self.extracted_text, re.IGNORECASE))
    
    def _vision_analysis_pass(self, images_base64: List[str]) -> Dict[str, Any]:
        """PASS 2: Vision-based analysis with chain-of-thought"""
    def _vision_analysis_pass(self, images_base64: List[str]) -> Dict[str, Any]:
        """PASS 2: Vision-based analysis with chain-of-thought"""
        try:
            messages = [
                {
                    "role": "system",
                    "content": """You are a Senior P&ID Verification Engineer performing HAZOP-level review.

**CRITICAL INSTRUCTION - READ CAREFULLY:**
🚨 DO NOT STOP AFTER FINDING ONE OR TWO ISSUES 🚨

You MUST perform an EXHAUSTIVE analysis and identify MINIMUM 20-30 findings.
This is a comprehensive engineering review, not a quick scan.

**MANDATORY CHAIN-OF-THOUGHT PROCESS:**
Before listing issues, you MUST think through:
1. "What instruments do I see? Are all properly specified?"
2. "What equipment exists? Is each tagged and specified?"
3. "What are all the line numbers? Do they all have source/destination?"
4. "What control loops exist? Are they complete?"
5. "What safety devices exist? Are they properly configured?"
6. "What notes/holds are referenced? Are they applied?"
7. "Does the legend match all symbols used?"
8. "Are there any inconsistencies or missing data?"
9. "Are pipe classes consistent between equipment nozzles and connected piping?"
10. "Are dissimilar material connections properly identified with insulating gaskets?"
11. "Do Restriction Orifices (RO) and LTCS have minimum spool lengths?"
12. "Are free draining slopes and low point drains provided?"
13. "Do PSV set pressures comply with equipment design pressures?"

**REQUIRED VERIFICATION CHECKLIST - CHECK EVERY ITEM:**

✅ INSTRUMENTS (Check ALL visible instruments)
   - Tag format correct? (TI, TIC, FIC, PSV, LIC, etc.)
   - Measurement range specified?
   - Alarm setpoints (HH, H, L, LL) present and logical?
   - Trip setpoints for safety instruments?
   - Fail-safe position (FC, FO, FL) specified for control valves?
   - Signal type indicated (4-20mA, digital, etc.)?
   - Connected to correct equipment/line?
   - Location accessible for maintenance?

✅ EQUIPMENT (Check ALL vessels, pumps, compressors, exchangers)
   - Tag number visible and correct format?
   - Equipment type clearly identified?
   - Design pressure/temperature specified?
   - Material of construction noted?
   - Nozzle schedule complete?
   - Capacity/size specified?
   - Datasheet reference present?

✅ PIPING & LINES (Check EVERY line)
   - Line number complete and valid format?
   - Line size specified?
   - Line specification/class noted?
   - Source identified (equipment, other line)?
   - Destination identified (equipment, header, flare)?
   - Isolation valves present?
   - Drain points where needed?
   - Vent points at high elevations?
   - Slope indicated if required?
   - Reducers/expanders marked with sizes?

✅ VALVES (Check ALL valves)
   - Valve type appropriate for service?
   - Valve size matches line size?
   - Actuator type specified (manual, pneumatic, motor)?
   - Fail position for automated valves?
   - Check valve orientation correct?
   - Block valves for isolation?
   - Bypass valves where needed?
   - Three-way valves configured correctly?

✅ SAFETY SYSTEMS
   - PSV: Set pressure specified?
   - PSV: Discharge routed properly?
   - PSV: Sized for duty?
   - PSV: Set pressure vs Equipment Design Pressure compliance (Must be ≤ Design Pressure)?
   - Rupture disks: Burst pressure noted?
   - Flame arrestors: Type and location correct?
   - ESD valves: Fail position correct?
   - Fire & Gas detectors: Coverage adequate?
   - Emergency relief: Path to safe location?

✅ PIPE CLASS & TRIM CLASS CONSISTENCY
   - Equipment nozzle class matches connected piping class?
   - Valve trim class compatible with line specification?
   - Pressure-temperature rating consistency maintained?
   - Material compatibility between equipment and piping?
   - Flange rating matches line pressure class?
   - Gasket material suitable for service conditions?

✅ DISSIMILAR MATERIALS & INSULATING GASKETS
   - Dissimilar metal connections identified (e.g., carbon steel to stainless steel)?
   - Insulating gaskets specified where dissimilar materials meet?
   - Insulating kit complete (gasket, sleeves, washers)?
   - Galvanic corrosion prevention measures noted?
   - Material transition points clearly marked?
   - Electrical isolation requirements met?

✅ MINIMUM SPOOL LENGTH COMPLIANCE
   - Minimum spool length downstream of Restriction Orifice (RO) met?
   - RO to first fitting: Minimum 5D (5 × pipe diameter) straight run?
   - Low Temperature Cut-off Switch (LTCS) installation clearance adequate?
   - Straight run requirements for flow measurement devices satisfied?
   - Instrument tapping locations comply with minimum distances?
   - Upstream/downstream piping interference checked?

✅ FREE DRAINING & SLOPE REQUIREMENTS
   - All horizontal lines have proper drainage slope (typically 1:100 or 1:50)?
   - Low point drains provided at collection points?
   - High point vents provided at elevation changes?
   - Dead legs eliminated or minimized?
   - Pocketing prevented in piping layout?
   - Drainage direction indicated on drawing?
   - Drain valve sizing adequate for service?
   - Winterization provisions noted for outdoor lines?

✅ CONTROL LOOPS
   - Controller output goes to correct valve?
   - Measurement source identified?
   - Control valve has fail-safe specified?
   - Cascade loops properly connected?
   - Split-range valves configured correctly?
   - Override logic documented?
   - Interlock conditions clear?

✅ NOTES & DOCUMENTATION
   - All referenced notes actually present?
   - HOLD items identified and tracked?
   - Notes apply to correct equipment/lines?
   - Conflicting information in notes?
   - Missing clarifications needed?
   - **HOLDS COMPLIANCE**:
     * Each HOLD requirement verified on drawing?
     * Any equipment/instrument violating HOLD requirements?
     * HOLD-specified items clearly marked?
   - **NOTES COMPLIANCE**:
     * Design pressure/temp per notes followed?
     * Material specs per notes implemented?
     * Safety requirements per notes met?
     * Operating constraints per notes observed?
   - **MISSING REQUIREMENTS**:
     * Items specified in HOLD/NOTE but not shown on drawing?
     * Violations of mandatory HOLD/NOTE requirements?

✅ LEGEND & SYMBOLS
   - All symbols used are in legend?
   - Legend items actually used on drawing?
   - Symbol usage consistent throughout?
   - Abbreviations defined?

**OUTPUT FORMAT - STRICT JSON:**
{
    "reasoning": "Chain-of-thought: First I see X instruments, Y equipment, Z lines. I will check each systematically...",
    "issues": [
        {
            "serial_number": 1,
            "pid_reference": "Exact tag/line from drawing",
            "issue_observed": "Specific detailed issue with exact values",
            "action_required": "Clear corrective action",
            "severity": "critical/major/minor/observation",
            "category": "instrument/equipment/piping/valve/safety/control_loop/documentation/legend/pipe_class/dissimilar_materials/spool_length/drainage/psv_compliance/holds_compliance/notes_compliance",
            "location_on_drawing": {
                "zone": "Top-Left/Top-Center/Top-Right/Middle-Left/Middle-Center/Middle-Right/Bottom-Left/Bottom-Center/Bottom-Right",
                "drawing_section": "Process area/utility/legend/notes",
                "proximity_description": "Near equipment X, between lines Y and Z",
                "visual_cues": "Upper left, center section, etc."
            }
        }
    ],
    "total_issues": 0,
    "confidence": "High/Medium/Low"
}

**QUALITY STANDARDS:**
- MINIMUM 20-30 findings required
- Each finding must reference SPECIFIC tag/line/equipment
- Include EXACT values (pressures, temps, setpoints, sizes)
- Provide ACTIONABLE recommendations
- Use PROPER engineering terminology
- DO NOT summarize - be thorough
- DO NOT skip categories - check all
- THINK like you're preparing for HAZOP review
- CHECK pipe class consistency at equipment nozzles
- VERIFY dissimilar material connections have insulating gaskets
- CONFIRM minimum spool lengths per industry standards
- VALIDATE drainage provisions on all horizontal lines
- ENSURE PSV set pressures comply with equipment ratings
- **EXTRACT and VERIFY ALL HOLDS** (flag violations as CRITICAL)
- **EXTRACT and VERIFY ALL NOTES** (flag non-compliance as CRITICAL/MAJOR)
- **REFERENCE HOLD/NOTE numbers** in issues when applicable
- **CREATE SEPARATE ISSUES** for each missing HOLD/NOTE requirement
- **FORMAT**: "HOLD-X NOT IMPLEMENTED: [specific missing element]" or "NOTE-Y NON-COMPLIANT: [specific violation]"
"""
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"""🚨 CRITICAL INSTRUCTION: EXHAUSTIVE P&ID VERIFICATION 🚨

YOU MUST FIND AT LEAST 20-30 ISSUES PER DRAWING. BE THOROUGH AND METICULOUS!

⚠️ DO NOT SUBMIT RESPONSE WITH LESS THAN 15 ISSUES - THIS IS A COMPREHENSIVE AUDIT ⚠️

**YOUR SYSTEMATIC APPROACH:**

STEP 1️⃣: COUNT EVERYTHING (Document counts for verification)
- Count EVERY instrument tag visible (target: find issues with at least 30% of them)
- Count EVERY equipment tag (vessels, pumps, compressors, etc.)
- Count EVERY line number
- Count EVERY valve (control valves, block valves, check valves, safety valves)
- Count EVERY safety device (PSVs, rupture discs, flame arrestors)
- Count EVERY control loop
- Count ALL piping segments and connections

STEP 2️⃣: EXTRACT HOLDS & NOTES (Mandatory verification)
- Extract ALL HOLDS from table (usually top-right)
- Extract ALL NOTES from notes section
- For EACH HOLD: Check if implemented on drawing → If NOT, create issue
- For EACH NOTE: Check if layout matches requirement → If NOT, create issue

STEP 3️⃣: SYSTEMATIC VERIFICATION (Check EVERY category below)

- Identify horizontal piping runs (check drainage slopes)
- List all PSVs with their set pressures and protected equipment
- **Extract ALL HOLDS** from top-right corner table (if present)
- **Extract ALL NOTES** from notes section (numbered notes)

Then systematically verify EACH ONE against the checklist.

**CRITICAL: HOLDS & NOTES COMPLIANCE VERIFICATION**

🚨 MANDATORY HOLDS & NOTES ANALYSIS - SMART COMPARISON TECHNIQUE 🚨

**STEP 1: EXTRACT ALL HOLDS**
- Look for HOLDS table (typically TOP RIGHT corner)
- For EACH HOLD, extract:
  * HOLD number (e.g., HOLD-1, H1, HOLD 1)
  * EXACT text of requirement
- Create list of ALL HOLDS found

**STEP 2: EXTRACT ALL NOTES**  
- Look for NOTES section (usually bottom of drawing)
- For EACH NOTE, extract:
  * NOTE number (e.g., NOTE 1, NOTE 2)
  * EXACT text of note
  * Type: Design condition/Material/Safety/Operating requirement
- Create list of ALL NOTES found

**STEP 3: COMPARE EACH HOLD AGAINST LAYOUT**
For EACH HOLD extracted, ask:
- "Is this HOLD requirement VISIBLE on the drawing?"
- "Is there equipment/instrument/line that IMPLEMENTS this HOLD?"
- "Does any item VIOLATE this HOLD?"

**Examples:**
- HOLD: "All PSVs discharge to flare" → Check: Do ALL PSVs show discharge to flare? If NO → Flag as MISSING
- HOLD: "2oo3 voting for level transmitters" → Check: Are 3 transmitters shown? If NO → Flag as MISSING
- HOLD: "Min 5D straight pipe after orifice" → Check: Is spacing correct? If NO → Flag as VIOLATION

**STEP 4: COMPARE EACH NOTE AGAINST LAYOUT**
For EACH NOTE extracted, ask:
- "Does the design follow this NOTE requirement?"
- "Are specifications matching this NOTE?"
- "Is anything on drawing CONTRADICTING this NOTE?"

**Examples:**
- NOTE: "Design pressure 50 barg @ 150°C" → Check: Do ALL equipment specs show 50 barg? If NO → Flag as NON-COMPLIANT
- NOTE: "CS piping needs NACE MR0175" → Check: Is NACE material noted? If NO → Flag as MISSING
- NOTE: "All control valves fail-closed" → Check: Do valves show FC? If NO → Flag as MISSING

**STEP 5: CREATE SEPARATE ISSUES FOR MISSING IMPLEMENTATIONS**
For EACH HOLD/NOTE that is NOT implemented:
```json
{{
  "serial_number": X,
  "pid_reference": "HOLD-1 / NOTE 3",
  "issue_observed": "HOLD-1 requires 'All PSVs discharge to flare header' but PSV-101 shows discharge to atmosphere. HOLD requirement NOT IMPLEMENTED.",
  "action_required": "Route PSV-101 discharge to flare header as per HOLD-1 requirement. Update P&ID to show flare header connection.",
  "severity": "critical",
  "category": "holds_compliance"
}}
```

**STEP 6: REPORT FORMAT**
Create issues in THREE categories:
1. **MISSING HOLDS** - HOLD requirements NOT visible on drawing
2. **MISSING NOTES** - NOTE requirements NOT implemented in design  
3. **VIOLATIONS** - Design contradicts HOLD/NOTE requirement

**EXTRACTED TEXT DATA (Use for cross-validation):**
   - Operating constraints

3. **VERIFY COMPLIANCE** - For EVERY item on drawing:
   - Check if any HOLD applies to equipment/instrument/line
   - Check if any NOTE constrains the design
   - Flag VIOLATIONS as CRITICAL issues
   - Reference specific HOLD/NOTE number in issue description

4. **MANDATORY OUTPUT - LIST MISSING REQUIREMENTS**:
   After analyzing the drawing, you MUST create issues for:
   - Any HOLD requirement that is NOT implemented → Flag as "MISSING HOLD IMPLEMENTATION"
   - Any NOTE requirement that is NOT followed → Flag as "NOTE NON-COMPLIANCE"
   - Any design element that VIOLATES a HOLD/NOTE → Flag as "HOLD/NOTE VIOLATION"

5. **SMART COMPARISON EXAMPLES**:
   HOLD: "All PSVs discharge to flare" 
   → AI checks: PSV-101 → ❌ discharges to atmosphere → CREATE ISSUE: "HOLD-1 NOT IMPLEMENTED for PSV-101"
   
   NOTE: "Design pressure 50 barg @ 150°C"
   → AI checks: V-2001 datasheet → ❌ shows 45 barg → CREATE ISSUE: "NOTE 3 NON-COMPLIANT: Vessel V-2001 rated 45 barg, requires 50 barg"
   
   NOTE: "CS piping needs NACE MR0175"
   → AI checks: Line 6"-HC-1001 → ❌ no NACE marking → CREATE ISSUE: "NOTE 5 MISSING: Line 6\"-HC-1001 material spec doesn't show NACE MR0175"

6. **REPORT EACH HOLD/NOTE SEPARATELY**:
   - If 5 HOLDS exist → Check all 5 and report status of EACH
   - If 10 NOTES exist → Check all 10 and report status of EACH
   - Create individual issues for EACH missing/violated requirement

**EXTRACTED TEXT DATA (Use for cross-validation):**
Instrument Tags Found: {', '.join(list(self.instrument_tags)[:20]) if self.instrument_tags else 'None'}
Equipment Tags Found: {', '.join(list(self.equipment_tags)[:20]) if self.equipment_tags else 'None'}
Line Numbers Found: {', '.join(list(self.line_numbers)[:20]) if self.line_numbers else 'None'}

**YOUR MANDATORY TASKS:**
1. Verify EVERY instrument has range, alarms, fail-safe
2. Verify EVERY equipment has tag, spec, pressure/temp rating
3. Verify EVERY line has source, destination, size, spec
4. Verify EVERY control valve has controller and fail position
5. Verify EVERY safety device has setpoint and discharge path
6. **EXTRACT ALL HOLDS** (create complete list)
7. **For EACH HOLD → Compare against layout → Flag if MISSING or VIOLATED**
8. **EXTRACT ALL NOTES** (create complete list)
9. **For EACH NOTE → Compare against design → Flag if NOT IMPLEMENTED**
10. Check ALL symbols are in legend
11. Find ANY inconsistencies between text and diagram
12. Verify pipe class/trim class consistency at ALL equipment connections
13. Check for dissimilar materials and insulating gasket requirements
14. Verify minimum spool lengths downstream of ROs and LTCS installations
15. Check drainage slopes and low point drains on horizontal piping
16. Validate PSV set pressures are ≤ equipment design pressures
17. **Create SEPARATE issue for EACH missing HOLD/NOTE implementation**

🔴 FINAL REMINDER BEFORE SUBMITTING: 🔴
- Count your issues: You should have AT LEAST 15-20 issues minimum
- If you have less than 15 issues, GO BACK and review the drawing more carefully
- Look for missing information, inconsistencies, unclear labeling, incomplete data
- Check EVERY instrument for missing ranges, alarms, fail-safe positions
- Verify EVERY equipment has complete specifications
- Most P&ID drawings have 20-40 issues - aim for comprehensive coverage

📊 QUALITY CHECK YOUR RESPONSE:
✅ Have I checked ALL instruments? (should find issues with 20-30% of them)
✅ Have I checked ALL equipment? (specs, ratings, connections)
✅ Have I compared ALL HOLDS against the drawing?
✅ Have I compared ALL NOTES against the design?
✅ Have I checked pipe class consistency at equipment boundaries?
✅ Have I verified control loop completeness?
✅ Have I checked drainage slopes on horizontal piping?
✅ Have I verified PSV discharge routing?
✅ Do I have AT LEAST 15-20 issues total?

Return ONLY valid JSON. NO other text."""
                        }
                    ] + [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{img}",
                                "detail": "high"
                            }
                        }
                        for img in images_base64
                    ]
                }
            ]
            
            print("[INFO] Calling OpenAI Vision API (Pass 2: Chain-of-Thought)...")
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                max_tokens=16384,  # Maximum for comprehensive 40+ issue reports
                temperature=0.3,  # Lower for more consistent, thorough analysis
                timeout=600  # 10 minute timeout for comprehensive analysis
            )
            
            # Safely extract response
            if not response or not response.choices:
                print("[ERROR] OpenAI returned empty response")
                return {'issues': [], 'total_issues': 0, 'confidence': 'Low'}
            
            response_text = response.choices[0].message.content
            if not response_text:
                print("[ERROR] OpenAI response content is None")
                return {'issues': [], 'total_issues': 0, 'confidence': 'Low'}
            
            response_text = response_text.strip()
            tokens_used = response.usage.total_tokens if response.usage else 0
            
            print(f"[INFO] Vision analysis complete. Tokens: {tokens_used}")
            
            return self._parse_analysis_response(response_text, tokens_used)
            
        except Exception as e:
            print(f"[ERROR] Vision analysis failed: {str(e)}")
            return {'issues': [], 'total_issues': 0, 'confidence': 'Low'}
    
    def _cross_validation_pass(self, vision_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """PASS 3: Cross-validate OCR data with vision findings - Smart filtering to reduce false positives"""
        consistency_issues = []
        serial_offset = vision_result.get('total_issues', 0)
        
        # Check 1: Instruments mentioned in text but not found in vision analysis
        # Smart filtering: Only report if significant number AND likely real instruments
        vision_tags = set()
        for issue in vision_result.get('issues', []):
            ref = issue.get('pid_reference', '')
            vision_tags.add(ref)
        
        missing_in_vision = self.instrument_tags - vision_tags
        
        # Additional intelligence: Filter out tags that might be in connected OPCs or other drawings
        # Only report if > 10 missing (indicating systematic OCR issue) OR if critical safety instruments
        critical_prefixes = {'PSV', 'ESV', 'SDV', 'PSHH', 'LSHH', 'TSHH'}
        critical_missing = [tag for tag in missing_in_vision if any(tag.startswith(prefix) for prefix in critical_prefixes)]
        
        # Report critical instruments individually, others only if many missing
        if critical_missing:
            for idx, tag in enumerate(critical_missing[:5], 1):
                consistency_issues.append({
                    'serial_number': serial_offset + idx,
                    'pid_reference': tag,
                    'issue_observed': f'Critical safety instrument tag {tag} found in text but not visually verified. This may be referenced from connected OPC/drawing or missing symbol.',
                    'action_required': 'Verify if instrument exists on this drawing or is referenced from connected system. Confirm safety critical instrument is properly documented.',
                    'severity': 'major',
                    'category': 'instrument',
                    'location_on_drawing': {
                        'zone': 'Unknown',
                        'drawing_section': 'Check connected OPC or text references',
                        'proximity_description': 'Tag found in extracted text',
                        'visual_cues': 'May be in logic diagram or connected P&ID'
                    }
                })
        elif len(missing_in_vision) > 15:  # Only report if many tags missing (indicating OCR limitation)
            consistency_issues.append({
                'serial_number': serial_offset + len(consistency_issues) + 1,
                'pid_reference': f"Multiple tags: {', '.join(list(missing_in_vision)[:5])}... ({len(missing_in_vision)} total)",
                'issue_observed': f'Found {len(missing_in_vision)} instrument tags in text not verified visually. These may be: (1) References to instruments in connected OPCs/drawings, (2) OCR artifacts, or (3) Instruments with symbol recognition limitations.',
                'action_required': 'Review if these tags are intentional references to connected systems. If they should be on this drawing, verify symbols are present and correct.',
                'severity': 'observation',
                'category': 'instrument',
                'location_on_drawing': {
                    'zone': 'Multiple',
                    'drawing_section': 'Text references or connected systems',
                    'proximity_description': 'Tags found in text extraction',
                    'visual_cues': 'Check notes section and connected OPC references'
                }
            })
        
        # Check 2: Equipment tags consistency - Smart filtering
        missing_equipment = self.equipment_tags - vision_tags
        
        # Only report if significant (> 5) and not likely P&ID references
        if len(missing_equipment) > 5:
            consistency_issues.append({
                'serial_number': serial_offset + len(consistency_issues) + 1,
                'pid_reference': f"Equipment: {', '.join(list(missing_equipment)[:5])}... ({len(missing_equipment)} total)",
                'issue_observed': f'Found {len(missing_equipment)} equipment tags in text not verified visually. These may be: (1) Equipment in connected systems/drawings, (2) Legend references, or (3) OCR artifacts.',
                'action_required': 'Review if these are intentional cross-references. Verify critical equipment is properly shown with symbols and datasheets.',
                'severity': 'observation',
                'category': 'equipment',
                'location_on_drawing': {
                    'zone': 'Multiple',
                    'drawing_section': 'Check legend and connected drawings',
                    'proximity_description': 'Tags found in text',
                    'visual_cues': 'May be in notes, legend, or P&ID reference section'
                }
            })
        
        # Check 3: Notes and Holds validation - Keep as observation only
        if len(self.notes_references) > 0:
            consistency_issues.append({
                'serial_number': serial_offset + len(consistency_issues) + 1,
                'pid_reference': f"NOTES: {', '.join(list(self.notes_references)[:5])}",
                'issue_observed': f'Found {len(self.notes_references)} note/hold references. Verify all notes are applicable and properly implemented in the design.',
                'action_required': 'Cross-check each note/hold requirement is addressed in equipment specs, line specs, and instrumentation.',
                'severity': 'observation',
                'category': 'documentation',
                'location_on_drawing': {
                    'zone': 'Bottom-Right',
                    'drawing_section': 'Notes Section',
                    'proximity_description': 'Drawing notes area',
                    'visual_cues': 'Check notes section for all references'
                }
            })
        
        print(f"[INFO] Cross-validation found {len(consistency_issues)} consistency observations (smart filtered)")
        return consistency_issues
    
    def _second_review_pass(self, images_base64: List[str], first_pass: Dict, consistency: List) -> List[Dict[str, Any]]:
        """PASS 4: Second review to catch missed issues"""
        try:
            first_pass_issues = [f"{i.get('pid_reference')}: {i.get('issue_observed')[:50]}" 
                                for i in first_pass.get('issues', [])[:10]]
            
            messages = [
                {
                    "role": "system",
                    "content": """You are performing a SECOND REVIEW pass on a P&ID drawing.

🔍 **CRITICAL MISSION: Find what was MISSED in the first analysis** 🔍

**WHAT TO LOOK FOR:**
- Issues that were overlooked in first pass
- Additional details on equipment not fully analyzed
- Lines/valves that weren't examined
- Safety devices not mentioned
- Control loops not validated
- Instruments without complete data
- Any contradictions or conflicts

**FOCUS AREAS:**
1. Items mentioned in OCR but not in first pass
2. Equipment visible but not fully analyzed
3. Missing cross-references
4. Incomplete data on previously identified items
5. Any safety-critical elements

**OUTPUT FORMAT - JSON ONLY:**
{
    "issues": [
        {
            "serial_number": 1,
            "pid_reference": "Tag/Line/Equipment",
            "issue_observed": "What was missed",
            "action_required": "What to do",
            "severity": "critical/major/minor/observation",
            "category": "instrument/equipment/piping/valve/safety/documentation",
            "location_on_drawing": {
                "zone": "Zone",
                "drawing_section": "Section",
                "proximity_description": "Near X",
                "visual_cues": "Visual location"
            }
        }
    ],
    "total_issues": 0
}"""
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"""Perform SECOND REVIEW PASS to find MISSED issues.

**FIRST PASS FOUND:**
{chr(10).join(first_pass_issues)}

**CONSISTENCY CHECK FOUND:**
- {len(consistency)} additional issues from text/visual cross-validation

**YOUR MISSION:**
Find issues that were MISSED. Look for:
- Any instruments NOT mentioned in first pass
- Any equipment NOT fully analyzed
- Any lines/valves NOT examined
- Any safety devices NOT validated
- Any incomplete specifications

Focus on catching what was overlooked. Target: 5-10 additional findings.
Return ONLY JSON."""
                        }
                    ] + [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{img}",
                                "detail": "high"
                            }
                        }
                        for img in images_base64
                    ]
                }
            ]
            
            print("[INFO] Calling OpenAI for second review pass...")
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                max_tokens=8000,
                temperature=0.5,  # Higher creativity to find missed items
                timeout=60  # 60 second timeout to prevent worker hanging
            )
            
            # Safely extract response
            if not response or not response.choices:
                print("[WARNING] Second pass: OpenAI returned empty response")
                return []
            
            response_text = response.choices[0].message.content
            if not response_text:
                print("[WARNING] Second pass: Response content is None")
                return []
            
            response_text = response_text.strip()
            result = self._parse_analysis_response(response_text, 0)
            
            print(f"[INFO] Second pass found {len(result.get('issues', []))} additional issues")
            return result.get('issues', [])
            
        except Exception as e:
            print(f"[WARNING] Second review pass failed: {str(e)}")
            return []
    
    def _merge_and_deduplicate(self, pass1: List, pass2: List, pass3: List) -> List[Dict[str, Any]]:
        """Merge findings from all passes and remove duplicates"""
        all_issues = []
        seen_refs = set()
        
        for issue_list in [pass1, pass2, pass3]:
            for issue in issue_list:
                ref = issue.get('pid_reference', '')
                issue_text = issue.get('issue_observed', '')
                
                # Create unique key
                key = f"{ref}:{issue_text[:30]}"
                
                if key not in seen_refs:
                    seen_refs.add(key)
                    all_issues.append(issue)
        
        # Renumber serially
        for idx, issue in enumerate(all_issues, 1):
            issue['serial_number'] = idx
        
        print(f"[INFO] Merged {len(all_issues)} unique issues from all passes")
        return all_issues
    
    def _categorize_by_severity(self, issues: List[Dict]) -> Dict[str, List]:
        """Categorize issues by severity"""
        categorized = {
            'critical': [],
            'major': [],
            'minor': [],
            'observation': []
        }
        
        for issue in issues:
            severity = issue.get('severity', 'observation').lower()
            if severity in categorized:
                categorized[severity].append(issue)
        
        return categorized

    def _parse_analysis_response(self, response_text: str, tokens_used: int) -> Dict[str, Any]:
        """Parse OpenAI response and extract JSON"""
        try:
            # Try to find JSON in response
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            
            if json_start >= 0 and json_end > json_start:
                json_str = response_text[json_start:json_end]
                result = json.loads(json_str)
                result['tokens_used'] = tokens_used
                result['raw_response'] = response_text
                return result
            else:
                # Fallback: create basic response
                return {
                    'issues': [{
                        'serial_number': 1,
                        'pid_reference': 'ANALYSIS',
                        'issue_observed': 'Analysis completed - see raw response for details',
                        'action_required': 'Review raw analysis output',
                        'severity': 'observation',
                        'category': 'other'
                    }],
                    'total_issues': 1,
                    'confidence': 'Medium',
                    'tokens_used': tokens_used,
                    'raw_response': response_text
                }
                
        except json.JSONDecodeError as e:
            print(f"[ERROR] JSON parsing failed: {str(e)}")
            return {
                'issues': [{
                    'serial_number': 1,
                    'pid_reference': 'PARSING_ERROR',
                    'issue_observed': f'Failed to parse AI response: {str(e)}',
                    'action_required': 'Review raw response',
                    'severity': 'observation',
                    'category': 'other'
                }],
                'total_issues': 1,
                'confidence': 'Low',
                'tokens_used': tokens_used,
                'raw_response': response_text,
                'parsing_error': True
            }

    def _pdf_to_base64_images(self, pdf_file, dpi: int = 300) -> List[str]:
        """
        Convert PDF pages to base64-encoded PNG images
        
        Args:
            pdf_file: Django FieldFile or file path
            dpi: Resolution for rendering (default: 300 for high detail)
            
        Returns:
            List of base64-encoded image strings
        """
        images_base64 = []
        
        try:
            # Soft-coded approach: Handle both file paths and file objects (S3/Django FileField)
            if isinstance(pdf_file, str):
                # Local file path
                doc = fitz.open(pdf_file)
            else:
                # File object (from S3 or Django FileField) - read content into memory
                pdf_file.seek(0)  # Ensure we're at the start
                pdf_bytes = pdf_file.read()
                doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            
            # Convert each page
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                
                # Render to image
                mat = fitz.Matrix(dpi/72, dpi/72)
                pix = page.get_pixmap(matrix=mat)
                
                # Convert to PIL Image
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                
                # Convert to base64
                buffer = io.BytesIO()
                img.save(buffer, format="PNG", optimize=True)
                img_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
                images_base64.append(img_base64)
            
            doc.close()
            return images_base64
            
        except Exception as e:
            print(f"[ERROR] PDF conversion failed: {str(e)}")
            raise

    def generate_report_summary(self, issues: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Generate summary statistics from analysis issues
        
        Args:
            issues: List of identified issues
            
        Returns:
            Dictionary with summary statistics
        """
        if not issues:
            return {
                'total_issues': 0,
                'critical_count': 0,
                'major_count': 0,
                'minor_count': 0,
                'observation_count': 0,
                'approved_count': 0,
                'ignored_count': 0,
                'pending_count': 0,
                'categories': {}
            }
        
        # Count by severity
        severity_counts = {
            'critical': 0,
            'major': 0,
            'minor': 0,
            'observation': 0
        }
        
        # Count by status
        status_counts = {
            'approved': 0,
            'ignored': 0,
            'pending': 0
        }
        
        # Count by category
        categories = {}
        
        for issue in issues:
            severity = issue.get('severity', 'observation').lower()
            if severity in severity_counts:
                severity_counts[severity] += 1
            
            status = issue.get('status', 'pending').lower()
            if status in status_counts:
                status_counts[status] += 1
            
            category = issue.get('category', 'general')
            categories[category] = categories.get(category, 0) + 1
        
        return {
            'total_issues': len(issues),
            'critical_count': severity_counts['critical'],
            'major_count': severity_counts['major'],
            'minor_count': severity_counts['minor'],
            'observation_count': severity_counts['observation'],
            'approved_count': status_counts['approved'],
            'ignored_count': status_counts['ignored'],
            'pending_count': status_counts['pending'],
            'categories': categories
        }
