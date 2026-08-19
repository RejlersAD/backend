"""
OCR Parser - Text Extraction from P&ID Images
NO LLM - Pure OCR extraction
"""
import re
import fitz  # PyMuPDF
from typing import List, Dict, Set, Any
from PIL import Image
import io


class OCRParser:
    """Pure OCR text extraction without AI interpretation"""
    
    def __init__(self, pdf_dpi: int = 300):
        self.pdf_dpi = pdf_dpi
        self.extracted_text = ""
        self.text_blocks = []
        
    def extract_text_from_pdf(self, pdf_file) -> Dict[str, Any]:
        """
        Extract raw text from PDF using PyMuPDF OCR
        
        Returns:
            {
                'raw_text': str,
                'text_blocks': List[str],
                'page_count': int
            }
        """
        try:
            # Handle Django FieldFile or file path
            if hasattr(pdf_file, 'read'):
                pdf_bytes = pdf_file.read()
                if hasattr(pdf_file, 'seek'):
                    pdf_file.seek(0)
            else:
                with open(pdf_file, 'rb') as f:
                    pdf_bytes = f.read()
            
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            all_text = []
            text_blocks = []
            
            for page_num in range(len(doc)):
                page = doc[page_num]
                
                # Extract text blocks with position info
                blocks = page.get_text("blocks")
                for block in blocks:
                    if block[6] == 0:  # Text block (not image)
                        text = block[4].strip()
                        if text:
                            text_blocks.append(text)
                            all_text.append(text)
                
                # Also get full page text
                page_text = page.get_text()
                if page_text:
                    all_text.append(page_text)
            
            doc.close()
            
            self.extracted_text = '\n'.join(all_text)
            self.text_blocks = text_blocks
            
            return {
                'raw_text': self.extracted_text,
                'text_blocks': text_blocks,
                'page_count': len(doc)
            }
            
        except Exception as e:
            print(f"[ERROR] OCR extraction failed: {e}")
            return {
                'raw_text': '',
                'text_blocks': [],
                'page_count': 0
            }
    
    def get_text_tokens(self) -> List[str]:
        """Split extracted text into tokens for classification"""
        if not self.extracted_text:
            return []
        
        # Split by whitespace and newlines
        tokens = self.extracted_text.split()
        
        # Clean tokens
        cleaned = []
        for token in tokens:
            token = token.strip()
            if len(token) > 1:  # Skip single characters
                cleaned.append(token)
        
        return cleaned
