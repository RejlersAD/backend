"""
DesignIQ Celery Tasks
Background tasks for long-running operations like P&ID OCR processing
"""

from celery import shared_task
from django.utils import timezone
import logging
import tempfile
import os

logger = logging.getLogger(__name__)


@shared_task(bind=True, time_limit=600, soft_time_limit=540)
def process_pid_upload(self, pdf_path, line_format_config=None, user_id=None, list_type='line_list'):
    """
    Background task to process P&ID PDF upload with OCR
    
    Args:
        pdf_path: Path to the uploaded PDF file
        line_format_config: Optional line format configuration
        user_id: User ID who uploaded the file
        list_type: Type of engineering list
        
    Returns:
        dict: Processing results with extracted lines
    """
    from .pid_ocr_extractor_v2 import get_pid_extractor
    from .models import DesignProject
    from django.contrib.auth import get_user_model
    
    User = get_user_model()
    
    try:
        # Update task progress
        self.update_state(
            state='PROGRESS',
            meta={'current': 10, 'total': 100, 'status': 'Initializing OCR engines...'}
        )
        
        # Get singleton extractor instance (reuses loaded models - FAST!)
        extractor = get_pid_extractor()
        
        self.update_state(
            state='PROGRESS',
            meta={'current': 20, 'total': 100, 'status': 'Reading PDF file...'}
        )
        
        # Extract line numbers using Multi-Engine OCR
        line_items = extractor.extract_from_pdf(pdf_path, line_format_config=line_format_config)
        
        self.update_state(
            state='PROGRESS',
            meta={'current': 80, 'total': 100, 'status': 'Formatting results...'}
        )
        
        table_data = extractor.format_as_table_data(line_items)
        
        logger.info(f"✅ [Celery] Extracted {len(line_items)} line numbers")
        
        self.update_state(
            state='PROGRESS',
            meta={'current': 100, 'total': 100, 'status': 'Complete!'}
        )
        
        return {
            "success": True,
            "total_items": len(table_data),
            "extracted_lines": table_data,
            "pdf_path": pdf_path,
            "message": "P&ID processed successfully"
        }
        
    except Exception as e:
        logger.error(f"❌ [Celery] Error processing P&ID: {str(e)}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "message": "Failed to process P&ID"
        }
