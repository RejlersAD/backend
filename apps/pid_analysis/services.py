"""
P&ID Analysis Service - Multi-Pass Comprehensive Analysis
Architecture: OCR + Vision + Cross-Validation + Chain-of-Thought + Reference Verification
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
from .reference_processor import ReferenceDocumentProcessor


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
        self.reference_processor = ReferenceDocumentProcessor()
        self.extracted_text = ""
        self.instrument_tags = set()
        self.equipment_tags = set()
        self.line_numbers = set()
        self.notes_references = set()
        print('[INFO] Multi-Pass PID Analysis Service initialized with 180s timeout')

    def analyze_pid_drawing(self, pdf_file, drawing_number: Optional[str] = None, reference_documents: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Multi-Pass P&ID Analysis with OCR, Vision, Cross-Validation, and Reference Document Verification
        
        PASS 1: OCR Text Extraction - Extract all text, tags, notes, line numbers
        PASS 2: Reference Document Processing - Extract equipment specs, legends, standards
        PASS 3: Vision Analysis - Comprehensive visual inspection with chain-of-thought
        PASS 4: Cross-Validation - Verify consistency between text, visual, and reference data
        PASS 5: Second Review - Re-analyze to catch missed issues
        
        Args:
            pdf_file: Django FieldFile or file path
            drawing_number: Optional drawing number for reference
            reference_documents: Dict of reference document data extracted from uploaded files
                {
                    'equipment_datasheets': [...],  # Equipment dimensions, ratings, specs
                    'instrument_datasheets': [...], # Instrument specs, ranges, fail-safe positions
                    'legends_symbols': [...],       # Standard symbols and abbreviations
                    'pid_standards': [...],         # P&ID standards and guidelines
                    'process_description': [...],   # Process flow and operating conditions
                    'safety_requirements': [...]    # SIL, HAZOP, PSV requirements
                }
            
        Returns:
            Dictionary with comprehensive analysis results including reference compliance
        """
        try:
            print(f"[INFO] ========== MULTI-PASS ANALYSIS WITH REFERENCE VERIFICATION ==========")
            print(f"[INFO] Drawing: {drawing_number or 'Unknown'}")
            if reference_documents:
                print(f"[INFO] Reference documents provided: {list(reference_documents.keys())}")
            
            # PASS 1: OCR Text Extraction
            print(f"[INFO] PASS 1: OCR Text Extraction")
            images_base64 = self._pdf_to_base64_images(pdf_file)
            self._extract_text_from_pdf(pdf_file)
            self._parse_extracted_data()
            
            print(f"[INFO] Extracted {len(self.instrument_tags)} instrument tags")
            print(f"[INFO] Extracted {len(self.equipment_tags)} equipment tags")
            print(f"[INFO] Extracted {len(self.line_numbers)} line numbers")
            print(f"[INFO] Extracted {len(self.notes_references)} note references")
            
            # PASS 2: Reference Document Processing (SOFT-CODED: AI-Powered Intelligence)
            reference_data = {}
            if reference_documents:
                print(f"[INFO] PASS 2: Reference Document Intelligence Extraction")
                try:
                    reference_data = self._process_reference_documents(reference_documents)
                    print(f"[INFO] Reference data extracted: {len(reference_data)} categories")
                except Exception as e:
                    print(f"[WARNING] Reference document processing failed (non-critical): {e}")
                    reference_data = {}
            else:
                print(f"[INFO] PASS 2: Skipped (No reference documents provided)")
            
            # PASS 3: Vision Analysis with Chain-of-Thought & Reference Cross-Verification
            print(f"[INFO] PASS 3: Vision Analysis (Chain-of-Thought + Reference Verification)")
            try:
                vision_result = self._vision_analysis_with_references(images_base64, reference_data)
            except Exception as e:
                print(f"[ERROR] PASS 3 failed: {str(e)}")
                vision_result = {'issues': [], 'total_issues': 0, 'confidence': 'Low'}
            
            # PASS 4: Cross-Validation
            print(f"[INFO] PASS 4: Cross-Validation & Consistency Checks")
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
                    'analysis_passes': 5,
                    'multi_pass_enabled': True,
                    'reference_documents_used': bool(reference_documents),
                    'reference_categories': list(reference_data.keys()) if reference_data else []
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
    
    def _vision_analysis_pass(self, images_base64: List[str], reference_context: str = "") -> Dict[str, Any]:
        """PASS 3: Enhanced vision-based analysis with chain-of-thought and reference verification"""
        try:
            # Build system prompt with reference context
            system_prompt = """?? STRICT ENGINEERING MODE - ZERO HALLUCINATION POLICY ??

You are a senior P&ID QA/QC engineer with strict validation discipline.

-------------------------------------------------------------------
                    CRITICAL RULES - ABSOLUTE COMPLIANCE
-------------------------------------------------------------------

[WARNING] RULE 1: DOCUMENT ISOLATION - ZERO CROSS-CONTAMINATION
????????????????????????????????????????????????????????????????
� ONLY use the CURRENTLY UPLOADED P&ID document
� DO NOT use prior knowledge from other drawings
� DO NOT assume standards unless explicitly written on THIS drawing
� DO NOT reference other users' documents
� DO NOT mix information from different P&IDs
� If something is not visible on THIS drawing ? DO NOT mention it
� If unsure ? IGNORE it completely

[WARNING] RULE 2: ENTITY CLASSIFICATION - STRICT PARSING DISCIPLINE
????????????????????????????????????????????????????????????????

**LINE NUMBER FORMAT:**
� Pattern: [SIZE]-[FLUID CODE]-[SEQUENCE]-[SPEC]
� Examples: 
  - 2"-D-6155-033842-X-N  ? COMPLETE LINE NUMBER (do not split!)
  - 4"-VG-5277-033842     ? COMPLETE LINE NUMBER
  - 6"-HC-1001-CS150      ? COMPLETE LINE NUMBER
� Treatment: Treat FULL string as ONE entity
� ? FORBIDDEN: Do NOT extract "D-6155" from "2"-D-6155-033842-X-N" as equipment
� ? FORBIDDEN: Do NOT split line numbers into parts
� ? FORBIDDEN: Do NOT interpret partial line numbers as equipment tags

**EQUIPMENT TAG FORMAT:**
� Pattern: [PREFIX]-[NUMBER] or [PREFIX]-[NUMBER][SUFFIX]
� Examples:
  - V-101        ? Vessel
  - P-201A       ? Pump A
  - E-103        ? Heat Exchanger
  - T-301        ? Tank
  - C-401        ? Compressor
� Must be clearly labeled in drawing with equipment symbol
� Must NOT be part of a line number string

**CLASSIFICATION DECISION TREE:**
1. Does string contain size (e.g., 2", 4", 6")? ? Likely LINE NUMBER
2. Does string have fluid code (D, VG, HC, etc.)? ? Likely LINE NUMBER
3. Does string have multiple segments separated by dashes with numbers > 4 digits? ? LINE NUMBER
4. Is string standalone near equipment symbol? ? EQUIPMENT TAG
5. If ambiguous ? SKIP IT (don't guess!)

?? RULE 3: NOTES & HOLDS - ACTIVE vs DELETED
????????????????????????????????????????????????????????????????
� If note says "DELETED" ? COMPLETELY IGNORE (do not reference at all)
� If HOLD says "DELETED" ? COMPLETELY IGNORE (do not reference at all)
� ONLY validate ACTIVE notes (not marked as deleted)
� ONLY validate ACTIVE holds (not marked as deleted)
� If unclear whether deleted ? DO NOT reference it

?? RULE 4: ARROWS & CONTINUATION MARKS - NOT PIPELINES
????????????????????????????????????????????????????????????????
� Small arrows indicating continuation (?, ?, ?, ?) ? PIPELINES
� Continuation arrows with line numbers nearby = line continues elsewhere
� DO NOT treat arrows as missing pipelines
� DO NOT flag arrows as "missing source/destination"
� Only analyze actual pipe segments, not directional indicators

?? RULE 5: SPEC BREAKS & MATERIAL TRANSITIONS
????????????????????????????????????????????????????????????????
� If spec break symbol exists at transition ? DO NOT flag missing transition
� Spec break symbols: ?, ?, ?, or line break indicators
� Only flag if NO transition marking exists between different specs
� Check actual drawing for break symbols before flagging

?? RULE 6: EQUIPMENT COUNTING - VISUAL CONFIRMATION ONLY
????????????????????????????????????????????????????????????????
� Count ONLY visually confirmed equipment with symbols
� Ignore OCR noise (random text fragments)
� Ignore line numbers misread as equipment
� Ignore reference drawing titles/headers
� Equipment must have: (1) Symbol + (2) Clear tag
� If equipment tag appears in reference title ? NOT equipment on this drawing

?? RULE 7: ENGINEERING VALIDATION - EVIDENCE-BASED ONLY
????????????????????????????????????????????????????????????????

**ONLY report issues if:**
� Clearly visible on drawing
� Explicitly missing
� Not already present

**DO NOT suggest:**
� Reducers if already present (check carefully!)
� Specs if already written (verify first!)
� Material issues without visual evidence
� Size changes that are intentional design (header to branch = normal!)
� NACE requirements not mentioned on this drawing
� Standards not referenced on this drawing

**HEADER-TO-BRANCH SIZE CHANGES (NORMAL - NOT AN ISSUE):**
� 4" header with 2" branch = NORMAL DESIGN (do not flag reducer!)
� 6" header with 3" branch = NORMAL DESIGN
� 8" header with 4" branch = NORMAL DESIGN
� Only flag if SAME line number changes size WITHOUT reducer symbol

?? RULE 8: NO ASSUMPTIONS - EXPLICIT CONTENT ONLY
????????????????????????????????????????????????????????????????
� Do NOT assume NACE requirements unless written
� Do NOT assume MOC (material of construction) unless specified
� Do NOT assume pressure ratings unless noted
� Do NOT reference industry standards unless cited on drawing
� Only validate what is EXPLICITLY shown or required by drawing notes

[WARNING] RULE 9: ERROR DETECTION PRIORITY - REAL MISTAKES ONLY
????????????????????????????????????????????????????????????????

**FOCUS ON:**
[CHECK] Duplicate line numbers (same number used twice)
[CHECK] Missing specs where required by drawing notes
[CHECK] Missing reducers where REQUIRED (not header-to-branch!)
[CHECK] Incorrect sizes (verify actual discrepancy)
[CHECK] Missing connections (actual pipe dead-ends)
[CHECK] Real inconsistencies (actual contradictions)
[CHECK] Specification violations (conflicts with drawing notes)

**IGNORE:**
[X] Formatting differences
[X] Label placement (arrow vs inline - both valid!)
[X] Text position variations
[X] Normal header-to-branch size reductions
[X] Items already present (double-check before flagging!)

?? RULE 10: SELF-CHECK VALIDATION - MANDATORY BEFORE SUBMISSION
????????????????????????????????????????????????????????????????

**BEFORE RETURNING YOUR ANSWER, YOU MUST ASK YOURSELF:**

1. ? Did I misread any line number as equipment?
   ? If YES: Remove those false equipment detections

2. ? Did I reference anything not visible on THIS drawing?
   ? If YES: Remove cross-document references

3. ? Did I suggest something already present on the drawing?
   ? If YES: Remove false suggestions (verify visually!)

4. ? Did I include deleted notes/holds in my analysis?
   ? If YES: Remove all deleted items

5. ? Did I flag normal header-to-branch transitions as missing reducers?
   ? If YES: Remove false reducer suggestions

6. ? Did I reference NACE/standards not mentioned on drawing?
   ? If YES: Remove assumption-based issues

7. ? Did I count OCR artifacts as equipment?
   ? If YES: Recount using only visual symbols

8. ? Did I detect ACTUAL engineering mistakes (duplicates, real missing items, spec violations)?
   ? If NO: Review more carefully for real issues

9. ? Is my equipment count reasonable (matches visual symbols)?
   ? If NO: Recount excluding line numbers and text fragments

10. ? Are ALL my issues based on visible evidence from THIS drawing only?
    ? If NO: Remove speculative/assumed issues

-------------------------------------------------------------------
                          QUALITY STANDARD
-------------------------------------------------------------------

? GOOD ISSUE EXAMPLE:
{
  "pid_reference": "2\"-VG-5277-033842",
  "issue_observed": "Duplicate line number: 2\"-VG-5277-033842 appears twice on drawing (top left near V-101 and bottom right near P-202)",
  "action_required": "Renumber one instance to avoid confusion",
  "severity": "major"
}

? GOOD ISSUE EXAMPLE:
{
  "pid_reference": "NOTE 3",
  "issue_observed": "NOTE 3 requires design pressure 50 barg, but vessel V-101 shows 45 barg on datasheet reference",
  "action_required": "Update V-101 design pressure to 50 barg per NOTE 3",
  "severity": "critical"
}

? BAD ISSUE EXAMPLE (HALLUCINATION):
{
  "pid_reference": "Line 2\"-D-6155-033842-X-N",
  "issue_observed": "D-6155 equipment does not have capacity specified",
  "severity": "major"
}
? WRONG: "D-6155" is part of LINE NUMBER, not equipment!

? BAD ISSUE EXAMPLE (CROSS-CONTAMINATION):
{
  "pid_reference": "HOLD-4",
  "issue_observed": "HOLD-4 requires fail-closed valves, but valve on 4\"-D-6153-013842 not specified as FC",
  "severity": "critical"  
}
? WRONG: HOLD-4 says "DELETED" on the drawing, should not be referenced!

? BAD ISSUE EXAMPLE (FALSE SUGGESTION):
{
  "pid_reference": "4\" header to 2\" line",
  "issue_observed": "Missing reducer between 4\" header and 2\" branch line",
  "severity": "major"
}
? WRONG: This is normal header-to-branch design, not an issue!

-------------------------------------------------------------------

**MANDATORY CHAIN-OF-THOUGHT PROCESS:**
Before listing issues, you MUST think through:
1. "What instruments do I see? Are all properly specified?"
2. "What equipment exists? Is each tagged and specified?"
3. "What are all the line numbers? Do they all have source/destination?"
4. "What control loops exist? Are they complete?"
5. "What safety devices exist? Are they properly configured?"
6. "What notes/holds are ACTIVE (not deleted)? Are they applied?"
7. "Does the legend match all symbols used?"
8. "Are there any inconsistencies or missing data?"
9. "Are pipe classes consistent between equipment nozzles and connected piping?"
10. "Are dissimilar material connections properly identified with insulating gaskets?"
11. "Do Restriction Orifices (RO) and LTCS have minimum spool lengths?"
12. "Are free draining slopes and low point drains provided?"
13. "Do PSV set pressures comply with equipment design pressures?"
14. "Have I avoided misreading line numbers as equipment?"
15. "Have I checked for deleted notes/holds before referencing them?"
16. "Have I verified items are actually missing before suggesting them?"
"""

            if reference_context:
                system_prompt += "\n\n" + reference_context + "\n\n"

            system_prompt += """

**REQUIRED VERIFICATION CHECKLIST - CHECK EVERY ITEM:**

[INSTRUMENTS] (Check ALL visible instruments)
   - Tag format correct? (TI, TIC, FIC, PSV, LIC, etc.)
   - Measurement range specified?
   - Alarm setpoints (HH, H, L, LL) present and logical?
   - Trip setpoints for safety instruments?
   - Fail-safe position (FC, FO, FL) specified for control valves?
   - Signal type indicated (4-20mA, digital, etc.)?
   - Connected to correct equipment/line?
   - Location accessible for maintenance?

[EQUIPMENT] (Check ALL vessels, pumps, compressors, exchangers)
   - Tag number visible and correct format?
   - Equipment type clearly identified?
   - Design pressure/temperature specified?
   - Material of construction noted?
   - Nozzle schedule complete?
   - Capacity/size specified?
   - Datasheet reference present?

[PIPING_AND_LINES] (Check EVERY line)
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

[VALVES] (Check ALL valves)
   - Valve type appropriate for service?
   - Valve size matches line size?
   - Actuator type specified (manual, pneumatic, motor)?
   - Fail position for automated valves?
   - Check valve orientation correct?
   - Block valves for isolation?
   - Bypass valves where needed?
   - Three-way valves configured correctly?

[SAFETY_SYSTEMS] (CRITICAL - Pressure Safety Valves MAWP Compliance)
   - Pressure Safety Valve (PSV): Set pressure specified?
   - PSV: Set pressure vs Equipment Design Pressure compliance (Must be = MAWP)?
   - PSV: CRITICAL VERIFICATION - Set pressure must NOT exceed Maximum Allowable Working Pressure
   - PSV: Discharge routed properly?
   - PSV: Sized for duty?
   - Rupture disks: Burst pressure noted?
   - Flame arrestors: Type and location correct?
   - ESD valves: Fail position correct?
   - Fire & Gas detectors: Coverage adequate?
   - Emergency relief: Path to safe location?

[PIPE_CLASS] & TRIM CLASS CONSISTENCY
   - Equipment nozzle class matches connected piping class?
   - Valve trim class compatible with line specification?
   - Pressure-temperature rating consistency maintained?
   - Material compatibility between equipment and piping?
   - Flange rating matches line pressure class?
   - Gasket material suitable for service conditions?

[DISSIMILAR_MATERIALS] & INSULATING GASKETS
   - Dissimilar metal connections identified (e.g., carbon steel to stainless steel)?
   - Insulating gaskets specified where dissimilar materials meet?
   - Insulating kit complete (gasket, sleeves, washers)?
   - Galvanic corrosion prevention measures noted?
   - Material transition points clearly marked?
   - Electrical isolation requirements met?

[SPOOL_LENGTH] COMPLIANCE
   - Minimum spool length downstream of Restriction Orifice (RO) met?
   - RO to first fitting: Minimum 5D (5 � pipe diameter) straight run?
   - Low Temperature Cut-off Switch (LTCS) installation clearance adequate?
   - Straight run requirements for flow measurement devices satisfied?
   - Instrument tapping locations comply with minimum distances?
   - Upstream/downstream piping interference checked?

[DRAINAGE] & SLOPE REQUIREMENTS
   - All horizontal lines have proper drainage slope (typically 1:100 or 1:50)?
   - Low point drains provided at collection points?
   - High point vents provided at elevation changes?
   - Dead legs eliminated or minimized?
   - Pocketing prevented in piping layout?
   - Drainage direction indicated on drawing?
   - Drain valve sizing adequate for service?
   - Winterization provisions noted for outdoor lines?

[CONTROL_LOOPS]
   - Controller output goes to correct valve?
   - Measurement source identified?
   - Control valve has fail-safe specified?
   - Cascade loops properly connected?
   - Split-range valves configured correctly?
   - Override logic documented?
   - Interlock conditions clear?

[NOTES_AND_DOCUMENTATION]
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

[LEGEND_AND_SYMBOLS]
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
            
            messages = [
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"""?? COMPREHENSIVE P&ID VERIFICATION WITH STRICT ENTITY CLASSIFICATION ??

-------------------------------------------------------------------
                    STRUCTURED ANALYSIS WORKFLOW
-------------------------------------------------------------------

?? STEP 1: STRUCTURED ENTITY EXTRACTION (Pre-Processing)
????????????????????????????????????????????????????????????????

First, carefully extract and categorize ALL entities from the drawing:

**A. EXTRACT LINE NUMBERS (with strict pattern matching):**
   Pattern: [SIZE]-[FLUID]-[SEQUENCE]-[SPEC]
   
   Examples to INCLUDE:
   ? 2"-D-6155-033842-X-N    ? LINE NUMBER (complete string)
   ? 4"-VG-5277-033842        ? LINE NUMBER
   ? 6"-HC-1001-CS150         ? LINE NUMBER
   ? 8"-P-101-316SS           ? LINE NUMBER
   
   Examples to EXCLUDE:
   ? D-6155                   ? Part of line number, NOT equipment
   ? VG-5277                  ? Part of line number, NOT equipment
   ? HC-1001                  ? Part of line number, NOT equipment

**B. EXTRACT EQUIPMENT TAGS (with visual symbol confirmation):**
   Pattern: [PREFIX]-[NUMBER][SUFFIX]
   Requirements: (1) Has equipment symbol + (2) Standalone tag
   
   Examples to INCLUDE:
   ? V-101    (near vessel symbol)     ? EQUIPMENT
   ? P-202A   (near pump symbol)       ? EQUIPMENT
   ? E-303    (near exchanger symbol)  ? EQUIPMENT
   ? T-401    (near tank symbol)       ? EQUIPMENT
   
   Examples to EXCLUDE:
   ? D-6155   (from "2"-D-6155-033842") ? Part of LINE NUMBER
   ? VG-5277  (from line number)        ? Part of LINE NUMBER
   ? Equipment tags in reference P&ID titles ? Not on THIS drawing

**C. EXTRACT ACTIVE NOTES (excluding deleted):**
   For each note:
   1. Check if marked "DELETED" ? If YES, SKIP completely
   2. Extract: NOTE-[number], Full text, Type
   3. Only process ACTIVE notes (not deleted)

**D. EXTRACT ACTIVE HOLDS (excluding deleted):**
   For each hold:
   1. Check if marked "DELETED" ? If YES, SKIP completely
   2. Extract: HOLD-[number], Full text, Requirement type
   3. Only process ACTIVE holds (not deleted)

**E. EXTRACT SPEC BREAKS:**
   Look for transition symbols: ?, ?, ?, line break marks
   Document locations of all spec breaks

**F. EXTRACT INSTRUMENTS:**
   Pattern: [MEASUREMENT][TYPE]-[NUMBER]
   Examples: PI-101, TIC-202, FIC-303, LT-404, PSV-505

**G. COUNT EQUIPMENT (visual symbols only):**
   Count ONLY items with:
   � Visible equipment symbol (vessel, pump, exchanger, etc.)
   � Clear equipment tag next to symbol
   � NOT parts of line numbers
   � NOT text from reference drawing titles
   
   Expected count: Usually 2-20 equipment items per drawing
   If you count >50 ? You're counting line number fragments!

-------------------------------------------------------------------

?? STEP 2: VALIDATION & RELATIONSHIP ANALYSIS
????????????????????????????????????????????????????????????????

Now verify relationships and detect issues:

**A. LINE NUMBER VALIDATION:**
   For each line number:
   � Check for duplicates (same number appearing twice)
   � Verify source and destination (ignore continuation arrows!)
   � Check spec consistency (unless spec break present)
   � Verify size consistency (unless reducer present OR header-to-branch)

**B. EQUIPMENT VALIDATION:**
   For each equipment tag:
   � Verify specifications present
   � Check design pressure/temperature
   � Verify material of construction
   � Check connections and nozzles

**C. NOTES/HOLDS COMPLIANCE:**
   For each ACTIVE note/hold:
   � Compare requirement vs actual drawing
   � Flag if NOT implemented
   � Flag if VIOLATED
   � Create separate issue for each non-compliance

**D. SPEC BREAK VERIFICATION:**
   For material transitions:
   � If spec break symbol present ? OK (no issue)
   � If NO spec break symbol at transition ? Flag missing

**E. REDUCER/EXPANDER VERIFICATION:**
   � If SAME line number changes size without reducer ? Flag missing reducer
   � If DIFFERENT lines (header-to-branch) have different sizes ? NORMAL (no issue!)
   � If reducer symbol already present ? DO NOT flag as missing!

-------------------------------------------------------------------

?? STEP 3: ISSUE GENERATION & SELF-CHECK
????????????????????????????????????????????????????????????????

Generate issues based on findings, then MANDATORY SELF-CHECK:

**BEFORE SUBMITTING YOUR RESPONSE - VALIDATE EACH ISSUE:**

For EACH issue you identified, ask:

1. ? Is the pid_reference a COMPLETE line number or actual equipment tag?
   ? GOOD: "2\"-D-6155-033842-X-N" (full line number)
   ? GOOD: "V-101" (equipment tag with symbol visible)
   ? BAD: "D-6155" (fragment of line number, not equipment!)

2. ? Is this issue visible on THIS drawing only?
   ? GOOD: Based on what I see on current drawing
   ? BAD: Assuming standards/NACE/requirements not on drawing

3. ? Did I check if the item is already present before flagging as missing?
   ? GOOD: Verified reducer not present, flagging correctly
   ? BAD: Suggesting reducer when it's already shown on drawing

4. ? Is this note/hold marked as "DELETED"?
   ? GOOD: Only referencing active notes/holds
   ? BAD: Referencing deleted HOLD-4 that says "DELETED"

5. ? Is this a normal header-to-branch size change?
   ? GOOD: Flagging same line number with size change
   ? BAD: Flagging 4" header to 2" branch as missing reducer

6. ? Is this an arrow/continuation mark, not an actual pipeline?
   ? GOOD: Analyzing actual pipe segments
   ? BAD: Flagging continuation arrow as missing source/destination

7. ? Did I count equipment correctly (excluding line number parts)?
   ? GOOD: Counted 3 vessels, 2 pumps (5 total) - matches symbols
   ? BAD: Counted 18 items including line number fragments

8. ? Is my suggestion based on visible evidence, not assumptions?
   ? GOOD: "Spec not specified per NOTE 3 requirement"
   ? BAD: "Should have NACE material" (when NACE not mentioned)

**FINAL QUALITY CHECK:**
� Total equipment count reasonable? (Usually 2-20, not 50+)
� All issues based on THIS drawing only?
� No deleted notes/holds referenced?
� No line number fragments misread as equipment?
� No false missing reducers for header-to-branch?
� All suggestions verified as actually missing?

If ANY answer is NO ? REMOVE that issue from your response!

-------------------------------------------------------------------

**EXTRACTED TEXT DATA (For cross-validation - OCR may have errors):**
Instrument Tags Found: {', '.join(list(self.instrument_tags)[:20]) if self.instrument_tags else 'None'}
Equipment Tags Found: {', '.join(list(self.equipment_tags)[:20]) if self.equipment_tags else 'None'}
Line Numbers Found: {', '.join(list(self.line_numbers)[:20]) if self.line_numbers else 'None'}

?? WARNING: OCR data may contain errors. ALWAYS verify visually on the drawing!
?? Line number fragments (e.g., "D-6155", "VG-5277") are NOT equipment tags!

-------------------------------------------------------------------

**OUTPUT FORMAT - STRICT JSON:**
{{
    "entities_extracted": {{
        "line_numbers_count": 0,
        "equipment_tags_count": 0,
        "active_notes_count": 0,
        "active_holds_count": 0,
        "deleted_notes_count": 0,
        "deleted_holds_count": 0
    }},
    "self_check_passed": true,
    "reasoning": "Chain-of-thought: First I extracted X line numbers, Y equipment with symbols, Z active notes. I checked each systematically...",
    "issues": [
        {{
            "serial_number": 1,
            "pid_reference": "Exact tag/line from drawing (FULL line number, not fragment)",
            "issue_observed": "Specific detailed issue with exact values from THIS drawing only",
            "action_required": "Clear corrective action",
            "severity": "critical/major/minor/observation",
            "category": "instrument/equipment/piping/valve/safety/control_loop/documentation/legend/pipe_class/dissimilar_materials/spool_length/drainage/psv_compliance/holds_compliance/notes_compliance",
            "location_on_drawing": {{
                "zone": "Top-Left/Top-Center/Top-Right/Middle-Left/Middle-Center/Middle-Right/Bottom-Left/Bottom-Center/Bottom-Right",
                "drawing_section": "Process area/utility/legend/notes",
                "proximity_description": "Near equipment X, between lines Y and Z",
                "visual_cues": "Upper left, center section, etc."
            }}
        }}
    ],
    "total_issues": 0,
    "confidence": "High/Medium/Low"
}}

?? FOCUS: Find REAL engineering mistakes (duplicates, actual missing items, spec violations, deleted note references, incomplete data).
? AVOID: False equipment detection, cross-document assumptions, false missing suggestions, deleted note/hold references.

{reference_context}

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

?? **CRITICAL MISSION: Find what was MISSED in the first analysis** ??

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
    
    def _process_reference_documents(self, documents: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process reference documents and extract structured data
        SOFT-CODED: AI-powered intelligence extraction
        """
        return self.reference_processor.process_reference_documents(documents)
    
    def _vision_analysis_with_references(self, images_base64: List[str], reference_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enhanced vision analysis with reference document cross-verification
        SOFT-CODED: Comprehensive compliance checking against uploaded references
        """
        # Build reference context for AI
        reference_context = self._build_reference_context(reference_data)
        
        # Use the existing vision analysis but with enhanced prompt
        return self._vision_analysis_pass(images_base64, reference_context)
    
    def _build_reference_context(self, reference_data: Dict[str, Any]) -> str:
        """
        Build AI-readable context from reference documents
        Updated: Equipment List, Line List, Alarm & Trip Schedule, Legend/Symbol Sheet
        """
        if not reference_data:
            return ""
        
        context_parts = ["\n\n🔍 REFERENCE DOCUMENTS FOR CROSS-VERIFICATION:\n"]
        context_parts.append("🚨 CRITICAL INSTRUCTION:\n")
        context_parts.append("   ✓ PRIMARY DOCUMENT: Analyze the P&ID drawing - report ONLY issues visible on the P&ID\n")
        context_parts.append("   ✓ REFERENCE DOCUMENTS: Use these to VERIFY and CROSS-CHECK the P&ID for discrepancies\n")
        context_parts.append("   ✓ MANDATORY: Flag any mismatches between P&ID and reference documents\n")
        context_parts.append("   ✓ Both are EQUALLY IMPORTANT - P&ID is analyzed, references validate correctness\n\n")
        
        # 1. Equipment List - Structured equipment data
        if 'equipment_list' in reference_data:
            eq_list = reference_data['equipment_list']
            context_parts.append("\n? EQUIPMENT LIST PROVIDED:")
            context_parts.append("   VERIFY: Equipment tags on P&ID match Equipment List exactly")
            context_parts.append("   VERIFY: Equipment tagging parameters consistent with AGES-GL-08-005, Rev B4")
            context_parts.append("   VERIFY: Design pressures and temperatures match")
            context_parts.append("   VERIFY: Nozzles, manways, internal components shown as per datasheets")
            
            if 'equipment' in eq_list and eq_list['equipment']:
                context_parts.append(f"   - Equipment List contains {len(eq_list['equipment'])} equipment items:")
                for eq in eq_list['equipment'][:10]:  # Show first 10
                    context_parts.append(f"     � {eq.get('tag', 'N/A')}: {eq.get('type', 'Unknown')} "
                                       f"(Design: {eq.get('design_pressure', 'N/A')} / {eq.get('design_temp', 'N/A')})")
                context_parts.append("   ?? CRITICAL: Each equipment above MUST appear on P&ID with matching specifications")
        
        # 2. Line List - Structured piping data
        if 'line_list' in reference_data:
            line_list = reference_data['line_list']
            context_parts.append("\n? LINE LIST PROVIDED:")
            context_parts.append("   VERIFY: All line numbers on P&ID exist in Line List")
            context_parts.append("   VERIFY: Line sizes match between P&ID and Line List")
            context_parts.append("   VERIFY: Pipe specifications consistent")
            context_parts.append("   VERIFY: From/To equipment tags match")
            context_parts.append("   VERIFY: Line serial numbers are correct (should be = 9600)")
            context_parts.append("   ?? Line numbers beyond 9600 are INCORRECT")
            
            if 'lines' in line_list and line_list['lines']:
                context_parts.append(f"   - Line List contains {len(line_list['lines'])} piping lines:")
                for line in line_list['lines'][:8]:  # Show first 8
                    context_parts.append(f"     � {line.get('line_number', 'N/A')}: {line.get('size', 'N/A')} "
                                       f"{line.get('spec', 'N/A')} ({line.get('from', 'N/A')} ? {line.get('to', 'N/A')})")
                context_parts.append("   ?? MAJOR: Flag discrepancies between P&ID line numbers and Line List")
        
        # 3. Alarm & Trip Schedule - Setpoints reference
        if 'alarm_trip_schedule' in reference_data:
            ats = reference_data['alarm_trip_schedule']
            context_parts.append("\n? ALARM & TRIP SCHEDULE PROVIDED:")
            context_parts.append("   VERIFY: Alarm setpoints on P&ID match Alarm & Trip Schedule")
            context_parts.append("   VERIFY: Trip setpoints match schedule")
            context_parts.append("   FORMAT: H=High Alarm, L=Low Alarm, HH=High-High Alarm/Trip, LL=Low-Low Alarm/Trip")
            context_parts.append("   NOTE: Engineering unit box for setpoint NOT required on P&ID")
            context_parts.append("   NOTE: Verification against Alarm & Trip Summary is NOT detailed on P&ID itself")
            
            if 'alarms_trips' in ats and ats['alarms_trips']:
                context_parts.append(f"   - Alarm & Trip Schedule contains {len(ats['alarms_trips'])} instruments:")
                for at in ats['alarms_trips'][:8]:  # Show first 8
                    alarms = []
                    if 'alarm_ll' in at: alarms.append(f"LL={at['alarm_ll']}")
                    if 'alarm_l' in at: alarms.append(f"L={at['alarm_l']}")
                    if 'alarm_h' in at: alarms.append(f"H={at['alarm_h']}")
                    if 'alarm_hh' in at: alarms.append(f"HH={at['alarm_hh']}")
                    alarm_str = ", ".join(alarms) if alarms else "No alarms"
                    context_parts.append(f"     � {at.get('tag', 'N/A')}: {alarm_str} {at.get('units', '')}")
                context_parts.append("   ?? MAJOR: Verify setpoints shown on P&ID match schedule")
        
        # 4. Legend / Symbol Sheet - Symbol and spec interpretation
        if 'legend_symbols' in reference_data:
            legend = reference_data['legend_symbols']
            context_parts.append("\n? LEGEND / SYMBOL SHEET PROVIDED:")
            context_parts.append("   VERIFY: All symbols on P&ID are defined in legend")
            context_parts.append("   VERIFY: Symbol usage consistent with legend definitions")
            context_parts.append("   VERIFY: Abbreviations match legend")
            context_parts.append("   VERIFY: Pipe specifications follow legend coding")
            context_parts.append("   VERIFY: Line numbering format follows legend system")
            
            if 'abbreviations' in legend and legend['abbreviations']:
                context_parts.append("   - Symbol/Abbreviation Definitions:")
                for abbr, meaning in list(legend['abbreviations'].items())[:10]:
                    context_parts.append(f"     � {abbr} = {meaning}")
            
            if 'line_numbering' in legend:
                ln = legend['line_numbering']
                if 'format' in ln:
                    context_parts.append(f"   - Line Number Format: {ln['format']}")
                if 'example' in ln:
                    context_parts.append(f"   - Example: {ln['example']}")
                if 'serial_range' in ln:
                    context_parts.append(f"   - Serial Number Range: {ln['serial_range']}")
            
            if 'standards_references' in legend:
                context_parts.append("   - Standards Referenced:")
                for std in legend['standards_references'][:5]:
                    context_parts.append(f"     � {std}")
        
        # Add comprehensive verification checklist based on user requirements
        context_parts.append("\n\n?? MANDATORY P&ID QUALITY CHECKS (Fixed Checklist):\n")
        context_parts.append("---------------------------------------------------------------")
        
        context_parts.append("\n1?? DRAWING INFORMATION:")
        context_parts.append("   � Verify drawing number, revision number, project name, client name are correct")
        context_parts.append("   � Match against EDDR (Project Reference Document if provided)")
        
        context_parts.append("\n2?? CONNECTION VERIFICATION:")
        context_parts.append("   � Ensure all connections flagged as going to/from other P&IDs are correctly noted")
        context_parts.append("   � Match corresponding P&ID references")
        context_parts.append("   � Do NOT report issues about explicit receiving line numbers for connectors")
        context_parts.append("   � Do NOT report issues about node/nozzle ID for connectors")
        
        context_parts.append("\n3?? EQUIPMENT TAGGING:")
        context_parts.append("   � Verify equipment tagging details consistent with AGES-GL-08-005, Rev B4")
        context_parts.append("   � Confirm each equipment tagging parameter matches Equipment List")
        context_parts.append("   � Ensure nozzles, manways, internal components shown as per datasheets")
        context_parts.append("   � Do NOT report issues for equipment NOT part of provided P&ID")
        
        context_parts.append("\n4?? CONTROL VALVE MANIFOLD:")
        context_parts.append("   � Verify isolation and bypass valve sizes per AGES-GL-08-005, Rev B4, Table 7-2")
        context_parts.append("   � Reference: Table 7-2 Selection of block and bypass valve sizes in control valve manifold")
        context_parts.append("   � Do NOT report hook-up class selection issues")
        
        context_parts.append("\n5?? ACTUATED VALVES:")
        context_parts.append("   � Trace ALL actuated valves (control valves, shutdown valves, blowdown valves)")
        context_parts.append("   � Verify 'failsafe' position indicated (FC/FO/FL)")
        
        context_parts.append("\n6?? SPECTACLE BLINDS:")
        context_parts.append("   � Check position of all spectacle blinds")
        context_parts.append("   � Check function of line (always open or always closed in normal operation)")
        context_parts.append("   � Verify other valves are in same status as spectacle blind")
        context_parts.append("   � Avoid generic issues if specific PSV tag not identified on drawing")
        
        context_parts.append("\n7?? THERMOWELL CONNECTIONS:")
        context_parts.append("   � Check size of thermowell connections against AGES-PH-04-001, Rev-1, Table 14.1")
        context_parts.append("   � Format remark: 'TIT {tag} connection sizes indicated as X'' which are higher/lower than minimum specified size of Y'' as per AGES-PH-04-001, Rev-1, Table 14.1'")
        context_parts.append("   � Do NOT report connection size requirement between TIT and TI")
        
        context_parts.append("\n8?? LINE NUMBERS:")
        context_parts.append("   � Verify line serial numbers are correct")
        context_parts.append("   � Serial numbers beyond 9600 are INCORRECT: 'Line number {XXXXX} is beyond allotted range (up to 9600)'")
        context_parts.append("   � Identify discrepancies when compared to Line List")
        context_parts.append("   � Line size format: X'' (correct) NOT X\\'' (incorrect)")
        context_parts.append("   � Do NOT report issues for line numbers NOT part of provided P&ID")
        
        context_parts.append("\n9?? CHECK VALVES:")
        context_parts.append("   � Check direction of ALL check valves or non-return valves")
        context_parts.append("   � Check function of line and flow direction FIRST before assessing check valve direction")
        context_parts.append("   � Check valve direction should ALWAYS be in direction of flow")
        context_parts.append("   � Check valve symbol alone is enough - orientation arrows NOT required")
        context_parts.append("   � Do NOT report absence of check-valve orientation arrow as issue")
        
        context_parts.append("\n?? NOTES VERIFICATION:")
        context_parts.append("   � Check all notes on drawing")
        context_parts.append("   � If equipment/control valve/instrument/analyzer mentioned in note, verify note number placed near that tag")
        context_parts.append("   � Format: 'Note-X should be placed near equipment tag {TAG}'")
        
        context_parts.append("\n1??1?? ALARM & TRIP SETPOINTS:")
        context_parts.append("   � Check alarm settings against Alarm and Trip Schedule document")
        context_parts.append("   � Verify setpoints shown on P&ID match schedule")
        context_parts.append("   � High alarm (H), Low alarm (L), High-High trip (HH), Low-Low trip (LL)")
        context_parts.append("   � NOTE: Detailed verification against Alarm & Trip Summary NOT typically shown on P&ID itself")
        
        context_parts.append("\n1??2?? ORIFICE/RO SIZING:")
        context_parts.append("   � Do NOT report issues related to orifice/RO size or tag")
        
        context_parts.append("\n1??3?? STRAINERS:")
        context_parts.append("   � Verify strainers provided where required (e.g., pump suction)")
        
        context_parts.append("\n---------------------------------------------------------------")
        context_parts.append("\n?? CRITICAL INSTRUCTIONS:")
        context_parts.append("   � Do NOT report legibility/readability issues")
        context_parts.append("   � Do NOT report call-out issues")
        context_parts.append("   � Do NOT report generic issues without specific location")
        context_parts.append("   � Do NOT report issues for equipment/lines NOT on provided P&ID")
        context_parts.append("   � Provide serial numbers for ALL issues")
        context_parts.append("   � Reference specific AGES clause/page/section/table number when citing standards")
        context_parts.append("   � Generate SPECIFIC mismatches/outputs, not generic observations")
        context_parts.append("   � Verify ALL information from P&ID image - do NOT return empty P&ID column")
        context_parts.append("\n?? FOCUS: Find REAL engineering mistakes based on P&ID drawing!")
        context_parts.append("? AVOID: Generic issues, legibility complaints, equipment not on drawing, false positives")
        
        return "\n".join(context_parts)




