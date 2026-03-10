"""
SLD (Single Line Diagram) Vision Extractor
Uses OpenAI GPT-4o Vision to extract electrical equipment from SLD drawings
GENERATIVE AI APPROACH - No hallucination, only extract visible equipment
"""
import logging
import base64
import json
from typing import Dict, List
from openai import OpenAI
import os
import fitz  # PyMuPDF
from PIL import Image
from io import BytesIO

logger = logging.getLogger(__name__)


class SLDVisionExtractor:
    """
    Extract electrical equipment data from SLD drawings using Vision AI
    
    Features:
    - Detects equipment types (Transformer, Diesel Generator, Switchgear, etc.)
    - Extracts ratings and specifications
    - Identifies connections and topology
    - Recognizes equipment tags/labels
    """
    
    # Soft-coded configuration
    EXTRACTION_CONFIG = {
        'max_pages': 20,  # Maximum pages to process per SLD
        'image_quality': 300,  # DPI for PDF to image conversion
        'vision_model': 'gpt-4o',  # OpenAI Vision model
        'temperature': 0.1,  # Low temperature for accuracy
        'max_tokens': 4096,  # Maximum response tokens
        'image_detail': 'high',  # Image detail level
    }
    
    # Equipment types to detect (soft-coded)
    EQUIPMENT_TYPES = {
        'transformer': {
            'keywords': ['transformer', 'tx', 'xfmr', 't1', 't2', 'power transformer', 'distribution transformer'],
            'icon': '⚡',
            'category': 'Power Equipment'
        },
        'diesel_generator': {
            'keywords': ['generator', 'gen', 'dg', 'diesel generator', 'emergency generator', 'standby generator'],
            'icon': '🔋',
            'category': 'Generation'
        },
        'switchgear_11kv': {
            'keywords': ['switchgear', 'swgr', '11kv', '11 kv', 'mv switchgear', 'medium voltage'],
            'icon': '🔌',
            'category': 'Distribution'
        },
        'circuit_breaker': {
            'keywords': ['circuit breaker', 'cb', 'breaker', 'vcb', 'acb', 'mccb', 'mcb'],
            'icon': '🔒',
            'category': 'Protection'
        },
        'motor': {
            'keywords': ['motor', 'm1', 'm2', 'em', 'induction motor', 'ac motor'],
            'icon': '⚙️',
            'category': 'Load'
        },
        'vfd': {
            'keywords': ['vfd', 'variable frequency drive', 'inverter', 'frequency converter'],
            'icon': '📊',
            'category': 'Control'
        },
        'ups': {
            'keywords': ['ups', 'uninterruptible power supply', 'battery backup'],
            'icon': '🔋',
            'category': 'Backup Power'
        },
        'capacitor_bank': {
            'keywords': ['capacitor bank', 'cap bank', 'power factor correction', 'pfc'],
            'icon': '🔆',
            'category': 'Power Quality'
        }
    }
    
    def __init__(self, api_key=None):
        """Initialize extractor with OpenAI API key"""
        self.api_key = api_key or os.getenv('OPENAI_API_KEY')
        if not self.api_key:
            logger.warning("[SLDVisionExtractor] No OpenAI API key found")
            self.client = None
        else:
            self.client = OpenAI(api_key=self.api_key)
    
    def extract_from_file(self, file_path: str, datasheet_types: List[str] = None) -> Dict:
        """
        Extract electrical equipment data from SLD file
        
        Args:
            file_path: Path to SLD file (PDF, PNG, JPG)
            datasheet_types: Specific datasheet types to extract (optional)
            
        Returns:
            Dict with structured equipment data
        """
        if not self.client:
            return self._fallback_extraction()
        
        logger.info(f"[SLDVisionExtractor] Starting SLD extraction from {file_path}")
        
        try:
            # Convert file to images
            images = self._file_to_images(file_path)
            logger.info(f"[SLDVisionExtractor] Converted to {len(images)} images")
            
            # Extract equipment from each page
            all_equipment = []
            metadata = {}
            
            for page_num, image_base64 in enumerate(images, 1):
                logger.info(f"[SLDVisionExtractor] Processing page {page_num}...")
                
                page_data = self._extract_page_equipment(
                    image_base64, 
                    page_num, 
                    datasheet_types
                )
                
                if page_data.get('equipment'):
                    all_equipment.extend(page_data['equipment'])
                
                if page_data.get('metadata'):
                    metadata.update(page_data['metadata'])
            
            # Organize by equipment type
            equipment_by_type = self._organize_by_type(all_equipment)
            
            result = {
                'equipment': all_equipment,
                'equipment_by_type': equipment_by_type,
                'equipment_count': len(all_equipment),
                'metadata': metadata,
                'extraction_method': 'vision_ai',
                'confidence': 'high' if all_equipment else 'low',
                'pages_processed': len(images)
            }
            
            logger.info(f"[SLDVisionExtractor] ✅ Extracted {len(all_equipment)} equipment items")
            return result
            
        except Exception as e:
            logger.error(f"[SLDVisionExtractor] ❌ Error: {e}")
            return self._fallback_extraction(error=str(e))
    
    def _file_to_images(self, file_path: str) -> List[str]:
        """
        Convert file to base64 encoded images
        Supports PDF, PNG, JPG formats
        """
        images = []
        file_ext = os.path.splitext(file_path)[1].lower()
        
        if file_ext == '.pdf':
            # PDF to images
            doc = fitz.open(file_path)
            total_pages = len(doc)
            pages_to_process = min(total_pages, self.EXTRACTION_CONFIG['max_pages'])
            
            logger.info(f"[SLDVisionExtractor] PDF has {total_pages} pages, processing {pages_to_process}")
            
            for page_num in range(pages_to_process):
                page = doc[page_num]
                # High quality rendering
                matrix = fitz.Matrix(
                    self.EXTRACTION_CONFIG['image_quality'] / 72, 
                    self.EXTRACTION_CONFIG['image_quality'] / 72
                )
                pix = page.get_pixmap(matrix=matrix)
                img_bytes = pix.tobytes("png")
                img_base64 = base64.b64encode(img_bytes).decode('utf-8')
                images.append(img_base64)
            
            doc.close()
            
        elif file_ext in ['.png', '.jpg', '.jpeg']:
            # Image file
            with open(file_path, 'rb') as f:
                img_bytes = f.read()
                img_base64 = base64.b64encode(img_bytes).decode('utf-8')
                images.append(img_base64)
        
        else:
            logger.warning(f"[SLDVisionExtractor] Unsupported file format: {file_ext}")
        
        return images
    
    def _extract_page_equipment(self, image_base64: str, page_num: int, 
                                datasheet_types: List[str] = None) -> Dict:
        """
        Extract equipment from single SLD page using Vision AI
        """
        
        # Build equipment types filter
        equipment_filter = ""
        if datasheet_types:
            equipment_filter = f"\nFOCUS ON THESE EQUIPMENT TYPES: {', '.join(datasheet_types)}"
        
        system_prompt = f"""You are an expert electrical engineering assistant specialized in Single Line Diagram (SLD) analysis.

TASK: Extract all electrical equipment visible in this SLD drawing.

EQUIPMENT TYPES TO DETECT:
{json.dumps(self.EQUIPMENT_TYPES, indent=2)}
{equipment_filter}

EXTRACTION RULES:
- Extract ALL visible equipment symbols and labels
- Capture equipment tags/designations (e.g., T1, CB-01, M-001)
- Extract ratings and specifications shown on diagram
- Identify voltage levels (11kV, 415V, 230V, etc.)
- Note connections between equipment
- Preserve units exactly as shown (kV, kVA, MW, A, etc.)
- Do NOT hallucinate - only extract visible data
- Return null if information not shown

OUTPUT FORMAT: Valid JSON only"""

        user_prompt = f"""Analyze this Single Line Diagram (Page {page_num}) and extract all electrical equipment.

Return JSON with this structure:
{{
  "equipment": [
    {{
      "type": "transformer|diesel_generator|switchgear_11kv|circuit_breaker|motor|vfd|ups|capacitor_bank",
      "tag": "Equipment tag or designation",
      "description": "Brief description from diagram",
      "voltage_primary": "Primary/input voltage with unit",
      "voltage_secondary": "Secondary/output voltage with unit",
      "power_rating": "Power rating with unit (kVA, MVA, kW, MW)",
      "current_rating": "Current rating with unit (A, kA)",
      "frequency": "Frequency (Hz)",
      "manufacturer": "Manufacturer if shown",
      "model": "Model number if shown",
      "busbar": "Connected busbar name",
      "feeder": "Feeder name if applicable",
      "location": "Location or area code if shown",
      "protection": "Protection devices (CB, relay, fuse)",
      "additional_specs": {{}}
    }}
  ],
  "metadata": {{
    "drawing_number": "Drawing number if shown",
    "drawing_title": "Drawing title",
    "revision": "Revision number",
    "voltage_levels": ["List of voltage levels in diagram"],
    "project": "Project name/code if shown"
  }}
}}

IMPORTANT:
1. Extract EVERY equipment symbol you can identify
2. Be precise with ratings and values
3. Include equipment connections (which busbar/feeder)
4. If a field is not visible, use null
5. Return valid JSON only (no markdown, no explanations)"""

        try:
            response = self.client.chat.completions.create(
                model=self.EXTRACTION_CONFIG['vision_model'],
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{image_base64}",
                                    "detail": self.EXTRACTION_CONFIG['image_detail']
                                }
                            }
                        ]
                    }
                ],
                temperature=self.EXTRACTION_CONFIG['temperature'],
                max_tokens=self.EXTRACTION_CONFIG['max_tokens']
            )
            
            result_text = response.choices[0].message.content
            
            # Parse JSON (handle markdown code blocks)
            result_text = result_text.strip()
            if result_text.startswith('```json'):
                result_text = result_text[7:]
            if result_text.startswith('```'):
                result_text = result_text[3:]
            if result_text.endswith('```'):
                result_text = result_text[:-3]
            result_text = result_text.strip()
            
            data = json.loads(result_text)
            
            # Add page number to each equipment
            if data.get('equipment'):
                for eq in data['equipment']:
                    eq['page'] = page_num
            
            logger.info(f"[SLDVisionExtractor] Page {page_num}: Found {len(data.get('equipment', []))} equipment")
            return data
            
        except json.JSONDecodeError as e:
            logger.error(f"[SLDVisionExtractor] JSON parse error on page {page_num}: {e}")
            logger.error(f"Response was: {result_text[:500]}")
            return {'equipment': [], 'metadata': {}}
            
        except Exception as e:
            logger.error(f"[SLDVisionExtractor] Vision API error on page {page_num}: {e}")
            return {'equipment': [], 'metadata': {}}
    
    def _organize_by_type(self, equipment_list: List[Dict]) -> Dict:
        """Organize equipment by type for easier processing"""
        by_type = {}
        
        for eq in equipment_list:
            eq_type = eq.get('type', 'unknown')
            if eq_type not in by_type:
                by_type[eq_type] = []
            by_type[eq_type].append(eq)
        
        return by_type
    
    def _fallback_extraction(self, error: str = None) -> Dict:
        """
        Fallback when Vision API is not available
        Returns empty structure with error message
        """
        logger.warning("[SLDVisionExtractor] Using fallback extraction (no AI)")
        
        return {
            'equipment': [],
            'equipment_by_type': {},
            'equipment_count': 0,
            'metadata': {},
            'extraction_method': 'fallback_no_ai',
            'confidence': 'none',
            'pages_processed': 0,
            'error': error or 'OpenAI API key not configured',
            'message': 'Vision AI extraction not available. Please configure OPENAI_API_KEY environment variable.'
        }
