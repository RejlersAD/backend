"""
HMB Table Extractor using OpenAI Vision
STRICT ACCURACY MODE - No hallucination, only extract visible data
"""
import logging
import base64
import json
from typing import Dict, List
from openai import OpenAI
import os
import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


class HMBVisionExtractor:
    """
    Extract HMB stream data using Vision model
    Rules:
    - Extract only visible values
    - Do NOT interpret or calculate
    - Do NOT normalize unless explicit
    - Preserve units exactly as written
    - Return null if value not found
    """
    
    def __init__(self):
        self.client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
    
    def extract_from_pdf(self, pdf_path: str) -> Dict:
        """
        Extract structured stream data from HMB PDF
        
        Args:
            pdf_path: Path to HMB PDF file
            
        Returns:
            Dict with structured stream data
        """
        logger.info("[HMBVisionExtractor] Starting HMB extraction...")
        
        try:
            # Convert PDF pages to images
            images = self._pdf_to_images(pdf_path)
            logger.info(f"[HMBVisionExtractor] Converted {len(images)} pages to images")
            
            # Extract tables from each page using Vision
            all_streams = []
            process_conditions = {}
            
            for page_num, image_base64 in enumerate(images, 1):
                logger.info(f"[HMBVisionExtractor] Processing page {page_num}...")
                
                page_data = self._extract_page_data(image_base64, page_num)
                
                if page_data.get('streams'):
                    all_streams.extend(page_data['streams'])
                
                if page_data.get('process_conditions'):
                    process_conditions.update(page_data['process_conditions'])
            
            result = {
                'streams': all_streams,
                'process_conditions': process_conditions,
                'extraction_method': 'vision',
                'confidence': 'high' if all_streams else 'low'
            }
            
            logger.info(f"[HMBVisionExtractor] ✅ Extracted {len(all_streams)} streams")
            return result
            
        except Exception as e:
            logger.error(f"[HMBVisionExtractor] ❌ Error: {e}")
            # Return empty structure on failure
            return {
                'streams': [],
                'process_conditions': {},
                'extraction_method': 'vision_failed',
                'confidence': 'none',
                'error': str(e)
            }
    
    def _pdf_to_images(self, pdf_path: str, max_pages: int = 15) -> List[str]:
        """
        Convert PDF pages to base64 encoded images
        Increased to 15 pages to handle cover pages and find actual data
        """
        images = []
        doc = fitz.open(pdf_path)
        
        total_pages = len(doc)
        pages_to_process = min(total_pages, max_pages)
        
        logger.info(f"[HMBVisionExtractor] PDF has {total_pages} pages, processing first {pages_to_process}")
        
        for page_num in range(pages_to_process):
            page = doc[page_num]
            # Render page to image (300 DPI for good quality)
            pix = page.get_pixmap(matrix=fitz.Matrix(300/72, 300/72))
            img_bytes = pix.tobytes("png")
            img_base64 = base64.b64encode(img_bytes).decode('utf-8')
            images.append(img_base64)
        
        doc.close()
        return images
    
    def _extract_page_data(self, image_base64: str, page_num: int) -> Dict:
        """
        Extract structured data from single HMB page using Vision model
        """
        
        system_prompt = """You are an expert engineering data extraction assistant for Heat and Material Balance (HMB) documents.

INTELLIGENT EXTRACTION RULES:
- Extract all visible stream data from tables
- Read column headers to understand what each field represents
- Match stream/line identifiers carefully
- Preserve units exactly as written (barg, bara, °C, °F, etc.)
- Use engineering judgment to interpret table structure
- If multiple tables exist, extract from all of them
- If units are shown in headers (e.g., "Pressure (barg)"), apply to all rows
- Return null only if field is truly empty or not present
- Return ONLY valid JSON"""

        user_prompt = f"""Extract ALL stream data from this HMB document (Page {page_num}).

This page may be a cover page, title page, or table of contents. If so, return empty arrays.
If this page contains stream/line data tables, extract them thoroughly.

Look for tables containing:
- Stream/Line identifiers (Stream No., Line No., Tag, etc.)
- Fluid/Chemical names  
- Phase information (Gas, Liquid, Two-Phase, Mixed)
- Operating conditions (pressure, temperature)
- Design conditions
- Shut-off pressure or relief conditions

Return JSON in this structure (even if arrays are empty):
{{
  "streams": [
    {{
      "stream_id": "Stream identifier or number",
      "line_no": "Line number if different from stream_id",
      "fluid": "Fluid/chemical name",
      "phase": "Gas/Liquid/Two-Phase/Mixed",
      "state": "Normal/Supercritical/etc",
      "pressure_normal": "Operating pressure value",
      "pressure_design": "Design pressure value",
      "pressure_unit": "barg/bara/psig/etc",
      "temp_min": "Minimum operating temperature",
      "temp_max": "Maximum operating temperature",
      "temp_unit": "°C/°F/K",
      "design_temp_min": "Minimum design temperature",
      "design_temp_max": "Maximum design temperature",
      "design_temp_unit": "°C/°F/K",
      "shut_off_pressure": "Shut-off or relief pressure with unit"
    }}
  ],
  "process_conditions": {{
    "ambient_temp_min": "Minimum ambient temperature",
    "ambient_temp_max": "Maximum ambient temperature",
    "ambient_temp_unit": "°C/°F"
  }}
}}

Instructions:
1. If this is a cover/title page, return: {{"streams": [], "process_conditions": {{}}}}
2. If stream tables exist, extract EVERY row
3. Use table headers to identify columns
4. If units are in headers, extract them
5. If a field is not in the table, use null"""

        try:
            # IMPORTANT: gpt-4o Vision doesn't support response_format with images
            # We'll parse JSON from markdown code blocks
            response = self.client.chat.completions.create(
                model="gpt-4o",  # Vision model
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
                                    "detail": "high"
                                }
                            }
                        ]
                    }
                ],
                temperature=0.1,
                max_tokens=4096
                # NOTE: No response_format parameter when using images!
            )
            
            result_text = response.choices[0].message.content
            
            # Debug logging
            if result_text is None:
                logger.error(f"[HMBVisionExtractor] Page {page_num}: OpenAI returned None! Response: {response}")
                logger.error(f"[HMBVisionExtractor] Finish reason: {response.choices[0].finish_reason if response.choices else 'NO CHOICES'}")
                return {'streams': [], 'process_conditions': {}}
            
            logger.info(f"[HMBVisionExtractor] Page {page_num} raw response length: {len(result_text)}")
            
            # Extract JSON from markdown code blocks if present
            if '```json' in result_text:
                result_text = result_text.split('```json')[1].split('```')[0].strip()
            elif '```' in result_text:
                result_text = result_text.split('```')[1].split('```')[0].strip()
            
            page_data = json.loads(result_text)
            
            logger.info(f"[HMBVisionExtractor] Page {page_num}: Found {len(page_data.get('streams', []))} streams")
            return page_data
            
        except json.JSONDecodeError as e:
            logger.error(f"[HMBVisionExtractor] Page {page_num} JSON decode error: {e}")
            logger.error(f"[HMBVisionExtractor] Raw text was: {result_text if 'result_text' in locals() else 'NOT SET'}")
            return {'streams': [], 'process_conditions': {}}
        except Exception as e:
            logger.error(f"[HMBVisionExtractor] Page {page_num} error: {e}")
            logger.error(f"[HMBVisionExtractor] Error type: {type(e).__name__}")
            import traceback
            logger.error(f"[HMBVisionExtractor] Traceback: {traceback.format_exc()}")
            return {'streams': [], 'process_conditions': {}}
