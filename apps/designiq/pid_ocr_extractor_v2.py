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
from typing import List, Dict, Optional, Tuple
import numpy as np
from openai import OpenAI
from django.conf import settings
import base64

# Import OpenCV-based FROM-TO detector
try:
    from apps.designiq.from_to_detector import FromToDetector
    FROM_TO_DETECTOR_AVAILABLE = True
except ImportError as e:
    logger.warning(f"⚠️ FromToDetector not available: {e}")
    FROM_TO_DETECTOR_AVAILABLE = False

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
        self.from_to_detector = None
        self._init_engines()
        self._init_from_to_detector()
    
    def _init_from_to_detector(self):
        """Initialize OpenCV-based FROM-TO detector"""
        if FROM_TO_DETECTOR_AVAILABLE:
            try:
                # Configure with tuned defaults for P&ID diagrams
                config = {
                    'min_symbol_area': 50,
                    'max_symbol_area': 5000,
                    'epsilon_factor': 0.02,
                    'min_vertices': 3,
                    'max_vertices': 7,
                    'canny_low': 50,
                    'canny_high': 150,
                    'endpoint_radius': 0.05,
                    'max_ocr_distance': 0.1,
                    'line_number_pattern': r'\b\d{1,2}["\']?-[A-Z]{2,3}-\d{4}\b'
                }
                self.from_to_detector = FromToDetector(config=config)
                logger.info("✅ OpenCV FROM-TO Detector initialized")
            except Exception as e:
                logger.warning(f"⚠️ Failed to initialize FROM-TO detector: {e}")
        else:
            logger.warning("⚠️ OpenCV FROM-TO detection not available")
    
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
            logger.info(f"  ✅ Tesseract extracted {len(tesseract_text)} characters (combined)")
        except Exception as e:
            logger.warning(f"  ⚠️ Tesseract failed: {e}")
        
        # 2. EasyOCR - Enable rotation detection for vertical text
        if self.easyocr_reader:
            try:
                img_array = np.array(img)
                # Basic readtext without rotation_info parameter
                easyocr_result = self.easyocr_reader.readtext(
                    img_array, 
                    detail=0,
                    paragraph=False
                )
                easyocr_text = ' '.join(easyocr_result)
                results['easyocr'] = easyocr_text
                logger.info(f"  ✅ EasyOCR extracted {len(easyocr_text)} characters")
            except Exception as e:
                logger.warning(f"  ⚠️ EasyOCR failed: {e}")
        
        # 3. PaddleOCR
        if self.paddleocr_reader:
            try:
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
                    logger.info(f"  ✅ PaddleOCR extracted {len(paddle_text)} characters")
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
    
    def extract_spatial_data(self, img: Image.Image) -> List[Dict]:
        """
        📍 Extract spatial/position data from PaddleOCR for FROM-TO detection
        
        Returns list of text items with bounding boxes and positions:
        [{'text': str, 'bbox': list, 'center_x': float, 'center_y': float, 'confidence': float}]
        """
        spatial_data = []
        
        if not self.paddleocr_reader:
            logger.warning("  ⚠️ PaddleOCR not available for spatial extraction")
            return spatial_data
        
        try:
            img_array = np.array(img)
            paddle_result = self.paddleocr_reader.ocr(img_array)
            
            if paddle_result and isinstance(paddle_result, list) and len(paddle_result) > 0:
                first_page = paddle_result[0]
                if first_page and isinstance(first_page, list):
                    for line in first_page:
                        if line and isinstance(line, (list, tuple)) and len(line) >= 2:
                            bbox = line[0]  # [[x0,y0], [x1,y0], [x1,y1], [x0,y1]]
                            text_data = line[1]  # (text, confidence)
                            
                            if isinstance(text_data, (list, tuple)) and len(text_data) >= 2:
                                text = str(text_data[0])
                                confidence = float(text_data[1])
                                
                                # Calculate center point of bounding box
                                x_coords = [point[0] for point in bbox]
                                y_coords = [point[1] for point in bbox]
                                center_x = sum(x_coords) / 4
                                center_y = sum(y_coords) / 4
                                
                                spatial_data.append({
                                    'text': text,
                                    'bbox': bbox,
                                    'center_x': center_x,
                                    'center_y': center_y,
                                    'confidence': confidence
                                })
        except Exception as e:
            logger.warning(f"  ⚠️ Spatial data extraction failed: {e}")
        
        return spatial_data
    
    def parse_with_regex(self, extracted_text: str, page_num: int, include_area: bool = False, format_type: str = 'onshore') -> List[Dict]:
        """
        🎯 RELIABLE REGEX-BASED APPROACH:
        Use pure Python regex to find line number patterns directly in OCR text.
        
        Line format (onshore without area): SIZE-FLUID-SEQUENCE-PIPECLASS(-INSULATION)?
        Example: 12-D-5777-033842-N
        
        Line format (onshore with area): SIZE"-AREA-FLUID-SEQUENCE-PIPECLASS(-INSULATION)?
        Example: 4"-41-SWR-64313-A2AU16-V
        
        Line format (offshore): AREA-FLUID-SIZE-PIPECLASS-SEQUENCE(-INSULATION)?
        Example: 604-HO-8-BC2GA0-1071-H
        
        This is MORE RELIABLE than OpenAI because:
        - Deterministic pattern matching
        - No API failures or hallucinations
        - Faster processing
        - Consistent results
        """
        format_label = 'OFFSHORE' if format_type == 'offshore' else ('WITH AREA' if include_area else 'WITHOUT AREA')
        logger.info(f"  🔍 Using REGEX pattern matching on OCR text ({format_label})")
        
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
        # Format WITHOUT AREA: SIZE-FLUID-SEQUENCE-PIPECLASS(-INSULATION)?
        # Examples: 6-VG-4952-011505-X, 16-PG-4005-011441-X, 6-PG-5143-031440
        #
        # Format WITH AREA: SIZE"-AREA-FLUID-SEQUENCE-PIPECLASS(-INSULATION)?
        # Examples: 4"-41-SWR-64313-A2AU16-V, 16"-25-PG-4667-031441-X
        #
        # Format OFFSHORE: AREA-FLUID-SIZE-PIPECLASS-SEQUENCE(-INSULATION)?
        # Examples: 604-HO-8-BC2GA0-1071-H, 41-SWR-16-A2AU16-64313-V
        
        if format_type == 'offshore':
            # ADNOC OFFSHORE PATTERNS: AREA-FLUID-SIZE-PIPECLASS-SEQUENCE(-INSULATION)?
            # Same methodology as onshore with area - flexible patterns, same validation
            patterns = [
                # Pattern 1: Standard with word boundaries (most reliable)
                r'\b(\d{2,3})\s*-\s*([A-Z]{1,3})\s*-\s*(\d{1,2})"?\s*-\s*([A-Z0-9]{5,6})\s*-\s*(\d{4,5})(?:\s*-\s*([A-Z]{1,2}))?\b',
                
                # Pattern 2: With flexible spacing
                r'\b(\d{2,3})\s*-+\s*([A-Z]{1,3})\s*-+\s*(\d{1,2})"?\s*-+\s*([A-Z0-9]{5,6})\s*-+\s*(\d{4,5})(?:\s*-+\s*([A-Z]{1,2}))?\b',
                
                # Pattern 3: Compact format (no spaces)
                r'\b(\d{2,3})-([A-Z]{1,3})-(\d{1,2})"?-([A-Z0-9]{5,6})-(\d{4,5})(?:-([A-Z]{1,2}))?\b',
                
                # Pattern 4: With spaces and lookahead
                r'(?:^|\s)(\d{2,3})\s*-+\s*([A-Z]{1,3})\s+-+\s*(\d{1,2})"?\s+-+\s*([A-Z0-9]{5,6})\s+-+\s*(\d{4,5})(?:\s*-+\s*([A-Z]{1,2}))?(?=\s|$|-)',
                
                # Pattern 5: Case insensitive
                r'(?:^|\s)(\d{2,3})\s*-\s*([A-Za-z]{1,3})\s*-\s*(\d{1,2})"?\s*-\s*([A-Za-z0-9]{5,6})\s*-\s*(\d{4,5})(?:\s*-\s*([A-Za-z]{1,2}))?(?=\s|$|-)',
            ]
        elif include_area:
            # WITH AREA PATTERNS: SIZE"-AREA-FLUID-SEQUENCE-PIPECLASS(-INSULATION)?
            patterns = [
                # Pattern 1: With quote after size (most common with area)
                r'\b(\d{1,2})"?\s*-\s*(\d{2,3})\s*-\s*([A-Z]{1,3})\s*-\s*(\d{4,5})\s*-\s*([A-Z0-9]{5,6})(?:\s*-\s*([A-Z]{1,2}))?\b',
                
                # Pattern 2: Flexible spacing with quote
                r'\b(\d{1,2})"\s*-+\s*(\d{2,3})\s*-+\s*([A-Z]{1,3})\s*-+\s*(\d{4,5})\s*-+\s*([A-Z0-9]{5,6})(?:\s*-+\s*([A-Z]{1,2}))?\b',
                
                # Pattern 3: Compact format
                r'\b(\d{1,2})"-(\d{2,3})-([A-Z]{1,3})-(\d{4,5})-([A-Z0-9]{5,6})(?:-([A-Z]{1,2}))?\b',
                
                # Pattern 4: With spaces
                r'(?:^|\s)(\d{1,2})"?\s*-+\s*(\d{2,3})\s+-+\s*([A-Z]{1,3})\s+-+\s*(\d{4,5})\s+-+\s*([A-Z0-9]{5,6})(?:\s*-+\s*([A-Z]{1,2}))?(?=\s|$|-)',
                
                # Pattern 5: Case insensitive
                r'(?:^|\s)(\d{1,2})"?\s*-\s*(\d{2,3})\s*-\s*([A-Za-z]{1,3})\s*-\s*(\d{4,5})\s*-\s*([A-Za-z0-9]{5,6})(?:\s*-\s*([A-Za-z]{1,2}))?(?=\s|$|-)',
            ]
        else:
            # WITHOUT AREA PATTERNS (ORIGINAL): SIZE-FLUID-SEQUENCE-PIPECLASS(-INSULATION)?
            patterns = [
                # Pattern 1: Standard with word boundaries (most reliable)
                r'\b(\d{1,2})\s*-\s*([A-Z]{1,2})\s*-\s*(\d{4})\s*-\s*(\d{5,6})(?:\s*-\s*([A-Z]{1,2}))?\b',
            
                # Pattern 2: With optional quote after size
                r'\b(\d{1,2})-?\s*-\s*([A-Z]{1,2})\s*-\s*(\d{4})\s*-\s*(\d{5,6})(?:\s*-\s*([A-Z]{1,2}))?\b',
            
                # Pattern 3: More lenient spacing
                r'(?:^|\s)(\d{1,2})\s*-+\s*([A-Z]{1,2})\s*-+\s*(\d{4})\s*-+\s*(\d{5,6})(?:\s*-+\s*([A-Z]{1,2}))?(?:\s|$|[-,.])',
            
                # Pattern 4: Compact (no spaces at all)
                r'\b(\d{1,2})-([A-Z]{1,2})-(\d{4})-(\d{5,6})(?:-([A-Z]{1,2}))?\b',
            
                # Pattern 5: With flexible separators (space or hyphen)
                r'\b(\d{1,2})[\s-]+([A-Z]{1,2})[\s-]+(\d{4})[\s-]+(\d{5,6})(?:[\s-]+([A-Z]{1,2}))?\b',
            
                # Pattern 6: Case insensitive with word boundaries
                r'(?:^|\s)(\d{1,2})\s*-\s*([A-Za-z]{1,2})\s*-\s*(\d{4})\s*-\s*(\d{5,6})(?:\s*-\s*([A-Za-z]{1,2}))?(?=\s|$|-)',
            ]
        
        found_lines = []
        seen_lines = set()
        rejected = []
        
        for pattern_idx, pattern in enumerate(patterns, 1):
            matches = re.finditer(pattern, normalized_text, re.IGNORECASE)
            
            for match in matches:
                # Extract and clean components
                if format_type == 'offshore':
                    # Offshore: AREA-FLUID-SIZE-PIPECLASS-SEQUENCE(-INSULATION)?
                    area = match.group(1).strip()
                    fluid = match.group(2).strip().upper()
                    size = match.group(3).strip()
                    pipr_class = match.group(4).strip()
                    seq = match.group(5).strip()
                    insulation = match.group(6).strip().upper() if match.lastindex >= 6 and match.group(6) else ''
                elif include_area:
                    # With area: SIZE"-AREA-FLUID-SEQUENCE-PIPECLASS(-INSULATION)?
                    size = match.group(1).strip()
                    area = match.group(2).strip()
                    fluid = match.group(3).strip().upper()
                    seq = match.group(4).strip()
                    pipr_class = match.group(5).strip()
                    insulation = match.group(6).strip().upper() if match.lastindex >= 6 and match.group(6) else ''
                else:
                    # Without area: SIZE-FLUID-SEQUENCE-PIPECLASS(-INSULATION)?
                    size = match.group(1).strip()
                    area = ''
                    fluid = match.group(2).strip().upper()
                    seq = match.group(3).strip()
                    pipr_class = match.group(4).strip()
                    insulation = match.group(5).strip().upper() if match.lastindex >= 5 and match.group(5) else ''
                
                # Smart cleaning: remove any non-alphanumeric from edges
                size = re.sub(r'[^0-9]', '', size)
                fluid = re.sub(r'[^A-Z]', '', fluid)
                seq = re.sub(r'[^0-9]', '', seq)
                if insulation:
                    insulation = re.sub(r'[^A-Z]', '', insulation)
                
                # STRICT VALIDATION
                # 1. SIZE: Must be 1-2 digits
                if not size or not size.isdigit() or len(size) > 2:
                    rejected.append(f"Invalid size: {size}")
                    continue
                
                # 2. AREA: For offshore and include_area formats, must be 2-3 digits
                if format_type == 'offshore' or include_area:
                    if not area or not area.isdigit() or len(area) not in [2, 3]:
                        rejected.append(f"Invalid area: {area}")
                        continue
                else:
                    area = ''  # Ensure area is empty for without-area format
                
                # 3. FLUID: Must be 1-3 uppercase letters (allow 3 for area format like SWR)
                max_fluid_len = 3 if (include_area or format_type == 'offshore') else 2
                if not fluid or not fluid.isalpha() or len(fluid) > max_fluid_len:
                    rejected.append(f"Invalid fluid: {fluid}")
                    continue
                
                # 4. SEQUENCE: Must be 4-5 digits for offshore/area formats, 4 for standard
                if format_type == 'offshore' or include_area:
                    seq_lengths = [4, 5]
                else:
                    seq_lengths = [4]
                if not seq or not seq.isdigit() or len(seq) not in seq_lengths:
                    rejected.append(f"Invalid sequence: {seq}")
                    continue
                
                # 5. PIPE CLASS: Same validation for offshore and area formats (5-6 chars)
                if format_type == 'offshore' or include_area:
                    # Offshore/Area format: 5-6 alphanumeric characters (e.g., A2AU16, BC2GA0, AC2NL1)
                    if not pipr_class or len(pipr_class) not in [5, 6]:
                        rejected.append(f"Invalid pipe class: {pipr_class}")
                        continue
                    if not pipr_class.isalnum():
                        rejected.append(f"Invalid pipe class (not alphanumeric): {pipr_class}")
                        continue
                else:
                    # Without area: 5-6 digits only
                    if not pipr_class or len(pipr_class) not in [5, 6]:
                        rejected.append(f"Invalid pipe class: {pipr_class}")
                        continue
                    pipr_class = re.sub(r'[^0-9]', '', pipr_class)
                    if not pipr_class.isdigit():
                        rejected.append(f"Invalid pipe class (not numeric): {pipr_class}")
                        continue
                
                # 6. INSULATION: Optional, must be 1-2 letters if present
                if insulation and (not insulation.isalpha() or len(insulation) > 2):
                    rejected.append(f"Invalid insulation: {insulation}")
                    continue
                
                # Build line number string
                if format_type == 'offshore':
                    # Offshore format: AREA-FLUID-SIZE-PIPECLASS-SEQUENCE(-INSULATION)?
                    if insulation:
                        line_number = f"{area}-{fluid}-{size}-{pipr_class}-{seq}-{insulation}"
                    else:
                        line_number = f"{area}-{fluid}-{size}-{pipr_class}-{seq}"
                elif include_area:
                    # With area format: SIZE"-AREA-FLUID-SEQUENCE-PIPECLASS(-INSULATION)?
                    if insulation:
                        line_number = f"{size}\"-{area}-{fluid}-{seq}-{pipr_class}-{insulation}"
                    else:
                        line_number = f"{size}\"-{area}-{fluid}-{seq}-{pipr_class}"
                else:
                    # Without area format: SIZE-FLUID-SEQUENCE-PIPECLASS(-INSULATION)?
                    if insulation:
                        line_number = f"{size}-{fluid}-{seq}-{pipr_class}-{insulation}"
                    else:
                        line_number = f"{size}-{fluid}-{seq}-{pipr_class}"
                
                # Deduplicate
                if line_number in seen_lines:
                    continue
                seen_lines.add(line_number)
                
                # Create line entry
                line_entry = {
                    'line_number': line_number,
                    'size': f'{size}"',
                    'fluid_code': fluid,
                    'sequence_no': seq,
                    'pipr_class': pipr_class,
                    'insulation': insulation,
                    'area': area if (format_type == 'offshore' or include_area) else '',
                    'page': page_num,
                    'from_equipment': '',
                    'to_equipment': '',
                    'extraction_method': 'regex_direct',
                    'original_detection': match.group(0).strip()
                }
                
                found_lines.append(line_entry)
        
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
    
    def extract_from_pdf(self, pdf_path: str, include_area: bool = False, format_type: str = 'onshore') -> List[Dict]:
        """
        🚀 INTELLIGENT AI-FIRST EXTRACTION:
        
        PHASE 1: COMPREHENSIVE TEXT EXTRACTION
        - Tesseract OCR: Fast and reliable
        - EasyOCR: Good with varied fonts
        - PaddleOCR: Excellent with Asian characters and complex layouts
        - ALL THREE combined = Maximum text coverage
        
        PHASE 2: AI INTELLIGENCE
        - OpenAI GPT-4 searches through ALL text
        - Finds line numbers in ANY format (hyphens, spaces, periods, etc.)
        - Understands context better than rigid regex
        - Adapts to OCR variations automatically
        
        PHASE 3: STRICT VALIDATION
        - Validates ALL 4 mandatory components
        - Rejects invalid patterns
        - Ensures data quality
        
        Args:
            pdf_path: Path to P&ID PDF file
            include_area: If True, detect format with Area (SIZE"-AREA-FLUID-SEQUENCE-PIPECLASS)
                         If False, detect standard format (SIZE-FLUID-SEQUENCE-PIPECLASS)
            format_type: 'onshore' (default) or 'offshore' (AREA-FLUID-SIZE-PIPECLASS-SEQUENCE)
            
        Returns:
            List of validated line items
        """
        try:
            doc = fitz.open(pdf_path)
            all_line_items = []
            
            all_line_items = []
            
            logger.info(f"🚀 STARTING AI-FIRST P&ID EXTRACTION")
            logger.info(f"📄 File: {pdf_path}")
            logger.info(f"📄 Pages: {len(doc)}")
            logger.info(f"🧠 Strategy: OCR ALL TEXT → AI INTELLIGENCE → STRICT VALIDATION")
            if format_type == 'offshore':
                format_msg = 'OFFSHORE (AREA-FLUID-SIZE-PIPECLASS-SEQUENCE)'
            elif include_area:
                format_msg = 'WITH AREA (SIZE"-AREA-FLUID-SEQ-PIPECLASS)'
            else:
                format_msg = 'WITHOUT AREA (SIZE-FLUID-SEQ-PIPECLASS)'
            logger.info(f"📍 Format: {format_msg}")
            
            for page_num in range(len(doc)):
                page = doc[page_num]
                logger.info(f"\n{'='*60}")
                logger.info(f"📄 PAGE {page_num + 1}/{len(doc)}")
                logger.info(f"{'='*60}")
                
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
                
                if not combined_text or len(combined_text) < 10:
                    logger.warning("  ⚠️ Combined text too short, skipping page")
                    continue
                
                # PHASE 2: REGEX Pattern Matching (Reliable & Fast)
                logger.info("🔍 PHASE 2: REGEX Pattern Recognition")
                logger.info(f"  📝 OCR Text Sample (first 500 chars): {combined_text[:500]}")
                line_items = self.parse_with_regex(combined_text, page_num + 1, include_area=include_area, format_type=format_type)
                
                if not line_items:
                    logger.warning("  ⚠️ No line numbers found on this page")
                    continue
                
                # SUCCESS: Add basic line items first
                all_line_items.extend(line_items)
                logger.info(f"✅ PAGE {page_num + 1} BASIC EXTRACTION: {len(line_items)} line numbers extracted")
                
                # PHASE 3: Flow Direction Detection (OPTIONAL - Vision-Based Enhancement)
                # This is a post-processing step that won't break if it fails
                try:
                    logger.info("🔺 PHASE 3: Smart Vision Flow Direction Detection (Optional)")
                    
                    # Try vision-based detection directly (doesn't need spatial data from PaddleOCR)
                    if line_items:
                        enhanced_items = self.enhance_with_flow_direction(
                            line_items.copy(), img, None, page_num + 1
                        )
                        # Update the items in all_line_items with enhanced versions
                        if enhanced_items:
                            # Remove the basic items we just added
                            all_line_items = all_line_items[:-len(line_items)]
                            # Add enhanced items
                            all_line_items.extend(enhanced_items)
                            logger.info(f"  ✅ Flow direction enhancement completed")
                except Exception as e:
                    logger.warning(f"  ⚠️ Flow direction detection failed (non-critical): {e}")
                    logger.warning(f"  → Continuing with basic line items only")
            
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
    
    def detect_flow_with_vision(self, img: Image.Image, page_num: int) -> Dict:
        """
        🔺 SMART VISION: Use GPT-4 Vision to detect arrows with positions
        
        Returns structured data with:
        - arrows: [{'bbox_normalized': [x1,y1,x2,y2], 'orientation': str, 'center': [x,y]}]
        """
        logger.info(f"  🔺 detect_flow_with_vision called for page {page_num}")
        logger.info(f"  🔑 OpenAI client available: {self.openai_client is not None}")
        
        if not self.openai_client:
            logger.warning("  ⚠️ OpenAI not available for vision detection")
            return {'arrows': []}
        
        try:
            logger.info(f"  🔺 Running arrow detection with GPT-4 Vision on page {page_num}")
            
            # Get image dimensions for normalization
            img_width, img_height = img.size
            
            # Convert image to base64
            buffered = io.BytesIO()
            img.save(buffered, format="PNG")
            img_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
            
            prompt = f"""You are a P&ID diagram expert. Detect ALL flow direction arrows/triangles on pipeline lines.

IMAGE DIMENSIONS: {img_width}x{img_height} pixels

OUTPUT FORMAT (JSON only):
{{
    "arrows": [
        {{
            "bbox_normalized": [x1, y1, x2, y2],
            "center_normalized": [x, y],
            "orientation": "up|down|left|right|unknown",
            "confidence": "high|medium|low"
        }}
    ]
}}

RULES:
- Find ALL arrows/triangles on pipelines
- bbox_normalized: [x1/width, y1/height, x2/width, y2/height] (values 0-1)
- center_normalized: [(x1+x2)/(2*width), (y1+y2)/(2*height)] (values 0-1)
- orientation: direction arrow POINTS TO (downstream)
- Return ONLY valid JSON, no explanations

Analyze and return JSON:"""
            
            response = self.openai_client.chat.completions.create(
                model="gpt-4o",  # GPT-4 Omni with vision
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{img_base64}",
                                    "detail": "high"
                                }
                            }
                        ]
                    }
                ],
                temperature=0.1,
                max_tokens=3000
            )
            
            result_text = response.choices[0].message.content.strip()
            logger.info(f"  📝 GPT-4 Vision raw response: {result_text[:500]}...")  # Log first 500 chars
            
            # Clean markdown code blocks if present
            if '```' in result_text:
                parts = result_text.split('```')
                for part in parts:
                    if part.strip().startswith('json') or part.strip().startswith('{'):
                        result_text = part.replace('json', '').strip()
                        break
            
            vision_data = json.loads(result_text)
            
            # Log results
            arrows = vision_data.get('arrows', [])
            
            logger.info(f"  ✅ Detected {len(arrows)} arrows with positions")
            
            return vision_data
            
        except json.JSONDecodeError as e:
            logger.warning(f"  ⚠️ JSON decode error in vision response: {e}")
            logger.warning(f"  📄 Raw response was: {result_text[:1000] if 'result_text' in locals() else 'No response'}")
            return {'arrows': []}
        except Exception as e:
            logger.warning(f"  ⚠️ Vision detection failed: {e}")
            return {'arrows': []}
    
    def find_line_endpoints(self, spatial_data: List[Dict], line_number: str) -> Tuple[Optional[Dict], Optional[Dict]]:
        """
        🎯 Find the two endpoints of a detected line based on spatial data
        
        Returns (start_point, end_point) where each point is:
        {'x': float, 'y': float, 'text': str}
        """
        # Find all spatial items that contain this line number
        line_occurrences = []
        for item in spatial_data:
            if line_number in item.get('text', ''):
                line_occurrences.append({
                    'x': item['center_x'],
                    'y': item['center_y'],
                    'text': item['text']
                })
        
        if len(line_occurrences) < 2:
            # Line only appears once or not enough spatial data
            return None, None
        
        # Sort by position to find extremes (leftmost/rightmost or topmost/bottommost)
        # Try horizontal first (x-axis)
        x_sorted = sorted(line_occurrences, key=lambda p: p['x'])
        x_spread = x_sorted[-1]['x'] - x_sorted[0]['x']
        
        # Try vertical (y-axis)
        y_sorted = sorted(line_occurrences, key=lambda p: p['y'])
        y_spread = y_sorted[-1]['y'] - y_sorted[0]['y']
        
        # Use the axis with greater spread
        if x_spread > y_spread:
            # Horizontal line
            return x_sorted[0], x_sorted[-1]
        else:
            # Vertical line
            return y_sorted[0], y_sorted[-1]
    
    def associate_symbols_to_endpoints(
        self, 
        endpoint1: Dict, 
        endpoint2: Dict, 
        symbols: List[Dict],
        search_radius: float = 150.0
    ) -> Tuple[Optional[Dict], Optional[Dict]]:
        """
        🔗 Associate flow symbols to line endpoints
        
        Returns (symbol_at_endpoint1, symbol_at_endpoint2)
        """
        def distance(p1, p2):
            return np.sqrt((p1['x'] - p2['center_x'])**2 + (p1['y'] - p2['center_y'])**2)
        
        # Find closest symbol to each endpoint
        symbol1 = None
        min_dist1 = search_radius
        
        for symbol in symbols:
            dist = distance(endpoint1, symbol)
            if dist < min_dist1:
                min_dist1 = dist
                symbol1 = symbol
        
        symbol2 = None
        min_dist2 = search_radius
        
        for symbol in symbols:
            dist = distance(endpoint2, symbol)
            if dist < min_dist2:
                min_dist2 = dist
                symbol2 = symbol
        
        return symbol1, symbol2
    
    def determine_from_to(
        self,
        endpoint1: Dict,
        endpoint2: Dict,
        symbol1: Optional[Dict],
        symbol2: Optional[Dict]
    ) -> Tuple[str, str]:
        """
        🎯 Determine FROM→TO direction based on symbol orientation
        
        LOGIC:
        - If both endpoints have symbols:
          - Symbol pointing AWAY from line = FROM (upstream)
          - Symbol pointing INTO line = TO (downstream)
        
        - If only one endpoint has symbol:
          - Endpoint WITH symbol = TO (downstream)
          - Endpoint WITHOUT symbol = FROM (upstream)
        
        - If no symbols:
          - Use positional heuristic (left→right, top→bottom)
        
        Returns (from_text, to_text) extracted from endpoint texts
        """
        def extract_equipment_tag(text: str) -> str:
            """Extract equipment tag from text like 'V-201' or 'P-101'"""
            # Look for patterns like: LETTER-NUMBER or LETTER-NUMBER-LETTER
            match = re.search(r'\b([A-Z])-(\d+)([A-Z])?\b', text, re.IGNORECASE)
            if match:
                return match.group(0)
            return text.strip()
        
        from_endpoint = None
        to_endpoint = None
        
        if symbol1 and symbol2:
            # Both have symbols - use orientation
            # Symbols typically point TOWARD downstream (TO)
            # So endpoint with symbol pointing AWAY is FROM
            
            # Simple heuristic: if symbols point toward each other, 
            # the "upstream" symbol orientation indicates FROM
            orientation1 = symbol1.get('orientation', 'unknown')
            orientation2 = symbol2.get('orientation', 'unknown')
            
            # For now, use position-based fallback if orientation unclear
            if endpoint1['x'] < endpoint2['x']:
                # Horizontal: left is FROM, right is TO
                from_endpoint, to_endpoint = endpoint1, endpoint2
            else:
                from_endpoint, to_endpoint = endpoint2, endpoint1
                
        elif symbol1 or symbol2:
            # Only one has symbol - endpoint WITH symbol is usually TO (downstream)
            if symbol1:
                from_endpoint, to_endpoint = endpoint2, endpoint1
            else:
                from_endpoint, to_endpoint = endpoint1, endpoint2
        else:
            # No symbols - use positional heuristic
            # Left→Right or Top→Bottom convention
            if abs(endpoint1['x'] - endpoint2['x']) > abs(endpoint1['y'] - endpoint2['y']):
                # More horizontal
                if endpoint1['x'] < endpoint2['x']:
                    from_endpoint, to_endpoint = endpoint1, endpoint2
                else:
                    from_endpoint, to_endpoint = endpoint2, endpoint1
            else:
                # More vertical
                if endpoint1['y'] < endpoint2['y']:
                    from_endpoint, to_endpoint = endpoint1, endpoint2
                else:
                    from_endpoint, to_endpoint = endpoint2, endpoint1
        
        from_text = extract_equipment_tag(from_endpoint['text']) if from_endpoint else ''
        to_text = extract_equipment_tag(to_endpoint['text']) if to_endpoint else ''
        
        return from_text, to_text
    
    def enhance_with_flow_direction(
        self,
        line_items: List[Dict],
        img: Image.Image,
        spatial_data: Optional[List[Dict]],
        page_num: int
    ) -> List[Dict]:
        """
        🚀 OpenCV-Based FROM-TO Detection: Detect arrow symbols and connect line numbers
        
        Strategy:
        1. Detect arrow/triangle symbols using OpenCV (Canny edges + contours + PCA)
        2. Get OCR positions for all detected line numbers
        3. Create virtual "lines" from line number positions
        4. Associate symbols to line endpoints via proximity
        5. Infer FROM/TO roles using orientation analysis
        6. Map endpoints to line numbers with intelligent scoring
        """
        logger.info(f"  🔺 PHASE 3: OpenCV-Based FROM-TO Detection")
        
        # Check if detector available
        if not self.from_to_detector:
            logger.warning(f"  ⚠️ FROM-TO detector not available, skipping")
            return line_items
        
        # Step 1: Get image dimensions
        img_width, img_height = img.size
        img_array = np.array(img)
        
        # Step 2: Extract OCR positions using EasyOCR
        ocr_positions = []
        if self.easyocr_reader:
            try:
                easyocr_result = self.easyocr_reader.readtext(img_array, detail=1)
                
                for detection in easyocr_result:
                    bbox, text, conf = detection
                    # Calculate bbox and center
                    x_coords = [point[0] for point in bbox]
                    y_coords = [point[1] for point in bbox]
                    center_x = sum(x_coords) / 4
                    center_y = sum(y_coords) / 4
                    
                    # Normalize coordinates (0-1 range)
                    x1_norm = min(x_coords) / img_width
                    y1_norm = min(y_coords) / img_height
                    x2_norm = max(x_coords) / img_width
                    y2_norm = max(y_coords) / img_height
                    center_x_norm = center_x / img_width
                    center_y_norm = center_y / img_height
                    
                    ocr_positions.append({
                        'id': f'ocr_{len(ocr_positions)}',
                        'text': text.upper().strip(),
                        'bbox': (x1_norm, y1_norm, x2_norm, y2_norm),
                        'center_x_norm': center_x_norm,
                        'center_y_norm': center_y_norm,
                        'confidence': conf
                    })
                
                logger.info(f"  📍 Extracted {len(ocr_positions)} OCR items with positions")
            except Exception as e:
                logger.warning(f"  ⚠️ Could not extract spatial OCR data: {e}")
                return line_items
        else:
            logger.warning(f"  ⚠️ EasyOCR not available for spatial extraction")
            return line_items
        
        # Step 3: Build position map and create virtual "lines" from line number positions
        line_position_map = {}  # {line_number: (avg_x, avg_y)}
        virtual_lines = []
        
        for line_item in line_items:
            line_number = line_item['line_number'].upper().strip()
            line_positions = []
            
            # Find all OCR occurrences of this line number
            for pos in ocr_positions:
                if line_number in pos['text']:
                    line_positions.append({
                        'x': pos['center_x_norm'],
                        'y': pos['center_y_norm'],
                        'confidence': pos['confidence']
                    })
            
            if line_positions:
                # Calculate average position
                avg_x = sum(p['x'] for p in line_positions) / len(line_positions)
                avg_y = sum(p['y'] for p in line_positions) / len(line_positions)
                line_position_map[line_number] = (avg_x, avg_y)
                
                # Create virtual "line" for FROM-TO detector
                # (single point, will be used for symbol proximity matching)
                virtual_lines.append({
                    'id': line_number,
                    'points': [(avg_x, avg_y)]  # Single point representing line position
                })
        
        logger.info(f"  🗺️ Created {len(virtual_lines)} virtual lines from OCR positions")
        
        # Step 4: Run OpenCV FROM-TO detection
        try:
            from_to_map = self.from_to_detector.detect_from_to(
                image=img_array,
                lines=virtual_lines,
                ocr_items=ocr_positions
            )
            
            logger.info(f"  ✅ OpenCV detection completed: {len(from_to_map)} mappings")
        except Exception as e:
            logger.warning(f"  ⚠️ OpenCV FROM-TO detection failed: {e}")
            return line_items
        
        # Step 5: Apply FROM-TO results to line items
        enhanced_items = []
        for line_item in line_items:
            line_number = line_item['line_number'].upper().strip()
            
            if line_number in from_to_map:
                mapping = from_to_map[line_number]
                line_item['from_line'] = mapping.get('from_line', '')
                line_item['to_line'] = mapping.get('to_line', '')
                line_item['flow_detection_method'] = 'opencv_cv'
                line_item['flow_confidence'] = 'high'
                
                if mapping.get('from_line') or mapping.get('to_line'):
                    logger.info(f"  ✅ {line_number}: FROM={mapping.get('from_line', 'N/A')} → TO={mapping.get('to_line', 'N/A')}")
            
            enhanced_items.append(line_item)
        
        detected_count = sum(1 for item in enhanced_items if item.get('from_line') or item.get('to_line'))
        logger.info(f"  ✅ Mapped FROM/TO for {detected_count}/{len(line_items)} lines using OpenCV")
        
        return enhanced_items
    
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
            line_number = item.get('line_number', '')
            
            table_data.append({
                'original_detection': line_number,  # Full line as detected (FIRST COLUMN)
                'line_number': line_number,
                'fluid_code': fluid_code,
                'fluid_description': fluid_code_names.get(fluid_code, 'Unknown'),
                'size': item.get('size', ''),
                'sequence_no': item.get('sequence_no', ''),
                'pipr_class': item.get('pipr_class', ''),
                'insulation': insulation,
                'insulation_description': insulation_names.get(insulation, 'Unknown'),
                'from_equipment': item.get('from_equipment', ''),
                'to_equipment': item.get('to_equipment', ''),
                'from_line': item.get('from_line', ''),  # NEW: Symbol-based FROM detection
                'to_line': item.get('to_line', ''),      # NEW: Symbol-based TO detection
                'flow_detection_method': item.get('flow_detection_method', ''),
                'flow_confidence': item.get('flow_confidence', ''),
                'page': item.get('page', 1),
                'confidence': item.get('confidence', 'medium')
            })
        
        return table_data
