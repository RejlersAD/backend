"""
Real P&ID Valve Extractor using HYBRID approach: OCR + OpenAI Vision
STEP 1: Extract ALL text using Tesseract/EasyOCR/PaddleOCR
STEP 2: Send OCR text + image to OpenAI Vision for intelligent structuring
"""
import logging
import os
import base64
from pathlib import Path
from typing import Dict, List
import fitz  # PyMuPDF
from PIL import Image
import io
from openai import OpenAI
from datetime import datetime
import pytesseract

logger = logging.getLogger(__name__)


class RealPIDExtractor:
    """
    HYBRID P&ID valve extractor: OCR engines + OpenAI Vision
    1. Multi-engine OCR extracts ALL raw text
    2. Vision AI structures and validates the OCR results
    """
    
    def __init__(self):
        self.openai_client = None
        self.easyocr_reader = None
        self.paddleocr_reader = None
        
        # Initialize OpenAI
        try:
            api_key = os.getenv('OPENAI_API_KEY')
            if api_key:
                self.openai_client = OpenAI(api_key=api_key)
                logger.info("✅ OpenAI Vision initialized")
            else:
                logger.warning("⚠️ No OPENAI_API_KEY found")
        except Exception as e:
            logger.warning(f"⚠️ Failed to initialize OpenAI: {e}")
        
        # Initialize OCR engines (best-effort)
        self._init_ocr_engines()
    
    def _init_ocr_engines(self):
        """Initialize OCR engines (Tesseract, EasyOCR, PaddleOCR) - best effort"""
        # Try EasyOCR
        try:
            import easyocr
            self.easyocr_reader = easyocr.Reader(['en'], gpu=False, verbose=False)
            logger.info("✅ EasyOCR initialized")
        except Exception as e:
            logger.warning(f"⚠️ EasyOCR not available: {e}")
        
        # Try PaddleOCR
        try:
            from paddleocr import PaddleOCR
            os.environ['DISABLE_MODEL_SOURCE_CHECK'] = 'True'
            self.paddleocr_reader = PaddleOCR(lang='en', use_angle_cls=False, show_log=False)
            logger.info("✅ PaddleOCR initialized")
        except Exception as e:
            logger.warning(f"⚠️ PaddleOCR not available: {e}")
        
        # Tesseract (usually available)
        try:
            pytesseract.get_tesseract_version()
            logger.info("✅ Tesseract OCR available")
        except Exception as e:
            logger.warning(f"⚠️ Tesseract not available: {e}")
    
    def _extract_text_with_ocr(self, pdf_path: str) -> str:
        """
        Extract ALL text from PDF using multiple OCR engines
        Priority: EasyOCR > PaddleOCR > Tesseract
        Returns combined text from all engines
        """
        logger.info("📝 [OCR] Extracting text from PDF using multi-engine OCR...")
        
        try:
            # Convert PDF to image
            doc = fitz.open(pdf_path)
            page = doc[0]
            
            # High resolution for OCR (2.5x for better text detection)
            zoom = 2.5
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            
            # Convert to PIL Image
            img_data = pix.tobytes("png")
            img = Image.open(io.BytesIO(img_data))
            
            ocr_texts = []
            
            # Try EasyOCR (best for engineering drawings)
            if self.easyocr_reader:
                try:
                    results = self.easyocr_reader.readtext(img_data, detail=0, paragraph=False)
                    easy_text = ' '.join(results)
                    if easy_text.strip():
                        ocr_texts.append(f"[EasyOCR Results]\n{easy_text}")
                        logger.info(f"✅ EasyOCR extracted {len(results)} text elements")
                except Exception as e:
                    logger.warning(f"⚠️ EasyOCR failed: {e}")
            
            # Try PaddleOCR
            if self.paddleocr_reader:
                try:
                    results = self.paddleocr_reader.ocr(img_data, cls=False)
                    if results and results[0]:
                        paddle_text = ' '.join([line[1][0] for line in results[0]])
                        if paddle_text.strip():
                            ocr_texts.append(f"[PaddleOCR Results]\n{paddle_text}")
                            logger.info(f"✅ PaddleOCR extracted {len(results[0])} text elements")
                except Exception as e:
                    logger.warning(f"⚠️ PaddleOCR failed: {e}")
            
            # Try Tesseract
            try:
                tess_text = pytesseract.image_to_string(img, config='--psm 11')
                if tess_text.strip():
                    ocr_texts.append(f"[Tesseract Results]\n{tess_text}")
                    logger.info(f"✅ Tesseract extracted text")
            except Exception as e:
                logger.warning(f"⚠️ Tesseract failed: {e}")
            
            doc.close()
            
            # Combine all OCR results
            combined_text = '\n\n'.join(ocr_texts)
            
            if combined_text.strip():
                logger.info(f"✅ [OCR] Total extracted text length: {len(combined_text)} chars")
                logger.info(f"📝 [OCR] Text preview: {combined_text[:200]}...")
                return combined_text
            else:
                logger.warning("⚠️ [OCR] No text extracted by any engine")
                return ""
                
        except Exception as e:
            logger.error(f"❌ [OCR] Failed: {e}", exc_info=True)
            return ""
    
    def extract_valves_from_pdf(self, pdf_path: str, original_filename: str = None, valve_type: str = None) -> Dict:
        """
        HYBRID extraction: OCR + OpenAI Vision
        
        STEP 1: Extract all text using OCR engines (Tesseract, EasyOCR, PaddleOCR)
        STEP 2: Send OCR text + image to Vision for intelligent structuring
        
        Args:
            pdf_path: Path to P&ID PDF
            original_filename: Original filename (for P&ID number extraction)
            valve_type: Filter for specific valve type ('SDV', 'MOV', or None for all)
        
        Returns:
            Dict with valves list and drawing info
        """
        if not self.openai_client:
            raise ValueError("OpenAI client not initialized")
        
        logger.info(f"🚀 [RealPIDExtractor] Starting HYBRID extraction (OCR + Vision) from: {original_filename or pdf_path}")
        logger.info(f"🎯 [RealPIDExtractor] Target valve type: {valve_type or 'ALL'}")
        
        try:
            # STEP 1: Run multi-engine OCR to extract ALL text
            logger.info("📝 [RealPIDExtractor] STEP 1: Running OCR engines...")
            ocr_text = self._extract_text_with_ocr(pdf_path)
            
            if ocr_text:
                logger.info(f"✅ [RealPIDExtractor] OCR extracted {len(ocr_text)} characters")
            else:
                logger.warning("⚠️ [RealPIDExtractor] OCR returned no text, Vision will work alone")
            
            # STEP 2: Convert PDF to image for Vision
            logger.info("🖼️ [RealPIDExtractor] STEP 2: Preparing image for Vision...")
            # Open PDF and convert first page to image
            doc = fitz.open(pdf_path)
            page = doc[0]  # Process first page (main drawing)
            
            # High-resolution rendering for better OCR
            pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            
            # Convert to base64 for OpenAI Vision
            buffered = io.BytesIO()
            img.save(buffered, format="PNG")
            img_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
            
            logger.info("📸 [RealPIDExtractor] Image prepared, calling OpenAI Vision with OCR context...")
            
            # Build intelligent prompt with OCR context
            valve_filter_text = f"Focus specifically on {valve_type} type valves." if valve_type else "Extract all valve types (SDV, MOV, PSV, etc.)."
            
            ocr_context = ""
            if ocr_text:
                ocr_context = f"""\n\n=== OCR EXTRACTED TEXT ===
The following text was extracted from the drawing using OCR engines:

{ocr_text[:3000]}\n
=== END OCR TEXT ===

Use this OCR text to help identify valve tag numbers, line numbers, and other text in the drawing.
"""
            
            prompt = f"""You are an expert P&ID (Piping & Instrumentation Diagram) analyst. 
Analyze this P&ID drawing and extract ALL valve information.
{ocr_context}
{valve_filter_text}

For EACH valve you find, extract:
1. Tag Number (e.g., SDV-XXX-NNN, MOV-XXX-NNN, XV-XXX-NNN — use EXACT tags from drawing)
2. Valve Type (SDV, MOV, PSV, XV, etc.) - from the tag prefix
3. Line Number (the piping line it's on, e.g., 6"-GA-100-1501-A2B)
4. Service/Description (what the valve controls, e.g., "Main Gas Line Shutdown")
5. Location/Area on drawing
6. Any visible specifications (size, class, fail position if shown)

Return the data as a JSON array with this structure:
[
  {{
    "tag_no": "SDV-100-001",
    "tag": "SDV-100-001",
    "type": "SDV",
    "line_no": "6\\"-GA-100-1501-A2B",
    "service": "Natural Gas Main Line Shutdown",
    "location": "Main Gas Line Inlet",
    "piping_class": "ASME B16.5 150#",
    "notes": "any additional info visible"
  }}
]

Be thorough - extract EVERY valve visible in the drawing. Return ONLY the JSON array, no other text."""
            
            # Call OpenAI Vision API
            response = self.openai_client.chat.completions.create(
                model="gpt-4o",  # GPT-4 Vision model
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
                max_tokens=4000,
                temperature=0.1  # Low temperature for factual extraction
            )
            
            # Parse response
            content = response.choices[0].message.content.strip()
            logger.info(f"📥 [RealPIDExtractor] Received response from OpenAI Vision")
            
            # Extract JSON from response (handle markdown code blocks)
            import json
            import re
            
            # Remove markdown code blocks if present
            content = re.sub(r'```json\s*', '', content)
            content = re.sub(r'```\s*', '', content)
            content = content.strip()
            
            try:
                valves_list = json.loads(content)
            except json.JSONDecodeError as e:
                logger.error(f"❌ [RealPIDExtractor] Failed to parse JSON: {e}")
                logger.error(f"Response content: {content[:500]}")
                raise ValueError(f"Failed to parse Vision API response: {e}")
            
            # Filter by valve type if specified
            if valve_type:
                original_count = len(valves_list)
                valves_list = [
                    v for v in valves_list 
                    if v.get('type', '').upper() == valve_type.upper() or 
                       valve_type.upper() in v.get('tag_no', '').upper()
                ]
                logger.info(f"🔍 [RealPIDExtractor] Filtered {original_count} valves → {len(valves_list)} {valve_type} valves")
            
            # Extract P&ID number from filename
            pid_no = self._extract_pid_number(original_filename or pdf_path)
            
            # Build result structure
            result = {
                'valves': valves_list,
                'drawing_info': {
                    'pid_no': pid_no,
                    'date': datetime.now().strftime('%d-%b-%Y'),
                    'extraction_method': 'HYBRID: OCR (Tesseract/EasyOCR/PaddleOCR) + OpenAI Vision',
                    'ocr_text_length': len(ocr_text) if ocr_text else 0,
                    'source_file': original_filename or os.path.basename(pdf_path)
                }
            }
            
            logger.info(f"✅ [RealPIDExtractor] Successfully extracted {len(valves_list)} valves")
            if valves_list:
                logger.info(f"📋 [RealPIDExtractor] Sample valve: {valves_list[0].get('tag_no')} ({valves_list[0].get('type')})")
            
            return result
            
        except Exception as e:
            logger.error(f"❌ [RealPIDExtractor] Extraction failed: {e}", exc_info=True)
            raise
    
    def _extract_pid_number(self, filename: str) -> str:
        """Extract P&ID number from filename"""
        if not filename:
            return 'UNKNOWN-PID'
        
        # Remove extension and use as P&ID number
        pid_no = Path(filename).stem
        logger.info(f"📄 [RealPIDExtractor] P&ID No from filename: {pid_no}")
        return pid_no
