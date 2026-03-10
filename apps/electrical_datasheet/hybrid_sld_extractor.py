"""
Hybrid Cost-Optimized SLD Extractor
Uses local OCR + GPT-3.5-turbo for 95% cost reduction vs GPT-4o Vision

COST SAVINGS:
- Old: GPT-4o Vision = ~$75 per 1000 pages
- New: PaddleOCR + GPT-3.5-turbo = ~$0.50 per 1000 pages
- Savings: ~99% cost reduction!

ARCHITECTURE:
1. Extract text from SLD using PaddleOCR (FREE, local)
2. Send extracted text to GPT-3.5-turbo for interpretation (CHEAP)
3. Fallback to GPT-4o Vision only if both fail (EXPENSIVE, disabled by default)
"""

import logging
import json
from typing import Dict, List
import os
import fitz  # PyMuPDF
from PIL import Image
from io import BytesIO
import base64
from openai import OpenAI

from .ai_provider_config import AIProviderConfig

logger = logging.getLogger(__name__)


class HybridSLDExtractor:
    """
    Cost-optimized hybrid extractor using local OCR + cheap LLM
    """
    
    def __init__(self):
        self.config = AIProviderConfig
        self.strategy = self.config.get_active_strategy()
        self.openai_client = None
        
        # Initialize OpenAI client if available
        if self.config.is_openai_available():
            self.openai_client = OpenAI(api_key=self.config.get_openai_api_key())
        
        # Initialize OCR engine
        self.ocr_engine = None
        self._init_ocr_engine()
        
        logger.info(f"[HybridSLDExtractor] Using strategy: {self.strategy['name']}")
        logger.info(f"[HybridSLDExtractor] Methods: {self.strategy['methods']}")
    
    def _init_ocr_engine(self):
        """Initialize local OCR engine (PaddleOCR preferred)"""
        ocr_config = self.config.LOCAL_OCR_CONFIG
        engine_type = ocr_config.get('engine', 'paddleocr')
        
        try:
            if engine_type == 'paddleocr':
                from paddleocr import PaddleOCR
                self.ocr_engine = PaddleOCR(
                    use_angle_cls=True,
                    lang='en',
                    use_gpu=ocr_config.get('use_gpu', False),
                    show_log=False
                )
                logger.info("[HybridSLDExtractor] ✅ PaddleOCR initialized (FREE)")
            elif engine_type == 'tesseract':
                import pytesseract
                self.ocr_engine = 'tesseract'
                logger.info("[HybridSLDExtractor] ✅ Tesseract initialized (FREE)")
            else:
                logger.warning(f"[HybridSLDExtractor] Unknown OCR engine: {engine_type}")
        except ImportError as e:
            logger.warning(f"[HybridSLDExtractor] OCR engine not available: {e}")
            self.ocr_engine = None
    
    def extract_from_file(self, file_path: str, datasheet_types: List[str] = None) -> Dict:
        """
        Main extraction method - tries methods in order of cost
        
        Args:
            file_path: Path to SLD file (PDF, PNG, JPG)
            datasheet_types: Specific datasheet types to extract
        
        Returns:
            Dict with extraction results
        """
        logger.info(f"[HybridSLDExtractor] Starting extraction from {file_path}")
        
        # Estimate cost
        num_pages = self._count_pages(file_path)
        cost_estimate = self.config.estimate_cost(num_pages)
        logger.info(f"[HybridSLDExtractor] Estimated cost: {cost_estimate['breakdown']}")
        
        # Try each method in strategy order
        for method in self.strategy['methods']:
            logger.info(f"[HybridSLDExtractor] Trying method: {method}")
            
            try:
                if method == 'local_ocr':
                    result = self._extract_with_local_ocr(file_path, datasheet_types)
                elif method == 'gpt_3.5_turbo':
                    result = self._extract_with_gpt35(file_path, datasheet_types)
                elif method == 'gpt_4_turbo':
                    result = self._extract_with_gpt4(file_path, datasheet_types)
                elif method == 'gpt_4o_vision':
                    result = self._extract_with_gpt4o_vision(file_path, datasheet_types)
                else:
                    continue
                
                # Check quality
                if self._validate_result(result):
                    result['extraction_method'] = method
                    result['actual_cost'] = self.config.estimate_cost(num_pages, method)
                    logger.info(f"[HybridSLDExtractor] ✅ Success with {method}")
                    return result
                else:
                    logger.warning(f"[HybridSLDExtractor] Low quality result from {method}, trying next...")
                    
            except Exception as e:
                logger.error(f"[HybridSLDExtractor] Error with {method}: {e}")
                continue
        
        # All methods failed
        logger.error("[HybridSLDExtractor] ❌ All extraction methods failed")
        return self._empty_result(error="All extraction methods failed")
    
    def _extract_with_local_ocr(self, file_path: str, datasheet_types: List[str]) -> Dict:
        """
        Extract using local OCR only (FREE)
        Uses PaddleOCR or Tesseract + rule-based parsing
        """
        if not self.ocr_engine:
            raise Exception("OCR engine not initialized")
        
        logger.info("[HybridSLDExtractor] Using local OCR (FREE)")
        
        # Convert file to images
        images = self._file_to_images(file_path)
        
        # Extract text from all pages
        all_text = []
        for img_array in images:
            text = self._ocr_image(img_array)
            all_text.append(text)
        
        # Combine all text
        combined_text = "\n\n=== PAGE BREAK ===\n\n".join(all_text)
        
        # Rule-based equipment extraction
        equipment = self._parse_equipment_from_text(combined_text, datasheet_types)
        
        return {
            'equipment': equipment,
            'equipment_by_type': self._organize_by_type(equipment),
            'equipment_count': len(equipment),
            'metadata': self._extract_metadata_from_text(combined_text),
            'extraction_method': 'local_ocr',
            'confidence': 'medium',
            'pages_processed': len(images),
            'cost': 0.0
        }
    
    def _extract_with_gpt35(self, file_path: str, datasheet_types: List[str]) -> Dict:
        """
        Extract using local OCR + GPT-3.5-turbo interpretation (VERY LOW COST)
        This is the recommended method: ~$0.50 per 1000 pages
        """
        if not self.openai_client:
            raise Exception("OpenAI client not available")
        
        if not self.ocr_engine:
            raise Exception("OCR engine not initialized")
        
        logger.info("[HybridSLDExtractor] Using PaddleOCR + GPT-3.5-turbo (LOW COST)")
        
        # Step 1: Extract text using local OCR (FREE)
        images = self._file_to_images(file_path)
        all_text = []
        for img_array in images:
            text = self._ocr_image(img_array)
            all_text.append(text)
        
        combined_text = "\n\n=== PAGE BREAK ===\n\n".join(all_text)
        
        # Step 2: Send text to GPT-3.5-turbo for interpretation (CHEAP)
        gpt_config = self.config.GPT_35_TURBO_CONFIG
        
        system_prompt = self._build_system_prompt(datasheet_types)
        user_prompt = self._build_user_prompt_for_text(combined_text, datasheet_types)
        
        response = self.openai_client.chat.completions.create(
            model=gpt_config['model'],
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=gpt_config['temperature'],
            max_tokens=gpt_config['max_tokens'],
            response_format={"type": "json_object"}
        )
        
        result_text = response.choices[0].message.content
        data = json.loads(result_text)
        
        # Add metadata
        data['extraction_method'] = 'gpt_3.5_turbo'
        data['confidence'] = 'high'
        data['pages_processed'] = len(images)
        data['equipment_count'] = len(data.get('equipment', []))
        data['equipment_by_type'] = self._organize_by_type(data.get('equipment', []))
        
        logger.info(f"[HybridSLDExtractor] GPT-3.5: Extracted {data['equipment_count']} equipment")
        return data
    
    def _extract_with_gpt4(self, file_path: str, datasheet_types: List[str]) -> Dict:
        """
        Extract using local OCR + GPT-4-turbo (MEDIUM COST)
        ~10x more expensive than GPT-3.5, but better accuracy
        """
        # Similar to GPT-3.5 but with GPT-4-turbo model
        # Implementation similar to _extract_with_gpt35
        raise NotImplementedError("GPT-4-turbo extraction not yet implemented")
    
    def _extract_with_gpt4o_vision(self, file_path: str, datasheet_types: List[str]) -> Dict:
        """
        Extract using GPT-4o Vision (HIGH COST - FALLBACK ONLY)
        ~150x more expensive than local OCR + GPT-3.5
        Only use if explicitly enabled
        """
        if not self.config.ENABLED_PROVIDERS.get('gpt_4o_vision', False):
            raise Exception("GPT-4o Vision is disabled (high cost)")
        
        logger.warning("[HybridSLDExtractor] Using GPT-4o Vision (HIGH COST!)")
        
        # Import the original vision extractor
        from .sld_vision_extractor import SLDVisionExtractor
        extractor = SLDVisionExtractor()
        return extractor.extract_from_file(file_path, datasheet_types)
    
    def _file_to_images(self, file_path: str) -> List:
        """Convert file to image arrays for OCR"""
        images = []
        file_ext = os.path.splitext(file_path)[1].lower()
        
        if file_ext == '.pdf':
            doc = fitz.open(file_path)
            for page_num in range(min(len(doc), 20)):  # Process up to 20 pages
                page = doc[page_num]
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # 2x zoom for better OCR
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                images.append(img)
            doc.close()
        elif file_ext in ['.png', '.jpg', '.jpeg']:
            img = Image.open(file_path)
            images.append(img)
        
        return images
    
    def _ocr_image(self, img) -> str:
        """Run OCR on image using PaddleOCR or Tesseract"""
        try:
            if isinstance(self.ocr_engine, str) and self.ocr_engine == 'tesseract':
                # Tesseract
                import pytesseract
                return pytesseract.image_to_string(img)
            else:
                # PaddleOCR
                import numpy as np
                img_array = np.array(img)
                result = self.ocr_engine.ocr(img_array, cls=True)
                
                # Extract text from PaddleOCR result
                text_lines = []
                if result and result[0]:
                    for line in result[0]:
                        if line[1][0]:  # text content
                            text_lines.append(line[1][0])
                
                return "\n".join(text_lines)
        except Exception as e:
            logger.error(f"[HybridSLDExtractor] OCR error: {e}")
            return ""
    
    def _parse_equipment_from_text(self, text: str, datasheet_types: List[str]) -> List[Dict]:
        """
        Rule-based equipment parsing from OCR text
        Returns list of equipment dictionaries
        """
        equipment = []
        
        # Simple keyword-based detection
        lines = text.split('\n')
        current_equipment = {}
        
        for line in lines:
            line_lower = line.lower()
            
            # Detect transformers
            if any(kw in line_lower for kw in ['transformer', 'tx', 'xfmr', 'kva', 'mva']):
                if 'kva' in line_lower or 'mva' in line_lower:
                    current_equipment = {
                        'type': 'transformer',
                        'description': line.strip(),
                        'tag': self._extract_tag(line),
                        'power_rating': self._extract_rating(line)
                    }
                    equipment.append(current_equipment)
            
            # Detect generators
            elif any(kw in line_lower for kw in ['generator', 'gen', 'dg', 'diesel']):
                current_equipment = {
                    'type': 'diesel_generator',
                    'description': line.strip(),
                    'tag': self._extract_tag(line),
                }
                equipment.append(current_equipment)
            
            # Detect switchgear
            elif any(kw in line_lower for kw in ['switchgear', 'swgr', '11kv', 'switchboard']):
                current_equipment = {
                    'type': 'switchgear_11kv',
                    'description': line.strip(),
                    'tag': self._extract_tag(line),
                }
                equipment.append(current_equipment)
        
        return equipment
    
    def _extract_tag(self, text: str) -> str:
        """Extract equipment tag from text"""
        import re
        # Look for patterns like T-01, CB-001, M-1, etc.
        match = re.search(r'[A-Z]{1,3}[-_]?\d{1,4}', text)
        return match.group(0) if match else None
    
    def _extract_rating(self, text: str) -> str:
        """Extract power rating from text"""
        import re
        match = re.search(r'(\d+\.?\d*)\s*(kva|mva|kw|mw)', text, re.IGNORECASE)
        return match.group(0) if match else None
    
    def _extract_metadata_from_text(self, text: str) -> Dict:
        """Extract drawing metadata from text"""
        import re
        
        metadata = {}
        
        # Extract drawing number
        dwg_match = re.search(r'dwg[\s\-:]*([A-Z0-9\-]+)', text, re.IGNORECASE)
        if dwg_match:
            metadata['drawing_number'] = dwg_match.group(1)
        
        # Extract revision
        rev_match = re.search(r'rev[\s\-:]*([A-Z0-9]+)', text, re.IGNORECASE)
        if rev_match:
            metadata['revision'] = rev_match.group(1)
        
        return metadata
    
    def _build_system_prompt(self, datasheet_types: List[str]) -> str:
        """Build system prompt for LLM"""
        return """You are an expert electrical engineering assistant specialized in analyzing Single Line Diagram (SLD) text data.

Your task is to extract electrical equipment information from OCR-extracted text. The text may be noisy or incomplete due to OCR errors.

EQUIPMENT TYPES TO DETECT:
- transformer: Transformers, power transformers, distribution transformers
- diesel_generator: Generators, diesel generators, emergency generators
- switchgear_11kv: Switchgear, 11kV switchgear, MV switchgear
- circuit_breaker: Circuit breakers, CB, breakers
- motor: Motors, AC motors, induction motors
- vfd: Variable frequency drives, inverters
- ups: UPS systems, battery backup
- capacitor_bank: Capacitor banks, power factor correction

EXTRACTION RULES:
- Extract ALL equipment mentioned in the text
- Capture equipment tags, ratings, and specifications
- Handle OCR errors gracefully
- Return null if information not found
- Output valid JSON only

OUTPUT FORMAT:
{
  "equipment": [
    {
      "type": "equipment_type",
      "tag": "Equipment tag",
      "description": "Description",
      "voltage_primary": "Primary voltage",
      "voltage_secondary": "Secondary voltage",
      "power_rating": "Power rating (kVA/MVA/kW/MW)",
      "current_rating": "Current rating (A/kA)",
      "manufacturer": "Manufacturer if mentioned",
      "location": "Location if mentioned"
    }
  ],
  "metadata": {
    "drawing_number": "Drawing number if found",
    "revision": "Revision if found",
    "voltage_levels": ["Voltage levels mentioned"]
  }
}"""
    
    def _build_user_prompt_for_text(self, text: str, datasheet_types: List[str]) -> str:
        """Build user prompt with OCR text"""
        return f"""Analyze this OCR-extracted text from a Single Line Diagram and extract all electrical equipment.

OCR TEXT:
{text[:8000]}  

Extract equipment information and return valid JSON as specified in system prompt.
Focus on these equipment types: {', '.join(datasheet_types) if datasheet_types else 'all types'}"""
    
    def _organize_by_type(self, equipment_list: List[Dict]) -> Dict:
        """Organize equipment by type"""
        by_type = {}
        for eq in equipment_list:
            eq_type = eq.get('type', 'unknown')
            if eq_type not in by_type:
                by_type[eq_type] = []
            by_type[eq_type].append(eq)
        return by_type
    
    def _validate_result(self, result: Dict) -> bool:
        """Validate extraction result quality"""
        quality_config = self.config.QUALITY_CHECKS
        
        # Check minimum equipment extracted
        equipment_count = result.get('equipment_count', len(result.get('equipment', [])))
        if equipment_count < quality_config['min_equipment_extracted']:
            return False
        
        # Check confidence
        confidence = result.get('confidence', 'none')
        if confidence == 'none':
            return False
        
        return True
    
    def _count_pages(self, file_path: str) -> int:
        """Count pages in file"""
        file_ext = os.path.splitext(file_path)[1].lower()
        if file_ext == '.pdf':
            doc = fitz.open(file_path)
            count = len(doc)
            doc.close()
            return count
        else:
            return 1  # Image file
    
    def _empty_result(self, error: str = None) -> Dict:
        """Return empty result structure"""
        return {
            'equipment': [],
            'equipment_by_type': {},
            'equipment_count': 0,
            'metadata': {},
            'extraction_method': 'failed',
            'confidence': 'none',
            'pages_processed': 0,
            'error': error
        }
