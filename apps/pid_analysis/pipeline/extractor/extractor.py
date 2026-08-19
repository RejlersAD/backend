"""
PID Extractor - Main extraction orchestrator
Coordinates OCR, classification, and symbol detection
"""
from typing import Dict, Any, Set, List
from .ocr_parser import OCRParser
from .regex_classifier import RegexClassifier
from .symbol_detector import SymbolDetector
import uuid


class PIDExtractor:
    """
    Discriminative extraction layer - source of truth for P&ID analysis
    NO LLM - Pure extraction and classification
    """
    
    def __init__(self, pdf_dpi: int = 300):
        self.ocr_parser = OCRParser(pdf_dpi=pdf_dpi)
        self.regex_classifier = RegexClassifier()
        self.symbol_detector = SymbolDetector()
        
        # Context isolation: Unique ID for this extraction
        self.document_id = str(uuid.uuid4())
        
    def extract(self, pdf_file, drawing_number: str = None) -> Dict[str, Any]:
        """
        Extract structured data from P&ID
        
        This is the SOURCE OF TRUTH - everything downstream uses this data
        
        Returns:
            {
                'document_id': str,  # Unique ID for context isolation
                'drawing_number': str,
                'lines': Set[str],
                'equipment': Set[str],
                'instruments': Set[str],
                'notes': Dict[int, str],  # Active notes only
                'deleted_notes': Set[int],
                'spec_breaks': List[str],
                'valves': List[Dict],
                'reducers': List[Dict],
                'connectors': Set[str],
                'arrows': List[Dict],
                'raw_text': str,
                'extraction_metadata': Dict
            }
        """
        print(f"[EXTRACTOR] Starting extraction - Document ID: {self.document_id}")
        
        # Step 1: OCR text extraction
        ocr_result = self.ocr_parser.extract_text_from_pdf(pdf_file)
        print(f"[EXTRACTOR] OCR extracted {len(ocr_result['text_blocks'])} text blocks")
        
        # Step 2: Tokenize text
        tokens = self.ocr_parser.get_text_tokens()
        print(f"[EXTRACTOR] Tokenized {len(tokens)} tokens")
        
        # Step 3: Classify tokens deterministically
        classified = self.regex_classifier.classify_tokens(tokens)
        print(f"[EXTRACTOR] Classified elements:")
        print(f"  - Line numbers: {len(classified['line_numbers'])}")
        print(f"  - Equipment tags: {len(classified['equipment_tags'])}")
        print(f"  - Instrument tags: {len(classified['instrument_tags'])}")
        print(f"  - Active notes: {len(classified['notes'])}")
        print(f"  - Deleted notes: {len(classified['deleted_notes'])}")
        print(f"  - Spec breaks: {len(classified['spec_breaks'])}")
        
        # Step 4: Detect symbols (CV/heuristic)
        symbols = self.symbol_detector.detect_symbols(pdf_file)
        print(f"[EXTRACTOR] Detected symbols:")
        print(f"  - Reducers: {len(symbols['reducers'])}")
        print(f"  - Valves: {len(symbols['valves'])}")
        print(f"  - Arrows: {len(symbols['arrows'])}")
        
        # Compile final structured data
        extracted_data = {
            'document_id': self.document_id,
            'drawing_number': drawing_number or 'Unknown',
            'lines': classified['line_numbers'],
            'equipment': classified['equipment_tags'],
            'instruments': classified['instrument_tags'],
            'notes': classified['notes'],
            'deleted_notes': classified['deleted_notes'],
            'spec_breaks': classified['spec_breaks'] + symbols['spec_breaks'],
            'valves': symbols['valves'],
            'reducers': symbols['reducers'],
            'connectors': classified['connectors'],
            'arrows': symbols['arrows'],
            'raw_text': ocr_result['raw_text'],
            'extraction_metadata': {
                'page_count': ocr_result['page_count'],
                'token_count': len(tokens),
                'text_block_count': len(ocr_result['text_blocks'])
            }
        }
        
        print(f"[EXTRACTOR] Extraction complete - Document ID: {self.document_id}")
        return extracted_data
