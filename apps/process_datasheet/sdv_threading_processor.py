"""
Threading-based async processor for SDV datasheet generation
Fallback when Celery is in EAGER mode or not available
"""
import logging
import os
import base64
import threading
import uuid
import sys
from django.core.cache import cache

logger = logging.getLogger(__name__)


def log_and_print(message):
    """Log to both logger and stderr (which Docker captures)"""
    logger.info(message)
    sys.stderr.write(f"{message}\n")
    sys.stderr.flush()


def process_sdv_in_thread(pid_file_path, hmb_file_path, pid_filename, user_email, job_id):
    """
    Process SDV datasheet in background thread
    Stores result in Django cache
    
    Args:
        pid_file_path: Path to P&ID PDF
        hmb_file_path: Path to HMB PDF
        pid_filename: Original P&ID filename
        user_email: User email
        job_id: Unique job identifier
    """
    log_and_print(f"🚀 [SDV Thread {job_id[:8]}] Starting processing...")
    
    try:
        # Update progress
        cache.set(f'sdv_task_{job_id}_progress', 10, timeout=3600)
        cache.set(f'sdv_task_{job_id}_stage', 'Extracting P&ID data...', timeout=3600)
        
        # Import here to avoid issues
        from apps.process_datasheet.mock_extractors import MockPIDExtractor, match_lines_to_streams
        from apps.process_datasheet.hmb_vision_extractor import HMBVisionExtractor
        from apps.process_datasheet.sdv_ai_mapper import SDVDatasheetAIMapper
        from apps.process_datasheet.sdv_excel_generator_dynamic import SDVExcelGeneratorDynamic
        
        # STEP 1: Extract P&ID data
        log_and_print(f"📄 [SDV {job_id[:8]}] STEP 1: Extracting P&ID...")
        pid_extractor = MockPIDExtractor()
        pid_data = pid_extractor.extract_from_pdf(pid_file_path, original_filename=pid_filename)
        log_and_print(f"✅ [SDV {job_id[:8]}] P&ID extracted: {len(pid_data.get('valves', []))} valves")
        
        cache.set(f'sdv_task_{job_id}_progress', 30, timeout=3600)
        cache.set(f'sdv_task_{job_id}_stage', 'Extracting HMB data with Vision AI (this may take 2-5 minutes)...', timeout=3600)
        
        # STEP 2: Extract HMB data using Vision (this is the slow part)
        log_and_print(f"👁️ [SDV {job_id[:8]}] STEP 2: Extracting HMB with Vision...")
        try:
            vision_extractor = HMBVisionExtractor()
            hmb_data = vision_extractor.extract_from_pdf(hmb_file_path)
            log_and_print(f"✅ [SDV {job_id[:8]}] HMB extracted: {len(hmb_data.get('streams', []))} streams")
        except Exception as e:
            logger.warning(f"[SDV Thread {job_id}] Vision failed, using mock: {e}")
            from apps.process_datasheet.mock_extractors import MockHMBExtractor
            hmb_extractor = MockHMBExtractor()
            hmb_data = hmb_extractor.extract_from_pdf(hmb_file_path)
        
        cache.set(f'sdv_task_{job_id}_progress', 60, timeout=3600)
        cache.set(f'sdv_task_{job_id}_stage', 'Matching P&ID and HMB data...', timeout=3600)
        
        # STEP 3: Match lines
        log_and_print(f"🔗 [SDV {job_id[:8]}] STEP 3: Matching lines...")
        line_context = match_lines_to_streams(pid_data, hmb_data)
        log_and_print(f"✅ [SDV {job_id[:8]}] Lines matched")
        
        cache.set(f'sdv_task_{job_id}_progress', 75, timeout=3600)
        cache.set(f'sdv_task_{job_id}_stage', 'AI intelligent mapping...', timeout=3600)
        
        # STEP 4: AI Mapping
        log_and_print(f"🤖 [SDV {job_id[:8]}] STEP 4: AI intelligent mapping...")
        mapper = SDVDatasheetAIMapper()
        mapped_data = mapper.map_pid_hmb_to_datasheet(pid_data, hmb_data, line_context)
        log_and_print(f"✅ [SDV {job_id[:8]}] AI mapping complete: {len(mapped_data.get('valves', []))} valves mapped")
        
        cache.set(f'sdv_task_{job_id}_progress', 90, timeout=3600)
        cache.set(f'sdv_task_{job_id}_stage', 'Generating Excel datasheet...', timeout=3600)
        
        # STEP 5: Generate Excel
        log_and_print(f"📈 [SDV {job_id[:8]}] STEP 5: Generating Excel...")
        generator = SDVExcelGeneratorDynamic()
        excel_buffer = generator.generate_datasheet(mapped_data)
        excel_bytes = excel_buffer.getvalue()
        excel_base64 = base64.b64encode(excel_bytes).decode('utf-8')
        log_and_print(f"✅ [SDV {job_id[:8]}] Excel generated: {len(excel_bytes)} bytes")
        
        # STEP 6: Generate HTML
        from apps.process_datasheet.sdv_streams_view import generate_html_preview
        html_preview = generate_html_preview(mapped_data)
        
        # Store result
        result = {
            'success': True,
            'html_preview': html_preview,
            'excel_file': excel_base64,
            'filename': f'SDV_Datasheet_{pid_data.get("drawing_info", {}).get("pid_no", "Unknown")}.xlsx'
        }
        
        cache.set(f'sdv_task_{job_id}_result', result, timeout=3600)
        cache.set(f'sdv_task_{job_id}_progress', 100, timeout=3600)
        cache.set(f'sdv_task_{job_id}_stage', 'Complete!', timeout=3600)
        
        log_and_print(f"✅✅✅ [SDV {job_id[:8]}] COMPLETE! Datasheet ready.")
        
    except Exception as e:
        logger.error(f"[SDV Thread {job_id}] ❌ Error: {e}", exc_info=True)
        error_result = {
            'success': False,
            'error': str(e)
        }
        cache.set(f'sdv_task_{job_id}_result', error_result, timeout=3600)
        cache.set(f'sdv_task_{job_id}_stage', f'Error: {str(e)}', timeout=3600)
    
    finally:
        # Cleanup temp files
        try:
            if os.path.exists(pid_file_path):
                os.remove(pid_file_path)
            if os.path.exists(hmb_file_path):
                os.remove(hmb_file_path)
        except Exception as e:
            logger.warning(f"[SDV Thread {job_id}] Cleanup error: {e}")


def start_async_processing(pid_file_path, hmb_file_path, pid_filename, user_email):
    """
    Start async processing in a background thread
    
    Returns:
        job_id: Unique identifier for tracking
    """
    job_id = str(uuid.uuid4())
    
    # Start thread
    thread = threading.Thread(
        target=process_sdv_in_thread,
        args=(pid_file_path, hmb_file_path, pid_filename, user_email, job_id),
        daemon=True  # Thread will not prevent program exit
    )
    thread.start()
    
    logger.info(f"[SDV] Started background thread with job_id: {job_id}")
    return job_id
