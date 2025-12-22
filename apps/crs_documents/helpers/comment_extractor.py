"""
PDF Comment Extractor - NEW HELPER MODULE
Extracts reviewer comments from ConsolidatedComments PDF
Does NOT modify existing code or APIs
"""

import re
import PyPDF2
from typing import List, Dict, Optional
from io import BytesIO


class ReviewerComment:
    """Data structure for a single reviewer comment"""
    
    def __init__(self):
        self.reviewer_name: str = "Not Provided"
        self.comment_text: str = ""
        self.discipline: str = "Not Provided"
        self.section_reference: str = "Not Provided"
        self.page_number: Optional[int] = None
        self.comment_type: str = "GENERAL"
        self.raw_text: str = ""


def extract_reviewer_comments(pdf_buffer: BytesIO) -> List[ReviewerComment]:
    """
    Extract reviewer comments from PDF
    
    Args:
        pdf_buffer: BytesIO object containing PDF file
        
    Returns:
        List of ReviewerComment objects
        
    Implementation: Safe, isolated, no side effects
    """
    comments = []
    
    try:
        pdf_reader = PyPDF2.PdfReader(pdf_buffer)
        
        # Extract text from all pages
        for page_num, page in enumerate(pdf_reader.pages, start=1):
            text = page.extract_text()
            
            # Extract comments from this page
            page_comments = _extract_comments_from_text(text, page_num)
            comments.extend(page_comments)
            
            # Extract PDF annotations (callouts, highlights, etc.)
            if '/Annots' in page:
                annotation_comments = _extract_annotations(page, page_num)
                comments.extend(annotation_comments)
    
    except Exception as e:
        print(f"Error extracting PDF comments: {str(e)}")
        # Return empty list on error - safe fallback
        return []
    
    # Deduplicate and clean
    comments = _deduplicate_comments(comments)
    
    return comments


def _extract_comments_from_text(text: str, page_num: int) -> List[ReviewerComment]:
    """Extract comments from page text using pattern matching"""
    comments = []
    
    # Pattern 1: "Name – Callout:" or "Name - Comment:"
    callout_pattern = r'([A-Z][a-z]+ [A-Z][a-z]+)\s*[–-]\s*(Callout|Comment|Note):\s*(.+?)(?=\n[A-Z][a-z]+ [A-Z][a-z]+\s*[–-]|\n\n|$)'
    
    for match in re.finditer(callout_pattern, text, re.DOTALL):
        comment = ReviewerComment()
        comment.reviewer_name = match.group(1).strip()
        comment.comment_text = match.group(3).strip()
        comment.page_number = page_num
        comment.raw_text = match.group(0)
        comment.comment_type = classify_comment(comment.comment_text)
        comment.discipline = _extract_discipline(text, comment.reviewer_name)
        comments.append(comment)
    
    # Pattern 2: Standalone directive statements
    directive_patterns = [
        r'(EC shall\s+.+?)(?=\n\n|\n[A-Z][a-z]+ [A-Z][a-z]+|$)',
        r'(Please\s+.+?)(?=\n\n|\n[A-Z][a-z]+ [A-Z][a-z]+|$)',
        r'(What about\s+.+?)(?=\n\n|$)',
        r'(To be evaluated\s+.+?)(?=\n\n|$)',
        r'(HOLD[\s\S]+?)(?=\n\n|$)',
    ]
    
    for pattern in directive_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            comment_text = match.group(1).strip()
            
            # Skip if too short or too long (likely not a comment)
            if len(comment_text) < 10 or len(comment_text) > 500:
                continue
            
            comment = ReviewerComment()
            comment.comment_text = comment_text
            comment.page_number = page_num
            comment.raw_text = match.group(0)
            comment.comment_type = classify_comment(comment_text)
            comment.reviewer_name = _extract_reviewer_from_context(text, match.start())
            comment.discipline = _extract_discipline(text, comment.reviewer_name)
            comments.append(comment)
    
    return comments


def _extract_annotations(page, page_num: int) -> List[ReviewerComment]:
    """Extract comments from PDF annotations (callouts, sticky notes, etc.)"""
    comments = []
    
    try:
        annotations = page['/Annots']
        
        for annotation in annotations:
            annot_obj = annotation.get_object()
            
            # Check if it's a text annotation
            if annot_obj.get('/Subtype') == '/Text' or annot_obj.get('/Subtype') == '/FreeText':
                comment = ReviewerComment()
                
                # Get comment content
                if '/Contents' in annot_obj:
                    comment.comment_text = str(annot_obj['/Contents'])
                    comment.page_number = page_num
                    comment.comment_type = classify_comment(comment.comment_text)
                    
                    # Try to extract reviewer from annotation
                    if '/T' in annot_obj:  # Title/Author field
                        comment.reviewer_name = str(annot_obj['/T'])
                    
                    comments.append(comment)
    
    except Exception as e:
        # Silently handle annotation extraction errors
        pass
    
    return comments


def _extract_reviewer_from_context(text: str, position: int) -> str:
    """Extract reviewer name from surrounding context"""
    # Look backwards up to 200 characters for a name
    context = text[max(0, position - 200):position]
    
    # Look for "Name:" or "Reviewer: Name"
    name_pattern = r'([A-Z][a-z]+ [A-Z][a-z]+)(?:\s*:|$)'
    matches = re.findall(name_pattern, context)
    
    if matches:
        return matches[-1]  # Return the closest name
    
    return "Not Provided"


def _extract_discipline(text: str, reviewer_name: str) -> str:
    """Extract discipline from document context"""
    disciplines = {
        'DCU': ['DCU', 'Distributed Control Unit'],
        'MHC': ['MHC', 'Material Handling'],
        'Utilities': ['Utilities', 'Utility'],
        'Safety': ['Safety', 'HSE', 'Health'],
        'Process': ['Process Engineering', 'Process'],
        'Electrical': ['Electrical', 'E&I', 'Instrumentation'],
        'Mechanical': ['Mechanical', 'Rotating Equipment'],
        'Civil': ['Civil', 'Structural'],
        'Piping': ['Piping', 'Pipeline'],
    }
    
    # Search in the text around the reviewer name
    search_text = text.lower()
    reviewer_pos = search_text.find(reviewer_name.lower())
    
    if reviewer_pos >= 0:
        # Check 500 characters before and after
        context = search_text[max(0, reviewer_pos - 500):min(len(search_text), reviewer_pos + 500)]
        
        for discipline, keywords in disciplines.items():
            for keyword in keywords:
                if keyword.lower() in context:
                    return discipline
    
    return "Not Provided"


def _deduplicate_comments(comments: List[ReviewerComment]) -> List[ReviewerComment]:
    """Remove duplicate comments based on text similarity"""
    unique_comments = []
    seen_texts = set()
    
    for comment in comments:
        # Create a normalized version for comparison
        normalized = comment.comment_text.lower().strip()[:100]  # First 100 chars
        
        if normalized not in seen_texts and len(normalized) > 10:
            seen_texts.add(normalized)
            unique_comments.append(comment)
    
    return unique_comments


def classify_comment(comment_text: str) -> str:
    """
    Classify comment type based on content
    
    Returns one of: HOLD, ADEQUACY, RECOMMENDATION, CLARIFICATION, GENERAL
    """
    text_lower = comment_text.lower()
    
    # HOLD - Blocking issues
    if any(keyword in text_lower for keyword in ['hold', 'stop', 'do not proceed', 'must not']):
        return 'HOLD'
    
    # ADEQUACY - Requirements and compliance
    if any(keyword in text_lower for keyword in ['shall', 'must', 'required', 'ec shall', 'contractor shall']):
        return 'ADEQUACY'
    
    # RECOMMENDATION - Suggestions
    if any(keyword in text_lower for keyword in ['recommend', 'suggest', 'consider', 'should', 'could']):
        return 'RECOMMENDATION'
    
    # CLARIFICATION - Questions and requests for info
    if any(keyword in text_lower for keyword in ['?', 'clarify', 'what about', 'please explain', 'unclear', 'confirm']):
        return 'CLARIFICATION'
    
    return 'GENERAL'


def get_comment_statistics(comments: List[ReviewerComment]) -> Dict:
    """
    Generate statistics from extracted comments
    Safe helper function for reporting
    """
    stats = {
        'total': len(comments),
        'by_type': {},
        'by_discipline': {},
        'by_reviewer': {},
        'pages_with_comments': set(),
    }
    
    for comment in comments:
        # Count by type
        comment_type = comment.comment_type
        stats['by_type'][comment_type] = stats['by_type'].get(comment_type, 0) + 1
        
        # Count by discipline
        discipline = comment.discipline
        stats['by_discipline'][discipline] = stats['by_discipline'].get(discipline, 0) + 1
        
        # Count by reviewer
        reviewer = comment.reviewer_name
        stats['by_reviewer'][reviewer] = stats['by_reviewer'].get(reviewer, 0) + 1
        
        # Track pages
        if comment.page_number:
            stats['pages_with_comments'].add(comment.page_number)
    
    stats['pages_with_comments'] = len(stats['pages_with_comments'])
    
    return stats
