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
        Extract ALL text from ALL pages of the PDF using multiple OCR engines.
        Priority: EasyOCR > PaddleOCR > Tesseract
        Returns combined text from all engines across all pages.
        """
        logger.info("📝 [OCR] Extracting text from ALL pages using multi-engine OCR...")

        try:
            doc = fitz.open(pdf_path)
            num_pages = len(doc)
            logger.info(f"📄 [OCR] PDF has {num_pages} page(s)")

            all_page_texts = []

            for page_num in range(num_pages):
                page = doc[page_num]

                # High resolution for OCR (3.0x for better text detection in circles)
                zoom = 3.0
                mat = fitz.Matrix(zoom, zoom)
                pix = page.get_pixmap(matrix=mat)

                # Convert to PIL Image
                img_data = pix.tobytes("png")
                img = Image.open(io.BytesIO(img_data))

                page_texts = []

                # Try EasyOCR (best for engineering drawings with circles/bubbles)
                if self.easyocr_reader:
                    try:
                        results = self.easyocr_reader.readtext(img_data, detail=0, paragraph=False)
                        easy_text = ' '.join(results)
                        if easy_text.strip():
                            page_texts.append(f"[EasyOCR Page {page_num + 1}]\n{easy_text}")
                            logger.info(f"✅ EasyOCR page {page_num + 1}: {len(results)} text elements")
                    except Exception as e:
                        logger.warning(f"⚠️ EasyOCR page {page_num + 1} failed: {e}")

                # Try PaddleOCR
                if self.paddleocr_reader:
                    try:
                        results = self.paddleocr_reader.ocr(img_data, cls=False)
                        if results and results[0]:
                            paddle_text = ' '.join([line[1][0] for line in results[0]])
                            if paddle_text.strip():
                                page_texts.append(f"[PaddleOCR Page {page_num + 1}]\n{paddle_text}")
                                logger.info(f"✅ PaddleOCR page {page_num + 1}: {len(results[0])} text elements")
                    except Exception as e:
                        logger.warning(f"⚠️ PaddleOCR page {page_num + 1} failed: {e}")

                # Tesseract with PSM 11 (sparse text) + PSM 6 for denser regions
                for psm in [11, 6]:
                    try:
                        tess_text = pytesseract.image_to_string(img, config=f'--psm {psm}')
                        if tess_text.strip():
                            page_texts.append(f"[Tesseract PSM{psm} Page {page_num + 1}]\n{tess_text}")
                            logger.info(f"✅ Tesseract PSM{psm} page {page_num + 1}: extracted text")
                            break
                    except Exception as e:
                        logger.warning(f"⚠️ Tesseract PSM{psm} page {page_num + 1} failed: {e}")

                if page_texts:
                    all_page_texts.extend(page_texts)

            doc.close()

            combined_text = '\n\n'.join(all_page_texts)

            if combined_text.strip():
                logger.info(f"✅ [OCR] Total extracted text: {len(combined_text)} chars across {num_pages} page(s)")
                logger.info(f"📝 [OCR] Text preview: {combined_text[:200]}...")
                return combined_text
            else:
                logger.warning("⚠️ [OCR] No text extracted by any engine on any page")
                return ""

        except Exception as e:
            logger.error(f"❌ [OCR] Failed: {e}", exc_info=True)
            return ""

    def extract_valves_from_pdf(self, pdf_path: str, original_filename: str = None, valve_type: str = None) -> dict:
        """
        HYBRID extraction: Multi-engine OCR + OpenAI Vision across ALL pages.

        STEP 1: Extract all text from ALL pages using OCR engines
        STEP 2: For each page, send OCR context + page image to Vision API
        STEP 3: Merge valve lists from all pages, deduplicate by tag_no

        Args:
            pdf_path: Path to P&ID PDF
            original_filename: Original filename (for P&ID number extraction)
            valve_type: Filter for specific valve type ('SDV', 'MOV', or None for all)

        Returns:
            Dict with valves list and drawing info
        """
        if not self.openai_client:
            raise ValueError("OpenAI client not initialized")

        logger.info(f"🚀 [RealPIDExtractor] Starting HYBRID extraction from: {original_filename or pdf_path}")
        logger.info(f"🎯 [RealPIDExtractor] Target valve type: {valve_type or 'ALL'}")

        try:
            # STEP 1: Run multi-engine OCR on ALL pages
            logger.info("📝 [RealPIDExtractor] STEP 1: Running OCR on all pages...")
            ocr_text = self._extract_text_with_ocr(pdf_path)

            if ocr_text:
                logger.info(f"✅ [RealPIDExtractor] OCR extracted {len(ocr_text)} characters")
            else:
                logger.warning("⚠️ [RealPIDExtractor] OCR returned no text, Vision will work alone")

            # STEP 2: Process each page with Vision API
            logger.info("🖼️ [RealPIDExtractor] STEP 2: Processing all pages with Vision AI...")

            doc = fitz.open(pdf_path)
            num_pages = len(doc)
            logger.info(f"📄 [RealPIDExtractor] PDF has {num_pages} page(s)")

            valve_filter_text = (
                f"Focus specifically on {valve_type} type valves." if valve_type
                else "Extract ALL valve types (SDV, MOV, PSV, XV, etc.)."
            )

            all_valves = []
            seen_tag_nos = set()

            import json
            import re

            for page_num in range(num_pages):
                page = doc[page_num]
                logger.info(f"🔍 [RealPIDExtractor] Processing page {page_num + 1}/{num_pages}...")

                # High-resolution rendering (3.0x to read small text inside circles)
                pix = page.get_pixmap(matrix=fitz.Matrix(3.0, 3.0))
                img = Image.open(io.BytesIO(pix.tobytes("png")))

                # Trim image size to stay within API token limits (~4000x4000 max)
                max_dim = 4096
                if img.width > max_dim or img.height > max_dim:
                    scale = max_dim / max(img.width, img.height)
                    img = img.resize(
                        (int(img.width * scale), int(img.height * scale)),
                        Image.LANCZOS
                    )

                buffered = io.BytesIO()
                img.save(buffered, format="PNG")
                img_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')

                ocr_context = ""
                if ocr_text:
                    # Include OCR text segment (first 3000 chars shared across all pages)
                    ocr_context = f"""\n\n=== OCR EXTRACTED TEXT (All Pages) ===
{ocr_text[:3000]}
=== END OCR TEXT ===

Use this OCR text to help identify valve tag numbers, line numbers, and other text in the drawing.
"""

                prompt = f"""You are an expert P&ID (Piping & Instrumentation Diagram) analyst.
Analyze page {page_num + 1} of this P&ID drawing and extract ALL valve information.
{ocr_context}
{valve_filter_text}

IMPORTANT: Valve tag numbers in P&ID drawings are typically written INSIDE CIRCLES or BUBBLES.
Look carefully for any circle symbols (○ ◎ ⊙) containing text — these are valve tags.
Tag formats vary: MOV-8001, MOV-200-001, SDV-100A, XV-5001 etc. Extract EXACTLY as shown.

For EACH valve you find, extract:
1. Tag Number — copy EXACTLY from the circle/bubble in the drawing
2. Valve Type (MOV, SDV, PSV, XV, etc.) — inferred from tag prefix
3. Line Number (the piping line it connects to, e.g. 6\"-GA-100-1501-A2B)
4. Service/Description (what the valve controls)
5. Location/Area on drawing
6. Any visible specifications (size, class, fail position)

Return a JSON array ONLY (no other text):
[
  {{
    "tag_no": "<exact tag from drawing circle>",
    "tag": "<exact tag from drawing circle>",
    "type": "<prefix, e.g. MOV>",
    "line_no": "<piping line number>",
    "service": "<valve service/description>",
    "location": "<area on drawing>",
    "piping_class": "<if visible>",
    "notes": "<other visible info>"
  }}
]

If no valves are visible on this page, return an empty array [].
"""

                try:
                    response = self.openai_client.chat.completions.create(
                        model="gpt-4o",
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
                        temperature=0.1
                    )

                    content = response.choices[0].message.content.strip()
                    logger.info(f"[RealPIDExtractor] Page {page_num + 1} raw response (first 400 chars): {content[:400]}")
                    content = re.sub(r'```json\s*', '', content)
                    content = re.sub(r'```\s*', '', content)
                    content = content.strip()

                    # If response starts with prose instead of JSON, try to find the array
                    if not content.startswith('['):
                        bracket = content.find('[')
                        if bracket != -1:
                            content = content[bracket:]
                            logger.warning(f"[RealPIDExtractor] Page {page_num + 1}: JSON array found at offset {bracket}")

                    page_valves = json.loads(content)

                    # Deduplicate by tag_no across pages
                    new_count = 0
                    for valve in page_valves:
                        tag = (valve.get('tag_no') or valve.get('tag') or '').strip().upper()
                        if tag and tag not in seen_tag_nos:
                            seen_tag_nos.add(tag)
                            all_valves.append(valve)
                            new_count += 1
                        elif not tag:
                            all_valves.append(valve)
                            new_count += 1

                    logger.info(f"✅ [RealPIDExtractor] Page {page_num + 1}: {len(page_valves)} valves found, {new_count} new (total: {len(all_valves)})")

                except json.JSONDecodeError as e:
                    logger.error(f"[RealPIDExtractor] Failed to parse JSON for page {page_num + 1}: {e}. Raw content: {content[:300]}")
                except Exception as e:
                    logger.error(f"[RealPIDExtractor] Vision API failed for page {page_num + 1}: {e}")

            doc.close()

            valves_list = all_valves

            # Filter by valve type if specified (broad match: type field OR prefix in tag_no)
            if valve_type:
                original_count = len(valves_list)
                vt_upper = valve_type.upper()
                valves_list = [
                    v for v in valves_list
                    if v.get('type', '').upper() == vt_upper
                    or v.get('tag_no', '').upper().startswith(vt_upper)
                    or v.get('tag', '').upper().startswith(vt_upper)
                ]
                logger.info(f"🔍 [RealPIDExtractor] Filtered {original_count} → {len(valves_list)} {valve_type} valves")

            # Extract P&ID number from filename
            pid_no = self._extract_pid_number(original_filename or pdf_path)

            result = {
                'valves': valves_list,
                'drawing_info': {
                    'pid_no': pid_no,
                    'date': datetime.now().strftime('%d-%b-%Y'),
                    'extraction_method': f'HYBRID: OCR (Tesseract/EasyOCR/PaddleOCR) + OpenAI Vision ({num_pages} page(s))',
                    'ocr_text_length': len(ocr_text) if ocr_text else 0,
                    'source_file': original_filename or os.path.basename(pdf_path),
                    'pages_processed': num_pages,
                }
            }

            logger.info(f"✅ [RealPIDExtractor] Complete: {len(valves_list)} valves from {num_pages} page(s)")
            if valves_list:
                logger.info(f"📋 [RealPIDExtractor] All tags found: {[v.get('tag_no') for v in valves_list]}")

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
