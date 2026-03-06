$file = "apps\process_datasheet\sdv_threading_processor.py"
$lines = Get-Content $file

# Find the line with "STEP 2: Extract HMB"
for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match "STEP 2: Extract HMB") {
        # Insert timeout code before the try block
        $newLines = @()
        $newLines += $lines[0..($i)]  # Include everything up to comment
        $newLines += "        import signal"
        $newLines += "        from contextlib import contextmanager"
        $newLines += "        "
        $newLines += "        @contextmanager"
        $newLines += "        def timeout_context(seconds):"
        $newLines += "            def timeout_handler(signum, frame):"
        $newLines += "                raise TimeoutError(f`"Operation timed out after {seconds} seconds`")"
        $newLines += "            original_handler = signal.signal(signal.SIGALRM, timeout_handler)"
        $newLines += "            signal.alarm(seconds)"
        $newLines += "            try:"
        $newLines += "                yield"
        $newLines += "            finally:"
        $newLines += "                signal.alarm(0)"
        $newLines += "                signal.signal(signal.SIGALRM, original_handler)"
        $newLines += "        "
        
        # Modify the try block to include timeout
        $i += 2  # Skip to try line
        $newLines += "        try:"
        $newLines += "            with timeout_context(120):  # 2 minute timeout"
        
        # Add existing code with proper indentation
        $tryEnd = $i + 5
        for ($j = $i + 1; $j -le $tryEnd; $j++) {
            $line = $lines[$j]
            if ($line -match "^\s+vision_extractor" -or $line -match "^\s+hmb_data" -or $line -match "^\s+log_and_print.*HMB extracted") {
                $newLines += "    " + $line  # Add extra indentation
            } else {
                break
            }
        }
        
        # Add validation check
        $newLines += "                if not hmb_data.get('streams'):"
        $newLines += "                    raise ValueError(`"Vision returned 0 streams`")"
        $newLines += "        except (Exception, TimeoutError) as e:"
        $newLines += "            logger.warning(f`"[SDV Thread {job_id}] Vision failed/timeout, using mock: {e}`")"
        $newLines += "            log_and_print(f`" [SDV {job_id[:8]}] HMB Vision failed, using mock data...`")"
        $newLines += "            from apps.process_datasheet.mock_extractors import MockHMBExtractor"
        $newLines += "            hmb_extractor = MockHMBExtractor()"
        $newLines += "            hmb_data = hmb_extractor.extract_from_pdf(hmb_file_path)"
        $newLines += "            log_and_print(f`" [SDV {job_id[:8]}] Mock HMB: {len(hmb_data.get('streams', []))} streams`")"
        
        # Skip old except block and add rest of file
        for ($k = $tryEnd + 6; $k -lt $lines.Count; $k++) {
            $newLines += $lines[$k]
        }
        
        $newLines | Set-Content $file -Encoding UTF8
        Write-Output " SDV Timeout fix applied!"
        break
    }
}
