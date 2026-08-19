"""
Electrical Checklist Extraction Service
OCR-powered extraction using Tesseract + EasyOCR (FREE)
Optional AI fallback for complex cases (Gemini/GPT-4o)
Signature detection using image processing
"""
import os
import logging
import json
import base64
import io
from typing import Dict, List, Optional, Any
from PIL import Image

from .config import CHECKLIST_TEMPLATE, OCR_CONFIG

logger = logging.getLogger(__name__)


class ChecklistExtractionService:
    """
    Service for extracting data from electrical inspection checklists
    PRIMARY: Tesseract OCR (free, fast)
    SECONDARY: EasyOCR (free, ML-based)
    FALLBACK: AI Vision (optional, paid)
    """
    
    def __init__(self):
        self.stats = {
            "pages_processed": 0,
            "fields_extracted": 0,
            "signatures_found": 0,
            "extraction_method": "pending"
        }
    
    def extract_from_pdf(self, pdf_file, template_id: str = "ups_battery_inspection", 
                         extract_signatures: bool = True) -> Dict[str, Any]:
        """
        Main extraction method
        
        Args:
            pdf_file: Django UploadedFile or file path
            template_id: Checklist template identifier
            extract_signatures: Whether to detect signatures
        
        Returns:
            Dict with extracted data, signatures, and metadata
        """
        logger.info(f"[ChecklistExtraction] Starting extraction for template: {template_id}")
        
        # Convert PDF to images
        pdf_images = self._pdf_to_images(pdf_file)
        if not pdf_images:
            raise ValueError("Failed to convert PDF to images")
        
        logger.info(f"[ChecklistExtraction] Converted {len(pdf_images)} pages to images")
        
        # Placeholder: Extract field data
        extracted_fields = {}
        
        # Placeholder: Extract signatures
        signatures = []
        
        # Calculate placeholder statistics
        result = {
            "template_id": template_id,
            "template_name": CHECKLIST_TEMPLATE["name"],
            "fields_extracted": len(extracted_fields),
            "sections_completed": self._count_completed_sections(extracted_fields),
            "signatures_found": len(signatures),
            "confidence_score": 0,
            "extracted_data": extracted_fields,
            "signatures": signatures,
            "metadata": {
                "pages_processed": len(pdf_images),
                "extraction_method": "ocr",
                "stats": self.stats,
                "note": "Basic extraction - OCR implementation pending"
            }
        }
        
        logger.info(f"[ChecklistExtraction] ✅ Extraction complete: {len(extracted_fields)} fields, {len(signatures)} signatures")
        return result
    
    def _pdf_to_images(self, pdf_file) -> List[Image.Image]:
        """Convert PDF pages to PIL Images"""
        try:
            import fitz  # PyMuPDF
            
            # Read PDF bytes
            if hasattr(pdf_file, 'read'):
                pdf_bytes = pdf_file.read()
                pdf_file.seek(0)  # Reset file pointer
            else:
                with open(pdf_file, 'rb') as f:
                    pdf_bytes = f.read()
            
            # Open PDF with PyMuPDF
            pdf_document = fitz.open(stream=pdf_bytes, filetype="pdf")
            images = []
            
            for page_num in range(len(pdf_document)):
                page = pdf_document[page_num]
                # Render page at 300 DPI for good OCR quality
                pix = page.get_pixmap(matrix=fitz.Matrix(300/72, 300/72))
                img_bytes = pix.tobytes("png")
                img = Image.open(io.BytesIO(img_bytes))
                images.append(img)
                self.stats["pages_processed"] += 1
            
            pdf_document.close()
            return images
            
        except Exception as e:
            logger.error(f"[ChecklistExtraction] PDF to image conversion failed: {e}")
            return []
    
    def _count_completed_sections(self, extracted_fields: Dict) -> int:
        """Count how many template sections have at least one extracted field"""
        if not extracted_fields:
            return 0
        
        sections_with_data = set()
        for field_key in extracted_fields.keys():
            # Find which section this field belongs to
            for section in CHECKLIST_TEMPLATE["sections"]:
                for field in section["fields"]:
                    if field["key"] == field_key:
                        sections_with_data.add(section["id"])
                        break
        
        return len(sections_with_data)
