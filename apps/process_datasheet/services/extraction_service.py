"""
PDF Extraction Service
Multi-modal PDF extraction using GPT-4 Vision and traditional parsing
"""
import os
import io
import json
import base64
from typing import Dict, Any, List, Tuple, Optional
from decimal import Decimal
import logging

try:
    import PyPDF2
    import pdfplumber
    from PIL import Image
except ImportError:
    PyPDF2 = None
    pdfplumber = None
    Image = None

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

logger = logging.getLogger(__name__)


class ExtractionService:
    """
    PDF extraction service for process datasheets
    Uses multi-modal approach: Text extraction + Vision AI
    """
    
    def __init__(self):
        """Initialize extraction service"""
        self.openai_client = None
        if OpenAI:
            api_key = os.getenv('OPENAI_API_KEY')
            if api_key:
                self.openai_client = OpenAI(api_key=api_key)
    
    def extract_from_pdf(self, pdf_path: str, equipment_config: Dict) -> Dict[str, Any]:
        """
        Extract datasheet information from PDF
        
        Args:
            pdf_path: Path to PDF file
            equipment_config: Equipment type configuration
            
        Returns:
            Extracted data with confidence scores
        """
        results = {
            'success': False,
            'data': {},
            'confidence': {},
            'method': 'unknown',
            'errors': []
        }
        
        try:
            # Try text-based extraction first (faster)
            text_data = self._extract_text_based(pdf_path, equipment_config)
            
            if text_data['confidence_avg'] > 0.7:
                results['success'] = True
                results['data'] = text_data['data']
                results['confidence'] = text_data['confidence']
                results['method'] = 'text'
                return results
            
            # If text extraction low confidence, try vision-based
            if self.openai_client:
                vision_data = self._extract_vision_based(pdf_path, equipment_config)
                
                # Merge results with higher confidence wins
                merged_data = self._merge_extractions(text_data, vision_data)
                
                results['success'] = True
                results['data'] = merged_data['data']
                results['confidence'] = merged_data['confidence']
                results['method'] = 'hybrid'
                return results
            else:
                # No vision available, use text extraction
                results['success'] = True
                results['data'] = text_data['data']
                results['confidence'] = text_data['confidence']
                results['method'] = 'text_only'
                results['errors'].append('OpenAI API not configured for vision extraction')
                return results
                
        except Exception as e:
            logger.error(f"Extraction failed: {str(e)}")
            results['errors'].append(str(e))
            return results
    
    def _extract_text_based(self, pdf_path: str, equipment_config: Dict) -> Dict[str, Any]:
        """
        Extract using traditional text parsing
        """
        if not pdfplumber:
            raise ImportError("pdfplumber not installed")
        
        extracted_data = {}
        confidence_scores = {}
        
        try:
            with pdfplumber.open(pdf_path) as pdf:
                # Extract all text
                full_text = ""
                for page in pdf.pages:
                    full_text += page.extract_text() or ""
                
                # Extract tables
                tables = []
                for page in pdf.pages:
                    page_tables = page.extract_tables()
                    if page_tables:
                        tables.extend(page_tables)
                
                # Map fields using patterns and keywords
                sections = equipment_config.get('sections', [])
                
                for section in sections:
                    section_id = section['id']
                    extracted_data[section_id] = {}
                    
                    for field in section.get('fields', []):
                        field_id = field['id']
                        field_label = field['label']
                        
                        # Try to find field in text
                        value, confidence = self._find_field_in_text(
                            full_text,
                            tables,
                            field_label,
                            field.get('type', 'text')
                        )
                        
                        if value:
                            # Handle value/unit structure
                            if 'unit' in field:
                                extracted_data[section_id][field_id] = {
                                    'value': value,
                                    'unit': field['unit']
                                }
                            else:
                                extracted_data[section_id][field_id] = value
                            
                            confidence_scores[field_id] = confidence
            
            # Calculate average confidence
            avg_confidence = sum(confidence_scores.values()) / len(confidence_scores) if confidence_scores else 0.0
            
            return {
                'data': extracted_data,
                'confidence': confidence_scores,
                'confidence_avg': avg_confidence
            }
            
        except Exception as e:
            logger.error(f"Text extraction error: {str(e)}")
            return {
                'data': {},
                'confidence': {},
                'confidence_avg': 0.0
            }
    
    def _find_field_in_text(self, text: str, tables: List, label: str, 
                           field_type: str) -> Tuple[Optional[str], float]:
        """
        Find field value in extracted text using pattern matching
        """
        import re
        
        # Common patterns for each field type
        patterns = {
            'tag_number': r'(?:Tag|Tag Number|Item)[:\s]+([A-Z0-9-]+)',
            'pressure': r'(?:' + re.escape(label) + r')[:\s]+([\d.]+)\s*(?:bar|psi)',
            'temperature': r'(?:' + re.escape(label) + r')[:\s]+([\d.-]+)\s*(?:°C|°F|C)',
            'flow': r'(?:' + re.escape(label) + r')[:\s]+([\d.]+)\s*(?:m³/h|kg/h)',
            'generic': re.escape(label) + r'[:\s]+([^\n]+)'
        }
        
        # Try label-specific pattern
        label_pattern = patterns.get(field_type, patterns['generic'])
        match = re.search(label_pattern, text, re.IGNORECASE)
        
        if match:
            value = match.group(1).strip()
            confidence = 0.8 if field_type != 'generic' else 0.6
            return value, confidence
        
        # Try to find in tables
        for table in tables:
            for row in table:
                if any(label.lower() in str(cell).lower() for cell in row if cell):
                    # Value likely in next cell
                    for cell in row:
                        if cell and cell.strip() and label.lower() not in cell.lower():
                            return str(cell).strip(), 0.7
        
        return None, 0.0
    
    def _extract_vision_based(self, pdf_path: str, equipment_config: Dict) -> Dict[str, Any]:
        """
        Extract using GPT-4 Vision
        """
        if not self.openai_client:
            return {'data': {}, 'confidence': {}, 'confidence_avg': 0.0}
        
        try:
            # Convert PDF first page to image
            image_data = self._pdf_to_image(pdf_path)
            
            # Create extraction prompt
            prompt = self._create_extraction_prompt(equipment_config)
            
            # Call GPT-4 Vision
            response = self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": prompt
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{image_data}"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=4000,
                temperature=0.1
            )
            
            # Parse response
            content = response.choices[0].message.content
            extracted_data = json.loads(content)
            
            return {
                'data': extracted_data.get('data', {}),
                'confidence': extracted_data.get('confidence', {}),
                'confidence_avg': extracted_data.get('confidence_avg', 0.8)
            }
            
        except Exception as e:
            logger.error(f"Vision extraction error: {str(e)}")
            return {'data': {}, 'confidence': {}, 'confidence_avg': 0.0}
    
    def _pdf_to_image(self, pdf_path: str, page_num: int = 0) -> str:
        """Convert PDF page to base64 image"""
        if not Image:
            raise ImportError("PIL not installed")
        
        try:
            import pdf2image
            images = pdf2image.convert_from_path(pdf_path, first_page=page_num+1, last_page=page_num+1)
            
            if images:
                buffer = io.BytesIO()
                images[0].save(buffer, format='PNG')
                img_bytes = buffer.getvalue()
                return base64.b64encode(img_bytes).decode('utf-8')
        except Exception:
            pass
        
        # Fallback: use pdfplumber to extract image
        if pdfplumber:
            try:
                with pdfplumber.open(pdf_path) as pdf:
                    page = pdf.pages[page_num]
                    img = page.to_image(resolution=150)
                    buffer = io.BytesIO()
                    img.save(buffer, format='PNG')
                    img_bytes = buffer.getvalue()
                    return base64.b64encode(img_bytes).decode('utf-8')
            except Exception as e:
                logger.error(f"Image conversion error: {str(e)}")
        
        raise ValueError("Could not convert PDF to image")
    
    def _create_extraction_prompt(self, equipment_config: Dict) -> str:
        """Create structured prompt for vision extraction"""
        sections = equipment_config.get('sections', [])
        
        field_descriptions = []
        for section in sections:
            section_name = section['name']
            for field in section.get('fields', []):
                field_descriptions.append(
                    f"- {field['label']} (ID: {field['id']}, Type: {field['type']})"
                )
        
        prompt = f"""You are a process engineering expert analyzing a datasheet document.

Equipment Type: {equipment_config.get('name')}
Description: {equipment_config.get('description')}

Extract the following information from this datasheet image:

{chr(10).join(field_descriptions)}

Return your response as a JSON object with this structure:
{{
  "data": {{
    "section_id": {{
      "field_id": {{"value": "extracted_value", "unit": "unit_if_applicable"}},
      ...
    }},
    ...
  }},
  "confidence": {{
    "field_id": confidence_score_0_to_1,
    ...
  }},
  "confidence_avg": average_confidence_score
}}

Important:
- Extract all values exactly as shown
- Include units where applicable
- If a field is not found, omit it from the response
- Assign confidence scores based on clarity (0.0-1.0)
- Be precise with technical values (pressures, temperatures, flow rates)

Return ONLY the JSON object, no additional text."""

        return prompt
    
    def _merge_extractions(self, text_result: Dict, vision_result: Dict) -> Dict[str, Any]:
        """
        Merge text and vision extraction results
        Higher confidence value wins for each field
        """
        merged_data = {}
        merged_confidence = {}
        
        # Combine data from both sources
        all_sections = set(list(text_result['data'].keys()) + list(vision_result['data'].keys()))
        
        for section_id in all_sections:
            merged_data[section_id] = {}
            
            text_section = text_result['data'].get(section_id, {})
            vision_section = vision_result['data'].get(section_id, {})
            
            all_fields = set(list(text_section.keys()) + list(vision_section.keys()))
            
            for field_id in all_fields:
                text_conf = text_result['confidence'].get(field_id, 0.0)
                vision_conf = vision_result['confidence'].get(field_id, 0.0)
                
                # Use higher confidence source
                if vision_conf > text_conf:
                    if field_id in vision_section:
                        merged_data[section_id][field_id] = vision_section[field_id]
                        merged_confidence[field_id] = vision_conf
                else:
                    if field_id in text_section:
                        merged_data[section_id][field_id] = text_section[field_id]
                        merged_confidence[field_id] = text_conf
        
        # Calculate average confidence
        avg_conf = sum(merged_confidence.values()) / len(merged_confidence) if merged_confidence else 0.0
        
        return {
            'data': merged_data,
            'confidence': merged_confidence,
            'confidence_avg': avg_conf
        }
    
    def extract_with_retry(self, pdf_path: str, equipment_config: Dict,
                          max_retries: int = 3) -> Dict[str, Any]:
        """
        Extract with automatic retry on failure
        """
        for attempt in range(max_retries):
            try:
                result = self.extract_from_pdf(pdf_path, equipment_config)
                
                if result['success']:
                    return result
                
                logger.warning(f"Extraction attempt {attempt + 1} failed, retrying...")
                
            except Exception as e:
                logger.error(f"Extraction attempt {attempt + 1} error: {str(e)}")
                
                if attempt == max_retries - 1:
                    return {
                        'success': False,
                        'data': {},
                        'confidence': {},
                        'method': 'failed',
                        'errors': [f'All {max_retries} attempts failed']
                    }
        
        return {
            'success': False,
            'data': {},
            'confidence': {},
            'method': 'failed',
            'errors': ['Maximum retries exceeded']
        }
