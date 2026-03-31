"""
Formatter Agent - Converts issues to exact output format
"""
from typing import Dict, List, Any
import re


class FormatterAgent:
    """
    Agent 3: Format output to match EXACT existing schema
    
    Ensures:
    - Field names match existing system
    - Wording is standardized
    - Sort order is deterministic
    """
    
    def __init__(self):
        # Standard wording templates
        self.wording_templates = {
            'missing_pressure': 'Design pressure not specified for line {line}',
            'missing_material': 'Material specification missing for line {line}',
            'duplicate_line': 'Line number {line} appears multiple times',
            'spec_break_missing': 'Material transition requires spec break symbol',
        }
    
    def format(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Format issues to match existing output schema
        
        Args:
            issues: Raw issues from rules + agents
        
        Returns:
            Formatted issues matching existing system output
        """
        print(f"[FORMATTER] Formatting {len(issues)} issue(s) for output...")
        
        formatted = []
        
        for issue in issues:
            formatted_issue = self._format_single_issue(issue)
            if formatted_issue:
                formatted.append(formatted_issue)
        
        # DETERMINISTIC SORTING
        formatted_sorted = self._sort_issues(formatted)
        
        # NORMALIZE TEXT
        formatted_normalized = self._normalize_text(formatted_sorted)
        
        print(f"[FORMATTER] Formatted {len(formatted_normalized)} issue(s)")
        return formatted_normalized
    
    def _format_single_issue(self, issue: Dict[str, Any]) -> Dict[str, Any]:
        """Format single issue to match schema"""
        
        # Required fields
        formatted = {
            'line_number': issue.get('line_number', 'N/A'),
            'issue_type': issue.get('issue_type', 'other'),
            'description': issue.get('description', 'No description'),
            'confidence': issue.get('confidence', 'medium')
        }
        
        # Optional fields
        if 'recommendation' in issue:
            formatted['recommendation'] = issue['recommendation']
        
        if 'rule_name' in issue:
            formatted['rule_name'] = issue['rule_name']
        
        if 'source' in issue:
            formatted['source'] = issue['source']
        
        return formatted
    
    def _sort_issues(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Sort issues deterministically
        
        Sort by:
        1. issue_type (alphabetically)
        2. line_number (alphabetically)
        """
        return sorted(
            issues,
            key=lambda x: (
                x.get('issue_type', 'zzzz'),
                x.get('line_number', 'zzzz')
            )
        )
    
    def _normalize_text(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Normalize text to use consistent wording
        
        This ensures same issues are worded identically
        """
        for issue in issues:
            # Use template if available
            issue_type = issue.get('issue_type', '')
            if issue_type in self.wording_templates:
                template = self.wording_templates[issue_type]
                issue['description'] = template.format(line=issue.get('line_number', 'N/A'))
            
            # Standardize confidence levels
            confidence = issue.get('confidence', 'medium').lower()
            if confidence not in ['high', 'medium', 'low']:
                issue['confidence'] = 'medium'
            
            # Clean up text
            issue['description'] = self._clean_text(issue['description'])
            if 'recommendation' in issue:
                issue['recommendation'] = self._clean_text(issue['recommendation'])
        
        return issues
    
    def _clean_text(self, text: str) -> str:
        """Clean and standardize text"""
        # Remove extra whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        
        # Standardize quotes
        text = text.replace('"', "'")
        
        # Capitalize first letter
        if text:
            text = text[0].upper() + text[1:]
        
        return text
