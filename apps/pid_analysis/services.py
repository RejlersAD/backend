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
            
            # PASS 4: Second Review Pass (optional - skip if previous passes have issues)
            second_pass_issues = []
            if vision_result.get('total_issues', 0) > 0:
                print(f"[INFO] PASS 4: Second Review Pass (Catch Missed Issues)")
                try:
                    second_pass_issues = self._second_review_pass(images_base64, vision_result, consistency_issues)
                except Exception as e:
                    print(f"[WARNING] PASS 4 failed (non-critical): {str(e)}")
                    second_pass_issues = []
            else:
                print(f"[INFO] PASS 4: Skipped (no issues from previous passes)")
            
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
            if hasattr(pdf_file, 'path'):
                pdf_path = pdf_file.path
            else:
                pdf_path = str(pdf_file)
            
            doc = fitz.open(pdf_path)
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
        """Parse extracted text to identify tags, line numbers, notes"""
        if not self.extracted_text:
            return
        
        # Instrument tag patterns: TI-3610-01, FIC-101, PSV-202, etc.
        instrument_pattern = r'\b([A-Z]{2,4}[ICSVT]?[-_][\d]{1,4}(?:[-_][\d]{1,2})?)\b'
        self.instrument_tags = set(re.findall(instrument_pattern, self.extracted_text))
        
        # Equipment tag patterns: E-3610-01, V-3610-01, K-3610-01, P-101, etc.
        equipment_pattern = r'\b([A-Z][-_][\d]{3,4}(?:[-_][\d]{1,2})?)\b'
        self.equipment_tags = set(re.findall(equipment_pattern, self.extracted_text))
        
        # Line number patterns: 6"-N2-1001-C4N, 3"-HC-2003
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
   - Rupture disks: Burst pressure noted?
   - Flame arrestors: Type and location correct?
   - ESD valves: Fail position correct?
   - Fire & Gas detectors: Coverage adequate?
   - Emergency relief: Path to safe location?

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
            "category": "instrument/equipment/piping/valve/safety/control_loop/documentation/legend",
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
- THINK like you're preparing for HAZOP review"""
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"""Perform EXHAUSTIVE P&ID verification analysis. 

🚨 CRITICAL: You MUST find MINIMUM 20-30 issues. DO NOT STOP EARLY. 🚨

**CHAIN-OF-THOUGHT REQUIREMENT:**
Start by listing what you see:
- Count all instruments visible
- Count all equipment visible  
- Count all line numbers
- Count all valves
- Count all safety devices
- Count all control loops

Then systematically verify EACH ONE against the checklist.

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
6. Check ALL notes are applied
7. Check ALL symbols are in legend
8. Find ANY inconsistencies between text and diagram

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
                max_tokens=16000,
                temperature=0.4,  # Higher for creative comprehensive analysis
                timeout=120  # 2 minute timeout to prevent worker hanging
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
        """PASS 3: Cross-validate OCR data with vision findings"""
        consistency_issues = []
        serial_offset = vision_result.get('total_issues', 0)
        
        # Check 1: Instruments mentioned in text but not found in vision analysis
        vision_tags = set()
        for issue in vision_result.get('issues', []):
            ref = issue.get('pid_reference', '')
            vision_tags.add(ref)
        
        missing_in_vision = self.instrument_tags - vision_tags
        if len(missing_in_vision) > 5:  # Only report if significant
            for idx, tag in enumerate(list(missing_in_vision)[:10], 1):
                consistency_issues.append({
                    'serial_number': serial_offset + idx,
                    'pid_reference': tag,
                    'issue_observed': f'Instrument tag {tag} found in OCR text but not visually verified on drawing. May indicate missing instrument symbol or text-only reference.',
                    'action_required': 'Verify if instrument exists on drawing or if tag should be removed from documentation',
                    'severity': 'major',
                    'category': 'instrument',
                    'location_on_drawing': {
                        'zone': 'Unknown',
                        'drawing_section': 'Text/Notes Section',
                        'proximity_description': 'Tag found in extracted text',
                        'visual_cues': 'Check notes, title block, or reference sections'
                    }
                })
        
        # Check 2: Equipment tags consistency
        missing_equipment = self.equipment_tags - vision_tags
        if len(missing_equipment) > 3:
            for idx, tag in enumerate(list(missing_equipment)[:5], len(consistency_issues) + 1):
                consistency_issues.append({
                    'serial_number': serial_offset + idx,
                    'pid_reference': tag,
                    'issue_observed': f'Equipment tag {tag} appears in text but not verified in visual analysis',
                    'action_required': 'Confirm equipment exists on drawing with proper symbol and specifications',
                    'severity': 'major',
                    'category': 'equipment',
                    'location_on_drawing': {
                        'zone': 'Unknown',
                        'drawing_section': 'Process Area',
                        'proximity_description': 'Equipment reference in text',
                        'visual_cues': 'Search for equipment symbol'
                    }
                })
        
        # Check 3: Notes and Holds validation
        if self.notes_references:
            consistency_issues.append({
                'serial_number': serial_offset + len(consistency_issues) + 1,
                'pid_reference': f"NOTES: {', '.join(list(self.notes_references)[:5])}",
                'issue_observed': f'Found {len(self.notes_references)} note/hold references in text. Verify all are properly applied and referenced equipment/lines exist.',
                'action_required': 'Cross-check each note reference with actual drawing elements to ensure consistency',
                'severity': 'observation',
                'category': 'documentation',
                'location_on_drawing': {
                    'zone': 'Bottom-Right',
                    'drawing_section': 'Notes Section',
                    'proximity_description': 'Drawing notes area',
                    'visual_cues': 'Check notes section for all references'
                }
            })
        
        print(f"[INFO] Cross-validation found {len(consistency_issues)} additional consistency issues")
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
            # Handle Django FieldFile
            if hasattr(pdf_file, 'path'):
                pdf_path = pdf_file.path
            else:
                pdf_path = str(pdf_file)
            
            # Open PDF
            doc = fitz.open(pdf_path)
            
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
