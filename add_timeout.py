import re

# Read the file
with open('apps/process_datasheet/mov_threading_processor.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find and replace the HMB extraction section
old_code = '''        # STEP 2: Extract HMB data using Vision (this is the slow part)
        log_and_print(f" [MOV {job_id[:8]}] STEP 2: Extracting HMB with Vision...")
        try:
            vision_extractor = HMBVisionExtractor()
            hmb_data = vision_extractor.extract_from_pdf(hmb_file_path)
            log_and_print(f" [MOV {job_id[:8]}] HMB extracted: {len(hmb_data.get('streams', []))} streams")
        except Exception as e:
            logger.warning(f"[MOV Thread {job_id}] Vision failed, using mock: {e}")
            from apps.process_datasheet.mock_extractors import MockHMBExtractor
            hmb_extractor = MockHMBExtractor()
            hmb_data = hmb_extractor.extract_from_pdf(hmb_file_path)'''

new_code = '''        # STEP 2: Extract HMB data using Vision with timeout fallback
        log_and_print(f" [MOV {job_id[:8]}] STEP 2: Extracting HMB with Vision (120s timeout)...")
        import signal
        from contextmanager import contextmanager
        
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
                if hmb_data.get('streams'):
                    log_and_print(f" [MOV {job_id[:8]}] HMB Vision extracted: {len(hmb_data.get('streams', []))} streams")
                else:
                    raise ValueError("Vision returned 0 streams")
        except (Exception, TimeoutError) as e:
            logger.warning(f"[MOV Thread {job_id}] Vision failed/timeout, using mock: {e}")
            log_and_print(f" [MOV {job_id[:8]}] HMB Vision failed, using mock data...")
            from apps.process_datasheet.mock_extractors import MockHMBExtractor
            hmb_extractor = MockHMBExtractor()
            hmb_data = hmb_extractor.extract_from_pdf(hmb_file_path)
            log_and_print(f" [MOV {job_id[:8]}] Mock HMB: {len(hmb_data.get('streams', []))} streams")'''

# Replace the code
if old_code in content:
    content = content.replace(old_code, new_code)
    with open('apps/process_datasheet/mov_threading_processor.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print(" Timeout fix applied successfully!")
else:
    print(" Could not find exact match - manual fix needed")
