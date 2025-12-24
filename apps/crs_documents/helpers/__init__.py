"""
CRS Documents Helper Modules
Safe extension for PDF comment extraction and CRS template population
Enhanced with intelligent comment cleaning and S3 template integration
"""

from .comment_extractor import (
    extract_reviewer_comments, 
    classify_comment,
    get_comment_statistics,
    ReviewerComment
)
from .template_populator import populate_crs_template, generate_populated_crs
from .template_manager import get_crs_template, get_template_info

# Import comment cleaner (optional - may not have OpenAI installed)
try:
    from .comment_cleaner import (
        CommentCleaner,
        get_comment_cleaner,
        clean_comment_text,
        CleaningResult
    )
    CLEANER_AVAILABLE = True
except ImportError:
    CLEANER_AVAILABLE = False

__all__ = [
    # Comment Extraction
    'extract_reviewer_comments',
    'classify_comment',
    'get_comment_statistics',
    'ReviewerComment',
    
    # Template Population
    'populate_crs_template',
    'generate_populated_crs',
    
    # Template Management
    'get_crs_template',
    'get_template_info',
    
    # Comment Cleaning (if available)
    'CommentCleaner',
    'get_comment_cleaner',
    'clean_comment_text',
    'CleaningResult',
    'CLEANER_AVAILABLE',
]
