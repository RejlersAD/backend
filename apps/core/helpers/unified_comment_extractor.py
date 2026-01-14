"""
UNIFIED PDF COMMENT EXTRACTOR
=============================
Single source of truth for PDF comment extraction across all CRS systems.
Based on CRS document management logic - proven and optimized.

Used by:
- apps/crs_documents (CRS Document Management)
- apps/crs (CRS Multi-Revision)

Any changes to extraction logic, text detection, or filtering should be made here ONLY.
"""

import re
import fitz  # PyMuPDF - better for annotations
import PyPDF2
from typing import List, Dict, Optional
from io import BytesIO
import logging

logger = logging.getLogger(__name__)


# ============================================
# DATA STRUCTURES
# ============================================

class ReviewerComment:
    """Universal data structure for a reviewer comment"""
    
    def __init__(self):
        self.reviewer_name: str = "Not Provided"
        self.comment_text: str = ""
        self.discipline: str = "Not Provided"
        self.section_reference: str = "Not Provided"
        self.page_number: Optional[int] = None
        self.comment_type: str = "GENERAL"
        self.raw_text: str = ""
        self.cleaned: bool = False
        self.cleaning_method: str = ""
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for API responses"""
        return {
            'page': self.page_number,
            'reviewer': self.reviewer_name,
            'text': self.comment_text,
            'type': self.comment_type,
            'discipline': self.discipline,
            'section_reference': self.section_reference,
            'cleaned': self.cleaned,
            'cleaning_method': self.cleaning_method
        }


# ============================================
# PRE-CLEANING
# ============================================

def _pre_clean_annotation_text(text: str) -> str:
    """
    Pre-clean annotation text to remove common annotation type labels
    Applied before the main comment cleaner runs
    """
    cleaned = text.strip()
    
    # Remove annotation type labels at the beginning
    annotation_prefixes = [
        r'^(text\s*box|Text\s*Box|TEXT\s*BOX)\s*[-:]?\s*',
        r'^(callout|Callout|CALLOUT)\s*[-:]?\s*',
        r'^(free\s*text|Free\s*Text|FREE\s*TEXT)\s*[-:]?\s*',
        r'^(note|Note|NOTE)\s*[-:]?\s*',
        r'^(sticky\s*note|Sticky\s*Note|STICKY\s*NOTE)\s*[-:]?\s*',
        r'^(comment|Comment|COMMENT)\s*[-:]?\s*',
        r'^(highlight|Highlight|HIGHLIGHT)\s*[-:]?\s*',
    ]
    
    for pattern in annotation_prefixes:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
    
    # Clean up any leftover whitespace
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    
    return cleaned


# ============================================
# MAIN EXTRACTION FUNCTION
# ============================================

def extract_reviewer_comments(
    pdf_buffer: BytesIO,
    apply_cleaning: bool = True,
    filter_reviewers: bool = True
) -> List[ReviewerComment]:
    """
    UNIFIED EXTRACTION METHOD - Single source of truth
    
    Args:
        pdf_buffer: BytesIO object containing PDF file
        apply_cleaning: Whether to apply intelligent comment cleaning (default: True)
        filter_reviewers: Whether to filter out "Not Provided" reviewers (default: True)
        
    Returns:
        List of ReviewerComment objects
        
    Implementation: Safe, isolated, no side effects
    """
    import time
    total_start = time.time()
    comments = []
    
    # Initialize cleaner if available and cleaning is requested
    cleaner = None
    if apply_cleaning:
        try:
            from apps.crs_documents.helpers.comment_cleaner import get_comment_cleaner
            cleaner = get_comment_cleaner()
            logger.info("✅ Comment cleaner initialized")
        except ImportError:
            logger.info("ℹ️ Comment cleaner not available, proceeding without cleaning")
        except Exception as e:
            logger.warning(f"⚠️ Could not initialize comment cleaner: {e}")
    
    try:
        # Use PyMuPDF (fitz) for better annotation extraction
        pdf_start = time.time()
        pdf_bytes = pdf_buffer.read()
        pdf_buffer.seek(0)  # Reset for potential re-use
        
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        logger.info(f"[PERF] PDF open took {time.time() - pdf_start:.2f}s")
        logger.info(f"📄 Opened PDF with {len(doc)} pages")
        
        # SAFETY: Check if PDF is too large (prevents timeout)
        if len(doc) > 300:
            logger.error(f"❌ PDF too large: {len(doc)} pages (max 300)")
            doc.close()
            raise ValueError(f"PDF has {len(doc)} pages, maximum is 300 pages")
        
        page_start = time.time()
        for page_num in range(len(doc)):
            page = doc[page_num]
            
            # SAFETY: Check elapsed time every 50 pages to prevent timeout
            if page_num % 50 == 0 and page_num > 0:
                elapsed = time.time() - total_start
                if elapsed > 90:  # 90 seconds warning threshold
                    logger.warning(f"⚠️ Processing taking long: {elapsed:.1f}s at page {page_num}/{len(doc)}")
                if elapsed > 110:  # 110 seconds abort threshold (before 120s worker timeout)
                    logger.error(f"❌ Aborting: Processing timeout at page {page_num}/{len(doc)}")
                    doc.close()
                    raise TimeoutError(f"Processing aborted at page {page_num} to prevent worker timeout")
            
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
                        
                        # PERFORMANCE: Quick AutoCAD rejection BEFORE processing
                        content_lower = content.lower()
                        if any(kw in content_lower for kw in ['autocad', 'autodesk', 'acad', 'dwg', 'layer', 'xref']):
                            continue
                        if '%%' in content or content.startswith('A-') or content.startswith('M-'):
                            continue
                        
                        # Pre-clean the content to remove annotation type labels
                        content_cleaned = _pre_clean_annotation_text(content.strip())
                        
                        # Skip if pre-cleaning removed everything
                        if not content_cleaned or len(content_cleaned) < 5:
                            continue
                        
                        comment = ReviewerComment()
                        comment.comment_text = content_cleaned
                        comment.page_number = page_num + 1
                        comment.comment_type = _map_annot_type_to_comment_type(annot_type)
                        comment.reviewer_name = title.strip() if title.strip() else "Not Provided"
                        comment.raw_text = f"{annot_type}: {content}"
                        
                        # Try to extract discipline from content
                        comment.discipline = _extract_discipline_from_text(content)
                        
                        comments.append(comment)
                        
                    except Exception as e:
                        logger.warning(f"Error extracting annotation: {e}")
                        continue
            
            # PERFORMANCE OPTIMIZATION: Skip text extraction (causes timeout on large PDFs)
            # Text extraction with get_text() is VERY slow and often finds false positives
            # Annotations are faster and more accurate for CRS comments
            # If needed, enable selectively with a flag
        
        logger.info(f"[PERF] Page processing took {time.time() - page_start:.2f}s for {len(doc)} pages")
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
    
    # Keep all comments including duplicates on same page
    logger.info(f"Keeping all comments including duplicates: {len(comments)} comments")
    
    # Filter out incomplete comments and AutoCAD elements
    filter_start = time.time()
    comments = _filter_incomplete_comments(comments)
    logger.info(f"[PERF] Filtering took {time.time() - filter_start:.2f}s")
    logger.info(f"After filtering incomplete: {len(comments)} comments")
    
    # Apply intelligent cleaning if cleaner is available
    cleaning_start = time.time()
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
        
        logger.info(f"[PERF] Cleaning took {time.time() - cleaning_start:.2f}s")
        logger.info(f"✅ Cleaned {len(cleaned_comments)} comments, skipped {skipped_count} technical elements")
        comments = cleaned_comments
    
    # Filter out comments with "Not Provided" reviewer (optional)
    if filter_reviewers:
        comments = _filter_not_provided_reviewers(comments)
        logger.info(f"After filtering 'Not Provided' reviewers: {len(comments)} comments")
    
    logger.info(f"[PERF] Total extraction time: {time.time() - total_start:.2f}s")
    
    return comments


# ============================================
# FILTERING & CLASSIFICATION
# ============================================

def _filter_not_provided_reviewers(comments: List[ReviewerComment]) -> List[ReviewerComment]:
    """
    Filter out comments where reviewer name is 'Not Provided' or similar
    These are usually technical elements or unattributed annotations
    """
    filtered = []
    not_provided_patterns = ['not provided', 'not_provided', 'unknown', 'n/a', 'na', '']
    
    for comment in comments:
        reviewer = comment.reviewer_name.lower().strip() if comment.reviewer_name else ''
        
        # Skip if reviewer matches any "not provided" pattern
        if reviewer in not_provided_patterns:
            logger.debug(f"Filtered out comment with 'Not Provided' reviewer: {comment.comment_text[:50]}...")
            continue
        
        filtered.append(comment)
    
    return filtered


def _filter_incomplete_comments(comments: List[ReviewerComment]) -> List[ReviewerComment]:
    """
    Filter out incomplete or malformed comments using intelligent criteria
    
    SMART FILTERING: Only reject if clearly not a meaningful comment
    - Has reviewer attribution? Likely meaningful
    - Has actual words (not just numbers)? Keep it
    - Not an AutoCAD/technical element? Keep it
    
    ENHANCED: Comprehensive AutoCAD pattern filtering (OPTIMIZED FOR SPEED)
    """
    filtered_comments = []
    
    # Pre-compile regex patterns for performance (compile once, use many times)
    import re
    autocad_layer_pattern = re.compile(r'^[A-Z]+-[A-Z]+-[A-Z0-9]+$')
    autocad_text_style_pattern = re.compile(r'^(STANDARD|ROMANS|ROMAND|SIMPLEX|COMPLEX|ITALIC)\s*$', re.IGNORECASE)
    scale_pattern = re.compile(r'^(SCALE|NTS|NOT TO SCALE)\s*[:=]?\s*[\d:/]*$', re.IGNORECASE)
    coordinate_pattern = re.compile(r'^[XYZ]\s*[=:]?\s*[\d\.\-]+$', re.IGNORECASE)
    drawing_ref_pattern = re.compile(r'^(DWG|SHT|SHEET)\s*[#:]?\s*[\d\-]+$', re.IGNORECASE)
    only_numbers_pattern = re.compile(r'^[\d\s\.\-\/\,\:\(\)\[\]]+$')
    proper_name_pattern = re.compile(r'^[A-Z][a-z]+\s+[A-Z][a-z]+$')
    has_letters_pattern = re.compile(r'[a-zA-Z]{2,}')
    
    # Pre-defined lists for fast lookup
    autocad_keywords = {'autocad', 'autodesk', 'acad', 'dwg', 'dxf', 'shx', 'xref', 'viewport', 'mview'}
    annotation_labels = {'text box', 'callout', 'free text', 'note', 'sticky note', 
                        'highlight', 'typewriter', 'comment', 'annotation'}
    
    for comment in comments:
        text = comment.comment_text.strip()
        text_lower = text.lower()
        reviewer = comment.reviewer_name.strip() if comment.reviewer_name else ""
        
        # Minimum length check (3 chars)
        if len(text) < 3:
            continue
        
        # ============================================
        # ENHANCED AUTOCAD FILTERS (OPTIMIZED FOR SPEED)
        # ============================================
        
        # Quick keyword check first (fastest - set lookup is O(1))
        if any(kw in text_lower for kw in autocad_keywords):
            continue
        
        # Quick special character check
        if '%%' in text:
            continue
        
        # Pattern checks (pre-compiled regex - much faster)
        if autocad_layer_pattern.match(text):
            continue
        
        if len(text) < 20 and autocad_text_style_pattern.match(text):
            continue
        
        if scale_pattern.match(text):
            continue
        
        if len(text) < 30 and coordinate_pattern.match(text):
            continue
        
        if drawing_ref_pattern.match(text):
            continue
        
        # Skip if it's ONLY numbers/symbols (AutoCAD coordinates/dimensions)
        if only_numbers_pattern.match(text):
            continue
        
        # Skip if it's ONLY a proper name pattern
        if len(text) > 5 and proper_name_pattern.match(text) and reviewer.lower() == text.lower():
            continue
        
        # Skip ONLY if it's exactly an annotation label (nothing else)
        if text_lower.strip() in annotation_labels:
            continue
        
        # SMART DECISION: If comment has a reviewer AND contains actual words, keep it
        has_letters = bool(has_letters_pattern.search(text))
        
        if has_letters and reviewer and reviewer.lower() != 'not provided':
            # This is likely a meaningful comment from a real reviewer
            filtered_comments.append(comment)
            continue
        
        # For comments without clear reviewer, be more selective
        if not reviewer or reviewer.lower() == 'not provided':
            # Needs to have some substance (multiple words or punctuation)
            words = text.split()
            if len(words) >= 2 or any(text.endswith(p) for p in '.!?,;:'):
                filtered_comments.append(comment)
        else:
            # Has reviewer, keep it
            filtered_comments.append(comment)
    
    return filtered_comments


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


# ============================================
# FALLBACK EXTRACTION (PyPDF2)
# ============================================

def _extract_with_pypdf2(pdf_buffer: BytesIO) -> List[ReviewerComment]:
    """Fallback extraction using PyPDF2"""
    comments = []
    pdf_buffer.seek(0)
    
    try:
        pdf_reader = PyPDF2.PdfReader(pdf_buffer)
        
        for page_num, page in enumerate(pdf_reader.pages, start=1):
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
                    comment.comment_type = _map_annot_type_to_comment_type(str(annot_obj.get('/Subtype')))
                    
                    if '/T' in annot_obj:
                        comment.reviewer_name = str(annot_obj['/T'])
                    
                    if comment.comment_text.strip():
                        comments.append(comment)
    
    except Exception as e:
        logger.debug(f"PyPDF2 annotation extraction error: {e}")
    
    return comments


# ============================================
# UTILITY FUNCTIONS
# ============================================

def convert_comments_to_dict_list(comments: List[ReviewerComment]) -> List[Dict]:
    """Convert ReviewerComment objects to dictionary list for API responses"""
    return [comment.to_dict() for comment in comments]


def classify_comment(text: str) -> str:
    """Classify comment type based on text content"""
    text_lower = text.lower()
    
    if 'hold' in text_lower or 'stop' in text_lower:
        return 'HOLD'
    elif 'recommend' in text_lower or 'suggest' in text_lower:
        return 'RECOMMENDATION'
    elif 'clarify' in text_lower or 'explain' in text_lower or 'what' in text_lower:
        return 'CLARIFICATION'
    elif 'adequate' in text_lower or 'satisfactory' in text_lower:
        return 'ADEQUACY'
    else:
        return 'GENERAL'


def get_comment_statistics(comments: List[ReviewerComment]) -> Dict:
    """
    Get statistics about extracted comments
    
    Returns:
        Dictionary with comment statistics
    """
    if not comments:
        return {
            'total_comments': 0,
            'by_type': {},
            'by_discipline': {},
            'by_reviewer': {},
            'pages_with_comments': 0
        }
    
    stats = {
        'total_comments': len(comments),
        'by_type': {},
        'by_discipline': {},
        'by_reviewer': {},
        'pages_with_comments': len(set(c.page_number for c in comments if c.page_number))
    }
    
    for comment in comments:
        # Count by type
        comment_type = comment.comment_type or 'GENERAL'
        stats['by_type'][comment_type] = stats['by_type'].get(comment_type, 0) + 1
        
        # Count by discipline
        discipline = comment.discipline or 'Not Provided'
        stats['by_discipline'][discipline] = stats['by_discipline'].get(discipline, 0) + 1
        
        # Count by reviewer
        reviewer = comment.reviewer_name or 'Not Provided'
        stats['by_reviewer'][reviewer] = stats['by_reviewer'].get(reviewer, 0) + 1
    
    return stats
