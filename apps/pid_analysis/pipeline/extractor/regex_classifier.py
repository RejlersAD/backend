"""
Regex Classifier - Deterministic Element Classification
NO LLM - Pure pattern matching
"""
import re
from typing import List, Dict, Set, Tuple, Optional, Any
from enum import Enum


class ElementType(Enum):
    """Element types for classification"""
    LINE_NUMBER = "line_number"
    EQUIPMENT_TAG = "equipment_tag"
    INSTRUMENT_TAG = "instrument_tag"
    VALVE = "valve"
    NOTE = "note"
    SPEC_BREAK = "spec_break"
    ARROW_CONNECTOR = "arrow"
    DRAWING_NUMBER = "drawing_number"
    PID_CONNECTOR = "pid_connector"
    DELETED_NOTE = "deleted_note"
    HOLD_NOTE = "hold_note"
    TITLE_BLOCK_TEXT = "title_block"
    UNKNOWN = "unknown"


class RegexClassifier:
    """
    Deterministic classifier using regex patterns
    
    CRITICAL RULES:
    1. Line numbers ALWAYS start with pipe size (e.g., 2"-D-6155-...)
    2. Equipment inside line numbers (D-6155) is NOT separate equipment
    3. DELETED notes are IGNORED completely
    4. Drawing numbers (16.01.08.1678) are NOT line numbers
    """
    
    # ═══════════════════════════════════════════════════════════════
    # PATTERN DEFINITIONS
    # ═══════════════════════════════════════════════════════════════
    
    # LINE NUMBER: Must start with pipe size + dash
    # Examples: 2"-D-6155-033842-X-N, 4"-HC-1001-CS150-X-N
    LINE_NUMBER_PATTERN = re.compile(
        r'^(\d+(?:\.\d+)?)["\']?\s*[-–—]\s*([A-Z]{1,4})\s*[-–—]\s*(\d{3,})',
        re.IGNORECASE
    )
    
    # ADNOC LINE NUMBER (Abu Dhabi Oil Co. Ltd): SIZE"-FLUID-PIPINGCLASS-SEQUENCE
    # Examples: 6"-CD-AC3N-8256, 8"-HO-BD2A-1023, 10"-AG-XY1Z-9999
    ADNOC_LINE_NUMBER_PATTERN = re.compile(
        r'^(\d{1,2})"\s*[-–—]\s*([A-Z]{2,3})\s*[-–—]\s*([A-Z0-9]{2,6})\s*[-–—]\s*(\d{4})$',
        re.IGNORECASE
    )
    
    # EQUIPMENT TAG: Letter-Number format (NOT from line numbers)
    # Examples: P-3610, V-201, E-301
    # Exclusion: D-XXXX where XXXX >= 1000 (drain lines)
    EQUIPMENT_PATTERN = re.compile(
        r'^([A-Z]{1,2})[-_](\d{2,4})([A-Z]?)$',
        re.IGNORECASE
    )
    
    # INSTRUMENT TAG: ISA-5.1 format
    # Examples: FT-101, PIC-3601, 13-FE-4580, TI-201A
    INSTRUMENT_PATTERN = re.compile(
        r'^(?:\d{1,2}[-])?([A-Z]{2,3}(?:IC|IT|IND|SH|SL|AL|AH|V|C|I|T|E|Y|S|G|M)?[-_]?\d{3,5}[A-Z]?)$',
        re.IGNORECASE
    )
    
    # DRAWING NUMBER: Dot-separated (NOT dash-separated)
    # Examples: 16.01.08.1678, 14.01.08.1603
    DRAWING_NUMBER_PATTERN = re.compile(
        r'^\d{1,4}\.\d{1,4}\.\d{1,4}\.\d{2,6}$'
    )
    
    # P&ID CONNECTOR: NN-PP-NNN-NNNNN
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
    
    # REGULAR NOTE/HOLD: Without DELETED
    NOTE_PATTERN = re.compile(
        r'\b(NOTE|HOLD)\s*(\d+)\b',
        re.IGNORECASE
    )
    
    # SPEC BREAK: Material class transition
    # Examples: SPEC BRK, SB-1, SPEC BREAK
    SPEC_BREAK_PATTERN = re.compile(
        r'\b(SPEC\s*BR?K?|SB[-]?\d+|MATERIAL\s*CHANGE)\b',
        re.IGNORECASE
    )
    
    # VALVE: Common valve types
    VALVE_PATTERN = re.compile(
        r'\b(VALVE|[A-Z]V[-]?\d+|GATE|GLOBE|BALL|CHECK|BUTTERFLY)\b',
        re.IGNORECASE
    )
    
    # ARROW/CONNECTOR indicators
    ARROW_PATTERN = re.compile(
        r'\b(ARROW|CONNECTOR|FLOW\s*DIRECTION|\-\>|\<\-)\b',
        re.IGNORECASE
    )
    
    def __init__(self):
        self.instrument_tags = set()
        self.equipment_tags = set()
        self.line_numbers = set()
        self.notes = {}  # {note_id: text}
        self.deleted_notes = set()  # IDs to ignore
        self.spec_breaks = []
        self.valves = []
        self.drawing_numbers = set()
        self.connectors = set()
    
    def classify_token(self, token: str) -> Tuple[ElementType, str]:
        """
        Classify a single token deterministically
        
        Returns:
            (ElementType, reason)
        """
        token = token.strip()
        if not token:
            return (ElementType.UNKNOWN, "empty")
        
        # RULE 1: Check DELETED notes first (must be ignored)
        if self.DELETED_NOTE_PATTERN.search(token):
            return (ElementType.DELETED_NOTE, "contains DELETED keyword")
        
        # RULE 2: Line numbers (HIGHEST PRIORITY - starts with size)
        if self.LINE_NUMBER_PATTERN.match(token):
            return (ElementType.LINE_NUMBER, "starts with pipe size")
        
        # RULE 2b: ADNOC Line numbers (Abu Dhabi Oil Co. Ltd format)
        if self.ADNOC_LINE_NUMBER_PATTERN.match(token):
            return (ElementType.LINE_NUMBER, "ADNOC format line number")
        
        # RULE 3: Drawing numbers (NOT line numbers)
        if self.DRAWING_NUMBER_PATTERN.match(token):
            return (ElementType.DRAWING_NUMBER, "dot-separated drawing number")
        
        # RULE 4: P&ID connectors
        if self.PID_CONNECTOR_PATTERN.match(token):
            return (ElementType.PID_CONNECTOR, "PP connector format")
        
        # RULE 5: Instrument tags (ISA format)
        if self.INSTRUMENT_PATTERN.match(token):
            return (ElementType.INSTRUMENT_TAG, "ISA-5.1 format")
        
        # RULE 6: Equipment tags (but NOT if from line number)
        eq_match = self.EQUIPMENT_PATTERN.match(token)
        if eq_match:
            prefix, number, suffix = eq_match.groups()
            number_val = int(number)
            
            # CRITICAL: D-XXXX where XXXX >= 1000 is a drain line, NOT equipment
            if prefix.upper() == 'D' and number_val >= 1000:
                return (ElementType.UNKNOWN, "drain line prefix in line number")
            
            return (ElementType.EQUIPMENT_TAG, "equipment tag format")
        
        # RULE 7: Notes/Holds
        if self.NOTE_PATTERN.search(token):
            return (ElementType.NOTE, "note reference")
        
        # RULE 8: Spec breaks
        if self.SPEC_BREAK_PATTERN.search(token):
            return (ElementType.SPEC_BREAK, "spec break indicator")
        
        # RULE 9: Valves
        if self.VALVE_PATTERN.search(token):
            return (ElementType.VALVE, "valve indicator")
        
        # RULE 10: Arrows/Connectors
        if self.ARROW_PATTERN.search(token):
            return (ElementType.ARROW_CONNECTOR, "arrow/connector indicator")
        
        return (ElementType.UNKNOWN, "no pattern match")
    
    def classify_tokens(self, tokens: List[str]) -> Dict[str, Any]:
        """
        Classify all tokens and group by type
        
        Returns:
            {
                'line_numbers': Set[str],
                'equipment_tags': Set[str],
                'instrument_tags': Set[str],
                'notes': Dict[int, str],
                'deleted_notes': Set[int],
                'spec_breaks': List[str],
                'valves': List[str],
                'connectors': Set[str],
                'drawing_numbers': Set[str]
            }
        """
        for token in tokens:
            element_type, reason = self.classify_token(token)
            
            if element_type == ElementType.LINE_NUMBER:
                self.line_numbers.add(token)
            
            elif element_type == ElementType.EQUIPMENT_TAG:
                # Double-check it's not inside a line number
                is_substring = False
                for line_num in self.line_numbers:
                    if token in line_num:
                        is_substring = True
                        break
                
                if not is_substring:
                    self.equipment_tags.add(token)
            
            elif element_type == ElementType.INSTRUMENT_TAG:
                self.instrument_tags.add(token)
            
            elif element_type == ElementType.NOTE:
                match = self.NOTE_PATTERN.search(token)
                if match:
                    note_type, note_id = match.groups()
                    self.notes[int(note_id)] = token
            
            elif element_type == ElementType.DELETED_NOTE:
                match = self.NOTE_PATTERN.search(token)
                if match:
                    note_id = int(match.group(2))
                    self.deleted_notes.add(note_id)
            
            elif element_type == ElementType.SPEC_BREAK:
                self.spec_breaks.append(token)
            
            elif element_type == ElementType.VALVE:
                self.valves.append(token)
            
            elif element_type == ElementType.PID_CONNECTOR:
                self.connectors.add(token)
            
            elif element_type == ElementType.DRAWING_NUMBER:
                self.drawing_numbers.add(token)
        
        # Remove notes that are marked DELETED
        active_notes = {
            note_id: text 
            for note_id, text in self.notes.items() 
            if note_id not in self.deleted_notes
        }
        
        return {
            'line_numbers': self.line_numbers,
            'equipment_tags': self.equipment_tags,
            'instrument_tags': self.instrument_tags,
            'notes': active_notes,
            'deleted_notes': self.deleted_notes,
            'spec_breaks': self.spec_breaks,
            'valves': self.valves,
            'connectors': self.connectors,
            'drawing_numbers': self.drawing_numbers
        }
