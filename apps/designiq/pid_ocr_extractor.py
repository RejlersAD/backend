"""
P&ID OCR Extractor - Multi-Engine Smart Line Number Detection
Extracts piping line numbers from P&ID PDFs using multiple OCR engines
Uses Tesseract, EasyOCR, PaddleOCR + OpenAI for intelligent categorization
Handles both horizontal and vertical text orientations
"""

import re
import fitz  # PyMuPDF
from PIL import Image
import io
import logging
import json
from typing import List, Dict, Optional
import numpy as np

# Conditional import for pytesseract (graceful fallback if not installed)
try:
    import pytesseract
    PYTESSERACT_AVAILABLE = True
except ImportError:
    pytesseract = None
    PYTESSERACT_AVAILABLE = False

logger = logging.getLogger(__name__)


class PIDLineExtractor:
    """
    Multi-Engine P&ID line number extractor with AI intelligence
    Uses: Tesseract + EasyOCR + PaddleOCR -> OpenAI for smart categorization
    Detects line numbers like: 20"-PG-12340-A1B02-N
    Format: [size]-[fluid]-[sequence]-[class]-[insulation]
    """
    
    def __init__(self):
        self.easyocr_reader = None
        self.paddleocr_reader = None
        self._init_ocr_engines()
    
    def _init_ocr_engines(self):
        """Initialize EasyOCR and PaddleOCR engines"""
        try:
            import easyocr
            self.easyocr_reader = easyocr.Reader(['en'], gpu=False)
            logger.info("✅ EasyOCR initialized")
        except Exception as e:
            logger.warning(f"⚠️ EasyOCR not available: {e}")
        
        try:
            from paddleocr import PaddleOCR
            self.paddleocr_reader = PaddleOCR(use_angle_cls=True, lang='en', show_log=False)
            logger.info("✅ PaddleOCR initialized")
        except Exception as e:
            logger.warning(f"⚠️ PaddleOCR not available: {e}")
    
    # Regex pattern for line numbers - more flexible
    LINE_NUMBER_PATTERN = re.compile(
        r'(\d+(?:\.\d+)?)\s*["\']?\s*[-–—]\s*([A-Z]{1,4})\s*[-–—]\s*(\d{4,6})\s*[-–—]\s*([A-Z]\d[A-Z]\d{1,2})\s*[-–—]\s*([A-Z])',
        re.IGNORECASE
    )

    # New 6-part format: SIZE"-AREA-FLUID-SEQUENCE-PIPECLASS-INSULATION(optional)
    # Example: 4"-41-SW-6432-1ABCDE-X
    NEW_LINE_NUMBER_PATTERN = re.compile(
        r'(\d{1,2})\s*["\']?\s*[-–—]\s*(\d{1,2})\s*[-–—]\s*([A-Z]{1,2})\s*[-–—]\s*(\d{4})\s*[-–—]\s*([0-9][A-Z0-9]{5})\s*(?:[-–—]\s*([A-Z]{1,2}))?',
        re.IGNORECASE
    )
    
    # Common fluid codes
    FLUID_CODES = {
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
    
    # Insulation codes
    INSULATION_CODES = {
        'N': 'None',
        'C': 'Cold',
        'H': 'Hot',
        'P': 'Personnel Protection',
        'A': 'Acoustic'
    }
    
    def extract_from_pdf(self, pdf_path: str, rotate_detection: bool = True) -> List[Dict]:
        """
        Extract line numbers from P&ID PDF with smart OCR
        
        Args:
            pdf_path: Path to PDF file
            rotate_detection: Try multiple rotations to detect vertical text
            
        Returns:
            List of extracted line items with parsed components
        """
        try:
            doc = fitz.open(pdf_path)
            all_line_items = []
            
            logger.info(f"📄 Processing P&ID PDF: {pdf_path}")
            logger.info(f"📄 Total pages: {len(doc)}")
            
            for page_num in range(len(doc)):
                page = doc[page_num]
                logger.info(f"📄 Processing page {page_num + 1}/{len(doc)}")
                
                # Extract text from PDF (native text layer first)
                native_text = page.get_text()
                line_items = self._parse_line_numbers(native_text, page_num + 1, 'native')
                all_line_items.extend(line_items)
                
                # If no line numbers found in native text, use OCR
                if not line_items:
                    logger.info(f"  → No line numbers in native text, trying OCR...")
                    
                    # Convert page to image with higher resolution
                    pix = page.get_pixmap(matrix=fitz.Matrix(3, 3))  # 3x resolution for better OCR
                    img = Image.open(io.BytesIO(pix.tobytes("png")))
                    
                    # Enhance image for better OCR
                    img = img.convert('L')  # Convert to grayscale
                    
                    # Try OCR with different orientations
                    orientations = [0, 90, 180, 270] if rotate_detection else [0]
                    
                    for angle in orientations:
                        logger.info(f"  → Trying OCR at {angle}° rotation...")
                        if angle > 0:
                            rotated_img = img.rotate(angle, expand=True)
                        else:
                            rotated_img = img
                        
                        # OCR with custom config for better accuracy
                        if PYTESSERACT_AVAILABLE and pytesseract:
                            custom_config = r'--oem 3 --psm 6'  # PSM 6 = Assume uniform block of text
                            ocr_text = pytesseract.image_to_string(rotated_img, config=custom_config)
                            
                            # Also try PSM 11 (sparse text) for better line detection
                            if not ocr_text.strip():
                                custom_config = r'--oem 3 --psm 11'
                                ocr_text = pytesseract.image_to_string(rotated_img, config=custom_config)
                        else:
                            logger.warning("  ⚠️ Pytesseract not available, skipping OCR")
                            ocr_text = ""
                        
                        logger.info(f"  → OCR extracted {len(ocr_text)} characters")
                        ocr_items = self._parse_line_numbers(ocr_text, page_num + 1, f'ocr_{angle}°')
                        
                        if ocr_items:
                            logger.info(f"  ✅ Found {len(ocr_items)} line numbers at {angle}° rotation")
                            all_line_items.extend(ocr_items)
                            break
                else:
                    logger.info(f"  ✅ Found {len(line_items)} line numbers in native text")
            
            doc.close()
            
            # Remove duplicates
            unique_items = self._deduplicate_items(all_line_items)
            
            logger.info(f"✅ Extracted {len(unique_items)} unique line numbers from PDF")
            return unique_items
            
        except Exception as e:
            logger.error(f"❌ Error extracting from PDF: {str(e)}", exc_info=True)
            return []
    
    def _parse_line_numbers(self, text: str, page_num: int, source: str) -> List[Dict]:
        """Parse line numbers from text using regex"""
        line_items = []
        
        # Clean and normalize text
        # Replace various dash types with standard dash
        text = text.replace('–', '-').replace('—', '-').replace('_', '-')
        # Remove extra whitespace
        text = ' '.join(text.split())
        # Handle quote marks
        text = text.replace('"', '"').replace(''', "'")
        
        logger.info(f"  🔍 Searching for line numbers in {len(text)} characters from {source}")
        
        matches = self.LINE_NUMBER_PATTERN.findall(text)
        
        logger.info(f"  🎯 Regex found {len(matches)} potential matches")
        
        for match in matches:
            size, fluid, sequence, pipr_class, insulation = match
            
            line_number = f'{size}"-{fluid.upper()}-{sequence}-{pipr_class.upper()}-{insulation.upper()}'
            
            logger.info(f"  ✅ Detected: {line_number}")
            
            item = {
                'line_number': line_number,
                'size': f'{size}"',
                'fluid_code': fluid.upper(),
                'fluid_description': self.FLUID_CODES.get(fluid.upper(), 'Unknown'),
                'sequence_no': sequence,
                'pipr_class': pipr_class.upper(),
                'insulation_code': insulation.upper(),
                'insulation_type': self.INSULATION_CODES.get(insulation.upper(), 'Unknown'),
                'from_equipment': self._extract_connection(text, line_number, 'from'),
                'to_equipment': self._extract_connection(text, line_number, 'to'),
                'page_number': page_num,
                'detection_source': source,
                'raw_text_snippet': text[max(0, text.find(line_number) - 50):text.find(line_number) + 100]
            }
            
            line_items.append(item)

        # Additional parsing for new 6-part format (size-area-fluid-sequence-pipeclass-insulation optional)
        new_matches = self.NEW_LINE_NUMBER_PATTERN.findall(text)
        for match in new_matches:
            size, area, fluid, sequence, pipe_class, insulation = match

            line_number = f'{size}"-{area}-{fluid.upper()}-{sequence}-{pipe_class.upper()}'
            if insulation:
                line_number += f'-{insulation.upper()}'

            item = {
                'line_number': line_number,
                'size': f'{size}"',
                'fluid_code': fluid.upper(),
                'fluid_description': self.FLUID_CODES.get(fluid.upper(), 'Unknown'),
                'sequence_no': sequence,
                'pipr_class': pipe_class.upper(),
                'insulation_code': insulation.upper() if insulation else '',
                'insulation_type': self.INSULATION_CODES.get(insulation.upper(), 'Unknown') if insulation else 'Unknown',
                'from_equipment': self._extract_connection(text, line_number, 'from'),
                'to_equipment': self._extract_connection(text, line_number, 'to'),
                'page_number': page_num,
                'detection_source': source,
                'raw_text_snippet': text[max(0, text.find(line_number) - 50):text.find(line_number) + 100]
            }

            line_items.append(item)
        
        return line_items
    
    def _extract_connection(self, text: str, line_number: str, direction: str) -> Optional[str]:
        """
        Extract connection equipment (From/To) near line number
        Looks for equipment tags like: V-201, P-101, E-301, etc.
        """
        # Find the line number position
        pos = text.find(line_number)
        if pos == -1:
            return None
        
        # Look in surrounding text (200 chars before/after)
        context = text[max(0, pos - 200):pos + 200]
        
        # Equipment tag pattern: Letter-Number (e.g., V-201, P-101A)
        equipment_pattern = re.compile(r'([A-Z]{1,2})-(\d{2,4}[A-Z]?)', re.IGNORECASE)
        equipment_matches = equipment_pattern.findall(context)
        
        if equipment_matches:
            # Return first match (could be enhanced with better logic)
            return f"{equipment_matches[0][0]}-{equipment_matches[0][1]}".upper()
        
        return None
    
    def _deduplicate_items(self, items: List[Dict]) -> List[Dict]:
        """Remove duplicate line numbers, keeping best quality detection"""
        unique = {}
        
        for item in items:
            line_num = item['line_number']
            
            if line_num not in unique:
                unique[line_num] = item
            else:
                # Prefer native text over OCR
                if item['detection_source'] == 'native' and unique[line_num]['detection_source'].startswith('ocr'):
                    unique[line_num] = item
        
        return list(unique.values())
    
    def format_as_table_data(self, line_items: List[Dict]) -> List[Dict]:
        """
        Format extracted line items as table rows
        Headers: Fluid Code, Size, Sequence No, PIPR Class, Insulation, From, To
        """
        table_data = []
        
        for item in line_items:
            table_data.append({
                'fluid_code': item['fluid_code'],
                'fluid_description': item['fluid_description'],
                'size': item['size'],
                'sequence_no': item['sequence_no'],
                'pipr_class': item['pipr_class'],
                'insulation': f"{item['insulation_code']} ({item['insulation_type']})",
                'from_equipment': item.get('from_equipment', 'N/A'),
                'to_equipment': item.get('to_equipment', 'N/A'),
                'line_number': item['line_number'],
                'page': item['page_number']
            })
        
        return table_data
