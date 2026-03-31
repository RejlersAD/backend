"""
Rule Engine - Orchestrates all deterministic validation rules
"""
from typing import Dict, List, Any
from .validation_rules import (
    LineClassificationRule,
    NoteHandlingRule,
    SpecBreakRule,
    ReducerValidationRule,
    ArrowHandlingRule,
    DuplicateLineRule,
    MissingDataRule
)


class RuleEngine:
    """
    Deterministic rule engine that runs BEFORE LLM
    Replaces most LLM logic with deterministic checks
    """
    
    def __init__(self):
        # Initialize all rules
        self.rules = [
            LineClassificationRule(),
            NoteHandlingRule(),
            SpecBreakRule(),
            ReducerValidationRule(),
            ArrowHandlingRule(),
            DuplicateLineRule(),
            MissingDataRule()
        ]
        
        print(f"[RULE ENGINE] Initialized with {len(self.rules)} deterministic rules")
    
    def validate(self, extracted_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Run all validation rules on extracted data
        
        Args:
            extracted_data: Output from PIDExtractor
        
        Returns:
            List of issues found by deterministic rules
        """
        print(f"[RULE ENGINE] Running {len(self.rules)} validation rules...")
        
        all_issues = []
        
        for rule in self.rules:
            try:
                issues = rule.validate(extracted_data)
                if issues:
                    print(f"[RULE ENGINE] {rule.rule_name}: Found {len(issues)} issue(s)")
                    all_issues.extend(issues)
                else:
                    print(f"[RULE ENGINE] {rule.rule_name}: No issues")
            except Exception as e:
                print(f"[RULE ENGINE ERROR] {rule.rule_name} failed: {e}")
        
        print(f"[RULE ENGINE] Total issues from deterministic rules: {len(all_issues)}")
        return all_issues
    
    def get_grounding_data(self, extracted_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract grounding data for LLM validation
        
        This data is the SOURCE OF TRUTH - LLM must NOT contradict it
        
        Returns:
            {
                'lines_present': Set[str],
                'equipment_present': Set[str],
                'instruments_present': Set[str],
                'notes_present': Dict[int, str],
                'deleted_notes': Set[int],
                'spec_breaks_present': List[str],
                'reducers_present': int,
                'document_id': str
            }
        """
        return {
            'lines_present': extracted_data.get('lines', set()),
            'equipment_present': extracted_data.get('equipment', set()),
            'instruments_present': extracted_data.get('instruments', set()),
            'notes_present': extracted_data.get('notes', {}),
            'deleted_notes': extracted_data.get('deleted_notes', set()),
            'spec_breaks_present': extracted_data.get('spec_breaks', []),
            'reducers_present': len(extracted_data.get('reducers', [])),
            'document_id': extracted_data.get('document_id', 'unknown')
        }
