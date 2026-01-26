"""
P&ID OCR Extractor V2 - Multi-Engine OCR
Uses Tesseract, EasyOCR, PaddleOCR for text extraction + Regex for line parsing
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
import time

logger = logging.getLogger(__name__)


class PIDLineExtractorV2:
    """
    Multi-Engine P&ID line number extractor
    Step 1: Extract ALL text using Tesseract, EasyOCR, PaddleOCR
    Step 2: Parse line numbers using regex patterns from user configuration
    """
    
    def __init__(self):
        self.easyocr_reader = None
        self.paddleocr_reader = None
        self.openai_client = None
        self.fast_mode = getattr(settings, 'PID_OCR_FAST_MODE', True)
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
            # Enable angle classification for vertical text detection
            self.paddleocr_reader = PaddleOCR(
                use_angle_cls=True,  # Enable 180-degree angle classification
                lang='en',
                use_space_char=True,  # Preserve spaces
                show_log=False  # Reduce log spam
            )
            logger.info("✅ PaddleOCR initialized (with vertical text support)")
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
        
        # 1. Tesseract OCR - Multiple PSM modes to detect vertical text
        try:
            t0 = time.time()
            # PSM 6: Assume uniform block of text (horizontal)
            tesseract_text = pytesseract.image_to_string(img, config='--psm 6')
            
            # PSM 5: Single vertical block of text
            try:
                tesseract_vertical = pytesseract.image_to_string(img, config='--psm 5')
                if tesseract_vertical and len(tesseract_vertical.strip()) > 10:
                    tesseract_text += ' ' + tesseract_vertical
                    logger.info(f"  📐 Tesseract vertical text: +{len(tesseract_vertical)} characters")
            except:
                pass
            
            # PSM 11: Sparse text. Find as much text as possible in no particular order
            try:
                tesseract_sparse = pytesseract.image_to_string(img, config='--psm 11')
                if tesseract_sparse and len(tesseract_sparse.strip()) > 10:
                    tesseract_text += ' ' + tesseract_sparse
                    logger.info(f"  🔍 Tesseract sparse text: +{len(tesseract_sparse)} characters")
            except:
                pass
            
            results['tesseract'] = tesseract_text
            logger.info(f"  ✅ Tesseract extracted {len(tesseract_text)} characters (combined) in {time.time() - t0:.2f}s")
        except Exception as e:
            logger.warning(f"  ⚠️ Tesseract failed: {e}")
        
        # 2. EasyOCR - Enable rotation detection for vertical text
        if self.easyocr_reader:
            try:
                t0 = time.time()
                img_array = np.array(img)
                # Basic readtext without rotation_info parameter
                easyocr_result = self.easyocr_reader.readtext(
                    img_array, 
                    detail=0,
                    paragraph=False
                )
                easyocr_text = ' '.join(easyocr_result)
                results['easyocr'] = easyocr_text
                logger.info(f"  ✅ EasyOCR extracted {len(easyocr_text)} characters in {time.time() - t0:.2f}s")
            except Exception as e:
                logger.warning(f"  ⚠️ EasyOCR failed: {e}")
        
        # 3. PaddleOCR
        if self.paddleocr_reader:
            try:
                t0 = time.time()
                img_array = np.array(img)
                # PaddleOCR returns nested list structure
                paddle_result = self.paddleocr_reader.ocr(img_array)
                paddle_texts = []
                
                # Handle different result structures
                if paddle_result:
                    # PaddleOCR returns [[line1_data, line2_data, ...]] or None
                    if isinstance(paddle_result, list) and len(paddle_result) > 0:
                        first_page = paddle_result[0]
                        if first_page and isinstance(first_page, list):
                            for line in first_page:
                                # Each line is [bbox, (text, confidence)]
                                if line and isinstance(line, (list, tuple)) and len(line) >= 2:
                                    text_data = line[1]
                                    if isinstance(text_data, (list, tuple)) and len(text_data) > 0:
                                        paddle_texts.append(str(text_data[0]))
                
                if paddle_texts:
                    paddle_text = ' '.join(paddle_texts)
                    results['paddleocr'] = paddle_text
                    logger.info(f"  ✅ PaddleOCR extracted {len(paddle_text)} characters in {time.time() - t0:.2f}s")
                else:
                    logger.warning(f"  ⚠️ PaddleOCR: No text extracted")
            except Exception as e:
                logger.warning(f"  ⚠️ PaddleOCR failed: {e}")
                import traceback
                logger.debug(f"PaddleOCR traceback: {traceback.format_exc()}")
        
        return results
    
    def combine_and_deduplicate_text(self, ocr_results: Dict[str, str]) -> str:
        """
        🧩 INTELLIGENT TEXT COMBINATION:
        Combine text from all OCR engines smartly
        
        Strategy: Keep ALL text from all engines - don't lose variations!
        Why? Different OCR engines see different things:
        - Tesseract might see "12-D-5777"
        - EasyOCR might see "12 D 5777"  
        - PaddleOCR might see "12.D.5777"
        
        OpenAI is smart enough to recognize these are the same line!
        """
        if not ocr_results:
            return ""
        
        # Combine ALL text with engine labels for debugging
        combined_parts = []
        for engine, text in ocr_results.items():
            if text and text.strip():
                combined_parts.append(text.strip())
        
        combined = '\n\n'.join(combined_parts)
        
        total_chars = sum(len(t) for t in ocr_results.values())
        logger.info(f"  📝 Combined: {total_chars} total characters from {len(ocr_results)} engines")
        logger.info(f"  📝 Final text length: {len(combined)} characters")
        
        return combined

    def _normalize_text(self, text: str, separator: Optional[str] = None, allow_variable: bool = True) -> str:
        """Normalize OCR text by standardizing separators and spacing."""
        normalized = text

        # Remove quotes that often appear after size (e.g., 20")
        normalized = normalized.replace('"', '').replace("'", '')

        # Replace OCR noise and common separators with a standard hyphen
        replace_chars = ['=', '~', '—', '–', '―', '─', '|', '/', '°', '″', '_', '.']
        if allow_variable and separator and separator not in ['-', '']:
            replace_chars.append(separator)

        for char in replace_chars:
            normalized = normalized.replace(char, '-')

        # Collapse multiple hyphens and trim surrounding spaces
        normalized = re.sub(r'-{2,}', '-', normalized)
        normalized = re.sub(r'\s*-\s*', '-', normalized)
        return normalized

    def parse_with_custom_format(self, extracted_text: str, page_num: int, format_config: Dict) -> List[Dict]:
        """
        Parse line numbers using a user-defined component order and patterns.
        """
        logger.info("  🧩 Using CUSTOM line format configuration")

        try:
            components = format_config.get('components') or []
            separator = format_config.get('separator', '-') or '-'
            allow_variable = bool(format_config.get('allowVariableSeparators', True))
        except Exception:
            logger.warning("  ⚠️ Invalid format config; falling back to default regex")
            return self.parse_with_regex(extracted_text, page_num)

        if not components:
            logger.warning("  ⚠️ Empty format config; falling back to default regex")
            return self.parse_with_regex(extracted_text, page_num)

        # Sort components by order
        ordered_components = sorted(components, key=lambda c: c.get('order', 0))

        # Default component patterns (max digits/letters per requirement)
        default_patterns = {
            'line_size': r'\d{1,2}',
            'area': r'\d{2,3}',
            'fluid_code': r'[A-Z]{1,2}',
            'sequence_no': r'\d{3,5}',
            'pipe_class': r'\d{3,6}[0-9][A-Z]',
            'insulation': r'[A-Z]{1,2}',
        }
        relaxed_patterns = {
            **default_patterns,
            'pipe_class': r'\d{3,6}'
        }

        # Build regex with ordered capture groups
        group_patterns = []
        component_ids = []
        component_defs = []
        for comp in ordered_components:
            comp_id = comp.get('id')
            if not comp_id:
                continue
            pattern = comp.get('pattern') or default_patterns.get(comp_id)
            if not pattern:
                continue
            group_patterns.append(f"({pattern})")
            component_ids.append(comp_id)
            component_defs.append(comp)

        if not group_patterns:
            logger.warning("  ⚠️ No valid components in format config; falling back to default regex")
            return self.parse_with_regex(extracted_text, page_num)

        # Normalize text if variable separators are allowed
        normalized_text = self._normalize_text(extracted_text, separator, allow_variable=allow_variable)

        if allow_variable:
            sep_pattern = r'\s*[-–—_./]*\s*'
        else:
            sep_pattern = rf'\s*{re.escape(separator)}\s*'

        pattern = re.compile(
            r'(?<![A-Z0-9])' + sep_pattern.join(group_patterns) + r'(?![A-Z0-9])',
            re.IGNORECASE
        )

        found_lines = []
        seen_lines = set()

        for match in pattern.finditer(normalized_text):
            component_values = {}
            valid = True

            for idx, comp_id in enumerate(component_ids, 1):
                raw_value = match.group(idx).strip()
                if not raw_value:
                    valid = False
                    break

                expected_pattern = default_patterns.get(comp_id)
                override_pattern = component_defs[idx - 1].get('pattern')
                validate_pattern = override_pattern or expected_pattern
                if validate_pattern and not re.fullmatch(validate_pattern, raw_value, flags=re.IGNORECASE):
                    valid = False
                    break

                # Normalize casing
                if comp_id in ['fluid_code', 'insulation', 'pipe_class']:
                    raw_value = raw_value.upper()

                component_values[comp_id] = raw_value

            if not valid:
                continue

            # Build line_number using configured order
            line_parts = [component_values.get(cid, '') for cid in component_ids]
            line_number = '-'.join([p for p in line_parts if p])

            if line_number in seen_lines:
                continue
            seen_lines.add(line_number)

            line_entry = {
                'line_number': line_number,
                'size': f"{component_values.get('line_size', '')}\"" if component_values.get('line_size') else '',
                'area': component_values.get('area', ''),
                'fluid_code': component_values.get('fluid_code', ''),
                'sequence_no': component_values.get('sequence_no', ''),
                'pipr_class': component_values.get('pipe_class', ''),
                'insulation': component_values.get('insulation', ''),
                'page': page_num,
                'from_equipment': '',
                'to_equipment': '',
                'extraction_method': 'custom_format',
                'original_detection': match.group(0).strip()
            }

            found_lines.append(line_entry)

        # Relaxed pass for pipe_class if strict found nothing
        if not found_lines:
            logger.info("  🧩 CUSTOM format strict=0; trying relaxed pipe_class")
            relaxed_group_patterns = []
            for comp in ordered_components:
                comp_id = comp.get('id')
                if not comp_id:
                    continue
                pattern_override = comp.get('pattern')
                pattern_relaxed = pattern_override or relaxed_patterns.get(comp_id)
                if not pattern_relaxed:
                    continue
                relaxed_group_patterns.append(f"({pattern_relaxed})")

            if relaxed_group_patterns:
                relaxed_pattern = re.compile(
                    r'(?<![A-Z0-9])' + sep_pattern.join(relaxed_group_patterns) + r'(?![A-Z0-9])',
                    re.IGNORECASE
                )

                for match in relaxed_pattern.finditer(normalized_text):
                    component_values = {}
                    valid = True

                    for idx, comp_id in enumerate(component_ids, 1):
                        raw_value = match.group(idx).strip()
                        if not raw_value:
                            valid = False
                            break

                        expected_pattern = relaxed_patterns.get(comp_id)
                        override_pattern = component_defs[idx - 1].get('pattern')
                        if comp_id == 'pipe_class':
                            validate_pattern = expected_pattern
                        else:
                            validate_pattern = override_pattern or expected_pattern
                        if validate_pattern and not re.fullmatch(validate_pattern, raw_value, flags=re.IGNORECASE):
                            valid = False
                            break

                        if comp_id in ['fluid_code', 'insulation', 'pipe_class']:
                            raw_value = raw_value.upper()

                        component_values[comp_id] = raw_value

                    if not valid:
                        continue

                    line_parts = [component_values.get(cid, '') for cid in component_ids]
                    line_number = '-'.join([p for p in line_parts if p])

                    if line_number in seen_lines:
                        continue
                    seen_lines.add(line_number)

                    found_lines.append({
                        'line_number': line_number,
                        'size': f"{component_values.get('line_size', '')}\"" if component_values.get('line_size') else '',
                        'area': component_values.get('area', ''),
                        'fluid_code': component_values.get('fluid_code', ''),
                        'sequence_no': component_values.get('sequence_no', ''),
                        'pipr_class': component_values.get('pipe_class', ''),
                        'insulation': component_values.get('insulation', ''),
                        'page': page_num,
                        'from_equipment': '',
                        'to_equipment': '',
                        'extraction_method': 'custom_format_relaxed',
                        'original_detection': match.group(0).strip()
                    })

        # Fallback: token-based matching if strict regex found nothing
        if not found_lines:
            logger.info("  🧩 CUSTOM format fallback: token-based matching")
            tokens = re.findall(r'[A-Z0-9]+', normalized_text.upper())
            window_size = len(component_ids)

            for i in range(0, max(0, len(tokens) - window_size + 1)):
                window = tokens[i:i + window_size]
                component_values = {}
                valid = True

                for idx, comp_id in enumerate(component_ids):
                    token = window[idx]
                    expected_pattern = relaxed_patterns.get(comp_id)
                    override_pattern = component_defs[idx].get('pattern')
                    if comp_id == 'pipe_class':
                        validate_pattern = expected_pattern
                    else:
                        validate_pattern = override_pattern or expected_pattern

                    if not validate_pattern:
                        valid = False
                        break

                    if not re.fullmatch(validate_pattern, token, flags=re.IGNORECASE):
                        valid = False
                        break

                    if comp_id in ['fluid_code', 'insulation', 'pipe_class']:
                        token = token.upper()

                    component_values[comp_id] = token

                if not valid:
                    continue

                line_parts = [component_values.get(cid, '') for cid in component_ids]
                line_number = '-'.join([p for p in line_parts if p])

                if line_number in seen_lines:
                    continue
                seen_lines.add(line_number)

                found_lines.append({
                    'line_number': line_number,
                    'size': f"{component_values.get('line_size', '')}\"" if component_values.get('line_size') else '',
                    'area': component_values.get('area', ''),
                    'fluid_code': component_values.get('fluid_code', ''),
                    'sequence_no': component_values.get('sequence_no', ''),
                    'pipr_class': component_values.get('pipe_class', ''),
                    'insulation': component_values.get('insulation', ''),
                    'page': page_num,
                    'from_equipment': '',
                    'to_equipment': '',
                    'extraction_method': 'custom_format_tokens',
                    'original_detection': ' '.join(window)
                })

        # Fallback: token-based matching with optional AREA (if enabled)
        if not found_lines and 'area' in component_ids:
            logger.info("  🧩 CUSTOM format fallback: token-based with optional AREA")
            tokens = re.findall(r'[A-Z0-9]+', normalized_text.upper())

            reduced_ids = [cid for cid in component_ids if cid != 'area']
            reduced_size = len(reduced_ids)

            for i in range(0, max(0, len(tokens) - reduced_size + 1)):
                window = tokens[i:i + reduced_size]
                component_values = {}
                valid = True

                # Try to pick area from the token immediately before the window
                area_value = ''
                if i - 1 >= 0:
                    candidate_area = tokens[i - 1]
                    if re.fullmatch(relaxed_patterns.get('area', r'\d{2,3}'), candidate_area):
                        area_value = candidate_area

                for idx, comp_id in enumerate(reduced_ids):
                    token = window[idx]
                    expected_pattern = relaxed_patterns.get(comp_id)
                    override_pattern = component_defs[idx].get('pattern') if idx < len(component_defs) else None
                    if comp_id == 'pipe_class':
                        validate_pattern = expected_pattern
                    else:
                        validate_pattern = override_pattern or expected_pattern

                    if not validate_pattern:
                        valid = False
                        break

                    if not re.fullmatch(validate_pattern, token, flags=re.IGNORECASE):
                        valid = False
                        break

                    if comp_id in ['fluid_code', 'insulation', 'pipe_class']:
                        token = token.upper()

                    component_values[comp_id] = token

                if not valid:
                    continue

                if area_value:
                    component_values['area'] = area_value

                line_parts = [component_values.get(cid, '') for cid in component_ids]
                line_number = '-'.join([p for p in line_parts if p])

                if line_number in seen_lines:
                    continue
                seen_lines.add(line_number)

                found_lines.append({
                    'line_number': line_number,
                    'size': f"{component_values.get('line_size', '')}\"" if component_values.get('line_size') else '',
                    'area': component_values.get('area', ''),
                    'fluid_code': component_values.get('fluid_code', ''),
                    'sequence_no': component_values.get('sequence_no', ''),
                    'pipr_class': component_values.get('pipe_class', ''),
                    'insulation': component_values.get('insulation', ''),
                    'page': page_num,
                    'from_equipment': '',
                    'to_equipment': '',
                    'extraction_method': 'custom_format_tokens_area',
                    'original_detection': ' '.join(window)
                })

        # Fallback: ignore AREA if configured but not present
        if not found_lines and 'area' in component_ids:
            logger.info("  🧩 CUSTOM format fallback: ignore AREA")
            reduced_components = [c for c in ordered_components if c.get('id') != 'area']
            reduced_group_patterns = []
            reduced_component_ids = []
            reduced_component_defs = []

            for comp in reduced_components:
                comp_id = comp.get('id')
                if not comp_id:
                    continue
                pattern_override = comp.get('pattern')
                pattern_relaxed = pattern_override or relaxed_patterns.get(comp_id)
                if not pattern_relaxed:
                    continue
                reduced_group_patterns.append(f"({pattern_relaxed})")
                reduced_component_ids.append(comp_id)
                reduced_component_defs.append(comp)

            if reduced_group_patterns:
                reduced_pattern = re.compile(
                    r'(?<![A-Z0-9])' + sep_pattern.join(reduced_group_patterns) + r'(?![A-Z0-9])',
                    re.IGNORECASE
                )

                for match in reduced_pattern.finditer(normalized_text):
                    component_values = {}
                    valid = True

                    for idx, comp_id in enumerate(reduced_component_ids, 1):
                        raw_value = match.group(idx).strip()
                        if not raw_value:
                            valid = False
                            break

                        expected_pattern = relaxed_patterns.get(comp_id)
                        override_pattern = reduced_component_defs[idx - 1].get('pattern')
                        if comp_id == 'pipe_class':
                            validate_pattern = expected_pattern
                        else:
                            validate_pattern = override_pattern or expected_pattern
                        if validate_pattern and not re.fullmatch(validate_pattern, raw_value, flags=re.IGNORECASE):
                            valid = False
                            break

                        if comp_id in ['fluid_code', 'insulation', 'pipe_class']:
                            raw_value = raw_value.upper()

                        component_values[comp_id] = raw_value

                    if not valid:
                        continue

                    line_parts = [component_values.get(cid, '') for cid in reduced_component_ids]
                    line_number = '-'.join([p for p in line_parts if p])

                    if line_number in seen_lines:
                        continue
                    seen_lines.add(line_number)

                    found_lines.append({
                        'line_number': line_number,
                        'size': f"{component_values.get('line_size', '')}\"" if component_values.get('line_size') else '',
                        'area': '',
                        'fluid_code': component_values.get('fluid_code', ''),
                        'sequence_no': component_values.get('sequence_no', ''),
                        'pipr_class': component_values.get('pipe_class', ''),
                        'insulation': component_values.get('insulation', ''),
                        'page': page_num,
                        'from_equipment': '',
                        'to_equipment': '',
                        'extraction_method': 'custom_format_no_area',
                        'original_detection': match.group(0).strip()
                    })

        logger.info(f"  🧩 CUSTOM format found {len(found_lines)} unique line numbers")
        return found_lines
    
    def parse_with_regex(self, extracted_text: str, page_num: int) -> List[Dict]:
        """
        🎯 RELIABLE REGEX-BASED APPROACH:
        Use pure Python regex to find line number patterns directly in OCR text.
        
        Line format: SIZE-FLUID-SEQUENCE-PIPECLASS(-INSULATION)?
        Example: 12-D-5777-033842-N
        
        This is MORE RELIABLE than OpenAI because:
        - Deterministic pattern matching
        - No API failures or hallucinations
        - Faster processing
        - Consistent results
        """
        logger.info("  🔍 Using REGEX pattern matching on OCR text")
        
        # First, normalize the text - replace all dash-like characters with standard hyphen
        # OCR often sees: = ~ — – ― ─ | / as separators
        normalized_text = extracted_text
        for char in ['=', '~', '—', '–', '―', '─', '|', '/', '°', '″', '\'', '"']:
            normalized_text = normalized_text.replace(char, '-')
        
        # Remove multiple consecutive hyphens (replace with single hyphen)
        normalized_text = re.sub(r'-{2,}', '-', normalized_text)
        
        # Remove extra spaces around hyphens
        normalized_text = re.sub(r'\s+-\s+', '-', normalized_text)
        
        # Add spaces around hyphens for better word boundary detection
        normalized_text_spaced = normalized_text.replace('-', ' - ')
        
        logger.info(f"  📝 Normalized text sample (first 500 chars): {normalized_text[:500]}")
        
        # SMART FLEXIBLE REGEX PATTERNS
        # Format: SIZE-FLUID-SEQUENCE-PIPECLASS(-INSULATION)?
        # Examples: 6-VG-4952-011505-X, 16-PG-4005-011441-X, 6-PG-5143-031440
        
        patterns = [
            # Pattern 1: Standard with word boundaries (most reliable)
            r'\b(\d{1,2})\s*-\s*([A-Z]{1,2})\s*-\s*(\d{3,5})\s*-\s*(\d{3,6}[0-9][A-Z])(?:\s*-\s*([A-Z]{1,2}))?\b',
            
            # Pattern 2: With optional quote after size
            r'\b(\d{1,2})-?\s*-\s*([A-Z]{1,2})\s*-\s*(\d{3,5})\s*-\s*(\d{3,6}[0-9][A-Z])(?:\s*-\s*([A-Z]{1,2}))?\b',
            
            # Pattern 3: More lenient spacing
            r'(?:^|\s)(\d{1,2})\s*-+\s*([A-Z]{1,2})\s*-+\s*(\d{3,5})\s*-+\s*(\d{3,6}[0-9][A-Z])(?:\s*-+\s*([A-Z]{1,2}))?(?:\s|$|[-,.])',
            
            # Pattern 4: Compact (no spaces at all)
            r'\b(\d{1,2})-([A-Z]{1,2})-(\d{3,5})-(\d{3,6}[0-9][A-Z])(?:-([A-Z]{1,2}))?\b',
            
            # Pattern 5: With flexible separators (space or hyphen)
            r'\b(\d{1,2})[\s-]+([A-Z]{1,2})[\s-]+(\d{3,5})[\s-]+(\d{3,6}[0-9][A-Z])(?:[\s-]+([A-Z]{1,2}))?\b',
            
            # Pattern 6: Case insensitive with word boundaries
            r'(?:^|\s)(\d{1,2})\s*-\s*([A-Za-z]{1,2})\s*-\s*(\d{3,5})\s*-\s*(\d{3,6}[0-9][A-Z])(?:\s*-\s*([A-Za-z]{1,2}))?(?=\s|$|-)',
        ]
        
        found_lines = []
        seen_lines = set()
        rejected = []
        
        for pattern_idx, pattern in enumerate(patterns, 1):
            matches = re.finditer(pattern, normalized_text, re.IGNORECASE)
            
            for match in matches:
                # Extract and clean components
                size = match.group(1).strip()
                fluid = match.group(2).upper().strip()
                sequence = match.group(3).strip()
                pipe_class = match.group(4).strip()
                insulation = match.group(5).upper().strip() if match.group(5) else ''
                
                # Smart cleaning: remove any non-alphanumeric from edges
                size = re.sub(r'[^0-9]', '', size)
                fluid = re.sub(r'[^A-Z]', '', fluid)
                sequence = re.sub(r'[^0-9]', '', sequence)
                pipe_class = re.sub(r'[^0-9A-Z]', '', pipe_class).upper()
                if insulation:
                    insulation = re.sub(r'[^A-Z]', '', insulation)
                
                # Validate components (more lenient)
                # Size: 1-2 digits (common sizes: 1, 2, 3, 4, 6, 8, 10, 12, 16, 20, 24, 30, 36)
                if not size or not size.isdigit() or len(size) > 2:
                    rejected.append(f"Invalid size: {size}")
                    continue
                
                # Fluid: 1-2 letters only (common: D, PG, VG, CW, ST, PC, PO, etc.)
                if not fluid or not fluid.isalpha() or len(fluid) > 2:
                    rejected.append(f"Invalid fluid: {fluid}")
                    continue
                
                # Sequence: 3-5 digits
                if len(sequence) < 3 or len(sequence) > 5 or not sequence.isdigit():
                    rejected.append(f"Invalid sequence: {sequence} (need 3-5 digits)")
                    continue
                
                # Pipe class: 3-6 digits followed by digit+letter
                if not re.fullmatch(r'\d{3,6}[0-9][A-Z]', pipe_class):
                    rejected.append(f"Invalid pipe_class: {pipe_class} (need 3-6 digits + digit+letter)")
                    continue
                
                # Insulation: optional, 1-2 letters if present
                if insulation and (not insulation.isalpha() or len(insulation) > 2):
                    rejected.append(f"Invalid insulation: {insulation}")
                    continue
                
                # Build line number
                line_number = f"{size}-{fluid}-{sequence}-{pipe_class}"
                if insulation:
                    line_number += f"-{insulation}"
                
                # Deduplicate
                if line_number in seen_lines:
                    continue
                seen_lines.add(line_number)
                
                # Create line entry
                line_entry = {
                    'line_number': line_number,
                    'size': f'{size}"',
                    'fluid_code': fluid,
                    'sequence_no': sequence,
                    'pipr_class': pipe_class,
                    'insulation': insulation,
                    'page': page_num,
                    'from_equipment': '',
                    'to_equipment': '',
                    'extraction_method': 'regex_direct',
                    'original_detection': match.group(0).strip()
                }
                
                found_lines.append(line_entry)

        if not found_lines:
            logger.info("  🎯 REGEX strict=0; trying relaxed pipe_class")
            relaxed_patterns = [
                r'\b(\d{1,2})\s*-\s*([A-Z]{1,2})\s*-\s*(\d{3,5})\s*-\s*(\d{3,6})(?:\s*-\s*([A-Z]{1,2}))?\b',
                r'\b(\d{1,2})-?\s*-\s*([A-Z]{1,2})\s*-\s*(\d{3,5})\s*-\s*(\d{3,6})(?:\s*-\s*([A-Z]{1,2}))?\b',
                r'(?:^|\s)(\d{1,2})\s*-+\s*([A-Z]{1,2})\s*-+\s*(\d{3,5})\s*-+\s*(\d{3,6})(?:\s*-+\s*([A-Z]{1,2}))?(?:\s|$|[-,.])',
                r'\b(\d{1,2})-([A-Z]{1,2})-(\d{3,5})-(\d{3,6})(?:-([A-Z]{1,2}))?\b',
                r'\b(\d{1,2})[\s-]+([A-Z]{1,2})[\s-]+(\d{3,5})[\s-]+(\d{3,6})(?:[\s-]+([A-Z]{1,2}))?\b',
                r'(?:^|\s)(\d{1,2})\s*-\s*([A-Za-z]{1,2})\s*-\s*(\d{3,5})\s*-\s*(\d{3,6})(?:\s*-\s*([A-Za-z]{1,2}))?(?=\s|$|-)',
            ]

            for pattern_idx, pattern in enumerate(relaxed_patterns, 1):
                matches = re.finditer(pattern, normalized_text, re.IGNORECASE)

                for match in matches:
                    size = match.group(1).strip()
                    fluid = match.group(2).upper().strip()
                    sequence = match.group(3).strip()
                    pipe_class = match.group(4).strip()
                    insulation = match.group(5).upper().strip() if match.group(5) else ''

                    size = re.sub(r'[^0-9]', '', size)
                    fluid = re.sub(r'[^A-Z]', '', fluid)
                    sequence = re.sub(r'[^0-9]', '', sequence)
                    pipe_class = re.sub(r'[^0-9A-Z]', '', pipe_class).upper()
                    if insulation:
                        insulation = re.sub(r'[^A-Z]', '', insulation)

                    if not size or not size.isdigit() or len(size) > 2:
                        continue
                    if not fluid or not fluid.isalpha() or len(fluid) > 2:
                        continue
                    if len(sequence) < 3 or len(sequence) > 5 or not sequence.isdigit():
                        continue
                    if not re.fullmatch(r'\d{3,6}', pipe_class):
                        continue
                    if insulation and (not insulation.isalpha() or len(insulation) > 2):
                        continue

                    line_number = f"{size}-{fluid}-{sequence}-{pipe_class}"
                    if insulation:
                        line_number += f"-{insulation}"

                    if line_number in seen_lines:
                        continue
                    seen_lines.add(line_number)

                    found_lines.append({
                        'line_number': line_number,
                        'size': f'{size}"',
                        'fluid_code': fluid,
                        'sequence_no': sequence,
                        'pipr_class': pipe_class,
                        'insulation': insulation,
                        'page': page_num,
                        'from_equipment': '',
                        'to_equipment': '',
                        'extraction_method': 'regex_relaxed',
                        'original_detection': match.group(0).strip()
                    })
        
        # Log summary with debugging info
        if rejected and len(rejected) <= 20:
            logger.info(f"  ⚠️ Rejected {len(rejected)} potential matches: {rejected[:10]}")
        elif rejected:
            logger.info(f"  ⚠️ Rejected {len(rejected)} potential matches (showing first 10): {rejected[:10]}")
        
        logger.info(f"  🎯 REGEX found {len(found_lines)} unique line numbers from {len(patterns)} patterns")
        return found_lines
    
    def parse_with_openai(self, extracted_text: str, page_num: int) -> List[Dict]:
        """
        DEPRECATED: OpenAI is unreliable, use parse_with_regex instead
        """
        logger.warning("  ⚠️ OpenAI method is deprecated, using REGEX instead")
        return self.parse_with_regex(extracted_text, page_num)
        
        # Use full text for maximum extraction
        text_chunk = extracted_text[:12000]  # Increased from 8000
        
        prompt = f"""🎯 MISSION: Extract ALL P&ID line numbers from OCR text (may be messy!)

📋 **LINE FORMAT:** SIZE-FLUID-SEQUENCE-PIPECLASS(-INSULATION)?

🔍 **SEARCH STRATEGY:**
1. Look for patterns with ALL 4 mandatory components
2. Accept ANY separator: hyphens, spaces, periods, underscores, or mixed
3. Ignore extra whitespace, quotes, or OCR noise
4. Extract variations like:
   - "12-D-5777-033842-N" (standard)
   - "12 D 5777 033842 N" (spaces)
   - "12"-D-5777-033842" (with quote)
   - "12.D.5777.033842.N" (periods)
   - "12  D  5777  033842" (multiple spaces)
   - "12_D_5777_033842_N" (underscores)
   - Even "12D 5777033842N" (minimal separators)

✅ **MANDATORY COMPONENTS (ALL 4 REQUIRED):**
- **SIZE:** 1-2 digits ONLY (6, 8, 10, 12, 16, 20, 24, etc.)
  ❌ REJECT: 05 (leading zero invalid), 4003 (3-4 digits invalid)
  
- **FLUID:** 1-2 LETTERS ONLY (D, PG, CW, ST, W, etc.)
  ❌ REJECT: Numbers like 03, or missing entirely
  
- **SEQUENCE:** EXACTLY 4 digits (0001, 5777, 1234, 9999)
  ❌ REJECT: 011441 (6 digits), 31441 (5 digits), 123 (3 digits)
  
- **PIPECLASS:** 5 OR 6 digits (01701, 033842, 011441, 11440, 123456)
  ✅ ACCEPT: Both 5-digit (01701, 11440) and 6-digit (033842, 011441)
  ❌ REJECT: Wrong length (1-4 or 7+ digits)

🎨 **OPTIONAL (5th component):**
- **INSULATION:** ONLY these codes: H, PP, X, N, E, FP, AA
  ❌ NOT fluid codes (PG, D, CW are FLUID not insulation!)

✅ **VALID EXAMPLES:**
"12-D-5777-033842-N" → Extract as: size:12, fluid:D, seq:5777, class:033842, insul:N
"16 PG 4105 011441 X" → Extract as: size:16, fluid:PG, seq:4105, class:011441, insul:X
"10.PG.0003.033842" → Extract as: size:10, fluid:PG, seq:0003, class:033842, insul:""
"4-D-6013-01701" → Extract as: size:4, fluid:D, seq:6013, class:01701 (5 digits OK!), insul:""
"24-CW-1234-123456-H" → Extract as: size:24, fluid:CW, seq:1234, class:123456, insul:H
"8  ST  9999  11440  FP" → Extract as: size:8, fluid:ST, seq:9999, class:11440 (5 digits), insul:FP

❌ **INVALID - MUST REJECT:**
"05-011441-X" → MISSING FLUID+SEQUENCE (only 3 components)
"4003-031441-X" → SIZE wrong (4 digits), SEQUENCE wrong (5 digits)
"40-03-31441" → FLUID is number (invalid), SEQUENCE 5 digits
"12-1234-123456" → MISSING FLUID (only 3 components)
"PG-5777-033842" → MISSING SIZE (only 3 components)

📤 **OUTPUT FORMAT (JSON only):**
[
  {{
    "line_number": "12-D-5777-033842-N",
    "size": "12",
    "fluid_code": "D",
    "sequence_no": "5777",
    "pipr_class": "033842",
    "insulation": "N",
    "from_equipment": "",
    "to_equipment": "",
    "confidence": "high"
  }}
]

**CRITICAL OUTPUT RULES:**
1. "size" - NUMBERS ONLY (no quotes): "12" not "12\""
2. "pipr_class" - NUMBERS ONLY: "033842" not "033842-X" or "01701+YN"
3. "insulation" - SINGLE CODE ONLY: "N" not "X-N" or "+YN"
4. If you see "033842-X-N", split it: pipr_class="033842", insulation="X"
5. If you see "01701+YN", split it: pipr_class="01701", insulation="N"

🔥 **CRITICAL INSTRUCTIONS:**
1. ONLY extract line numbers that are ACTUALLY PRESENT in the text
2. DO NOT make up, guess, or invent any line numbers
3. DO NOT extrapolate or create patterns
4. If you're unsure, DON'T extract it
5. Better to extract ZERO lines than extract FAKE lines
6. Return EMPTY array [] if you don't see clear line numbers

Extract ALL line numbers now! 🚀"""

        try:
            response = self.openai_client.chat.completions.create(
                model="gpt-4-turbo-preview",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a strict P&ID line number extractor. You ONLY extract text that is clearly visible. You NEVER hallucinate, guess, or make up data. If unsure, return empty array."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.1,  # Very low creativity but not zero - allows pattern recognition
                max_tokens=4096  # Increased for more extractions
            )
            
            result_text = response.choices[0].message.content.strip()
            
            # Clean markdown code blocks
            if '```' in result_text:
                parts = result_text.split('```')
                for part in parts:
                    if part.strip().startswith('json') or part.strip().startswith('['):
                        result_text = part.replace('json', '').strip()
                        break
            
            parsed_lines = json.loads(result_text)
            
            # CRITICAL: Validate each extraction strictly
            valid_lines = []
            rejected = []
            
            for line in parsed_lines:
                # Extract components
                size = str(line.get('size', '')).replace('"', '').strip()
                fluid = str(line.get('fluid_code', '')).strip().upper()
                seq = str(line.get('sequence_no', '')).strip()
                pipr_class = str(line.get('pipr_class', '')).strip()
                insul = str(line.get('insulation', '')).strip().upper()
                
                # SMART CLEANING: Handle merged components
                # Sometimes OCR merges pipe_class with insulation like "033842-X-N" or "01701+YN"
                if pipr_class and not pipr_class.isdigit():
                    # Extract only the numeric part from beginning
                    import re
                    match = re.match(r'^(\d+)', pipr_class)
                    if match:
                        clean_pipr = match.group(1)
                        # Extract insulation from the rest
                        remainder = pipr_class[len(clean_pipr):].strip('-+_. ')
                        if remainder and remainder.replace('-', '').replace('+', '').isalpha():
                            # Found insulation in pipe class
                            if not insul or insul == remainder[:2].upper():
                                insul = remainder[:2].upper() if len(remainder) >= 2 else remainder.upper()
                        pipr_class = clean_pipr
                
                # STRICT VALIDATION
                try:
                    # Size: 1-2 digits ONLY
                    if not size or not size.isdigit() or len(size) > 2 or int(size) < 1 or int(size) > 99:
                        rejected.append(f"{line.get('line_number', 'N/A')} - Invalid size: {size}")
                        continue
                    
                    # Fluid: 1-2 LETTERS ONLY
                    if not fluid or not fluid.isalpha() or len(fluid) > 2:
                        rejected.append(f"{line.get('line_number', 'N/A')} - Invalid fluid: {fluid}")
                        continue
                    
                    # Sequence: EXACTLY 4 digits
                    if not seq or not seq.isdigit() or len(seq) != 4:
                        rejected.append(f"{line.get('line_number', 'N/A')} - Invalid sequence: {seq}")
                        continue
                    
                    # Pipe Class: 5 OR 6 digits (real PDFs have both!)
                    if not pipr_class or not pipr_class.isdigit() or len(pipr_class) not in [5, 6]:
                        rejected.append(f"{line.get('line_number', 'N/A')} - Invalid pipe class: {pipr_class}")
                        continue
                    
                    # Insulation: OPTIONAL but must be 1-2 letters if present
                    if insul and (not insul.isalpha() or len(insul) > 2):
                        rejected.append(f"{line.get('line_number', 'N/A')} - Invalid insulation: {insul}")
                        continue
                    
                    # Update with cleaned values
                    line['size'] = size + '"'
                    line['fluid_code'] = fluid
                    line['sequence_no'] = seq
                    line['pipr_class'] = pipr_class
                    line['insulation'] = insul
                    line['line_number'] = f"{size}-{fluid}-{seq}-{pipr_class}{'-' + insul if insul else ''}"
                    line['page'] = page_num
                    line['extraction_method'] = 'openai_intelligent'
                    
                    valid_lines.append(line)
                    
                except Exception as e:
                    rejected.append(f"{line.get('line_number', 'N/A')} - Validation error: {e}")
            
            if rejected:
                logger.info(f"  ⚠️ Rejected {len(rejected)} invalid extractions")
                for r in rejected[:5]:  # Log first 5
                    logger.info(f"    ❌ {r}")
            
            logger.info(f"  🧠 OpenAI extracted {len(valid_lines)} VALID line numbers (rejected {len(rejected)})")
            return valid_lines
            
        except json.JSONDecodeError as e:
            logger.error(f"  ❌ OpenAI returned invalid JSON: {e}")
            logger.error(f"  Response was: {result_text[:200]}...")
            return []
        except Exception as e:
            logger.error(f"  ❌ OpenAI failed: {e}")
            return []
        
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
    
    def extract_from_pdf(self, pdf_path: str, line_format_config: Optional[Dict] = None) -> List[Dict]:
        """
        P&ID Line Number Extraction using OCR + Regex
        
        PHASE 1: COMPREHENSIVE TEXT EXTRACTION
        - Tesseract OCR: Fast and reliable
        - EasyOCR: Good with varied fonts
        - PaddleOCR: Excellent with Asian characters and complex layouts
        - ALL THREE combined = Maximum text coverage
        
        PHASE 2: REGEX PARSING
        - Uses line_format_config patterns provided by user
        - Extracts only actual text from OCR
        - No AI generation or assumptions
        
        PHASE 3: VALIDATION
        - Validates components match user's format
        - Returns only what was actually detected
        
        Args:
            pdf_path: Path to P&ID PDF file
            line_format_config: User's line number format configuration
            
        Returns:
            List of extracted line items with actual OCR data only
        """
        try:
            doc = fitz.open(pdf_path)
            all_line_items = []
            
            logger.info(f"🚀 STARTING P&ID EXTRACTION")
            logger.info(f"📄 File: {pdf_path}")
            logger.info(f"📄 Pages: {len(doc)}")
            logger.info(f"🔧 Strategy: Multi-Engine OCR → Regex Parsing → Validation")
            
            for page_num in range(len(doc)):
                page = doc[page_num]
                logger.info(f"\n{'='*60}")
                logger.info(f"📄 PAGE {page_num + 1}/{len(doc)}")
                logger.info(f"{'='*60}")
                
                # FAST PATH: Try vector/text layer first (much faster than OCR)
                try:
                    pdf_text = page.get_text("text") or ""
                except Exception:
                    pdf_text = ""

                if pdf_text and len(pdf_text.strip()) >= 50:
                    logger.info(f"⚡ Using PDF text layer on page {page_num + 1} (len={len(pdf_text)})")
                    logger.info(f"🧪 PDF text sample (first 800 chars): {pdf_text[:800]}")
                    logger.info(f"🧪 PDF text sample (last 800 chars): {pdf_text[-800:] if len(pdf_text) > 800 else pdf_text}")
                    ocr_results = {'pdf_text': pdf_text}
                    combined_text = self.combine_and_deduplicate_text(ocr_results)

                    logger.info("🔍 PHASE 2: REGEX Pattern Recognition (PDF text layer)")
                    logger.info(f"  📝 PDF Text Sample (first 500 chars): {combined_text[:500]}")
                    if line_format_config:
                        line_items = self.parse_with_custom_format(combined_text, page_num + 1, line_format_config)
                        if not line_items:
                            logger.info("🔁 CUSTOM format yielded 0, falling back to REGEX")
                            line_items = self.parse_with_regex(combined_text, page_num + 1)
                    else:
                        line_items = self.parse_with_regex(combined_text, page_num + 1)

                    if line_items:
                        all_line_items.extend(line_items)
                        logger.info(f"✅ PAGE {page_num + 1} COMPLETE (PDF text): {len(line_items)} line numbers extracted")
                        if self.fast_mode:
                            continue

                # High-resolution rendering (2.5x for crisp text)
                pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5))
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                img = img.convert('L')  # Grayscale for better OCR
                
                # PHASE 1: Extract ALL text with 3 OCR engines
                logger.info("🔍 PHASE 1: Multi-Engine Text Extraction")
                ocr_results = self.extract_all_text_from_image(img)
                
                if not ocr_results:
                    logger.warning("  ⚠️ No text extracted from any OCR engine")
                    continue
                
                # Combine text from all engines
                combined_text = self.combine_and_deduplicate_text(ocr_results)
                logger.info(f"🧪 OCR combined sample (first 800 chars): {combined_text[:800]}")
                logger.info(f"🧪 OCR combined sample (last 800 chars): {combined_text[-800:] if len(combined_text) > 800 else combined_text}")
                
                if not combined_text or len(combined_text) < 10:
                    logger.warning("  ⚠️ Combined text too short, skipping page")
                    continue
                
                # PHASE 2: REGEX Pattern Matching (Reliable & Fast)
                logger.info("🔍 PHASE 2: REGEX Pattern Recognition")
                logger.info(f"  📝 OCR Text Sample (first 500 chars): {combined_text[:500]}")
                if line_format_config:
                    line_items = self.parse_with_custom_format(combined_text, page_num + 1, line_format_config)
                    if not line_items:
                        logger.info("🔁 CUSTOM format yielded 0, falling back to REGEX")
                        line_items = self.parse_with_regex(combined_text, page_num + 1)
                else:
                    line_items = self.parse_with_regex(combined_text, page_num + 1)
                
                if not line_items:
                    logger.warning("  ⚠️ No line numbers found on this page")
                    continue
                
                all_line_items.extend(line_items)
                logger.info(f"✅ PAGE {page_num + 1} COMPLETE: {len(line_items)} line numbers extracted")
            
            doc.close()
            
            # PHASE 3: Final deduplication and validation
            logger.info(f"\n{'='*60}")
            logger.info("🎯 PHASE 3: Final Validation & Deduplication")
            logger.info(f"{'='*60}")
            logger.info(f"  📊 Raw extractions: {len(all_line_items)}")
            
            unique_items = self._deduplicate_items(all_line_items)
            logger.info(f"  📊 After deduplication: {len(unique_items)}")
            
            logger.info(f"\n{'='*60}")
            logger.info(f"🎉 EXTRACTION COMPLETE: {len(unique_items)} UNIQUE LINE NUMBERS")
            logger.info(f"{'='*60}\n")
            
            return unique_items
            
        except Exception as e:
            logger.error(f"❌ EXTRACTION FAILED: {str(e)}", exc_info=True)
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
        Returns ONLY actual OCR data - no generated/assumed descriptions
        """
        table_data = []
        for item in line_items:
            table_data.append({
                'line_number': item.get('line_number', ''),
                'size': item.get('size', ''),
                'area': item.get('area', ''),
                'fluid_code': item.get('fluid_code', ''),
                'sequence_no': item.get('sequence_no', ''),
                'pipr_class': item.get('pipr_class', ''),
                'insulation': item.get('insulation', ''),
                'from_equipment': item.get('from_equipment', ''),
                'to_equipment': item.get('to_equipment', ''),
                'page': item.get('page', 1)
            })
        
        return table_data
