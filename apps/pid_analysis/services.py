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
            
            # PASS 5: Second Review Pass (only if very few substantive issues found)
            # Threshold reduced to 5 to avoid forcing fabrication when the drawing is clean.
            # The previous threshold of 20 was causing the AI to invent findings.
            second_pass_issues = []
            issues_found = vision_result.get('total_issues', 0)
            if issues_found < 15:  # Run second pass if fewer than 15 issues to catch missed elements
                print(f"[INFO] PASS 5: Second Review Pass ({issues_found} issues found — checking systematically for missed items)")
                try:
                    second_pass_issues = self._second_review_pass(images_base64, vision_result, consistency_issues)
                except Exception as e:
                    print(f"[WARNING] PASS 5 failed (non-critical): {str(e)}")
                    second_pass_issues = []
            else:
                print(f"[INFO] PASS 5: Skipped ({issues_found} issues already found — no forced second pass)")

            
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
        # EXCLUDE P&ID connector/sheet numbers: format NN-PP-NNN-NNNNN where "PP" is a P&ID prefix
        # (e.g., 13-PP-152-45060 is a sheet connector number, NOT a process piping line number)
        line_pattern = r'\b([\d]+"?[-][A-Z]{1,4}[-][\d]{3,4}(?:[-][A-Z\d]+)?)\b'
        raw_line_numbers = set(re.findall(line_pattern, self.extracted_text))
        
        # Filter out P&ID connector numbers: pattern like NN-PP-DDD-DDDDD (starts with digits, then PP, then numbers)
        pid_connector_pattern = re.compile(r'^\d+[-]PP[-]\d+[-]\d+', re.IGNORECASE)
        self.line_numbers = {ln for ln in raw_line_numbers if not pid_connector_pattern.match(ln)}
        
        # Note references: NOTE 1, NOTE 2, HOLD 1, etc.
        note_pattern = r'\b((?:NOTE|HOLD|REF)[\s]*[\d]+)\b'
        self.notes_references = set(re.findall(note_pattern, self.extracted_text, re.IGNORECASE))

    def _build_per_instrument_instructions(self) -> str:
        """
        Build an explicit per-loop, per-tag checkbox checklist.
        Groups OCR instruments by ISA-5.1 loop number, augments with related
        tags from OCR line_numbers, then generates mandatory per-tag checks.
        Rule: Each unchecked box = one JSON finding.
        """
        import re

        # ISA-5.1 function-code sets (prefix-based match)
        CTRL      = ('HIC', 'FIC', 'LIC', 'TIC', 'PIC', 'ZIC', 'AIC', 'WIC', 'HC', 'FC', 'LC', 'TC', 'PC')
        VALVE     = ('XV', 'SDV', 'BDV', 'FCV', 'HV', 'MOV', 'SOV', 'LCV', 'TCV', 'PCV', 'EV', 'PV', 'CV')
        XMIT      = ('FT', 'PT', 'TT', 'LT', 'AT', 'FE', 'TE', 'PE', 'LE', 'FIT', 'PIT', 'TIT', 'LIT', 'AIT')
        SOLENOID  = ('XY', 'HY', 'TY', 'PY', 'FY', 'LY')
        SWITCH    = ('TSHH', 'TSLL', 'PSHH', 'PSLL', 'LSHH', 'LSLL', 'TSH', 'TSL', 'PSH', 'PSL',
                     'FSH', 'FSL', 'ZSH', 'ZSL', 'XZSH', 'XZSL', 'XZLH', 'XZLL')
        INDIC     = ('FI', 'PI', 'TI', 'LI', 'AI', 'PG', 'PDI', 'VI', 'TG', 'WI')
        SAFETY_V  = ('PSV', 'PRV', 'PDSV', 'TSV')
        ALL_INST  = CTRL + VALVE + XMIT + SOLENOID + SWITCH + INDIC + SAFETY_V

        def func_code(tag: str) -> str:
            for part in tag.split('-'):
                if not part.isdigit():
                    return part.upper()
            return tag.split('-')[0].upper()

        def loop_num(tag: str) -> str:
            nums = re.findall(r'\d+', tag)
            return nums[-1] if nums else '0'

        # --- Build loops dict from OCR instrument tags ---
        loops: dict = {}
        for tag in sorted(self.instrument_tags or []):
            ln = loop_num(tag)
            loops.setdefault(ln, set()).add(tag)

        # --- Augment loops with instrument-type tags from OCR line_numbers ---
        # OCR line_numbers often contain area-prefixed instrument references like 13-XY-4513
        for raw in (self.line_numbers or []):
            parts = raw.split('-')
            if len(parts) == 3 and parts[0].isdigit() and not parts[1].isdigit():
                fc = parts[1].upper()
                ln = parts[2]
                short = f"{fc}-{ln}"
                # Add only genuine instrument function codes (not equipment)
                if any(fc.startswith(p) for p in ALL_INST):
                    loops.setdefault(ln, set()).add(short)

        if not loops:
            return ("No OCR-confirmed instrument tags — visually scan the entire drawing "
                    "for any instruments and report missing documentation as findings.")

        lines = [
            "=== MANDATORY INSTRUMENT LOOP VERIFICATION ===",
            "QC RULE: For every □ item below, look at the drawing image.",
            "If you CANNOT VISUALLY CONFIRM the element IS present on the drawing → it IS a finding.",
            "Add one JSON issue per unchecked □ item. Do NOT group multiple checkboxes into one finding.",
            "Expected: 15-35 findings for a drawing with 10+ instruments at IFC stage.",
            "",
        ]

        for lnum in sorted(loops.keys()):
            tags = sorted(loops[lnum])
            ctrl_t   = [t for t in tags if any(func_code(t).startswith(p) for p in CTRL)]
            valve_t  = [t for t in tags if any(func_code(t).startswith(p) for p in VALVE)]
            xmit_t   = [t for t in tags if any(func_code(t).startswith(p) for p in XMIT)]
            sol_t    = [t for t in tags if any(func_code(t).startswith(p) for p in SOLENOID)]
            sw_t     = [t for t in tags if any(func_code(t).startswith(p) for p in SWITCH)]
            ind_t    = [t for t in tags if any(func_code(t).startswith(p) for p in INDIC)]
            sfv_t    = [t for t in tags if any(func_code(t).startswith(p) for p in SAFETY_V)]

            lines.append(f"── LOOP {lnum}  ({', '.join(tags)})")

            paired_cv = set()

            # Controller → paired valve checks (controller has highest priority)
            for ctag in ctrl_t:
                lines.append(f"  □ [{ctag}] Controller symbol visible and tag labeled on drawing?  → NO = MAJOR")
                if valve_t:
                    vtag = valve_t[0]
                    paired_cv.add(vtag)
                    lines.append(f"  □ [{ctag}] Is control valve {vtag} body symbol physically drawn near {ctag}?  → NO = CRITICAL")
                    lines.append(f"  □ [{vtag}] Is fail-safe position FC, FO, or FL annotated ON {vtag} symbol?  → NO = MAJOR")
                    lines.append(f"  □ [{ctag}→{vtag}] Is control signal dashed line drawn from {ctag} to {vtag}?  → NO = MAJOR")
                else:
                    lines.append(f"  □ [{ctag}] Is there any final control element (valve with actuator) in this control loop?  → NO = CRITICAL")

            # Actuated valves not already covered by controller pairing
            for vtag in valve_t:
                if vtag in paired_cv:
                    continue
                lines.append(f"  □ [{vtag}] Valve body symbol (triangle/gate/globe) visible for {vtag}?  → NO = MAJOR")
                lines.append(f"  □ [{vtag}] Actuator symbol attached to {vtag}?  → NO = MAJOR")
                lines.append(f"  □ [{vtag}] Fail-safe FC, FO, or FL labeled on {vtag} symbol?  → NO = MINOR")
                lines.append(f"  □ [{vtag}] DCS or controller signal connection shown to {vtag}?  → NO = MINOR")

            # Solenoids / I-P converters
            for stag in sol_t:
                lines.append(f"  □ [{stag}] Solenoid/I-P {stag} symbol visible and connected to valve?  → NO = MINOR")
                lines.append(f"  □ [{stag}] DCS signal line to {stag} shown?  → NO = MINOR")

            # Field transmitters / elements
            for xtag in xmit_t:
                lines.append(f"  □ [{xtag}] Transmitter/element {xtag} symbol visible on drawing?  → NO = MAJOR")
                lines.append(f"  □ [{xtag}] Signal type (4-20 mA or dashed line) shown from {xtag}?  → NO = MINOR")
                if ctrl_t:
                    lines.append(f"  □ [{xtag}→{ctrl_t[0]}] Signal connection from {xtag} to {ctrl_t[0]} or DCS visible?  → NO = MAJOR")

            # Safety switches / process switches
            for stag in sw_t:
                lines.append(f"  □ [{stag}] Switch symbol visible and process tap connected?  → NO = MAJOR")
                lines.append(f"  □ [{stag}] DCS / SIS / interlock connection shown for {stag}?  → NO = CRITICAL")
                lines.append(f"  □ [{stag}] Setpoint or trip-function label noted near {stag}?  → NO = MINOR")

            # Indicators / gauges
            for itag in ind_t:
                lines.append(f"  □ [{itag}] Indicator {itag} visible and tag label clear?  → NO = MINOR")
                lines.append(f"  □ [{itag}] Process tap / connection shown for {itag}?  → NO = MINOR")

            # Safety relief valves
            for svtag in sfv_t:
                lines.append(f"  □ [{svtag}] PSV/PRV symbol visible and set pressure annotated?  → NO = CRITICAL")
                lines.append(f"  □ [{svtag}] Discharge line shown with destination (flare / vent)?  → NO = CRITICAL")

            lines.append("")

        return '\n'.join(lines)
    
    def _vision_analysis_pass(self, images_base64: List[str], reference_context: str = "") -> Dict[str, Any]:
        """PASS 3: Systematic vision-based P&ID quality analysis"""
        try:
            system_prompt = """You are a senior P&ID QA/QC engineer performing a formal quality control review.
Analyze ONLY the provided drawing — base all findings on what is VISUALLY PRESENT, not assumptions.

CORE RULES (follow strictly):
1. ONLY report elements that are visually confirmed on this drawing — never invent tags
2. LINE NUMBERS format: SIZE-FLUIDCODE-SEQ-SPEC (e.g. 4"-HC-1001-CS150)
   - P&ID sheet connector numbers like NN-PP-NNN-NNNNN are NOT process piping lines — ignore for piping checks
   - Tags with area prefix (e.g. 13-FE-4580) are INSTRUMENT TAGS not line numbers
3. INSTRUMENT CLASSIFICATION (ISA-5.1):
   - FI, PI, TI, LI, PG = INDICATORS only — no control loop, no alarm setpoints by definition
   - FIC, PIC, TIC, LIC, HIC = CONTROLLERS — do have control loops (require paired control valve)
   - PSHH, LSHH, TSHH = Safety switches — verify interlock connection
   - ZSH, ZSL, XZSH, XZSL = Position/limit switches — NOT primary safety instruments
   - XV, SDV soft tags inside DCS logic blocks without a valve body symbol = soft references, not physical valves
4. FAIL-SAFE: FC/FO/FL already annotated on a valve symbol = already specified — do NOT flag again
5. HEADER-TO-BRANCH: A large pipe reducing to a smaller branch tap is NORMAL — do not flag as missing reducer
6. NOTES/HOLDS: Read the actual note text. Deleted notes are invisible — only flag active requirements
7. BEFORE flagging any issue, confirm: "Can I see this element right now on this drawing?"

Return ONLY valid JSON in this exact format:
{
    "reasoning": "Summary of what you examined category by category",
    "issues": [
        {
            "serial_number": 1,
            "pid_reference": "Exact tag/line/equipment visible on drawing",
            "issue_observed": "Specific issue with exact values",
            "action_required": "Clear corrective action",
            "severity": "critical/major/minor/observation",
            "category": "instrument/equipment/piping/valve/safety/control_loop/documentation/legend/pipe_class/psv_compliance/holds_compliance/notes_compliance",
            "location_on_drawing": {
                "zone": "Top-Left/Top-Center/Top-Right/Middle-Left/Middle-Center/Middle-Right/Bottom-Left/Bottom-Center/Bottom-Right",
                "drawing_section": "Process area/utility/legend/notes",
                "proximity_description": "Near which equipment or line",
                "visual_cues": "Describe exact position"
            }
        }
    ],
    "total_issues": 0,
    "confidence": "High/Medium/Low"
}"""

            if reference_context:
                system_prompt += "\n\nREFERENCE DOCUMENTS:\n" + reference_context
            
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
                            "text": f"""Please perform a complete, systematic P&ID Quality Control review on this drawing.

This review should follow the standard for an IFC-stage QC check at an EPC oil and gas company.

{reference_context}

--- OCR-CONFIRMED ELEMENTS ON THIS DRAWING ---
These tags were extracted by OCR — use them as a systematic check checklist:

INSTRUMENT TAGS ({len(self.instrument_tags)} total):
{chr(10).join('  - ' + t for t in sorted(self.instrument_tags)[:30]) if self.instrument_tags else '  None detected'}

LINE NUMBERS ({len(self.line_numbers)} total, first 25):
{chr(10).join('  - ' + ln for ln in sorted(self.line_numbers)[:25]) if self.line_numbers else '  None detected'}

NOTE: Tags in format AREA-FUNCCODE-NUMBER (e.g. 13-FE-4580) are INSTRUMENT TAGS.
Line numbers in format NN-PP-NNN-NNNNN are P&ID sheet connectors — exclude from piping line checks.

--- PER-INSTRUMENT SYSTEMATIC CHECK ---
{self._build_per_instrument_instructions()}

--- PIPING LINE CHECK ---
For each line number above (excluding PP-prefix connectors):
  1) Is the full line number visible? (format: SIZE-FLUIDCODE-SEQ-SPEC, e.g. 4"-HC-1001-CS150)
  2) Is the pipe spec class labeled on the line?
  3) Are isolation valves present at equipment nozzle connections?
  4) Are spec breaks indicated where pipe class changes?
  5) Is the source and destination clear (equipment tag or OPC arrow)?

--- OVERALL DRAWING CHECKS ---
Equipment: For each visible vessel/pump/compressor/exchanger — tag format, design conditions, nozzle connections
Safety: Any visible PSV/PRV: set pressure, discharge routing, sizing
Notes/Holds: Read each active note/hold text — flag non-compliance as separate critical/major issues
Documentation: Legend completeness, title block revision, legibility, symbol consistency

--- RULES (always apply) ---
- Report ONLY elements visually confirmed on this drawing
- FI/PI/TI/LI/PG = indicators only — no control loop or alarm setpoints required
- FC/FO/FL already on valve symbol = fail-safe IS specified — do NOT re-flag
- P&ID connector numbers (PP-prefix) are NOT process piping lines
- XV soft-tags in DCS logic blocks without valve body symbol = not physical valves

--- CRITICAL REPORTING REQUIREMENT ---
EVERY □ checkbox in the MANDATORY INSTRUMENT LOOP VERIFICATION section above that you
CANNOT VISUALLY CONFIRM on the drawing MUST become a separate JSON issue entry.
"Cannot confirm" = element is absent, unclear, or not annotated. One □ = one issue.
Do NOT merge multiple checkboxes into one finding. Do NOT skip unconfirmed checkboxes.
The expected issue count for an IFC-stage drawing with 10+ instruments is 15–35.

Return ONLY valid JSON:
{{
    "reasoning": "What you examined: list instruments by category, describe lines, equipment, safety, notes checked",
    "issues": [
        {{
            "serial_number": 1,
            "pid_reference": "Exact tag/line/equipment visible on drawing",
            "issue_observed": "Specific issue with exact values",
            "action_required": "Clear corrective action",
            "severity": "critical/major/minor/observation",
            "category": "instrument/equipment/piping/valve/safety/control_loop/documentation/legend/pipe_class/psv_compliance/holds_compliance/notes_compliance",
            "location_on_drawing": {{
                "zone": "Top-Left/Top-Center/Top-Right/Middle-Left/Middle-Center/Middle-Right/Bottom-Left/Bottom-Center/Bottom-Right",
                "drawing_section": "Process area/utility/legend/notes",
                "proximity_description": "Near which equipment or line",
                "visual_cues": "Describe exact position on the page"
            }}
        }}
    ],
    "total_issues": 0,
    "confidence": "High/Medium/Low"
}}

Return ONLY valid JSON. No markdown, no text outside the JSON."""
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

            msg = response.choices[0].message
            finish = response.choices[0].finish_reason
            print(f"[DEBUG] finish_reason={finish}")

            # Check for content-policy refusal
            if hasattr(msg, 'refusal') and msg.refusal:
                print(f"[ERROR] OpenAI refusal: {msg.refusal[:200]}")
                return {'issues': [], 'total_issues': 0, 'confidence': 'Low'}

            response_text = msg.content
            if not response_text:
                print(f"[ERROR] OpenAI response content is None (finish={finish})")
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
        
        # RULE 18 ENFORCEMENT: Never generate hallucinated critical-instrument issues from OCR alone.
        # OCR may extract tag strings from drawing text, notes, or title-block that are NOT actual
        # physical instruments on this sheet. We only raise an observation-level note here — never
        # a "critical" or "major" finding — because we cannot visually confirm the instrument exists.
        # Previously "PSV" was in critical_prefixes causing false PSV findings (Issue #3 from expert).
        # Cross-validation issues are now observation-only and clearly flagged as "text reference only".
        
        if len(missing_in_vision) > 10:  # Only report if many tags missing (possible OCR limitation)
            consistency_issues.append({
                'serial_number': serial_offset + len(consistency_issues) + 1,
                'pid_reference': f"OCR Text Reference: {', '.join(list(missing_in_vision)[:5])}... ({len(missing_in_vision)} total)",
                'issue_observed': f'Found {len(missing_in_vision)} instrument tag strings in extracted text that were not visually confirmed on drawing. These may be: (1) references to instruments on connected OPC/drawings, (2) OCR artifacts, (3) tags in notes/title block, or (4) instruments with symbol recognition limitations. Visual confirmation required.',
                'action_required': 'Review if these tags are cross-references to connected drawings. If they should be on this drawing, verify instrument symbols are physically present.',
                'severity': 'observation',
                'category': 'instrument',
                'location_on_drawing': {
                    'zone': 'Multiple',
                    'drawing_section': 'Text references or connected systems',
                    'proximity_description': 'Tags found in text extraction only — NOT visually confirmed',
                    'visual_cues': 'Check notes section and connected OPC/drawing references'
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
        """PASS 4: Second review targeting tags not mentioned in the first pass"""
        try:
            first_pass_issues = first_pass.get('issues', [])
            first_refs = {i.get('pid_reference', '').upper() for i in first_pass_issues}

            # Find OCR tags not mentioned in first-pass findings
            all_ocr = sorted(list(self.instrument_tags or []) + list({
                f"{p.split('-')[1]}-{p.split('-')[2]}"
                for p in (self.line_numbers or [])
                if len(p.split('-')) == 3 and p.split('-')[0].isdigit() and not p.split('-')[1].isdigit()
            }))
            unchecked = [t for t in all_ocr if not any(t.upper() in ref or ref in t.upper() for ref in first_refs)]

            first_summary = '\n'.join(
                f"  - {i.get('pid_reference')}: {i.get('issue_observed','')[:60]}"
                for i in first_pass_issues[:15]
            )

            unchecked_str = ', '.join(unchecked[:20]) if unchecked else 'All tags were addressed'

            messages = [
                {
                    "role": "system",
                    "content": """Perform a focused SECOND REVIEW on a P&ID drawing.

STRICT RULES:
- ONLY report issues visually confirmed on the drawing — never fabricate
- Apply ISA-5.1: FI/PI/TI/LI/PG = indicators only (no control loop)
- FC/FO/FL already annotated on valve = fail-safe specified — do NOT re-flag
- P&ID connector numbers (NN-PP-NNN-NNNNN) are NOT process piping lines
- If no additional issues exist, return empty issues array

WHAT TO LOOK FOR:
- Tags listed as unchecked that need verification on the drawing
- Any instruments / equipment visible but not addressed in first pass
- Control loops where signal connections are absent
- Missing fail-safe annotations on actuated valves
- Safety switches without interlock wiring shown

Return ONLY valid JSON with "issues" array and "total_issues" integer."""
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"""SECOND REVIEW PASS — focus on tags NOT yet addressed.

FIRST PASS ({len(first_pass_issues)} issues found):
{first_summary}

TAGS NOT YET COVERED IN FINDINGS: {unchecked_str}

For each uncovered tag, look at the drawing and check:
- Is the instrument symbol visible and properly labeled?
- Is its signal connection / wiring clearly shown?
- Is required annotation (fail-safe, setpoint reference, etc.) present?
Report any missing or unclear elements as separate issues.

Also scan the drawing broadly for any visible elements not covered at all (equipment nozzles,
spec breaks, legend items, title block revision) that have genuine issues.

Return ONLY valid JSON: {{"issues": [...], "total_issues": N}}"""
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
                max_tokens=12000,
                temperature=0.4,
                timeout=300
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
            print(f"[DEBUG RAW SECOND PASS] len={len(response_text)} | preview={response_text[:120]}")
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
                    context_parts.append(f"     - {eq.get('tag', 'N/A')}: {eq.get('type', 'Unknown')} "
                                       f"(Design: {eq.get('design_pressure', 'N/A')} / {eq.get('design_temp', 'N/A')})")
                context_parts.append("   -- CRITICAL: Each equipment above MUST appear on P&ID with matching specifications")
        
        # 2. Line List - Structured piping data
        if 'line_list' in reference_data:
            line_list = reference_data['line_list']
            context_parts.append("\n? LINE LIST PROVIDED:")
            context_parts.append("   VERIFY: All line numbers on P&ID exist in Line List")
            context_parts.append("   VERIFY: Line sizes match between P&ID and Line List")
            context_parts.append("   VERIFY: Pipe specifications consistent")
            context_parts.append("   VERIFY: From/To equipment tags match")
            context_parts.append("   VERIFY: Line serial numbers are correct (should be = 9600)")
            context_parts.append("   -- Line numbers beyond 9600 are INCORRECT")
            
            if 'lines' in line_list and line_list['lines']:
                context_parts.append(f"   - Line List contains {len(line_list['lines'])} piping lines:")
                for line in line_list['lines'][:8]:  # Show first 8
                    context_parts.append(f"     - {line.get('line_number', 'N/A')}: {line.get('size', 'N/A')} "
                                       f"{line.get('spec', 'N/A')} ({line.get('from', 'N/A')} ? {line.get('to', 'N/A')})")
                context_parts.append("   -- MAJOR: Flag discrepancies between P&ID line numbers and Line List")
        
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
                    context_parts.append(f"     - {at.get('tag', 'N/A')}: {alarm_str} {at.get('units', '')}")
                context_parts.append("   -- MAJOR: Verify setpoints shown on P&ID match schedule")
        
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
                    context_parts.append(f"     - {abbr} = {meaning}")
            
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
                    context_parts.append(f"     - {std}")
        
        # Add comprehensive verification checklist based on user requirements
        context_parts.append("\n\n-- MANDATORY P&ID QUALITY CHECKS (Fixed Checklist):\n")
        context_parts.append("---------------------------------------------------------------")
        
        context_parts.append("\n1-- DRAWING INFORMATION:")
        context_parts.append("   - Verify drawing number, revision number, project name, client name are correct")
        context_parts.append("   - Match against EDDR (Project Reference Document if provided)")
        
        context_parts.append("\n2-- CONNECTION VERIFICATION:")
        context_parts.append("   - Ensure all connections flagged as going to/from other P&IDs are correctly noted")
        context_parts.append("   - Match corresponding P&ID references")
        context_parts.append("   - Do NOT report issues about explicit receiving line numbers for connectors")
        context_parts.append("   - Do NOT report issues about node/nozzle ID for connectors")
        
        context_parts.append("\n3-- EQUIPMENT TAGGING:")
        context_parts.append("   - Verify equipment tagging details consistent with AGES-GL-08-005, Rev B4")
        context_parts.append("   - Confirm each equipment tagging parameter matches Equipment List")
        context_parts.append("   - Ensure nozzles, manways, internal components shown as per datasheets")
        context_parts.append("   - Do NOT report issues for equipment NOT part of provided P&ID")
        
        context_parts.append("\n4-- CONTROL VALVE MANIFOLD:")
        context_parts.append("   - Verify isolation and bypass valve sizes per AGES-GL-08-005, Rev B4, Table 7-2")
        context_parts.append("   - Reference: Table 7-2 Selection of block and bypass valve sizes in control valve manifold")
        context_parts.append("   - Do NOT report hook-up class selection issues")
        
        context_parts.append("\n5-- ACTUATED VALVES:")
        context_parts.append("   - Trace ALL actuated valves (control valves, shutdown valves, blowdown valves)")
        context_parts.append("   - Verify 'failsafe' position indicated (FC/FO/FL)")
        
        context_parts.append("\n6-- SPECTACLE BLINDS:")
        context_parts.append("   - Check position of all spectacle blinds")
        context_parts.append("   - Check function of line (always open or always closed in normal operation)")
        context_parts.append("   - Verify other valves are in same status as spectacle blind")
        context_parts.append("   - Avoid generic issues if specific PSV tag not identified on drawing")
        
        context_parts.append("\n7-- THERMOWELL CONNECTIONS:")
        context_parts.append("   - Check size of thermowell connections against AGES-PH-04-001, Rev-1, Table 14.1")
        context_parts.append("   - Format remark: 'TIT {tag} connection sizes indicated as X'' which are higher/lower than minimum specified size of Y'' as per AGES-PH-04-001, Rev-1, Table 14.1'")
        context_parts.append("   - Do NOT report connection size requirement between TIT and TI")
        
        context_parts.append("\n8-- LINE NUMBERS:")
        context_parts.append("   - Verify line serial numbers are correct")
        context_parts.append("   - Serial numbers beyond 9600 are INCORRECT: 'Line number {XXXXX} is beyond allotted range (up to 9600)'")
        context_parts.append("   - Identify discrepancies when compared to Line List")
        context_parts.append("   - Line size format: X'' (correct) NOT X\\'' (incorrect)")
        context_parts.append("   - Do NOT report issues for line numbers NOT part of provided P&ID")
        
        context_parts.append("\n9-- CHECK VALVES:")
        context_parts.append("   - Check direction of ALL check valves or non-return valves")
        context_parts.append("   - Check function of line and flow direction FIRST before assessing check valve direction")
        context_parts.append("   - Check valve direction should ALWAYS be in direction of flow")
        context_parts.append("   - Check valve symbol alone is enough - orientation arrows NOT required")
        context_parts.append("   - Do NOT report absence of check-valve orientation arrow as issue")
        
        context_parts.append("\n-- NOTES VERIFICATION:")
        context_parts.append("   - Check all notes on drawing")
        context_parts.append("   - If equipment/control valve/instrument/analyzer mentioned in note, verify note number placed near that tag")
        context_parts.append("   - Format: 'Note-X should be placed near equipment tag {TAG}'")
        
        context_parts.append("\n1--1-- ALARM & TRIP SETPOINTS:")
        context_parts.append("   - Check alarm settings against Alarm and Trip Schedule document")
        context_parts.append("   - Verify setpoints shown on P&ID match schedule")
        context_parts.append("   - High alarm (H), Low alarm (L), High-High trip (HH), Low-Low trip (LL)")
        context_parts.append("   - NOTE: Detailed verification against Alarm & Trip Summary NOT typically shown on P&ID itself")
        
        context_parts.append("\n1--2-- ORIFICE/RO SIZING:")
        context_parts.append("   - Do NOT report issues related to orifice/RO size or tag")
        
        context_parts.append("\n1--3-- STRAINERS:")
        context_parts.append("   - Verify strainers provided where required (e.g., pump suction)")
        
        context_parts.append("\n---------------------------------------------------------------")
        context_parts.append("\n-- CRITICAL INSTRUCTIONS:")
        context_parts.append("   - Do NOT report legibility/readability issues")
        context_parts.append("   - Do NOT report call-out issues")
        context_parts.append("   - Do NOT report generic issues without specific location")
        context_parts.append("   - Do NOT report issues for equipment/lines NOT on provided P&ID")
        context_parts.append("   - Provide serial numbers for ALL issues")
        context_parts.append("   - Reference specific AGES clause/page/section/table number when citing standards")
        context_parts.append("   - Generate SPECIFIC mismatches/outputs, not generic observations")
        context_parts.append("   - Verify ALL information from P&ID image - do NOT return empty P&ID column")
        context_parts.append("\n?FOCUS: Find REAL engineering mistakes based on P&ID drawing!")
        context_parts.append("AVOID: Generic issues, legibility complaints, equipment not on drawing, false positives")
        
        return "\n".join(context_parts)




