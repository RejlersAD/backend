"""
PID Verification V2 - Layer 1: Free Open-Source OCR Extraction
==============================================================
Implements multi-engine OCR extraction using free/open-source tools:
  - Tesseract OCR (pytesseract with PSM modes 11, 6, 3)
  - PyMuPDF (fitz) - word/block-level text extraction
  - pdfplumber - table extraction
  - Yellow-region OCR - highlighted area extraction

This layer is ALWAYS executed (free, no API costs).
"""

import os
import io
import re
import logging
import time
from typing import Dict, List, Tuple, Optional
from pathlib import Path
from decimal import Decimal
from PIL import Image, ImageOps, ImageEnhance
import fitz  # PyMuPDF
import pdfplumber

# OCR Engines
try:
    import pytesseract
    from pytesseract import Output
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False
    logging.warning("[Layer1] pytesseract not installed")

# Import configuration
from ..extraction_config import (
    LAYER1_OCR_CONFIG,
    EXTRACTION_PROFILES,
)

logger = logging.getLogger(__name__)


class Layer1OCRExtractor:
    """
    Layer 1: Free open-source OCR extraction service.
    
    Handles multi-page, multi-file extraction with:
      - Tesseract OCR (multiple PSM modes)
      - PyMuPDF word/block extraction
      - pdfplumber table extraction
      - Yellow-region highlighting detection
      - Spatial text grouping
      - Regex pattern matching
    """
    
    def __init__(self, extraction_profile='detailed'):
        """
        Initialize Layer 1 extractor.
        
        Args:
            extraction_profile: 'detailed' | 'legend' | 'tabular'
        """
        self.profile = EXTRACTION_PROFILES.get(extraction_profile, EXTRACTION_PROFILES['detailed'])
        self.config = LAYER1_OCR_CONFIG
        self.results = {
            'total_pages': 0,
            'total_processing_time': 0.0,
            'per_page_results': [],
            'aggregated_data': {},
            'engines_used': [],
        }
    
    def extract_from_pdf(self, pdf_path: str, file_type: str = 'pid_drawing') -> Dict:
        """
        Extract data from PDF file using all Layer 1 engines.
        
        Args:
            pdf_path: Path to PDF file
            file_type: 'pid_drawing' | 'legend_sheet' | 'equipment_list' | 'line_list' | 'pms'
        
        Returns:
            {
                'file_info': {...},
                'total_pages': int,
                'per_page_results': [...],
                'aggregated_data': {...},
                'processing_time': float,
                'engines_used': [str],
            }
        """
        start_time = time.time()
        logger.info(f"[Layer1] Starting extraction for {pdf_path} (type: {file_type})")
        
        # Open PDF with PyMuPDF for page count
        pdf_doc = fitz.open(pdf_path)
        total_pages = len(pdf_doc)
        pdf_doc.close()
        
        file_size = os.path.getsize(pdf_path)
        file_hash = self._compute_file_hash(pdf_path)
        
        self.results['total_pages'] = total_pages
        self.results['file_info'] = {
            'filename': os.path.basename(pdf_path),
            'file_size_bytes': file_size,
            'file_hash': file_hash,
            'page_count': total_pages,
            'file_type': file_type,
        }
        
        # Process each page
        for page_num in range(1, total_pages + 1):
            logger.info(f"[Layer1] Processing page {page_num}/{total_pages}")
            page_result = self._process_page(pdf_path, page_num, file_type)
            self.results['per_page_results'].append(page_result)
        
        # Aggregate data across all pages
        self.results['aggregated_data'] = self._aggregate_page_results()
        
        end_time = time.time()
        self.results['total_processing_time'] = round(end_time - start_time, 2)
        
        logger.info(f"[Layer1] Extraction complete: {total_pages} pages in {self.results['total_processing_time']}s")
        
        return self.results
    
    def _process_page(self, pdf_path: str, page_num: int, file_type: str) -> Dict:
        """
        Process a single page with all Layer 1 engines.
        
        Returns:
            {
                'page_num': int,
                'tesseract_result': {...},
                'pymupdf_result': {...},
                'pdfplumber_result': {...},
                'yellow_region_result': {...},
                'merged_items': {...},
                'confidence_score': float,
            }
        """
        page_start = time.time()
        page_result = {
            'page_num': page_num,
            'tesseract_result': {},
            'pymupdf_result': {},
            'pdfplumber_result': {},
            'yellow_region_result': {},
            'merged_items': {},
            'confidence_score': 0.0,
            'processing_time': 0.0,
        }
        
        # Convert PDF page to image for OCR
        page_image = self._pdf_page_to_image(pdf_path, page_num)
        
        # Engine 1: Tesseract OCR
        if TESSERACT_AVAILABLE and self._is_engine_enabled('pytesseract'):
            logger.info(f"[Layer1] Running Tesseract on page {page_num}")
            page_result['tesseract_result'] = self._run_tesseract(page_image, page_num)
            if 'pytesseract' not in self.results['engines_used']:
                self.results['engines_used'].append('pytesseract')
        
        # Engine 2: PyMuPDF word/block extraction
        if self._is_engine_enabled('pymupdf'):
            logger.info(f"[Layer1] Running PyMuPDF on page {page_num}")
            page_result['pymupdf_result'] = self._run_pymupdf(pdf_path, page_num)
            if 'pymupdf' not in self.results['engines_used']:
                self.results['engines_used'].append('pymupdf')
        
        # Engine 3: pdfplumber table extraction
        if self._is_engine_enabled('pdfplumber') and file_type in ['equipment_list', 'line_list', 'pms', 'legend_sheet']:
            logger.info(f"[Layer1] Running pdfplumber on page {page_num}")
            page_result['pdfplumber_result'] = self._run_pdfplumber(pdf_path, page_num)
            if 'pdfplumber' not in self.results['engines_used']:
                self.results['engines_used'].append('pdfplumber')
        
        # Engine 4: Yellow-region OCR (for marked-up P&IDs)
        if self.config.get('yellow_region_ocr', {}).get('enabled', False) and file_type == 'pid_drawing':
            logger.info(f"[Layer1] Running yellow-region detection on page {page_num}")
            page_result['yellow_region_result'] = self._detect_yellow_regions(page_image, page_num)
        
        # Merge results from all engines
        page_result['merged_items'] = self._merge_engine_results(page_result)
        page_result['confidence_score'] = self._calculate_confidence(page_result)
        
        page_end = time.time()
        page_result['processing_time'] = round(page_end - page_start, 2)
        
        return page_result
    
    def _pdf_page_to_image(self, pdf_path: str, page_num: int) -> Image.Image:
        """Convert PDF page to PIL Image for OCR."""
        pdf_doc = fitz.open(pdf_path)
        page = pdf_doc.load_page(page_num - 1)  # 0-indexed
        
        # Get DPI from config
        dpi = self.profile.get('ocr_settings', {}).get('dpi', 150)
        zoom = dpi / 72  # 72 is default PDF DPI
        mat = fitz.Matrix(zoom, zoom)
        
        pix = page.get_pixmap(matrix=mat)
        img_data = pix.tobytes("png")
        img = Image.open(io.BytesIO(img_data))
        
        pdf_doc.close()
        return img
    
    def _run_tesseract(self, image: Image.Image, page_num: int) -> Dict:
        """
        Run Tesseract OCR with multiple PSM modes.
        
        Returns:
            {
                'text': str,
                'confidence': float,
                'words': [{text, bbox, conf}],
                'psm_mode_used': int,
            }
        """
        if not TESSERACT_AVAILABLE:
            return {'error': 'Tesseract not available'}
        
        # Pre-process image
        processed_image = self._preprocess_image(image)
        
        # Try multiple PSM modes
        tesseract_config = next((e for e in self.config['engines'] if e['name'] == 'pytesseract'), {})
        psm_modes = tesseract_config.get('psm_modes', [11, 6, 3])
        
        best_result = None
        best_confidence = 0
        
        for psm_mode in psm_modes:
            try:
                # Run OCR with current PSM mode
                custom_config = f'--psm {psm_mode} --oem 3'
                
                # Get detailed word-level data
                data = pytesseract.image_to_data(
                    processed_image,
                    output_type=Output.DICT,
                    config=custom_config
                )
                
                # Calculate average confidence
                confidences = [float(c) for c in data['conf'] if c != '-1']
                avg_conf = sum(confidences) / len(confidences) if confidences else 0
                
                # Extract text
                text = pytesseract.image_to_string(processed_image, config=custom_config)
                
                # Build word list with bounding boxes
                words = []
                for i in range(len(data['text'])):
                    if data['text'][i].strip():
                        words.append({
                            'text': data['text'][i],
                            'bbox': [data['left'][i], data['top'][i], data['width'][i], data['height'][i]],
                            'confidence': float(data['conf'][i]) if data['conf'][i] != '-1' else 0,
                        })
                
                result = {
                    'text': text,
                    'confidence': avg_conf,
                    'words': words,
                    'psm_mode_used': psm_mode,
                }
                
                # Keep best result
                if avg_conf > best_confidence:
                    best_confidence = avg_conf
                    best_result = result
                
                logger.debug(f"[Tesseract] PSM {psm_mode}: {avg_conf:.1f}% confidence, {len(words)} words")
            
            except Exception as e:
                logger.error(f"[Tesseract] PSM {psm_mode} failed: {str(e)}")
                continue
        
        if best_result is None:
            return {'error': 'All PSM modes failed', 'text': '', 'confidence': 0, 'words': []}
        
        # Extract patterns (equipment tags, line numbers, etc.)
        best_result['extracted_patterns'] = self._extract_patterns(best_result['text'])
        
        return best_result
    
    def _run_pymupdf(self, pdf_path: str, page_num: int) -> Dict:
        """
        Run PyMuPDF word and block extraction.
        
        Returns:
            {
                'text': str,
                'words': [{text, bbox}],
                'blocks': [{text, bbox}],
            }
        """
        try:
            pdf_doc = fitz.open(pdf_path)
            page = pdf_doc.load_page(page_num - 1)
            
            # Extract text
            text = page.get_text()
            
            # Extract words with coordinates
            words = []
            word_list = page.get_text("words")  # Returns list of (x0, y0, x1, y1, "word", block_no, line_no, word_no)
            for w in word_list:
                words.append({
                    'text': w[4],
                    'bbox': [w[0], w[1], w[2] - w[0], w[3] - w[1]],  # [x, y, width, height]
                })
            
            # Extract text blocks
            blocks = []
            block_dict = page.get_text("dict")
            for block in block_dict.get("blocks", []):
                if block.get("type") == 0:  # Text block
                    bbox = block.get("bbox", [0, 0, 0, 0])
                    block_text = ""
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            block_text += span.get("text", "") + " "
                    
                    blocks.append({
                        'text': block_text.strip(),
                        'bbox': [bbox[0], bbox[1], bbox[2] - bbox[0], bbox[3] - bbox[1]],
                    })
            
            pdf_doc.close()
            
            result = {
                'text': text,
                'words': words,
                'blocks': blocks,
                'extracted_patterns': self._extract_patterns(text),
            }
            
            logger.debug(f"[PyMuPDF] Extracted {len(words)} words, {len(blocks)} blocks")
            return result
        
        except Exception as e:
            logger.error(f"[PyMuPDF] Extraction failed: {str(e)}")
            return {'error': str(e), 'text': '', 'words': [], 'blocks': []}
    
    def _run_pdfplumber(self, pdf_path: str, page_num: int) -> Dict:
        """
        Run pdfplumber table extraction.
        
        Returns:
            {
                'tables': [[row1], [row2], ...],
                'text': str,
            }
        """
        try:
            with pdfplumber.open(pdf_path) as pdf:
                page = pdf.pages[page_num - 1]
                
                # Extract tables
                tables = page.extract_tables()
                
                # Extract text
                text = page.extract_text() or ""
                
                result = {
                    'tables': tables if tables else [],
                    'text': text,
                    'table_count': len(tables) if tables else 0,
                }
                
                logger.debug(f"[pdfplumber] Extracted {result['table_count']} tables")
                return result
        
        except Exception as e:
            logger.error(f"[pdfplumber] Extraction failed: {str(e)}")
            return {'error': str(e), 'tables': [], 'text': ''}
    
    def _detect_yellow_regions(self, image: Image.Image, page_num: int) -> Dict:
        """
        Detect yellow-highlighted regions and extract text from them.
        
        Returns:
            {
                'regions': [{bbox, text, confidence}],
                'region_count': int,
            }
        """
        # TODO: Implement yellow region detection using HSV color filtering
        # This is a placeholder - full implementation would use cv2 for color detection
        return {
            'regions': [],
            'region_count': 0,
            'note': 'Yellow region detection not yet implemented'
        }
    
    def _preprocess_image(self, image: Image.Image) -> Image.Image:
        """Apply preprocessing to improve OCR quality."""
        tesseract_config = next((e for e in self.config['engines'] if e['name'] == 'pytesseract'), {})
        preprocessing = tesseract_config.get('preprocessing', {})
        
        # Convert to grayscale
        if preprocessing.get('grayscale', True):
            image = ImageOps.grayscale(image)
        
        # Auto-contrast
        if preprocessing.get('auto_contrast', True):
            image = ImageOps.autocontrast(image)
        
        # Enhance sharpness
        if preprocessing.get('sharpen', False):
            enhancer = ImageEnhance.Sharpness(image)
            image = enhancer.enhance(2.0)
        
        return image
    
    def _extract_patterns(self, text: str) -> Dict:
        """
        Extract equipment tags, line numbers, instrument tags using regex.
        
        Returns:
            {
                'equipment_tags': [str],
                'line_numbers': [str],
                'instrument_tags': [str],
            }
        """
        regex_patterns = self.profile.get('regex_patterns', {})
        
        equipment_tags = []
        line_numbers = []
        instrument_tags = []
        
        # Equipment tag pattern: P-101, V-205A, etc.
        if 'equipment_tag' in regex_patterns:
            pattern = regex_patterns['equipment_tag']
            equipment_tags = re.findall(pattern, text)
        
        # Line number pattern: 2"-CS-1001-A1, etc.
        if 'line_number' in regex_patterns:
            pattern = regex_patterns['line_number']
            line_numbers = re.findall(pattern, text)
        
        # Instrument tag pattern: FIC-101, PT-205, etc.
        if 'instrument_tag' in regex_patterns:
            pattern = regex_patterns['instrument_tag']
            instrument_tags = re.findall(pattern, text)
        
        return {
            'equipment_tags': list(set(equipment_tags)),  # Remove duplicates
            'line_numbers': list(set(line_numbers)),
            'instrument_tags': list(set(instrument_tags)),
        }
    
    def _merge_engine_results(self, page_result: Dict) -> Dict:
        """
        Merge results from all engines into unified structure.
        
        Returns:
            {
                'equipment_tags': [{tag, source, bbox, confidence}],
                'line_numbers': [...],
                'instrument_tags': [...],
                'text_all': str,
                'tables': [...],
            }
        """
        merged = {
            'equipment_tags': [],
            'line_numbers': [],
            'instrument_tags': [],
            'text_all': '',
            'tables': [],
        }
        
        # Merge Tesseract results
        if 'tesseract_result' in page_result and 'extracted_patterns' in page_result['tesseract_result']:
            patterns = page_result['tesseract_result']['extracted_patterns']
            for tag in patterns.get('equipment_tags', []):
                merged['equipment_tags'].append({
                    'tag': tag,
                    'source': 'tesseract',
                    'confidence': page_result['tesseract_result'].get('confidence', 0),
                })
            for line in patterns.get('line_numbers', []):
                merged['line_numbers'].append({
                    'line_number': line,
                    'source': 'tesseract',
                })
            for inst in patterns.get('instrument_tags', []):
                merged['instrument_tags'].append({
                    'tag': inst,
                    'source': 'tesseract',
                })
            merged['text_all'] += page_result['tesseract_result'].get('text', '') + '\n'
        
        # Merge PyMuPDF results
        if 'pymupdf_result' in page_result and 'extracted_patterns' in page_result['pymupdf_result']:
            patterns = page_result['pymupdf_result']['extracted_patterns']
            for tag in patterns.get('equipment_tags', []):
                if not any(t['tag'] == tag for t in merged['equipment_tags']):
                    merged['equipment_tags'].append({
                        'tag': tag,
                        'source': 'pymupdf',
                        'confidence': 100,  # PyMuPDF extracts from text layer = high confidence
                    })
            merged['text_all'] += page_result['pymupdf_result'].get('text', '') + '\n'
        
        # Merge pdfplumber tables
        if 'pdfplumber_result' in page_result:
            merged['tables'] = page_result['pdfplumber_result'].get('tables', [])
        
        return merged
    
    def _calculate_confidence(self, page_result: Dict) -> float:
        """Calculate overall confidence score for the page."""
        confidences = []
        
        if 'tesseract_result' in page_result and 'confidence' in page_result['tesseract_result']:
            confidences.append(page_result['tesseract_result']['confidence'])
        
        # PyMuPDF text layer extraction is 100% confident if successful
        if 'pymupdf_result' in page_result and len(page_result['pymupdf_result'].get('words', [])) > 0:
            confidences.append(100.0)
        
        return sum(confidences) / len(confidences) if confidences else 0.0
    
    def _aggregate_page_results(self) -> Dict:
        """Aggregate data from all pages."""
        aggregated = {
            'equipment_tags': [],
            'line_numbers': [],
            'instrument_tags': [],
            'all_text': '',
            'all_tables': [],
        }
        
        for page_result in self.results['per_page_results']:
            merged = page_result.get('merged_items', {})
            
            # Aggregate equipment tags
            for tag_obj in merged.get('equipment_tags', []):
                if not any(t['tag'] == tag_obj['tag'] for t in aggregated['equipment_tags']):
                    tag_obj['page'] = page_result['page_num']
                    aggregated['equipment_tags'].append(tag_obj)
            
            # Aggregate line numbers
            for line_obj in merged.get('line_numbers', []):
                if not any(l['line_number'] == line_obj['line_number'] for l in aggregated['line_numbers']):
                    line_obj['page'] = page_result['page_num']
                    aggregated['line_numbers'].append(line_obj)
            
            # Aggregate instrument tags
            for inst_obj in merged.get('instrument_tags', []):
                if not any(i['tag'] == inst_obj['tag'] for i in aggregated['instrument_tags']):
                    inst_obj['page'] = page_result['page_num']
                    aggregated['instrument_tags'].append(inst_obj)
            
            # Aggregate text
            aggregated['all_text'] += merged.get('text_all', '')
            
            # Aggregate tables
            for table in merged.get('tables', []):
                aggregated['all_tables'].append({
                    'page': page_result['page_num'],
                    'data': table,
                })
        
        return aggregated
    
    def _compute_file_hash(self, file_path: str) -> str:
        """Compute SHA-256 hash of file."""
        import hashlib
        sha256 = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256.update(chunk)
        return sha256.hexdigest()
    
    def _is_engine_enabled(self, engine_name: str) -> bool:
        """Check if an engine is enabled in config."""
        for engine in self.config.get('engines', []):
            if engine['name'] == engine_name:
                return engine.get('enabled', False)
        return False
