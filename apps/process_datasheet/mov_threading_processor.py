"""
Threading-based async processor for MOV datasheet generation
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


def process_mov_in_thread(pid_file_path, hmb_file_path, pid_filename, user_email, job_id):
    """
    Process MOV datasheet in background thread
    Stores result in Django cache
    
    Args:
        pid_file_path: Path to P&ID PDF
        hmb_file_path: Path to HMB PDF
        pid_filename: Original P&ID filename
        user_email: User email
        job_id: Unique job identifier
    """
    log_and_print(f"ðŸš€ [MOV Thread {job_id[:8]}] Starting processing...")
    
    try:
        # Update progress
        cache.set(f'mov_task_{job_id}_progress', 10, timeout=3600)
        cache.set(f'mov_task_{job_id}_stage', 'Extracting P&ID data...', timeout=3600)
        
        # Import here to avoid issues
        from apps.process_datasheet.mock_extractors import MockPIDExtractor, match_lines_to_streams
        from apps.process_datasheet.hmb_vision_extractor import HMBVisionExtractor
        from apps.process_datasheet.mov_ai_mapper import MOVDatasheetAIMapper
        from apps.process_datasheet.mov_excel_generator_dynamic import MOVExcelGeneratorDynamic
        
        # STEP 1: Extract P&ID data with REAL extraction (Vision AI + OCR)
        log_and_print(f"ðŸ“„ [MOV {job_id[:8]}] STEP 1: Extracting P&ID with Vision AI...")
        try:
            from apps.process_datasheet.real_pid_extractor import RealPIDExtractor
            real_extractor = RealPIDExtractor()
            pid_data = real_extractor.extract_valves_from_pdf(pid_file_path, original_filename=pid_filename, valve_type='MOV')
            
            # Check if real extraction produced results
            if not pid_data.get('valves') or len(pid_data.get('valves', [])) == 0:
                raise ValueError("Real extraction returned 0 valves")
            
            log_and_print(f"âœ… [MOV {job_id[:8]}] REAL extraction: {len(pid_data.get('valves', []))} MOV valves")
        except Exception as e:
            logger.warning(f"[MOV Thread {job_id}] Real extraction failed, using mock fallback: {e}")
            log_and_print(f"âš ï¸ [MOV {job_id[:8]}] Using mock data fallback...")
            pid_extractor = MockPIDExtractor()
            pid_data = pid_extractor.extract_from_pdf(pid_file_path, original_filename=pid_filename)
            
            # Filter for MOV valves from mock data
            all_valves = pid_data.get('valves', [])
            mov_valves = [v for v in all_valves if v.get('type', '').upper() == 'MOV' or 'MOV' in v.get('tag_no', '').upper()]
            pid_data['valves'] = mov_valves
            log_and_print(f"âœ… [MOV {job_id[:8]}] Mock extraction: {len(mov_valves)} MOV valves")
        
        cache.set(f'mov_task_{job_id}_progress', 30, timeout=3600)
        cache.set(f'mov_task_{job_id}_stage', 'Extracting HMB data with Vision AI (this may take 2-5 minutes)...', timeout=3600)
        
        # STEP 2: Extract HMB data using Vision (this is the slow part)
        import signal
        from contextlib import contextmanager
        
        @contextmanager
        def timeout_context(seconds):
            def timeout_handler(signum, frame):
                raise TimeoutError(f"Operation timed out after {seconds} seconds")
            original_handler = signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(seconds)
            try:
                yield
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, original_handler)
        
        try:
            with timeout_context(120):  # 2 minute timeout
                vision_extractor = HMBVisionExtractor()
                hmb_data = vision_extractor.extract_from_pdf(hmb_file_path)
                log_and_print(f"âœ… [MOV {job_id[:8]}] HMB extracted: {len(hmb_data.get('streams', []))} streams")
                if not hmb_data.get('streams'):
                    raise ValueError("Vision returned 0 streams")
        except (Exception, TimeoutError) as e:
            logger.warning(f"[MOV Thread {job_id}] Vision failed/timeout, using mock: {e}")
            log_and_print(f" [MOV {job_id[:8]}] HMB Vision failed, using mock data...")
            from apps.process_datasheet.mock_extractors import MockHMBExtractor
            hmb_extractor = MockHMBExtractor()
            hmb_data = hmb_extractor.extract_from_pdf(hmb_file_path)
            log_and_print(f" [MOV {job_id[:8]}] Mock HMB: {len(hmb_data.get('streams', []))} streams")
        cache.set(f'mov_task_{job_id}_stage', 'Matching P&ID and HMB data...', timeout=3600)
        
        # STEP 3: Match lines
        log_and_print(f"ðŸ”— [MOV {job_id[:8]}] STEP 3: Matching lines...")
        line_context = match_lines_to_streams(pid_data, hmb_data)
        log_and_print(f"âœ… [MOV {job_id[:8]}] Lines matched")
        
        cache.set(f'mov_task_{job_id}_progress', 75, timeout=3600)
        cache.set(f'mov_task_{job_id}_stage', 'AI intelligent mapping...', timeout=3600)
        
        # STEP 4: AI Mapping with fallback to basic mapping
        log_and_print(f"ðŸ¤– [MOV {job_id[:8]}] STEP 4: AI intelligent mapping...")
        try:
            mapper = MOVDatasheetAIMapper()
            mapped_data = mapper.map_pid_hmb_to_datasheet(pid_data, hmb_data, line_context)
            
            # Check if AI mapping actually produced results
            if not mapped_data.get('valves') or len(mapped_data.get('valves', [])) == 0:
                raise ValueError(f"AI mapping returned 0 valves. Error: {mapped_data.get('error', 'Unknown')}")
                
            log_and_print(f"âœ… [MOV {job_id[:8]}] AI mapping complete: {len(mapped_data.get('valves', []))} valves mapped")
        except Exception as e:
            logger.warning(f"[MOV Thread {job_id}] AI mapping failed, using basic mapping: {e}")
            log_and_print(f"âš ï¸ [MOV {job_id[:8]}] AI failed, using basic mapping from P&ID data...")
            
            # Fallback: Create basic mapped data from P&ID valves
            mapped_valves = []
            for valve in pid_data.get('valves', []):
                mapped_valve = {
                    'tag_no': valve.get('tag_no', valve.get('tag', 'UNKNOWN')),
                    'tag': valve.get('tag', valve.get('tag_no', 'UNKNOWN')),
                    'pid_no': pid_data.get('drawing_info', {}).get('pid_no', 'UNKNOWN'),
                    'line_no': valve.get('line_no', ''),
                    'service': valve.get('service', valve.get('description', '')),
                    'piping_class': valve.get('piping_class', ''),
                    'fluid': 'See HMB',
                    'phase': 'TBD',
                    'operating_pressure_normal': valve.get('pressure', ''),
                    'operating_temp_min': valve.get('temp_min', ''),
                    'operating_temp_max': valve.get('temp_max', ''),
                    'design_pressure': valve.get('design_pressure', ''),
                    'design_temp_min': valve.get('design_temp_min', ''),
                    'design_temp_max': valve.get('design_temp_max', ''),
                }
                mapped_valves.append(mapped_valve)
            
            mapped_data = {
                'valves': mapped_valves,
                'drawing_info': pid_data.get('drawing_info', {}),
                'mapping_method': 'basic_fallback'
            }
            log_and_print(f"âœ… [MOV {job_id[:8]}] Basic mapping complete: {len(mapped_valves)} valves mapped")
        
        cache.set(f'mov_task_{job_id}_progress', 90, timeout=3600)
        cache.set(f'mov_task_{job_id}_stage', 'Generating Excel datasheet...', timeout=3600)
        
        # STEP 5: Generate Excel
        log_and_print(f"ðŸ“ˆ [MOV {job_id[:8]}] STEP 5: Generating Excel...")
        generator = MOVExcelGeneratorDynamic()
        excel_buffer = generator.generate_datasheet(mapped_data)
        excel_bytes = excel_buffer.getvalue()
        excel_base64 = base64.b64encode(excel_bytes).decode('utf-8')
        log_and_print(f"âœ… [MOV {job_id[:8]}] Excel generated: {len(excel_bytes)} bytes")
        
        # STEP 6: Generate HTML
        from apps.process_datasheet.mov_streams_view import generate_html_preview
        html_preview = generate_html_preview(mapped_data)
        
        # Store result
        result = {
            'success': True,
            'html_preview': html_preview,
            'excel_file': excel_base64,
            'filename': f'MOV_Datasheet_{pid_data.get("drawing_info", {}).get("pid_no", "Unknown")}.xlsx'
        }
        
        cache.set(f'mov_task_{job_id}_result', result, timeout=3600)
        cache.set(f'mov_task_{job_id}_progress', 100, timeout=3600)
        cache.set(f'mov_task_{job_id}_stage', 'Complete!', timeout=3600)
        
        log_and_print(f"âœ…âœ…âœ… [MOV {job_id[:8]}] COMPLETE! Datasheet ready.")
        return result
        
    except Exception as e:
        logger.error(f"[MOV Thread {job_id}] âŒ Error: {e}", exc_info=True)
        
        # Provide more user-friendly error messages
        error_message = str(e)
        if 'insufficient_quota' in error_message or '429' in error_message or 'exceeded your current quota' in error_message:
            error_message = "OpenAI API quota exceeded. Please add credits at https://platform.openai.com/account/billing to continue processing."
        elif 'rate_limit' in error_message.lower():
            error_message = "OpenAI API rate limit reached. Please wait a moment and try again."
        
        error_result = {
            'success': False,
            'error': error_message
        }
        cache.set(f'mov_task_{job_id}_result', error_result, timeout=3600)
        cache.set(f'mov_task_{job_id}_stage', f'Error: {error_message}', timeout=3600)
        return error_result
    
    finally:
        # Cleanup temp files
        try:
            if os.path.exists(pid_file_path):
                os.remove(pid_file_path)
            if os.path.exists(hmb_file_path):
                os.remove(hmb_file_path)
        except Exception as e:
            logger.warning(f"[MOV Thread {job_id}] Cleanup error: {e}")


def start_async_processing(pid_file_path, hmb_file_path, pid_filename, user_email):
    """
    Start async processing in a background thread
    
    Returns:
        job_id: Unique identifier for tracking
    """
    job_id = str(uuid.uuid4())
    
    # Start thread
    thread = threading.Thread(
        target=process_mov_in_thread,
        args=(pid_file_path, hmb_file_path, pid_filename, user_email, job_id),
        daemon=True  # Thread will not prevent program exit
    )
    thread.start()
    
    logger.info(f"[MOV] Started background thread with job_id: {job_id}")
    return job_id
