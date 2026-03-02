"""
OCR-Based MOV Equipment Extractor (COST-FREE ALTERNATIVE)

Smart extraction using OCR + Pattern Matching instead of expensive Vision API
- Uses EasyOCR/Tesseract for text extraction (FREE)
- Pattern matching using regex from configuration
- No API costs, unlimited extractions
- Soft-coded field detection patterns

COST COMPARISON:
- GPT-4o Vision: ~$0.01-0.03 per image
- OCR + Pattern Matching: $0 (completely free)
"""

import logging
import re
import io
from typing import List, Dict, Optional
from PIL import Image
from pdf2image import convert_from_bytes
import os

from apps.process_datasheet.config.mov_datasheet_config import (
    MOV_DATASHEET_FIELDS,
    EXTRACTION_PATTERNS,
    get_all_fields
)

logger = logging.getLogger(__name__)


class OCRMOVExtractor:
    """
    Cost-effective MOV extractor using OCR + Pattern Matching
    Zero API costs, unlimited extractions
    """

    def __init__(self):
        """Initialize OCR engine"""
        self.ocr_engine = None
        self.ocr_available = False
        
        # Try to initialize EasyOCR (preferred)
        try:
            import easyocr
            self.ocr_engine = easyocr.Reader(['en'], gpu=False, verbose=False)
            self.ocr_available = True
            self.ocr_type = 'easyocr'
            logger.info("[OCR-MOV] ✅ EasyOCR initialized successfully (FREE)")
        except ImportError:
            logger.warning("[OCR-MOV] ⚠️ EasyOCR not available, trying Tesseract...")
            
            # Fallback to Tesseract
            try:
                import pytesseract
                from PIL import Image
                # Test if tesseract is installed
                pytesseract.get_tesseract_version()
                self.ocr_engine = pytesseract
                self.ocr_available = True
                self.ocr_type = 'tesseract'
                logger.info("[OCR-MOV] ✅ Tesseract OCR initialized successfully (FREE)")
            except Exception as e:
                logger.error(f"[OCR-MOV] ❌ No OCR engine available: {e}")
                logger.error("[OCR-MOV] Install: pip install easyocr OR pip install pytesseract")
                self.ocr_available = False
        
        # Load extraction patterns from configuration
        self.patterns = EXTRACTION_PATTERNS
        self.all_fields = get_all_fields()
        
        # Soft-coded MOV detection patterns
        self.mov_patterns = {
            'tag_pattern': r'MOV[-\s]*(\d{3,4}[A-Z]?)',  # MOV-101, MOV 102A
            'alternative_tags': [
                r'MOT[-\s]*V[-\s]*(\d{3,4})',  # MOT-V-101
                r'MV[-\s]*(\d{3,4})',  # MV-101
                r'XV[-\s]*(\d{3,4})',  # XV-101 (some standards)
            ],
            'service_indicators': [
                r'ISOLATION', r'SHUTDOWN', r'CONTROL', r'FEED', r'DISCHARGE',
                r'INLET', r'OUTLET', r'BYPASS', r'DRAIN', r'VENT'
            ],
            'valve_types': [
                r'BALL\s*VALVE', r'GATE\s*VALVE', r'GLOBE\s*VALVE', 
                r'BUTTERFLY\s*VALVE', r'PLUG\s*VALVE'
            ],
            'actuator_indicators': [
                r'ELECTRIC', r'MOTOR', r'ROTORK', r'AUMA', r'LIMITORQUE',
                r'ACTUATOR', r'MOV', r'MOTOR\s*OPERATED'
            ]
        }

    def extract_movs_from_pid(self, pid_file_path: str, drawing_info: Dict = None) -> List[Dict]:
        """
        Extract MOV equipment using OCR + Pattern Matching (FREE)
        
        Args:
            pid_file_path: Path to P&ID file
            drawing_info: Optional drawing metadata
            
        Returns:
            list: Extracted MOV data
        """
        if not self.ocr_available:
            logger.error("[OCR-MOV] ❌ OCR engine not available")
            return []
        
        try:
            logger.info(f"[OCR-MOV] 🔍 Starting FREE OCR extraction from: {pid_file_path}")
            
            # Convert file to image
            image = self._load_image(pid_file_path)
            if not image:
                logger.error("[OCR-MOV] ❌ Failed to load image")
                return []
            
            # Extract text using OCR
            text_data = self._extract_text_with_ocr(image)
            logger.info(f"[OCR-MOV] 📄 Extracted {len(text_data)} text blocks")
            
            # Detect MOV equipment using pattern matching
            movs = self._detect_movs_from_text(text_data, drawing_info or {})
            
            logger.info(f"[OCR-MOV] ✅ Found {len(movs)} MOV equipment (ZERO COST)")
            return movs
            
        except Exception as e:
            logger.error(f"[OCR-MOV] ❌ Extraction error: {str(e)}")
            import traceback
            logger.error(f"[OCR-MOV] Traceback: {traceback.format_exc()}")
            return []

    def _load_image(self, file_path: str) -> Optional[Image.Image]:
        """
        Load image from file (supports PDF, PNG, JPG)
        
        Args:
            file_path: Path to file
            
        Returns:
            PIL Image or None
        """
        try:
            with open(file_path, 'rb') as f:
                file_data = f.read()
            
            # Check if PDF
            if file_path.lower().endswith('.pdf'):
                logger.info("[OCR-MOV] Converting PDF to image...")
                images = convert_from_bytes(file_data, dpi=200, first_page=1, last_page=1)
                if images:
                    return images[0]
            else:
                # Image file
                return Image.open(io.BytesIO(file_data))
                
        except Exception as e:
            logger.error(f"[OCR-MOV] ❌ Image loading error: {str(e)}")
            return None

    def _extract_text_with_ocr(self, image: Image.Image) -> List[Dict]:
        """
        Extract text from image using OCR
        
        Args:
            image: PIL Image
            
        Returns:
            list: Text blocks with positions and content
        """
        text_blocks = []
        
        try:
            if self.ocr_type == 'easyocr':
                # EasyOCR requires numpy array
                import numpy as np
                image_array = np.array(image)
                
                # EasyOCR provides position + text
                results = self.ocr_engine.readtext(image_array)
                for (bbox, text, confidence) in results:
                    text_blocks.append({
                        'bbox': bbox,
                        'text': text.upper(),  # Normalize to uppercase
                        'confidence': confidence
                    })
                    
            elif self.ocr_type == 'tesseract':
                # Tesseract text extraction
                import pytesseract
                text = pytesseract.image_to_string(image)
                # Split into lines and create blocks
                lines = text.split('\n')
                for idx, line in enumerate(lines):
                    if line.strip():
                        text_blocks.append({
                            'bbox': None,  # Tesseract default doesn't provide bbox
                            'text': line.strip().upper(),
                            'confidence': 0.8,  # Default confidence
                            'line_number': idx
                        })
            
            logger.info(f"[OCR-MOV] ✅ OCR extracted {len(text_blocks)} text blocks")
            
        except Exception as e:
            logger.error(f"[OCR-MOV] ❌ OCR extraction error: {str(e)}")
        
        return text_blocks

    def _detect_movs_from_text(self, text_blocks: List[Dict], drawing_info: Dict) -> List[Dict]:
        """
        Detect MOV equipment from OCR text using pattern matching
        
        Args:
            text_blocks: OCR extracted text blocks
            drawing_info: Drawing metadata
            
        Returns:
            list: Detected MOV equipment
        """
        movs = []
        
        # Combine all text for full-text search
        full_text = ' '.join([block['text'] for block in text_blocks])
        
        # Find all MOV tag numbers
        mov_tags = self._find_mov_tags(full_text)
        logger.info(f"[OCR-MOV] 🎯 Detected {len(mov_tags)} MOV tags")
        
        # For each MOV tag, extract associated data
        for tag in mov_tags:
            mov_data = self._extract_mov_details(tag, text_blocks, full_text, drawing_info)
            if mov_data:
                movs.append(mov_data)
        
        return movs

    def _find_mov_tags(self, text: str) -> List[str]:
        """
        Find all MOV tag numbers in text
        
        Args:
            text: Full OCR text
            
        Returns:
            list: MOV tag numbers
        """
        tags = set()
        
        # Primary pattern: MOV-XXX
        matches = re.findall(self.mov_patterns['tag_pattern'], text, re.IGNORECASE)
        for match in matches:
            tags.add(f"MOV-{match}")
        
        # Alternative patterns
        for alt_pattern in self.mov_patterns['alternative_tags']:
            matches = re.findall(alt_pattern, text, re.IGNORECASE)
            for match in matches:
                tags.add(f"MOV-{match}")
        
        return sorted(list(tags))

    def _extract_mov_details(self, tag: str, text_blocks: List[Dict], 
                            full_text: str, drawing_info: Dict) -> Dict:
        """
        Extract detailed MOV specifications using pattern matching
        
        Args:
            tag: MOV tag number
            text_blocks: OCR text blocks
            full_text: Combined text
            drawing_info: Drawing metadata
            
        Returns:
            dict: MOV data with all available fields
        """
        mov_data = {
            'tag_number': tag,
            'pid_no': drawing_info.get('drawing_number', 'N/A'),
        }
        
        # Extract service description (context around tag)
        mov_data['service'] = self._extract_service(tag, full_text)
        
        # Extract line number (typically near tag)
        mov_data['line_number'] = self._extract_line_number(tag, full_text)
        
        # Extract piping class
        mov_data['piping_class'] = self._extract_piping_class(full_text)
        
        # Extract valve type
        mov_data['valve_type'] = self._extract_valve_type(full_text)
        
        # Extract size (valve size)
        mov_data['valve_size'] = self._extract_size(tag, full_text)
        
        # Extract pressure values
        pressures = self._extract_pressure_values(full_text)
        mov_data.update(pressures)
        
        # Extract temperature values
        temperatures = self._extract_temperature_values(full_text)
        mov_data.update(temperatures)
        
        # Extract materials
        mov_data['body_material'] = self._extract_material(full_text, 'body')
        mov_data['trim_material'] = self._extract_material(full_text, 'trim')
        
        # Extract actuator info
        mov_data['actuator_type'] = self._extract_actuator_type(full_text)
        mov_data['fail_position'] = self._extract_fail_position(full_text)
        
        # Extract fluid info
        mov_data['fluid'] = self._extract_fluid(full_text)
        mov_data['state'] = self._extract_fluid_state(full_text)
        
        # Set defaults for missing fields
        mov_data = self._set_defaults(mov_data)
        
        logger.info(f"[OCR-MOV] ✅ Extracted details for {tag}")
        return mov_data

    def _extract_service(self, tag: str, text: str) -> str:
        """Extract service description from text near tag"""
        # Look for service indicators near tag
        tag_position = text.find(tag)
        if tag_position >= 0:
            context = text[max(0, tag_position-50):min(len(text), tag_position+100)]
            for indicator in self.mov_patterns['service_indicators']:
                if re.search(indicator, context, re.IGNORECASE):
                    return indicator.title() + " Service"
        return "Process Isolation"

    def _extract_line_number(self, tag: str, text: str) -> str:
        """Extract line number near MOV tag"""
        # Pattern: 2"-HC-1001-A1
        line_pattern = r'(\d+"[-\s]*[A-Z]{2}[-\s]*\d{3,4}[-\s]*[A-Z]?\d?)'
        tag_position = text.find(tag)
        if tag_position >= 0:
            context = text[max(0, tag_position-100):min(len(text), tag_position+100)]
            match = re.search(line_pattern, context)
            if match:
                return match.group(1)
        return "N/A"

    def _extract_piping_class(self, text: str) -> str:
        """Extract piping class (e.g., 300# RF, 600# RTJ)"""
        class_pattern = r'(150|300|600|900|1500|2500)\s*#?\s*(RF|RTJ|BW)?'
        match = re.search(class_pattern, text)
        if match:
            rating = match.group(1)
            end_type = match.group(2) or 'RF'
            return f"{rating}# {end_type}"
        return "300# RF"

    def _extract_valve_type(self, text: str) -> str:
        """Extract valve type"""
        for valve_type in self.mov_patterns['valve_types']:
            if re.search(valve_type, text, re.IGNORECASE):
                return valve_type.replace(r'\s*', ' ').title()
        return "Ball Valve"

    def _extract_size(self, tag: str, text: str) -> str:
        """Extract valve size"""
        # Pattern: 2", 3", 4" near tag
        size_pattern = r'(\d+\.?\d*)\s*["\']'
        tag_position = text.find(tag)
        if tag_position >= 0:
            context = text[max(0, tag_position-50):min(len(text), tag_position+50)]
            match = re.search(size_pattern, context)
            if match:
                return f"{match.group(1)} inch"
        return "2 inch"

    def _extract_pressure_values(self, text: str) -> Dict:
        """Extract pressure values using patterns from config"""
        pressure_data = {}
        
        # Use patterns from configuration
        pressure_pattern = self.patterns.get('pressure', r'(\d+\.?\d*)\s*(bar|psi|kpa)')
        matches = re.findall(pressure_pattern, text, re.IGNORECASE)
        
        if len(matches) >= 1:
            pressure_data['operating_pressure_normal'] = matches[0][0]
        if len(matches) >= 2:
            pressure_data['operating_pressure_max'] = matches[1][0]
        if len(matches) >= 3:
            pressure_data['design_pressure_max'] = matches[2][0]
        
        return pressure_data

    def _extract_temperature_values(self, text: str) -> Dict:
        """Extract temperature values"""
        temp_data = {}
        
        # Use patterns from configuration
        temp_pattern = self.patterns.get('temperature', r'(-?\d+\.?\d*)\s*(°C|C|DEG\s*C)')
        matches = re.findall(temp_pattern, text, re.IGNORECASE)
        
        if len(matches) >= 1:
            temp_data['operating_temp_normal'] = matches[0][0]
        if len(matches) >= 2:
            temp_data['operating_temp_max'] = matches[1][0]
        
        return temp_data

    def _extract_material(self, text: str, material_type: str) -> str:
        """Extract material specifications"""
        materials = {
            'A105': 'A105 Carbon Steel',
            'A216': 'A216 WCB',
            'CF8M': 'CF8M Stainless Steel',
            '316': '316 Stainless Steel',
            'A182': 'A182 F316'
        }
        
        for code, full_name in materials.items():
            if code in text:
                return full_name
        
        return "Carbon Steel" if material_type == 'body' else "Stainless Steel"

    def _extract_actuator_type(self, text: str) -> str:
        """Extract actuator type"""
        for indicator in self.mov_patterns['actuator_indicators']:
            if re.search(indicator, text, re.IGNORECASE):
                return indicator.title()
        return "Electric Motor"

    def _extract_fail_position(self, text: str) -> str:
        """Extract fail position"""
        if re.search(r'FAIL\s*CLOSE|FC', text, re.IGNORECASE):
            return "FC (Fail Close)"
        elif re.search(r'FAIL\s*OPEN|FO', text, re.IGNORECASE):
            return "FO (Fail Open)"
        elif re.search(r'FAIL\s*LAST|FL', text, re.IGNORECASE):
            return "FL (Fail Last)"
        return "FC (Fail Close)"

    def _extract_fluid(self, text: str) -> str:
        """Extract fluid type"""
        fluids = ['NATURAL GAS', 'CRUDE OIL', 'WATER', 'STEAM', 'AIR', 'NITROGEN', 'METHANOL']
        for fluid in fluids:
            if fluid in text:
                return fluid.title()
        return "Process Fluid"

    def _extract_fluid_state(self, text: str) -> str:
        """Extract fluid state"""
        if 'GAS' in text or 'VAPOR' in text:
            return "Gas"
        elif 'LIQUID' in text or 'OIL' in text or 'WATER' in text:
            return "Liquid"
        return "Gas/Liquid"

    def _set_defaults(self, mov_data: Dict) -> Dict:
        """Set default values for missing fields"""
        defaults = {
            'phase': 'Single Phase',
            'operating_pressure_min': '0',
            'operating_pressure_normal': mov_data.get('operating_pressure_normal', '10'),
            'operating_pressure_max': mov_data.get('operating_pressure_max', '12'),
            'operating_temp_min': '15',
            'operating_temp_normal': mov_data.get('operating_temp_normal', '50'),
            'operating_temp_max': mov_data.get('operating_temp_max', '80'),
            'design_pressure_min': '0',
            'design_pressure_max': mov_data.get('design_pressure_max', '20'),
            'design_temp_min': '0',
            'design_temp_max': '100',
            'seat_leakage_class': 'Class VI',
            'nace_compliant': 'Yes',
            'valve_close_time': '30',
            'valve_open_time': '30',
            'actuator_voltage': '220 VAC',
            'actuator_current': '2.5',
            'actuator_power': '0.55',
            'actuator_torque': '150'
        }
        
        for key, value in defaults.items():
            if key not in mov_data or not mov_data[key]:
                mov_data[key] = value
        
        return mov_data


# Convenience function
def extract_movs_with_ocr(pid_file_path: str, drawing_info: Dict = None) -> List[Dict]:
    """
    Extract MOVs using OCR (FREE alternative to Vision API)
    
    Args:
        pid_file_path: Path to P&ID file
        drawing_info: Optional drawing metadata
        
    Returns:
        list: Extracted MOV data
    """
    extractor = OCRMOVExtractor()
    return extractor.extract_movs_from_pid(pid_file_path, drawing_info)
