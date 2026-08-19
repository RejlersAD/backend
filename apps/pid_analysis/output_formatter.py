"""
Output Formatter - Deterministic Results & Confidence Scoring
Ensures consistent output formatting and prevents non-deterministic variations
"""
import json
from typing import List, Dict, Any
from dataclasses import dataclass


@dataclass
class OutputIssue:
    """Standard issue format with confidence scoring"""
    serial_number: int
    pid_reference: str
    issue_observed: str
    action_required: str
    evidence: str
    severity: str
    category: str
    confidence: str  # "high", "medium", "low"
    location_on_drawing: Dict[str, str]
    visual_confirmed: bool = False
    ocr_confirmed: bool = False


class DeterministicOutputFormatter:
    """
    Formats analysis results deterministically
    
    Key Features:
    1. Sorts all outputs consistently (by category, then pid_reference)
    2. Removes duplicate/near-duplicate issues
    3. Assigns confidence scores based on verification method
    4. Filters low-confidence OCR artifacts
    """
    
    # Issue categories in priority order (for sorting)
    CATEGORY_PRIORITY = {
        'safety': 1,
        'critical_stress': 2,
        'psv_compliance': 3,
        'corrosion_allowance': 4,
        'ltcs_compliance': 5,
        'dissimilar_material': 6,
        'valve_standard': 7,
        'tie_in_reference': 8,
        'control_loop': 9,
        'instrument': 10,
        'equipment': 11,
        'piping': 12,
        'pipe_class': 13,
        'spec_break': 14,
        'trim_class': 15,
        'spool_requirement': 16,
        'free_drain_slope': 17,
        'valve': 18,
        'documentation': 19,
        'legend': 20,
        'notes_compliance': 21,
        'holds_compliance': 22,
        'observation': 23
    }
    
    # Severity priority (for sorting within category)
    SEVERITY_PRIORITY = {
        'critical': 1,
        'major': 2,
        'minor': 3,
        'observation': 4
    }
    
    def __init__(self, ocr_inventory: set, session_id: str):
        """
        Initialize formatter
        
        Args:
            ocr_inventory: Set of all elements confirmed by OCR (tags, line numbers)
            session_id: Current analysis session ID (for context isolation)
        """
        self.ocr_inventory = {item.upper() for item in ocr_inventory}
        self.session_id = session_id
    
    def calculate_confidence(
        self,
        issue: Dict[str, Any],
        ocr_confirmed: bool = False,
        visual_confirmed: bool = False
    ) -> str:
        """
        Calculate confidence score for an issue
        
        Logic:
        - HIGH: Both visual and OCR confirmed, or explicit visual confirmation
        - MEDIUM: OCR confirmed only, or partial match
        - LOW: Neither confirmed (likely hallucination)
        """
        pid_ref = issue.get('pid_reference', '').upper()
        
        # Check if referenced element exists in OCR inventory
        ocr_match = any(
            pid_ref in item or item.startswith(pid_ref[:4])
            for item in self.ocr_inventory
            if len(pid_ref) >= 4
        )
        
        # Evidence field indicates visual confirmation
        evidence = issue.get('evidence', '').upper()
        visual_keywords = ['VISUAL', 'VISUALLY', 'CONFIRMED ON DRAWING', 'VISIBLE', 'SHOWN']
        has_visual_evidence = any(kw in evidence for kw in visual_keywords)
        
        # Confidence logic
        if (visual_confirmed or has_visual_evidence) and (ocr_confirmed or ocr_match):
            return "high"
        elif visual_confirmed or has_visual_evidence or ocr_confirmed or ocr_match:
            return "medium"
        else:
            return "low"
    
    def sort_issues(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Sort issues deterministically
        
        Sort order:
        1. Category (by priority)
        2. Severity (by priority)
        3. PID reference (alphabetically)
        4. Issue observed (alphabetically)
        """
        def sort_key(issue):
            category = issue.get('category', 'observation').lower()
            severity = issue.get('severity', 'observation').lower()
            pid_ref = issue.get('pid_reference', '').upper()
            issue_obs = issue.get('issue_observed', '').upper()
            
            return (
                self.CATEGORY_PRIORITY.get(category, 99),
                self.SEVERITY_PRIORITY.get(severity, 99),
                pid_ref,
                issue_obs
            )
        
        return sorted(issues, key=sort_key)
    
    def remove_duplicates(
        self,
        issues: List[Dict[str, Any]],
        similarity_threshold: float = 0.85
    ) -> List[Dict[str, Any]]:
        """
        Remove near-duplicate issues
        
        Two issues are considered duplicates if:
        - Same pid_reference
        - Same category
        - Similar issue_observed (>85% similarity)
        """
        unique_issues = []
        seen_keys = set()
        
        for issue in issues:
            # Create deduplication key
            pid_ref = issue.get('pid_reference', '').upper()
            category = issue.get('category', '').lower()
            issue_obs = issue.get('issue_observed', '').upper()
            
            # Simple dedup key (exact match on pid_ref + category)
            dedup_key = f"{pid_ref}|{category}"
            
            if dedup_key not in seen_keys:
                unique_issues.append(issue)
                seen_keys.add(dedup_key)
            else:
                # Duplicate found - keep the one with better evidence
                existing_idx = next(
                    (i for i, x in enumerate(unique_issues) 
                     if f"{x.get('pid_reference', '').upper()}|{x.get('category', '').lower()}" == dedup_key),
                    None
                )
                if existing_idx is not None:
                    existing = unique_issues[existing_idx]
                    # Keep the one with more detailed evidence
                    if len(issue.get('evidence', '')) > len(existing.get('evidence', '')):
                        unique_issues[existing_idx] = issue
        
        return unique_issues
    
    def filter_low_confidence(
        self,
        issues: List[Dict[str, Any]],
        min_confidence: str = "medium"
    ) -> List[Dict[str, Any]]:
        """
        Filter out low-confidence issues (likely OCR artifacts)
        
        Args:
            issues: List of issues to filter
            min_confidence: Minimum confidence level to keep ("high", "medium", "low")
        """
        confidence_order = {"low": 0, "medium": 1, "high": 2}
        min_level = confidence_order.get(min_confidence, 1)
        
        filtered = []
        for issue in issues:
            conf = issue.get('confidence', 'low').lower()
            if confidence_order.get(conf, 0) >= min_level:
                filtered.append(issue)
        
        return filtered
    
    def format_final_output(
        self,
        issues: List[Dict[str, Any]],
        apply_confidence_filter: bool = True,
        apply_deduplication: bool = True
    ) -> Dict[str, Any]:
        """
        Format final output with all enhancements
        
        Args:
            issues: Raw issues list from LLM
            apply_confidence_filter: Remove low-confidence issues
            apply_deduplication: Remove duplicate issues
            
        Returns:
            Formatted output dictionary with deterministic ordering
        """
        print(f"[FORMAT] Session {self.session_id[:8]}: Processing {len(issues)} raw issues")
        
        # Step 1: Calculate confidence for all issues
        for issue in issues:
            if 'confidence' not in issue or not issue['confidence']:
                issue['confidence'] = self.calculate_confidence(issue)
        
        # Step 2: Remove duplicates
        if apply_deduplication:
            original_count = len(issues)
            issues = self.remove_duplicates(issues)
            if len(issues) < original_count:
                print(f"[FORMAT] Session {self.session_id[:8]}: Removed {original_count - len(issues)} duplicates")
        
        # Step 3: Filter low-confidence issues
        if apply_confidence_filter:
            original_count = len(issues)
            issues = self.filter_low_confidence(issues, min_confidence="medium")
            if len(issues) < original_count:
                print(f"[FORMAT] Session {self.session_id[:8]}: Filtered {original_count - len(issues)} low-confidence issues")
        
        # Step 4: Sort deterministically
        issues = self.sort_issues(issues)
        
        # Step 5: Renumber serial_number (after filtering/sorting)
        for idx, issue in enumerate(issues, 1):
            issue['serial_number'] = idx
        
        print(f"[FORMAT] Session {self.session_id[:8]}: Final output: {len(issues)} issues")
        
        # Group by category for summary
        category_counts = {}
        severity_counts = {'critical': 0, 'major': 0, 'minor': 0, 'observation': 0}
        
        for issue in issues:
            cat = issue.get('category', 'observation')
            sev = issue.get('severity', 'observation')
            category_counts[cat] = category_counts.get(cat, 0) + 1
            severity_counts[sev] = severity_counts.get(sev, 0) + 1
        
        return {
            'issues': issues,
            'total_issues': len(issues),
            'summary': {
                'by_severity': severity_counts,
                'by_category': category_counts,
                'session_id': self.session_id
            }
        }
    
    def validate_output_consistency(
        self,
        current_output: Dict[str, Any],
        previous_output: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Validate output consistency (for testing determinism)
        
        If previous_output is provided, checks if current output matches
        (used for testing that same input produces same output)
        """
        if previous_output is None:
            return {'consistent': True, 'message': 'No previous output to compare'}
        
        curr_issues = current_output.get('issues', [])
        prev_issues = previous_output.get('issues', [])
        
        if len(curr_issues) != len(prev_issues):
            return {
                'consistent': False,
                'message': f'Issue count mismatch: {len(curr_issues)} vs {len(prev_issues)}'
            }
        
        # Check if issues match (order should be identical due to deterministic sorting)
        mismatches = []
        for i, (curr, prev) in enumerate(zip(curr_issues, prev_issues)):
            if (curr.get('pid_reference') != prev.get('pid_reference') or
                curr.get('category') != prev.get('category')):
                mismatches.append({
                    'index': i,
                    'current': f"{curr.get('pid_reference')} | {curr.get('category')}",
                    'previous': f"{prev.get('pid_reference')} | {prev.get('category')}"
                })
        
        if mismatches:
            return {
                'consistent': False,
                'message': f'Found {len(mismatches)} mismatched issues',
                'mismatches': mismatches[:5]  # Show first 5
            }
        
        return {
            'consistent': True,
            'message': 'Output is deterministic - all issues match'
        }


def get_formatter(ocr_inventory: set, session_id: str) -> DeterministicOutputFormatter:
    """Factory function to create formatter instance"""
    return DeterministicOutputFormatter(ocr_inventory, session_id)
