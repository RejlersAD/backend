"""
PDF Extraction Service for Finance Module
Integrated with Django and RAD AI
"""
import PyPDF2
import re
from typing import Dict, Optional
from datetime import datetime
from django.core.files.uploadedfile import UploadedFile
import os
import logging

logger = logging.getLogger(__name__)


class PDFExtractor:
    """Extract text and data from PDF invoices"""
    
    def __init__(self):
        """Initialize PDF extractor"""
        pass
    
    def extract_text_from_pdf(self, pdf_path: str) -> str:
        """Extract text from PDF using PyPDF2"""
        try:
            with open(pdf_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                text = ""
                for page in pdf_reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
                return text.strip()
        except Exception as e:
            logger.error(f"PyPDF2 extraction failed: {e}")
            return ""
    
    def extract_text_with_ocr(self, pdf_path: str) -> str:
        """
        Extract text from PDF using OCR (for scanned documents)
        Requires pytesseract and pdf2image (optional feature)
        """
        try:
            from pdf2image import convert_from_path
            import pytesseract
            
            images = convert_from_path(pdf_path)
            text = ""
            
            for image in images:
                page_text = pytesseract.image_to_string(image)
                text += page_text + "\n"
            
            return text.strip()
        except ImportError:
            logger.warning("OCR libraries not installed (pytesseract, pdf2image)")
            return ""
        except Exception as e:
            logger.error(f"OCR extraction failed: {e}")
            return ""
    
    def extract_text(self, pdf_path: str, use_ocr: bool = True) -> str:
        """Extract text from PDF, ALWAYS try OCR first for better accuracy"""
        # Try OCR first for maximum accuracy (handles both scanned and digital PDFs)
        logger.info("🔍 Attempting OCR extraction for best text quality...")
        text = self.extract_text_with_ocr(pdf_path)
        
        # If OCR fails or yields little text, fallback to PyPDF2
        if not text or len(text) < 50:
            logger.info("OCR yielded insufficient text, falling back to PyPDF2...")
            text = self.extract_text_from_pdf(pdf_path)
        else:
            logger.info(f"✅ OCR extraction successful: {len(text)} characters extracted")
        
        return text
    
    def normalize_amount(self, amount_str: str) -> float:
        """
        Normalize amount string to float
        Handles various formats: AED 1,234.56, $1,234.56, 1234.56, €1.234,56
        """
        if not amount_str:
            return 0.0
        
        cleaned = re.sub(r'[^\d,.\-]', '', str(amount_str).strip())
        
        # Handle European vs US format
        if ',' in cleaned and '.' in cleaned:
            comma_pos = cleaned.rindex(',')
            period_pos = cleaned.rindex('.')
            
            if comma_pos > period_pos:
                # European format: 1.234,56
                cleaned = cleaned.replace('.', '').replace(',', '.')
            else:
                # US format: 1,234.56
                cleaned = cleaned.replace(',', '')
        elif ',' in cleaned:
            parts = cleaned.split(',')
            if len(parts[-1]) == 2 and len(parts) == 2:
                # Likely decimal: 1234,56
                cleaned = cleaned.replace(',', '.')
            else:
                # Thousands separator
                cleaned = cleaned.replace(',', '')
        
        try:
            return float(cleaned)
        except ValueError:
            return 0.0
    
    def extract_invoice_data(self, text: str) -> Dict:
        """Extract structured invoice data from text"""
        data = {
            'vendor_name': None,
            'invoice_number': None,
            'invoice_date': None,
            'amount': None,
            'tax_amount': None,
            'total_amount': None,
            'currency': 'AED'
        }
        
        # Extract vendor name with smart pattern matching
        vendor_patterns = [
            r'(?:From|Vendor|Company|Supplier)\s*:?\s*([A-Za-z][\w\s&,.-]+(?:Inc\.?|LLC|Ltd\.?|Corp\.?|Corporation|Co\.?|Company|Solutions|Services|Group|Enterprises))',
        ]
        
        skip_keywords = ['invoice', 'bill to', 'ship to', 'phone', 'email', 'fax', 'tax id', 'vat', 'address', 'street', 'city', 'state', 'zip']
        
        for pattern in vendor_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                vendor = match.group(1).strip()
                data['vendor_name'] = vendor
                break
        
        # Fallback: extract from first few lines if pattern matching fails
        if not data['vendor_name']:
            lines = text.split('\n')[:15]
            for line in lines:
                line = line.strip()
                # Skip lines with unwanted keywords
                if any(keyword in line.lower() for keyword in skip_keywords):
                    continue
                # Look for lines with company indicators
                if re.search(r'\b(?:Inc\.?|LLC|Ltd\.?|Corp\.?|Corporation|Co\.?|Company|Solutions|Services|Group|Enterprises)\b', line, re.IGNORECASE):
                    # Clean up the line
                    cleaned = re.sub(r'^[^A-Za-z]+', '', line)  # Remove leading non-letters
                    cleaned = re.split(r'\d{3,}', cleaned)[0]  # Stop at phone/address numbers
                    cleaned = cleaned.strip()
                    if len(cleaned) > 5 and len(cleaned) < 100:
                        data['vendor_name'] = cleaned
                        break
        
        # Extract invoice number
        invoice_patterns = [
            r'Invoice\s*#?\s*:?\s*([A-Z0-9\-]+)',
            r'Invoice\s*Number\s*:?\s*([A-Z0-9\-]+)',
            r'INV\s*#?\s*:?\s*([A-Z0-9\-]+)',
            r'INV-(\d{4}-\d{2}-\d{4,6})',  # Format like INV-2024-12-0847
        ]
        for pattern in invoice_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                data['invoice_number'] = match.group(1)
                break
        
        # Generate unique invoice number if not found
        if not data['invoice_number']:
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            data['invoice_number'] = f'INV-{timestamp}'
        
        # Extract dates
        date_patterns = [
            r'(?:Invoice\s*)?Date\s*:?\s*(\w+\s+\d{1,2},?\s+\d{4})',  # December 15, 2024
            r'(?:Invoice\s*)?Date\s*:?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})',  # 12/15/2024
            r'(?:Invoice\s*)?Date\s*:?\s*(\d{4}-\d{2}-\d{2})',  # 2024-12-15
        ]
        for pattern in date_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                data['invoice_date'] = match.group(1)
                break
        
        # Extract amounts
        amount_patterns = [
            r'TOTAL\s+AMOUNT\s+DUE\s*:?\s*[\$€£¥₹AED]?\s*([\d,]+\.?\d*)',
            r'Total\s+Amount\s*:?\s*[\$€£¥₹AED]?\s*([\d,]+\.?\d*)',
            r'TOTAL\s*:?\s*[\$€£¥₹AED]?\s*([\d,]+\.?\d*)',
            r'Grand\s+Total\s*:?\s*[\$€£¥₹AED]?\s*([\d,]+\.?\d*)',
            r'Invoice\s+Total\s*:?\s*[\$€£¥₹AED]?\s*([\d,]+\.?\d*)',
            r'Amount\s+Due\s*:?\s*[\$€£¥₹AED]?\s*([\d,]+\.?\d*)',
        ]
        
        for pattern in amount_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                amount_str = match.group(1)
                data['total_amount'] = self.normalize_amount(amount_str)
                break
        
        return data
    
    
    def process_invoice(self, file_path: str) -> Optional[Dict]:
        '''
        Complete invoice processing pipeline
        Returns extracted data dictionary or None if failed
        '''
        try:
            # Extract text
            text = self.extract_text(file_path)

            if not text or len(text) < 50:
                logger.error(f'Insufficient text extracted from {file_path}')
                return None

            # Extract structured data
            invoice_data = self.extract_invoice_data(text)
            invoice_data['extracted_text'] = text

            return invoice_data

        except Exception as e:
            logger.error(f'Invoice processing failed: {e}', exc_info=True)
            return None
