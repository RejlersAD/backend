"""
CRS Documents Helper Modules
Safe extension for PDF comment extraction and CRS template population
"""

from .comment_extractor import extract_reviewer_comments, classify_comment
from .template_populator import populate_crs_template, generate_populated_crs

__all__ = [
    'extract_reviewer_comments',
    'classify_comment',
    'populate_crs_template',
    'generate_populated_crs',
]
