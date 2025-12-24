"""
PDF Comment Extractor - NEW HELPER MODULE
Extracts reviewer comments from ConsolidatedComments PDF
Does NOT modify existing code or APIs
Enhanced with intelligent comment cleaning
Uses PyMuPDF (fitz) for better annotation extraction
"""

import re
import fitz  # PyMuPDF - better for annotations
import PyPDF2
from typing import List, Dict, Optional
from io import BytesIO
import logging

# Import comment cleaner for intelligent text processing
try:
    from .comment_cleaner import get_comment_cleaner, CleaningResult
    CLEANER_AVAILABLE = True
except ImportError:
    CLEANER_AVAILABLE = False

logger = logging.getLogger(__name__)


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
        self.cleaned: bool = False  # Flag to indicate if cleaning was applied
        self.cleaning_method: str = ""  # "rule-based", "openai", or "hybrid"


def extract_reviewer_comments(pdf_buffer: BytesIO, apply_cleaning: bool = True) -> List[ReviewerComment]:
    """
    Extract reviewer comments from PDF using PyMuPDF (fitz)
    
    Args:
        pdf_buffer: BytesIO object containing PDF file
        apply_cleaning: Whether to apply intelligent comment cleaning (default: True)
        
    Returns:
        List of ReviewerComment objects
        
    Implementation: Safe, isolated, no side effects
    """
    comments = []
    
    # Initialize cleaner if available and cleaning is requested
    cleaner = None
    if apply_cleaning and CLEANER_AVAILABLE:
        try:
            cleaner = get_comment_cleaner()
            logger.info("✅ Comment cleaner initialized")
        except Exception as e:
            logger.warning(f"⚠️ Could not initialize comment cleaner: {e}")
    
    try:
        # Use PyMuPDF (fitz) for better annotation extraction
        pdf_bytes = pdf_buffer.read()
        pdf_buffer.seek(0)  # Reset for potential re-use
        
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        logger.info(f"📄 Opened PDF with {len(doc)} pages")
        
        for page_num in range(len(doc)):
            page = doc[page_num]
            
            # Extract annotations (comments, highlights, etc.)
            annotations = page.annots()
            if annotations:
                for annot in annotations:
                    try:
                        annot_type = annot.type[1]  # Get annotation type name
                        content = annot.info.get("content", "") or ""
                        title = annot.info.get("title", "") or ""  # Often contains author name
                        subject = annot.info.get("subject", "") or ""
                        
                        # Skip empty annotations
                        if not content.strip():
                            # Try to get text from popup or other sources
                            popup = annot.info.get("popup", "")
                            if popup:
                                content = str(popup)
                        
                        if not content.strip():
                            continue
                        
                        comment = ReviewerComment()
                        comment.comment_text = content.strip()
                        comment.page_number = page_num + 1
                        comment.comment_type = _map_annot_type_to_comment_type(annot_type)
                        comment.reviewer_name = title.strip() if title.strip() else "Not Provided"
                        comment.raw_text = f"{annot_type}: {content}"
                        
                        # Try to extract discipline from content
                        comment.discipline = _extract_discipline_from_text(content)
                        
                        comments.append(comment)
                        logger.debug(f"Found annotation: {annot_type} - {content[:50]}...")
                        
                    except Exception as e:
                        logger.warning(f"Error extracting annotation: {e}")
                        continue
            
            # Also extract text-based comments from page content
            text = page.get_text()
            if text:
                text_comments = _extract_comments_from_text(text, page_num + 1)
                comments.extend(text_comments)
        
        doc.close()
        logger.info(f"✅ Extracted {len(comments)} raw comments from PDF")
    
    except Exception as e:
        logger.error(f"Error extracting PDF comments with PyMuPDF: {str(e)}")
        # Fallback to PyPDF2
        try:
            logger.info("Falling back to PyPDF2...")
            comments = _extract_with_pypdf2(pdf_buffer)
        except Exception as e2:
            logger.error(f"PyPDF2 fallback also failed: {str(e2)}")
            return []
    
    # Deduplicate comments
    comments = _deduplicate_comments(comments)
    logger.info(f"After deduplication: {len(comments)} comments")
    
    # Apply intelligent cleaning if cleaner is available
    if cleaner:
        cleaned_comments = []
        skipped_count = 0
        
        for comment in comments:
            try:
                result = cleaner.clean_comment(comment.comment_text)
                
                if result.should_skip:
                    skipped_count += 1
                    logger.debug(f"Skipped comment: {comment.comment_text[:50]}... Reason: {result.skip_reason}")
                    continue
                
                # Update comment with cleaned text
                comment.raw_text = comment.comment_text  # Preserve original
                comment.comment_text = result.cleaned_text
                comment.cleaned = True
                comment.cleaning_method = result.cleaning_method
                cleaned_comments.append(comment)
                
            except Exception as e:
                logger.warning(f"Cleaning error for comment: {e}")
                # Keep original comment on error
                cleaned_comments.append(comment)
        
        logger.info(f"✅ Cleaned {len(cleaned_comments)} comments, skipped {skipped_count} technical elements")
        comments = cleaned_comments
    
    return comments


def _map_annot_type_to_comment_type(annot_type: str) -> str:
    """Map PDF annotation type to our comment type"""
    annot_type_lower = annot_type.lower()
    
    if 'highlight' in annot_type_lower:
        return 'ADEQUACY'
    elif 'text' in annot_type_lower or 'note' in annot_type_lower:
        return 'GENERAL'
    elif 'stamp' in annot_type_lower or 'hold' in annot_type_lower:
        return 'HOLD'
    elif 'freetext' in annot_type_lower:
        return 'RECOMMENDATION'
    elif 'ink' in annot_type_lower or 'line' in annot_type_lower:
        return 'CLARIFICATION'
    else:
        return 'GENERAL'


def _extract_discipline_from_text(text: str) -> str:
    """Extract discipline from comment text"""
    text_lower = text.lower()
    
    disciplines = {
        'DCU': ['dcu', 'distributed control'],
        'MHC': ['mhc', 'material handling'],
        'Utilities': ['utility', 'utilities'],
        'Safety': ['safety', 'hse', 'health'],
        'Process': ['process'],
        'Electrical': ['electrical', 'e&i', 'instrumentation'],
        'Mechanical': ['mechanical', 'rotating'],
        'Civil': ['civil', 'structural'],
        'Piping': ['piping', 'pipeline'],
    }
    
    for discipline, keywords in disciplines.items():
        for keyword in keywords:
            if keyword in text_lower:
                return discipline
    
    return "Not Provided"


def _extract_with_pypdf2(pdf_buffer: BytesIO) -> List[ReviewerComment]:
    """Fallback extraction using PyPDF2"""
    comments = []
    pdf_buffer.seek(0)
    
    try:
        pdf_reader = PyPDF2.PdfReader(pdf_buffer)
        
        for page_num, page in enumerate(pdf_reader.pages, start=1):
            text = page.extract_text()
            
            # Extract comments from text
            page_comments = _extract_comments_from_text(text, page_num)
            comments.extend(page_comments)
            
            # Extract PDF annotations
            if '/Annots' in page:
                annotation_comments = _extract_annotations_pypdf2(page, page_num)
                comments.extend(annotation_comments)
    
    except Exception as e:
        logger.error(f"PyPDF2 extraction error: {str(e)}")
    
    return comments


def _extract_annotations_pypdf2(page, page_num: int) -> List[ReviewerComment]:
    """Extract comments from PDF annotations using PyPDF2"""
    comments = []
    
    try:
        annotations = page['/Annots']
        
        for annotation in annotations:
            annot_obj = annotation.get_object()
            
            if annot_obj.get('/Subtype') in ['/Text', '/FreeText', '/Highlight', '/Ink', '/Stamp']:
                comment = ReviewerComment()
                
                if '/Contents' in annot_obj:
                    comment.comment_text = str(annot_obj['/Contents'])
                    comment.page_number = page_num
                    comment.comment_type = classify_comment(comment.comment_text)
                    
                    if '/T' in annot_obj:
                        comment.reviewer_name = str(annot_obj['/T'])
                    
                    if comment.comment_text.strip():
                        comments.append(comment)
    
    except Exception as e:
        logger.debug(f"PyPDF2 annotation extraction error: {e}")
    
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
