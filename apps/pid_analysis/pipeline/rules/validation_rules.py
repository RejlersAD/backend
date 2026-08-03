"""
Deterministic Validation Rules
NO LLM - Pure logic-based validation
"""
from typing import Dict, List, Any, Set
from abc import ABC, abstractmethod


class ValidationRule(ABC):
    """Base class for all validation rules"""
    
    def __init__(self, rule_name: str, severity: str = "medium"):
        self.rule_name = rule_name
        self.severity = severity  # "high", "medium", "low"
    
    @abstractmethod
    def validate(self, extracted_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Run validation rule on extracted data
        
        Returns:
            List of issues found:
            [
                {
                    'line_number': str,
                    'issue_type': str,
                    'description': str,
                    'recommendation': str,
                    'confidence': 'high',  # Deterministic rules always high
                    'rule_name': str
                }
            ]
        """
        pass


class LineClassificationRule(ValidationRule):
    """
    RULE 1: Line Classification Fix
    NEVER treat line numbers as equipment
    """
    
    def __init__(self):
        super().__init__("line_classification", severity="high")
    
    def validate(self, extracted_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Prevent misclassification of line numbers as equipment"""
        issues = []
        
        lines = extracted_data.get('lines', set())
        equipment = extracted_data.get('equipment', set())
        
        # Check if any equipment tag appears inside line numbers
        for eq_tag in equipment:
            for line_num in lines:
                if eq_tag in line_num:
                    # This equipment tag is actually part of a line number
                    issues.append({
                        'line_number': line_num,
                        'issue_type': 'misclassification',
                        'description': f"Equipment tag '{eq_tag}' incorrectly extracted from line number '{line_num}'",
                        'recommendation': f"Remove '{eq_tag}' from equipment list - it is part of line number",
                        'confidence': 'high',
                        'rule_name': self.rule_name
                    })
        
        return issues


class NoteHandlingRule(ValidationRule):
    """
    RULE 2: Note Handling
    If note contains "DELETED" → ignore completely
    """
    
    def __init__(self):
        super().__init__("note_handling", severity="high")
    
    def validate(self, extracted_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Verify DELETED notes are not referenced"""
        issues = []
        
        deleted_notes = extracted_data.get('deleted_notes', set())
        active_notes = extracted_data.get('notes', {})
        
        # Warn if deleted notes still appear in active list
        for note_id in deleted_notes:
            if note_id in active_notes:
                issues.append({
                    'line_number': 'N/A',
                    'issue_type': 'deleted_note_reference',
                    'description': f"Note {note_id} is marked DELETED but still referenced",
                    'recommendation': f"Remove all references to Note {note_id}",
                    'confidence': 'high',
                    'rule_name': self.rule_name
                })
        
        if deleted_notes:
            print(f"[RULE] Ignoring {len(deleted_notes)} deleted note(s): {deleted_notes}")
        
        return issues


class SpecBreakRule(ValidationRule):
    """
    RULE 3: Spec Break Logic
    If spec_break exists → do NOT raise material transition issue
    """
    
    def __init__(self):
        super().__init__("spec_break_validation", severity="medium")
    
    def validate(self, extracted_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Check spec breaks are properly documented"""
        issues = []
        
        spec_breaks = extracted_data.get('spec_breaks', [])
        lines = extracted_data.get('lines', set())
        
        if not spec_breaks and len(lines) > 10:
            # Large drawing with no spec breaks might be missing documentation
            issues.append({
                'line_number': 'Multiple',
                'issue_type': 'missing_spec_breaks',
                'description': f"Drawing has {len(lines)} lines but no spec breaks documented",
                'recommendation': "Verify all material class transitions have spec break symbols",
                'confidence': 'medium',
                'rule_name': self.rule_name
            })
        
        print(f"[RULE] Found {len(spec_breaks)} spec break(s) in drawing")
        return issues


class ReducerValidationRule(ValidationRule):
    """
    RULE 4: Reducer Validation
    If reducer detected → do NOT suggest adding
    If small line from large header → allow (no error)
    """
    
    def __init__(self):
        super().__init__("reducer_validation", severity="low")
    
    def validate(self, extracted_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Check reducer usage"""
        issues = []
        
        reducers = extracted_data.get('reducers', [])
        lines = extracted_data.get('lines', set())
        
        # Check for size transitions in line numbers
        size_changes = []
        for line_num in lines:
            # Extract pipe size from line number (e.g., "2"-...)
            import re
            size_match = re.match(r'^(\d+(?:\.\d+)?)', line_num)
            if size_match:
                size = size_match.group(1)
                size_changes.append((line_num, size))
        
        # Group by line series to detect transitions
        # This is a simplified check - real implementation would be more sophisticated
        
        print(f"[RULE] Detected {len(reducers)} reducer(s) in drawing")
        return issues


class ArrowHandlingRule(ValidationRule):
    """
    RULE 5: Arrow Handling
    Arrows = connectors only
    Do NOT treat as pipelines
    """
    
    def __init__(self):
        super().__init__("arrow_handling", severity="medium")
    
    def validate(self, extracted_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Verify arrows are not misclassified"""
        issues = []
        
        arrows = extracted_data.get('arrows', [])
        connectors = extracted_data.get('connectors', set())
        lines = extracted_data.get('lines', set())
        
        # Check if any line number looks like a connector
        for line_num in lines:
            if 'ARROW' in line_num.upper() or '->' in line_num:
                issues.append({
                    'line_number': line_num,
                    'issue_type': 'arrow_misclassification',
                    'description': f"'{line_num}' appears to be an arrow/connector, not a pipeline",
                    'recommendation': "Reclassify as connector, not pipeline line number",
                    'confidence': 'high',
                    'rule_name': self.rule_name
                })
        
        print(f"[RULE] Found {len(arrows)} arrow(s) and {len(connectors)} connector(s)")
        return issues


class DuplicateLineRule(ValidationRule):
    """
    RULE 6: Duplicate Line Detection
    Detect exact duplicates
    """
    
    def __init__(self):
        super().__init__("duplicate_detection", severity="medium")
    
    def validate(self, extracted_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Find duplicate line numbers"""
        issues = []
        
        lines = list(extracted_data.get('lines', set()))
        seen = set()
        duplicates = set()
        
        for line_num in lines:
            if line_num in seen:
                duplicates.add(line_num)
            seen.add(line_num)
        
        for dup in duplicates:
            issues.append({
                'line_number': dup,
                'issue_type': 'duplicate_line',
                'description': f"Line number '{dup}' appears multiple times in drawing",
                'recommendation': "Verify if duplicate or remove redundant instance",
                'confidence': 'high',
                'rule_name': self.rule_name
            })
        
        if duplicates:
            print(f"[RULE] Found {len(duplicates)} duplicate line number(s)")
        
        return issues


class MissingDataRule(ValidationRule):
    """
    RULE 7: Missing Data Detection
    Detect missing:
    - design pressure
    - temperature (if applicable)
    - material specifications
    """
    
    def __init__(self):
        super().__init__("missing_data", severity="medium")
    
    def validate(self, extracted_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Find missing critical data"""
        issues = []
        
        raw_text = extracted_data.get('raw_text', '').upper()
        lines = extracted_data.get('lines', set())
        
        # Check for common missing data patterns
        has_pressure = any(keyword in raw_text for keyword in 
                          ['PRESSURE', 'PSI', 'BAR', 'KPA', 'MPA'])
        
        has_temperature = any(keyword in raw_text for keyword in 
                             ['TEMP', '°C', '°F', 'CELSIUS', 'FAHRENHEIT'])
        
        has_material = any(keyword in raw_text for keyword in 
                          ['CS', 'SS', 'STAINLESS', 'CARBON STEEL', 'A106', 'A312'])
        
        if not has_pressure and len(lines) > 0:
            issues.append({
                'line_number': 'Multiple',
                'issue_type': 'missing_pressure',
                'description': "Design pressure not clearly specified in drawing",
                'recommendation': "Add design pressure specifications to line list or notes",
                'confidence': 'medium',
                'rule_name': self.rule_name
            })
        
        if not has_material and len(lines) > 0:
            issues.append({
                'line_number': 'Multiple',
                'issue_type': 'missing_material',
                'description': "Material specifications not clearly indicated",
                'recommendation': "Add material class specifications or spec breaks",
                'confidence': 'medium',
                'rule_name': self.rule_name
            })
        
        return issues
