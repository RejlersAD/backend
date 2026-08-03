"""
Sanity Checker Agent - Removes hallucinations and false positives
"""
from typing import Dict, List, Any


class SanityCheckerAgent:
    """
    Agent 2: Remove hallucinated issues
    
    Filters out:
    - References to data not in extracted_data
    - Cross-document contamination
    - Invented notes or specs
    """
    
    def __init__(self):
        pass
    
    def check(
        self, 
        issues: List[Dict[str, Any]], 
        extracted_data: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Remove hallucinated or false positive issues
        
        Args:
            issues: Combined issues from rules + verifier
            extracted_data: Ground truth data
        
        Returns:
            Filtered issues (only valid ones)
        """
        print(f"[SANITY CHECKER] Checking {len(issues)} issue(s) for hallucinations...")
        
        valid_issues = []
        removed_count = 0
        
        for issue in issues:
            if self._is_valid_issue(issue, extracted_data):
                valid_issues.append(issue)
            else:
                removed_count += 1
                print(f"[SANITY CHECKER] Removed hallucinated issue: {issue.get('description', 'N/A')[:60]}")
        
        print(f"[SANITY CHECKER] Removed {removed_count} hallucinated issue(s)")
        print(f"[SANITY CHECKER] {len(valid_issues)} valid issue(s) remaining")
        
        return valid_issues
    
    def _is_valid_issue(self, issue: Dict[str, Any], extracted_data: Dict[str, Any]) -> bool:
        """Check if issue is grounded in extracted data"""
        
        line_number = issue.get('line_number', '')
        description = issue.get('description', '').upper()
        
        # Get ground truth sets
        lines = extracted_data.get('lines', set())
        equipment = extracted_data.get('equipment', set())
        instruments = extracted_data.get('instruments', set())
        notes = extracted_data.get('notes', {})
        deleted_notes = extracted_data.get('deleted_notes', set())
        
        # CHECK 1: If line number specified, it must exist
        if line_number and line_number != 'Multiple' and line_number != 'N/A':
            # Check if line exists in extracted data
            line_exists = any(line_number in line for line in lines)
            if not line_exists:
                print(f"[SANITY CHECKER] Line '{line_number}' not in extracted data")
                return False
        
        # CHECK 2: Don't reference DELETED notes
        for note_id in deleted_notes:
            if f'NOTE {note_id}' in description or f'NOTE{note_id}' in description:
                print(f"[SANITY CHECKER] References deleted note {note_id}")
                return False
        
        # CHECK 3: Don't reference notes that don't exist
        import re
        note_refs = re.findall(r'NOTE\s*(\d+)', description)
        for note_ref in note_refs:
            note_id = int(note_ref)
            if note_id not in notes and note_id not in deleted_notes:
                print(f"[SANITY CHECKER] References non-existent note {note_id}")
                return False
        
        # CHECK 4: Don't reference equipment/instruments not in extracted data
        # CRITICAL: D-XXXX inside line numbers is NOT equipment
        import re
        for eq_tag in equipment:
            # Skip equipment tags that appear inside line numbers
            for line in lines:
                if eq_tag in line:
                    # This equipment tag is part of a line number, not standalone
                    equipment = equipment - {eq_tag}
                    break
        
        # CHECK 5: Don't flag "D-6155" as equipment if it's inside a line like "2"-D-6155-..."
        eq_in_desc = re.findall(r'\b([A-Z]-\d{3,5})\b', description)
        for eq in eq_in_desc:
            # Check if this matches a line number pattern component
            is_inside_line = any(eq in line for line in lines)
            if is_inside_line and eq not in equipment:
                print(f"[SANITY CHECKER] '{eq}' is part of line number, not equipment")
                return False
        
        # CHECK 6: Don't reference standards not mentioned in raw text
        raw_text = extracted_data.get('raw_text', '').upper()
        
        # Common false positive patterns
        false_positive_keywords = [
            'NACE MR0175',  # Only if not in raw_text
            'ASME B31.3',   # Only if not in raw_text
            'API 570',      # Only if not in raw_text
        ]
        
        for keyword in false_positive_keywords:
            if keyword in description and keyword not in raw_text:
                print(f"[SANITY CHECKER] References standard '{keyword}' not in document")
                return False
        
        # Passed all checks
        return True
