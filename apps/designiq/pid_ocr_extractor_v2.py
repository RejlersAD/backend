"""
P&ID OCR Extractor V2 - Multi-Engine + AI Intelligence
Uses Tesseract, EasyOCR, PaddleOCR + OpenAI for accurate line detection
"""

import re
import fitz  # PyMuPDF
from PIL import Image
import pytesseract
import io
import logging
import json
from typing import List, Dict, Optional
import numpy as np
from openai import OpenAI
from django.conf import settings

logger = logging.getLogger(__name__)


class PIDLineExtractorV2:
    """
    Multi-Engine P&ID line number extractor with AI intelligence
    Step 1: Extract ALL text using Tesseract, EasyOCR, PaddleOCR
    Step 2: Use OpenAI to intelligently categorize into table format
    """
    
    def __init__(self):
        self.easyocr_reader = None
        self.paddleocr_reader = None
        self.openai_client = None
        self._init_engines()
    
    def _init_engines(self):
        """Initialize all OCR engines and OpenAI"""
        # Initialize EasyOCR
        try:
            import easyocr
            self.easyocr_reader = easyocr.Reader(['en'], gpu=False)
            logger.info("✅ EasyOCR initialized")
        except Exception as e:
            logger.warning(f"⚠️ EasyOCR not available: {e}")
        
        # Initialize PaddleOCR
        try:
            from paddleocr import PaddleOCR
            # PaddleOCR doesn't have show_log parameter, use logging control instead
            self.paddleocr_reader = PaddleOCR(use_angle_cls=True, lang='en')
            logger.info("✅ PaddleOCR initialized")
        except Exception as e:
            logger.warning(f"⚠️ PaddleOCR not available: {e}")
        
        # Initialize OpenAI
        try:
            openai_key = getattr(settings, 'OPENAI_API_KEY', None)
            if openai_key:
                self.openai_client = OpenAI(api_key=openai_key)
                logger.info("✅ OpenAI initialized")
            else:
                logger.warning("⚠️ OPENAI_API_KEY not configured")
        except Exception as e:
            logger.warning(f"⚠️ OpenAI not available: {e}")
    
    def extract_all_text_from_image(self, img: Image.Image) -> Dict[str, str]:
        """
        Extract text using all available OCR engines
        Returns combined text from all engines
        """
        results = {}
        
        # 1. Tesseract OCR
        try:
            tesseract_text = pytesseract.image_to_string(img, config='--psm 6')
            results['tesseract'] = tesseract_text
            logger.info(f"  ✅ Tesseract extracted {len(tesseract_text)} characters")
        except Exception as e:
            logger.warning(f"  ⚠️ Tesseract failed: {e}")
        
        # 2. EasyOCR
        if self.easyocr_reader:
            try:
                img_array = np.array(img)
                easyocr_result = self.easyocr_reader.readtext(img_array, detail=0)
                easyocr_text = ' '.join(easyocr_result)
                results['easyocr'] = easyocr_text
                logger.info(f"  ✅ EasyOCR extracted {len(easyocr_text)} characters")
            except Exception as e:
                logger.warning(f"  ⚠️ EasyOCR failed: {e}")
        
        # 3. PaddleOCR
        if self.paddleocr_reader:
            try:
                img_array = np.array(img)
                paddle_result = self.paddleocr_reader.ocr(img_array, cls=True)
                paddle_texts = []
                if paddle_result and paddle_result[0]:
                    for line in paddle_result[0]:
                        if line and len(line) > 1:
                            paddle_texts.append(line[1][0])
                paddle_text = ' '.join(paddle_texts)
                results['paddleocr'] = paddle_text
                logger.info(f"  ✅ PaddleOCR extracted {len(paddle_text)} characters")
            except Exception as e:
                logger.warning(f"  ⚠️ PaddleOCR failed: {e}")
        
        return results
    
    def combine_and_deduplicate_text(self, ocr_results: Dict[str, str]) -> str:
        """
        Combine text from all OCR engines and remove duplicates intelligently
        """
        all_words = set()
        for engine, text in ocr_results.items():
            words = text.split()
            all_words.update(words)
        
        combined = ' '.join(sorted(all_words))
        logger.info(f"  📝 Combined text: {len(combined)} characters, {len(all_words)} unique words")
        return combined
    
    def parse_with_openai(self, extracted_text: str, page_num: int) -> List[Dict]:
        """
        Use OpenAI to intelligently parse line numbers
        
        Line number format: 12-D-5777-033842-N
        - SIZE: 1-2 digits (MANDATORY)
        - FLUID: 1-2 LETTERS only (MANDATORY)
        - SEQUENCE: exactly 4 digits (MANDATORY)
        - PIPECLASS: exactly 6 digits (MANDATORY)
        - INSULATION: OPTIONAL 1-2 characters
        """
        if not self.openai_client:
            logger.warning("⚠️ OpenAI not available, falling back to regex parsing")
            return self._fallback_regex_parse(extracted_text, page_num)
        
        prompt = f"""You are an expert P&ID line number extractor. Extract ALL piping line numbers from the text.

**CRITICAL RULES:**
1. ALL 4 components are MANDATORY: SIZE, FLUID, SEQUENCE, PIPECLASS
2. REJECT any line missing SIZE, FLUID, SEQUENCE, or PIPECLASS
3. INSULATION is the ONLY optional component

**REQUIRED LINE FORMAT:**
SIZE-FLUID-SEQUENCE-PIPECLASS(-INSULATION)?

**MANDATORY COMPONENTS (ALL MUST BE PRESENT):**
- SIZE: 1-2 digits ONLY (e.g., 6, 12, 20, 24) - NOT 05, NOT 40, NOT 4003
- FLUID: 1-2 LETTERS ONLY (e.g., D, PG, CW, ST) - NOT numbers like 03, NOT missing
- SEQUENCE: EXACTLY 4 digits (e.g., 5777, 0003, 1234) - NOT 5 digits like 31441, NOT 011441
- PIPECLASS: EXACTLY 6 digits (e.g., 033842, 011441, 123456)

**OPTIONAL COMPONENT:**
- INSULATION: ONLY these exact codes: H, PP, X, N, E, FP, AA (case insensitive)
- NOT fluid codes like PG, D, CW, ST - these are FLUID not INSULATION

**VALID EXAMPLES (All 4 components present):**
✅ "12-D-5777-033842-N" → size:12, fluid:D, seq:5777, pipr_class:033842, insulation:N
✅ "16-PG-4105-011441-X" → size:16, fluid:PG, seq:4105, pipr_class:011441, insulation:X
✅ "10-PG-0003-033842" → size:10, fluid:PG, seq:0003, pipr_class:033842, insulation:""
✅ "20-CW-1234-123456-H" → size:20, fluid:CW, seq:1234, pipr_class:123456, insulation:H
✅ "6-D-0001-011440-PP" → size:6, fluid:D, seq:0001, pipr_class:011440, insulation:PP
✅ "8-ST-9999-888888-FP" → size:8, fluid:ST, seq:9999, pipr_class:888888, insulation:FP
✅ "24-CW-0050-033842-AA" → size:24, fluid:CW, seq:0050, pipr_class:033842, insulation:AA

**INVALID - REJECT THESE (Missing required components):**
❌ "05-011441-X" (MISSING FLUID and SEQUENCE - only has size-pipeclass-insulation)
❌ "4003-031441-X" (SIZE wrong: 4003 is 4 digits, SEQUENCE wrong: 31441 is 5 digits)
❌ "40-03-31441" (FLUID is number 03 not letters, SEQUENCE is 5 digits)
❌ "4005-011441" (MISSING FLUID and SEQUENCE)
❌ "12-1234-123456" (MISSING FLUID - has size-sequence-pipeclass but no fluid)
❌ Any line missing SIZE, FLUID, SEQUENCE, or PIPECLASS

**OUTPUT JSON:**
Return ONLY a JSON array:
[
  {{
    "line_number": "complete line (e.g., 12-D-5777-033842-N)",
    "size": "with quote (e.g., 12\\")",
    "fluid_code": "uppercase (e.g., D or PG)",
    "sequence_no": "4 digits (e.g., 5777)",
    "pipr_class": "6 digits (e.g., 033842)",
    "insulation": "char or empty (e.g., N or empty)",
    "from_equipment": "",
    "to_equipment": "",
    "confidence": "high | medium | low"
  }}
]

**TEXT TO ANALYZE:**
{extracted_text[:8000]}

**EXTRACTION RULES:**
- Extract EVERY occurrence of SIZE-FLUID-SEQ-PIPECLASS pattern
- Insulation is OPTIONAL (can be present or absent)
- If you see variations like "12 D 5777 033842" extract it!
- If you see "12.D.5777.033842" extract it!
- If you see "12-D-5777-033842" extract it!
- Look for patterns across ENTIRE document
- Don't skip partial patterns - we validate later

Extract ALL valid line numbers. Return JSON only."""

        try:
            response = self.openai_client.chat.completions.create(
                model="gpt-4-turbo-preview",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,  # Slightly higher for more variation detection
                max_tokens=4000
            )
            
            result_text = response.choices[0].message.content.strip()
            
            # Remove markdown code blocks if present
            if result_text.startswith('```'):
                result_text = result_text.split('```')[1]
                if result_text.startswith('json'):
                    result_text = result_text[4:]
                result_text = result_text.strip()
            
            parsed_lines = json.loads(result_text)
            
            # Add page number to each item
            for line in parsed_lines:
                line['page'] = page_num
            
            logger.info(f"  ✅ OpenAI found {len(parsed_lines)} line numbers")
            
            # Also run regex as backup and combine results
            regex_lines = self._fallback_regex_parse(extracted_text, page_num)
            logger.info(f"  ✅ Regex found {len(regex_lines)} line numbers")
            
            # Combine both results
            all_lines = parsed_lines + regex_lines
            
            # Deduplicate by line_number
            seen = set()
            unique = []
            for item in all_lines:
                key = item.get('line_number', '')
                if key and key not in seen:
                    seen.add(key)
                    unique.append(item)
            
            logger.info(f"  🎯 Combined total: {len(unique)} unique line numbers")
            return unique
            
        except Exception as e:
            logger.error(f"  ❌ OpenAI parsing failed: {e}")
            return self._fallback_regex_parse(extracted_text, page_num)
        
        prompt = f"""You are an expert P&ID (Piping and Instrumentation Diagram) analyst specializing in piping line number extraction.

**CRITICAL TASK:** Extract ALL piping line numbers from the OCR text below using the EXACT formula format.

**LINE NUMBER FORMULA:**
[Line Size]"-[Fluid Code]-[Sequence No]-[Pipe Class](-[Insulation])?

**IMPORTANT: Insulation is OPTIONAL - it may or may not be present!**

**REGEX PATTERN:**
[0-9]{{1,2}}"-[A-Z]{{1,4}}-[0-9]{{4}}-[PIPE_CLASS](-[A-Z]{{1,2}})?

**COMPONENT BREAKDOWN (ALL REQUIRED EXCEPT INSULATION):**

1. **LINE SIZE:** [0-9]{{1,2}}" (max 2 digits + quote mark) - **REQUIRED**
   - Examples: 10", 20", 12", 8", 6", 4", 3", 2", 1"
   - MUST have quote mark (") immediately after number
   - Range: 1" to 99"

2. **FLUID CODE:** [A-Z]{{2,4}} (2-4 uppercase letters) - **REQUIRED**
   - Examples: PG, PL, CW, SW, ST, CO, AI, PA, FW, DW
   - Common codes: PG (Process Gas), PL (Process Liquid), CW (Cooling Water)
   - ST (Steam), CO (Condensate), AI (Instrument Air), PA (Plant Air)
   - N2 (Nitrogen), FW (Fire Water), DW (Drinking Water)

3. **SEQUENCE NUMBER:** [0-9]{{4}} (EXACTLY 4 digits) - **REQUIRED**
   - Examples: 0003, 1234, 5678, 0001, 9999
   - MUST be exactly 4 digits (pad with zeros if needed)
   - Range: 0000 to 9999

4. **PIPE CLASS:** - **REQUIRED** - DO NOT CONFUSE WITH INSULATION!
   This is the piping specification/material class. Two formats:
   
   A. **Alphanumeric Format:** [A-Z][0-9][A-Z][0-9]{{2}}
      - Pattern: Letter-Digit-Letter-TwoDigits (5 chars total)
      - Examples: A1B02, B2C03, C3D04, A1B01
      - **EXACTLY 5 characters: Letter + Digit + Letter + 2 Digits**
   
   B. **Numeric Format:** [0-9]{{6}} OR [0-9]{{6}}-[A-Z]
      - Pattern: 6 digits OR 6 digits + dash + 1 LETTER
      - Examples: 011440, 123456, 033842-X, 654321-A
      - **This is 6 or 8 characters total (6 digits + dash + letter)**
      - Note: After dash comes a LETTER (A-Z), not a digit
   
   **CRITICAL WARNING:** The pipe class is COMPLETE as shown above!
   Do NOT take the last character as insulation!
   Insulation comes AFTER pipe class with its own dash separator!

5. **INSULATION CODE:** (-[A-Z]{{1,2}})? - **OPTIONAL (CAN BE ABSENT)**
   - **THIS IS COMPLETELY OPTIONAL - MOST LINES DON'T HAVE IT!**
   - Only present if there's ANOTHER dash AFTER the complete pipe class
   - **ONLY VALID CODES (case insensitive):**
     * H (Heat conservation and process temperature control)
     * PP (Personnel protection)
     * E (Electrical traced line and insulated)
     * FP (Fire protection of piping and equipment)
     * AA (Acoustic insulation)
     * N (No insulation)
   - **CRITICAL:** If you see letters like "x", "X", "A", "B", etc. (NOT in the list above), 
     they are part of the pipe class, NOT insulation!
   - **If not present or not valid, set to empty string ""**

**COMPLETE EXAMPLE FORMATS:**

✅ **WITH INSULATION (insulation is separate component after pipe class):**
- 20"-PG-1234-A1B02-N 
  → size: 20", fluid: PG, seq: 1234, pipe_class: A1B02, insulation: N
- 12"-CW-5678-B2C03-PP 
  → size: 12", fluid: CW, seq: 5678, pipe_class: B2C03, insulation: PP
- 8"-ST-0001-011440-H 
  → size: 8", fluid: ST, seq: 0001, pipe_class: 011440, insulation: H
- 10"-PG-0003-033842-X-H 
  → size: 10", fluid: PG, seq: 0003, pipe_class: 033842-X, insulation: H

✅ **WITHOUT INSULATION (most common case - no insulation component):**
- 20"-PG-1234-A1B02 
  → size: 20", fluid: PG, seq: 1234, pipe_class: A1B02, insulation: ""
- 36"-PG-4403-031441-x 
  → size: 36", fluid: PG, seq: 4403, pipe_class: 031441-x, insulation: ""
- 16"-PG-4105-011441-X 
  → size: 16", fluid: PG, seq: 4105, pipe_class: 011441-X, insulation: ""
- 10"-PG-0003-033842-X 
  → size: 10", fluid: PG, seq: 0003, pipe_class: 033842-X, insulation: ""

⚠️ **CRITICAL:** 
- In "031441-x", the "-x" is part of the pipe class, NOT insulation!
- "x" is NOT a valid insulation code (valid: H, PP, N, AA, E, FP)
- Insulation only appears if there's ANOTHER dash with valid code: "031441-x-H"

**EQUIPMENT TAG DETECTION:**
Identify equipment connection points (FROM/TO) near line numbers:
- Pattern: [LETTER]-[NUMBER] or [LETTER]-[NUMBER][LETTER]
- Examples: V-201, P-101, E-301, T-401, C-201
- Types: V (Vessel), P (Pump), E (Exchanger), T (Tank), C (Compressor), R (Reactor)

**DETECTION RULES:**
1. Line numbers can appear horizontally, vertically, or at angles
2. May have spaces between components (normalize to dashes)
3. Quote mark after size is MANDATORY
4. First 4 components (Size, Fluid, Sequence, Pipe Class) are REQUIRED
5. Insulation (5th component) is OPTIONAL - may or may not be present
6. Extract nearby equipment tags as FROM/TO connections
7. Handle OCR errors: O→0, I→1, S→5, Z→2
8. **DO NOT mistake the last part of pipe class as insulation!**

**OUTPUT JSON STRUCTURE:**
Return a JSON array with objects in this EXACT format:
[
  {{
    "line_number": "complete line number as detected (e.g., 20\"-PG-1234-A1B02 or 20\"-PG-1234-A1B02-N)",
    "size": "pipe size with quote (e.g., 20\")",
    "fluid_code": "2-4 letter code uppercase (e.g., PG)",
    "sequence_no": "exactly 4 digits (e.g., 1234)",
    "pipr_class": "pipe class code - DO NOT include insulation here (e.g., A1B02 or 011440-2)",
    "insulation": "1-2 letter code if present, empty string if not present (e.g., N or \"\")",
    "from_equipment": "source equipment tag if nearby (e.g., V-201) or empty string",
    "to_equipment": "destination equipment tag if nearby (e.g., P-101) or empty string",
    "confidence": "high (all clear) | medium (some OCR artifacts) | low (incomplete)"
  }}
]

**PARSING EXAMPLES (FOLLOW THESE EXACTLY):**

Example 1: "20\"-PG-1234-A1B02-N"
  → size: "20\"", fluid_code: "PG", sequence_no: "1234", pipr_class: "A1B02", insulation: "N"
  (5 components: size, fluid, seq, pipe class, insulation)

Example 2: "10\"-PG-0003-033842-X"
  → size: "10\"", fluid_code: "PG", sequence_no: "0003", pipr_class: "033842-X", insulation: ""
  (4 components: size, fluid, seq, pipe class - NO insulation)

Example 3: "28\"-PG-3212-C3D04"
  → size: "28\"", fluid_code: "PG", sequence_no: "3212", pipr_class: "C3D04", insulation: ""
  (4 components: size, fluid, seq, pipe class - NO insulation)

Example 4: "10\"-PG-0003-033842-X-H"
  → size: "10\"", fluid_code: "PG", sequence_no: "0003", pipr_class: "033842-X", insulation: "H"
  (5 components: size, fluid, seq, pipe class, insulation)

**CRITICAL NOTES:**
- In Example 2: "033842-X" is the COMPLETE pipe class (6 digits + dash + 1 LETTER)
- The "-X" is NOT insulation, it's part of the pipe class (letter suffix)
- Only if there's ANOTHER dash after "033842-X" would there be insulation
- Most lines (80%+) do NOT have insulation - empty string "" is normal

**OCR TEXT TO ANALYZE:**
{extracted_text[:4000]}

**INSTRUCTIONS:**
- Return ONLY valid JSON array - NO markdown, NO code blocks, NO explanations
- Extract EVERY line number found in the text
- Use empty strings "" for missing from_equipment/to_equipment
- Normalize spacing and dashes in line numbers
- Group equipment tags with closest line number by proximity

**RETURN JSON NOW:**"""

        try:
            logger.info("  🤖 Sending to OpenAI for intelligent parsing...")
            response = self.openai_client.chat.completions.create(
                model="gpt-4-turbo-preview",
                messages=[
                    {"role": "system", "content": "You are a P&ID analysis expert. Return only valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=2000
            )
            
            result_text = response.choices[0].message.content.strip()
            
            # Remove markdown code blocks if present
            if result_text.startswith("```"):
                result_text = result_text.split("```")[1]
                if result_text.startswith("json"):
                    result_text = result_text[4:]
                result_text = result_text.strip()
            
            parsed_lines = json.loads(result_text)
            
            # Add page number to each item
            for line in parsed_lines:
                line['page'] = page_num
            
            logger.info(f"  ✅ OpenAI parsed {len(parsed_lines)} line numbers")
            return parsed_lines
            
        except Exception as e:
            logger.error(f"  ❌ OpenAI parsing failed: {e}")
            return self._fallback_regex_parse(extracted_text, page_num)
    
    def _fallback_regex_parse(self, text: str, page_num: int) -> List[Dict]:
        """
        ENHANCED regex parsing for P&ID line numbers
        
        Format: SIZE-FLUID-SEQ-PIPECLASS(-INSULATION)?
        Example: 12-D-5777-033842-N
        
        STRICT VALIDATION:
        - SIZE: 1-2 digits ONLY (6, 12, 20, 24) - NOT 3-4 digits
        - FLUID: 1-2 LETTERS ONLY (D, PG, CW, ST) - MUST be present
        - SEQ: EXACTLY 4 digits (5777, 0003)
        - PIPECLASS: EXACTLY 6 digits (033842, 011441)
        - INSULATION: OPTIONAL - ONLY these codes: H, PP, X, N, E, FP, AA
          * NOT fluid codes like PG, D, CW - those are FLUID not INSULATION
        
        REJECTS:
        - "4003-031441-X" (size 4003 is 4 digits, too long!)
        - Patterns without FLUID code
        - Incomplete patterns
        """
        results = []
        all_matches = []
        
        # Pattern 1: Standard format with hyphens: 12-D-5777-033842-N
        # More flexible with whitespace and optional quote variations
        pattern1 = re.compile(
            r'(?:^|[^0-9])(\d{1,2})\s*["\'"]?\s*[-–—]\s*([A-Z]{1,2})\s*[-–—]\s*(\d{4})\s*[-–—]\s*(\d{6})(?:\s*[-–—]?\s*([A-Za-z]{1,2}))?(?:[^0-9]|$)',
            re.IGNORECASE | re.MULTILINE
        )
        
        # Pattern 2: With quote after size: 12"-D-5777-033842-N or 12" - D - 5777 - 033842
        pattern2 = re.compile(
            r'(?:^|[^0-9])(\d{1,2})\s*["\']\s*[-–—]?\s*([A-Z]{1,2})\s*[-–—]\s*(\d{4})\s*[-–—]\s*(\d{6})(?:\s*[-–—]?\s*([A-Za-z]{1,2}))?(?:[^0-9]|$)',
            re.IGNORECASE | re.MULTILINE
        )
        
        # Pattern 3: Space-separated: 12 D 5777 033842 N (OCR sometimes loses hyphens)
        pattern3 = re.compile(
            r'(?:^|[^0-9])(\d{1,2})\s*["\'"]?\s+([A-Z]{1,2})\s+(\d{4})\s+(\d{6})(?:\s+([A-Za-z0-9]{1,2}))?(?:[^0-9]|$)',
            re.IGNORECASE | re.MULTILINE
        )
        
        # Pattern 4: Mixed separators: 12"-D 5777-033842 (handles OCR inconsistencies)
        pattern4 = re.compile(
            r'(?:^|[^0-9])(\d{1,2})\s*["\'"]?\s*[-–—\s]+([A-Z]{1,2})\s+[-–—\s]?\s*(\d{4})\s*[-–—\s]+(\d{6})(?:\s*[-–—]?\s*([A-Za-z0-9]{1,2}))?(?:[^0-9]|$)',
            re.IGNORECASE | re.MULTILINE
        )
        
        # Pattern 5: With periods (OCR sometimes sees hyphens as periods)
        pattern5 = re.compile(
            r'(?:^|[^0-9])(\d{1,2})\s*["\'"]?\s*[.\s]*([A-Z]{1,2})\s*[.\s]*(\d{4})\s*[.\s]*(\d{6})(?:\s*[.\s]*([A-Za-z0-9]{1,2}))?(?:[^0-9]|$)',
            re.IGNORECASE | re.MULTILINE
        )
        
        # Pattern 6: Very loose spacing (multiple spaces/tabs)
        pattern6 = re.compile(
            r'(?:^|[^0-9])(\d{1,2})\s*["\'"]?\s{1,8}([A-Z]{1,2})\s{1,8}(\d{4})\s{1,8}(\d{6})(?:\s{1,8}([A-Za-z]{1,2}))?(?:[^0-9]|$)',
            re.IGNORECASE | re.MULTILINE
        )
        
        # Pattern 7: With underscores (OCR sometimes sees hyphens as underscores)
        pattern7 = re.compile(
            r'(?:^|[^0-9])(\d{1,2})\s*["\'"]?\s*[_-]\s*([A-Z]{1,2})\s*[_-]\s*(\d{4})\s*[_-]\s*(\d{6})(?:\s*[_-]?\s*([A-Za-z]{1,2}))?(?:[^0-9]|$)',
            re.IGNORECASE | re.MULTILINE
        )
        
        # Pattern 8: Very minimal separators (almost concatenated)
        pattern8 = re.compile(
            r'(?:^|[^0-9])(\d{1,2})["\'"]?\s*([A-Z]{1,2})\s*(\d{4})\s*(\d{6})(?:\s*([A-Za-z]{1,2}))?(?:[^0-9]|$)',
            re.IGNORECASE | re.MULTILINE
        )
        
        # Collect all matches from all patterns
        for pattern_num, pattern in enumerate([pattern1, pattern2, pattern3, pattern4, pattern5, pattern6, pattern7, pattern8], 1):
            matches = pattern.findall(text)
            for match in matches:
                size, fluid, seq, pipr_class, insulation = match
                
                # ULTRA STRICT VALIDATION - ALL 4 COMPONENTS MANDATORY
                
                # 1. SIZE: MUST be 1-2 digits (reject "05", "40", "4003")
                if not size or len(size) > 2 or len(size) < 1:
                    continue
                try:
                    size_int = int(size)
                    if not (1 <= size_int <= 99):
                        continue
                except ValueError:
                    continue
                
                # 2. FLUID: MUST be 1-2 LETTERS only (reject "03", empty, numbers)
                if not fluid or len(fluid) > 2 or len(fluid) < 1:
                    continue
                if not fluid.isalpha():  # Reject if contains numbers
                    continue
                
                # 3. SEQUENCE: MUST be EXACTLY 4 digits (reject "31441" which is 5)
                if not seq or len(seq) != 4:
                    continue
                if not seq.isdigit():
                    continue
                
                # 4. PIPE CLASS: MUST be EXACTLY 6 digits
                if not pipr_class or len(pipr_class) != 6:
                    continue
                if not pipr_class.isdigit():
                    continue
                
                # 5. INSULATION: OPTIONAL - ONLY specific codes (H, PP, X, N, E, FP, AA)
                VALID_INSULATION = {'H', 'PP', 'X', 'N', 'E', 'FP', 'AA', 'h', 'pp', 'x', 'n', 'e', 'fp', 'aa', ''}
                # Strip whitespace from insulation
                insulation = insulation.strip() if insulation else ''
                # If insulation is provided but not in valid list, skip
                if insulation and insulation not in VALID_INSULATION:
                    continue
                
                # Build line number
                line_parts = [f'{size}"-{fluid.upper()}-{seq}-{pipr_class}']
                if insulation:
                    line_parts.append(insulation.upper())
                
                line_number = '-'.join(line_parts)
                
                # Add to matches (will deduplicate later)
                all_matches.append({
                    'line_number': line_number,
                    'size': f'{size}"',
                    'fluid_code': fluid.upper(),
                    'sequence_no': seq,
                    'pipr_class': pipr_class,
                    'insulation': insulation.upper() if insulation else '',
                    'from_equipment': '',
                    'to_equipment': '',
                    'page': page_num,
                    'confidence': 'high' if pattern_num <= 2 else 'medium',
                    'pattern': pattern_num
                })
        
        # Deduplicate by line_number (keep highest confidence)
        seen = {}
        pattern_stats = {}
        
        for match in all_matches:
            line_num = match['line_number']
            pattern_num = match.get('pattern', 0)
            
            # Track pattern distribution
            pattern_stats[pattern_num] = pattern_stats.get(pattern_num, 0) + 1
            
            if line_num not in seen or match['confidence'] == 'high':
                seen[line_num] = match
        
        results = list(seen.values())
        
        # Log pattern distribution
        if pattern_stats:
            logger.info(f"  📊 Pattern distribution: {dict(sorted(pattern_stats.items()))}")
        
        # Remove pattern field before returning
        for item in results:
            item.pop('pattern', None)
        
        logger.info(f"  📝 Regex found {len(results)} unique valid line numbers from {len(all_matches)} total matches")
        return results
    
    def extract_from_pdf(self, pdf_path: str) -> List[Dict]:
        """
        Main extraction method - Process PDF with multi-engine OCR + AI
        HIGH QUALITY MODE: Maximum resolution, all pages, OpenAI + Regex
        
        Args:
            pdf_path: Path to P&ID PDF file
            
        Returns:
            List of extracted line items with all components
        """
        try:
            doc = fitz.open(pdf_path)
            all_line_items = []
            
            logger.info(f"📄 Processing P&ID PDF: {pdf_path}")
            logger.info(f"📄 Total pages: {len(doc)}")
            logger.info(f"🎯 HIGH QUALITY MODE: Processing ALL pages with maximum OCR")
            
            # Process ALL pages for complete extraction
            for page_num in range(len(doc)):
                page = doc[page_num]
                logger.info(f"📄 Processing page {page_num + 1}/{len(doc)}")
                
                # Convert page to HIGH RESOLUTION image for best OCR (2.5x)
                pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5))  # High quality
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                
                # Convert to grayscale for better OCR
                img = img.convert('L')
                
                # Step 1: Extract text with ALL OCR engines
                logger.info("  🔍 Extracting text with Tesseract + EasyOCR + PaddleOCR...")
                ocr_results = self.extract_all_text_from_image(img)
                
                # Step 2: Combine and deduplicate
                combined_text = self.combine_and_deduplicate_text(ocr_results)
                
                # Step 3: Parse with OpenAI + Regex (combined approach)
                logger.info("  🤖 Parsing with OpenAI + Regex (dual approach)...")
                line_items = self.parse_with_openai(combined_text, page_num + 1)
                
                all_line_items.extend(line_items)
                logger.info(f"  ✅ Page {page_num + 1}: Found {len(line_items)} line numbers")
            
            doc.close()
            
            # Deduplicate final results
            unique_items = self._deduplicate_items(all_line_items)
            
            logger.info(f"🎉 TOTAL EXTRACTED: {len(unique_items)} unique line numbers")
            return unique_items
            
        except Exception as e:
            logger.error(f"❌ Error processing PDF: {str(e)}", exc_info=True)
            return []
    
    def _deduplicate_items(self, items: List[Dict]) -> List[Dict]:
        """Remove duplicate line numbers"""
        seen = set()
        unique = []
        
        for item in items:
            key = item.get('line_number', '')
            if key and key not in seen:
                seen.add(key)
                unique.append(item)
        
        return unique
    
    def format_as_table_data(self, line_items: List[Dict]) -> List[Dict]:
        """
        Format extracted line items for frontend table display
        """
        fluid_code_names = {
            'PG': 'Process Gas',
            'PL': 'Process Liquid',
            'CW': 'Cooling Water',
            'SW': 'Sea Water',
            'ST': 'Steam',
            'CO': 'Condensate',
            'AI': 'Instrument Air',
            'PA': 'Plant Air',
            'N2': 'Nitrogen',
            'FW': 'Fire Water',
            'DW': 'Drinking Water',
            'WW': 'Waste Water'
        }
        
        insulation_names = {
            'N': 'None',
            'C': 'Cold',
            'H': 'Hot',
            'P': 'Personnel Protection',
            'A': 'Acoustic'
        }
        
        table_data = []
        for item in line_items:
            fluid_code = item.get('fluid_code', '')
            insulation = item.get('insulation', '')
            
            table_data.append({
                'line_number': item.get('line_number', ''),
                'fluid_code': fluid_code,
                'fluid_description': fluid_code_names.get(fluid_code, 'Unknown'),
                'size': item.get('size', ''),
                'sequence_no': item.get('sequence_no', ''),
                'pipr_class': item.get('pipr_class', ''),
                'insulation': insulation,
                'insulation_description': insulation_names.get(insulation, 'Unknown'),
                'from_equipment': item.get('from_equipment', ''),
                'to_equipment': item.get('to_equipment', ''),
                'page': item.get('page', 1),
                'confidence': item.get('confidence', 'medium')
            })
        
        return table_data
