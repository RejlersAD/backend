"""
P&ID Element Classifier - Pre-LLM Deterministic Parsing
This module prevents misclassification of line numbers as equipment tags
and provides strict pattern matching BEFORE sending data to LLM.

CRITICAL: This runs BEFORE AI analysis to prevent hallucinations and context mixing.
"""
import re
from typing import Dict, List, Set, Tuple, Optional
from dataclasses import dataclass
from enum import Enum


class ElementType(Enum):
    """Strict element classification types"""
    LINE_NUMBER = "line_number"
    EQUIPMENT_TAG = "equipment_tag"
    INSTRUMENT_TAG = "instrument_tag"
    VALVE = "valve"
    NOTE = "note"
    SPEC_BREAK = "spec_break"
    ARROW_CONNECTOR = "arrow"
    DRAWING_NUMBER = "drawing_number"
    PID_CONNECTOR = "pid_connector"
    DELETED_NOTE = "deleted_note"  # Must be ignored
    TITLE_BLOCK_TEXT = "title_block_text"  # Reference, not equipment
    UNKNOWN = "unknown"


@dataclass
class ClassifiedElement:
    """Container for classified element with confidence"""
    raw_text: str
    element_type: ElementType
    confidence: str  # "high", "medium", "low"
    reason: str
    visual_confirmed: bool = False
    context_snippet: str = ""


class StrictElementClassifier:
    """
    Deterministic classifier that prevents common misclassifications
    
    Key Rules:
    1. Anything starting with pipe size (e.g., 2"-) is ALWAYS a line number
    2. D-6155 inside "2"-D-6155-033842" is NOT equipment
    3. DELETED notes must be completely ignored
    4. Drawing numbers (16.01.08.1678) are NOT line numbers
    5. Arrows and connectors are NOT pipelines
    """
    
    # ══════════════════════════════════════════════════════════════
    # STRICT PATTERN DEFINITIONS (DETERMINISTIC)
    # ══════════════════════════════════════════════════════════════
    
    # LINE NUMBER: Must start with digit + quote + dash
    # Examples: 2"-D-6155-033842-X-N, 4"-HC-1001-CS150
    LINE_NUMBER_PATTERN = re.compile(
        r'^(\d+(?:\.\d+)?)["\']?\s*[-–—]\s*([A-Z]{1,4})\s*[-–—]\s*(\d{3,})\s*(?:[-–—](.+))?$',
        re.IGNORECASE
    )
    
    # ADNOC LINE NUMBER (Abu Dhabi Oil Co. Ltd): SIZE"-FLUID-PIPINGCLASS-SEQUENCE
    # Examples: 6"-CD-AC3N-8256, 8"-HO-BD2A-1023, 10"-AG-XY1Z-9999
    # Pattern: number + " + dash + 2-3 letters + dash + alphanumeric + dash + 4 digits
    ADNOC_LINE_NUMBER_PATTERN = re.compile(
        r'^(\d{1,2})"\s*[-–—]\s*([A-Z]{2,3})\s*[-–—]\s*([A-Z0-9]{2,6})\s*[-–—]\s*(\d{4})$',
        re.IGNORECASE
    )
    
    # EQUIPMENT TAG: Letter-Number (NOT starting with size)
    # Examples: P-3610, V-201, E-301, D-255 (but NOT D-6155 from line number)
    # Rule: D-XXXX where XXXX >= 1000 is a DRAIN LINE, not equipment
    EQUIPMENT_PATTERN = re.compile(
        r'^([A-Z]{1,2})[-_](\d{2,4})([A-Z]?)$',
        re.IGNORECASE
    )
    
    # INSTRUMENT TAG: ISA-5.1 format with function code
    # Examples: FT-101, PIC-3601, 13-FE-4580, TI-201A
    INSTRUMENT_PATTERN = re.compile(
        r'^(?:\d{1,2}[-])?([A-Z]{2,3}(?:IC|IT|IND|SH|SL|AL|AH|V|C|I|T|E|Y|S|G|M)?[-_]?\d{3,5}[A-Z]?)$',
        re.IGNORECASE
    )
    
    # DRAWING NUMBER: Dot-separated (NEVER dash-separated)
    # Examples: 16.01.08.1678, 14.01.08.1603
    DRAWING_NUMBER_PATTERN = re.compile(
        r'^\d{1,4}\.\d{1,4}\.\d{1,4}\.\d{2,6}$'
    )
    
    # P&ID CONNECTOR: NN-PP-NNN-NNNNN format
    # Examples: 13-PP-152-45060
    PID_CONNECTOR_PATTERN = re.compile(
        r'^\d+[-]PP[-]\d+[-]\d+$',
        re.IGNORECASE
    )
    
    # DELETED NOTE: Contains "DELETED" keyword
    # Examples: "NOTE 5 DELETED", "HOLD 3 - DELETED"
    DELETED_NOTE_PATTERN = re.compile(
        r'\b(NOTE|HOLD)\s*\d+.*?DELETED\b',
        re.IGNORECASE
    )
    
    # NOTE/HOLD REFERENCE
    NOTE_PATTERN = re.compile(
        r'\b((?:NOTE|HOLD|REF)\s*\d+)\b',
        re.IGNORECASE
    )
    
    def __init__(self):
        """Initialize classifier with strict rules"""
        pass
    
    def classify_element(self, text: str, context: str = "") -> ClassifiedElement:
        """
        Classify a single text element using strict deterministic rules
        
        Args:
            text: The text to classify
            context: Surrounding text (50-100 chars) for context analysis
            
        Returns:
            ClassifiedElement with type, confidence, and reasoning
        """
        text = text.strip()
        if not text:
            return ClassifiedElement(
                raw_text=text,
                element_type=ElementType.UNKNOWN,
                confidence="low",
                reason="Empty text"
            )
        
        # ═══════════════════════════════════════════════════════════
        # RULE 1: DRAWING NUMBERS (highest priority - prevent confusion)
        # ═══════════════════════════════════════════════════════════
        if self.DRAWING_NUMBER_PATTERN.match(text):
            return ClassifiedElement(
                raw_text=text,
                element_type=ElementType.DRAWING_NUMBER,
                confidence="high",
                reason="Dot-separated format indicates drawing number",
                context_snippet=context
            )
        
        # ═══════════════════════════════════════════════════════════
        # RULE 2: DELETED NOTES (must be completely ignored)
        # ═══════════════════════════════════════════════════════════
        if self.DELETED_NOTE_PATTERN.search(text) or 'DELETED' in text.upper():
            return ClassifiedElement(
                raw_text=text,
                element_type=ElementType.DELETED_NOTE,
                confidence="high",
                reason="Contains DELETED keyword - must be ignored",
                context_snippet=context
            )
        
        # ═══════════════════════════════════════════════════════════
        # RULE 3: P&ID CONNECTORS (sheet reference, not equipment)
        # ═══════════════════════════════════════════════════════════
        if self.PID_CONNECTOR_PATTERN.match(text):
            return ClassifiedElement(
                raw_text=text,
                element_type=ElementType.PID_CONNECTOR,
                confidence="high",
                reason="NN-PP-NNN pattern is P&ID sheet connector",
                context_snippet=context
            )
        
        # ═══════════════════════════════════════════════════════════
        # RULE 4: LINE NUMBERS (CRITICAL - check BEFORE equipment)
        # If it starts with digit + quote + dash → ALWAYS line number
        # ═══════════════════════════════════════════════════════════
        line_match = self.LINE_NUMBER_PATTERN.match(text)
        if line_match:
            size, fluid_code, sequence, rest = line_match.groups()
            return ClassifiedElement(
                raw_text=text,
                element_type=ElementType.LINE_NUMBER,
                confidence="high",
                reason=f'Starts with pipe size {size}" - NEVER equipment',
                context_snippet=context
            )
        
        # ═══════════════════════════════════════════════════════════
        # RULE 4b: ADNOC LINE NUMBERS (Abu Dhabi Oil Co. Ltd format)
        # Pattern: SIZE"-FLUID-PIPINGCLASS-SEQUENCE (e.g., 6"-CD-AC3N-8256)
        # ═══════════════════════════════════════════════════════════
        adnoc_match = self.ADNOC_LINE_NUMBER_PATTERN.match(text)
        if adnoc_match:
            size, fluid_code, piping_class, sequence = adnoc_match.groups()
            return ClassifiedElement(
                raw_text=text,
                element_type=ElementType.LINE_NUMBER,
                confidence="high",
                reason=f'ADNOC format: {size}"-{fluid_code}-{piping_class}-{sequence}',
                context_snippet=context
            )
        
        # Check if text contains embedded line number pattern
        # Example: "2"-D-6155-033842" contains D-6155, but it's NOT equipment
        if re.search(r'\d+["\']?\s*[-–—]', text):
            return ClassifiedElement(
                raw_text=text,
                element_type=ElementType.LINE_NUMBER,
                confidence="high",
                reason="Contains pipe size prefix - part of line number",
                context_snippet=context
            )
        
        # ═══════════════════════════════════════════════════════════
        # RULE 5: INSTRUMENT TAGS (check before equipment)
        # ═══════════════════════════════════════════════════════════
        inst_match = self.INSTRUMENT_PATTERN.match(text)
        if inst_match:
            return ClassifiedElement(
                raw_text=text,
                element_type=ElementType.INSTRUMENT_TAG,
                confidence="high",
                reason=f"Matches ISA-5.1 instrument pattern",
                context_snippet=context
            )
        
        # ═══════════════════════════════════════════════════════════
        # RULE 6: EQUIPMENT TAGS (with D-XXXX drain line exclusion)
        # ═══════════════════════════════════════════════════════════
        eq_match = self.EQUIPMENT_PATTERN.match(text)
        if eq_match:
            prefix, number, suffix = eq_match.groups()
            number_int = int(number)
            
            # Special handling for D-XXXX patterns
            if prefix.upper() == 'D':
                if number_int < 200:
                    return ClassifiedElement(
                        raw_text=text,
                        element_type=ElementType.PID_CONNECTOR,
                        confidence="high",
                        reason=f"D-{number_int} < 200: P&ID sheet reference",
                        context_snippet=context
                    )
                elif number_int >= 1000:
                    return ClassifiedElement(
                        raw_text=text,
                        element_type=ElementType.LINE_NUMBER,
                        confidence="high",
                        reason=f"D-{number_int} >= 1000: Drain service line",
                        context_snippet=context
                    )
            
            # Valid equipment tag
            return ClassifiedElement(
                raw_text=text,
                element_type=ElementType.EQUIPMENT_TAG,
                confidence="high",
                reason=f"Equipment pattern: {prefix}-{number}",
                context_snippet=context
            )
        
        # ═══════════════════════════════════════════════════════════
        # RULE 7: NOTE/HOLD REFERENCES
        # ═══════════════════════════════════════════════════════════
        note_match = self.NOTE_PATTERN.search(text)
        if note_match:
            return ClassifiedElement(
                raw_text=text,
                element_type=ElementType.NOTE,
                confidence="high",
                reason="Matches NOTE/HOLD pattern",
                context_snippet=context
            )
        
        # ═══════════════════════════════════════════════════════════
        # DEFAULT: UNKNOWN (low confidence)
        # ═══════════════════════════════════════════════════════════
        return ClassifiedElement(
            raw_text=text,
            element_type=ElementType.UNKNOWN,
            confidence="low",
            reason="Does not match any known pattern",
            context_snippet=context
        )
    
    def classify_batch(
        self, 
        text_elements: List[str], 
        full_text: str = ""
    ) -> Dict[ElementType, List[ClassifiedElement]]:
        """
        Classify a batch of elements and group by type
        
        Args:
            text_elements: List of text strings to classify
            full_text: Complete OCR text for context extraction
            
        Returns:
            Dictionary mapping ElementType to list of classified elements
        """
        results: Dict[ElementType, List[ClassifiedElement]] = {
            elem_type: [] for elem_type in ElementType
        }
        
        for text in text_elements:
            # Extract context (50 chars before/after)
            context = ""
            if full_text:
                pos = full_text.find(text)
                if pos != -1:
                    context = full_text[max(0, pos-50):pos+len(text)+50]
            
            classified = self.classify_element(text, context)
            results[classified.element_type].append(classified)
        
        return results
    
    def extract_line_numbers_only(self, text: str) -> Set[str]:
        """
        Extract ONLY valid line numbers (never misclassify as equipment)
        
        Returns:
            Set of confirmed line numbers
        """
        # Find all potential line number patterns
        potential = re.findall(
            r'\b([\d]+(?:\.\d+)?["\']?\s*[-–—]\s*[A-Z]{1,4}\s*[-–—]\s*\d{3,}(?:\s*[-–—][A-Z\d]+)*)\b',
            text,
            re.IGNORECASE
        )
        
        confirmed_lines = set()
        for candidate in potential:
            classified = self.classify_element(candidate.strip(), text)
            if classified.element_type == ElementType.LINE_NUMBER:
                # Normalize: remove extra spaces, standardize dashes
                normalized = re.sub(r'\s+', '', candidate)
                normalized = normalized.replace('–', '-').replace('—', '-')
                confirmed_lines.add(normalized)
        
        return confirmed_lines
    
    def extract_equipment_tags_only(self, text: str) -> Set[str]:
        """
        Extract ONLY valid equipment tags (never include line number fragments)
        
        Returns:
            Set of confirmed equipment tags
        """
        potential = re.findall(
            r'\b([A-Z]{1,2}[-_]\d{2,4}(?:[-_][A-Z\d]{1,2})?)\b',
            text,
            re.IGNORECASE
        )
        
        confirmed_equipment = set()
        for candidate in potential:
            classified = self.classify_element(candidate.upper(), text)
            if classified.element_type == ElementType.EQUIPMENT_TAG:
                confirmed_equipment.add(classified.raw_text.upper())
        
        return confirmed_equipment
    
    def filter_deleted_notes(self, notes_list: List[str]) -> List[str]:
        """
        Remove any notes marked as DELETED
        
        Args:
            notes_list: List of note references (e.g., ["NOTE 1", "NOTE 5 DELETED"])
            
        Returns:
            Filtered list with DELETED notes removed
        """
        active_notes = []
        for note in notes_list:
            classified = self.classify_element(note)
            if classified.element_type != ElementType.DELETED_NOTE:
                active_notes.append(note)
        
        return active_notes
    
    def is_title_block_reference(self, text: str, y_position: float = None) -> bool:
        """
        Detect if text is from title block or drawing references
        (should not be treated as equipment)
        
        Args:
            text: Text to check
            y_position: Vertical position on page (0.0-1.0, where 1.0 is bottom)
            
        Returns:
            True if likely from title block/references
        """
        # Title blocks usually at bottom of drawing (y > 0.85)
        if y_position and y_position > 0.85:
            return True
        
        # Common title block keywords
        title_keywords = [
            'DRAWING', 'DWG', 'REV', 'REVISION', 'DATE', 'APPROVED',
            'CHECKED', 'DRAWN', 'SHEET', 'PROJECT', 'CLIENT', 'TITLE',
            'CONTRACTOR', 'SCALE', 'UNIT', 'SIZE'
        ]
        
        text_upper = text.upper()
        if any(keyword in text_upper for keyword in title_keywords):
            return True
        
        return False
    
    def validate_spec_break(
        self, 
        line_number: str, 
        visual_symbols: List[Dict]
    ) -> Tuple[bool, str]:
        """
        Check if spec break symbol exists for a line
        
        Args:
            line_number: Line number to check
            visual_symbols: List of detected symbols with positions
            
        Returns:
            (has_spec_break, reason)
        """
        # Look for spec break symbols near the line
        spec_break_symbols = ['triangle', 'diamond', 'hexagon', 'spec_break']
        
        for symbol in visual_symbols:
            if symbol.get('type', '').lower() in spec_break_symbols:
                # Check proximity to line number
                # (implementation would check coordinate proximity)
                return (True, f"Spec break symbol found near {line_number}")
        
        return (False, "No spec break symbol detected visually")
    
    def validate_reducer(
        self,
        upstream_size: str,
        downstream_size: str,
        visual_symbols: List[Dict]
    ) -> Tuple[bool, str]:
        """
        Check if reducer/expander symbol exists between different sizes
        
        Args:
            upstream_size: Upstream pipe size (e.g., "4")
            downstream_size: Downstream pipe size (e.g., "2")
            visual_symbols: List of detected symbols
            
        Returns:
            (has_reducer, reason)
        """
        # Check for reducer/expander symbols
        reducer_symbols = ['reducer', 'expander', 'concentric_reducer', 'eccentric_reducer']
        
        for symbol in visual_symbols:
            if symbol.get('type', '').lower() in reducer_symbols:
                return (True, f"Reducer found: {upstream_size}\" to {downstream_size}\"")
        
        return (False, "No reducer symbol detected between different sizes")


# ══════════════════════════════════════════════════════════════════════
# GLOBAL INSTANCE (reusable across requests)
# ══════════════════════════════════════════════════════════════════════
_CLASSIFIER_INSTANCE = None


def get_classifier() -> StrictElementClassifier:
    """Get singleton classifier instance"""
    global _CLASSIFIER_INSTANCE
    if _CLASSIFIER_INSTANCE is None:
        _CLASSIFIER_INSTANCE = StrictElementClassifier()
    return _CLASSIFIER_INSTANCE
